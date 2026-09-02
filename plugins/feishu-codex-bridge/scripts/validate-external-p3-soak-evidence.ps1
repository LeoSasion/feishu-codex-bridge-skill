[CmdletBinding()]
param(
    [string]$DesktopRoot = (Split-Path -Parent $PSScriptRoot),
    [Parameter(Mandatory = $true)][string]$HarnessRoot,
    [Parameter(Mandatory = $true)][string]$ProjectRoot,
    [Parameter(Mandatory = $true)][string]$EvidencePath,
    [Parameter(Mandatory = $true)][ValidatePattern('^[a-f0-9]{64}$')][string]$ExpectedEvidenceSha256,
    [Parameter(Mandatory = $true)][string]$P0EvidencePath,
    [Parameter(Mandatory = $true)][ValidatePattern('^[a-f0-9]{64}$')][string]$ExpectedP0EvidenceSha256
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$nativeInvocation = [System.Environment]::GetCommandLineArgs()
$fileArgumentIndexes = @(
    for ($index = 0; $index -lt $nativeInvocation.Count; $index += 1) {
        if ([string]$nativeInvocation[$index] -ieq '-File') { $index }
    }
)
if ($fileArgumentIndexes.Count -ne 1) {
    throw 'P3 soak evidence validation requires a clean pwsh -File invocation.'
}
$fileArgumentIndex = [int]$fileArgumentIndexes[0]
if ($fileArgumentIndex -lt 1 -or $fileArgumentIndex + 1 -ge $nativeInvocation.Count) {
    throw 'P3 soak evidence validation clean PowerShell invocation is incomplete.'
}
$hostFlags = @($nativeInvocation[1..($fileArgumentIndex - 1)] | ForEach-Object {
    ([string]$_).ToLowerInvariant()
})
$expectedHostFlags = @('-nologo', '-noprofile', '-noninteractive', '-executionpolicy', 'bypass')
$invokedScriptPath = [System.IO.Path]::GetFullPath([string]$nativeInvocation[$fileArgumentIndex + 1])
$expectedShellPath = [System.IO.Path]::GetFullPath([System.IO.Path]::Combine($PSHOME, 'pwsh.exe'))
$actualShellPath = [System.IO.Path]::GetFullPath(
    [System.Diagnostics.Process]::GetCurrentProcess().MainModule.FileName
)
if (($hostFlags -join "`n") -cne ($expectedHostFlags -join "`n") -or
    -not $actualShellPath.Equals($expectedShellPath, [System.StringComparison]::OrdinalIgnoreCase) -or
    -not $invokedScriptPath.Equals(
        [System.IO.Path]::GetFullPath($PSCommandPath),
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
    throw 'P3 soak evidence validation requires the exact script under clean pwsh flags.'
}
if ($env:CODEX_BRIDGE_CHILD -eq '1') {
    throw 'P3 soak evidence validation refuses to run from a Codex child process.'
}
if ([string]$PSVersionTable.PSEdition -cne 'Core' -or
    [version]$PSVersionTable.PSVersion -lt [version]'7.4') {
    throw 'P3 soak semantic validation requires PowerShell 7.4+.'
}
if (-not [System.Runtime.InteropServices.RuntimeInformation]::IsOSPlatform(
        [System.Runtime.InteropServices.OSPlatform]::Windows
    )) {
    throw 'P3 soak semantic validation currently supports Windows only.'
}

$requiredPowerShellModules = @(
    [System.IO.Path]::Combine($PSHOME, 'Modules', 'Microsoft.PowerShell.Utility', 'Microsoft.PowerShell.Utility.psd1'),
    [System.IO.Path]::Combine($PSHOME, 'Modules', 'Microsoft.PowerShell.Management', 'Microsoft.PowerShell.Management.psd1')
)
foreach ($modulePath in $requiredPowerShellModules) {
    if (-not [System.IO.File]::Exists($modulePath)) {
        throw 'P3 soak validator is missing a required built-in PowerShell module.'
    }
    Microsoft.PowerShell.Core\Import-Module -Name $modulePath -Force -ErrorAction Stop
}
$PSModuleAutoLoadingPreference = 'None'
$script:StrictUtf8 = New-Object System.Text.UTF8Encoding($false, $true)
$script:Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

function Get-FullPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    return [System.IO.Path]::GetFullPath($Path)
}

function Get-FileSha256 {
    param([Parameter(Mandatory = $true)][string]$Path)
    return (Microsoft.PowerShell.Utility\Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-StringSha256 {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Value)
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([System.BitConverter]::ToString(
            $algorithm.ComputeHash($script:Utf8NoBom.GetBytes($Value))
        )).Replace('-', '').ToLowerInvariant()
    } finally {
        $algorithm.Dispose()
    }
}

function Get-CurrentWindowsUserSid {
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    if ($null -eq $identity -or $null -eq $identity.User -or
        [string]::IsNullOrWhiteSpace([string]$identity.User.Value)) {
        throw 'P3 soak validator could not resolve the current Windows user SID.'
    }
    return [string]$identity.User.Value
}

function Get-LifecycleMutexName {
    param(
        [Parameter(Mandatory = $true)][string]$Project,
        [Parameter(Mandatory = $true)][string]$UserSid
    )
    $material = (Get-FullPath -Path $Project).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    ).ToUpperInvariant()
    return 'Global\FeishuCodexBridge-Lifecycle-' +
        (Get-StringSha256 -Value ($UserSid + "`n" + $material)).Substring(0, 24)
}

function ConvertTo-P3DateTimeOffset {
    param(
        [Parameter(Mandatory = $true)][object]$Value,
        [Parameter(Mandatory = $true)][string]$Role
    )
    if ($Value -is [DateTimeOffset]) {
        return [DateTimeOffset]$Value
    }
    if ($Value -is [DateTime]) {
        if (([DateTime]$Value).Kind -eq [DateTimeKind]::Unspecified) {
            throw "$Role must include an explicit offset."
        }
        return [DateTimeOffset]([DateTime]$Value)
    }
    $parsed = [DateTimeOffset]::MinValue
    if (-not [DateTimeOffset]::TryParse(
            [string]$Value,
            [System.Globalization.CultureInfo]::InvariantCulture,
            [System.Globalization.DateTimeStyles]::RoundtripKind,
            [ref]$parsed
        )) {
        throw "$Role is not a round-trip date-time value."
    }
    return $parsed
}

