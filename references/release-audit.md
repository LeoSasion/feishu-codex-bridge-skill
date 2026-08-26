# Reproducible source release and external evidence

Read this reference only when preparing a source release, changing the release
inventory, adding fault tests, or accepting an external P0-B receipt. It does
not authorize install, upgrade, process lifecycle, Codex invocation, Gateway
work, or a dynamic test inside Codex Desktop.

## P0-A source audit

`assets/release-inventory.json` is the only path inventory. Counts are derived;
they are never a release authority. `.gitignore` is an audited source file, not
a security boundary.

From the Desktop Skill root, perform the full read-only audit with an explicit
Sibling root:

```powershell
& .\scripts\audit-feishu-codex-release.ps1 `
  -DesktopRoot . `
  -HarnessRoot ..\feishu-codex-harness-bridge
```

The script walks only inventory-owned paths. It never descends into `.git`,
`.codex`, `.agents/skills`, the independent
`plugins/human-authorization-relay` coexistence project, the exact root `.tmp`
local-tooling tree, `_retired`, or test/cache exclusions. The P0
`.agents/plugins/marketplace.json` and
`plugins/feishu-codex-final-return` source are explicitly inventoried and copied
into the Bridge snapshot; the audit makes no claim about the excluded sibling
plugin or `.tmp`. The script rejects
unknown files or directories, reparse points, runtime artifacts, non-UTF-8 or
binary content, unfinished scaffold markers, broken local Markdown links,
unbalanced fences, real-shaped task/Feishu IDs, high-confidence credentials,
and non-fixture absolute local paths. Findings show only rule, component,
relative path, line, and a candidate fingerprint; they never echo the value.

The JSON result contains each normalized relative path, raw-file SHA-256, and
byte size. Its stable digest is SHA-256 over UTF-8 without BOM:

```text
feishu-codex-source-manifest-v1\n
<component>\t<relative/path>\t<lowercase-raw-file-sha256>\n
...
```

Records use ordinal order and `/` separators. A second snapshot must match the
first, and the initially parsed inventory bytes must match the inventory record
inside both snapshots, so a source mutation during audit fails closed. Roots,
their ancestors, traversed entries, and any output parent must be FileSystem
paths with no reparse point. Inventory path count and aggregate retained bytes
are bounded per component. If the excluded workspace `AGENTS.md` mirror exists,
its presence and bounded hash are checked both before and after the second
snapshot against the canonical audited rules. `-OutputPath` is optional;
when used, it must be outside both Skill roots, both destination and `.pending`
must be absent, and publication uses create-new plus atomic rename. It never
overwrites evidence.

`bridge validate` invokes the same audit in Desktop-only mode and retains its
existing behavior-marker checks. Full release acceptance always requires the
explicit Sibling root and both components in the result.

## Fault and race contract

P0-B must execute every row outside Codex Desktop with the Listener stopped.
“Covered” means a matching source test exists; it is not a claim that this
machine has dynamically run it. “External scenario” remains a required P0-B
supervisor check even when related unit coverage exists.

