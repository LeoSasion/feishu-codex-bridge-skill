[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Scope = 'doctor',

    [Parameter(Position = 1)]
    [string]$Action = 'doctor',

    [string]$ProjectRoot = (Get-Location).Path,

    [string]$ObsidianRoot,

    [switch]$Force,

    [switch]$SkipHooks
)

$ErrorActionPreference = 'Stop'

function Show-Usage {
    @'
Feishu Codex Bridge dispatcher

Usage:
  feishu-codex-bridge.ps1 feishu install
  feishu-codex-bridge.ps1 feishu configure
  feishu-codex-bridge.ps1 feishu login
  feishu-codex-bridge.ps1 feishu doctor
  feishu-codex-bridge.ps1 bridge install -ProjectRoot <path>
  feishu-codex-bridge.ps1 bridge start -ProjectRoot <path>
  feishu-codex-bridge.ps1 bridge stop -ProjectRoot <path>
  feishu-codex-bridge.ps1 bridge doctor -ProjectRoot <path>
  feishu-codex-bridge.ps1 obsidian connect -ProjectRoot <path> -ObsidianRoot <path>
  feishu-codex-bridge.ps1 obsidian doctor -ProjectRoot <path>
  feishu-codex-bridge.ps1 doctor -ProjectRoot <path>

The Feishu path does not configure Obsidian. Use obsidian connect only after
the user explicitly requests a knowledge-base or local-note connection.
'@ | Write-Output
}

function Show-WelcomeAndMountConsent {
    @'
欢迎使用 Codex 飞书机器人。

安装飞书 CLI 后，可以把飞书私聊和群聊 @ 消息挂载到当前 Codex 项目；每个聊天会对应一个持久的 Codex 会话，并保留上下文。

挂载只会写入当前项目的桥接脚本和 Codex hooks，不会自动连接 Obsidian，也不会自动申请或授予飞书权限。

是否同意挂载？请在 Codex 对话中明确回复“同意挂载”“确认”或“是”。在获得明确同意前，不会运行 bridge install，也不会启动监听器。
'@ | Write-Output
}

function Require-Command {
    param([Parameter(Mandatory = $true)][string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command not found: $Name"
    }
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList
    )

    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "$FilePath failed with exit code $LASTEXITCODE"
    }
}

function Resolve-Project {
    return (Resolve-Path -LiteralPath $ProjectRoot).Path
}

function Invoke-FeishuInstall {
    Require-Command 'npm'
    Require-Command 'npx'
    Show-WelcomeAndMountConsent
    Write-Output 'Installing the official Feishu CLI package.'
    Invoke-Checked 'npm' @('install', '-g', '@larksuite/cli')
    Write-Output 'Installing the official Feishu CLI Skill.'
    Invoke-Checked 'npx' @('-y', 'skills', 'add', 'https://open.feishu.cn', '--skill', '-y')
    Write-Output ''
    Write-Output 'Feishu CLI installation completed. The bridge is not mounted yet.'
    Show-WelcomeAndMountConsent
}

function Invoke-FeishuConfigure {
    Require-Command 'lark-cli'
    Invoke-Checked 'lark-cli' @('config', 'init', '--new')
}

function Invoke-FeishuLogin {
    Require-Command 'lark-cli'
    Invoke-Checked 'lark-cli' @('auth', 'login', '--recommend')
}

function Invoke-FeishuDoctor {
    Require-Command 'lark-cli'
    Write-Output 'Checking Feishu authentication and configuration.'
    Invoke-Checked 'lark-cli' @('auth', 'status', '--json', '--verify')
}

function Invoke-BridgeInstall {
    $resolvedProjectRoot = Resolve-Project
    $installer = Join-Path $PSScriptRoot 'install-feishu-codex-bridge.ps1'
    $arguments = @{ ProjectRoot = $resolvedProjectRoot }
    if ($Force) { $arguments['Force'] = $true }
    if ($SkipHooks) { $arguments['SkipHooks'] = $true }
    & $installer @arguments
    if (-not $?) {
        throw "$installer failed"
    }
}

function Invoke-BridgeStart {
    $resolvedProjectRoot = Resolve-Project
    $scriptPath = Join-Path $resolvedProjectRoot '.codex\hooks\start-feishu-codex-bridge.ps1'
    if (-not (Test-Path -LiteralPath $scriptPath)) {
        throw "Bridge is not installed: $scriptPath"
    }
    & $scriptPath
}

function Invoke-BridgeStop {
    $resolvedProjectRoot = Resolve-Project
    $scriptPath = Join-Path $resolvedProjectRoot '.codex\hooks\stop-feishu-codex-bridge.ps1'
    if (-not (Test-Path -LiteralPath $scriptPath)) {
        throw "Bridge is not installed: $scriptPath"
    }
    & $scriptPath
}

