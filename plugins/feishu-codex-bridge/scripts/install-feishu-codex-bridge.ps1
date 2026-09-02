[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot,

    [switch]$Force,

    [switch]$SkipHooks,

    [switch]$SkipRuntimeConfig,

    [switch]$HooksOnly,

    [switch]$MigrateLegacyRuntime
)

$ErrorActionPreference = 'Stop'
$resolvedProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
$skillRoot = Split-Path -Parent $PSScriptRoot
$runtimeRoot = Join-Path $resolvedProjectRoot '.codex\feishu-codex-bridge-runtime'
$legacyRuntimeRoot = Join-Path $resolvedProjectRoot '.codex\feishu-bridge'
$hooksRoot = Join-Path $resolvedProjectRoot '.codex\hooks'
$bridgeTarget = Join-Path $runtimeRoot 'bridge.py'
$beeperQueueTarget = Join-Path $runtimeRoot 'beeper_queue_cli.py'
$coreTarget = Join-Path $runtimeRoot 'bridge_core'
$startTarget = Join-Path $hooksRoot 'start-feishu-codex-bridge.ps1'
$stopTarget = Join-Path $hooksRoot 'stop-feishu-codex-bridge.ps1'
$envTarget = Join-Path $runtimeRoot 'bridge.env'
$runtimeManifestTarget = Join-Path $runtimeRoot 'runtime-manifest.json'
$hooksConfigPath = Join-Path $resolvedProjectRoot '.codex\hooks.json'
$backupRoot = Join-Path $runtimeRoot ('backups\' + (Get-Date -Format 'yyyyMMdd-HHmmss'))

function Get-BridgeProcessIdentity {
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [Parameter(Mandatory = $true)][string]$BridgeScript
    )
    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if (-not $process) {
        return [pscustomobject]@{ Exists = $false; Verified = $true; IsBridge = $false; ProcessName = '' }
    }
    $processName = [string]$process.ProcessName
    if ($processName -notmatch '(?i)^python(?:w|[0-9.]*)?$') {
        return [pscustomobject]@{ Exists = $true; Verified = $true; IsBridge = $false; ProcessName = $processName }
    }
    try {
        $record = Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f $ProcessId) -ErrorAction Stop
    } catch {
        return [pscustomobject]@{ Exists = $true; Verified = $false; IsBridge = $false; ProcessName = $processName }
    }
    $commandLine = if ($record) { [string]$record.CommandLine } else { '' }
    if ([string]::IsNullOrWhiteSpace($commandLine)) {
        return [pscustomobject]@{ Exists = $true; Verified = $false; IsBridge = $false; ProcessName = $processName }
    }
    $expected = [System.IO.Path]::GetFullPath($BridgeScript).Replace('/', '\')
    $observed = $commandLine.Replace('/', '\')
    return [pscustomobject]@{
        Exists = $true
        Verified = $true
        IsBridge = $observed.IndexOf($expected, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
        ProcessName = $processName
    }
}

function Assert-BridgeStopped {
    param([Parameter(Mandatory = $true)][string]$CandidateRuntimeRoot)
    $pidPath = Join-Path $CandidateRuntimeRoot 'bridge.pid'
    if (-not (Test-Path -LiteralPath $pidPath -PathType Leaf)) { return }
    $bridgePid = 0
    if (-not [int]::TryParse((Get-Content -LiteralPath $pidPath -Raw).Trim(), [ref]$bridgePid) -or
        $bridgePid -le 0) { return }
    $identity = Get-BridgeProcessIdentity `
        -ProcessId $bridgePid `
        -BridgeScript (Join-Path $CandidateRuntimeRoot 'bridge.py')
    if (-not $identity.Exists -or ($identity.Verified -and -not $identity.IsBridge)) { return }
    if (-not $identity.Verified) {
        throw "Bridge PID $bridgePid exists, but its Python command line could not be verified; refusing installation changes."
    }
    throw "Bridge must be stopped before installation changes; stop this exact verified Bridge as a separate observable transaction (PID $bridgePid)."
}

function Move-LegacyBridgeRuntime {
    if (-not $MigrateLegacyRuntime) { return }
    if (-not $Force -or -not $HooksOnly) {
        throw 'MigrateLegacyRuntime requires a forced hook-only refresh.'
    }
    if ((Test-Path -LiteralPath $runtimeRoot) -or
        -not (Test-Path -LiteralPath $legacyRuntimeRoot -PathType Container)) {
        throw ('Runtime migration requires exactly one legacy directory and no canonical runtime ' +
            'directory; refusing to merge or infer durable state ownership.')
    }
    Assert-BridgeStopped -CandidateRuntimeRoot $legacyRuntimeRoot

    $projectInfo = Get-Item -LiteralPath $resolvedProjectRoot -Force -ErrorAction Stop
    $codexRoot = Join-Path $resolvedProjectRoot '.codex'
    $codexInfo = Get-Item -LiteralPath $codexRoot -Force -ErrorAction Stop
    $legacyInfo = Get-Item -LiteralPath $legacyRuntimeRoot -Force -ErrorAction Stop
    foreach ($directory in @($projectInfo, $codexInfo, $legacyInfo)) {
        if (-not $directory.PSIsContainer -or
            ($directory.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
            throw 'Runtime migration refuses non-directory or reparse-point path components.'
        }
    }
    $expectedCodexRoot = [System.IO.Path]::GetFullPath((Join-Path $resolvedProjectRoot '.codex'))
    if (-not $legacyInfo.Parent.FullName.Equals($expectedCodexRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
        -not $codexInfo.FullName.Equals($expectedCodexRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'Runtime migration path containment verification failed.'
    }

    Move-Item -LiteralPath $legacyInfo.FullName -Destination $runtimeRoot -ErrorAction Stop
    if ((Test-Path -LiteralPath $legacyRuntimeRoot) -or
        -not (Test-Path -LiteralPath $runtimeRoot -PathType Container)) {
        throw 'Runtime migration did not reach one unambiguous canonical directory.'
    }
    Write-Output 'Migrated the stopped Bridge runtime directory to .codex\feishu-codex-bridge-runtime.'
}

function Assert-ManifestCapableHooks {
    foreach ($hookTarget in @($startTarget, $stopTarget)) {
        if (-not (Test-Path -LiteralPath $hookTarget -PathType Leaf)) {
            throw "Cannot create runtime manifest because the installed hook is missing: $hookTarget"
        }
    }
    $startHookText = Get-Content -LiteralPath $startTarget -Raw
    if ($startHookText -notmatch [regex]::Escape('$BRIDGE_RUNTIME_MANIFEST_SCHEMA = 1')) {
        throw ('The installed start hook predates the runtime-manifest gate. ' +
            'Refresh hooks as a separate observable transaction, then run the matching runtime upgrade automatically.')
    }
}

if ($HooksOnly -and ($SkipHooks -or $SkipRuntimeConfig)) {
    throw 'HooksOnly cannot be combined with runtime or configuration options.'
}
if ($HooksOnly -and -not $Force -and
    ((Test-Path -LiteralPath $startTarget) -or (Test-Path -LiteralPath $stopTarget))) {
    throw 'HooksOnly requires Force when replacing installed lifecycle hooks.'
}
if ((Test-Path -LiteralPath $runtimeRoot) -and (Test-Path -LiteralPath $legacyRuntimeRoot)) {
    throw ('Both canonical and legacy Bridge runtime directories exist. Refusing to choose or merge ' +
        'two possible durable state authorities.')
}
if ((Test-Path -LiteralPath $legacyRuntimeRoot) -and -not $MigrateLegacyRuntime) {
    throw 'A legacy Bridge runtime exists; use the canonical bridge upgrade command for stopped migration.'
}
Move-LegacyBridgeRuntime
if ($HooksOnly -or $Force -or $SkipHooks) {
    Assert-BridgeStopped -CandidateRuntimeRoot $runtimeRoot
}
if ($HooksOnly) {
    foreach ($requiredInstalledPath in @($bridgeTarget, $envTarget, $startTarget, $stopTarget)) {
        if (-not (Test-Path -LiteralPath $requiredInstalledPath -PathType Leaf)) {
            throw "Hook-only refresh requires an existing bridge installation: $requiredInstalledPath"
        }
    }
    if (Test-Path -LiteralPath $hooksConfigPath -PathType Leaf) {
        try {
            Get-Content -LiteralPath $hooksConfigPath -Raw -Encoding utf8 |
                ConvertFrom-Json -ErrorAction Stop | Out-Null
        } catch {
            throw "Existing hooks.json is invalid; repair it before hook-only refresh: $($_.Exception.Message)"
        }
    }
}
if (-not $HooksOnly -and $SkipRuntimeConfig -and -not (Test-Path -LiteralPath $envTarget -PathType Leaf)) {
    throw "Cannot skip runtime configuration because it does not exist: $envTarget"
}
if (-not $HooksOnly -and $SkipHooks) {
    # Fail before merging rules or copying runtime code. A runtime-only upgrade
    # is safe only after the installed hook already knows this manifest schema.
    Assert-ManifestCapableHooks
}

Write-Output 'Installer leaves project AGENTS.md rules unchanged; run bridge init as a separately observable automatic transaction.'

New-Item -ItemType Directory -Force -Path $runtimeRoot, $hooksRoot | Out-Null

function Backup-Target {
    param([Parameter(Mandatory = $true)][string]$Target)
    if (-not (Test-Path -LiteralPath $Target)) { return }
    New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
    Copy-Item -LiteralPath $Target -Destination $backupRoot -Recurse -Force
}

function Copy-BridgeFile {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Target
    )
    if (Test-Path -LiteralPath $Target) {
        if (-not $Force) {
            Write-Output "Preserved existing $Target"
            return
        }
        Backup-Target $Target
    }
    Copy-Item -LiteralPath $Source -Destination $Target -Force
    Write-Output "Installed $Target"
}

function Copy-BridgeDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Target
    )
    if (Test-Path -LiteralPath $Target) {
        if (-not $Force) {
            Write-Output "Preserved existing $Target"
            return
        }
        $resolvedTarget = (Resolve-Path -LiteralPath $Target).Path
        if (-not $resolvedTarget.StartsWith($runtimeRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to replace directory outside bridge runtime: $resolvedTarget"
        }
        Backup-Target $Target
        Remove-Item -LiteralPath $resolvedTarget -Recurse -Force
    }
    Copy-Item -LiteralPath $Source -Destination $Target -Recurse -Force
    Write-Output "Installed $Target"
}

function Remove-RetiredReadinessState {
    if (-not $Force) { return }

    $retiredStatePath = Join-Path $runtimeRoot 'readiness-state.env'
    if (-not (Test-Path -LiteralPath $retiredStatePath)) { return }

    $runtimeInfo = Get-Item -LiteralPath $runtimeRoot -Force -ErrorAction Stop
    $retiredStateInfo = Get-Item -LiteralPath $retiredStatePath -Force -ErrorAction Stop
    $expectedRuntimePath = [System.IO.Path]::GetFullPath($runtimeRoot)
    $expectedStatePath = [System.IO.Path]::GetFullPath($retiredStatePath)
    if (-not $runtimeInfo.PSIsContainer -or
        ($runtimeInfo.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
        $retiredStateInfo.PSIsContainer -or
        ($retiredStateInfo.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
        -not $retiredStateInfo.FullName.Equals($expectedStatePath, [System.StringComparison]::OrdinalIgnoreCase) -or
        -not $retiredStateInfo.DirectoryName.Equals($expectedRuntimePath, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'Retired readiness state is not the exact ordinary runtime leaf; refusing cleanup.'
    }

    Remove-Item -LiteralPath $retiredStateInfo.FullName -Force
    Write-Output 'Removed retired historical readiness marker state.'
}

function Remove-RetiredPollingState {
    if (-not $Force) { return }

    $runtimeInfo = Get-Item -LiteralPath $runtimeRoot -Force -ErrorAction Stop
    $expectedRuntimePath = [System.IO.Path]::GetFullPath($runtimeRoot)
    if (-not $runtimeInfo.PSIsContainer -or
        ($runtimeInfo.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
        -not $runtimeInfo.FullName.Equals($expectedRuntimePath, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'Bridge runtime root is not the exact ordinary directory; refusing retired polling-state cleanup.'
    }

    # The first value is an exact pre-glossary data root. It is inspected only
    # for retired polling metadata and never gains current producer authority.
    foreach ($queueRootName in @('desktop-router', 'beeper')) {
        $queueRootPath = Join-Path $runtimeRoot $queueRootName
        $retiredStatePath = Join-Path $queueRootPath 'heartbeat.json'
        if (-not (Test-Path -LiteralPath $retiredStatePath)) { continue }

        $queueRootInfo = Get-Item -LiteralPath $queueRootPath -Force -ErrorAction Stop
        $retiredStateInfo = Get-Item -LiteralPath $retiredStatePath -Force -ErrorAction Stop
        $expectedQueueRootPath = [System.IO.Path]::GetFullPath($queueRootPath)
        $expectedStatePath = [System.IO.Path]::GetFullPath($retiredStatePath)
        if (-not $queueRootInfo.PSIsContainer -or
            ($queueRootInfo.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
            -not $queueRootInfo.FullName.Equals($expectedQueueRootPath, [System.StringComparison]::OrdinalIgnoreCase) -or
            $retiredStateInfo.PSIsContainer -or
            ($retiredStateInfo.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
            -not $retiredStateInfo.FullName.Equals($expectedStatePath, [System.StringComparison]::OrdinalIgnoreCase) -or
            -not $retiredStateInfo.DirectoryName.Equals($expectedQueueRootPath, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Retired polling state is not the exact ordinary runtime leaf for $queueRootName; refusing cleanup."
        }

        Remove-Item -LiteralPath $retiredStateInfo.FullName -Force
        Write-Output "Removed retired polling state from $queueRootName."
    }
}

function Write-MigratedStoppedHealthSnapshot {
    param(
        [Parameter(Mandatory = $true)][object]$Health,
        [Parameter(Mandatory = $true)][string]$Message
    )

    $temporaryHealthPath = Join-Path $runtimeRoot (
        'health-migration-' + [Guid]::NewGuid().ToString('N') + '.tmp'
    )
    try {
        $healthJson = $Health | ConvertTo-Json -Depth 30
        $utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText($temporaryHealthPath, $healthJson, $utf8WithoutBom)
        Move-Item -LiteralPath $temporaryHealthPath -Destination (Join-Path $runtimeRoot 'health.json') -Force
    } finally {
        Remove-Item -LiteralPath $temporaryHealthPath -Force -ErrorAction SilentlyContinue
    }
    Write-Output $Message
}

function Convert-RetiredStoppedHealthSnapshot {
    if (-not $Force) { return }

    $healthPath = Join-Path $runtimeRoot 'health.json'
    if (-not (Test-Path -LiteralPath $healthPath)) { return }

    $runtimeInfo = Get-Item -LiteralPath $runtimeRoot -Force -ErrorAction Stop
    $healthInfo = Get-Item -LiteralPath $healthPath -Force -ErrorAction Stop
    $expectedRuntimePath = [System.IO.Path]::GetFullPath($runtimeRoot)
    $expectedHealthPath = [System.IO.Path]::GetFullPath($healthPath)
    if (-not $runtimeInfo.PSIsContainer -or
        ($runtimeInfo.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
        -not $runtimeInfo.FullName.Equals($expectedRuntimePath, [System.StringComparison]::OrdinalIgnoreCase) -or
        $healthInfo.PSIsContainer -or
        ($healthInfo.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
        -not $healthInfo.FullName.Equals($expectedHealthPath, [System.StringComparison]::OrdinalIgnoreCase) -or
        -not $healthInfo.DirectoryName.Equals($expectedRuntimePath, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'Bridge health snapshot is not the exact ordinary runtime leaf; refusing retired-state migration.'
    }

    try {
        $health = Get-Content -LiteralPath $healthInfo.FullName -Raw -Encoding utf8 |
            ConvertFrom-Json -ErrorAction Stop
    } catch {
        throw "Bridge health snapshot is invalid; refusing retired-state migration: $($_.Exception.Message)"
    }
    $currentBeeperProperty = $health.PSObject.Properties['beeper_queue']
    $preGlossaryBeeperProperty = $health.PSObject.Properties['desktop_router']
    if ($currentBeeperProperty -and $preGlossaryBeeperProperty) {
        throw 'Bridge health snapshot mixes current and pre-glossary Beeper metadata; refusing retired-state migration.'
    }
    if (-not $currentBeeperProperty -and -not $preGlossaryBeeperProperty) {
        throw 'Bridge health snapshot has no Beeper metadata; refusing retired-state migration.'
    }

    $queue = $health.queue
    $isIntegerZero = {
        param($Value)
        return (($Value -is [int] -or $Value -is [long]) -and [long]$Value -eq 0)
    }
    $commonIdle = (
        $health.status -is [string] -and [string]$health.status -ceq 'stopped' -and
        $health.event_consumer -is [bool] -and -not [bool]$health.event_consumer -and
        (& $isIntegerZero $health.active_turns) -and
        $null -ne $queue -and
        (& $isIntegerZero $queue.queued) -and
        (& $isIntegerZero $queue.running) -and
        (& $isIntegerZero $queue.control_sending) -and
        (& $isIntegerZero $queue.reply_pending) -and
        (& $isIntegerZero $health.actionable_retryable_failed)
    )

    if ($preGlossaryBeeperProperty) {
        $preGlossaryBeeper = $preGlossaryBeeperProperty.Value
        [string[]]$actualPreGlossaryKeys = @(
            $preGlossaryBeeper.PSObject.Properties.Name |
                ForEach-Object { [string]$_ } |
                Sort-Object
        )
        [string[]]$expectedPreGlossaryKeys = @(
            'claimed', 'pending', 'wake_inflight', 'wake_lease_remaining_seconds'
        ) | Sort-Object
        if (($actualPreGlossaryKeys -join "`n") -cne ($expectedPreGlossaryKeys -join "`n") -or
            -not $commonIdle -or
            $preGlossaryBeeper.wake_inflight -isnot [bool] -or [bool]$preGlossaryBeeper.wake_inflight -or
            $null -ne $preGlossaryBeeper.wake_lease_remaining_seconds -or
            -not (& $isIntegerZero $preGlossaryBeeper.pending) -or
            -not (& $isIntegerZero $preGlossaryBeeper.claimed) -or
            $health.session_owner -isnot [string] -or [string]$health.session_owner -cne 'desktop-router' -or
            $health.codex_transport -isnot [string] -or [string]$health.codex_transport -cne 'experimental-codex-queue' -or
            $health.gateway_state -isnot [string] -or [string]$health.gateway_state -cne 'experimental-gateway-registered-load-unobserved' -or
            $health.target_writer -isnot [string] -or [string]$health.target_writer -cne 'desktop-task-only') {
            throw 'Pre-glossary Bridge health metadata is not exactly stopped and idle; refusing migration.'
        }

        $observationProperty = $health.PSObject.Properties['experimental_mvp_observation']
        if ($observationProperty -and $null -ne $observationProperty.Value) {
            $observation = $observationProperty.Value
            [string[]]$actualObservationKeys = @(
                $observation.PSObject.Properties.Name |
                    ForEach-Object { [string]$_ } |
                    Sort-Object
            )
            [string[]]$expectedObservationKeys = @(
                'answer_free',
                'feishu_delivery_observed',
                'known_delivery_fidelity_observed',
                'listener_outbox_scrubbed',
                'producer_namespace',
                'schema_version',
                'single_inbox_claim_observed',
                'status',
                'target_final_source'
            ) | Sort-Object
            if (($actualObservationKeys -join "`n") -cne ($expectedObservationKeys -join "`n") -or
                (($observation.schema_version -isnot [int]) -and ($observation.schema_version -isnot [long])) -or
                [long]$observation.schema_version -ne 1 -or
                $observation.status -isnot [string] -or [string]$observation.status -cne 'passed' -or
                $observation.answer_free -isnot [bool] -or -not [bool]$observation.answer_free -or
                $observation.producer_namespace -isnot [string] -or [string]$observation.producer_namespace -cne 'experimental-gateway-v1' -or
                $observation.target_final_source -isnot [string] -or [string]$observation.target_final_source -cne 'target_mcp' -or
                $observation.feishu_delivery_observed -isnot [bool] -or -not [bool]$observation.feishu_delivery_observed -or
                $observation.known_delivery_fidelity_observed -isnot [bool] -or -not [bool]$observation.known_delivery_fidelity_observed -or
                $observation.single_inbox_claim_observed -isnot [bool] -or -not [bool]$observation.single_inbox_claim_observed -or
                $observation.listener_outbox_scrubbed -isnot [bool] -or -not [bool]$observation.listener_outbox_scrubbed) {
                throw 'Pre-glossary observation has an unsupported shape; refusing migration.'
            }
        }

        $health.PSObject.Properties.Remove('desktop_router')
        $health.PSObject.Properties.Remove('codex_transport')
        $health.PSObject.Properties.Remove('gateway_state')
        $health.PSObject.Properties.Remove('target_writer')
        $health.session_owner = 'beeper'
        $health | Add-Member -NotePropertyName 'beeper_queue' -NotePropertyValue ([pscustomobject][ordered]@{
            dial_inflight = $false
            dial_lease_remaining_seconds = $null
            pending = 0
            claimed = 0
        }) -Force
        $health | Add-Member -NotePropertyName 'beeper_transport' -NotePropertyValue 'codex-queue' -Force
        $health | Add-Member -NotePropertyName 'beeper_state' -NotePropertyValue 'beeper-registered-load-unobserved' -Force
        $health | Add-Member -NotePropertyName 'responder_writer' -NotePropertyValue 'desktop-task-only' -Force
        $health | Add-Member -NotePropertyName 'mvp_observation' -NotePropertyValue $null -Force
        Write-MigratedStoppedHealthSnapshot -Health $health -Message (
            'Migrated exact stopped pre-glossary Bridge health metadata to the canonical terminology schema.'
        )
        return
    }

    [string[]]$actualBeeperKeys = @(
        $currentBeeperProperty.Value.PSObject.Properties.Name |
            ForEach-Object { [string]$_ } |
            Sort-Object
    )
    [string[]]$currentBeeperKeys = @(
        'claimed', 'pending', 'dial_inflight', 'dial_lease_remaining_seconds'
    ) | Sort-Object
    if (($actualBeeperKeys -join "`n") -ceq ($currentBeeperKeys -join "`n")) {
        $oldObservationProperty = $health.PSObject.Properties['experimental_mvp_observation']
        $observationProperty = $health.PSObject.Properties['mvp_observation']
        $oldTransport = (
            $health.beeper_transport -is [string] -and
            [string]$health.beeper_transport -ceq 'experimental-codex-queue'
        )
        $newTransport = (
            $health.beeper_transport -is [string] -and
            [string]$health.beeper_transport -ceq 'codex-queue'
        )
        $oldState = (
            $health.beeper_state -is [string] -and
            [string]$health.beeper_state -cin @(
                'experimental-beeper-registered-load-unobserved',
                'experimental-beeper-unavailable'
            )
        )
        $newState = (
            $health.beeper_state -is [string] -and
            [string]$health.beeper_state -cin @(
                'beeper-registered-load-unobserved',
                'beeper-unavailable'
            )
        )

        if ($newTransport -and $newState -and -not $oldObservationProperty -and $observationProperty) {
            return
        }
        if (-not $oldTransport -or -not $oldState -or -not $oldObservationProperty -or $observationProperty) {
            throw 'Bridge health snapshot mixes or uses unsupported Beeper naming; refusing migration.'
        }

        $beeper = $currentBeeperProperty.Value
        if (-not $commonIdle -or
            $health.bridge_version -isnot [string] -or [string]$health.bridge_version -cne '4.2.0-alpha.60' -or
            $health.session_owner -isnot [string] -or [string]$health.session_owner -cne 'beeper' -or
            $health.responder_writer -isnot [string] -or [string]$health.responder_writer -cne 'desktop-task-only' -or
            $beeper.dial_inflight -isnot [bool] -or [bool]$beeper.dial_inflight -or
            $null -ne $beeper.dial_lease_remaining_seconds -or
            -not (& $isIntegerZero $beeper.pending) -or
            -not (& $isIntegerZero $beeper.claimed)) {
            throw 'Previously named Beeper health metadata is not exactly stopped and idle; refusing migration.'
        }

        if ($null -ne $oldObservationProperty.Value) {
            $observation = $oldObservationProperty.Value
            [string[]]$actualObservationKeys = @(
                $observation.PSObject.Properties.Name |
                    ForEach-Object { [string]$_ } |
                    Sort-Object
            )
            [string[]]$expectedObservationKeys = @(
                'answer_free',
                'bridge_outbox_scrubbed',
                'feishu_delivery_observed',
                'final_callback_source',
                'known_delivery_fidelity_observed',
                'producer_namespace',
                'schema_version',
                'single_inbox_claim_observed',
                'status'
            ) | Sort-Object
            if (($actualObservationKeys -join "`n") -cne ($expectedObservationKeys -join "`n") -or
                (($observation.schema_version -isnot [int]) -and ($observation.schema_version -isnot [long])) -or
                [long]$observation.schema_version -ne 1 -or
                $observation.status -isnot [string] -or [string]$observation.status -cne 'passed' -or
                $observation.answer_free -isnot [bool] -or -not [bool]$observation.answer_free -or
                $observation.producer_namespace -isnot [string] -or [string]$observation.producer_namespace -cne 'experimental-beeper-v1' -or
                $observation.final_callback_source -isnot [string] -or [string]$observation.final_callback_source -cne 'final_callback' -or
                $observation.feishu_delivery_observed -isnot [bool] -or -not [bool]$observation.feishu_delivery_observed -or
                $observation.known_delivery_fidelity_observed -isnot [bool] -or -not [bool]$observation.known_delivery_fidelity_observed -or
                $observation.single_inbox_claim_observed -isnot [bool] -or -not [bool]$observation.single_inbox_claim_observed -or
                $observation.bridge_outbox_scrubbed -isnot [bool] -or -not [bool]$observation.bridge_outbox_scrubbed) {
                throw 'Previously named MVP observation has an unsupported shape; refusing migration.'
            }
        }

        $health.beeper_transport = 'codex-queue'
        $health.beeper_state = if ([string]$health.beeper_state -ceq 'experimental-beeper-unavailable') {
            'beeper-unavailable'
        } else {
            'beeper-registered-load-unobserved'
        }
        $health.PSObject.Properties.Remove('experimental_mvp_observation')
        $health | Add-Member -NotePropertyName 'mvp_observation' -NotePropertyValue $null -Force
        Write-MigratedStoppedHealthSnapshot -Health $health -Message (
            'Migrated exact stopped Bridge health metadata to the unprefixed Beeper schema.'
        )
        return
    }

    [string[]]$retiredBeeperKeys = @(
        'claimed',
        'pending',
        'scheduler_age_seconds',
        'scheduler_fresh',
        'dial_inflight',
        'work_heartbeat_age_seconds',
        'work_heartbeat_fresh'
    ) | Sort-Object
    if (($actualBeeperKeys -join "`n") -cne ($retiredBeeperKeys -join "`n")) {
        throw 'Bridge health snapshot has an unsupported Beeper shape; refusing retired-state migration.'
    }

    $beeper = $currentBeeperProperty.Value
    if (-not $commonIdle -or
        $beeper.scheduler_fresh -isnot [bool] -or [bool]$beeper.scheduler_fresh -or
        $null -ne $beeper.scheduler_age_seconds -or
        $beeper.work_heartbeat_fresh -isnot [bool] -or [bool]$beeper.work_heartbeat_fresh -or
        $null -ne $beeper.work_heartbeat_age_seconds -or
        $beeper.dial_inflight -isnot [bool] -or [bool]$beeper.dial_inflight -or
        -not (& $isIntegerZero $beeper.pending) -or
        -not (& $isIntegerZero $beeper.claimed)) {
        throw 'Retired Bridge health metadata is not exactly stopped and idle; refusing migration.'
    }

    $health.beeper_queue = [pscustomobject][ordered]@{
        dial_inflight = $false
        dial_lease_remaining_seconds = $null
        pending = 0
        claimed = 0
    }
    Write-MigratedStoppedHealthSnapshot -Health $health -Message (
        'Migrated exact stopped Bridge health metadata to the current dial-lease schema.'
    )
}

function Write-BridgeRuntimeManifest {
    $expectedVersion = '4.2.0-alpha.64'
    $runtimeFiles = @(
        'bridge.py',
        'beeper_queue_cli.py',
        'bridge_core/__init__.py',
        'bridge_core/config.py',
        'bridge_core/beeper_client.py',
        'bridge_core/beeper_queue.py',
        'bridge_core/legacy_identifiers.py',
        'bridge_core/lark.py',
        'bridge_core/runtime.py',
        'bridge_core/state.py'
    )

    Assert-ManifestCapableHooks

    $codeHashes = [ordered]@{}
    foreach ($relative in $runtimeFiles) {
        $target = Join-Path $runtimeRoot ($relative.Replace('/', '\'))
        if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {
            throw "Cannot create runtime manifest because installed code is missing: $target"
        }
        $codeHashes[$relative] = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
    }

    $installedConfig = Get-Content -LiteralPath (Join-Path $coreTarget 'config.py') -Raw
    if ($installedConfig -notmatch 'BRIDGE_VERSION\s*=\s*["'']([^"'']+)["'']') {
        throw 'Installed runtime has no readable BRIDGE_VERSION marker.'
    }
    $installedVersion = [string]$Matches[1]
    if ($installedVersion -ne $expectedVersion) {
        throw "Installed runtime version '$installedVersion' is obsolete; expected '$expectedVersion'. Use a forced upgrade after verifying the exact target and source/runtime scope."
    }

    $manifest = [ordered]@{
        schema_version = 1
        bridge_version = $installedVersion
        code_files = $codeHashes
        start_hook_sha256 = (Get-FileHash -LiteralPath $startTarget -Algorithm SHA256).Hash.ToLowerInvariant()
        stop_hook_sha256 = (Get-FileHash -LiteralPath $stopTarget -Algorithm SHA256).Hash.ToLowerInvariant()
        generated_at = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    }
    $temporary = "$runtimeManifestTarget.tmp"
    try {
        $json = $manifest | ConvertTo-Json -Depth 10
        $utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText($temporary, $json, $utf8WithoutBom)
        Move-Item -LiteralPath $temporary -Destination $runtimeManifestTarget -Force
    } finally {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    }
    Write-Output "Installed runtime integrity manifest: $runtimeManifestTarget"
}

function Update-InstalledStoppedHealthSnapshot {
    $healthPath = Join-Path $runtimeRoot 'health.json'
    if (-not (Test-Path -LiteralPath $healthPath -PathType Leaf)) { return }

    $runtimeInfo = Get-Item -LiteralPath $runtimeRoot -Force -ErrorAction Stop
    $healthInfo = Get-Item -LiteralPath $healthPath -Force -ErrorAction Stop
    $manifestInfo = Get-Item -LiteralPath $runtimeManifestTarget -Force -ErrorAction Stop
    $expectedRuntimePath = [System.IO.Path]::GetFullPath($runtimeRoot)
    if (-not $runtimeInfo.PSIsContainer -or
        ($runtimeInfo.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
        $healthInfo.PSIsContainer -or
        ($healthInfo.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
        $manifestInfo.PSIsContainer -or
        ($manifestInfo.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
        -not $healthInfo.DirectoryName.Equals($expectedRuntimePath, [System.StringComparison]::OrdinalIgnoreCase) -or
        -not $manifestInfo.DirectoryName.Equals($expectedRuntimePath, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'Installed Bridge health refresh inputs are not exact ordinary runtime leaves.'
    }

    try {
        $health = Get-Content -LiteralPath $healthInfo.FullName -Raw -Encoding utf8 |
            ConvertFrom-Json -ErrorAction Stop
    } catch {
        throw "Installed Bridge health snapshot is invalid; refusing version refresh: $($_.Exception.Message)"
    }
    $queue = $health.queue
    $beeper = $health.beeper_queue
    $isIntegerZero = {
        param($Value)
        return (($Value -is [int] -or $Value -is [long]) -and [long]$Value -eq 0)
    }
    [string[]]$actualBeeperKeys = @(
        $beeper.PSObject.Properties.Name | ForEach-Object { [string]$_ } | Sort-Object
    )
    [string[]]$expectedBeeperKeys = @(
        'claimed', 'pending', 'dial_inflight', 'dial_lease_remaining_seconds'
    ) | Sort-Object
    if (($actualBeeperKeys -join "`n") -cne ($expectedBeeperKeys -join "`n") -or
        $health.status -isnot [string] -or [string]$health.status -cne 'stopped' -or
        $health.event_consumer -isnot [bool] -or [bool]$health.event_consumer -or
        -not (& $isIntegerZero $health.active_turns) -or
        $health.session_owner -isnot [string] -or [string]$health.session_owner -cne 'beeper' -or
        $health.beeper_transport -isnot [string] -or [string]$health.beeper_transport -cne 'codex-queue' -or
        $health.responder_writer -isnot [string] -or [string]$health.responder_writer -cne 'desktop-task-only' -or
        $null -eq $queue -or
        -not (& $isIntegerZero $queue.queued) -or
        -not (& $isIntegerZero $queue.running) -or
        -not (& $isIntegerZero $queue.control_sending) -or
        -not (& $isIntegerZero $queue.reply_pending) -or
        -not (& $isIntegerZero $health.actionable_retryable_failed) -or
        $beeper.dial_inflight -isnot [bool] -or [bool]$beeper.dial_inflight -or
        $null -ne $beeper.dial_lease_remaining_seconds -or
        -not (& $isIntegerZero $beeper.pending) -or
        -not (& $isIntegerZero $beeper.claimed)) {
        throw 'Installed Bridge health is not exactly stopped and idle; refusing version refresh.'
    }

    $health.bridge_version = '4.2.0-alpha.64'
    $health.runtime_manifest_sha256 = (
        Get-FileHash -LiteralPath $runtimeManifestTarget -Algorithm SHA256 -ErrorAction Stop
    ).Hash.ToLowerInvariant()
    $health.updated_at = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
    Write-MigratedStoppedHealthSnapshot -Health $health -Message (
        'Refreshed exact stopped Bridge health metadata for the installed runtime manifest.'
    )
}

if ($HooksOnly) {
    Copy-BridgeFile (Join-Path $skillRoot 'scripts\start-feishu-codex-bridge.ps1') $startTarget
    Copy-BridgeFile (Join-Path $skillRoot 'scripts\stop-feishu-codex-bridge.ps1') $stopTarget
    if (Test-Path -LiteralPath $runtimeManifestTarget -PathType Leaf) {
        Backup-Target $runtimeManifestTarget
        Remove-Item -LiteralPath $runtimeManifestTarget -Force
        Write-Output 'Invalidated the previous runtime manifest; start remains fail-closed until the matching runtime upgrade completes.'
    }
    Convert-RetiredStoppedHealthSnapshot
} else {
    Copy-BridgeFile (Join-Path $skillRoot 'scripts\bridge.py') $bridgeTarget
    Copy-BridgeFile (Join-Path $skillRoot 'scripts\beeper_queue_cli.py') $beeperQueueTarget
    Copy-BridgeDirectory (Join-Path $skillRoot 'scripts\bridge_core') $coreTarget
    if ($SkipHooks) {
        Write-Output 'Skipped lifecycle hook scripts.'
    } else {
        Copy-BridgeFile (Join-Path $skillRoot 'scripts\start-feishu-codex-bridge.ps1') $startTarget
        Copy-BridgeFile (Join-Path $skillRoot 'scripts\stop-feishu-codex-bridge.ps1') $stopTarget
    }

    if ($SkipRuntimeConfig) {
        Write-Output "Skipped runtime configuration: $envTarget"
    } else {
    if (-not (Test-Path -LiteralPath $envTarget)) {
    @'
# Feishu Codex Bridge configuration. Never store app secrets or OAuth tokens here.
# The Feishu bridge writes a durable local queue. The bounded local producer may
# queue one opaque page to the exact registered Beeper; active-work lease
# renewal never creates a second turn or opens a Responder App Server.
CODEX_BRIDGE_ACCESS_MODE=locked
CODEX_BRIDGE_EVENT_READY_TIMEOUT=15
CODEX_BRIDGE_MAX_CONCURRENT_TURNS=2
CODEX_BRIDGE_BEEPER_TIMEOUT=3600
CODEX_BRIDGE_BEEPER_CLAIM_TTL=7200
CODEX_BRIDGE_BEEPER_RETENTION_HOURS=168
CODEX_BRIDGE_BEEPER_DIAL_TTL=180
CODEX_BRIDGE_BEEPER_GRACE_MAX_SECONDS=30
CODEX_BRIDGE_LIFECYCLE_MODE=hooks

# Access is fail-closed by default. Configure one or more IDs before activation;
# compatibility mode is an explicit legacy migration choice, never a default.
# CODEX_BRIDGE_OWNER_OPEN_ID=
# CODEX_BRIDGE_ADMIN_OPEN_IDS=
# CODEX_BRIDGE_ALLOWED_USER_OPEN_IDS=
# CODEX_BRIDGE_ALLOWED_CHAT_IDS=

# Knowledge sources, including Obsidian vaults, belong to the Responder Codex
# project's directory and are never configured by this bridge.
'@ | Set-Content -LiteralPath $envTarget -Encoding utf8
    Write-Output "Created $envTarget"
} else {
    $existingEnvironmentLines = @(Get-Content -LiteralPath $envTarget -Encoding utf8)
    # Exact pre-glossary names are admitted only in this one migration boundary.
    # They never become aliases accepted by the current runtime.
    $terminologyMigration = [ordered]@{
        'CODEX_BRIDGE_ROUTER_TIMEOUT' = 'CODEX_BRIDGE_BEEPER_TIMEOUT'
        'CODEX_BRIDGE_ROUTER_CLAIM_TTL' = 'CODEX_BRIDGE_BEEPER_CLAIM_TTL'
        'CODEX_BRIDGE_ROUTER_RETENTION_HOURS' = 'CODEX_BRIDGE_BEEPER_RETENTION_HOURS'
        'CODEX_BRIDGE_ROUTER_WAKE_TTL' = 'CODEX_BRIDGE_BEEPER_DIAL_TTL'
        'CODEX_BRIDGE_ROUTER_GRACE_MAX_SECONDS' = 'CODEX_BRIDGE_BEEPER_GRACE_MAX_SECONDS'
    }
    $retiredEnvironmentPattern = '^\s*(?:CODEX_BRIDGE_ROUTER_HEARTBEAT_TTL|CODEX_BRIDGE_GATEWAY_SCHEDULER_TTL)\s*='
    $currentEnvironmentLines = [System.Collections.Generic.List[string]]::new()
    $migratedRetiredNames = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::Ordinal
    )
    $environmentChanged = $false
    foreach ($line in $existingEnvironmentLines) {
        $candidate = [string]$line
        if ($candidate -match $retiredEnvironmentPattern) {
            $environmentChanged = $true
            continue
        }
        if ($candidate -notmatch '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$') {
            $currentEnvironmentLines.Add($candidate)
            continue
        }
        $name = [string]$Matches[1]
        $value = [string]$Matches[2]
        if (-not $terminologyMigration.Contains($name)) {
            $currentEnvironmentLines.Add($candidate)
            continue
        }
        if (-not $migratedRetiredNames.Add($name)) {
            throw "Bridge environment contains duplicate retired $name entries; refusing terminology migration."
        }
        $currentName = [string]$terminologyMigration[$name]
        $currentMatches = @(
            $existingEnvironmentLines | Where-Object {
                [string]$_ -match ('^\s*' + [regex]::Escape($currentName) + '\s*=(.*)$')
            }
        )
        if ($currentMatches.Count -gt 1) {
            throw "Bridge environment contains duplicate $currentName entries; refusing terminology migration."
        }
        if ($currentMatches.Count -eq 1) {
            [void]([string]$currentMatches[0] -match ('^\s*' + [regex]::Escape($currentName) + '\s*=(.*)$'))
            if ([string]$Matches[1] -cne $value) {
                throw "Bridge environment contains conflicting retired and current values for $currentName; refusing terminology migration."
            }
        } else {
            $currentEnvironmentLines.Add("$currentName=$value")
        }
        $environmentChanged = $true
    }
    if ($environmentChanged) {
        @($currentEnvironmentLines) | Set-Content -LiteralPath $envTarget -Encoding utf8
        Write-Output "Migrated retired terminology settings in $envTarget"
    } else {
        Write-Output "Preserved existing $envTarget"
    }
}

    }

    Remove-RetiredReadinessState
    Remove-RetiredPollingState
    Convert-RetiredStoppedHealthSnapshot
    Write-BridgeRuntimeManifest
    Update-InstalledStoppedHealthSnapshot

    if ($Force -and (Test-Path -LiteralPath $backupRoot)) {
        Write-Output "Previous bridge code was backed up to $backupRoot"
    }
}

if ($SkipHooks) {
    Write-Output 'Skipped .codex/hooks.json registration.'
    exit 0
}

if (Test-Path -LiteralPath $hooksConfigPath) {
    Backup-Target $hooksConfigPath
    Write-Output "Backed up the previous hook configuration to $backupRoot"
    $config = Get-Content -LiteralPath $hooksConfigPath -Raw -Encoding utf8 | ConvertFrom-Json
} else {
    $config = [pscustomobject]@{
        description = 'Lease-manage the local Feishu bridge while Codex Desktop is in use.'
        hooks = [pscustomobject]@{}
    }
}
if (-not $config.PSObject.Properties['hooks']) {
    $config | Add-Member -MemberType NoteProperty -Name hooks -Value ([pscustomobject]@{})
}

function New-CommandHook {
    param(
        [Parameter(Mandatory = $true)][string]$ScriptPath,
        [Parameter(Mandatory = $true)][int]$Timeout,
        [Parameter(Mandatory = $true)][string]$StatusMessage,
        [switch]$HookInvocation
    )
    $command = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $ScriptPath
    if ($HookInvocation) { $command += ' -HookInvocation' }
    return [pscustomobject]@{
        type = 'command'
        command = $command
        commandWindows = $command
        timeout = $Timeout
        statusMessage = $StatusMessage
    }
}

function Add-BridgeHook {
    param(
        [Parameter(Mandatory = $true)][string]$EventName,
        [Parameter(Mandatory = $true)][object]$Entry
    )
    $property = $config.hooks.PSObject.Properties[$EventName]
    # Do not assign an empty array through an `if` expression here. PowerShell
    # emits no pipeline object for that branch, turning the first `+=` into a
    # scalar PSCustomObject. Codex requires every event value to remain a JSON
    # matcher-group array even when it contains exactly one entry.
    $entries = @()
    if ($property) { $entries = @($property.Value) }
    $entries += $Entry
    if ($property) { $property.Value = $entries }
    else { $config.hooks | Add-Member -MemberType NoteProperty -Name $EventName -Value $entries }
}

function Test-BridgeCommandHook {
    param([object]$Hook, [Parameter(Mandatory = $true)][string]$ScriptPath)

    if (-not $Hook) { return $false }
    $baseCommand = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $ScriptPath
    foreach ($propertyName in @('command', 'commandWindows')) {
        $property = $Hook.PSObject.Properties[$propertyName]
        if (-not $property) { continue }
        $command = ([string]$property.Value).Trim()
        if ($command.Equals($baseCommand, [System.StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
        if ($command.StartsWith(($baseCommand + ' '), [System.StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
    }
    return $false
}

function Remove-BridgeHook {
    param(
        [Parameter(Mandatory = $true)][string]$EventName,
        [Parameter(Mandatory = $true)][string]$ScriptPath
    )

    $property = $config.hooks.PSObject.Properties[$EventName]
    if (-not $property) { return }
    $remainingEntries = @()
    foreach ($entry in @($property.Value)) {
        $hooksProperty = $entry.PSObject.Properties['hooks']
        if (-not $hooksProperty) {
            $remainingEntries += $entry
            continue
        }
        $remainingHooks = @(
            @($hooksProperty.Value) | Where-Object {
                -not (Test-BridgeCommandHook $_ $ScriptPath)
            }
        )
        if ($remainingHooks.Count -gt 0) {
            $hooksProperty.Value = $remainingHooks
            $remainingEntries += $entry
        }
    }
    $property.Value = $remainingEntries
}

Remove-BridgeHook 'SessionStart' $startTarget
Remove-BridgeHook 'SessionEnd' $stopTarget
Add-BridgeHook 'SessionStart' ([pscustomobject]@{
    matcher = 'startup|resume'
    hooks = @((New-CommandHook $startTarget 10 'Activating Feishu bridge lease' -HookInvocation))
})
Add-BridgeHook 'SessionEnd' ([pscustomobject]@{
    hooks = @((New-CommandHook $stopTarget 3 'Releasing Feishu bridge lease' -HookInvocation))
})

$hooksConfigTemporary = "$hooksConfigPath.tmp"
try {
    # Windows PowerShell 5.1's `Set-Content -Encoding utf8` writes a BOM.
    # Codex hook configuration is parsed as BOM-less JSON, so write explicit
    # UTF-8 without a BOM before atomically replacing the live file.
    $hooksJson = $config | ConvertTo-Json -Depth 30
    $utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($hooksConfigTemporary, $hooksJson, $utf8WithoutBom)
    Move-Item -LiteralPath $hooksConfigTemporary -Destination $hooksConfigPath -Force
} finally {
    Remove-Item -LiteralPath $hooksConfigTemporary -Force -ErrorAction SilentlyContinue
}
Write-Output "Registered lease-aware Feishu bridge hooks in $hooksConfigPath"
if ($HooksOnly) {
    Write-Output 'Hook-only refresh completed. Runtime code, bridge.env, and project rules were unchanged; exact stopped retired health metadata may have been migrated, and no runtime manifest was signed.'
}
