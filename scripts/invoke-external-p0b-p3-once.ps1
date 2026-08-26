[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$PythonExecutable,

    [string]$DesktopRoot = (Split-Path -Parent $PSScriptRoot),

    [string]$HarnessRoot = '',

    [string]$ProjectRoot = '',

    [ValidateRange(1, 100)]
    [int]$Iterations = 25,

    [ValidateRange(30, 900)]
    [int]$TimeoutSeconds = 300,

    [Parameter(Mandatory = $true)]
    [switch]$ExternalSuiteAcknowledged
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not $ExternalSuiteAcknowledged) {
    throw 'This suite may run only in an external terminal after explicit acknowledgement.'
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
$pwsh = Join-Path $PSHOME 'pwsh.exe'
$p0Wrapper = Join-Path $desktop 'scripts\invoke-external-p0b-once.ps1'
$p3Wrapper = Join-Path $desktop 'scripts\invoke-external-p3-soak-once.ps1'

foreach ($requiredPath in @($desktop, $harness, $project, $python, $pwsh, $p0Wrapper, $p3Wrapper)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "Required external suite path does not exist: $requiredPath"
    }
}
foreach ($requiredFile in @($python, $pwsh, $p0Wrapper, $p3Wrapper)) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "Required external suite file has the wrong path type: $requiredFile"
    }
}

function Invoke-ExternalJsonPair {
    param(
        [Parameter(Mandatory = $true)][string]$StageName,
        [Parameter(Mandatory = $true)][string]$ScriptPath,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList
    )

    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $pwsh
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    foreach ($argument in @(
        '-NoLogo',
        '-NoProfile',
        '-NonInteractive',
        '-ExecutionPolicy',
        'Bypass',
        '-File',
        $ScriptPath
    ) + $ArgumentList) {
        [void]$startInfo.ArgumentList.Add([string]$argument)
    }

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    try {
        if (-not $process.Start()) {
            throw "$StageName process did not start."
        }
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $process.WaitForExit()
        [System.Threading.Tasks.Task]::WaitAll(@($stdoutTask, $stderrTask))
        $stdout = [string]$stdoutTask.Result
        $stderr = [string]$stderrTask.Result
        $exitCode = $process.ExitCode
    }
    finally {
        $process.Dispose()
    }

    if ($exitCode -ne 0) {
        $diagnosticLines = @(
            (($stderr + [Environment]::NewLine + $stdout) -split "`r?`n") |
                Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
                Select-Object -Last 120
        )
        $details = $diagnosticLines -join [Environment]::NewLine
        throw (
            "$StageName failed with exit code $exitCode." +
            $(if ([string]::IsNullOrWhiteSpace($details)) {
                ''
            }
            else {
                [Environment]::NewLine + $details
            })
        )
    }
    if (-not [string]::IsNullOrWhiteSpace($stderr)) {
        throw "$StageName wrote unexpected stderr despite a zero exit code."
    }

    $lines = @(
        ($stdout -split "`r?`n") |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )
    if ($lines.Count -ne 2) {
        throw "$StageName returned $($lines.Count) nonempty stdout lines instead of two JSON objects."
    }
    $objects = New-Object System.Collections.Generic.List[object]
    foreach ($line in $lines) {
        try {
            $objects.Add(($line | Microsoft.PowerShell.Utility\ConvertFrom-Json -ErrorAction Stop))
        }
        catch {
            throw "$StageName returned invalid JSON: $($_.Exception.Message)"
        }
    }
    return $objects.ToArray()
}

$p0 = @(Invoke-ExternalJsonPair -StageName 'P0-B one-shot wrapper' -ScriptPath $p0Wrapper `
    -ArgumentList @(
        '-PythonExecutable', $python,
        '-DesktopRoot', $desktop,
        '-HarnessRoot', $harness,
        '-ProjectRoot', $project,
        '-ExternalTestRunnerAcknowledged'
    ))
if ($p0.Count -ne 2) {
    throw 'P0-B one-shot wrapper did not return exactly two parsed JSON objects.'
}
$p0Envelope = $p0[0]
$p0Validation = $p0[1]
$p0EvidencePath = [string]$p0Envelope.evidence_path
$p0EvidenceSha256 = [string]$p0Envelope.evidence_sha256
if ([string]$p0Envelope.runner_status -cne 'pass' -or
    [string]$p0Validation.status -cne 'pass' -or
    [string]$p0Validation.evidence_sha256 -cne $p0EvidenceSha256 -or
    [string]::IsNullOrWhiteSpace($p0EvidencePath) -or
    -not (Test-Path -LiteralPath $p0EvidencePath -PathType Leaf)) {
    throw 'P0-B one-shot wrapper pair failed the suite handoff contract.'
}

$p3 = @(Invoke-ExternalJsonPair -StageName 'P3 one-shot wrapper' -ScriptPath $p3Wrapper `
    -ArgumentList @(
        '-PythonExecutable', $python,
        '-P0EvidencePath', $p0EvidencePath,
        '-ExpectedP0EvidenceSha256', $p0EvidenceSha256,
        '-DesktopRoot', $desktop,
        '-HarnessRoot', $harness,
        '-ProjectRoot', $project,
        '-Iterations', [string]$Iterations,
        '-TimeoutSeconds', [string]$TimeoutSeconds,
        '-ExternalSoakAcknowledged'
    ))
if ($p3.Count -ne 2) {
    throw 'P3 one-shot wrapper did not return exactly two parsed JSON objects.'
}
$p3Envelope = $p3[0]
$p3Validation = $p3[1]
if ([string]$p3Envelope.runner_status -cne 'pass' -or
    [string]$p3Validation.status -cne 'pass' -or
    [string]$p3Validation.p0_evidence_sha256 -cne $p0EvidenceSha256 -or
    [string]$p3Validation.evidence_sha256 -cne [string]$p3Envelope.evidence_sha256) {
    throw 'P3 one-shot wrapper pair failed the suite handoff contract.'
}

[pscustomobject][ordered]@{
    schema_version = 1
    runner_status = 'pass'
    p0b = [pscustomobject][ordered]@{
        envelope = $p0Envelope
        validation = $p0Validation
    }
    p3_soak = [pscustomobject][ordered]@{
        envelope = $p3Envelope
        validation = $p3Validation
    }
} | Microsoft.PowerShell.Utility\ConvertTo-Json -Depth 20 -Compress
