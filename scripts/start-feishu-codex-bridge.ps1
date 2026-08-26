[CmdletBinding()]
param(
    [switch]$HookInvocation
)

$ErrorActionPreference = 'Stop'
$BRIDGE_RUNTIME_MANIFEST_SCHEMA = 1

if ($env:CODEX_BRIDGE_CHILD -eq '1') {
    Write-Output 'Skipping Feishu bridge lifecycle hook inside its Codex child.'
    exit 0
}

$startHookScript = $MyInvocation.MyCommand.Path
$hooksRoot = Split-Path -Parent $startHookScript
$projectRoot = Split-Path -Parent (Split-Path -Parent $hooksRoot)
$runtimeRoot = Join-Path $projectRoot '.codex\feishu-bridge'
$bridgeScript = Join-Path $runtimeRoot 'bridge.py'
$runtimeManifest = Join-Path $runtimeRoot 'runtime-manifest.json'
$stopHookScript = Join-Path $hooksRoot 'stop-feishu-codex-bridge.ps1'
$pidFile = Join-Path $runtimeRoot 'bridge.pid'
$stopFile = Join-Path $runtimeRoot 'stop.request'
$launcherOut = Join-Path $runtimeRoot 'launcher.stdout.log'
$launcherErr = Join-Path $runtimeRoot 'launcher.stderr.log'
$envFile = Join-Path $runtimeRoot 'bridge.env'
$leaseRoot = Join-Path $runtimeRoot 'leases'

