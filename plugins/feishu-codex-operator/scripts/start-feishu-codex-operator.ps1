[CmdletBinding()]
param(
    [switch]$HookInvocation,
    [switch]$DetachedLaunch,
    [ValidatePattern('^[a-f0-9]{24}$')]
    [string]$LeaseId = ''
)

$ErrorActionPreference = 'Stop'
$OPERATOR_RUNTIME_MANIFEST_SCHEMA = 1
$DETACHED_LAUNCH_TIMEOUT_SECONDS = 7

# FEISHU_OPERATOR_HOOK_SUCCESS_STDOUT_SILENT_V1
function Write-LifecycleStatus {
    param([Parameter(Mandatory = $true)][string]$Message)
    if (-not $HookInvocation) {
        Write-Output $Message
    }
}

if ($HookInvocation -and $DetachedLaunch) {
    throw 'HookInvocation and DetachedLaunch are mutually exclusive.'
}
if ($DetachedLaunch -and [string]::IsNullOrWhiteSpace($LeaseId)) {
    throw 'DetachedLaunch requires an exact lease id.'
}
if (-not $DetachedLaunch -and -not [string]::IsNullOrWhiteSpace($LeaseId)) {
    throw 'LeaseId is reserved for DetachedLaunch.'
}

if ($env:CODEX_OPERATOR_CHILD -eq '1') {
    Write-LifecycleStatus 'Skipping Feishu operator lifecycle hook inside its Codex child.'
    exit 0
}

$startHookScript = $MyInvocation.MyCommand.Path
$hooksRoot = Split-Path -Parent $startHookScript
$projectRoot = Split-Path -Parent (Split-Path -Parent $hooksRoot)
$runtimeRoot = Join-Path $projectRoot '.codex\feishu-codex-operator-runtime'
$operatorScript = Join-Path $runtimeRoot 'operator_main.py'
$runtimeManifest = Join-Path $runtimeRoot 'runtime-manifest.json'
$stopHookScript = Join-Path $hooksRoot 'stop-feishu-codex-operator.ps1'
$pidFile = Join-Path $runtimeRoot 'operator.pid'
$stopFile = Join-Path $runtimeRoot 'stop.request'
$launcherOut = Join-Path $runtimeRoot 'launcher.stdout.log'
$launcherErr = Join-Path $runtimeRoot 'launcher.stderr.log'
$envFile = Join-Path $runtimeRoot 'operator.env'
$leaseRoot = Join-Path $runtimeRoot 'leases'

function Get-InputPayload {
    param([switch]$Required)

    if (-not $Required) { return $null }

    $raw = [Console]::In.ReadToEnd()
    if ([string]::IsNullOrWhiteSpace($raw)) {
        throw 'SessionStart hook input was empty; refusing to create a operator lease.'
    }
    try {
        return $raw | ConvertFrom-Json -ErrorAction Stop
    } catch {
        throw "SessionStart hook input was invalid JSON; refusing to create a operator lease: $($_.Exception.Message)"
    }
}

function Get-FirstValue {
    param([object]$Object, [string[]]$Names)
    if (-not $Object) { return $null }
    foreach ($name in $Names) {
        $property = $Object.PSObject.Properties[$name]
        if ($property -and -not [string]::IsNullOrWhiteSpace([string]$property.Value)) {
            return [string]$property.Value
        }
    }
    return $null
}

function Get-ShortHash {
    param([Parameter(Mandatory = $true)][string]$Value)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($Value)
        $hash = $sha.ComputeHash($bytes)
        return ([System.BitConverter]::ToString($hash) -replace '-', '').Substring(0, 24).ToLowerInvariant()
    } finally {
        $sha.Dispose()
    }
}

