[CmdletBinding()]
param(
    [string]$DesktopRoot = (Split-Path -Parent $PSScriptRoot),
    [Parameter(Mandatory = $true)][string]$HarnessRoot,
    [Parameter(Mandatory = $true)][string]$ProjectRoot,
    [Parameter(Mandatory = $true)][string]$EvidencePath,
    [Parameter(Mandatory = $true)][ValidatePattern('^[a-f0-9]{64}$')][string]$ExpectedEvidenceSha256
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
    throw 'P0-B evidence validation requires a clean pwsh -File invocation.'
}
$fileArgumentIndex = [int]$fileArgumentIndexes[0]
if ($fileArgumentIndex -lt 1 -or $fileArgumentIndex + 1 -ge $nativeInvocation.Count) {
    throw 'P0-B evidence validator PowerShell invocation is incomplete.'
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
    throw 'P0-B evidence validation requires the exact script under clean pwsh flags.'
}
if ([string]$PSVersionTable.PSEdition -cne 'Core' -or
    [version]$PSVersionTable.PSVersion -lt [version]'7.4') {
    throw 'P0-B semantic evidence validation requires PowerShell 7.4+.'
}
if (-not [System.Runtime.InteropServices.RuntimeInformation]::IsOSPlatform(
        [System.Runtime.InteropServices.OSPlatform]::Windows
    )) {
    throw 'P0-B semantic evidence validation currently supports Windows only.'
}
$requiredPowerShellModules = @(
    [System.IO.Path]::Combine($PSHOME, 'Modules', 'Microsoft.PowerShell.Utility', 'Microsoft.PowerShell.Utility.psd1'),
    [System.IO.Path]::Combine($PSHOME, 'Modules', 'Microsoft.PowerShell.Security', 'Microsoft.PowerShell.Security.psd1'),
    [System.IO.Path]::Combine($PSHOME, 'Modules', 'Microsoft.PowerShell.Management', 'Microsoft.PowerShell.Management.psd1')
)
foreach ($modulePath in $requiredPowerShellModules) {
    if (-not [System.IO.File]::Exists($modulePath)) {
        throw 'P0-B evidence validator is missing a required built-in PowerShell module.'
    }
    Microsoft.PowerShell.Core\Import-Module -Name $modulePath -Force -ErrorAction Stop
}
$PSModuleAutoLoadingPreference = 'None'

$script:MaximumEvidenceBytes = 4194304L
$script:MaximumSourceFileBytes = 2097152L
$script:MaximumPythonBytes = 67108864L
$script:MaximumCaptureBytes = 16777216L
$script:StrictUtf8 = New-Object System.Text.UTF8Encoding($false, $true)

if ($null -eq ('FeishuCodexBridge.ExternalP0BPath' -as [type])) {
    Microsoft.PowerShell.Utility\Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
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
            StringBuilder devicePathBuffer,
            int maximumLength
        );

        public static void AssertOrdinaryLocalDrive(string path) {
            string root = System.IO.Path.GetPathRoot(path);
            if (String.IsNullOrEmpty(root) || root.Length < 2) {
                throw new ArgumentException("Path has no DOS drive root", "path");
            }
            var deviceBuffer = new StringBuilder(4096);
            uint length = QueryDosDeviceW(root.Substring(0, 2), deviceBuffer, deviceBuffer.Capacity);
            if (length == 0) {
                throw new Win32Exception(Marshal.GetLastWin32Error(), "QueryDosDeviceW failed");
            }
            string device = deviceBuffer.ToString();
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
            throw 'P0-B evidence validator bounded read limit was exceeded.'
        }
        $bytes = New-Object byte[] ([int]$stream.Length)
        $offset = 0
        while ($offset -lt $bytes.Length) {
            $count = $stream.Read($bytes, $offset, $bytes.Length - $offset)
            if ($count -le 0) { throw 'A validated file ended during its bounded read.' }
            $offset += $count
        }
        if ($stream.ReadByte() -ne -1) { throw 'A validated file grew during its bounded read.' }
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
        throw 'P0-B evidence validator pinned read limit was exceeded.'
    }
    $Stream.Position = 0
    $bytes = New-Object byte[] ([int]$Stream.Length)
    $offset = 0
    while ($offset -lt $bytes.Length) {
        $count = $Stream.Read($bytes, $offset, $bytes.Length - $offset)
        if ($count -le 0) { throw 'A pinned validation file ended during its read.' }
        $offset += $count
    }
    if ($Stream.ReadByte() -ne -1) { throw 'A pinned validation file grew during its read.' }
    $Stream.Position = 0
    return ,$bytes
}

function Get-NormalizedFullPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    $fullPath = [System.IO.Path]::GetFullPath($Path)
    $root = [System.IO.Path]::GetPathRoot($fullPath)
    if ($fullPath.StartsWith('\\', [System.StringComparison]::Ordinal) -or
        $root -notmatch '^[A-Za-z]:[\\/]$') {
        throw 'P0-B validation paths must use an unambiguous local DOS drive path; UNC and device namespaces are refused.'
    }
    [FeishuCodexBridge.ExternalP0BPath]::AssertOrdinaryLocalDrive($fullPath)
    if ($fullPath.Equals($root, [System.StringComparison]::OrdinalIgnoreCase)) { return $root }
    return $fullPath.TrimEnd('\', '/')
}

