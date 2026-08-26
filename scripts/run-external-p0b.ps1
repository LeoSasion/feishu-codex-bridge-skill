[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$DesktopRoot,
    [Parameter(Mandatory = $true)][string]$HarnessRoot,
    [Parameter(Mandatory = $true)][string]$ProjectRoot,
    [Parameter(Mandatory = $true)][string]$PythonExecutable,
    [Parameter(Mandatory = $true)][string]$ExternalWorkRoot,
    [Parameter(Mandatory = $true)][string]$EvidenceDirectory,
    [ValidateSet('external_terminal', 'ci')][string]$RunnerSurface = 'external_terminal',
    [switch]$ExternalTestRunnerAcknowledged
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
    throw 'P0-B must be launched by a clean pwsh -NoProfile -NonInteractive -File invocation.'
}
$fileArgumentIndex = [int]$fileArgumentIndexes[0]
if ($fileArgumentIndex -lt 1 -or $fileArgumentIndex + 1 -ge $nativeInvocation.Count) {
    throw 'P0-B clean PowerShell invocation is incomplete.'
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
    throw 'P0-B requires the exact audited script under pwsh -NoProfile -NonInteractive -File.'
}

if (-not $ExternalTestRunnerAcknowledged -or $env:FEISHU_BRIDGE_EXTERNAL_TEST_RUNNER -ne '1') {
    throw ('P0-B may run only from an external terminal or CI. Set ' +
        'FEISHU_BRIDGE_EXTERNAL_TEST_RUNNER=1 and pass -ExternalTestRunnerAcknowledged.')
}
if ($env:CODEX_BRIDGE_CHILD -eq '1') {
    throw 'P0-B refuses to run from a Codex child process.'
}
$isWindowsHost = [System.Runtime.InteropServices.RuntimeInformation]::IsOSPlatform(
    [System.Runtime.InteropServices.OSPlatform]::Windows
)
if (-not $isWindowsHost) {
    throw 'This Desktop Bridge P0-B supervisor currently supports Windows only.'
}
if ([string]$PSVersionTable.PSEdition -cne 'Core' -or
    [version]$PSVersionTable.PSVersion -lt [version]'7.4') {
    throw 'P0-B requires PowerShell 7.4+ under a clean pwsh process.'
}
$requiredPowerShellModules = @(
    [System.IO.Path]::Combine($PSHOME, 'Modules', 'Microsoft.PowerShell.Utility', 'Microsoft.PowerShell.Utility.psd1'),
    [System.IO.Path]::Combine($PSHOME, 'Modules', 'Microsoft.PowerShell.Security', 'Microsoft.PowerShell.Security.psd1'),
    [System.IO.Path]::Combine($PSHOME, 'Modules', 'Microsoft.PowerShell.Management', 'Microsoft.PowerShell.Management.psd1'),
    [System.IO.Path]::Combine($PSHOME, 'Modules', 'CimCmdlets', 'CimCmdlets.psd1')
)
foreach ($modulePath in $requiredPowerShellModules) {
    if (-not [System.IO.File]::Exists($modulePath)) {
        throw 'P0-B clean PowerShell installation is missing a required built-in module.'
    }
    Microsoft.PowerShell.Core\Import-Module -Name $modulePath -Force -ErrorAction Stop
}
$PSModuleAutoLoadingPreference = 'None'

$script:Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$script:StrictUtf8 = New-Object System.Text.UTF8Encoding($false, $true)
$script:P0BMaxFileBytes = 2097152L
$script:ChildEnvironmentPrefixes = @(
    'PYTHON',
    'CODEX_BRIDGE_',
    'FEISHU_',
    'LARK_',
    'LARKCLI_'
)

if ($null -ne ('FeishuCodexBridge.ExternalP0BJob' -as [type]) -or
    $null -ne ('FeishuCodexBridge.ExternalP0BPath' -as [type])) {
    throw 'P0-B native helper types were already loaded before the clean supervisor initialized.'
}
if ($null -eq ('FeishuCodexBridge.ExternalP0BJob' -as [type])) {
    Microsoft.PowerShell.Utility\Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;

namespace FeishuCodexBridge {
    public static class ExternalP0BPath {
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

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool CloseHandle(IntPtr handle);

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
            string device = target.ToString();
            if (!device.StartsWith(@"\Device\HarddiskVolume", StringComparison.OrdinalIgnoreCase)) {
                throw new InvalidOperationException("P0-B refuses SUBST, mapped, or non-local DOS drive aliases");
            }
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
                throw new Win32Exception(Marshal.GetLastWin32Error(), "CreateFileW failed for path canonicalization");
            }
            try {
                var buffer = new StringBuilder(512);
                uint length = GetFinalPathNameByHandleW(handle, buffer, (uint)buffer.Capacity, 0);
                if (length == 0) {
                    throw new Win32Exception(Marshal.GetLastWin32Error(), "GetFinalPathNameByHandleW failed");
                }
                if (length >= buffer.Capacity) {
                    buffer = new StringBuilder(checked((int)length + 1));
                    length = GetFinalPathNameByHandleW(handle, buffer, (uint)buffer.Capacity, 0);
                    if (length == 0 || length >= buffer.Capacity) {
                        throw new Win32Exception(Marshal.GetLastWin32Error(), "GetFinalPathNameByHandleW returned an unstable path length");
                    }
                }
                string result = buffer.ToString();
                if (result.StartsWith(@"\\?\UNC\", StringComparison.OrdinalIgnoreCase)) {
                    return @"\\" + result.Substring(8);
                }
                if (result.StartsWith(@"\\?\", StringComparison.OrdinalIgnoreCase)) {
                    return result.Substring(4);
                }
                return result;
            } finally {
                CloseHandle(handle);
            }
        }
    }

    public sealed class ExternalP0BJob : IDisposable {
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

        public ExternalP0BJob() {
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

        ~ExternalP0BJob() {
            Dispose();
        }
    }
}
'@
}

function Get-BytesSha256 {
    param([Parameter(Mandatory = $true)][AllowEmptyCollection()][byte[]]$Bytes)
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([System.BitConverter]::ToString($algorithm.ComputeHash($Bytes))).Replace('-', '').ToLowerInvariant()
    } finally {
        $algorithm.Dispose()
    }
}

function Get-StringSha256 {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Text)
    return Get-BytesSha256 -Bytes ([System.Text.Encoding]::UTF8.GetBytes($Text))
}

function Get-FileSha256 {
    param([Parameter(Mandatory = $true)][string]$Path)
    return (Microsoft.PowerShell.Utility\Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-StreamSha256 {
    param([Parameter(Mandatory = $true)][System.IO.Stream]$Stream)
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        if ($Stream.CanSeek) { $Stream.Position = 0 }
        $hash = ([System.BitConverter]::ToString($algorithm.ComputeHash($Stream))).Replace('-', '').ToLowerInvariant()
        if ($Stream.CanSeek) { $Stream.Position = 0 }
        return $hash
    } finally {
        $algorithm.Dispose()
    }
}

function Read-BoundedFileBytes {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][long]$MaximumBytes
    )
    $stream = New-Object System.IO.FileStream(
        $Path,
        [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Read,
        [System.IO.FileShare]::Read
    )
    try {
        if ($stream.Length -gt $MaximumBytes -or $stream.Length -gt [int]::MaxValue) {
            throw 'P0-B file exceeds the bounded read limit.'
        }
        $bytes = New-Object byte[] ([int]$stream.Length)
        $offset = 0
        while ($offset -lt $bytes.Length) {
            $count = $stream.Read($bytes, $offset, $bytes.Length - $offset)
            if ($count -le 0) { throw 'P0-B file ended during its bounded read.' }
            $offset += $count
        }
        if ($stream.ReadByte() -ne -1) { throw 'P0-B file grew during its bounded read.' }
        return ,$bytes
    } finally {
        $stream.Dispose()
    }
}

function Read-PinnedStreamBytes {
    param(
        [Parameter(Mandatory = $true)][System.IO.FileStream]$Stream,
        [Parameter(Mandatory = $true)][long]$MaximumBytes
    )
    if ($Stream.Length -gt $MaximumBytes -or $Stream.Length -gt [int]::MaxValue) {
        throw 'Pinned P0-B file exceeds the bounded read limit.'
    }
    $Stream.Position = 0
    $bytes = New-Object byte[] ([int]$Stream.Length)
    $offset = 0
    while ($offset -lt $bytes.Length) {
        $count = $Stream.Read($bytes, $offset, $bytes.Length - $offset)
        if ($count -le 0) { throw 'Pinned P0-B file ended during its bounded read.' }
        $offset += $count
    }
    if ($Stream.ReadByte() -ne -1) { throw 'Pinned P0-B file grew during its bounded read.' }
    $Stream.Position = 0
    return ,$bytes
}

function Get-UtcTimestamp {
    param([DateTimeOffset]$Value = [DateTimeOffset]::UtcNow)
    return $Value.UtcDateTime.ToString('o', [System.Globalization.CultureInfo]::InvariantCulture)
}

function Get-NormalizedFullPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    $fullPath = [System.IO.Path]::GetFullPath($Path)
    $pathRoot = [System.IO.Path]::GetPathRoot($fullPath)
    if ($fullPath.StartsWith('\\', [System.StringComparison]::Ordinal) -or
        $pathRoot -notmatch '^[A-Za-z]:[\\/]$') {
        throw 'P0-B paths must use an unambiguous local DOS drive path; UNC and device namespaces are refused.'
    }
    [FeishuCodexBridge.ExternalP0BPath]::AssertOrdinaryLocalDrive($fullPath)
    if ($fullPath.Equals($pathRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $pathRoot
    }
    return $fullPath.TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
}

