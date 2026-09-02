[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateNotNullOrEmpty()][string]$PythonExecutable,
    [Parameter(Mandatory = $true)][ValidateNotNullOrEmpty()][string]$ArtifactRoot,
    [Parameter(Mandatory = $true)][ValidateNotNullOrEmpty()][string]$P0EvidencePath,
    [Parameter(Mandatory = $true)][ValidatePattern('^[a-f0-9]{64}$')][string]$ExpectedP0EvidenceSha256,
    [string]$DesktopRoot = (Split-Path -Parent $PSScriptRoot),
    [Parameter(Mandatory = $true)][ValidateNotNullOrEmpty()][string]$HarnessRoot,
    [string]$ProjectRoot = '',
    [ValidateRange(25, 100)][int]$Iterations = 25,
    [ValidateRange(30, 900)][int]$TimeoutSeconds = 300,
    [switch]$ExternalSoakAcknowledged
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ($null -ne ('FeishuCodexBridge.ExternalOneShotPath' -as [type])) {
    throw 'External one-shot path helper type was already loaded before initialization.'
}
Microsoft.PowerShell.Utility\Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;

namespace FeishuCodexBridge {
    public static class ExternalOneShotPath {
        private const uint FILE_SHARE_READ = 0x00000001;
        private const uint FILE_SHARE_WRITE = 0x00000002;
        private const uint FILE_SHARE_DELETE = 0x00000004;
        private const uint OPEN_EXISTING = 3;
        private const uint FILE_FLAG_BACKUP_SEMANTICS = 0x02000000;

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern IntPtr CreateFileW(
            string fileName,
            uint desiredAccess,
            uint shareMode,
            IntPtr securityAttributes,
            uint creationDisposition,
            uint flagsAndAttributes,
            IntPtr templateFile
        );

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern uint GetFinalPathNameByHandleW(
            IntPtr file,
            StringBuilder filePath,
            uint filePathLength,
            uint flags
        );

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern uint GetLongPathNameW(
            string shortPath,
            StringBuilder longPath,
            uint bufferLength
        );

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool CloseHandle(IntPtr handle);

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern uint QueryDosDeviceW(
            string deviceName,
            StringBuilder targetPath,
            int maximumLength
        );

        private static string GetOrdinaryLocalDriveDevice(string path) {
            string root = System.IO.Path.GetPathRoot(path);
            if (String.IsNullOrEmpty(root) || root.Length < 2 || root[1] != ':') {
                throw new ArgumentException("Path has no DOS drive root", "path");
            }
            var target = new StringBuilder(4096);
            uint length = QueryDosDeviceW(root.Substring(0, 2), target, target.Capacity);
            if (length == 0) {
                throw new Win32Exception(Marshal.GetLastWin32Error(), "QueryDosDeviceW failed");
            }
            string device = target.ToString().TrimEnd('\\');
            if (!device.StartsWith(@"\Device\HarddiskVolume", StringComparison.OrdinalIgnoreCase)) {
                throw new InvalidOperationException(
                    "External one-shot wrappers refuse SUBST, mapped, or non-local DOS drive aliases"
                );
            }
            return device;
        }

        public static void AssertOrdinaryLocalDrive(string path) {
            GetOrdinaryLocalDriveDevice(path);
        }

        private static string ExpandExistingLongPath(string path) {
            var buffer = new StringBuilder(512);
            uint length = GetLongPathNameW(path, buffer, (uint)buffer.Capacity);
            if (length == 0) {
                throw new Win32Exception(
                    Marshal.GetLastWin32Error(),
                    "GetLongPathNameW failed for external artifact path canonicalization"
                );
            }
            if (length >= buffer.Capacity) {
                buffer = new StringBuilder(checked((int)length + 1));
                length = GetLongPathNameW(path, buffer, (uint)buffer.Capacity);
                if (length == 0 || length >= buffer.Capacity) {
                    throw new Win32Exception(
                        Marshal.GetLastWin32Error(),
                        "GetLongPathNameW returned an unstable path length"
                    );
                }
            }
            return buffer.ToString();
        }

