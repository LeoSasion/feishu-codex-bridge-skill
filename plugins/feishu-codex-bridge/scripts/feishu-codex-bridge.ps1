[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Scope = 'doctor',

    [Parameter(Position = 1)]
    [string]$Action = 'doctor',

    [string]$ProjectRoot = (Get-Location).Path,

    [ValidateSet('compat', 'locked')]
    [string]$AccessMode,

    [string]$OwnerOpenId,

    [string]$AdminOpenIds,

    [string]$AllowedUserOpenIds,

    [string]$AllowedChatIds,

    [string]$AuthScope,

    [string]$AuthDomain,

    [switch]$Recommend,

    # Deprecated compatibility switch. Feishu Desktop installation is already
    # authorized by an in-scope owner request and this value is never a gate.
    [switch]$DesktopInstallConsent,

    [string]$DeviceCode,

    [switch]$NoWait,

    [int]$Tail = 80,

    [switch]$Json,

    [switch]$RunTests,

    [switch]$ExternalTestRunnerAcknowledged
)

$ErrorActionPreference = 'Stop'

function Show-Usage {
    @'
Feishu Codex Bridge

Usage:
  feishu-codex-bridge.ps1 feishu install|configure|doctor
  feishu-codex-bridge.ps1 feishu desktop-status
  feishu-codex-bridge.ps1 feishu desktop-install [-DesktopInstallConsent]
  feishu-codex-bridge.ps1 feishu login -Recommend -NoWait
  feishu-codex-bridge.ps1 feishu login -AuthScope <scope> -NoWait
  feishu-codex-bridge.ps1 feishu login -AuthDomain <domain> -NoWait
  feishu-codex-bridge.ps1 feishu login -DeviceCode <device_code>
  feishu-codex-bridge.ps1 bridge init|install|start|stop|restart
  feishu-codex-bridge.ps1 bridge hooks
  feishu-codex-bridge.ps1 bridge upgrade
  feishu-codex-bridge.ps1 bridge final-callback-status
  feishu-codex-bridge.ps1 bridge final-callback-register
  feishu-codex-bridge.ps1 bridge final-callback-unregister
  feishu-codex-bridge.ps1 bridge status|doctor|readiness|validate [-Json]
  feishu-codex-bridge.ps1 bridge preflight|logs|test
  feishu-codex-bridge.ps1 bridge test -RunTests -ExternalTestRunnerAcknowledged
  feishu-codex-bridge.ps1 bridge access -AccessMode locked -OwnerOpenId <open_id>
  feishu-codex-bridge.ps1 doctor

Knowledge bases, including Obsidian vaults, belong to the bound Codex project's
directory. The bridge has no knowledge-base command or setting.
'@ | Write-Output
}

function Show-WelcomeAndAutomaticWorkflow {
    @'
欢迎使用 Codex 飞书机器人。

飞书 CLI 安装完成后，可以把私聊和群聊 @ 消息挂载到当前 Codex 项目。每个私聊、群聊或群话题都能映射到一个可在 Codex Desktop 查看和继续的持久会话。

首次消息会提示发送 /init，随后通过对话菜单查看并选择一个现有、未归档的 Codex Desktop 任务。当前 MVP 不创建、恢复、归档或压缩任务，也不在飞书里改变回复方式。Bridge 只负责持久收件、事件触发的一次性 Beeper Dial 与最终回复；所有退役的 Beeper producer surface 均保持永久不可执行。Bridge 不会打开或占用 Responder 任务，业务执行与最终答案始终由绑定的 Responder 所有。默认只回传最终答案，不发送思考或工具过程。

Bridge 挂载会在当前项目写入桥接运行文件和 Codex hooks，也不会替用户授予飞书权限。用户提出本项目内的安装、配置、生命周期、Hook、插件、Schema 或只读诊断目标后，所需的精确本地动作会在该请求范围内连续自动执行并逐步验收，不设置逐动作人工审批门禁，也不调用外部授权中继；这不包括任何退役 Beeper producer surface 的创建、挂载、rehydrate、page、激活、恢复或 canary。Responder 自己的模型、推理、沙箱、插件和知识库设置保持不变；Bridge 不安装、注册或检索 Obsidian。

自动执行不会扩大请求范围：每次写入前仍核对精确目标、路径、版本、进程身份、影响范围和恢复路径；发布、凭据变更、跨项目修改或请求范围外的破坏性操作仍需用户明确提出。飞书二维码、OAuth、UAC 或身份页面若真实要求真人操作，只交还该不可自动化的外部交互，随后继续其余已请求流程。locked access 在至少配置一个经验证身份前保持拒绝全部事件。
'@ | Write-Output
}

function Require-Command {
    param([Parameter(Mandatory = $true)][string]$Name)
    Update-ProcessPathFromEnvironment
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
    if ($LASTEXITCODE -ne 0) { throw "$FilePath failed with exit code $LASTEXITCODE" }
}

function Resolve-Project {
    return (Resolve-Path -LiteralPath $ProjectRoot).Path
}

function Get-BridgeProcessIdentity {
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [Parameter(Mandatory = $true)][string]$BridgeScript
    )
    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if (-not $process) {
        return [pscustomobject]@{
            Exists = $false
            Verified = $true
            IsBridge = $false
            Process = $null
            ProcessName = ''
            Reason = 'process_absent'
        }
    }
    $processName = [string]$process.ProcessName
    if ($processName -notmatch '(?i)^python(?:w|[0-9.]*)?$') {
        return [pscustomobject]@{
            Exists = $true
            Verified = $true
            IsBridge = $false
            Process = $process
            ProcessName = $processName
            Reason = 'non_python_process'
        }
    }
    try {
        $record = Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f $ProcessId) -ErrorAction Stop
    } catch {
        return [pscustomobject]@{
            Exists = $true
            Verified = $false
            IsBridge = $false
            Process = $process
            ProcessName = $processName
            Reason = 'command_line_unavailable'
        }
    }
    $commandLine = if ($record) { [string]$record.CommandLine } else { '' }
    if ([string]::IsNullOrWhiteSpace($commandLine)) {
        return [pscustomobject]@{
            Exists = $true
            Verified = $false
            IsBridge = $false
            Process = $process
            ProcessName = $processName
            Reason = 'command_line_unavailable'
        }
    }
    $expected = [System.IO.Path]::GetFullPath($BridgeScript).Replace('/', '\')
    $observed = $commandLine.Replace('/', '\')
    $matches = $observed.IndexOf(
        $expected,
        [System.StringComparison]::OrdinalIgnoreCase
    ) -ge 0
    return [pscustomobject]@{
        Exists = $true
        Verified = $true
        IsBridge = $matches
        Process = $process
        ProcessName = $processName
        Reason = $(if ($matches) { 'exact_bridge_script' } else { 'different_python_command' })
    }
}

function Get-BridgePidState {
    $paths = Get-BridgePaths
    if (-not (Test-Path -LiteralPath $paths.Pid -PathType Leaf)) {
        return [pscustomobject]@{ HasPidFile = $false; Pid = 0; Identity = $null }
    }
    $pidValue = 0
    if (-not [int]::TryParse((Get-Content -LiteralPath $paths.Pid -Raw).Trim(), [ref]$pidValue) -or
        $pidValue -le 0) {
        return [pscustomobject]@{ HasPidFile = $true; Pid = 0; Identity = $null }
    }
    $identity = Get-BridgeProcessIdentity `
        -ProcessId $pidValue `
        -BridgeScript (Join-Path $paths.Runtime 'bridge.py')
    return [pscustomobject]@{ HasPidFile = $true; Pid = $pidValue; Identity = $identity }
}

function Assert-BridgeStopped {
    $state = Get-BridgePidState
    if (-not $state.HasPidFile -or $state.Pid -le 0 -or -not $state.Identity.Exists) {
        return
    }
    if (-not $state.Identity.Verified) {
        throw "Bridge PID $($state.Pid) exists, but its command line could not be verified; refusing a lifecycle mutation."
    }
    if ($state.Identity.IsBridge) {
        throw "Bridge must be stopped before this lifecycle mutation; stop this exact verified Bridge as a separate observable transaction (PID $($state.Pid))."
    }
}

function Get-BridgePaths {
    $resolved = Resolve-Project
    return [pscustomobject]@{
        Project = $resolved
        Runtime = Join-Path $resolved '.codex\feishu-bridge'
        Start = Join-Path $resolved '.codex\hooks\start-feishu-codex-bridge.ps1'
        Stop = Join-Path $resolved '.codex\hooks\stop-feishu-codex-bridge.ps1'
        Health = Join-Path $resolved '.codex\feishu-bridge\health.json'
        Pid = Join-Path $resolved '.codex\feishu-bridge\bridge.pid'
        Env = Join-Path $resolved '.codex\feishu-bridge\bridge.env'
        Log = Join-Path $resolved '.codex\feishu-bridge\bridge.log'
    }
}

function Set-BridgeEnvValue {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Value
    )
    if ($Name -notmatch '^CODEX_BRIDGE_[A-Z0-9_]+$') {
        throw "Invalid bridge environment key: $Name"
    }
    if ($Value -match '[\r\n\x00]') {
        throw "Bridge environment value for $Name must be one line and contain no NUL byte."
    }
    $paths = Get-BridgePaths
    if (-not (Test-Path -LiteralPath $paths.Env -PathType Leaf)) {
        throw "Bridge environment is not installed: $($paths.Env)"
    }

    $pattern = '^\s*#?\s*' + [regex]::Escape($Name) + '\s*='
    $replacement = "$Name=$Value"
    $updated = $false
    $outputLines = @()
    foreach ($line in @(Get-Content -LiteralPath $paths.Env)) {
        if ($line -match $pattern) {
            if (-not $updated) {
                $outputLines += $replacement
                $updated = $true
            }
            continue
        }
        $outputLines += $line
    }
    if (-not $updated) { $outputLines += $replacement }

    $temporary = Join-Path (Split-Path -Parent $paths.Env) ([System.IO.Path]::GetRandomFileName())
    try {
        $outputLines | Set-Content -LiteralPath $temporary -Encoding utf8
        Move-Item -LiteralPath $temporary -Destination $paths.Env -Force
    } finally {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    }
}

function Get-BridgeEnvSemanticIssues {
    param([Parameter(Mandatory = $true)][System.Collections.IDictionary]$Values)

    $issues = New-Object System.Collections.Generic.List[string]
    $booleanValues = @('0', '1', 'false', 'true', 'no', 'yes', 'off', 'on')
    foreach ($name in @(
        'CODEX_BRIDGE_DOWNLOAD_RESOURCES'
    )) {
        if (-not $Values.Contains($name)) { continue }
        $value = ([string]$Values[$name]).Trim().ToLowerInvariant()
        if ($value -notin $booleanValues) {
            $issues.Add("$name must be an explicit boolean: 0/1, false/true, no/yes, or off/on.")
        }
    }

    $enumSpecs = @(
        [pscustomobject]@{ Name = 'CODEX_BRIDGE_ACCESS_MODE'; Values = @('locked', 'compat') },
        [pscustomobject]@{ Name = 'CODEX_BRIDGE_LIFECYCLE_MODE'; Values = @('hooks', 'manual') },
        [pscustomobject]@{ Name = 'CODEX_BRIDGE_REPLY_FORMAT'; Values = @('text', 'markdown') }
    )
    foreach ($spec in $enumSpecs) {
        if (-not $Values.Contains($spec.Name)) { continue }
        $value = ([string]$Values[$spec.Name]).Trim().ToLowerInvariant()
        if ($value -notin $spec.Values) {
            $issues.Add("$($spec.Name) must be one of: $($spec.Values -join ', ').")
        }
    }

    $integerSpecs = @(
        [pscustomobject]@{ Name = 'CODEX_BRIDGE_EVENT_READY_TIMEOUT'; Minimum = 5L; Maximum = 120L },
        [pscustomobject]@{ Name = 'CODEX_BRIDGE_BEEPER_TIMEOUT'; Minimum = 30L; Maximum = 86400L },
        [pscustomobject]@{ Name = 'CODEX_BRIDGE_BEEPER_CLAIM_TTL'; Minimum = 60L; Maximum = 86400L },
        [pscustomobject]@{ Name = 'CODEX_BRIDGE_BEEPER_RETENTION_HOURS'; Minimum = 1L; Maximum = 8760L },
        [pscustomobject]@{ Name = 'CODEX_BRIDGE_BEEPER_DIAL_TTL'; Minimum = 60L; Maximum = 900L },
        [pscustomobject]@{ Name = 'CODEX_BRIDGE_BEEPER_GRACE_MAX_SECONDS'; Minimum = 0L; Maximum = 60L },
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
            $issues.Add("$($spec.Name) must be an integer.")
            continue
        }
        if ($parsed -lt $spec.Minimum -or $parsed -gt $spec.Maximum) {
            $issues.Add("$($spec.Name) must be within $($spec.Minimum)..$($spec.Maximum).")
        }
    }
    return $issues
}

function Get-BridgeEnvFileState {
    $paths = Get-BridgePaths
    $values = @{}
    $issues = New-Object System.Collections.Generic.List[string]
    if (-not (Test-Path -LiteralPath $paths.Env -PathType Leaf)) {
        $issues.Add('bridge.env is missing.')
        return [pscustomobject]@{ Values = $values; Issues = $issues }
    }
    try {
        $envLines = @(Get-Content -LiteralPath $paths.Env -ErrorAction Stop)
    } catch {
        $issues.Add("bridge.env could not be read: $($_.Exception.Message)")
        return [pscustomobject]@{ Values = $values; Issues = $issues }
    }
    $lineNumber = 0
    foreach ($rawLine in $envLines) {
        $lineNumber += 1
        $line = $rawLine.Trim()
        if ($line.Length -eq 0 -or $line.StartsWith('#')) { continue }
        if ($line -match '\x00') {
            $issues.Add("line $lineNumber contains a NUL byte.")
            continue
        }
        $parts = $line -split '=', 2
        if ($parts.Count -ne 2) {
            $issues.Add("line $lineNumber is not NAME=VALUE.")
            continue
        }
        $name = $parts[0].Trim()
        if ($name -cnotmatch '^CODEX_BRIDGE_[A-Z0-9_]+$') {
            $issues.Add("line $lineNumber has an unsupported key.")
            continue
        }
        if ($values.ContainsKey($name)) {
            $issues.Add("duplicate key at line ${lineNumber}: $name")
            continue
        }
        $values[$name] = $parts[1].Trim()
    }
    foreach ($semanticIssue in @(Get-BridgeEnvSemanticIssues -Values $values)) {
        $issues.Add([string]$semanticIssue)
    }
    return [pscustomobject]@{ Values = $values; Issues = $issues }
}

function Assert-BridgeIdentifierList {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Value,
        [Parameter(Mandatory = $true)][ValidateSet('ou_', 'oc_')][string]$Prefix,
        [switch]$Single
    )
    if ([string]::IsNullOrWhiteSpace($Value)) { return }
    if ($Value -match '[\r\n\x00]') {
        throw "$Name must be one line and contain no NUL byte."
    }
    $tokens = @($Value -split ',' | ForEach-Object { $_.Trim() })
    if ($Single -and $tokens.Count -ne 1) {
        throw "$Name accepts exactly one Feishu ID."
    }
    $pattern = '^' + [regex]::Escape($Prefix) + '[A-Za-z0-9_-]+$'
    if ($tokens.Count -eq 0 -or @($tokens | Where-Object { -not $_ -or $_ -notmatch $pattern }).Count -gt 0) {
        throw "$Name must contain only comma-separated Feishu IDs beginning with $Prefix"
    }
}

function Invoke-FeishuInstall {
    Require-Command 'npm'
    Require-Command 'npx'
    Show-WelcomeAndAutomaticWorkflow
    Write-Output 'Installing the official Feishu CLI package.'
    Invoke-Checked 'npm' @('install', '-g', '@larksuite/cli')
    Write-Output 'Installing the official Feishu CLI Skill.'
    Invoke-Checked 'npx' @('-y', 'skills', 'add', 'https://open.feishu.cn', '--skill', '-y')
    Write-Output 'Feishu CLI installation completed. The bridge is not mounted yet.'
    Show-WelcomeAndAutomaticWorkflow
}

function Invoke-FeishuConfigure {
    Require-Command 'lark-cli'
    Invoke-Checked 'lark-cli' @('config', 'init', '--new')
}

function Invoke-FeishuLogin {
    Require-Command 'lark-cli'
    $selectorCount = @(
        [bool]$AuthScope,
        [bool]$AuthDomain,
        [bool]$Recommend,
        [bool]$DeviceCode
    ).Where({ $_ }).Count
    if ($selectorCount -ne 1) {
        throw 'Choose exactly one of AuthScope, AuthDomain, Recommend, or DeviceCode.'
    }
    if ($DeviceCode) {
        if ($NoWait) {
            throw 'Do not combine -DeviceCode with -NoWait.'
        }
        Invoke-Checked 'lark-cli' @('auth', 'login', '--device-code', $DeviceCode)
        return
    }
    if (-not $NoWait) {
        throw 'Feishu login requires -NoWait so the OAuth URL can be shown before the next step.'
    }
    $arguments = @('auth', 'login')
    if ($AuthScope) { $arguments += @('--scope', $AuthScope) }
    if ($AuthDomain) { $arguments += @('--domain', $AuthDomain) }
    if ($Recommend) { $arguments += '--recommend' }
    $arguments += @('--no-wait', '--json')
    Invoke-Checked 'lark-cli' $arguments
}

function Invoke-FeishuDoctor {
    Require-Command 'lark-cli'
    Write-Output 'Checking Feishu bot authentication and granted capabilities.'
    Invoke-Checked 'lark-cli' @('auth', 'status', '--json', '--verify')
}

function Get-FeishuDesktopExecutable {
    $candidates = New-Object System.Collections.Generic.List[string]

    foreach ($process in @(Get-Process -Name Feishu -ErrorAction SilentlyContinue)) {
        try {
            if ($process.Path) { $candidates.Add([string]$process.Path) }
        } catch {
            # Protected process metadata is not proof that the client is absent.
        }
    }

    foreach ($knownPath in @(
        (Join-Path $env:LOCALAPPDATA 'Feishu\Feishu.exe'),
        (Join-Path $env:LOCALAPPDATA 'Feishu\app\Feishu.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Feishu\Feishu.exe'),
        (Join-Path $env:ProgramFiles 'Feishu\Feishu.exe')
    )) {
        if ($knownPath) { $candidates.Add($knownPath) }
    }

    foreach ($uninstallRoot in @(
        'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'
    )) {
        foreach ($entry in @(Get-ItemProperty -Path $uninstallRoot -ErrorAction SilentlyContinue)) {
            if ([string]$entry.DisplayName -notmatch '^(飞书|Feishu)(\s|$)') { continue }
            if ($entry.DisplayIcon) { $candidates.Add([string]$entry.DisplayIcon) }
            if ($entry.InstallLocation) {
                $candidates.Add((Join-Path ([string]$entry.InstallLocation) 'Feishu.exe'))
                $candidates.Add((Join-Path ([string]$entry.InstallLocation) 'app\Feishu.exe'))
            }
        }
    }

    $seen = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($candidate in $candidates) {
        $clean = ([string]$candidate).Trim().Trim('"') -replace ',\d+$', ''
        if (-not $clean -or -not $seen.Add($clean)) { continue }
        if (Test-Path -LiteralPath $clean -PathType Leaf) {
            return (Resolve-Path -LiteralPath $clean).Path
        }
    }
    return $null
}

function Invoke-FeishuDesktopStatus {
    $executable = Get-FeishuDesktopExecutable
    $processes = @(Get-Process -Name Feishu -ErrorAction SilentlyContinue)
    if ($executable) {
        Write-Output ("[PASS] Feishu Desktop is installed: {0}" -f $executable)
    } else {
        Write-Output '[MISSING] Feishu Desktop was not found in running processes, known install paths, or uninstall registry entries.'
    }
    if ($processes.Count -gt 0) {
        Write-Output ("[PASS] Feishu Desktop is running (process count: {0})." -f $processes.Count)
    } else {
        Write-Output '[INFO] Feishu Desktop is not running.'
    }
    Write-Output '[PENDING] Installation, process, and cached files do not prove login. Inspect the client UI: a main workspace proves logged-in; a QR/account screen proves logged-out; otherwise report unknown.'
}