function Get-CanonicalComparisonPath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Role
    )
    $fullPath = Get-NormalizedFullPath -Path $Path
    [void](Test-NoReparsePathChain -Path $fullPath -Role $Role)
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
        $existing = Get-NormalizedFullPath -Path $parent
    }
    $canonical = Get-NormalizedFullPath -Path `
        ([FeishuCodexBridge.ExternalP0BPath]::ResolveExisting($existing))
    foreach ($segment in $suffix) { $canonical = Join-Path $canonical $segment }
    return Get-NormalizedFullPath -Path $canonical
}

function Test-NoReparsePathChain {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Role
    )
    $fullPath = Get-NormalizedFullPath -Path $Path
    $pathRoot = [System.IO.Path]::GetPathRoot($fullPath)
    $current = Get-NormalizedFullPath -Path $pathRoot
    $segments = @(
        $fullPath.Substring($pathRoot.Length).TrimStart('\', '/').Split(
            [char[]]@([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar),
            [System.StringSplitOptions]::RemoveEmptyEntries
        )
    )
    foreach ($segment in @('') + $segments) {
        if ($segment) { $current = Join-Path $current $segment }
        $item = Get-Item -LiteralPath $current -Force -ErrorAction SilentlyContinue
        if ($null -eq $item) {
            return $false
        }
        if ([string]$item.PSProvider.Name -ne 'FileSystem') {
            throw "$Role must use the FileSystem provider."
        }
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "$Role must not contain a reparse point in its path chain."
        }
    }
    return $true
}

function Assert-NoReparsePathChain {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Role
    )
    if (-not (Test-NoReparsePathChain -Path $Path -Role $Role)) {
        throw "$Role contains a missing path segment."
    }
}

function Test-SafeRelativePath {
    param([AllowEmptyString()][string]$Path)
    return [bool](
        $Path -and -not $Path.Contains('\') -and -not $Path.StartsWith('/') -and
        $Path -notmatch '(^|/)\.\.?(/|$)' -and $Path -notmatch '[:\x00-\x1f]'
    )
}

function Resolve-ExistingPath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Role,
        [switch]$Leaf
    )
    $resolved = Get-NormalizedFullPath -Path $Path
    $pathType = if ($Leaf) { 'Leaf' } else { 'Container' }
    Assert-NoReparsePathChain -Path $resolved -Role $Role
    if (-not (Test-Path -LiteralPath $resolved -PathType $pathType)) {
        throw "$Role has the wrong path type."
    }
    $canonical = Get-CanonicalComparisonPath -Path $resolved -Role $Role
    Assert-NoReparsePathChain -Path $canonical -Role $Role
    if (-not (Test-Path -LiteralPath $canonical -PathType $pathType)) {
        throw "$Role changed path type during physical canonicalization."
    }
    return $canonical
}

function Test-IsWithinRoot {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Candidate
    )
    $rootPath = Get-CanonicalComparisonPath -Path $Root -Role 'isolation root'
    $candidatePath = Get-CanonicalComparisonPath -Path $Candidate -Role 'isolation candidate'
    if ($candidatePath.Equals($rootPath, [System.StringComparison]::OrdinalIgnoreCase)) { return $true }
    $rootPrefix = if ($rootPath.EndsWith([string][System.IO.Path]::DirectorySeparatorChar) -or
        $rootPath.EndsWith([string][System.IO.Path]::AltDirectorySeparatorChar)) {
        $rootPath
    } else {
        $rootPath + [System.IO.Path]::DirectorySeparatorChar
    }
    return $candidatePath.StartsWith(
        $rootPrefix,
        [System.StringComparison]::OrdinalIgnoreCase
    )
}

function Assert-ExternalDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$Directory,
        [Parameter(Mandatory = $true)][string]$Role,
        [Parameter(Mandatory = $true)][string[]]$ForbiddenRoots
    )
    $resolved = Resolve-ExistingPath -Path $Directory -Role $Role
    foreach ($root in $ForbiddenRoots) {
        if ((Test-IsWithinRoot -Root $root -Candidate $resolved) -or
            (Test-IsWithinRoot -Root $resolved -Candidate $root)) {
            throw "$Role must be separate from source, project, and live runtime roots."
        }
    }
    return $resolved
}

function Write-NewUtf8File {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Text
    )
    $stream = New-Object System.IO.FileStream(
        $Path,
        [System.IO.FileMode]::CreateNew,
        [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::None
    )
    try {
        $writer = New-Object System.IO.StreamWriter($stream, $script:Utf8NoBom)
        try {
            $writer.Write($Text)
            $writer.Flush()
            $writer.BaseStream.Flush()
        } finally {
            $writer.Dispose()
        }
    } finally {
        if ($null -ne $stream) { $stream.Dispose() }
    }
}

function Write-NewBytesFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][byte[]]$Bytes
    )
    $stream = New-Object System.IO.FileStream(
        $Path,
        [System.IO.FileMode]::CreateNew,
        [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::None
    )
    try {
        $stream.Write($Bytes, 0, $Bytes.Length)
        $stream.Flush()
    } finally {
        $stream.Dispose()
    }
}

function Invoke-CapturedProcess {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][hashtable]$EnvironmentOverrides,
        [Parameter(Mandatory = $true)][string]$CapturePrefix,
        [ValidateRange(1, 7200)][int]$TimeoutSeconds = 300
    )
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $Executable
    foreach ($argument in $Arguments) {
        [void]$startInfo.ArgumentList.Add([string]$argument)
    }
    $startInfo.WorkingDirectory = $WorkingDirectory
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $inheritedSensitiveNames = @(
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
    foreach ($name in $inheritedSensitiveNames) {
        [void]$startInfo.EnvironmentVariables.Remove([string]$name)
    }
    foreach ($entry in $EnvironmentOverrides.GetEnumerator()) {
        $startInfo.EnvironmentVariables[[string]$entry.Key] = [string]$entry.Value
    }
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    $job = [FeishuCodexBridge.ExternalP0BJob]::new()
    $jobDisposed = $false
    $processStarted = $false
    $stdoutTask = $null
    $stderrTask = $null
    try {
        $startedAt = [DateTimeOffset]::UtcNow
        if (-not $process.Start()) { throw 'External child process did not start.' }
        $processStarted = $true
        $job.Assign($process)
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $timedOut = -not $process.WaitForExit($TimeoutSeconds * 1000)
        if ($timedOut) {
            try {
                # The bool overload terminates the exact child tree, including
                # descendants that may still hold redirected pipe handles.
                $process.Kill($true)
            } catch {
                throw 'External child process timed out and its process tree could not be terminated.'
            }
            if (-not $process.WaitForExit(30000)) {
                throw 'External child process tree did not exit within the bounded termination window.'
            }
        }
        # Closing a KILL_ON_JOB_CLOSE job after its direct child exits removes
        # normal-success descendants before redirected pipe completion is trusted.
        $job.Dispose()
        $jobDisposed = $true
        $captureTasks = [System.Threading.Tasks.Task[]]@($stdoutTask, $stderrTask)
        if (-not [System.Threading.Tasks.Task]::WaitAll($captureTasks, 30000)) {
            throw 'External child output pipes did not close within the bounded capture window.'
        }
        $stdout = $stdoutTask.GetAwaiter().GetResult()
        $stderr = $stderrTask.GetAwaiter().GetResult()
        $finishedAt = [DateTimeOffset]::UtcNow
        $exitCode = $process.ExitCode
        $stdoutPath = $CapturePrefix + '.stdout.txt'
        $stderrPath = $CapturePrefix + '.stderr.txt'
        Write-NewUtf8File -Path $stdoutPath -Text $stdout
        Write-NewUtf8File -Path $stderrPath -Text $stderr
        if ($timedOut) {
            throw "External child process exceeded its $TimeoutSeconds second timeout."
        }
        return [pscustomobject]@{
            Argv = @($Executable) + @($Arguments)
            StartedAt = $startedAt
            FinishedAt = $finishedAt
            ExitCode = $exitCode
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

function Get-BoundedRuntimeSnapshot {
    param(
        [Parameter(Mandatory = $true)][string]$Project,
        [Parameter(Mandatory = $true)][string]$Runtime
    )
    $paths = [ordered]@{
        'runtime/bridge.py' = Join-Path $Runtime 'bridge.py'
        'runtime/router_queue.py' = Join-Path $Runtime 'router_queue.py'
        'runtime/bridge_core/__init__.py' = Join-Path $Runtime 'bridge_core\__init__.py'
        'runtime/bridge_core/codex_client.py' = Join-Path $Runtime 'bridge_core\codex_client.py'
        'runtime/bridge_core/config.py' = Join-Path $Runtime 'bridge_core\config.py'
        'runtime/bridge_core/desktop_router.py' = Join-Path $Runtime 'bridge_core\desktop_router.py'
        'runtime/bridge_core/lark.py' = Join-Path $Runtime 'bridge_core\lark.py'
        'runtime/bridge_core/project_routing.py' = Join-Path $Runtime 'bridge_core\project_routing.py'
        'runtime/bridge_core/runtime.py' = Join-Path $Runtime 'bridge_core\runtime.py'
        'runtime/bridge_core/state.py' = Join-Path $Runtime 'bridge_core\state.py'
        'runtime/bridge.env' = Join-Path $Runtime 'bridge.env'
        'runtime/runtime-manifest.json' = Join-Path $Runtime 'runtime-manifest.json'
        'hooks/start' = Join-Path $Project '.codex\hooks\start-feishu-codex-bridge.ps1'
        'hooks/stop' = Join-Path $Project '.codex\hooks\stop-feishu-codex-bridge.ps1'
        'hooks/config' = Join-Path $Project '.codex\hooks.json'
        'rules/gateway' = Join-Path $Project '.codex\rules\feishu-router.rules'
    }
    $records = New-Object System.Collections.Generic.List[string]
    $present = 0
    foreach ($entry in $paths.GetEnumerator()) {
        $existsWithoutReparse = Test-NoReparsePathChain -Path $entry.Value `
            -Role ("bounded runtime/control file {0}" -f $entry.Key)
        if ($existsWithoutReparse) {
            if (-not (Test-Path -LiteralPath $entry.Value -PathType Leaf)) {
                throw "Bounded runtime/control path $($entry.Key) has the wrong type."
            }
            $present += 1
            $records.Add(("{0}`tpresent`t{1}" -f $entry.Key, (Get-FileSha256 -Path $entry.Value)))
        } else {
            $records.Add(("{0}`tabsent`t-" -f $entry.Key))
        }
    }
    $canonical = ($records -join "`n") + "`n"
    return [pscustomobject]@{
        ManifestSha256 = Get-StringSha256 -Text $canonical
        PathCount = $present
        Scope = @(
            $Runtime,
            (Join-Path $Project '.codex\hooks'),
            (Join-Path $Project '.codex\hooks.json'),
            (Join-Path $Project '.codex\rules\feishu-router.rules')
        )
    }
}