        public static string ResolveExisting(string path) {
            IntPtr handle = CreateFileW(
                path,
                0,
                FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                IntPtr.Zero,
                OPEN_EXISTING,
                FILE_FLAG_BACKUP_SEMANTICS,
                IntPtr.Zero
            );
            if (handle == new IntPtr(-1)) {
                throw new Win32Exception(
                    Marshal.GetLastWin32Error(),
                    "CreateFileW failed for external artifact path canonicalization"
                );
            }
            try {
                var buffer = new StringBuilder(512);
                uint length = GetFinalPathNameByHandleW(handle, buffer, (uint)buffer.Capacity, 0);
                if (length == 0) {
                    throw new Win32Exception(
                        Marshal.GetLastWin32Error(),
                        "GetFinalPathNameByHandleW failed"
                    );
                }
                if (length >= buffer.Capacity) {
                    buffer = new StringBuilder(checked((int)length + 1));
                    length = GetFinalPathNameByHandleW(
                        handle,
                        buffer,
                        (uint)buffer.Capacity,
                        0
                    );
                    if (length == 0 || length >= buffer.Capacity) {
                        throw new Win32Exception(
                            Marshal.GetLastWin32Error(),
                            "GetFinalPathNameByHandleW returned an unstable path length"
                        );
                    }
                }
                string result = buffer.ToString();
                if (result.StartsWith(@"\\?\UNC\", StringComparison.OrdinalIgnoreCase)) {
                    result = @"\\" + result.Substring(8);
                }
                else if (result.StartsWith(@"\\?\", StringComparison.OrdinalIgnoreCase)) {
                    result = result.Substring(4);
                }
                return ExpandExistingLongPath(result);
            } finally {
                CloseHandle(handle);
            }
        }

        public static string ResolveExistingDevicePath(string path) {
            string resolved = ResolveExisting(path);
            string root = System.IO.Path.GetPathRoot(resolved);
            if (String.IsNullOrEmpty(root) || root.Length < 2 || root[1] != ':') {
                throw new InvalidOperationException(
                    "Handle canonicalization did not return an ordinary DOS drive path"
                );
            }
            string device = GetOrdinaryLocalDriveDevice(resolved);
            string suffix = resolved.Substring(root.Length).TrimStart('\\', '/');
            return suffix.Length == 0 ? device : device + @"\" + suffix;
        }
    }
}
'@

function Get-NormalizedLocalDosPath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Role
    )

    $fullPath = [System.IO.Path]::GetFullPath($Path)
    $root = [System.IO.Path]::GetPathRoot($fullPath)
    if ($fullPath.StartsWith('\\', [System.StringComparison]::Ordinal) -or
        $root -notmatch '^[A-Za-z]:[\\/]$') {
        throw "$Role must use an ordinary local DOS drive path."
    }
    [FeishuCodexBridge.ExternalOneShotPath]::AssertOrdinaryLocalDrive($fullPath)
    if ($fullPath.Equals($root, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $root
    }
    return $fullPath.TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
}

function Test-IsWithinPath {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Candidate
    )

    $rootPath = $Root.TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
    $candidatePath = $Candidate.TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
    if ([string]::Equals($rootPath, $candidatePath, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $true
    }
    return $candidatePath.StartsWith(
        $rootPath + [System.IO.Path]::DirectorySeparatorChar,
        [System.StringComparison]::OrdinalIgnoreCase
    )
}

function Assert-NoReparseExistingPathPrefix {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [string]$Role = 'ArtifactRoot'
    )

    $current = Get-NormalizedLocalDosPath -Path $Path -Role $Role
    while (-not (Test-Path -LiteralPath $current)) {
        $parent = [System.IO.Directory]::GetParent($current)
        if ($null -eq $parent) {
            throw 'ArtifactRoot does not have an existing ordinary path prefix.'
        }
        $current = $parent.FullName
    }
    while ($true) {
        $item = Get-Item -LiteralPath $current -Force -ErrorAction Stop
        if ([string]$item.PSProvider.Name -ne 'FileSystem') {
            throw "$Role path chain must use the FileSystem provider."
        }
        if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
            throw "$Role path chain contains a reparse point: $($item.FullName)"
        }
        $parent = [System.IO.Directory]::GetParent($current)
        if ($null -eq $parent) { break }
        $current = $parent.FullName
    }
}