function Get-InputPayload {
    param([switch]$Required)

    if (-not $Required) { return $null }

    $raw = [Console]::In.ReadToEnd()
    if ([string]::IsNullOrWhiteSpace($raw)) {
        throw 'SessionStart hook input was empty; refusing to create a bridge lease.'
    }
    try {
        return $raw | ConvertFrom-Json -ErrorAction Stop
    } catch {
        throw "SessionStart hook input was invalid JSON; refusing to create a bridge lease: $($_.Exception.Message)"
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

function Get-BridgeProcessIdentity {
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [Parameter(Mandatory = $true)][string]$BridgeScript
    )
    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if (-not $process) {
        return [pscustomobject]@{
            Exists = $false; Verified = $true; IsBridge = $false
            Process = $null; ProcessName = ''; Reason = 'process_absent'
        }
    }
    $processName = [string]$process.ProcessName
    if ($processName -notmatch '(?i)^python(?:w|[0-9.]*)?$') {
        return [pscustomobject]@{
            Exists = $true; Verified = $true; IsBridge = $false
            Process = $process; ProcessName = $processName; Reason = 'non_python_process'
        }
    }
    try {
        $record = Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f $ProcessId) -ErrorAction Stop
    } catch {
        return [pscustomobject]@{
            Exists = $true; Verified = $false; IsBridge = $false
            Process = $process; ProcessName = $processName; Reason = 'command_line_unavailable'
        }
    }
    $commandLine = if ($record) { [string]$record.CommandLine } else { '' }
    if ([string]::IsNullOrWhiteSpace($commandLine)) {
        return [pscustomobject]@{
            Exists = $true; Verified = $false; IsBridge = $false
            Process = $process; ProcessName = $processName; Reason = 'command_line_unavailable'
        }
    }
    $expected = [System.IO.Path]::GetFullPath($BridgeScript).Replace('/', '\')
    $observed = $commandLine.Replace('/', '\')
    $matches = $observed.IndexOf($expected, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
    return [pscustomobject]@{
        Exists = $true; Verified = $true; IsBridge = $matches
        Process = $process; ProcessName = $processName
        Reason = $(if ($matches) { 'exact_bridge_script' } else { 'different_python_command' })
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
    throw 'Could not resolve the Windows user SID for the bridge lifecycle mutex.'
}
$lifecycleMutexMaterial = [string]$mutexIdentity.User.Value + "`n" + $mutexProjectRoot
$lifecycleMutexName = 'Global\FeishuCodexBridge-Lifecycle-' + (Get-ShortHash $lifecycleMutexMaterial)
$lifecycleMutex = [System.Threading.Mutex]::new($false, $lifecycleMutexName)
$lifecycleMutexOwned = $false
try {
    try {
        $lifecycleMutexOwned = $lifecycleMutex.WaitOne(0)
    } catch [System.Threading.AbandonedMutexException] {
        $lifecycleMutexOwned = $true
    }
    if (-not $lifecycleMutexOwned) {
        throw 'Feishu bridge lifecycle is locked by an external verification or another start operation.'
    }

New-Item -ItemType Directory -Force -Path $runtimeRoot, $leaseRoot | Out-Null

function Assert-BridgeRuntimeManifest {
    $expectedFiles = @(
        'bridge.py',
        'router_queue.py',
        'bridge_core/__init__.py',
        'bridge_core/config.py',
        'bridge_core/codex_client.py',
        'bridge_core/desktop_router.py',
        'bridge_core/lark.py',
        'bridge_core/project_routing.py',
        'bridge_core/runtime.py',
        'bridge_core/state.py'
    )
    if (-not (Test-Path -LiteralPath $runtimeManifest -PathType Leaf)) {
        throw "Feishu bridge runtime manifest is missing: $runtimeManifest"
    }
    try {
        $manifest = Get-Content -LiteralPath $runtimeManifest -Raw | ConvertFrom-Json -ErrorAction Stop
    } catch {
        throw "Feishu bridge runtime manifest is invalid: $($_.Exception.Message)"
    }
    if ([int]$manifest.schema_version -ne $BRIDGE_RUNTIME_MANIFEST_SCHEMA) {
        throw "Unsupported Feishu bridge runtime manifest schema: $($manifest.schema_version)"
    }
    $installedConfigPath = Join-Path $runtimeRoot 'bridge_core\config.py'
    $installedConfig = Get-Content -LiteralPath $installedConfigPath -Raw
    if ($installedConfig -notmatch 'BRIDGE_VERSION\s*=\s*["'']([^"'']+)["'']') {
        throw 'Installed Feishu bridge runtime has no readable BRIDGE_VERSION marker.'
    }
    $installedVersion = [string]$Matches[1]
    if ([string]$manifest.bridge_version -ne $installedVersion) {
        throw "Feishu bridge runtime manifest version '$($manifest.bridge_version)' does not match installed runtime '$installedVersion'."
    }
    if (-not $manifest.code_files) {
        throw 'Feishu bridge runtime manifest has no code-file hashes.'
    }
    $manifestProperties = @($manifest.code_files.PSObject.Properties)
    if ($manifestProperties.Count -ne $expectedFiles.Count) {
        throw 'Feishu bridge runtime manifest code-file set is incomplete or contains unexpected entries.'
    }
    foreach ($relative in $expectedFiles) {
        $property = $manifest.code_files.PSObject.Properties[$relative]
        if (-not $property -or [string]$property.Value -notmatch '^[a-f0-9]{64}$') {
            throw "Feishu bridge runtime manifest has no valid hash for $relative"
        }
        $target = Join-Path $runtimeRoot ($relative.Replace('/', '\'))
        if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {
            throw "Feishu bridge installed code is missing: $target"
        }
        $actual = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne [string]$property.Value) {
            throw "Feishu bridge installed code failed its runtime manifest check: $relative"
        }
    }
    foreach ($hook in @(
        @{ Path = $startHookScript; Hash = [string]$manifest.start_hook_sha256; Name = 'start hook' },
        @{ Path = $stopHookScript; Hash = [string]$manifest.stop_hook_sha256; Name = 'stop hook' }
    )) {
        if ($hook.Hash -notmatch '^[a-f0-9]{64}$' -or -not (Test-Path -LiteralPath $hook.Path -PathType Leaf)) {
            throw "Feishu bridge runtime manifest has no valid $($hook.Name) binding."
        }
        $actual = (Get-FileHash -LiteralPath $hook.Path -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne $hook.Hash) {
            throw "Feishu bridge $($hook.Name) failed its runtime manifest check."
        }
    }
}

function Assert-BridgeEnvSemantics {
    param([Parameter(Mandatory = $true)][System.Collections.IDictionary]$Values)

    $booleanValues = @('0', '1', 'false', 'true', 'no', 'yes', 'off', 'on')
    foreach ($name in @(
        'CODEX_BRIDGE_ALLOW_PROJECT_CREATE',
        'CODEX_BRIDGE_DOWNLOAD_RESOURCES'
    )) {
        if (-not $Values.Contains($name)) { continue }
        if (([string]$Values[$name]).Trim().ToLowerInvariant() -notin $booleanValues) {
            throw "Bridge environment value for $name is not an explicit boolean."
        }
    }

    $enumSpecs = @(
        [pscustomobject]@{ Name = 'CODEX_BRIDGE_ACCESS_MODE'; Values = @('locked', 'compat') },
        [pscustomobject]@{ Name = 'CODEX_BRIDGE_LIFECYCLE_MODE'; Values = @('hooks', 'manual') },
        [pscustomobject]@{ Name = 'CODEX_BRIDGE_REPLY_FORMAT'; Values = @('text', 'markdown') }
    )
    foreach ($spec in $enumSpecs) {
        if (-not $Values.Contains($spec.Name)) { continue }
        if (([string]$Values[$spec.Name]).Trim().ToLowerInvariant() -notin $spec.Values) {
            throw "Bridge environment value for $($spec.Name) is not one of: $($spec.Values -join ', ')."
        }
    }

    $integerSpecs = @(
        [pscustomobject]@{ Name = 'CODEX_BRIDGE_EVENT_READY_TIMEOUT'; Minimum = 5L; Maximum = 120L },
        [pscustomobject]@{ Name = 'CODEX_BRIDGE_ROUTER_TIMEOUT'; Minimum = 30L; Maximum = 86400L },
        [pscustomobject]@{ Name = 'CODEX_BRIDGE_ROUTER_HEARTBEAT_TTL'; Minimum = 15L; Maximum = 3600L },
        [pscustomobject]@{ Name = 'CODEX_BRIDGE_ROUTER_CLAIM_TTL'; Minimum = 60L; Maximum = 86400L },
        [pscustomobject]@{ Name = 'CODEX_BRIDGE_ROUTER_RETENTION_HOURS'; Minimum = 1L; Maximum = 8760L },
        [pscustomobject]@{ Name = 'CODEX_BRIDGE_ROUTER_WAKE_TTL'; Minimum = 60L; Maximum = 900L },
        [pscustomobject]@{ Name = 'CODEX_BRIDGE_GATEWAY_SCHEDULER_TTL'; Minimum = 120L; Maximum = 3600L },
        [pscustomobject]@{ Name = 'CODEX_BRIDGE_ROUTER_GRACE_MAX_SECONDS'; Minimum = 0L; Maximum = 60L },
        [pscustomobject]@{ Name = 'CODEX_BRIDGE_MAX_REPLY_CHARS'; Minimum = 500L; Maximum = 12000L },
        [pscustomobject]@{ Name = 'CODEX_BRIDGE_MAX_CONCURRENT_TURNS'; Minimum = 1L; Maximum = 4L },
        [pscustomobject]@{ Name = 'CODEX_BRIDGE_RECONNECT_MAX_SECONDS'; Minimum = 5L; Maximum = 300L },
        [pscustomobject]@{ Name = 'CODEX_BRIDGE_MAX_MESSAGE_RESOURCES'; Minimum = 1L; Maximum = 20L },
        [pscustomobject]@{ Name = 'CODEX_BRIDGE_MAX_IMAGE_BYTES'; Minimum = 1024L; Maximum = [long]::MaxValue },
        [pscustomobject]@{ Name = 'CODEX_BRIDGE_MAX_FILE_BYTES'; Minimum = 1024L; Maximum = [long]::MaxValue },
        [pscustomobject]@{ Name = 'CODEX_BRIDGE_MAX_TOTAL_RESOURCE_BYTES'; Minimum = 1024L; Maximum = [long]::MaxValue },
        [pscustomobject]@{ Name = 'CODEX_BRIDGE_RESOURCE_DOWNLOAD_TIMEOUT'; Minimum = 10L; Maximum = 1800L },
        [pscustomobject]@{ Name = 'CODEX_BRIDGE_RESOURCE_TTL_HOURS'; Minimum = 1L; Maximum = 8760L },
        [pscustomobject]@{ Name = 'CODEX_BRIDGE_LIFECYCLE_GRACE_SECONDS'; Minimum = 15L; Maximum = 3600L }
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
            throw "Bridge environment value for $($spec.Name) is not an integer."
        }
        if ($parsed -lt $spec.Minimum -or $parsed -gt $spec.Maximum) {
            throw "Bridge environment value for $($spec.Name) is outside its supported range."
        }
    }
}

function Get-ParentProcessId {
    try {
        $current = Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f $PID)
        if ($current -and $current.ParentProcessId) { return [int]$current.ParentProcessId }
    } catch {
    }
    return 0
}

function Write-Lease {
    param([object]$HookPayload)
    $eventName = Get-FirstValue $HookPayload @('hook_event_name', 'hookEventName', 'event_name', 'eventName')
    $sessionId = Get-FirstValue $HookPayload @('session_id', 'sessionId')
    $source = if ($eventName -match '^SessionStart$' -and $sessionId) { 'hook' } else { 'manual' }
    $identity = if ($sessionId) { $sessionId } else { "manual-$PID" }
    $leaseId = Get-ShortHash $identity
    $leasePath = Join-Path $leaseRoot ("{0}.json" -f $leaseId)
    $temporary = "$leasePath.tmp"
    $hostPid = if ($source -eq 'hook') { Get-ParentProcessId } else { 0 }
    [pscustomobject]@{
        version = 1
        lease_id = $leaseId
        source = $source
        status = 'active'
        host_pid = $hostPid
        updated_at = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    } | ConvertTo-Json | Set-Content -LiteralPath $temporary -Encoding utf8
    Move-Item -LiteralPath $temporary -Destination $leasePath -Force
    return $leaseId
}

if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
    throw "Bridge environment is missing: $envFile"
}
$bridgeEnvValues = [ordered]@{}
$bridgeEnvNames = [System.Collections.Generic.HashSet[string]]::new(
    [System.StringComparer]::OrdinalIgnoreCase
)
$bridgeEnvLineNumber = 0
foreach ($rawLine in @(Get-Content -LiteralPath $envFile -ErrorAction Stop)) {
    $bridgeEnvLineNumber += 1
    $line = $rawLine.Trim()
    if ($line.Length -eq 0 -or $line.StartsWith('#')) { continue }
    if ($line -match '\x00') {
        throw "Bridge environment line $bridgeEnvLineNumber contains a NUL byte."
    }
    $parts = $line -split '=', 2
    if ($parts.Count -ne 2) {
        throw "Bridge environment line $bridgeEnvLineNumber is not NAME=VALUE."
    }
    $name = $parts[0].Trim()
    if ($name -cnotmatch '^CODEX_BRIDGE_[A-Z0-9_]+$') {
        throw "Bridge environment line $bridgeEnvLineNumber has an unsupported key."
    }
    if (-not $bridgeEnvNames.Add($name)) {
        throw "Bridge environment contains a duplicate key at line ${bridgeEnvLineNumber}: $name"
    }
    $bridgeEnvValues[$name] = $parts[1].Trim()
}
Assert-BridgeEnvSemantics -Values $bridgeEnvValues

# The installed file is the sole source for the Bridge namespace. Clearing
# inherited values prevents a parent-level legacy `compat` mode from overriding
# a missing access key that must fall back to locked.
foreach ($existingBridgeEnv in @(Get-ChildItem Env:)) {
    if ($existingBridgeEnv.Name -like 'CODEX_BRIDGE_*') {
        Remove-Item -LiteralPath ("Env:{0}" -f $existingBridgeEnv.Name)
    }
}
foreach ($entry in $bridgeEnvValues.GetEnumerator()) {
    Set-Item -LiteralPath ("Env:{0}" -f $entry.Key) -Value ([string]$entry.Value)
}

$env:CODEX_BRIDGE_PROJECT_ROOT = $projectRoot
$env:CODEX_BRIDGE_RUNTIME_DIR = $runtimeRoot
$hookPayload = Get-InputPayload -Required:$HookInvocation
if ($HookInvocation) {
    $eventName = Get-FirstValue $hookPayload @('hook_event_name', 'hookEventName', 'event_name', 'eventName')
    $sessionId = Get-FirstValue $hookPayload @('session_id', 'sessionId')
    if ($eventName -ne 'SessionStart') {
        throw "Expected a SessionStart hook payload, received '$eventName'; refusing to create a bridge lease."
    }
    if (-not $sessionId) {
        throw 'SessionStart hook input had no session id; refusing to create a bridge lease.'
    }
}
Assert-BridgeRuntimeManifest
$existingBridgePid = 0
if (Test-Path -LiteralPath $pidFile) {
    $existingPidText = (Get-Content -LiteralPath $pidFile -Raw).Trim()
    $existingPid = 0
    if ([int]::TryParse($existingPidText, [ref]$existingPid)) {
        $identity = Get-BridgeProcessIdentity -ProcessId $existingPid -BridgeScript $bridgeScript
        if ($identity.Exists) {
            if (-not $identity.Verified) {
                throw "Listener PID $existingPid exists, but its Python command line could not be verified; refusing to start a second runtime."
            }
            if ($identity.IsBridge) {
                $existingBridgePid = $existingPid
            } else {
                Remove-Item -LiteralPath $pidFile -Force -ErrorAction Stop
                Write-Output "Removed stale Bridge PID file; PID $existingPid belongs to non-Bridge process $($identity.ProcessName)."
            }
        } else {
            Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
        }
    } else {
        Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    }
}

$leaseId = Write-Lease $hookPayload
if ($existingBridgePid -gt 0) {
    Write-Output "Feishu bridge lease $leaseId active; runtime already running (PID $existingBridgePid)."
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
    throw 'Python 3.10+ was not found for the Feishu bridge.'
}

$pythonRuntime = Get-UsablePython
$python = $pythonRuntime.Path
$arguments = @($pythonRuntime.Prefix) + @($bridgeScript)
$process = Start-Process `
    -FilePath $python `
    -ArgumentList $arguments `
    -WorkingDirectory $projectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $launcherOut `
    -RedirectStandardError $launcherErr `
    -PassThru

Write-Output "Activated Feishu bridge lease $leaseId; launcher PID $($process.Id)."
} finally {
    if ($lifecycleMutexOwned) {
        try { $lifecycleMutex.ReleaseMutex() } catch [System.ApplicationException] { }
    }
    $lifecycleMutex.Dispose()
}
