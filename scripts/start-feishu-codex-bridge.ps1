$ErrorActionPreference = 'Stop'

if ($env:CODEX_BRIDGE_CHILD -eq '1') {
    Write-Output 'Skipping Feishu Codex bridge start inside bridge child session.'
    exit 0
}

$hooksRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent (Split-Path -Parent $hooksRoot)
$runtimeRoot = Join-Path $projectRoot '.codex\feishu-bridge'
$bridgeScript = Join-Path $runtimeRoot 'bridge.py'
$pidFile = Join-Path $runtimeRoot 'bridge.pid'
$stopFile = Join-Path $runtimeRoot 'stop.request'
$launcherOut = Join-Path $runtimeRoot 'launcher.stdout.log'
$launcherErr = Join-Path $runtimeRoot 'launcher.stderr.log'
$envFile = Join-Path $runtimeRoot 'bridge.env'

New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null

if (Test-Path -LiteralPath $envFile) {
    Get-Content -LiteralPath $envFile | ForEach-Object {
        $line = $_.Trim()
        if ($line.Length -eq 0 -or $line.StartsWith('#')) {
            return
        }
        $parts = $line -split '=', 2
        if ($parts.Count -ne 2) {
            return
        }
        $name = $parts[0].Trim()
        if ($name -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') {
            return
        }
        Set-Item -Path ("Env:{0}" -f $name) -Value $parts[1].Trim()
    }
}

if (Test-Path -LiteralPath $pidFile) {
    $existingPidText = (Get-Content -LiteralPath $pidFile -Raw).Trim()
    $existingProcess = $null
    if ([int]::TryParse($existingPidText, [ref]$existingPid)) {
        $existingProcess = Get-Process -Id $existingPid -ErrorAction SilentlyContinue
    }
    if ($existingProcess) {
        Write-Output "Feishu Codex bridge already running: PID $existingPid"
        exit 0
    }
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
}

Remove-Item -LiteralPath $stopFile -Force -ErrorAction SilentlyContinue

$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        $python = $pythonCommand.Source
    } else {
        $pythonCommand = Get-Command py.exe -ErrorAction SilentlyContinue
        if (-not $pythonCommand) {
            throw 'Python 3 was not found for the Feishu Codex bridge.'
        }
        $python = $pythonCommand.Source
    }
}

if ($python -like '*\py.exe') {
    $arguments = @('-3', $bridgeScript)
} else {
    $arguments = @($bridgeScript)
}

$process = Start-Process `
    -FilePath $python `
    -ArgumentList $arguments `
    -WorkingDirectory $projectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $launcherOut `
    -RedirectStandardError $launcherErr `
    -PassThru

Write-Output "Started Feishu Codex bridge launcher PID $($process.Id)"
