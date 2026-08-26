# P1 isolated alpha.2 to alpha.4 migration rehearsal

Use this procedure only after P0-A and the external P0-B semantic validator pass.
P1 proves the stopped-runtime migration path in a disposable project. It does
not change the live project, start a Listener, create or register a Gateway,
change a scheduler, or send a Feishu message.

## Isolation decision

A separate Windows account is optional and is never created implicitly. On a
disposable VM, the default is a create-new project directory under the current
Windows account because the runtime, hooks.json, hooks, queue, bindings, and
rollback baseline are all project-local. If the user asks for stronger
OS-identity isolation, explain the cost and ask before creating an account.

The lab project must be physically separate from the Skill source, use an
ordinary local DOS-drive path, contain no reparse point, and not already exist.
The external runner rejects Codex Desktop/CLI ancestry, requires clean
PowerShell 7.4+, and pins a Python Software Foundation Authenticode-valid
python.exe. Run every stage from an independent terminal, never from Codex
Desktop.

## Approval and stage contract

Each row is a separate checkpoint. Name the exact lab path, files or process
affected, interruption risk, and recovery path. Consent for one row does not
authorize the next.

| Stage | Exact effect | Expected observation |
| --- | --- | --- |
| Prepare | Create one lab project, copy the pinned alpha.2 runtime and legacy hooks, create synthetic locked access plus queue/binding canaries, and pin a rollback baseline | phase=prepared, Listener stopped, alpha.2, legacy hooks, no manifest, one pending canary |
| Hook refresh | Run public bridge hooks against only the lab project | phase=hooks_refreshed, current hooks, alpha.2 runtime, no manifest, all canaries preserved |
| Runtime upgrade | Run public bridge upgrade against only the lab project | phase=upgraded, alpha.4, valid manifest, source/runtime parity, all canaries preserved |
| Rollback | Verify the pinned baseline, create a durable single-use intent, move the upgraded .codex tree to a timestamped quarantine, and atomically restore the alpha.2 tree | phase=rolled_back, legacy hooks and alpha.2 restored; exactly one quarantine and the intent retained |

Preparation and rollback are performed by
scripts/external-p1-migration-lab.ps1. The tool deliberately has no hook
refresh, runtime upgrade, start, restart, Gateway, scheduler, Codex CLI, App
Server, or deletion command. The observe action reads files and process metadata
only; it does not call the queue helper or open SQLite through a mutating API.

Python isolated mode does not implicitly add the helper's directory to
`sys.path`. The runner therefore keeps `-I -S -B`, passes the pinned runtime
as a real argument, inserts only that exact directory, and executes the copied
helper through a fixed `runpy.run_path` bootstrap. Do not regress to direct
`python -I router_queue.py`, ambient `PYTHONPATH`, or a shell-composed command.
Preparation is assembled under `<lab-root>.preparing` and published with a
same-parent directory rename only after all canaries and the baseline manifest
exist. A failed staging directory is retained for diagnosis; it is never
silently adopted, overwritten, or deleted.

Ordinary rollback is single-use. It is allowed only from an exact passing
`upgraded` observation with no existing quarantine or rollback guard. Before
copying a restore candidate, the runner atomically creates
`.p1-control/rollback-intent.json`; any replay, concurrent attempt, partial
failure, extra quarantine, or already-restored alpha.2 state fails closed for
manual review. A successful `rolled_back` observation requires exactly one
quarantine and that durable guard. Never treat `quarantine_count > 1` as a pass.

## External command shapes

Choose a create-new lab root and use the alpha.2 runtime plus legacy-hook
backup roots recorded in the handoff. First prepare:

~~~powershell
$env:FEISHU_BRIDGE_EXTERNAL_MIGRATION = '1'
& <pwsh-7.4.exe> -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File <desktop-root>\scripts\external-p1-migration-lab.ps1 -Action prepare -DesktopRoot <desktop-root> -LabProjectRoot <lab-root> -PythonExecutable <psf-python.exe> -Alpha2RuntimeRoot <alpha2-runtime-root> -LegacyHooksRoot <legacy-hooks-root> -ExternalMigrationAcknowledged
~~~

Observation is a separate read-only command:

~~~powershell
& <pwsh-7.4.exe> -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File <desktop-root>\scripts\external-p1-migration-lab.ps1 -Action observe -DesktopRoot <desktop-root> -LabProjectRoot <lab-root> -PythonExecutable <psf-python.exe> -ExternalMigrationAcknowledged
~~~

After a fresh approval for the hook-only administrative action:

~~~powershell
& <pwsh-7.4.exe> -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File <desktop-root>\scripts\feishu-codex-bridge.ps1 bridge hooks -ProjectRoot <lab-root>
~~~

Run observe and require phase=hooks_refreshed plus status=pass. Only then, after
another fresh approval, perform the runtime-only upgrade:

~~~powershell
& <pwsh-7.4.exe> -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File <desktop-root>\scripts\feishu-codex-bridge.ps1 bridge upgrade -ProjectRoot <lab-root>
~~~

Run observe and require phase=upgraded, runtime_manifest_valid=true, and
status=pass. bridge doctor for the lab may be captured as a separate read-only
diagnostic; it must still report the Listener stopped and source/runtime parity
current.

Finally, only after a separate rollback approval:

~~~powershell
& <pwsh-7.4.exe> -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File <desktop-root>\scripts\external-p1-migration-lab.ps1 -Action rollback -DesktopRoot <desktop-root> -LabProjectRoot <lab-root> -PythonExecutable <psf-python.exe> -ExternalMigrationAcknowledged
~~~

Retain the timestamped after-upgrade quarantine until the rehearsal has been
reviewed. Removing the whole disposable lab or its quarantine is a later,
explicit cleanup action.

## Acceptance

P1 passes only when all four phase observations are supplied and independently
consistent: every phase is stopped, the exact unrelated hook survives, locked
access remains valid, the sessions and pending request hashes remain unchanged,
the Bridge SessionStart/SessionEnd registrations remain array-shaped and unique,
the upgrade manifest binds all runtime files and both hooks, and rollback
matches the pinned baseline manifest with exactly one quarantine and its
single-use rollback intent. These local observations are test
evidence, not a signature or cryptographic attestation. A failure stops at the
current stage; never compensate by starting the Listener or broadening an
approval.
