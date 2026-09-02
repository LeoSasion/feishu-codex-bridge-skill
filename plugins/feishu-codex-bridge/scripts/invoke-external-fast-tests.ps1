[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateNotNullOrEmpty()][string]$PythonExecutable,
    [string]$DesktopRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$ProjectRoot = '',
    [ValidateRange(30, 300)][int]$TimeoutSeconds = 180,
    [Parameter(Mandatory = $true)][switch]$ExternalTestRunnerAcknowledged
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$PSModuleAutoLoadingPreference = 'None'

foreach ($moduleManifest in @(
    [IO.Path]::Combine($PSHOME, 'Modules', 'Microsoft.PowerShell.Utility', 'Microsoft.PowerShell.Utility.psd1'),
    [IO.Path]::Combine($PSHOME, 'Modules', 'Microsoft.PowerShell.Security', 'Microsoft.PowerShell.Security.psd1'),
    [IO.Path]::Combine($PSHOME, 'Modules', 'Microsoft.PowerShell.Management', 'Microsoft.PowerShell.Management.psd1'),
    [IO.Path]::Combine($PSHOME, 'Modules', 'CimCmdlets', 'CimCmdlets.psd1')
)) {
    Import-Module -Name $moduleManifest -Force -ErrorAction Stop
}

function Write-AnswerFreeResult {
    param(
        [Parameter(Mandatory = $true)][string]$Status,
        [string]$ErrorCode = ''
    )
    $payload = [ordered]@{
        schema_version = 1
        lane = 'development-fast'
        status = $Status
        release_evidence = $false
    }
    if ($ErrorCode) { $payload.error_code = $ErrorCode }
    $payload | ConvertTo-Json -Compress -Depth 4
}

function Invoke-JsonCommand {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][string]$StageName,
        [ValidateRange(5, 60)][int]$CommandTimeoutSeconds = 30
    )

    $startInfo = [Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $Executable
    $startInfo.WorkingDirectory = $WorkingDirectory
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    foreach ($argument in $Arguments) {
        [void]$startInfo.ArgumentList.Add([string]$argument)
    }
    foreach ($name in @($startInfo.Environment.Keys)) {
        if ([string]$name -match '^(?i:PYTHON|CODEX_BRIDGE_|FEISHU_|LARK_|LARKCLI_)') {
            [void]$startInfo.Environment.Remove([string]$name)
        }
    }

    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    $processStarted = $false
    $stdoutTask = $null
    $stderrTask = $null
    try {
        if (-not $process.Start()) { throw "$StageName process did not start." }
        $processStarted = $true
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($CommandTimeoutSeconds * 1000)) {
            try {
                $process.Kill($true)
            } catch {
                throw "$StageName timed out and its process tree could not be terminated."
            }
            if (-not $process.WaitForExit(30000)) {
                throw "$StageName timed out and its process tree did not exit within 30 seconds."
            }
            if (-not [Threading.Tasks.Task]::WaitAll(
                    [Threading.Tasks.Task[]]@($stdoutTask, $stderrTask),
                    30000
                )) {
                throw "$StageName timed out and its output pipes did not close within 30 seconds."
            }
            throw "$StageName exceeded its $CommandTimeoutSeconds second timeout."
        }
        if (-not [Threading.Tasks.Task]::WaitAll(
                [Threading.Tasks.Task[]]@($stdoutTask, $stderrTask),
                30000
            )) {
            throw "$StageName output pipes did not close within 30 seconds."
        }
        $stdout = [string]$stdoutTask.GetAwaiter().GetResult()
        $stderr = [string]$stderrTask.GetAwaiter().GetResult()
        $exitCode = $process.ExitCode
    } finally {
        try {
            if ($processStarted -and -not $process.HasExited) {
                try {
                    $process.Kill($true)
                } catch {
                    throw "$StageName cleanup could not terminate its process tree."
                }
                if (-not $process.WaitForExit(30000)) {
                    throw "$StageName cleanup could not confirm process-tree exit within 30 seconds."
                }
            }
        } finally {
            $process.Dispose()
        }
    }
    if ($exitCode -ne 0 -or -not [string]::IsNullOrWhiteSpace($stderr)) {
        throw 'json_command_failed'
    }
    $output = @(
        ($stdout -split "`r?`n") |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )
    if ($output.Count -ne 1) { throw 'json_command_failed' }
    try {
        return $output[0] | ConvertFrom-Json -ErrorAction Stop
    } catch {
        throw 'json_command_invalid'
    }
}

