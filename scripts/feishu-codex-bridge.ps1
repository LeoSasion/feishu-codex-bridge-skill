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

    [switch]$DesktopInstallConsent,

    [string]$DeviceCode,

    [ValidatePattern('^\d+(?:\.\d+){3}$')]
    [string]$DesktopBuild,

    [ValidateSet('on', 'off')]
    [string]$ProjectCreate,

    [string]$ProjectsRoot,

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
  feishu-codex-bridge.ps1 feishu desktop-install -DesktopInstallConsent
  feishu-codex-bridge.ps1 feishu login -Recommend -NoWait
  feishu-codex-bridge.ps1 feishu login -AuthScope <scope> -NoWait
  feishu-codex-bridge.ps1 feishu login -AuthDomain <domain> -NoWait
  feishu-codex-bridge.ps1 feishu login -DeviceCode <device_code>
  feishu-codex-bridge.ps1 bridge init|install|start|stop|restart
  feishu-codex-bridge.ps1 bridge hooks
  feishu-codex-bridge.ps1 bridge upgrade
  feishu-codex-bridge.ps1 bridge canary-gate [-DesktopBuild <package-version>]
  feishu-codex-bridge.ps1 bridge final-return-status
  feishu-codex-bridge.ps1 bridge final-return-register
  feishu-codex-bridge.ps1 bridge final-return-unregister
  feishu-codex-bridge.ps1 bridge status|doctor|validate [-Json]
  feishu-codex-bridge.ps1 bridge preflight|logs|test
  feishu-codex-bridge.ps1 bridge test -RunTests -ExternalTestRunnerAcknowledged
  feishu-codex-bridge.ps1 bridge access -AccessMode locked -OwnerOpenId <open_id>
  feishu-codex-bridge.ps1 bridge projects -ProjectCreate on|off [-ProjectsRoot <existing-parent>]
  feishu-codex-bridge.ps1 doctor

Knowledge bases, including Obsidian vaults, belong to the bound Codex project's
directory. The bridge has no knowledge-base command or setting.
'@ | Write-Output
}

function Show-WelcomeAndMountConsent {
    @'
欢迎使用 Codex 飞书机器人。

飞书 CLI 安装完成后，可以把私聊和群聊 @ 消息挂载到当前 Codex 项目。每个私聊、群聊或群话题都能映射到一个可在 Codex Desktop 查看和继续的持久会话。

首次消息会提示发送 /init，随后通过对话菜单按项目查看任务名称和完整任务 ID、选择已有任务或新建任务、查看归档、压缩、解除连接与设置回复方式。旧斜杠命令不会执行，只会引导回 /init。默认任务在当前 Bridge 项目执行；如需隔离不同工作，可在本机显式启用后由 owner/admin 在 /init 中创建独立项目。飞书监听器先写入本地持久队列；一个两分钟 Gateway 调度 heartbeat 在专用 Codex Desktop Gateway 现有会话中产生自动化回合，空轮只检查数量、代次和租约，有消息时 Gateway 在同一回合领取并通过任务间通信交给目标会话。Bridge 不会打开或占用目标会话。默认只回传最终答案，不发送思考或工具过程。

Listener 挂载会在当前项目写入桥接运行文件和 Codex hooks，也不会替用户授予飞书权限。Listener 安装后还需要逐项授权，才能在 Codex Desktop 创建一个专属 Gateway 任务、挂载合同并注册、创建指向该现有任务的调度 heartbeat 自动化以及激活它；安装器不会暗中完成这些动作。目标会话自己的模型、推理、沙箱、插件和知识库设置保持不变；Bridge 不安装、注册或检索 Obsidian。

是否同意挂载？请在 Codex 对话中明确回复“同意挂载”“确认”或“是”。得到明确同意后，仍要分别说明并审批 bridge init、首次 bridge install，以及至少配置一个身份的 locked bridge access，不能合并执行；空 allowlist 会拒绝所有飞书事件。
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

function Assert-BridgeListenerStopped {
    $state = Get-BridgePidState
    if (-not $state.HasPidFile -or $state.Pid -le 0 -or -not $state.Identity.Exists) {
        return
    }
    if (-not $state.Identity.Verified) {
        throw "Listener PID $($state.Pid) exists, but its command line could not be verified; refusing a lifecycle mutation."
    }
    if ($state.Identity.IsBridge) {
        throw "Listener must be stopped under a separate approval (PID $($state.Pid))."
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
        'CODEX_BRIDGE_ALLOW_PROJECT_CREATE',
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
    Show-WelcomeAndMountConsent
    Write-Output 'Installing the official Feishu CLI package.'
    Invoke-Checked 'npm' @('install', '-g', '@larksuite/cli')
    Write-Output 'Installing the official Feishu CLI Skill.'
    Invoke-Checked 'npx' @('-y', 'skills', 'add', 'https://open.feishu.cn', '--skill', '-y')
    Write-Output 'Feishu CLI installation completed. The bridge is not mounted yet.'
    Show-WelcomeAndMountConsent
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
        throw 'Feishu login requires -NoWait so the authorization URL can be shown before the next step.'
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
    if (-not $DesktopInstallConsent) {
        throw 'Feishu Desktop installation requires current explicit user consent; rerun with -DesktopInstallConsent only after disclosing the official source, install scope, and login hand-off.'
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
        Assert-BridgeListenerStopped
        # Public bridge upgrades are runtime-only. Hooks, project rules, config,
        # and restart remain separately named approval checkpoints.
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
    Write-Output 'Lifecycle hooks refreshed only. A separately approved runtime install or upgrade must write the matching manifest before start.'
}

function Assert-BridgeStartReady {
    $paths = Get-BridgePaths
    if (-not (Test-Path -LiteralPath $paths.Start)) { throw "Bridge is not installed: $($paths.Start)" }
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
            "Run a separately approved bridge install or upgrade first ({0})." -f ($details -join '; '))
    }
    $envState = Get-BridgeEnvFileState
    if ($envState.Issues.Count -gt 0) {
        throw "Refusing to start with an invalid bridge.env: $($envState.Issues -join ' | ')"
    }
    return $paths
}

function Invoke-BridgeStart {
    $paths = Assert-BridgeStartReady
    & $paths.Start
}

function Invoke-BridgeRestart {
    # Validate before interruption so a stale install cannot turn a healthy
    # process into an avoidable outage during an approved restart.
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
            throw "Listener PID $($state.Pid) exists, but its command line could not be verified; refusing to stop any process."
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
            throw ('Installed stop hook lacks PID-identity fencing. Refresh hooks while the Listener is stopped; ' +
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
            Write-Output ("Runtime PID file: stale; referenced process is {0}, not this Bridge Listener." -f $pidState.Identity.ProcessName)
        }
    }
    if (Test-Path -LiteralPath $paths.Health) {
        $health = Get-Content -LiteralPath $paths.Health -Raw | ConvertFrom-Json
        $owner = if ($health.session_owner) { $health.session_owner } else { 'unknown' }
        $transport = if ($health.codex_transport) { $health.codex_transport } else { 'desktop-gateway-unknown' }
        $gatewayState = if ($health.gateway_state) { $health.gateway_state } else { 'legacy-health-snapshot' }
        Write-Output ("Health: {0}; version={1}; Feishu consumer={2}; owner={3}; gateway={4}; transport={5}; active={6}" -f $health.status, $health.bridge_version, $health.event_consumer, $owner, $gatewayState, $transport, $health.active_turns)
        if ($health.desktop_router) {
            $router = $health.desktop_router
            $schedulerFresh = if ($router.PSObject.Properties.Name -contains 'scheduler_fresh') { [bool]$router.scheduler_fresh } else { [bool]$router.sentinel_fresh }
            $schedulerAge = if ($router.PSObject.Properties.Name -contains 'scheduler_age_seconds') { $router.scheduler_age_seconds } else { $router.sentinel_age_seconds }
            $workFresh = if ($router.PSObject.Properties.Name -contains 'work_heartbeat_fresh') { [bool]$router.work_heartbeat_fresh } else { [bool]$router.ready }
            $workAge = if ($router.PSObject.Properties.Name -contains 'work_heartbeat_age_seconds') { $router.work_heartbeat_age_seconds } else { $router.heartbeat_age_seconds }
            $schedulerAgeText = if ($null -eq $schedulerAge) { 'unknown' } else { ('{0:N1}s' -f [double]$schedulerAge) }
            $workAgeText = if ($null -eq $workAge) { 'unknown' } else { ('{0:N1}s' -f [double]$workAge) }
            Write-Output ("Gateway scheduler: {0}; last probe age={1}." -f $(if ($schedulerFresh) { 'fresh' } else { 'stale' }), $schedulerAgeText)
            $workState = if (-not [bool]$router.wake_inflight) { 'idle' } elseif ($workFresh) { 'fresh' } else { 'stale' }
            Write-Output ("Active-work lease: {0}; heartbeat age={1}; wake inflight={2}." -f $workState, $workAgeText, [bool]$router.wake_inflight)
        }
        if ($health.queue) {
            Write-Output ("Queue: {0}" -f (($health.queue | ConvertTo-Json -Compress)))
        }
    } else {
        Write-Output 'Health: no Listener health snapshot yet.'
    }
}

