#requires -Version 7.0
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-OperatorDesktopPaths {
    param([switch]$IncludeLegacy)
    @((Join-Path ([Environment]::GetFolderPath('DesktopDirectory')) 'Codex拓展入口.lnk'),
      (Join-Path ([Environment]::GetFolderPath('Programs')) 'Codex拓展入口.lnk'))
    if ($IncludeLegacy) {
        @((Join-Path ([Environment]::GetFolderPath('DesktopDirectory')) 'Codex.lnk'),
          (Join-Path ([Environment]::GetFolderPath('Programs')) 'Codex.lnk'),
          (Join-Path ([Environment]::GetFolderPath('DesktopDirectory')) 'Codex（同步本地模型）.lnk'))
    }
}

function Assert-OperatorPlainPath([string]$Path) {
    $cursor = [IO.Path]::GetFullPath($Path)
    while ($cursor) {
        if (Test-Path -LiteralPath $cursor) {
            if (((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw 'Installation path contains a link or reparse point.'
            }
        }
        $cursor = [IO.Path]::GetDirectoryName($cursor)
    }
}

function Get-OperatorFingerprint([string]$Path) {
    Assert-OperatorPlainPath $Path
    if (-not (Test-Path -LiteralPath $Path)) { return 'absent' }
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw 'Expected an ordinary file.' }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Write-OperatorAtomicBytes([string]$Path, [byte[]]$Bytes) {
    Assert-OperatorPlainPath $Path
    $parent = Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    $temporary = Join-Path $parent ('.operator-' + [Guid]::NewGuid().ToString('N') + '.tmp')
    try {
        [IO.File]::WriteAllBytes($temporary, $Bytes)
        [IO.File]::Move($temporary, $Path, $true)
    } finally {
        if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary }
    }
}

function Get-OperatorOwnership([string]$ProjectRoot) {
    $project = [IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\')
    $journal = Join-Path $project '.codex/operator-installation/ownership.json'
    Assert-OperatorPlainPath $journal
    if (-not (Test-Path -LiteralPath $journal)) {
        foreach ($evidence in @('.codex/operator-installation/ownership-required',
                '.codex/operator-installation/runtime-owner.json', '.codex/operator-installation/originals',
                '.codex/operator-installation/uninstall-receipt.json',
                '.codex/feishu-codex-operator-runtime/runtime-manifest.json',
                '.codex/operator-desktop-entry/Codex.exe',
                '.codex/operator-desktop-entry/Codex拓展入口.exe')) {
            if (Test-Path -LiteralPath (Join-Path $project $evidence)) {
                throw 'Ownership journal missing from an existing installation; recovery stopped.'
            }
        }
        return @{schema_version=1; project=$project; entries=@{}}
    }
    if ((Get-Item -LiteralPath $journal).Length -gt 1048576) { throw 'Ownership journal is too large.' }
    $state = Get-Content -LiteralPath $journal -Raw -Encoding utf8 | ConvertFrom-Json -AsHashtable
    if ($state.schema_version -ne 1 -or $state.project -ine $project -or $state.entries -isnot [Collections.IDictionary]) {
        throw 'Ownership journal identity is invalid.'
    }
    return $state
}

function Save-OperatorOwnership([string]$ProjectRoot, $State) {
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes(($State | ConvertTo-Json -Depth 12))
    Write-OperatorAtomicBytes (Join-Path $ProjectRoot '.codex/operator-installation/ownership-required') ([byte[]]@(1))
    Write-OperatorAtomicBytes (Join-Path $ProjectRoot '.codex/operator-installation/ownership.json') $bytes
}

function Start-OperatorInstallation {
    param([string]$ProjectRoot, [string[]]$LinkPaths = @())
    $project = [IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\')
    $lock = Open-OperatorOwnershipLock $project
    try {
        $state = Get-OperatorOwnership $project
        $restored = @($state.entries.Values | Where-Object { $_.status -eq 'restored' }).Count
        if (-not $restored) {
            if (-not (Test-Path -LiteralPath (Join-Path $project '.codex/operator-installation/ownership.json'))) {
                Save-OperatorOwnership $project $state
            }
            return
        }
        if ($restored -ne $state.entries.Count -or
            (Test-Path -LiteralPath (Join-Path $project '.codex/feishu-codex-operator-runtime'))) {
            throw 'Complete the previous uninstall before starting a new installation.'
        }
        $receiptPath = Join-Path $project '.codex/operator-installation/uninstall-receipt.json'
        Assert-OperatorPlainPath $receiptPath
        $receipt = Get-Content -LiteralPath $receiptPath -Raw -Encoding utf8 | ConvertFrom-Json
        if ($receipt.schema_version -ne 1 -or $receipt.project -ine $project -or $receipt.uninstalled -ne $true) {
            throw 'A completed uninstall receipt for this project is required.'
        }
        $plan = Get-OperatorRestorePlan $project $LinkPaths
        if ($plan.conflicts) { throw 'Restored files or original backups changed; reinstall stopped.' }
        # Publish a new generation only after retaining the previous recovery data.
        # A crash before publication leaves the old, still-restored journal intact.
        $history = Join-Path $project ('.codex/operator-installation/history/' + [Guid]::NewGuid().ToString('N'))
        foreach ($name in @('ownership.json','uninstall-receipt.json','runtime-owner.json')) {
            $source = Join-Path $project ('.codex/operator-installation/' + $name)
            if (Test-Path -LiteralPath $source) {
                Assert-OperatorPlainPath $source
                Write-OperatorAtomicBytes (Join-Path $history $name) ([IO.File]::ReadAllBytes($source))
            }
        }
        Save-OperatorOwnership $project @{schema_version=1; project=$project; entries=@{};
            reactivate_native_launcher=$true}
    } finally { $lock.Dispose() }
}

function Assert-OperatorManagedTarget([string]$ProjectRoot, [string]$Path, [string[]]$LinkPaths) {
    $allowed = @('AGENTS.md', '.codex/hooks.json', '.codex/hooks/start-feishu-codex-operator.ps1',
                 '.codex/hooks/stop-feishu-codex-operator.ps1') | ForEach-Object { [IO.Path]::GetFullPath((Join-Path $ProjectRoot $_)) }
    $allowed += $LinkPaths
    if ([IO.Path]::GetFullPath($Path) -notin $allowed) { throw 'Unowned installation target.' }
    Assert-OperatorPlainPath $Path
}

function Open-OperatorOwnershipLock([string]$ProjectRoot) {
    $directory = Join-Path $ProjectRoot '.codex/operator-installation'
    Assert-OperatorPlainPath $directory
    New-Item -ItemType Directory -Force -Path $directory | Out-Null
    return [IO.File]::Open((Join-Path $directory 'transaction.lock'), [IO.FileMode]::OpenOrCreate,
                          [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
}

function Set-OperatorManagedFile {
    param([string]$ProjectRoot, [string]$Path, [AllowEmptyCollection()][byte[]]$Bytes,
          [string[]]$LinkPaths = @())
    $Path = [IO.Path]::GetFullPath($Path)
    Assert-OperatorManagedTarget $ProjectRoot $Path $LinkPaths
    $lock = Open-OperatorOwnershipLock $ProjectRoot
    try {
        $state = Get-OperatorOwnership $ProjectRoot
        $current = Get-OperatorFingerprint $Path
        $after = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($Bytes)).ToLowerInvariant()
        if ($state.entries.Contains($Path)) {
            $row = $state.entries[$Path]
            if ($row.status -eq 'restored') { throw 'Previously uninstalled integration requires an explicit fresh setup.' }
            if ($current -cne $row.after -and -not ($row.status -eq 'pending' -and $current -ceq $row.previous)) {
                throw 'Managed file changed after installation; preserved for review.'
            }
            if ($current -ceq $after -and $row.status -eq 'installed') { return }
        } else {
            # Preserve the original before publishing an intent or changing the target.
            if ($current -ne 'absent') {
                $backup = Join-Path $ProjectRoot ('.codex/operator-installation/originals/' + $current + '.bin')
                if (-not (Test-Path -LiteralPath $backup)) { Write-OperatorAtomicBytes $backup ([IO.File]::ReadAllBytes($Path)) }
                if ((Get-OperatorFingerprint $backup) -cne $current) { throw 'Original backup verification failed.' }
            }
            $row = @{before=$current; after=$after; status='pending'}
            $state.entries[$Path] = $row
        }
        $row.previous = $current; $row.after = $after; $row.status = 'pending'
        Save-OperatorOwnership $ProjectRoot $state
        if ((Get-OperatorFingerprint $Path) -cne $current) { throw 'Installation target changed concurrently.' }
        Write-OperatorAtomicBytes $Path $Bytes
        $row.status = 'installed'
        Save-OperatorOwnership $ProjectRoot $state
    } finally { $lock.Dispose() }
}

function Get-OperatorRestorePlan {
    param([string]$ProjectRoot, [string[]]$LinkPaths = @())
    $state = Get-OperatorOwnership $ProjectRoot
    $items = @()
    foreach ($path in $state.entries.Keys) {
        Assert-OperatorManagedTarget $ProjectRoot $path $LinkPaths
        $row = $state.entries[$path]
        if ($row.status -notin @('pending','installed','restored') -or
            $row.before -cnotmatch '^(absent|[a-f0-9]{64})$' -or $row.after -cnotmatch '^[a-f0-9]{64}$') {
            throw 'Invalid ownership record.'
        }
        $current = Get-OperatorFingerprint $path
        $conflict = $current -cne $row.after -and $current -cne $row.before
        if ($row.status -eq 'pending' -and $row.previous -cmatch '^(absent|[a-f0-9]{64})$' -and $current -ceq $row.previous) { $conflict = $false }
        if ($row.status -eq 'restored' -and $current -cne $row.before) { $conflict = $true }
        if ($row.before -ne 'absent') {
            $backup = Join-Path $ProjectRoot ('.codex/operator-installation/originals/' + $row.before + '.bin')
            if ((Get-OperatorFingerprint $backup) -cne $row.before) { $conflict = $true }
        }
        $items += [pscustomobject]@{path=$path; before=$row.before; after=$row.after; current=$current;
            action=$(if ($row.before -eq 'absent') {'remove_created_file'} else {'restore_original'}); conflict=$conflict}
    }
    return [pscustomobject]@{items=$items; conflicts=@($items | Where-Object conflict).Count; owned_files=$items.Count}
}

function Restore-OperatorManagedFiles {
    param([string]$ProjectRoot, [string[]]$LinkPaths = @())
    $lock = Open-OperatorOwnershipLock $ProjectRoot
    try {
        $plan = Get-OperatorRestorePlan $ProjectRoot $LinkPaths
        if ($plan.conflicts) { throw 'Restore conflict: current files and original backups were preserved.' }
        $state = Get-OperatorOwnership $ProjectRoot
        foreach ($item in $plan.items) {
            $current = Get-OperatorFingerprint $item.path
            if ($current -cne $item.current) { throw 'Restore target changed concurrently.' }
            if ($current -cne $item.before) {
                if ($item.before -eq 'absent') { Remove-Item -LiteralPath $item.path }
                else {
                    $backup = Join-Path $ProjectRoot ('.codex/operator-installation/originals/' + $item.before + '.bin')
                    Write-OperatorAtomicBytes $item.path ([IO.File]::ReadAllBytes($backup))
                }
            }
            $state.entries[$item.path].status = 'restored'
            Save-OperatorOwnership $ProjectRoot $state
        }
        return $plan
    } finally { $lock.Dispose() }
}

Export-ModuleMember -Function Get-OperatorDesktopPaths,Assert-OperatorPlainPath,Get-OperatorFingerprint,
    Write-OperatorAtomicBytes,Get-OperatorOwnership,Set-OperatorManagedFile,Get-OperatorRestorePlan,
    Restore-OperatorManagedFiles,Start-OperatorInstallation
