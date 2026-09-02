# Product run-once contract

This reference defines the ideal production producer shape for the
single-Beeper Desktop architecture. It is not the admission contract for the
separately disclosed `beeper` local queue attempt.

## Current capability verdict

No currently exposed Bridge product surface has supplied a closed runtime
attestation for a product-enforced, pre-dispatch single run. Recurring schedules,
manual turns, helper counters, post-dispatch pause, agent cooperation, and
Bridge-side supervision is not a hard cap because a model turn may already have
started.

The experiment may run because the owner explicitly accepts this gap: it
consumes a durable Bridge-owned grant before one exact CLI queue attempt, but it
cannot prove one product model turn. Bind success discloses that duplicate or
missed work remains possible; ambiguous outcomes are terminal and never
automatically retried. A conditional one-shot deep link may load only the exact
Beeper after an accepted queue item remains unclaimed; it carries no payload,
does not queue again, and does not improve the product attestation. Therefore,
for production claims:

- historical Beeper namespaces remain permanently non-executable;
- source-only validation always leaves `activation_allowed=false`;
- missing product capability keeps exactly-once readiness blocked even when the
  single documented experiment is usable;
- future Codex releases are evaluated by capability shape and runtime evidence,
  not by preserving branches for older Desktop or CLI versions.

## Required product contract

The normalized surface kind is
`single_beeper_run_once`. Names in this reference are
project vocabulary; they do not claim that the current Codex product exposes
matching API fields.

### Beeper topology and ownership

For each installed Bridge namespace, the product must establish exactly one
immutable Beeper.

The Beeper:

- is distinct from every historical Beeper record;
- is not bound to a Feishu scope;
- cannot be selected as a Responder or contact itself;
- uses only the allowed Desktop task-coordination methods;
- never performs user business or supplies the authoritative final.

The selected Desktop responder remains sole owner of its context, project, model,
tools, execution and final. Bridge, App Server, shell, UI, database, rollout
and any other transport remain forbidden as alternate responder clients.

### Pre-dispatch single-run budget

Before model dispatch, the product atomically consumes one durable,
non-resettable grant that fixes:

- one candidate execution;
- one Beeper model turn;
- one immutable idempotency key-to-execution mapping;
- no rearm, update, recurrence or next run.

Duplicate keys coalesce to the same execution. Distinct-key, queued,
overlapping, duplicate and retry paths must be rejected before creating a
second execution or turn. Restart and failover cannot restore the consumed
budget.

### Immutable receipt

A closed answer-free receipt must bind the exact candidate, product build,
surface fingerprint and one-run-to-one-turn relation. It proves:

- the grant was consumed before dispatch;
- exactly one execution and Beeper turn occurred;
- duplicate coalescing and second-dispatch suppression held;
- every terminal outcome consumed the budget;
- no next run or rearm remained;
- a bounded quiet window created no additional execution or turn;
- exactly one Beeper identity remained stable;
- scope binding, Responder identity collision, Beeper-as-Responder selection, business execution,
  non-policy calls and alternate responder clients remained zero;
- Desktop responder ownership and all terminal safety markers were preserved.

Thread IDs, prompts, answers, paths, queue data and remote error text must not
enter this receipt.

## Source-side contract

The exact closed shapes are owned by:

- `assets/desktop-beeper-run-once-candidate.schema.json`;
- `assets/desktop-beeper-run-once-runtime-attestation.schema.json`;
- `scripts/beeper_run_once_contract.py`.

The auditor recomputes canonical digests and the surface fingerprint, rejects
duplicate JSON members and extra fields, and verifies namespace isolation,
ownership, budget and receipt requirements. Human documentation does not mirror
the schemas' field list.

A static pass means only that supplied artifacts are eligible for a later
runtime-attestation design. It never proves product origin, material difference,
runtime behavior, task-tool availability, lifecycle-Hook review,
product-attested Final Callback origin, live E2E or activation.

## Runtime admission

A later owner-requested runtime attestation is acceptable only when the current
Codex product itself emits the closed receipt for the same installed source and
product build. A hand-authored JSON object, copied prompt, scheduled/manual turn,
CLI/App Server mutation or historical receipt is not product provenance.

Admission also requires, as separate evidence layers:

1. exact current Desktop task-coordination capability;
2. visible lifecycle-Hook review bound to the same source;
3. exact-source live end-to-end Final Callback with product-attested caller/turn
   origin (the current bearer capability does not satisfy this layer);
4. exact isolation from all retired producer state without adopting
   incident-specific marker dependencies.

Failure, timeout, disconnect, malformed output or unverifiable origin fails
closed without replay. Runtime attestation does not reactivate historical
surfaces and does not make existing durable holds retryable.

## Evolution rule

When Codex Desktop updates:

1. inspect the current capability shape;
2. regenerate version-bound protocol artifacts when applicable;
3. update the single current adapter and closed schemas;
4. delete the replaced adapter after the new contract passes;
5. run focused contract tests, then release Gate B and any required Soak;
6. keep `activation_allowed=false` until current product runtime evidence and
   live E2E both close.

Do not add version-number conditionals to preserve a superseded product shape.
A capability removed by a newer product becomes an explicit blocker until the
single current adapter is updated.
