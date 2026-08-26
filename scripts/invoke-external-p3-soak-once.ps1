[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateNotNullOrEmpty()][string]$PythonExecutable,
    [Parameter(Mandatory = $true)][ValidateNotNullOrEmpty()][string]$P0EvidencePath,
    [Parameter(Mandatory = $true)][ValidatePattern('^[a-f0-9]{64}$')][string]$ExpectedP0EvidenceSha256,
    [string]$DesktopRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$HarnessRoot = '',
    [string]$ProjectRoot = '',
    [ValidateRange(1, 100)][int]$Iterations = 25,
    [ValidateRange(30, 900)][int]$TimeoutSeconds = 300,
    [switch]$ExternalSoakAcknowledged
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not $ExternalSoakAcknowledged) {
    throw 'This wrapper may run only in an external terminal after explicit acknowledgement.'
}

$desktop = [System.IO.Path]::GetFullPath($DesktopRoot)
if ([string]::IsNullOrWhiteSpace($HarnessRoot)) {
    $HarnessRoot = Join-Path (Split-Path -Parent $desktop) 'feishu-codex-harness-bridge'
}
if ([string]::IsNullOrWhiteSpace($ProjectRoot)) { $ProjectRoot = $desktop }
$harness = [System.IO.Path]::GetFullPath($HarnessRoot)
$project = [System.IO.Path]::GetFullPath($ProjectRoot)
$python = [System.IO.Path]::GetFullPath($PythonExecutable)
$p0Evidence = [System.IO.Path]::GetFullPath($P0EvidencePath)
foreach ($requiredPath in @($desktop, $harness, $project, $python, $p0Evidence)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "Required external P3 soak path does not exist: $requiredPath"
    }
}

$supervisor = Join-Path $desktop 'scripts\run-external-p3-soak.ps1'
$validator = Join-Path $desktop 'scripts\validate-external-p3-soak-evidence.ps1'
foreach ($requiredScript in @($supervisor, $validator)) {
    if (-not (Test-Path -LiteralPath $requiredScript -PathType Leaf)) {
        throw "Required external P3 soak script is missing: $requiredScript"
    }
}
$pwsh = Join-Path $PSHOME 'pwsh.exe'
if (-not (Test-Path -LiteralPath $pwsh -PathType Leaf)) {
    throw 'PowerShell 7 executable is unavailable beside the current host.'
}

$driveRoot = [System.IO.Path]::GetPathRoot($desktop)
if ([string]::IsNullOrWhiteSpace($driveRoot)) {
    throw 'DesktopRoot must be on an ordinary local drive.'
}
$runTag = '{0}-{1}' -f (Get-Date -Format 'yyyyMMdd-HHmmss'), ([guid]::NewGuid().ToString('N').Substring(0, 8))
$workRoot = Join-Path $driveRoot "codex-bridge-p3-soak-work-$runTag"
$evidenceRoot = Join-Path $driveRoot "codex-bridge-p3-soak-evidence-$runTag"
New-Item -ItemType Directory -Path $workRoot, $evidenceRoot -ErrorAction Stop | Out-Null

function Invoke-CleanPowerShellJson {
    param(
        [Parameter(Mandatory = $true)][string]$ScriptPath,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList,
        [Parameter(Mandatory = $true)][string]$StageName
    )
    $nativePreferenceVariable = Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue
    $oldNativePreference = if ($null -ne $nativePreferenceVariable) {
        $nativePreferenceVariable.Value
    } else { $null }
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
    } finally {
        if ($null -ne $nativePreferenceVariable) {
            $script:PSNativeCommandUseErrorActionPreference = $oldNativePreference
        }
    }
    if ($exitCode -ne 0) {
        $details = @($output | Select-Object -Last 80) -join [Environment]::NewLine
        throw (
            "$StageName failed with exit code $exitCode. " +
            "Work: $workRoot Evidence: $evidenceRoot" +
            $(if ([string]::IsNullOrWhiteSpace($details)) { '' } else {
                [Environment]::NewLine + $details
            })
        )
    }
    $nonempty = @($output | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if ($nonempty.Count -ne 1) {
        $nonempty | Write-Output
        throw "$StageName returned $($nonempty.Count) nonempty lines instead of one JSON object."
    }
    try { return $nonempty[0] | ConvertFrom-Json -ErrorAction Stop }
    catch {
        $nonempty | Write-Output
        throw "$StageName did not return valid JSON."
    }
}