| ID | Fault injection point | Expected durable state | Replay decision | Test contract | Coverage |
| --- | --- | --- | --- | --- | --- |
| F01 | An identical producer has passed preflight when the first producer publishes and the request is claimed | Both producers return the same ID; one immutable canonical `pending` file and one atomically published, fully fenced `claimed` file remain | Exclude the canonical request from later claims; a second claim returns empty and the request is never republished or executed twice | `test_identical_producer_overlap_cannot_republish_claimed_request`; `test_exclusive_claim_publication_keeps_canonical_pending` | Source tests present |
| F02 | A legacy or damaged claimed record has no fence after its owner/wake disappears | Claim terminalizes as `legacy_unfenced_claim`, `retryable=false`, `may_have_started=true` | Never replay | `test_legacy_unfenced_claim_is_terminalized_as_uncertain` | Source test present |
| F03 | Receipt payload is exclusively published, then compatibility-marker open or descriptor close is fault-injected to fail and the disposable cache is absent | Receipt payload remains the authoritative first terminal result and the finalizer reports the committed outcome | Return the same terminal result; only an explicit safe failure may create a generation | `test_receipt_payload_without_marker_is_authoritative_and_not_replayed`; `test_receipt_payload_survives_marker_descriptor_close_failure` | Source tests present; external run pending |
| F04 | Orphan marker exists without a receipt payload and the claim becomes stale | Recover exactly one permanent unknown receipt that survives cleanup | `may_have_started=true`; never replay | `test_orphan_terminal_receipt_recovers_as_unknown_and_survives_cleanup` | Source test present |
| F05 | `complete` and `fail` finalizers race | Exactly one valid first receipt; no later result overwrites it | Use only the first terminal outcome | `test_concurrent_terminal_finalizers_preserve_first_receipt` | Source test present; external run pending |
| F06 | Wake SQLite write fails during producer submission, then becomes available | One complete pending file remains authoritative; generation metadata may be temporarily absent | The next probe reconciles and claims the same request once | `test_wake_database_lock_preserves_pending_and_reconciles_once` | Source test present; external run pending |
| F07 | Two producers publish the same operation/key with different payloads concurrently | Exactly one fingerprint wins; no partial or overwritten request | Losing producer receives a protocol conflict and creates no generation | `test_concurrent_conflicting_producers_publish_one_fingerprint` | Source test present; external run pending |
| F08 | Generation zero ends in an allowed explicit safe failure, including an abandoned read-only inspect claim after its five-minute TTL | Generation-zero receipt/tombstone and deterministic generation-one pending request both remain; an equally old mutating claim stays nonterminal under its long TTL | Advance exactly once only for the explicit safe/read-only failure; repeated submit returns generation one | `test_explicit_safe_failure_advances_one_retry_generation`; `test_retry_generation_ancestry_survives_response_cleanup`; `test_stale_read_only_claim_advances_retry_generation`; `test_mutating_claim_keeps_long_ttl_when_read_claim_would_expire` | Source tests present |
| F09 | Lifecycle/unknown/disabled code or malformed booleans claim retryability | Generation zero remains the only terminal result | Never advance | `test_target_lifecycle_failure_never_advances_retry_generation`; `test_retry_generation_requires_explicit_json_booleans` | Source tests present |
| F10 | The confirmed `/init` new-project action returns an unknown create outcome immediately after persisting its exact staged marker | Event key, name, exact direct-child root, and original create request key remain unchanged | Only the same event may resume that exact root and clear all three marker fields after success | `test_fresh_project_marker_precedes_unknown_create_and_same_event_recovers`; `test_same_event_resumes_exact_pending_project_marker` | Source tests present; external run pending |
| F11 | A different event arrives while a project marker is unresolved | Original marker and directory remain unchanged; no second task/directory | Refuse adoption and await exact-event recovery or review | `test_different_event_cannot_overwrite_a_pending_project_marker` | Source test present |
| F12 | Duplicate, empty, malformed, or out-of-range recognized env values reach dispatcher, start chain, and queue helper | No Listener/PID/lease/claim and no `bridge.env` rewrite | Repair configuration; do not retry data-plane work | `test_malformed_bridge_env_fails_closed_at_dispatcher_start_and_queue_entrypoints` plus external supervisor scenario | Source test present; external run pending |

The three F12 entrypoints are the dispatcher
`scripts/feishu-codex-bridge.ps1`, the Listener start chain beginning at
`scripts/start-feishu-codex-bridge.ps1`, and
`scripts/router_queue.py`. A marker-only source check is not dynamic proof.

## P0-B hash-bound create-new receipt

The versioned schema is
`assets/external-test-evidence.schema.json`. The same external supervisor must:

1. run the full P0-A audit, bind its exact manifest/path-set/inventory/fault
   hashes, pin every audited Desktop and Harness source file without write/delete
   sharing, create a create-new external snapshot of every audited Desktop file,
   pin every snapshot file, verify each copied target byte hash and exact path
   set before and after the tests (and again before schema use), and run tests
   only from that snapshot;
