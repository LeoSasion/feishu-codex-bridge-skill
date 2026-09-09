#requires -Version 7.0
[CmdletBinding()]
param([ValidateSet('preview','install','restore')][string]$Action = 'preview',
      [Parameter(Mandatory=$true)][string]$ProjectRoot,
      [string]$StartupBundle, [switch]$Library)
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'operator_installation.psm1') -Force -DisableNameChecking

function Show-OperatorInstallationNotice {
    @'
初始化说明：将配置当前用户桌面和开始菜单的 Codex 启动入口，并先保存原快捷方式。
Codex 已运行时只打开现有窗口；本地模型同步仅在对应功能已配置且 Codex 完全退出后的启动时执行。
原生任务栏固定项可能需要手动重新固定。不会修改应用程序本体、默认模型、审批或沙箱设置。
安全卸载会按安装记录恢复入口、项目规则和 Hooks，遇到后续修改则停止并保留原件。
请先执行 operator uninstall 预览，再执行 operator uninstall -Apply 完成恢复，最后在 Desktop 移除插件。
直接删除插件不能保证恢复项目配置。任务、模型文件、凭据和业务数据会保留；卸载后保留一个只打开原生 Codex 的小启动程序，避免任务栏固定项失效。
'@ | Write-Output
}

function Install-OperatorDesktopEntry {
    param([string]$ProjectRoot, [string]$StartupBundle)
    $project = [IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\')
    $bundle = Join-Path $project '.codex/operator-desktop-entry'
    Assert-OperatorPlainPath $bundle
    $targets = @(Get-OperatorDesktopPaths)
    Start-OperatorInstallation -ProjectRoot $project -LinkPaths $targets
    $state = Get-OperatorOwnership $project
    $nativeMarker = Join-Path $bundle 'native-only'
    $reactivate = Test-Path -LiteralPath $nativeMarker
    if ($reactivate) {
        Assert-OperatorPlainPath $nativeMarker
        if ($state.reactivate_native_launcher -ne $true) { throw 'Complete uninstall before reinstalling the launcher.' }
        $buildFile = Join-Path $bundle 'launcher-manifest.json'
        Assert-OperatorPlainPath $buildFile
        $oldBuild = Get-Content -LiteralPath $buildFile -Raw -Encoding utf8 | ConvertFrom-Json
        if ($oldBuild.schema_version -ne 1 -or $oldBuild.native_fallback -ne 'native-only-v1' -or
            $oldBuild.binary_sha256 -cne (Get-OperatorFingerprint (Join-Path $bundle 'Codex.exe'))) {
            throw 'The retained native launcher changed; reinstall stopped.'
        }
    }
    $packages = @(Get-AppxPackage -Name OpenAI.Codex)
    if ($packages.Count -ne 1) { throw 'Cannot identify the installed Codex package.' }
    $nativeRoot = [IO.Path]::GetFullPath($packages[0].InstallLocation).TrimEnd('\') + '\'
    $nativeExe = Join-Path $nativeRoot 'app/ChatGPT.exe'
    $shell = New-Object -ComObject WScript.Shell
    # Never adopt an unrelated shortcut merely because its filename says Codex.
    foreach ($target in $targets) {
        if (-not (Test-Path -LiteralPath $target)) { continue }
        $fingerprint = Get-OperatorFingerprint $target
        if ($state.entries.Contains($target) -and $state.entries[$target].after -ceq $fingerprint) { continue }
        $link = $shell.CreateShortcut($target)
        $native = $link.TargetPath -and [IO.Path]::GetFullPath($link.TargetPath).StartsWith($nativeRoot,[StringComparison]::OrdinalIgnoreCase)
        if (-not $native) { throw 'An existing Codex shortcut needs a reviewed ownership migration.' }
    }
    $configuration = @{schema_version=1; mode='native'}
    $configurationPath = Join-Path $bundle 'desktop-entry.json'
    if (-not $reactivate -and (Test-Path -LiteralPath $configurationPath)) {
        $configuration = Get-Content -LiteralPath $configurationPath -Raw -Encoding utf8 | ConvertFrom-Json -AsHashtable
        if ($configuration.schema_version -ne 1 -or $configuration.mode -notin @('native','reviewed_startup')) { throw 'Desktop entry configuration changed.' }
    }
    if ($StartupBundle) {
        $workflow = [IO.Path]::GetFullPath($StartupBundle).TrimEnd('\')
        if ((Split-Path -Parent $workflow) -ine (Join-Path $project '.codex')) { throw 'Startup workflow must belong to this project.' }
        $plan = Get-Content -LiteralPath (Join-Path $workflow 'startup-sync-plan.json') -Raw -Encoding utf8 | ConvertFrom-Json
        if ((Get-OperatorFingerprint (Join-Path $workflow 'start-codex-with-lmstudio.ps1')) -cne $plan.entry_files.'start-codex-with-lmstudio.ps1') {
            throw 'Startup workflow digest changed.'
        }
        $configuration.mode = 'reviewed_startup'
        $configuration.startup_bundle = [IO.Path]::GetRelativePath($project,$workflow)
    }
    $hash = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($project.ToLowerInvariant())))
    $mutex = [Threading.Mutex]::new($false,('Local\CodexOperatorDesktopEntry-' + $hash))
    $owns = $false
    try {
        try { $owns = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $owns = $true }
        if (-not $owns) { throw 'Desktop entry is busy; setup did not retry.' }
        New-Item -ItemType Directory -Force -Path $bundle | Out-Null
        $compiler = Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        if (-not (Test-Path -LiteralPath $compiler)) { throw 'Windows C# compiler is unavailable.' }
        $candidate = Join-Path $bundle ('candidate-' + [Guid]::NewGuid().ToString('N') + '.exe')
        $icon = Join-Path $bundle 'Codex.ico'
        if (-not (Test-Path -LiteralPath $icon)) {
            Add-Type -AssemblyName System.Drawing
            $nativeIcon = [Drawing.Icon]::ExtractAssociatedIcon($nativeExe)
            $stream = [IO.File]::Open($icon,[IO.FileMode]::CreateNew)
            try { $nativeIcon.Save($stream) } finally { $stream.Dispose(); $nativeIcon.Dispose() }
        }
        & $compiler /nologo /target:winexe /optimize+ /platform:anycpu /reference:System.Windows.Forms.dll "/win32icon:$icon" "/out:$candidate" (Join-Path $PSScriptRoot 'operator_desktop_entry.cs')
        if ($LASTEXITCODE -ne 0) { throw 'Desktop entry build failed.' }
        $entry = Join-Path $bundle 'operator_desktop_entry.ps1'
        Write-OperatorAtomicBytes $entry ([IO.File]::ReadAllBytes((Join-Path $PSScriptRoot 'operator_desktop_entry.ps1')))
        $configuration.entry_script_sha256 = Get-OperatorFingerprint $entry
        Write-OperatorAtomicBytes $configurationPath ([Text.UTF8Encoding]::new($false).GetBytes(($configuration | ConvertTo-Json)))
        $executable = Join-Path $bundle 'Codex.exe'
        [IO.File]::Move($candidate,$executable,$true)
        $build = @{schema_version=1; native_fallback='native-only-v1'; binary_sha256=(Get-OperatorFingerprint $executable);
                   entry_script_sha256=$configuration.entry_script_sha256}
        Write-OperatorAtomicBytes (Join-Path $bundle 'launcher-manifest.json') ([Text.UTF8Encoding]::new($false).GetBytes(($build | ConvertTo-Json)))
        foreach ($target in $targets) {
            # The old named synchronization shortcut is updated only when present.
            if ($target -eq $targets[2] -and -not (Test-Path -LiteralPath $target)) { continue }
            $temporary = Join-Path $bundle ('shortcut-' + [Guid]::NewGuid().ToString('N') + '.lnk')
            try {
                $link = $shell.CreateShortcut($temporary)
                $link.TargetPath = $executable; $link.WorkingDirectory = $bundle
                $link.IconLocation = $icon + ',0'; $link.WindowStyle = 7
                $link.Description = '打开 Codex；已配置的本地模型同步在完全退出后启动时执行。'
                $link.Save()
                Set-OperatorManagedFile -ProjectRoot $project -Path $target -Bytes ([IO.File]::ReadAllBytes($temporary)) -LinkPaths $targets
            } finally { if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary } }
        }
        if ($reactivate) { Remove-Item -LiteralPath $nativeMarker }
        return [pscustomobject]@{entry_installed=$true; mode=$configuration.mode; taskbar_pin_automatic=$false}
    } finally { if ($owns) { $mutex.ReleaseMutex() }; $mutex.Dispose() }
}

if ($Library) { return }
switch ($Action) {
    'preview' { Get-OperatorRestorePlan $ProjectRoot @(Get-OperatorDesktopPaths) | ConvertTo-Json -Depth 6 }
    'install' { Show-OperatorInstallationNotice; Install-OperatorDesktopEntry $ProjectRoot $StartupBundle | ConvertTo-Json }
    'restore' { throw 'Use operator uninstall so routing is detached before restoring the entry.' }
}
