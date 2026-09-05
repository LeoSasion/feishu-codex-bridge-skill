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

    [string]$BeeperThreadId,

    [string]$AuthScope,

    [string]$AuthDomain,

    [switch]$Recommend,

    [string]$DeviceCode,

    [switch]$NoWait,

    [int]$Tail = 80,

    [switch]$Json,

    [switch]$RunTests
)

$ErrorActionPreference = 'Stop'

function Show-Usage {
    @'
Feishu Codex Operator

Usage:
  feishu-codex-operator.ps1 feishu install|configure|doctor
  feishu-codex-operator.ps1 feishu desktop-status
  feishu-codex-operator.ps1 feishu desktop-install
  feishu-codex-operator.ps1 feishu login -Recommend -NoWait
  feishu-codex-operator.ps1 feishu login -AuthScope <scope> -NoWait
  feishu-codex-operator.ps1 feishu login -AuthDomain <domain> -NoWait
  feishu-codex-operator.ps1 feishu login -DeviceCode <device_code>
  feishu-codex-operator.ps1 operator init|install|start|stop|restart
  feishu-codex-operator.ps1 operator hooks
  feishu-codex-operator.ps1 operator upgrade [-BeeperThreadId <task_uuid>]
  feishu-codex-operator.ps1 operator beeper-configure -BeeperThreadId <task_uuid>
  feishu-codex-operator.ps1 operator final-callback-status
  feishu-codex-operator.ps1 operator final-callback-register
  feishu-codex-operator.ps1 operator final-callback-unregister
  feishu-codex-operator.ps1 operator status|doctor|readiness|validate [-Json]
  feishu-codex-operator.ps1 operator preflight|logs|test
  feishu-codex-operator.ps1 operator test -RunTests
  feishu-codex-operator.ps1 operator access -AccessMode locked -OwnerOpenId <open_id>
  feishu-codex-operator.ps1 doctor

Knowledge bases, including Obsidian vaults, belong to the bound Codex project's
directory. The operator has no knowledge-base command or setting.
'@ | Write-Output
}