function Get-CanonicalComparisonPath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Role
    )
    $fullPath = Get-NormalizedFullPath -Path $Path
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
        $existing = Get-NormalizedFullPath -Path $parent
    }
    $canonical = Get-NormalizedFullPath -Path `
        ([FeishuCodexBridge.ExternalP0BPath]::ResolveExisting($existing))
    foreach ($segment in $suffix) { $canonical = Join-Path $canonical $segment }
    return Get-NormalizedFullPath -Path $canonical
}

function Assert-NoReparsePathChain {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Role
    )
    $fullPath = Get-NormalizedFullPath -Path $Path
    $root = [System.IO.Path]::GetPathRoot($fullPath)
    $current = $root
    $segments = @(
        $fullPath.Substring($root.Length).TrimStart('\', '/').Split(
            [char[]]@([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar),
            [System.StringSplitOptions]::RemoveEmptyEntries
        )
    )
    foreach ($segment in @('') + $segments) {
        if ($segment) { $current = Join-Path $current $segment }
        $item = Get-Item -LiteralPath $current -Force -ErrorAction SilentlyContinue
        if ($null -eq $item) { throw "$Role contains a missing path segment." }
        if ([string]$item.PSProvider.Name -ne 'FileSystem' -or
            ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "$Role must be a FileSystem path with no reparse point."
        }
    }
}

function Assert-NoReparseExistingPathPrefix {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Role
    )
    $fullPath = Get-NormalizedFullPath -Path $Path
    $root = [System.IO.Path]::GetPathRoot($fullPath)
    $current = $root
    $segments = @(
        $fullPath.Substring($root.Length).TrimStart('\', '/').Split(
            [char[]]@([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar),
            [System.StringSplitOptions]::RemoveEmptyEntries
        )
    )
    foreach ($segment in @('') + $segments) {
        if ($segment) { $current = Join-Path $current $segment }
        $item = Get-Item -LiteralPath $current -Force -ErrorAction SilentlyContinue
        if ($null -eq $item) { return }
        if ([string]$item.PSProvider.Name -ne 'FileSystem' -or
            ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "$Role has a reparse point in its existing path prefix."
        }
    }
}

function Resolve-ExistingPath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Role,
        [switch]$Leaf
    )
    $resolved = Get-NormalizedFullPath -Path $Path
    Assert-NoReparsePathChain -Path $resolved -Role $Role
    $type = if ($Leaf) { 'Leaf' } else { 'Container' }
    if (-not (Test-Path -LiteralPath $resolved -PathType $type)) {
        throw "$Role has the wrong path type."
    }
    $canonical = Get-CanonicalComparisonPath -Path $resolved -Role $Role
    Assert-NoReparsePathChain -Path $canonical -Role $Role
    if (-not (Test-Path -LiteralPath $canonical -PathType $type)) {
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
    $prefix = if ($rootPath.EndsWith([string][System.IO.Path]::DirectorySeparatorChar) -or
        $rootPath.EndsWith([string][System.IO.Path]::AltDirectorySeparatorChar)) {
        $rootPath
    } else {
        $rootPath + [System.IO.Path]::DirectorySeparatorChar
    }
    return $candidatePath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)
}

function Assert-SeparatePath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string[]]$ForbiddenRoots,
        [Parameter(Mandatory = $true)][string]$Role
    )
    foreach ($root in $ForbiddenRoots) {
        if ((Test-IsWithinRoot -Root $root -Candidate $Path) -or
            (Test-IsWithinRoot -Root $Path -Candidate $root)) {
            throw "$Role is not isolated from a protected root."
        }
    }
}

function Test-SafeRelativePath {
    param([Parameter(Mandatory = $true)][string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path) -or $Path.Contains('\') -or
        [System.IO.Path]::IsPathRooted($Path)) { return $false }
    foreach ($segment in $Path.Split('/')) {
        if ([string]::IsNullOrWhiteSpace($segment) -or $segment -eq '.' -or $segment -eq '..') {
            return $false
        }
    }
    return $true
}

function Get-ReleaseDesktopComponent {
    param([Parameter(Mandatory = $true)][object]$ReleaseAudit)
    $components = @($ReleaseAudit.components | Where-Object { [string]$_.name -ceq 'desktop_bridge' })
    if ($components.Count -ne 1 -or [int]$components[0].file_count -ne @($components[0].files).Count) {
        throw 'Current release audit has no internally consistent Desktop component.'
    }
    return $components[0]
}

function Get-PinnedDesktopSnapshotState {
    param(
        [Parameter(Mandatory = $true)][string]$SnapshotRoot,
        [Parameter(Mandatory = $true)][object]$ReleaseAudit
    )
    $snapshot = Resolve-ExistingPath -Path $SnapshotRoot -Role 'retained external Desktop snapshot'
    $component = Get-ReleaseDesktopComponent -ReleaseAudit $ReleaseAudit
    $expectedFiles = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::Ordinal)
    $expectedDirectories = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::Ordinal)
    foreach ($file in @($component.files)) {
        $relative = [string]$file.path
        if (-not (Test-SafeRelativePath -Path $relative) -or -not $expectedFiles.Add($relative)) {
            throw 'Current release audit contains an unsafe or duplicate Desktop path.'
        }
        $segments = $relative.Split('/')
        for ($index = 1; $index -lt $segments.Count; $index += 1) {
            [void]$expectedDirectories.Add(($segments[0..($index - 1)] -join '/'))
        }
    }

    $actualFiles = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::Ordinal)
    $actualDirectories = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::Ordinal)
    $stack = New-Object System.Collections.Generic.Stack[object]
    $stack.Push([pscustomobject]@{ FullPath = $snapshot; RelativePath = '' })
    while ($stack.Count -gt 0) {
        $current = $stack.Pop()
        Assert-NoReparsePathChain -Path ([string]$current.FullPath) -Role 'retained snapshot traversal'
        $directory = New-Object System.IO.DirectoryInfo([string]$current.FullPath)
        foreach ($entry in $directory.EnumerateFileSystemInfos()) {
            $relative = if ($current.RelativePath) {
                ([string]$current.RelativePath).TrimEnd('/') + '/' + $entry.Name
            } else { $entry.Name }
            if (($entry.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw 'Retained Desktop snapshot contains a reparse point.'
            }
            $isDirectory = ($entry.Attributes -band [System.IO.FileAttributes]::Directory) -ne 0
            if ($isDirectory) {
                if (-not $expectedDirectories.Contains($relative) -or
                    -not $actualDirectories.Add($relative)) {
                    throw 'Retained Desktop snapshot contains an unknown or duplicate directory.'
                }
                $stack.Push([pscustomobject]@{ FullPath = $entry.FullName; RelativePath = $relative })
            } else {
                if (-not $expectedFiles.Contains($relative) -or -not $actualFiles.Add($relative)) {
                    throw 'Retained Desktop snapshot contains an unknown, duplicate, or case-drifted file.'
                }
            }
        }
    }
    if ($actualFiles.Count -ne $expectedFiles.Count -or
        $actualDirectories.Count -ne $expectedDirectories.Count) {
        throw 'Retained Desktop snapshot path set is incomplete.'
    }

    $streams = New-Object 'System.Collections.Generic.List[System.IO.FileStream]'
    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add('feishu-codex-desktop-snapshot-v1')
    try {
        foreach ($file in @($component.files)) {
            $relative = [string]$file.path
            $path = Get-NormalizedFullPath -Path (Join-Path $snapshot ($relative.Replace('/', '\')))
            Assert-NoReparsePathChain -Path $path -Role 'retained snapshot file'
            $stream = New-Object System.IO.FileStream(
                $path,
                [System.IO.FileMode]::Open,
                [System.IO.FileAccess]::Read,
                [System.IO.FileShare]::Read
            )
            $streams.Add($stream)
            $bytes = Read-PinnedStreamBytes -Stream $stream -MaximumBytes $script:MaximumSourceFileBytes
            $hash = Get-BytesSha256 -Bytes $bytes
            if ($hash -cne [string]$file.sha256 -or $bytes.LongLength -ne [long]$file.size_bytes) {
                throw 'Retained Desktop snapshot differs from the current full release audit.'
            }
            $lines.Add(("{0}`t{1}`t{2}" -f $relative, $hash, $bytes.LongLength))
        }
        return [pscustomobject]@{
            Handles = $streams.ToArray()
            ManifestSha256 = Get-StringSha256 -Text (($lines -join "`n") + "`n")
            FileCount = $streams.Count
        }
    } catch {
        foreach ($stream in $streams) { $stream.Dispose() }
        throw
    }
}

function Get-BoundedRuntimeSnapshot {
    param(
        [Parameter(Mandatory = $true)][string]$Project,
        [Parameter(Mandatory = $true)][string]$Runtime
    )
    $paths = [ordered]@{
        'runtime/bridge.py' = Join-Path $Runtime 'bridge.py'
        'runtime/beeper_queue_cli.py' = Join-Path $Runtime 'beeper_queue_cli.py'
        'runtime/bridge_core/__init__.py' = Join-Path $Runtime 'bridge_core\__init__.py'
        'runtime/bridge_core/beeper_client.py' = Join-Path $Runtime 'bridge_core\beeper_client.py'
        'runtime/bridge_core/config.py' = Join-Path $Runtime 'bridge_core\config.py'
        'runtime/bridge_core/beeper_queue.py' = Join-Path $Runtime 'bridge_core\beeper_queue.py'
        'runtime/bridge_core/legacy_identifiers.py' = Join-Path $Runtime 'bridge_core\legacy_identifiers.py'
        'runtime/bridge_core/lark.py' = Join-Path $Runtime 'bridge_core\lark.py'
        'runtime/bridge_core/runtime.py' = Join-Path $Runtime 'bridge_core\runtime.py'
        'runtime/bridge_core/state.py' = Join-Path $Runtime 'bridge_core\state.py'
        'runtime/bridge.env' = Join-Path $Runtime 'bridge.env'
        'runtime/runtime-manifest.json' = Join-Path $Runtime 'runtime-manifest.json'
        'hooks/start' = Join-Path $Project '.codex\hooks\start-feishu-codex-bridge.ps1'
        'hooks/stop' = Join-Path $Project '.codex\hooks\stop-feishu-codex-bridge.ps1'
        'hooks/config' = Join-Path $Project '.codex\hooks.json'
        'rules/beeper' = Join-Path $Project '.codex\rules\feishu-beeper.rules'
    }
    $records = New-Object System.Collections.Generic.List[string]
    $present = 0
    foreach ($entry in $paths.GetEnumerator()) {
        Assert-NoReparseExistingPathPrefix -Path $entry.Value `
            -Role 'current bounded runtime/control path'
        if (Test-Path -LiteralPath $entry.Value -PathType Leaf) {
            Assert-NoReparsePathChain -Path $entry.Value -Role 'current bounded runtime/control file'
            $present += 1
            $bytes = Read-BoundedFileBytes -Path $entry.Value -MaximumBytes $script:MaximumSourceFileBytes
            $records.Add(("{0}`tpresent`t{1}" -f $entry.Key, (Get-BytesSha256 -Bytes $bytes)))
        } elseif (Test-Path -LiteralPath $entry.Value) {
            throw 'Current bounded runtime/control path has the wrong type.'
        } else {
            $records.Add(("{0}`tabsent`t-" -f $entry.Key))
        }
    }
    return [pscustomobject]@{
        ManifestSha256 = Get-StringSha256 -Text (($records -join "`n") + "`n")
        PathCount = $present
        Scope = @(
            $Runtime,
            (Join-Path $Project '.codex\hooks'),
            (Join-Path $Project '.codex\hooks.json'),
            (Join-Path $Project '.codex\rules\feishu-beeper.rules')
        )
    }
}

