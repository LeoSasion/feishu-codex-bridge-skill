[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$DesktopRoot,
    [Parameter(Mandatory = $true)][string]$HarnessRoot,
    [Parameter(Mandatory = $true)][string]$ProjectRoot,
    [Parameter(Mandatory = $true)][string]$PythonExecutable,
    [Parameter(Mandatory = $true)][string]$P0EvidencePath,
    [Parameter(Mandatory = $true)][ValidatePattern('^[a-f0-9]{64}$')][string]$ExpectedP0EvidenceSha256,
    [Parameter(Mandatory = $true)][string]$ExternalWorkRoot,
    [Parameter(Mandatory = $true)][string]$EvidenceDirectory,
    [ValidateRange(1, 100)][int]$Iterations = 25,
    [ValidateRange(30, 900)][int]$TimeoutSeconds = 300,
    [ValidateSet('external_terminal', 'ci')][string]$RunnerSurface = 'external_terminal',
    [switch]$ExternalSoakAcknowledged
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
    throw 'P3 soak must be launched by a clean pwsh -NoProfile -NonInteractive -File invocation.'
}
$fileArgumentIndex = [int]$fileArgumentIndexes[0]
if ($fileArgumentIndex -lt 1 -or $fileArgumentIndex + 1 -ge $nativeInvocation.Count) {
    throw 'P3 soak clean PowerShell invocation is incomplete.'
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
    throw 'P3 soak requires the exact audited script under clean pwsh flags.'
}
if (-not $ExternalSoakAcknowledged -or $env:FEISHU_BRIDGE_EXTERNAL_P3_SOAK -ne '1') {
    throw ('P3 soak may run only from an external terminal or CI. Set ' +
        'FEISHU_BRIDGE_EXTERNAL_P3_SOAK=1 and pass -ExternalSoakAcknowledged.')
}
if ($env:CODEX_BRIDGE_CHILD -eq '1') {
    throw 'P3 soak refuses to run from a Codex child process.'
}
if (-not [System.Runtime.InteropServices.RuntimeInformation]::IsOSPlatform(
        [System.Runtime.InteropServices.OSPlatform]::Windows
    )) {
    throw 'The Desktop Bridge P3 soak supervisor currently supports Windows only.'
}
if ([string]$PSVersionTable.PSEdition -cne 'Core' -or
    [version]$PSVersionTable.PSVersion -lt [version]'7.4') {
    throw 'P3 soak requires PowerShell 7.4+ under a clean pwsh process.'
}

$requiredPowerShellModules = @(
    [System.IO.Path]::Combine($PSHOME, 'Modules', 'Microsoft.PowerShell.Utility', 'Microsoft.PowerShell.Utility.psd1'),
    [System.IO.Path]::Combine($PSHOME, 'Modules', 'Microsoft.PowerShell.Management', 'Microsoft.PowerShell.Management.psd1'),
    [System.IO.Path]::Combine($PSHOME, 'Modules', 'CimCmdlets', 'CimCmdlets.psd1')
)
foreach ($modulePath in $requiredPowerShellModules) {
    if (-not [System.IO.File]::Exists($modulePath)) {
        throw 'P3 soak clean PowerShell installation is missing a required built-in module.'
    }
    Microsoft.PowerShell.Core\Import-Module -Name $modulePath -Force -ErrorAction Stop
}
$PSModuleAutoLoadingPreference = 'None'
$script:Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$script:ChildEnvironmentPrefixes = @('PYTHON', 'CODEX_BRIDGE_', 'FEISHU_', 'LARK_', 'LARKCLI_')

if ($null -ne ('FeishuCodexBridge.ExternalP3SoakJob' -as [type]) -or
    $null -ne ('FeishuCodexBridge.ExternalP3SoakPath' -as [type])) {
    throw 'P3 soak native helper types were already loaded before the clean supervisor initialized.'
}
Microsoft.PowerShell.Utility\Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;

namespace FeishuCodexBridge {
    public static class ExternalP3SoakPath {
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern uint QueryDosDeviceW(
            string deviceName,
            StringBuilder targetPath,
            int maximumLength
        );