function Invoke-BridgeDoctor {
    $resolvedProjectRoot = Resolve-Project
    $runtimeRoot = Join-Path $resolvedProjectRoot '.codex\feishu-bridge'
    $hooksConfig = Join-Path $resolvedProjectRoot '.codex\hooks.json'
    $bridge = Join-Path $runtimeRoot 'bridge.py'
    $startHook = Join-Path $resolvedProjectRoot '.codex\hooks\start-feishu-codex-bridge.ps1'
    $stopHook = Join-Path $resolvedProjectRoot '.codex\hooks\stop-feishu-codex-bridge.ps1'

    foreach ($path in @($bridge, $startHook, $stopHook, $hooksConfig)) {
        if (-not (Test-Path -LiteralPath $path)) {
            throw "Missing bridge artifact: $path"
        }
    }
    Write-Output "Bridge files and hook registration are present under $resolvedProjectRoot."
    $envPath = Join-Path $runtimeRoot 'bridge.env'
    if (Test-Path -LiteralPath $envPath) {
        $hasObsidian = Select-String -LiteralPath $envPath -Pattern '^\s*CODEX_BRIDGE_OBSIDIAN_ROOT=' -Quiet
        if ($hasObsidian) {
            Write-Output 'Optional Obsidian configuration is present; use obsidian doctor to inspect it.'
        } else {
            Write-Output 'Obsidian retrieval is not configured.'
        }
    } else {
        Write-Output 'Obsidian retrieval is not configured.'
    }
}

function Invoke-ObsidianConnect {
    if (-not $ObsidianRoot) {
        throw 'ObsidianRoot is required. Only run obsidian connect after an explicit knowledge-base request.'
    }
    $resolvedObsidianRoot = (Resolve-Path -LiteralPath $ObsidianRoot).Path
    $resolvedProjectRoot = Resolve-Project
    $installer = Join-Path $PSScriptRoot 'install-feishu-codex-bridge.ps1'
    $arguments = @{ ProjectRoot = $resolvedProjectRoot; ObsidianRoot = $resolvedObsidianRoot }
    if ($Force) { $arguments['Force'] = $true }
    if ($SkipHooks) { $arguments['SkipHooks'] = $true }
    & $installer @arguments
    if (-not $?) {
        throw "$installer failed"
    }
}

function Invoke-ObsidianDoctor {
    $resolvedProjectRoot = Resolve-Project
    $envPath = Join-Path $resolvedProjectRoot '.codex\feishu-bridge\bridge.env'
    if (-not (Test-Path -LiteralPath $envPath)) {
        throw "No Obsidian connection is configured: $envPath"
    }
    $line = Get-Content -LiteralPath $envPath | Where-Object { $_ -match '^\s*CODEX_BRIDGE_OBSIDIAN_ROOT=(.+)$' } | Select-Object -First 1
    if (-not $line) {
        throw 'No CODEX_BRIDGE_OBSIDIAN_ROOT entry is configured.'
    }
    $configuredRoot = ($line -replace '^\s*CODEX_BRIDGE_OBSIDIAN_ROOT=', '').Trim()
    if (-not (Test-Path -LiteralPath $configuredRoot -PathType Container)) {
        throw "Configured Obsidian root does not exist: $configuredRoot"
    }
    $noteCount = @(Get-ChildItem -LiteralPath $configuredRoot -Filter '*.md' -File -Recurse -ErrorAction SilentlyContinue).Count
    Write-Output "Obsidian Markdown root is readable: $configuredRoot ($noteCount notes)."
}

$scopeName = $Scope.ToLowerInvariant()
$actionName = $Action.ToLowerInvariant()

switch ($scopeName) {
    'help' { Show-Usage; exit 0 }
    '-help' { Show-Usage; exit 0 }
    '--help' { Show-Usage; exit 0 }
    'doctor' {
        Invoke-FeishuDoctor
        Invoke-BridgeDoctor
        Write-Output 'Obsidian was not inspected; run obsidian doctor only when explicitly needed.'
        exit 0
    }
    'feishu' {
        switch ($actionName) {
            'install' { Invoke-FeishuInstall; break }
            'configure' { Invoke-FeishuConfigure; break }
            'login' { Invoke-FeishuLogin; break }
            'doctor' { Invoke-FeishuDoctor; break }
            default { Show-Usage; throw "Unknown Feishu subcommand: $Action" }
        }
        break
    }
    'bridge' {
        switch ($actionName) {
            'install' { Invoke-BridgeInstall; break }
            'start' { Invoke-BridgeStart; break }
            'stop' { Invoke-BridgeStop; break }
            'doctor' { Invoke-BridgeDoctor; break }
            default { Show-Usage; throw "Unknown bridge subcommand: $Action" }
        }
        break
    }
    'obsidian' {
        switch ($actionName) {
            'connect' { Invoke-ObsidianConnect; break }
            'doctor' { Invoke-ObsidianDoctor; break }
            default { Show-Usage; throw "Unknown Obsidian subcommand: $Action" }
        }
        break
    }
    default { Show-Usage; throw "Unknown command scope: $Scope" }
}
