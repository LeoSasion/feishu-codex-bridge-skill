# Reproducible source release and external evidence

Use this reference when choosing validation for a change, preparing a source
release, changing the release inventory or test contract, or accepting external
Gate B evidence. It describes evidence boundaries; it does not authorize an
install, process lifecycle action, Codex invocation, Beeper action, or live send.

The current runtime admits only the isolated
`beeper` local producer described by the architecture contract.
All dynamic checks described here are stopped, isolated tests: they do not use
that producer, publish queue work, arm a Final Callback, invoke a Beeper,
contact Feishu, or certify live final-callback compatibility.

## Human map and machine authority

Human validation is organized into four impact-based lanes:

- **Smoke**: the smallest current-path check that detects an unusable build or
  broken entry boundary.
- **Contract**: schema, parser, inventory, capability-shape, and policy checks.
- **Fault**: representative failure, race, fencing, recovery, and no-replay checks.
- **Soak**: bounded repetition of concurrency, persistence, retry, and transport
  invariants after Gate B has passed.

This map keeps the development loop proportional to the change. It does not
change the maintained release gate. The current full external P0-B runner remains
the authoritative Gate B dynamic suite for release acceptance, and its machine
registry remains authoritative for exact tests and required mappings. Human docs
must not duplicate that registry.

The maintained development fast lane runs exactly 56 current-path tests: 12
Smoke, 25 Contract, and 19 representative current-route Fault tests. It exists to
shorten feedback while the Bridge is stopped. It automatically removes its
temporary state and publishes no release evidence; a pass cannot be promoted,
wrapped, or cited as Gate B or P3 evidence.

Any selected dynamic lane still runs only through the audited clean-PowerShell
external supervisor while the exact Bridge is verified stopped. Never run raw
`unittest`, `pytest`, `bridge test -RunTests`, or a fake App Server in Codex Desktop.

## Impact selector

| Change impact | Development loop | Required acceptance |
| --- | --- | --- |
| Markdown, links, or comments with no executable policy change | Gate A only; do not interrupt the inner loop for dynamic evidence | Current release receipts still bind every inventory byte, so a release candidate needs fresh Gate B |
| Skill, AGENTS, plugin metadata, or inventory policy | Gate A plus affected Contract checks | Gate A; reinstall and verify in a new task when packaging changes |
| Executable code, runtime configuration, generated schema, parser, capability adapter, or test contract | Affected Smoke, Contract, and Fault checks | Fresh full Gate B |
| Concurrency, persistence, retry/no-replay, fencing, Final Callback, outbox, or transport | Affected Contract and Fault checks | Fresh full Gate B plus P3 |
| Release candidate | All affected lanes | Gate A and fresh full Gate B; add P3 only when a durability/transport boundary above changed |

The current evidence contracts bind every inventory file, so any inventory-byte
change—including documentation—means an older receipt is not exact-source evidence
for the changed tree. That does not justify interrupting a documentation development
loop: run Gate A, mark the prior receipt as a baseline, and obtain fresh Gate B only
when the tree becomes a release candidate. A future behavior-manifest split would
need its own schema and validator; it is not implemented by this policy text.

P0-B is required when executable code, runtime configuration, schemas, or the test
contract changes, and for every release candidate. P3 is required only for changes
that affect concurrency, persistence, retry, fencing, outbox, Final Callback transport,
or an equivalent durable boundary. An unrelated release candidate does not require
P3. Ordinary development does not run P3.

## Stable invariant families

The exact tests live in machine registries. Release reasoning uses these stable
families instead of incident numbers, test IDs, or version-specific bug stories.

