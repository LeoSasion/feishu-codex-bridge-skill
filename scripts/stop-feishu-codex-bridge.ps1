$ErrorActionPreference = 'Stop'

if ($env:CODEX_BRIDGE_CHILD -eq '1') {
    Write-Output 'Skipping Feishu Codex bridge stop inside bridge child session.'
    exit 0
}

$hooksRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent (Split-Path -Parent $hooksRoot)
$runtimeRoot = Join-Path $projectRoot '.codex\feishu-bridge'
$pidFile = Join-Path $runtimeRoot 'bridge.pid'
$stopFile = Join-Path $runtimeRoot 'stop.request'

New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null
Set-Content -LiteralPath $stopFile -Value ([DateTime]::UtcNow.ToString('o')) -Encoding ascii

if (-not (Test-Path -LiteralPath $pidFile)) {
    Write-Output 'Feishu Codex bridge is not running.'
    exit 0
}

$pidText = (Get-Content -LiteralPath $pidFile -Raw).Trim()
$bridgePid = 0
if (-not [int]::TryParse($pidText, [ref]$bridgePid)) {
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    Write-Output 'Removed an invalid Feishu Codex bridge PID file.'
    exit 0
}

$deadline = (Get-Date).AddSeconds(12)
while ((Get-Date) -lt $deadline) {
    $process = Get-Process -Id $bridgePid -ErrorAction SilentlyContinue
    if (-not $process) {
        Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
        Write-Output "Stopped Feishu Codex bridge PID $bridgePid"
        exit 0
    }
    Start-Sleep -Milliseconds 500
}

$process = Get-Process -Id $bridgePid -ErrorAction SilentlyContinue
if ($process) {
    Stop-Process -Id $bridgePid -Force -ErrorAction SilentlyContinue
    Write-Output "Force-stopped unresponsive Feishu Codex bridge PID $bridgePid"
}
Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