2. acquire the current-user, project-derived `Global\` lifecycle mutex before
   the pre-check and retain it through the post-check, second audit, and receipt
   publication. Both installed lifecycle hooks and `.codex/hooks.json` are
   opened read-only without write/delete sharing, must byte-match the audited
   mutex-aware source and exact unique registration, and remain pinned for the
   complete window;
3. prove before, after, and immediately before evidence assembly that `bridge
   status` reports exactly `Runtime: stopped`, the PID is absent or stale, and
   no exact Listener command line is alive;
4. place the snapshot, test temp, stdout/stderr capture, and evidence outside both
   source and installed runtime. Every root must be an ordinary local DOS-drive
   path: UNC/device namespaces plus SUBST, mapped, and other non-local drive aliases
   are refused; existing prefixes are resolved through Win32 file handles so 8.3
   and equivalent physical paths compare identically; every reparse point in each
   path chain is rejected;
5. require the exact audited supervisor to start under `pwsh -NoLogo -NoProfile
   -NonInteractive -ExecutionPolicy Bypass -File`, import built-in modules from
   that exact PowerShell home, inspect itself plus at least one live parent and
   reject any observed Codex ancestor, record whether older ancestry ended at
   the process-tree root or an already-exited ancestor, pin a PSF Authenticode-
   valid `python.exe`, remove inherited `PYTHON*`, `CODEX_BRIDGE_*`, `FEISHU_*`,
   and `LARK*` variables, then run the exact external test argv with Python
   `-I -S -B`,
   `FEISHU_BRIDGE_EXTERNAL_TEST_RUNNER=1`, `PYTHONDONTWRITEBYTECODE=1`, and an
   isolated `FEISHU_BRIDGE_TEST_TMP`;
6. execute the whole discovered suite through the audited structured test driver,
   require its create-new nonce-bound `unittest.TestResult` JSON to contain all
   exact 19 test IDs behind F01-F12 with no failures/errors, and place each child
   in a KILL_ON_JOB_CLOSE Windows Job with bounded timeout and pipe closure;
7. compare the bounded runtime/control-file manifest before, after, and at final
   evidence assembly; rerun the complete P0-A audit and require its canonical
   JSON digest to equal the pre-test audit; and
8. validate the final receipt against the audited JSON Schema before publishing
   `p0b-v1-<new-receipt-id>.json` using exclusive create. A rerun always
   gets a new ID and never changes or deletes an earlier receipt.

After the file is closed and flushed, compute its whole-file SHA-256 and submit
only a small envelope containing the basename, hash, schema version, and runner
status. Do not copy the receipt, test temp, or output back into either Skill.
JSON Schema validates shape and constants only. It cannot establish cross-field
equality or prove execution. P0-B passes only when the supplied file also passes
`scripts/validate-external-p0b-evidence.ps1`, which pins and rehashes the receipt,
retained snapshot, structured result and captures; recomputes F01-F12 evidence,
mutex/SID, current hooks, Python/PowerShell, current P0-A, and bounded runtime;
and checks all cross-field and timestamp relations. This is intentionally a
current-environment revalidation: the retained work directory, exact toolchain,
hooks, and bounded runtime must still exist and match.

The maintained supervisor is `scripts/run-external-p0b.ps1`. Codex Desktop may
prepare its exact argument values but must never invoke it. From an external
terminal, first set `FEISHU_BRIDGE_EXTERNAL_TEST_RUNNER=1`, then pass explicit
Desktop source, Harness source, live project, Python executable, empty external
work parent, evidence directory, and `-ExternalTestRunnerAcknowledged`. The
external supervisor and semantic validator require clean PowerShell 7.4+ `-File`
invocations and prove that their
`Test-Json -SchemaFile` enforces draft-2020-12 `$defs` and `const` before any
dynamic test. Windows PowerShell 5.1 remains supported by the bridge runtime,
but is not an evidence-validator surface. The supervisor never
stops the Listener: a live or unknown pre/post observation aborts without a
passing receipt. Work captures remain outside source for diagnosis; evidence
publication is create-new and hash-bound; it is not filesystem immutability.
All path arguments must resolve on ordinary local drive letters. Do not use UNC,
`\\?\`/`\\.\` device syntax, SUBST drives, mapped drives, or reparse-point aliases.

For `4.2.0-alpha.30`, the discovered suite must also exercise the P0 Hook
boundary: exact arm/task/turn/raw-prompt-hash binding; strict registered-Gateway
delegation-wrapper binding with unchanged inner Unicode input; ignored unarmed,
wrong-source, malformed, and mismatched events; answer-free Hook-observation
diagnostics; same-turn Stop continuation replacement; late-Hook fencing after
native selection; terminal completion fencing; and Chinese punctuation plus
emoji roundtrip. Static shape alone is insufficient; the retained structured
result must show those tests passed with no failure, error, or skip.
The same release must statically require top-level direct
`mcp__codex_app` task methods in the Gateway, heartbeat, manual-cycle, bootstrap,
and model-preflight assets; it must also reject the retired pattern of invoking
`codex_app__*` through `functions.exec`, `ALL_TOOLS`, or `tools[...]`. P0-B/P3
remain stopped external tests and do not themselves certify a live Desktop MCP
surface.
Before deploying over a pre-alpha.28 runtime, the source dispatcher must also
prove that read-only `bridge final-return-status` returns an answer-free
`upgrade_required` contract without dispatching the unsupported old helper
subcommand. Registration remains unavailable until the separately approved
runtime upgrade succeeds.

Prefer the maintained one-shot wrapper when running manually. It creates unique
work/evidence roots, launches both audited scripts through clean PowerShell, stops
at the first failure, and never attempts semantic validation without a valid
supervisor envelope:

```powershell
& <pwsh-7.4-or-newer.exe> -NoLogo -NoProfile -NonInteractive `
  -ExecutionPolicy Bypass -File <desktop-root>\scripts\invoke-external-p0b-once.ps1 `
  -PythonExecutable <psf-python.exe> -ExternalTestRunnerAcknowledged
