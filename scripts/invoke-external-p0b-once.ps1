[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$PythonExecutable,

    [string]$DesktopRoot = (Split-Path -Parent $PSScriptRoot),

    [string]$HarnessRoot = '',

    [string]$ProjectRoot = '',

    [Parameter(Mandatory = $true)]
    [switch]$ExternalTestRunnerAcknowledged
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not $ExternalTestRunnerAcknowledged) {
    throw 'This wrapper may run only in an external terminal after explicit acknowledgement.'
}

$desktop = [System.IO.Path]::GetFullPath($DesktopRoot)
if ([string]::IsNullOrWhiteSpace($HarnessRoot)) {
    $HarnessRoot = Join-Path (Split-Path -Parent $desktop) 'feishu-codex-harness-bridge'
}
if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = $desktop
}
$harness = [System.IO.Path]::GetFullPath($HarnessRoot)
$project = [System.IO.Path]::GetFullPath($ProjectRoot)
$python = [System.IO.Path]::GetFullPath($PythonExecutable)

foreach ($requiredPath in @($desktop, $harness, $project, $python)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "Required external P0-B path does not exist: $requiredPath"
    }
}

$supervisor = Join-Path $desktop 'scripts\run-external-p0b.ps1'
$validator = Join-Path $desktop 'scripts\validate-external-p0b-evidence.ps1'
foreach ($requiredScript in @($supervisor, $validator)) {
    if (-not (Test-Path -LiteralPath $requiredScript -PathType Leaf)) {
        throw "Required external P0-B script is missing: $requiredScript"
    }
}

$hookPairs = @(
    @(
        'SessionStart',
        (Join-Path $desktop 'scripts\start-feishu-codex-bridge.ps1'),
        (Join-Path $project '.codex\hooks\start-feishu-codex-bridge.ps1')
    ),
    @(
        'SessionEnd',
        (Join-Path $desktop 'scripts\stop-feishu-codex-bridge.ps1'),
        (Join-Path $project '.codex\hooks\stop-feishu-codex-bridge.ps1')
    )
)
foreach ($hookPair in $hookPairs) {
    $eventName = [string]$hookPair[0]
    $sourceHook = [string]$hookPair[1]
    $installedHook = [string]$hookPair[2]
    if (-not (Test-Path -LiteralPath $sourceHook -PathType Leaf) -or
        -not (Test-Path -LiteralPath $installedHook -PathType Leaf)) {
        throw "$eventName lifecycle hook is missing. P0-B did not start."
    }
    $sourceHash = (Get-FileHash -LiteralPath $sourceHook -Algorithm SHA256).Hash
    $installedHash = (Get-FileHash -LiteralPath $installedHook -Algorithm SHA256).Hash
    if ($sourceHash -cne $installedHash) {
        throw (
            "$eventName lifecycle hook does not match the audited source. " +
            "P0-B did not start. Do not copy it manually; refresh Bridge hooks " +
            "under a separate approved administrative action, then rerun."
        )
    }
}

$pwsh = Join-Path $PSHOME 'pwsh.exe'
if (-not (Test-Path -LiteralPath $pwsh -PathType Leaf)) {
    throw "PowerShell 7 executable is unavailable beside the current host: $pwsh"
}

$driveRoot = [System.IO.Path]::GetPathRoot($desktop)
if ([string]::IsNullOrWhiteSpace($driveRoot)) {
    throw 'DesktopRoot must be on an ordinary local drive.'
}
$runTag = '{0}-{1}' -f (Get-Date -Format 'yyyyMMdd-HHmmss'), ([guid]::NewGuid().ToString('N').Substring(0, 8))
$workRoot = Join-Path $driveRoot "codex-bridge-p0b-work-$runTag"
$evidenceRoot = Join-Path $driveRoot "codex-bridge-p0b-evidence-$runTag"
New-Item -ItemType Directory -Path $workRoot, $evidenceRoot -ErrorAction Stop | Out-Null

