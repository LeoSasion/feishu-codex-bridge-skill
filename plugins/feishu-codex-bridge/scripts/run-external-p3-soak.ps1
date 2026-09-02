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
    [ValidateRange(25, 100)][int]$Iterations = 25,
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
    throw ('P3 soak requires the verified external-supervisor environment. Set ' +
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
            if (!deviceBuffer.ToString().StartsWith(@"\Device\HarddiskVolume", StringComparison.OrdinalIgnoreCase)) {
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

function Get-StreamSha256 {
    param([Parameter(Mandatory = $true)][System.IO.FileStream]$Stream)
    $originalPosition = $Stream.Position
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        $Stream.Position = 0
        return ([System.BitConverter]::ToString($algorithm.ComputeHash($Stream))).Replace('-', '').ToLowerInvariant()
    } finally {
        $Stream.Position = $originalPosition
        $algorithm.Dispose()
    }
}

function Get-CurrentWindowsUserSid {
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    if ($null -eq $identity -or $null -eq $identity.User -or
        [string]::IsNullOrWhiteSpace([string]$identity.User.Value)) {
        throw 'P3 soak could not resolve the current Windows user SID.'
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

function Invoke-CapturedProcess {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][hashtable]$EnvironmentOverrides,
        [Parameter(Mandatory = $true)][string]$CapturePrefix,
        [ValidateRange(1, 1800)][int]$TimeoutSeconds,
        [switch]$DeferCapture
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
        $stdoutSha256 = $null
        $stderrSha256 = $null
        if (-not $DeferCapture) {
            Write-NewUtf8File -Path $stdoutPath -Text $stdout
            Write-NewUtf8File -Path $stderrPath -Text $stderr
            $stdoutSha256 = Get-FileSha256 -Path $stdoutPath
            $stderrSha256 = Get-FileSha256 -Path $stderrPath
        }
        return [pscustomobject]@{
            StartedAt = $startedAt
            FinishedAt = $finishedAt
            ExitCode = $process.ExitCode
            TimedOut = $timedOut
            Stdout = $stdout
            Stderr = $stderr
            StdoutSha256 = $stdoutSha256
            StderrSha256 = $stderrSha256
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

function Get-BridgeObservation {
    param(
        [Parameter(Mandatory = $true)][ValidateSet('pre', 'post', 'final')][string]$Label,
        [Parameter(Mandatory = $true)][string]$ShellExecutable,
        [Parameter(Mandatory = $true)][string]$PythonExecutable,
        [Parameter(Mandatory = $true)][string]$Dispatcher,
        [Parameter(Mandatory = $true)][string]$BeeperHelper,
        [Parameter(Mandatory = $true)][string]$Project,
        [Parameter(Mandatory = $true)][string]$Runtime,
        [Parameter(Mandatory = $true)][string]$WorkDirectory
    )
    $arguments = @(
        '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
        '-File', $Dispatcher,
        'bridge', 'status', '-ProjectRoot', $Project, '-Json'
    )
    $statusCapturePrefix = Join-Path $WorkDirectory ("status-{0}" -f $Label)
    $capture = Invoke-CapturedProcess -Executable $ShellExecutable -Arguments $arguments `
        -WorkingDirectory $Project -EnvironmentOverrides @{} `
        -CapturePrefix $statusCapturePrefix -TimeoutSeconds 30 -DeferCapture
    if ($capture.TimedOut -or $capture.ExitCode -ne 0 -or
        -not [string]::IsNullOrWhiteSpace([string]$capture.Stderr)) {
        throw "Bridge status $Label failed or produced stderr."
    }
    $status = Convert-SingleJsonObject -Text $capture.Stdout -Role "Bridge status $Label"
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
        -not (Test-JsonIntegerZero -Value $queueCounts.reply_pending)) {
        throw "Bridge status $Label did not report the exact stopped and idle runtime."
    }

    $pidPath = Join-Path $Runtime 'bridge.pid'
    $pidFileState = if (Test-Path -LiteralPath $pidPath -PathType Leaf) { 'stale' } else { 'absent' }
    $statusPidStateMatches = if ($pidFileState -ceq 'absent') {
        [string]$status.runtime.pid_file_state -ceq 'absent'
    } else {
        [string]$status.runtime.pid_file_state -cin @('invalid', 'stale_process_absent', 'stale_foreign_process')
    }
    if (-not $statusPidStateMatches -or
        -not (Test-JsonBooleanValue -Value $status.runtime.pid_file_present `
            -Expected ($pidFileState -ceq 'stale')) -or
        $null -ne $status.runtime.pid) {
        throw "Bridge status $Label PID-file facts differ from the exact stopped checkpoint."
    }
    $bridgeScript = Get-FullPath -Path (Join-Path $Runtime 'bridge.py')
    try {
        $matching = @(
            CimCmdlets\Get-CimInstance Win32_Process -ErrorAction Stop |
                Where-Object {
                    $_.CommandLine -and
                    ([string]$_.CommandLine).IndexOf(
                        $bridgeScript,
                        [System.StringComparison]::OrdinalIgnoreCase
                    ) -ge 0
                }
        )
    } catch {
        throw "Bridge process scan $Label was unavailable."
    }
    if ($matching.Count -ne 0) {
        throw "Bridge is not stopped at the $Label checkpoint."
    }

    $beeperArguments = @(
        '-S', '-B', $BeeperHelper,
        '--runtime-dir', $Runtime,
        '--queue-namespace', 'beeper',
        'status'
    )
    $beeperCapturePrefix = Join-Path $WorkDirectory ("beeper-status-{0}" -f $Label)
    $beeperCapture = Invoke-CapturedProcess -Executable $PythonExecutable `
        -Arguments $beeperArguments -WorkingDirectory (Split-Path -Parent $BeeperHelper) `
        -EnvironmentOverrides @{} -CapturePrefix $beeperCapturePrefix `
        -TimeoutSeconds 30 -DeferCapture
    if ($beeperCapture.TimedOut -or $beeperCapture.ExitCode -ne 0 -or
        -not [string]::IsNullOrWhiteSpace([string]$beeperCapture.Stderr)) {
        throw "Beeper queue status $Label failed or produced stderr."
    }
    $beeperStatus = Convert-SingleJsonObject -Text $beeperCapture.Stdout `
        -Role "Beeper queue status $Label"
    $expectedBeeperProperties = @(
        'claimed', 'beeper_host_id', 'ok', 'pending', 'registered', 'beeper_thread_id',
        'dial_generation', 'dial_inflight', 'dial_lease_remaining_seconds'
    )
    if (-not (Test-ExactJsonPropertySet -Value $beeperStatus -Names $expectedBeeperProperties) -or
        -not (Test-JsonBooleanValue -Value $beeperStatus.ok -Expected $true) -or
        -not (Test-JsonIntegerZero -Value $beeperStatus.pending) -or
        -not (Test-JsonIntegerZero -Value $beeperStatus.claimed) -or
        -not (Test-JsonBooleanValue -Value $beeperStatus.dial_inflight -Expected $false) -or
        $null -ne $beeperStatus.dial_lease_remaining_seconds) {
        throw "Beeper queue status $Label did not report the exact answer-free idle contract."
    }
    $statusStdoutPath = $statusCapturePrefix + '.stdout.txt'
    $statusStderrPath = $statusCapturePrefix + '.stderr.txt'
    $beeperStdoutPath = $beeperCapturePrefix + '.stdout.txt'
    $beeperStderrPath = $beeperCapturePrefix + '.stderr.txt'
    Write-NewUtf8File -Path $statusStdoutPath -Text $capture.Stdout
    Write-NewUtf8File -Path $statusStderrPath -Text $capture.Stderr
    Write-NewUtf8File -Path $beeperStdoutPath -Text $beeperCapture.Stdout
    Write-NewUtf8File -Path $beeperStderrPath -Text $beeperCapture.Stderr
    return [pscustomobject][ordered]@{
        observed_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
        capture_contract = 'answer_free_idle_status_v1'
        state = 'stopped'
        status_argv = @($ShellExecutable) + @($arguments)
        status_exit_code = 0
        status_stdout_sha256 = Get-FileSha256 -Path $statusStdoutPath
        status_stderr_sha256 = Get-FileSha256 -Path $statusStderrPath
        health_snapshot_present = $true
        health_snapshot_valid = $true
        health_status = 'stopped'
        health_event_consumer = $false
        health_active_turns = 0
        health_dial_inflight = $false
        health_dial_lease_remaining_seconds = $null
        health_queue_counts = [ordered]@{
            queued = 0
            running = 0
            control_sending = 0
            reply_pending = 0
        }
        pid_file_state = $pidFileState
        pid_process_alive = $false
        matching_bridge_process_count = 0
        beeper_status_argv = @($PythonExecutable) + @($beeperArguments)
        beeper_status_exit_code = 0
        beeper_status_stdout_sha256 = Get-FileSha256 -Path $beeperStdoutPath
        beeper_status_stderr_sha256 = Get-FileSha256 -Path $beeperStderrPath
        beeper_source_role = 'p0_source_snapshot'
        beeper_runtime_role = 'project_runtime'
        beeper_queue_cli_namespace = 'beeper'
        beeper_ok = $true
        beeper_pending = 0
        beeper_claimed = 0
        beeper_dial_inflight = $false
        beeper_dial_lease_remaining_seconds = $null
    }
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
if ([int]$p0Receipt.schema_version -ne 2 -or
    [string]$p0Receipt.evidence_kind -cne 'feishu-codex-bridge.p0b.external-test' -or
    [string]$p0Receipt.execution.runner_status -cne 'pass') {
    throw 'P3 soak requires one passing P0-B v2 receipt.'
}

$receiptId = [guid]::NewGuid().ToString('D').ToLowerInvariant()
$workDirectoryLeaf = 's-' + (Get-StringSha256 -Value $receiptId).Substring(0, 8)
$workDirectory = Join-Path $externalWork $workDirectoryLeaf
$testTemp = Join-Path $workDirectory 'test-temp'
New-Item -ItemType Directory -Path $workDirectory, $testTemp -ErrorAction Stop | Out-Null
foreach ($runtimePath in @($workDirectory, $testTemp)) {
    [FeishuCodexBridge.ExternalP3SoakPath]::AssertOrdinaryLocalDrive($runtimePath)
    Assert-NoReparsePathChain -Path $runtimePath
    if (-not (Test-IsWithinRoot -Candidate $runtimePath -Root $externalWork)) {
        throw 'P3 soak runtime path escaped its unique external work boundary.'
    }
}
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
if ([int]$preValidation.validation_schema_version -ne 2 -or
    [string]$preValidation.status -cne 'pass' -or
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
$lifecycleMutex = $null
$lifecycleMutexOwned = $false
$installedHookStream = $null
$installedStopHookStream = $null
$hooksConfigStream = $null
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

$sourceStartHookRecords = @($desktopP0Components[0].files | Where-Object {
    [string]$_.path -ceq 'scripts/start-feishu-codex-bridge.ps1'
})
$sourceStopHookRecords = @($desktopP0Components[0].files | Where-Object {
    [string]$_.path -ceq 'scripts/stop-feishu-codex-bridge.ps1'
})
if ($sourceStartHookRecords.Count -ne 1 -or $sourceStopHookRecords.Count -ne 1) {
    throw 'P3 soak retained source does not bind one exact start and stop lifecycle hook.'
}
$sourceStartHook = Get-FullPath -Path (Join-Path $snapshotRoot 'scripts\start-feishu-codex-bridge.ps1')
$sourceStopHook = Get-FullPath -Path (Join-Path $snapshotRoot 'scripts\stop-feishu-codex-bridge.ps1')
$sourceStartHookHash = Get-FileSha256 -Path $sourceStartHook
$sourceStopHookHash = Get-FileSha256 -Path $sourceStopHook
if ($sourceStartHookHash -cne [string]$sourceStartHookRecords[0].sha256 -or
    $sourceStopHookHash -cne [string]$sourceStopHookRecords[0].sha256) {
    throw 'P3 soak retained lifecycle hooks differ from the bound P0 snapshot records.'
}

$currentUserSid = Get-CurrentWindowsUserSid
$lifecycleMutexName = Get-LifecycleMutexName -Project $project -UserSid $currentUserSid
$lifecycleMutex = [System.Threading.Mutex]::new($false, $lifecycleMutexName)
try {
    $lifecycleMutexOwned = $lifecycleMutex.WaitOne(0)
} catch [System.Threading.AbandonedMutexException] {
    $lifecycleMutexOwned = $true
}
if (-not $lifecycleMutexOwned) {
    throw 'Bridge lifecycle mutex is already held; P3 soak requires an uninterrupted stopped window.'
}

$runtime = Get-FullPath -Path (Join-Path $project '.codex\feishu-bridge')
$dispatcher = Get-FullPath -Path (Join-Path $snapshotRoot 'scripts\feishu-codex-bridge.ps1')
$beeperHelper = Get-FullPath -Path (Join-Path $snapshotRoot 'scripts\beeper_queue_cli.py')
$installedStartHook = Get-FullPath -Path (Join-Path $project '.codex\hooks\start-feishu-codex-bridge.ps1')
$installedStopHook = Get-FullPath -Path (Join-Path $project '.codex\hooks\stop-feishu-codex-bridge.ps1')
$installedHooksConfig = Get-FullPath -Path (Join-Path $project '.codex\hooks.json')
foreach ($requiredLifecycleFile in @(
        $dispatcher,
        $beeperHelper,
        $sourceStartHook,
        $sourceStopHook,
        $installedStartHook,
        $installedStopHook,
        $installedHooksConfig
    )) {
    if (-not (Test-Path -LiteralPath $requiredLifecycleFile -PathType Leaf)) {
        throw 'P3 soak required lifecycle file is missing.'
    }
    Assert-NoReparsePathChain -Path $requiredLifecycleFile
}
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
$installedHooksConfigHash = Get-StreamSha256 -Stream $hooksConfigStream
$p0Lifecycle = $p0Receipt.bridge_stopped_receipt.lifecycle_mutex
$hookComparison = (
    $installedStartHookHash -ceq $sourceStartHookHash -and
    $installedStopHookHash -ceq $sourceStopHookHash -and
    $installedHookStream.Length -eq [long]$sourceStartHookRecords[0].size_bytes -and
    $installedStopHookStream.Length -eq [long]$sourceStopHookRecords[0].size_bytes
)
$hookRegistrationValidated = (
    $hookComparison -and
    [bool]$p0Lifecycle.hook_registration_validated -and
    [string]$p0Lifecycle.hook_comparison -ceq 'equal' -and
    [string]$p0Lifecycle.lifecycle_exclusion_scope -ceq 'registered_start_and_stop_hooks' -and
    [bool]$p0Lifecycle.held_for_complete_window -and
    [string]$p0Lifecycle.name_sha256 -ceq (Get-StringSha256 -Value $lifecycleMutexName) -and
    [string]$p0Lifecycle.owner_sid_sha256 -ceq (Get-StringSha256 -Value $currentUserSid) -and
    [string]$p0Lifecycle.source_hook_sha256 -ceq $sourceStartHookHash -and
    [string]$p0Lifecycle.installed_hook_sha256 -ceq $installedStartHookHash -and
    [string]$p0Lifecycle.source_stop_hook_sha256 -ceq $sourceStopHookHash -and
    [string]$p0Lifecycle.installed_stop_hook_sha256 -ceq $installedStopHookHash -and
    [string]$p0Lifecycle.hooks_config_sha256 -ceq $installedHooksConfigHash
)
if (-not $hookRegistrationValidated) {
    throw 'P3 soak lifecycle mutex or installed Hook registration differs from validated P0-B evidence.'
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
$preObservation = Get-BridgeObservation -Label 'pre' -ShellExecutable $pwsh `
    -PythonExecutable $python -Dispatcher $dispatcher -BeeperHelper $beeperHelper `
    -Project $project -Runtime $runtime -WorkDirectory $workDirectory
$runnerCapture = $null
$postObservation = $null
try {
    $runnerCapture = Invoke-CapturedProcess -Executable $python -Arguments $runnerArguments `
        -WorkingDirectory $snapshotRoot -EnvironmentOverrides @{
            FEISHU_BRIDGE_EXTERNAL_TEST_RUNNER = '1'
            FEISHU_BRIDGE_EXTERNAL_P3_SOAK = '1'
            FEISHU_BRIDGE_TEST_TMP = $testTemp
            PYTHONDONTWRITEBYTECODE = '1'
            Path = $childPath
        } -CapturePrefix (Join-Path $workDirectory 'soak-runner') -TimeoutSeconds $TimeoutSeconds
} finally {
    $postObservation = Get-BridgeObservation -Label 'post' -ShellExecutable $pwsh `
        -PythonExecutable $python -Dispatcher $dispatcher -BeeperHelper $beeperHelper `
        -Project $project -Runtime $runtime -WorkDirectory $workDirectory
}
if ($null -eq $runnerCapture) {
    throw 'P3 soak runner did not return a captured process result.'
}
if ($runnerCapture.TimedOut) { throw "P3 soak exceeded its $TimeoutSeconds second hard timeout." }
if (-not (Test-Path -LiteralPath $resultPath -PathType Leaf)) {
    throw 'P3 soak runner did not publish its structured result.'
}
$result = Get-Content -LiteralPath $resultPath -Raw -Encoding utf8 |
    Microsoft.PowerShell.Utility\ConvertFrom-Json -ErrorAction Stop
if ($runnerCapture.ExitCode -ne 0 -or [int]$result.schema_version -ne 2 -or
    [string]$result.runner_status -cne 'pass') {
    $failedIds = @(@($result.failure_test_ids) + @($result.error_test_ids) | Select-Object -Unique)
    throw ('P3 soak runner failed.' + $(if ($failedIds.Count) { ' Test IDs: ' + ($failedIds -join ', ') } else { '' }))
}
if (@($runnerCapture.Stdout -split '\r?\n' | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }).Count -ne 0) {
    throw 'P3 soak runner stdout must remain empty; progress belongs on retained stderr.'
}

$p0EvidenceRehashedAfterRun = (Get-FileSha256 -Path $p0Evidence) -ceq $ExpectedP0EvidenceSha256
if (-not $p0EvidenceRehashedAfterRun) {
    throw 'P3 soak bound P0 evidence changed during the run.'
}
$sourceUnchanged = $true
foreach ($fileRecord in @($desktopP0Components[0].files)) {
    $snapshotPath = Get-FullPath -Path (
        Join-Path $snapshotRoot (([string]$fileRecord.path).Replace('/', '\'))
    )
    if ((Get-FileSha256 -Path $snapshotPath) -cne [string]$fileRecord.sha256 -or
        (Microsoft.PowerShell.Management\Get-Item -LiteralPath $snapshotPath).Length -ne
            [long]$fileRecord.size_bytes) {
        $sourceUnchanged = $false
        break
    }
}
if (-not $sourceUnchanged) { throw 'P3 soak retained source snapshot changed during the run.' }
$finalObservation = Get-BridgeObservation -Label 'final' -ShellExecutable $pwsh `
    -PythonExecutable $python -Dispatcher $dispatcher -BeeperHelper $beeperHelper `
    -Project $project -Runtime $runtime -WorkDirectory $workDirectory
$bridgeStopped = @(@($preObservation, $postObservation, $finalObservation) | Where-Object {
    [string]$_.state -ceq 'stopped' -and
    [int]$_.status_exit_code -eq 0 -and
    [string]$_.health_status -ceq 'stopped' -and
    -not [bool]$_.health_event_consumer -and
    [int]$_.health_active_turns -eq 0 -and
    -not [bool]$_.health_dial_inflight -and
    $null -eq $_.health_dial_lease_remaining_seconds -and
    [int]$_.health_queue_counts.queued -eq 0 -and
    [int]$_.health_queue_counts.running -eq 0 -and
    [int]$_.health_queue_counts.control_sending -eq 0 -and
    [int]$_.health_queue_counts.reply_pending -eq 0 -and
    -not [bool]$_.pid_process_alive -and
    [int]$_.matching_bridge_process_count -eq 0 -and
    [int]$_.beeper_status_exit_code -eq 0 -and
    [bool]$_.beeper_ok -and
    [int]$_.beeper_pending -eq 0 -and
    [int]$_.beeper_claimed -eq 0 -and
    -not [bool]$_.beeper_dial_inflight -and
    $null -eq $_.beeper_dial_lease_remaining_seconds
}).Count -eq 3
$p0PreValidated = (
    [string]$preValidation.status -ceq 'pass' -and
    [bool]$preValidation.current_environment_revalidated -and
    [string]$preValidation.source_manifest_sha256 -ceq $sourceManifest
)
$snapshotFilesPinned = $snapshotPins.Count -eq [int]$desktopP0Components[0].file_count

$evidenceName = 'p3-soak-v2-' + $receiptId + '.json'
$evidencePath = Join-Path $evidenceRoot $evidenceName
$receipt = [ordered]@{
    schema_version = 2
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
    bridge_stopped_receipt = [ordered]@{
        pre = $preObservation
        post = $postObservation
        final = $finalObservation
        lifecycle_mutex = [ordered]@{
            name_sha256 = Get-StringSha256 -Value $lifecycleMutexName
            namespace = 'global_current_user'
            owner_sid_sha256 = Get-StringSha256 -Value $currentUserSid
            source_hook_sha256 = $sourceStartHookHash
            installed_hook_sha256 = $installedStartHookHash
            source_stop_hook_sha256 = $sourceStopHookHash
            installed_stop_hook_sha256 = $installedStopHookHash
            hooks_config_sha256 = $installedHooksConfigHash
            hook_comparison = $(if ($hookComparison) { 'equal' } else { 'different' })
            hook_registration_validated = [bool]$hookRegistrationValidated
            lifecycle_exclusion_scope = 'registered_start_and_stop_hooks'
            held_for_complete_window = [bool]$lifecycleMutexOwned
        }
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
        bridge_stopped = [bool]$bridgeStopped
        runner_surface = $RunnerSurface
        process_ancestry_depth = [int]$processGuard.Depth
        process_ancestry_complete = [bool]$processGuard.Complete
        process_ancestry_termination = [string]$processGuard.Termination
        codex_ancestor_match_count = [int]$processGuard.CodexMatchCount
        p0_pre_validated = [bool]$p0PreValidated
        p0_evidence_rehashed_after_run = [bool]$p0EvidenceRehashedAfterRun
        source_snapshot_reused = $true
        snapshot_files_pinned = [bool]$snapshotFilesPinned
        snapshot_file_count = [int]$snapshotPins.Count
        source_unchanged = [bool]$sourceUnchanged
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
    schema_version = 2
    runner_status = 'pass'
} | Microsoft.PowerShell.Utility\ConvertTo-Json -Compress | Write-Output
} finally {
    if ($null -ne $installedHookStream) { $installedHookStream.Dispose() }
    if ($null -ne $installedStopHookStream) { $installedStopHookStream.Dispose() }
    if ($null -ne $hooksConfigStream) { $hooksConfigStream.Dispose() }
    foreach ($pin in $snapshotPins) { $pin.Dispose() }
    if ($lifecycleMutexOwned) {
        try { $lifecycleMutex.ReleaseMutex() } catch [System.ApplicationException] { }
    }
    if ($null -ne $lifecycleMutex) { $lifecycleMutex.Dispose() }
}