$oldP3Soak = [Environment]::GetEnvironmentVariable(
    'FEISHU_BRIDGE_EXTERNAL_P3_SOAK',
    [EnvironmentVariableTarget]::Process
)
try {
    [Environment]::SetEnvironmentVariable(
        'FEISHU_BRIDGE_EXTERNAL_P3_SOAK',
        '1',
        [EnvironmentVariableTarget]::Process
    )
    $envelope = Invoke-CleanPowerShellJson -ScriptPath $supervisor -StageName 'P3 soak supervisor' `
        -ArgumentList @(
            '-DesktopRoot', $desktop,
            '-HarnessRoot', $harness,
            '-ProjectRoot', $project,
            '-PythonExecutable', $python,
            '-P0EvidencePath', $p0Evidence,
            '-ExpectedP0EvidenceSha256', $ExpectedP0EvidenceSha256,
            '-ExternalWorkRoot', $workRoot,
            '-EvidenceDirectory', $evidenceRoot,
            '-Iterations', [string]$Iterations,
            '-TimeoutSeconds', [string]$TimeoutSeconds,
            '-RunnerSurface', 'external_terminal',
            '-ExternalSoakAcknowledged'
        )
    if ([int]$envelope.schema_version -ne 1 -or
        [string]$envelope.runner_status -cne 'pass' -or
        [string]$envelope.evidence_file -cnotmatch '^p3-soak-v1-[a-f0-9-]{36}\.json$' -or
        [System.IO.Path]::GetFileName([string]$envelope.evidence_file) -cne
            [string]$envelope.evidence_file -or
        [string]$envelope.evidence_sha256 -cnotmatch '^[a-f0-9]{64}$') {
        throw 'P3 soak supervisor JSON failed its envelope contract.'
    }
    $evidencePath = Join-Path $evidenceRoot ([string]$envelope.evidence_file)
    $envelope | Add-Member -NotePropertyName evidence_path -NotePropertyValue $evidencePath
    $validation = Invoke-CleanPowerShellJson -ScriptPath $validator -StageName 'P3 soak semantic validator' `
        -ArgumentList @(
            '-DesktopRoot', $desktop,
            '-HarnessRoot', $harness,
            '-ProjectRoot', $project,
            '-EvidencePath', $evidencePath,
            '-ExpectedEvidenceSha256', ([string]$envelope.evidence_sha256),
            '-P0EvidencePath', $p0Evidence,
            '-ExpectedP0EvidenceSha256', $ExpectedP0EvidenceSha256
        )
    if ([int]$validation.validation_schema_version -ne 1 -or
        [string]$validation.status -cne 'pass' -or
        [string]$validation.evidence_file -cne [string]$envelope.evidence_file -or
        [string]$validation.evidence_sha256 -cne [string]$envelope.evidence_sha256 -or
        [string]$validation.p0_evidence_sha256 -cne $ExpectedP0EvidenceSha256 -or
        -not [bool]$validation.semantic_relations_validated -or
        -not [bool]$validation.retained_artifacts_pinned -or
        -not [bool]$validation.current_environment_revalidated) {
        throw 'P3 soak semantic validator JSON failed its wrapper contract.'
    }
} finally {
    [Environment]::SetEnvironmentVariable(
        'FEISHU_BRIDGE_EXTERNAL_P3_SOAK',
        $oldP3Soak,
        [EnvironmentVariableTarget]::Process
    )
}

$envelope | ConvertTo-Json -Compress -Depth 10
$validation | ConvertTo-Json -Compress -Depth 10