function Get-ListenerObservation {
    param(
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][string]$ShellExecutable,
        [Parameter(Mandatory = $true)][string]$Dispatcher,
        [Parameter(Mandatory = $true)][string]$Project,
        [Parameter(Mandatory = $true)][string]$Runtime,
        [Parameter(Mandatory = $true)][string]$WorkDirectory
    )
    $arguments = @(
        '-NoLogo',
        '-NoProfile',
        '-NonInteractive',
        '-ExecutionPolicy',
        'Bypass',
        '-File',
        $Dispatcher,
        'bridge',
        'status',
        '-ProjectRoot',
        $Project
    )
    $capture = Invoke-CapturedProcess -Executable $ShellExecutable -Arguments $arguments `
        -WorkingDirectory $Project -EnvironmentOverrides @{} `
        -CapturePrefix (Join-Path $WorkDirectory ("status-{0}" -f $Label))
    if ($capture.ExitCode -ne 0) { throw "Listener status $Label failed." }
    $runtimeLines = @([regex]::Matches($capture.Stdout, '(?m)^Runtime:\s*([^\r\n]+)\s*$'))
    if ($runtimeLines.Count -ne 1 -or $runtimeLines[0].Groups[1].Value.Trim() -cne 'stopped') {
        throw "Listener status $Label did not report exactly one stopped runtime."
    }

    $pidPath = Join-Path $Runtime 'bridge.pid'
    $pidFileState = 'absent'
    if (Test-Path -LiteralPath $pidPath -PathType Leaf) {
        $pidFileState = 'stale'
    }
    $bridgeScript = [System.IO.Path]::GetFullPath((Join-Path $Runtime 'bridge.py'))
    try {
        $matching = @(
            CimCmdlets\Get-CimInstance Win32_Process -ErrorAction Stop |
                Where-Object {
                    $_.CommandLine -and
                    ([string]$_.CommandLine).IndexOf($bridgeScript, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
                }
        )
    } catch {
        throw "Listener process scan $Label was unavailable."
    }
    if ($matching.Count -ne 0) {
        throw "Listener is not stopped at the $Label checkpoint."
    }
    $state = if (Test-Path -LiteralPath $Runtime -PathType Container) { 'stopped' } else { 'absent' }
    return [pscustomobject][ordered]@{
        observed_at_utc = Get-UtcTimestamp
        state = $state
        status_argv = @($capture.Argv)
        status_exit_code = 0
        status_stdout_sha256 = $capture.StdoutSha256
        pid_file_state = $pidFileState
        pid_process_alive = $false
        matching_listener_process_count = 0
    }
}

function Get-CurrentShellExecutable {
    $process = Microsoft.PowerShell.Management\Get-Process -Id $PID
    if ($process.Path -and (Test-Path -LiteralPath $process.Path -PathType Leaf)) {
        return [System.IO.Path]::GetFullPath($process.Path)
    }
    foreach ($name in @('pwsh.exe', 'powershell.exe')) {
        $candidate = Join-Path $PSHOME $name
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    }
    throw 'Could not resolve the current PowerShell executable.'
}

function Get-CurrentWindowsUserSid {
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    if ($null -eq $identity -or $null -eq $identity.User -or
        [string]::IsNullOrWhiteSpace([string]$identity.User.Value)) {
        throw 'P0-B could not resolve the current Windows user SID.'
    }
    return [string]$identity.User.Value
}

function Get-ExternalRunnerProcessGuard {
    $visited = New-Object 'System.Collections.Generic.HashSet[int]'
    $currentId = [int]$PID
    $depth = 0
    $termination = 'process_tree_root'
    while ($currentId -gt 0 -and $depth -lt 64) {
        if (-not $visited.Add($currentId)) {
            throw 'P0-B process ancestry contained a cycle.'
        }
        $processRecord = CimCmdlets\Get-CimInstance Win32_Process -Filter ("ProcessId = {0}" -f $currentId) `
            -ErrorAction Stop
        if ($null -eq $processRecord) {
            if ($depth -lt 2) {
                throw 'P0-B could not inspect itself and one live external parent.'
            }
            $termination = 'exited_ancestor'
            break
        }
        $name = [string]$processRecord.Name
        $path = [string]$processRecord.ExecutablePath
        if ($name -match '^(?i:codex)(?:\.exe)?$' -or
            $path -match '(?i)[\\/]OpenAI\.Codex(?:_|[\\/])') {
            throw 'P0-B refuses a process tree with a Codex Desktop or Codex CLI ancestor.'
        }
        $depth += 1
        $currentId = [int]$processRecord.ParentProcessId
    }
    if ($currentId -gt 0 -and $termination -eq 'process_tree_root') {
        throw 'P0-B process ancestry exceeded its bounded inspection depth.'
    }
    return [pscustomobject]@{
        Depth = $depth
        CodexMatchCount = 0
        Complete = $termination -eq 'process_tree_root'
        Termination = $termination
    }
}

function Get-TrustedPythonSignature {
    param([Parameter(Mandatory = $true)][string]$Path)
    $signature = Microsoft.PowerShell.Security\Get-AuthenticodeSignature -LiteralPath $Path -ErrorAction Stop
    if ([string]$signature.Status -cne 'Valid' -or
        $null -eq $signature.SignerCertificate -or
        [string]$signature.SignerCertificate.Subject -notmatch '(?i)(?:^|,\s*)O=Python Software Foundation(?:,|$)') {
        throw 'P0-B requires a valid Python Software Foundation Authenticode signature.'
    }
    return [pscustomobject]@{
        Status = 'valid'
        SignerSubjectSha256 = Get-StringSha256 -Text ([string]$signature.SignerCertificate.Subject)
        CertificateThumbprintSha256 = Get-StringSha256 -Text `
            ([string]$signature.SignerCertificate.Thumbprint).ToLowerInvariant()
    }
}

function Get-LifecycleMutexName {
    param(
        [Parameter(Mandatory = $true)][string]$Project,
        [Parameter(Mandatory = $true)][string]$UserSid
    )
    $material = (Get-NormalizedFullPath -Path $Project).ToUpperInvariant()
    return 'Global\FeishuCodexBridge-Lifecycle-' + `
        (Get-StringSha256 -Text ($UserSid + "`n" + $material)).Substring(0, 24)
}