function Get-PhysicalComparisonPath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Role,
        [switch]$RejectLexicalAlias
    )

    $fullPath = Get-NormalizedLocalDosPath -Path $Path -Role $Role
    Assert-NoReparseExistingPathPrefix -Path $fullPath -Role $Role
    $existing = $fullPath
    $suffix = New-Object 'System.Collections.Generic.List[string]'
    while (-not (Test-Path -LiteralPath $existing)) {
        $leaf = [System.IO.Path]::GetFileName($existing)
        $parent = [System.IO.Path]::GetDirectoryName($existing)
        if ([string]::IsNullOrEmpty($leaf) -or [string]::IsNullOrEmpty($parent) -or
            $parent.Equals($existing, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "$Role has no resolvable existing path prefix."
        }
        $suffix.Insert(0, $leaf)
        $existing = Get-NormalizedLocalDosPath -Path $parent -Role $Role
    }
    $resolvedExisting = Get-NormalizedLocalDosPath `
        -Path ([FeishuCodexBridge.ExternalOneShotPath]::ResolveExisting($existing)) `
        -Role $Role
    if ($RejectLexicalAlias -and
        -not $existing.Equals($resolvedExisting, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "$Role must not use an 8.3 or other lexical filesystem alias."
    }
    $dosPath = $resolvedExisting
    $devicePath = [FeishuCodexBridge.ExternalOneShotPath]::ResolveExistingDevicePath($existing)
    foreach ($segment in $suffix) {
        $dosPath = [System.IO.Path]::Combine($dosPath, $segment)
        $devicePath = $devicePath.TrimEnd('\') + '\' + $segment
    }
    return [pscustomobject][ordered]@{
        DosPath = Get-NormalizedLocalDosPath -Path $dosPath -Role $Role
        DevicePath = $devicePath.TrimEnd('\')
    }
}

function Assert-PhysicalIsolation {
    param(
        [Parameter(Mandatory = $true)]$ArtifactRecord,
        [Parameter(Mandatory = $true)][object[]]$ProtectedRecords
    )

    foreach ($protectedRecord in $ProtectedRecords) {
        if ((Test-IsWithinPath -Root $protectedRecord.DevicePath -Candidate $ArtifactRecord.DevicePath) -or
            (Test-IsWithinPath -Root $ArtifactRecord.DevicePath -Candidate $protectedRecord.DevicePath)) {
            throw 'ArtifactRoot must be separate from Desktop, Harness, project, and runtime roots.'
        }
    }
}

function Resolve-ArtifactRoot {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string[]]$ProtectedRoots
    )

    if (-not [System.IO.Path]::IsPathFullyQualified($Path) -or
        $Path.StartsWith('\\?\', [System.StringComparison]::Ordinal) -or
        $Path.StartsWith('\\.\', [System.StringComparison]::Ordinal)) {
        throw 'ArtifactRoot must be an ordinary fully qualified filesystem path.'
    }
    # Keep the caller's DOS spelling until after the lexical gate.  On Windows,
    # Path.GetFullPath can expand an existing 8.3 segment (for example,
    # PROGRA~1) before the later handle-based comparison sees it.  Only slash
    # direction and terminal separators are non-semantic here; every other
    # rewrite (short name, dot segment, or repeated interior separator) is an
    # alias and must fail closed.
    $lexicalPath = $Path.Replace('/', '\')
    if ($lexicalPath -match '^[A-Za-z]:\\+$') {
        $lexicalPath = $lexicalPath.Substring(0, 2) + '\'
    }
    else {
        $lexicalPath = $lexicalPath.TrimEnd('\')
    }
    $fullPath = Get-NormalizedLocalDosPath -Path $Path -Role 'ArtifactRoot'
    if (-not $lexicalPath.Equals(
            $fullPath,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
        throw 'ArtifactRoot must not use an 8.3 or other lexical filesystem alias.'
    }
    $driveRoot = [System.IO.Path]::GetPathRoot($fullPath)
    if ([string]::IsNullOrWhiteSpace($driveRoot) -or $driveRoot -cnotmatch '^[A-Za-z]:\\$') {
        throw 'ArtifactRoot must be on an ordinary local drive.'
    }
    $drive = [System.IO.DriveInfo]::new($driveRoot)
    if (-not $drive.IsReady -or $drive.DriveType -ne [System.IO.DriveType]::Fixed) {
        throw 'ArtifactRoot must be on an ordinary ready fixed local drive.'
    }
    if ([string]::Equals(
            $fullPath,
            $driveRoot,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
        throw 'ArtifactRoot cannot be a filesystem root.'
    }
    $artifactPre = Get-PhysicalComparisonPath `
        -Path $fullPath `
        -Role 'ArtifactRoot' `
        -RejectLexicalAlias
    $protectedPre = @(
        foreach ($protectedRoot in $ProtectedRoots) {
            if (-not (Test-Path -LiteralPath $protectedRoot)) {
                throw 'A protected root does not exist.'
            }
            Assert-NoReparseExistingPathPrefix -Path $protectedRoot -Role 'protected root'
            Get-PhysicalComparisonPath -Path $protectedRoot -Role 'protected root'
        }
    )
    Assert-PhysicalIsolation -ArtifactRecord $artifactPre -ProtectedRecords $protectedPre
    if (Test-Path -LiteralPath $fullPath) {
        if (-not (Test-Path -LiteralPath $fullPath -PathType Container)) {
            throw 'ArtifactRoot exists but is not a directory.'
        }
    }
    else {
        New-Item -ItemType Directory -Path $fullPath -ErrorAction Stop | Out-Null
    }
    Assert-NoReparseExistingPathPrefix -Path $fullPath -Role 'ArtifactRoot'
    $artifactPost = Get-PhysicalComparisonPath `
        -Path $fullPath `
        -Role 'ArtifactRoot' `
        -RejectLexicalAlias
    if (-not $artifactPost.DevicePath.Equals(
            $artifactPre.DevicePath,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
        throw 'ArtifactRoot changed physical identity while it was being prepared.'
    }
    $protectedPost = @(
        foreach ($protectedRoot in $ProtectedRoots) {
            if (-not (Test-Path -LiteralPath $protectedRoot)) {
                throw 'A protected root disappeared during ArtifactRoot preparation.'
            }
            Assert-NoReparseExistingPathPrefix -Path $protectedRoot -Role 'protected root'
            Get-PhysicalComparisonPath -Path $protectedRoot -Role 'protected root'
        }
    )
    if ($protectedPre.Count -ne $protectedPost.Count) {
        throw 'Protected-root physical identity set changed during ArtifactRoot preparation.'
    }
    for ($index = 0; $index -lt $protectedPre.Count; $index += 1) {
        if (-not $protectedPre[$index].DevicePath.Equals(
                $protectedPost[$index].DevicePath,
                [System.StringComparison]::OrdinalIgnoreCase
            )) {
            throw 'A protected root changed physical identity during ArtifactRoot preparation.'
        }
    }
    Assert-PhysicalIsolation -ArtifactRecord $artifactPost -ProtectedRecords $protectedPost
    return $artifactPost.DosPath
}

if (-not $ExternalSoakAcknowledged) {
    throw 'This wrapper requires the external-supervisor verification switch.'
}

$desktop = [System.IO.Path]::GetFullPath($DesktopRoot)
$repositoryCandidate = Split-Path -Parent (Split-Path -Parent $desktop)
$repositoryMarketplace = Join-Path $repositoryCandidate '.agents\plugins\marketplace.json'
if (-not [System.IO.Path]::IsPathFullyQualified($HarnessRoot) -or
    $HarnessRoot.StartsWith('\\', [System.StringComparison]::Ordinal)) {
    throw 'HarnessRoot must be an explicit ordinary fully qualified filesystem path.'
}
if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = if (Test-Path -LiteralPath $repositoryMarketplace -PathType Leaf) {
        $repositoryCandidate
    } else {
        $desktop
    }
}
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

$protectedRoots = @($desktop, $harness, $project, (Split-Path -Parent $python), $PSHOME)
$artifact = Resolve-ArtifactRoot -Path $ArtifactRoot -ProtectedRoots $protectedRoots
$runTag = '{0}-{1}' -f (Get-Date -Format 'yyyyMMdd-HHmmss'), ([guid]::NewGuid().ToString('N').Substring(0, 8))
$runRoot = Join-Path $artifact "p3-soak-$runTag"
if (Test-Path -LiteralPath $runRoot) {
    throw "Unique P3 artifact run destination already exists: $runRoot"
}
$artifactVerified = Resolve-ArtifactRoot -Path $artifact -ProtectedRoots $protectedRoots
if (-not $artifactVerified.Equals($artifact, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'ArtifactRoot canonical path changed before P3 run-directory creation.'
}
New-Item -ItemType Directory -Path $runRoot -ErrorAction Stop | Out-Null
$workRoot = Join-Path $runRoot 'work'
$evidenceRoot = Join-Path $runRoot 'evidence'
Assert-NoReparseExistingPathPrefix -Path $runRoot -Role 'P3 run root'
$artifactRecord = Get-PhysicalComparisonPath `
    -Path $artifact `
    -Role 'ArtifactRoot' `
    -RejectLexicalAlias
$runRecord = Get-PhysicalComparisonPath `
    -Path $runRoot `
    -Role 'P3 run root' `
    -RejectLexicalAlias
if (-not (Test-IsWithinPath -Root $artifactRecord.DevicePath -Candidate $runRecord.DevicePath) -or
    $artifactRecord.DevicePath.Equals($runRecord.DevicePath, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'P3 run root escaped the physical ArtifactRoot boundary.'
}
New-Item -ItemType Directory -Path $workRoot, $evidenceRoot -ErrorAction Stop | Out-Null
Assert-NoReparseExistingPathPrefix -Path $workRoot
Assert-NoReparseExistingPathPrefix -Path $evidenceRoot

function Invoke-CleanPowerShellJson {
    param(
        [Parameter(Mandatory = $true)][string]$ScriptPath,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList,
        [Parameter(Mandatory = $true)][string]$StageName,
        [Parameter(Mandatory = $true)][ValidateRange(60, 1800)][int]$StageTimeoutSeconds
    )
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $pwsh
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    foreach ($argument in @(
        '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
        '-File', $ScriptPath
    ) + $ArgumentList) {
        [void]$startInfo.ArgumentList.Add([string]$argument)
    }

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    $processStarted = $false
    $stdoutTask = $null
    $stderrTask = $null
    try {
        if (-not $process.Start()) {
            throw "$StageName process did not start."
        }
        $processStarted = $true
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($StageTimeoutSeconds * 1000)) {
            try {
                $process.Kill($true)
            } catch {
                throw "$StageName timed out and its process tree could not be terminated."
            }
            if (-not $process.WaitForExit(30000)) {
                throw "$StageName timed out and its process tree did not exit within 30 seconds."
            }
            if (-not [System.Threading.Tasks.Task]::WaitAll(
                    [System.Threading.Tasks.Task[]]@($stdoutTask, $stderrTask),
                    30000
                )) {
                throw "$StageName timed out and its output pipes did not close within 30 seconds."
            }
            throw "$StageName exceeded its $StageTimeoutSeconds second wrapper timeout."
        }
        if (-not [System.Threading.Tasks.Task]::WaitAll(
                [System.Threading.Tasks.Task[]]@($stdoutTask, $stderrTask),
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
    if ($exitCode -ne 0) {
        $details = @(
            (($stderr + [Environment]::NewLine + $stdout) -split '\r?\n') |
                Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
                Select-Object -Last 80
        ) -join [Environment]::NewLine
        throw (
            "$StageName failed with exit code $exitCode. " +
            "Work: $workRoot Evidence: $evidenceRoot" +
            $(if ([string]::IsNullOrWhiteSpace($details)) { '' } else {
                [Environment]::NewLine + $details
            })
        )
    }
    if (-not [string]::IsNullOrWhiteSpace($stderr)) {
        throw "$StageName wrote unexpected stderr despite a zero exit code."
    }
    $nonempty = @(
        ($stdout -split '\r?\n') | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )
    if ($nonempty.Count -ne 1) {
        throw "$StageName returned $($nonempty.Count) nonempty lines instead of one JSON object."
    }
    try { return $nonempty[0] | ConvertFrom-Json -ErrorAction Stop }
    catch {
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
    $supervisorStageTimeout = [Math]::Min(1800, $TimeoutSeconds + 360)
    $validatorStageTimeout = 420
    $envelope = Invoke-CleanPowerShellJson -ScriptPath $supervisor -StageName 'P3 soak supervisor' `
        -StageTimeoutSeconds $supervisorStageTimeout `
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
    if ([int]$envelope.schema_version -ne 2 -or
        [string]$envelope.runner_status -cne 'pass' -or
        [string]$envelope.evidence_file -cnotmatch '^p3-soak-v2-[a-f0-9-]{36}\.json$' -or
        [System.IO.Path]::GetFileName([string]$envelope.evidence_file) -cne
            [string]$envelope.evidence_file -or
        [string]$envelope.evidence_sha256 -cnotmatch '^[a-f0-9]{64}$') {
        throw 'P3 soak supervisor JSON failed its envelope contract.'
    }
    $evidencePath = Join-Path $evidenceRoot ([string]$envelope.evidence_file)
    $envelope | Add-Member -NotePropertyName evidence_path -NotePropertyValue $evidencePath
    $validation = Invoke-CleanPowerShellJson -ScriptPath $validator -StageName 'P3 soak semantic validator' `
        -StageTimeoutSeconds $validatorStageTimeout `
        -ArgumentList @(
            '-DesktopRoot', $desktop,
            '-HarnessRoot', $harness,
            '-ProjectRoot', $project,
            '-EvidencePath', $evidencePath,
            '-ExpectedEvidenceSha256', ([string]$envelope.evidence_sha256),
            '-P0EvidencePath', $p0Evidence,
            '-ExpectedP0EvidenceSha256', $ExpectedP0EvidenceSha256
        )
    if ([int]$validation.validation_schema_version -ne 2 -or
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
