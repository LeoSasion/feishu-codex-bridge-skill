[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('prepare', 'observe', 'rollback')]
    [string]$Action,

    [Parameter(Mandatory = $true)][string]$DesktopRoot,
    [Parameter(Mandatory = $true)][string]$LabProjectRoot,
    [Parameter(Mandatory = $true)][string]$PythonExecutable,
    [string]$Alpha2RuntimeRoot,
    [string]$LegacyHooksRoot,
    [switch]$ExternalMigrationAcknowledged
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

if (-not $ExternalMigrationAcknowledged -or $env:FEISHU_BRIDGE_EXTERNAL_MIGRATION -ne '1') {
    throw ('P1 migration work may run only from an explicitly acknowledged external terminal. ' +
        'Set FEISHU_BRIDGE_EXTERNAL_MIGRATION=1 and pass -ExternalMigrationAcknowledged.')
}
if ($env:CODEX_BRIDGE_CHILD -eq '1') {
    throw 'P1 migration work refuses to run from a Codex child process.'
}
if (-not [System.Runtime.InteropServices.RuntimeInformation]::IsOSPlatform(
        [System.Runtime.InteropServices.OSPlatform]::Windows
    )) {
    throw 'The P1 migration lab currently supports Windows only.'
}
if ([string]$PSVersionTable.PSEdition -cne 'Core' -or
    [version]$PSVersionTable.PSVersion -lt [version]'7.4') {
    throw 'The P1 migration lab requires PowerShell 7.4+.'
}

$script:Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$script:RuntimeFiles = @(
    'bridge.py',
    'router_queue.py',
    'bridge_core/__init__.py',
    'bridge_core/config.py',
    'bridge_core/codex_client.py',
    'bridge_core/desktop_router.py',
    'bridge_core/lark.py',
    'bridge_core/project_routing.py',
    'bridge_core/runtime.py',
    'bridge_core/state.py'
)
$script:PendingRequestId = '11111111111111111111111111111111'
$script:TargetThreadId = 'p1-target-thread-canary'
$script:RouterThreadId = 'p1-gateway-thread-canary'
$script:SyntheticOwnerOpenId = 'ou_' + 'p1migrationcanary'
$script:UnrelatedHookMarker = 'P1_UNRELATED_HOOK_CANARY'

function Get-NormalizedPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    $full = [System.IO.Path]::GetFullPath($Path)
    $root = [System.IO.Path]::GetPathRoot($full)
    if ($full.StartsWith('\\', [System.StringComparison]::Ordinal) -or
        $root -notmatch '^[A-Za-z]:[\\/]$') {
        throw 'P1 paths must use an ordinary local DOS drive path.'
    }
    if ($full.Equals($root, [System.StringComparison]::OrdinalIgnoreCase)) { return $root }
    return $full.TrimEnd([char[]]@('\', '/'))
}

function Assert-NoReparsePathChain {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Role,
        [switch]$AllowMissingLeaf
    )
    $full = Get-NormalizedPath -Path $Path
    $root = [System.IO.Path]::GetPathRoot($full)
    $current = $root
    $segments = @(
        $full.Substring($root.Length).Split(
            [char[]]@([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar),
            [System.StringSplitOptions]::RemoveEmptyEntries
        )
    )
    for ($index = 0; $index -lt $segments.Count; $index += 1) {
        $current = Join-Path $current $segments[$index]
        $item = Get-Item -LiteralPath $current -Force -ErrorAction SilentlyContinue
        if ($null -eq $item) {
            if ($AllowMissingLeaf -and $index -eq $segments.Count - 1) { return }
            throw "$Role contains a missing path segment: $current"
        }
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "$Role must not contain a reparse point."
        }
    }
}

function Resolve-ExistingLocalPath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Role,
        [switch]$Leaf
    )
    $normalized = Get-NormalizedPath -Path $Path
    Assert-NoReparsePathChain -Path $normalized -Role $Role
    $resolved = (Resolve-Path -LiteralPath $normalized -ErrorAction Stop).Path
    if ($Leaf -and -not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
        throw "$Role must be a file."
    }
    if (-not $Leaf -and -not (Test-Path -LiteralPath $resolved -PathType Container)) {
        throw "$Role must be a directory."
    }
    return Get-NormalizedPath -Path $resolved
}