function Assert-InstalledHookRegistration {
    param(
        [Parameter(Mandatory = $true)][string]$StartHookPath,
        [Parameter(Mandatory = $true)][string]$StopHookPath,
        [Parameter(Mandatory = $true)][System.IO.FileStream]$ConfigStream
    )
    $configBytes = Read-PinnedStreamBytes -Stream $ConfigStream -MaximumBytes $script:P0BMaxFileBytes
    try {
        $configText = $script:StrictUtf8.GetString($configBytes)
    } catch [System.Text.DecoderFallbackException] {
        throw 'Installed hooks.json is not strict UTF-8.'
    }
    $jsonDocument = [System.Text.Json.JsonDocument]::Parse($configText)
    try {
        $pendingElements = New-Object System.Collections.Generic.Stack[System.Text.Json.JsonElement]
        $pendingElements.Push($jsonDocument.RootElement)
        while ($pendingElements.Count -gt 0) {
            $element = $pendingElements.Pop()
            if ($element.ValueKind -eq [System.Text.Json.JsonValueKind]::Object) {
                $names = New-Object 'System.Collections.Generic.HashSet[string]' `
                    ([System.StringComparer]::OrdinalIgnoreCase)
                foreach ($property in $element.EnumerateObject()) {
                    if (-not $names.Add($property.Name)) {
                        throw 'Installed hooks.json contains duplicate or case-colliding object keys.'
                    }
                    $pendingElements.Push($property.Value)
                }
            } elseif ($element.ValueKind -eq [System.Text.Json.JsonValueKind]::Array) {
                foreach ($item in $element.EnumerateArray()) { $pendingElements.Push($item) }
            }
        }
    } finally {
        $jsonDocument.Dispose()
    }
    $config = $configText | Microsoft.PowerShell.Utility\ConvertFrom-Json
    $hookPropertyNames = @($config.hooks.PSObject.Properties.Name)
    if (@($hookPropertyNames | Where-Object { $_ -ieq 'SessionStart' }).Count -ne 1 -or
        @($hookPropertyNames | Where-Object { $_ -ieq 'SessionEnd' }).Count -ne 1 -or
        -not ($hookPropertyNames -ccontains 'SessionStart') -or
        -not ($hookPropertyNames -ccontains 'SessionEnd')) {
        throw 'Installed hooks.json lifecycle property casing is ambiguous.'
    }
    $startGroups = @($config.hooks.SessionStart)
    $stopGroups = @($config.hooks.SessionEnd)
    if ($startGroups.Count -ne 1 -or $stopGroups.Count -ne 1 -or
        [string]$startGroups[0].matcher -cne 'startup|resume' -or
        @($startGroups[0].hooks).Count -ne 1 -or
        @($stopGroups[0].hooks).Count -ne 1) {
        throw 'Installed hooks.json does not contain one exact SessionStart and SessionEnd registration.'
    }
    $startCommand = @($startGroups[0].hooks)[0]
    $stopCommand = @($stopGroups[0].hooks)[0]
    $expectedStart = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "' + `
        $StartHookPath + '" -HookInvocation'
    $expectedStop = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "' + `
        $StopHookPath + '" -HookInvocation'
    if ([string]$startCommand.type -cne 'command' -or
        [string]$startCommand.command -cne $expectedStart -or
        [string]$startCommand.commandWindows -cne $expectedStart -or
        [int]$startCommand.timeout -ne 10 -or
        [string]$stopCommand.type -cne 'command' -or
        [string]$stopCommand.command -cne $expectedStop -or
        [string]$stopCommand.commandWindows -cne $expectedStop -or
        [int]$stopCommand.timeout -ne 3) {
        throw 'Installed hooks.json command, path, or timeout differs from the audited lifecycle hooks.'
    }
    return Get-BytesSha256 -Bytes $configBytes
}

function Get-ReleaseDesktopComponent {
    param([Parameter(Mandatory = $true)][object]$ReleaseAudit)
    $components = @($ReleaseAudit.components | Where-Object { [string]$_.name -ceq 'desktop_bridge' })
    if ($components.Count -ne 1 -or [int]$components[0].file_count -ne @($components[0].files).Count) {
        throw 'Release audit has no internally consistent Desktop component.'
    }
    return $components[0]
}

function Get-DesktopSnapshotCanonicalText {
    param([Parameter(Mandatory = $true)][object]$Component)
    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add('feishu-codex-desktop-snapshot-v1')
    foreach ($file in @($Component.files)) {
        $lines.Add(("{0}`t{1}`t{2}" -f $file.path, $file.sha256, $file.size_bytes))
    }
    return ($lines -join "`n") + "`n"
}

function Get-DesktopSnapshotState {
    param(
        [Parameter(Mandatory = $true)][string]$SnapshotRoot,
        [Parameter(Mandatory = $true)][object]$ReleaseAudit
    )
    $snapshot = Resolve-ExistingPath -Path $SnapshotRoot -Role 'external Desktop snapshot'
    $expectedComponent = Get-ReleaseDesktopComponent -ReleaseAudit $ReleaseAudit
    $expectedFiles = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::Ordinal)
    $expectedDirectories = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::Ordinal)
    foreach ($file in @($expectedComponent.files)) {
        $relative = [string]$file.path
        if (-not (Test-SafeRelativePath -Path $relative) -or -not $expectedFiles.Add($relative)) {
            throw 'Release audit contains an unsafe or duplicate Desktop path.'
        }
        $segments = $relative.Split('/')
        for ($index = 1; $index -lt $segments.Count; $index++) {
            [void]$expectedDirectories.Add(($segments[0..($index - 1)] -join '/'))
        }
    }
    $actualFiles = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::Ordinal)
    $actualDirectories = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::Ordinal)
    $stack = New-Object System.Collections.Generic.Stack[object]
    $stack.Push([pscustomobject]@{ FullPath = $snapshot; RelativePath = '' })
    while ($stack.Count -gt 0) {
        $current = $stack.Pop()
        Assert-NoReparsePathChain -Path ([string]$current.FullPath) -Role 'external snapshot traversal'
        $directory = New-Object System.IO.DirectoryInfo([string]$current.FullPath)
        foreach ($entry in $directory.EnumerateFileSystemInfos()) {
            $relative = if ($current.RelativePath) {
                ([string]$current.RelativePath).TrimEnd('/') + '/' + $entry.Name
            } else {
                $entry.Name
            }
            if (($entry.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw 'External Desktop snapshot contains a reparse point.'
            }
            $isDirectory = ($entry.Attributes -band [System.IO.FileAttributes]::Directory) -ne 0
            if ($isDirectory) {
                if (-not $expectedDirectories.Contains($relative) -or
                    -not $actualDirectories.Add($relative)) {
                    throw 'External Desktop snapshot contains an unknown or duplicate directory.'
                }
                $stack.Push([pscustomobject]@{ FullPath = $entry.FullName; RelativePath = $relative })
            } else {
                if (-not $expectedFiles.Contains($relative) -or -not $actualFiles.Add($relative)) {
                    throw 'External Desktop snapshot contains an unknown, duplicate, or case-drifted file.'
                }
            }
        }
    }
    if ($actualFiles.Count -ne $expectedFiles.Count -or
        $actualDirectories.Count -ne $expectedDirectories.Count) {
        throw 'External Desktop snapshot path set is incomplete.'
    }
    $actualRecords = New-Object System.Collections.Generic.List[object]
    foreach ($file in @($expectedComponent.files)) {
        $relative = [string]$file.path
        $path = Get-NormalizedFullPath -Path (Join-Path $snapshot ($relative.Replace('/', '\')))
        Assert-NoReparsePathChain -Path $path -Role 'external snapshot file'
        $bytes = Read-BoundedFileBytes -Path $path -MaximumBytes $script:P0BMaxFileBytes
        Assert-NoReparsePathChain -Path $path -Role 'external snapshot file after read'
        $hash = Get-BytesSha256 -Bytes $bytes
        if ($hash -cne [string]$file.sha256 -or $bytes.LongLength -ne [long]$file.size_bytes) {
            throw 'External Desktop snapshot file bytes differ from the full release audit.'
        }
        $actualRecords.Add([pscustomobject][ordered]@{
            path = $relative
            sha256 = $hash
            size_bytes = $bytes.LongLength
        })
    }
    $actualComponent = [pscustomobject]@{ files = $actualRecords.ToArray() }
    $actualCanonical = Get-DesktopSnapshotCanonicalText -Component $actualComponent
    [string[]]$pathEntries = @(
        @($actualDirectories | ForEach-Object { "directory`t$_" }) +
        @($actualFiles | ForEach-Object { "file`t$_" })
    )
    [System.Array]::Sort($pathEntries, [System.StringComparer]::Ordinal)
    $pathSetCanonical = "feishu-codex-desktop-snapshot-path-set-v1`n" + ($pathEntries -join "`n") + "`n"
    return [pscustomobject]@{
        ManifestSha256 = Get-StringSha256 -Text $actualCanonical
        PathSetSha256 = Get-StringSha256 -Text $pathSetCanonical
        FileCount = $actualRecords.Count
    }
}

function Open-PinnedDesktopSnapshot {
    param(
        [Parameter(Mandatory = $true)][string]$SnapshotRoot,
        [Parameter(Mandatory = $true)][object]$ReleaseAudit
    )
    $snapshot = Resolve-ExistingPath -Path $SnapshotRoot -Role 'external Desktop snapshot to pin'
    $component = Get-ReleaseDesktopComponent -ReleaseAudit $ReleaseAudit
    $streams = New-Object 'System.Collections.Generic.List[System.IO.FileStream]'
    $pinnedFiles = New-Object System.Collections.Generic.List[object]
    try {
        foreach ($file in @($component.files)) {
            $relative = [string]$file.path
            if (-not (Test-SafeRelativePath -Path $relative)) {
                throw 'Release audit contains an unsafe pinned snapshot path.'
            }
            $path = Get-NormalizedFullPath -Path (Join-Path $snapshot ($relative.Replace('/', '\')))
            if (-not (Test-IsWithinRoot -Root $snapshot -Candidate $path)) {
                throw 'Pinned snapshot file escaped its root.'
            }
            Assert-NoReparsePathChain -Path $path -Role 'pinned external snapshot file'
            $stream = New-Object System.IO.FileStream(
                $path,
                [System.IO.FileMode]::Open,
                [System.IO.FileAccess]::Read,
                [System.IO.FileShare]::Read
            )
            $streams.Add($stream)
            $hash = Get-StreamSha256 -Stream $stream
            if ($hash -cne [string]$file.sha256 -or
                $stream.Length -ne [long]$file.size_bytes) {
                throw 'Pinned external snapshot file differs from its audited record.'
            }
            $pinnedFiles.Add([pscustomobject][ordered]@{
                path = $relative
                sha256 = $hash
                size_bytes = $stream.Length
            })
        }
        $actualComponent = [pscustomobject]@{ files = $pinnedFiles.ToArray() }
        return [pscustomobject]@{
            Handles = $streams.ToArray()
            ManifestSha256 = Get-StringSha256 -Text `
                (Get-DesktopSnapshotCanonicalText -Component $actualComponent)
            FileCount = $streams.Count
        }
    } catch {
        foreach ($stream in $streams) { $stream.Dispose() }
        throw
    }
}

function Open-PinnedReleaseSources {
    param(
        [Parameter(Mandatory = $true)][string]$DesktopRoot,
        [Parameter(Mandatory = $true)][string]$HarnessRoot,
        [Parameter(Mandatory = $true)][object]$ReleaseAudit
    )
    $roots = @{ desktop_bridge = $DesktopRoot; harness_sibling = $HarnessRoot }
    $streams = New-Object 'System.Collections.Generic.List[System.IO.FileStream]'
    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add('feishu-codex-source-manifest-v1')
    try {
        foreach ($component in @($ReleaseAudit.components)) {
            $componentName = [string]$component.name
            if (-not $roots.ContainsKey($componentName)) {
                throw 'Release audit contains an unknown component while pinning source.'
            }
            foreach ($file in @($component.files)) {
                $relative = [string]$file.path
                if (-not (Test-SafeRelativePath -Path $relative)) {
                    throw 'Release audit contains an unsafe source path while pinning.'
                }
                $path = Get-NormalizedFullPath -Path `
                    (Join-Path $roots[$componentName] ($relative.Replace('/', '\')))
                if (-not (Test-IsWithinRoot -Root $roots[$componentName] -Candidate $path)) {
                    throw 'Pinned release source escaped its component root.'
                }
                Assert-NoReparsePathChain -Path $path -Role 'pinned release source file'
                $stream = New-Object System.IO.FileStream(
                    $path,
                    [System.IO.FileMode]::Open,
                    [System.IO.FileAccess]::Read,
                    [System.IO.FileShare]::Read
                )
                $streams.Add($stream)
                $hash = Get-StreamSha256 -Stream $stream
                if ($hash -cne [string]$file.sha256 -or
                    $stream.Length -ne [long]$file.size_bytes) {
                    throw 'Pinned release source differs from the full release audit.'
                }
                $lines.Add(("{0}`t{1}`t{2}" -f $componentName, $relative, $hash))
            }
        }
        $canonical = ($lines -join "`n") + "`n"
        $manifestHash = Get-StringSha256 -Text $canonical
        if ($manifestHash -cne [string]$ReleaseAudit.source_manifest_sha256) {
            throw 'Pinned release source manifest differs from the full P0-A manifest.'
        }
        return [pscustomobject]@{
            Handles = $streams.ToArray()
            ManifestSha256 = $manifestHash
            FileCount = $streams.Count
        }
    } catch {
        foreach ($stream in $streams) { $stream.Dispose() }
        throw
    }
}

function Copy-AuditedDesktopSnapshot {
    param(
        [Parameter(Mandatory = $true)][string]$Desktop,
        [Parameter(Mandatory = $true)][object]$ReleaseAudit,
        [Parameter(Mandatory = $true)][string]$Destination
    )
    New-Item -ItemType Directory -Path $Destination -ErrorAction Stop | Out-Null
    Assert-NoReparsePathChain -Path $Destination -Role 'external source snapshot'
    $component = Get-ReleaseDesktopComponent -ReleaseAudit $ReleaseAudit
    $copiedFiles = New-Object System.Collections.Generic.List[object]
    foreach ($file in @($component.files)) {
        $relative = [string]$file.path
        if (-not (Test-SafeRelativePath -Path $relative) -or
            [string]$file.sha256 -cnotmatch '^[a-f0-9]{64}$' -or
            [long]$file.size_bytes -lt 0 -or
            [long]$file.size_bytes -gt $script:P0BMaxFileBytes) {
            throw 'Release audit contains an unsafe Desktop snapshot record.'
        }
        $source = Get-NormalizedFullPath -Path (Join-Path $Desktop ($relative.Replace('/', '\')))
        if (-not (Test-IsWithinRoot -Root $Desktop -Candidate $source)) {
            throw 'Audited Desktop source file is missing or outside its root.'
        }
        Assert-NoReparsePathChain -Path $source -Role 'audited Desktop source file'
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
            throw 'Audited Desktop source file has the wrong path type.'
        }
        $bytes = Read-BoundedFileBytes -Path $source -MaximumBytes $script:P0BMaxFileBytes
        Assert-NoReparsePathChain -Path $source -Role 'audited Desktop source file after read'
        if ((Get-BytesSha256 -Bytes $bytes) -cne [string]$file.sha256 -or
            $bytes.LongLength -ne [long]$file.size_bytes) {
            throw 'Desktop source changed while its external snapshot was being created.'
        }
        $destinationPath = Get-NormalizedFullPath -Path (Join-Path $Destination ($relative.Replace('/', '\')))
        if (-not (Test-IsWithinRoot -Root $Destination -Candidate $destinationPath)) {
            throw 'Audited Desktop destination escaped the external snapshot root.'
        }
        $destinationParent = Split-Path -Parent $destinationPath
        if (-not (Test-NoReparsePathChain -Path $destinationParent -Role 'external source snapshot parent')) {
            New-Item -ItemType Directory -Path $destinationParent -Force -ErrorAction Stop | Out-Null
        }
        Assert-NoReparsePathChain -Path $destinationParent -Role 'external source snapshot parent'
        Write-NewBytesFile -Path $destinationPath -Bytes $bytes
        Assert-NoReparsePathChain -Path $destinationPath -Role 'external source snapshot file'
        $destinationBytes = Read-BoundedFileBytes -Path $destinationPath -MaximumBytes $script:P0BMaxFileBytes
        Assert-NoReparsePathChain -Path $destinationPath -Role 'external source snapshot file after read'
        $destinationHash = Get-BytesSha256 -Bytes $destinationBytes
        if ($destinationHash -cne [string]$file.sha256 -or
            $destinationBytes.LongLength -ne [long]$file.size_bytes) {
            throw 'External Desktop snapshot target bytes differ from the audited source.'
        }
        $copiedFiles.Add([pscustomobject][ordered]@{
            path = $relative
            sha256 = $destinationHash
            size_bytes = $destinationBytes.LongLength
        })
    }
    $copiedComponent = [pscustomobject]@{ files = $copiedFiles.ToArray() }
    $expectedCanonical = Get-DesktopSnapshotCanonicalText -Component $component
    $actualCanonical = Get-DesktopSnapshotCanonicalText -Component $copiedComponent
    if ($actualCanonical -cne $expectedCanonical) {
        throw 'External Desktop snapshot target manifest differs from the audited Desktop component.'
    }
    return [pscustomobject]@{
        Root = $Destination
        CopyManifestSha256 = Get-StringSha256 -Text $actualCanonical
        FileCount = @($component.files).Count
    }
}

function Get-AuditEvidenceSha256 {
    param([Parameter(Mandatory = $true)][object]$Audit)
    return Get-StringSha256 -Text ($Audit | Microsoft.PowerShell.Utility\ConvertTo-Json -Depth 20 -Compress)
}

$desktop = Resolve-ExistingPath -Path $DesktopRoot -Role 'Desktop source root'
$harness = Resolve-ExistingPath -Path $HarnessRoot -Role 'Harness source root'
$project = Resolve-ExistingPath -Path $ProjectRoot -Role 'Desktop project root'
$expectedSupervisorPath = Get-NormalizedFullPath -Path `
    (Join-Path $desktop 'scripts\run-external-p0b.ps1')
$supervisorPath = Resolve-ExistingPath -Path $PSCommandPath -Role 'executing P0-B supervisor' -Leaf
if (-not $supervisorPath.Equals($expectedSupervisorPath, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'P0-B must execute the exact supervisor inside the audited Desktop source root.'
}
$supervisorStream = New-Object System.IO.FileStream(
    $supervisorPath,
    [System.IO.FileMode]::Open,
    [System.IO.FileAccess]::Read,
    [System.IO.FileShare]::Read
)
$supervisorHash = Get-StreamSha256 -Stream $supervisorStream
$python = Resolve-ExistingPath -Path $PythonExecutable -Role 'Python executable' -Leaf
if (-not ([System.IO.Path]::GetFileName($python)).Equals('python.exe', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'PythonExecutable must name python.exe so the recorded interpreter is unambiguous.'
}
$pythonStream = New-Object System.IO.FileStream(
    $python,
    [System.IO.FileMode]::Open,
    [System.IO.FileAccess]::Read,
    [System.IO.FileShare]::Read
)
$pythonHash = Get-StreamSha256 -Stream $pythonStream
$runtime = Get-NormalizedFullPath -Path (Join-Path $project '.codex\feishu-bridge')
$externalWork = Assert-ExternalDirectory -Directory $ExternalWorkRoot -Role 'External work root' `
    -ForbiddenRoots @($desktop, $harness, $project, $runtime)
$evidenceRoot = Assert-ExternalDirectory -Directory $EvidenceDirectory -Role 'Evidence directory' `
    -ForbiddenRoots @($desktop, $harness, $project, $runtime, $externalWork)
$shell = Get-CurrentShellExecutable
Assert-NoReparsePathChain -Path $shell -Role 'PowerShell executable'
$testJsonCommand = Microsoft.PowerShell.Core\Get-Command `
    'Microsoft.PowerShell.Utility\Test-Json' -ErrorAction SilentlyContinue
if ($null -eq $testJsonCommand -or -not $testJsonCommand.Parameters.ContainsKey('SchemaFile')) {
    throw 'P0-B requires a PowerShell Test-Json implementation with -SchemaFile support.'
}
$processGuard = Get-ExternalRunnerProcessGuard
$pythonSignature = Get-TrustedPythonSignature -Path $python

$currentUserSid = Get-CurrentWindowsUserSid
$lifecycleMutexName = Get-LifecycleMutexName -Project $project -UserSid $currentUserSid
$lifecycleMutex = [System.Threading.Mutex]::new($false, $lifecycleMutexName)
$lifecycleMutexOwned = $false
$installedHookStream = $null
$installedStopHookStream = $null
$hooksConfigStream = $null
$pinnedSnapshot = $null
$pinnedRelease = $null
$receiptStream = $null
try {
    try {
        $lifecycleMutexOwned = $lifecycleMutex.WaitOne(0)
    } catch [System.Threading.AbandonedMutexException] {
        $lifecycleMutexOwned = $true
    }
    if (-not $lifecycleMutexOwned) {
        throw 'Bridge lifecycle mutex is already held; P0-B requires an uninterrupted stopped window.'
    }

$receiptId = [guid]::NewGuid().ToString('D').ToLowerInvariant()
$workDirectory = Join-Path $externalWork ("p0b-work-{0}" -f $receiptId)
New-Item -ItemType Directory -Path $workDirectory -ErrorAction Stop | Out-Null
Assert-NoReparsePathChain -Path $workDirectory -Role 'external P0-B work directory'
$schemaProbePath = Join-Path $workDirectory 'draft-2020-12-capability.schema.json'
$schemaProbe = '{"$schema":"https://json-schema.org/draft/2020-12/schema","type":"array","minItems":2,"maxItems":2,"prefixItems":[{"$ref":"#/$defs/desktop"},{"$ref":"#/$defs/harness"}],"items":false,"$defs":{"desktop":{"const":"desktop_bridge"},"harness":{"const":"harness_sibling"}}}'
Write-NewUtf8File -Path $schemaProbePath -Text ($schemaProbe + "`n")
$schemaProbePass = '["desktop_bridge","harness_sibling"]' | Microsoft.PowerShell.Utility\Test-Json `
    -SchemaFile $schemaProbePath -ErrorAction Stop
$schemaProbeReject = '["harness_sibling","desktop_bridge"]' | Microsoft.PowerShell.Utility\Test-Json `
    -SchemaFile $schemaProbePath -ErrorAction SilentlyContinue
if (-not $schemaProbePass -or $schemaProbeReject) {
    throw 'PowerShell Test-Json did not enforce the required draft-2020-12 $defs/const/prefixItems capability.'
}

$auditScript = Resolve-ExistingPath -Path (Join-Path $desktop 'scripts\audit-feishu-codex-release.ps1') `
    -Role 'source release audit' -Leaf
$auditJson = @(& $auditScript -DesktopRoot $desktop -HarnessRoot $harness) -join "`n"
$releaseAudit = $auditJson | Microsoft.PowerShell.Utility\ConvertFrom-Json
if ($releaseAudit.status -ne 'pass' -or $releaseAudit.components.Count -ne 2) {
    throw 'Full source release audit did not pass.'
}
$beforeAuditSha256 = Get-AuditEvidenceSha256 -Audit $releaseAudit
$pinnedRelease = Open-PinnedReleaseSources -DesktopRoot $desktop -HarnessRoot $harness `
    -ReleaseAudit $releaseAudit
$releaseFileCount = 0
foreach ($component in @($releaseAudit.components)) { $releaseFileCount += @($component.files).Count }
if ($pinnedRelease.ManifestSha256 -cne [string]$releaseAudit.source_manifest_sha256 -or
    $pinnedRelease.FileCount -ne $releaseFileCount) {
    throw 'Pinned release source handles do not match the complete P0-A result.'
}
$sourceSnapshot = Copy-AuditedDesktopSnapshot -Desktop $desktop -ReleaseAudit $releaseAudit `
    -Destination (Join-Path $workDirectory 'source-snapshot')
$beforeSnapshot = Get-DesktopSnapshotState -SnapshotRoot $sourceSnapshot.Root -ReleaseAudit $releaseAudit
if ($beforeSnapshot.ManifestSha256 -cne $sourceSnapshot.CopyManifestSha256 -or
    $beforeSnapshot.FileCount -ne $sourceSnapshot.FileCount) {
    throw 'External Desktop snapshot changed between copy and its pre-test inventory audit.'
}
$pinnedSnapshot = Open-PinnedDesktopSnapshot -SnapshotRoot $sourceSnapshot.Root `
    -ReleaseAudit $releaseAudit
if ($pinnedSnapshot.ManifestSha256 -cne $beforeSnapshot.ManifestSha256 -or
    $pinnedSnapshot.FileCount -ne $beforeSnapshot.FileCount) {
    throw 'Pinned Desktop snapshot handles do not match the pre-test audit.'
}
$dispatcher = Resolve-ExistingPath -Path (Join-Path $sourceSnapshot.Root 'scripts\feishu-codex-bridge.ps1') `
    -Role 'audited snapshot bridge dispatcher' -Leaf
$desktopComponent = Get-ReleaseDesktopComponent -ReleaseAudit $releaseAudit
$supervisorRecords = @($desktopComponent.files | Where-Object {
    [string]$_.path -ceq 'scripts/run-external-p0b.ps1'
})
if ($supervisorRecords.Count -ne 1 -or
    $supervisorHash -cne [string]$supervisorRecords[0].sha256 -or
    $supervisorStream.Length -ne [long]$supervisorRecords[0].size_bytes) {
    throw 'Executing P0-B supervisor does not match its full release-audit record.'
}
$sourceStartHookRecords = @($desktopComponent.files | Where-Object {
    [string]$_.path -ceq 'scripts/start-feishu-codex-bridge.ps1'
})
$sourceStopHookRecords = @($desktopComponent.files | Where-Object {
    [string]$_.path -ceq 'scripts/stop-feishu-codex-bridge.ps1'
})
if ($sourceStartHookRecords.Count -ne 1 -or $sourceStopHookRecords.Count -ne 1) {
    throw 'Release audit does not bind exactly one start and stop lifecycle hook.'
}
$sourceStartHookHash = [string]$sourceStartHookRecords[0].sha256
$sourceStopHookHash = [string]$sourceStopHookRecords[0].sha256
$installedStartHook = Resolve-ExistingPath -Path (Join-Path $project '.codex\hooks\start-feishu-codex-bridge.ps1') `
    -Role 'installed start hook' -Leaf
$installedStopHook = Resolve-ExistingPath -Path (Join-Path $project '.codex\hooks\stop-feishu-codex-bridge.ps1') `
    -Role 'installed stop hook' -Leaf
$installedHooksConfig = Resolve-ExistingPath -Path (Join-Path $project '.codex\hooks.json') `
    -Role 'installed hooks config' -Leaf
$installedHookStream = New-Object System.IO.FileStream(
    $installedStartHook,
    [System.IO.FileMode]::Open,
    [System.IO.FileAccess]::Read,
    [System.IO.FileShare]::Read
)
$installedStopHookStream = New-Object System.IO.FileStream(
    $installedStopHook,
    [System.IO.FileMode]::Open,
    [System.IO.FileAccess]::Read,
    [System.IO.FileShare]::Read
)
$hooksConfigStream = New-Object System.IO.FileStream(
    $installedHooksConfig,
    [System.IO.FileMode]::Open,
    [System.IO.FileAccess]::Read,
    [System.IO.FileShare]::Read
)
$installedStartHookHash = Get-StreamSha256 -Stream $installedHookStream
$installedStopHookHash = Get-StreamSha256 -Stream $installedStopHookStream
if ($installedStartHookHash -cne $sourceStartHookHash -or
    $installedHookStream.Length -ne [long]$sourceStartHookRecords[0].size_bytes -or
    $installedStopHookHash -cne $sourceStopHookHash -or
    $installedStopHookStream.Length -ne [long]$sourceStopHookRecords[0].size_bytes) {
    throw 'Installed lifecycle hooks do not match the audited mutex-aware source.'
}
$installedHooksConfigHash = Assert-InstalledHookRegistration -StartHookPath $installedStartHook `
    -StopHookPath $installedStopHook -ConfigStream $hooksConfigStream

$pythonIdentityCode = 'import json,sys;print(json.dumps({"implementation":sys.implementation.name,"version":sys.version,"executable":sys.executable,"isolated":sys.flags.isolated,"ignore_environment":sys.flags.ignore_environment,"no_user_site":sys.flags.no_user_site,"no_site":sys.flags.no_site},separators=(",",":")))'
$pythonIdentityCapture = Invoke-CapturedProcess -Executable $python `
    -Arguments @('-I', '-S', '-B', '-c', $pythonIdentityCode) `
    -WorkingDirectory $workDirectory -EnvironmentOverrides @{} `
    -CapturePrefix (Join-Path $workDirectory 'python-identity')
if ($pythonIdentityCapture.ExitCode -ne 0 -or -not [string]::IsNullOrWhiteSpace($pythonIdentityCapture.Stderr)) {
    throw 'Isolated Python identity check failed.'
}
$pythonIdentity = $pythonIdentityCapture.Stdout.Trim() | Microsoft.PowerShell.Utility\ConvertFrom-Json
$identityExecutable = Get-NormalizedFullPath -Path ([string]$pythonIdentity.executable)
if ([string]$pythonIdentity.implementation -cne 'cpython' -or
    -not $identityExecutable.Equals($python, [System.StringComparison]::OrdinalIgnoreCase) -or
    [int]$pythonIdentity.isolated -ne 1 -or
    [int]$pythonIdentity.ignore_environment -ne 1 -or
    [int]$pythonIdentity.no_user_site -ne 1 -or
    [int]$pythonIdentity.no_site -ne 1 -or
    [string]$pythonIdentity.version -notmatch '^(\d+)\.(\d+)(?:\.([0-9]+))?') {
    throw 'Python identity, executable, or isolation flags were not the required CPython values.'
}
if ([version]("{0}.{1}" -f $Matches[1], $Matches[2]) -lt [version]'3.10') {
    throw 'P0-B requires Python 3.10+.'
}
$pythonVersionText = [string]$pythonIdentity.version

$preObservation = Get-ListenerObservation -Label 'pre' -ShellExecutable $shell `
    -Dispatcher $dispatcher -Project $project -Runtime $runtime -WorkDirectory $workDirectory
$beforeRuntime = Get-BoundedRuntimeSnapshot -Project $project -Runtime $runtime

$routerTest = 'test_desktop_router.DesktopRouterQueueTests.'
$runtimeMarkerTest = 'test_runtime.PendingProjectMarkerTests.'
$envEntrypointTest = 'test_agents_rules.BridgeEnvEntrypointTests.'
$faultTests = [ordered]@{
    F01 = @(
        "${routerTest}test_exclusive_claim_publication_keeps_canonical_pending"
        "${routerTest}test_identical_producer_overlap_cannot_republish_claimed_request"
    )
    F02 = @("${routerTest}test_legacy_unfenced_claim_is_terminalized_as_uncertain")
    F03 = @(
        "${routerTest}test_receipt_payload_without_marker_is_authoritative_and_not_replayed"
        "${routerTest}test_receipt_payload_survives_marker_descriptor_close_failure"
    )
    F04 = @("${routerTest}test_orphan_terminal_receipt_recovers_as_unknown_and_survives_cleanup")
    F05 = @("${routerTest}test_concurrent_terminal_finalizers_preserve_first_receipt")
    F06 = @("${routerTest}test_wake_database_lock_preserves_pending_and_reconciles_once")
    F07 = @("${routerTest}test_concurrent_conflicting_producers_publish_one_fingerprint")
    F08 = @(
        "${routerTest}test_explicit_safe_failure_advances_one_retry_generation"
        "${routerTest}test_retry_generation_ancestry_survives_response_cleanup"
        "${routerTest}test_stale_read_only_claim_advances_retry_generation"
        "${routerTest}test_mutating_claim_keeps_long_ttl_when_read_claim_would_expire"
    )
    F09 = @(
        "${routerTest}test_target_lifecycle_failure_never_advances_retry_generation"
        "${routerTest}test_retry_generation_requires_explicit_json_booleans"
    )
    F10 = @(
        "${runtimeMarkerTest}test_fresh_project_marker_precedes_unknown_create_and_same_event_recovers"
        "${runtimeMarkerTest}test_same_event_resumes_exact_pending_project_marker"
    )
    F11 = @("${runtimeMarkerTest}test_different_event_cannot_overwrite_a_pending_project_marker")
    F12 = @("${envEntrypointTest}test_malformed_bridge_env_fails_closed_at_dispatcher_start_and_queue_entrypoints")
}
$requiredFaultTestIds = @(
    foreach ($entry in $faultTests.GetEnumerator()) {
        foreach ($testId in @($entry.Value)) { [string]$testId }
    }
)
$requiredFaultTestSet = New-Object 'System.Collections.Generic.HashSet[string]' `
    ([System.StringComparer]::Ordinal)
foreach ($testId in $requiredFaultTestIds) {
    if (-not $requiredFaultTestSet.Add($testId)) {
        throw 'P0-B supervisor fault contract contains duplicate test IDs.'
    }
}
if ($faultTests.Count -ne 12 -or $requiredFaultTestIds.Count -ne 19) {
    throw 'P0-B supervisor fault contract must define exactly F01-F12 and 19 unique tests.'
}
$structuredResultNonce = [guid]::NewGuid().ToString('D').ToLowerInvariant()
$structuredResultPath = Join-Path $workDirectory 'structured-test-result.json'
$testDriver = Resolve-ExistingPath -Path `
    (Join-Path $sourceSnapshot.Root 'scripts\external_p0b_test_runner.py') `
    -Role 'audited structured P0-B test driver' -Leaf
$testArguments = @(
    '-I',
    '-S',
    '-B',
    $testDriver,
    '--tests-dir',
    (Join-Path $sourceSnapshot.Root 'tests'),
    '--result-path',
    $structuredResultPath,
    '--nonce',
    $structuredResultNonce
)
$childPath = (Split-Path -Parent $python) + [System.IO.Path]::PathSeparator +
    (Split-Path -Parent $shell) + [System.IO.Path]::PathSeparator +
    [System.Environment]::SystemDirectory
$testEnvironment = @{
    FEISHU_BRIDGE_EXTERNAL_TEST_RUNNER = '1'
    PYTHONDONTWRITEBYTECODE = '1'
    FEISHU_BRIDGE_TEST_TMP = $workDirectory
    Path = $childPath
}
$testCapture = $null
$postObservation = $null
$afterRuntime = $null
$postReleaseAudit = $null
$afterSnapshot = $null
try {
    $testCapture = Invoke-CapturedProcess -Executable $python -Arguments $testArguments `
        -WorkingDirectory $sourceSnapshot.Root -EnvironmentOverrides $testEnvironment `
        -CapturePrefix (Join-Path $workDirectory 'dynamic-tests') -TimeoutSeconds 1800
} finally {
    $postObservation = Get-ListenerObservation -Label 'post' -ShellExecutable $shell `
        -Dispatcher $dispatcher -Project $project -Runtime $runtime -WorkDirectory $workDirectory
    $afterRuntime = Get-BoundedRuntimeSnapshot -Project $project -Runtime $runtime
    $postAuditJson = @(& $auditScript -DesktopRoot $desktop -HarnessRoot $harness) -join "`n"
    $postReleaseAudit = $postAuditJson | Microsoft.PowerShell.Utility\ConvertFrom-Json
    $afterSnapshot = Get-DesktopSnapshotState -SnapshotRoot $sourceSnapshot.Root -ReleaseAudit $releaseAudit
}
if ($null -eq $testCapture -or $testCapture.ExitCode -ne 0) {
    $diagnosticLines = New-Object 'System.Collections.Generic.List[string]'
    if (Test-Path -LiteralPath $structuredResultPath -PathType Leaf) {
        try {
            $diagnosticBytes = Read-BoundedFileBytes -Path $structuredResultPath `
                -MaximumBytes $script:P0BMaxFileBytes
            Assert-NoReparsePathChain -Path $structuredResultPath `
                -Role 'failed structured P0-B unittest result'
            $diagnosticJson = $script:StrictUtf8.GetString($diagnosticBytes) |
                Microsoft.PowerShell.Utility\ConvertFrom-Json
            $diagnosticIds = @(
                @($diagnosticJson.failure_test_ids) +
                @($diagnosticJson.error_test_ids) +
                @($diagnosticJson.missing_required_test_ids) |
                    ForEach-Object { [string]$_ } |
                    Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
                    Select-Object -Unique -First 24
            )
            if ($diagnosticIds.Count) {
                $diagnosticLines.Add(
                    'Failing/error P0-B test IDs: ' + ($diagnosticIds -join ', ')
                )
            }
        } catch {
            $diagnosticLines.Add('Structured failure summary was unavailable or invalid.')
        }
    }
    if (-not $diagnosticLines.Count -and
        $null -ne $testCapture -and
        -not [string]::IsNullOrWhiteSpace([string]$testCapture.Stderr)) {
        $stderrTail = @(
            ([string]$testCapture.Stderr -split "`r?`n") |
                Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
                Select-Object -Last 20
        ) -join [Environment]::NewLine
        if ($stderrTail.Length -gt 4000) {
            $stderrTail = $stderrTail.Substring($stderrTail.Length - 4000)
        }
        $diagnosticLines.Add($stderrTail)
    }
    $diagnosticSuffix = if ($diagnosticLines.Count) {
        [Environment]::NewLine + ($diagnosticLines -join [Environment]::NewLine)
    } else {
        ''
    }
    throw (
        'External dynamic test command failed; retained work files were not ' +
        'published as passing evidence.' + $diagnosticSuffix
    )
}
$structuredResult = Resolve-ExistingPath -Path $structuredResultPath `
    -Role 'structured P0-B unittest result' -Leaf
$structuredResultBytes = Read-BoundedFileBytes -Path $structuredResult `
    -MaximumBytes $script:P0BMaxFileBytes
Assert-NoReparsePathChain -Path $structuredResult -Role 'structured P0-B unittest result after read'
$structuredResultSha256 = Get-BytesSha256 -Bytes $structuredResultBytes
try {
    $structuredResultJson = $script:StrictUtf8.GetString($structuredResultBytes)
    $structuredTestResult = $structuredResultJson | Microsoft.PowerShell.Utility\ConvertFrom-Json
} catch {
    throw 'Structured P0-B unittest result is not strict UTF-8 JSON.'
}
$expectedStructuredProperties = @(
    'error_test_ids',
    'failure_test_ids',
    'missing_required_test_ids',
    'nonce',
    'required_fault_test_ids',
    'runner_status',
    'schema_version',
    'skipped_test_ids',
    'successful_test_ids',
    'tests_discovered',
    'tests_run'
)
$actualStructuredProperties = @($structuredTestResult.PSObject.Properties.Name)
[System.Array]::Sort($actualStructuredProperties, [System.StringComparer]::Ordinal)
if (($actualStructuredProperties -join "`n") -cne ($expectedStructuredProperties -join "`n") -or
    [int]$structuredTestResult.schema_version -ne 1 -or
    [string]$structuredTestResult.nonce -cne $structuredResultNonce -or
    [string]$structuredTestResult.runner_status -cne 'pass' -or
    [int]$structuredTestResult.tests_run -ne [int]$structuredTestResult.tests_discovered -or
    @($structuredTestResult.error_test_ids).Count -ne 0 -or
    @($structuredTestResult.failure_test_ids).Count -ne 0 -or
    @($structuredTestResult.missing_required_test_ids).Count -ne 0 -or
    (@($structuredTestResult.required_fault_test_ids) -join "`n") -cne
        ($requiredFaultTestIds -join "`n")) {
    throw 'Structured P0-B unittest result failed its exact semantic contract.'
}
$successfulTestIds = @($structuredTestResult.successful_test_ids | ForEach-Object { [string]$_ })
$successfulTestSet = New-Object 'System.Collections.Generic.HashSet[string]' `
    ([System.StringComparer]::Ordinal)
foreach ($testId in $successfulTestIds) {
    if (-not $successfulTestSet.Add($testId)) {
        throw 'Structured P0-B unittest result contains duplicate successful test IDs.'
    }
}
foreach ($testId in $requiredFaultTestIds) {
    if (-not $successfulTestSet.Contains($testId)) {
        throw 'Structured P0-B unittest result is missing a required successful test ID.'
    }
}
$testsRun = [int]$structuredTestResult.tests_run
$skipped = @($structuredTestResult.skipped_test_ids).Count
if ($successfulTestIds.Count + $skipped -ne $testsRun) {
    throw 'Structured P0-B unittest result counts are inconsistent.'
}
if ($beforeRuntime.ManifestSha256 -cne $afterRuntime.ManifestSha256 -or
    $beforeRuntime.PathCount -ne $afterRuntime.PathCount) {
    throw 'Live runtime/control files changed during P0-B; no passing receipt was published.'
}
$afterAuditSha256 = Get-AuditEvidenceSha256 -Audit $postReleaseAudit
if ($postReleaseAudit.status -ne 'pass' -or $beforeAuditSha256 -cne $afterAuditSha256) {
    throw 'Audited source changed during P0-B; no passing receipt was published.'
}
if ($null -eq $afterSnapshot -or
    $beforeSnapshot.ManifestSha256 -cne $afterSnapshot.ManifestSha256 -or
    $beforeSnapshot.FileCount -ne $afterSnapshot.FileCount -or
    $beforeSnapshot.PathSetSha256 -cne $afterSnapshot.PathSetSha256) {
    throw 'External Desktop snapshot changed during P0-B; no passing receipt was published.'
}

$faultResults = New-Object System.Collections.Generic.List[object]
foreach ($entry in $faultTests.GetEnumerator()) {
    $faultCanonical = ([string]$entry.Key) + "`n" + (@($entry.Value) -join "`n") + "`n" +
        $structuredResultSha256 + "`n"
    $faultResults.Add([pscustomobject][ordered]@{
        fault_id = [string]$entry.Key
        test_id = @($entry.Value) -join ';'
        status = 'pass'
        evidence_sha256 = Get-StringSha256 -Text $faultCanonical
    })
}

$requiredFaultTestCount = $requiredFaultTestIds.Count
if ($requiredFaultTestCount -ne 19 -or $testsRun -lt $requiredFaultTestCount) {
    throw 'External unittest count is smaller than the exact 19-test fault contract.'
}

$validationSnapshot = Get-DesktopSnapshotState -SnapshotRoot $sourceSnapshot.Root -ReleaseAudit $releaseAudit
if ($validationSnapshot.ManifestSha256 -cne $afterSnapshot.ManifestSha256 -or
    $validationSnapshot.FileCount -ne $afterSnapshot.FileCount -or
    $validationSnapshot.PathSetSha256 -cne $afterSnapshot.PathSetSha256) {
    throw 'External Desktop snapshot changed before final evidence assembly.'
}
$finalObservation = Get-ListenerObservation -Label 'final' -ShellExecutable $shell `
    -Dispatcher $dispatcher -Project $project -Runtime $runtime -WorkDirectory $workDirectory
$finalRuntime = Get-BoundedRuntimeSnapshot -Project $project -Runtime $runtime
if ($beforeRuntime.ManifestSha256 -cne $finalRuntime.ManifestSha256 -or
    $beforeRuntime.PathCount -ne $finalRuntime.PathCount) {
    throw 'Live runtime/control files changed before final evidence assembly.'
}

$receipt = [pscustomobject][ordered]@{
    schema_version = 1
    evidence_kind = 'feishu-codex-bridge.p0b.external-test'
    receipt_id = $receiptId
    created_at_utc = Get-UtcTimestamp
    release_audit = $releaseAudit
    source_guard = [pscustomobject][ordered]@{
        before_audit_sha256 = $beforeAuditSha256
        after_audit_sha256 = $afterAuditSha256
        comparison = 'equal'
        before_snapshot_manifest_sha256 = $beforeSnapshot.ManifestSha256
        after_snapshot_manifest_sha256 = $afterSnapshot.ManifestSha256
        snapshot_comparison = 'equal'
        snapshot_file_count = $sourceSnapshot.FileCount
        pinned_snapshot_file_count = $pinnedSnapshot.FileCount
        snapshot_files_pinned_for_complete_window = $true
        pinned_release_file_count = $pinnedRelease.FileCount
        release_files_pinned_for_complete_window = $true
        tests_ran_from_snapshot = $true
    }
    runner = [pscustomobject][ordered]@{
        surface = $RunnerSurface
        codex_desktop_origin = $false
        os = [pscustomobject][ordered]@{
            name = [System.Environment]::OSVersion.Platform.ToString()
            version = [System.Environment]::OSVersion.VersionString
            architecture = $(if ([string]::IsNullOrWhiteSpace([string]$env:PROCESSOR_ARCHITECTURE)) {
                if ([System.Environment]::Is64BitOperatingSystem) { 'x64' } else { 'x86' }
            } else { [string]$env:PROCESSOR_ARCHITECTURE })
        }
        powershell = [pscustomobject][ordered]@{
            implementation = [string]$PSVersionTable.PSEdition
            version = [string]$PSVersionTable.PSVersion
            executable_sha256 = Get-FileSha256 -Path $shell
        }
        python = [pscustomobject][ordered]@{
            implementation = 'CPython'
            version = $pythonVersionText
            executable_sha256 = $pythonHash
        }
        guard = [pscustomobject][ordered]@{
            process_ancestry_complete = $processGuard.Complete
            process_ancestry_depth = $processGuard.Depth
            process_ancestry_termination = $processGuard.Termination
            codex_ancestor_match_count = $processGuard.CodexMatchCount
            external_origin_asserted = $true
            python_authenticode_status = $pythonSignature.Status
            python_signer_subject_sha256 = $pythonSignature.SignerSubjectSha256
            python_certificate_thumbprint_sha256 = $pythonSignature.CertificateThumbprintSha256
            python_isolated_mode = $true
            python_no_site = $true
            python_identity_verified = $true
            inherited_python_environment_removed = $true
            inherited_sensitive_environment_removed = $true
            child_path_restricted = $true
            clean_powershell_invocation = $true
            powershell_profile_loaded = $false
            supervisor_bound_to_release_audit = $true
            supervisor_sha256 = $supervisorHash
        }
    }
    listener_stopped_receipt = [pscustomobject][ordered]@{
        pre = $preObservation
        post = $postObservation
        final = $finalObservation
        lifecycle_mutex = [pscustomobject][ordered]@{
            name_sha256 = Get-StringSha256 -Text $lifecycleMutexName
            namespace = 'global_current_user'
            owner_sid_sha256 = Get-StringSha256 -Text $currentUserSid
            source_hook_sha256 = $sourceStartHookHash
            installed_hook_sha256 = $installedStartHookHash
            source_stop_hook_sha256 = $sourceStopHookHash
            installed_stop_hook_sha256 = $installedStopHookHash
            hooks_config_sha256 = $installedHooksConfigHash
            hook_comparison = 'equal'
            hook_registration_validated = $true
            lifecycle_exclusion_scope = 'registered_start_and_stop_hooks'
            held_for_complete_window = $true
        }
    }
    execution = [pscustomobject][ordered]@{
        argv = @($testCapture.Argv)
        working_directory = $sourceSnapshot.Root
        environment = [pscustomobject][ordered]@{
            FEISHU_BRIDGE_EXTERNAL_TEST_RUNNER = '1'
            PYTHONDONTWRITEBYTECODE = '1'
            FEISHU_BRIDGE_TEST_TMP = $workDirectory
        }
        test_tmp_outside_source = $true
        test_tmp_outside_live_runtime = $true
        started_at_utc = Get-UtcTimestamp -Value $testCapture.StartedAt
        finished_at_utc = Get-UtcTimestamp -Value $testCapture.FinishedAt
        exit_code = 0
        runner_status = 'pass'
        tests_run = $testsRun
        required_fault_test_count = $requiredFaultTestCount
        failures = 0
        errors = 0
        skipped = $skipped
        fault_results = $faultResults.ToArray()
        stdout_sha256 = $testCapture.StdoutSha256
        stderr_sha256 = $testCapture.StderrSha256
        structured_result_sha256 = $structuredResultSha256
        structured_result_nonce_sha256 = Get-StringSha256 -Text $structuredResultNonce
        structured_result_verified = $true
        test_result_source = 'unittest.TestResult'
        timeout_seconds = 1800
        timed_out = $false
        process_tree_timeout_mode = 'kill_entire_process_tree'
        process_job_object_mode = 'kill_on_close'
        process_job_object_enforced = $testCapture.JobObjectEnforced
        process_start_assignment_mode = 'start_then_immediate_assign'
        termination_wait_seconds = 30
        capture_close_wait_seconds = 30
    }
    runtime_guard = [pscustomobject][ordered]@{
        snapshot_scope = @($beforeRuntime.Scope)
        before_manifest_sha256 = $beforeRuntime.ManifestSha256
        after_manifest_sha256 = $afterRuntime.ManifestSha256
        final_manifest_sha256 = $finalRuntime.ManifestSha256
        before_path_count = $beforeRuntime.PathCount
        after_path_count = $afterRuntime.PathCount
        final_path_count = $finalRuntime.PathCount
        comparison = 'equal'
        bounded_control_files_unchanged = $true
    }
    immutability = [pscustomobject][ordered]@{
        write_mode = 'exclusive_create'
        destination_preexisted = $false
        receipt_outside_source = $true
        receipt_outside_live_runtime = $true
        overwrite_attempted = $false
    }
}

$receiptName = "p0b-v1-{0}.json" -f $receiptId
$receiptPath = Join-Path $evidenceRoot $receiptName
$pendingPath = $receiptPath + '.pending'
Assert-NoReparsePathChain -Path $evidenceRoot -Role 'external evidence directory'
if ((Test-Path -LiteralPath $receiptPath) -or (Test-Path -LiteralPath $pendingPath)) {
    throw 'Evidence destination or pending path already exists; overwrite is forbidden.'
}
$receiptJson = $receipt | Microsoft.PowerShell.Utility\ConvertTo-Json -Depth 20
$receiptBytes = $script:Utf8NoBom.GetBytes($receiptJson + "`n")
$expectedReceiptSha256 = Get-BytesSha256 -Bytes $receiptBytes
$schemaPath = Join-Path $sourceSnapshot.Root 'assets\external-test-evidence.schema.json'
if (-not ($receiptJson | Microsoft.PowerShell.Utility\Test-Json `
        -SchemaFile $schemaPath -ErrorAction Stop)) {
    throw 'P0-B receipt failed its audited JSON Schema; no evidence was published.'
}
$expectedFaultIds = @(1..12 | ForEach-Object { 'F{0:d2}' -f $_ })
if ($faultResults.Count -ne 12 -or
    ((@($faultResults | ForEach-Object { [string]$_.fault_id }) -join ',') -cne ($expectedFaultIds -join ','))) {
    throw 'P0-B receipt failed its exact F01-F12 semantic contract.'
}
$pendingOwned = $false
try {
    Write-NewUtf8File -Path $pendingPath -Text ($receiptJson + "`n")
    $pendingOwned = $true
    [System.IO.File]::Move($pendingPath, $receiptPath)
    $pendingOwned = $false
} finally {
    if ($pendingOwned -and (Test-Path -LiteralPath $pendingPath)) {
        Remove-Item -LiteralPath $pendingPath -Force
    }
}
$receiptStream = New-Object System.IO.FileStream(
    $receiptPath,
    [System.IO.FileMode]::Open,
    [System.IO.FileAccess]::Read,
    [System.IO.FileShare]::Read
)
$publishedReceiptSha256 = Get-StreamSha256 -Stream $receiptStream
if ($publishedReceiptSha256 -cne $expectedReceiptSha256 -or
    $receiptStream.Length -ne $receiptBytes.LongLength) {
    throw 'Published P0-B receipt bytes differ from the schema-validated create-new payload.'
}

[pscustomobject][ordered]@{
    evidence_file = $receiptName
    evidence_sha256 = $publishedReceiptSha256
    schema_version = 1
    runner_status = 'pass'
} | Microsoft.PowerShell.Utility\ConvertTo-Json -Compress | Write-Output
} finally {
    if ($null -ne $receiptStream) {
        $receiptStream.Dispose()
    }
    if ($null -ne $pinnedSnapshot) {
        foreach ($stream in @($pinnedSnapshot.Handles)) {
            $stream.Dispose()
        }
    }
    if ($null -ne $pinnedRelease) {
        foreach ($stream in @($pinnedRelease.Handles)) {
            $stream.Dispose()
        }
    }
    if ($null -ne $installedHookStream) {
        $installedHookStream.Dispose()
    }
    if ($null -ne $installedStopHookStream) {
        $installedStopHookStream.Dispose()
    }
    if ($null -ne $hooksConfigStream) {
        $hooksConfigStream.Dispose()
    }
    if ($null -ne $supervisorStream) {
        $supervisorStream.Dispose()
    }
    if ($null -ne $pythonStream) {
        $pythonStream.Dispose()
    }
    if ($lifecycleMutexOwned) {
        try { $lifecycleMutex.ReleaseMutex() } catch [System.ApplicationException] { }
    }
    $lifecycleMutex.Dispose()
}