function Read-StrictUtf8 {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [ValidateRange(1, 16777216)][int]$MaximumBytes
    )
    $item = Microsoft.PowerShell.Management\Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if ($item.Length -gt $MaximumBytes) { throw "P3 retained file exceeds its size bound: $($item.Name)" }
    return $script:StrictUtf8.GetString([System.IO.File]::ReadAllBytes($item.FullName))
}

function Assert-NoReparsePathChain {
    param([Parameter(Mandatory = $true)][string]$Path)
    $current = Microsoft.PowerShell.Management\Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    while ($null -ne $current) {
        if (($current.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "P3 retained path chain contains a reparse point: $($current.Name)"
        }
        if ($current -is [System.IO.FileInfo]) {
            $current = $current.Directory
        } elseif ($current -is [System.IO.DirectoryInfo]) {
            $current = $current.Parent
        } else {
            throw "P3 retained path chain contains an unsupported filesystem item: $($current.FullName)"
        }
    }
}

function Test-IsWithinRoot {
    param(
        [Parameter(Mandatory = $true)][string]$Candidate,
        [Parameter(Mandatory = $true)][string]$Root
    )
    $candidatePath = (Get-FullPath -Path $Candidate).TrimEnd('\')
    $rootPath = (Get-FullPath -Path $Root).TrimEnd('\')
    if ($candidatePath.Equals($rootPath, [System.StringComparison]::OrdinalIgnoreCase)) { return $true }
    return $candidatePath.StartsWith($rootPath + '\', [System.StringComparison]::OrdinalIgnoreCase)
}

function Assert-UniqueJsonObjectKeys {
    param(
        [Parameter(Mandatory = $true)][string]$Json,
        [Parameter(Mandatory = $true)][string]$Role
    )
    $document = [System.Text.Json.JsonDocument]::Parse($Json)
    try {
        $pending = New-Object System.Collections.Generic.Stack[System.Text.Json.JsonElement]
        $pending.Push($document.RootElement)
        while ($pending.Count -gt 0) {
            $element = $pending.Pop()
            if ($element.ValueKind -eq [System.Text.Json.JsonValueKind]::Object) {
                $names = New-Object 'System.Collections.Generic.HashSet[string]' `
                    ([System.StringComparer]::Ordinal)
                foreach ($property in $element.EnumerateObject()) {
                    if (-not $names.Add($property.Name)) { throw "$Role contains a duplicate JSON key." }
                    $pending.Push($property.Value)
                }
            } elseif ($element.ValueKind -eq [System.Text.Json.JsonValueKind]::Array) {
                foreach ($item in $element.EnumerateArray()) { $pending.Push($item) }
            }
        }
    } finally {
        $document.Dispose()
    }
}

function Test-ExactJsonPropertySet {
    param(
        [Parameter(Mandatory = $true)][AllowNull()][object]$Value,
        [Parameter(Mandatory = $true)][string[]]$Names
    )
    if ($null -eq $Value) { return $false }
    $actual = @($Value.PSObject.Properties.Name | Sort-Object)
    $expected = @($Names | Sort-Object)
    return ($actual -join "`n") -ceq ($expected -join "`n")
}

function Test-JsonBooleanValue {
    param(
        [Parameter(Mandatory = $true)][AllowNull()][object]$Value,
        [Parameter(Mandatory = $true)][bool]$Expected
    )
    return ($Value -is [bool]) -and ([bool]$Value -eq $Expected)
}

function Test-JsonIntegerZero {
    param([Parameter(Mandatory = $true)][AllowNull()][object]$Value)
    return ($Value -is [int] -or $Value -is [long]) -and ([long]$Value -eq 0)
}

function Test-MvpObservation {
    param([Parameter(Mandatory = $true)][AllowNull()][object]$Value)
    if ($null -eq $Value) { return $true }
    $schemaVersionIsInteger = (
        $Value.schema_version -is [int] -or
        $Value.schema_version -is [long]
    )
    return (
        (Test-ExactJsonPropertySet -Value $Value -Names @(
            'schema_version', 'status', 'answer_free', 'producer_namespace',
            'final_callback_source', 'feishu_delivery_observed',
            'known_delivery_fidelity_observed', 'single_inbox_claim_observed',
            'bridge_outbox_scrubbed'
        )) -and
        $schemaVersionIsInteger -and [long]$Value.schema_version -eq 1 -and
        [string]$Value.status -ceq 'passed' -and
        (Test-JsonBooleanValue -Value $Value.answer_free -Expected $true) -and
        [string]$Value.producer_namespace -ceq 'beeper' -and
        [string]$Value.final_callback_source -ceq 'final_callback' -and
        (Test-JsonBooleanValue -Value $Value.feishu_delivery_observed -Expected $true) -and
        (Test-JsonBooleanValue -Value $Value.known_delivery_fidelity_observed -Expected $true) -and
        (Test-JsonBooleanValue -Value $Value.single_inbox_claim_observed -Expected $true) -and
        (Test-JsonBooleanValue -Value $Value.bridge_outbox_scrubbed -Expected $true)
    )
}

function Test-AdmissibleStoppedStatus {
    param([Parameter(Mandatory = $true)]$Status)

    $statusCode = [string]$Status.status
    $manifest = $Status.installed_manifest
    if ($null -eq $manifest) { return $false }
    $issueCodesIsArray = $manifest.issue_codes -is [System.Array]
    $issueCodes = @($manifest.issue_codes)
    $issueCountIsInteger = (
        $manifest.issue_count -is [int] -or
        $manifest.issue_count -is [long]
    )

    if ($statusCode -ceq 'pass') {
        return (
            (Test-JsonBooleanValue -Value $manifest.present -Expected $true) -and
            (Test-JsonBooleanValue -Value $manifest.valid -Expected $true) -and
            -not [string]::IsNullOrWhiteSpace([string]$manifest.bridge_version) -and
            $issueCountIsInteger -and [long]$manifest.issue_count -eq 0 -and
            $issueCodesIsArray -and
            $issueCodes.Count -eq 0 -and
            $null -eq $Status.health_issue
        )
    }
    if ($statusCode -cne 'warning') { return $false }
    return (
        (Test-JsonBooleanValue -Value $manifest.present -Expected $false) -and
        (Test-JsonBooleanValue -Value $manifest.valid -Expected $false) -and
        $null -eq $manifest.bridge_version -and
        $issueCountIsInteger -and [long]$manifest.issue_count -eq 1 -and
        $issueCodesIsArray -and
        $issueCodes.Count -eq 1 -and
        [string]$issueCodes[0] -ceq 'integrity_check_failed' -and
        $null -eq $Status.health_issue
    )
}

function Invoke-P0Validator {
    param(
        [Parameter(Mandatory = $true)][string]$Shell,
        [Parameter(Mandatory = $true)][string]$Validator,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $Shell
    foreach ($argument in @(
        '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
        '-File', $Validator
    ) + $Arguments) {
        [void]$startInfo.ArgumentList.Add([string]$argument)
    }
    $startInfo.WorkingDirectory = Split-Path -Parent $Validator
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    $processStarted = $false
    $stdoutTask = $null
    $stderrTask = $null
    try {
        if (-not $process.Start()) { throw 'P0 validator child did not start.' }
        $processStarted = $true
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(240000)) {
            try {
                $process.Kill($true)
            } catch {
                throw 'P0 validator child timed out and its process tree could not be terminated.'
            }
            if (-not $process.WaitForExit(30000)) {
                throw 'P0 validator child timed out and its process tree did not exit within 30 seconds.'
            }
            if (-not [System.Threading.Tasks.Task]::WaitAll(
                    [System.Threading.Tasks.Task[]]@($stdoutTask, $stderrTask),
                    30000
                )) {
                throw 'P0 validator child timed out and its output pipes did not close within 30 seconds.'
            }
            throw 'P0 validator child exceeded 240 seconds.'
        }
        if (-not [System.Threading.Tasks.Task]::WaitAll(
                [System.Threading.Tasks.Task[]]@($stdoutTask, $stderrTask),
                30000
            )) {
            throw 'P0 validator output pipes did not close.'
        }
        $stdout = $stdoutTask.GetAwaiter().GetResult()
        $stderr = $stderrTask.GetAwaiter().GetResult()
        if ($process.ExitCode -ne 0) { throw "Current P0 validation failed: $stderr" }
        if (-not [string]::IsNullOrWhiteSpace($stderr)) {
            throw 'Current P0 validator wrote unexpected stderr despite a zero exit code.'
        }
        $lines = @($stdout -split '\r?\n' | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
        if ($lines.Count -ne 1) { throw 'Current P0 validator did not return one JSON object.' }
        return $lines[0] | Microsoft.PowerShell.Utility\ConvertFrom-Json -ErrorAction Stop
    } finally {
        if ($processStarted) {
            if (-not $process.HasExited) {
                try {
                    $process.Kill($true)
                } catch {
                    throw 'P0 validator cleanup could not terminate its process tree.'
                }
                if (-not $process.WaitForExit(30000)) {
                    throw 'P0 validator cleanup could not confirm process-tree exit within 30 seconds.'
                }
            }
            if ($null -ne $stdoutTask -and $null -ne $stderrTask -and
                -not [System.Threading.Tasks.Task]::WaitAll(
                    [System.Threading.Tasks.Task[]]@($stdoutTask, $stderrTask),
                    30000
                )) {
                throw 'P0 validator cleanup could not drain output pipes within 30 seconds.'
            }
        }
        $process.Dispose()
    }
}

function Add-PinnedReadHandle {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [System.Collections.Generic.List[System.IO.FileStream]]$Pins
    )
    $stream = New-Object System.IO.FileStream(
        $Path,
        [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Read,
        [System.IO.FileShare]::Read
    )
    $Pins.Add($stream)
}

function Get-RetainedBridgeObservation {
    param(
        [Parameter(Mandatory = $true)][ValidateSet('pre', 'post', 'final')][string]$Label,
        [Parameter(Mandatory = $true)][object]$Expected,
        [Parameter(Mandatory = $true)][string]$WorkDirectory,
        [Parameter(Mandatory = $true)][string]$ExpectedShellExecutable,
        [Parameter(Mandatory = $true)][string]$ExpectedPythonExecutable,
        [Parameter(Mandatory = $true)][string]$ExpectedDispatcher,
        [Parameter(Mandatory = $true)][string]$ExpectedBeeperHelper,
        [Parameter(Mandatory = $true)][string]$ExpectedProject,
        [Parameter(Mandatory = $true)][string]$ExpectedRuntime,
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [System.Collections.Generic.List[System.IO.FileStream]]$Pins
    )
    $stdoutPath = Join-Path $WorkDirectory ("status-{0}.stdout.txt" -f $Label)
    $stderrPath = Join-Path $WorkDirectory ("status-{0}.stderr.txt" -f $Label)
    $beeperStdoutPath = Join-Path $WorkDirectory ("beeper-status-{0}.stdout.txt" -f $Label)
    $beeperStderrPath = Join-Path $WorkDirectory ("beeper-status-{0}.stderr.txt" -f $Label)
    foreach ($path in @($stdoutPath, $stderrPath, $beeperStdoutPath, $beeperStderrPath)) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "P3 retained Bridge $Label observation is missing."
        }
        Assert-NoReparsePathChain -Path $path
        Add-PinnedReadHandle -Path $path -Pins $Pins
    }
    if ((Get-FileSha256 -Path $stdoutPath) -cne [string]$Expected.status_stdout_sha256 -or
        (Get-FileSha256 -Path $stderrPath) -cne [string]$Expected.status_stderr_sha256 -or
        (Get-FileSha256 -Path $beeperStdoutPath) -cne
            [string]$Expected.beeper_status_stdout_sha256 -or
        (Get-FileSha256 -Path $beeperStderrPath) -cne
            [string]$Expected.beeper_status_stderr_sha256) {
        throw "P3 retained Bridge $Label observation hash differs from the receipt."
    }
    if ((Microsoft.PowerShell.Management\Get-Item -LiteralPath $stderrPath).Length -ne 0 -or
        (Microsoft.PowerShell.Management\Get-Item -LiteralPath $beeperStderrPath).Length -ne 0) {
        throw "P3 retained Bridge $Label status stderr capture is not empty."
    }
    $statusJson = Read-StrictUtf8 -Path $stdoutPath -MaximumBytes 1048576
    Assert-UniqueJsonObjectKeys -Json $statusJson -Role "P3 Bridge status $Label"
    $status = $statusJson | Microsoft.PowerShell.Utility\ConvertFrom-Json -ErrorAction Stop
    $health = $status.health_snapshot
    $queueCounts = if ($null -eq $health) { $null } else { $health.queue_counts }
    $healthVersionRelationIsAdmissible = (
        -not [string]::IsNullOrWhiteSpace([string]$health.bridge_version) -and
        (([string]$status.status -ceq 'pass' -and
          [string]$health.bridge_version -ceq
            [string]$status.installed_manifest.bridge_version) -or
         ([string]$status.status -ceq 'warning' -and
          $null -eq $status.installed_manifest.bridge_version))
    )
    $expectedPidFileState = [string]$Expected.pid_file_state
    $statusPidStateMatches = if ($expectedPidFileState -ceq 'absent') {
        [string]$status.runtime.pid_file_state -ceq 'absent'
    } elseif ($expectedPidFileState -ceq 'stale') {
        [string]$status.runtime.pid_file_state -cin @('invalid', 'stale_process_absent', 'stale_foreign_process')
    } else {
        $false
    }
    if (-not (Test-ExactJsonPropertySet -Value $status -Names @(
            'schema_version', 'command', 'status', 'runtime', 'installed_manifest',
            'health_snapshot', 'health_issue'
        )) -or
        -not (Test-ExactJsonPropertySet -Value $status.runtime -Names @(
            'state', 'running', 'pid', 'pid_file_present', 'pid_file_state',
            'identity_verified', 'identity_is_bridge'
        )) -or
        -not (Test-ExactJsonPropertySet -Value $status.installed_manifest -Names @(
            'present', 'valid', 'bridge_version', 'issue_count', 'issue_codes'
        )) -or
        -not (Test-ExactJsonPropertySet -Value $health -Names @(
            'present', 'valid', 'status', 'bridge_version', 'event_consumer',
            'session_owner', 'beeper_state', 'beeper_transport', 'active_turns',
            'schema_current', 'process_identity_current', 'runtime_manifest_current', 'snapshot_fresh',
            'dial_inflight', 'dial_lease_remaining_seconds', 'beeper_pending',
            'beeper_claimed', 'actionable_retryable_failed', 'queue_counts',
            'latest_delivery_fidelity', 'mvp_observation'
        )) -or
        -not (Test-ExactJsonPropertySet -Value $queueCounts -Names @(
            'queued', 'running', 'control_sending', 'reply_pending',
            'retryable_failed', 'completed', 'terminal_failed'
        )) -or
        ($null -ne $health.latest_delivery_fidelity -and
            -not (Test-ExactJsonPropertySet -Value $health.latest_delivery_fidelity `
                -Names @('fidelity', 'transforms'))) -or
        -not (Test-MvpObservation `
            -Value $health.mvp_observation) -or
        [int]$status.schema_version -ne 1 -or
        [string]$status.command -cne 'bridge.status' -or
        -not (Test-AdmissibleStoppedStatus -Status $status) -or
        -not $healthVersionRelationIsAdmissible -or
        [string]$status.runtime.state -cne 'stopped' -or
        -not (Test-JsonBooleanValue -Value $status.runtime.running -Expected $false) -or
        $null -ne $status.runtime.pid -or
        $null -ne $status.health_issue -or
        $null -eq $health -or
        -not (Test-JsonBooleanValue -Value $health.present -Expected $true) -or
        -not (Test-JsonBooleanValue -Value $health.valid -Expected $true) -or
        [string]$health.status -cne 'stopped' -or
        -not (Test-JsonBooleanValue -Value $health.event_consumer -Expected $false) -or
        -not (Test-JsonIntegerZero -Value $health.active_turns) -or
        $health.schema_current -isnot [bool] -or
        $health.process_identity_current -isnot [bool] -or
        -not (Test-JsonBooleanValue -Value $health.process_identity_current -Expected $false) -or
        $health.runtime_manifest_current -isnot [bool] -or
        $health.snapshot_fresh -isnot [bool] -or
        -not (Test-JsonBooleanValue -Value $health.dial_inflight -Expected $false) -or
        $null -ne $health.dial_lease_remaining_seconds -or
        -not (Test-JsonIntegerZero -Value $health.beeper_pending) -or
        -not (Test-JsonIntegerZero -Value $health.beeper_claimed) -or
        (([bool]$health.schema_current -and
          -not (Test-JsonIntegerZero -Value $health.actionable_retryable_failed)) -or
         (-not [bool]$health.schema_current -and
          $null -ne $health.actionable_retryable_failed)) -or
        $null -eq $queueCounts -or
        -not (Test-JsonIntegerZero -Value $queueCounts.queued) -or
        -not (Test-JsonIntegerZero -Value $queueCounts.running) -or
        -not (Test-JsonIntegerZero -Value $queueCounts.control_sending) -or
        -not (Test-JsonIntegerZero -Value $queueCounts.reply_pending) -or
        -not $statusPidStateMatches -or
        -not (Test-JsonBooleanValue -Value $status.runtime.pid_file_present `
            -Expected ($expectedPidFileState -ceq 'stale')) -or
        [string]$Expected.state -cne 'stopped' -or
        [int]$Expected.status_exit_code -ne 0 -or
        $expectedPidFileState -cnotin @('absent', 'stale') -or
        [bool]$Expected.pid_process_alive -or
        [int]$Expected.matching_bridge_process_count -ne 0 -or
        [string]$Expected.capture_contract -cne 'answer_free_idle_status_v1' -or
        -not [bool]$Expected.health_snapshot_present -or
        -not [bool]$Expected.health_snapshot_valid -or
        [string]$Expected.health_status -cne 'stopped' -or
        [bool]$Expected.health_event_consumer -or
        [int]$Expected.health_active_turns -ne 0 -or
        [bool]$Expected.health_dial_inflight -or
        $null -ne $Expected.health_dial_lease_remaining_seconds -or
        [int]$Expected.health_queue_counts.queued -ne 0 -or
        [int]$Expected.health_queue_counts.running -ne 0 -or
        [int]$Expected.health_queue_counts.control_sending -ne 0 -or
        [int]$Expected.health_queue_counts.reply_pending -ne 0) {
        throw "P3 retained Bridge $Label observation is not an exact stopped checkpoint."
    }

    $expectedStatusArgv = @(
        $ExpectedShellExecutable,
        '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
        '-File', $ExpectedDispatcher,
        'bridge', 'status', '-ProjectRoot', $ExpectedProject, '-Json'
    )
    $expectedBeeperArgv = @(
        $ExpectedPythonExecutable,
        '-S', '-B', $ExpectedBeeperHelper,
        '--runtime-dir', $ExpectedRuntime,
        '--queue-namespace', 'beeper',
        'status'
    )
    if ((@($Expected.status_argv) -join "`n") -cne ($expectedStatusArgv -join "`n") -or
        (@($Expected.beeper_status_argv) -join "`n") -cne
            ($expectedBeeperArgv -join "`n")) {
        throw "P3 retained Bridge $Label observation argv differs from the fixed commands."
    }

    $beeperJson = Read-StrictUtf8 -Path $beeperStdoutPath -MaximumBytes 1048576
    Assert-UniqueJsonObjectKeys -Json $beeperJson -Role "P3 Beeper queue status $Label"
    $beeperStatus = $beeperJson | Microsoft.PowerShell.Utility\ConvertFrom-Json -ErrorAction Stop
    if (-not (Test-ExactJsonPropertySet -Value $beeperStatus -Names @(
            'claimed', 'beeper_host_id', 'ok', 'pending', 'registered', 'beeper_thread_id',
            'dial_generation', 'dial_inflight', 'dial_lease_remaining_seconds'
        )) -or
        -not (Test-JsonBooleanValue -Value $beeperStatus.ok -Expected $true) -or
        -not (Test-JsonIntegerZero -Value $beeperStatus.pending) -or
        -not (Test-JsonIntegerZero -Value $beeperStatus.claimed) -or
        -not (Test-JsonBooleanValue -Value $beeperStatus.dial_inflight -Expected $false) -or
        $null -ne $beeperStatus.dial_lease_remaining_seconds -or
        [int]$Expected.beeper_status_exit_code -ne 0 -or
        [string]$Expected.beeper_source_role -cne 'p0_source_snapshot' -or
        [string]$Expected.beeper_runtime_role -cne 'project_runtime' -or
        [string]$Expected.beeper_queue_cli_namespace -cne 'beeper' -or
        -not [bool]$Expected.beeper_ok -or
        [int]$Expected.beeper_pending -ne 0 -or
        [int]$Expected.beeper_claimed -ne 0 -or
        [bool]$Expected.beeper_dial_inflight -or
        $null -ne $Expected.beeper_dial_lease_remaining_seconds) {
        throw "P3 retained Beeper queue $Label observation is not the exact idle contract."
    }
    return ConvertTo-P3DateTimeOffset -Value $Expected.observed_at_utc `
        -Role "P3 Bridge $Label observation timestamp"
}

$desktop = Get-FullPath -Path $DesktopRoot
$harness = Get-FullPath -Path $HarnessRoot
$project = Get-FullPath -Path $ProjectRoot
$evidence = Get-FullPath -Path $EvidencePath
$p0Evidence = Get-FullPath -Path $P0EvidencePath
foreach ($path in @($desktop, $harness, $project, $evidence, $p0Evidence)) {
    if (-not (Test-Path -LiteralPath $path)) { throw "P3 validation path does not exist: $path" }
    Assert-NoReparsePathChain -Path $path
}
$validatorPins = New-Object 'System.Collections.Generic.List[System.IO.FileStream]'
try {
Add-PinnedReadHandle -Path $evidence -Pins $validatorPins
Add-PinnedReadHandle -Path $p0Evidence -Pins $validatorPins
if ((Get-FileSha256 -Path $evidence) -cne $ExpectedEvidenceSha256) {
    throw 'P3 evidence SHA-256 does not match its envelope.'
}
if ((Get-FileSha256 -Path $p0Evidence) -cne $ExpectedP0EvidenceSha256) {
    throw 'P0 evidence SHA-256 does not match the P3 validation input.'
}
$p0Json = Read-StrictUtf8 -Path $p0Evidence -MaximumBytes 4194304
Assert-UniqueJsonObjectKeys -Json $p0Json -Role 'P0 evidence bound by P3'
$p0Receipt = $p0Json | Microsoft.PowerShell.Utility\ConvertFrom-Json -ErrorAction Stop
if ([int]$p0Receipt.schema_version -ne 2 -or
    [string]$p0Receipt.evidence_kind -cne 'feishu-codex-bridge.p0b.external-test' -or
    [string]$p0Receipt.execution.runner_status -cne 'pass') {
    throw 'P3 validation requires one passing P0-B v2 receipt.'
}
$currentComponentRoots = @{
    desktop_bridge = $desktop
    harness_sibling = $harness
}
foreach ($component in @($p0Receipt.release_audit.components)) {
    $componentName = [string]$component.name
    if (-not $currentComponentRoots.ContainsKey($componentName) -or
        [int]$component.file_count -ne @($component.files).Count) {
        throw 'P0 evidence bound by P3 has an invalid current source component.'
    }
    $componentRoot = [string]$currentComponentRoots[$componentName]
    foreach ($fileRecord in @($component.files)) {
        $relativePath = [string]$fileRecord.path
        $segments = @($relativePath.Split('/'))
        if ([string]::IsNullOrWhiteSpace($relativePath) -or
            [System.IO.Path]::IsPathRooted($relativePath) -or
            $relativePath.Contains('\') -or
            $relativePath.Contains(':') -or
            @($segments | Where-Object { $_ -in @('', '.', '..') }).Count -ne 0) {
            throw 'P0 evidence bound by P3 contains an unsafe current source path.'
        }
        $currentPath = Get-FullPath -Path (
            Join-Path $componentRoot ($relativePath.Replace('/', '\'))
        )
        if (-not (Test-IsWithinRoot -Candidate $currentPath -Root $componentRoot) -or
            -not (Test-Path -LiteralPath $currentPath -PathType Leaf)) {
            throw 'P0 evidence bound by P3 current source path is missing or escaped its root.'
        }
        Assert-NoReparsePathChain -Path $currentPath
        Add-PinnedReadHandle -Path $currentPath -Pins $validatorPins
    }
}
if (@($p0Receipt.release_audit.components).Count -ne 2 -or
    (@($p0Receipt.release_audit.components.name | Sort-Object) -join "`n") -cne
        ((@('desktop_bridge', 'harness_sibling') | Sort-Object) -join "`n")) {
    throw 'P0 evidence bound by P3 does not define the exact current source roots.'
}
$p0DesktopComponents = @(
    $p0Receipt.release_audit.components | Where-Object { [string]$_.name -ceq 'desktop_bridge' }
)
if ($p0DesktopComponents.Count -ne 1 -or
    [int]$p0DesktopComponents[0].file_count -ne @($p0DesktopComponents[0].files).Count) {
    throw 'P0 evidence bound by P3 has an invalid Desktop source-file contract.'
}

$evidenceJson = Read-StrictUtf8 -Path $evidence -MaximumBytes 4194304
Assert-UniqueJsonObjectKeys -Json $evidenceJson -Role 'P3 evidence'
$schemaPath = Join-Path $desktop 'assets\external-p3-soak-evidence.schema.json'
Assert-NoReparsePathChain -Path $schemaPath
Add-PinnedReadHandle -Path $schemaPath -Pins $validatorPins
if (-not ($evidenceJson | Microsoft.PowerShell.Utility\Test-Json -SchemaFile $schemaPath -ErrorAction Stop)) {
    throw 'P3 evidence failed the current JSON Schema.'
}
$receipt = $evidenceJson | Microsoft.PowerShell.Utility\ConvertFrom-Json -ErrorAction Stop
$expectedEvidenceName = 'p3-soak-v2-' + [string]$receipt.receipt_id + '.json'
if ([System.IO.Path]::GetFileName($evidence) -cne $expectedEvidenceName) {
    throw 'P3 evidence filename does not match its receipt ID.'
}
if ([System.IO.Path]::GetFileName($p0Evidence) -cne [string]$receipt.p0_evidence.file -or
    [string]$receipt.p0_evidence.sha256 -cne $ExpectedP0EvidenceSha256) {
    throw 'P3 evidence does not bind the supplied P0 receipt.'
}
if ([int]$receipt.guards.process_ancestry_depth -lt 2 -or
    [int]$receipt.guards.process_ancestry_depth -gt 64 -or
    [int]$receipt.guards.codex_ancestor_match_count -ne 0 -or
    [string]$receipt.guards.runner_surface -notin @('external_terminal', 'ci') -or
    [string]$receipt.guards.process_ancestry_termination -notin @(
        'process_tree_root', 'exited_ancestor'
    ) -or
    [bool]$receipt.guards.process_ancestry_complete -ne
        ([string]$receipt.guards.process_ancestry_termination -ceq 'process_tree_root') -or
    -not [bool]$receipt.guards.bridge_stopped -or
    -not [bool]$receipt.guards.p0_pre_validated -or
    -not [bool]$receipt.guards.p0_evidence_rehashed_after_run -or
    -not [bool]$receipt.guards.source_snapshot_reused -or
    -not [bool]$receipt.guards.snapshot_files_pinned -or
    -not [bool]$receipt.guards.source_unchanged -or
    -not [bool]$receipt.guards.job_object_enforced -or
    [int]$receipt.guards.snapshot_file_count -ne [int]$p0DesktopComponents[0].file_count) {
    throw 'P3 evidence external-origin or snapshot-pinning guard is invalid.'
}

$workDirectory = Get-FullPath -Path ([string]$receipt.execution.working_directory)
$expectedWorkDirectoryLeaf = 's-' + (
    Get-StringSha256 -Value ([string]$receipt.receipt_id)
).Substring(0, 8)
if ([System.IO.Path]::GetFileName($workDirectory) -cne $expectedWorkDirectoryLeaf -or
    -not (Test-Path -LiteralPath $workDirectory -PathType Container)) {
    throw 'P3 evidence does not identify its retained work directory.'
}
Assert-NoReparsePathChain -Path $workDirectory
foreach ($protectedRoot in @($desktop, $harness, $project)) {
    if ((Test-IsWithinRoot -Candidate $workDirectory -Root $protectedRoot) -or
        (Test-IsWithinRoot -Candidate $evidence -Root $protectedRoot)) {
        throw 'P3 retained work or evidence entered a source/live project root.'
    }
}

$resultPath = Join-Path $workDirectory 'structured-soak-result.json'
$stdoutPath = Join-Path $workDirectory 'soak-runner.stdout.txt'
$stderrPath = Join-Path $workDirectory 'soak-runner.stderr.txt'
foreach ($retainedPath in @($resultPath, $stdoutPath, $stderrPath)) {
    if (-not (Test-Path -LiteralPath $retainedPath -PathType Leaf)) {
        throw 'P3 retained evidence artifact is missing.'
    }
    Assert-NoReparsePathChain -Path $retainedPath
    Add-PinnedReadHandle -Path $retainedPath -Pins $validatorPins
}
if ((Get-FileSha256 -Path $resultPath) -cne [string]$receipt.execution.result_sha256 -or
    (Get-FileSha256 -Path $stdoutPath) -cne [string]$receipt.execution.stdout_sha256 -or
    (Get-FileSha256 -Path $stderrPath) -cne [string]$receipt.execution.stderr_sha256) {
    throw 'P3 retained artifact hash differs from the receipt.'
}
if ((Microsoft.PowerShell.Management\Get-Item -LiteralPath $stdoutPath).Length -ne 0) {
    throw 'P3 retained runner stdout is not empty.'
}
$p0SnapshotRoot = Get-FullPath -Path ([string]$p0Receipt.execution.working_directory)
$expectedPython = Get-FullPath -Path ([string]$p0Receipt.execution.argv[0])
$expectedShell = Get-FullPath -Path (Join-Path $PSHOME 'pwsh.exe')
$expectedDispatcher = Get-FullPath -Path (
    Join-Path $p0SnapshotRoot 'scripts\feishu-codex-bridge.ps1'
)
$expectedBeeperHelper = Get-FullPath -Path (
    Join-Path $p0SnapshotRoot 'scripts\beeper_queue_cli.py'
)
$expectedRuntime = Get-FullPath -Path (Join-Path $project '.codex\feishu-bridge')
if ([System.IO.Path]::GetFileName($p0SnapshotRoot) -cne 'source-snapshot' -or
    -not (Test-Path -LiteralPath $p0SnapshotRoot -PathType Container)) {
    throw 'P3 bound P0 receipt does not identify its retained source snapshot.'
}
foreach ($observationExecutable in @(
        $expectedPython, $expectedShell, $expectedDispatcher, $expectedBeeperHelper
    )) {
    if (-not (Test-Path -LiteralPath $observationExecutable -PathType Leaf)) {
        throw 'P3 retained idle observation executable is missing.'
    }
    Assert-NoReparsePathChain -Path $observationExecutable
}
$preObservedAt = Get-RetainedBridgeObservation -Label 'pre' `
    -Expected $receipt.bridge_stopped_receipt.pre -WorkDirectory $workDirectory `
    -ExpectedShellExecutable $expectedShell -ExpectedPythonExecutable $expectedPython `
    -ExpectedDispatcher $expectedDispatcher -ExpectedBeeperHelper $expectedBeeperHelper `
    -ExpectedProject $project -ExpectedRuntime $expectedRuntime `
    -Pins $validatorPins
$postObservedAt = Get-RetainedBridgeObservation -Label 'post' `
    -Expected $receipt.bridge_stopped_receipt.post -WorkDirectory $workDirectory `
    -ExpectedShellExecutable $expectedShell -ExpectedPythonExecutable $expectedPython `
    -ExpectedDispatcher $expectedDispatcher -ExpectedBeeperHelper $expectedBeeperHelper `
    -ExpectedProject $project -ExpectedRuntime $expectedRuntime `
    -Pins $validatorPins
$finalObservedAt = Get-RetainedBridgeObservation -Label 'final' `
    -Expected $receipt.bridge_stopped_receipt.final -WorkDirectory $workDirectory `
    -ExpectedShellExecutable $expectedShell -ExpectedPythonExecutable $expectedPython `
    -ExpectedDispatcher $expectedDispatcher -ExpectedBeeperHelper $expectedBeeperHelper `
    -ExpectedProject $project -ExpectedRuntime $expectedRuntime `
    -Pins $validatorPins

$resultJson = Read-StrictUtf8 -Path $resultPath -MaximumBytes 4194304
Assert-UniqueJsonObjectKeys -Json $resultJson -Role 'P3 structured result'
$result = $resultJson | Microsoft.PowerShell.Utility\ConvertFrom-Json -ErrorAction Stop
$expectedScenarios = [ordered]@{
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
$actualScenarioPairs = @($result.scenario_contract | ForEach-Object {
    ([string]$_.scenario_id) + '|' + ([string]$_.test_id)
})
$expectedScenarioPairs = @($expectedScenarios.GetEnumerator() | ForEach-Object {
    ([string]$_.Key) + '|' + ([string]$_.Value)
})
if (($actualScenarioPairs -join "`n") -cne ($expectedScenarioPairs -join "`n")) {
    throw 'P3 structured result scenario contract differs from the validator.'
}

$iterations = [int]$receipt.execution.iterations
$scenarioCount = $expectedScenarios.Count
$expectedTotal = $iterations * $scenarioCount
if ($iterations -lt 25 -or $iterations -gt 100 -or
    [int]$result.schema_version -ne 2 -or
    [string]$result.runner_status -cne 'pass' -or
    $null -ne $result.runner_error_code -or
    [int]$result.iterations_requested -ne $iterations -or
    [int]$result.iterations_completed -ne $iterations -or
    [int]$result.scenario_count -ne $scenarioCount -or
    [int]$result.total_tests_run -ne $expectedTotal -or
    [int]$result.expected_total_tests -ne $expectedTotal -or
    [int]$receipt.execution.total_tests_run -ne $expectedTotal -or
    [int]$result.hard_timeout_seconds -ne [int]$receipt.execution.hard_timeout_seconds -or
    [int]$result.min_iterations -ne 25 -or
    [int]$result.max_iterations -ne 100 -or
    [string]$result.child_process_policy -cne 'forbidden' -or
    [int]$result.child_process_attempts -ne 0 -or
    [bool]$result.live_desktop_contacted -or
    [bool]$result.live_feishu_contacted -or
    @($result.failure_test_ids).Count -ne 0 -or
    @($result.error_test_ids).Count -ne 0 -or
    @($result.skipped_test_ids).Count -ne 0) {
    throw 'P3 structured result failed its exact semantic contract.'
}
if ((Get-StringSha256 -Value ([string]$result.nonce)) -cne
    [string]$receipt.execution.runner_nonce_sha256) {
    throw 'P3 structured result nonce does not match the receipt.'
}

$passCountNames = @($result.scenario_pass_counts.PSObject.Properties.Name | Sort-Object)
$expectedPassCountNames = @($expectedScenarios.Keys | Sort-Object)
if (($passCountNames -join "`n") -cne ($expectedPassCountNames -join "`n")) {
    throw 'P3 structured result pass-count keys differ from the scenario contract.'
}
foreach ($scenarioName in $expectedPassCountNames) {
    $scenarioProperty = $result.scenario_pass_counts.PSObject.Properties[$scenarioName]
    if ($null -eq $scenarioProperty -or [int]$scenarioProperty.Value -ne $iterations) {
        throw 'P3 scenario did not pass every requested iteration.'
    }
}
$iterationResults = @($result.iteration_results)
if ($iterationResults.Count -ne $iterations) { throw 'P3 iteration result count is incomplete.' }
for ($index = 0; $index -lt $iterationResults.Count; $index += 1) {
    $item = $iterationResults[$index]
    if ([int]$item.iteration -ne ($index + 1) -or
        [string]$item.status -cne 'pass' -or
        [int]$item.tests_run -ne $scenarioCount -or
        [double]$item.duration_seconds -lt 0) {
        throw 'P3 iteration result is invalid.'
    }
}
$maximumIteration = ($iterationResults | Measure-Object -Property duration_seconds -Maximum).Maximum
if ([Math]::Abs([double]$maximumIteration - [double]$result.max_iteration_duration_seconds) -gt 0.000001 -or
    [Math]::Abs([double]$result.duration_seconds - [double]$receipt.execution.duration_seconds) -gt 0.000001 -or
    [double]$result.duration_seconds -ge [double]$receipt.execution.hard_timeout_seconds) {
    throw 'P3 duration relations are invalid.'
}
$createdAt = $null
$startedAt = $null
$finishedAt = $null
try {
    $createdAt = ConvertTo-P3DateTimeOffset -Value $receipt.created_at_utc `
        -Role 'P3 receipt creation timestamp'
    $startedAt = ConvertTo-P3DateTimeOffset -Value $receipt.execution.started_at_utc `
        -Role 'P3 runner start timestamp'
    $finishedAt = ConvertTo-P3DateTimeOffset -Value $receipt.execution.finished_at_utc `
        -Role 'P3 runner finish timestamp'
} catch {
    throw 'P3 receipt timestamps are not round-trip date-time values.'
}
$capturedDuration = ($finishedAt - $startedAt).TotalSeconds
if ($preObservedAt -gt $startedAt -or
    $startedAt -gt $finishedAt -or
    $finishedAt -gt $postObservedAt -or
    $postObservedAt -gt $finalObservedAt -or
    $finalObservedAt -gt $createdAt -or
    $finishedAt -gt $createdAt -or
    $createdAt -gt [DateTimeOffset]::UtcNow.AddMinutes(5) -or
    [double]$result.duration_seconds -gt ($capturedDuration + 0.01)) {
    throw 'P3 receipt timestamp and duration relations are invalid.'
}

$pwsh = Join-Path $PSHOME 'pwsh.exe'
$p0Validator = Join-Path $desktop 'scripts\validate-external-p0b-evidence.ps1'
$p0Validation = Invoke-P0Validator -Shell $pwsh -Validator $p0Validator -Arguments @(
    '-DesktopRoot', $desktop,
    '-HarnessRoot', $harness,
    '-ProjectRoot', $project,
    '-EvidencePath', $p0Evidence,
    '-ExpectedEvidenceSha256', $ExpectedP0EvidenceSha256
)
if ([int]$p0Validation.validation_schema_version -ne 2 -or
    [string]$p0Validation.status -cne 'pass' -or
    -not [bool]$p0Validation.current_environment_revalidated -or
    [string]$p0Validation.source_manifest_sha256 -cne [string]$receipt.source_manifest_sha256) {
    throw 'P3 validation could not revalidate the bound P0 environment and source.'
}
$currentUserSid = Get-CurrentWindowsUserSid
$currentLifecycleMutexName = Get-LifecycleMutexName -Project $project -UserSid $currentUserSid
$p3Lifecycle = $receipt.bridge_stopped_receipt.lifecycle_mutex
$p0Lifecycle = $p0Receipt.bridge_stopped_receipt.lifecycle_mutex
if ([string]$p3Lifecycle.name_sha256 -cne (Get-StringSha256 -Value $currentLifecycleMutexName) -or
    [string]$p3Lifecycle.owner_sid_sha256 -cne (Get-StringSha256 -Value $currentUserSid) -or
    [string]$p3Lifecycle.namespace -cne 'global_current_user' -or
    [string]$p3Lifecycle.hook_comparison -cne 'equal' -or
    -not [bool]$p3Lifecycle.hook_registration_validated -or
    [string]$p3Lifecycle.lifecycle_exclusion_scope -cne 'registered_start_and_stop_hooks' -or
    -not [bool]$p3Lifecycle.held_for_complete_window -or
    [string]$p3Lifecycle.source_hook_sha256 -cne [string]$p3Lifecycle.installed_hook_sha256 -or
    [string]$p3Lifecycle.source_stop_hook_sha256 -cne [string]$p3Lifecycle.installed_stop_hook_sha256 -or
    [string]$p3Lifecycle.name_sha256 -cne [string]$p0Lifecycle.name_sha256 -or
    [string]$p3Lifecycle.owner_sid_sha256 -cne [string]$p0Lifecycle.owner_sid_sha256 -or
    [string]$p3Lifecycle.source_hook_sha256 -cne [string]$p0Lifecycle.source_hook_sha256 -or
    [string]$p3Lifecycle.installed_hook_sha256 -cne [string]$p0Lifecycle.installed_hook_sha256 -or
    [string]$p3Lifecycle.source_stop_hook_sha256 -cne [string]$p0Lifecycle.source_stop_hook_sha256 -or
    [string]$p3Lifecycle.installed_stop_hook_sha256 -cne [string]$p0Lifecycle.installed_stop_hook_sha256 -or
    [string]$p3Lifecycle.hooks_config_sha256 -cne [string]$p0Lifecycle.hooks_config_sha256) {
    throw 'P3 lifecycle mutex and Hook evidence do not match the current user and validated P0-B receipt.'
}
$inventoryPath = Join-Path $desktop 'assets\release-inventory.json'
Assert-NoReparsePathChain -Path $inventoryPath
Add-PinnedReadHandle -Path $inventoryPath -Pins $validatorPins
$inventory = Get-Content -LiteralPath $inventoryPath `
    -Raw -Encoding utf8 | Microsoft.PowerShell.Utility\ConvertFrom-Json -ErrorAction Stop
if ([string]$inventory.source_version -cne [string]$receipt.source_version) {
    throw 'P3 evidence source version is not current.'
}
if ([string]$p0Receipt.release_audit.source_version -cne [string]$receipt.source_version) {
    throw 'P3 evidence source version differs from its bound P0 receipt.'
}

[ordered]@{
    validation_schema_version = 2
    status = 'pass'
    evidence_file = [System.IO.Path]::GetFileName($evidence)
    evidence_sha256 = $ExpectedEvidenceSha256
    p0_evidence_sha256 = $ExpectedP0EvidenceSha256
    source_manifest_sha256 = [string]$receipt.source_manifest_sha256
    runner_surface = [string]$receipt.guards.runner_surface
    iterations = $iterations
    scenario_count = $scenarioCount
    snapshot_file_count = [int]$receipt.guards.snapshot_file_count
    total_tests_run = $expectedTotal
    semantic_relations_validated = $true
    retained_artifacts_pinned = $true
    current_environment_revalidated = $true
    cryptographic_attestation = $false
} | Microsoft.PowerShell.Utility\ConvertTo-Json -Compress | Write-Output
} finally {
    foreach ($pin in $validatorPins) { $pin.Dispose() }
}