| Family | Required invariant |
| --- | --- |
| Source authority | Only the canonical package root, release inventory, manifest, and repository Marketplace route can identify development and release input. Cache and retained snapshots are never editable source. |
| Lifecycle isolation | Dynamic evidence comes from an external clean supervisor with the exact Bridge stopped, bounded child containment, isolated temporary state, and no live Desktop, Beeper, scheduler, Bridge, or Feishu contact. |
| Identity and fencing | Operation, event, request, generation, claim, fence, Responder, Final Callback capability, and receipt identities remain bound across every state transition; stale or mismatched actors fail closed. The bearer capability does not manufacture a product turn identity. |
| Idempotency and terminality | Duplicate work converges on one canonical request and one first terminal outcome; conflicts never overwrite durable state. |
| Retry and replay | Only an explicit retryable outcome known not to have started may advance once; unknown, lifecycle, responder-started, and attachment-uncertain outcomes never replay. |
| Final provenance and fidelity | The local producer may complete only from the exact fenced Responder-owned Final Callback with `final_callback_source=final_callback`; the Beeper cannot submit. Authoritative text, Unicode, whitespace, and immutable outbound planning survive without native/readback/UI/database/shell fallback. The bearer capability does not attest product caller or turn. |
| Persistence and retention | Restart and cleanup preserve unresolved claims, ancestry, staging, and sealed outbox state; terminal reconciliation scrubs answer-bearing material without deleting unresolved work. |
| Beeper role and task ownership | One isolated Beeper may contact Desktop Responders only; each Responder retains its project, context, tools, execution, and authoritative final, and no alternate Responder client is allowed. |
| Evidence integrity | Retained source and outputs are hash-bound and pinned across the run, receipts publish create-new, and an independent semantic validator recomputes cross-field relations. |

Adding a test should strengthen one of these families or introduce a genuinely new
cross-version invariant. Tests that exercise a superseded executable route are
removed together with that route's executable code after the replacement is
covered. Do not keep them as permanent compatibility work or optimize toward a
particular total test count. The complete discovered-suite count is intentionally
not a stable documentation contract.

## Gate A: canonical source audit

`assets/release-inventory.json` is the only path inventory. Counts are derived and
are never release authority. The audit entry point is
`scripts/audit-feishu-codex-release.ps1` with source role
`canonical-development` and the explicit sibling Harness root.

Gate A walks only inventory-owned package paths and rejects unknown paths, reparse
points, runtime artifacts, non-UTF-8 or binary content, unfinished scaffolds,
broken local Markdown links, unbalanced fences, credential-shaped material,
real-shaped identities, and non-fixture local absolute paths. Findings remain
answer-free and secret-free.

Source authority also requires exactly one normalized repository Marketplace
route to the audited plugin root. A root manual, copied tree, installed cache, or
external retained snapshot can carry useful knowledge but cannot pass as canonical
development input.

The audit records normalized relative paths, byte sizes, and raw-file SHA-256
values in ordinal order. It takes two matching snapshots and verifies the parsed
inventory bytes inside both, so mutation during audit fails closed. Optional
published audit output is outside source and uses create-new semantics.

`bridge validate` may reuse the Desktop-only audit for diagnostics, but a release
acceptance audit supplies both Desktop and Harness roots. A passing Gate A proves
source shape and authority only; it does not prove runtime behavior.

## Gate B: maintained full external runner

Gate B is the stopped, hash-bound, full external suite historically named P0-B.
For release acceptance, use the maintained one-shot wrapper
`scripts/invoke-external-p0b-once.ps1`; the lower-level supervisor, structured
test driver, evidence schema, and semantic validator remain machine-owned details.

The stopped release window may follow an intentional Hook-only refresh, which
removes the old runtime manifest so startup stays fail-closed until the later
runtime upgrade. The refresh may also delete retired health fields, but only
when the retained snapshot has the exact known old shape and independently
reports a stopped, fully idle Bridge and Beeper queue; every other shape
fails closed without rewriting that snapshot. In this transition state, status
`warning` is admissible only when the manifest is absent with the single
`integrity_check_failed` code, the installed Hooks exactly match the audited
source, health is valid and stopped, and both queues are idle. Every other
warning remains a hard failure.

The wrapper and supervisor must:

1. run the complete Gate A audit and bind its exact manifest and inventory;
2. verify before, during, and after the window that the exact Bridge is stopped,
   its active/dial/delivery queues are idle, and the Beeper queue has
   no pending or claimed page;
3. copy the complete audited source to a retained external source snapshot;
4. keep every audited source and snapshot file pinned read-only through execution
   and post-validation;
5. run the full discovered test suite plus the authoritative machine-required
   contract under a bounded external clean-PowerShell supervisor;
6. isolate temporary state and contain child processes with a hard timeout;
7. prove zero live Desktop, Beeper, scheduler, Bridge, App Server, and Feishu
   contact from the test runner;
