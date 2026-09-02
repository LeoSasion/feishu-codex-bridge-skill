[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot,

    [switch]$Check
)

$ErrorActionPreference = 'Stop'
$startMarker = '<!-- FEISHU_CODEX_BRIDGE_RULES_START -->'
$endMarker = '<!-- FEISHU_CODEX_BRIDGE_RULES_END -->'
$resolvedProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path

if (-not (Test-Path -LiteralPath $resolvedProjectRoot -PathType Container)) {
    throw "Project root is not a directory: $resolvedProjectRoot"
}

$skillRoot = Split-Path -Parent $PSScriptRoot
$fragmentPath = Join-Path $skillRoot 'assets\AGENTS.feishu-codex-bridge.md'
$agentsPath = Join-Path $resolvedProjectRoot 'AGENTS.md'
$backupRoot = Join-Path $resolvedProjectRoot (
    '.codex\feishu-codex-bridge-runtime\backups\' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-agents-rules'
)

if (-not (Test-Path -LiteralPath $fragmentPath -PathType Leaf)) {
    throw "Managed AGENTS.md fragment is missing: $fragmentPath"
}

$fragment = Get-Content -LiteralPath $fragmentPath -Raw -Encoding utf8
$fragmentStartCount = [regex]::Matches($fragment, [regex]::Escape($startMarker)).Count
$fragmentEndCount = [regex]::Matches($fragment, [regex]::Escape($endMarker)).Count
if ($fragmentStartCount -ne 1 -or $fragmentEndCount -ne 1) {
    throw 'Managed AGENTS.md fragment must contain exactly one start and one end marker.'
}

$agentsExists = Test-Path -LiteralPath $agentsPath -PathType Leaf
if ($agentsExists) {
    $agentsItem = Get-Item -LiteralPath $agentsPath -Force
    if (($agentsItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing to replace a linked AGENTS.md file: $agentsPath"
    }
}
[string]$existing = if ($agentsExists) {
    Get-Content -LiteralPath $agentsPath -Raw -Encoding utf8
} else {
    ''
}
$newline = if ($existing.Contains("`r`n")) { "`r`n" } else { "`n" }
$managedBlock = ($fragment -replace "`r?`n", $newline).TrimEnd("`r", "`n")

$startMatches = [regex]::Matches($existing, [regex]::Escape($startMarker))
$endMatches = [regex]::Matches($existing, [regex]::Escape($endMarker))
if ($startMatches.Count -ne $endMatches.Count -or $startMatches.Count -gt 1) {
    throw ('AGENTS.md has malformed or duplicate Feishu bridge markers. ' +
        'No changes were written; repair the markers manually and retry.')
}

$status = ''
$merged = $existing
if ($startMatches.Count -eq 0) {
    $status = 'missing'
    if ([string]::IsNullOrEmpty($existing)) {
        $merged = $managedBlock + $newline
    } else {
        $separator = if ($existing.EndsWith($newline + $newline)) {
            ''
        } elseif ($existing.EndsWith($newline)) {
            $newline
        } else {
            $newline + $newline
        }
        $merged = $existing + $separator + $managedBlock + $newline
    }
} else {
    $startIndex = $startMatches[0].Index
    $endIndex = $endMatches[0].Index
    if ($endIndex -le $startIndex) {
        throw 'AGENTS.md Feishu bridge markers are out of order. No changes were written.'
    }
    $endAfter = $endIndex + $endMarker.Length
    $currentBlock = $existing.Substring($startIndex, $endAfter - $startIndex)
    if ($currentBlock -ceq $managedBlock) {
        $status = 'current'
    } else {
        $status = 'update-needed'
        $merged = $existing.Substring(0, $startIndex) + $managedBlock + $existing.Substring($endAfter)
    }
}

if ($Check) {
    Write-Output "AGENTS.md managed rules: $status."
    return
}

if ($status -eq 'current') {
    Write-Output "AGENTS.md managed rules are current: $agentsPath"
    return
}

if ($agentsExists) {
    New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
    $backupPath = Join-Path $backupRoot 'AGENTS.md'
    Copy-Item -LiteralPath $agentsPath -Destination $backupPath -Force
    Write-Output "Backed up AGENTS.md before managed-rule sync: $backupPath"
}

$temporaryPath = Join-Path $resolvedProjectRoot ('.AGENTS.md.feishu-codex-bridge.' + [guid]::NewGuid().ToString('N') + '.tmp')
try {
    $utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($temporaryPath, $merged, $utf8WithoutBom)
    Move-Item -LiteralPath $temporaryPath -Destination $agentsPath -Force
} finally {
    Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue
}

if ($status -eq 'missing') {
    Write-Output "Appended the Feishu bridge managed rules to $agentsPath"
} else {
    Write-Output "Updated only the Feishu bridge managed rules in $agentsPath"
}