function Invoke-CleanPowerShellJson {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ScriptPath,

        [Parameter(Mandatory = $true)]
        [string[]]$ArgumentList,

        [Parameter(Mandatory = $true)]
        [string]$StageName
    )

    $nativePreferenceVariable = Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue
    $oldNativePreference = if ($null -ne $nativePreferenceVariable) {
        $nativePreferenceVariable.Value
    }
    else {
        $null
    }
    try {
        if ($null -ne $nativePreferenceVariable) {
            $script:PSNativeCommandUseErrorActionPreference = $false
        }
        $output = @(
            & $pwsh -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass `
                -File $ScriptPath @ArgumentList 2>&1 |
                ForEach-Object { [string]$_ }
        )
        $exitCode = $LASTEXITCODE
    }
    finally {
        if ($null -ne $nativePreferenceVariable) {
            $script:PSNativeCommandUseErrorActionPreference = $oldNativePreference
        }
    }
    if ($exitCode -ne 0) {
        $details = @($output | Select-Object -Last 80) -join [Environment]::NewLine
        throw (
            "$StageName failed with exit code $exitCode. " +
            "Work: $workRoot Evidence: $evidenceRoot" +
            $(if ([string]::IsNullOrWhiteSpace($details)) {
                ''
            }
            else {
                [Environment]::NewLine + $details
            })
        )
    }
    $nonempty = @($output | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if ($nonempty.Count -ne 1) {
        $nonempty | Write-Output
        throw "$StageName returned $($nonempty.Count) nonempty lines instead of one JSON object."
    }
    try {
        return $nonempty[0] | ConvertFrom-Json -ErrorAction Stop
    }
    catch {
        $nonempty | Write-Output
        throw "$StageName did not return valid JSON: $($_.Exception.Message)"
    }
}

$oldExternalRunner = [Environment]::GetEnvironmentVariable(
    'FEISHU_BRIDGE_EXTERNAL_TEST_RUNNER',
    [EnvironmentVariableTarget]::Process
)
try {
    [Environment]::SetEnvironmentVariable(
        'FEISHU_BRIDGE_EXTERNAL_TEST_RUNNER',
        '1',
        [EnvironmentVariableTarget]::Process
    )
    $envelope = Invoke-CleanPowerShellJson `
        -ScriptPath $supervisor `
        -StageName 'P0-B supervisor' `
        -ArgumentList @(
            '-DesktopRoot', $desktop,
            '-HarnessRoot', $harness,
            '-ProjectRoot', $project,
            '-PythonExecutable', $python,
            '-ExternalWorkRoot', $workRoot,
            '-EvidenceDirectory', $evidenceRoot,
            '-RunnerSurface', 'external_terminal',
            '-ExternalTestRunnerAcknowledged'
        )

    if ([string]::IsNullOrWhiteSpace([string]$envelope.evidence_file) -or
        [string]::IsNullOrWhiteSpace([string]$envelope.evidence_sha256)) {
        throw 'P0-B supervisor JSON is missing evidence_file or evidence_sha256.'
    }
    $evidencePath = Join-Path $evidenceRoot ([string]$envelope.evidence_file)
    $envelope | Add-Member -NotePropertyName evidence_path -NotePropertyValue $evidencePath
    $validation = Invoke-CleanPowerShellJson `
        -ScriptPath $validator `
        -StageName 'P0-B semantic validator' `
        -ArgumentList @(
            '-DesktopRoot', $desktop,
            '-HarnessRoot', $harness,
            '-ProjectRoot', $project,
            '-EvidencePath', $evidencePath,
            '-ExpectedEvidenceSha256', ([string]$envelope.evidence_sha256)
        )
}
finally {
    [Environment]::SetEnvironmentVariable(
        'FEISHU_BRIDGE_EXTERNAL_TEST_RUNNER',
        $oldExternalRunner,
        [EnvironmentVariableTarget]::Process
    )
}

$envelope | ConvertTo-Json -Compress -Depth 10
$validation | ConvertTo-Json -Compress -Depth 10
