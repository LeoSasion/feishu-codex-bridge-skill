[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ProjectRoot,
    [switch]$Force,
    [switch]$SkipHooks,
    [switch]$SkipRuntimeConfig,
    [switch]$HooksOnly,
    [string]$BeeperThreadId = ''
)

$ErrorActionPreference = 'Stop'
$project = (Resolve-Path -LiteralPath $ProjectRoot).Path
$source = Split-Path -Parent $PSScriptRoot
$runtime = Join-Path $project '.codex\feishu-codex-operator-runtime'
$hooksRoot = Join-Path $project '.codex\hooks'
$startHook = Join-Path $hooksRoot 'start-feishu-codex-operator.ps1'
$stopHook = Join-Path $hooksRoot 'stop-feishu-codex-operator.ps1'
$hooksConfig = Join-Path $project '.codex\hooks.json'
$envFile = Join-Path $runtime 'operator.env'
$manifestFile = Join-Path $runtime 'runtime-manifest.json'
$operatorFile = Join-Path $runtime 'operator_main.py'
$backupRoot = Join-Path $runtime ('backups\' + (Get-Date -Format 'yyyyMMdd-HHmmss'))

function Test-OperatorProcess {
    $pidFile = Join-Path $runtime 'operator.pid'
    if (-not (Test-Path -LiteralPath $pidFile -PathType Leaf)) { return }
    $value = (Get-Content -LiteralPath $pidFile -Raw -ErrorAction Stop).Trim()
    $processId = 0
    if (-not [int]::TryParse($value, [ref]$processId) -or $processId -le 0) {
        throw 'Installed Operator PID file is invalid; inspect it before upgrading.'
    }
    $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if (-not $process) { return }
    if ([string]$process.ProcessName -notmatch '(?i)^python(?:w|[0-9.]*)?$') {
        throw 'Installed Operator PID points to a live foreign process; refusing upgrade.'
    }
    try {
        $record = Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f $processId) -ErrorAction Stop
    } catch {
        throw 'Installed Operator process identity cannot be verified; refusing upgrade.'
    }
    $expected = [System.IO.Path]::GetFullPath($operatorFile).Replace('/', '\')
    $observed = [string]$record.CommandLine
    if (-not [string]::IsNullOrWhiteSpace($observed) -and
        $observed.Replace('/', '\').IndexOf(
            $expected,
            [System.StringComparison]::OrdinalIgnoreCase
        ) -ge 0) {
        throw 'Stop the exact installed Operator before install or upgrade.'
    }
    throw 'Installed Operator PID points to an unverifiable live process; refusing upgrade.'
}

function Backup-File([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return }
    New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
    $name = [System.IO.Path]::GetFileName($Path)
    $destination = Join-Path $backupRoot $name
    $suffix = 0
    while (Test-Path -LiteralPath $destination) {
        $suffix++
        $destination = Join-Path $backupRoot ("{0}.{1}" -f $name, $suffix)
    }
    Copy-Item -LiteralPath $Path -Destination $destination
}

function Install-File([string]$From, [string]$To) {
    if (-not (Test-Path -LiteralPath $From -PathType Leaf)) {
        throw "Source file is missing: $From"
    }
    $parent = Split-Path -Parent $To
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    if (Test-Path -LiteralPath $To -PathType Leaf) {
        if (-not $Force -and
            (Get-FileHash -LiteralPath $From -Algorithm SHA256).Hash -cne
            (Get-FileHash -LiteralPath $To -Algorithm SHA256).Hash) {
            throw "Installed file differs; rerun with -Force after reviewing it: $To"
        }
        Backup-File $To
    }
    Copy-Item -LiteralPath $From -Destination $To -Force
}

function Ensure-Environment {
    if ($SkipRuntimeConfig) { return }
    if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
        @'
# Feishu Codex Operator configuration. Never store app secrets or OAuth tokens here.
CODEX_OPERATOR_ACCESS_MODE=locked
CODEX_OPERATOR_EVENT_READY_TIMEOUT=15
CODEX_OPERATOR_MAX_CONCURRENT_TURNS=2
CODEX_OPERATOR_UNKNOWN_STATUS_TIMEOUT=300
CODEX_OPERATOR_CALLBACK_GRACE_SECONDS=20
CODEX_OPERATOR_CALLBACK_RETENTION_HOURS=168
CODEX_OPERATOR_APP_SERVER_TIMEOUT=20
CODEX_OPERATOR_BEEPER_THREAD_ID=
CODEX_OPERATOR_BEEPER_MODEL=
CODEX_OPERATOR_BEEPER_REASONING_EFFORT=
CODEX_OPERATOR_BEEPER_PROMPT_LANGUAGE=
CODEX_OPERATOR_LIFECYCLE_MODE=hooks
# CODEX_OPERATOR_OWNER_OPEN_ID=
# CODEX_OPERATOR_ADMIN_OPEN_IDS=
# CODEX_OPERATOR_ALLOWED_USER_OPEN_IDS=
# CODEX_OPERATOR_ALLOWED_CHAT_IDS=
# Optional exact Desktop-bundled CLI override:
# CODEX_OPERATOR_CODEX_EXE=
'@ | Set-Content -LiteralPath $envFile -Encoding utf8
        return
    }

    $lines = [System.Collections.Generic.List[string]]::new()
    foreach ($line in Get-Content -LiteralPath $envFile -Encoding utf8) {
        $lines.Add([string]$line)
    }
    $existing = @{}
    foreach ($line in $lines) {
        if ([string]$line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$') {
            $name = [string]$Matches[1]
            if ($existing.ContainsKey($name)) {
                throw "Operator environment contains duplicate $name entries; refusing migration."
            }
            $existing[$name] = [string]$Matches[2]
        }
    }

    function Resolve-MigratedValue {
        param(
            [Parameter(Mandatory = $true)][string]$CurrentName,
            [Parameter(Mandatory = $true)][string[]]$LegacyNames,
            [Parameter(Mandatory = $true)][string]$DefaultValue
        )
        if ($existing.ContainsKey($CurrentName)) {
            return [string]$existing[$CurrentName]
        }
        $found = @($LegacyNames | Where-Object { $existing.ContainsKey($_) })
        if ($found.Count -eq 0) { return $DefaultValue }
        $values = @($found | ForEach-Object { [string]$existing[$_] } | Sort-Object -Unique)
        if ($values.Count -ne 1) {
            throw "Operator environment contains conflicting legacy values for $CurrentName; refusing migration."
        }
        return [string]$values[0]
    }

    $defaults = [ordered]@{
        CODEX_OPERATOR_UNKNOWN_STATUS_TIMEOUT = Resolve-MigratedValue `
            -CurrentName 'CODEX_OPERATOR_UNKNOWN_STATUS_TIMEOUT' `
            -LegacyNames @('CODEX_OPERATOR_RESPONDER_TIMEOUT', 'CODEX_OPERATOR_BEEPER_TIMEOUT', 'CODEX_OPERATOR_ROUTER_TIMEOUT') `
            -DefaultValue '300'
        CODEX_OPERATOR_CALLBACK_GRACE_SECONDS = '20'
        CODEX_OPERATOR_CALLBACK_RETENTION_HOURS = Resolve-MigratedValue `
            -CurrentName 'CODEX_OPERATOR_CALLBACK_RETENTION_HOURS' `
            -LegacyNames @('CODEX_OPERATOR_BEEPER_RETENTION_HOURS', 'CODEX_OPERATOR_ROUTER_RETENTION_HOURS') `
            -DefaultValue '168'
        CODEX_OPERATOR_APP_SERVER_TIMEOUT = '20'
        CODEX_OPERATOR_BEEPER_THREAD_ID = ''
        CODEX_OPERATOR_BEEPER_MODEL = ''
        CODEX_OPERATOR_BEEPER_REASONING_EFFORT = ''
        CODEX_OPERATOR_BEEPER_PROMPT_LANGUAGE = ''
    }
    $retiredNames = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::Ordinal
    )
    foreach ($name in @(
        'CODEX_OPERATOR_RESPONDER_TIMEOUT',
        'CODEX_OPERATOR_BEEPER_TIMEOUT',
        'CODEX_OPERATOR_BEEPER_CLAIM_TTL',
        'CODEX_OPERATOR_BEEPER_RETENTION_HOURS',
        'CODEX_OPERATOR_BEEPER_DIAL_TTL',
        'CODEX_OPERATOR_BEEPER_GRACE_MAX_SECONDS',
        'CODEX_OPERATOR_ROUTER_TIMEOUT',
        'CODEX_OPERATOR_ROUTER_CLAIM_TTL',
        'CODEX_OPERATOR_ROUTER_RETENTION_HOURS',
        'CODEX_OPERATOR_ROUTER_WAKE_TTL',
        'CODEX_OPERATOR_ROUTER_GRACE_MAX_SECONDS',
        'CODEX_OPERATOR_ALLOW_PROJECT_CREATE'
    )) {
        [void]$retiredNames.Add($name)
    }
    $migratedLines = [System.Collections.Generic.List[string]]::new()
    $changed = $false
    foreach ($line in $lines) {
        if ([string]$line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=') {
            $name = [string]$Matches[1]
            if ($retiredNames.Contains($name)) {
                $changed = $true
                continue
            }
        }
        $migratedLines.Add([string]$line)
    }
    foreach ($entry in $defaults.GetEnumerator()) {
        if (-not $existing.ContainsKey([string]$entry.Key)) {
            $migratedLines.Add(("{0}={1}" -f $entry.Key, $entry.Value))
            $changed = $true
        }
    }
    if ($changed) {
        Backup-File $envFile
        $migratedLines | Set-Content -LiteralPath $envFile -Encoding utf8
    }
}