8. compare source, runtime-control, lifecycle-Hook, callback, and test artifacts across the window;
9. publish one nonce-bound evidence receipt using create-new semantics; and
10. pass the independent semantic validator against the exact receipt hash.

The retained `source-snapshot` is evidence input only. It must fail execution-
source validation and must never become a development or release source merely
because its bytes match canonical source.

The structured result must report the discovered and machine-required tests with
no failure, error, skip, timeout, or unexpected child-process attempt. Gate B
evidence schema v2 hash-binds all three Bridge, health, and Beeper queue observations.
Exact test
names, historical fault labels, and version-specific regressions belong only to
the runner registry and source tests.

The required contract contains 19 machine-owned mappings. Each must resolve to a
test in the same full discovery and must represent the current executable route.
When an architecture route changes, update the implementation, required mapping,
validator, and tests together; a retired route cannot satisfy current Gate B
coverage merely because its old tests still pass.

The evidence JSON Schema checks shape and constants. It cannot prove execution or
cross-field equality. Acceptance therefore requires the independent semantic
validator to pin and rehash the receipt, retained snapshot, structured result,
captures, current source manifest, and bounded runtime artifacts; it also
recomputes the test mapping, identity relations, time bounds, and stopped-state
observations.

The receipt is immutable by convention and create-new publication, not by a claim
that the filesystem cannot change. Validate its exact whole-file SHA-256 while the
retained work directory and current environment still exist and match. A rerun
creates a new receipt and never overwrites an earlier one.

On any nonzero wrapper or validator result, stop. Preserve the bounded diagnostic
roots, classify the failure as source, test-contract, environment, or product
capability, and change the hypothesis before another run. Never parse an absent
envelope or validate an empty path.

## P3: bounded stopped soak

P3 is a separate release/nightly lane for repeated concurrency, persistence,
retry, recovery, and transport invariants. It begins only after a fresh passing
Gate B receipt for the exact same source and its independent semantic validation.

The maintained machine registry still defines exactly ten ordered, independently
valuable invariant slots. Human planning may group them into fewer families, but
that does not reduce or replace the ten-slot machine contract. Release/nightly
execution enforces at least 25 iterations, so accepted evidence contains at least
250 scenario executions. Ordinary development does not run P3.

P3 reuses Gate B's retained source snapshot, keeps its audited files pinned,
publishes a create-new receipt, and requires its own independent semantic
validator. P3 evidence schema v2 additionally binds a lifecycle mutex for the
whole run and answer-free Bridge-stopped observations from the pre, post, and
final phases. See [p3-bounded-soak.md](p3-bounded-soak.md) for the concise contract.

## Evidence interpretation

A Gate B or P3 pass is current-environment local evidence. Neither receipt nor
validator output is a signature or cryptographic attestation. The result trusts
the selected local toolchain and the bounded observations the supervisor records.

Neither gate certifies a live Desktop task-tool surface, lifecycle-Hook trust,
Beeper activation, product-attested Final Callback caller/turn identity, Feishu API
persistence, client rendering, or production readiness. It cannot clear a
retired producer state or authorize a Bridge start, Beeper registration,
producer activation, schedule, canary, or responder mutation.

Production conclusions come only from the separate answer-free readiness surface
and any independently authorized runtime evidence required by that contract.
Static shape and isolated dynamic tests must remain visibly distinct from live
capability and live end-to-end evidence.

## Release acceptance sequence

1. Classify the change with the impact selector.
2. Complete affected development lanes and pre-freeze contract checks.
3. Finalize code, contracts, tests, inventory and source version, then update the
   unique cachebuster once.
4. Freeze canonical source and run Gate A over those exact bytes.
5. For a release candidate, obtain a fresh full Gate B pass.
6. Obtain P3 only when the selector requires it.
7. Update runtime/plugin packaging only from the frozen canonical source.
8. Reinstall from the repository Marketplace route and verify the loaded snapshot
   in a new Desktop task.
9. Complete lifecycle-Hook trust review and read answer-free readiness independently.
10. Publish, commit, push, or tag only when the user explicitly requested it.

Do not weaken a gate to accommodate an old incident or a new Desktop build. Adapt
the current implementation and machine contract, then keep this human reference
focused on invariant families and evidence boundaries.