function Assert-BridgeIdle {
    param(
        [Parameter(Mandatory = $true)][string]$Python,
        [Parameter(Mandatory = $true)][string]$Desktop,
        [Parameter(Mandatory = $true)][string]$Project
    )
    $dispatcher = Join-Path $Desktop 'scripts\feishu-codex-bridge.ps1'
    $bridge = Invoke-JsonCommand -Executable (Join-Path $PSHOME 'pwsh.exe') -Arguments @(
        '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
        '-File', $dispatcher, 'bridge', 'status', '-ProjectRoot', $Project, '-Json'
    ) -WorkingDirectory $Project -StageName 'bridge_status_precondition'
    if ($bridge.runtime.state -cne 'stopped' -or $bridge.runtime.running -ne $false -or
        $bridge.health_snapshot.status -cne 'stopped' -or
        $bridge.health_snapshot.event_consumer -ne $false -or
        [int]$bridge.health_snapshot.active_turns -ne 0 -or
        $bridge.health_snapshot.dial_inflight -ne $false -or
        [int]$bridge.health_snapshot.queue_counts.queued -ne 0 -or
        [int]$bridge.health_snapshot.queue_counts.running -ne 0 -or
        [int]$bridge.health_snapshot.queue_counts.control_sending -ne 0 -or
        [int]$bridge.health_snapshot.queue_counts.reply_pending -ne 0) {
        throw 'bridge_not_idle'
    }

    $beeper = Invoke-JsonCommand -Executable $Python -Arguments @(
        '-S', '-B', (Join-Path $Desktop 'scripts\beeper_queue_cli.py'),
        '--runtime-dir', (Join-Path $Project '.codex\feishu-bridge'),
        '--queue-namespace', 'beeper', 'status'
    ) -WorkingDirectory (Join-Path $Desktop 'scripts') -StageName 'beeper_queue_precondition'
    if ($beeper.ok -ne $true -or [int]$beeper.pending -ne 0 -or
        [int]$beeper.claimed -ne 0 -or $beeper.dial_inflight -ne $false) {
        throw 'beeper_queue_not_idle'
    }
}

