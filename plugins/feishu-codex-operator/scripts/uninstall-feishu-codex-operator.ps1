#requires -Version 7.0
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$ProjectRoot, [switch]$Apply,
      [string]$CodexConfig, [int]$RouterPort = 4317)
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'operator_installation.psm1') -Force -DisableNameChecking
$project = [IO.Path]::GetFullPath((Resolve-Path -LiteralPath $ProjectRoot).Path).TrimEnd('\')
$entryMutex = $null
$ownsEntryMutex = $false
if ($Apply) {
    $entryHash = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($project.ToLowerInvariant())))
    $entryMutex = [Threading.Mutex]::new($false,('Local\CodexOperatorDesktopEntry-' + $entryHash))
    try { $ownsEntryMutex = $entryMutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $ownsEntryMutex = $true }
    if (-not $ownsEntryMutex) { $entryMutex.Dispose(); throw 'Desktop startup is in progress; uninstall did not retry.' }
}
try {
$runtime = Join-Path $project '.codex/feishu-codex-operator-runtime'
$bundle = Join-Path $project '.codex/operator-desktop-entry'
if (-not $CodexConfig) {
    $configurationRoot = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path ([Environment]::GetFolderPath('UserProfile')) '.codex' }
    $CodexConfig = Join-Path $configurationRoot 'config.toml'
}
Assert-OperatorPlainPath $runtime
Assert-OperatorPlainPath $bundle
$links = @(Get-OperatorDesktopPaths -IncludeLegacy)
$restore = Get-OperatorRestorePlan $project $links
$processes = @(Get-CimInstance Win32_Process)
$operatorPath = Join-Path $runtime 'operator_main.py'
$running = @($processes | Where-Object { $_.Name -match '^python' -and $_.CommandLine -and
    $_.CommandLine.Replace('/','\').IndexOf($operatorPath,[StringComparison]::OrdinalIgnoreCase) -ge 0 })
$python = (Get-Command python -ErrorAction Stop).Source
$helper = Join-Path $PSScriptRoot 'operator_uninstall.py'
$observed = & $python -B $helper inspect --project-root $project --codex-config $CodexConfig --port $RouterPort
if ($LASTEXITCODE -ne 0) { throw 'Uninstall routing preflight failed; nothing was restored.' }
$routing = $observed | ConvertFrom-Json
$blocks = @()
if ($restore.conflicts) { $blocks += 'managed_files_or_backups_changed' }
if ($running.Count) { $blocks += 'stop_exact_operator_before_uninstall' }
if ($routing.pending_callbacks) { $blocks += 'callbacks_pending' }
if ($routing.active_router_requests) { $blocks += 'router_requests_active' }
$launchers = @('Codex拓展入口.exe','Codex.exe' | ForEach-Object { Join-Path $bundle $_ } | Where-Object { Test-Path -LiteralPath $_ })
if ($launchers.Count -gt 1) { $blocks += 'ambiguous_native_fallback_builds' }
foreach ($launcher in $launchers) {
    $buildFile = Join-Path $bundle 'launcher-manifest.json'
    Assert-OperatorPlainPath $buildFile
    if (-not (Test-Path -LiteralPath $buildFile)) { $blocks += 'native_fallback_build_record_missing' }
    else {
        $build = Get-Content -LiteralPath $buildFile -Raw -Encoding utf8 | ConvertFrom-Json
        if ($build.schema_version -ne 1 -or $build.native_fallback -ne 'native-only-v1' -or
            $build.binary_sha256 -cne (Get-OperatorFingerprint $launcher)) { $blocks += 'native_fallback_build_changed' }
    }
}
if ($routing.owned_router_entry) {
    $packages = @(Get-AppxPackage -Name OpenAI.Codex)
    if ($packages.Count -ne 1) { $blocks += 'desktop_identity_unknown' }
    else {
        $prefix = [IO.Path]::GetFullPath($packages[0].InstallLocation).TrimEnd('\') + '\'
        if (@($processes | Where-Object { $_.Name -eq 'ChatGPT.exe' -and (-not $_.ExecutablePath -or
                [IO.Path]::GetFullPath($_.ExecutablePath).StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase)) }).Count) {
            $blocks += 'close_desktop_before_restoring_active_router_config'
        }
    }
}
# Old installations lack an authoritative original-file journal. Do not guess
# at their backups or claim all integrations are recoverable automatically.
if (Test-Path -LiteralPath $runtime) {
    $ownerFile = Join-Path $project '.codex/operator-installation/runtime-owner.json'
    Assert-OperatorPlainPath $ownerFile
    if (-not (Test-Path -LiteralPath $ownerFile)) { $blocks += 'legacy_installation_requires_reviewed_ownership_migration' }
    else {
        $owner = Get-Content -LiteralPath $ownerFile -Raw -Encoding utf8 | ConvertFrom-Json
        if ($owner.schema_version -ne 1 -or $owner.project -ine $project -or $owner.fresh_install -ne $true) {
            $blocks += 'runtime_ownership_record_invalid'
        }
    }
}
$preview = [ordered]@{mode='preview'; ready=($blocks.Count -eq 0); blockers=$blocks; files=$restore.items;
    routing=$routing; data_retained=$true; taskbar_native_launcher_retained=$true; desktop_plugin_removal='separate_user_action'}
if (-not $Apply) { $preview | ConvertTo-Json -Depth 10; exit 0 }
if ($blocks.Count) { $preview | ConvertTo-Json -Depth 10; throw 'Uninstall stopped at preflight; no restore was attempted.' }
& $python -B $helper detach --project-root $project --codex-config $CodexConfig --port $RouterPort
if ($LASTEXITCODE -ne 0) { throw 'Routing detachment stopped; no files were restored or requests replayed.' }
if ($launchers.Count -eq 1) {
    Write-OperatorAtomicBytes (Join-Path $bundle 'native-only') ([Text.Encoding]::ASCII.GetBytes('native-only'))
}
Restore-OperatorManagedFiles $project $links | Out-Null
$archive = $null
if (Test-Path -LiteralPath $runtime) {
    $remaining = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^python' -and $_.CommandLine -and
        $_.CommandLine.Replace('/','\').IndexOf(($runtime+'\'),[StringComparison]::OrdinalIgnoreCase) -ge 0 })
    if ($remaining.Count) { throw 'A runtime process remains; files and data were preserved.' }
    $archiveRoot = [IO.Path]::GetFullPath((Join-Path $project '.codex/operator-uninstalled'))
    $archive = [IO.Path]::GetFullPath((Join-Path $archiveRoot ([Guid]::NewGuid().ToString('N'))))
    if ($runtime -ine [IO.Path]::GetFullPath((Join-Path $project '.codex/feishu-codex-operator-runtime')) -or
        -not $archive.StartsWith(($archiveRoot+'\'),[StringComparison]::OrdinalIgnoreCase)) { throw 'Archive path mismatch.' }
    Assert-OperatorPlainPath $archive
    New-Item -ItemType Directory -Force -Path $archiveRoot | Out-Null
    Move-Item -LiteralPath $runtime -Destination $archive
}
$receipt = [ordered]@{schema_version=1; project=$project; uninstalled=$true; restored_files=$restore.owned_files; data_archive=$archive;
    native_launcher_retained=$true; plugin_ui_removal_pending=$true; tasks_replayed=0}
Write-OperatorAtomicBytes (Join-Path $project '.codex/operator-installation/uninstall-receipt.json') ([Text.UTF8Encoding]::new($false).GetBytes(($receipt | ConvertTo-Json)))
$receipt | ConvertTo-Json
} finally {
    if ($ownsEntryMutex) { $entryMutex.ReleaseMutex() }
    if ($entryMutex) { $entryMutex.Dispose() }
}