function Show-WelcomeAndAutomaticWorkflow {
    @'
欢迎使用 Codex 飞书机器人。

飞书 CLI 安装完成后，可以把私聊和群聊 @ 消息挂载到当前 Codex 项目。每个私聊、群聊或群话题都能映射到一个可在 Codex Desktop 查看和继续的持久会话。

首次消息会提示发送 /init，随后通过对话菜单查看并选择一个现有、未归档的 Codex Desktop 任务。当前版本不创建、恢复、归档或压缩任务。Operator 把普通消息 queue 到固定的最小 Beeper；Beeper 只向精确绑定的 Responder 中继一次。业务执行与最终答案始终由该 Responder 所有。默认只回传 Final Callback 的最终答案，不发送思考或工具过程。

Operator 挂载会在当前项目写入桥接运行文件和 Codex hooks，也不会替用户授予飞书权限。/init 的独立 App Server 只按需执行 thread/list 和 includeTurns=false 的 thread/read，不创建 Desktop 查询对话。Responder 自己的模型、推理、沙箱、插件和知识库设置保持不变；Operator 不安装、注册或检索 Obsidian。

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

function Get-OperatorProcessIdentity {
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [Parameter(Mandatory = $true)][string]$OperatorScript
    )
    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if (-not $process) {
        return [pscustomobject]@{
            Exists = $false
            Verified = $true
            IsOperator = $false
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
            IsOperator = $false
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
            IsOperator = $false
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
            IsOperator = $false
            Process = $process
            ProcessName = $processName
            Reason = 'command_line_unavailable'
        }
    }
    $expected = [System.IO.Path]::GetFullPath($OperatorScript).Replace('/', '\')
    $observed = $commandLine.Replace('/', '\')
    $matches = $observed.IndexOf(
        $expected,
        [System.StringComparison]::OrdinalIgnoreCase
    ) -ge 0
    return [pscustomobject]@{
        Exists = $true
        Verified = $true
        IsOperator = $matches
        Process = $process
        ProcessName = $processName
        Reason = $(if ($matches) { 'exact_operator_script' } else { 'different_python_command' })
    }
}

function Get-OperatorPidState {
    $paths = Get-OperatorPaths
    if (-not (Test-Path -LiteralPath $paths.Pid -PathType Leaf)) {
        return [pscustomobject]@{ HasPidFile = $false; Pid = 0; Identity = $null }
    }
    $pidValue = 0
    if (-not [int]::TryParse((Get-Content -LiteralPath $paths.Pid -Raw).Trim(), [ref]$pidValue) -or
        $pidValue -le 0) {
        return [pscustomobject]@{ HasPidFile = $true; Pid = 0; Identity = $null }
    }
    $identity = Get-OperatorProcessIdentity `
        -ProcessId $pidValue `
        -OperatorScript (Join-Path $paths.Runtime 'operator_main.py')
    return [pscustomobject]@{ HasPidFile = $true; Pid = $pidValue; Identity = $identity }
}

function Assert-OperatorStopped {
    $state = Get-OperatorPidState
    if (-not $state.HasPidFile -or $state.Pid -le 0 -or -not $state.Identity.Exists) {
        return
    }
    if (-not $state.Identity.Verified) {
        throw "Operator PID $($state.Pid) exists, but its command line could not be verified; refusing a lifecycle mutation."
    }
    if ($state.Identity.IsOperator) {
        throw "Operator must be stopped before this lifecycle mutation; stop this exact verified Operator as a separate observable transaction (PID $($state.Pid))."
    }
}

function Get-OperatorPaths {
    $resolved = Resolve-Project
    $canonicalRuntime = Join-Path $resolved '.codex\feishu-codex-operator-runtime'
    if (Test-Path -LiteralPath $canonicalRuntime -PathType Leaf) {
        throw "Runtime path is not a directory: $canonicalRuntime"
    }
    $runtime = $canonicalRuntime
    return [pscustomobject]@{
        Project = $resolved
        Runtime = $runtime
        Start = Join-Path $resolved '.codex\hooks\start-feishu-codex-operator.ps1'
        Stop = Join-Path $resolved '.codex\hooks\stop-feishu-codex-operator.ps1'
        Health = Join-Path $runtime 'health.json'
        Pid = Join-Path $runtime 'operator.pid'
        Env = Join-Path $runtime 'operator.env'
        Log = Join-Path $runtime 'operator.log'
    }
}

function Set-OperatorEnvValue {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Value
    )
    if ($Name -notmatch '^CODEX_OPERATOR_[A-Z0-9_]+$') {
        throw "Invalid operator environment key: $Name"
    }
    if ($Value -match '[\r\n\x00]') {
        throw "Operator environment value for $Name must be one line and contain no NUL byte."
    }
    $paths = Get-OperatorPaths
    if (-not (Test-Path -LiteralPath $paths.Env -PathType Leaf)) {
        throw "Operator environment is not installed: $($paths.Env)"
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

function Get-OperatorEnvSemanticIssues {
    param([Parameter(Mandatory = $true)][System.Collections.IDictionary]$Values)

    $issues = New-Object System.Collections.Generic.List[string]
    $booleanValues = @('0', '1', 'false', 'true', 'no', 'yes', 'off', 'on')
    foreach ($name in @(
        'CODEX_OPERATOR_DOWNLOAD_RESOURCES'
    )) {
        if (-not $Values.Contains($name)) { continue }
        $value = ([string]$Values[$name]).Trim().ToLowerInvariant()
        if ($value -notin $booleanValues) {
            $issues.Add("$name must be an explicit boolean: 0/1, false/true, no/yes, or off/on.")
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
        $value = ([string]$Values[$spec.Name]).Trim().ToLowerInvariant()
        if ($value -notin $spec.Values) {
            $issues.Add("$($spec.Name) must be one of: $($spec.Values -join ', ').")
        }
    }
    $reasoningOverride = if ($Values.Contains('CODEX_OPERATOR_BEEPER_REASONING_EFFORT')) {
        ([string]$Values['CODEX_OPERATOR_BEEPER_REASONING_EFFORT']).Trim().ToLowerInvariant()
    } else { '' }
    $modelOverride = if ($Values.Contains('CODEX_OPERATOR_BEEPER_MODEL')) {
        ([string]$Values['CODEX_OPERATOR_BEEPER_MODEL']).Trim().ToLowerInvariant()
    } else { '' }
    if ($reasoningOverride.Length -gt 0 -and $modelOverride -cne 'gpt-5.3-codex-spark') {
        $issues.Add('CODEX_OPERATOR_BEEPER_REASONING_EFFORT requires CODEX_OPERATOR_BEEPER_MODEL=gpt-5.3-codex-spark.')
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
            $issues.Add("$($spec.Name) must be an integer.")
            continue
        }
        if ($parsed -lt $spec.Minimum -or $parsed -gt $spec.Maximum) {
            $issues.Add("$($spec.Name) must be within $($spec.Minimum)..$($spec.Maximum).")
        }
    }
    if (-not $Values.Contains('CODEX_OPERATOR_BEEPER_THREAD_ID') -or
        ([string]$Values['CODEX_OPERATOR_BEEPER_THREAD_ID']).Trim() -cnotmatch
            '^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$') {
        $issues.Add('CODEX_OPERATOR_BEEPER_THREAD_ID must be an exact Codex task UUID.')
    }
    return $issues
}

function Get-OperatorEnvFileState {
    $paths = Get-OperatorPaths
    $values = @{}
    $issues = New-Object System.Collections.Generic.List[string]
    if (-not (Test-Path -LiteralPath $paths.Env -PathType Leaf)) {
        $issues.Add('operator.env is missing.')
        return [pscustomobject]@{ Values = $values; Issues = $issues }
    }
    try {
        $envLines = @(Get-Content -LiteralPath $paths.Env -ErrorAction Stop)
    } catch {
        $issues.Add("operator.env could not be read: $($_.Exception.Message)")
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
        if ($name -cnotmatch '^CODEX_OPERATOR_[A-Z0-9_]+$') {
            $issues.Add("line $lineNumber has an unsupported key.")
            continue
        }
        if ($values.ContainsKey($name)) {
            $issues.Add("duplicate key at line ${lineNumber}: $name")
            continue
        }
        $values[$name] = $parts[1].Trim()
    }
    foreach ($semanticIssue in @(Get-OperatorEnvSemanticIssues -Values $values)) {
        $issues.Add([string]$semanticIssue)
    }
    return [pscustomobject]@{ Values = $values; Issues = $issues }
}

function Assert-OperatorIdentifierList {
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
    Write-Output 'Feishu CLI installation completed. The operator is not mounted yet.'
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
    $runRoot = Join-Path $systemTemp ('feishu-codex-operator-client-' + [Guid]::NewGuid().ToString('N'))
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
    $installer = Join-Path $PSScriptRoot 'install-feishu-codex-operator.ps1'
    $arguments = @{ ProjectRoot = $resolvedProjectRoot }
    if (-not [string]::IsNullOrWhiteSpace($BeeperThreadId)) {
        $arguments['BeeperThreadId'] = $BeeperThreadId
    }
    if ($Upgrade) {
        $paths = Get-OperatorPaths
        Assert-OperatorStopped
        $arguments['Force'] = $true
        # Upgrade the runtime and its path-stable lifecycle scripts together.
        # Existing credentials and current settings are preserved; only
        # retired Router/Beeper configuration names are migrated. Hook
        # registration and restart remain separately observable transactions.
        $arguments['SkipHooks'] = $true
    } else {
        $paths = Get-OperatorPaths
        $runtimeResidue = @()
        if (Test-Path -LiteralPath $paths.Runtime -PathType Container) {
            $runtimeResidue = @(
                Get-ChildItem -LiteralPath $paths.Runtime -Force |
                    Where-Object { $_.Name -ne 'backups' }
            )
        }
        foreach ($existing in @($paths.Start, $paths.Stop) + @($runtimeResidue | ForEach-Object { $_.FullName })) {
            if (Test-Path -LiteralPath $existing) {
                throw "operator install is first-bootstrap only; an installed or partial operator already exists: $existing"
            }
        }
    }
    & $installer @arguments
    if (-not $?) { throw "$installer failed" }
}

function Invoke-OperatorHooksRefresh {
    Invoke-OperatorValidate | Out-Null
    $resolvedProjectRoot = Resolve-Project
    $paths = Get-OperatorPaths
    $installer = Join-Path $PSScriptRoot 'install-feishu-codex-operator.ps1'
    $arguments = @{ ProjectRoot = $resolvedProjectRoot; HooksOnly = $true; Force = $true }
    & $installer @arguments
    if (-not $?) { throw "$installer failed" }
    Write-Output 'Lifecycle hooks refreshed only. Run and verify the matching runtime install or upgrade before start; the current workflow continues automatically.'
}

function Assert-OperatorStartReady {
    $paths = Get-OperatorPaths
    if (-not (Test-Path -LiteralPath $paths.Start)) { throw "Operator is not installed: $($paths.Start)" }
    $envState = Get-OperatorEnvFileState
    if ($envState.Issues.Count -gt 0) {
        throw "Refusing to start with an invalid operator.env: $($envState.Issues -join ' | ')"
    }
    Invoke-OperatorValidate | Out-Null
    $parity = Get-OperatorParity
    if (-not $parity.Current) {
        $details = @()
        if ($parity.Missing.Count -gt 0) {
            $details += "missing: $($parity.Missing -join ', ')"
        }
        if ($parity.Mismatch.Count -gt 0) {
            $details += "mismatched: $($parity.Mismatch -join ', ')"
        }
        throw ("Refusing to start a stale or incomplete installed Feishu operator runtime. " +
            "Run and verify the exact operator install or upgrade first ({0})." -f ($details -join '; '))
    }
    return $paths
}

function Invoke-OperatorStart {
    $paths = Assert-OperatorStartReady
    & $paths.Start
}

function Invoke-OperatorRestart {
    # Validate before interruption so a stale install cannot turn a healthy
    # process into an avoidable outage during an automatically orchestrated restart.
    $paths = Assert-OperatorStartReady
    Invoke-OperatorStop
    & $paths.Start
}

function Invoke-OperatorStop {
    $paths = Get-OperatorPaths
    if (-not (Test-Path -LiteralPath $paths.Stop)) { throw "Operator is not installed: $($paths.Stop)" }
    $state = Get-OperatorPidState
    if ($state.HasPidFile -and $state.Pid -gt 0 -and $state.Identity.Exists) {
        if (-not $state.Identity.Verified) {
            throw "Operator PID $($state.Pid) exists, but its command line could not be verified; refusing to stop any process."
        }
        if (-not $state.Identity.IsOperator) {
            Remove-Item -LiteralPath $paths.Pid -Force -ErrorAction Stop
            Write-Output (
                "Removed stale Operator PID file; PID {0} belongs to non-Operator process {1}. No process was stopped." -f
                $state.Pid, $state.Identity.ProcessName
            )
            return
        }
        $installedStop = Get-Content -LiteralPath $paths.Stop -Raw -ErrorAction Stop
        if ($installedStop -notmatch [regex]::Escape('Get-OperatorProcessIdentity')) {
            throw ('Installed stop hook lacks PID-identity fencing. Refresh hooks while the Operator is stopped; ' +
                'refusing to delegate a force-stop that could hit a reused PID.')
        }
    }
    & $paths.Stop
}

function Invoke-OperatorStatus {
    $contract = Get-OperatorStatusContract
    $runtime = $contract.runtime
    Write-Output ("Runtime: {0}; running={1}; identity_verified={2}" -f $runtime.state, $runtime.running, $runtime.identity_verified)
    $manifest = $contract.installed_manifest
    Write-Output ("Runtime manifest: present={0}; valid={1}; version={2}" -f $manifest.present, $manifest.valid, $manifest.operator_version)
    $health = $contract.health_snapshot
    if (-not $health.present) {
        Write-Output 'Health: no snapshot yet.'
    } elseif (-not $health.valid) {
        Write-Output 'Health: invalid answer-free snapshot.'
    } else {
        Write-Output ("Health: {0}; Feishu consumer={1}; responder={2}; observer={3}; catalog={4}; callbacks={5}; active={6}; unknown={7}s; callback_grace={8}s" -f
            $health.status,
            $health.event_consumer,
            $health.responder_transport,
            $health.responder_status_observer,
            $health.catalog_transport,
            $health.callback_pending,
            $health.active_turns,
            $health.unknown_status_timeout_seconds,
            $health.callback_grace_seconds)
        Write-Output ("Beeper wake-up signal: lease_active={0}; lease={1}s; fallback_delay={2}s" -f
            $health.beeper_wake_signal.lease_active,
            $health.beeper_wake_signal.lease_seconds,
            $health.beeper_wake_signal.fallback_delay_seconds)
        if ($health.queue_counts) {
            Write-Output ("Queue: {0}" -f ($health.queue_counts | ConvertTo-Json -Compress))
        }
    }
    if (@($contract.issue_codes).Count -gt 0) {
        Write-Output ("Issues: {0}" -f (@($contract.issue_codes) -join ', '))
    }
}

function Get-OperatorParity {
    $skillRoot = Split-Path -Parent $PSScriptRoot
    $pairs = [ordered]@{
        'operator_main.py' = @(
            (Join-Path $skillRoot 'scripts\operator_main.py'),
            (Join-Path (Get-OperatorPaths).Runtime 'operator_main.py')
        )
        'routing_cli.py' = @(
            (Join-Path $skillRoot 'scripts\routing_cli.py'),
            (Join-Path (Get-OperatorPaths).Runtime 'routing_cli.py')
        )
        'operator_core\__init__.py' = @(
            (Join-Path $skillRoot 'scripts\operator_core\__init__.py'),
            (Join-Path (Get-OperatorPaths).Runtime 'operator_core\__init__.py')
        )
        'operator_core\app_server_catalog.py' = @(
            (Join-Path $skillRoot 'scripts\operator_core\app_server_catalog.py'),
            (Join-Path (Get-OperatorPaths).Runtime 'operator_core\app_server_catalog.py')
        )
        'operator_core\dispatch.py' = @(
            (Join-Path $skillRoot 'scripts\operator_core\dispatch.py'),
            (Join-Path (Get-OperatorPaths).Runtime 'operator_core\dispatch.py')
        )
        'operator_core\telemetry.py' = @(
            (Join-Path $skillRoot 'scripts\operator_core\telemetry.py'),
            (Join-Path (Get-OperatorPaths).Runtime 'operator_core\telemetry.py')
        )
        'operator_core\app_server.py' = @(
            (Join-Path $skillRoot 'scripts\operator_core\app_server.py'),
            (Join-Path (Get-OperatorPaths).Runtime 'operator_core\app_server.py')
        )
        'operator_core\final_callback.py' = @(
            (Join-Path $skillRoot 'scripts\operator_core\final_callback.py'),
            (Join-Path (Get-OperatorPaths).Runtime 'operator_core\final_callback.py')
        )
        'operator_core\config.py' = @(
            (Join-Path $skillRoot 'scripts\operator_core\config.py'),
            (Join-Path (Get-OperatorPaths).Runtime 'operator_core\config.py')
        )
        'operator_core\lark.py' = @(
            (Join-Path $skillRoot 'scripts\operator_core\lark.py'),
            (Join-Path (Get-OperatorPaths).Runtime 'operator_core\lark.py')
        )
        'operator_core\rate_limits.py' = @(
            (Join-Path $skillRoot 'scripts\operator_core\rate_limits.py'),
            (Join-Path (Get-OperatorPaths).Runtime 'operator_core\rate_limits.py')
        )
        'operator_core\responder_observer.py' = @(
            (Join-Path $skillRoot 'scripts\operator_core\responder_observer.py'),
            (Join-Path (Get-OperatorPaths).Runtime 'operator_core\responder_observer.py')
        )
        'operator_core\beeper_relay.py' = @(
            (Join-Path $skillRoot 'scripts\operator_core\beeper_relay.py'),
            (Join-Path (Get-OperatorPaths).Runtime 'operator_core\beeper_relay.py')
        )
        'operator_core\runtime.py' = @(
            (Join-Path $skillRoot 'scripts\operator_core\runtime.py'),
            (Join-Path (Get-OperatorPaths).Runtime 'operator_core\runtime.py')
        )
        'operator_core\state.py' = @(
            (Join-Path $skillRoot 'scripts\operator_core\state.py'),
            (Join-Path (Get-OperatorPaths).Runtime 'operator_core\state.py')
        )
        'start hook' = @(
            (Join-Path $skillRoot 'scripts\start-feishu-codex-operator.ps1'),
            (Get-OperatorPaths).Start
        )
        'stop hook' = @(
            (Join-Path $skillRoot 'scripts\stop-feishu-codex-operator.ps1'),
            (Get-OperatorPaths).Stop
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

function Get-InstalledOperatorManifestIssues {
    $paths = Get-OperatorPaths
    $manifestPath = Join-Path $paths.Runtime 'runtime-manifest.json'
    $issues = New-Object System.Collections.Generic.List[string]
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
    $installedConfigPath = Join-Path $paths.Runtime 'operator_core\config.py'
    if (Test-Path -LiteralPath $installedConfigPath -PathType Leaf) {
        try {
            $installedConfig = Get-Content -LiteralPath $installedConfigPath -Raw -ErrorAction Stop
            if ($installedConfig -notmatch 'OPERATOR_VERSION\s*=\s*["'']([^"'']+)["'']') {
                $issues.Add('installed runtime has no readable OPERATOR_VERSION marker.')
            } elseif ([string]$manifest.operator_version -ne [string]$Matches[1]) {
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

function Get-InstalledOperatorHookIssues {
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

    # The exact-path checks below prove that the two current handlers exist and
    # are unique. Scan every project Hook event by the lifecycle script leaf
    # names as well so a retired-path or wrong-event Operator handler cannot hide
    # outside those two exact-path counts and make machine verification pass.
    $startScriptName = [System.IO.Path]::GetFileName($StartScriptPath)
    $stopScriptName = [System.IO.Path]::GetFileName($StopScriptPath)
    $unexpectedOperatorHandlerCount = 0
    foreach ($eventProperty in @($hooksProperty.Value.PSObject.Properties)) {
        $eventName = [string]$eventProperty.Name
        foreach ($group in @($eventProperty.Value)) {
            $groupHooksProperty = $group.PSObject.Properties['hooks']
            if (-not $groupHooksProperty) { continue }
            $groupHooksAreArray = $groupHooksProperty.Value -is [System.Array]
            foreach ($handler in @($groupHooksProperty.Value)) {
                $commandProperty = $handler.PSObject.Properties['command']
                $windowsCommandProperty = $handler.PSObject.Properties['commandWindows']
                $command = if ($commandProperty) { [string]$commandProperty.Value } else { '' }
                $windowsCommand = if ($windowsCommandProperty) { [string]$windowsCommandProperty.Value } else { '' }
                $referencesStart = (
                    $command.IndexOf($startScriptName, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -or
                    $windowsCommand.IndexOf($startScriptName, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
                )
                $referencesStop = (
                    $command.IndexOf($stopScriptName, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -or
                    $windowsCommand.IndexOf($stopScriptName, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
                )
                if (-not $referencesStart -and -not $referencesStop) { continue }

                $expectedScript = if ($eventName -ceq 'SessionStart' -and $referencesStart -and -not $referencesStop) {
                    $StartScriptPath
                } elseif ($eventName -ceq 'SessionEnd' -and $referencesStop -and -not $referencesStart) {
                    $StopScriptPath
                } else {
                    $null
                }
                $expectedCommand = if ($expectedScript) {
                    'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{0}" -HookInvocation' -f $expectedScript
                } else {
                    ''
                }
                if (-not $groupHooksAreArray -or
                    -not $expectedScript -or
                    -not $command.Equals($expectedCommand, [System.StringComparison]::OrdinalIgnoreCase) -or
                    -not $windowsCommand.Equals($expectedCommand, [System.StringComparison]::OrdinalIgnoreCase)) {
                    $unexpectedOperatorHandlerCount += 1
                }
            }
        }
    }
    if ($unexpectedOperatorHandlerCount -gt 0) {
        $issues.Add(
            "Project hooks include unexpected Operator lifecycle handlers; found $unexpectedOperatorHandlerCount."
        )
    }

    $specifications = @(
        [pscustomobject]@{
            Event = 'SessionStart'
            Script = $StartScriptPath
            Matcher = 'startup|resume'
            Timeout = 10
            StatusMessage = 'Activating Feishu operator lease'
        },
        [pscustomobject]@{
            Event = 'SessionEnd'
            Script = $StopScriptPath
            Matcher = $null
            Timeout = 3
            StatusMessage = 'Releasing Feishu operator lease'
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

        $operatorHandlerCount = 0
        foreach ($group in @($eventProperty.Value)) {
            $groupHooksProperty = $group.PSObject.Properties['hooks']
            if (-not $groupHooksProperty) { continue }
            foreach ($handler in @($groupHooksProperty.Value)) {
                $commandProperty = $handler.PSObject.Properties['command']
                $windowsCommandProperty = $handler.PSObject.Properties['commandWindows']
                $command = if ($commandProperty) { [string]$commandProperty.Value } else { '' }
                $windowsCommand = if ($windowsCommandProperty) { [string]$windowsCommandProperty.Value } else { '' }
                $referencesOperator = (
                    $command.IndexOf($specification.Script, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -or
                    $windowsCommand.IndexOf($specification.Script, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
                )
                if (-not $referencesOperator) { continue }

                $operatorHandlerCount += 1
                $expectedCommand = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{0}" -HookInvocation' -f $specification.Script
                $typeProperty = $handler.PSObject.Properties['type']
                $timeoutProperty = $handler.PSObject.Properties['timeout']
                $statusProperty = $handler.PSObject.Properties['statusMessage']
                if (-not $typeProperty -or [string]$typeProperty.Value -cne 'command') {
                    $issues.Add("$($specification.Event) Operator handler type is not command.")
                }
                if (-not $command.Equals($expectedCommand, [System.StringComparison]::OrdinalIgnoreCase) -or
                    -not $windowsCommand.Equals($expectedCommand, [System.StringComparison]::OrdinalIgnoreCase)) {
                    $issues.Add("$($specification.Event) Operator command is not the exact installed command.")
                }
                $timeoutIsInteger = if ($timeoutProperty) {
                    $timeoutProperty.Value -is [System.SByte] -or
                    $timeoutProperty.Value -is [System.Byte] -or
                    $timeoutProperty.Value -is [System.Int16] -or
                    $timeoutProperty.Value -is [System.UInt16] -or
                    $timeoutProperty.Value -is [System.Int32] -or
                    $timeoutProperty.Value -is [System.UInt32] -or
                    $timeoutProperty.Value -is [System.Int64] -or
                    $timeoutProperty.Value -is [System.UInt64]
                } else {
                    $false
                }
                if (-not $timeoutProperty -or
                    -not $timeoutIsInteger -or
                    [long]$timeoutProperty.Value -ne $specification.Timeout) {
                    $issues.Add("$($specification.Event) Operator timeout is not $($specification.Timeout) seconds.")
                }
                if (-not $statusProperty -or [string]$statusProperty.Value -cne $specification.StatusMessage) {
                    $issues.Add("$($specification.Event) Operator status message is not current.")
                }
                if ($specification.Matcher) {
                    $matcherProperty = $group.PSObject.Properties['matcher']
                    if (-not $matcherProperty -or [string]$matcherProperty.Value -cne $specification.Matcher) {
                        $issues.Add("$($specification.Event) Operator matcher is not '$($specification.Matcher)'.")
                    }
                } else {
                    $matcherProperty = $group.PSObject.Properties['matcher']
                    if ($matcherProperty) {
                        $issues.Add("$($specification.Event) Operator matcher must be absent.")
                    }
                }
            }
        }
        if ($operatorHandlerCount -ne 1) {
            $issues.Add("$($specification.Event) must contain exactly one Operator handler; found $operatorHandlerCount.")
        }
    }
    foreach ($specification in $specifications) {
        try {
            $scriptInfo = Get-Item -LiteralPath $specification.Script -Force -ErrorAction Stop
            if ($scriptInfo.PSIsContainer -or
                ($scriptInfo.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
                $scriptInfo.Length -gt 2097152) {
                throw 'lifecycle script is not a bounded regular file'
            }
            $scriptText = Get-Content -LiteralPath $specification.Script -Raw -Encoding utf8 -ErrorAction Stop
            $writeOutputCount = [regex]::Matches($scriptText, '(?m)^\s*Write-Output\b').Count
            $successOutputSilent = (
                $scriptText -match [regex]::Escape('FEISHU_OPERATOR_HOOK_SUCCESS_STDOUT_SILENT_V1') -and
                $scriptText -match [regex]::Escape('if (-not $HookInvocation)') -and
                $scriptText -match [regex]::Escape('Write-Output $Message') -and
                $writeOutputCount -eq 1
            )
            if (-not $successOutputSilent) {
                $issues.Add("$($specification.Event) HookInvocation success output is not silent.")
            }
        } catch {
            $issues.Add("$($specification.Event) HookInvocation output contract could not be verified.")
        }
    }
    return $issues
}

function Invoke-OperatorDoctor {
    $contract = Get-OperatorDoctorContract
    Write-Output ("Doctor: {0}" -f $contract.status)
    foreach ($check in $contract.checks.GetEnumerator()) {
        Write-Output ("- {0}: {1}" -f $check.Key, $(if ($check.Value) { 'pass' } else { 'fail' }))
    }
    Invoke-OperatorStatus
}

function Write-OperatorJson {
    param([Parameter(Mandatory = $true)]$InputObject)

    $InputObject | ConvertTo-Json -Depth 12 -Compress | Write-Output
}

function Test-OperatorJsonInteger {
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

function Test-OperatorJsonNumber {
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

function Test-OperatorVersionString {
    param([AllowNull()]$Value)

    return (
        $Value -is [string] -and
        ([string]$Value).Length -le 96 -and
        [string]$Value -cmatch '^[0-9]{1,5}\.[0-9]{1,5}\.[0-9]{1,5}(?:-[0-9A-Za-z](?:[0-9A-Za-z.-]{0,63})?)?$'
    )
}

function Get-OperatorStatusContract {
    $paths = Get-OperatorPaths
    $pidState = Get-OperatorPidState
    $running = (
        $pidState.Pid -gt 0 -and
        $pidState.Identity.Exists -and
        $pidState.Identity.Verified -and
        $pidState.Identity.IsOperator
    )
    $runtimeState = if ($running) {
        'running'
    } elseif ($pidState.Pid -gt 0 -and $pidState.Identity.Exists -and -not $pidState.Identity.Verified) {
        'unknown'
    } else {
        'stopped'
    }

    $manifestPath = Join-Path $paths.Runtime 'runtime-manifest.json'
    $manifestIssues = @(Get-InstalledOperatorManifestIssues)
    $manifestVersion = $null
    $manifestSha256 = $null
    if (Test-Path -LiteralPath $manifestPath -PathType Leaf) {
        try {
            $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding utf8 |
                ConvertFrom-Json -ErrorAction Stop
            $manifestVersion = [string]$manifest.operator_version
            $manifestSha256 = (
                Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256
            ).Hash.ToLowerInvariant()
        } catch {
            $manifestIssues = @('invalid_runtime_manifest')
        }
    }

    $healthSummary = [ordered]@{
        present = $false
        valid = $false
        status = $null
        operator_version = $null
        event_consumer = $null
        session_owner = $null
        responder_transport = $null
        responder_status_observer = $null
        beeper_wake_signal = [ordered]@{
            lease_active = $null
            lease_seconds = $null
            fallback_delay_seconds = $null
        }
        catalog_transport = $null
        unknown_status_timeout_seconds = $null
        callback_grace_seconds = $null
        account_rate_limits = [ordered]@{
            status = $null
            limit_id = $null
            remaining_percent = $null
            window_duration_minutes = $null
            reset_at = $null
            beeper_model = $null
            beeper_reasoning_effort = $null
            beeper_limit_id = $null
            beeper_remaining_percent = $null
            beeper_window_duration_minutes = $null
            beeper_reset_at = $null
        }
        callback_pending = $null
        active_turns = $null
        queue_counts = $null
        access_mode = $null
        access_configured = $null
        runtime_manifest_current = $false
        snapshot_fresh = $false
    }
    $healthIssue = $null
    if (Test-Path -LiteralPath $paths.Health -PathType Leaf) {
        $healthSummary.present = $true
        try {
            $health = Get-Content -LiteralPath $paths.Health -Raw -Encoding utf8 |
                ConvertFrom-Json -ErrorAction Stop
            if (
                [string]$health.status -notin @('online', 'degraded', 'stopping', 'stopped') -or
                [string]$health.session_owner -cne 'responder' -or
                [string]$health.responder_writer -cne 'beeper-task-send' -or
                [string]$health.responder_transport -notin @('beeper-relay', 'unavailable') -or
                [string]$health.responder_status_observer -notin @('app-server-metadata-readonly', 'unavailable') -or
                [string]$health.catalog_transport -notin @('app-server-readonly', 'unavailable') -or
                $health.event_consumer -isnot [bool] -or
                $health.beeper_wake_signal.lease_active -isnot [bool] -or
                -not (Test-OperatorJsonInteger -Value $health.beeper_wake_signal.lease_seconds -Minimum 1) -or
                [long]$health.beeper_wake_signal.lease_seconds -gt 86400 -or
                -not (Test-OperatorJsonInteger -Value $health.beeper_wake_signal.fallback_delay_seconds -Minimum 1) -or
                [long]$health.beeper_wake_signal.fallback_delay_seconds -gt 300 -or
                -not (Test-OperatorJsonInteger -Value $health.pid -Minimum 1) -or
                -not (Test-OperatorJsonInteger -Value $health.active_turns -Minimum 0) -or
                -not (Test-OperatorJsonInteger -Value $health.unknown_status_timeout_seconds -Minimum 30) -or
                [long]$health.unknown_status_timeout_seconds -gt 86400 -or
                -not (Test-OperatorJsonInteger -Value $health.callback_grace_seconds -Minimum 10) -or
                [long]$health.callback_grace_seconds -gt 30 -or
                -not (Test-OperatorJsonInteger -Value $health.callback_queue.pending -Minimum 0) -or
                -not (Test-OperatorJsonNumber -Value $health.started_at) -or
                -not (Test-OperatorJsonNumber -Value $health.updated_at)
            ) {
                throw 'invalid current health'
            }
            $healthSummary.valid = $true
            $healthSummary.status = [string]$health.status
            $healthSummary.operator_version = [string]$health.operator_version
            $healthSummary.event_consumer = [bool]$health.event_consumer
            $healthSummary.session_owner = 'responder'
            $healthSummary.responder_transport = [string]$health.responder_transport
            $healthSummary.responder_status_observer = [string]$health.responder_status_observer
            $healthSummary.beeper_wake_signal.lease_active = [bool]$health.beeper_wake_signal.lease_active
            $healthSummary.beeper_wake_signal.lease_seconds = [int]$health.beeper_wake_signal.lease_seconds
            $healthSummary.beeper_wake_signal.fallback_delay_seconds = [int]$health.beeper_wake_signal.fallback_delay_seconds
            $healthSummary.catalog_transport = [string]$health.catalog_transport
            $healthSummary.unknown_status_timeout_seconds = [int]$health.unknown_status_timeout_seconds
            $healthSummary.callback_grace_seconds = [int]$health.callback_grace_seconds
            if ($null -ne $health.account_rate_limits) {
                $rateStatus = [string]$health.account_rate_limits.status
                $rateLimitId = $health.account_rate_limits.limit_id
                $rateRemaining = $health.account_rate_limits.remaining_percent
                $rateWindowMinutes = $health.account_rate_limits.window_duration_minutes
                $rateResetAt = $health.account_rate_limits.reset_at
                $beeperModel = $health.account_rate_limits.beeper_model
                $beeperReasoningEffort = $health.account_rate_limits.beeper_reasoning_effort
                $beeperLimitId = $health.account_rate_limits.beeper_limit_id
                $beeperRemaining = $health.account_rate_limits.beeper_remaining_percent
                $beeperWindowMinutes = $health.account_rate_limits.beeper_window_duration_minutes
                $beeperResetAt = $health.account_rate_limits.beeper_reset_at
                $beeperPolicyValid = (
                    ($null -eq $beeperModel -and $null -eq $beeperReasoningEffort) -or
                    ([string]$beeperModel -ceq 'gpt-5.3-codex-spark' -and
                        [string]$beeperReasoningEffort -in @('low', 'medium', 'high')) -or
                    ([string]$beeperModel -ceq 'gpt-5.6-luna' -and
                        [string]$beeperReasoningEffort -ceq 'low')
                )
                if (
                    $rateStatus -notin @('cached', 'stale', 'unavailable') -or
                    ($null -ne $rateLimitId -and (
                        $rateLimitId -isnot [string] -or
                        ([string]$rateLimitId).Length -gt 80 -or
                        [string]$rateLimitId -cnotmatch '^[0-9A-Za-z._-]+$'
                    )) -or
                    ($null -ne $rateRemaining -and (
                        -not (Test-OperatorJsonInteger -Value $rateRemaining -Minimum 0) -or
                        [long]$rateRemaining -gt 100
                    )) -or
                    ($null -ne $rateWindowMinutes -and
                        -not (Test-OperatorJsonInteger -Value $rateWindowMinutes -Minimum 1)) -or
                    ($null -ne $rateResetAt -and
                        -not (Test-OperatorJsonInteger -Value $rateResetAt -Minimum 1)) -or
                    -not $beeperPolicyValid -or
                    ($null -ne $beeperLimitId -and (
                        $beeperLimitId -isnot [string] -or
                        ([string]$beeperLimitId).Length -gt 80 -or
                        [string]$beeperLimitId -cnotmatch '^[0-9A-Za-z._-]+$'
                    )) -or
                    ($null -ne $beeperRemaining -and (
                        -not (Test-OperatorJsonInteger -Value $beeperRemaining -Minimum 0) -or
                        [long]$beeperRemaining -gt 100
                    )) -or
                    ($null -ne $beeperWindowMinutes -and
                        -not (Test-OperatorJsonInteger -Value $beeperWindowMinutes -Minimum 1)) -or
                    ($null -ne $beeperResetAt -and
                        -not (Test-OperatorJsonInteger -Value $beeperResetAt -Minimum 1))
                ) {
                    throw 'invalid account rate-limit health'
                }
                $healthSummary.account_rate_limits.status = $rateStatus
                $healthSummary.account_rate_limits.limit_id = if ($null -ne $rateLimitId) { [string]$rateLimitId } else { $null }
                $healthSummary.account_rate_limits.remaining_percent = if ($null -ne $rateRemaining) { [int]$rateRemaining } else { $null }
                $healthSummary.account_rate_limits.window_duration_minutes = if ($null -ne $rateWindowMinutes) { [int]$rateWindowMinutes } else { $null }
                $healthSummary.account_rate_limits.reset_at = if ($null -ne $rateResetAt) { [long]$rateResetAt } else { $null }
                $healthSummary.account_rate_limits.beeper_model = if ($null -ne $beeperModel) { [string]$beeperModel } else { $null }
                $healthSummary.account_rate_limits.beeper_reasoning_effort = if ($null -ne $beeperReasoningEffort) { [string]$beeperReasoningEffort } else { $null }
                $healthSummary.account_rate_limits.beeper_limit_id = if ($null -ne $beeperLimitId) { [string]$beeperLimitId } else { $null }
                $healthSummary.account_rate_limits.beeper_remaining_percent = if ($null -ne $beeperRemaining) { [int]$beeperRemaining } else { $null }
                $healthSummary.account_rate_limits.beeper_window_duration_minutes = if ($null -ne $beeperWindowMinutes) { [int]$beeperWindowMinutes } else { $null }
                $healthSummary.account_rate_limits.beeper_reset_at = if ($null -ne $beeperResetAt) { [long]$beeperResetAt } else { $null }
            }
            $healthSummary.callback_pending = [int]$health.callback_queue.pending
            $healthSummary.active_turns = [int]$health.active_turns
            $healthSummary.queue_counts = $health.queue
            $healthSummary.access_mode = [string]$health.access_mode
            $healthSummary.access_configured = [bool]$health.access_configured
            $healthSummary.runtime_manifest_current = (
                $manifestSha256 -and
                [string]$health.runtime_manifest_sha256 -ceq $manifestSha256
            )
            $now = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
            $healthSummary.snapshot_fresh = (
                [double]$health.updated_at -le ($now + 5.0) -and
                ($now - [double]$health.updated_at) -le 20.0
            )
        } catch {
            $healthIssue = 'invalid_health_snapshot'
        }
    }

    $issues = [System.Collections.Generic.List[string]]::new()
    foreach ($issue in $manifestIssues) { $issues.Add('integrity_check_failed') }
    if ($healthIssue) { $issues.Add($healthIssue) }
    if ($running -and (-not $healthSummary.valid -or -not $healthSummary.snapshot_fresh)) {
        $issues.Add('live_health_unavailable')
    }

    return [ordered]@{
        schema_version = 1
        command = 'operator.status'
        runtime = [ordered]@{
            state = $runtimeState
            running = $running
            pid = if ($pidState.Pid -gt 0) { [int]$pidState.Pid } else { $null }
            identity_verified = [bool]$pidState.Identity.Verified
        }
        installed_manifest = [ordered]@{
            present = (Test-Path -LiteralPath $manifestPath -PathType Leaf)
            valid = ($manifestIssues.Count -eq 0)
            operator_version = $manifestVersion
        }
        health_snapshot = $healthSummary
        issue_codes = @($issues | Select-Object -Unique)
    }
}

function Get-FinalCallbackRegistrationState {
    $paths = Get-OperatorPaths
    $registry = Join-Path $env:LOCALAPPDATA 'OpenAI\Codex\feishu-codex-final-callback\registration.json'
    $result = [ordered]@{ configured = $false; matches_runtime = $false; schema_current = $false }
    if (-not (Test-Path -LiteralPath $registry -PathType Leaf)) { return $result }
    try {
        $value = Get-Content -LiteralPath $registry -Raw -Encoding utf8 |
            ConvertFrom-Json -ErrorAction Stop
        $result.configured = $true
        $result.schema_current = ([int]$value.schema_version -eq 2)
        $result.matches_runtime = (
            [System.IO.Path]::GetFullPath([string]$value.runtime_dir).Equals(
                [System.IO.Path]::GetFullPath($paths.Runtime),
                [System.StringComparison]::OrdinalIgnoreCase
            )
        )
    } catch {
        return $result
    }
    return $result
}

function Get-OperatorDoctorContract {
    $status = Get-OperatorStatusContract
    $parity = Get-OperatorParity
    $paths = Get-OperatorPaths
    $hooks = @(
        Get-InstalledOperatorHookIssues `
            -HooksConfigPath (Join-Path $paths.Project '.codex\hooks.json') `
            -StartScriptPath $paths.Start `
            -StopScriptPath $paths.Stop
    )
    $callback = Get-FinalCallbackRegistrationState
    $checks = [ordered]@{
        source_runtime_parity = [bool]$parity.Current
        runtime_manifest = [bool]$status.installed_manifest.valid
        lifecycle_hooks = ($hooks.Count -eq 0)
        final_callback_registered = (
            [bool]$callback.configured -and
            [bool]$callback.matches_runtime -and
            [bool]$callback.schema_current
        )
    }
    $failed = @($checks.GetEnumerator() | Where-Object { -not [bool]$_.Value })
    return [ordered]@{
        schema_version = 1
        command = 'operator.doctor'
        status = if ($failed.Count -eq 0) { 'passed' } else { 'failed' }
        checks = $checks
        issue_codes = @($failed | ForEach-Object { [string]$_.Key })
    }
}

function Get-OperatorReadinessContract {
    $status = Get-OperatorStatusContract
    $health = $status.health_snapshot
    $callback = Get-FinalCallbackRegistrationState
    $gates = [ordered]@{
        runtime_running = [bool]$status.runtime.running
        runtime_manifest = [bool]$status.installed_manifest.valid
        health_current = (
            [bool]$health.valid -and
            [bool]$health.runtime_manifest_current -and
            [bool]$health.snapshot_fresh
        )
        feishu_consumer = ([bool]$health.event_consumer)
        access_configured = (
            [string]$health.access_mode -ceq 'locked' -and
            [bool]$health.access_configured
        )
        minimal_beeper_relay = ([string]$health.responder_transport -ceq 'beeper-relay')
        init_catalog = ([string]$health.catalog_transport -ceq 'app-server-readonly')
        final_callback = (
            [bool]$callback.configured -and
            [bool]$callback.matches_runtime -and
            [bool]$callback.schema_current
        )
    }
    $failed = @($gates.GetEnumerator() | Where-Object { -not [bool]$_.Value })
    return [ordered]@{
        schema_version = 1
        command = 'operator.readiness'
        status = if ($failed.Count -eq 0) { 'ready' } else { 'not_ready' }
        ready = ($failed.Count -eq 0)
        gates = $gates
        issue_codes = @($failed | ForEach-Object { [string]$_.Key })
    }
}

function Invoke-OperatorReadiness {
    $contract = Get-OperatorReadinessContract
    Write-Output ("Readiness: {0}" -f $contract.status)
    foreach ($gate in $contract.gates.GetEnumerator()) {
        Write-Output ("- {0}: {1}" -f $gate.Key, $(if ($gate.Value) { 'pass' } else { 'fail' }))
    }
}

function Get-OperatorValidateContract {
    $pluginRoot = Split-Path -Parent $PSScriptRoot
    $inventory = Join-Path $pluginRoot 'assets\release-inventory.json'
    $rules = Join-Path $pluginRoot 'assets\AGENTS.feishu-codex-operator.md'
    $rootRules = Join-Path (Resolve-Project) 'AGENTS.md'
    $checks = [ordered]@{
        release_inventory = (Test-Path -LiteralPath $inventory -PathType Leaf)
        rules_mirror = (
            (Test-Path -LiteralPath $rootRules -PathType Leaf) -and
            (Get-FileHash -LiteralPath $rootRules -Algorithm SHA256).Hash -ceq
            (Get-FileHash -LiteralPath $rules -Algorithm SHA256).Hash
        )
        minimal_beeper_source = (
            (Test-Path -LiteralPath (Join-Path $pluginRoot 'scripts\operator_core\beeper_relay.py')) -and
            -not (Test-Path -LiteralPath (Join-Path $pluginRoot 'scripts\operator_core\beeper_queue.py'))
        )
        callback_source = (Test-Path -LiteralPath (Join-Path $pluginRoot 'scripts\operator_core\final_callback.py'))
        readonly_catalog_source = (Test-Path -LiteralPath (Join-Path $pluginRoot 'scripts\operator_core\app_server_catalog.py'))
        adaptive_rate_limit_source = (Test-Path -LiteralPath (Join-Path $pluginRoot 'scripts\operator_core\rate_limits.py'))
        responder_observer_source = (Test-Path -LiteralPath (Join-Path $pluginRoot 'scripts\operator_core\responder_observer.py'))
    }
    $failed = @($checks.GetEnumerator() | Where-Object { -not [bool]$_.Value })
    return [ordered]@{
        schema_version = 1
        command = 'operator.validate'
        status = if ($failed.Count -eq 0) { 'passed' } else { 'failed' }
        checks = $checks
        issue_codes = @($failed | ForEach-Object { [string]$_.Key })
    }
}

function Invoke-OperatorLogs {
    $paths = Get-OperatorPaths
    if (-not (Test-Path -LiteralPath $paths.Log)) { throw 'No operator log exists yet.' }
    Get-Content -LiteralPath $paths.Log -Tail ([Math]::Max(1, [Math]::Min($Tail, 500)))
}

function Invoke-OperatorValidate {
    $contract = Get-OperatorValidateContract
    if ([string]$contract.status -cne 'passed') {
        throw ("Operator source validation failed: {0}" -f (@($contract.issue_codes) -join ', '))
    }
    $pluginRoot = Split-Path -Parent $PSScriptRoot
    $audit = Join-Path $pluginRoot 'scripts\audit-feishu-codex-release.ps1'
    $auditWire = @(& $audit) -join ''
    $auditResult = $auditWire | ConvertFrom-Json -ErrorAction Stop
    if ([string]$auditResult.status -cne 'passed') {
        throw 'Operator release audit failed.'
    }
    Write-Output 'Operator source, inventory, minimal Beeper relay, callback, and read-only catalog contracts passed.'

    if ($RunTests) {
        $status = Get-OperatorStatusContract
        if ([bool]$status.runtime.running) {
            throw 'Stop the exact Operator before running focused tests.'
        }
        Update-ProcessPathFromEnvironment
        $python = Get-ExecutablePreflightResult 'python.exe'
        $pythonPrefix = @()
        if (-not $python.Available) {
            $python = Get-ExecutablePreflightResult 'py.exe' @('-3', '--version')
            $pythonPrefix = @('-3')
        }
        if (-not $python.Available) {
            throw 'Python 3.10+ is required for Operator tests.'
        }
        & $python.Source @pythonPrefix -m unittest discover -s (Join-Path $pluginRoot 'tests') -v
        if ($LASTEXITCODE -ne 0) {
            throw "Operator unit tests failed with exit code $LASTEXITCODE."
        }
    }
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

function Invoke-OperatorPreflight {
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
        Write-Output ("[WARN] Verified dependencies are outside persistent user/machine PATH; Operator-local discovery will use: {0}" -f ($missingPersistentPaths -join '; '))
        $pathDiscoveryFallback = $true
    } else {
        Write-Output '[PASS] Verified executable directories are present in persistent user/machine PATH.'
    }

    try {
        $validation = @(Invoke-OperatorValidate)
        Write-Output ("[PASS] Skill source: {0}" -f ($validation -join ' '))
    } catch {
        Write-Output ("[FAIL] Skill source validation: {0}" -f $_.Exception.Message)
        $failed = $true
    }

    $paths = Get-OperatorPaths
    if (Test-Path -LiteralPath (Join-Path $paths.Runtime 'operator_main.py') -PathType Leaf) {
        Write-Output '[INFO] Operator runtime is installed; use operator doctor to inspect parity and health.'
    } else {
        Write-Output '[PENDING] Operator runtime is not installed. Preflight does not install it.'
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
        Write-Output '[INFO] Do not claim global PATH availability. Use the Operator wrapper and exact verified executable paths; start fresh-shell checks with the current shell executable absolute path.'
    }
    Write-Output '[INFO] Ordinary requests queue the fixed minimal Beeper once; it sends once to the exact bound Responder. /init uses only the on-demand read-only App Server catalog.'
    $script:OperatorPreflightFailed = $failed
}

function Invoke-OperatorTests {
    if (-not $RunTests) {
        throw 'Use operator test -RunTests after verifying the Operator is stopped.'
    }
    Invoke-OperatorValidate
}

function Invoke-OperatorAccess {
    if (-not $AccessMode) { throw 'AccessMode is required: compat or locked.' }
    $paths = Get-OperatorPaths
    if (-not (Test-Path -LiteralPath $paths.Env -PathType Leaf)) {
        throw "Operator environment is not installed: $($paths.Env)"
    }
    $envState = Get-OperatorEnvFileState
    if ($envState.Issues.Count -gt 0) {
        throw "Operator environment is invalid: $($envState.Issues -join ' | ')"
    }

    $identityValues = [ordered]@{
        CODEX_OPERATOR_OWNER_OPEN_ID = [pscustomobject]@{ Value = $OwnerOpenId; Prefix = 'ou_'; Single = $true }
        CODEX_OPERATOR_ADMIN_OPEN_IDS = [pscustomobject]@{ Value = $AdminOpenIds; Prefix = 'ou_'; Single = $false }
        CODEX_OPERATOR_ALLOWED_USER_OPEN_IDS = [pscustomobject]@{ Value = $AllowedUserOpenIds; Prefix = 'ou_'; Single = $false }
        CODEX_OPERATOR_ALLOWED_CHAT_IDS = [pscustomobject]@{ Value = $AllowedChatIds; Prefix = 'oc_'; Single = $false }
    }
    foreach ($entry in $identityValues.GetEnumerator()) {
        Assert-OperatorIdentifierList -Name $entry.Key -Value ([string]$entry.Value.Value) `
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
        Assert-OperatorIdentifierList -Name $entry.Key -Value $effective `
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

    Set-OperatorEnvValue 'CODEX_OPERATOR_ACCESS_MODE' $AccessMode
    foreach ($entry in $identityValues.GetEnumerator()) {
        if (-not [string]::IsNullOrWhiteSpace([string]$entry.Value.Value)) {
            Set-OperatorEnvValue $entry.Key ([string]$entry.Value.Value)
        }
    }
    Write-Output 'Access policy only was updated; runtime code, hooks, and project rules were not changed. Restart the operator separately to apply it.'
}

function Invoke-MinimalBeeperConfigure {
    if ([string]::IsNullOrWhiteSpace($BeeperThreadId) -or
        $BeeperThreadId.Trim().ToLowerInvariant() -cnotmatch
            '^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$') {
        throw 'BeeperThreadId must be an exact Codex task UUID.'
    }
    Assert-OperatorStopped
    $paths = Get-OperatorPaths
    if (-not (Test-Path -LiteralPath $paths.Env -PathType Leaf)) {
        throw "Operator environment is not installed: $($paths.Env)"
    }
    Set-OperatorEnvValue 'CODEX_OPERATOR_BEEPER_THREAD_ID' $BeeperThreadId.Trim().ToLowerInvariant()
    Write-Output 'Minimal Beeper task binding was updated. Restart the Operator separately to apply it.'
}

function Invoke-FinalCallbackRegistryHelper {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet('final-callback-registry-status', 'final-callback-register', 'final-callback-unregister')]
        [string]$Command
    )

    $paths = Get-OperatorPaths
    $helper = Join-Path $paths.Runtime 'routing_cli.py'
    $installedVersion = 'unknown'
    $manifestPath = Join-Path $paths.Runtime 'runtime-manifest.json'
    if (Test-Path -LiteralPath $manifestPath -PathType Leaf) {
        try {
            $installedManifest = Get-Content -LiteralPath $manifestPath -Raw -ErrorAction Stop |
                ConvertFrom-Json -ErrorAction Stop
            if (-not [string]::IsNullOrWhiteSpace([string]$installedManifest.operator_version)) {
                $installedVersion = [string]$installedManifest.operator_version
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
            $helperSupportsFinalCallback = $helperText.Contains('final-callback-registry-status')
        } catch {
            throw 'The installed Operator final-callback helper could not be inspected.'
        }
    }
    if (-not $helperSupportsFinalCallback) {
        if ($Command -eq 'final-callback-registry-status') {
            Write-Output (ConvertTo-Json -Compress -InputObject ([ordered]@{
                schema_version = 1
                command = 'operator.final-callback-status'
                status = 'upgrade_required'
                installed_operator_version = $installedVersion
                required_runtime_capability = 'request_id_final_callback_routing'
            }))
            return
        }
        throw 'The installed Operator runtime predates request_id Final Callback routing; run and verify operator upgrade first.'
    }
    if ($Command -eq 'final-callback-register') {
        $manifestIssues = @(Get-InstalledOperatorManifestIssues)
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

if ($Json -and -not ($scopeName -eq 'operator' -and $actionName -in @('status', 'doctor', 'readiness', 'validate'))) {
    throw '-Json is supported only for operator status, operator doctor, operator readiness, and operator validate.'
}

switch ($scopeName) {
    'help' { Show-Usage; exit 0 }
    '-help' { Show-Usage; exit 0 }
    '--help' { Show-Usage; exit 0 }
    'doctor' {
        Invoke-FeishuDoctor
        Invoke-OperatorDoctor
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
    'operator' {
        switch ($actionName) {
            'init' { Invoke-AgentsInit }
            'install' { Invoke-Installer }
            'upgrade' { Invoke-Installer -Upgrade; Write-Output 'Upgrade installed. Continue with the separately observable operator restart transaction to activate it.' }
            'hooks' { Invoke-OperatorHooksRefresh }
            'final-callback-status' {
                Invoke-FinalCallbackRegistryHelper -Command 'final-callback-registry-status'
            }
            'final-callback-register' {
                Invoke-FinalCallbackRegistryHelper -Command 'final-callback-register'
            }
            'final-callback-unregister' {
                Invoke-FinalCallbackRegistryHelper -Command 'final-callback-unregister'
            }
            'beeper-configure' { Invoke-MinimalBeeperConfigure }
            'start' { Invoke-OperatorStart }
            'stop' { Invoke-OperatorStop }
            'restart' { Invoke-OperatorRestart }
            'preflight' {
                Invoke-OperatorPreflight
                if ($script:OperatorPreflightFailed) { exit 2 }
            }
            'status' {
                if ($Json) { Write-OperatorJson -InputObject (Get-OperatorStatusContract) }
                else { Invoke-OperatorStatus }
            }
            'doctor' {
                if ($Json) {
                    $doctorContract = Get-OperatorDoctorContract
                    Write-OperatorJson -InputObject $doctorContract
                    if ($doctorContract.status -eq 'failed') { exit 2 }
                }
                else { Invoke-OperatorDoctor }
            }
            'readiness' {
                if ($Json) {
                    $readinessContract = Get-OperatorReadinessContract
                    Write-OperatorJson -InputObject $readinessContract
                    if (-not $readinessContract.ready) { exit 2 }
                }
                else {
                    $readinessContract = Get-OperatorReadinessContract
                    Invoke-OperatorReadiness
                    if (-not $readinessContract.ready) { exit 2 }
                }
            }
            'logs' { Invoke-OperatorLogs }
            'validate' {
                if ($Json) {
                    $validationContract = Get-OperatorValidateContract
                    Write-OperatorJson -InputObject $validationContract
                    if ($validationContract.status -ne 'passed') { exit 2 }
                } else { Invoke-OperatorValidate }
            }
            'test' { Invoke-OperatorTests }
            'access' { Invoke-OperatorAccess }
            default { Show-Usage; throw "Unknown operator subcommand: $Action" }
        }
    }
    default { Show-Usage; throw "Unknown command scope: $Scope" }
}
