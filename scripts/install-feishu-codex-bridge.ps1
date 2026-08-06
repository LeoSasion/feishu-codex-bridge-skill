[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot,

    [string]$ObsidianRoot,

    [switch]$Force,

    [switch]$SkipHooks
)

$ErrorActionPreference = 'Stop'

$resolvedProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
$skillRoot = Split-Path -Parent $PSScriptRoot
$runtimeRoot = Join-Path $resolvedProjectRoot '.codex\feishu-bridge'
$hooksRoot = Join-Path $resolvedProjectRoot '.codex\hooks'
$bridgeTarget = Join-Path $runtimeRoot 'bridge.py'
$startTarget = Join-Path $hooksRoot 'start-feishu-codex-bridge.ps1'
$stopTarget = Join-Path $hooksRoot 'stop-feishu-codex-bridge.ps1'
$envTarget = Join-Path $runtimeRoot 'bridge.env'
$hooksConfigPath = Join-Path $resolvedProjectRoot '.codex\hooks.json'

New-Item -ItemType Directory -Force -Path $runtimeRoot, $hooksRoot | Out-Null

function Copy-BridgeFile {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Target
    )

    if ((Test-Path -LiteralPath $Target) -and -not $Force) {
        Write-Output "Preserved existing $Target"
        return
    }
    Copy-Item -LiteralPath $Source -Destination $Target -Force
    Write-Output "Installed $Target"
}

Copy-BridgeFile (Join-Path $skillRoot 'scripts\bridge.py') $bridgeTarget
Copy-BridgeFile (Join-Path $skillRoot 'scripts\start-feishu-codex-bridge.ps1') $startTarget
Copy-BridgeFile (Join-Path $skillRoot 'scripts\stop-feishu-codex-bridge.ps1') $stopTarget

if ($ObsidianRoot) {
    $resolvedObsidianRoot = (Resolve-Path -LiteralPath $ObsidianRoot).Path
    if ((Test-Path -LiteralPath $envTarget) -and -not $Force) {
        Write-Output "Preserved existing $envTarget"
    } else {
        @"
# Optional bridge configuration. Do not put Feishu secrets in this file.
CODEX_BRIDGE_OBSIDIAN_ROOT=$resolvedObsidianRoot
# CODEX_BRIDGE_MODEL=gpt-5.6-luna
# CODEX_BRIDGE_MODEL_CONTEXT_TOKENS=1050000
# CODEX_BRIDGE_DESKTOP_REFRESH=1
"@ | Set-Content -LiteralPath $envTarget -Encoding utf8
        Write-Output "Wrote $envTarget"
    }
}

if ($SkipHooks) {
    Write-Output 'Skipped .codex/hooks.json registration.'
    exit 0
}

if (Test-Path -LiteralPath $hooksConfigPath) {
    $config = Get-Content -LiteralPath $hooksConfigPath -Raw | ConvertFrom-Json
} else {
    $config = [pscustomobject]@{
        description = 'Start and stop the local Feishu-to-Codex bridge with this Codex project session.'
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
        [Parameter(Mandatory = $true)][string]$StatusMessage
    )

    $command = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $ScriptPath
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
    if ($property) {
        $entries = @($property.Value)
    } else {
        $entries = @()
    }
    $entries += $Entry
    if ($property) {
        $property.Value = $entries
    } else {
        $config.hooks | Add-Member -MemberType NoteProperty -Name $EventName -Value $entries
    }
}

$serialized = $config | ConvertTo-Json -Depth 20
if ($serialized -notmatch [regex]::Escape($startTarget)) {
    Add-BridgeHook 'SessionStart' ([pscustomobject]@{
        matcher = 'startup|resume'
        hooks = @(
            (New-CommandHook $startTarget 10 'Starting Feishu Codex bridge')
        )
    })
}

$serialized = $config | ConvertTo-Json -Depth 20
if ($serialized -notmatch [regex]::Escape($stopTarget)) {
    Add-BridgeHook 'SessionEnd' ([pscustomobject]@{
        hooks = @(
            (New-CommandHook $stopTarget 15 'Stopping Feishu Codex bridge')
        )
    })
}

$config | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $hooksConfigPath -Encoding utf8
Write-Output "Registered Feishu bridge hooks in $hooksConfigPath"