function Get-BridgeParity {
    $skillRoot = Split-Path -Parent $PSScriptRoot
    $pairs = [ordered]@{
        'bridge.py' = @(
            (Join-Path $skillRoot 'scripts\bridge.py'),
            (Join-Path (Get-BridgePaths).Runtime 'bridge.py')
        )
        'router_queue.py' = @(
            (Join-Path $skillRoot 'scripts\router_queue.py'),
            (Join-Path (Get-BridgePaths).Runtime 'router_queue.py')
        )
        'bridge_core\__init__.py' = @(
            (Join-Path $skillRoot 'scripts\bridge_core\__init__.py'),
            (Join-Path (Get-BridgePaths).Runtime 'bridge_core\__init__.py')
        )
        'bridge_core\codex_client.py' = @(
            (Join-Path $skillRoot 'scripts\bridge_core\codex_client.py'),
            (Join-Path (Get-BridgePaths).Runtime 'bridge_core\codex_client.py')
        )
        'bridge_core\desktop_router.py' = @(
            (Join-Path $skillRoot 'scripts\bridge_core\desktop_router.py'),
            (Join-Path (Get-BridgePaths).Runtime 'bridge_core\desktop_router.py')
        )
        'bridge_core\config.py' = @(
            (Join-Path $skillRoot 'scripts\bridge_core\config.py'),
            (Join-Path (Get-BridgePaths).Runtime 'bridge_core\config.py')
        )
        'bridge_core\lark.py' = @(
            (Join-Path $skillRoot 'scripts\bridge_core\lark.py'),
            (Join-Path (Get-BridgePaths).Runtime 'bridge_core\lark.py')
        )
        'bridge_core\project_routing.py' = @(
            (Join-Path $skillRoot 'scripts\bridge_core\project_routing.py'),
            (Join-Path (Get-BridgePaths).Runtime 'bridge_core\project_routing.py')
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
        $target = $pairs[$name][1]
        if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {
            $missing += $name
            continue
        }
        $sourceHash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
        $targetHash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash
        if ($sourceHash -ne $targetHash) { $mismatch += $name }
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
        $target = Join-Path $paths.Runtime ($relative.Replace('/', '\'))
        if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {
            $issues.Add("installed runtime file is missing: $relative")
            continue
        }
        try {
            $actual = (Get-FileHash -LiteralPath $target -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant()
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

function Invoke-BridgeDoctor {
    $paths = Get-BridgePaths
    $hooksConfig = Join-Path $paths.Project '.codex\hooks.json'
    $requiredPaths = @(
        (Join-Path $paths.Runtime 'bridge.py'),
        (Join-Path $paths.Runtime 'router_queue.py'),
        (Join-Path $paths.Runtime 'bridge_core\runtime.py'),
        (Join-Path $paths.Runtime 'bridge_core\desktop_router.py'),
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
        $projectCreateValue = ([string]$envState.Values['CODEX_BRIDGE_ALLOW_PROJECT_CREATE']).Trim().ToLowerInvariant()
        $projectCreateEnabled = $projectCreateValue -in @('1', 'true', 'yes', 'on')
        $projectRootConfigured = -not [string]::IsNullOrWhiteSpace(
            [string]$envState.Values['CODEX_BRIDGE_PROJECTS_ROOT']
        )
        Write-Output ("Feishu /init new-project action: {0}; project container: {1}." -f $(if ($projectCreateEnabled) { 'enabled for owner/admin' } else { 'disabled (default)' }), $(if ($projectRootConfigured) { 'explicitly configured' } else { 'bridge project parent (default)' }))
        if ($envState.Values.ContainsKey('CODEX_BRIDGE_SESSION_OWNER')) {
            Write-Warning 'Legacy CODEX_BRIDGE_SESSION_OWNER is no longer used by schema v4 and can be removed.'
        }
    }
    Write-Output 'Knowledge context: inherited from the bound Codex project; no bridge-side knowledge configuration.'
    Write-Output 'Session mode: desktop-router (durable queue plus Codex Desktop task-to-task tools; no target App Server writer).'
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
    $manifestIssues = @(Get-InstalledBridgeManifestIssues)
    if ($manifestPresent) {
        try {
            $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding utf8 |
                ConvertFrom-Json -ErrorAction Stop
            $manifestVersion = [string]$manifest.bridge_version
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
        gateway_state = $null
        codex_transport = $null
        active_turns = $null
        scheduler_fresh = $null
        scheduler_age_seconds = $null
        work_heartbeat_fresh = $null
        work_heartbeat_age_seconds = $null
        wake_inflight = $null
        queue_counts = $null
    }
    $healthIssue = $null
    if (Test-Path -LiteralPath $paths.Health -PathType Leaf) {
        $healthSummary.present = $true
        try {
            $health = Get-Content -LiteralPath $paths.Health -Raw -Encoding utf8 |
                ConvertFrom-Json -ErrorAction Stop
            $healthSummary.valid = $true
            $healthSummary.status = [string]$health.status
            $healthSummary.bridge_version = [string]$health.bridge_version
            $healthSummary.event_consumer = if ($null -eq $health.event_consumer) { $null } else { [bool]$health.event_consumer }
            $healthSummary.session_owner = if ($health.session_owner) { [string]$health.session_owner } else { $null }
            $healthSummary.gateway_state = if ($health.gateway_state) { [string]$health.gateway_state } else { $null }
            $healthSummary.codex_transport = if ($health.codex_transport) { [string]$health.codex_transport } else { $null }
            $healthSummary.active_turns = if ($null -eq $health.active_turns) { $null } else { [int]$health.active_turns }
            if ($health.desktop_router) {
                $router = $health.desktop_router
                $healthSummary.scheduler_fresh = if ($router.PSObject.Properties.Name -contains 'scheduler_fresh') {
                    [bool]$router.scheduler_fresh
                } else { [bool]$router.sentinel_fresh }
                $healthSummary.scheduler_age_seconds = if ($router.PSObject.Properties.Name -contains 'scheduler_age_seconds') {
                    $router.scheduler_age_seconds
                } else { $router.sentinel_age_seconds }
                $healthSummary.work_heartbeat_fresh = if ($router.PSObject.Properties.Name -contains 'work_heartbeat_fresh') {
                    [bool]$router.work_heartbeat_fresh
                } else { [bool]$router.ready }
                $healthSummary.work_heartbeat_age_seconds = if ($router.PSObject.Properties.Name -contains 'work_heartbeat_age_seconds') {
                    $router.work_heartbeat_age_seconds
                } else { $router.heartbeat_age_seconds }
                $healthSummary.wake_inflight = [bool]$router.wake_inflight
            }
            if ($health.queue) {
                $queueCounts = [ordered]@{
                    queued = 0
                    running = 0
                    reply_pending = 0
                    retryable_failed = 0
                    completed = 0
                    terminal_failed = 0
                }
                foreach ($queueStatus in @($queueCounts.Keys)) {
                    $property = $health.queue.PSObject.Properties[$queueStatus]
                    if ($property) { $queueCounts[$queueStatus] = [int]$property.Value }
                }
                $healthSummary.queue_counts = $queueCounts
            }
        } catch {
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
        router_queue_py = (Join-Path $paths.Runtime 'router_queue.py')
        runtime_py = (Join-Path $paths.Runtime 'bridge_core\runtime.py')
        desktop_router_py = (Join-Path $paths.Runtime 'bridge_core\desktop_router.py')
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
    $projectCreateEnabled = $false
    $projectsRootConfigured = $false
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
        $projectCreateValue = ([string]$envState.Values['CODEX_BRIDGE_ALLOW_PROJECT_CREATE']).Trim().ToLowerInvariant()
        $projectCreateEnabled = $projectCreateValue -in @('1', 'true', 'yes', 'on')
        $projectsRootConfigured = -not [string]::IsNullOrWhiteSpace(
            [string]$envState.Values['CODEX_BRIDGE_PROJECTS_ROOT']
        )
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
        project_creation = [ordered]@{
            enabled = $projectCreateEnabled
            root_explicitly_configured = $projectsRootConfigured
        }
        agents_rules = [ordered]@{
            current = $agentsCurrent
            issue = $agentsIssue
        }
        knowledge_context = 'target_project_inherited'
        session_mode = 'desktop-router'
    }
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
    $releaseAuditText = @(& $releaseAuditPath -DesktopRoot $skillRoot -DesktopOnly) -join "`n"
    try {
        $releaseAuditResult = $releaseAuditText | ConvertFrom-Json
    } catch {
        throw "Desktop release audit did not return valid JSON: $($_.Exception.Message)"
    }
    if ($releaseAuditResult.status -ne 'pass' -or $releaseAuditResult.components.Count -ne 1 -or
        $releaseAuditResult.components[0].name -ne 'desktop_bridge') {
        throw 'Desktop release audit did not return one passing desktop_bridge component.'
    }
    $ignoreText = Get-Content -LiteralPath (Join-Path $skillRoot '.gitignore') -Raw
    foreach ($releaseLocal in @('.codex/', '.agents/skills/', '_retired/', '/.tmp/', '/plugins/human-authorization-relay/', '/AGENTS.md', '/skills-lock.json')) {
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
    foreach ($legacyContract in @('assets\desktop-router-task.md', 'assets\desktop-router-sentinel.md')) {
        if (Test-Path -LiteralPath (Join-Path $skillRoot $legacyContract)) {
            throw "Legacy two-task contract must be removed: $legacyContract"
        }
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

    $p1LabPath = Join-Path $skillRoot 'scripts\external-p1-migration-lab.ps1'
    $p1LabText = Get-Content -LiteralPath $p1LabPath -Raw -Encoding utf8
    foreach ($marker in @(
        "[ValidateSet('prepare', 'observe', 'rollback')]",
        'FEISHU_BRIDGE_EXTERNAL_MIGRATION',
        'Assert-ExternalProcess',
        'Assert-PsfPython',
        'Assert-ListenerStopped',
        'Test-LockedAccessConfiguration',
        'Test-UnrelatedHookPreserved',
        'Test-BridgeHookRegistration',
        'Get-QueueFileCounts',
        'runpy.run_path',
        '[System.IO.Directory]::Move',
        '.preparing',
        'rollback-intent.json',
        'ordinary rollback requires one clean upgraded phase',
        'baseline_manifest_sha256',
        'after-upgrade-'
    )) {
        if ($p1LabText -notmatch [regex]::Escape($marker)) {
            throw "P1 isolated migration lab is missing safety marker: $marker"
        }
    }
    foreach ($forbiddenP1Marker in @(
        'Remove-Item',
        'codex.exe',
        'app-server',
        "'bridge', 'hooks'",
        "'bridge', 'upgrade'",
        "'bridge', 'start'",
        "'bridge', 'restart'"
    )) {
        if ($p1LabText -match [regex]::Escape($forbiddenP1Marker)) {
            throw "P1 isolated migration lab must not perform an administrative stage: $forbiddenP1Marker"
        }
    }
    $p1ObservationFunction = [regex]::Match(
        $p1LabText,
        '(?ms)^function Get-LabObservation\s*\{.*?^Assert-ExternalProcess'
    )
    if (-not $p1ObservationFunction.Success -or
        $p1ObservationFunction.Value -match [regex]::Escape('Invoke-QueueStatus')) {
        throw 'P1 observe must be isolated as a read-only filesystem observation.'
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
        'p0-validation-post',
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
        scheduler_overlap = 'test_desktop_router.DesktopRouterQueueTests.test_concurrent_sentinel_probes_reserve_exactly_one_wake'
        identical_producer_overlap = 'test_desktop_router.DesktopRouterQueueTests.test_identical_producer_overlap_cannot_republish_claimed_request'
        conflicting_producer_overlap = 'test_desktop_router.DesktopRouterQueueTests.test_concurrent_conflicting_producers_publish_one_fingerprint'
        terminal_finalizer_race = 'test_desktop_router.DesktopRouterQueueTests.test_concurrent_terminal_finalizers_preserve_first_receipt'
        long_task_lease = 'test_desktop_router.DesktopRouterQueueTests.test_fresh_router_heartbeat_protects_a_long_running_claim'
        long_task_retention = 'test_desktop_router.DesktopRouterQueueTests.test_retention_never_deletes_a_nonterminal_long_running_claim'
        listener_pre_model_restart = 'test_state.DurableStateTests.test_restart_requeues_work_that_never_started_model'
        listener_post_model_restart = 'test_state.DurableStateTests.test_restart_does_not_rerun_a_started_model_turn'
        feishu_rate_limit_network_retry = 'test_routing.RoutingTests.test_rate_limit_and_network_failures_remain_retryable'
        withdrawn_message_terminal = 'test_runtime.ReplyDeliveryTests.test_terminal_reply_result_is_not_rescheduled'
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
        [bool]$p3Schema.properties.execution.properties.live_desktop_contacted.const -or
        [bool]$p3Schema.properties.execution.properties.live_feishu_contacted.const -or
        $p3ReferenceText -notmatch [regex]::Escape('P0-B''s retained')) {
        throw 'P3 bounded soak schema or operator reference drifted from the stopped external contract.'
    }

    $startHookText = Get-Content -LiteralPath (Join-Path $skillRoot 'scripts\start-feishu-codex-bridge.ps1') -Raw
    $stopHookText = Get-Content -LiteralPath (Join-Path $skillRoot 'scripts\stop-feishu-codex-bridge.ps1') -Raw
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

    $installerText = Get-Content -LiteralPath (Join-Path $skillRoot 'scripts\install-feishu-codex-bridge.ps1') -Raw
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
        'runtime-manifest.json',
        'CODEX_BRIDGE_ACCESS_MODE=locked',
        '$expectedVersion = ''4.2.0-alpha.30''',
        '$BRIDGE_RUNTIME_MANIFEST_SCHEMA = 1'
    )) {
        if ($installerText -notmatch [regex]::Escape($marker)) {
            throw "Installer is missing lifecycle safety marker: $marker"
        }
    }
    foreach ($hooksOnlyMarker in @(
        '[switch]$HooksOnly',
        'Listener must be stopped under a separate approval',
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

    $dispatcherText = Get-Content -LiteralPath (Join-Path $skillRoot 'scripts\feishu-codex-bridge.ps1') -Raw
    foreach ($marker in @(
        'bridge init',
        'bridge install',
        'bridge upgrade',
        'bridge hooks',
        'bridge canary-gate',
        'bridge final-return-status',
        'bridge final-return-register',
        'bridge final-return-unregister',
        'bridge preflight',
        'bridge validate',
        'bridge projects',
        'SkipRuntimeConfig',
        'first-bootstrap only',
        'Get-InstalledBridgeHookIssues',
        'must be a matcher-group array',
        '[switch]$Json',
        'Get-BridgeStatusContract',
        'Get-BridgeDoctorContract',
        'Get-BridgeValidateContract',
        'Invoke-FinalReturnRegistryHelper',
        'final-return-registry-status',
        "command = 'bridge.status'",
        "command = 'bridge.doctor'",
        "command = 'bridge.validate'",
        'child_process_started = $false',
        '-Json is supported only for bridge status, bridge doctor, and bridge validate.'
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
    $pluginRoot = Join-Path $skillRoot 'plugins\feishu-codex-final-return'
    $pluginManifest = Get-Content -LiteralPath (Join-Path $pluginRoot '.codex-plugin\plugin.json') -Raw -Encoding utf8 | ConvertFrom-Json -ErrorAction Stop
    $pluginMcp = Get-Content -LiteralPath (Join-Path $pluginRoot '.mcp.json') -Raw -Encoding utf8 | ConvertFrom-Json -ErrorAction Stop
    $pluginHooks = Get-Content -LiteralPath (Join-Path $pluginRoot 'hooks\hooks.json') -Raw -Encoding utf8 | ConvertFrom-Json -ErrorAction Stop
    $pluginServerText = Get-Content -LiteralPath (Join-Path $pluginRoot 'scripts\final_return_mcp_server.py') -Raw -Encoding utf8
    $pluginMarketplace = Get-Content -LiteralPath (Join-Path $skillRoot '.agents\plugins\marketplace.json') -Raw -Encoding utf8 | ConvertFrom-Json -ErrorAction Stop
    if ([string]$pluginManifest.name -cne 'feishu-codex-final-return' -or
        [string]$pluginManifest.mcpServers -cne './.mcp.json') {
        throw 'P0 final-return plugin manifest is invalid.'
    }
    $pluginHookEvents = @($pluginHooks.hooks.PSObject.Properties.Name | Sort-Object)
    if ($pluginHookEvents.Count -ne 2 -or
        $pluginHookEvents[0] -cne 'Stop' -or
        $pluginHookEvents[1] -cne 'UserPromptSubmit') {
        throw 'P0 final-return plugin must define exactly UserPromptSubmit and Stop Hooks.'
    }
    $promptHook = $pluginHooks.hooks.UserPromptSubmit[0].hooks[0]
    $stopHook = $pluginHooks.hooks.Stop[0].hooks[0]
    foreach ($hook in @($promptHook, $stopHook)) {
        if ([string]$hook.type -cne 'mcp_tool' -or
            [string]$hook.server -cne 'feishu_final_return') {
            throw 'P0 final-return Hook must call only the local feishu_final_return MCP server.'
        }
    }
    if ([string]$promptHook.tool -cne 'bind_user_prompt' -or
        [string]$promptHook.input.session_id -cne '${session_id}' -or
        [string]$promptHook.input.turn_id -cne '${turn_id}' -or
        [string]$promptHook.input.prompt -cne '${prompt}' -or
        [string]$stopHook.tool -cne 'capture_stop_final' -or
        [string]$stopHook.input.session_id -cne '${session_id}' -or
        [string]$stopHook.input.turn_id -cne '${turn_id}' -or
        [string]$stopHook.input.stop_hook_active -cne '${stop_hook_active}' -or
        [string]$stopHook.input.last_assistant_message -cne '${last_assistant_message}') {
        throw 'P0 final-return Hook input mapping drifted from the structured lifecycle contract.'
    }
    $mcpServer = $pluginMcp.mcpServers.feishu_final_return
    if (-not $mcpServer -or [string]$mcpServer.default_tools_approval_mode -cne 'auto') {
        throw 'P0 final-return MCP server must be configured for synchronous Hook-only automatic tool approval.'
    }
    foreach ($marker in @(
        'return {"ui": {"visibility": []}}',
        'return {"continue": True}',
        'ensure_ascii=True',
        'input=wire',
        'runtime-manifest.json',
        'final-return-hook',
        'stop_hook_active'
    )) {
        if ($pluginServerText -notmatch [regex]::Escape($marker)) {
            throw "P0 final-return MCP server is missing safety marker: $marker"
        }
    }
    foreach ($forbiddenPluginMarker in @('transcript_path', 'read_thread')) {
        if ($pluginServerText -match [regex]::Escape($forbiddenPluginMarker)) {
            throw "P0 final-return plugin must not inspect target history: $forbiddenPluginMarker"
        }
    }
    $marketplaceEntries = @($pluginMarketplace.plugins | Where-Object { $_.name -eq 'feishu-codex-final-return' })
    if ($marketplaceEntries.Count -ne 1 -or
        [string]$marketplaceEntries[0].source.source -cne 'local' -or
        [string]$marketplaceEntries[0].source.path -cne './plugins/feishu-codex-final-return' -or
        [string]$marketplaceEntries[0].policy.installation -cne 'AVAILABLE') {
        throw 'Repo-local P0 final-return plugin marketplace registration is invalid.'
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
        '(?ms)^function Invoke-BridgeAccess\s*\{.*?^function Invoke-BridgeProjectsConfig\s*\{'
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
        'feishu desktop-install -DesktopInstallConsent',
        'api/package_info?platform=',
        'Get-AuthenticodeSignature',
        'Installation, process, and cached files do not prove login'
    )) {
        if ($dispatcherText -notmatch [regex]::Escape($desktopMarker)) {
            throw "Dispatcher is missing Feishu Desktop safety marker: $desktopMarker"
        }
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
        'desktop-install -DesktopInstallConsent',
        '[PENDING] Feishu Desktop'
    )) {
        if ($preflightFunction.Value -match [regex]::Escape($forbiddenPreflightMarker)) {
            throw "Generic bridge preflight must not inspect Feishu Desktop: $forbiddenPreflightMarker"
        }
    }
    $canaryGateFunction = [regex]::Match(
        $dispatcherText,
        '(?ms)^function Get-CodexDesktopBuildState\s*\{.*?^function Get-ExecutablePreflightResult\s*\{'
    )
    if (-not $canaryGateFunction.Success) {
        throw 'Dispatcher canary gate function could not be isolated for read-only validation.'
    }
    foreach ($marker in @(
        'Get-CodexDesktopBuildState',
        'Get-DesktopGatewayCompatibilityMarkers',
        '26.818.5229.0',
        '26.818.8289.0',
        'scheduler_cap_unenforced',
        'target_final_readback_unavailable',
        'known_surface_incompatibility',
        'unmarked_build_requires_fresh_preflight',
        'eligible_for_fresh_preflight'
    )) {
        if ($canaryGateFunction.Value -notmatch [regex]::Escape($marker)) {
            throw "Dispatcher canary gate is missing fail-closed marker: $marker"
        }
    }
    foreach ($forbiddenCanaryGateMarker in @(
        'Invoke-Installer',
        'Invoke-BridgeStart',
        'Invoke-BridgeRestart',
        'Set-Content',
        'Remove-Item',
        'automation_update',
        'router_queue.py'
    )) {
        if ($canaryGateFunction.Value -match [regex]::Escape($forbiddenCanaryGateMarker)) {
            throw "Read-only canary gate contains a mutating marker: $forbiddenCanaryGateMarker"
        }
    }
    $clientText = Get-Content -LiteralPath (Join-Path $skillRoot 'scripts\bridge_core\codex_client.py') -Raw
    $routerText = Get-Content -LiteralPath (Join-Path $skillRoot 'scripts\bridge_core\desktop_router.py') -Raw
    $queueHelperText = Get-Content -LiteralPath (Join-Path $skillRoot 'scripts\router_queue.py') -Raw
    $runtimeText = Get-Content -LiteralPath (Join-Path $skillRoot 'scripts\bridge_core\runtime.py') -Raw
    $pythonRuntimeText = $clientText + "`n" + $routerText + "`n" + $runtimeText
    foreach ($forbidden in @(
        'CodexAppServer',
        'thread/resume',
        'thread/compact/start',
        'turn/start',
        'subprocess.Popen',
        'request_desktop_refresh',
        'codex://'
    )) {
        if ($pythonRuntimeText.Contains($forbidden)) {
            throw "Unsafe legacy target-writer transport remains: $forbidden. Complete the Desktop Gateway migration before starting the bridge."
        }
    }
    foreach ($marker in @('DesktopRouterCodex', 'send_message_to_thread', 'wait_threads', 'CodexSessionNotBound', 'DesktopRouterQueue', 'list_task_catalog', 'DesktopTaskCatalog', 'DESKTOP_TASK_CATALOG_LIMIT = 50', 'INIT_WIZARD_CATALOG_LIMIT = DESKTOP_TASK_CATALOG_LIMIT')) {
        if ($pythonRuntimeText -notmatch [regex]::Escape($marker)) {
            throw "Desktop Gateway transport marker is missing: $marker"
        }
    }
    foreach ($retryGenerationMarker in @(
        'MAX_RETRY_GENERATIONS',
        '_response_allows_retry',
        'retry_generation',
        'target_result_unknown',
        'read-only Desktop Gateway operation cannot be finalized with',
        'exceeded safe retry generations'
    )) {
        if ($routerText -notmatch [regex]::Escape($retryGenerationMarker)) {
            throw "Desktop Gateway queue is missing deterministic safe-retry validation: $retryGenerationMarker"
        }
    }
    foreach ($manualCycleMarker in @(
        'manual_cycle_tickets',
        'authorize_manual_cycle',
        'manual_probe',
        'authorized_request_id',
        'authorized_operation',
        'manual_ticket_consumed',
        '--manual-ticket',
        'manual-authorize',
        '--expected-operation'
    )) {
        if (($routerText + "`n" + $queueHelperText) -notmatch [regex]::Escape($manualCycleMarker)) {
            throw "Desktop Gateway queue is missing one-ticket manual-cycle marker: $manualCycleMarker"
        }
    }
    foreach ($archiveResultMarker in @(
        '_confirmed_archives',
        '_invalid_completed_result',
        'expected_thread_id',
        'completed_may_have_started=True',
        'thread_id in archives',
        'Desktop Gateway reported an unrequested task as archived',
        'without an authoritative final answer'
    )) {
        if ($clientText -notmatch [regex]::Escape($archiveResultMarker)) {
            throw "Desktop Gateway client is missing completed-result validation: $archiveResultMarker"
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
        $wireText = $queueHelperText + "`n" + (Get-Content -LiteralPath (Join-Path $skillRoot 'assets\desktop-gateway-task.md') -Raw)
        if ($wireText -notmatch [regex]::Escape($unicodeWireMarker)) {
            throw "Queue helper is missing Unicode-safe stdout contract marker: $unicodeWireMarker"
        }
    }
    foreach ($environmentMarker in @(
        'Get-BridgeEnvSemanticIssues',
        'Assert-BridgeEnvSemantics',
        'CODEX_BRIDGE_ALLOW_PROJECT_CREATE',
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
        'WaitOne(0)',
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
        if ($routerText -notmatch [regex]::Escape($receiptMarker)) {
            throw "Desktop Gateway queue is missing durable terminal-receipt handling: $receiptMarker"
        }
    }
    foreach ($canonicalClaimMarker in @(
        'def _actionable_pending_paths',
        'immutable, stable publication anchor',
        '_atomic_write_json_exclusive(target, claimed_request)',
        'if self._terminal_result_exists(source.stem)'
    )) {
        if ($routerText -notmatch [regex]::Escape($canonicalClaimMarker)) {
            throw "Desktop Gateway queue is missing canonical/fenced claim publication: $canonicalClaimMarker"
        }
    }
    $expireClaimsFunction = [regex]::Match(
        $routerText,
        '(?ms)^    def expire_stale_claims\(.*?^    def cleanup\('
    )
    $liveWakeProtectionIndex = $expireClaimsFunction.Value.IndexOf('if router_ready:')
    $claimReadIndex = $expireClaimsFunction.Value.IndexOf('request = _read_json(path)')
    if (-not $expireClaimsFunction.Success -or $liveWakeProtectionIndex -lt 0 -or
        $claimReadIndex -lt 0 -or $liveWakeProtectionIndex -gt $claimReadIndex) {
        throw 'Stale-claim maintenance must protect a live owner before terminalizing a legacy or damaged unfenced claim.'
    }
    foreach ($readClaimMarker in @(
        'read_claim_ttl_seconds: int = 300',
        'self.read_claim_ttl_seconds = min(',
        'if operation in READ_ONLY_OPERATIONS',
        'else self.claim_ttl_seconds'
    )) {
        if ($routerText -notmatch [regex]::Escape($readClaimMarker)) {
            throw "Desktop Gateway queue is missing the bounded read-only claim TTL: $readClaimMarker"
        }
    }
    foreach ($mutationUnknownMarker in @(
        'project task creation outcome unknown',
        'Codex thread creation outcome unknown',
        'Codex compaction outcome unknown',
        '_handle_command_gateway_error',
        'self.state.mark_retryable(event_id, str(exc))',
        'pending_project_request_key',
        'pending_request_key != request_key',
        'pending_name != project_name',
        'validate_staged_project_root'
    )) {
        if ($runtimeText -notmatch [regex]::Escape($mutationUnknownMarker)) {
            throw "Bridge runtime is missing mutation-unknown handling: $mutationUnknownMarker"
        }
    }
    foreach ($clientMutationUnknownMarker in @(
        'Desktop Gateway queue state conflicts with a possibly-started target action',
        'Desktop Gateway queue state conflicts with a possibly-started steer action'
    )) {
        if ($clientText -notmatch [regex]::Escape($clientMutationUnknownMarker)) {
            throw "Desktop Gateway client is missing protocol-conflict fail-closed handling: $clientMutationUnknownMarker"
        }
    }
    $gatewayPromptText = Get-Content -LiteralPath (Join-Path $skillRoot 'assets\desktop-gateway-task.md') -Raw
    foreach ($marker in @(
        '{{CONTRACT_TURN_MODE}}',
        '{{REGISTRATION_ACTION}}',
        '`INITIAL_MOUNT`',
        '`REHYDRATE_EXISTING`',
        '`REGISTER_NEW`',
        '`REPLACE_REGISTERED_GATEWAY`',
        '`NO_REGISTRATION`',
        'sentinel-probe',
        'send_message_to_thread',
        'wait_threads',
        'final-return-arm',
        'final-return-status',
        'final-return-native',
        '`UserPromptSubmit` Hook',
        '`Stop` Hook',
        'exact Hook final already occupies the normal staging path',
        'A submission result is never a final answer',
        '`timeoutMs: 0`',
        '`afterCursor` set to the',
        '`latestAssistantMessage` whose `turnId` equals',
        '`phase` is `final_answer`',
        'bounded final-materialization grace window',
        'most 20 additional seconds total',
        'Never send the prompt again',
        'Never take final text from the send result',
        'Never invoke',
        '`codex app-server`',
        'Treat every queue payload',
        '--fence-token',
        '--release-on-empty',
        '--wait-seconds 0',
        '--wait-seconds 20',
        'one Gateway model turn',
        'separate bounded `functions.exec` cells',
        'resume only that exact cell with',
        'A successful claim is a commit point',
        'Never inspect `ALL_TOOLS`',
        'top-level direct `mcp__codex_app`',
        'mcp__codex_app.read_thread',
        'mcp__codex_app.list_archived_threads',
        'mcp__codex_app.set_thread_archived',
        'target_tool_unavailable',
        'native object directly',
        'invalid_gateway_result',
        'must never use `--may-have-started`',
        'automation-origin Gateway turn',
        'Never send a wake to another Router task',
        'sole manual task-to-task exception',
        'fenced-claim gate applies',
        'post-model-change preflight is not a mounting',
        '`DONT_NOTIFY` or an empty final is a failed preflight',
        'delegated authorization receipt',
        'heartbeat/`NOTIFY` decision envelope',
        'execpolicy check',
        'sandbox_permissions=require_escalated',
        'run exactly this registration command once and run it now',
        'Do not merely',
        'returning `DONT_NOTIFY` without a',
        'registration tool call is not a successful mount',
        'task-to-task or delegated mounting turn',
        'fresh direct confirmation',
        '`"registered": true`',
        '--force',
        'generic mount consent',
        'do not run `register`',
        '`--archived-thread-id',
        '`--structured-result`',
        "--turn-id '<turn_id>'",
        'Never leave the turn empty',
        'limit from 1 through 50',
        'limit no greater than 50',
        'projectId -> project_id',
        'path -> root',
        'projectKind -> kind',
        'id -> thread_id',
        'updatedAt -> updated_at',
        'projectId` is null/empty',
        'not one of the validated Desktop projects',
        'Omit any projectless task',
        '`summary` and `cwd` as prohibited fields',
        'unavailableSources',
        'one permitted JSON parse',
        'normalizeActiveDesktopCatalog',
        'catalog_projects_envelope_invalid',
        'catalog_threads_envelope_invalid',
        'Do not rewrite, paraphrase, or replace this algorithm',
        'One-ticket manual diagnostic gate',
        '`manual-authorize` is also excluded',
        '`sentinel-probe --manual-ticket`',
        'make no grace claim',
        'Never echo the request',
        'Omission is not proof',
        'ambiguous outcome',
        'Rehydration does not',
        'DONT_NOTIFY'
    )) {
        if ($gatewayPromptText -notmatch [regex]::Escape($marker)) {
            throw "Desktop Gateway task prompt is missing safety marker: $marker"
        }
    }
    $manualCyclePromptText = Get-Content -LiteralPath (Join-Path $skillRoot 'assets\desktop-gateway-manual-cycle.md') -Raw
    foreach ($marker in @(
        'MANUAL_DIAGNOSTIC_CYCLE_V1',
        '{{GATEWAY_THREAD_ID}}',
        '{{HOST_ID}}',
        '{{EXPECTED_OPERATION}}',
        '{{MANUAL_TICKET}}',
        '{{BRIDGE_VERSION}}',
        '{{PYTHON}}',
        '{{RUNTIME_DIR}}',
        '{{OPERATION_CONTRACT}}',
        'sentinel-probe --router-thread-id',
        '--manual-ticket',
        'manual_ticket_consumed=true',
        'make exactly one zero-wait claim',
        'A successful claim is a commit point',
        'top-level direct `mcp__codex_app` tool calls',
        'Never invoke a Desktop task method from `functions.exec`',
        'separate short `functions.exec` cells',
        'Do not make the 20-second grace claim',
        'manual_single_request',
        'Source-exact operation contract',
        'may not retain an earlier rehydration prompt',
        'model-authored operation logic',
        'refresh scheduler freshness',
        'DONT_NOTIFY'
    )) {
        if ($manualCyclePromptText -notmatch [regex]::Escape($marker)) {
            throw "Desktop Gateway manual-cycle template is missing marker: $marker"
        }
    }
    $manualRendererText = Get-Content -LiteralPath (Join-Path $skillRoot 'scripts\render_gateway_manual_cycle.py') -Raw
    foreach ($marker in @(
        'ALLOWED_MANUAL_OPERATIONS',
        '_extract_operation_contract',
        '{{OPERATION_CONTRACT}}',
        '## Complete or fail',
        'unresolved Gateway prompt placeholders'
    )) {
        if ($manualRendererText -notmatch [regex]::Escape($marker)) {
            throw "Desktop Gateway manual-cycle renderer is missing marker: $marker"
        }
    }
    $gatewayBootstrapText = Get-Content -LiteralPath (Join-Path $skillRoot 'assets\desktop-gateway-bootstrap.md') -Raw
    foreach ($marker in @(
        'read-only capability preflight',
        'Omit model and reasoning overrides',
        'top-level `mcp__codex_app` server',
        'mcp__codex_app.list_threads',
        'mcp__codex_app.list_projects',
        'compatible_for_mount_preflight',
        'direct_mcp_invoked',
        'list_projects_invoked',
        'explicit limit no greater than 50',
        'Do not call either method through `functions.exec`',
        'compact `wait_threads` result only as a',
        'exact final with `read_thread`',
        'does not certify scheduled automation-origin tool availability'
    )) {
        if ($gatewayBootstrapText -notmatch [regex]::Escape($marker)) {
            throw "Desktop Gateway bootstrap template is missing marker: $marker"
        }
    }
    $gatewayModelPreflightText = Get-Content -LiteralPath (Join-Path $skillRoot 'assets\desktop-gateway-model-preflight.md') -Raw
    foreach ($marker in @(
        'post-model-change capability preflight',
        'exact existing registered Feishu Desktop Gateway',
        'manual task-to-task turn',
        'sole manual task-to-task exception',
        'fenced-claim gate applies to routed work',
        'Do not finish with `DONT_NOTIFY` or an empty response',
        'top-level `mcp__codex_app` server',
        'mcp__codex_app.list_threads',
        'mcp__codex_app.list_projects',
        'compatible_for_model_canary',
        'direct_mcp_invoked',
        'list_projects_invoked',
        'explicit limit no greater than 50',
        'Do not use this preflight to retry',
        'not proof of a new official Desktop surface',
        'does not certify scheduled automation-origin tool availability',
        'or authorize scheduler activation'
    )) {
        if ($gatewayModelPreflightText -notmatch [regex]::Escape($marker)) {
            throw "Desktop Gateway model preflight template is missing marker: $marker"
        }
    }
    $heartbeatPromptText = Get-Content -LiteralPath (Join-Path $skillRoot 'assets\desktop-gateway-heartbeat.md') -Raw
    foreach ($marker in @(
        'sentinel-probe',
        'DONT_NOTIFY',
        'existing dedicated Gateway task',
        'every two minutes',
        'capped at no more than three runs',
        'scheduler_cap_unenforced',
        'requested limit capped at 50',
        'Only a successful live `/init` catalog-and-selection canary unlocks',
        'two separate later approvals',
        'same surface until a different',
        'task changes are not sufficient',
        'full contract already mounted in this exact existing task',
        'targetThreadId',
        'persist the new automation as `ACTIVE`',
        'immediately issue a full update',
        'unexpected automation-origin turn',
        'same automation-origin turn',
        'unsupported Sentinel-to-Router delegated hop',
        'controlling task verified fresh owner approval',
        'delegated authorization receipt',
        'heartbeat/`NOTIFY` decision envelope',
        'execpolicy check',
        'Never wake or message another Router or Sentinel task',
        'One scheduler model turn owns the complete cycle',
        'separate bounded `functions.exec` cells',
        'resume only that exact cell with `functions.wait`',
        'A successful claim is a commit point',
        'Never call a Desktop app tool through `functions.exec`',
        'mcp__codex_app.list_threads',
        'mcp__codex_app.list_archived_threads',
        'mcp__codex_app.read_thread',
        'mcp__codex_app.list_projects',
        'mcp__codex_app.create_thread',
        'mcp__codex_app.send_message_to_thread',
        'mcp__codex_app.wait_threads',
        'mcp__codex_app.set_thread_archived',
        'zero-time exact-target direct `mcp__codex_app.wait_threads` baseline cursor',
        '`final-return-arm`',
        '`final-return-status`',
        '`final-return-native`',
        '`UserPromptSubmit` and `Stop` MCP Hooks',
        '`afterCursor` equal to the baseline',
        '`phase=final_answer`',
        'final-materialization grace of at most 20 additional seconds',
        'never re-sending',
        'Never use the send result, baseline message, `read_thread`',
        'normalize the direct `mcp__codex_app.read_thread`',
        'never `target_result_unknown --may-have-started`'
    )) {
        if ($heartbeatPromptText -notmatch [regex]::Escape($marker)) {
            throw "Desktop Gateway heartbeat template is missing marker: $marker"
        }
    }
    foreach ($legacyMarker in @(
        'Immediately use `functions.exec` to invoke one metadata-only `sentinel-probe`'
    )) {
        if ($heartbeatPromptText -match [regex]::Escape($legacyMarker)) {
            throw "Desktop Gateway heartbeat template retains split-cycle wording: $legacyMarker"
        }
    }
    $routerRuleText = Get-Content -LiteralPath (Join-Path $skillRoot 'assets\feishu-router.rules.template') -Raw
    foreach ($marker in @(
        'prefix_rule(',
        '{{PYTHON_RULE_PATH}}',
        '{{ROUTER_QUEUE_RULE_PATH}}',
        '{{RUNTIME_DIR_RULE_PATH}}',
        '"sentinel-probe"',
        '"claim"',
        '"release"',
        '"heartbeat"',
        '"final-return-arm"',
        '"final-return-status"',
        '"final-return-native"',
        '"stage-path"',
        '"complete"',
        '"fail"',
        'match = [',
        'not_match = [',
        'Deliberately omit `register`',
        'decision = "allow"'
    )) {
        if ($routerRuleText -notmatch [regex]::Escape($marker)) {
            throw "Desktop Gateway allow-rule template is missing marker: $marker"
        }
    }
    if (([regex]::Matches($routerRuleText, [regex]::Escape('prefix_rule('))).Count -ne 10) {
        throw 'Desktop Gateway allow-rule template must contain exactly ten command-specific rules.'
    }
    if (([regex]::Matches($routerRuleText, '(?m)^\s*match\s*=\s*\[')).Count -ne 10) {
        throw 'Every Desktop Gateway allow rule must contain an inline match test.'
    }
    if (([regex]::Matches($routerRuleText, '(?m)^\s*not_match\s*=\s*\[')).Count -ne 10) {
        throw 'Every Desktop Gateway allow rule must contain an inline not_match test.'
    }
    foreach ($forbiddenRuleCommand in @('"register"', '"status"', '"manual-authorize"', '"final-return-hook"', '"final-return-register"', '"final-return-unregister"')) {
        if ($routerRuleText -match [regex]::Escape($forbiddenRuleCommand)) {
            throw "Desktop Gateway allow-rule template must not auto-allow: $forbiddenRuleCommand"
        }
    }
    $stateText = Get-Content -LiteralPath (Join-Path $skillRoot 'scripts\bridge_core\state.py') -Raw
    foreach ($marker in @('related_thread_ids', 'clear_expired_init_wizards', 'replace_thread', 'record_project_route', 'sync_active_project')) {
        if ($stateText -notmatch [regex]::Escape($marker)) {
            throw "Bridge state is missing conversational wizard state marker: $marker"
        }
    }
    $runtimeText = Get-Content -LiteralPath (Join-Path $skillRoot 'scripts\bridge_core\runtime.py') -Raw
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
    foreach ($wizardMarker in @('INIT_WIZARD_TTL_SECONDS', 'UNSUPPORTED_COMMAND_REPLY', '_init_wizards', 'init_wizard_expires_at', '_begin_init_wizard', '_handle_init_wizard_reply', 'list_task_catalog')) {
        if ($runtimeText -notmatch [regex]::Escape($wizardMarker)) {
            throw "Bridge runtime is missing conversational /init marker: $wizardMarker"
        }
    }
    foreach ($removedCommandImplementation in @('_help_answer', '_reuse_previous_thread', '_handle_new_request', '_process_control_event', 'decide_new_intent', '_handle_project_command', '_project_use')) {
        if ($runtimeText -match [regex]::Escape($removedCommandImplementation)) {
            throw "Bridge runtime retains an obsolete command implementation: $removedCommandImplementation"
        }
    }
    if ($runtimeText -match 'sessions\.update\([^\r\n]+\{\s*["'']init_wizard["'']') {
        throw 'Bridge runtime must not persist the /init catalog snapshot in sessions.json.'
    }
    foreach ($projectMarker in @('_project_new', 'pending_project_request_key', 'resolve_new_project_root', 'allow_project_create')) {
        if ($runtimeText -notmatch [regex]::Escape($projectMarker)) {
            throw "Bridge runtime is missing project-routing marker: $projectMarker"
        }
    }
    $larkText = Get-Content -LiteralPath (Join-Path $skillRoot 'scripts\bridge_core\lark.py') -Raw
    foreach ($richReplyMarker in @('_markdown_post_content', '"--msg-type", "post", "--content"', 'one ``md`` node')) {
        if ($larkText -notmatch [regex]::Escape($richReplyMarker)) {
            throw "Feishu adapter is missing rich-reply compatibility marker: $richReplyMarker"
        }
    }
    $configText = Get-Content -LiteralPath (Join-Path $skillRoot 'scripts\bridge_core\config.py') -Raw
    if ($configText -notmatch [regex]::Escape('BRIDGE_VERSION = "4.2.0-alpha.30"')) {
        throw 'Bridge version marker is not 4.2.0-alpha.30.'
    }
    foreach ($accessDefaultMarker in @(
        '"CODEX_BRIDGE_ACCESS_MODE": ("locked"',
        'access_mode = _enum_env("CODEX_BRIDGE_ACCESS_MODE")',
        'semantic_issues = validate_bridge_env_values(os.environ)'
    )) {
        if ($configText -notmatch [regex]::Escape($accessDefaultMarker)) {
            throw "Bridge config is missing locked/fail-closed access default: $accessDefaultMarker"
        }
    }
    foreach ($wakeMarker in @('CODEX_BRIDGE_ROUTER_WAKE_TTL', 'CODEX_BRIDGE_GATEWAY_SCHEDULER_TTL', 'CODEX_BRIDGE_ROUTER_GRACE_MAX_SECONDS')) {
        if ($configText -notmatch [regex]::Escape($wakeMarker)) {
            throw "Bridge config is missing on-demand wake marker: $wakeMarker"
        }
        if ($installerText -notmatch [regex]::Escape($wakeMarker)) {
            throw "Installer is missing on-demand wake default: $wakeMarker"
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
    $skillText = Get-Content -LiteralPath (Join-Path $skillRoot 'SKILL.md') -Raw
    if ($skillText -notmatch '(?ms)^---\s*\r?\nname:\s*feishu-codex-bridge\s*\r?\ndescription:\s*.+?\r?\n---') {
        throw 'SKILL.md frontmatter is missing required name/description fields.'
    }
    foreach ($canaryMarker in @(
        'completes `/init`',
        'catalog without selection',
        'completed count is not success',
        'bridge access -AccessMode locked',
        'Require exact-scope binding',
        'leave it paused/completed',
        'Production then requires two later'
    )) {
        if ($skillText -notmatch [regex]::Escape($canaryMarker)) {
            throw "SKILL.md is missing live /init canary marker: $canaryMarker"
        }
    }
    foreach ($bootstrapMarker in @(
        'Run read-only `bridge preflight`',
        'One disclosed onboarding approval',
        'lark-cli config init --new',
        'auth login --recommend',
        'runnable official Codex CLI',
        'binary is not that CLI',
        'auth status --json --verify',
        'three independent results'
    )) {
        if ($skillText -notmatch [regex]::Escape($bootstrapMarker)) {
            throw "SKILL.md is missing first-use dependency bootstrap marker: $bootstrapMarker"
        }
    }
    foreach ($approvalCompressionMarker in @(
        '### Approval compression',
        'read-only postcondition checks',
        'shell quoting or transport syntax',
        'generic `continue` or `next step` prompts',
        'standing consent for later actions',
        'it never bundles separate client impact'
    )) {
        if ($skillText -notmatch [regex]::Escape($approvalCompressionMarker)) {
            throw "SKILL.md is missing approval-compression marker: $approvalCompressionMarker"
        }
    }
    foreach ($p0PriorityMarker in @(
        'P0: exact Codex final reply return to Feishu',
        'P2: future official Desktop build compatibility',
        'feishu-codex-final-return',
        'bridge final-return-status',
        'bridge final-return-register',
        'UserPromptSubmit',
        'Stop'
    )) {
        if ($skillText -notmatch [regex]::Escape($p0PriorityMarker)) {
            throw "SKILL.md is missing P0 reply-return priority marker: $p0PriorityMarker"
        }
    }
    foreach ($diagnosticContractMarker in @(
        '`bridge status -Json`',
        '`bridge doctor -Json`',
        '`bridge validate -Json`',
        '`schema_version=1`',
        'one compact JSON object',
        'Never include Feishu or Codex task IDs'
    )) {
        if ($skillText -notmatch [regex]::Escape($diagnosticContractMarker)) {
            throw "SKILL.md is missing machine-readable diagnostic marker: $diagnosticContractMarker"
        }
    }
    $hookPermissionText = Get-Content -LiteralPath (Join-Path $skillRoot 'references\permissions-and-hooks.md') -Raw
    foreach ($hookPermissionMarker in @(
        'independently runnable Codex CLI',
        'npm install -g @openai/codex',
        'must not run',
        'WindowsApps ACLs',
        'decision: allow'
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
        'Only a positively different official build/surface may begin a new',
        'UserPromptSubmit',
        'Stop',
        'feishu-codex-final-return'
    )) {
        if ($hookPermissionText -notmatch [regex]::Escape($lifecycleMarker)) {
            throw "Permissions/hooks reference is missing lifecycle/canary marker: $lifecycleMarker"
        }
    }
    foreach ($frontendSkillMarker in @(
        'Optional Feishu frontend takeover',
        'Do not run client detection',
        'feishu desktop-status',
        'feishu desktop-install -DesktopInstallConsent',
        'stop so the user can scan or authenticate'
    )) {
        if ($skillText -notmatch [regex]::Escape($frontendSkillMarker)) {
            throw "SKILL.md is missing conditional frontend-takeover marker: $frontendSkillMarker"
        }
    }
    $desktopReferenceText = Get-Content -LiteralPath (Join-Path $skillRoot 'references\feishu-desktop-client.md') -Raw
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
        'QR user authorization does not replace',
        'Bot tenant-scope audit',
        'administrator approval for Bot scopes',
        'user OAuth'
    )) {
        if ($skillText -notmatch [regex]::Escape($permissionMarker)) {
            throw "SKILL.md is missing common permission profile marker: $permissionMarker"
        }
    }
    $permissionProfileText = Get-Content -LiteralPath (Join-Path $skillRoot 'references\openclaw-common-chat-permissions.md') -Raw
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
    $agentsFragment = Get-Content -LiteralPath (Join-Path $skillRoot 'assets\AGENTS.feishu-codex-bridge.md') -Raw
    foreach ($marker in @('<!-- FEISHU_CODEX_BRIDGE_RULES_START -->', '<!-- FEISHU_CODEX_BRIDGE_RULES_END -->')) {
        if ([regex]::Matches($agentsFragment, [regex]::Escape($marker)).Count -ne 1) {
            throw "Managed AGENTS.md fragment must contain exactly one marker: $marker"
        }
    }
    foreach ($codexCliMarker in @(
        'Program Files\WindowsApps\OpenAI.Codex_*\app\resources',
        'installed `@openai/codex` shim and package',
        'Never change WindowsApps ACLs',
        'Every `/hooks` or `execpolicy check`'
    )) {
        if ($agentsFragment -notmatch [regex]::Escape($codexCliMarker)) {
            throw "Managed AGENTS.md fragment is missing independent Codex CLI marker: $codexCliMarker"
        }
    }
    foreach ($marker in @(
        'delegated authorization receipt',
        'heartbeat/`NOTIFY` decision envelope',
        'manual or task-to-task copy of the scheduler prompt is not authorized',
        'The first `bridge install` is one explicitly disclosed bootstrap action',
        'public `bridge upgrade` is runtime-only',
        'Fresh installs write locked and a missing access key remains locked',
        'or empty recognized boolean, enum, or integer values refuse startup',
        'Return only IDs whose',
        'next deterministic request generation',
        'Manual start and restart require current source/runtime parity',
        'scheduler_cap_unenforced',
        'limit no greater than 50',
        'source-defined one-ticket',
        '`manual-authorize`',
        '`sentinel-probe --manual-ticket`'
    )) {
        if ($agentsFragment -notmatch [regex]::Escape($marker)) {
            throw "Managed AGENTS.md fragment is missing Gateway authorization marker: $marker"
        }
    }
    $interfaceText = Get-Content -LiteralPath (Join-Path $skillRoot 'agents\openai.yaml') -Raw
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

function Get-CodexDesktopBuildState {
    param([string]$ExplicitBuild)

    if ($ExplicitBuild) {
        return [pscustomobject][ordered]@{
            build = $ExplicitBuild
            detection = 'explicit'
            error = $null
        }
    }

    $candidates = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    $detections = New-Object System.Collections.Generic.List[string]
    $packageSegmentPattern = '(?i)^OpenAI\.Codex_(?<build>\d+(?:\.\d+){3})_'
    $pathSeparatorPattern = [regex]::Escape([string][System.IO.Path]::DirectorySeparatorChar)

    foreach ($process in @(Get-Process -Name 'ChatGPT' -ErrorAction SilentlyContinue)) {
        try {
            $path = [string]$process.Path
            foreach ($segment in @($path -split $pathSeparatorPattern)) {
                if ($segment -match $packageSegmentPattern) {
                    if ($candidates.Add($Matches['build'])) { $detections.Add('process_path') }
                    break
                }
            }
        } catch {
            # A protected process path is not evidence that the build changed.
        }
    }

    if ($candidates.Count -eq 0) {
        try {
            foreach ($process in @(Get-CimInstance Win32_Process -ErrorAction Stop)) {
                foreach ($candidateText in @([string]$process.ExecutablePath, [string]$process.CommandLine)) {
                    foreach ($segment in @($candidateText -split $pathSeparatorPattern)) {
                        if ($segment -match $packageSegmentPattern) {
                            if ($candidates.Add($Matches['build'])) { $detections.Add('win32_process') }
                            break
                        }
                    }
                }
            }
        } catch {
            # Callers may supply -DesktopBuild from a separately approved read-only
            # process query when this execution surface cannot access Win32_Process.
        }
    }

    if ($candidates.Count -eq 0 -and (Get-Command Get-AppxPackage -ErrorAction SilentlyContinue)) {
        try {
            foreach ($package in @(Get-AppxPackage -Name 'OpenAI.Codex' -ErrorAction Stop)) {
                $version = [string]$package.Version
                if ($version -match '^\d+(?:\.\d+){3}$' -and $candidates.Add($version)) {
                    $detections.Add('appx_registration')
                }
            }
        } catch {
            # Registration discovery is a read-only fallback; absence is unknown.
        }
    }

    $builds = @($candidates | Sort-Object)
    if ($builds.Count -eq 1) {
        return [pscustomobject][ordered]@{
            build = $builds[0]
            detection = $(if ($detections.Count -gt 0) { $detections[0] } else { 'detected' })
            error = $null
        }
    }
    if ($builds.Count -gt 1) {
        return [pscustomobject][ordered]@{
            build = $null
            detection = 'conflict'
            error = 'multiple_running_desktop_builds'
        }
    }
    return [pscustomobject][ordered]@{
        build = $null
        detection = 'unavailable'
        error = 'desktop_build_unavailable'
    }
}

function Get-DesktopGatewayCompatibilityMarkers {
    # Build-level findings contain no task, Feishu scope, local path, or payload.
    # Add a build only after a genuine automation-origin canary proves a
    # fail-closed surface defect with no target mutation, either through an
    # authoritative terminal result or an observed scheduler hard-cap breach.
    return @(
        [pscustomobject][ordered]@{
            desktop_build = '26.818.5229.0'
            surface = 'windows_desktop_heartbeat_automation_origin'
            outcome = 'incompatible'
            terminal_code = 'target_tool_unavailable'
            may_have_started = $false
            evidence_kind = 'live_claimed_terminal'
        }
        [pscustomobject][ordered]@{
            desktop_build = '26.818.8289.0'
            surface = 'windows_desktop_heartbeat_automation_origin'
            outcome = 'incompatible'
            terminal_code = 'scheduler_cap_unenforced'
            may_have_started = $false
            evidence_kind = 'live_scheduler_run_count'
            declared_run_cap = 3
            observed_run_count = 4
            capability_findings = @(
                'scheduler_cap_unenforced'
                'target_final_readback_unavailable'
            )
            blocked_manual_operations = @('send_message_to_thread')
            target_input_transport = 'verified'
            target_context_continuity = 'verified_two_turn'
            target_final_return = 'unavailable'
        }
    )
}

function Invoke-BridgeCanaryGate {
    $buildState = Get-CodexDesktopBuildState -ExplicitBuild $DesktopBuild
    $result = [ordered]@{
        schema_version = 1
        status = 'unknown'
        eligible_for_fresh_preflight = $false
        desktop_build = $buildState.build
        detection = $buildState.detection
        reason = $buildState.error
        marker = $null
    }
    $script:BridgeCanaryGateExitCode = 4

    if ($buildState.build) {
        $marker = @(
            Get-DesktopGatewayCompatibilityMarkers |
                Where-Object { $_.desktop_build -eq $buildState.build }
        ) | Select-Object -First 1
        if ($marker) {
            $result.status = 'blocked'
            $result.reason = 'known_surface_incompatibility'
            $result.marker = $marker
            $script:BridgeCanaryGateExitCode = 3
        } else {
            $result.status = 'pass'
            $result.eligible_for_fresh_preflight = $true
            $result.reason = 'unmarked_build_requires_fresh_preflight'
            $script:BridgeCanaryGateExitCode = 0
        }
    }

    $result | ConvertTo-Json -Depth 6 -Compress | Write-Output
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
        Write-Output '[INFO] Listener runtime is installed; use bridge doctor to inspect parity and health.'
    } else {
        Write-Output '[PENDING] Listener runtime is not installed. Preflight does not install it.'
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
        Write-Output '[NEXT] Ask for explicit approval to install the listed missing prerequisites from official sources, then rerun preflight. This is not Listener mount consent.'
    }
    if ($pathDiscoveryFallback) {
        Write-Output '[INFO] Do not claim global PATH availability. Use the Bridge wrapper and exact verified executable paths; start fresh-shell checks with the current shell executable absolute path.'
    }
    Write-Output '[MANUAL] In Codex Desktop, verify list/read/create/send/wait/archive task tools and automation support before mounting a Gateway.'
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
    Assert-BridgeListenerStopped
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

function Invoke-BridgeProjectsConfig {
    if (-not $ProjectCreate) {
        throw 'ProjectCreate is required: on or off.'
    }
    if ($ProjectsRoot) {
        $resolvedContainer = (Resolve-Path -LiteralPath $ProjectsRoot).Path
        if (-not (Test-Path -LiteralPath $resolvedContainer -PathType Container)) {
            throw "ProjectsRoot must be an existing directory: $resolvedContainer"
        }
        $resolvedProject = Resolve-Project
        $projectPrefix = $resolvedProject.TrimEnd('\') + '\'
        if (
            $resolvedContainer.Equals($resolvedProject, [System.StringComparison]::OrdinalIgnoreCase) -or
            $resolvedContainer.StartsWith($projectPrefix, [System.StringComparison]::OrdinalIgnoreCase)
        ) {
            throw 'ProjectsRoot must be outside the bridge project so new project files cannot mix into it.'
        }
        Set-BridgeEnvValue -Name 'CODEX_BRIDGE_PROJECTS_ROOT' -Value $resolvedContainer
    }
    $value = if ($ProjectCreate -eq 'on') { '1' } else { '0' }
    Set-BridgeEnvValue -Name 'CODEX_BRIDGE_ALLOW_PROJECT_CREATE' -Value $value
    if ($ProjectCreate -eq 'on') {
        Write-Warning 'Enabled the explicit owner/admin new-project action inside /init. Each confirmed request may create one direct child directory and one persisted Codex task.'
    } else {
        Write-Output 'Disabled project creation from Feishu. Existing project routes remain switchable.'
    }
    Write-Output 'Project routing configuration updated. Restart the bridge separately to apply it.'
}

function Invoke-FinalReturnRegistryHelper {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet('final-return-registry-status', 'final-return-register', 'final-return-unregister')]
        [string]$Command
    )

    $paths = Get-BridgePaths
    $helper = Join-Path $paths.Runtime 'router_queue.py'
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

    $helperSupportsFinalReturn = $false
    if (Test-Path -LiteralPath $helper -PathType Leaf) {
        try {
            $helperText = Get-Content -LiteralPath $helper -Raw -ErrorAction Stop
            $helperSupportsFinalReturn = $helperText.Contains(
                'subcommands.add_parser("final-return-registry-status")'
            )
        } catch {
            throw 'The installed Bridge final-return helper could not be inspected.'
        }
    }
    if (-not $helperSupportsFinalReturn) {
        if ($Command -eq 'final-return-registry-status') {
            Write-Output (ConvertTo-Json -Compress -InputObject ([ordered]@{
                schema_version = 1
                command = 'bridge.final-return-status'
                status = 'upgrade_required'
                installed_bridge_version = $installedVersion
                required_runtime_capability = 'p0_exact_final_return'
            }))
            return
        }
        throw 'The installed Bridge runtime predates P0 exact final-return registration; approve and run bridge upgrade first.'
    }
    if ($Command -eq 'final-return-register') {
        $manifestIssues = @(Get-InstalledBridgeManifestIssues)
        if ($manifestIssues.Count -gt 0) {
            throw (
                'Final-return registration requires a valid installed runtime manifest: ' +
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
        throw 'Python 3.10+ is required for final-return registry management.'
    }

    & $python.Source @pythonPrefix -S -B $helper --runtime-dir $paths.Runtime $Command
    if ($LASTEXITCODE -ne 0) {
        throw "Final-return registry helper failed with exit code $LASTEXITCODE."
    }
}

$scopeName = $Scope.ToLowerInvariant()
$actionName = $Action.ToLowerInvariant()

if ($Json -and -not ($scopeName -eq 'bridge' -and $actionName -in @('status', 'doctor', 'validate'))) {
    throw '-Json is supported only for bridge status, bridge doctor, and bridge validate.'
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
            'upgrade' { Invoke-Installer -Upgrade; Write-Output 'Upgrade installed. After separate approval, run bridge restart to activate it.' }
            'hooks' { Invoke-BridgeHooksRefresh }
            'canary-gate' {
                Invoke-BridgeCanaryGate
                exit $script:BridgeCanaryGateExitCode
            }
            'final-return-status' {
                Invoke-FinalReturnRegistryHelper -Command 'final-return-registry-status'
            }
            'final-return-register' {
                Invoke-FinalReturnRegistryHelper -Command 'final-return-register'
            }
            'final-return-unregister' {
                Invoke-FinalReturnRegistryHelper -Command 'final-return-unregister'
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
            'projects' { Invoke-BridgeProjectsConfig }
            default { Show-Usage; throw "Unknown bridge subcommand: $Action" }
        }
    }
    default { Show-Usage; throw "Unknown command scope: $Scope" }
}