function Set-MinimalBeeperThreadId {
    if ($SkipRuntimeConfig -or [string]::IsNullOrWhiteSpace($BeeperThreadId)) { return }
    $candidate = $BeeperThreadId.Trim().ToLowerInvariant()
    if ($candidate -cnotmatch '^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$') {
        throw 'BeeperThreadId must be an exact Codex task UUID.'
    }
    $lines = [System.Collections.Generic.List[string]]::new()
    $replaced = $false
    foreach ($line in Get-Content -LiteralPath $envFile -Encoding utf8) {
        if ([string]$line -match '^\s*CODEX_OPERATOR_BEEPER_THREAD_ID\s*=') {
            if ($replaced) { throw 'Operator environment contains duplicate CODEX_OPERATOR_BEEPER_THREAD_ID entries.' }
            $lines.Add("CODEX_OPERATOR_BEEPER_THREAD_ID=$candidate")
            $replaced = $true
        } else {
            $lines.Add([string]$line)
        }
    }
    if (-not $replaced) { $lines.Add("CODEX_OPERATOR_BEEPER_THREAD_ID=$candidate") }
    [System.IO.File]::WriteAllLines($envFile, $lines, [System.Text.UTF8Encoding]::new($false))
}

function New-CommandHook([string]$ScriptPath, [int]$Timeout, [string]$Message, [bool]$HookInvocation) {
    $command = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $ScriptPath
    if ($HookInvocation) { $command += ' -HookInvocation' }
    return [pscustomobject]@{
        type = 'command'
        command = $command
        commandWindows = $command
        timeout = $Timeout
        statusMessage = $Message
    }
}

