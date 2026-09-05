[CmdletBinding()]
param(
    [switch]$HookInvocation
)

$ErrorActionPreference = 'Stop'

# FEISHU_OPERATOR_HOOK_SUCCESS_STDOUT_SILENT_V1
function Write-LifecycleStatus {
    param([Parameter(Mandatory = $true)][string]$Message)
    if (-not $HookInvocation) {
        Write-Output $Message
    }
}

if ($env:CODEX_OPERATOR_CHILD -eq '1') {
    Write-LifecycleStatus 'Skipping Feishu operator lifecycle hook inside its Codex child.'
    exit 0
}

$hooksRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent (Split-Path -Parent $hooksRoot)
$runtimeRoot = Join-Path $projectRoot '.codex\feishu-codex-operator-runtime'
$pidFile = Join-Path $runtimeRoot 'operator.pid'
$stopFile = Join-Path $runtimeRoot 'stop.request'
$leaseRoot = Join-Path $runtimeRoot 'leases'

function Get-InputPayload {
    param([switch]$Required)

    if (-not $Required) { return $null }

    $raw = [Console]::In.ReadToEnd()
    if ([string]::IsNullOrWhiteSpace($raw)) {
        throw 'SessionEnd hook input was empty; refusing to change operator lifecycle state.'
    }
    try {
        return $raw | ConvertFrom-Json -ErrorAction Stop
    } catch {
        throw "SessionEnd hook input was invalid JSON; refusing to change operator lifecycle state: $($_.Exception.Message)"
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
        $lifecycleMutexOwned = $lifecycleMutex.WaitOne(0)
    } catch [System.Threading.AbandonedMutexException] {
        $lifecycleMutexOwned = $true
    }
    if (-not $lifecycleMutexOwned) {
        throw 'Feishu operator lifecycle is locked by an external verification or another lifecycle operation.'
    }

New-Item -ItemType Directory -Force -Path $runtimeRoot, $leaseRoot | Out-Null

function Release-Lease {
    param([Parameter(Mandatory = $true)][string]$SessionId)
    $leaseId = Get-ShortHash $SessionId
    $leasePath = Join-Path $leaseRoot ("{0}.json" -f $leaseId)
    if (-not (Test-Path -LiteralPath $leasePath)) {
        Write-LifecycleStatus "Feishu operator lease $leaseId was already absent."
        return
    }
    try {
        $lease = Get-Content -LiteralPath $leasePath -Raw | ConvertFrom-Json
    } catch {
        $lease = [pscustomobject]@{ version = 1; lease_id = $leaseId; source = 'hook' }
    }
    $lease | Add-Member -MemberType NoteProperty -Name status -Value 'released' -Force
    $lease | Add-Member -MemberType NoteProperty -Name updated_at -Value ([DateTimeOffset]::UtcNow.ToUnixTimeSeconds()) -Force
    $temporary = "$leasePath.tmp"
    $lease | ConvertTo-Json | Set-Content -LiteralPath $temporary -Encoding utf8
    Move-Item -LiteralPath $temporary -Destination $leasePath -Force
    Write-LifecycleStatus "Released Feishu operator lease $leaseId; shared runtime remains lease-managed."
}

$hookPayload = Get-InputPayload -Required:$HookInvocation
if ($HookInvocation) {
    $eventName = Get-FirstValue $hookPayload @('hook_event_name', 'hookEventName', 'event_name', 'eventName')
    $sessionId = Get-FirstValue $hookPayload @('session_id', 'sessionId')
    if ($eventName -ne 'SessionEnd') {
        throw "Expected a SessionEnd hook payload, received '$eventName'; refusing to change operator lifecycle state."
    }
    if (-not $sessionId) {
        throw 'SessionEnd hook input had no session id; refusing to change operator lifecycle state.'
    }
    Release-Lease $sessionId
    exit 0
}

# A direct/manual invocation is an explicit request to stop the shared runtime.
Get-ChildItem -LiteralPath $leaseRoot -Filter '*.json' -File -ErrorAction SilentlyContinue | ForEach-Object {
    try {
        $lease = Get-Content -LiteralPath $_.FullName -Raw | ConvertFrom-Json
        $lease | Add-Member -MemberType NoteProperty -Name status -Value 'released' -Force
        $lease | Add-Member -MemberType NoteProperty -Name host_pid -Value 0 -Force
        $lease | Add-Member -MemberType NoteProperty -Name updated_at -Value ([DateTimeOffset]::UtcNow.ToUnixTimeSeconds()) -Force
        $lease | ConvertTo-Json | Set-Content -LiteralPath $_.FullName -Encoding utf8
    } catch {
    }
}
Set-Content -LiteralPath $stopFile -Value ([DateTime]::UtcNow.ToString('o')) -Encoding ascii

if (-not (Test-Path -LiteralPath $pidFile)) {
    Write-LifecycleStatus 'Feishu operator is not running.'
    exit 0
}

$pidText = (Get-Content -LiteralPath $pidFile -Raw).Trim()
$operatorPid = 0
if (-not [int]::TryParse($pidText, [ref]$operatorPid)) {
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    Write-LifecycleStatus 'Removed an invalid Feishu operator PID file.'
    exit 0
}

$operatorScript = Join-Path $runtimeRoot 'operator_main.py'
$identity = Get-OperatorProcessIdentity -ProcessId $operatorPid -OperatorScript $operatorScript
if (-not $identity.Exists) {
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    Write-LifecycleStatus "Stopped Feishu operator PID $operatorPid."
    exit 0
}
if (-not $identity.Verified) {
    throw "Operator PID $operatorPid exists, but its Python command line could not be verified; refusing to stop any process."
}
if (-not $identity.IsOperator) {
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    Write-LifecycleStatus "Removed stale Operator PID file; PID $operatorPid belongs to non-Operator process $($identity.ProcessName). No process was stopped."
    exit 0
}

$deadline = (Get-Date).AddSeconds(20)
while ((Get-Date) -lt $deadline) {
    $identity = Get-OperatorProcessIdentity -ProcessId $operatorPid -OperatorScript $operatorScript
    if (-not $identity.Exists) {
        Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
        Write-LifecycleStatus "Stopped Feishu operator PID $operatorPid."
        exit 0
    }
    if (-not $identity.Verified) {
        # Win32_Process may lose the command line while the verified Operator is
        # completing normal process teardown.  Unverifiable is never permission
        # to stop the process, but it is also not proof that the PID was reused.
        # Keep observing through the existing grace period.  Success still
        # requires the process to disappear (or become a verified non-Operator
        # process); an identity that remains unverifiable fails closed below.
        Start-Sleep -Milliseconds 500
        continue
    }
    if (-not $identity.IsOperator) {
        Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
        Write-LifecycleStatus "Operator exited; reused PID $operatorPid now belongs to $($identity.ProcessName). No process was stopped."
        exit 0
    }
    Start-Sleep -Milliseconds 500
}

$identity = Get-OperatorProcessIdentity -ProcessId $operatorPid -OperatorScript $operatorScript
if ($identity.Exists -and -not $identity.Verified) {
    throw "Operator PID $operatorPid changed to an unverifiable Python process; refusing to force-stop it."
}
if ($identity.Exists -and $identity.IsOperator) {
    Stop-Process -InputObject $identity.Process -Force -ErrorAction Stop
    Write-LifecycleStatus "Force-stopped unresponsive Feishu operator PID $operatorPid."
} elseif ($identity.Exists) {
    Write-LifecycleStatus "Operator exited; reused PID $operatorPid belongs to $($identity.ProcessName). No process was stopped."
}
Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
} finally {
    if ($lifecycleMutexOwned) {
        try { $lifecycleMutex.ReleaseMutex() } catch [System.ApplicationException] { }
    }
    $lifecycleMutex.Dispose()
}
