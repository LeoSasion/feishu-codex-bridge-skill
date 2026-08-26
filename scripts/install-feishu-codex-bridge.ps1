[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot,

    [switch]$Force,

    [switch]$SkipHooks,

    [switch]$SkipRuntimeConfig,

    [switch]$HooksOnly
)

$ErrorActionPreference = 'Stop'
$resolvedProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
$skillRoot = Split-Path -Parent $PSScriptRoot
$runtimeRoot = Join-Path $resolvedProjectRoot '.codex\feishu-bridge'
$hooksRoot = Join-Path $resolvedProjectRoot '.codex\hooks'
$bridgeTarget = Join-Path $runtimeRoot 'bridge.py'
$routerQueueTarget = Join-Path $runtimeRoot 'router_queue.py'
$coreTarget = Join-Path $runtimeRoot 'bridge_core'
$startTarget = Join-Path $hooksRoot 'start-feishu-codex-bridge.ps1'
$stopTarget = Join-Path $hooksRoot 'stop-feishu-codex-bridge.ps1'
$envTarget = Join-Path $runtimeRoot 'bridge.env'
$runtimeManifestTarget = Join-Path $runtimeRoot 'runtime-manifest.json'
$hooksConfigPath = Join-Path $resolvedProjectRoot '.codex\hooks.json'
$backupRoot = Join-Path $runtimeRoot ('backups\' + (Get-Date -Format 'yyyyMMdd-HHmmss'))

function Get-BridgeProcessIdentity {
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [Parameter(Mandatory = $true)][string]$BridgeScript
    )
    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if (-not $process) {
        return [pscustomobject]@{ Exists = $false; Verified = $true; IsBridge = $false; ProcessName = '' }
    }
    $processName = [string]$process.ProcessName
    if ($processName -notmatch '(?i)^python(?:w|[0-9.]*)?$') {
        return [pscustomobject]@{ Exists = $true; Verified = $true; IsBridge = $false; ProcessName = $processName }
    }
    try {
        $record = Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f $ProcessId) -ErrorAction Stop
    } catch {
        return [pscustomobject]@{ Exists = $true; Verified = $false; IsBridge = $false; ProcessName = $processName }
    }
    $commandLine = if ($record) { [string]$record.CommandLine } else { '' }
    if ([string]::IsNullOrWhiteSpace($commandLine)) {
        return [pscustomobject]@{ Exists = $true; Verified = $false; IsBridge = $false; ProcessName = $processName }
    }
    $expected = [System.IO.Path]::GetFullPath($BridgeScript).Replace('/', '\')
    $observed = $commandLine.Replace('/', '\')
    return [pscustomobject]@{
        Exists = $true
        Verified = $true
        IsBridge = $observed.IndexOf($expected, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
        ProcessName = $processName
    }
}

function Assert-ListenerStopped {
    $pidPath = Join-Path $runtimeRoot 'bridge.pid'
    if (-not (Test-Path -LiteralPath $pidPath -PathType Leaf)) { return }
    $listenerPid = 0
    if (-not [int]::TryParse((Get-Content -LiteralPath $pidPath -Raw).Trim(), [ref]$listenerPid) -or
        $listenerPid -le 0) { return }
    $identity = Get-BridgeProcessIdentity -ProcessId $listenerPid -BridgeScript $bridgeTarget
    if (-not $identity.Exists -or ($identity.Verified -and -not $identity.IsBridge)) { return }
    if (-not $identity.Verified) {
        throw "Listener PID $listenerPid exists, but its Python command line could not be verified; refusing installation changes."
    }
    throw "Listener must be stopped under a separate approval (PID $listenerPid)."
}

function Assert-ManifestCapableHooks {
    foreach ($hookTarget in @($startTarget, $stopTarget)) {
        if (-not (Test-Path -LiteralPath $hookTarget -PathType Leaf)) {
            throw "Cannot create runtime manifest because the installed hook is missing: $hookTarget"
        }
    }
    $startHookText = Get-Content -LiteralPath $startTarget -Raw
    if ($startHookText -notmatch [regex]::Escape('$BRIDGE_RUNTIME_MANIFEST_SCHEMA = 1')) {
        throw ('The installed start hook predates the runtime-manifest gate. ' +
            'Refresh hooks under a separate approval before installing or upgrading runtime code.')
    }
}

if ($HooksOnly -and ($SkipHooks -or $SkipRuntimeConfig)) {
    throw 'HooksOnly cannot be combined with runtime or configuration options.'
}
if ($HooksOnly -and -not $Force -and
    ((Test-Path -LiteralPath $startTarget) -or (Test-Path -LiteralPath $stopTarget))) {
    throw 'HooksOnly requires Force when replacing installed lifecycle hooks.'
}
if ($HooksOnly -or $Force -or $SkipHooks) {
    Assert-ListenerStopped
}
if ($HooksOnly) {
    foreach ($requiredInstalledPath in @($bridgeTarget, $envTarget, $startTarget, $stopTarget)) {
        if (-not (Test-Path -LiteralPath $requiredInstalledPath -PathType Leaf)) {
            throw "Hook-only refresh requires an existing bridge installation: $requiredInstalledPath"
        }
    }
    if (Test-Path -LiteralPath $hooksConfigPath -PathType Leaf) {
        try {
            Get-Content -LiteralPath $hooksConfigPath -Raw -Encoding utf8 |
                ConvertFrom-Json -ErrorAction Stop | Out-Null
        } catch {
            throw "Existing hooks.json is invalid; repair it before hook-only refresh: $($_.Exception.Message)"
        }
    }
}
if (-not $HooksOnly -and $SkipRuntimeConfig -and -not (Test-Path -LiteralPath $envTarget -PathType Leaf)) {
    throw "Cannot skip runtime configuration because it does not exist: $envTarget"
}
if (-not $HooksOnly -and $SkipHooks) {
    # Fail before merging rules or copying runtime code. A runtime-only upgrade
    # is safe only after the installed hook already knows this manifest schema.
    Assert-ManifestCapableHooks
}

Write-Output 'Installer leaves project AGENTS.md rules unchanged; use bridge init under a separate approval.'

New-Item -ItemType Directory -Force -Path $runtimeRoot, $hooksRoot | Out-Null

function Backup-Target {
    param([Parameter(Mandatory = $true)][string]$Target)
    if (-not (Test-Path -LiteralPath $Target)) { return }
    New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
    Copy-Item -LiteralPath $Target -Destination $backupRoot -Recurse -Force
}

function Copy-BridgeFile {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Target
    )
    if (Test-Path -LiteralPath $Target) {
        if (-not $Force) {
            Write-Output "Preserved existing $Target"
            return
        }
        Backup-Target $Target
    }
    Copy-Item -LiteralPath $Source -Destination $Target -Force
    Write-Output "Installed $Target"
}

function Copy-BridgeDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Target
    )
    if (Test-Path -LiteralPath $Target) {
        if (-not $Force) {
            Write-Output "Preserved existing $Target"
            return
        }
        $resolvedTarget = (Resolve-Path -LiteralPath $Target).Path
        if (-not $resolvedTarget.StartsWith($runtimeRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to replace directory outside bridge runtime: $resolvedTarget"
        }
        Backup-Target $Target
        Remove-Item -LiteralPath $resolvedTarget -Recurse -Force
    }
    Copy-Item -LiteralPath $Source -Destination $Target -Recurse -Force
    Write-Output "Installed $Target"
}