function Get-CurrentWindowsUserSid {
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    if ($null -eq $identity -or $null -eq $identity.User) {
        throw 'P0-B evidence validator could not resolve the current Windows user SID.'
    }
    return [string]$identity.User.Value
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

function Get-ObjectSha256 {
    param([Parameter(Mandatory = $true)][object]$Object)
    return Get-StringSha256 -Text `
        ($Object | Microsoft.PowerShell.Utility\ConvertTo-Json -Depth 20 -Compress)
}

function Assert-UniqueJsonObjectKeys {
    param(
        [Parameter(Mandatory = $true)][string]$Json,
        [Parameter(Mandatory = $true)][string]$Role
    )
    $document = [System.Text.Json.JsonDocument]::Parse($Json)
    try {
        $pendingElements = New-Object System.Collections.Generic.Stack[System.Text.Json.JsonElement]
        $pendingElements.Push($document.RootElement)
        while ($pendingElements.Count -gt 0) {
            $element = $pendingElements.Pop()
            if ($element.ValueKind -eq [System.Text.Json.JsonValueKind]::Object) {
                $names = New-Object 'System.Collections.Generic.HashSet[string]' `
                    ([System.StringComparer]::OrdinalIgnoreCase)
                foreach ($property in $element.EnumerateObject()) {
                    if (-not $names.Add($property.Name)) {
                        throw "$Role contains duplicate or case-colliding object keys."
                    }
                    $pendingElements.Push($property.Value)
                }
            } elseif ($element.ValueKind -eq [System.Text.Json.JsonValueKind]::Array) {
                foreach ($item in $element.EnumerateArray()) { $pendingElements.Push($item) }
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

function Get-ComponentRecord {
    param(
        [Parameter(Mandatory = $true)][object]$Audit,
        [Parameter(Mandatory = $true)][string]$Component,
        [Parameter(Mandatory = $true)][string]$RelativePath
    )
    $components = @($Audit.components | Where-Object { [string]$_.name -ceq $Component })
    if ($components.Count -ne 1) { throw "Audit component $Component is not unique." }
    $records = @($components[0].files | Where-Object { [string]$_.path -ceq $RelativePath })
    if ($records.Count -ne 1) { throw "Audit path $Component/$RelativePath is not unique." }
    return $records[0]
}

function Assert-HookConfig {
    param(
        [Parameter(Mandatory = $true)][string]$ConfigPath,
        [Parameter(Mandatory = $true)][string]$StartHookPath,
        [Parameter(Mandatory = $true)][string]$StopHookPath
    )
    $bytes = Read-BoundedFileBytes -Path $ConfigPath -MaximumBytes $script:MaximumSourceFileBytes
    try {
        $configText = $script:StrictUtf8.GetString($bytes)
    } catch {
        throw 'Installed hooks.json was not strict UTF-8 JSON.'
    }
    Assert-UniqueJsonObjectKeys -Json $configText -Role 'Installed hooks.json'
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
        @($startGroups[0].hooks).Count -ne 1 -or @($stopGroups[0].hooks).Count -ne 1) {
        throw 'Installed hook registration is not unique.'
    }
    $start = @($startGroups[0].hooks)[0]
    $stop = @($stopGroups[0].hooks)[0]
    $expectedStart = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "' + $StartHookPath + '" -HookInvocation'
    $expectedStop = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "' + $StopHookPath + '" -HookInvocation'
    if ([string]$start.type -cne 'command' -or [string]$start.command -ine $expectedStart -or
        [string]$start.commandWindows -ine $expectedStart -or [int]$start.timeout -ne 10 -or
        [string]$stop.type -cne 'command' -or [string]$stop.command -ine $expectedStop -or
        [string]$stop.commandWindows -ine $expectedStop -or [int]$stop.timeout -ne 3) {
        throw 'Installed hook registration differs from the exact lifecycle contract.'
    }
    return Get-BytesSha256 -Bytes $bytes
}

$evidenceStream = $null
$schemaStream = $null
$pythonStream = $null
$runnerShellStream = $null
$snapshotState = $null
$lifecycleMutex = $null
$lifecycleMutexOwned = $false
$captureStreams = New-Object 'System.Collections.Generic.List[System.IO.FileStream]'
try {
    $desktop = Resolve-ExistingPath -Path $DesktopRoot -Role 'Desktop source root'
    $harness = Resolve-ExistingPath -Path $HarnessRoot -Role 'Harness source root'
    $project = Resolve-ExistingPath -Path $ProjectRoot -Role 'Desktop project root'
    $canonicalRuntimeCandidate = Join-Path $project '.codex\feishu-codex-bridge-runtime'
    $legacyRuntimeCandidate = Join-Path $project '.codex\feishu-bridge'
    if ((Test-Path -LiteralPath $canonicalRuntimeCandidate) -and
        (Test-Path -LiteralPath $legacyRuntimeCandidate)) {
        throw 'Bridge runtime layout is ambiguous; Gate B validation refuses two durable state authorities.'
    }
    $runtime = Get-NormalizedFullPath -Path $(
        if (Test-Path -LiteralPath $legacyRuntimeCandidate -PathType Container) {
            $legacyRuntimeCandidate
        } else {
            $canonicalRuntimeCandidate
        }
    )
    $evidence = Resolve-ExistingPath -Path $EvidencePath -Role 'P0-B evidence file' -Leaf
    $evidenceParent = Resolve-ExistingPath -Path (Split-Path -Parent $evidence) `
        -Role 'P0-B evidence directory'
    Assert-SeparatePath -Path $evidenceParent -ForbiddenRoots @($desktop, $harness, $project, $runtime) `
        -Role 'P0-B evidence directory'

    $evidenceStream = New-Object System.IO.FileStream(
        $evidence,
        [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Read,
        [System.IO.FileShare]::Read
    )
    $evidenceBytes = Read-PinnedStreamBytes -Stream $evidenceStream `
        -MaximumBytes $script:MaximumEvidenceBytes
    $evidenceSha256 = Get-BytesSha256 -Bytes $evidenceBytes
    if ($evidenceSha256 -cne $ExpectedEvidenceSha256.ToLowerInvariant()) {
        throw 'P0-B evidence bytes do not match the supplied hash envelope.'
    }
    try {
        $evidenceJson = $script:StrictUtf8.GetString($evidenceBytes)
        Assert-UniqueJsonObjectKeys -Json $evidenceJson -Role 'P0-B evidence JSON'
        $receipt = $evidenceJson | Microsoft.PowerShell.Utility\ConvertFrom-Json
    } catch {
        throw 'P0-B evidence file is not strict UTF-8 JSON.'
    }
    $expectedEvidenceName = 'p0b-v2-' + [string]$receipt.receipt_id + '.json'
    if ([System.IO.Path]::GetFileName($evidence) -cne $expectedEvidenceName) {
        throw 'P0-B evidence filename is not bound to its receipt ID.'
    }

    $auditScript = Resolve-ExistingPath -Path `
        (Join-Path $desktop 'scripts\audit-feishu-codex-release.ps1') `
        -Role 'current full release audit' -Leaf
    $currentAuditJson = @(& $auditScript -DesktopRoot $desktop -HarnessRoot $harness) -join "`n"
    $currentAudit = $currentAuditJson | Microsoft.PowerShell.Utility\ConvertFrom-Json
    if ([string]$currentAudit.status -cne 'pass') { throw 'Current full P0-A audit did not pass.' }

    $schemaPath = Resolve-ExistingPath -Path `
        (Join-Path $desktop 'assets\external-test-evidence.schema.json') `
        -Role 'current P0-B evidence schema' -Leaf
    $schemaRecord = Get-ComponentRecord -Audit $currentAudit -Component 'desktop_bridge' `
        -RelativePath 'assets/external-test-evidence.schema.json'
    $schemaStream = New-Object System.IO.FileStream(
        $schemaPath,
        [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Read,
        [System.IO.FileShare]::Read
    )
    $schemaBytes = Read-PinnedStreamBytes -Stream $schemaStream `
        -MaximumBytes $script:MaximumSourceFileBytes
    if ((Get-BytesSha256 -Bytes $schemaBytes) -cne [string]$schemaRecord.sha256 -or
        $schemaBytes.LongLength -ne [long]$schemaRecord.size_bytes) {
        throw 'Pinned evidence schema differs from the current full release audit.'
    }
    $testJsonCommand = Microsoft.PowerShell.Core\Get-Command `
        'Microsoft.PowerShell.Utility\Test-Json' -ErrorAction SilentlyContinue
    if ($null -eq $testJsonCommand -or -not $testJsonCommand.Parameters.ContainsKey('SchemaFile') -or
        -not $testJsonCommand.Parameters.ContainsKey('Schema')) {
        throw 'P0-B evidence validator lacks the required Test-Json schema surface.'
    }
    $probeSchema = '{"$schema":"https://json-schema.org/draft/2020-12/schema","type":"object","required":["probe"],"properties":{"probe":{"$ref":"#/$defs/enabled"}},"$defs":{"enabled":{"const":true}}}'
    $probePass = '{"probe":true}' | Microsoft.PowerShell.Utility\Test-Json `
        -Schema $probeSchema -ErrorAction Stop
    $probeReject = '{"probe":false}' | Microsoft.PowerShell.Utility\Test-Json `
        -Schema $probeSchema -ErrorAction SilentlyContinue
    if (-not $probePass -or $probeReject) {
        throw 'P0-B evidence validator did not enforce draft-2020-12 $defs/const.'
    }
    if (-not ($evidenceJson | Microsoft.PowerShell.Utility\Test-Json `
            -SchemaFile $schemaPath -ErrorAction Stop)) {
        throw 'P0-B evidence failed the current pinned JSON Schema.'
    }

    $currentAuditSha256 = Get-ObjectSha256 -Object $currentAudit
    if ((Get-ObjectSha256 -Object $receipt.release_audit) -cne $currentAuditSha256 -or
        [string]$receipt.source_guard.before_audit_sha256 -cne $currentAuditSha256 -or
        [string]$receipt.source_guard.after_audit_sha256 -cne $currentAuditSha256) {
        throw 'Receipt P0-A evidence does not exactly match the current full release audit.'
    }
    $releaseFileCount = 0
    foreach ($component in @($currentAudit.components)) { $releaseFileCount += @($component.files).Count }
    if ([int]$receipt.source_guard.pinned_release_file_count -ne $releaseFileCount) {
        throw 'Receipt pinned release-file count differs from the current audited release.'
    }

$expectedTests = [ordered]@{
    F01 = 'test_beeper_queue.BeeperQueueTests.test_namespace_and_registration_are_closed_and_immutable;test_beeper_queue.BeeperQueueTests.test_beeper_and_tombstones_cannot_be_business_responders'
    F02 = 'test_beeper_client.BeeperClientContractTests.test_argv_contains_only_fixed_control_and_opaque_page;test_beeper_client.BeeperClientContractTests.test_same_request_never_spawns_twice'
    F03 = 'test_beeper_client.BeeperClientContractTests.test_reserved_beeper_loads_exact_uri_once_without_requeue'
    F04 = 'test_beeper_client.BeeperClientContractTests.test_load_assist_failure_is_safe_and_terminal'
    F05 = 'test_beeper_client.BeeperClientContractTests.test_readonly_unknown_is_safe_terminal_and_not_retried;test_beeper_queue.BeeperQueueTests.test_readonly_claim_expiry_is_terminal_and_not_replayed'
    F06 = 'test_beeper_queue.BeeperQueueTests.test_unclaimed_failure_cas_and_claim_are_exclusive'
    F07 = 'test_beeper_queue.BeeperQueueTests.test_finish_waits_for_delayed_beeper_claim'
    F08 = 'test_beeper_queue.BeeperQueueTests.test_final_callback_finish_is_exactly_once;test_beeper_client.BeeperClientContractTests.test_completed_send_requires_top_level_final_callback_source'
    F09 = 'test_beeper_queue.BeeperQueueTests.test_final_callback_conflict_fails_closed_and_scrubs_capability;test_beeper_queue.BeeperQueueTests.test_catalog_tamper_is_rejected_and_scrubbed'
    F10 = 'test_beeper_queue.BeeperQueueTests.test_catalog_interrupted_consume_is_not_replayed_and_ages_out;test_beeper_client.BeeperClientContractTests.test_final_callback_timeout_is_terminal_and_not_retried'
    F11 = 'test_runtime.StableConversationScopeTests.test_binding_commit_control_crash_is_terminal_after_reopen;test_beeper_queue.BeeperQueueTests.test_catalog_is_staged_answer_free_then_consumed_once'
    F12 = 'test_agents_rules.BridgeEnvEntrypointTests.test_malformed_bridge_env_fails_closed_at_dispatcher_start_and_queue_entrypoints'
}
    $desktopComponent = Get-ReleaseDesktopComponent -ReleaseAudit $currentAudit
    $desktopFileCount = @($desktopComponent.files).Count
    $workingDirectory = Resolve-ExistingPath -Path ([string]$receipt.execution.working_directory) `
        -Role 'retained external Desktop snapshot'
    if ([System.IO.Path]::GetFileName($workingDirectory) -cne 'source-snapshot') {
        throw 'Retained Desktop snapshot has the wrong leaf name.'
    }
    $workDirectory = Resolve-ExistingPath -Path (Split-Path -Parent $workingDirectory) `
        -Role 'retained P0-B work directory'
    $expectedWorkDirectoryLeaf = 'w-' +
        (Get-StringSha256 -Text ([string]$receipt.receipt_id)).Substring(0, 8)
    if ([System.IO.Path]::GetFileName($workDirectory) -cne $expectedWorkDirectoryLeaf) {
        throw 'Retained P0-B work directory is not bound to the receipt ID.'
    }
    $externalWorkRoot = Resolve-ExistingPath -Path (Split-Path -Parent $workDirectory) `
        -Role 'external P0-B work root'
    Assert-SeparatePath -Path $externalWorkRoot -ForbiddenRoots @($desktop, $harness, $project, $runtime) `
        -Role 'external P0-B work root'
    Assert-SeparatePath -Path $evidenceParent -ForbiddenRoots @($externalWorkRoot) `
        -Role 'P0-B evidence directory'
    if (-not (Get-NormalizedFullPath -Path ([string]$receipt.execution.environment.FEISHU_BRIDGE_TEST_TMP)).Equals(
            $workDirectory, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'Receipt test temp does not equal its retained P0-B work directory.'
    }

    $snapshotState = Get-PinnedDesktopSnapshotState -SnapshotRoot $workingDirectory `
        -ReleaseAudit $currentAudit
    if ([string]$receipt.source_guard.before_snapshot_manifest_sha256 -cne $snapshotState.ManifestSha256 -or
        [string]$receipt.source_guard.after_snapshot_manifest_sha256 -cne $snapshotState.ManifestSha256 -or
        [int]$receipt.source_guard.snapshot_file_count -ne $snapshotState.FileCount -or
        [int]$receipt.source_guard.pinned_snapshot_file_count -ne $snapshotState.FileCount -or
        $snapshotState.FileCount -ne $desktopFileCount) {
        throw 'Receipt snapshot evidence does not match the pinned retained Desktop snapshot.'
    }

    $argv = @($receipt.execution.argv)
    $expectedDriver = Get-NormalizedFullPath -Path `
        (Join-Path $workingDirectory 'scripts\external_p0b_test_runner.py')
    $expectedTestsPath = Get-NormalizedFullPath -Path (Join-Path $workingDirectory 'tests')
    $expectedStructuredPath = Get-NormalizedFullPath -Path `
        (Join-Path $workDirectory 'structured-test-result.json')
    if ($argv.Count -ne 11 -or [string]$argv[1] -cne '-I' -or
        [string]$argv[2] -cne '-S' -or [string]$argv[3] -cne '-B' -or
        -not (Get-NormalizedFullPath -Path ([string]$argv[4])).Equals(
            $expectedDriver, [System.StringComparison]::OrdinalIgnoreCase) -or
        [string]$argv[5] -cne '--tests-dir' -or
        -not (Get-NormalizedFullPath -Path ([string]$argv[6])).Equals(
            $expectedTestsPath, [System.StringComparison]::OrdinalIgnoreCase) -or
        [string]$argv[7] -cne '--result-path' -or
        -not (Get-NormalizedFullPath -Path ([string]$argv[8])).Equals(
            $expectedStructuredPath, [System.StringComparison]::OrdinalIgnoreCase) -or
        [string]$argv[9] -cne '--nonce' -or
        [string]$argv[10] -cnotmatch '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' -or
        (Get-StringSha256 -Text ([string]$argv[10])) -cne
            [string]$receipt.execution.structured_result_nonce_sha256) {
        throw 'Receipt argv is not the exact structured isolated unittest command.'
    }
    $startRecord = Get-ComponentRecord -Audit $currentAudit -Component 'desktop_bridge' `
        -RelativePath 'scripts/start-feishu-codex-bridge.ps1'
    $stopRecord = Get-ComponentRecord -Audit $currentAudit -Component 'desktop_bridge' `
        -RelativePath 'scripts/stop-feishu-codex-bridge.ps1'
    $supervisorRecord = Get-ComponentRecord -Audit $currentAudit -Component 'desktop_bridge' `
        -RelativePath 'scripts/run-external-p0b.ps1'
    $installedStart = Resolve-ExistingPath -Path `
        (Join-Path $project '.codex\hooks\start-feishu-codex-bridge.ps1') `
        -Role 'installed start hook' -Leaf
    $installedStop = Resolve-ExistingPath -Path `
        (Join-Path $project '.codex\hooks\stop-feishu-codex-bridge.ps1') `
        -Role 'installed stop hook' -Leaf
    foreach ($hookRecord in @(
        [pscustomobject]@{ Path = $installedStart; Record = $startRecord; Role = 'installed start hook' },
        [pscustomobject]@{ Path = $installedStop; Record = $stopRecord; Role = 'installed stop hook' }
    )) {
        $stream = New-Object System.IO.FileStream(
            $hookRecord.Path,
            [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::Read,
            [System.IO.FileShare]::Read
        )
        $captureStreams.Add($stream)
        $bytes = Read-PinnedStreamBytes -Stream $stream -MaximumBytes $script:MaximumSourceFileBytes
        $hash = Get-BytesSha256 -Bytes $bytes
        if ($hash -cne [string]$hookRecord.Record.sha256 -or
            $bytes.LongLength -ne [long]$hookRecord.Record.size_bytes) {
            throw "$($hookRecord.Role) differs from its current audited source."
        }
        if ($hookRecord.Role -eq 'installed start hook') { $startHash = $hash } else { $stopHash = $hash }
    }
    $configPath = Resolve-ExistingPath -Path (Join-Path $project '.codex\hooks.json') `
        -Role 'installed hooks config' -Leaf
    $configStream = New-Object System.IO.FileStream(
        $configPath,
        [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Read,
        [System.IO.FileShare]::Read
    )
    $captureStreams.Add($configStream)
    $configHash = Assert-HookConfig -ConfigPath $configPath `
        -StartHookPath $installedStart -StopHookPath $installedStop

    $currentUserSid = Get-CurrentWindowsUserSid
    $lifecycleMutexName = Get-LifecycleMutexName -Project $project -UserSid $currentUserSid
    $lifecycleMutex = [System.Threading.Mutex]::new($false, $lifecycleMutexName)
    try {
        $lifecycleMutexOwned = $lifecycleMutex.WaitOne(0)
    } catch [System.Threading.AbandonedMutexException] {
        $lifecycleMutexOwned = $true
    }
    if (-not $lifecycleMutexOwned) {
        throw 'Bridge lifecycle mutex is busy during current-environment evidence validation.'
    }
    $mutexEvidence = $receipt.bridge_stopped_receipt.lifecycle_mutex
    if ([string]$mutexEvidence.name_sha256 -cne (Get-StringSha256 -Text $lifecycleMutexName) -or
        [string]$mutexEvidence.owner_sid_sha256 -cne (Get-StringSha256 -Text $currentUserSid) -or
        [string]$mutexEvidence.source_hook_sha256 -cne [string]$startRecord.sha256 -or
        [string]$mutexEvidence.installed_hook_sha256 -cne $startHash -or
        [string]$mutexEvidence.source_stop_hook_sha256 -cne [string]$stopRecord.sha256 -or
        [string]$mutexEvidence.installed_stop_hook_sha256 -cne $stopHash -or
        [string]$mutexEvidence.hooks_config_sha256 -cne $configHash -or
        [string]$receipt.runner.guard.supervisor_sha256 -cne [string]$supervisorRecord.sha256) {
        throw 'Receipt supervisor, mutex, or lifecycle-hook evidence differs from current state.'
    }

    $python = Resolve-ExistingPath -Path ([string]$argv[0]) -Role 'receipt Python executable' -Leaf
    $pythonStream = New-Object System.IO.FileStream(
        $python,
        [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Read,
        [System.IO.FileShare]::Read
    )
    $pythonBytes = Read-PinnedStreamBytes -Stream $pythonStream `
        -MaximumBytes $script:MaximumPythonBytes
    $pythonHash = Get-BytesSha256 -Bytes $pythonBytes
    $pythonSignature = Microsoft.PowerShell.Security\Get-AuthenticodeSignature `
        -LiteralPath $python -ErrorAction Stop
    if ($pythonHash -cne [string]$receipt.runner.python.executable_sha256 -or
        [string]$pythonSignature.Status -cne 'Valid' -or
        $null -eq $pythonSignature.SignerCertificate -or
        [string]$pythonSignature.SignerCertificate.Subject -notmatch
            '(?i)(?:^|,\s*)O=Python Software Foundation(?:,|$)' -or
        (Get-StringSha256 -Text ([string]$pythonSignature.SignerCertificate.Subject)) -cne
            [string]$receipt.runner.guard.python_signer_subject_sha256 -or
        (Get-StringSha256 -Text (
            ([string]$pythonSignature.SignerCertificate.Thumbprint).ToLowerInvariant()
        )) -cne [string]$receipt.runner.guard.python_certificate_thumbprint_sha256 -or
        [string]$receipt.runner.python.implementation -cne 'CPython') {
        throw 'Receipt Python executable, signature, or provenance hashes do not match current state.'
    }
    $pythonVersionInfo = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($python)
    if ([string]::IsNullOrWhiteSpace([string]$pythonVersionInfo.ProductVersion) -or
        -not ([string]$receipt.runner.python.version).StartsWith(
            [string]$pythonVersionInfo.ProductVersion,
            [System.StringComparison]::Ordinal
        )) {
        throw 'Receipt Python version does not match the pinned executable metadata.'
    }

    $preStatusArgv = @($receipt.bridge_stopped_receipt.pre.status_argv)
    if ($preStatusArgv.Count -lt 1) { throw 'Receipt pre status argv has no runner shell.' }
    $runnerShell = Resolve-ExistingPath -Path ([string]$preStatusArgv[0]) `
        -Role 'receipt runner PowerShell executable' -Leaf
    $runnerShellStream = New-Object System.IO.FileStream(
        $runnerShell,
        [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Read,
        [System.IO.FileShare]::Read
    )
    $runnerShellBytes = Read-PinnedStreamBytes -Stream $runnerShellStream `
        -MaximumBytes $script:MaximumPythonBytes
    $runnerShellVersion = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($runnerShell)
    if ([string]$receipt.runner.powershell.implementation -cne 'Core' -or
        [string]$receipt.runner.powershell.executable_sha256 -cne
            (Get-BytesSha256 -Bytes $runnerShellBytes) -or
        [string]::IsNullOrWhiteSpace([string]$runnerShellVersion.FileVersion) -or
        -not (([string]$runnerShellVersion.FileVersion).Equals(
                [string]$receipt.runner.powershell.version,
                [System.StringComparison]::Ordinal
            ) -or ([string]$runnerShellVersion.FileVersion).StartsWith(
                ([string]$receipt.runner.powershell.version + '.'),
                [System.StringComparison]::Ordinal
            ))) {
        throw 'Receipt PowerShell surface does not match its retained runner executable.'
    }

    $captureRecords = [ordered]@{
        stdout = [pscustomobject]@{
            Path = Join-Path $workDirectory 'dynamic-tests.stdout.txt'
            ExpectedHash = [string]$receipt.execution.stdout_sha256
            MaximumBytes = $script:MaximumCaptureBytes
        }
        stderr = [pscustomobject]@{
            Path = Join-Path $workDirectory 'dynamic-tests.stderr.txt'
            ExpectedHash = [string]$receipt.execution.stderr_sha256
            MaximumBytes = $script:MaximumCaptureBytes
        }
        structured = [pscustomobject]@{
            Path = $expectedStructuredPath
            ExpectedHash = [string]$receipt.execution.structured_result_sha256
            MaximumBytes = $script:MaximumSourceFileBytes
        }
    }
    $captureBytes = @{}
    foreach ($entry in $captureRecords.GetEnumerator()) {
        $path = Resolve-ExistingPath -Path $entry.Value.Path `
            -Role ("retained P0-B {0} capture" -f $entry.Key) -Leaf
        $stream = New-Object System.IO.FileStream(
            $path,
            [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::Read,
            [System.IO.FileShare]::Read
        )
        $captureStreams.Add($stream)
        $bytes = Read-PinnedStreamBytes -Stream $stream -MaximumBytes ([long]$entry.Value.MaximumBytes)
        if ((Get-BytesSha256 -Bytes $bytes) -cne [string]$entry.Value.ExpectedHash) {
            throw "Retained P0-B $($entry.Key) capture differs from its receipt hash."
        }
        $captureBytes[[string]$entry.Key] = $bytes
    }

    try {
        $structuredJson = $script:StrictUtf8.GetString([byte[]]$captureBytes.structured)
        Assert-UniqueJsonObjectKeys -Json $structuredJson -Role 'P0-B structured unittest result'
        $structured = $structuredJson | Microsoft.PowerShell.Utility\ConvertFrom-Json
    } catch {
        throw 'Retained structured unittest result is not strict UTF-8 JSON.'
    }
    $requiredFaultTestIds = @(
        foreach ($entry in $expectedTests.GetEnumerator()) {
            foreach ($testId in ([string]$entry.Value).Split(';')) { $testId }
        }
    )
    $structuredRequiredIds = @($structured.required_fault_test_ids | ForEach-Object { [string]$_ })
    $successfulIds = @($structured.successful_test_ids | ForEach-Object { [string]$_ })
    $successfulSet = New-Object 'System.Collections.Generic.HashSet[string]' `
        ([System.StringComparer]::Ordinal)
    foreach ($testId in $successfulIds) {
        if (-not $successfulSet.Add($testId)) {
            throw 'Retained structured unittest result contains duplicate successes.'
        }
    }
    if ([int]$structured.schema_version -ne 1 -or
        [string]$structured.nonce -cne [string]$argv[10] -or
        [string]$structured.runner_status -cne 'pass' -or
        ($structuredRequiredIds -join "`n") -cne ($requiredFaultTestIds -join "`n") -or
        @($structured.missing_required_test_ids).Count -ne 0 -or
        @($structured.failure_test_ids).Count -ne 0 -or
        @($structured.error_test_ids).Count -ne 0 -or
        @($structured.skipped_test_ids).Count -ne 0 -or
        [int]$receipt.execution.skipped -ne 0 -or
        [int]$structured.tests_run -ne [int]$structured.tests_discovered -or
        [int]$structured.tests_run -ne [int]$receipt.execution.tests_run -or
        @($structured.skipped_test_ids).Count -ne [int]$receipt.execution.skipped -or
        $successfulIds.Count -ne [int]$structured.tests_run) {
        throw 'Retained structured unittest result differs from the receipt contract.'
    }
    if ([bool]$receipt.execution.structured_result_verified -ne $true -or
        [string]$receipt.execution.test_result_source -cne 'unittest.TestResult' -or
        [bool]$receipt.execution.process_job_object_enforced -ne $true -or
        [string]$receipt.execution.process_job_object_mode -cne 'kill_on_close' -or
        [string]$receipt.execution.process_start_assignment_mode -cne 'start_then_immediate_assign' -or
        [string]$receipt.execution.process_tree_timeout_mode -cne 'kill_entire_process_tree') {
        throw 'Receipt structured-result or process-containment contract is incomplete.'
    }
    if ([bool]$receipt.runner.guard.inherited_sensitive_environment_removed -ne $true -or
        [bool]$receipt.runner.guard.child_path_restricted -ne $true -or
        [bool]$receipt.runner.guard.clean_powershell_invocation -ne $true -or
        [bool]$receipt.runner.guard.powershell_profile_loaded -ne $false) {
        throw 'Receipt clean-shell or child-environment contract is incomplete.'
    }
    if ([int]$receipt.runner.guard.process_ancestry_depth -lt 2 -or
        [int]$receipt.runner.guard.codex_ancestor_match_count -ne 0 -or
        [bool]$receipt.runner.guard.external_origin_asserted -ne $true -or
        [string]$receipt.runner.guard.process_ancestry_termination -notin
            @('process_tree_root', 'exited_ancestor') -or
        ([bool]$receipt.runner.guard.process_ancestry_complete -ne
            ([string]$receipt.runner.guard.process_ancestry_termination -ceq 'process_tree_root'))) {
        throw 'Receipt external process-ancestry contract is inconsistent.'
    }
    foreach ($testId in $requiredFaultTestIds) {
        if (-not $successfulSet.Contains($testId)) {
            throw 'Retained structured unittest result lacks a required successful test.'
        }
    }

    $faultResults = @($receipt.execution.fault_results)
    if ($faultResults.Count -ne 12 -or
        [int]$receipt.execution.required_fault_test_count -ne $requiredFaultTestIds.Count -or
        $requiredFaultTestIds.Count -ne 19) {
        throw 'Receipt F01-F12 result count is incomplete.'
    }
    $faultIndex = 0
    foreach ($entry in $expectedTests.GetEnumerator()) {
        $actual = $faultResults[$faultIndex]
        $testIds = ([string]$entry.Value).Split(';')
        $faultCanonical = ([string]$entry.Key) + "`n" + ($testIds -join "`n") + "`n" +
            [string]$receipt.execution.structured_result_sha256 + "`n"
        if ([string]$actual.fault_id -cne [string]$entry.Key -or
            [string]$actual.test_id -cne [string]$entry.Value -or
            [string]$actual.status -cne 'pass' -or
            [string]$actual.evidence_sha256 -cne (Get-StringSha256 -Text $faultCanonical)) {
            throw 'Receipt F01-F12 mapping or evidence hash differs from retained TestResult evidence.'
        }
        $faultIndex += 1
    }

    $currentRuntime = Get-BoundedRuntimeSnapshot -Project $project -Runtime $runtime
    if ([string]$receipt.runtime_guard.before_manifest_sha256 -cne $currentRuntime.ManifestSha256 -or
        [string]$receipt.runtime_guard.after_manifest_sha256 -cne $currentRuntime.ManifestSha256 -or
        [string]$receipt.runtime_guard.final_manifest_sha256 -cne $currentRuntime.ManifestSha256 -or
        [int]$receipt.runtime_guard.before_path_count -ne $currentRuntime.PathCount -or
        [int]$receipt.runtime_guard.after_path_count -ne $currentRuntime.PathCount -or
        [int]$receipt.runtime_guard.final_path_count -ne $currentRuntime.PathCount) {
        throw 'Receipt runtime guard does not match the current bounded runtime/control snapshot.'
    }
    $receiptScope = @($receipt.runtime_guard.snapshot_scope)
    if ($receiptScope.Count -ne $currentRuntime.Scope.Count) {
        throw 'Receipt runtime snapshot scope has the wrong size.'
    }
    for ($index = 0; $index -lt $receiptScope.Count; $index += 1) {
        if (-not (Get-NormalizedFullPath -Path ([string]$receiptScope[$index])).Equals(
                (Get-NormalizedFullPath -Path ([string]$currentRuntime.Scope[$index])),
                [System.StringComparison]::OrdinalIgnoreCase)) {
            throw 'Receipt runtime snapshot scope differs from the current exact scope.'
        }
    }

    $expectedDispatcher = Get-NormalizedFullPath -Path `
        (Join-Path $workingDirectory 'scripts\feishu-codex-bridge.ps1')
    foreach ($label in @('pre', 'post', 'final')) {
        $observation = $receipt.bridge_stopped_receipt.$label
        $statusArgv = @($observation.status_argv)
        if ($statusArgv.Count -ne 13 -or
            -not (Get-NormalizedFullPath -Path ([string]$statusArgv[0])).Equals(
                $runnerShell, [System.StringComparison]::OrdinalIgnoreCase) -or
            [string]$statusArgv[1] -cne '-NoLogo' -or
            [string]$statusArgv[2] -cne '-NoProfile' -or
            [string]$statusArgv[3] -cne '-NonInteractive' -or
            [string]$statusArgv[4] -cne '-ExecutionPolicy' -or
            [string]$statusArgv[5] -cne 'Bypass' -or
            [string]$statusArgv[6] -cne '-File' -or
            -not (Get-NormalizedFullPath -Path ([string]$statusArgv[7])).Equals(
                $expectedDispatcher, [System.StringComparison]::OrdinalIgnoreCase) -or
            [string]$statusArgv[8] -cne 'bridge' -or [string]$statusArgv[9] -cne 'status' -or
            [string]$statusArgv[10] -cne '-ProjectRoot' -or
            -not (Get-NormalizedFullPath -Path ([string]$statusArgv[11])).Equals(
                $project, [System.StringComparison]::OrdinalIgnoreCase) -or
            [string]$statusArgv[12] -cne '-Json') {
            throw "Receipt $label status argv differs from the exact stopped observation command."
        }
        $statusCaptures = [ordered]@{
            stdout = [pscustomobject]@{
                Path = Join-Path $workDirectory ("status-{0}.stdout.txt" -f $label)
                ExpectedHash = [string]$observation.status_stdout_sha256
            }
            stderr = [pscustomobject]@{
                Path = Join-Path $workDirectory ("status-{0}.stderr.txt" -f $label)
                ExpectedHash = [string]$observation.status_stderr_sha256
            }
        }
        $statusBytesByKind = @{}
        foreach ($entry in $statusCaptures.GetEnumerator()) {
            $statusPath = Resolve-ExistingPath -Path $entry.Value.Path `
                -Role ("retained {0} status {1} capture" -f $label, $entry.Key) -Leaf
            $statusStream = New-Object System.IO.FileStream(
                $statusPath,
                [System.IO.FileMode]::Open,
                [System.IO.FileAccess]::Read,
                [System.IO.FileShare]::Read
            )
            $captureStreams.Add($statusStream)
            $bytes = Read-PinnedStreamBytes -Stream $statusStream `
                -MaximumBytes $script:MaximumCaptureBytes
            if ((Get-BytesSha256 -Bytes $bytes) -cne [string]$entry.Value.ExpectedHash) {
                throw "Receipt $label status $($entry.Key) capture differs from its recorded hash."
            }
            $statusBytesByKind[[string]$entry.Key] = $bytes
        }
        if (([byte[]]$statusBytesByKind.stderr).Length -ne 0) {
            throw "Receipt $label status stderr is not empty."
        }
        $statusBytes = [byte[]]$statusBytesByKind.stdout
        $statusText = $script:StrictUtf8.GetString($statusBytes)
        Assert-UniqueJsonObjectKeys -Json $statusText -Role "P0-B Bridge status $label"
        try {
        $status = $statusText | Microsoft.PowerShell.Utility\ConvertFrom-Json -ErrorAction Stop
        } catch {
            throw "Receipt $label status capture is not valid JSON."
        }
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
        $statusPidStateMatches = if ([string]$observation.pid_file_state -ceq 'absent') {
            [string]$status.runtime.pid_file_state -ceq 'absent'
        } else {
            [string]$status.runtime.pid_file_state -in @(
                'invalid', 'stale_process_absent', 'stale_foreign_process'
            )
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
                -Expected ([string]$observation.pid_file_state -ceq 'stale')) -or
            [string]$observation.state -cne 'stopped' -or
            [int]$observation.status_exit_code -ne 0 -or
            -not (Test-JsonBooleanValue -Value $observation.health_idle -Expected $true) -or
            [string]$observation.pid_file_state -notin @('absent', 'stale') -or
            -not (Test-JsonBooleanValue -Value $observation.pid_process_alive -Expected $false) -or
            -not (Test-JsonIntegerZero -Value $observation.matching_bridge_process_count)) {
            throw "Receipt $label status capture is not an exact stopped and idle checkpoint."
        }

        $beeperArgv = @($observation.beeper_status_argv)
        $expectedBeeper = Get-NormalizedFullPath -Path `
            (Join-Path $workingDirectory 'scripts\beeper_queue_cli.py')
        if ($beeperArgv.Count -ne 9 -or
            -not (Get-NormalizedFullPath -Path ([string]$beeperArgv[0])).Equals(
                $python, [System.StringComparison]::OrdinalIgnoreCase) -or
            [string]$beeperArgv[1] -cne '-S' -or [string]$beeperArgv[2] -cne '-B' -or
            -not (Get-NormalizedFullPath -Path ([string]$beeperArgv[3])).Equals(
                $expectedBeeper, [System.StringComparison]::OrdinalIgnoreCase) -or
            [string]$beeperArgv[4] -cne '--runtime-dir' -or
            -not (Get-NormalizedFullPath -Path ([string]$beeperArgv[5])).Equals(
                $runtime, [System.StringComparison]::OrdinalIgnoreCase) -or
            [string]$beeperArgv[6] -cne '--queue-namespace' -or
            [string]$beeperArgv[7] -cne 'beeper' -or
            [string]$beeperArgv[8] -cne 'status') {
            throw "Receipt $label Beeper queue argv differs from the exact idle observation command."
        }
        $beeperCaptures = [ordered]@{
            stdout = [pscustomobject]@{
                Path = Join-Path $workDirectory ("beeper-status-{0}.stdout.txt" -f $label)
                ExpectedHash = [string]$observation.beeper_status_stdout_sha256
            }
            stderr = [pscustomobject]@{
                Path = Join-Path $workDirectory ("beeper-status-{0}.stderr.txt" -f $label)
                ExpectedHash = [string]$observation.beeper_status_stderr_sha256
            }
        }
        $beeperBytesByKind = @{}
        foreach ($entry in $beeperCaptures.GetEnumerator()) {
            $beeperPath = Resolve-ExistingPath -Path $entry.Value.Path `
                -Role ("retained {0} Beeper queue {1} capture" -f $label, $entry.Key) -Leaf
            $beeperStream = New-Object System.IO.FileStream(
                $beeperPath,
                [System.IO.FileMode]::Open,
                [System.IO.FileAccess]::Read,
                [System.IO.FileShare]::Read
            )
            $captureStreams.Add($beeperStream)
            $bytes = Read-PinnedStreamBytes -Stream $beeperStream `
                -MaximumBytes $script:MaximumCaptureBytes
            if ((Get-BytesSha256 -Bytes $bytes) -cne [string]$entry.Value.ExpectedHash) {
                throw "Receipt $label Beeper queue $($entry.Key) capture differs from its recorded hash."
            }
            $beeperBytesByKind[[string]$entry.Key] = $bytes
        }
        if (([byte[]]$beeperBytesByKind.stderr).Length -ne 0) {
            throw "Receipt $label Beeper queue stderr is not empty."
        }
        $beeperText = $script:StrictUtf8.GetString([byte[]]$beeperBytesByKind.stdout)
        Assert-UniqueJsonObjectKeys -Json $beeperText -Role "P0-B Beeper queue status $label"
        try {
            $beeperStatus = $beeperText | Microsoft.PowerShell.Utility\ConvertFrom-Json -ErrorAction Stop
        } catch {
            throw "Receipt $label Beeper queue capture is not valid JSON."
        }
        if (-not (Test-ExactJsonPropertySet -Value $beeperStatus -Names @(
                'claimed', 'beeper_host_id', 'ok', 'pending', 'registered', 'beeper_thread_id',
                'dial_generation', 'dial_inflight', 'dial_lease_remaining_seconds'
            )) -or
            [int]$observation.beeper_status_exit_code -ne 0 -or
            -not (Test-JsonBooleanValue -Value $observation.beeper_queue_idle -Expected $true) -or
            -not (Test-JsonBooleanValue -Value $beeperStatus.ok -Expected $true) -or
            -not (Test-JsonIntegerZero -Value $beeperStatus.pending) -or
            -not (Test-JsonIntegerZero -Value $beeperStatus.claimed) -or
            -not (Test-JsonBooleanValue -Value $beeperStatus.dial_inflight -Expected $false) -or
            $null -ne $beeperStatus.dial_lease_remaining_seconds) {
            throw "Receipt $label Beeper queue capture is not an exact idle checkpoint."
        }
    }

    $preTime = [DateTimeOffset]::Parse([string]$receipt.bridge_stopped_receipt.pre.observed_at_utc)
    $startedTime = [DateTimeOffset]::Parse([string]$receipt.execution.started_at_utc)
    $finishedTime = [DateTimeOffset]::Parse([string]$receipt.execution.finished_at_utc)
    $postTime = [DateTimeOffset]::Parse([string]$receipt.bridge_stopped_receipt.post.observed_at_utc)
    $finalTime = [DateTimeOffset]::Parse([string]$receipt.bridge_stopped_receipt.final.observed_at_utc)
    $createdTime = [DateTimeOffset]::Parse([string]$receipt.created_at_utc)
    $maximumExecutionSeconds = [int]$receipt.execution.timeout_seconds +
        [int]$receipt.execution.capture_close_wait_seconds + 5
    if ($preTime -gt $startedTime -or $startedTime -gt $finishedTime -or
        $finishedTime -gt $postTime -or $postTime -gt $finalTime -or
        $finalTime -gt $createdTime -or
        ($finishedTime - $startedTime).TotalSeconds -gt $maximumExecutionSeconds) {
        throw 'Receipt timestamps are unordered or exceed the bounded execution window.'
    }

    $finalRuntimeRecheck = Get-BoundedRuntimeSnapshot -Project $project -Runtime $runtime
    if ($finalRuntimeRecheck.ManifestSha256 -cne $currentRuntime.ManifestSha256 -or
        $finalRuntimeRecheck.PathCount -ne $currentRuntime.PathCount) {
        throw 'Current bounded runtime/control state changed during evidence validation.'
    }
    $finalAuditJson = @(& $auditScript -DesktopRoot $desktop -HarnessRoot $harness) -join "`n"
    $finalAudit = $finalAuditJson | Microsoft.PowerShell.Utility\ConvertFrom-Json
    if ([string]$finalAudit.status -cne 'pass' -or
        (Get-ObjectSha256 -Object $finalAudit) -cne $currentAuditSha256) {
        throw 'Current audited source changed during evidence validation.'
    }

    [pscustomobject][ordered]@{
        validation_schema_version = 2
        status = 'pass'
        evidence_file = [System.IO.Path]::GetFileName($evidence)
        evidence_sha256 = $evidenceSha256
        source_manifest_sha256 = [string]$currentAudit.source_manifest_sha256
        semantic_relations_validated = $true
        retained_artifacts_pinned = $true
        current_environment_revalidated = $true
        cryptographic_attestation = $false
    } | Microsoft.PowerShell.Utility\ConvertTo-Json -Compress | Write-Output
} finally {
    foreach ($stream in $captureStreams) { $stream.Dispose() }
    if ($null -ne $snapshotState) {
        foreach ($stream in @($snapshotState.Handles)) { $stream.Dispose() }
    }
    if ($null -ne $pythonStream) { $pythonStream.Dispose() }
    if ($null -ne $runnerShellStream) { $runnerShellStream.Dispose() }
    if ($null -ne $schemaStream) { $schemaStream.Dispose() }
    if ($null -ne $evidenceStream) { $evidenceStream.Dispose() }
    if ($lifecycleMutexOwned) {
        try { $lifecycleMutex.ReleaseMutex() } catch [System.ApplicationException] { }
    }
    if ($null -ne $lifecycleMutex) { $lifecycleMutex.Dispose() }
}