function Invoke-FeishuDesktopInstall {
    if ($DesktopInstallConsent) {
        Write-Output '[INFO] -DesktopInstallConsent is deprecated and is a compatibility no-op; the in-scope desktop-install request follows the automatic verified path.'
    }

    $existing = Get-FeishuDesktopExecutable
    if ($existing) {
        Write-Output ("Feishu Desktop is already installed: {0}" -f $existing)
        Invoke-FeishuDesktopStatus
        return
    }

    $platform = if ([Environment]::Is64BitOperatingSystem) { 16 } else { 7 }
    $metadataEndpoint = "https://www.feishu.cn/api/package_info?platform=$platform"
    Write-Output ("Querying official Feishu package metadata: {0}" -f $metadataEndpoint)
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    $response = Invoke-RestMethod -Method Get -Uri $metadataEndpoint
    if ($null -eq $response -or [int]$response.code -ne 0 -or -not $response.data.download_link) {
        throw 'The official Feishu package metadata endpoint returned no usable download.'
    }

    $downloadUri = [Uri]([string]$response.data.download_link)
    if ($downloadUri.Scheme -ne 'https' -or $downloadUri.Host -notmatch '(^|\.)feishucdn\.com$') {
        throw "Refusing an unexpected Feishu download origin: $($downloadUri.Scheme)://$($downloadUri.Host)"
    }
    $expectedMd5 = ([string]$response.data.hash).Trim().ToLowerInvariant()
    if ($expectedMd5 -notmatch '^[0-9a-f]{32}$') {
        throw 'The official Feishu package metadata did not include a valid MD5 value.'
    }

    $systemTemp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
    $runRoot = Join-Path $systemTemp ('feishu-codex-bridge-client-' + [Guid]::NewGuid().ToString('N'))
    $resolvedRunRoot = [IO.Path]::GetFullPath($runRoot)
    if (-not $resolvedRunRoot.StartsWith($systemTemp + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing unsafe temporary directory: $resolvedRunRoot"
    }
    New-Item -ItemType Directory -Path $resolvedRunRoot | Out-Null

    try {
        $installerName = [IO.Path]::GetFileName($downloadUri.AbsolutePath)
        if (-not $installerName.EndsWith('.exe', [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing a non-executable Feishu package name: $installerName"
        }
        $installerPath = Join-Path $resolvedRunRoot $installerName
        $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
        if ($curl) {
            Invoke-Checked $curl.Source @('-L', '--fail', '--silent', '--show-error', '--output', $installerPath, $downloadUri.AbsoluteUri)
        } else {
            Invoke-WebRequest -UseBasicParsing -Uri $downloadUri.AbsoluteUri -OutFile $installerPath
        }

        $actualMd5 = (Get-FileHash -LiteralPath $installerPath -Algorithm MD5).Hash.ToLowerInvariant()
        if ($actualMd5 -ne $expectedMd5) {
            throw "Feishu installer hash mismatch. Expected $expectedMd5; got $actualMd5."
        }
        $signature = Get-AuthenticodeSignature -LiteralPath $installerPath
        if ($signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid) {
            throw "Feishu installer Authenticode signature is not valid: $($signature.Status) $($signature.StatusMessage)"
        }
        Write-Output ("Verified Feishu {0}; signer: {1}" -f $response.data.version_number, $signature.SignerCertificate.Subject)

        $installer = Start-Process -FilePath $installerPath -ArgumentList '--command=quiet_install' -Wait -PassThru -WindowStyle Hidden
        if ($installer.ExitCode -ne 0) {
            throw "Feishu installer exited with code $($installer.ExitCode)."
        }
        $deadline = [DateTime]::UtcNow.AddSeconds(45)
        do {
            $installed = Get-FeishuDesktopExecutable
            if ($installed) { break }
            Start-Sleep -Seconds 2
        } while ([DateTime]::UtcNow -lt $deadline)
        if (-not $installed) {
            throw 'Feishu installer completed, but no installed client executable was found within 45 seconds.'
        }
        Write-Output ("Feishu Desktop installation verified: {0}" -f $installed)
        Write-Output 'Login is not inferred from installation. Launch and inspect the client UI; the user must complete QR/account authentication.'
    } finally {
        if (Test-Path -LiteralPath $resolvedRunRoot -PathType Container) {
            Remove-Item -LiteralPath $resolvedRunRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

function Invoke-AgentsInit {
    param([switch]$Check)
    $resolvedProjectRoot = Resolve-Project
    $mergeScript = Join-Path $PSScriptRoot 'merge-agents-rules.ps1'
    if (-not (Test-Path -LiteralPath $mergeScript -PathType Leaf)) {
        throw "AGENTS.md merge helper is missing: $mergeScript"
    }
    $arguments = @{ ProjectRoot = $resolvedProjectRoot }
    if ($Check) { $arguments['Check'] = $true }
    & $mergeScript @arguments
    if (-not $?) { throw "$mergeScript failed" }
}

function Invoke-Installer {
    param([switch]$Upgrade)
    $resolvedProjectRoot = Resolve-Project
    $installer = Join-Path $PSScriptRoot 'install-feishu-codex-bridge.ps1'
    $arguments = @{ ProjectRoot = $resolvedProjectRoot }
    if ($Upgrade) {
        Assert-BridgeStopped
        # Public bridge upgrades are runtime-only. Hooks, project rules, config,
        # and restart remain separately observable transactions that the
        # controlling task executes and verifies without pausing for approval.
        $arguments['Force'] = $true
        $arguments['SkipHooks'] = $true
        $arguments['SkipRuntimeConfig'] = $true
    } else {
        $paths = Get-BridgePaths
        $runtimeResidue = @()
        if (Test-Path -LiteralPath $paths.Runtime -PathType Container) {
            $runtimeResidue = @(
                Get-ChildItem -LiteralPath $paths.Runtime -Force |
                    Where-Object { $_.Name -ne 'backups' }
            )
        }
        foreach ($existing in @($paths.Start, $paths.Stop) + @($runtimeResidue | ForEach-Object { $_.FullName })) {
            if (Test-Path -LiteralPath $existing) {
                throw "bridge install is first-bootstrap only; an installed or partial bridge already exists: $existing"
            }
        }
    }
    & $installer @arguments
    if (-not $?) { throw "$installer failed" }
}

function Invoke-BridgeHooksRefresh {
    Invoke-BridgeValidate | Out-Null
    $resolvedProjectRoot = Resolve-Project
    $installer = Join-Path $PSScriptRoot 'install-feishu-codex-bridge.ps1'
    & $installer -ProjectRoot $resolvedProjectRoot -HooksOnly -Force
    if (-not $?) { throw "$installer failed" }
    Write-Output 'Lifecycle hooks refreshed only. Run and verify the matching runtime install or upgrade before start; the current workflow continues automatically.'
}

function Assert-BridgeStartReady {
    $paths = Get-BridgePaths
    if (-not (Test-Path -LiteralPath $paths.Start)) { throw "Bridge is not installed: $($paths.Start)" }
    $envState = Get-BridgeEnvFileState
    if ($envState.Issues.Count -gt 0) {
        throw "Refusing to start with an invalid bridge.env: $($envState.Issues -join ' | ')"
    }
    Invoke-BridgeValidate | Out-Null
    $parity = Get-BridgeParity
    if (-not $parity.Current) {
        $details = @()
        if ($parity.Missing.Count -gt 0) {
            $details += "missing: $($parity.Missing -join ', ')"
        }
        if ($parity.Mismatch.Count -gt 0) {
            $details += "mismatched: $($parity.Mismatch -join ', ')"
        }
        throw ("Refusing to start a stale or incomplete installed Feishu bridge runtime. " +
            "Run and verify the exact bridge install or upgrade first ({0})." -f ($details -join '; '))
    }
    return $paths
}

function Invoke-BridgeStart {
    $paths = Assert-BridgeStartReady
    & $paths.Start
}

function Invoke-BridgeRestart {
    # Validate before interruption so a stale install cannot turn a healthy
    # process into an avoidable outage during an automatically orchestrated restart.
    $paths = Assert-BridgeStartReady
    Invoke-BridgeStop
    & $paths.Start
}

function Invoke-BridgeStop {
    $paths = Get-BridgePaths
    if (-not (Test-Path -LiteralPath $paths.Stop)) { throw "Bridge is not installed: $($paths.Stop)" }
    $state = Get-BridgePidState
    if ($state.HasPidFile -and $state.Pid -gt 0 -and $state.Identity.Exists) {
        if (-not $state.Identity.Verified) {
            throw "Bridge PID $($state.Pid) exists, but its command line could not be verified; refusing to stop any process."
        }
        if (-not $state.Identity.IsBridge) {
            Remove-Item -LiteralPath $paths.Pid -Force -ErrorAction Stop
            Write-Output (
                "Removed stale Bridge PID file; PID {0} belongs to non-Bridge process {1}. No process was stopped." -f
                $state.Pid, $state.Identity.ProcessName
            )
            return
        }
        $installedStop = Get-Content -LiteralPath $paths.Stop -Raw -ErrorAction Stop
        if ($installedStop -notmatch [regex]::Escape('Get-BridgeProcessIdentity')) {
            throw ('Installed stop hook lacks PID-identity fencing. Refresh hooks while the Bridge is stopped; ' +
                'refusing to delegate a force-stop that could hit a reused PID.')
        }
    }
    & $paths.Stop
}

function Invoke-BridgeStatus {
    $paths = Get-BridgePaths
    $pidState = Get-BridgePidState
    $running = $false
    $runtimeState = 'stopped'
    if ($pidState.Pid -gt 0 -and $pidState.Identity.Exists) {
        if (-not $pidState.Identity.Verified) {
            $runtimeState = 'unknown'
        } elseif ($pidState.Identity.IsBridge) {
            $running = $true
            $runtimeState = 'running'
        }
    }
    Write-Output ("Runtime: {0}{1}" -f $runtimeState, $(if ($running) { " (PID $($pidState.Pid))" } else { '' }))
    if ($pidState.HasPidFile -and -not $running) {
        if ($pidState.Pid -le 0) {
            Write-Output 'Runtime PID file: invalid.'
        } elseif (-not $pidState.Identity.Exists) {
            Write-Output 'Runtime PID file: stale; referenced process is absent.'
        } elseif (-not $pidState.Identity.Verified) {
            Write-Output 'Runtime PID file: unresolved; referenced Python command line is unavailable.'
            Write-Output 'Runtime identity check: rerun this same read-only status command in a clean external shell that can query Win32_Process; do not infer running or stopped from the PID file alone.'
        } else {
            Write-Output ("Runtime PID file: stale; referenced process is {0}, not this Bridge process." -f $pidState.Identity.ProcessName)
        }
    }
    if (Test-Path -LiteralPath $paths.Health) {
        $contract = Get-BridgeStatusContract
        $health = $contract.health_snapshot
        if (-not [bool]$health.valid) {
            Write-Output 'Health: invalid answer-free snapshot; no raw fields were displayed.'
        } else {
            Write-Output ("Health: {0}; version={1}; Feishu consumer={2}; owner={3}; beeper={4}; transport={5}; active={6}" -f $health.status, $health.bridge_version, $health.event_consumer, $health.session_owner, $health.beeper_state, $health.beeper_transport, $health.active_turns)
            $dialLeaseText = if ($null -eq $health.dial_lease_remaining_seconds) { 'none' } else { ('{0:N1}s' -f [double]$health.dial_lease_remaining_seconds) }
            $dialState = if ([bool]$health.dial_inflight) { 'inflight' } else { 'idle' }
            Write-Output ("Beeper dial lease: {0}; remaining={1}." -f $dialState, $dialLeaseText)
            if ($health.queue_counts) {
                Write-Output ("Queue: {0}" -f (($health.queue_counts | ConvertTo-Json -Compress)))
            }
        }
    } else {
        Write-Output 'Health: no Bridge health snapshot yet.'
    }
}

function Get-BridgeParity {
    $skillRoot = Split-Path -Parent $PSScriptRoot
    $pairs = [ordered]@{
        'bridge.py' = @(
            (Join-Path $skillRoot 'scripts\bridge.py'),
            (Join-Path (Get-BridgePaths).Runtime 'bridge.py')
        )
        'beeper_queue_cli.py' = @(
            (Join-Path $skillRoot 'scripts\beeper_queue_cli.py'),
            (Join-Path (Get-BridgePaths).Runtime 'beeper_queue_cli.py')
        )
        'bridge_core\__init__.py' = @(
            (Join-Path $skillRoot 'scripts\bridge_core\__init__.py'),
            (Join-Path (Get-BridgePaths).Runtime 'bridge_core\__init__.py')
        )
        'bridge_core\beeper_client.py' = @(
            (Join-Path $skillRoot 'scripts\bridge_core\beeper_client.py'),
            (Join-Path (Get-BridgePaths).Runtime 'bridge_core\beeper_client.py')
        )
        'bridge_core\beeper_queue.py' = @(
            (Join-Path $skillRoot 'scripts\bridge_core\beeper_queue.py'),
            (Join-Path (Get-BridgePaths).Runtime 'bridge_core\beeper_queue.py')
        )
        'bridge_core\legacy_identifiers.py' = @(
            (Join-Path $skillRoot 'scripts\bridge_core\legacy_identifiers.py'),
            (Join-Path (Get-BridgePaths).Runtime 'bridge_core\legacy_identifiers.py')
        )
        'bridge_core\config.py' = @(
            (Join-Path $skillRoot 'scripts\bridge_core\config.py'),
            (Join-Path (Get-BridgePaths).Runtime 'bridge_core\config.py')
        )
        'bridge_core\lark.py' = @(
            (Join-Path $skillRoot 'scripts\bridge_core\lark.py'),
            (Join-Path (Get-BridgePaths).Runtime 'bridge_core\lark.py')
        )
        'bridge_core\runtime.py' = @(
            (Join-Path $skillRoot 'scripts\bridge_core\runtime.py'),
            (Join-Path (Get-BridgePaths).Runtime 'bridge_core\runtime.py')
        )
        'bridge_core\state.py' = @(
            (Join-Path $skillRoot 'scripts\bridge_core\state.py'),
            (Join-Path (Get-BridgePaths).Runtime 'bridge_core\state.py')
        )
        'start hook' = @(
            (Join-Path $skillRoot 'scripts\start-feishu-codex-bridge.ps1'),
            (Get-BridgePaths).Start
        )
        'stop hook' = @(
            (Join-Path $skillRoot 'scripts\stop-feishu-codex-bridge.ps1'),
            (Get-BridgePaths).Stop
        )
    }
    $missing = @()
    $mismatch = @()
    foreach ($name in $pairs.Keys) {
        $source = $pairs[$name][0]
        $destination = $pairs[$name][1]
        if (-not (Test-Path -LiteralPath $destination -PathType Leaf)) {
            $missing += $name
            continue
        }
        $sourceHash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
        $destinationHash = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash
        if ($sourceHash -ne $destinationHash) { $mismatch += $name }
    }
    return [pscustomobject]@{
        Missing = $missing
        Mismatch = $mismatch
        Current = ($missing.Count -eq 0 -and $mismatch.Count -eq 0)
    }
}

function Get-InstalledBridgeManifestIssues {
    $paths = Get-BridgePaths
    $manifestPath = Join-Path $paths.Runtime 'runtime-manifest.json'
    $issues = New-Object System.Collections.Generic.List[string]
    $expectedFiles = @(
        'bridge.py',
        'beeper_queue_cli.py',
        'bridge_core/__init__.py',
        'bridge_core/config.py',
        'bridge_core/beeper_client.py',
        'bridge_core/beeper_queue.py',
        'bridge_core/legacy_identifiers.py',
        'bridge_core/lark.py',
        'bridge_core/runtime.py',
        'bridge_core/state.py'
    )
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        $issues.Add('runtime-manifest.json is missing.')
        return $issues
    }
    try {
        $manifest = Get-Content -LiteralPath $manifestPath -Raw |
            ConvertFrom-Json -ErrorAction Stop
    } catch {
        $issues.Add("runtime-manifest.json is invalid JSON: $($_.Exception.Message)")
        return $issues
    }
    $manifestSchema = 0
    $manifestSchemaText = [string]$manifest.schema_version
    if (-not [int]::TryParse($manifestSchemaText, [ref]$manifestSchema) -or $manifestSchema -ne 1) {
        $issues.Add("runtime manifest schema is not 1: $manifestSchemaText")
    }
    if (-not $manifest.code_files) {
        $issues.Add('runtime manifest has no code-file hashes.')
        return $issues
    }
    $actualNames = @($manifest.code_files.PSObject.Properties.Name | Sort-Object)
    $expectedNames = @($expectedFiles | Sort-Object)
    $fileSetMismatch = ($actualNames.Count -ne $expectedNames.Count)
    if (-not $fileSetMismatch) {
        foreach ($expectedName in $expectedNames) {
            if ($actualNames -notcontains $expectedName) {
                $fileSetMismatch = $true
                break
            }
        }
    }
    if ($fileSetMismatch) {
        $issues.Add('runtime manifest code-file set is incomplete or contains unexpected entries.')
    }
    foreach ($relative in $expectedFiles) {
        $property = $manifest.code_files.PSObject.Properties[$relative]
        if (-not $property -or [string]$property.Value -notmatch '^[a-f0-9]{64}$') {
            $issues.Add("runtime manifest has no valid hash for $relative")
            continue
        }
        $destination = Join-Path $paths.Runtime ($relative.Replace('/', '\'))
        if (-not (Test-Path -LiteralPath $destination -PathType Leaf)) {
            $issues.Add("installed runtime file is missing: $relative")
            continue
        }
        try {
            $actual = (Get-FileHash -LiteralPath $destination -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant()
        } catch {
            $issues.Add("installed runtime file could not be hashed: $relative ($($_.Exception.Message))")
            continue
        }
        if ($actual -ne [string]$property.Value) {
            $issues.Add("installed runtime hash mismatch: $relative")
        }
    }
    $installedConfigPath = Join-Path $paths.Runtime 'bridge_core\config.py'
    if (Test-Path -LiteralPath $installedConfigPath -PathType Leaf) {
        try {
            $installedConfig = Get-Content -LiteralPath $installedConfigPath -Raw -ErrorAction Stop
            if ($installedConfig -notmatch 'BRIDGE_VERSION\s*=\s*["'']([^"'']+)["'']') {
                $issues.Add('installed runtime has no readable BRIDGE_VERSION marker.')
            } elseif ([string]$manifest.bridge_version -ne [string]$Matches[1]) {
                $issues.Add('runtime manifest version does not match installed config.py.')
            }
        } catch {
            $issues.Add("installed config.py could not be read: $($_.Exception.Message)")
        }
    }
    foreach ($hook in @(
        [pscustomobject]@{ Path = $paths.Start; Hash = [string]$manifest.start_hook_sha256; Name = 'start hook' },
        [pscustomobject]@{ Path = $paths.Stop; Hash = [string]$manifest.stop_hook_sha256; Name = 'stop hook' }
    )) {
        if ($hook.Hash -notmatch '^[a-f0-9]{64}$' -or -not (Test-Path -LiteralPath $hook.Path -PathType Leaf)) {
            $issues.Add("runtime manifest has no valid $($hook.Name) binding.")
            continue
        }
        try {
            $actual = (Get-FileHash -LiteralPath $hook.Path -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant()
        } catch {
            $issues.Add("installed $($hook.Name) could not be hashed: $($_.Exception.Message)")
            continue
        }
        if ($actual -ne $hook.Hash) {
            $issues.Add("installed $($hook.Name) hash mismatch.")
        }
    }
    return $issues
}

function Get-InstalledBridgeHookIssues {
    param(
        [Parameter(Mandatory = $true)][string]$HooksConfigPath,
        [Parameter(Mandatory = $true)][string]$StartScriptPath,
        [Parameter(Mandatory = $true)][string]$StopScriptPath
    )

    $issues = New-Object System.Collections.Generic.List[string]
    if (-not (Test-Path -LiteralPath $HooksConfigPath -PathType Leaf)) {
        $issues.Add('hooks.json is missing.')
        return $issues
    }

    $bytes = [System.IO.File]::ReadAllBytes($HooksConfigPath)
    if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
        $issues.Add('hooks.json has a UTF-8 BOM; Codex requires BOM-less JSON.')
    }
    try {
        $config = Get-Content -LiteralPath $HooksConfigPath -Raw -Encoding utf8 | ConvertFrom-Json -ErrorAction Stop
    } catch {
        $issues.Add("hooks.json is not valid JSON: $($_.Exception.Message)")
        return $issues
    }
    $hooksProperty = $config.PSObject.Properties['hooks']
    if (-not $hooksProperty) {
        $issues.Add('hooks.json has no hooks object.')
        return $issues
    }

    $specifications = @(
        [pscustomobject]@{
            Event = 'SessionStart'
            Script = $StartScriptPath
            Matcher = 'startup|resume'
            Timeout = 10
            StatusMessage = 'Activating Feishu bridge lease'
        },
        [pscustomobject]@{
            Event = 'SessionEnd'
            Script = $StopScriptPath
            Matcher = $null
            Timeout = 3
            StatusMessage = 'Releasing Feishu bridge lease'
        }
    )

    foreach ($specification in $specifications) {
        $eventProperty = $hooksProperty.Value.PSObject.Properties[$specification.Event]
        if (-not $eventProperty) {
            $issues.Add("$($specification.Event) is missing.")
            continue
        }
        if ($eventProperty.Value -isnot [System.Array]) {
            $issues.Add("$($specification.Event) must be a matcher-group array, not a single object.")
            continue
        }

        $bridgeHandlerCount = 0
        foreach ($group in @($eventProperty.Value)) {
            $groupHooksProperty = $group.PSObject.Properties['hooks']
            if (-not $groupHooksProperty) { continue }
            foreach ($handler in @($groupHooksProperty.Value)) {
                $commandProperty = $handler.PSObject.Properties['command']
                $windowsCommandProperty = $handler.PSObject.Properties['commandWindows']
                $command = if ($commandProperty) { [string]$commandProperty.Value } else { '' }
                $windowsCommand = if ($windowsCommandProperty) { [string]$windowsCommandProperty.Value } else { '' }
                $referencesBridge = (
                    $command.IndexOf($specification.Script, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -or
                    $windowsCommand.IndexOf($specification.Script, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
                )
                if (-not $referencesBridge) { continue }

                $bridgeHandlerCount += 1
                $expectedCommand = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{0}" -HookInvocation' -f $specification.Script
                $typeProperty = $handler.PSObject.Properties['type']
                $timeoutProperty = $handler.PSObject.Properties['timeout']
                $statusProperty = $handler.PSObject.Properties['statusMessage']
                if (-not $typeProperty -or [string]$typeProperty.Value -cne 'command') {
                    $issues.Add("$($specification.Event) Bridge handler type is not command.")
                }
                if (-not $command.Equals($expectedCommand, [System.StringComparison]::OrdinalIgnoreCase) -or
                    -not $windowsCommand.Equals($expectedCommand, [System.StringComparison]::OrdinalIgnoreCase)) {
                    $issues.Add("$($specification.Event) Bridge command is not the exact installed command.")
                }
                if (-not $timeoutProperty -or [int]$timeoutProperty.Value -ne $specification.Timeout) {
                    $issues.Add("$($specification.Event) Bridge timeout is not $($specification.Timeout) seconds.")
                }
                if (-not $statusProperty -or [string]$statusProperty.Value -cne $specification.StatusMessage) {
                    $issues.Add("$($specification.Event) Bridge status message is not current.")
                }
                if ($specification.Matcher) {
                    $matcherProperty = $group.PSObject.Properties['matcher']
                    if (-not $matcherProperty -or [string]$matcherProperty.Value -cne $specification.Matcher) {
                        $issues.Add("$($specification.Event) Bridge matcher is not '$($specification.Matcher)'.")
                    }
                }
            }
        }
        if ($bridgeHandlerCount -ne 1) {
            $issues.Add("$($specification.Event) must contain exactly one Bridge handler; found $bridgeHandlerCount.")
        }
    }
    return $issues
}

function Get-BridgeHistoricalBeeperRulesState {
    # Inspect only the one project-owned historical Beeper rules file. Do not
    # enumerate other execution-policy files or return file contents/paths.
    $paths = Get-BridgePaths
    $rulesPath = Join-Path $paths.Project '.codex\rules\feishu-beeper.rules'
    if (-not (Test-Path -LiteralPath $rulesPath)) {
        return [pscustomobject][ordered]@{
            present = $false
            historical_beeper_rules_tombstoned = $true
            allow_prefix_present = $false
            issue_codes = @()
        }
    }

    try {
        $rulesInfo = Get-Item -LiteralPath $rulesPath -Force -ErrorAction Stop
        if ($rulesInfo.PSIsContainer -or
            ($rulesInfo.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
            $rulesInfo.Length -gt 1048576) {
            throw 'historical Beeper rule file is invalid'
        }
        $rulesText = Get-Content -LiteralPath $rulesPath -Raw -Encoding utf8 -ErrorAction Stop
        $allowPrefixPresent = (
            $rulesText -match '(?m)^\s*prefix_rule\s*\(' -or
            $rulesText -match '(?m)^\s*decision\s*=\s*["'']allow["'']'
        )
        $tombstoneMarkerPresent = $rulesText -match [regex]::Escape('HISTORICAL_BEEPER_RULES_TOMBSTONE_V1')
        [string[]]$issueCodes = @()
        if ($allowPrefixPresent) {
            $issueCodes += 'historical_beeper_rules_allow_present'
        }
        if (-not $tombstoneMarkerPresent) {
            $issueCodes += 'historical_beeper_rules_tombstone_missing'
        }
        return [pscustomobject][ordered]@{
            present = $true
            historical_beeper_rules_tombstoned = ($issueCodes.Count -eq 0)
            allow_prefix_present = $allowPrefixPresent
            issue_codes = @($issueCodes)
        }
    } catch {
        return [pscustomobject][ordered]@{
            present = $true
            historical_beeper_rules_tombstoned = $false
            allow_prefix_present = $false
            issue_codes = @('historical_beeper_rules_unresolved')
        }
    }
}

function Invoke-BridgeDoctor {
    $paths = Get-BridgePaths
    $hooksConfig = Join-Path $paths.Project '.codex\hooks.json'
    $requiredPaths = @(
        (Join-Path $paths.Runtime 'bridge.py'),
        (Join-Path $paths.Runtime 'beeper_queue_cli.py'),
        (Join-Path $paths.Runtime 'bridge_core\runtime.py'),
        (Join-Path $paths.Runtime 'bridge_core\beeper_queue.py'),
        $paths.Env,
        $paths.Start,
        $paths.Stop,
        $hooksConfig
    )
    $missingPaths = @($requiredPaths | Where-Object { -not (Test-Path -LiteralPath $_) })
    if ($missingPaths.Count -gt 0) {
        Write-Warning ("Missing installed bridge artifacts: {0}" -f ($missingPaths -join '; '))
    } else {
        Write-Output 'Bridge code and lifecycle hooks are installed.'
    }
    if (Test-Path -LiteralPath $hooksConfig -PathType Leaf) {
        $hookIssues = @(Get-InstalledBridgeHookIssues -HooksConfigPath $hooksConfig -StartScriptPath $paths.Start -StopScriptPath $paths.Stop)
        if ($hookIssues.Count -eq 0) {
            Write-Output 'Lifecycle hook configuration: structurally valid and unique; trust state requires the Codex hook browser.'
        } else {
            foreach ($hookIssue in $hookIssues) {
                Write-Warning ("Lifecycle hook configuration: {0}" -f $hookIssue)
            }
        }
    }
    $manifestIssues = @(Get-InstalledBridgeManifestIssues)
    if ($manifestIssues.Count -eq 0) {
        Write-Output 'Installed runtime manifest: version, file set, code hashes, and hook hashes are valid.'
    } else {
        foreach ($manifestIssue in $manifestIssues) {
            Write-Warning ("Installed runtime manifest: {0}" -f $manifestIssue)
        }
    }
    $historicalRules = Get-BridgeHistoricalBeeperRulesState
    if ([bool]$historicalRules.historical_beeper_rules_tombstoned) {
        Write-Output 'Historical Beeper execution rules: tombstoned; no legacy allow prefix is installed.'
    } else {
        Write-Warning (
            'Historical Beeper execution rules are not safely tombstoned: {0}' -f
            (@($historicalRules.issue_codes) -join ',')
        )
    }
    Invoke-BridgeStatus
    $parity = Get-BridgeParity
    if ($parity.Current) {
        Write-Output 'Source/runtime parity: current.'
    } else {
        if ($parity.Missing.Count -gt 0) {
            Write-Warning ("Source/runtime missing files: {0}" -f ($parity.Missing -join ', '))
        }
        if ($parity.Mismatch.Count -gt 0) {
            Write-Warning ("Source/runtime mismatched files: {0}" -f ($parity.Mismatch -join ', '))
        }
        Write-Output 'Source/runtime parity: update pending; bridge upgrade does not restart the live process.'
    }
    $envState = Get-BridgeEnvFileState
    if ($envState.Issues.Count -gt 0) {
        foreach ($envIssue in $envState.Issues) {
            Write-Warning ("Bridge environment: {0}" -f $envIssue)
        }
        Write-Warning 'Access policy was not inferred from an ambiguous bridge.env; start fails closed until the file is repaired.'
    } else {
        $accessMode = ([string]$envState.Values['CODEX_BRIDGE_ACCESS_MODE']).Trim().ToLowerInvariant()
        $identitySpecs = @(
            [pscustomobject]@{ Name = 'CODEX_BRIDGE_OWNER_OPEN_ID'; Prefix = 'ou_'; Single = $true },
            [pscustomobject]@{ Name = 'CODEX_BRIDGE_ADMIN_OPEN_IDS'; Prefix = 'ou_'; Single = $false },
            [pscustomobject]@{ Name = 'CODEX_BRIDGE_ALLOWED_USER_OPEN_IDS'; Prefix = 'ou_'; Single = $false },
            [pscustomobject]@{ Name = 'CODEX_BRIDGE_ALLOWED_CHAT_IDS'; Prefix = 'oc_'; Single = $false }
        )
        $hasValidIdentity = $false
        foreach ($identitySpec in $identitySpecs) {
            $identityValue = if ($envState.Values.ContainsKey($identitySpec.Name)) {
                [string]$envState.Values[$identitySpec.Name]
            } else {
                ''
            }
            if ([string]::IsNullOrWhiteSpace($identityValue)) { continue }
            try {
                Assert-BridgeIdentifierList -Name $identitySpec.Name -Value $identityValue `
                    -Prefix $identitySpec.Prefix -Single:([bool]$identitySpec.Single)
                $hasValidIdentity = $true
            } catch {
                Write-Warning ("Access policy has an invalid identity list: {0}" -f $identitySpec.Name)
            }
        }
        if ($accessMode -eq 'locked') {
            if ($hasValidIdentity) {
                Write-Output 'Access policy: locked allowlist mode with at least one configured identity.'
            } else {
                Write-Warning 'Access policy is locked but has no configured identity; every Feishu event is denied until bridge access adds one.'
            }
        } elseif ($accessMode -eq 'compat') {
            Write-Warning 'Access policy is explicit legacy compatibility mode; with no IDs it accepts every sender. Configure locked access before production.'
        } else {
            Write-Warning 'Access policy key is missing; runtime uses locked/fail-closed. Configure at least one validated ID before activation.'
        }
        Write-Output 'Feishu /init scope: select and bind one existing, non-archived Desktop task only.'
        if ($envState.Values.ContainsKey('CODEX_BRIDGE_SESSION_OWNER')) {
            Write-Warning 'Legacy CODEX_BRIDGE_SESSION_OWNER is no longer used by schema v4 and can be removed.'
        }
    }
    Write-Output 'Knowledge context: inherited from the bound Codex project; no bridge-side knowledge configuration.'
    Write-Output 'Session mode: Beeper (durable Page queue plus Codex Desktop task-to-task tools; no alternate Responder client).'
    try {
        Invoke-AgentsInit -Check
    } catch {
        Write-Warning ("AGENTS.md managed rules check failed: {0}" -f $_.Exception.Message)
    }
}

function Write-BridgeJson {
    param([Parameter(Mandatory = $true)]$InputObject)

    $InputObject | ConvertTo-Json -Depth 12 -Compress | Write-Output
}

function Test-BridgeJsonInteger {
    param(
        [AllowNull()]$Value,
        [long]$Minimum = 0
    )

    if ($Value -isnot [int] -and $Value -isnot [long]) { return $false }
    try {
        return [long]$Value -ge $Minimum
    } catch {
        return $false
    }
}

function Test-BridgeJsonNumber {
    param([AllowNull()]$Value)

    if ($Value -isnot [int] -and
        $Value -isnot [long] -and
        $Value -isnot [single] -and
        $Value -isnot [double] -and
        $Value -isnot [decimal]) {
        return $false
    }
    try {
        $number = [double]$Value
        return -not [double]::IsNaN($number) -and -not [double]::IsInfinity($number)
    } catch {
        return $false
    }
}

function Test-BridgeVersionString {
    param([AllowNull()]$Value)

    return (
        $Value -is [string] -and
        ([string]$Value).Length -le 96 -and
        [string]$Value -cmatch '^[0-9]{1,5}\.[0-9]{1,5}\.[0-9]{1,5}(?:-[0-9A-Za-z](?:[0-9A-Za-z.-]{0,63})?)?$'
    )
}

function Get-BridgeStatusContract {
    $paths = Get-BridgePaths
    $pidState = Get-BridgePidState
    $running = $false
    $runtimeState = 'stopped'
    if ($pidState.Pid -gt 0 -and $pidState.Identity.Exists) {
        if (-not $pidState.Identity.Verified) {
            $runtimeState = 'unknown'
        } elseif ($pidState.Identity.IsBridge) {
            $running = $true
            $runtimeState = 'running'
        }
    }

    $pidFileState = 'absent'
    if ($pidState.HasPidFile) {
        if ($running) {
            $pidFileState = 'active'
        } elseif ($pidState.Pid -le 0) {
            $pidFileState = 'invalid'
        } elseif (-not $pidState.Identity.Exists) {
            $pidFileState = 'stale_process_absent'
        } elseif (-not $pidState.Identity.Verified) {
            $pidFileState = 'identity_unresolved'
        } else {
            $pidFileState = 'stale_foreign_process'
        }
    }

    $manifestPath = Join-Path $paths.Runtime 'runtime-manifest.json'
    $manifestPresent = Test-Path -LiteralPath $manifestPath -PathType Leaf
    $manifestVersion = $null
    $installedManifestSha256 = $null
    $manifestIssues = @(Get-InstalledBridgeManifestIssues)
    if ($manifestPresent) {
        try {
            $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding utf8 |
                ConvertFrom-Json -ErrorAction Stop
            if (Test-BridgeVersionString -Value $manifest.bridge_version) {
                $manifestVersion = [string]$manifest.bridge_version
            }
            $installedManifestSha256 = (
                Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256 -ErrorAction Stop
            ).Hash.ToLowerInvariant()
        } catch {
            # The issue is already represented by Get-InstalledBridgeManifestIssues.
        }
    }
    [string[]]$manifestIssueCodes = @()
    if ($manifestIssues.Count -gt 0) { $manifestIssueCodes = @('integrity_check_failed') }

    $healthSummary = [ordered]@{
        present = $false
        valid = $false
        status = $null
        bridge_version = $null
        event_consumer = $null
        session_owner = $null
        beeper_state = $null
        beeper_transport = $null
        active_turns = $null
        schema_current = $false
        process_identity_current = $false
        runtime_manifest_current = $false
        snapshot_fresh = $false
        dial_inflight = $null
        dial_lease_remaining_seconds = $null
        beeper_pending = $null
        beeper_claimed = $null
        actionable_retryable_failed = $null
        queue_counts = $null
        latest_delivery_fidelity = $null
        mvp_observation = $null
    }
    $healthIssue = $null
    if (Test-Path -LiteralPath $paths.Health -PathType Leaf) {
        $healthSummary.present = $true
        try {
            $health = Get-Content -LiteralPath $paths.Health -Raw -Encoding utf8 |
                ConvertFrom-Json -ErrorAction Stop
            if ($health.status -isnot [string] -or
                [string]$health.status -cnotin @('online', 'degraded', 'stopping', 'stopped') -or
                -not (Test-BridgeVersionString -Value $health.bridge_version) -or
                $health.session_owner -isnot [string] -or
                [string]$health.session_owner -cne 'beeper' -or
                $health.beeper_state -isnot [string] -or
                [string]$health.beeper_state -cnotin @(
                    'beeper-registered-load-unobserved',
                    'beeper-unavailable',
                    'historical-producer-tombstoned'
                ) -or
                $health.beeper_transport -isnot [string] -or
                [string]$health.beeper_transport -cnotin @(
                    'codex-queue',
                    'historical-desktop-beeper-tombstoned'
                ) -or
                $health.event_consumer -isnot [bool] -or
                -not (Test-BridgeJsonInteger -Value $health.pid -Minimum 1) -or
                -not (Test-BridgeJsonInteger -Value $health.active_turns -Minimum 0) -or
                -not (Test-BridgeJsonNumber -Value $health.started_at) -or
                -not (Test-BridgeJsonNumber -Value $health.updated_at)) {
                throw 'Health snapshot contains invalid current-runtime metadata.'
            }
            $runtimeManifestProperty = $health.PSObject.Properties['runtime_manifest_sha256']
            $actionableRetryProperty = $health.PSObject.Properties['actionable_retryable_failed']
            $observationProperty = $health.PSObject.Properties['mvp_observation']
            if ($runtimeManifestProperty -and
                ($runtimeManifestProperty.Value -isnot [string] -or
                 [string]$runtimeManifestProperty.Value -cnotmatch '^[a-f0-9]{64}$')) {
                throw 'Health snapshot contains invalid runtime manifest metadata.'
            }
            if ($actionableRetryProperty -and
                -not (Test-BridgeJsonInteger -Value $actionableRetryProperty.Value -Minimum 0)) {
                throw 'Health snapshot contains invalid actionable retry metadata.'
            }
            $healthSummary.valid = $true
            $healthSummary.status = [string]$health.status
            $healthSummary.bridge_version = [string]$health.bridge_version
            $healthSummary.event_consumer = [bool]$health.event_consumer
            $healthSummary.session_owner = if ($health.session_owner) { [string]$health.session_owner } else { $null }
            $healthSummary.beeper_state = if ($health.beeper_state) { [string]$health.beeper_state } else { $null }
            $healthSummary.beeper_transport = if ($health.beeper_transport) { [string]$health.beeper_transport } else { $null }
            $healthSummary.active_turns = [int]$health.active_turns
            if ($actionableRetryProperty) {
                $healthSummary.actionable_retryable_failed = [int]$actionableRetryProperty.Value
            }
            $healthStartedAt = [double]$health.started_at
            $healthUpdatedAt = [double]$health.updated_at
            $verifiedProcessStartedAt = $null
            if ($running -and
                [bool]$pidState.Identity.Verified -and
                $null -ne $pidState.Identity.Process) {
                try {
                    $processStartedAt = (
                        $pidState.Identity.Process.StartTime.ToUniversalTime()
                    )
                    $verifiedProcessStartedAt = (
                        [DateTimeOffset]$processStartedAt
                    ).ToUnixTimeMilliseconds() / 1000.0
                } catch {
                    $verifiedProcessStartedAt = $null
                }
            }
            $healthSummary.process_identity_current = (
                $running -and
                [bool]$pidState.Identity.Verified -and
                [long]$health.pid -eq [long]$pidState.Pid -and
                $null -ne $verifiedProcessStartedAt -and
                $healthStartedAt -ge ([double]$verifiedProcessStartedAt - 1.0)
            )
            $healthSummary.runtime_manifest_current = (
                $null -ne $runtimeManifestProperty -and
                $null -ne $installedManifestSha256 -and
                [string]$runtimeManifestProperty.Value -ceq [string]$installedManifestSha256
            )
            $healthNow = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
            $healthSummary.snapshot_fresh = (
                $healthStartedAt -gt 0 -and
                $healthUpdatedAt -ge $healthStartedAt -and
                $healthUpdatedAt -le ($healthNow + 5.0) -and
                ($healthNow - $healthUpdatedAt) -le 20.0
            )
            if (-not $health.beeper_queue) {
                throw 'Health snapshot is missing Beeper metadata.'
            }
            $beeper = $health.beeper_queue
            [string[]]$requiredBeeperKeys = @(
                'dial_inflight',
                'dial_lease_remaining_seconds',
                'pending',
                'claimed'
            )
            [string[]]$beeperKeys = @(
                $beeper.PSObject.Properties.Name | ForEach-Object { [string]$_ }
            )
            if ($beeperKeys.Count -ne $requiredBeeperKeys.Count -or
                @($beeperKeys | Where-Object { $_ -notin $requiredBeeperKeys }).Count -gt 0) {
                throw 'Health snapshot contains an unsupported Beeper key set.'
            }
            $dialInflightProperty = $beeper.PSObject.Properties['dial_inflight']
            if (-not $dialInflightProperty -or $dialInflightProperty.Value -isnot [bool]) {
                throw 'Health snapshot contains invalid Beeper dial metadata.'
            }
            foreach ($beeperIntegerKey in @('pending', 'claimed')) {
                $beeperProperty = $beeper.PSObject.Properties[$beeperIntegerKey]
                if (-not $beeperProperty -or
                    -not (Test-BridgeJsonInteger -Value $beeperProperty.Value -Minimum 0)) {
                    throw 'Health snapshot contains invalid Beeper counts.'
                }
            }
            $dialLeaseProperty = $beeper.PSObject.Properties['dial_lease_remaining_seconds']
            if (-not $dialLeaseProperty -or
                ([bool]$dialInflightProperty.Value -and
                 ($null -eq $dialLeaseProperty.Value -or
                  -not (Test-BridgeJsonNumber -Value $dialLeaseProperty.Value) -or
                  [double]$dialLeaseProperty.Value -lt 0)) -or
                (-not [bool]$dialInflightProperty.Value -and
                 $null -ne $dialLeaseProperty.Value)) {
                throw 'Health snapshot contains invalid Beeper dial-lease metadata.'
            }
            $healthSummary.dial_inflight = [bool]$dialInflightProperty.Value
            $healthSummary.dial_lease_remaining_seconds = if ($null -eq $dialLeaseProperty.Value) {
                $null
            } else {
                [double]$dialLeaseProperty.Value
            }
            $healthSummary.beeper_pending = [int]$beeper.pending
            $healthSummary.beeper_claimed = [int]$beeper.claimed
            if (-not $health.queue) {
                throw 'Health snapshot is missing Bridge queue counts.'
            }
            [string[]]$requiredQueueKeys = @(
                'queued',
                'running',
                'control_sending',
                'reply_pending',
                'retryable_failed',
                'completed',
                'terminal_failed'
            )
            [string[]]$queueKeys = @(
                $health.queue.PSObject.Properties.Name | ForEach-Object { [string]$_ }
            )
            if (@($queueKeys | Where-Object { $_ -notin $requiredQueueKeys }).Count -gt 0) {
                throw 'Health snapshot contains an unsupported Bridge queue key set.'
            }
            $queueCounts = [ordered]@{}
            foreach ($queueStatus in $requiredQueueKeys) {
                $queueProperty = $health.queue.PSObject.Properties[$queueStatus]
                if ($queueProperty -and
                    -not (Test-BridgeJsonInteger -Value $queueProperty.Value -Minimum 0)) {
                    throw 'Health snapshot contains invalid Bridge queue counts.'
                }
                $queueCounts[$queueStatus] = if ($queueProperty) {
                    [int]$queueProperty.Value
                } else {
                    0
                }
            }
            $healthSummary.queue_counts = $queueCounts
            $healthSummary.schema_current = (
                $null -ne $runtimeManifestProperty -and
                $null -ne $actionableRetryProperty -and
                $null -ne $observationProperty -and
                $queueKeys.Count -eq $requiredQueueKeys.Count -and
                @($requiredQueueKeys | Where-Object { $_ -notin $queueKeys }).Count -eq 0
            )
            if ($health.PSObject.Properties.Name -contains 'latest_delivery_fidelity') {
                $delivery = $health.latest_delivery_fidelity
                $allowedFidelity = @('identity', 'explicit_transform', 'unknown', 'not_applicable')
                $allowedTransforms = @(
                    'attachment_marker',
                    'attachment_omitted',
                    'chunking',
                    'empty_fallback',
                    'markdown'
                )
                $fidelity = [string]$delivery.fidelity
                [string[]]$transforms = @(
                    @($delivery.transforms) | ForEach-Object { [string]$_ }
                )
                $uniqueTransforms = @($transforms | Select-Object -Unique)
                if ($fidelity -notin $allowedFidelity -or
                    @($transforms | Where-Object { $_ -notin $allowedTransforms }).Count -gt 0 -or
                    $uniqueTransforms.Count -ne $transforms.Count -or
                    ($fidelity -eq 'explicit_transform' -and $transforms.Count -eq 0) -or
                    ($fidelity -ne 'explicit_transform' -and $transforms.Count -gt 0)) {
                    throw 'Health snapshot contains invalid delivery fidelity metadata.'
                }
                $healthSummary.latest_delivery_fidelity = [ordered]@{
                    fidelity = $fidelity
                    transforms = @($transforms)
                }
            }
            if ($health.PSObject.Properties.Name -contains 'mvp_observation' -and
                $null -ne $health.mvp_observation) {
                $observation = $health.mvp_observation
                [string[]]$requiredObservationKeys = @(
                    'schema_version',
                    'status',
                    'answer_free',
                    'producer_namespace',
                    'final_callback_source',
                    'feishu_delivery_observed',
                    'known_delivery_fidelity_observed',
                    'single_inbox_claim_observed',
                    'bridge_outbox_scrubbed'
                )
                [string[]]$observationKeys = @(
                    $observation.PSObject.Properties.Name | ForEach-Object { [string]$_ }
                )
                if ($observationKeys.Count -ne $requiredObservationKeys.Count -or
                    @($requiredObservationKeys | Where-Object { $_ -notin $observationKeys }).Count -gt 0 -or
                    -not (Test-BridgeJsonInteger -Value $observation.schema_version -Minimum 1) -or
                    [int]$observation.schema_version -ne 1 -or
                    [string]$observation.status -cne 'passed' -or
                    $observation.answer_free -isnot [bool] -or
                    -not [bool]$observation.answer_free -or
                    [string]$observation.producer_namespace -cne 'beeper' -or
                    [string]$observation.final_callback_source -cne 'final_callback') {
                    throw 'Health snapshot contains invalid MVP metadata.'
                }
                foreach ($booleanKey in @(
                    'feishu_delivery_observed',
                    'known_delivery_fidelity_observed',
                    'single_inbox_claim_observed',
                    'bridge_outbox_scrubbed'
                )) {
                    if ($observation.$booleanKey -isnot [bool] -or
                        -not [bool]$observation.$booleanKey) {
                        throw 'Health snapshot contains incomplete MVP metadata.'
                    }
                }
                $healthSummary.mvp_observation = [ordered]@{
                    schema_version = 1
                    status = 'passed'
                    answer_free = $true
                    producer_namespace = 'beeper'
                    final_callback_source = 'final_callback'
                    feishu_delivery_observed = $true
                    known_delivery_fidelity_observed = $true
                    single_inbox_claim_observed = $true
                    bridge_outbox_scrubbed = $true
                }
            }
        } catch {
            $healthSummary.valid = $false
            foreach ($field in @(
                'status',
                'bridge_version',
                'event_consumer',
                'session_owner',
                'beeper_state',
                'beeper_transport',
                'active_turns',
                'dial_inflight',
                'dial_lease_remaining_seconds',
                'beeper_pending',
                'beeper_claimed',
                'actionable_retryable_failed',
                'queue_counts',
                'latest_delivery_fidelity',
                'mvp_observation'
            )) {
                $healthSummary[$field] = $null
            }
            foreach ($field in @(
                'schema_current',
                'process_identity_current',
                'runtime_manifest_current',
                'snapshot_fresh'
            )) {
                $healthSummary[$field] = $false
            }
            $healthIssue = 'invalid_health_snapshot'
        }
    }

    $contractStatus = 'pass'
    if ($runtimeState -eq 'unknown' -or $manifestIssues.Count -gt 0 -or $healthIssue) {
        $contractStatus = 'warning'
    }
    return [pscustomobject][ordered]@{
        schema_version = 1
        command = 'bridge.status'
        status = $contractStatus
        runtime = [ordered]@{
            state = $runtimeState
            running = $running
            pid = $(if ($running) { [int]$pidState.Pid } else { $null })
            pid_file_present = [bool]$pidState.HasPidFile
            pid_file_state = $pidFileState
            identity_verified = [bool]$pidState.Identity.Verified
            identity_is_bridge = [bool]$pidState.Identity.IsBridge
        }
        installed_manifest = [ordered]@{
            present = $manifestPresent
            valid = ($manifestIssues.Count -eq 0)
            bridge_version = $manifestVersion
            issue_count = $manifestIssues.Count
            issue_codes = $manifestIssueCodes
        }
        health_snapshot = $healthSummary
        health_issue = $healthIssue
    }
}

function Get-BridgeDoctorContract {
    $paths = Get-BridgePaths
    $hooksConfig = Join-Path $paths.Project '.codex\hooks.json'
    $required = [ordered]@{
        bridge_py = (Join-Path $paths.Runtime 'bridge.py')
        beeper_queue_cli_py = (Join-Path $paths.Runtime 'beeper_queue_cli.py')
        runtime_py = (Join-Path $paths.Runtime 'bridge_core\runtime.py')
        beeper_queue_py = (Join-Path $paths.Runtime 'bridge_core\beeper_queue.py')
        bridge_env = $paths.Env
        start_hook = $paths.Start
        stop_hook = $paths.Stop
        hooks_json = $hooksConfig
    }
    $missingArtifacts = @(
        foreach ($entry in $required.GetEnumerator()) {
            if (-not (Test-Path -LiteralPath $entry.Value -PathType Leaf)) {
                [string]$entry.Key
            }
        }
    )
    $hookIssues = if (Test-Path -LiteralPath $hooksConfig -PathType Leaf) {
        @(Get-InstalledBridgeHookIssues -HooksConfigPath $hooksConfig -StartScriptPath $paths.Start -StopScriptPath $paths.Stop)
    } else {
        @('hooks.json is missing.')
    }
    $manifestIssues = @(Get-InstalledBridgeManifestIssues)
    $historicalRules = Get-BridgeHistoricalBeeperRulesState
    [string[]]$hookIssueCodes = @()
    if ($hookIssues.Count -gt 0) { $hookIssueCodes = @('hook_contract_invalid') }
    $parity = Get-BridgeParity
    $envState = Get-BridgeEnvFileState
    $envIssues = @($envState.Issues)
    [string[]]$environmentIssueCodes = @()
    if ($envIssues.Count -gt 0) { $environmentIssueCodes = @('environment_invalid') }

    $accessMode = $null
    $hasValidIdentity = $false
    $invalidIdentityKeys = New-Object System.Collections.Generic.List[string]
    if ($envIssues.Count -eq 0) {
        $accessMode = ([string]$envState.Values['CODEX_BRIDGE_ACCESS_MODE']).Trim().ToLowerInvariant()
        $identitySpecs = @(
            [pscustomobject]@{ Name = 'CODEX_BRIDGE_OWNER_OPEN_ID'; Prefix = 'ou_'; Single = $true },
            [pscustomobject]@{ Name = 'CODEX_BRIDGE_ADMIN_OPEN_IDS'; Prefix = 'ou_'; Single = $false },
            [pscustomobject]@{ Name = 'CODEX_BRIDGE_ALLOWED_USER_OPEN_IDS'; Prefix = 'ou_'; Single = $false },
            [pscustomobject]@{ Name = 'CODEX_BRIDGE_ALLOWED_CHAT_IDS'; Prefix = 'oc_'; Single = $false }
        )
        foreach ($identitySpec in $identitySpecs) {
            $identityValue = if ($envState.Values.ContainsKey($identitySpec.Name)) {
                [string]$envState.Values[$identitySpec.Name]
            } else { '' }
            if ([string]::IsNullOrWhiteSpace($identityValue)) { continue }
            try {
                Assert-BridgeIdentifierList -Name $identitySpec.Name -Value $identityValue `
                    -Prefix $identitySpec.Prefix -Single:([bool]$identitySpec.Single)
                $hasValidIdentity = $true
            } catch {
                $invalidIdentityKeys.Add([string]$identitySpec.Name)
            }
        }
    }

    $agentsCurrent = $false
    $agentsIssue = $null
    try {
        $null = @(Invoke-AgentsInit -Check)
        $agentsCurrent = $true
    } catch {
        $agentsIssue = 'managed_rules_not_current'
    }

    $statusContract = Get-BridgeStatusContract
    $hardFailure = (
        $missingArtifacts.Count -gt 0 -or
        $hookIssues.Count -gt 0 -or
        $manifestIssues.Count -gt 0 -or
        -not [bool]$historicalRules.historical_beeper_rules_tombstoned -or
        $envIssues.Count -gt 0 -or
        $invalidIdentityKeys.Count -gt 0 -or
        -not $agentsCurrent -or
        $statusContract.runtime.state -eq 'unknown'
    )
    $warning = (
        -not $parity.Current -or
        $accessMode -ne 'locked' -or
        -not $hasValidIdentity -or
        $statusContract.status -ne 'pass'
    )
    return [pscustomobject][ordered]@{
        schema_version = 1
        command = 'bridge.doctor'
        status = $(if ($hardFailure) { 'fail' } elseif ($warning) { 'warning' } else { 'pass' })
        runtime = $statusContract.runtime
        installed_manifest = $statusContract.installed_manifest
        health_snapshot = $statusContract.health_snapshot
        artifacts = [ordered]@{
            complete = ($missingArtifacts.Count -eq 0)
            missing = @($missingArtifacts)
        }
        hooks = [ordered]@{
            valid = ($hookIssues.Count -eq 0)
            issue_count = $hookIssues.Count
            issue_codes = $hookIssueCodes
            trust_requires_visible_review = $true
        }
        historical_beeper_rules = [ordered]@{
            present = [bool]$historicalRules.present
            historical_beeper_rules_tombstoned = [bool]$historicalRules.historical_beeper_rules_tombstoned
            allow_prefix_present = [bool]$historicalRules.allow_prefix_present
            issue_codes = @($historicalRules.issue_codes)
        }
        source_runtime_parity = [ordered]@{
            current = [bool]$parity.Current
            missing_count = @($parity.Missing).Count
            mismatched_count = @($parity.Mismatch).Count
        }
        environment = [ordered]@{
            valid = ($envIssues.Count -eq 0)
            issue_count = $envIssues.Count
            issue_codes = $environmentIssueCodes
        }
        access_policy = [ordered]@{
            mode = $accessMode
            valid_identity_configured = $hasValidIdentity
            invalid_identity_keys = @($invalidIdentityKeys)
        }
        agents_rules = [ordered]@{
            current = $agentsCurrent
            issue = $agentsIssue
        }
        knowledge_context = 'responder_project_inherited'
        session_mode = 'beeper'
    }
}

function Get-BridgeReadinessUtf8Sha256 {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Value
    )

    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($Value)
        $hash = $sha256.ComputeHash($bytes)
        return ([System.BitConverter]::ToString($hash)).Replace('-', '').ToLowerInvariant()
    } finally {
        $sha256.Dispose()
    }
}

function Get-BridgeRunOnceReadinessBindings {
    # These bindings prove only that an answer-free evidence envelope names the
    # exact installed manifest and exact source/runtime contract bytes currently
    # being diagnosed. They do not authenticate who observed a run_once or make
    # an owner-created file controller evidence.
    $paths = Get-BridgePaths
    $skillRoot = Split-Path -Parent $PSScriptRoot
    $surfaceNamespace = 'feishu-codex-bridge.beeper-run-once.v1'
    $runtimeInfo = Get-Item -LiteralPath $paths.Runtime -Force -ErrorAction Stop
    if (-not $runtimeInfo.PSIsContainer -or
        ($runtimeInfo.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw 'run_once readiness runtime directory is invalid'
    }

    $manifestPath = Join-Path $paths.Runtime 'runtime-manifest.json'
    $inventoryPath = Join-Path $skillRoot 'assets\release-inventory.json'
    foreach ($boundedInput in @($manifestPath, $inventoryPath)) {
        $boundedInfo = Get-Item -LiteralPath $boundedInput -Force -ErrorAction Stop
        if ($boundedInfo.PSIsContainer -or
            ($boundedInfo.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
            $boundedInfo.Length -gt 2097152) {
            throw 'run_once readiness provenance input is invalid'
        }
    }
    $manifestSha256 = (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant()
    $inventorySha256 = (Get-FileHash -LiteralPath $inventoryPath -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant()

    $sourceRuntimePairs = [ordered]@{
        'bridge.py' = @(
            (Join-Path $skillRoot 'scripts\bridge.py'),
            (Join-Path $paths.Runtime 'bridge.py')
        )
        'beeper_queue_cli.py' = @(
            (Join-Path $skillRoot 'scripts\beeper_queue_cli.py'),
            (Join-Path $paths.Runtime 'beeper_queue_cli.py')
        )
        'bridge_core/__init__.py' = @(
            (Join-Path $skillRoot 'scripts\bridge_core\__init__.py'),
            (Join-Path $paths.Runtime 'bridge_core\__init__.py')
        )
        'bridge_core/beeper_client.py' = @(
            (Join-Path $skillRoot 'scripts\bridge_core\beeper_client.py'),
            (Join-Path $paths.Runtime 'bridge_core\beeper_client.py')
        )
        'bridge_core/config.py' = @(
            (Join-Path $skillRoot 'scripts\bridge_core\config.py'),
            (Join-Path $paths.Runtime 'bridge_core\config.py')
        )
        'bridge_core/beeper_queue.py' = @(
            (Join-Path $skillRoot 'scripts\bridge_core\beeper_queue.py'),
            (Join-Path $paths.Runtime 'bridge_core\beeper_queue.py')
        )
        'bridge_core/legacy_identifiers.py' = @(
            (Join-Path $skillRoot 'scripts\bridge_core\legacy_identifiers.py'),
            (Join-Path $paths.Runtime 'bridge_core\legacy_identifiers.py')
        )
        'bridge_core/lark.py' = @(
            (Join-Path $skillRoot 'scripts\bridge_core\lark.py'),
            (Join-Path $paths.Runtime 'bridge_core\lark.py')
        )
        'bridge_core/runtime.py' = @(
            (Join-Path $skillRoot 'scripts\bridge_core\runtime.py'),
            (Join-Path $paths.Runtime 'bridge_core\runtime.py')
        )
        'bridge_core/state.py' = @(
            (Join-Path $skillRoot 'scripts\bridge_core\state.py'),
            (Join-Path $paths.Runtime 'bridge_core\state.py')
        )
        'start-hook' = @(
            (Join-Path $skillRoot 'scripts\start-feishu-codex-bridge.ps1'),
            $paths.Start
        )
        'stop-hook' = @(
            (Join-Path $skillRoot 'scripts\stop-feishu-codex-bridge.ps1'),
            $paths.Stop
        )
    }
    [string[]]$sourceRuntimeMaterial = @(
        'schema_version=1',
        "surface_namespace=$surfaceNamespace"
    )
    foreach ($pair in $sourceRuntimePairs.GetEnumerator()) {
        $sourceInfo = Get-Item -LiteralPath $pair.Value[0] -Force -ErrorAction Stop
        $runtimeFileInfo = Get-Item -LiteralPath $pair.Value[1] -Force -ErrorAction Stop
        foreach ($fileInfo in @($sourceInfo, $runtimeFileInfo)) {
            if ($fileInfo.PSIsContainer -or
                ($fileInfo.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
                $fileInfo.Length -gt 2097152) {
                throw 'run_once readiness source/runtime input is invalid'
            }
        }
        $sourceHash = (Get-FileHash -LiteralPath $pair.Value[0] -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant()
        $runtimeHash = (Get-FileHash -LiteralPath $pair.Value[1] -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant()
        $sourceRuntimeMaterial += "source.$($pair.Key).sha256=$sourceHash"
        $sourceRuntimeMaterial += "runtime.$($pair.Key).sha256=$runtimeHash"
    }
    $sourceRuntimeSha256 = Get-BridgeReadinessUtf8Sha256 -Value ($sourceRuntimeMaterial -join "`n")

    $surfaceInputs = [ordered]@{
        'candidate-schema' = (Join-Path $skillRoot 'assets\desktop-beeper-run-once-candidate.schema.json')
        'runtime-attestation-schema' = (Join-Path $skillRoot 'assets\desktop-beeper-run-once-runtime-attestation.schema.json')
        'beeper-run-once-contract' = (Join-Path $skillRoot 'scripts\beeper_run_once_contract.py')
        'readiness-controller' = (Join-Path $skillRoot 'scripts\feishu-codex-bridge.ps1')
    }
    [string[]]$surfaceMaterial = @(
        'schema_version=1',
        "surface_namespace=$surfaceNamespace"
    )
    foreach ($surfaceInput in $surfaceInputs.GetEnumerator()) {
        $surfaceInfo = Get-Item -LiteralPath $surfaceInput.Value -Force -ErrorAction Stop
        if ($surfaceInfo.PSIsContainer -or
            ($surfaceInfo.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
            $surfaceInfo.Length -gt 2097152) {
            throw 'run_once readiness surface contract input is invalid'
        }
        $surfaceHash = (Get-FileHash -LiteralPath $surfaceInput.Value -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant()
        $surfaceMaterial += "$($surfaceInput.Key).sha256=$surfaceHash"
    }
    $surfaceContractSha256 = Get-BridgeReadinessUtf8Sha256 -Value ($surfaceMaterial -join "`n")
    $bindingMaterial = @(
        'schema_version=1',
        "surface_namespace=$surfaceNamespace",
        "runtime_manifest_sha256=$manifestSha256",
        "release_inventory_sha256=$inventorySha256",
        "source_runtime_sha256=$sourceRuntimeSha256",
        "surface_contract_sha256=$surfaceContractSha256"
    ) -join "`n"

    return [pscustomobject][ordered]@{
        runtime_manifest_sha256 = $manifestSha256
        release_inventory_sha256 = $inventorySha256
        source_runtime_sha256 = $sourceRuntimeSha256
        surface_contract_sha256 = $surfaceContractSha256
        evidence_binding_sha256 = (Get-BridgeReadinessUtf8Sha256 -Value $bindingMaterial)
    }
}

function Get-BridgeRunOnceReadinessEvidenceState {
    $paths = Get-BridgePaths
    $statePath = Join-Path $paths.Runtime 'run-once-readiness-evidence.env'
    $emptyState = [ordered]@{
        status = 'unavailable'
        schema_valid = $false
        answer_free = $true
        surface_namespace_isolated = $false
        controller_provenance_supported = $false
        installed_manifest_bound = $false
        release_inventory_bound = $false
        source_runtime_bound = $false
        surface_contract_bound = $false
        evidence_binding_bound = $false
        closed_runtime_attestation = $false
        immutable_runtime_receipt_attested = $false
        single_beeper_attested = $false
        beeper_role_isolation_attested = $false
        desktop_responder_ownership_attested = $false
        task_coordination_policy_attested = $false
        alternate_responder_client_exclusion_attested = $false
        task_tool_surface_attested = $false
        hook_visible_review_attested = $false
        exact_source_live_e2e_attested = $false
        product_final_callback_attested = $false
        no_replay_attested = $false
        production_gate_passed = $false
        issue_codes = @('run_once_evidence_unavailable')
    }

    try {
        if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) {
            return [pscustomobject]$emptyState
        }
        $runtimeInfo = Get-Item -LiteralPath $paths.Runtime -Force -ErrorAction Stop
        $stateInfo = Get-Item -LiteralPath $statePath -Force -ErrorAction Stop
        if (-not $runtimeInfo.PSIsContainer -or
            ($runtimeInfo.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
            $stateInfo.PSIsContainer -or
            ($stateInfo.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
            $stateInfo.Length -gt 16384) {
            throw 'run_once readiness evidence path is invalid'
        }
        $values = @{}
        foreach ($rawLine in @(Get-Content -LiteralPath $statePath -Encoding utf8 -ErrorAction Stop)) {
            if ([string]::IsNullOrWhiteSpace($rawLine) -or $rawLine -match '\x00') {
                throw 'run_once readiness evidence contains an invalid line'
            }
            $parts = $rawLine -split '=', 2
            if ($parts.Count -ne 2 -or $parts[0] -cnotmatch '^[A-Z][A-Z0-9_]*$') {
                throw 'run_once readiness evidence contains an invalid assignment'
            }
            $key = [string]$parts[0]
            if ($values.ContainsKey($key)) {
                throw 'run_once readiness evidence contains a duplicate key'
            }
            $values[$key] = [string]$parts[1]
        }
        $requiredKeys = @(
            'SCHEMA_VERSION',
            'EVIDENCE_KIND',
            'SURFACE_NAMESPACE',
            'CONTROLLER_PROVENANCE_KIND',
            'PROVENANCE_RUNTIME_MANIFEST_SHA256',
            'PROVENANCE_RELEASE_INVENTORY_SHA256',
            'PROVENANCE_SOURCE_RUNTIME_SHA256',
            'PROVENANCE_SURFACE_CONTRACT_SHA256',
            'EVIDENCE_BINDING_SHA256',
            'RUNTIME_ATTESTATION_RECEIPT_SHA256',
            'RUNTIME_ATTESTATION_STATUS',
            'RUNTIME_RECEIPT_IMMUTABLE',
            'BEEPER_TOPOLOGY_STATUS',
            'BEEPER_ROLE_ISOLATION_STATUS',
            'DESKTOP_RESPONDER_OWNERSHIP_STATUS',
            'TASK_COORDINATION_POLICY_STATUS',
            'ALTERNATE_RESPONDER_CLIENT_EXCLUSION_STATUS',
            'HOOK_VISIBLE_REVIEW_STATUS',
            'LIVE_E2E_STATUS',
            'FINAL_CALLBACK_SOURCE',
            'NO_REPLAY_ATTESTED'
        )
        if ($values.Count -ne $requiredKeys.Count -or
            @($requiredKeys | Where-Object { -not $values.ContainsKey($_) }).Count -gt 0) {
            throw 'run_once readiness evidence has an unsupported key set'
        }
        foreach ($digestKey in @(
            'PROVENANCE_RUNTIME_MANIFEST_SHA256',
            'PROVENANCE_RELEASE_INVENTORY_SHA256',
            'PROVENANCE_SOURCE_RUNTIME_SHA256',
            'PROVENANCE_SURFACE_CONTRACT_SHA256',
            'EVIDENCE_BINDING_SHA256',
            'RUNTIME_ATTESTATION_RECEIPT_SHA256'
        )) {
            if ($values[$digestKey] -cnotmatch '^[a-f0-9]{64}$') {
                throw 'run_once readiness evidence contains an invalid digest'
            }
        }
        if ($values['SCHEMA_VERSION'] -cne '2' -or
            $values['EVIDENCE_KIND'] -cne 'feishu_codex_bridge_beeper_readiness_v2' -or
            $values['SURFACE_NAMESPACE'] -cne 'feishu-codex-bridge.beeper-run-once.v1' -or
            $values['CONTROLLER_PROVENANCE_KIND'] -cne 'unsupported_v1' -or
            $values['RUNTIME_ATTESTATION_STATUS'] -cnotin @('pass', 'fail', 'unobserved') -or
            $values['BEEPER_TOPOLOGY_STATUS'] -cnotin @('pass', 'fail', 'unobserved') -or
            $values['BEEPER_ROLE_ISOLATION_STATUS'] -cnotin @('pass', 'fail', 'unobserved') -or
            $values['DESKTOP_RESPONDER_OWNERSHIP_STATUS'] -cnotin @('pass', 'fail', 'unobserved') -or
            $values['TASK_COORDINATION_POLICY_STATUS'] -cnotin @('pass', 'fail', 'unobserved') -or
            $values['ALTERNATE_RESPONDER_CLIENT_EXCLUSION_STATUS'] -cnotin @('pass', 'fail', 'unobserved') -or
            $values['HOOK_VISIBLE_REVIEW_STATUS'] -cnotin @('pass', 'fail', 'unobserved') -or
            $values['LIVE_E2E_STATUS'] -cnotin @('pass', 'fail', 'unobserved') -or
            $values['FINAL_CALLBACK_SOURCE'] -cnotin @('product_attested_final_callback', 'unknown') -or
            $values['RUNTIME_RECEIPT_IMMUTABLE'] -cnotin @('true', 'false') -or
            $values['NO_REPLAY_ATTESTED'] -cnotin @('true', 'false')) {
            throw 'run_once readiness evidence schema is unsupported'
        }

        $bindings = Get-BridgeRunOnceReadinessBindings
        $parity = Get-BridgeParity
        $installedManifestBound = [string]$bindings.runtime_manifest_sha256 -ceq $values['PROVENANCE_RUNTIME_MANIFEST_SHA256']
        $releaseInventoryBound = [string]$bindings.release_inventory_sha256 -ceq $values['PROVENANCE_RELEASE_INVENTORY_SHA256']
        $sourceRuntimeBound = (
            [string]$bindings.source_runtime_sha256 -ceq $values['PROVENANCE_SOURCE_RUNTIME_SHA256'] -and
            [bool]$parity.Current
        )
        $surfaceContractBound = [string]$bindings.surface_contract_sha256 -ceq $values['PROVENANCE_SURFACE_CONTRACT_SHA256']
        $evidenceBindingBound = [string]$bindings.evidence_binding_sha256 -ceq $values['EVIDENCE_BINDING_SHA256']
        $allBindingsCurrent = (
            $installedManifestBound -and
            $releaseInventoryBound -and
            $sourceRuntimeBound -and
            $surfaceContractBound -and
            $evidenceBindingBound
        )

        # No supported controller-owned writer/attestor exists in this source.
        # Therefore even an exact, all-pass, schema-shaped file is untrusted and
        # can never lift a readiness gate. A later product implementation must
        # add and validate its controller provenance before changing this value.
        $controllerProvenanceSupported = $false
        $trustedEvidence = $allBindingsCurrent -and $controllerProvenanceSupported
        $closedRuntimeAttestation = (
            $trustedEvidence -and
            $values['RUNTIME_ATTESTATION_STATUS'] -ceq 'pass' -and
            $values['RUNTIME_RECEIPT_IMMUTABLE'] -ceq 'true'
        )
        $singleBeeperAttested = (
            $trustedEvidence -and
            $values['BEEPER_TOPOLOGY_STATUS'] -ceq 'pass'
        )
        $beeperRoleIsolationAttested = (
            $trustedEvidence -and
            $values['BEEPER_ROLE_ISOLATION_STATUS'] -ceq 'pass'
        )
        $desktopResponderOwnershipAttested = (
            $trustedEvidence -and
            $values['DESKTOP_RESPONDER_OWNERSHIP_STATUS'] -ceq 'pass'
        )
        $taskCoordinationPolicyAttested = (
            $trustedEvidence -and
            $values['TASK_COORDINATION_POLICY_STATUS'] -ceq 'pass'
        )
        $alternateResponderClientExclusionAttested = (
            $trustedEvidence -and
            $values['ALTERNATE_RESPONDER_CLIENT_EXCLUSION_STATUS'] -ceq 'pass'
        )
        $taskToolAttested = (
            $closedRuntimeAttestation -and
            $singleBeeperAttested -and
            $beeperRoleIsolationAttested -and
            $desktopResponderOwnershipAttested -and
            $taskCoordinationPolicyAttested -and
            $alternateResponderClientExclusionAttested
        )
        $hookReviewAttested = $trustedEvidence -and $values['HOOK_VISIBLE_REVIEW_STATUS'] -ceq 'pass'
        $liveE2eAttested = (
            $trustedEvidence -and
            $values['LIVE_E2E_STATUS'] -ceq 'pass' -and
            $values['FINAL_CALLBACK_SOURCE'] -ceq 'product_attested_final_callback'
        )
        $noReplayAttested = $liveE2eAttested -and $values['NO_REPLAY_ATTESTED'] -ceq 'true'
        $productionGatePassed = (
            $closedRuntimeAttestation -and
            $taskToolAttested -and
            $hookReviewAttested -and
            $liveE2eAttested -and
            $noReplayAttested
        )
        $evidenceStatus = if (-not $allBindingsCurrent) {
            'stale_provenance'
        } elseif (-not $controllerProvenanceSupported) {
            'unsupported_no_product_origin'
        } elseif ($productionGatePassed) {
            'passed'
        } else {
            'incomplete'
        }
        [string[]]$issueCodes = @()
        if (-not $allBindingsCurrent) {
            $issueCodes += 'run_once_evidence_provenance_stale'
        }
        if (-not $controllerProvenanceSupported) {
            $issueCodes += 'unsupported_no_product_origin'
        }
        if ($allBindingsCurrent -and $controllerProvenanceSupported -and -not $productionGatePassed) {
            $issueCodes += 'run_once_evidence_incomplete'
        }

        return [pscustomobject][ordered]@{
            status = $evidenceStatus
            schema_valid = $true
            answer_free = $true
            surface_namespace_isolated = $true
            controller_provenance_supported = $controllerProvenanceSupported
            installed_manifest_bound = $installedManifestBound
            release_inventory_bound = $releaseInventoryBound
            source_runtime_bound = $sourceRuntimeBound
            surface_contract_bound = $surfaceContractBound
            evidence_binding_bound = $evidenceBindingBound
            closed_runtime_attestation = $closedRuntimeAttestation
            immutable_runtime_receipt_attested = $closedRuntimeAttestation
            single_beeper_attested = $singleBeeperAttested
            beeper_role_isolation_attested = $beeperRoleIsolationAttested
            desktop_responder_ownership_attested = $desktopResponderOwnershipAttested
            task_coordination_policy_attested = $taskCoordinationPolicyAttested
            alternate_responder_client_exclusion_attested = $alternateResponderClientExclusionAttested
            task_tool_surface_attested = $taskToolAttested
            hook_visible_review_attested = $hookReviewAttested
            exact_source_live_e2e_attested = $liveE2eAttested
            product_final_callback_attested = $liveE2eAttested
            no_replay_attested = $noReplayAttested
            production_gate_passed = $productionGatePassed
            issue_codes = @($issueCodes)
        }
    } catch {
        return [pscustomobject][ordered]@{
            status = 'invalid'
            schema_valid = $false
            answer_free = $true
            surface_namespace_isolated = $false
            controller_provenance_supported = $false
            installed_manifest_bound = $false
            release_inventory_bound = $false
            source_runtime_bound = $false
            surface_contract_bound = $false
            evidence_binding_bound = $false
            closed_runtime_attestation = $false
            immutable_runtime_receipt_attested = $false
            single_beeper_attested = $false
            beeper_role_isolation_attested = $false
            desktop_responder_ownership_attested = $false
            task_coordination_policy_attested = $false
            alternate_responder_client_exclusion_attested = $false
            task_tool_surface_attested = $false
            hook_visible_review_attested = $false
            exact_source_live_e2e_attested = $false
            product_final_callback_attested = $false
            no_replay_attested = $false
            production_gate_passed = $false
            issue_codes = @('run_once_evidence_invalid')
        }
    }
}

function Get-BridgeReadinessContract {
    $doctor = Get-BridgeDoctorContract
    $newSurfaceEvidence = Get-BridgeRunOnceReadinessEvidenceState

    $installationChecks = [ordered]@{
        installed_manifest_valid = [bool]$doctor.installed_manifest.valid
        artifacts_complete = [bool]$doctor.artifacts.complete
        hook_configuration_valid = [bool]$doctor.hooks.valid
        historical_beeper_rules_tombstoned = [bool]$doctor.historical_beeper_rules.historical_beeper_rules_tombstoned
        source_runtime_parity_current = [bool]$doctor.source_runtime_parity.current
        environment_valid = [bool]$doctor.environment.valid
        access_policy_ready = (
            [string]$doctor.access_policy.mode -ceq 'locked' -and
            [bool]$doctor.access_policy.valid_identity_configured -and
            @($doctor.access_policy.invalid_identity_keys).Count -eq 0
        )
        agents_rules_current = [bool]$doctor.agents_rules.current
        runtime_identity_safe = ([string]$doctor.runtime.state -cne 'unknown')
    }
    [string[]]$installationIssueCodes = @()
    if (-not $installationChecks.installed_manifest_valid) { $installationIssueCodes += 'installed_manifest_invalid' }
    if (-not $installationChecks.artifacts_complete) { $installationIssueCodes += 'installed_artifacts_incomplete' }
    if (-not $installationChecks.hook_configuration_valid) { $installationIssueCodes += 'hook_configuration_invalid' }
    if (-not $installationChecks.historical_beeper_rules_tombstoned) {
        $installationIssueCodes += 'historical_beeper_rules_not_tombstoned'
        $installationIssueCodes += @($doctor.historical_beeper_rules.issue_codes)
    }
    if (-not $installationChecks.source_runtime_parity_current) { $installationIssueCodes += 'source_runtime_parity_stale' }
    if (-not $installationChecks.environment_valid) { $installationIssueCodes += 'environment_invalid' }
    if (-not $installationChecks.access_policy_ready) { $installationIssueCodes += 'access_policy_not_ready' }
    if (-not $installationChecks.agents_rules_current) { $installationIssueCodes += 'agents_rules_not_current' }
    if (-not $installationChecks.runtime_identity_safe) { $installationIssueCodes += 'runtime_identity_unresolved' }
    $installationReady = ($installationIssueCodes.Count -eq 0)

    $newSurfaceTrusted = (
        [bool]$newSurfaceEvidence.controller_provenance_supported -and
        [bool]$newSurfaceEvidence.installed_manifest_bound -and
        [bool]$newSurfaceEvidence.release_inventory_bound -and
        [bool]$newSurfaceEvidence.source_runtime_bound -and
        [bool]$newSurfaceEvidence.surface_contract_bound -and
        [bool]$newSurfaceEvidence.evidence_binding_bound
    )
    $hookReviewPassed = [bool]$newSurfaceEvidence.hook_visible_review_attested
    $hookReviewBlocker = if (-not $installationChecks.hook_configuration_valid) {
        'hook_configuration_invalid'
    } elseif ($hookReviewPassed) {
        $null
    } else {
        'hook_visible_review_required'
    }
    $schedulerTerminalObserved = $false
    $taskToolTerminalObserved = $false
    $schedulerSurfacePassed = (
        $newSurfaceTrusted -and
        [bool]$newSurfaceEvidence.closed_runtime_attestation -and
        [bool]$newSurfaceEvidence.immutable_runtime_receipt_attested -and
        [bool]$newSurfaceEvidence.single_beeper_attested -and
        [bool]$newSurfaceEvidence.beeper_role_isolation_attested
    )
    $taskToolSurfacePassed = (
        $newSurfaceTrusted -and
        [bool]$newSurfaceEvidence.closed_runtime_attestation -and
        [bool]$newSurfaceEvidence.immutable_runtime_receipt_attested -and
        [bool]$newSurfaceEvidence.single_beeper_attested -and
        [bool]$newSurfaceEvidence.beeper_role_isolation_attested -and
        [bool]$newSurfaceEvidence.desktop_responder_ownership_attested -and
        [bool]$newSurfaceEvidence.task_coordination_policy_attested -and
        [bool]$newSurfaceEvidence.alternate_responder_client_exclusion_attested -and
        [bool]$newSurfaceEvidence.task_tool_surface_attested
    )
    [string[]]$schedulerBlockerCodes = @(
        if (-not $schedulerSurfacePassed) { 'run_once_runtime_attestation_unverified' }
        if (-not [bool]$newSurfaceEvidence.single_beeper_attested) { 'single_beeper_unverified' }
        if (-not [bool]$newSurfaceEvidence.beeper_role_isolation_attested) { 'beeper_role_isolation_unverified' }
    )
    [string[]]$taskToolBlockerCodes = @(
        if (-not $taskToolSurfacePassed) { 'run_once_task_tool_surface_unverified' }
        if (-not [bool]$newSurfaceEvidence.desktop_responder_ownership_attested) { 'desktop_responder_ownership_unverified' }
        if (-not [bool]$newSurfaceEvidence.task_coordination_policy_attested) { 'task_coordination_policy_unverified' }
        if (-not [bool]$newSurfaceEvidence.alternate_responder_client_exclusion_attested) { 'alternate_responder_client_exclusion_unverified' }
    )
    $liveE2ePassed = (
        $newSurfaceTrusted -and
        [bool]$newSurfaceEvidence.exact_source_live_e2e_attested -and
        [bool]$newSurfaceEvidence.product_final_callback_attested -and
        [bool]$newSurfaceEvidence.no_replay_attested
    )
    $liveE2eBlocker = if ($liveE2ePassed) { $null } else { 'product_live_e2e_attestation_unverified' }

    $health = $doctor.health_snapshot
    $mvpObservation = $health.mvp_observation
    $bridgeRunning = (
        [bool]$doctor.runtime.running -and
        [bool]$doctor.runtime.identity_verified -and
        [bool]$doctor.runtime.identity_is_bridge
    )
    $currentRuntimeHealth = (
        [bool]$health.present -and
        [bool]$health.valid -and
        [bool]$health.schema_current -and
        [bool]$health.process_identity_current -and
        [bool]$health.runtime_manifest_current -and
        [bool]$health.snapshot_fresh -and
        [string]$health.status -ceq 'online' -and
        [bool]$health.event_consumer -and
        -not [string]::IsNullOrWhiteSpace([string]$doctor.installed_manifest.bridge_version) -and
        [string]$health.bridge_version -ceq [string]$doctor.installed_manifest.bridge_version
    )
    $isolatedProducerRegistered = (
        [string]$health.session_owner -ceq 'beeper' -and
        [string]$health.beeper_transport -ceq 'codex-queue' -and
        [string]$health.beeper_state -ceq 'beeper-registered-load-unobserved'
    )
    $queueCounts = $health.queue_counts
    $postDeliveryIdle = (
        $null -ne $queueCounts -and
        $null -ne $health.active_turns -and
        [int]$health.active_turns -eq 0 -and
        $null -ne $health.beeper_pending -and
        [int]$health.beeper_pending -eq 0 -and
        $null -ne $health.beeper_claimed -and
        [int]$health.beeper_claimed -eq 0 -and
        $null -ne $health.actionable_retryable_failed -and
        [int]$health.actionable_retryable_failed -eq 0 -and
        $health.dial_inflight -is [bool] -and
        -not [bool]$health.dial_inflight -and
        $null -eq $health.dial_lease_remaining_seconds -and
        [int]$queueCounts.queued -eq 0 -and
        [int]$queueCounts.running -eq 0 -and
        [int]$queueCounts.control_sending -eq 0 -and
        [int]$queueCounts.reply_pending -eq 0
    )
    $finalCallbackObserved = (
        $null -ne $mvpObservation -and
        [string]$mvpObservation.status -ceq 'passed' -and
        [string]$mvpObservation.final_callback_source -ceq 'final_callback'
    )
    $feishuDeliveryObserved = (
        $finalCallbackObserved -and
        [bool]$mvpObservation.feishu_delivery_observed
    )
    $knownDeliveryFidelityObserved = (
        $finalCallbackObserved -and
        [bool]$mvpObservation.known_delivery_fidelity_observed
    )
    $singleInboxClaimObserved = (
        $finalCallbackObserved -and
        [bool]$mvpObservation.single_inbox_claim_observed
    )
    $bridgeOutboxScrubbed = (
        $finalCallbackObserved -and
        [bool]$mvpObservation.bridge_outbox_scrubbed
    )
    $mvpChecks = [ordered]@{
        installation_integrity = $installationReady
        bridge_running = $bridgeRunning
        current_runtime_health = $currentRuntimeHealth
        isolated_producer_registered = $isolatedProducerRegistered
        post_delivery_idle = $postDeliveryIdle
        final_callback_observed = $finalCallbackObserved
        feishu_delivery_observed = $feishuDeliveryObserved
        known_delivery_fidelity_observed = $knownDeliveryFidelityObserved
        single_inbox_claim_observed = $singleInboxClaimObserved
        bridge_outbox_scrubbed = $bridgeOutboxScrubbed
    }
    [string[]]$mvpBlockerCodes = @(
        if (-not $installationReady) { 'installation_not_current' }
        if (-not $bridgeRunning) { 'bridge_not_running' }
        if (-not $currentRuntimeHealth) { 'runtime_health_not_current' }
        if (-not $isolatedProducerRegistered) { 'beeper_not_registered' }
        if (-not $postDeliveryIdle) { 'post_delivery_not_idle' }
        if (-not $finalCallbackObserved) { 'final_callback_not_observed_in_current_process' }
        if (-not $feishuDeliveryObserved) { 'feishu_delivery_not_observed' }
        if (-not $knownDeliveryFidelityObserved) { 'delivery_fidelity_unknown' }
        if (-not $singleInboxClaimObserved) { 'single_inbox_claim_not_observed' }
        if (-not $bridgeOutboxScrubbed) { 'bridge_outbox_not_scrubbed' }
    )
    $mvpPassed = $mvpBlockerCodes.Count -eq 0

    [string[]]$productionBlockerCodes = @()
    $productionBlockerCodes += @($installationIssueCodes)
    if ($hookReviewBlocker) { $productionBlockerCodes += $hookReviewBlocker }
    $productionBlockerCodes += @($schedulerBlockerCodes)
    $productionBlockerCodes += @($taskToolBlockerCodes)
    if ($liveE2eBlocker) { $productionBlockerCodes += $liveE2eBlocker }
    $productionBlockerCodes += @($newSurfaceEvidence.issue_codes)
    [string[]]$uniqueProductionBlockerCodes = @(
        $productionBlockerCodes | Select-Object -Unique
    )
    # Historical markers remain permanent in their closed namespace, but they
    # are not copied into a materially different product run_once namespace.
    # Only controller-owned, exact-source evidence may lift the new gates.
    $productionEligible = (
        $installationReady -and
        $hookReviewPassed -and
        $schedulerSurfacePassed -and
        $taskToolSurfacePassed -and
        $liveE2ePassed -and
        [bool]$newSurfaceEvidence.production_gate_passed
    )

    return [pscustomobject][ordered]@{
        schema_version = 1
        command = 'bridge.readiness'
        status = $(if ($productionEligible) { 'ready' } else { 'blocked' })
        answer_free = $true
        installation_integrity = [ordered]@{
            status = $(if ($installationReady) { 'pass' } else { 'fail' })
            production_gate_passed = $installationReady
            checks = $installationChecks
            issue_codes = @($installationIssueCodes)
        }
        mvp = [ordered]@{
            status = $(if ($mvpPassed) { 'passed_with_accepted_risks' } else { 'blocked' })
            eligible = $mvpPassed
            evidence_level = 'current_process_runtime_observation'
            production_equivalent = $false
            checks = $mvpChecks
            accepted_risk_codes = @(
                'product_run_once_unavailable',
                'final_callback_caller_turn_attestation_unavailable'
            )
            blocker_codes = @($mvpBlockerCodes)
        }
        hook_review = [ordered]@{
            status = $(
                if (-not $installationChecks.hook_configuration_valid) { 'configuration_invalid' }
                elseif ($hookReviewPassed) { 'passed' }
                else { 'review_required' }
            )
            configured = [bool]$installationChecks.hook_configuration_valid
            machine_verifiable = $newSurfaceTrusted
            visible_review_observed = $hookReviewPassed
            production_gate_passed = $hookReviewPassed
            blocker_codes = @($(if ($hookReviewBlocker) { $hookReviewBlocker }))
        }
        scheduler_surface = [ordered]@{
            status = $(if ($schedulerSurfacePassed) { 'passed' } else { 'blocked' })
            hard_cap_enforced = $schedulerSurfacePassed
            exact_surface_attested = $newSurfaceTrusted
            automation_identity_attested = $schedulerSurfacePassed
            responder_attested = $schedulerSurfacePassed
            prompt_attested = $schedulerSurfacePassed
            recurrence_attested = $schedulerSurfacePassed
            immutable_runtime_receipt_attested = [bool]$newSurfaceEvidence.immutable_runtime_receipt_attested
            single_beeper_attested = [bool]$newSurfaceEvidence.single_beeper_attested
            beeper_role_isolation_attested = [bool]$newSurfaceEvidence.beeper_role_isolation_attested
            historical_terminal_observed = $schedulerTerminalObserved
            production_gate_passed = $schedulerSurfacePassed
            blocker_codes = @($schedulerBlockerCodes)
        }
        task_tool_surface = [ordered]@{
            status = $(if ($taskToolSurfacePassed) { 'passed' } else { 'blocked' })
            exact_surface_attested = $newSurfaceTrusted
            responder_attested = $taskToolSurfacePassed
            immutable_runtime_receipt_attested = [bool]$newSurfaceEvidence.immutable_runtime_receipt_attested
            desktop_responder_ownership_attested = [bool]$newSurfaceEvidence.desktop_responder_ownership_attested
            task_coordination_policy_attested = [bool]$newSurfaceEvidence.task_coordination_policy_attested
            alternate_responder_client_exclusion_attested = [bool]$newSurfaceEvidence.alternate_responder_client_exclusion_attested
            terminal_failure_observed = $taskToolTerminalObserved
            production_gate_passed = $taskToolSurfacePassed
            blocker_codes = @($taskToolBlockerCodes)
        }
        live_e2e = [ordered]@{
            status = $(if ($liveE2ePassed) { 'passed' } else { 'unverified' })
            exact_source_attested = $liveE2ePassed
            product_final_callback_attested = $liveE2ePassed
            feishu_delivery_attested = $liveE2ePassed
            production_gate_passed = $liveE2ePassed
            blocker_codes = @($(if ($liveE2eBlocker) { $liveE2eBlocker }))
        }
        future_surface_evidence = $newSurfaceEvidence
        terminal_markers = [ordered]@{
            historical_state = 'closed'
            evidence_status = 'retired'
            contract_forbidden_codes = @('native_final_readback_unsupported')
            historical_observed_codes = @()
            historical_namespace_closed = $true
            forensic_material_match = $false
            provenance_current = $false
            exact_surface_attested = $newSurfaceTrusted
            scheduler_automation_identity_attested = $schedulerSurfacePassed
            scheduler_responder_attested = $schedulerSurfacePassed
            scheduler_prompt_attested = $schedulerSurfacePassed
            scheduler_recurrence_attested = $schedulerSurfacePassed
            immutable_runtime_receipt_attested = [bool]$newSurfaceEvidence.immutable_runtime_receipt_attested
            historical_namespace_blocking_codes = @()
            production_blocking_historical_codes = @()
            non_blocking_historical_codes = @()
            issue_codes = @()
        }
        production = [ordered]@{
            status = $(if ($productionEligible) { 'ready' } else { 'blocked' })
            eligible = $productionEligible
            blocker_codes = @($uniqueProductionBlockerCodes)
        }
    }
}

function Invoke-BridgeReadiness {
    param([Parameter(Mandatory = $true)]$Contract)

    $contract = $Contract
    Write-Output ("Production readiness: {0}" -f $contract.status.ToUpperInvariant())
    Write-Output ("MVP: {0}" -f $contract.mvp.status.ToUpperInvariant())
    Write-Output ("Installation integrity: {0}" -f $contract.installation_integrity.status.ToUpperInvariant())
    Write-Output ("Hook review: {0}" -f $contract.hook_review.status.ToUpperInvariant())
    Write-Output ("Scheduler surface: {0}" -f $contract.scheduler_surface.status.ToUpperInvariant())
    Write-Output ("Task-tool surface: {0}" -f $contract.task_tool_surface.status.ToUpperInvariant())
    Write-Output ("Live E2E: {0}" -f $contract.live_e2e.status.ToUpperInvariant())
    Write-Output ("Future run_once evidence: {0}" -f $contract.future_surface_evidence.status.ToUpperInvariant())
    Write-Output ("Production eligible: {0}" -f ([string][bool]$contract.production.eligible).ToLowerInvariant())
    Write-Output ("Terminal markers: {0}" -f ((
        @($contract.terminal_markers.contract_forbidden_codes) +
        @($contract.terminal_markers.historical_observed_codes) |
            Select-Object -Unique
    ) -join ','))
}

function Get-BridgeValidateContract {
    try {
        $null = @(Invoke-BridgeValidate)
        return [pscustomobject][ordered]@{
            schema_version = 1
            command = 'bridge.validate'
            status = 'pass'
            child_process_started = $false
            error = $null
        }
    } catch {
        return [pscustomobject][ordered]@{
            schema_version = 1
            command = 'bridge.validate'
            status = 'fail'
            child_process_started = $false
            error = [ordered]@{
                code = 'validation_failed'
            }
        }
    }
}

function Invoke-BridgeLogs {
    $paths = Get-BridgePaths
    if (-not (Test-Path -LiteralPath $paths.Log)) { throw 'No bridge log exists yet.' }
    Get-Content -LiteralPath $paths.Log -Tail ([Math]::Max(1, [Math]::Min($Tail, 500)))
}

function Invoke-BridgeValidate {
    $skillRoot = Split-Path -Parent $PSScriptRoot
    $inventoryPath = Join-Path $skillRoot 'assets\release-inventory.json'
    if (-not (Test-Path -LiteralPath $inventoryPath -PathType Leaf)) {
        throw 'Release inventory is missing.'
    }
    try {
        $releaseInventory = Get-Content -LiteralPath $inventoryPath -Raw -Encoding utf8 | ConvertFrom-Json
    } catch {
        throw "Release inventory is invalid JSON: $($_.Exception.Message)"
    }
    if ([int]$releaseInventory.schema_version -ne 1) {
        throw 'Unsupported release inventory schema.'
    }
    $desktopInventories = @($releaseInventory.components | Where-Object { $_.name -eq 'desktop_bridge' })
    if ($desktopInventories.Count -ne 1) {
        throw 'Release inventory must contain exactly one desktop_bridge component.'
    }
    $required = @($desktopInventories[0].paths | ForEach-Object { ([string]$_).Replace('/', '\') })
    $missing = @($required | Where-Object { -not (Test-Path -LiteralPath (Join-Path $skillRoot $_)) })
    if ($missing.Count -gt 0) {
        throw "Missing Skill artifacts: $($missing -join ', ')"
    }
    $releaseAuditPath = Join-Path $skillRoot 'scripts\audit-feishu-codex-release.ps1'
    $repositoryMarketplacePath = Join-Path `
        (Split-Path -Parent (Split-Path -Parent $skillRoot)) `
        '.agents\plugins\marketplace.json'
    $sourceRole = if (Test-Path -LiteralPath $repositoryMarketplacePath -PathType Leaf) {
        'canonical-development'
    } else {
        'installed-snapshot'
    }
    $releaseAuditText = @(& $releaseAuditPath -DesktopRoot $skillRoot -DesktopOnly -SourceRole $sourceRole) -join "`n"
    try {
        $releaseAuditResult = $releaseAuditText | ConvertFrom-Json
    } catch {
        throw "Desktop release audit did not return valid JSON: $($_.Exception.Message)"
    }
    if ($releaseAuditResult.status -ne 'pass' -or $releaseAuditResult.components.Count -ne 1 -or
        $releaseAuditResult.components[0].name -ne 'desktop_bridge') {
        throw 'Desktop release audit did not return one passing desktop_bridge component.'
    }
    $ignoreText = Get-Content -LiteralPath (Join-Path $skillRoot '.gitignore') -Raw -Encoding utf8
    foreach ($releaseLocal in @('.codex/', '.agents/skills/', '_retired/', '/.tmp/', '/AGENTS.md', '/EXPERIMENT-LOG.md', '/skills-lock.json')) {
        if ($ignoreText -notmatch [regex]::Escape($releaseLocal)) {
            throw "Release hygiene is missing local-only ignore: $releaseLocal"
        }
    }
    $workspaceAgents = Join-Path $skillRoot 'AGENTS.md'
    $canonicalAgents = Join-Path $skillRoot 'assets\AGENTS.feishu-codex-bridge.md'
    if ((Test-Path -LiteralPath $workspaceAgents -PathType Leaf) -and
        (Get-FileHash -LiteralPath $workspaceAgents -Algorithm SHA256).Hash -ne
        (Get-FileHash -LiteralPath $canonicalAgents -Algorithm SHA256).Hash) {
        throw 'Workspace AGENTS.md and the canonical Bridge policy asset have drifted.'
    }
    $syntaxErrors = @()
    foreach ($path in @(
        $required |
            Where-Object { $_ -like '*.ps1' } |
            ForEach-Object { Join-Path $skillRoot $_ }
    )) {
        $tokens = $null
        $errors = $null
        [System.Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$errors) | Out-Null
        if ($errors.Count -gt 0) {
            $syntaxErrors += ("{0}: {1}" -f $path, (($errors | ForEach-Object { $_.Message }) -join '; '))
        }
    }
    if ($syntaxErrors.Count -gt 0) {
        throw "PowerShell syntax validation failed: $($syntaxErrors -join ' | ')"
    }

    $p3RunnerText = Get-Content -LiteralPath (
        Join-Path $skillRoot 'scripts\external_p3_soak_runner.py'
    ) -Raw -Encoding utf8
    $p3SupervisorText = Get-Content -LiteralPath (
        Join-Path $skillRoot 'scripts\run-external-p3-soak.ps1'
    ) -Raw -Encoding utf8
    $p3ValidatorText = Get-Content -LiteralPath (
        Join-Path $skillRoot 'scripts\validate-external-p3-soak-evidence.ps1'
    ) -Raw -Encoding utf8
    $p3WrapperText = Get-Content -LiteralPath (
        Join-Path $skillRoot 'scripts\invoke-external-p3-soak-once.ps1'
    ) -Raw -Encoding utf8
    $externalSuiteWrapperText = Get-Content -LiteralPath (
        Join-Path $skillRoot 'scripts\invoke-external-p0b-p3-once.ps1'
    ) -Raw -Encoding utf8
    $p3ReferenceText = Get-Content -LiteralPath (
        Join-Path $skillRoot 'references\p3-bounded-soak.md'
    ) -Raw -Encoding utf8
    $p3Schema = Get-Content -LiteralPath (
        Join-Path $skillRoot 'assets\external-p3-soak-evidence.schema.json'
    ) -Raw -Encoding utf8 | ConvertFrom-Json -ErrorAction Stop
    foreach ($marker in @(
        'FEISHU_BRIDGE_EXTERNAL_P3_SOAK',
        'ExternalP3SoakJob',
        'Get-ExternalRunnerProcessGuard',
        'codex_ancestor_match_count',
        'runner_surface',
        'snapshot_files_pinned',
        'p0-validation-pre',
        'Get-LifecycleMutexName',
        'Get-BridgeObservation',
        'bridge_stopped_receipt',
        'held_for_complete_window',
        'p0_evidence_rehashed_after_run',
        'source-snapshot',
        'live_desktop_contacted',
        'live_feishu_contacted'
    )) {
        if ($p3SupervisorText -notmatch [regex]::Escape($marker)) {
            throw "P3 bounded soak supervisor is missing safety marker: $marker"
        }
    }
    foreach ($pathGuardText in @($p3SupervisorText, $p3ValidatorText)) {
        foreach ($marker in @(
            '[System.IO.FileInfo]',
            '$current.Directory',
            '[System.IO.DirectoryInfo]',
            '$current.Parent'
        )) {
            if ($pathGuardText -notmatch [regex]::Escape($marker)) {
                throw "P3 file/directory path-chain guard is missing marker: $marker"
            }
        }
    }
    if ($p3ValidatorText -notmatch [regex]::Escape('[AllowEmptyCollection()]') -or
        $p3ValidatorText -notmatch [regex]::Escape('Add-PinnedReadHandle')) {
        throw 'P3 validator must accept its initially empty pinned-handle collection.'
    }
    foreach ($marker in @(
        'ConvertTo-P3DateTimeOffset',
        '[DateTimeKind]::Unspecified',
        '[DateTimeOffset]([DateTime]$Value)'
    )) {
        if ($p3ValidatorText -notmatch [regex]::Escape($marker)) {
            throw "P3 validator is missing timestamp precision marker: $marker"
        }
    }
    foreach ($marker in @(
        'MIN_ITERATIONS = 25',
        'MAX_ITERATIONS = 100',
        'SCENARIO_CONTRACT = (',
        'class ForbiddenPopen(original_popen)',
        'child_process_policy',
        'child_process_attempts',
        'live_desktop_contacted',
        'live_feishu_contacted'
    )) {
        if ($p3RunnerText -notmatch [regex]::Escape($marker)) {
            throw "P3 bounded soak runner is missing contract marker: $marker"
        }
    }
    $p3ScenarioPairs = [ordered]@{
        grant_claim_race = 'test_beeper_queue.BeeperQueueTests.test_unclaimed_failure_cas_and_claim_are_exclusive'
        callback_duplicate_convergence = 'test_beeper_queue.BeeperQueueTests.test_final_callback_finish_is_exactly_once'
        callback_conflict_convergence = 'test_beeper_queue.BeeperQueueTests.test_final_callback_conflict_fails_closed_and_scrubs_capability'
        terminal_release_race = 'test_beeper_queue.BeeperQueueTests.test_finish_rechecks_terminal_after_release_race'
        delayed_claim_window = 'test_beeper_queue.BeeperQueueTests.test_finish_waits_for_delayed_beeper_claim'
        unclaimed_restart_recovery = 'test_beeper_queue.BeeperQueueTests.test_unclaimed_crash_state_reconciles_on_restart'
        pre_start_restart_requeue = 'test_state.DurableStateTests.test_restart_requeues_work_that_never_started_model'
        post_start_restart_no_replay = 'test_state.DurableStateTests.test_restart_does_not_rerun_a_started_model_turn'
        retryable_delivery_disposition = 'test_routing.RoutingTests.test_rate_limit_and_network_failures_remain_retryable'
        terminal_delivery_disposition = 'test_runtime.ReplyDeliveryTests.test_terminal_reply_result_is_not_rescheduled'
    }
    if ($p3ScenarioPairs.Count -ne 10 -or
        [regex]::Matches($p3RunnerText, '"scenario_id"\s*:').Count -ne 10) {
        throw 'P3 bounded soak must define exactly ten scenarios.'
    }
    foreach ($entry in $p3ScenarioPairs.GetEnumerator()) {
        $testMethod = ([string]$entry.Value).Split('.')[-1]
        if ($p3RunnerText -notmatch [regex]::Escape([string]$entry.Key) -or
            $p3RunnerText -notmatch [regex]::Escape($testMethod) -or
            $p3ValidatorText -notmatch [regex]::Escape([string]$entry.Key) -or
            $p3ValidatorText -notmatch [regex]::Escape([string]$entry.Value)) {
            throw "P3 bounded soak scenario mapping drifted: $($entry.Key)"
        }
    }
    foreach ($marker in @(
        'run-external-p3-soak.ps1',
        'validate-external-p3-soak-evidence.ps1',
        'P3 soak supervisor',
        'P3 soak semantic validator'
    )) {
        if ($p3WrapperText -notmatch [regex]::Escape($marker)) {
            throw "P3 one-shot wrapper is missing sequencing marker: $marker"
        }
    }
    foreach ($marker in @(
        'ExternalSuiteAcknowledged',
        'invoke-external-p0b-once.ps1',
        'invoke-external-p3-soak-once.ps1',
        '$startInfo.RedirectStandardOutput = $true',
        '$startInfo.RedirectStandardError = $true',
        '$p0EvidencePath = [string]$p0Envelope.evidence_path',
        "'-P0EvidencePath', `$p0EvidencePath",
        "'-ExpectedP0EvidenceSha256', `$p0EvidenceSha256"
    )) {
        if ($externalSuiteWrapperText -notmatch [regex]::Escape($marker)) {
            throw "Combined external P0-B/P3 wrapper is missing sequencing marker: $marker"
        }
    }
    if ($externalSuiteWrapperText -match [regex]::Escape('2>&1')) {
        throw 'Combined external P0-B/P3 wrapper must keep child stderr separate from JSON stdout.'
    }
    foreach ($forbiddenP3Marker in @(
        'codex.exe',
        'app-server',
        'lark-cli',
        "'bridge', 'start'",
        "'bridge', 'restart'"
    )) {
        if ($p3RunnerText -match [regex]::Escape($forbiddenP3Marker) -or
            $p3SupervisorText -match [regex]::Escape($forbiddenP3Marker)) {
            throw "P3 bounded soak must not contact a live surface: $forbiddenP3Marker"
        }
    }
    if ([string]$p3Schema.properties.evidence_kind.const -cne 'external_p3_bounded_soak' -or
        [int]$p3Schema.properties.execution.properties.scenario_count.const -ne 10 -or
        [int]$p3Schema.properties.execution.properties.iterations.minimum -ne 25 -or
        [int]$p3Schema.properties.execution.properties.iterations.maximum -ne 100 -or
        [int]$p3Schema.properties.execution.properties.total_tests_run.minimum -ne 250 -or
        [bool]$p3Schema.properties.guards.properties.p0_evidence_rehashed_after_run.const -ne $true -or
        [bool]$p3Schema.properties.bridge_stopped_receipt.properties.lifecycle_mutex.properties.held_for_complete_window.const -ne $true -or
        [bool]$p3Schema.properties.execution.properties.live_desktop_contacted.const -or
        [bool]$p3Schema.properties.execution.properties.live_feishu_contacted.const -or
        $p3ReferenceText -notmatch [regex]::Escape('Gate B''s retained')) {
        throw 'P3 bounded soak schema or operator reference drifted from the stopped external contract.'
    }

    $startHookText = Get-Content -LiteralPath (Join-Path $skillRoot 'scripts\start-feishu-codex-bridge.ps1') -Raw -Encoding utf8
    $stopHookText = Get-Content -LiteralPath (Join-Path $skillRoot 'scripts\stop-feishu-codex-bridge.ps1') -Raw -Encoding utf8
    foreach ($hookText in @($startHookText, $stopHookText)) {
        foreach ($marker in @('[switch]$HookInvocation', 'Get-InputPayload -Required:$HookInvocation')) {
            if ($hookText -notmatch [regex]::Escape($marker)) {
                throw "Lifecycle hook is missing fail-closed invocation marker: $marker"
            }
        }
        if ($hookText -match [regex]::Escape('[Console]::IsInputRedirected')) {
            throw 'Lifecycle hooks must not use IsInputRedirected to distinguish Codex hooks from manual commands.'
        }
    }
    foreach ($pythonDiscoveryMarker in @(
        'Update-ProcessPathFromEnvironment',
        'Get-Command python.exe -All',
        'Get-Command py.exe -All',
        'Python 3.10+ was not found'
    )) {
        if ($startHookText -notmatch [regex]::Escape($pythonDiscoveryMarker)) {
            throw "Start hook is missing robust Python discovery marker: $pythonDiscoveryMarker"
        }
    }
    foreach ($detachedLifecycleMarker in @(
        '[switch]$DetachedLaunch',
        'Get-DesktopCodexHostProcessId',
        'Assert-DetachedLaunchLease',
        'Win32_ProcessStartup',
        'CREATE_BREAKAWAY_FROM_JOB',
        '-DetachedLaunch -LeaseId',
        'did not produce a verified Bridge before its bounded deadline'
    )) {
        if ($startHookText -notmatch [regex]::Escape($detachedLifecycleMarker)) {
            throw "Start hook is missing detached lifecycle marker: $detachedLifecycleMarker"
        }
    }
    foreach ($integrityMarker in @(
        '$BRIDGE_RUNTIME_MANIFEST_SCHEMA = 1',
        'runtime-manifest.json',
        'Assert-BridgeRuntimeManifest',
        'does not match installed runtime',
        'failed its runtime manifest check',
        'Bridge environment is missing:',
        'contains a duplicate key at line',
        "-cnotmatch '^CODEX_BRIDGE_[A-Z0-9_]+$'",
        "-like 'CODEX_BRIDGE_*'",
        'Get-ChildItem Env:',
        'Set-Item -LiteralPath'
    )) {
        if ($startHookText -notmatch [regex]::Escape($integrityMarker)) {
            throw "Start hook is missing runtime-integrity marker: $integrityMarker"
        }
    }

    $installerText = Get-Content -LiteralPath (Join-Path $skillRoot 'scripts\install-feishu-codex-bridge.ps1') -Raw -Encoding utf8
    foreach ($marker in @(
        "Remove-BridgeHook 'SessionStart'",
        "Remove-BridgeHook 'SessionEnd'",
        'New-CommandHook $startTarget 10',
        'New-CommandHook $stopTarget 3',
        'Get-Content -LiteralPath $hooksConfigPath -Raw -Encoding utf8',
        '$entries = @()',
        'if ($property) { $entries = @($property.Value) }',
        'New-Object System.Text.UTF8Encoding($false)',
        '[System.IO.File]::WriteAllText($hooksConfigTemporary, $hooksJson, $utf8WithoutBom)',
        '-HookInvocation',
        'SkipRuntimeConfig',
        'Skipped lifecycle hook scripts.',
        'Write-BridgeRuntimeManifest',
        'Update-InstalledStoppedHealthSnapshot',
        'runtime-manifest.json',
        'CODEX_BRIDGE_ACCESS_MODE=locked',
        '$expectedVersion = ''4.2.0-alpha.63''',
        '$BRIDGE_RUNTIME_MANIFEST_SCHEMA = 1'
    )) {
        if ($installerText -notmatch [regex]::Escape($marker)) {
            throw "Installer is missing lifecycle safety marker: $marker"
        }
    }
    foreach ($hooksOnlyMarker in @(
        '[switch]$HooksOnly',
        'Bridge must be stopped before installation changes',
        'separate observable transaction',
        'Get-BridgeProcessIdentity',
        'Hook-only refresh requires an existing bridge installation',
        'Invalidated the previous runtime manifest',
        'no runtime manifest was signed'
    )) {
        if ($installerText -notmatch [regex]::Escape($hooksOnlyMarker)) {
            throw "Installer is missing atomic hook-only marker: $hooksOnlyMarker"
        }
    }
    if ($installerText -match [regex]::Escape('merge-agents-rules.ps1')) {
        throw 'Installer must not merge project rules; bridge init is the separate policy action.'
    }
    if ($installerText -match [regex]::Escape('CODEX_BRIDGE_ACCESS_MODE=compat')) {
        throw 'Fresh installs must be locked/fail-closed; compat is explicit legacy migration behavior.'
    }
    foreach ($forbiddenInstallerAccessParameter in @('$AccessMode', '$OwnerOpenId', '$AdminOpenIds', '$AllowedUserOpenIds', '$AllowedChatIds')) {
        if ($installerText -match [regex]::Escape($forbiddenInstallerAccessParameter)) {
            throw "Installer must not mutate access policy; use bridge access: $forbiddenInstallerAccessParameter"
        }
    }
    if ($installerText -match [regex]::Escape('$config | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $hooksConfigTemporary -Encoding utf8')) {
        throw 'Installer must write Codex hook JSON as UTF-8 without a BOM.'
    }
    if ($installerText -match [regex]::Escape('$entries = if ($property) { @($property.Value) } else { @() }')) {
        throw 'Installer must not collapse an empty matcher-group array into a scalar hook object.'
    }

    $dispatcherText = Get-Content -LiteralPath (Join-Path $skillRoot 'scripts\feishu-codex-bridge.ps1') -Raw -Encoding utf8
    foreach ($marker in @(
        'bridge init',
        'bridge install',
        'bridge upgrade',
        'bridge hooks',
        'bridge final-callback-status',
        'bridge final-callback-register',
        'bridge final-callback-unregister',
        'bridge preflight',
        'bridge readiness',
        'bridge validate',
        'SkipRuntimeConfig',
        'first-bootstrap only',
        'Get-InstalledBridgeHookIssues',
        'Get-BridgeHistoricalBeeperRulesState',
        'historical_beeper_rules_tombstoned',
        'must be a matcher-group array',
        '[switch]$Json',
        'Get-BridgeStatusContract',
        'Get-BridgeDoctorContract',
        'Get-BridgeReadinessContract',
        'Get-BridgeRunOnceReadinessBindings',
        'Get-BridgeRunOnceReadinessEvidenceState',
        'Get-BridgeValidateContract',
        'run-once-readiness-evidence.env',
        'feishu-codex-bridge.beeper-run-once.v1',
        'Invoke-FinalCallbackRegistryHelper',
        'final-callback-registry-status',
        "command = 'bridge.status'",
        "command = 'bridge.doctor'",
        "command = 'bridge.readiness'",
        "command = 'bridge.validate'",
        'answer_free = $true',
        'child_process_started = $false',
        '-Json is supported only for bridge status, bridge doctor, bridge readiness, and bridge validate.'
    )) {
        if ($dispatcherText -notmatch [regex]::Escape($marker)) {
            throw "Dispatcher is missing command marker: $marker"
        }
    }
    foreach ($manifestDiagnosticMarker in @(
        'Get-InstalledBridgeManifestIssues',
        'runtime-manifest.json is missing.',
        '[int]::TryParse($manifestSchemaText, [ref]$manifestSchema)',
        'installed runtime file could not be hashed:',
        'Installed runtime manifest: version, file set, code hashes, and hook hashes are valid.'
    )) {
        if ($dispatcherText -notmatch [regex]::Escape($manifestDiagnosticMarker)) {
            throw "Dispatcher is missing installed-runtime manifest diagnostic marker: $manifestDiagnosticMarker"
        }
    }
    $pluginRoot = $skillRoot
    $repositoryRoot = Split-Path -Parent (Split-Path -Parent $pluginRoot)
    $pluginManifest = Get-Content -LiteralPath (Join-Path $pluginRoot '.codex-plugin\plugin.json') -Raw -Encoding utf8 | ConvertFrom-Json -ErrorAction Stop
    $pluginMcp = Get-Content -LiteralPath (Join-Path $pluginRoot '.mcp.json') -Raw -Encoding utf8 | ConvertFrom-Json -ErrorAction Stop
    $pluginHooksPath = Join-Path $pluginRoot 'hooks\hooks.json'
    $pluginServerText = Get-Content -LiteralPath (Join-Path $pluginRoot 'scripts\final_callback_mcp_server.py') -Raw -Encoding utf8
    $pluginMarketplacePath = Join-Path $repositoryRoot '.agents\plugins\marketplace.json'
    if ([string]$pluginManifest.name -cne 'feishu-codex-bridge' -or
        [string]$pluginManifest.mcpServers -cne './.mcp.json') {
        throw 'P0 Final Callback component manifest is invalid.'
    }
    if (Test-Path -LiteralPath $pluginHooksPath) {
        throw 'The Responder-owned Final Callback plugin must not register UserPromptSubmit or Stop Hooks.'
    }
    $mcpServer = $pluginMcp.mcpServers.feishu_final_callback
    if (-not $mcpServer -or [string]$mcpServer.default_tools_approval_mode -cne 'auto') {
        throw 'P0 Responder-owned Final Callback MCP server must use local automatic tool approval.'
    }
    $mcpToolsBlock = [regex]::Match(
        $pluginServerText,
        '(?ms)^TOOLS:\s*list\[dict\[str,\s*Any\]\]\s*=\s*\[.*?^\]'
    )
    if (-not $mcpToolsBlock.Success) {
        throw 'P0 Responder-owned Final Callback MCP public tool registry could not be isolated.'
    }
    $mcpPublicToolNames = @(
        [regex]::Matches(
            $mcpToolsBlock.Value,
            '(?m)^\s{8}"name":\s*"([a-z0-9_]+)",\s*$'
        ) | ForEach-Object { $_.Groups[1].Value }
    )
    $expectedMcpPublicToolNames = @(
        'claim_and_arm',
        'claim_readonly',
        'complete_readonly',
        'submit_final_callback',
        'finish_final_callback',
        'fail_page'
    )
    $mcpPublicToolDifference = @(
        Compare-Object -ReferenceObject $expectedMcpPublicToolNames -DifferenceObject $mcpPublicToolNames
    )
    if ($mcpPublicToolNames.Count -ne $expectedMcpPublicToolNames.Count -or
        $mcpPublicToolDifference.Count -ne 0) {
        throw 'P0 Responder-owned Final Callback MCP server must expose exactly the six current closed operations.'
    }
    if ($pluginServerText -match '"name"\s*:\s*"finish_readonly"') {
        throw 'Read-only finishing is Bridge-internal and must not be exposed as a public MCP tool.'
    }
    foreach ($marker in @(
        '"name": "claim_and_arm"',
        '"name": "claim_readonly"',
        '"name": "complete_readonly"',
        '"name": "submit_final_callback"',
        '"name": "finish_final_callback"',
        '"name": "fail_page"',
        'ensure_ascii=True',
        'input_bytes=wire',
        'runtime-manifest.json',
        'submit-final-callback',
        'finish-final-callback',
        'INTERNAL_UNCLAIMED_FAILURE_CODES',
        'unclaimed Beeper failures are reserved for the Bridge CAS',
        'selection_proof',
        'final_callback_capability',
        'final_answer'
    )) {
        if ($pluginServerText -notmatch [regex]::Escape($marker)) {
            throw "P0 Responder-owned Final Callback MCP server is missing safety marker: $marker"
        }
    }
    foreach ($forbiddenPluginMarker in @(
        'transcript_path',
        'read_thread',
        'bind_user_prompt',
        'capture_stop_final',
        'final-callback-hook'
    )) {
        if ($pluginServerText -match [regex]::Escape($forbiddenPluginMarker)) {
            throw "P0 Responder-owned Final Callback plugin retains a forbidden Hook/history marker: $forbiddenPluginMarker"
        }
    }
    if (Test-Path -LiteralPath $pluginMarketplacePath -PathType Leaf) {
        $pluginMarketplace = Get-Content -LiteralPath $pluginMarketplacePath -Raw -Encoding utf8 | ConvertFrom-Json -ErrorAction Stop
        $marketplaceEntries = @($pluginMarketplace.plugins | Where-Object { $_.name -eq 'feishu-codex-bridge' })
        if ($marketplaceEntries.Count -ne 1 -or
            [string]$marketplaceEntries[0].source.source -cne 'local' -or
            [string]$marketplaceEntries[0].source.path -cne './plugins/feishu-codex-bridge' -or
            [string]$marketplaceEntries[0].policy.installation -cne 'AVAILABLE') {
            throw 'Repo-local Feishu Bridge plugin marketplace registration is invalid.'
        }
    }
    $startFunction = [regex]::Match(
        $dispatcherText,
        '(?ms)^function Assert-BridgeStartReady\s*\{.*?^function Invoke-BridgeStart\s*\{'
    )
    if (-not $startFunction.Success) {
        throw 'Dispatcher start function could not be isolated for parity validation.'
    }
    foreach ($marker in @('Get-BridgeParity', 'Refusing to start a stale or incomplete installed Feishu bridge runtime')) {
        if ($startFunction.Value -notmatch [regex]::Escape($marker)) {
            throw "Dispatcher start path is missing source/runtime parity marker: $marker"
        }
    }
    $restartFunction = [regex]::Match(
        $dispatcherText,
        '(?ms)^function Invoke-BridgeRestart\s*\{.*?^function Invoke-BridgeStop\s*\{'
    )
    $restartReadyIndex = $restartFunction.Value.IndexOf('Assert-BridgeStartReady')
    $restartStopIndex = $restartFunction.Value.IndexOf('Invoke-BridgeStop')
    if (-not $restartFunction.Success -or $restartReadyIndex -lt 0 -or
        $restartStopIndex -lt 0 -or $restartReadyIndex -gt $restartStopIndex) {
        throw 'bridge restart must validate the installed runtime before stopping the live process.'
    }
    $accessFunction = [regex]::Match(
        $dispatcherText,
        '(?ms)^function Invoke-BridgeAccess\s*\{.*?^function Invoke-FinalCallbackRegistryHelper\s*\{'
    )
    if (-not $accessFunction.Success) {
        throw 'Dispatcher access function could not be isolated for side-effect validation.'
    }
    if ($accessFunction.Value -match [regex]::Escape('Invoke-Installer')) {
        throw 'bridge access must not invoke the installer or modify runtime code, hooks, or project rules.'
    }
    foreach ($marker in @('Assert-BridgeIdentifierList', 'Set-BridgeEnvValue', 'Access policy only was updated')) {
        if ($accessFunction.Value -notmatch [regex]::Escape($marker)) {
            throw "Dispatcher access path is missing policy-only marker: $marker"
        }
    }
    if ($dispatcherText -notmatch [regex]::Escape('[\r\n\x00]')) {
        throw 'Dispatcher must reject multi-line and NUL bridge environment values before writing bridge.env.'
    }
    foreach ($desktopMarker in @(
        'feishu desktop-status',
        'feishu desktop-install [-DesktopInstallConsent]',
        '-DesktopInstallConsent is deprecated and is a compatibility no-op',
        'api/package_info?platform=',
        'Get-AuthenticodeSignature',
        'Installation, process, and cached files do not prove login'
    )) {
        if ($dispatcherText -notmatch [regex]::Escape($desktopMarker)) {
            throw "Dispatcher is missing Feishu Desktop safety marker: $desktopMarker"
        }
    }
    $desktopInstallFunction = [regex]::Match(
        $dispatcherText,
        '(?ms)^function Invoke-FeishuDesktopInstall\s*\{.*?^function Invoke-AgentsInit\s*\{'
    )
    if (-not $desktopInstallFunction.Success) {
        throw 'Dispatcher desktop-install function could not be isolated for automatic-path validation.'
    }
    if ($desktopInstallFunction.Value -match 'if\s*\(\s*-not\s+\$DesktopInstallConsent') {
        throw '-DesktopInstallConsent is a deprecated compatibility no-op and must not gate installation.'
    }
    $preflightFunction = [regex]::Match(
        $dispatcherText,
        '(?ms)^function Invoke-BridgePreflight\s*\{.*?^function Invoke-BridgeTests\s*\{'
    )
    if (-not $preflightFunction.Success) {
        throw 'Dispatcher preflight function could not be isolated for frontend-takeover boundary validation.'
    }
    foreach ($forbiddenPreflightMarker in @(
        'Get-FeishuDesktopExecutable',
        'Invoke-FeishuDesktopStatus',
        'Invoke-FeishuDesktopInstall',
        '[PENDING] Feishu Desktop'
    )) {
        if ($preflightFunction.Value -match [regex]::Escape($forbiddenPreflightMarker)) {
            throw "Generic bridge preflight must not inspect Feishu Desktop: $forbiddenPreflightMarker"
        }
    }
    $clientText = Get-Content -LiteralPath (Join-Path $skillRoot 'scripts\bridge_core\beeper_client.py') -Raw -Encoding utf8
    $beeperText = Get-Content -LiteralPath (Join-Path $skillRoot 'scripts\bridge_core\beeper_queue.py') -Raw -Encoding utf8
    $queueHelperText = Get-Content -LiteralPath (Join-Path $skillRoot 'scripts\beeper_queue_cli.py') -Raw -Encoding utf8
    $runtimeText = Get-Content -LiteralPath (Join-Path $skillRoot 'scripts\bridge_core\runtime.py') -Raw -Encoding utf8
    $desktopBeeperTestText = Get-Content -LiteralPath (Join-Path $skillRoot 'tests\test_beeper_queue.py') -Raw -Encoding utf8
    $beeperClientTestText = Get-Content -LiteralPath (Join-Path $skillRoot 'tests\test_beeper_client.py') -Raw -Encoding utf8
    $runtimeTestText = Get-Content -LiteralPath (Join-Path $skillRoot 'tests\test_runtime.py') -Raw -Encoding utf8
    $pythonRuntimeText = $clientText + "`n" + $beeperText + "`n" + $runtimeText
    $beeperDeepLinkMarker = 'BEEPER_DEEP_LINK_PREFIX = "codex://threads/"'
    if ([regex]::Matches($clientText, [regex]::Escape($beeperDeepLinkMarker)).Count -ne 1) {
        throw 'Beeper must contain exactly one fixed cold-load deep-link prefix.'
    }
    $pythonRuntimeWithoutBeeperDeepLink = $pythonRuntimeText.Replace($beeperDeepLinkMarker, '')
    foreach ($forbidden in @(
        'CodexAppServer',
        'thread/resume',
        'thread/compact/start',
        'turn/start',
        'subprocess.Popen',
        'request_desktop_refresh'
    )) {
        if ($pythonRuntimeText.Contains($forbidden)) {
            throw "Unsafe legacy responder-writer transport remains: $forbidden. Complete the Desktop Beeper migration before starting the bridge."
        }
    }
    if ($pythonRuntimeWithoutBeeperDeepLink.Contains('codex://')) {
        throw 'A deep link exists outside the one fixed Beeper cold-load prefix.'
    }
    foreach ($coldLoadMarker in @(
        'wait_for_beeper_claim',
        'fail_page_if_unclaimed',
        'UNCLAIMED_FAILURE_CODES',
        'beeper_load_assist_failed',
        'beeper_claim_timeout',
        'os.startfile(uri)'
    )) {
        if ($pythonRuntimeText -notmatch [regex]::Escape($coldLoadMarker)) {
            throw "Beeper cold-load safety marker is missing: $coldLoadMarker"
        }
    }
    foreach ($marker in @(
        'HistoricalBeeperClient',
        'send_message_to_thread',
        'ResponderNotBound',
        'BeeperQueue',
        'list_task_catalog',
        'DesktopTaskCatalog',
        'DESKTOP_TASK_CATALOG_LIMIT = 50',
        'INIT_WIZARD_CATALOG_LIMIT = DESKTOP_TASK_CATALOG_LIMIT',
        'selection_proof',
        'No public finish_readonly tool exists; the Bridge owns'
    )) {
        if ($pythonRuntimeText -notmatch [regex]::Escape($marker)) {
            throw "Desktop Beeper transport marker is missing: $marker"
        }
    }
    foreach ($producerTombstoneMarker in @(
        '_ensure_admissible_producer',
        'historical-desktop-beeper-tombstoned',
        'historical-producer-tombstoned',
        'producer_unavailable_no_retry'
    )) {
        if (($clientText + "`n" + $queueHelperText + "`n" + $runtimeText) -notmatch [regex]::Escape($producerTombstoneMarker)) {
            throw "Historical Desktop producer tombstone marker is missing: $producerTombstoneMarker"
        }
    }
    foreach ($misleadingLiveMarker in @('desktop-beeper-on-demand', 'scheduled-idle')) {
        if ($clientText -match [regex]::Escape($misleadingLiveMarker)) {
            throw "Historical Desktop producer client retains a live health label: $misleadingLiveMarker"
        }
    }
    foreach ($retryGenerationMarker in @(
        'MAX_RETRY_GENERATIONS',
        '_response_allows_retry',
        'retry_generation',
        'responder_result_unknown',
        'read-only Desktop Beeper operation cannot be finalized with',
        'exceeded safe retry generations'
    )) {
        if ($beeperText -notmatch [regex]::Escape($retryGenerationMarker)) {
            throw "Desktop Beeper queue is missing deterministic safe-retry validation: $retryGenerationMarker"
        }
    }
    foreach ($finalReturnSourceMarker in @(
        'FINAL_CALLBACK_SOURCES',
        'resolution_source',
        'final_callback_source',
        'not_applicable'
    )) {
        if (($beeperText + "`n" + $queueHelperText) -notmatch [regex]::Escape($finalReturnSourceMarker)) {
            throw "Final Callback answer-free source provenance marker is missing: $finalReturnSourceMarker"
        }
    }
    foreach ($completedResultMarker in @(
        '_invalid_completed_result',
        'expected_thread_id',
        'completed_may_have_started=True',
        'without an authoritative Final Callback answer'
    )) {
        if ($clientText -notmatch [regex]::Escape($completedResultMarker)) {
            throw "Desktop Beeper client is missing completed-result validation: $completedResultMarker"
        }
    }
    foreach ($retiredClientMethod in @(
        'class ThreadCreation',
        'def create_thread(',
        'def restore_thread(',
        'def compact(',
        '_confirmed_archives'
    )) {
        if ($clientText -match [regex]::Escape($retiredClientMethod)) {
            throw "Desktop Beeper client retains a retired mutation method: $retiredClientMethod"
        }
    }
    foreach ($exactFinalClientMarker in @(
        'final_answer = result.get("final_answer")',
        'not final_answer.strip()',
        'final_answer=final_answer',
        'expected_operation == "send_message_to_thread"',
        'response.get("final_callback_source") != "final_callback"',
        'Desktop Beeper send completion has no Final Callback source',
        'Beeper did not publish the fenced Final Callback result',
        'Desktop Beeper terminal operation does not match its request'
    )) {
        if ($clientText -notmatch [regex]::Escape($exactFinalClientMarker)) {
            throw "Desktop Beeper client is missing lossless final-answer marker: $exactFinalClientMarker"
        }
    }
    if ($clientText -match [regex]::Escape('final_answer = str(result.get("final_answer") or "").strip()')) {
        throw 'Desktop Beeper client must not trim the authoritative final answer.'
    }
    foreach ($exactFinalQueueMarker in @(
        'final-callback answer exceeds the exact bounded transport limit',
        '_reconcile_terminal_final_callbacks',
        '_seal_current_final_callback',
        'current send completion accepts only a captured Final Callback source',
        'send completion answer failed captured Responder integrity',
        'Age alone can never make that nonterminal answer disposable',
        "SET state='captured', resolution_source='final_callback'",
        'final_callback_capability_sha256',
        "transport_mode='final_callback'",
        "SET state='completing', resolution_source=?, updated_at=?",
        "WHERE request_id=? AND state IN ('captured','completing')",
        'Beeper sends cannot use legacy native staging',
        '_reject_unsupported_send_mode',
        'Desktop Beeper steer is unsupported; no responder action was queued',
        '_read_exact_final_answer',
        'newline=""',
        '"final_callback_source": self._final_callback_resolution_source',
        "resolution_source IN",
        "ELSE 'unknown'",
        "state IN ('completed','failed','conflict','expired')",
        "state='conflict', prompt_sha256=''",
        "resolution_source='unknown'"
    )) {
        if ($beeperText -notmatch [regex]::Escape($exactFinalQueueMarker)) {
            throw "Desktop Beeper queue is missing exact-final terminal marker: $exactFinalQueueMarker"
        }
    }
    $finalCallbackTestText = $desktopBeeperTestText + "`n" + $beeperClientTestText + "`n" + $runtimeTestText
    foreach ($finalCallbackTestMarker in @(
        'test_final_callback_submission_is_fixed_to_namespace',
        'test_final_callback_submission_reader_preserves_exact_unicode',
        'test_minimal_claim_preserves_supported_non_uuid_responder_id',
        'test_final_callback_finish_is_exactly_once',
        'test_final_callback_conflict_fails_closed_and_scrubs_capability',
        'test_exact_duplicate_rejects_tampered_final_callback_stage',
        'test_expired_final_callback_capability_is_rejected_and_never_captured',
        'test_final_callback_timeout_is_terminal_and_not_retried',
        'test_completed_send_requires_top_level_final_callback_source',
        'test_final_callback_mcp_rejects_metadata_bearing_helper_output',
        'test_final_callback_mcp_accepts_supported_non_uuid_responder_id',
        'test_final_callback_answer_crosses_helper_only_on_utf8_stdin',
        'test_bridge_plugin_bundles_responder_owned_mcp_final_callback',
        'test_current_send_is_final_callback_sealed_and_unsupported_steer_is_rejected',
        'test_response_reconciles_a_published_receipt_without_reopening_queue',
        'test_cleanup_removes_old_staging_only_after_terminal_receipt_exists',
        'test_retention_preserves_nonterminal_captured_stage_until_terminal',
        'test_failed_terminal_operation_mismatch_is_rejected_before_interpretation'
    )) {
        if ($finalCallbackTestText -notmatch [regex]::Escape($finalCallbackTestMarker)) {
            throw "Final Callback current-send test contract is missing: $finalCallbackTestMarker"
        }
    }
    foreach ($immutableOutboxMarker in @(
        'build_reply_plan',
        'ReplyPlan.from_payload',
        'outbox freeze rejected category=state_conflict',
        'initialize_interrupted_reply_plan',
        'self.state.verified_outbound(event_id, event)',
        'reply_pending outbound envelope integrity failed',
        'attachment reply outcome is uncertain and was not replayed',
        'authoritative_source=MVP_FINAL_SOURCE',
        '"final_callback_source": MVP_FINAL_SOURCE',
        '"bridge_outbox_scrubbed": True',
        '"mvp_observation": mvp_observation'
    )) {
        if ($runtimeText -notmatch [regex]::Escape($immutableOutboxMarker)) {
            throw "Bridge runtime is missing immutable outbox marker: $immutableOutboxMarker"
        }
    }
    foreach ($queueEnvironmentMarker in @(
        'seen_names: set[str]',
        'contains a duplicate key at line',
        'is not NAME=VALUE',
        'validate_bridge_env_values',
        'is not an integer',
        'is outside {minimum}..{maximum}'
    )) {
        if ($queueHelperText -notmatch [regex]::Escape($queueEnvironmentMarker)) {
            throw "Queue helper is missing strict bridge environment validation: $queueEnvironmentMarker"
        }
    }
    foreach ($unicodeWireMarker in @(
        'ensure_ascii=True',
        'ASCII-only',
        'exactly one parse'
    )) {
        $wireText = $queueHelperText
        if ($wireText -notmatch [regex]::Escape($unicodeWireMarker)) {
            throw "Queue helper is missing Unicode-safe stdout contract marker: $unicodeWireMarker"
        }
    }
    foreach ($environmentMarker in @(
        'Get-BridgeEnvSemanticIssues',
        'Assert-BridgeEnvSemantics',
        'CODEX_BRIDGE_DOWNLOAD_RESOURCES',
        'CODEX_BRIDGE_MAX_TOTAL_RESOURCE_BYTES',
        'Refusing to start with an invalid bridge.env'
    )) {
        if ($dispatcherText -notmatch [regex]::Escape($environmentMarker) -and
            $startHookText -notmatch [regex]::Escape($environmentMarker)) {
            throw "Bridge entrypoints are missing strict recognized-value validation: $environmentMarker"
        }
    }
    foreach ($lifecycleMutexMarker in @(
        'FeishuCodexBridge-Lifecycle-',
        'WaitOne($mutexWaitMilliseconds)',
        'ReleaseMutex()'
    )) {
        if ($startHookText -notmatch [regex]::Escape($lifecycleMutexMarker)) {
            throw "Bridge start hook is missing the lifecycle verification mutex: $lifecycleMutexMarker"
        }
    }
    foreach ($receiptMarker in @(
        '_receipt_response',
        '_recover_interrupted_finalization',
        '_atomic_write_json_exclusive',
        '_compacted_terminal_receipt',
        '_terminal_result_exists'
    )) {
        if ($beeperText -notmatch [regex]::Escape($receiptMarker)) {
            throw "Desktop Beeper queue is missing durable terminal-receipt handling: $receiptMarker"
        }
    }
    foreach ($canonicalClaimMarker in @(
        'def _actionable_pending_paths',
        'immutable, stable publication anchor',
        '_atomic_write_json_exclusive(claimed_path, claimed_request)',
        'if self._terminal_result_exists(source.stem)'
    )) {
        if ($beeperText -notmatch [regex]::Escape($canonicalClaimMarker)) {
            throw "Desktop Beeper queue is missing canonical/fenced claim publication: $canonicalClaimMarker"
        }
    }
    $expireClaimsFunction = [regex]::Match(
        $beeperText,
        '(?ms)^    def expire_stale_claims\(.*?^    def cleanup\('
    )
    $claimReadIndex = $expireClaimsFunction.Value.IndexOf('request = _read_json(path)')
    $exactDialProtectionIndex = $expireClaimsFunction.Value.IndexOf(
        'self._claim_matches_live_dial('
    )
    $invalidClaimIndex = $expireClaimsFunction.Value.IndexOf('if request is None:')
    if (-not $expireClaimsFunction.Success -or $claimReadIndex -lt 0 -or
        $exactDialProtectionIndex -lt 0 -or $invalidClaimIndex -lt 0 -or
        $claimReadIndex -gt $exactDialProtectionIndex -or
        $exactDialProtectionIndex -gt $invalidClaimIndex -or
        $expireClaimsFunction.Value.IndexOf('pages_by_request.get(request_id)') -lt 0) {
        throw 'Stale-claim maintenance must protect only the exact live request/dial/fence/generation/page owner before terminalization.'
    }
    foreach ($readClaimMarker in @(
        'read_claim_ttl_seconds: int = 300',
        'self.read_claim_ttl_seconds = min(',
        'if operation in READ_ONLY_OPERATIONS',
        'else self.claim_ttl_seconds'
    )) {
        if ($beeperText -notmatch [regex]::Escape($readClaimMarker)) {
            throw "Desktop Beeper queue is missing the bounded read-only claim TTL: $readClaimMarker"
        }
    }
    foreach ($catalogStagingMarker in @(
        'catalog-staging',
        'feishu-codex-bridge/catalog-staging/v1',
        '_stage_catalog_blob',
        '_consume_catalog_blob',
        'hmac.compare_digest',
        'Consume a catalog blob once; the immutable receipt stays answer-free.',
        'Beeper-facing late failure is only an answer-free terminal ack.',
        'catalog_staged',
        '*.consuming'
    )) {
        if ($beeperText -notmatch [regex]::Escape($catalogStagingMarker)) {
            throw "Desktop Beeper queue is missing sealed private catalog staging: $catalogStagingMarker"
        }
    }
    foreach ($catalogStagingTestMarker in @(
        'test_catalog_is_staged_answer_free_then_consumed_once',
        'test_catalog_tamper_is_rejected_and_scrubbed',
        'test_catalog_interrupted_consume_is_not_replayed_and_ages_out',
        'test_catalog_cleanup_preserves_fresh_consumer_and_scrubs_stale_one',
        'test_beeper_and_tombstones_cannot_be_business_responders',
        'test_orphan_readonly_finalization_is_safe_unknown_and_not_replayed',
        'test_readonly_claim_expiry_is_terminal_and_not_replayed',
        'test_queue_helper_operational_failure_is_one_answer_free_ascii_object'
    )) {
        if ($desktopBeeperTestText -notmatch [regex]::Escape($catalogStagingTestMarker)) {
            throw "Desktop Beeper private catalog regression is missing: $catalogStagingTestMarker"
        }
    }
    foreach ($readonlyTerminalMarker in @(
        '_fail_expired_read_claim',
        '_finish_existing_readonly',
        'test_existing_catalog_handoff_failure_never_spawns_or_retries',
        'init reply aborted without replay',
        'init command aborted without replay',
        'test_unexpected_init_exception_is_terminal_and_never_scheduler_retried'
    )) {
        if (($beeperText + "`n" + $clientText + "`n" + $runtimeText + "`n" +
                $beeperClientTestText + "`n" + $runtimeTestText) -notmatch
            [regex]::Escape($readonlyTerminalMarker)) {
            throw "Current read-only terminal contract is missing: $readonlyTerminalMarker"
        }
    }
    foreach ($clientMutationUnknownMarker in @(
        'Desktop Beeper queue state conflicts with a possibly-started responder action',
        'no fenced in-flight steer lane; no responder send was submitted'
    )) {
        if ($clientText -notmatch [regex]::Escape($clientMutationUnknownMarker)) {
            throw "Desktop Beeper client is missing protocol-conflict fail-closed handling: $clientMutationUnknownMarker"
        }
    }
    $beeperRuleText = Get-Content -LiteralPath (Join-Path $skillRoot 'assets\feishu-beeper.rules.template') -Raw -Encoding utf8
    foreach ($marker in @(
        'HISTORICAL_BEEPER_RULES_TOMBSTONE_V1',
        'intentionally defines zero `prefix_rule` entries',
        'producer details are not an operational contract',
        'current isolated producer has its own closed',
        'admission path and does not obtain authority',
        'future product-level pre-dispatch `run_once` producer',
        'must never add rules here'
    )) {
        if ($beeperRuleText -notmatch [regex]::Escape($marker)) {
            throw "Historical Desktop Beeper rule tombstone is missing marker: $marker"
        }
    }
    foreach ($executableRulePattern in @(
        '(?m)^\s*prefix_rule\s*\(',
        '(?m)^\s*decision\s*=\s*["'']allow["'']',
        '(?m)^\s*match\s*=\s*\[',
        '(?m)^\s*not_match\s*=\s*\[',
        '\{\{(?:PYTHON|BEEPER_QUEUE|RUNTIME_DIR)_RULE_PATH\}\}'
    )) {
        if ($beeperRuleText -match $executableRulePattern) {
            throw "Historical Desktop Beeper rule tombstone retains executable allow syntax: $executableRulePattern"
        }
    }
    $stateText = Get-Content -LiteralPath (Join-Path $skillRoot 'scripts\bridge_core\state.py') -Raw -Encoding utf8
    foreach ($marker in @(
        'related_thread_ids',
        'def canonical_scope',
        '_conversation_scope',
        '_consolidate_scope_locked',
        'def consolidate_scope',
        'Return the one canonical session for a stable Feishu conversation.',
        'def bind_thread_if_current',
        'scope = self._conversation_scope(scope)',
        'next user message is rejected as a stale wizard reply'
    )) {
        if ($stateText -notmatch [regex]::Escape($marker)) {
            throw "Bridge state is missing canonical conversation or /init binding marker: $marker"
        }
    }
    foreach ($outboxStateMarker in @(
        'SCHEMA_VERSION = 7',
        '"control_sending"',
        'def admit_control(',
        'begin_control_reply',
        'finish_control_reply',
        'control reply interrupted after single-attempt admission',
        "WHERE event_id=? AND status='control_sending'",
        'outbound_plan_json',
        'OUTBOUND_ENVELOPE_DOMAIN',
        'outbound_answer_sha256',
        'outbound_answer_chars',
        'outbound_plan_sha256',
        'outbound_envelope_sha256',
        'sort_keys=True',
        'BEGIN IMMEDIATE',
        'verified_outbound',
        'latest_delivery_fidelity',
        "WHERE event_id=? AND status='running'",
        'initialize_interrupted_reply_plan',
        'outbound_plan_json IS NULL',
        'Freeze the first outbound answer/plan exactly once from running',
        "WHERE event_id=? AND status='reply_pending'",
        "status NOT IN ('completed','terminal_failed')",
        'payload_json=NULL, answer=NULL',
        'explicit_transform requires at least one transform label'
    )) {
        if ($stateText -notmatch [regex]::Escape($outboxStateMarker)) {
            throw "Bridge state is missing immutable answer-free outbox marker: $outboxStateMarker"
        }
    }
    $runtimeText = Get-Content -LiteralPath (Join-Path $skillRoot 'scripts\bridge_core\runtime.py') -Raw -Encoding utf8
    if ($runtimeText -notmatch [regex]::Escape('path.read_text(encoding="utf-8-sig")')) {
        throw 'Lifecycle lease reader must accept the UTF-8 BOM written by Windows PowerShell 5.1.'
    }
    foreach ($forbiddenMarker in @('ObsidianKnowledgeRetriever', 'should_retrieve', 'obsidian_retrieval', 'obsidian_root_registered', 'feishu_message')) {
        if ($runtimeText -match [regex]::Escape($forbiddenMarker)) {
            throw "Bridge runtime must not package model context: $forbiddenMarker"
        }
    }
    foreach ($requiredMarker in @('transport_attachments', 'service desk', 'never put RAG, summaries, history')) {
        if ($runtimeText -notmatch [regex]::Escape($requiredMarker)) {
            throw "Bridge runtime is missing service-desk boundary marker: $requiredMarker"
        }
    }
    foreach ($outboxRuntimeMarker in @(
        'verified_outbound(event_id, event)',
        'ReplyPlan.from_payload(verified_plan)',
        'reply_pending outbound envelope integrity failed',
        'integrity rejected before first send'
    )) {
        if ($runtimeText -notmatch [regex]::Escape($outboxRuntimeMarker)) {
            throw "Bridge runtime is missing sealed outbox verification marker: $outboxRuntimeMarker"
        }
    }
    foreach ($controlReplyMarker in @(
        '_deliver_control_once',
        'self.state.admit_control(event_id)',
        'self.state.begin_control_reply(event_id)',
        'self.state.finish_control_reply(',
        'Admission was already consumed.  Never replay',
        '_wizard_like_token',
        'build_reply_plan(answer, self.config, allow_attachments=False)'
    )) {
        if ($runtimeText -notmatch [regex]::Escape($controlReplyMarker)) {
            throw "Bridge runtime is missing single-attempt control-reply marker: $controlReplyMarker"
        }
    }
    foreach ($controlAdmissionTestMarker in @(
        'test_binding_commit_control_crash_is_terminal_after_reopen',
        'test_cancel_or_exit_control_crash_is_terminal_after_reopen',
        'test_other_group_member_wizard_token_is_not_routed_as_business',
        'test_initiator_role_change_cannot_turn_wizard_token_into_business',
        'test_init_task_title_marker_is_never_an_attachment',
        'test_init_project_label_marker_is_never_an_attachment'
    )) {
        if ($runtimeTestText -notmatch [regex]::Escape($controlAdmissionTestMarker)) {
            throw "Bridge runtime is missing a control-admission regression: $controlAdmissionTestMarker"
        }
    }
    foreach ($stableScopeMarker in @(
        'to the stable Feishu conversation itself',
        'return base, decision.role, fingerprint, decision.allowed',
        'scope = SessionStore.canonical_scope(scope)',
        'self.sessions.consolidate_scope(scope)',
        'Serialize them with new canonical'
    )) {
        if ($runtimeText -notmatch [regex]::Escape($stableScopeMarker)) {
            throw "Bridge runtime is missing canonical stable-scope marker: $stableScopeMarker"
        }
    }
    foreach ($wizardMarker in @(
        'INIT_WIZARD_TTL_SECONDS',
        'UNSUPPORTED_COMMAND_REPLY',
        '_init_wizards',
        'init_wizard_expires_at',
        '_begin_init_wizard',
        '_handle_init_wizard_reply',
        '_wizard_selection',
        '_bind_existing_thread',
        '_discard_wizard_memory',
        'list_task_catalog',
        'bind_thread_if_current',
        'selection_proof',
        'selected_task.get("kind") != "codex"'
    )) {
        if ($runtimeText -notmatch [regex]::Escape($wizardMarker)) {
            throw "Bridge runtime is missing conversational /init marker: $wizardMarker"
        }
    }
    foreach ($removedCommandImplementation in @(
        '_help_answer',
        '_reuse_previous_thread',
        '_handle_new_request',
        '_process_control_event',
        'decide_new_intent',
        '_handle_project_command',
        '_project_use',
        '_project_new',
        '_create_and_bind_thread',
        '_compact_and_continue',
        '_replace_unavailable_responder',
        '_should_auto_replace_unavailable_responder',
        '_responder_delivery_request_key',
        'pending_project_request_key',
        'resolve_new_project_root',
        'validate_staged_project_root'
    )) {
        if ($runtimeText -match [regex]::Escape($removedCommandImplementation)) {
            throw "Bridge runtime retains an obsolete command implementation: $removedCommandImplementation"
        }
    }
    if ($runtimeText -match 'sessions\.update\([^\r\n]+\{\s*["'']init_wizard["'']') {
        throw 'Bridge runtime must not persist the /init catalog snapshot in sessions.json.'
    }
    $larkText = Get-Content -LiteralPath (Join-Path $skillRoot 'scripts\bridge_core\lark.py') -Raw -Encoding utf8
    foreach ($richReplyMarker in @('_markdown_post_content', '"--msg-type", "post", "--content"', 'one ``md`` node')) {
        if ($larkText -notmatch [regex]::Escape($richReplyMarker)) {
            throw "Feishu adapter is missing rich-reply compatibility marker: $richReplyMarker"
        }
    }
    foreach ($exactReplyMarker in @(
        'class ReplyPlan',
        'frozen_text_piece',
        'kind == "post"',
        'Split a reply into lossless substrings',
        '_npm_shim_node_command',
        'Build an argv vector without routing user text through a command shell',
        'SAFE_ERROR_CODE_PATTERN',
        '_safe_error_code',
        'invalid answer-free error code',
        'missing_scopes_count',
        'category=cli_failure'
    )) {
        if ($larkText -notmatch [regex]::Escape($exactReplyMarker)) {
            throw "Feishu adapter is missing exact-reply marker: $exactReplyMarker"
        }
    }
    foreach ($forbiddenReplyMarker in @(
        '"/d", "/c"',
        'result.stderr[-800:]',
        'logger.info("event-consumer %s", text)',
        'error.get("message")',
        'error.get("hint")'
    )) {
        if ($larkText -match [regex]::Escape($forbiddenReplyMarker)) {
            throw "Feishu adapter retains a shell/log answer exposure marker: $forbiddenReplyMarker"
        }
    }
    $configText = Get-Content -LiteralPath (Join-Path $skillRoot 'scripts\bridge_core\config.py') -Raw -Encoding utf8
    if ($configText -notmatch [regex]::Escape('BRIDGE_VERSION = "4.2.0-alpha.63"')) {
        throw 'Bridge version marker is not 4.2.0-alpha.63.'
    }
    foreach ($accessDefaultMarker in @(
        '"CODEX_BRIDGE_ACCESS_MODE": ("locked"',
        '"CODEX_BRIDGE_REPLY_FORMAT": ("text"',
        'access_mode = _enum_env("CODEX_BRIDGE_ACCESS_MODE")',
        'semantic_issues = validate_bridge_env_values(os.environ)'
    )) {
        if ($configText -notmatch [regex]::Escape($accessDefaultMarker)) {
            throw "Bridge config is missing locked/fail-closed access default: $accessDefaultMarker"
        }
    }
    foreach ($dialMarker in @('CODEX_BRIDGE_BEEPER_DIAL_TTL', 'CODEX_BRIDGE_BEEPER_GRACE_MAX_SECONDS')) {
        if ($configText -notmatch [regex]::Escape($dialMarker)) {
            throw "Bridge config is missing on-demand dial marker: $dialMarker"
        }
        if ($installerText -notmatch [regex]::Escape($dialMarker)) {
            throw "Installer is missing on-demand dial default: $dialMarker"
        }
    }
    foreach ($forbiddenConfigMarker in @('CODEX_BRIDGE_OBSIDIAN_ROOT', 'obsidian_root', 'readable_roots')) {
        if ($configText -match [regex]::Escape($forbiddenConfigMarker)) {
            throw "Bridge config must not own knowledge roots: $forbiddenConfigMarker"
        }
    }
    $forbiddenBootstrapState = Join-Path $skillRoot '.codex-bootstrap'
    if (Test-Path -LiteralPath $forbiddenBootstrapState) {
        throw 'Publishable Skill root must not contain .codex-bootstrap runtime state.'
    }
    $skillText = Get-Content -LiteralPath (Join-Path $skillRoot 'skills\feishu-codex-bridge\SKILL.md') -Raw -Encoding utf8
    $usageText = Get-Content -LiteralPath (Join-Path $skillRoot 'feishu-codex-bridge-skill.md') -Raw -Encoding utf8
    $upgradeText = Get-Content -LiteralPath (Join-Path $skillRoot 'upgrade-bridge.md') -Raw -Encoding utf8
    $upgradeLineCount = @($upgradeText -split "\r?\n").Count
    if ($upgradeLineCount -gt 420) {
        throw "upgrade-bridge.md exceeded the compact entry-guide budget: $upgradeLineCount lines."
    }
    foreach ($upgradeRuleCluster in @(
        'R-AUTH',
        'R-PRODUCER',
        'R-BEEPER',
        'R-REPLAY',
        'R-FINAL',
        'R-READY',
        'R-DOC'
    )) {
        $rulePattern = [regex]::Escape("| $upgradeRuleCluster |")
        if ([regex]::Matches($upgradeText, $rulePattern).Count -ne 1) {
            throw "upgrade-bridge.md must define the rule cluster exactly once: $upgradeRuleCluster"
        }
    }
    foreach ($upgradeEvidenceTerm in @(
        'P0 Responder 目标',
        'Gate A',
        'Gate B',
        'Soak',
        'readiness'
    )) {
        if ($upgradeText -notmatch [regex]::Escape($upgradeEvidenceTerm)) {
            throw "upgrade-bridge.md is missing its normalized evidence term: $upgradeEvidenceTerm"
        }
    }
    $appServerContractText = Get-Content -LiteralPath (Join-Path $skillRoot 'scripts\app_server_contract.py') -Raw -Encoding utf8
    $appServerHostText = Get-Content -LiteralPath (Join-Path $skillRoot 'scripts\app_server_host.py') -Raw -Encoding utf8
    $appServerMvpText = Get-Content -LiteralPath (Join-Path $skillRoot 'scripts\app_server_mvp.py') -Raw -Encoding utf8
    $appServerBeeperReferenceText = Get-Content -LiteralPath (Join-Path $skillRoot 'references\app-server-probe.md') -Raw -Encoding utf8
    $beeperRunOnceContractText = Get-Content -LiteralPath (Join-Path $skillRoot 'scripts\beeper_run_once_contract.py') -Raw -Encoding utf8
    $beeperRunOnceReferenceText = Get-Content -LiteralPath (Join-Path $skillRoot 'references\beeper-run-once-candidate.md') -Raw -Encoding utf8
    $beeperCandidateSchemaText = Get-Content -LiteralPath (Join-Path $skillRoot 'assets\desktop-beeper-run-once-candidate.schema.json') -Raw -Encoding utf8
    $beeperRuntimeAttestationSchemaText = Get-Content -LiteralPath (Join-Path $skillRoot 'assets\desktop-beeper-run-once-runtime-attestation.schema.json') -Raw -Encoding utf8
    $sourceRouteContractText = Get-Content -LiteralPath (Join-Path $skillRoot 'scripts\source_route_contract.py') -Raw -Encoding utf8
    $releaseAuditSourceText = Get-Content -LiteralPath $releaseAuditPath -Raw -Encoding utf8
    try {
        $beeperCandidateSchema = $beeperCandidateSchemaText | ConvertFrom-Json -ErrorAction Stop
    } catch {
        throw 'Beeper run-once candidate schema is not valid JSON.'
    }
    try {
        $beeperRuntimeAttestationSchema = $beeperRuntimeAttestationSchemaText | ConvertFrom-Json -ErrorAction Stop
    } catch {
        throw 'Beeper runtime attestation schema is not valid JSON.'
    }
    if ($skillText -notmatch '(?ms)^---\s*\r?\nname:\s*feishu-codex-bridge\s*\r?\ndescription:\s*.+?\r?\n---') {
        throw 'SKILL.md frontmatter is missing required name/description fields.'
    }
    foreach ($beeperMarker in @(
        '4.2.0-alpha.63',
        '[feishu-codex-bridge-skill.md](../../feishu-codex-bridge-skill.md)',
        '[upgrade-bridge.md](../../upgrade-bridge.md)',
        '本地 `HANDOFF.md`',
        'EXPERIMENT-LOG.md',
        '`beeper`',
        '恰好一个非历史',
        'Responder-owned final',
        '`final_callback_source=final_callback`',
        'Beeper 不得提交',
        'may_have_started=true'
    )) {
        if ($skillText -notmatch [regex]::Escape($beeperMarker)) {
            throw "SKILL.md is missing minimal beeper/safety marker: $beeperMarker"
        }
    }
    foreach ($beeperTombstoneUsageMarker in @(
        '## 6. Beeper、`/init` 与恢复',
        '当前版本使用一个与历史路线隔离的本地 producer',
        '启用前的终态消息不会被接管或补发',
        '退休 producer lifecycle/surface 不是安装或恢复步骤',
        'Beeper 必须是新建任务',
        '理想产品',
        'temporary binding、shell、UI、数据库和 rollout',
        '未知结果不重放'
    )) {
        if ($usageText -notmatch [regex]::Escape($beeperTombstoneUsageMarker)) {
            throw "feishu-codex-bridge-skill.md is missing historical Beeper tombstone marker: $beeperTombstoneUsageMarker"
        }
    }
    foreach ($executableBeeperUsagePattern in @(
        '## 7\. Beeper 初次挂载',
        '初次挂载流程：',
        '仅在没有 registration 和 product run-once candidate 时创建',
        '候选首轮必须直接调用顶层',
        '以 `INITIAL_MOUNT` \+ `REGISTER_NEW` 发送完整合同',
        '后续 live canary 必须'
    )) {
        if ($usageText -match $executableBeeperUsagePattern) {
            throw "feishu-codex-bridge-skill.md retains executable historical Beeper guidance: $executableBeeperUsagePattern"
        }
    }
    foreach ($beeperTombstoneUpgradeMarker in @(
        '当前只开放与历史路线隔离的 `beeper`',
        '启用前的终态消息不补发，退休 producer surface 永久不可执行',
        '它不是产品级 `run_once`',
        '本地入口隔离，退休 surface 永久不可执行',
        'production exactly-once readiness 仍保持 blocked',
        '不确定结果永不重放',
        '相同字节、相同输入和相同失败原因不重复运行同一 gate'
    )) {
        if ($upgradeText -notmatch [regex]::Escape($beeperTombstoneUpgradeMarker)) {
            throw "upgrade-bridge.md is missing historical Beeper canary tombstone marker: $beeperTombstoneUpgradeMarker"
        }
    }
    foreach ($bootstrapMarker in @(
        '## 2. 只读预检',
        'lark-cli config init --new',
        'auth login --recommend',
        'WindowsApps',
        'auth status --json --verify',
        'user OAuth',
        'Bot credential',
        'Bot tenant scopes'
    )) {
        if ($usageText -notmatch [regex]::Escape($bootstrapMarker)) {
            throw "feishu-codex-bridge-skill.md is missing first-use dependency bootstrap marker: $bootstrapMarker"
        }
    }
    foreach ($automaticExecutionMarker in @(
        '用户要求安装、升级或配置本项目后',
        '不要求逐步回复',
        '精确路径、版本、进程身份和',
        'read-only postcondition check',
        '自动执行只包括本文定义的受限本地 producer',
        '未请求的发布'
    )) {
        if ($usageText -notmatch [regex]::Escape($automaticExecutionMarker)) {
            throw "feishu-codex-bridge-skill.md is missing automatic-execution marker: $automaticExecutionMarker"
        }
    }
    foreach ($p0PriorityMarker in @(
        'authoritative final',
        'Responder-owned Final Callback',
        'request/fence/Beeper/responder/prompt/capability',
        '`final_callback_source=final_callback`',
        'Beeper 不得提交',
        'native field、readback、UI、DB、OCR、clipboard 都不是 fallback',
        '不确定结果永不重放',
        '原始字符串',
        'outbound piece plan'
    )) {
        if ($upgradeText -notmatch [regex]::Escape($p0PriorityMarker)) {
            throw "upgrade-bridge.md is missing P0 reply-return development marker: $p0PriorityMarker"
        }
    }
    foreach ($protocolSchemaMarker in @(
        'Latest-first 兼容策略',
        '当前独立官方 CLI',
        'App Server Schema',
        '重新生成当前 CLI 对应的 App Server Schema',
        'capability/shape',
        '旧 Schema 不复用',
        '不授权启动 App Server',
        'fail closed',
        'fallback'
    )) {
        if ($upgradeText -notmatch [regex]::Escape($protocolSchemaMarker)) {
            throw "upgrade-bridge.md is missing version-matched App Server Schema marker: $protocolSchemaMarker"
        }
    }
    foreach ($beeperRunOnceContractMarker in @(
        'CANDIDATE_KIND = "single_beeper_run_once"',
        'CANDIDATE_SCHEMA_VERSION = 4',
        'PRODUCT_CONTRACT_SCHEMA_VERSION = 4',
        'RUNTIME_ATTESTATION_SCHEMA_VERSION = 3',
        'SURFACE_FINGERPRINT_RECIPE_ID = "beeper-surface-sha256-v1"',
        'CANDIDATE_MARKER_NAMESPACE = "feishu-codex-bridge.beeper-run-once.v1"',
        'RESERVED_HISTORICAL_MARKER_PREFIX',
        'BEEPER_EXPECTED',
        'TASK_COORDINATION_POLICY_EXPECTED',
        'TASK_COORDINATION_POLICY_CANONICAL_SHA256',
        '"product_enforced_max_model_turns": 1',
        '"max_executions_per_candidate": 1',
        '"single_use_dispatch_grant_required": True',
        '"budget_consumed_atomically_before_dispatch_required": True',
        '"distinct_key_second_dispatch_rejected_required": True',
        '"budget_non_resettable_required": True',
        '"budget_survives_restart_and_failover_required": True',
        '"rearm_or_update_allowed": False',
        '"helper_admission_is_hard_cap": False',
        '"old_surface_reactivation_allowed": False',
        '"product_contract_provenance_required": True',
        '"surface_fingerprint_bindings_required": True',
        '"runtime_attestation_receipt_schema_required": True',
        '"bounded_post_run_quiet_window_required": True',
        '"beeper_cardinality_required": 1',
        '"historical_beeper_reuse_forbidden": True',
        '"beeper_responder_contact_only_required": True',
        '"beeper_scope_binding_forbidden": True',
        '"beeper_as_responder_forbidden": True',
        '"beeper_self_contact_forbidden": True',
        '"desktop_responder_ownership_preserved_required": True',
        '"alternate_responder_client_forbidden": True',
        'product_contract_integrity_bound',
        'surface_fingerprint_integrity_bound',
        'candidate_marker_namespace_isolated',
        'runtime_attestation_receipt_schema_valid',
        'runtime_attestation_observed',
        'runtime_attestation_passed',
        'single_beeper_declared',
        'beeper_role_declared',
        'desktop_responder_ownership_preserved_declared',
        '_canonical_sha256',
        '_surface_fingerprint_payload',
        '_reject_json_constant',
        '"scheduler_enforced_max_model_turns"',
        '"max_executions_per_candidate"',
        '"cap_enforced_before_dispatch"',
        '"single_use_dispatch_grant"',
        '"budget_consumed_atomically_before_dispatch"',
        '"second_distinct_key_rejected_before_dispatch"',
        '"budget_non_resettable"',
        '"budget_survives_restart_and_failover"',
        '"rearm_or_update_allowed"',
        '"responder_thread_id_required"',
        '"new_thread_fallback_forbidden"',
        '"receipt_turn_cardinality"',
        '"all_terminal_states_consume_budget"',
        '"all_terminal_states_next_run_null"',
        '"queued_runs_suppressed"',
        '"overlapping_runs_suppressed"',
        '"retry_runs_suppressed"',
        '"idempotency_key_required"',
        '"duplicate_key_returns_same_execution"',
        '"immutable_execution_id"',
        '"immutable_surface_fingerprint"',
        '"immutable_run_receipt"',
        '"run_to_turn_mapping"',
        '"post_run_next_run_null"',
        'recurrence_count_not_hard_cap',
        'materially_different_surface_declaration_missing',
        '"policy_admissible_for_runtime_attestation": static_pass',
        '"product_contract_provenance_verified": False',
        '"surface_materially_different_certified": False',
        '"scheduler_cap_enforced_certified": False',
        '"task_tool_surface_certified": False',
        '"runtime_attestation_required": True',
        '"activation_allowed": False',
        '_reject_duplicate_json_members',
        'object_pairs_hook=_reject_duplicate_json_members',
        'ensure_ascii=True'
    )) {
        if ($beeperRunOnceContractText -notmatch [regex]::Escape($beeperRunOnceContractMarker)) {
            throw "Beeper run-once contract auditor is missing marker: $beeperRunOnceContractMarker"
        }
    }
    foreach ($forbiddenBeeperRunOnceContractMarker in @(
        'subprocess',
        'Popen',
        'Start-Process',
        'automation_update',
        'mcp__codex_app',
        'codex queue',
        'app-server',
        'beeper_queue_cli',
        'lark-cli',
        'urllib',
        'requests.',
        'socket',
        'http.client',
        'shell=True',
        'os.system',
        'startfile'
    )) {
        if ($beeperRunOnceContractText -match [regex]::Escape($forbiddenBeeperRunOnceContractMarker)) {
            throw "Beeper run-once contract auditor retains a launch/network/mutation marker: $forbiddenBeeperRunOnceContractMarker"
        }
    }
    foreach ($beeperRunOnceReferenceMarker in @(
        '# Product run-once contract',
        'Current capability verdict',
        '`single_beeper_run_once`',
        'one durable,',
        'one Beeper model turn',
        'Duplicate keys coalesce',
        'Immutable receipt',
        '`assets/desktop-beeper-run-once-candidate.schema.json`',
        '`assets/desktop-beeper-run-once-runtime-attestation.schema.json`',
        '`scripts/beeper_run_once_contract.py`',
        'source-only validation always leaves `activation_allowed=false`',
        'runtime attestation',
        'When Codex Desktop updates',
        'Do not add version-number conditionals'
    )) {
        if ($beeperRunOnceReferenceText -notmatch [regex]::Escape($beeperRunOnceReferenceMarker)) {
            throw "Beeper run-once reference is missing marker: $beeperRunOnceReferenceMarker"
        }
    }
    foreach ($sourceRouteMarker in @(
        'PLUGIN_NAME = "feishu-codex-bridge"',
        'RELEASE_NAME = "feishu-codex-bridge-plugin"',
        'canonical-development',
        'installed-snapshot',
        'legacy-or-copy',
        'knowledge_content_authoritative',
        'development_source_eligible',
        '_assert_existing_no_reparse',
        'codex_home_value',
        '_reject_duplicate_json_members',
        'ensure_ascii=True'
    )) {
        if ($sourceRouteContractText -notmatch [regex]::Escape($sourceRouteMarker)) {
            throw "Source-route contract is missing marker: $sourceRouteMarker"
        }
    }
    foreach ($forbiddenSourceRouteMarker in @(
        'subprocess',
        'Popen',
        'Start-Process',
        'automation_update',
        'mcp__codex_app',
        'lark-cli',
        'requests.',
        'socket',
        'shell=True',
        'os.system',
        'startfile'
    )) {
        if ($sourceRouteContractText -match [regex]::Escape($forbiddenSourceRouteMarker)) {
            throw "Source-route contract retains a launch/network/mutation marker: $forbiddenSourceRouteMarker"
        }
    }
    foreach ($releaseSourceRouteMarker in @(
        "[ValidateSet('canonical-development', 'installed-snapshot')]",
        'Release audit source role must use the exact supported casing.',
        'ConvertFrom-UniqueJsonBytes',
        '[System.StringComparer]::OrdinalIgnoreCase',
        '^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$',
        'Assert-ReleaseSourceRoute',
        'Repository marketplace has no unique canonical Feishu Bridge source route.',
        'Repository marketplace plugin collection is not a JSON array.',
        'Configured CODEX_HOME is not an exact fully qualified local path.',
        'Installed snapshot role is valid only for the exact versioned Codex plugin cache root.'
    )) {
        if ($releaseAuditSourceText -notmatch [regex]::Escape($releaseSourceRouteMarker)) {
            throw "Release audit is missing source-route marker: $releaseSourceRouteMarker"
        }
    }
    $beeperCandidateExpected = [ordered]@{
        schema_version = 4
        candidate_kind = 'single_beeper_run_once'
        responder_mode = 'existing_thread'
        recurrence_allowed = $false
        active_status_required = $false
        product_enforced_max_model_turns = 1
        max_executions_per_candidate = 1
        single_use_dispatch_grant_required = $true
        budget_consumed_atomically_before_dispatch_required = $true
        distinct_key_second_dispatch_rejected_required = $true
        budget_non_resettable_required = $true
        budget_survives_restart_and_failover_required = $true
        rearm_or_update_allowed = $false
        responder_thread_id_required = $true
        new_thread_fallback_forbidden = $true
        idempotency_key_required = $true
        immutable_execution_id_required = $true
        immutable_surface_fingerprint_required = $true
        duplicate_key_returns_same_execution = $true
        run_to_turn_mapping_required = $true
        receipt_turn_cardinality_required = 1
        terminal_completed_state_required = $true
        all_terminal_states_consume_budget_required = $true
        all_terminal_states_next_run_must_be_null = $true
        post_run_next_run_must_be_null = $true
        helper_admission_is_hard_cap = $false
        old_surface_reactivation_allowed = $false
        product_contract_provenance_required = $true
        surface_fingerprint_recipe_id = 'beeper-surface-sha256-v1'
        surface_fingerprint_bindings_required = $true
        candidate_terminal_marker_namespace = 'feishu-codex-bridge.beeper-run-once.v1'
        historical_terminal_marker_namespace_reuse_forbidden = $true
        runtime_attestation_receipt_schema_required = $true
        bounded_post_run_quiet_window_required = $true
        beeper_scope = 'bridge_installation'
        beeper_cardinality_required = 1
        exact_beeper_identity_required = $true
        beeper_identity_immutable_required = $true
        historical_beeper_reuse_forbidden = $true
        beeper_responder_contact_only_required = $true
        beeper_scope_binding_forbidden = $true
        beeper_as_responder_forbidden = $true
        beeper_self_contact_forbidden = $true
        desktop_responder_ownership_preserved_required = $true
        alternate_responder_client_forbidden = $true
        operation_scoped_task_coordination_policy_required = $true
    }
    $expectedBeeperSchemaTopKeys = @('$schema', 'additionalProperties', 'properties', 'required', 'title', 'type') | Sort-Object
    $actualBeeperSchemaTopKeys = @($beeperCandidateSchema.PSObject.Properties.Name | Sort-Object)
    if (@(Compare-Object -ReferenceObject $expectedBeeperSchemaTopKeys -DifferenceObject $actualBeeperSchemaTopKeys).Count -ne 0) {
        throw 'Beeper run-once candidate schema top-level keys changed.'
    }
    if ([string]$beeperCandidateSchema.PSObject.Properties['$schema'].Value -ne 'https://json-schema.org/draft/2020-12/schema') {
        throw 'Beeper run-once candidate schema must use JSON Schema draft 2020-12.'
    }
    if ([string]$beeperCandidateSchema.type -ne 'object' -or $beeperCandidateSchema.additionalProperties -ne $false) {
        throw 'Beeper run-once candidate schema must be a closed object.'
    }
    if ([string]$beeperCandidateSchema.title -ne 'Feishu Desktop single Beeper run-once candidate') {
        throw 'Beeper run-once candidate schema title changed.'
    }
    $expectedBeeperCandidateKeys = @($beeperCandidateExpected.Keys | Sort-Object)
    $requiredBeeperCandidateKeys = @($beeperCandidateSchema.required | ForEach-Object { [string]$_ } | Sort-Object)
    $propertyBeeperCandidateKeys = @($beeperCandidateSchema.properties.PSObject.Properties.Name | Sort-Object)
    if ($requiredBeeperCandidateKeys.Count -ne $expectedBeeperCandidateKeys.Count -or @(Compare-Object -ReferenceObject $expectedBeeperCandidateKeys -DifferenceObject $requiredBeeperCandidateKeys).Count -ne 0) {
        throw 'Beeper run-once candidate schema required fields changed.'
    }
    if (@(Compare-Object -ReferenceObject $expectedBeeperCandidateKeys -DifferenceObject $propertyBeeperCandidateKeys).Count -ne 0) {
        throw 'Beeper run-once candidate schema property fields changed.'
    }
    foreach ($beeperCandidateField in $expectedBeeperCandidateKeys) {
        $candidateDefinition = $beeperCandidateSchema.properties.PSObject.Properties[$beeperCandidateField]
        if ($null -eq $candidateDefinition -or $candidateDefinition.Value.PSObject.Properties.Count -ne 1 -or $null -eq $candidateDefinition.Value.PSObject.Properties['const']) {
            throw "Beeper run-once candidate schema field lacks const: $beeperCandidateField"
        }
        $actualCandidateConst = ConvertTo-Json -InputObject $candidateDefinition.Value.PSObject.Properties['const'].Value -Compress
        $expectedCandidateConst = ConvertTo-Json -InputObject $beeperCandidateExpected[$beeperCandidateField] -Compress
        if ($actualCandidateConst -cne $expectedCandidateConst) {
            throw "Beeper run-once candidate schema const changed: $beeperCandidateField"
        }
    }
    $expectedRuntimeAttestationTopKeys = @('$schema', 'additionalProperties', 'allOf', 'properties', 'required', 'title', 'type') | Sort-Object
    $actualRuntimeAttestationTopKeys = @($beeperRuntimeAttestationSchema.PSObject.Properties.Name | Sort-Object)
    if (@(Compare-Object -ReferenceObject $expectedRuntimeAttestationTopKeys -DifferenceObject $actualRuntimeAttestationTopKeys).Count -ne 0) {
        throw 'Beeper runtime attestation schema top-level keys changed.'
    }
    if ([string]$beeperRuntimeAttestationSchema.PSObject.Properties['$schema'].Value -cne 'https://json-schema.org/draft/2020-12/schema' -or
        [string]$beeperRuntimeAttestationSchema.title -cne 'Feishu Desktop single Beeper runtime attestation receipt' -or
        [string]$beeperRuntimeAttestationSchema.type -cne 'object' -or
        $beeperRuntimeAttestationSchema.additionalProperties -ne $false) {
        throw 'Beeper runtime attestation schema identity or closed-object contract changed.'
    }
    $expectedRuntimeAttestationFields = @(
        'schema_version', 'attestation_kind', 'status', 'candidate_kind', 'marker_namespace',
        'product_contract_canonical_sha256', 'candidate_schema_canonical_sha256',
        'runtime_attestation_schema_canonical_sha256', 'surface_fingerprint_sha256',
        'runtime_build_fingerprint_sha256', 'execution_receipt_sha256', 'receipt_immutable',
        'single_use_grant_consumed_before_dispatch', 'execution_count', 'beeper_turn_count',
        'run_to_turn_receipt_cardinality', 'same_key_same_execution',
        'distinct_key_rejected_before_dispatch', 'queued_second_dispatch_count',
        'overlap_second_dispatch_count', 'retry_second_dispatch_count', 'terminal_budget_consumed',
        'next_run_at_is_null', 'rearm_allowed', 'quiet_window_seconds',
        'quiet_window_new_execution_count', 'quiet_window_new_turn_count',
        'active_beeper_count',
        'beeper_identity_bound', 'beeper_identity_stable', 'historical_beeper_reuse_detected',
        'beeper_scope_binding_count', 'beeper_responder_collision_count',
        'beeper_self_contact_count', 'non_task_coordination_call_count',
        'beeper_business_execution_count', 'alternate_responder_client_count',
        'desktop_responder_ownership_preserved', 'task_coordination_policy_canonical_sha256',
        'activation_allowed'
    ) | Sort-Object
    $requiredRuntimeAttestationFields = @($beeperRuntimeAttestationSchema.required | ForEach-Object { [string]$_ } | Sort-Object)
    $propertyRuntimeAttestationFields = @($beeperRuntimeAttestationSchema.properties.PSObject.Properties.Name | Sort-Object)
    if (@(Compare-Object -ReferenceObject $expectedRuntimeAttestationFields -DifferenceObject $requiredRuntimeAttestationFields).Count -ne 0 -or
        @(Compare-Object -ReferenceObject $expectedRuntimeAttestationFields -DifferenceObject $propertyRuntimeAttestationFields).Count -ne 0) {
        throw 'Beeper runtime attestation schema fields changed.'
    }
    if ($beeperRuntimeAttestationSchema.properties.activation_allowed.const -ne $false -or
        [string]$beeperRuntimeAttestationSchema.properties.marker_namespace.const -cne 'feishu-codex-bridge.beeper-run-once.v1' -or
        @($beeperRuntimeAttestationSchema.allOf).Count -ne 1) {
        throw 'Beeper runtime attestation schema activation or namespace guard changed.'
    }
    $runtimePassProperties = $beeperRuntimeAttestationSchema.allOf[0].then.properties
    $expectedRuntimePass = [ordered]@{
        receipt_immutable = $true
        single_use_grant_consumed_before_dispatch = $true
        execution_count = 1
        beeper_turn_count = 1
        run_to_turn_receipt_cardinality = 1
        same_key_same_execution = $true
        distinct_key_rejected_before_dispatch = $true
        queued_second_dispatch_count = 0
        overlap_second_dispatch_count = 0
        retry_second_dispatch_count = 0
        terminal_budget_consumed = $true
        next_run_at_is_null = $true
        rearm_allowed = $false
        quiet_window_new_execution_count = 0
        quiet_window_new_turn_count = 0
        active_beeper_count = 1
        beeper_identity_bound = $true
        beeper_identity_stable = $true
        historical_beeper_reuse_detected = $false
        beeper_scope_binding_count = 0
        beeper_responder_collision_count = 0
        beeper_self_contact_count = 0
        non_task_coordination_call_count = 0
        beeper_business_execution_count = 0
        alternate_responder_client_count = 0
        desktop_responder_ownership_preserved = $true
        task_coordination_policy_canonical_sha256 = '046ba6d2902a190c41ee2da8344bd052d22d7b159454aa476a25bab632a60bd5'
    }
    $actualRuntimePassKeys = @($runtimePassProperties.PSObject.Properties.Name | Sort-Object)
    if (@(Compare-Object -ReferenceObject @($expectedRuntimePass.Keys | Sort-Object) -DifferenceObject $actualRuntimePassKeys).Count -ne 0) {
        throw 'Beeper runtime attestation pass assertions changed.'
    }
    foreach ($runtimePassField in $expectedRuntimePass.Keys) {
        $actualRuntimePassConst = ConvertTo-Json -InputObject $runtimePassProperties.PSObject.Properties[$runtimePassField].Value.const -Compress
        $expectedRuntimePassConst = ConvertTo-Json -InputObject $expectedRuntimePass[$runtimePassField] -Compress
        if ($actualRuntimePassConst -cne $expectedRuntimePassConst) {
            throw "Beeper runtime attestation pass assertion changed: $runtimePassField"
        }
    }
    foreach ($appServerContractMarker in @(
        'mcpServer/tool/call',
        'thread/compact/start',
        'CODEX_APP_TOOLS_PIPE_PATH',
        'desktop_task_coordination_certified',
        'runtime_attestation_required',
        'activation_allowed',
        'send_message_requires_prompt',
        'read_only_mvp_protocol_available',
        'ephemeral_thread_start_shape_available',
        'ephemeral_thread_path_nullable',
        'mcp_status_shape_available',
        'mcp_tool_response_shape_available',
        'jsonl_rpc_envelopes_available'
    )) {
        if ($appServerContractText -notmatch [regex]::Escape($appServerContractMarker)) {
            throw "App Server static contract auditor is missing marker: $appServerContractMarker"
        }
    }
    foreach ($appServerMvpMarker in @(
        'ALLOWED_REQUEST_METHODS',
        'READ_ONLY_TOOLS',
        'REQUEST_TIMEOUT_SECONDS',
        'initialize',
        'initialized',
        'thread/start',
        'mcpServerStatus/list',
        'mcpServer/tool/call',
        'list_threads',
        'list_projects',
        'control_thread_ephemeral',
        'thread.get("path") is not None',
        'model_turn_started',
        'responder_mutation_attempted',
        'queue_claimed',
        'activation_allowed',
        'server_request_unsupported'
    )) {
        if ($appServerMvpText -notmatch [regex]::Escape($appServerMvpMarker)) {
            throw "App Server read-only MVP is missing marker: $appServerMvpMarker"
        }
    }
    foreach ($forbiddenAppServerMvpMarker in @(
        'subprocess',
        'Popen',
        'codex app-server',
        'CODEX_APP_TOOLS_PIPE_PATH',
        'thread/resume',
        'turn/start',
        'thread/compact/start',
        'send_message_to_thread',
        'beeper_queue_cli'
    )) {
        if ($appServerMvpText -match [regex]::Escape($forbiddenAppServerMvpMarker)) {
            throw "App Server read-only MVP must not contain live or responder mutation surface: $forbiddenAppServerMvpMarker"
        }
    }
    foreach ($forbiddenAppServerContractMarker in @(
        'subprocess',
        'Popen',
        'Start-Process',
        'codex app-server'
    )) {
        if ($appServerContractText -match [regex]::Escape($forbiddenAppServerContractMarker)) {
            throw "App Server static contract auditor must not launch a process: $forbiddenAppServerContractMarker"
        }
    }
    foreach ($appServerHostMarker in @(
        'app-server-live-read-only-probe',
        'expected_codex_sha256',
        'audit_contract',
        'CREATE_SUSPENDED',
        'JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE',
        'NtResumeProcess',
        '[str(executable), "app-server", "--listen", "stdio://"]',
        'env=None',
        'stderr=subprocess.DEVNULL',
        'MAX_SESSION_FRAMES',
        'MAX_SESSION_BYTES',
        'hard_timeout_enforced',
        'owned_job_assigned_before_resume',
        'read_only_desktop_task_coordination_attested',
        'desktop_task_coordination_certified',
        'activation_allowed'
    )) {
        if ($appServerHostText -notmatch [regex]::Escape($appServerHostMarker)) {
            throw "App Server bounded live host is missing marker: $appServerHostMarker"
        }
    }
    foreach ($forbiddenAppServerHostMarker in @(
        'CODEX_APP_TOOLS_PIPE_PATH',
        'os.environ',
        'shell=True',
        'thread/resume',
        'turn/start',
        'thread/compact/start',
        'send_message_to_thread',
        'beeper_queue_cli',
        'bridge_core',
        'stderr=subprocess.PIPE'
    )) {
        if ($appServerHostText -match [regex]::Escape($forbiddenAppServerHostMarker)) {
            throw "App Server bounded live host retains forbidden surface: $forbiddenAppServerHostMarker"
        }
    }
    foreach ($appServerReferenceMarker in @(
        'activation_allowed',
        'desktop_task_port_unavailable',
        'mcpServer/tool/call',
        'thread/compact/start',
        'Raw `/compact`',
        'list_threads(limit<=50)',
        'send_message_to_thread',
        'prompt approval',
        'protocol core',
        'ephemeral=true',
        'queue_claimed=false',
        'responder_mutation_attempted=false',
        'scripts/app_server_host.py',
        'one-attempt',
        'runtime_provenance_unavailable',
        'static_inputs_bound=true',
        'runtime_source_bound=false',
        'desktop_task_coordination_certified` and'
    )) {
        if (($appServerBeeperReferenceText + "`n" + $upgradeText) -notmatch [regex]::Escape($appServerReferenceMarker)) {
            throw "App Server beeper guidance is missing marker: $appServerReferenceMarker"
        }
    }
    foreach ($experimentMethodMarker in @(
        '### 停止死循环规则',
        'EXPERIMENT-LOG.md',
        '动作可能已开始时',
        'local-only',
        '相同字节、相同输入和相同失败原因不重复运行同一 gate',
        '同一 hypothesis 最多一次受审实验',
        '测试失败必须先修源码或测试合同',
        '产品能力缺失',
        'source、test-contract、environment 或',
        'product-capability。日常开发不运行 P3'
    )) {
        if ($upgradeText -notmatch [regex]::Escape($experimentMethodMarker)) {
            throw "upgrade-bridge.md is missing experiment-method marker: $experimentMethodMarker"
        }
    }
    foreach ($diagnosticContractMarker in @(
        'bridge status -ProjectRoot <project-root> -Json',
        'bridge doctor -ProjectRoot <project-root> -Json',
        'bridge validate -ProjectRoot <project-root> -Json',
        'bridge readiness -ProjectRoot <project-root> -Json',
        '只输出一个 compact answer-free object',
        'mvp',
        'exactly-once',
        'task IDs'
    )) {
        if ($usageText -notmatch [regex]::Escape($diagnosticContractMarker)) {
            throw "feishu-codex-bridge-skill.md is missing machine-readable diagnostic marker: $diagnosticContractMarker"
        }
    }
    $hookPermissionText = Get-Content -LiteralPath (Join-Path $skillRoot 'references\permissions-and-hooks.md') -Raw -Encoding utf8
    foreach ($hookPermissionMarker in @(
        'independently runnable official Codex',
        'npm install -g @openai/codex',
        'must not run',
        'WindowsApps ACLs',
        'no retired producer prefix is allowed or matched'
    )) {
        if ($hookPermissionText -notmatch [regex]::Escape($hookPermissionMarker)) {
            throw "Permissions/hooks reference is missing independent Codex CLI marker: $hookPermissionMarker"
        }
    }
    foreach ($lifecycleMarker in @(
        'first `bridge install` is the one disclosed indivisible bootstrap',
        'bridge hooks',
        'bridge access -AccessMode locked',
        'invalidates the old manifest without signing a new one',
        'no further native-field attempt is permitted',
        'surface has six closed operations:',
        'Beeper-only `claim_and_arm`',
        'Beeper-only `claim_readonly`',
        'Beeper-only `complete_readonly`',
        'Responder-only `submit_final_callback`',
        'Beeper-only `finish_final_callback`',
        'ordinary MCP provides no product-attested caller or responder-turn identity',
        'Completion accepts only `final_callback_source=final_callback`',
        'the plugin contains no `hooks/hooks.json`',
        'Bridge `SessionStart` and `SessionEnd`.'
    )) {
        if ($hookPermissionText -notmatch [regex]::Escape($lifecycleMarker)) {
            throw "Permissions/hooks reference is missing lifecycle/canary marker: $lifecycleMarker"
        }
    }
    $beeperTaskText = Get-Content -LiteralPath (Join-Path $skillRoot 'assets\beeper-task.md') -Raw -Encoding utf8
    foreach ($readonlyFinishOwnershipMarker in @(
        'No public `finish_readonly` tool exists;',
        'internal read-only finishing belongs only to the Bridge.',
        'returns `terminal=true` with status `completed` or `failed`',
        'not receive or verify final-source metadata',
        'the Bridge verifies'
    )) {
        if ($beeperTaskText -notmatch [regex]::Escape($readonlyFinishOwnershipMarker)) {
            throw "Beeper task contract is missing Bridge-owned read-only finish marker: $readonlyFinishOwnershipMarker"
        }
    }
    if ($beeperTaskText -match [regex]::Escape('reports the `final_callback_source=final_callback`')) {
        throw 'Beeper task contract asks the answer-free MCP surface for hidden provenance.'
    }
    foreach ($historicalProducerTombstoneMarker in @(
        'The only executable producer is the isolated `beeper`',
        'one bounded spawn',
        'Pre-enable terminal rows remain untouched',
        'Historical producer details are not an operational contract',
        'A future product-level pre-dispatch `run_once` must be materially different'
    )) {
        if ($hookPermissionText -notmatch [regex]::Escape($historicalProducerTombstoneMarker)) {
            throw "Permissions/hooks reference is missing historical producer tombstone marker: $historicalProducerTombstoneMarker"
        }
    }
    foreach ($frontendSkillMarker in @(
        'feishu-desktop-client.md',
        '飞书 Windows 客户端只有在用户明确要求时',
        'Trust all'
    )) {
        if ($usageText -notmatch [regex]::Escape($frontendSkillMarker)) {
            throw "feishu-codex-bridge-skill.md is missing conditional frontend-takeover marker: $frontendSkillMarker"
        }
    }
    $desktopReferenceText = Get-Content -LiteralPath (Join-Path $skillRoot 'references\feishu-desktop-client.md') -Raw -Encoding utf8
    foreach ($desktopReferenceMarker in @(
        'https://www.feishu.cn/download',
        'feishu desktop-install -DesktopInstallConsent',
        'main workspace',
        'cached files do not prove',
        'SetIsBorderRequired',
        'coordinate input geometry is unavailable',
        'observation-only',
        'must complete authentication'
    )) {
        if ($desktopReferenceText -notmatch [regex]::Escape($desktopReferenceMarker)) {
            throw "Feishu Desktop reference is missing marker: $desktopReferenceMarker"
        }
    }
    foreach ($permissionMarker in @(
        'openclaw-common-chat',
        'auth login --recommend',
        'Bot tenant scopes',
        'user OAuth',
        'Bot credential',
        '用户 OAuth 不代替 Bot scope'
    )) {
        if ($usageText -notmatch [regex]::Escape($permissionMarker)) {
            throw "feishu-codex-bridge-skill.md is missing common permission profile marker: $permissionMarker"
        }
    }
    $permissionProfileText = Get-Content -LiteralPath (Join-Path $skillRoot 'references\openclaw-common-chat-permissions.md') -Raw -Encoding utf8
    foreach ($permissionProfileMarker in @(
        'lark-cli config init --new',
        'lark-cli auth login --recommend --no-wait --json',
        'lark-cli auth login --device-code',
        'QR authorization is not all Bot chat permissions',
        'developer-console UI',
        '/open-apis/application/v6/scopes --as bot',
        'Retry-After',
        'Bot tenant scopes are distinct'
    )) {
        if ($permissionProfileText -notmatch [regex]::Escape($permissionProfileMarker)) {
            throw "Permission profile is missing marker: $permissionProfileMarker"
        }
    }
    foreach ($permissionProfileTombstoneMarker in @(
        'creation or activation',
        'Every retired producer surface remains',
        'permanently non-executable'
    )) {
        if ($permissionProfileText -notmatch [regex]::Escape($permissionProfileTombstoneMarker)) {
            throw "Permission profile is missing historical producer tombstone marker: $permissionProfileTombstoneMarker"
        }
    }
    $agentsFragment = Get-Content -LiteralPath (Join-Path $skillRoot 'assets\AGENTS.feishu-codex-bridge.md') -Raw -Encoding utf8
    foreach ($marker in @('<!-- FEISHU_CODEX_BRIDGE_RULES_START -->', '<!-- FEISHU_CODEX_BRIDGE_RULES_END -->')) {
        if ([regex]::Matches($agentsFragment, [regex]::Escape($marker)).Count -ne 1) {
            throw "Managed AGENTS.md fragment must contain exactly one marker: $marker"
        }
    }
    foreach ($codexCliMarker in @(
        'Detect capabilities latest-first',
        'independent official CLI',
        'Generate version-bound Schema',
        'never change WindowsApps ACLs'
    )) {
        if ($agentsFragment -notmatch [regex]::Escape($codexCliMarker)) {
            throw "Managed AGENTS.md fragment is missing independent Codex CLI marker: $codexCliMarker"
        }
    }
    foreach ($marker in @(
        'The build may use exactly one isolated producer namespace',
        '`producer_unavailable_no_retry`',
        'Every retired producer lifecycle and control surface is',
        'No recurring Codex scheduled automation,',
        'polling producer, or periodic Beeper dial is installed or required.',
        'Bridge-owned at-most-once attempt',
        'Each installed Bridge namespace has exactly one independent,',
        'Every selected Desktop responder remains sole owner',
        'alternate responder client or reply fallback',
        'An outcome with `may_have_started=true` is terminal',
        'Completion accepts only `final_callback_source=final_callback`',
        'The selected Desktop responder must call `submit_final_callback` once',
        'The Beeper must never call that',
        'The only content-bearing claim responses are',
        '`claim_readonly`, which may return only one strictly bounded catalog or exact',
        'The plugin contributes no `UserPromptSubmit` or `Stop` Hook',
        'Fresh or missing access configuration resolves to `locked`',
        'Treat `bridge.pid` as an untrusted reference',
        'Manual start/restart and every SessionStart require current source/runtime',
        'perform normal in-scope install',
        'Automatic execution never widens scope',
        '`bridge readiness -Json`',
        'Focused unit tests may run locally',
        'Do not add or keep an executable compatibility branch solely for an older',
        'Publish only files admitted by the release inventory'
    )) {
        if ($agentsFragment -notmatch [regex]::Escape($marker)) {
            throw "Managed AGENTS.md fragment is missing automatic-execution or Beeper safety marker: $marker"
        }
    }
    $interfaceText = Get-Content -LiteralPath (Join-Path $skillRoot 'skills\feishu-codex-bridge\agents\openai.yaml') -Raw -Encoding utf8
    foreach ($marker in @('display_name:', 'short_description:', 'default_prompt:')) {
        if ($interfaceText -notmatch [regex]::Escape($marker)) {
            throw "agents/openai.yaml is missing interface field: $marker"
        }
    }
    Write-Output 'Static bridge validation passed; no child process was started.'
}

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
    $localPrograms = Join-Path $env:LOCALAPPDATA 'Programs'
    if (Test-Path -LiteralPath $localPrograms -PathType Container) {
        $discoveredPaths += @(
            Get-ChildItem -LiteralPath $localPrograms -Directory -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -eq 'nodejs' -or $_.Name -like 'node-*-win-*' } |
                Sort-Object Name -Descending |
                ForEach-Object { $_.FullName }
        )
    }
    $npmUserBin = Join-Path $env:APPDATA 'npm'
    if (Test-Path -LiteralPath $npmUserBin -PathType Container) { $discoveredPaths += $npmUserBin }

    $seen = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    $segments = New-Object System.Collections.Generic.List[string]
    $pathSources = @(
        [Environment]::GetEnvironmentVariable('Path', 'Machine'),
        [Environment]::GetEnvironmentVariable('Path', 'User'),
        $env:Path
    ) + $discoveredPaths
    foreach ($rawPath in $pathSources) {
        foreach ($segment in @($rawPath -split ';')) {
            $trimmed = $segment.Trim().Trim('"')
            if ($trimmed -and $seen.Add($trimmed)) { $segments.Add($trimmed) }
        }
    }
    $env:Path = $segments -join ';'
}

function Get-ExecutablePreflightResult {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [string[]]$VersionArguments = @('--version')
    )
    $commands = @(Get-Command $Name -All -ErrorAction SilentlyContinue)
    if ($commands.Count -eq 0) {
        return [pscustomobject]@{ Available = $false; Detail = 'not found on PATH' }
    }

    $seenSources = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    $failures = @()
    foreach ($command in $commands) {
        $source = [string]$command.Source
        if (-not $source -or -not $seenSources.Add($source)) { continue }
        try {
            $global:LASTEXITCODE = 0
            $versionOutput = (& $source @VersionArguments 2>&1 | Out-String).Trim()
            if ($LASTEXITCODE -ne 0) {
                $failures += "exited with code $LASTEXITCODE"
                continue
            }
            if (-not $versionOutput) { $versionOutput = 'version command succeeded' }
            return [pscustomobject]@{
                Available = $true
                Detail = $versionOutput
                Source = $source
            }
        } catch {
            $detail = ($_.Exception.Message -replace '(?s)At [A-Za-z]:\\.*$', '').Trim()
            $failures += $detail
        }
    }

    $summary = if ($failures.Count -gt 0) { $failures[0] } else { 'no executable candidate succeeded' }
    return [pscustomobject]@{
        Available = $false
        Detail = "found but could not execute: $summary"
        Source = ''
    }
}

function Get-IndependentCodexCliPreflightResult {
    $commands = @()
    foreach ($name in @('codex.cmd', 'codex.exe', 'codex')) {
        $commands += @(Get-Command $name -All -ErrorAction SilentlyContinue)
    }

    $seenSources = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    $packagedSources = @()
    $unverifiedSources = @()
    foreach ($command in $commands) {
        $source = [string]$command.Source
        if (-not $source -or -not $seenSources.Add($source)) { continue }
        if ($source -match '(?i)\\Program Files\\WindowsApps\\OpenAI\.Codex_[^\\]+\\app\\resources\\codex(?:\.exe)?$') {
            $packagedSources += $source
            continue
        }

        $prefix = Split-Path -Parent $source
        $packageJson = Join-Path $prefix 'node_modules\@openai\codex\package.json'
        if (Test-Path -LiteralPath $packageJson -PathType Leaf) {
            try {
                $package = Get-Content -LiteralPath $packageJson -Raw | ConvertFrom-Json
                if ($package.name -eq '@openai/codex' -and [string]$package.version) {
                    return [pscustomobject]@{
                        Available = $true
                        Detail = "@openai/codex $($package.version) (independent npm CLI; not executed)"
                        Source = $source
                    }
                }
            } catch {
                $unverifiedSources += "$source (invalid package metadata)"
                continue
            }
        }
        $unverifiedSources += $source
    }

    if ($packagedSources.Count -gt 0 -and $unverifiedSources.Count -eq 0) {
        return [pscustomobject]@{
            Available = $false
            Detail = 'only the Codex Desktop WindowsApps package resource was found; independent CLI not installed'
            Source = ''
        }
    }
    if ($unverifiedSources.Count -gt 0) {
        return [pscustomobject]@{
            Available = $false
            Detail = "unverified Codex command candidate: $($unverifiedSources[0])"
            Source = ''
        }
    }
    return [pscustomobject]@{
        Available = $false
        Detail = 'independent Codex CLI not found; Desktop package resources are not used'
        Source = ''
    }
}

function Invoke-BridgePreflight {
    $failed = $false
    $missingPrerequisites = $false
    $pathDiscoveryFallback = $false
    $executableSources = @()
    Update-ProcessPathFromEnvironment

    if ($PSVersionTable.PSVersion -ge [version]'5.1') {
        Write-Output ("[PASS] PowerShell {0}" -f $PSVersionTable.PSVersion)
    } else {
        Write-Output ("[FAIL] PowerShell 5.1+ is required; found {0}" -f $PSVersionTable.PSVersion)
        $failed = $true
        $missingPrerequisites = $true
    }

    $python = Get-ExecutablePreflightResult 'python.exe'
    if (-not $python.Available) {
        $python = Get-ExecutablePreflightResult 'py.exe' @('-3', '--version')
    }
    $pythonVersion = $null
    if ($python.Available -and $python.Detail -match '(?i)Python\s+(\d+)\.(\d+)') {
        $pythonVersion = [version]("{0}.{1}" -f $Matches[1], $Matches[2])
    }
    if ($python.Available -and $pythonVersion -and $pythonVersion -ge [version]'3.10') {
        Write-Output ("[PASS] Python {0}" -f $python.Detail)
        $executableSources += $python.Source
    } else {
        Write-Output ("[FAIL] Python 3.10+ is unavailable: {0}" -f $python.Detail)
        $failed = $true
        $missingPrerequisites = $true
    }

    foreach ($name in @('node', 'npm', 'npx', 'lark-cli')) {
        $result = Get-ExecutablePreflightResult $name
        if ($result.Available) {
            Write-Output ("[PASS] {0}: {1}" -f $name, $result.Detail)
            $executableSources += $result.Source
        } else {
            Write-Output ("[FAIL] {0}: {1}" -f $name, $result.Detail)
            $failed = $true
            $missingPrerequisites = $true
        }
    }

    $codexCli = Get-IndependentCodexCliPreflightResult
    if ($codexCli.Available) {
        Write-Output ("[PASS] codex-cli: {0}" -f $codexCli.Detail)
        $executableSources += $codexCli.Source
    } else {
        Write-Output ("[FAIL] codex-cli: {0}" -f $codexCli.Detail)
        $failed = $true
        $missingPrerequisites = $true
    }

    $persistentPathSegments = @{}
    foreach ($rawPath in @(
        [Environment]::GetEnvironmentVariable('Path', 'Machine'),
        [Environment]::GetEnvironmentVariable('Path', 'User')
    )) {
        foreach ($segment in @($rawPath -split ';')) {
            $expanded = [Environment]::ExpandEnvironmentVariables($segment.Trim().Trim('"')).TrimEnd('\')
            if ($expanded) { $persistentPathSegments[$expanded.ToLowerInvariant()] = $true }
        }
    }
    $missingPersistentPaths = @()
    foreach ($source in @($executableSources | Where-Object { $_ } | Sort-Object -Unique)) {
        $directory = (Split-Path -Parent $source).TrimEnd('\')
        if ($directory -and -not $persistentPathSegments.ContainsKey($directory.ToLowerInvariant())) {
            $missingPersistentPaths += $directory
        }
    }
    $missingPersistentPaths = @($missingPersistentPaths | Sort-Object -Unique)
    if ($missingPersistentPaths.Count -gt 0) {
        Write-Output ("[WARN] Verified dependencies are outside persistent user/machine PATH; Bridge-local discovery will use: {0}" -f ($missingPersistentPaths -join '; '))
        $pathDiscoveryFallback = $true
    } else {
        Write-Output '[PASS] Verified executable directories are present in persistent user/machine PATH.'
    }

    try {
        $validation = @(Invoke-BridgeValidate)
        Write-Output ("[PASS] Skill source: {0}" -f ($validation -join ' '))
    } catch {
        Write-Output ("[FAIL] Skill source validation: {0}" -f $_.Exception.Message)
        $failed = $true
    }

    $paths = Get-BridgePaths
    if (Test-Path -LiteralPath (Join-Path $paths.Runtime 'bridge.py') -PathType Leaf) {
        Write-Output '[INFO] Bridge runtime is installed; use bridge doctor to inspect parity and health.'
    } else {
        Write-Output '[PENDING] Bridge runtime is not installed. Preflight does not install it.'
    }

    try {
        $agentsCheck = @((Invoke-AgentsInit -Check)) -join ' '
        if ($agentsCheck -match 'managed rules:\s*current\.') {
            Write-Output '[PASS] AGENTS.md managed rules are current.'
        } else {
            Write-Output ("[PENDING] {0}" -f $agentsCheck)
        }
    } catch {
        Write-Output ("[FAIL] AGENTS.md managed rules check: {0}" -f $_.Exception.Message)
        $failed = $true
    }

    if ($missingPrerequisites) {
        Write-Output '[NEXT] Verify the official source, exact current-user install scope, PATH impact, and recovery path; then install the listed prerequisites automatically and rerun preflight.'
    }
    if ($pathDiscoveryFallback) {
        Write-Output '[INFO] Do not claim global PATH availability. Use the Bridge wrapper and exact verified executable paths; start fresh-shell checks with the current shell executable absolute path.'
    }
    Write-Output '[BLOCKED] Retired Desktop Beeper producer surfaces are terminal tombstones; do not mount, reresponder, rehydrate, page, activate, resume, or canary them. A future materially different product-level run_once surface requires separate implementation and closed runtime attestation.'
    $script:BridgePreflightFailed = $failed
}

function Invoke-BridgeTests {
    $testsRoot = Join-Path (Split-Path -Parent $PSScriptRoot) 'tests'
    if (-not (Test-Path -LiteralPath $testsRoot)) { throw "Skill tests are missing: $testsRoot" }
    if (-not $RunTests) {
        Invoke-BridgeValidate
        Write-Output 'Dynamic tests were skipped. They may run only from an external terminal or external CI.'
        return
    }
    if (-not $ExternalTestRunnerAcknowledged -or $env:FEISHU_BRIDGE_EXTERNAL_TEST_RUNNER -ne '1') {
        throw ('Dynamic bridge tests are prohibited inside Codex Desktop. ' +
            'From an external terminal only, set FEISHU_BRIDGE_EXTERNAL_TEST_RUNNER=1 ' +
            'and pass -RunTests -ExternalTestRunnerAcknowledged.')
    }
    $paths = Get-BridgePaths
    Assert-BridgeStopped
    Update-ProcessPathFromEnvironment
    $python = Get-ExecutablePreflightResult 'python.exe'
    $pythonPrefix = @()
    if (-not $python.Available) {
        $python = Get-ExecutablePreflightResult 'py.exe' @('-3', '--version')
        $pythonPrefix = @('-3')
    }
    if (-not $python.Available) { throw 'Python 3 is required to run bridge tests.' }
    $env:PYTHONDONTWRITEBYTECODE = '1'
    & $python.Source @pythonPrefix -B -m unittest discover -s $testsRoot -p 'test_*.py' -v
    if ($LASTEXITCODE -ne 0) { throw "Bridge tests failed with exit code $LASTEXITCODE" }
}

function Invoke-BridgeAccess {
    if (-not $AccessMode) { throw 'AccessMode is required: compat or locked.' }
    $paths = Get-BridgePaths
    if (-not (Test-Path -LiteralPath $paths.Env -PathType Leaf)) {
        throw "Bridge environment is not installed: $($paths.Env)"
    }
    $envState = Get-BridgeEnvFileState
    if ($envState.Issues.Count -gt 0) {
        throw "Bridge environment is invalid: $($envState.Issues -join ' | ')"
    }

    $identityValues = [ordered]@{
        CODEX_BRIDGE_OWNER_OPEN_ID = [pscustomobject]@{ Value = $OwnerOpenId; Prefix = 'ou_'; Single = $true }
        CODEX_BRIDGE_ADMIN_OPEN_IDS = [pscustomobject]@{ Value = $AdminOpenIds; Prefix = 'ou_'; Single = $false }
        CODEX_BRIDGE_ALLOWED_USER_OPEN_IDS = [pscustomobject]@{ Value = $AllowedUserOpenIds; Prefix = 'ou_'; Single = $false }
        CODEX_BRIDGE_ALLOWED_CHAT_IDS = [pscustomobject]@{ Value = $AllowedChatIds; Prefix = 'oc_'; Single = $false }
    }
    foreach ($entry in $identityValues.GetEnumerator()) {
        Assert-BridgeIdentifierList -Name $entry.Key -Value ([string]$entry.Value.Value) `
            -Prefix ([string]$entry.Value.Prefix) -Single:([bool]$entry.Value.Single)
    }
    $hasEffectiveIdentity = $false
    foreach ($entry in $identityValues.GetEnumerator()) {
        $effective = if ([string]::IsNullOrWhiteSpace([string]$entry.Value.Value)) {
            if ($envState.Values.ContainsKey($entry.Key)) {
                [string]$envState.Values[$entry.Key]
            } else {
                ''
            }
        } else {
            [string]$entry.Value.Value
        }
        Assert-BridgeIdentifierList -Name $entry.Key -Value $effective `
            -Prefix ([string]$entry.Value.Prefix) -Single:([bool]$entry.Value.Single)
        if (-not [string]::IsNullOrWhiteSpace($effective)) {
            $hasEffectiveIdentity = $true
        }
    }
    if ($AccessMode -eq 'locked' -and -not $hasEffectiveIdentity) {
        throw 'Locked mode requires at least one existing or supplied owner, admin, user, or chat ID.'
    }
    if ($AccessMode -eq 'compat') {
        if ($hasEffectiveIdentity) {
            Write-Warning 'Compatibility mode is legacy-only. Existing identities still constrain access; migrate to locked before canary or production.'
        } else {
            Write-Warning 'Compatibility mode with no identities accepts every Feishu sender. Do not use it for canary or production.'
        }
    }

    Set-BridgeEnvValue 'CODEX_BRIDGE_ACCESS_MODE' $AccessMode
    foreach ($entry in $identityValues.GetEnumerator()) {
        if (-not [string]::IsNullOrWhiteSpace([string]$entry.Value.Value)) {
            Set-BridgeEnvValue $entry.Key ([string]$entry.Value.Value)
        }
    }
    Write-Output 'Access policy only was updated; runtime code, hooks, and project rules were not changed. Restart the bridge separately to apply it.'
}

function Invoke-FinalCallbackRegistryHelper {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet('final-callback-registry-status', 'final-callback-register', 'final-callback-unregister')]
        [string]$Command
    )

    $paths = Get-BridgePaths
    $helper = Join-Path $paths.Runtime 'beeper_queue_cli.py'
    $installedVersion = 'unknown'
    $manifestPath = Join-Path $paths.Runtime 'runtime-manifest.json'
    if (Test-Path -LiteralPath $manifestPath -PathType Leaf) {
        try {
            $installedManifest = Get-Content -LiteralPath $manifestPath -Raw -ErrorAction Stop |
                ConvertFrom-Json -ErrorAction Stop
            if (-not [string]::IsNullOrWhiteSpace([string]$installedManifest.bridge_version)) {
                $installedVersion = [string]$installedManifest.bridge_version
            }
        } catch {
            # Registration still performs the full manifest check below.  The
            # read-only status contract intentionally reveals no local path or
            # parser detail when an older runtime lacks this capability.
        }
    }

    $helperSupportsFinalCallback = $false
    if (Test-Path -LiteralPath $helper -PathType Leaf) {
        try {
            $helperText = Get-Content -LiteralPath $helper -Raw -ErrorAction Stop
            $helperSupportsFinalCallback = $helperText.Contains(
                'subcommands.add_parser("final-callback-registry-status")'
            )
        } catch {
            throw 'The installed Bridge final-callback helper could not be inspected.'
        }
    }
    if (-not $helperSupportsFinalCallback) {
        if ($Command -eq 'final-callback-registry-status') {
            Write-Output (ConvertTo-Json -Compress -InputObject ([ordered]@{
                schema_version = 1
                command = 'bridge.final-callback-status'
                status = 'upgrade_required'
                installed_bridge_version = $installedVersion
                required_runtime_capability = 'p0_exact_final_callback'
            }))
            return
        }
        throw 'The installed Bridge runtime predates P0 exact final-callback registration; run and verify bridge upgrade first.'
    }
    if ($Command -eq 'final-callback-register') {
        $manifestIssues = @(Get-InstalledBridgeManifestIssues)
        if ($manifestIssues.Count -gt 0) {
            throw (
                'Final Callback registration requires a valid installed runtime manifest: ' +
                ($manifestIssues -join ' | ')
            )
        }
    }

    Update-ProcessPathFromEnvironment
    $python = Get-ExecutablePreflightResult 'python.exe'
    $pythonPrefix = @()
    if (-not $python.Available) {
        $python = Get-ExecutablePreflightResult 'py.exe' @('-3', '--version')
        $pythonPrefix = @('-3')
    }
    if (-not $python.Available) {
        throw 'Python 3.10+ is required for final-callback registry management.'
    }

    & $python.Source @pythonPrefix -S -B $helper --runtime-dir $paths.Runtime $Command
    if ($LASTEXITCODE -ne 0) {
        throw "Final Callback registry helper failed with exit code $LASTEXITCODE."
    }
}

$scopeName = $Scope.ToLowerInvariant()
$actionName = $Action.ToLowerInvariant()

if ($Json -and -not ($scopeName -eq 'bridge' -and $actionName -in @('status', 'doctor', 'readiness', 'validate'))) {
    throw '-Json is supported only for bridge status, bridge doctor, bridge readiness, and bridge validate.'
}

switch ($scopeName) {
    'help' { Show-Usage; exit 0 }
    '-help' { Show-Usage; exit 0 }
    '--help' { Show-Usage; exit 0 }
    'doctor' {
        Invoke-FeishuDoctor
        Invoke-BridgeDoctor
        exit 0
    }
    'feishu' {
        switch ($actionName) {
            'install' { Invoke-FeishuInstall }
            'configure' { Invoke-FeishuConfigure }
            'login' { Invoke-FeishuLogin }
            'doctor' { Invoke-FeishuDoctor }
            'desktop-status' { Invoke-FeishuDesktopStatus }
            'desktop-install' { Invoke-FeishuDesktopInstall }
            default { Show-Usage; throw "Unknown Feishu subcommand: $Action" }
        }
    }
    'bridge' {
        switch ($actionName) {
            'init' { Invoke-AgentsInit }
            'install' { Invoke-Installer }
            'upgrade' { Invoke-Installer -Upgrade; Write-Output 'Upgrade installed and verified. Continue with the separately observable bridge restart transaction to activate it.' }
            'hooks' { Invoke-BridgeHooksRefresh }
            'final-callback-status' {
                Invoke-FinalCallbackRegistryHelper -Command 'final-callback-registry-status'
            }
            'final-callback-register' {
                Invoke-FinalCallbackRegistryHelper -Command 'final-callback-register'
            }
            'final-callback-unregister' {
                Invoke-FinalCallbackRegistryHelper -Command 'final-callback-unregister'
            }
            'start' { Invoke-BridgeStart }
            'stop' { Invoke-BridgeStop }
            'restart' { Invoke-BridgeRestart }
            'preflight' {
                Invoke-BridgePreflight
                if ($script:BridgePreflightFailed) { exit 2 }
            }
            'status' {
                if ($Json) { Write-BridgeJson -InputObject (Get-BridgeStatusContract) }
                else { Invoke-BridgeStatus }
            }
            'doctor' {
                if ($Json) {
                    $doctorContract = Get-BridgeDoctorContract
                    Write-BridgeJson -InputObject $doctorContract
                    if ($doctorContract.status -eq 'fail') { exit 2 }
                }
                else { Invoke-BridgeDoctor }
            }
            'readiness' {
                if ($Json) {
                    $readinessContract = Get-BridgeReadinessContract
                    Write-BridgeJson -InputObject $readinessContract
                    if (-not $readinessContract.production.eligible) { exit 2 }
                }
                else {
                    $readinessContract = Get-BridgeReadinessContract
                    Invoke-BridgeReadiness -Contract $readinessContract
                    if (-not $readinessContract.production.eligible) { exit 2 }
                }
            }
            'logs' { Invoke-BridgeLogs }
            'validate' {
                if ($Json) {
                    $validationContract = Get-BridgeValidateContract
                    Write-BridgeJson -InputObject $validationContract
                    if ($validationContract.status -ne 'pass') { exit 2 }
                } else { Invoke-BridgeValidate }
            }
            'test' { Invoke-BridgeTests }
            'access' { Invoke-BridgeAccess }
            default { Show-Usage; throw "Unknown bridge subcommand: $Action" }
        }
    }
    default { Show-Usage; throw "Unknown command scope: $Scope" }
}
