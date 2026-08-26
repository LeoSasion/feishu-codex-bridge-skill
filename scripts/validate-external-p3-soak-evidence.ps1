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
    try {
        if (-not $process.Start()) { throw 'P0 validator child did not start.' }
        $processStarted = $true
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(240000)) {
            $process.Kill($true)
            [void]$process.WaitForExit(30000)
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
        $lines = @($stdout -split '\r?\n' | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
        if ($lines.Count -ne 1) { throw 'Current P0 validator did not return one JSON object.' }
        return $lines[0] | Microsoft.PowerShell.Utility\ConvertFrom-Json -ErrorAction Stop
    } finally {
        if ($processStarted) {
            try {
                if (-not $process.HasExited) {
                    $process.Kill($true)
                    [void]$process.WaitForExit(30000)
                }
            } catch { }
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
$expectedEvidenceName = 'p3-soak-v1-' + [string]$receipt.receipt_id + '.json'
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
    -not [bool]$receipt.guards.snapshot_files_pinned -or
    [int]$receipt.guards.snapshot_file_count -ne [int]$p0DesktopComponents[0].file_count) {
    throw 'P3 evidence external-origin or snapshot-pinning guard is invalid.'
}

$workDirectory = Get-FullPath -Path ([string]$receipt.execution.working_directory)
if ([System.IO.Path]::GetFileName($workDirectory) -cne ('p3-soak-work-' + [string]$receipt.receipt_id) -or
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

$resultJson = Read-StrictUtf8 -Path $resultPath -MaximumBytes 4194304
Assert-UniqueJsonObjectKeys -Json $resultJson -Role 'P3 structured result'
$result = $resultJson | Microsoft.PowerShell.Utility\ConvertFrom-Json -ErrorAction Stop
$expectedScenarios = [ordered]@{
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
if ([int]$result.schema_version -ne 1 -or
    [string]$result.runner_status -cne 'pass' -or
    $null -ne $result.runner_error_code -or
    [int]$result.iterations_requested -ne $iterations -or
    [int]$result.iterations_completed -ne $iterations -or
    [int]$result.scenario_count -ne $scenarioCount -or
    [int]$result.total_tests_run -ne $expectedTotal -or
    [int]$result.expected_total_tests -ne $expectedTotal -or
    [int]$receipt.execution.total_tests_run -ne $expectedTotal -or
    [int]$result.hard_timeout_seconds -ne [int]$receipt.execution.hard_timeout_seconds -or
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
if ($startedAt -gt $finishedAt -or
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
if ([string]$p0Validation.status -cne 'pass' -or
    -not [bool]$p0Validation.current_environment_revalidated -or
    [string]$p0Validation.source_manifest_sha256 -cne [string]$receipt.source_manifest_sha256) {
    throw 'P3 validation could not revalidate the bound P0 environment and source.'
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
    validation_schema_version = 1
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