```

Pass `-HarnessRoot`, `-ProjectRoot`, or `-DesktopRoot` only when their defaults do
not match the external lab. The wrapper retains failed work and prints its exact
directories; on success it preserves the supervisor envelope fields and adds
the exact `evidence_path` needed by a later P3 run. Do not continue with envelope
parsing after a failure.

When one external acceptance window should run P0-B and then P3, prefer the
maintained combined entry point:

```powershell
& <pwsh-7.4-or-newer.exe> -NoLogo -NoProfile -NonInteractive `
  -ExecutionPolicy Bypass -File <desktop-root>\scripts\invoke-external-p0b-p3-once.ps1 `
  -PythonExecutable <psf-python.exe> -Iterations 25 -TimeoutSeconds 300 `
  -ExternalSuiteAcknowledged
```

It invokes the two existing one-shot wrappers, keeps their stderr separate from
their JSON stdout, hands P3 only the validated P0 evidence path and SHA-256, and
emits one combined JSON object on success. On failure it surfaces the last
bounded child diagnostics, including retained work/evidence paths, and does not
start the next stage. Invoke it directly; do not wrap it in `2>&1` plus a second
bare exit-code exception, which hides the useful child failure.

For a command handed to a user in chat, do not depend on those defaults: render
one physical line with absolute PowerShell, Python, wrapper, Desktop, Harness,
and Project paths. Do not split setup into session variables or backtick-
continued fragments; a reopened PowerShell loses the variables and chat clients
can alter continuation lines. The wrapper owns unique directory creation,
exit-code gating, envelope parsing, and semantic-validator sequencing. After
any nonzero exit, stop and use only the retained diagnostic path and surfaced
failure IDs; never run `ConvertFrom-Json`, `Join-Path`, or the validator on an
empty/failed envelope.

Use this command shape in that external terminal; do not shorten it to `&
.\scripts\run-external-p0b.ps1`, because the script deliberately verifies the
clean host flags:

```powershell
$env:FEISHU_BRIDGE_EXTERNAL_TEST_RUNNER = '1'
& <pwsh-7.4-or-newer.exe> -NoLogo -NoProfile -NonInteractive `
  -ExecutionPolicy Bypass -File <desktop-root>\scripts\run-external-p0b.ps1 `
  -DesktopRoot <desktop-root> -HarnessRoot <harness-root> `
  -ProjectRoot <project-root> -PythonExecutable <psf-python.exe> `
  -ExternalWorkRoot <empty-external-work-root> `
  -EvidenceDirectory <external-evidence-directory> `
  -RunnerSurface external_terminal -ExternalTestRunnerAcknowledged
```

Then validate the emitted file and exact envelope hash from another clean
invocation while the retained work directory and current environment still
match:

