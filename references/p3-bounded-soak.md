# P3 bounded soak

P3 is a stopped, external, bounded regression soak for queue and delivery
invariants. It does not contact Codex Desktop, the Gateway scheduler, Feishu, or
the live Listener. It is evidence for repeated local concurrency and recovery
behavior, not evidence that a Desktop build can return a target final answer.

## Entry gate

Run P3 only after the same source version has a fresh passing P0-B receipt and
an independent semantic-validator pass. Supply the exact retained P0-B evidence
file and its lowercase SHA-256. The P3 supervisor validates that receipt before
and after the soak and runs the scenarios from P0-B's retained
`source-snapshot`; an older snapshot that lacks the P3 contract is rejected.
All audited Desktop snapshot files remain open read-only without write/delete
sharing for the runner and post-validation window, and their pinned count is
bound to the P0 receipt.

The live Listener must remain stopped. Use ordinary local drive paths only. Do
not use UNC, device, mapped, SUBST, or reparse-point paths. Codex Desktop must
never invoke the P3 supervisor, runner, or validator. The supervisor inspects
itself and at least one live parent, rejects a Codex Desktop/CLI ancestor, and
records the bounded ancestry termination plus the declared external/CI surface.
Both the supervisor and independent validator must walk file paths through
`FileInfo.Directory` and directory paths through `DirectoryInfo.Parent`. The
external P0-B suite extracts each maintained helper and probes one ordinary file
plus one ordinary directory before a same-source receipt can be accepted.

## Fixed scenario contract

Each iteration runs exactly these ten tests in this order:

| Scenario | Invariant |
| --- | --- |
| `scheduler_overlap` | Concurrent metadata probes reserve exactly one wake. |
| `identical_producer_overlap` | Duplicate producers cannot republish a claimed request. |
| `conflicting_producer_overlap` | Conflicting producers retain one fingerprint. |
| `terminal_finalizer_race` | Concurrent finalizers preserve the first terminal receipt. |
| `long_task_lease` | A fresh active-work heartbeat protects a long claim. |
| `long_task_retention` | Retention never deletes a nonterminal long claim. |
| `listener_pre_model_restart` | Work that never reached the model remains retryable. |
| `listener_post_model_restart` | A started model turn is not replayed after restart. |
| `feishu_rate_limit_network_retry` | Rate-limit and network failures remain retryable. |
| `withdrawn_message_terminal` | A terminal reply failure is not rescheduled. |

The default is 25 iterations. The hard contract allows 1-100 iterations and a
30-900 second timeout; the maintained wrapper defaults to 300 seconds. The
runner forbids child-process creation, writes no stdout, retains progress on
stderr, and records that neither live Desktop nor live Feishu was contacted.
Its `subprocess.Popen` guard remains a class so Python 3.13 modules such as
`asyncio.windows_utils` can safely subclass it, while every construction still
raises before the original process constructor runs and increments the attempt
counter. P0-B exercises that import-and-construction boundary in an isolated
interpreter before a same-source receipt can pass.
The supervisor additionally places every child in a Windows
`KILL_ON_JOB_CLOSE` Job and publishes evidence with create-new semantics.

## Maintained external entry point

If a fresh P0-B and P3 are both required, prefer the combined suite wrapper. It
runs the two P0-B gates first, passes P3 only their exact evidence path/SHA, keeps
child stderr separate from JSON stdout, and returns one combined JSON object:

```powershell
& <pwsh-7.4-or-newer.exe> -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File <desktop-root>\scripts\invoke-external-p0b-p3-once.ps1 -PythonExecutable <psf-python.exe> -DesktopRoot <desktop-root> -HarnessRoot <harness-root> -ProjectRoot <project-root> -Iterations 25 -TimeoutSeconds 300 -ExternalSuiteAcknowledged
```

When a same-source validated P0 receipt already exists, use the standalone P3
one-shot wrapper. Render it as one physical line with absolute paths when handing
it to a user:

```powershell
& <pwsh-7.4-or-newer.exe> -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File <desktop-root>\scripts\invoke-external-p3-soak-once.ps1 -PythonExecutable <same-psf-python-used-by-p0b.exe> -P0EvidencePath <p0b-evidence.json> -ExpectedP0EvidenceSha256 <lowercase-sha256> -DesktopRoot <desktop-root> -HarnessRoot <harness-root> -ProjectRoot <project-root> -Iterations 25 -TimeoutSeconds 300 -ExternalSoakAcknowledged
```

The wrapper creates unique work and evidence roots, then performs exactly:

1. clean-PowerShell P3 supervisor;
2. envelope and exit-code gate;
3. independent clean-PowerShell P3 semantic validator;
4. exactly two compact JSON output lines on success. The first preserves the
   supervisor envelope and adds the exact local `evidence_path`.

After a nonzero exit, stop. Use the retained work/evidence paths printed by the
wrapper; do not parse an absent envelope or invoke the validator with empty
arguments.

## Acceptance

Accept a run only when both lines say `status`/`runner_status=pass`, the evidence
hash matches, and the independent validator reports:

- all ten scenarios passed every requested iteration;
- total tests equal `iterations * 10` with no failure, error, or skip;
- no child-process attempt, timeout, Desktop contact, or Feishu contact;
- retained result/stdout/stderr hashes still match;
- the bound P0-B evidence and current source manifest revalidate.

The JSON Schema checks shape only. The independent validator recomputes the
scenario mapping and cross-field relations. Neither output is a signature or a
cryptographic attestation. A passing P3 soak does not authorize a Listener
start, Gateway activation, production scheduling, or a repeated live diagnostic
on a Desktop build that already has a terminal compatibility marker.

The validator's pinned-handle list is intentionally empty before its first
file is opened. Its collection parameter must explicitly allow that empty input;
P0-B extracts the helper, passes a new empty `List[FileStream]`, pins a zero-byte
file as the first handle, and verifies the handle before disposal.

PowerShell versions may deserialize JSON `date-time` strings either as strings
or as `DateTime` values. The validator must preserve the already-deserialized
ticks instead of culture-formatting them back to strings. P0-B verifies the
same seven-digit fractional timestamp through both object and string inputs;
the strict wall-clock versus monotonic-duration relation keeps its 0.01-second
tolerance.