function Write-BridgeRuntimeManifest {
    $expectedVersion = '4.2.0-alpha.30'
    $runtimeFiles = @(
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

    Assert-ManifestCapableHooks

    $codeHashes = [ordered]@{}
    foreach ($relative in $runtimeFiles) {
        $target = Join-Path $runtimeRoot ($relative.Replace('/', '\'))
        if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {
            throw "Cannot create runtime manifest because installed code is missing: $target"
        }
        $codeHashes[$relative] = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
    }

    $installedConfig = Get-Content -LiteralPath (Join-Path $coreTarget 'config.py') -Raw
    if ($installedConfig -notmatch 'BRIDGE_VERSION\s*=\s*["'']([^"'']+)["'']') {
        throw 'Installed runtime has no readable BRIDGE_VERSION marker.'
    }
    $installedVersion = [string]$Matches[1]
    if ($installedVersion -ne $expectedVersion) {
        throw "Installed runtime version '$installedVersion' is obsolete; expected '$expectedVersion'. Use an approved forced upgrade."
    }

    $manifest = [ordered]@{
        schema_version = 1
        bridge_version = $installedVersion
        code_files = $codeHashes
        start_hook_sha256 = (Get-FileHash -LiteralPath $startTarget -Algorithm SHA256).Hash.ToLowerInvariant()
        stop_hook_sha256 = (Get-FileHash -LiteralPath $stopTarget -Algorithm SHA256).Hash.ToLowerInvariant()
        generated_at = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    }
    $temporary = "$runtimeManifestTarget.tmp"
    try {
        $json = $manifest | ConvertTo-Json -Depth 10
        $utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText($temporary, $json, $utf8WithoutBom)
        Move-Item -LiteralPath $temporary -Destination $runtimeManifestTarget -Force
    } finally {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    }
    Write-Output "Installed runtime integrity manifest: $runtimeManifestTarget"
}

if ($HooksOnly) {
    Copy-BridgeFile (Join-Path $skillRoot 'scripts\start-feishu-codex-bridge.ps1') $startTarget
    Copy-BridgeFile (Join-Path $skillRoot 'scripts\stop-feishu-codex-bridge.ps1') $stopTarget
    if (Test-Path -LiteralPath $runtimeManifestTarget -PathType Leaf) {
        Backup-Target $runtimeManifestTarget
        Remove-Item -LiteralPath $runtimeManifestTarget -Force
        Write-Output 'Invalidated the previous runtime manifest; start remains fail-closed until a separate runtime upgrade.'
    }
} else {
    Copy-BridgeFile (Join-Path $skillRoot 'scripts\bridge.py') $bridgeTarget
    Copy-BridgeFile (Join-Path $skillRoot 'scripts\router_queue.py') $routerQueueTarget
    Copy-BridgeDirectory (Join-Path $skillRoot 'scripts\bridge_core') $coreTarget
    if ($SkipHooks) {
        Write-Output 'Skipped lifecycle hook scripts.'
    } else {
        Copy-BridgeFile (Join-Path $skillRoot 'scripts\start-feishu-codex-bridge.ps1') $startTarget
        Copy-BridgeFile (Join-Path $skillRoot 'scripts\stop-feishu-codex-bridge.ps1') $stopTarget
    }

    if ($SkipRuntimeConfig) {
        Write-Output "Skipped runtime configuration: $envTarget"
    } else {
    if (-not (Test-Path -LiteralPath $envTarget)) {
    @'
# Feishu Codex Bridge configuration. Never store app secrets or OAuth tokens here.
# The Feishu listener writes a durable local queue. A scheduler heartbeat wakes
# one existing Desktop Gateway task; the same cycle probes metadata and routes.
# The helper heartbeat is only active-work lease renewal; no target App Server opens.
CODEX_BRIDGE_ACCESS_MODE=locked
CODEX_BRIDGE_EVENT_READY_TIMEOUT=15
CODEX_BRIDGE_MAX_CONCURRENT_TURNS=2
CODEX_BRIDGE_ROUTER_TIMEOUT=3600
CODEX_BRIDGE_ROUTER_HEARTBEAT_TTL=90
CODEX_BRIDGE_ROUTER_CLAIM_TTL=7200
CODEX_BRIDGE_ROUTER_RETENTION_HOURS=168
CODEX_BRIDGE_ROUTER_WAKE_TTL=180
CODEX_BRIDGE_GATEWAY_SCHEDULER_TTL=300
CODEX_BRIDGE_ROUTER_GRACE_MAX_SECONDS=30
CODEX_BRIDGE_ALLOW_PROJECT_CREATE=0
CODEX_BRIDGE_LIFECYCLE_MODE=hooks

# Optional parent directory for projects created by an owner/admin after
# confirmation in the /init wizard. Unset defaults to the bridge project's parent.
# CODEX_BRIDGE_PROJECTS_ROOT=

# Access is fail-closed by default. Configure one or more IDs before activation;
# compatibility mode is an explicit legacy migration choice, never a default.
# CODEX_BRIDGE_OWNER_OPEN_ID=
# CODEX_BRIDGE_ADMIN_OPEN_IDS=
# CODEX_BRIDGE_ALLOWED_USER_OPEN_IDS=
# CODEX_BRIDGE_ALLOWED_CHAT_IDS=

# Knowledge sources, including Obsidian vaults, belong to the target Codex
# project's directory and are never configured by this bridge.
'@ | Set-Content -LiteralPath $envTarget -Encoding utf8
    Write-Output "Created $envTarget"
} else {
    Write-Output "Preserved existing $envTarget"
}

    }

    Write-BridgeRuntimeManifest

    if ($Force -and (Test-Path -LiteralPath $backupRoot)) {
        Write-Output "Previous bridge code was backed up to $backupRoot"
    }
}

if ($SkipHooks) {
    Write-Output 'Skipped .codex/hooks.json registration.'
    exit 0
}

if (Test-Path -LiteralPath $hooksConfigPath) {
    Backup-Target $hooksConfigPath
    Write-Output "Backed up the previous hook configuration to $backupRoot"
    $config = Get-Content -LiteralPath $hooksConfigPath -Raw -Encoding utf8 | ConvertFrom-Json
} else {
    $config = [pscustomobject]@{
        description = 'Lease-manage the local Feishu bridge while Codex Desktop is in use.'
        hooks = [pscustomobject]@{}
    }
}
if (-not $config.PSObject.Properties['hooks']) {
    $config | Add-Member -MemberType NoteProperty -Name hooks -Value ([pscustomobject]@{})
}

function New-CommandHook {
    param(
        [Parameter(Mandatory = $true)][string]$ScriptPath,
        [Parameter(Mandatory = $true)][int]$Timeout,
        [Parameter(Mandatory = $true)][string]$StatusMessage,
        [switch]$HookInvocation
    )
    $command = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $ScriptPath
    if ($HookInvocation) { $command += ' -HookInvocation' }
    return [pscustomobject]@{
        type = 'command'
        command = $command
        commandWindows = $command
        timeout = $Timeout
        statusMessage = $StatusMessage
    }
}

function Add-BridgeHook {
    param(
        [Parameter(Mandatory = $true)][string]$EventName,
        [Parameter(Mandatory = $true)][object]$Entry
    )
    $property = $config.hooks.PSObject.Properties[$EventName]
    # Do not assign an empty array through an `if` expression here. PowerShell
    # emits no pipeline object for that branch, turning the first `+=` into a
    # scalar PSCustomObject. Codex requires every event value to remain a JSON
    # matcher-group array even when it contains exactly one entry.
    $entries = @()
    if ($property) { $entries = @($property.Value) }
    $entries += $Entry
    if ($property) { $property.Value = $entries }
    else { $config.hooks | Add-Member -MemberType NoteProperty -Name $EventName -Value $entries }
}

function Test-BridgeCommandHook {
    param([object]$Hook, [Parameter(Mandatory = $true)][string]$ScriptPath)

    if (-not $Hook) { return $false }
    $baseCommand = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $ScriptPath
    foreach ($propertyName in @('command', 'commandWindows')) {
        $property = $Hook.PSObject.Properties[$propertyName]
        if (-not $property) { continue }
        $command = ([string]$property.Value).Trim()
        if ($command.Equals($baseCommand, [System.StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
        if ($command.StartsWith(($baseCommand + ' '), [System.StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
    }
    return $false
}

function Remove-BridgeHook {
    param(
        [Parameter(Mandatory = $true)][string]$EventName,
        [Parameter(Mandatory = $true)][string]$ScriptPath
    )

    $property = $config.hooks.PSObject.Properties[$EventName]
    if (-not $property) { return }
    $remainingEntries = @()
    foreach ($entry in @($property.Value)) {
        $hooksProperty = $entry.PSObject.Properties['hooks']
        if (-not $hooksProperty) {
            $remainingEntries += $entry
            continue
        }
        $remainingHooks = @(
            @($hooksProperty.Value) | Where-Object {
                -not (Test-BridgeCommandHook $_ $ScriptPath)
            }
        )
        if ($remainingHooks.Count -gt 0) {
            $hooksProperty.Value = $remainingHooks
            $remainingEntries += $entry
        }
    }
    $property.Value = $remainingEntries
}

Remove-BridgeHook 'SessionStart' $startTarget
Remove-BridgeHook 'SessionEnd' $stopTarget
Add-BridgeHook 'SessionStart' ([pscustomobject]@{
    matcher = 'startup|resume'
    hooks = @((New-CommandHook $startTarget 10 'Activating Feishu bridge lease' -HookInvocation))
})
Add-BridgeHook 'SessionEnd' ([pscustomobject]@{
    hooks = @((New-CommandHook $stopTarget 3 'Releasing Feishu bridge lease' -HookInvocation))
})

$hooksConfigTemporary = "$hooksConfigPath.tmp"
try {
    # Windows PowerShell 5.1's `Set-Content -Encoding utf8` writes a BOM.
    # Codex hook configuration is parsed as BOM-less JSON, so write explicit
    # UTF-8 without a BOM before atomically replacing the live file.
    $hooksJson = $config | ConvertTo-Json -Depth 30
    $utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($hooksConfigTemporary, $hooksJson, $utf8WithoutBom)
    Move-Item -LiteralPath $hooksConfigTemporary -Destination $hooksConfigPath -Force
} finally {
    Remove-Item -LiteralPath $hooksConfigTemporary -Force -ErrorAction SilentlyContinue
}
Write-Output "Registered lease-aware Feishu bridge hooks in $hooksConfigPath"
if ($HooksOnly) {
    Write-Output 'Hook-only refresh completed. Runtime code, bridge.env, and project rules were unchanged; no runtime manifest was signed.'
}