function Get-OperatorProcessIdentity {
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [Parameter(Mandatory = $true)][string]$OperatorScript
    )
    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if (-not $process) {
        return [pscustomobject]@{
            Exists = $false; Verified = $true; IsOperator = $false
            Process = $null; ProcessName = ''; Reason = 'process_absent'
        }
    }
    $processName = [string]$process.ProcessName
    if ($processName -notmatch '(?i)^python(?:w|[0-9.]*)?$') {
        return [pscustomobject]@{
            Exists = $true; Verified = $true; IsOperator = $false
            Process = $process; ProcessName = $processName; Reason = 'non_python_process'
        }
    }
    try {
        $record = Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f $ProcessId) -ErrorAction Stop
    } catch {
        return [pscustomobject]@{
            Exists = $true; Verified = $false; IsOperator = $false
            Process = $process; ProcessName = $processName; Reason = 'command_line_unavailable'
        }
    }
    $commandLine = if ($record) { [string]$record.CommandLine } else { '' }
    if ([string]::IsNullOrWhiteSpace($commandLine)) {
        return [pscustomobject]@{
            Exists = $true; Verified = $false; IsOperator = $false
            Process = $process; ProcessName = $processName; Reason = 'command_line_unavailable'
        }
    }
    $expected = [System.IO.Path]::GetFullPath($OperatorScript).Replace('/', '\')
    $observed = $commandLine.Replace('/', '\')
    $matches = $observed.IndexOf($expected, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
    return [pscustomobject]@{
        Exists = $true; Verified = $true; IsOperator = $matches
        Process = $process; ProcessName = $processName
        Reason = $(if ($matches) { 'exact_operator_script' } else { 'different_python_command' })
    }
}

$mutexProjectRoot = [System.IO.Path]::GetFullPath($projectRoot)
$mutexPathRoot = [System.IO.Path]::GetPathRoot($mutexProjectRoot)
if (-not $mutexProjectRoot.Equals($mutexPathRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    $mutexProjectRoot = $mutexProjectRoot.TrimEnd('\', '/')
}
$mutexProjectRoot = $mutexProjectRoot.ToUpperInvariant()
$mutexIdentity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
if ($null -eq $mutexIdentity -or $null -eq $mutexIdentity.User -or
    [string]::IsNullOrWhiteSpace([string]$mutexIdentity.User.Value)) {
    throw 'Could not resolve the Windows user SID for the operator lifecycle mutex.'
}
$lifecycleMutexMaterial = [string]$mutexIdentity.User.Value + "`n" + $mutexProjectRoot
$lifecycleMutexName = 'Global\FeishuCodexOperator-Lifecycle-' + (Get-ShortHash $lifecycleMutexMaterial)
$lifecycleMutex = [System.Threading.Mutex]::new($false, $lifecycleMutexName)
$lifecycleMutexOwned = $false
try {
    try {
        $mutexWaitMilliseconds = if ($DetachedLaunch) { 8000 } else { 0 }
        $lifecycleMutexOwned = $lifecycleMutex.WaitOne($mutexWaitMilliseconds)
    } catch [System.Threading.AbandonedMutexException] {
        $lifecycleMutexOwned = $true
    }
    if (-not $lifecycleMutexOwned) {
        throw 'Feishu operator lifecycle is locked by an external verification or another start operation.'
    }

New-Item -ItemType Directory -Force -Path $runtimeRoot, $leaseRoot | Out-Null

function Assert-OperatorRuntimeManifest {
    $expectedFiles = @(
        'operator_main.py',
        'routing_cli.py',
        'operator_core/__init__.py',
        'operator_core/config.py',
        'operator_core/app_server_catalog.py',
        'operator_core/app_server.py',
        'operator_core/dispatch.py',
        'operator_core/telemetry.py',
        'operator_core/final_callback.py',
        'operator_core/lark.py',
        'operator_core/rate_limits.py',
        'operator_core/responder_observer.py',
        'operator_core/beeper_relay.py',
        'operator_core/runtime.py',
        'operator_core/state.py'
    )
    if (-not (Test-Path -LiteralPath $runtimeManifest -PathType Leaf)) {
        throw "Feishu operator runtime manifest is missing: $runtimeManifest"
    }
    try {
        $manifest = Get-Content -LiteralPath $runtimeManifest -Raw | ConvertFrom-Json -ErrorAction Stop
    } catch {
        throw "Feishu operator runtime manifest is invalid: $($_.Exception.Message)"
    }
    if ([int]$manifest.schema_version -ne $OPERATOR_RUNTIME_MANIFEST_SCHEMA) {
        throw "Unsupported Feishu operator runtime manifest schema: $($manifest.schema_version)"
    }
    $installedConfigPath = Join-Path $runtimeRoot 'operator_core\config.py'
    $installedConfig = Get-Content -LiteralPath $installedConfigPath -Raw
    if ($installedConfig -notmatch 'OPERATOR_VERSION\s*=\s*["'']([^"'']+)["'']') {
        throw 'Installed Feishu operator runtime has no readable OPERATOR_VERSION marker.'
    }
    $installedVersion = [string]$Matches[1]
    if ([string]$manifest.operator_version -ne $installedVersion) {
        throw "Feishu operator runtime manifest version '$($manifest.operator_version)' does not match installed runtime '$installedVersion'."
    }
    if (-not $manifest.code_files) {
        throw 'Feishu operator runtime manifest has no code-file hashes.'
    }
    $manifestProperties = @($manifest.code_files.PSObject.Properties)
    if ($manifestProperties.Count -ne $expectedFiles.Count) {
        throw 'Feishu operator runtime manifest code-file set is incomplete or contains unexpected entries.'
    }
    foreach ($relative in $expectedFiles) {
        $property = $manifest.code_files.PSObject.Properties[$relative]
        if (-not $property -or [string]$property.Value -notmatch '^[a-f0-9]{64}$') {
            throw "Feishu operator runtime manifest has no valid hash for $relative"
        }
        $target = Join-Path $runtimeRoot ($relative.Replace('/', '\'))
        if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {
            throw "Feishu operator installed code is missing: $target"
        }
        $actual = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne [string]$property.Value) {
            throw "Feishu operator installed code failed its runtime manifest check: $relative"
        }
    }
    foreach ($hook in @(
        @{ Path = $startHookScript; Hash = [string]$manifest.start_hook_sha256; Name = 'start hook' },
        @{ Path = $stopHookScript; Hash = [string]$manifest.stop_hook_sha256; Name = 'stop hook' }
    )) {
        if ($hook.Hash -notmatch '^[a-f0-9]{64}$' -or -not (Test-Path -LiteralPath $hook.Path -PathType Leaf)) {
            throw "Feishu operator runtime manifest has no valid $($hook.Name) binding."
        }
        $actual = (Get-FileHash -LiteralPath $hook.Path -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne $hook.Hash) {
            throw "Feishu operator $($hook.Name) failed its runtime manifest check."
        }
    }
}

function Assert-OperatorEnvSemantics {
    param([Parameter(Mandatory = $true)][System.Collections.IDictionary]$Values)

    $booleanValues = @('0', '1', 'false', 'true', 'no', 'yes', 'off', 'on')
    foreach ($name in @(
        'CODEX_OPERATOR_DOWNLOAD_RESOURCES'
    )) {
        if (-not $Values.Contains($name)) { continue }
        if (([string]$Values[$name]).Trim().ToLowerInvariant() -notin $booleanValues) {
            throw "Operator environment value for $name is not an explicit boolean."
        }
    }

    $enumSpecs = @(
        [pscustomobject]@{ Name = 'CODEX_OPERATOR_ACCESS_MODE'; Values = @('locked', 'compat') },
        [pscustomobject]@{ Name = 'CODEX_OPERATOR_LIFECYCLE_MODE'; Values = @('hooks', 'manual') },
        [pscustomobject]@{ Name = 'CODEX_OPERATOR_REPLY_FORMAT'; Values = @('text', 'markdown') },
        [pscustomobject]@{ Name = 'CODEX_OPERATOR_BEEPER_MODEL'; Values = @('', 'gpt-5.3-codex-spark', 'gpt-5.6-luna') },
        [pscustomobject]@{ Name = 'CODEX_OPERATOR_BEEPER_REASONING_EFFORT'; Values = @('', 'low', 'high') },
        [pscustomobject]@{ Name = 'CODEX_OPERATOR_BEEPER_PROMPT_LANGUAGE'; Values = @('', 'en', 'zh-cn') }
    )
    foreach ($spec in $enumSpecs) {
        if (-not $Values.Contains($spec.Name)) { continue }
        if (([string]$Values[$spec.Name]).Trim().ToLowerInvariant() -notin $spec.Values) {
            throw "Operator environment value for $($spec.Name) is not one of: $($spec.Values -join ', ')."
        }
    }
    $reasoningOverride = if ($Values.Contains('CODEX_OPERATOR_BEEPER_REASONING_EFFORT')) {
        ([string]$Values['CODEX_OPERATOR_BEEPER_REASONING_EFFORT']).Trim().ToLowerInvariant()
    } else { '' }
    $modelOverride = if ($Values.Contains('CODEX_OPERATOR_BEEPER_MODEL')) {
        ([string]$Values['CODEX_OPERATOR_BEEPER_MODEL']).Trim().ToLowerInvariant()
    } else { '' }
    if ($reasoningOverride.Length -gt 0 -and $modelOverride -cne 'gpt-5.3-codex-spark') {
        throw 'Operator Beeper reasoning override requires an explicit Spark model override.'
    }

    $integerSpecs = @(
        [pscustomobject]@{ Name = 'CODEX_OPERATOR_EVENT_READY_TIMEOUT'; Minimum = 5L; Maximum = 120L },
        [pscustomobject]@{ Name = 'CODEX_OPERATOR_UNKNOWN_STATUS_TIMEOUT'; Minimum = 30L; Maximum = 86400L },
        [pscustomobject]@{ Name = 'CODEX_OPERATOR_CALLBACK_GRACE_SECONDS'; Minimum = 10L; Maximum = 30L },
        [pscustomobject]@{ Name = 'CODEX_OPERATOR_CALLBACK_RETENTION_HOURS'; Minimum = 1L; Maximum = 8760L },
        [pscustomobject]@{ Name = 'CODEX_OPERATOR_APP_SERVER_TIMEOUT'; Minimum = 5L; Maximum = 120L },
        [pscustomobject]@{ Name = 'CODEX_OPERATOR_MAX_REPLY_CHARS'; Minimum = 500L; Maximum = 12000L },
        [pscustomobject]@{ Name = 'CODEX_OPERATOR_MAX_CONCURRENT_TURNS'; Minimum = 1L; Maximum = 4L },
        [pscustomobject]@{ Name = 'CODEX_OPERATOR_RECONNECT_MAX_SECONDS'; Minimum = 5L; Maximum = 300L },
        [pscustomobject]@{ Name = 'CODEX_OPERATOR_MAX_MESSAGE_RESOURCES'; Minimum = 1L; Maximum = 20L },
        [pscustomobject]@{ Name = 'CODEX_OPERATOR_MAX_IMAGE_BYTES'; Minimum = 1024L; Maximum = [long]::MaxValue },
        [pscustomobject]@{ Name = 'CODEX_OPERATOR_MAX_FILE_BYTES'; Minimum = 1024L; Maximum = [long]::MaxValue },
        [pscustomobject]@{ Name = 'CODEX_OPERATOR_MAX_TOTAL_RESOURCE_BYTES'; Minimum = 1024L; Maximum = [long]::MaxValue },
        [pscustomobject]@{ Name = 'CODEX_OPERATOR_RESOURCE_DOWNLOAD_TIMEOUT'; Minimum = 10L; Maximum = 1800L },
        [pscustomobject]@{ Name = 'CODEX_OPERATOR_RESOURCE_TTL_HOURS'; Minimum = 1L; Maximum = 8760L },
        [pscustomobject]@{ Name = 'CODEX_OPERATOR_LIFECYCLE_GRACE_SECONDS'; Minimum = 15L; Maximum = 3600L }
    )
    foreach ($spec in $integerSpecs) {
        if (-not $Values.Contains($spec.Name)) { continue }
        $parsed = 0L
        $raw = ([string]$Values[$spec.Name]).Trim()
        if ($raw -cnotmatch '^-?[0-9]+$' -or -not [long]::TryParse(
            $raw,
            [System.Globalization.NumberStyles]::Integer,
            [System.Globalization.CultureInfo]::InvariantCulture,
            [ref]$parsed
        )) {
            throw "Operator environment value for $($spec.Name) is not an integer."
        }
        if ($parsed -lt $spec.Minimum -or $parsed -gt $spec.Maximum) {
            throw "Operator environment value for $($spec.Name) is outside its supported range."
        }
    }
    if (-not $Values.Contains('CODEX_OPERATOR_BEEPER_THREAD_ID') -or
        ([string]$Values['CODEX_OPERATOR_BEEPER_THREAD_ID']).Trim() -cnotmatch
            '^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$') {
        throw 'Operator environment must contain an exact CODEX_OPERATOR_BEEPER_THREAD_ID task UUID.'
    }
}

function Test-DesktopCodexProcessRecord {
    param([object]$Record)

    if (-not $Record -or [string]$Record.Name -ine 'codex.exe' -or
        [string]::IsNullOrWhiteSpace([string]$Record.ExecutablePath) -or
        [string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        return $false
    }
    try {
        $desktopBinRoot = [System.IO.Path]::GetFullPath(
            (Join-Path $env:LOCALAPPDATA 'OpenAI\Codex\bin')
        ).TrimEnd('\', '/') + '\'
        $executablePath = [System.IO.Path]::GetFullPath([string]$Record.ExecutablePath)
        return $executablePath.StartsWith(
            $desktopBinRoot,
            [System.StringComparison]::OrdinalIgnoreCase
        )
    } catch {
        return $false
    }
}

function Test-DesktopCodexHostProcessId {
    param([Parameter(Mandatory = $true)][int]$ProcessId)
    try {
        $record = Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f $ProcessId) -ErrorAction Stop
        return (Test-DesktopCodexProcessRecord $record)
    } catch {
        return $false
    }
}

function Get-DesktopCodexHostProcessId {
    $ancestorPid = $PID
    for ($depth = 0; $depth -lt 8; $depth += 1) {
        try {
            $record = Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f $ancestorPid) -ErrorAction Stop
        } catch {
            break
        }
        if (-not $record -or [int]$record.ParentProcessId -le 0) { break }
        $ancestorPid = [int]$record.ParentProcessId
        try {
            $ancestor = Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f $ancestorPid) -ErrorAction Stop
        } catch {
            break
        }
        if (Test-DesktopCodexProcessRecord $ancestor) {
            return $ancestorPid
        }
    }
    throw 'SessionStart did not originate from the current Codex Desktop process tree.'
}

function Get-LeasePath {
    param([Parameter(Mandatory = $true)][string]$ExactLeaseId)
    return (Join-Path $leaseRoot ("{0}.json" -f $ExactLeaseId))
}

function Read-Lease {
    param([Parameter(Mandatory = $true)][string]$ExactLeaseId)
    $leasePath = Get-LeasePath $ExactLeaseId
    if (-not (Test-Path -LiteralPath $leasePath -PathType Leaf)) { return $null }
    try {
        return (Get-Content -LiteralPath $leasePath -Raw | ConvertFrom-Json -ErrorAction Stop)
    } catch {
        return $null
    }
}

function Write-LeaseObject {
    param([Parameter(Mandatory = $true)][object]$Lease)
    $leasePath = Get-LeasePath ([string]$Lease.lease_id)
    $temporary = "$leasePath.tmp"
    $Lease | ConvertTo-Json | Set-Content -LiteralPath $temporary -Encoding utf8
    Move-Item -LiteralPath $temporary -Destination $leasePath -Force
}

function Set-LeaseLaunchState {
    param(
        [Parameter(Mandatory = $true)][string]$ExactLeaseId,
        [Parameter(Mandatory = $true)][ValidateSet('launching', 'running', 'failed')][string]$LaunchState,
        [switch]$Release
    )
    $lease = Read-Lease $ExactLeaseId
    if (-not $lease -or [string]$lease.lease_id -cne $ExactLeaseId) { return }
    $lease | Add-Member -MemberType NoteProperty -Name launch_status -Value $LaunchState -Force
    if ($Release -and [string]$lease.status -ceq 'active') {
        $lease | Add-Member -MemberType NoteProperty -Name status -Value 'released' -Force
    }
    $lease | Add-Member -MemberType NoteProperty -Name updated_at -Value ([DateTimeOffset]::UtcNow.ToUnixTimeSeconds()) -Force
    Write-LeaseObject $lease
}

function Assert-DetachedLaunchLease {
    param([Parameter(Mandatory = $true)][string]$ExactLeaseId)
    $lease = Read-Lease $ExactLeaseId
    $launchStatus = if ($lease) { [string]$lease.launch_status } else { '' }
    if (-not $lease -or [int]$lease.version -ne 1 -or
        [string]$lease.lease_id -cne $ExactLeaseId -or
        [string]$lease.status -cne 'active' -or
        $launchStatus -notin @('reserved', 'running')) {
        throw 'Detached launch lease is absent or no longer active.'
    }
    $launchDeadline = 0L
    if (-not [long]::TryParse([string]$lease.launch_deadline_at, [ref]$launchDeadline) -or
        [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() -gt $launchDeadline) {
        throw 'Detached launch lease has expired.'
    }
    $source = [string]$lease.source
    $hostPid = 0
    [void][int]::TryParse([string]$lease.host_pid, [ref]$hostPid)
    if ($source -ceq 'hook') {
        if ($hostPid -le 0 -or -not (Test-DesktopCodexHostProcessId $hostPid)) {
            throw 'Detached launch lease is not bound to a live Codex Desktop host.'
        }
    } elseif ($source -ceq 'manual') {
        if ($hostPid -ne 0) {
            throw 'Manual detached launch lease has an unexpected host process.'
        }
    } else {
        throw 'Detached launch lease has an unsupported source.'
    }
    return $lease
}

function Write-Lease {
    param([object]$HookPayload)
    $eventName = Get-FirstValue $HookPayload @('hook_event_name', 'hookEventName', 'event_name', 'eventName')
    $sessionId = Get-FirstValue $HookPayload @('session_id', 'sessionId')
    $source = if ($eventName -match '^SessionStart$' -and $sessionId) { 'hook' } else { 'manual' }
    $identity = if ($sessionId) { $sessionId } else { "manual-$PID" }
    $leaseId = Get-ShortHash $identity
    $hostPid = if ($source -eq 'hook') { Get-DesktopCodexHostProcessId } else { 0 }
    $now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    $existingLease = Read-Lease $leaseId
    if ($existingLease -and [string]$existingLease.status -ceq 'active' -and
        [string]$existingLease.source -ceq $source -and
        [int]$existingLease.host_pid -eq $hostPid -and
        [string]$existingLease.launch_status -in @('reserved', 'launching', 'running') -and
        ($source -ceq 'manual' -or (Test-DesktopCodexHostProcessId $hostPid))) {
        return $leaseId
    }
    $lease = [pscustomobject]@{
        version = 1
        lease_id = $leaseId
        source = $source
        status = 'active'
        host_pid = $hostPid
        launch_status = 'reserved'
        launch_deadline_at = $now + $DETACHED_LAUNCH_TIMEOUT_SECONDS
        updated_at = $now
    }
    Write-LeaseObject $lease
    return $leaseId
}

function Start-DetachedLaunchHelper {
    param([Parameter(Mandatory = $true)][string]$ExactLeaseId)

    $windowsPowerShell = Join-Path (
        [Environment]::GetFolderPath([Environment+SpecialFolder]::System)
    ) 'WindowsPowerShell\v1.0\powershell.exe'
    if (-not (Test-Path -LiteralPath $windowsPowerShell -PathType Leaf)) {
        throw 'Windows PowerShell is unavailable for detached Operator launch.'
    }
    # Both paths are operating-system paths and cannot contain a double quote.
    # LeaseId is constrained to 24 lowercase hexadecimal characters above.
    $commandLine = '"{0}" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{1}" -DetachedLaunch -LeaseId {2}' -f `
        $windowsPowerShell, $startHookScript, $ExactLeaseId
    $CREATE_BREAKAWAY_FROM_JOB = [uint32]0x01000000
    $CREATE_NO_WINDOW = [uint32]0x08000000
    $startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{
        CreateFlags = [uint32]($CREATE_BREAKAWAY_FROM_JOB -bor $CREATE_NO_WINDOW)
        ShowWindow = [uint16]0
    }
    $result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
        CommandLine = $commandLine
        CurrentDirectory = $projectRoot
        ProcessStartupInformation = $startup
    } -ErrorAction Stop
    if (-not $result -or [int]$result.ReturnValue -ne 0 -or [int]$result.ProcessId -le 0) {
        $returnValue = if ($result) { [int]$result.ReturnValue } else { -1 }
        throw "Detached Operator helper creation failed with WMI return value $returnValue."
    }
    return [int]$result.ProcessId
}

if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
    throw "Operator environment is missing: $envFile"
}
$operatorEnvValues = [ordered]@{}
$operatorEnvNames = [System.Collections.Generic.HashSet[string]]::new(
    [System.StringComparer]::OrdinalIgnoreCase
)
$operatorEnvLineNumber = 0
foreach ($rawLine in @(Get-Content -LiteralPath $envFile -ErrorAction Stop)) {
    $operatorEnvLineNumber += 1
    $line = $rawLine.Trim()
    if ($line.Length -eq 0 -or $line.StartsWith('#')) { continue }
    if ($line -match '\x00') {
        throw "Operator environment line $operatorEnvLineNumber contains a NUL byte."
    }
    $parts = $line -split '=', 2
    if ($parts.Count -ne 2) {
        throw "Operator environment line $operatorEnvLineNumber is not NAME=VALUE."
    }
    $name = $parts[0].Trim()
    if ($name -cnotmatch '^CODEX_OPERATOR_[A-Z0-9_]+$') {
        throw "Operator environment line $operatorEnvLineNumber has an unsupported key."
    }
    if (-not $operatorEnvNames.Add($name)) {
        throw "Operator environment contains a duplicate key at line ${operatorEnvLineNumber}: $name"
    }
    $operatorEnvValues[$name] = $parts[1].Trim()
}
Assert-OperatorEnvSemantics -Values $operatorEnvValues

# The installed file is the sole source for the Operator namespace. Clearing
# inherited values prevents a parent-level legacy `compat` mode from overriding
# a missing access key that must fall back to locked.
foreach ($existingOperatorEnv in @(Get-ChildItem Env:)) {
    if ($existingOperatorEnv.Name -like 'CODEX_OPERATOR_*') {
        Remove-Item -LiteralPath ("Env:{0}" -f $existingOperatorEnv.Name)
    }
}
foreach ($entry in $operatorEnvValues.GetEnumerator()) {
    Set-Item -LiteralPath ("Env:{0}" -f $entry.Key) -Value ([string]$entry.Value)
}

$env:CODEX_OPERATOR_PROJECT_ROOT = $projectRoot
$env:CODEX_OPERATOR_RUNTIME_DIR = $runtimeRoot
$hookPayload = Get-InputPayload -Required:$HookInvocation
if ($HookInvocation) {
    $eventName = Get-FirstValue $hookPayload @('hook_event_name', 'hookEventName', 'event_name', 'eventName')
    $sessionId = Get-FirstValue $hookPayload @('session_id', 'sessionId')
    if ($eventName -ne 'SessionStart') {
        throw "Expected a SessionStart hook payload, received '$eventName'; refusing to create a operator lease."
    }
    if (-not $sessionId) {
        throw 'SessionStart hook input had no session id; refusing to create a operator lease.'
    }
}
Assert-OperatorRuntimeManifest
$existingOperatorPid = 0
if (Test-Path -LiteralPath $pidFile) {
    $existingPidText = (Get-Content -LiteralPath $pidFile -Raw).Trim()
    $existingPid = 0
    if ([int]::TryParse($existingPidText, [ref]$existingPid)) {
        $identity = Get-OperatorProcessIdentity -ProcessId $existingPid -OperatorScript $operatorScript
        if ($identity.Exists) {
            if (-not $identity.Verified) {
                throw "Operator PID $existingPid exists, but its Python command line could not be verified; refusing to start a second runtime."
            }
            if ($identity.IsOperator) {
                $existingOperatorPid = $existingPid
            } else {
                Remove-Item -LiteralPath $pidFile -Force -ErrorAction Stop
                Write-LifecycleStatus "Removed stale Operator PID file; PID $existingPid belongs to non-Operator process $($identity.ProcessName)."
            }
        } else {
            Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
        }
    } else {
        Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    }
}

if ($DetachedLaunch) {
    $detachedLease = Assert-DetachedLaunchLease $LeaseId
    if ([string]$detachedLease.launch_status -ceq 'running' -and $existingOperatorPid -le 0) {
        Write-LifecycleStatus "Feishu operator lease $LeaseId already has a detached Operator launch in progress."
        exit 0
    }
} else {
    $LeaseId = Write-Lease $hookPayload
}
if ($existingOperatorPid -gt 0) {
    Set-LeaseLaunchState -ExactLeaseId $LeaseId -LaunchState running
    Write-LifecycleStatus "Feishu operator lease $LeaseId active; runtime already running (PID $existingOperatorPid)."
    exit 0
}

Remove-Item -LiteralPath $stopFile -Force -ErrorAction SilentlyContinue

function Update-ProcessPathFromEnvironment {
    $discoveredPaths = @()
    $pythonPrograms = Join-Path $env:LOCALAPPDATA 'Programs\Python'
    if (Test-Path -LiteralPath $pythonPrograms -PathType Container) {
        $launcher = Join-Path $pythonPrograms 'Launcher'
        if (Test-Path -LiteralPath $launcher -PathType Container) { $discoveredPaths += $launcher }
        $discoveredPaths += @(
            Get-ChildItem -LiteralPath $pythonPrograms -Directory -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -like 'Python*' } |
                Sort-Object Name -Descending |
                ForEach-Object { $_.FullName }
        )
    }
    $seen = @{}
    $segments = New-Object System.Collections.Generic.List[string]
    $pathSources = @(
        [Environment]::GetEnvironmentVariable('Path', 'Machine'),
        [Environment]::GetEnvironmentVariable('Path', 'User'),
        $env:Path
    ) + $discoveredPaths
    foreach ($rawPath in $pathSources) {
        foreach ($segment in @($rawPath -split ';')) {
            $trimmed = $segment.Trim().Trim('"')
            $key = $trimmed.ToLowerInvariant()
            if ($trimmed -and -not $seen.ContainsKey($key)) {
                $seen[$key] = $true
                $segments.Add($trimmed)
            }
        }
    }
    $env:Path = $segments -join ';'
}

function Get-UsablePython {
    Update-ProcessPathFromEnvironment
    $candidates = @()
    $venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
        $candidates += [pscustomobject]@{ Path = $venvPython; Prefix = @() }
    }
    foreach ($command in @(Get-Command python.exe -All -ErrorAction SilentlyContinue)) {
        $candidates += [pscustomobject]@{ Path = [string]$command.Source; Prefix = @() }
    }
    foreach ($command in @(Get-Command py.exe -All -ErrorAction SilentlyContinue)) {
        $candidates += [pscustomobject]@{ Path = [string]$command.Source; Prefix = @('-3') }
    }

    $seen = @{}
    foreach ($candidate in $candidates) {
        if (-not $candidate.Path) { continue }
        $key = ((@($candidate.Path) + @($candidate.Prefix)) -join '|').ToLowerInvariant()
        if ($seen.ContainsKey($key)) { continue }
        $seen[$key] = $true
        try {
            $global:LASTEXITCODE = 0
            $versionOutput = (& $candidate.Path @($candidate.Prefix) --version 2>&1 | Out-String).Trim()
            if ($LASTEXITCODE -ne 0 -or $versionOutput -notmatch '(?i)Python\s+(\d+)\.(\d+)') {
                continue
            }
            $version = [version]("{0}.{1}" -f $Matches[1], $Matches[2])
            if ($version -ge [version]'3.10') { return $candidate }
        } catch {
            continue
        }
    }
    throw 'Python 3.10+ was not found for the Feishu operator.'
}

if (-not $DetachedLaunch) {
    $currentLaunchLease = Read-Lease $LeaseId
    $helperPid = 0
    if ([string]$currentLaunchLease.launch_status -ceq 'reserved') {
        try {
            $helperPid = Start-DetachedLaunchHelper $LeaseId
        } catch {
            Set-LeaseLaunchState -ExactLeaseId $LeaseId -LaunchState failed -Release
            throw
        }
    } elseif ([string]$currentLaunchLease.launch_status -cne 'running') {
        Set-LeaseLaunchState -ExactLeaseId $LeaseId -LaunchState failed -Release
        throw 'Operator lease is not in a valid detached-launch state.'
    }

    # The helper must acquire this same mutex before it can revalidate the
    # lease and launch Python. Release it before waiting for the exact Operator.
    if ($lifecycleMutexOwned) {
        try { $lifecycleMutex.ReleaseMutex() } catch [System.ApplicationException] { }
        $lifecycleMutexOwned = $false
    }

    $launchDeadline = (Get-Date).AddSeconds($DETACHED_LAUNCH_TIMEOUT_SECONDS + 1)
    while ((Get-Date) -lt $launchDeadline) {
        $launchLease = Read-Lease $LeaseId
        if (-not $launchLease -or [string]$launchLease.status -cne 'active' -or
            [string]$launchLease.launch_status -ceq 'failed') {
            throw "Detached Operator helper $helperPid did not retain an active launch lease."
        }
        if (Test-Path -LiteralPath $pidFile -PathType Leaf) {
            $launchedPid = 0
            $launchedPidText = (Get-Content -LiteralPath $pidFile -Raw).Trim()
            if ([int]::TryParse($launchedPidText, [ref]$launchedPid) -and $launchedPid -gt 0) {
                $launchedIdentity = Get-OperatorProcessIdentity -ProcessId $launchedPid -OperatorScript $operatorScript
                if ($launchedIdentity.Exists -and $launchedIdentity.Verified -and $launchedIdentity.IsOperator) {
                    Write-LifecycleStatus "Activated Feishu operator lease $LeaseId; Operator PID $launchedPid."
                    exit 0
                }
            }
        }
        Start-Sleep -Milliseconds 200
    }

    try {
        try {
            $lifecycleMutexOwned = $lifecycleMutex.WaitOne(1000)
        } catch [System.Threading.AbandonedMutexException] {
            $lifecycleMutexOwned = $true
        }
        if ($lifecycleMutexOwned) {
            Set-LeaseLaunchState -ExactLeaseId $LeaseId -LaunchState failed -Release
        }
    } catch {
    }
    throw "Detached Operator helper $helperPid did not produce a verified Operator before its bounded deadline."
}

# This is the only branch that starts Python. It is reached only by the
# WMI-created helper after the exact lease, manifest and environment revalidate.
Set-LeaseLaunchState -ExactLeaseId $LeaseId -LaunchState launching
$pythonRuntime = Get-UsablePython
$python = $pythonRuntime.Path
$arguments = @($pythonRuntime.Prefix) + @($operatorScript)
$process = Start-Process `
    -FilePath $python `
    -ArgumentList $arguments `
    -WorkingDirectory $projectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $launcherOut `
    -RedirectStandardError $launcherErr `
    -PassThru

$startedIdentity = Get-OperatorProcessIdentity -ProcessId $process.Id -OperatorScript $operatorScript
if (-not $startedIdentity.Exists -or -not $startedIdentity.Verified -or -not $startedIdentity.IsOperator) {
    throw 'Detached Operator process did not match the exact installed operator_main.py identity.'
}
Set-LeaseLaunchState -ExactLeaseId $LeaseId -LaunchState running
Write-LifecycleStatus "Detached Feishu operator lease $LeaseId launched Operator PID $($process.Id)."
} catch {
    if ($DetachedLaunch -and -not [string]::IsNullOrWhiteSpace($LeaseId)) {
        try { Set-LeaseLaunchState -ExactLeaseId $LeaseId -LaunchState failed -Release } catch { }
    }
    throw
} finally {
    if ($lifecycleMutexOwned) {
        try { $lifecycleMutex.ReleaseMutex() } catch [System.ApplicationException] { }
    }
    $lifecycleMutex.Dispose()
}