```powershell
& <pwsh-7.4-or-newer.exe> -NoLogo -NoProfile -NonInteractive `
  -ExecutionPolicy Bypass -File <desktop-root>\scripts\validate-external-p0b-evidence.ps1 `
  -DesktopRoot <desktop-root> -HarnessRoot <harness-root> `
  -ProjectRoot <project-root> -EvidencePath <external-evidence-file> `
  -ExpectedEvidenceSha256 <lowercase-envelope-sha256>
```

Neither receipt nor validator output is a signature or cryptographic attestation.
The contract trusts the selected local PowerShell installation and Python
standard library; PSF Authenticode covers `python.exe`, not every loaded stdlib
file. KILL_ON_JOB_CLOSE covers assigned child trees, but a residual race exists
between `Process.Start()` and `AssignProcessToJobObject()`, and brokered processes
can escape a Job. A process chain that reaches an already-exited older ancestor
is recorded as incomplete; explicit external-origin acknowledgement plus the
inspected live chain is not proof about that missing ancestor. The lifecycle
mutex covers only the exact registered Bridge
start and stop hooks. Concurrent administrative mutation, an alternate manual
launcher, or direct `bridge.py` execution is outside this evidence contract and
must be excluded operationally.

## P3 bounded stopped soak

P3 begins only after the exact same source version has a fresh P0-B receipt and
independent semantic-validator pass. Read
[p3-bounded-soak.md](p3-bounded-soak.md). The maintained external entry point is
`scripts/invoke-external-p3-soak-once.ps1`; it binds the exact P0 evidence file
and SHA-256, then sequences the P3 supervisor and independent validator.

The P3 supervisor must reuse P0-B's retained `source-snapshot`, validate the P0
receipt before and after the run, reject an older snapshot that lacks the P3
contract, and keep every audited Desktop snapshot file pinned read-only without
write/delete sharing through runner execution and post-validation. It runs
exactly ten fixed concurrency/recovery/delivery tests for an
explicit 1-100 iterations under a 30-900 second hard timeout. The runner itself
forbids child-process creation and records zero live Desktop and Feishu contact;
the supervisor additionally contains every child in a Windows
KILL_ON_JOB_CLOSE Job. The Listener remains stopped throughout.

Publish only `p3-soak-v1-<new-receipt-id>.json` with create-new semantics. The
schema at `assets/external-p3-soak-evidence.schema.json` checks shape and fixed
guards only. Acceptance additionally requires
`scripts/validate-external-p3-soak-evidence.ps1` to rehash retained artifacts,
recompute the exact ordered scenario mapping and `iterations * 10` relations,
revalidate the bound P0 evidence/current source manifest, and reject every
failure, error, skip, timeout, child-process attempt, or live-surface marker.

P3 evidence is not a signature, cryptographic attestation, or live compatibility
result. It does not override `scheduler_cap_unenforced`,
`target_final_readback_unavailable`, or another terminal Desktop-build marker,
and it authorizes no Listener start, Gateway activation, or production schedule.

## P1 stopped migration rehearsal

P1 begins only after the P0-B semantic validator passes. Read
[p1-isolated-migration.md](p1-isolated-migration.md) and use the audited
`scripts/external-p1-migration-lab.ps1` from an external clean PowerShell
7.4+ terminal. The lab is a create-new current-user project by default; a
separate Windows account is optional and requires an explicit user decision.

The lab tool owns only fixture preparation, read-only observation, and a
quarantine-based rollback. It must not invoke `bridge hooks`, `bridge upgrade`,
start/restart, Codex CLI, App Server, Gateway, scheduler, or deletion. Hook-only
refresh and runtime-only upgrade are direct public dispatcher invocations under
two later approvals. The source validator parses the lab script, checks its
external-origin, PSF Python, stopped-Listener, state-canary, baseline-manifest,
and quarantine markers, and rejects embedded administrative stages. Dynamic
execution remains prohibited inside Codex Desktop.

Acceptance requires passing prepared, hooks_refreshed, upgraded, and rolled_back
observations. The upgraded phase must bind all runtime and hook hashes in the
alpha.4 manifest while preserving locked access, the unrelated hook, the
session binding, and the pending queue canary. Rollback must byte-match the
pinned alpha.2 baseline and retain the upgraded tree for recovery. P1 output is
local test evidence, not a signature or cryptographic attestation.
