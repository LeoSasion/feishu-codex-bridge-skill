#requires -Version 7.0
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$StartupBundle, [switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$entryMutex = $null
$ownsMutex = $false
$entryLog = $null
try {
    $bundle = [IO.Path]::GetFullPath($StartupBundle).TrimEnd('\')
    $privateRoot = Split-Path -Parent $bundle
    if ((Split-Path -Leaf $privateRoot) -cne '.codex') { throw 'The startup bundle must belong to the selected project.' }
    $projectRoot = Split-Path -Parent $privateRoot
    $expectedScripts = [IO.Path]::GetFullPath((Join-Path $projectRoot 'plugins\feishu-codex-operator\scripts'))
    $entryConfigFile = Join-Path $bundle 'desktop-entry.json'
    $entryConfig = if (Test-Path -LiteralPath $entryConfigFile) {
        if ((Get-Item -LiteralPath $entryConfigFile).Length -gt 16384) { throw 'Invalid desktop entry configuration.' }
        Get-Content -LiteralPath $entryConfigFile -Raw -Encoding utf8 | ConvertFrom-Json
    } else { $null }
    if ([IO.Path]::GetFullPath($PSScriptRoot) -ine $expectedScripts) {
        if ([IO.Path]::GetFullPath($PSScriptRoot) -ine $bundle -or -not $entryConfig -or
            (Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash.ToLowerInvariant() -cne $entryConfig.entry_script_sha256) {
            throw 'The installed entry identity changed.'
        }
    }
    $workflowBundle = $bundle
    $nativeOnly = $false
    if ($entryConfig) {
        if ($entryConfig.schema_version -ne 1 -or $entryConfig.mode -notin @('native','reviewed_startup')) { throw 'Invalid desktop entry mode.' }
        $nativeOnly = $entryConfig.mode -eq 'native'
        if (-not $nativeOnly) {
            $workflowBundle = [IO.Path]::GetFullPath((Join-Path $projectRoot $entryConfig.startup_bundle))
            if ((Split-Path -Parent $workflowBundle) -ine $privateRoot) { throw 'Startup workflow is outside the selected project.' }
        }
    }
    if (-not $CheckOnly) {
        $hash = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($projectRoot.ToLowerInvariant())))
        $entryMutex = [Threading.Mutex]::new($false, ('Local\CodexOperatorDesktopEntry-' + $hash))
        try { $ownsMutex = $entryMutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $ownsMutex = $true }
        if (-not $ownsMutex) { Write-Output 'Codex startup is already in progress.'; exit 0 }
        $logDirectory = Join-Path $bundle 'startup-logs'
        New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
        $entryLog = Join-Path $logDirectory ('unified-' + [Guid]::NewGuid().ToString('N') + '.log')
        New-Item -ItemType File -Path $entryLog | Out-Null
        $entryLog | Set-Content -LiteralPath (Join-Path $bundle 'unified-startup-last-log.txt') -Encoding utf8
    }
    $packages = @(Get-AppxPackage -Name 'OpenAI.Codex')
    if ($packages.Count -ne 1) { throw 'Cannot identify the installed Codex application.' }
    $appRoot = [IO.Path]::GetFullPath($packages[0].InstallLocation).TrimEnd('\') + '\'
    $executable = Join-Path $appRoot 'app\ChatGPT.exe'
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) { throw 'The Codex executable was not found.' }
    $running = $false
    foreach ($process in @(Get-CimInstance Win32_Process -Filter "Name='ChatGPT.exe'")) {
        if ([string]::IsNullOrWhiteSpace($process.ExecutablePath)) { throw 'Cannot identify a running Desktop process.' }
        if ([IO.Path]::GetFullPath($process.ExecutablePath).StartsWith($appRoot, [StringComparison]::OrdinalIgnoreCase)) { $running = $true }
    }
    if ($CheckOnly) {
        [ordered]@{action=$(if ($running) {'open_existing'} elseif ($nativeOnly) {'open_native'} else {'synchronize_then_open'}); configuration_changed=$false} | ConvertTo-Json -Compress
        exit 0
    }
    if (-not $running) {
        foreach ($process in @(Get-CimInstance Win32_Process -Filter "Name='ChatGPT.exe'")) {
            if ([string]::IsNullOrWhiteSpace($process.ExecutablePath)) { throw 'Cannot identify a running Desktop process.' }
            if ([IO.Path]::GetFullPath($process.ExecutablePath).StartsWith($appRoot, [StringComparison]::OrdinalIgnoreCase)) { $running = $true }
        }
    }
    if ($running -or $nativeOnly) {
        # Launch the user's interactive single-instance app to bring it back;
        # no sync, service restart, configuration edit or task is dispatched.
        Start-Process -FilePath $executable -WorkingDirectory (Split-Path -Parent $executable) -WindowStyle Normal
        Write-Output 'Opened the Codex application.'
        exit 0
    }
    $plan = Get-Content -LiteralPath (Join-Path $workflowBundle 'startup-sync-plan.json') -Raw -Encoding utf8 | ConvertFrom-Json
    $startup = Join-Path $workflowBundle 'start-codex-with-lmstudio.ps1'
    if ((Get-FileHash -LiteralPath $startup -Algorithm SHA256).Hash.ToLowerInvariant() -cne $plan.entry_files.'start-codex-with-lmstudio.ps1') {
        throw 'The reviewed startup workflow changed.'
    }
    & $startup *> $entryLog
    if ($LASTEXITCODE -ne 0) { throw 'The startup workflow stopped. No automatic retry was attempted.' }
    exit 0
} catch {
    $message = 'STOPPED: ' + $_.Exception.Message
    if ($entryLog) { $message | Add-Content -LiteralPath $entryLog -Encoding utf8 }
    Write-Output $message
    exit 1
} finally {
    if ($ownsMutex) { $entryMutex.ReleaseMutex() }
    if ($entryMutex) { $entryMutex.Dispose() }
}