function Update-Hooks {
    if ($SkipHooks) { return }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $hooksConfig) | Out-Null
    if (Test-Path -LiteralPath $hooksConfig -PathType Leaf) {
        Backup-File $hooksConfig
        $config = Get-Content -LiteralPath $hooksConfig -Raw -Encoding utf8 |
            ConvertFrom-Json -ErrorAction Stop
    } else {
        $config = [pscustomobject]@{ description = 'Lease-manage the local Feishu operator.'; hooks = [pscustomobject]@{} }
    }
    if (-not $config.PSObject.Properties['hooks']) {
        $config | Add-Member -NotePropertyName hooks -NotePropertyValue ([pscustomobject]@{})
    }

    foreach ($eventName in @('SessionStart', 'SessionEnd')) {
        $property = $config.hooks.PSObject.Properties[$eventName]
        $kept = @()
        if ($property) {
            foreach ($group in @($property.Value)) {
                $commands = @($group.hooks)
                $isOperator = $false
                foreach ($command in $commands) {
                    if ([string]$command.command -like '*feishu-codex-operator.ps1*') { $isOperator = $true }
                }
                if (-not $isOperator) { $kept += $group }
            }
        }
        $newGroup = if ($eventName -eq 'SessionStart') {
            [pscustomobject]@{
                matcher = 'startup|resume'
                hooks = @((New-CommandHook $startHook 10 'Activating Feishu operator lease' $true))
            }
        } else {
            [pscustomobject]@{
                hooks = @((New-CommandHook $stopHook 3 'Releasing Feishu operator lease' $true))
            }
        }
        $kept += $newGroup
        if ($property) { $property.Value = @($kept) }
        else { $config.hooks | Add-Member -NotePropertyName $eventName -NotePropertyValue @($kept) }
    }

    $temporary = "$hooksConfig.tmp"
    $json = $config | ConvertTo-Json -Depth 30
    [System.IO.File]::WriteAllText($temporary, $json, [System.Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $temporary -Destination $hooksConfig -Force
}

function Write-RuntimeManifest {
    [string[]]$files = @(
        'operator_main.py',
        'routing_cli.py',
        'operator_core/__init__.py',
        'operator_core/app_server_catalog.py',
        'operator_core/app_server.py',
        'operator_core/dispatch.py',
        'operator_core/telemetry.py',
        'operator_core/config.py',
        'operator_core/final_callback.py',
        'operator_core/lark.py',
        'operator_core/rate_limits.py',
        'operator_core/responder_observer.py',
        'operator_core/beeper_relay.py',
        'operator_core/runtime.py',
        'operator_core/state.py'
    )
    $hashes = [ordered]@{}
    foreach ($relative in $files) {
        $target = Join-Path $runtime ($relative.Replace('/', '\'))
        $hashes[$relative] = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    $manifest = [ordered]@{
        schema_version = 1
        operator_version = '4.2.0-alpha.86'
        code_files = $hashes
        start_hook_sha256 = (Get-FileHash -LiteralPath $startHook -Algorithm SHA256).Hash.ToLowerInvariant()
        stop_hook_sha256 = (Get-FileHash -LiteralPath $stopHook -Algorithm SHA256).Hash.ToLowerInvariant()
        generated_at = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    }
    [System.IO.File]::WriteAllText(
        $manifestFile,
        ($manifest | ConvertTo-Json -Depth 10),
        [System.Text.UTF8Encoding]::new($false)
    )
}

Test-OperatorProcess
New-Item -ItemType Directory -Force -Path $runtime, $hooksRoot | Out-Null

Install-File (Join-Path $source 'scripts\start-feishu-codex-operator.ps1') $startHook
Install-File (Join-Path $source 'scripts\stop-feishu-codex-operator.ps1') $stopHook
Update-Hooks

if ($HooksOnly) {
    if (Test-Path -LiteralPath $manifestFile -PathType Leaf) {
        Backup-File $manifestFile
        Remove-Item -LiteralPath $manifestFile -Force
    }
    Write-Output 'Lifecycle Hooks were refreshed; runtime manifest was invalidated until a full upgrade.'
    exit 0
}

[string[]]$runtimeFiles = @(
    'operator_main.py',
    'routing_cli.py',
    'operator_core\__init__.py',
    'operator_core\app_server_catalog.py',
    'operator_core\app_server.py',
    'operator_core\dispatch.py',
    'operator_core\telemetry.py',
    'operator_core\config.py',
    'operator_core\final_callback.py',
    'operator_core\lark.py',
    'operator_core\rate_limits.py',
    'operator_core\responder_observer.py',
    'operator_core\beeper_relay.py',
    'operator_core\runtime.py',
    'operator_core\state.py'
)
foreach ($relative in $runtimeFiles) {
    Install-File (Join-Path (Join-Path $source 'scripts') $relative) (Join-Path $runtime $relative)
}

Ensure-Environment
Set-MinimalBeeperThreadId
Write-RuntimeManifest
$health = Join-Path $runtime 'health.json'
if (Test-Path -LiteralPath $health -PathType Leaf) {
    Backup-File $health
    Remove-Item -LiteralPath $health -Force
}

Write-Output "Installed Feishu Codex Operator 4.2.0-alpha.86 into $runtime"
Write-Output 'The Operator remains stopped. Configure the minimal Beeper UUID, register Final Callback routing, review Hooks in Desktop settings, then start it.'