function Test-IsWithinRoot {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Candidate
    )
    $rootPath = (Get-NormalizedPath -Path $Root).TrimEnd([char[]]@('\', '/'))
    $candidatePath = Get-NormalizedPath -Path $Candidate
    return $candidatePath.Equals($rootPath, [System.StringComparison]::OrdinalIgnoreCase) -or
        $candidatePath.StartsWith($rootPath + '\', [System.StringComparison]::OrdinalIgnoreCase)
}

function Assert-ExternalProcess {
    $visited = New-Object 'System.Collections.Generic.HashSet[int]'
    $currentId = [int]$PID
    $depth = 0
    while ($currentId -gt 0 -and $depth -lt 64) {
        if (-not $visited.Add($currentId)) { throw 'P1 process ancestry contained a cycle.' }
        $record = Get-CimInstance Win32_Process -Filter ("ProcessId = {0}" -f $currentId) `
            -ErrorAction Stop
        if ($null -eq $record) {
            if ($depth -lt 2) { throw 'P1 could not inspect one live external parent.' }
            return
        }
        $name = [string]$record.Name
        $path = [string]$record.ExecutablePath
        if ($name -match '^(?i:codex)(?:\.exe)?$' -or
            $path -match '(?i)[\\/]OpenAI\.Codex(?:_|[\\/])') {
            throw 'P1 refuses a Codex Desktop or Codex CLI ancestor.'
        }
        $depth += 1
        $currentId = [int]$record.ParentProcessId
    }
    if ($depth -ge 64) { throw 'P1 process ancestry exceeded its bounded depth.' }
}

function Write-NewUtf8Json {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][object]$Value
    )
    if (Test-Path -LiteralPath $Path) { throw "Create-new JSON target already exists: $Path" }
    $json = $Value | ConvertTo-Json -Depth 20
    $stream = New-Object System.IO.FileStream(
        $Path,
        [System.IO.FileMode]::CreateNew,
        [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::None
    )
    try {
        $bytes = $script:Utf8NoBom.GetBytes($json + "`n")
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush()
    } finally {
        $stream.Dispose()
    }
}

function Write-NewUtf8Text {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Text
    )
    if (Test-Path -LiteralPath $Path) { throw "Create-new text target already exists: $Path" }
    $stream = New-Object System.IO.FileStream(
        $Path,
        [System.IO.FileMode]::CreateNew,
        [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::None
    )
    try {
        $bytes = $script:Utf8NoBom.GetBytes($Text)
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush()
    } finally {
        $stream.Dispose()
    }
}

function Get-FileSha256 {
    param([Parameter(Mandatory = $true)][string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-DirectoryManifest {
    param([Parameter(Mandatory = $true)][string]$Root)
    $resolved = Resolve-ExistingLocalPath -Path $Root -Role 'manifest root'
    $records = @(
        Get-ChildItem -LiteralPath $resolved -File -Recurse -Force |
            Where-Object { $_.FullName -notmatch '(?i)[\\/]__pycache__[\\/]' } |
            ForEach-Object {
                Assert-NoReparsePathChain -Path $_.FullName -Role 'manifest file'
                [pscustomobject][ordered]@{
                    path = $_.FullName.Substring($resolved.Length).TrimStart('\', '/').Replace('\', '/')
                    sha256 = Get-FileSha256 -Path $_.FullName
                    size_bytes = [long]$_.Length
                }
            } |
            Sort-Object path
    )
    $canonical = ($records | ForEach-Object { "{0}`t{1}`t{2}" -f $_.path, $_.sha256, $_.size_bytes }) -join "`n"
    $hash = [System.Security.Cryptography.SHA256]::HashData(
        $script:Utf8NoBom.GetBytes($canonical + "`n")
    )
    return [pscustomobject][ordered]@{
        file_count = $records.Count
        manifest_sha256 = [System.Convert]::ToHexString($hash).ToLowerInvariant()
        files = $records
    }
}

function Get-BridgeVersion {
    param([Parameter(Mandatory = $true)][string]$RuntimeRoot)
    $configPath = Join-Path $RuntimeRoot 'bridge_core\config.py'
    $text = Get-Content -LiteralPath $configPath -Raw -Encoding utf8
    if ($text -notmatch 'BRIDGE_VERSION\s*=\s*["'']([^"'']+)["'']') {
        throw 'Lab runtime has no readable BRIDGE_VERSION.'
    }
    return [string]$Matches[1]
}

function Assert-ListenerStopped {
    param([Parameter(Mandatory = $true)][string]$RuntimeRoot)
    $bridgeScript = Get-NormalizedPath -Path (Join-Path $RuntimeRoot 'bridge.py')
    $matches = @(
        Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
            $_.CommandLine -and
            ([string]$_.CommandLine).IndexOf($bridgeScript, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
        }
    )
    if ($matches.Count -ne 0) { throw 'P1 lab Listener process is not stopped.' }
}

function Invoke-QueueStatus {
    param(
        [Parameter(Mandatory = $true)][string]$Python,
        [Parameter(Mandatory = $true)][string]$RuntimeRoot
    )
    $helper = Join-Path $RuntimeRoot 'router_queue.py'
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $Python
    $startInfo.UseShellExecute = $false
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $startInfo.CreateNoWindow = $true
    $bootstrap = (
        'import runpy,sys;' +
        'runtime=sys.argv[1];helper=sys.argv[2];' +
        'sys.path.insert(0,runtime);' +
        'sys.argv=[helper,*sys.argv[3:]];' +
        'runpy.run_path(helper,run_name="__main__")'
    )
    foreach ($argument in @(
        '-I', '-S', '-B', '-c', $bootstrap, $RuntimeRoot, $helper,
        '--runtime-dir', $RuntimeRoot, 'status'
    )) {
        [void]$startInfo.ArgumentList.Add($argument)
    }
    foreach ($key in @($startInfo.Environment.Keys)) {
        if ($key -match '^(?i:CODEX_BRIDGE_|FEISHU_|LARK|PYTHON)') {
            [void]$startInfo.Environment.Remove($key)
        }
    }
    $startInfo.Environment['PYTHONDONTWRITEBYTECODE'] = '1'
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    try {
        if (-not $process.Start()) { throw 'Lab queue status process did not start.' }
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(30000)) {
            try { $process.Kill($true) } catch {}
            $process.WaitForExit()
            throw 'Lab queue initialization exceeded 30 seconds.'
        }
        $process.WaitForExit()
        $stdout = $stdoutTask.GetAwaiter().GetResult()
        $stderr = $stderrTask.GetAwaiter().GetResult()
        if ($process.ExitCode -ne 0) {
            throw "Lab queue initialization failed with exit code $($process.ExitCode): $($stderr.Trim())"
        }
        return $stdout | ConvertFrom-Json -ErrorAction Stop
    } finally {
        $process.Dispose()
    }
}

function Assert-PsfPython {
    param([Parameter(Mandatory = $true)][string]$Python)
    $signature = Get-AuthenticodeSignature -LiteralPath $Python
    if ($signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid -or
        $null -eq $signature.SignerCertificate -or
        [string]$signature.SignerCertificate.Subject -notmatch '(?i)Python Software Foundation') {
        throw 'PythonExecutable must be a valid Python Software Foundation-signed python.exe.'
    }
}

function Get-QueueFileCounts {
    param([Parameter(Mandatory = $true)][string]$RuntimeRoot)
    $routerRoot = Join-Path $RuntimeRoot 'desktop-router'
    $pending = @(Get-ChildItem -LiteralPath (Join-Path $routerRoot 'pending') -File -Filter '*.json' -ErrorAction SilentlyContinue).Count
    $claimed = @(Get-ChildItem -LiteralPath (Join-Path $routerRoot 'claimed') -File -Filter '*.json' -ErrorAction SilentlyContinue).Count
    return [pscustomobject][ordered]@{ pending = $pending; claimed = $claimed }
}

function Test-LockedAccessConfiguration {
    param([Parameter(Mandatory = $true)][string]$Path)
    $values = @{}
    foreach ($line in @(Get-Content -LiteralPath $Path -Encoding utf8)) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#')) { continue }
        $separator = $trimmed.IndexOf('=')
        if ($separator -le 0) { return $false }
        $name = $trimmed.Substring(0, $separator).Trim()
        if ($values.ContainsKey($name)) { return $false }
        $values[$name] = $trimmed.Substring($separator + 1).Trim()
    }
    return [string]$values['CODEX_BRIDGE_ACCESS_MODE'] -ceq 'locked' -and
        [string]$values['CODEX_BRIDGE_OWNER_OPEN_ID'] -match '^ou_[A-Za-z0-9_-]+$'
}

function Test-UnrelatedHookPreserved {
    param(
        [Parameter(Mandatory = $true)][string]$HooksConfig,
        [Parameter(Mandatory = $true)][string]$UnrelatedTarget
    )
    try {
        $config = Get-Content -LiteralPath $HooksConfig -Raw -Encoding utf8 | ConvertFrom-Json
        foreach ($group in @($config.hooks.SessionStart)) {
            foreach ($hook in @($group.hooks)) {
                if ([string]$hook.statusMessage -ceq $script:UnrelatedHookMarker -and
                    [string]$hook.command -match [regex]::Escape($UnrelatedTarget) -and
                    [string]$hook.commandWindows -match [regex]::Escape($UnrelatedTarget)) {
                    return $true
                }
            }
        }
        return $false
    } catch {
        return $false
    }
}

function Test-BridgeHookRegistration {
    param(
        [Parameter(Mandatory = $true)][string]$HooksConfig,
        [Parameter(Mandatory = $true)][string]$StartHook,
        [Parameter(Mandatory = $true)][string]$StopHook
    )
    try {
        $config = Get-Content -LiteralPath $HooksConfig -Raw -Encoding utf8 | ConvertFrom-Json
        foreach ($specification in @(
            [pscustomobject]@{ Event = 'SessionStart'; Script = $StartHook; Matcher = 'startup|resume' },
            [pscustomobject]@{ Event = 'SessionEnd'; Script = $StopHook; Matcher = '' }
        )) {
            $eventProperty = $config.hooks.PSObject.Properties[$specification.Event]
            if ($null -eq $eventProperty -or -not ($eventProperty.Value -is [System.Array])) {
                return $false
            }
            $bridgeHandlerCount = 0
            foreach ($group in @($eventProperty.Value)) {
                $hooksProperty = $group.PSObject.Properties['hooks']
                if ($null -eq $hooksProperty -or -not ($hooksProperty.Value -is [System.Array])) {
                    return $false
                }
                foreach ($hook in @($hooksProperty.Value)) {
                    $command = [string]$hook.command
                    $commandWindows = [string]$hook.commandWindows
                    $isBridgeHandler = (
                        [string]$hook.type -ceq 'command' -and
                        $command.IndexOf($specification.Script, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -and
                        $commandWindows.IndexOf($specification.Script, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -and
                        $command.IndexOf('-HookInvocation', [System.StringComparison]::Ordinal) -ge 0 -and
                        $commandWindows.IndexOf('-HookInvocation', [System.StringComparison]::Ordinal) -ge 0
                    )
                    if (-not $isBridgeHandler) { continue }
                    $bridgeHandlerCount += 1
                    $matcherProperty = $group.PSObject.Properties['matcher']
                    if ($specification.Matcher) {
                        if ($null -eq $matcherProperty -or
                            [string]$matcherProperty.Value -cne $specification.Matcher) {
                            return $false
                        }
                    } elseif ($null -ne $matcherProperty -and
                        -not [string]::IsNullOrWhiteSpace([string]$matcherProperty.Value)) {
                        return $false
                    }
                }
            }
            if ($bridgeHandlerCount -ne 1) { return $false }
        }
        return $true
    } catch {
        return $false
    }
}

function Get-PreservedState {
    param([Parameter(Mandatory = $true)][string]$ProjectRoot)
    $paths = [ordered]@{
        bridge_env = Join-Path $ProjectRoot '.codex\feishu-bridge\bridge.env'
        sessions = Join-Path $ProjectRoot '.codex\feishu-bridge\sessions.json'
        pending_canary = Join-Path $ProjectRoot (
            '.codex\feishu-bridge\desktop-router\pending\' + $script:PendingRequestId + '.json'
        )
    }
    $result = [ordered]@{}
    foreach ($entry in $paths.GetEnumerator()) {
        if (-not (Test-Path -LiteralPath $entry.Value -PathType Leaf)) {
            throw "P1 preserved-state canary is missing: $($entry.Value)"
        }
        $result[$entry.Key] = Get-FileSha256 -Path $entry.Value
    }
    return [pscustomobject]$result
}

function Test-ManifestValid {
    param(
        [Parameter(Mandatory = $true)][string]$RuntimeRoot,
        [Parameter(Mandatory = $true)][string]$StartHook,
        [Parameter(Mandatory = $true)][string]$StopHook
    )
    $manifestPath = Join-Path $RuntimeRoot 'runtime-manifest.json'
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { return $false }
    try {
        $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding utf8 | ConvertFrom-Json
        if ([int]$manifest.schema_version -ne 1 -or
            [string]$manifest.bridge_version -cne '4.2.0-alpha.4' -or
            [string]$manifest.start_hook_sha256 -cne (Get-FileSha256 -Path $StartHook) -or
            [string]$manifest.stop_hook_sha256 -cne (Get-FileSha256 -Path $StopHook)) {
            return $false
        }
        foreach ($relative in $script:RuntimeFiles) {
            $property = $manifest.code_files.PSObject.Properties[$relative]
            $target = Join-Path $RuntimeRoot ($relative.Replace('/', '\'))
            if ($null -eq $property -or -not (Test-Path -LiteralPath $target -PathType Leaf) -or
                [string]$property.Value -cne (Get-FileSha256 -Path $target)) {
                return $false
            }
        }
        return $true
    } catch {
        return $false
    }
}

function Get-LabObservation {
    param(
        [Parameter(Mandatory = $true)][string]$Desktop,
        [Parameter(Mandatory = $true)][string]$Project
    )
    $controlRoot = Join-Path $Project '.p1-control'
    $metadataPath = Join-Path $controlRoot 'baseline.json'
    if (-not (Test-Path -LiteralPath $metadataPath -PathType Leaf)) {
        throw 'P1 lab baseline metadata is missing.'
    }
    $metadata = Get-Content -LiteralPath $metadataPath -Raw -Encoding utf8 | ConvertFrom-Json
    $runtime = Join-Path $Project '.codex\feishu-bridge'
    $startHook = Join-Path $Project '.codex\hooks\start-feishu-codex-bridge.ps1'
    $stopHook = Join-Path $Project '.codex\hooks\stop-feishu-codex-bridge.ps1'
    $hooksConfig = Join-Path $Project '.codex\hooks.json'
    Assert-ListenerStopped -RuntimeRoot $runtime
    $version = Get-BridgeVersion -RuntimeRoot $runtime
    $startHash = Get-FileSha256 -Path $startHook
    $stopHash = Get-FileSha256 -Path $stopHook
    $sourceStartHash = Get-FileSha256 -Path (Join-Path $Desktop 'scripts\start-feishu-codex-bridge.ps1')
    $sourceStopHash = Get-FileSha256 -Path (Join-Path $Desktop 'scripts\stop-feishu-codex-bridge.ps1')
    $hooksCurrent = $startHash -ceq $sourceStartHash -and $stopHash -ceq $sourceStopHash
    $hooksLegacy = $startHash -ceq [string]$metadata.legacy_start_hook_sha256 -and
        $stopHash -ceq [string]$metadata.legacy_stop_hook_sha256
    $manifestPath = Join-Path $runtime 'runtime-manifest.json'
    $manifestPresent = Test-Path -LiteralPath $manifestPath -PathType Leaf
    $manifestValid = Test-ManifestValid -RuntimeRoot $runtime -StartHook $startHook -StopHook $stopHook
    $preserved = Get-PreservedState -ProjectRoot $Project
    $unrelatedPreserved = Test-UnrelatedHookPreserved -HooksConfig $hooksConfig `
        -UnrelatedTarget (Join-Path $controlRoot 'unrelated-hook.ps1')
    $bridgeHookRegistrationValid = Test-BridgeHookRegistration -HooksConfig $hooksConfig `
        -StartHook $startHook -StopHook $stopHook
    $lockedAccessPreserved = (
        [string]$preserved.bridge_env -ceq [string]$metadata.preserved_state.bridge_env -and
        (Test-LockedAccessConfiguration -Path (Join-Path $runtime 'bridge.env'))
    )
    $queueBindingPreserved = (
        [string]$preserved.sessions -ceq [string]$metadata.preserved_state.sessions -and
        [string]$preserved.pending_canary -ceq [string]$metadata.preserved_state.pending_canary
    )
    $queue = Get-QueueFileCounts -RuntimeRoot $runtime
    $quarantines = @(
        Get-ChildItem -LiteralPath $controlRoot -Directory -Filter 'after-upgrade-*' -ErrorAction SilentlyContinue
    )
    $rollbackGuard = Join-Path $controlRoot 'rollback-intent.json'
    $rollbackGuardPresent = Test-Path -LiteralPath $rollbackGuard -PathType Leaf
    $phase = 'unknown'
    if ($version -ceq '4.2.0-alpha.2' -and $hooksLegacy -and -not $manifestPresent) {
        if ($quarantines.Count -eq 0 -and -not $rollbackGuardPresent) {
            $phase = 'prepared'
        } elseif ($quarantines.Count -eq 1 -and $rollbackGuardPresent) {
            $phase = 'rolled_back'
        }
    } elseif ($version -ceq '4.2.0-alpha.2' -and $hooksCurrent -and
        -not $manifestPresent -and $quarantines.Count -eq 0 -and -not $rollbackGuardPresent) {
        $phase = 'hooks_refreshed'
    } elseif ($version -ceq '4.2.0-alpha.4' -and $hooksCurrent -and $manifestValid -and
        $quarantines.Count -eq 0 -and -not $rollbackGuardPresent) {
        $phase = 'upgraded'
    }
    return [pscustomobject][ordered]@{
        schema_version = 1
        status = $(if ($phase -ne 'unknown' -and $lockedAccessPreserved -and
                $queueBindingPreserved -and $unrelatedPreserved -and
                $bridgeHookRegistrationValid) { 'pass' } else { 'fail' })
        phase = $phase
        listener_stopped = $true
        runtime_version = $version
        hooks_legacy = $hooksLegacy
        hooks_current = $hooksCurrent
        runtime_manifest_present = $manifestPresent
        runtime_manifest_valid = $manifestValid
        locked_access_preserved = $lockedAccessPreserved
        queue_binding_canaries_preserved = $queueBindingPreserved
        unrelated_hook_preserved = $unrelatedPreserved
        bridge_hook_registration_valid = $bridgeHookRegistrationValid
        queue_pending_count = [int]$queue.pending
        queue_claimed_count = [int]$queue.claimed
        rollback_quarantine_count = $quarantines.Count
        rollback_guard_present = $rollbackGuardPresent
        baseline_manifest_sha256 = [string]$metadata.baseline_manifest_sha256
    }
}

Assert-ExternalProcess
$desktop = Resolve-ExistingLocalPath -Path $DesktopRoot -Role 'Desktop source root'
$python = Resolve-ExistingLocalPath -Path $PythonExecutable -Role 'Python executable' -Leaf
if ([System.IO.Path]::GetFileName($python) -ine 'python.exe') {
    throw 'PythonExecutable must name python.exe.'
}
Assert-PsfPython -Python $python
$lab = Get-NormalizedPath -Path $LabProjectRoot
if ((Test-IsWithinRoot -Root $desktop -Candidate $lab) -or
    (Test-IsWithinRoot -Root $lab -Candidate $desktop)) {
    throw 'P1 lab project must be separate from the Desktop Skill source root.'
}

switch ($Action.ToLowerInvariant()) {
    'prepare' {
        if (Test-Path -LiteralPath $lab) { throw 'P1 prepare requires a create-new lab project path.' }
        Assert-NoReparsePathChain -Path $lab -Role 'lab project path' -AllowMissingLeaf
        [void](Resolve-ExistingLocalPath -Path (Split-Path -Parent $lab) -Role 'lab parent')
        $build = $lab + '.preparing'
        if (Test-Path -LiteralPath $build) {
            throw 'P1 preparation staging path already exists; retain it for diagnosis and choose a new lab root.'
        }
        Assert-NoReparsePathChain -Path $build -Role 'lab preparation staging path' -AllowMissingLeaf
        if ([string]::IsNullOrWhiteSpace($Alpha2RuntimeRoot) -or
            [string]::IsNullOrWhiteSpace($LegacyHooksRoot)) {
            throw 'P1 prepare requires Alpha2RuntimeRoot and LegacyHooksRoot.'
        }
        $alpha2 = Resolve-ExistingLocalPath -Path $Alpha2RuntimeRoot -Role 'alpha.2 runtime root'
        $legacyHooks = Resolve-ExistingLocalPath -Path $LegacyHooksRoot -Role 'legacy hooks root'
        if ((Get-BridgeVersion -RuntimeRoot $alpha2) -cne '4.2.0-alpha.2') {
            throw 'P1 baseline runtime is not version 4.2.0-alpha.2.'
        }
        foreach ($relative in $script:RuntimeFiles) {
            if (-not (Test-Path -LiteralPath (Join-Path $alpha2 ($relative.Replace('/', '\'))) -PathType Leaf)) {
                throw "P1 alpha.2 baseline is missing: $relative"
            }
        }
        $legacyStart = Join-Path $legacyHooks 'start-feishu-codex-bridge.ps1'
        $legacyStop = Join-Path $legacyHooks 'stop-feishu-codex-bridge.ps1'
        foreach ($path in @($legacyStart, $legacyStop)) {
            if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
                throw "P1 legacy hook is missing: $path"
            }
        }
        [void](New-Item -ItemType Directory -Path $build)
        $runtime = Join-Path $build '.codex\feishu-bridge'
        $hooks = Join-Path $build '.codex\hooks'
        $control = Join-Path $build '.p1-control'
        New-Item -ItemType Directory -Path $runtime, $hooks, $control | Out-Null
        foreach ($relative in $script:RuntimeFiles) {
            $source = Join-Path $alpha2 ($relative.Replace('/', '\'))
            $target = Join-Path $runtime ($relative.Replace('/', '\'))
            New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
            Copy-Item -LiteralPath $source -Destination $target
        }
        Copy-Item -LiteralPath $legacyStart -Destination (Join-Path $hooks 'start-feishu-codex-bridge.ps1')
        Copy-Item -LiteralPath $legacyStop -Destination (Join-Path $hooks 'stop-feishu-codex-bridge.ps1')

        $envText = @"
CODEX_BRIDGE_ACCESS_MODE=locked
CODEX_BRIDGE_OWNER_OPEN_ID=$($script:SyntheticOwnerOpenId)
CODEX_BRIDGE_EVENT_READY_TIMEOUT=15
CODEX_BRIDGE_MAX_CONCURRENT_TURNS=2
CODEX_BRIDGE_ROUTER_TIMEOUT=3600
CODEX_BRIDGE_ROUTER_HEARTBEAT_TTL=90
CODEX_BRIDGE_ROUTER_CLAIM_TTL=7200
CODEX_BRIDGE_ROUTER_RETENTION_HOURS=168
CODEX_BRIDGE_ROUTER_WAKE_TTL=180
CODEX_BRIDGE_GATEWAY_SCHEDULER_TTL=300
CODEX_BRIDGE_ROUTER_GRACE_MAX_SECONDS=30
CODEX_BRIDGE_ALLOW_PROJECT_CREATE=0
CODEX_BRIDGE_LIFECYCLE_MODE=hooks
"@
        Write-NewUtf8Text -Path (Join-Path $runtime 'bridge.env') -Text ($envText.TrimStart() + "`n")
        Write-NewUtf8Text -Path (Join-Path $control 'unrelated-hook.ps1') `
            -Text ("# $($script:UnrelatedHookMarker)`n")

        $startTarget = Join-Path $lab '.codex\hooks\start-feishu-codex-bridge.ps1'
        $stopTarget = Join-Path $lab '.codex\hooks\stop-feishu-codex-bridge.ps1'
        $unrelatedTarget = Join-Path $lab '.p1-control\unrelated-hook.ps1'
        $hooksConfig = [ordered]@{
            description = 'P1 isolated alpha.2 migration fixture'
            hooks = [ordered]@{
                SessionStart = @(
                    [ordered]@{
                        matcher = 'startup|resume'
                        hooks = @([ordered]@{
                            type = 'command'
                            command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$startTarget`" -HookInvocation"
                            commandWindows = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$startTarget`" -HookInvocation"
                            timeout = 10
                            statusMessage = 'Legacy P1 bridge start hook'
                        })
                    },
                    [ordered]@{
                        matcher = 'startup'
                        hooks = @([ordered]@{
                            type = 'command'
                            command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$unrelatedTarget`" # $($script:UnrelatedHookMarker)"
                            commandWindows = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$unrelatedTarget`" # $($script:UnrelatedHookMarker)"
                            timeout = 2
                            statusMessage = $script:UnrelatedHookMarker
                        })
                    }
                )
                SessionEnd = @([ordered]@{
                    hooks = @([ordered]@{
                        type = 'command'
                        command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$stopTarget`" -HookInvocation"
                        commandWindows = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$stopTarget`" -HookInvocation"
                        timeout = 3
                        statusMessage = 'Legacy P1 bridge stop hook'
                    })
                })
            }
        }
        Write-NewUtf8Json -Path (Join-Path $build '.codex\hooks.json') -Value $hooksConfig
        [void](Invoke-QueueStatus -Python $python -RuntimeRoot $runtime)

        $pendingPath = Join-Path $runtime (
            'desktop-router\pending\' + $script:PendingRequestId + '.json'
        )
        Write-NewUtf8Json -Path $pendingPath -Value ([ordered]@{
            schema_version = 1
            request_id = $script:PendingRequestId
            operation = 'inspect_thread'
            payload = [ordered]@{ target_thread_id = $script:TargetThreadId }
            idempotency_key = 'p1-migration-queue-canary'
            retry_generation = 0
            fingerprint = ('a' * 64)
            created_at = 1
        })
        Write-NewUtf8Json -Path (Join-Path $runtime 'sessions.json') -Value ([ordered]@{
            version = 3
            sessions = [ordered]@{
                p1_scope_canary = [ordered]@{
                    thread_id = $script:TargetThreadId
                    router_thread_id = $script:RouterThreadId
                    session_owner = 'desktop-router'
                }
            }
        })
        $preserved = Get-PreservedState -ProjectRoot $build
        $baselineRoot = Join-Path $control 'baseline'
        New-Item -ItemType Directory -Path $baselineRoot | Out-Null
        Copy-Item -LiteralPath (Join-Path $build '.codex') `
            -Destination (Join-Path $baselineRoot '.codex') -Recurse
        $baselineManifest = Get-DirectoryManifest -Root (Join-Path $baselineRoot '.codex')
        Write-NewUtf8Json -Path (Join-Path $control 'baseline.json') -Value ([ordered]@{
            schema_version = 1
            source_version = '4.2.0-alpha.2'
            lab_project_root = $lab
            alpha2_runtime_root = $alpha2
            legacy_hooks_root = $legacyHooks
            legacy_start_hook_sha256 = Get-FileSha256 -Path $legacyStart
            legacy_stop_hook_sha256 = Get-FileSha256 -Path $legacyStop
            baseline_manifest_sha256 = $baselineManifest.manifest_sha256
            baseline_file_count = $baselineManifest.file_count
            preserved_state = $preserved
            prepared_at_utc = [DateTimeOffset]::UtcNow.UtcDateTime.ToString('o')
        })
        if (Test-Path -LiteralPath $lab) {
            throw 'P1 final lab path appeared while preparation was running; staging was retained.'
        }
        [System.IO.Directory]::Move($build, $lab)
        Get-LabObservation -Desktop $desktop -Project $lab |
            ConvertTo-Json -Depth 10 -Compress | Write-Output
    }
    'observe' {
        $lab = Resolve-ExistingLocalPath -Path $lab -Role 'P1 lab project'
        Get-LabObservation -Desktop $desktop -Project $lab |
            ConvertTo-Json -Depth 10 -Compress | Write-Output
    }
    'rollback' {
        $lab = Resolve-ExistingLocalPath -Path $lab -Role 'P1 lab project'
        $control = Join-Path $lab '.p1-control'
        $metadataPath = Join-Path $control 'baseline.json'
        $baselineCodex = Resolve-ExistingLocalPath -Path (Join-Path $control 'baseline\.codex') `
            -Role 'P1 rollback baseline'
        $metadata = Get-Content -LiteralPath $metadataPath -Raw -Encoding utf8 | ConvertFrom-Json
        $runtime = Join-Path $lab '.codex\feishu-bridge'
        Assert-ListenerStopped -RuntimeRoot $runtime
        $preRollback = Get-LabObservation -Desktop $desktop -Project $lab
        if ([string]$preRollback.status -cne 'pass' -or
            [string]$preRollback.phase -cne 'upgraded' -or
            [int]$preRollback.rollback_quarantine_count -ne 0 -or
            [bool]$preRollback.rollback_guard_present) {
            throw 'P1 ordinary rollback requires one clean upgraded phase and no prior rollback intent or quarantine.'
        }
        $rollbackGuard = Join-Path $control 'rollback-intent.json'
        Write-NewUtf8Json -Path $rollbackGuard -Value ([ordered]@{
            schema_version = 1
            source_phase = 'upgraded'
            baseline_manifest_sha256 = [string]$metadata.baseline_manifest_sha256
            started_at_utc = [DateTimeOffset]::UtcNow.UtcDateTime.ToString('o')
        })
        $candidate = Join-Path $control 'restore-candidate'
        if (Test-Path -LiteralPath $candidate) { throw 'P1 rollback candidate already exists.' }
        $quarantine = Join-Path $control ('after-upgrade-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
        if (Test-Path -LiteralPath $quarantine) { throw 'P1 rollback quarantine already exists.' }
        Copy-Item -LiteralPath $baselineCodex -Destination $candidate -Recurse
        $candidateManifest = Get-DirectoryManifest -Root $candidate
        if ([string]$candidateManifest.manifest_sha256 -cne [string]$metadata.baseline_manifest_sha256 -or
            [int]$candidateManifest.file_count -ne [int]$metadata.baseline_file_count) {
            throw 'P1 rollback candidate differs from the pinned alpha.2 baseline.'
        }
        $liveCodex = Join-Path $lab '.codex'
        try {
            Move-Item -LiteralPath $liveCodex -Destination $quarantine
            Move-Item -LiteralPath $candidate -Destination $liveCodex
        } catch {
            if (-not (Test-Path -LiteralPath $liveCodex) -and
                (Test-Path -LiteralPath $quarantine)) {
                Move-Item -LiteralPath $quarantine -Destination $liveCodex
            }
            throw
        }
        Get-LabObservation -Desktop $desktop -Project $lab |
            ConvertTo-Json -Depth 10 -Compress | Write-Output
    }
}