$script:FastLaneStage = 'invocation'
try {
    $native = [System.Environment]::GetCommandLineArgs()
    $fileIndexes = @(
        for ($index = 0; $index -lt $native.Count; $index += 1) {
            if ([string]$native[$index] -ieq '-File') { $index }
        }
    )
    if ($fileIndexes.Count -ne 1) { throw 'unclean_powershell' }
    $fileIndex = [int]$fileIndexes[0]
    $flags = @($native[1..($fileIndex - 1)] | ForEach-Object { ([string]$_).ToLowerInvariant() })
    if (($flags -join "`n") -cne (@('-nologo','-noprofile','-noninteractive','-executionpolicy','bypass') -join "`n")) {
        throw 'unclean_powershell'
    }
    $script:FastLaneStage = 'shell_identity'
    $expectedShell = [IO.Path]::GetFullPath((Join-Path $PSHOME 'pwsh.exe'))
    $actualShell = [IO.Path]::GetFullPath([Diagnostics.Process]::GetCurrentProcess().MainModule.FileName)
    if (-not $actualShell.Equals($expectedShell, [StringComparison]::OrdinalIgnoreCase) -or
        [string]$PSVersionTable.PSEdition -cne 'Core' -or
        [version]$PSVersionTable.PSVersion -lt [version]'7.4') {
        throw 'unsupported_powershell'
    }
    $script:FastLaneStage = 'external_environment'
    if (-not $ExternalTestRunnerAcknowledged -or
        $env:FEISHU_BRIDGE_EXTERNAL_TEST_RUNNER -cne '1' -or
        $env:CODEX_BRIDGE_CHILD -eq '1') {
        throw 'external_supervisor_required'
    }

    $script:FastLaneStage = 'source_path'
    $desktop = [IO.Path]::GetFullPath($DesktopRoot)
    $project = if ($ProjectRoot) { [IO.Path]::GetFullPath($ProjectRoot) } else {
        [IO.Path]::GetFullPath((Split-Path -Parent (Split-Path -Parent $desktop)))
    }
    $scriptPath = [IO.Path]::GetFullPath($PSCommandPath)
    $expectedScript = [IO.Path]::GetFullPath((Join-Path $desktop 'scripts\invoke-external-fast-tests.ps1'))
    if (-not $scriptPath.Equals($expectedScript, [StringComparison]::OrdinalIgnoreCase) -or
        -not (Test-Path -LiteralPath $desktop -PathType Container) -or
        -not (Test-Path -LiteralPath $project -PathType Container)) {
        throw 'source_path_invalid'
    }

    $script:FastLaneStage = 'python_identity'
    $python = [IO.Path]::GetFullPath($PythonExecutable)
    if (-not (Test-Path -LiteralPath $python -PathType Leaf) -or
        -not ([IO.Path]::GetFileName($python)).Equals('python.exe', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'python_invalid'
    }
    $signature = Get-AuthenticodeSignature -LiteralPath $python -ErrorAction Stop
    if ([string]$signature.Status -cne 'Valid' -or
        [string]$signature.SignerCertificate.Subject -notmatch '(?i)(?:^|,\s*)O=Python Software Foundation(?:,|$)') {
        throw 'python_untrusted'
    }

    $script:FastLaneStage = 'ancestry'
    $visited = [Collections.Generic.HashSet[int]]::new()
    $currentPid = [int]$PID
    $depth = 0
    while ($depth -lt 64 -and $currentPid -gt 0) {
        if (-not $visited.Add($currentPid)) { throw 'process_ancestry_invalid' }
        $records = @(Get-CimInstance Win32_Process -Filter ("ProcessId = {0}" -f $currentPid) -ErrorAction SilentlyContinue)
        if ($records.Count -ne 1) {
            if ($depth -lt 2) { throw 'process_ancestry_unavailable' }
            $currentPid = 0
            break
        }
        $record = $records[0]
        if ([string]$record.Name -match '^(?i:codex)(?:\.exe)?$' -or
            [string]$record.ExecutablePath -match '(?i)[\\/]OpenAI\.Codex(?:_|[\\/])') {
            throw 'codex_ancestor_forbidden'
        }
        $currentPid = [int]$record.ParentProcessId
        $depth += 1
    }
    if ($currentPid -gt 0) { throw 'process_ancestry_depth_exceeded' }

    $script:FastLaneStage = 'source_route'
    $route = Invoke-JsonCommand -Executable $python -Arguments @(
        '-I', '-S', '-B', (Join-Path $desktop 'scripts\source_route_contract.py'),
        '--plugin-root', $desktop,
        '--marketplace', (Join-Path $project '.agents\plugins\marketplace.json')
    ) -WorkingDirectory $desktop -StageName 'source_route_precondition'
    if ($route.status -cne 'pass' -or $route.role -cne 'canonical-development' -or
        $route.development_source_eligible -ne $true) {
        throw 'canonical_source_required'
    }

    $script:FastLaneStage = 'pre_idle'
    Assert-BridgeIdle -Python $python -Desktop $desktop -Project $project

    $script:FastLaneStage = 'runner'
    $tempParent = [IO.Path]::TrimEndingDirectorySeparator([IO.Path]::GetFullPath([IO.Path]::GetTempPath()))
    $tempRoot = Join-Path $tempParent ('feishu-bridge-fast-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $tempRoot -ErrorAction Stop | Out-Null
    $process = $null
    $processStarted = $false
    $stdoutTask = $null
    $stderrTask = $null
    $runnerOutput = $null
    $desiredExit = 2
    try {
        $startInfo = [Diagnostics.ProcessStartInfo]::new()
        $startInfo.FileName = $python
        $startInfo.WorkingDirectory = $desktop
        $startInfo.UseShellExecute = $false
        $startInfo.CreateNoWindow = $true
        $startInfo.RedirectStandardOutput = $true
        $startInfo.RedirectStandardError = $true
        foreach ($argument in @(
            '-I', '-S', '-B', (Join-Path $desktop 'scripts\external_fast_test_runner.py'),
            '--tests-dir', (Join-Path $desktop 'tests')
        )) { [void]$startInfo.ArgumentList.Add($argument) }
        foreach ($name in @($startInfo.Environment.Keys)) {
            if ([string]$name -match '^(?i:PYTHON|CODEX_BRIDGE_|FEISHU_|LARK_|LARKCLI_)') {
                [void]$startInfo.Environment.Remove([string]$name)
            }
        }
        $startInfo.Environment['PYTHONDONTWRITEBYTECODE'] = '1'
        $startInfo.Environment['FEISHU_BRIDGE_EXTERNAL_TEST_RUNNER'] = '1'
        $startInfo.Environment['FEISHU_BRIDGE_TEST_TMP'] = $tempRoot
        $startInfo.Environment['Path'] = (
            (Split-Path -Parent $python) + [IO.Path]::PathSeparator +
            (Split-Path -Parent $expectedShell) + [IO.Path]::PathSeparator +
            [Environment]::SystemDirectory
        )
        $process = [Diagnostics.Process]::new()
        $process.StartInfo = $startInfo
        if (-not $process.Start()) { throw 'runner_start_failed' }
        $processStarted = $true
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            try {
                $process.Kill($true)
            } catch {
                throw 'runner_termination_failed'
            }
            if (-not $process.WaitForExit(10000)) {
                throw 'runner_termination_unconfirmed'
            }
            if (-not [Threading.Tasks.Task]::WaitAll(
                    [Threading.Tasks.Task[]]@($stdoutTask, $stderrTask),
                    30000
                )) {
                throw 'runner_output_pipe_timeout'
            }
            throw 'runner_timeout'
        }
        if (-not [Threading.Tasks.Task]::WaitAll(
                [Threading.Tasks.Task[]]@($stdoutTask, $stderrTask),
                30000
            )) {
            throw 'runner_output_pipe_timeout'
        }
        $stdout = $stdoutTask.GetAwaiter().GetResult().Trim()
        $stderr = $stderrTask.GetAwaiter().GetResult().Trim()
        $exitCode = $process.ExitCode
        if ($stderr -or @($stdout -split "\r?\n" | Where-Object { $_ }).Count -ne 1) {
            throw 'runner_output_invalid'
        }
        try { $result = $stdout | ConvertFrom-Json -ErrorAction Stop } catch { throw 'runner_json_invalid' }
        if ([int]$result.schema_version -ne 1 -or $result.lane -cne 'development-fast' -or
            $result.release_evidence -ne $false) {
            throw 'runner_contract_invalid'
        }
        if ($result.status -ceq 'pass') {
            if ($exitCode -ne 0 -or [int]$result.tests_run -ne 56 -or
                [int]$result.smoke_count -ne 12 -or [int]$result.contract_count -ne 25 -or
                [int]$result.fault_count -ne 19 -or $result.unexpected_test_output -ne $false) {
                throw 'runner_pass_contract_invalid'
            }
            $desiredExit = 0
        } elseif ($result.status -ceq 'fail' -and $exitCode -eq 1) {
            $desiredExit = 1
        } elseif ($result.status -ceq 'error' -and $exitCode -eq 2) {
            $desiredExit = 2
        } else {
            throw 'runner_status_invalid'
        }
        $runnerOutput = $stdout
    } finally {
        if ($null -ne $process -and $processStarted) {
            try {
                if (-not $process.HasExited) {
                    $process.Kill($true)
                    if (-not $process.WaitForExit(10000)) {
                        throw 'runner_not_stopped_cleanup_refused'
                    }
                }
            } finally { $process.Dispose() }
        } elseif ($null -ne $process) {
            $process.Dispose()
        }
        $resolvedTemp = [IO.Path]::GetFullPath($tempRoot)
        $resolvedParent = [IO.Path]::TrimEndingDirectorySeparator([IO.Path]::GetFullPath([IO.Path]::GetDirectoryName($resolvedTemp)))
        if (-not $resolvedParent.Equals($tempParent, [StringComparison]::OrdinalIgnoreCase) -or
            [IO.Path]::GetFileName($resolvedTemp) -notmatch '^feishu-bridge-fast-[a-f0-9]{32}$') {
            throw 'temp_cleanup_boundary_invalid'
        }
        $tempItem = Get-Item -LiteralPath $resolvedTemp -Force -ErrorAction Stop
        if (-not $tempItem.PSIsContainer -or
            (($tempItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
            throw 'temp_cleanup_reparse_refused'
        }
        Remove-Item -LiteralPath $resolvedTemp -Recurse -Force -ErrorAction Stop
    }

    $script:FastLaneStage = 'post_idle'
    Assert-BridgeIdle -Python $python -Desktop $desktop -Project $project
    if (-not $runnerOutput) { throw 'runner_output_missing' }
    $runnerOutput
    exit $desiredExit
} catch {
    Write-AnswerFreeResult -Status 'error' -ErrorCode ("fast_lane_{0}_error" -f $script:FastLaneStage)
    exit 2
}