        public static void AssertOrdinaryLocalDrive(string path) {
            string root = System.IO.Path.GetPathRoot(path);
            if (String.IsNullOrEmpty(root) || root.Length < 2) {
                throw new ArgumentException("Path has no DOS drive root", "path");
            }
            var target = new StringBuilder(4096);
            uint length = QueryDosDeviceW(root.Substring(0, 2), target, target.Capacity);
            if (length == 0) {
                throw new Win32Exception(Marshal.GetLastWin32Error(), "QueryDosDeviceW failed");
            }
            if (!target.ToString().StartsWith(@"\Device\HarddiskVolume", StringComparison.OrdinalIgnoreCase)) {
                throw new InvalidOperationException("P3 soak refuses mapped, SUBST, or non-local drive aliases");
            }
        }
    }

    public sealed class ExternalP3SoakJob : IDisposable {
        private IntPtr handle;

        [StructLayout(LayoutKind.Sequential)]
        private struct JOBOBJECT_BASIC_LIMIT_INFORMATION {
            public long PerProcessUserTimeLimit;
            public long PerJobUserTimeLimit;
            public uint LimitFlags;
            public UIntPtr MinimumWorkingSetSize;
            public UIntPtr MaximumWorkingSetSize;
            public uint ActiveProcessLimit;
            public UIntPtr Affinity;
            public uint PriorityClass;
            public uint SchedulingClass;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct IO_COUNTERS {
            public ulong ReadOperationCount;
            public ulong WriteOperationCount;
            public ulong OtherOperationCount;
            public ulong ReadTransferCount;
            public ulong WriteTransferCount;
            public ulong OtherTransferCount;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION {
            public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
            public IO_COUNTERS IoInfo;
            public UIntPtr ProcessMemoryLimit;
            public UIntPtr JobMemoryLimit;
            public UIntPtr PeakProcessMemoryUsed;
            public UIntPtr PeakJobMemoryUsed;
        }

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern IntPtr CreateJobObject(IntPtr jobAttributes, string name);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool SetInformationJobObject(
            IntPtr job,
            int informationClass,
            ref JOBOBJECT_EXTENDED_LIMIT_INFORMATION information,
            uint informationLength
        );

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool CloseHandle(IntPtr handle);

        public ExternalP3SoakJob() {
            handle = CreateJobObject(IntPtr.Zero, null);
            if (handle == IntPtr.Zero) {
                throw new Win32Exception(Marshal.GetLastWin32Error(), "CreateJobObject failed");
            }
            var information = new JOBOBJECT_EXTENDED_LIMIT_INFORMATION();
            information.BasicLimitInformation.LimitFlags = 0x00002000;
            if (!SetInformationJobObject(
                handle,
                9,
                ref information,
                (uint)Marshal.SizeOf<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>()
            )) {
                int error = Marshal.GetLastWin32Error();
                CloseHandle(handle);
                handle = IntPtr.Zero;
                throw new Win32Exception(error, "SetInformationJobObject failed");
            }
        }

        public void Assign(Process process) {
            if (handle == IntPtr.Zero || !AssignProcessToJobObject(handle, process.Handle)) {
                throw new Win32Exception(Marshal.GetLastWin32Error(), "AssignProcessToJobObject failed");
            }
        }

        public void Dispose() {
            if (handle != IntPtr.Zero) {
                CloseHandle(handle);
                handle = IntPtr.Zero;
            }
            GC.SuppressFinalize(this);
        }

        ~ExternalP3SoakJob() {
            Dispose();
        }
    }
}
'@

function Get-FullPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    return [System.IO.Path]::GetFullPath($Path)
}

function Assert-NoReparsePathChain {
    param([Parameter(Mandatory = $true)][string]$Path)
    $current = Microsoft.PowerShell.Management\Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    while ($null -ne $current) {
        if (($current.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "P3 soak path chain contains a reparse point: $($current.FullName)"
        }
        if ($current -is [System.IO.FileInfo]) {
            $current = $current.Directory
        } elseif ($current -is [System.IO.DirectoryInfo]) {
            $current = $current.Parent
        } else {
            throw "P3 soak path chain contains an unsupported filesystem item: $($current.FullName)"
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
    return $candidatePath.StartsWith(
        $rootPath + '\',
        [System.StringComparison]::OrdinalIgnoreCase
    )
}

function Write-NewUtf8File {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Text
    )
    $bytes = $script:Utf8NoBom.GetBytes($Text)
    $stream = New-Object System.IO.FileStream(
        $Path,
        [System.IO.FileMode]::CreateNew,
        [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::Read
    )
    try {
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
    } finally {
        $stream.Dispose()
    }
}

function Get-FileSha256 {
    param([Parameter(Mandatory = $true)][string]$Path)
    return (Microsoft.PowerShell.Utility\Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-StringSha256 {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Value)
    $bytes = $script:Utf8NoBom.GetBytes($Value)
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([System.BitConverter]::ToString($algorithm.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant()
    } finally {
        $algorithm.Dispose()
    }
}

function Invoke-CapturedProcess {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][hashtable]$EnvironmentOverrides,
        [Parameter(Mandatory = $true)][string]$CapturePrefix,
        [ValidateRange(1, 1800)][int]$TimeoutSeconds
    )
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $Executable
    foreach ($argument in $Arguments) { [void]$startInfo.ArgumentList.Add([string]$argument) }
    $startInfo.WorkingDirectory = $WorkingDirectory
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $sensitiveNames = @(
        $startInfo.EnvironmentVariables.Keys | Where-Object {
            $candidateName = [string]$_
            foreach ($prefix in $script:ChildEnvironmentPrefixes) {
                if ($candidateName.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
                    return $true
                }
            }
            return $false
        }
    )
    foreach ($name in $sensitiveNames) { [void]$startInfo.EnvironmentVariables.Remove([string]$name) }
    foreach ($entry in $EnvironmentOverrides.GetEnumerator()) {
        $startInfo.EnvironmentVariables[[string]$entry.Key] = [string]$entry.Value
    }
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    $job = [FeishuCodexBridge.ExternalP3SoakJob]::new()
    $jobDisposed = $false
    $processStarted = $false
    try {
        $startedAt = [DateTimeOffset]::UtcNow
        if (-not $process.Start()) { throw 'External P3 child process did not start.' }
        $processStarted = $true
        $job.Assign($process)
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $timedOut = -not $process.WaitForExit($TimeoutSeconds * 1000)
        if ($timedOut) {
            $process.Kill($true)
            if (-not $process.WaitForExit(30000)) {
                throw 'External P3 child process tree did not exit after timeout.'
            }
        }
        $job.Dispose()
        $jobDisposed = $true
        if (-not [System.Threading.Tasks.Task]::WaitAll(
                [System.Threading.Tasks.Task[]]@($stdoutTask, $stderrTask),
                30000
            )) {
            throw 'External P3 child output pipes did not close within 30 seconds.'
        }
        $stdout = $stdoutTask.GetAwaiter().GetResult()
        $stderr = $stderrTask.GetAwaiter().GetResult()
        $finishedAt = [DateTimeOffset]::UtcNow
        $stdoutPath = $CapturePrefix + '.stdout.txt'
        $stderrPath = $CapturePrefix + '.stderr.txt'
        Write-NewUtf8File -Path $stdoutPath -Text $stdout
        Write-NewUtf8File -Path $stderrPath -Text $stderr
        return [pscustomobject]@{
            StartedAt = $startedAt
            FinishedAt = $finishedAt
            ExitCode = $process.ExitCode
            TimedOut = $timedOut
            Stdout = $stdout
            Stderr = $stderr
            StdoutSha256 = Get-FileSha256 -Path $stdoutPath
            StderrSha256 = Get-FileSha256 -Path $stderrPath
            JobObjectEnforced = $true
        }
    } finally {
        if ($processStarted) {
            try {
                if (-not $process.HasExited) {
                    $process.Kill($true)
                    [void]$process.WaitForExit(30000)
                }
            } catch { }
        }
        if (-not $jobDisposed) { $job.Dispose() }
        $process.Dispose()
    }
}

function Convert-SingleJsonObject {
    param(
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Text,
        [Parameter(Mandatory = $true)][string]$Role
    )
    $lines = @($Text -split '\r?\n' | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if ($lines.Count -ne 1) { throw "$Role returned $($lines.Count) nonempty lines instead of one JSON object." }
    try { return $lines[0] | Microsoft.PowerShell.Utility\ConvertFrom-Json -ErrorAction Stop }
    catch { throw "$Role did not return valid JSON." }
}

$processGuard = $null
function Get-ExternalRunnerProcessGuard {
    $visited = New-Object 'System.Collections.Generic.HashSet[int]'
    $currentId = [int]$PID
    $depth = 0
    $termination = 'process_tree_root'
    while ($currentId -gt 0 -and $depth -lt 64) {
        if (-not $visited.Add($currentId)) {
            throw 'P3 soak process ancestry contained a cycle.'
        }
        $processRecord = CimCmdlets\Get-CimInstance Win32_Process `
            -Filter ("ProcessId = {0}" -f $currentId) -ErrorAction Stop
        if ($null -eq $processRecord) {
            if ($depth -lt 2) {
                throw 'P3 soak could not inspect itself and one live external parent.'
            }
            $termination = 'exited_ancestor'
            break
        }
        $name = [string]$processRecord.Name
        $path = [string]$processRecord.ExecutablePath
        if ($name -match '^(?i:codex)(?:\.exe)?$' -or
            $path -match '(?i)[\\/]OpenAI\.Codex(?:_|[\\/])') {
            throw 'P3 soak refuses a process tree with a Codex Desktop or Codex CLI ancestor.'
        }
        $depth += 1
        $currentId = [int]$processRecord.ParentProcessId
    }
    if ($currentId -gt 0 -and $termination -eq 'process_tree_root') {
        throw 'P3 soak process ancestry exceeded its bounded inspection depth.'
    }
    return [pscustomobject]@{
        Depth = $depth
        CodexMatchCount = 0
        Complete = $termination -eq 'process_tree_root'
        Termination = $termination
    }
}

$processGuard = Get-ExternalRunnerProcessGuard
$desktop = Get-FullPath -Path $DesktopRoot
$harness = Get-FullPath -Path $HarnessRoot
$project = Get-FullPath -Path $ProjectRoot
$python = Get-FullPath -Path $PythonExecutable
$p0Evidence = Get-FullPath -Path $P0EvidencePath
$externalWork = Get-FullPath -Path $ExternalWorkRoot
$evidenceRoot = Get-FullPath -Path $EvidenceDirectory
foreach ($path in @($desktop, $harness, $project, $python, $p0Evidence, $externalWork, $evidenceRoot)) {
    if (-not (Test-Path -LiteralPath $path)) { throw "Required P3 soak path does not exist: $path" }
    [FeishuCodexBridge.ExternalP3SoakPath]::AssertOrdinaryLocalDrive($path)
    Assert-NoReparsePathChain -Path $path
}
if (-not (Test-Path -LiteralPath $python -PathType Leaf) -or
    -not (Test-Path -LiteralPath $p0Evidence -PathType Leaf) -or
    -not (Test-Path -LiteralPath $externalWork -PathType Container) -or
    -not (Test-Path -LiteralPath $evidenceRoot -PathType Container)) {
    throw 'P3 soak file/directory path roles are invalid.'
}
if (@(Get-ChildItem -LiteralPath $externalWork -Force).Count -ne 0) {
    throw 'P3 soak external work root must be empty.'
}
foreach ($protectedRoot in @($desktop, $harness, $project)) {
    if ((Test-IsWithinRoot -Candidate $externalWork -Root $protectedRoot) -or
        (Test-IsWithinRoot -Candidate $evidenceRoot -Root $protectedRoot)) {
        throw 'P3 soak work and evidence roots must remain outside source and live project roots.'
    }
}
if ((Test-IsWithinRoot -Candidate $externalWork -Root $evidenceRoot) -or
    (Test-IsWithinRoot -Candidate $evidenceRoot -Root $externalWork)) {
    throw 'P3 soak work and evidence roots must be physically distinct.'
}

$actualP0Hash = Get-FileSha256 -Path $p0Evidence
if ($actualP0Hash -cne $ExpectedP0EvidenceSha256) { throw 'P0 evidence hash does not match the supplied envelope.' }
$p0Receipt = Get-Content -LiteralPath $p0Evidence -Raw -Encoding utf8 |
    Microsoft.PowerShell.Utility\ConvertFrom-Json -ErrorAction Stop
if ([int]$p0Receipt.schema_version -ne 1 -or
    [string]$p0Receipt.evidence_kind -cne 'feishu-codex-bridge.p0b.external-test' -or
    [string]$p0Receipt.execution.runner_status -cne 'pass') {
    throw 'P3 soak requires one passing P0-B v1 receipt.'
}

$receiptId = [guid]::NewGuid().ToString('D').ToLowerInvariant()
$workDirectory = Join-Path $externalWork ('p3-soak-work-' + $receiptId)
$testTemp = Join-Path $workDirectory 'test-temp'
New-Item -ItemType Directory -Path $workDirectory, $testTemp -ErrorAction Stop | Out-Null
$pwsh = Join-Path $PSHOME 'pwsh.exe'
$p0Validator = Join-Path $desktop 'scripts\validate-external-p0b-evidence.ps1'
$schemaPath = Join-Path $desktop 'assets\external-p3-soak-evidence.schema.json'
$inventoryPath = Join-Path $desktop 'assets\release-inventory.json'
foreach ($requiredFile in @($pwsh, $p0Validator, $schemaPath, $inventoryPath)) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "P3 soak required file is missing: $requiredFile"
    }
}

$validatorArguments = @(
    '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
    '-File', $p0Validator,
    '-DesktopRoot', $desktop,
    '-HarnessRoot', $harness,
    '-ProjectRoot', $project,
    '-EvidencePath', $p0Evidence,
    '-ExpectedEvidenceSha256', $ExpectedP0EvidenceSha256
)
$preValidationCapture = Invoke-CapturedProcess -Executable $pwsh -Arguments $validatorArguments `
    -WorkingDirectory $desktop -EnvironmentOverrides @{} `
    -CapturePrefix (Join-Path $workDirectory 'p0-validation-pre') -TimeoutSeconds 240
if ($preValidationCapture.ExitCode -ne 0 -or $preValidationCapture.TimedOut) {
    throw 'P3 soak preflight P0 semantic validation failed.'
}
$preValidation = Convert-SingleJsonObject -Text $preValidationCapture.Stdout -Role 'P0 pre-validator'
if ([string]$preValidation.status -cne 'pass' -or
    -not [bool]$preValidation.current_environment_revalidated) {
    throw 'P3 soak preflight did not receive a current passing P0 validation.'
}

$snapshotRoot = Get-FullPath -Path ([string]$p0Receipt.execution.working_directory)
if (-not (Test-Path -LiteralPath $snapshotRoot -PathType Container) -or
    [System.IO.Path]::GetFileName($snapshotRoot) -cne 'source-snapshot') {
    throw 'P0 receipt does not identify its retained source snapshot.'
}
Assert-NoReparsePathChain -Path $snapshotRoot
if ((Test-IsWithinRoot -Candidate $workDirectory -Root $snapshotRoot) -or
    (Test-IsWithinRoot -Candidate $snapshotRoot -Root $workDirectory)) {
    throw 'P3 soak work directory must remain outside the retained source snapshot.'
}
$p0Python = Get-FullPath -Path ([string]$p0Receipt.execution.argv[0])
if (-not $python.Equals($p0Python, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'P3 soak Python does not match the PSF Python pinned by P0-B.'
}
$inventory = Get-Content -LiteralPath $inventoryPath -Raw -Encoding utf8 |
    Microsoft.PowerShell.Utility\ConvertFrom-Json -ErrorAction Stop
$sourceVersion = [string]$inventory.source_version
$sourceManifest = [string]$p0Receipt.release_audit.source_manifest_sha256
if ([string]::IsNullOrWhiteSpace($sourceVersion) -or
    $sourceManifest -cne [string]$preValidation.source_manifest_sha256 -or
    $sourceVersion -cne [string]$p0Receipt.release_audit.source_version) {
    throw 'P3 soak source version or manifest differs from the validated P0-B snapshot.'
}

$desktopP0Components = @(
    $p0Receipt.release_audit.components | Where-Object { [string]$_.name -ceq 'desktop_bridge' }
)
if ($desktopP0Components.Count -ne 1 -or
    [int]$desktopP0Components[0].file_count -ne @($desktopP0Components[0].files).Count) {
    throw 'P0 receipt has an invalid Desktop source-file contract.'
}
$snapshotPins = New-Object 'System.Collections.Generic.List[System.IO.FileStream]'
try {
foreach ($fileRecord in @($desktopP0Components[0].files)) {
    $relativePath = [string]$fileRecord.path
    $segments = @($relativePath.Split('/'))
    if ([string]::IsNullOrWhiteSpace($relativePath) -or
        [System.IO.Path]::IsPathRooted($relativePath) -or
        $relativePath.Contains('\') -or
        $relativePath.Contains(':') -or
        @($segments | Where-Object { $_ -in @('', '.', '..') }).Count -ne 0) {
        throw 'P0 receipt contains an unsafe Desktop snapshot path.'
    }
    $snapshotPath = Get-FullPath -Path (
        Join-Path $snapshotRoot ($relativePath.Replace('/', '\'))
    )
    if (-not (Test-IsWithinRoot -Candidate $snapshotPath -Root $snapshotRoot) -or
        -not (Test-Path -LiteralPath $snapshotPath -PathType Leaf)) {
        throw 'P0 receipt Desktop snapshot path is missing or outside the snapshot.'
    }
    Assert-NoReparsePathChain -Path $snapshotPath
    $pin = New-Object System.IO.FileStream(
        $snapshotPath,
        [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Read,
        [System.IO.FileShare]::Read
    )
    $snapshotPins.Add($pin)
    if ($pin.Length -ne [long]$fileRecord.size_bytes -or
        (Get-FileSha256 -Path $snapshotPath) -cne [string]$fileRecord.sha256) {
        throw 'Pinned P0 Desktop snapshot file differs from its receipt.'
    }
}
if ($snapshotPins.Count -ne [int]$desktopP0Components[0].file_count) {
    throw 'P3 soak did not pin every P0 Desktop snapshot file.'
}

$runner = Join-Path $snapshotRoot 'scripts\external_p3_soak_runner.py'
$testsDirectory = Join-Path $snapshotRoot 'tests'
foreach ($requiredSnapshotEntry in @($runner, $testsDirectory)) {
    if (-not (Test-Path -LiteralPath $requiredSnapshotEntry)) {
        throw 'The retained P0-B snapshot predates the P3 soak contract.'
    }
}
$resultPath = Join-Path $workDirectory 'structured-soak-result.json'
$runnerNonce = [guid]::NewGuid().ToString('D').ToLowerInvariant()
$runnerArguments = @(
    '-I', '-S', '-B', $runner,
    '--tests-dir', $testsDirectory,
    '--result-path', $resultPath,
    '--test-temp', $testTemp,
    '--nonce', $runnerNonce,
    '--iterations', [string]$Iterations,
    '--hard-timeout-seconds', [string]$TimeoutSeconds
)
$childPath = (Split-Path -Parent $python) + [System.IO.Path]::PathSeparator +
    (Split-Path -Parent $pwsh) + [System.IO.Path]::PathSeparator +
    [System.Environment]::SystemDirectory
$runnerCapture = Invoke-CapturedProcess -Executable $python -Arguments $runnerArguments `
    -WorkingDirectory $snapshotRoot -EnvironmentOverrides @{
        FEISHU_BRIDGE_EXTERNAL_TEST_RUNNER = '1'
        FEISHU_BRIDGE_EXTERNAL_P3_SOAK = '1'
        FEISHU_BRIDGE_TEST_TMP = $testTemp
        PYTHONDONTWRITEBYTECODE = '1'
        Path = $childPath
    } -CapturePrefix (Join-Path $workDirectory 'soak-runner') -TimeoutSeconds $TimeoutSeconds
if ($runnerCapture.TimedOut) { throw "P3 soak exceeded its $TimeoutSeconds second hard timeout." }
if (-not (Test-Path -LiteralPath $resultPath -PathType Leaf)) {
    throw 'P3 soak runner did not publish its structured result.'
}
$result = Get-Content -LiteralPath $resultPath -Raw -Encoding utf8 |
    Microsoft.PowerShell.Utility\ConvertFrom-Json -ErrorAction Stop
if ($runnerCapture.ExitCode -ne 0 -or [string]$result.runner_status -cne 'pass') {
    $failedIds = @(@($result.failure_test_ids) + @($result.error_test_ids) | Select-Object -Unique)
    throw ('P3 soak runner failed.' + $(if ($failedIds.Count) { ' Test IDs: ' + ($failedIds -join ', ') } else { '' }))
}
if (@($runnerCapture.Stdout -split '\r?\n' | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }).Count -ne 0) {
    throw 'P3 soak runner stdout must remain empty; progress belongs on retained stderr.'
}

$postValidationCapture = Invoke-CapturedProcess -Executable $pwsh -Arguments $validatorArguments `
    -WorkingDirectory $desktop -EnvironmentOverrides @{} `
    -CapturePrefix (Join-Path $workDirectory 'p0-validation-post') -TimeoutSeconds 240
if ($postValidationCapture.ExitCode -ne 0 -or $postValidationCapture.TimedOut) {
    throw 'P3 soak post-run P0 semantic validation failed.'
}
$postValidation = Convert-SingleJsonObject -Text $postValidationCapture.Stdout -Role 'P0 post-validator'
if ([string]$postValidation.status -cne 'pass' -or
    [string]$postValidation.source_manifest_sha256 -cne $sourceManifest -or
    [string]$postValidation.source_manifest_sha256 -cne [string]$preValidation.source_manifest_sha256) {
    throw 'P3 soak source or retained P0 evidence changed during the run.'
}

$evidenceName = 'p3-soak-v1-' + $receiptId + '.json'
$evidencePath = Join-Path $evidenceRoot $evidenceName
$receipt = [ordered]@{
    schema_version = 1
    evidence_kind = 'external_p3_bounded_soak'
    receipt_id = $receiptId
    created_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
    source_version = $sourceVersion
    source_manifest_sha256 = $sourceManifest
    p0_evidence = [ordered]@{
        file = [System.IO.Path]::GetFileName($p0Evidence)
        sha256 = $ExpectedP0EvidenceSha256
        validation_status = 'pass'
    }
    execution = [ordered]@{
        working_directory = $workDirectory
        iterations = [int]$result.iterations_completed
        scenario_count = [int]$result.scenario_count
        total_tests_run = [int]$result.total_tests_run
        hard_timeout_seconds = $TimeoutSeconds
        started_at_utc = $runnerCapture.StartedAt.ToString('o')
        finished_at_utc = $runnerCapture.FinishedAt.ToString('o')
        duration_seconds = [double]$result.duration_seconds
        runner_status = [string]$result.runner_status
        timed_out = [bool]$runnerCapture.TimedOut
        child_process_policy = [string]$result.child_process_policy
        child_process_attempts = [int]$result.child_process_attempts
        live_desktop_contacted = [bool]$result.live_desktop_contacted
        live_feishu_contacted = [bool]$result.live_feishu_contacted
        result_sha256 = Get-FileSha256 -Path $resultPath
        stdout_sha256 = [string]$runnerCapture.StdoutSha256
        stderr_sha256 = [string]$runnerCapture.StderrSha256
        runner_nonce_sha256 = Get-StringSha256 -Value $runnerNonce
    }
    guards = [ordered]@{
        listener_stopped = $true
        runner_surface = $RunnerSurface
        process_ancestry_depth = [int]$processGuard.Depth
        process_ancestry_complete = [bool]$processGuard.Complete
        process_ancestry_termination = [string]$processGuard.Termination
        codex_ancestor_match_count = [int]$processGuard.CodexMatchCount
        p0_pre_validated = $true
        p0_post_validated = $true
        source_snapshot_reused = $true
        snapshot_files_pinned = $true
        snapshot_file_count = [int]$snapshotPins.Count
        source_unchanged = $true
        job_object_enforced = [bool]$runnerCapture.JobObjectEnforced
    }
    immutability = [ordered]@{
        write_mode = 'create_new'
        destination_preexisted = $false
        evidence_outside_source = $true
        overwrite_attempted = $false
    }
}
$receiptJson = $receipt | Microsoft.PowerShell.Utility\ConvertTo-Json -Depth 12 -Compress
if (-not ($receiptJson | Microsoft.PowerShell.Utility\Test-Json -SchemaFile $schemaPath -ErrorAction Stop)) {
    throw 'P3 soak receipt failed its current JSON Schema.'
}
Write-NewUtf8File -Path $evidencePath -Text ($receiptJson + "`n")
$evidenceHash = Get-FileSha256 -Path $evidencePath
[ordered]@{
    evidence_file = $evidenceName
    evidence_sha256 = $evidenceHash
    schema_version = 1
    runner_status = 'pass'
} | Microsoft.PowerShell.Utility\ConvertTo-Json -Compress | Write-Output
} finally {
    foreach ($pin in $snapshotPins) { $pin.Dispose() }
}
