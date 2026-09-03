# Architecture and recovery boundaries

> **NON-PRODUCTION GUARD.** Historical Desktop Beeper producers remain
> permanently tombstoned and paused. The only executable exception is the
> separately registered `beeper` local queue attempt described
> here. It is not product-level `run_once` and must never be represented as one.

## Current state

The normative vocabulary is defined in
[Canonical terminology](terminology.md): **Bridge**, **Dial**, **Page**,
**Beeper**, **Responder**, and **Final Callback**. Current protocol surfaces do
not introduce role aliases.

The current Bridge validates authorized Feishu ingress, stores it durably,
and may consume one Bridge-owned local grant before making one bounded invocation
of the exact registered Desktop-bundled CLI. The CLI receives only an opaque
page and the exact newly created Beeper task ID.

The ordinary per-Page control prefix remains self-contained after task
compaction but is capped at 512 characters; it carries only the lane, exact-once
tool sequence, no-readback/no-replay boundary, failure side of the send boundary,
and terminal output rule. The lower-frequency read-only setup prefix keeps its
closed mapping contract and is capped at 1700 characters. Unit tests bind both
prefixes to the opaque 32-character Page and reject accidental business text or
Responder identity in the CLI argv. The detailed `beeper-task.md` remains a
creation-time role contract, not a per-Page runtime injection or an attested
substitute for these bounded self-contained controls.

```text
Feishu event -> Bridge durable inbox -> consume local grant
  -> codex queue(exact Beeper, opaque page)
  -> [if still reserved after grace: open exact Beeper deep link once]
  -> Beeper claim+arm
  -> one Desktop task-to-task call -> Responder-owned Final Callback
  -> sealed outbox -> Feishu
```

Each newly authorized Feishu event directly initiates this one bounded queue
attempt. The current route installs and requires no recurring Codex scheduled
automation, polling producer, or periodic Beeper dial; an active-work lease is
only request-scoped fencing/liveness state inside the admitted attempt.

CLI exit 0 proves queue acceptance only. Timeout, nonzero exit, crash, or an
ambiguous post-dispatch outcome is terminal and never re-queued. This local
boundary suppresses Bridge retries but cannot prove Codex created exactly one
model turn. Existing `producer_unavailable_no_retry` rows are prior terminal
history and are never adopted or replayed.

The cold-load assist is admitted only after the grant and accepted queue call,
while the same page is still atomically `reserved`. It opens exactly
`codex://threads/<registered Beeper UUID>` once, contains no payload or responder
identity, and sends no second message. If opening fails or the Beeper still
does not claim within the bounded wait, the Bridge CAS-terminalizes that
reserved page with `may_have_started=false`; any late claim is denied before
prompt disclosure. A claim race that wins first crosses the conservative
may-have-started boundary and is never treated as a safe unclaimed timeout.
The post-load claim window uses the Beeper's single bounded maximum; clients
must not silently shorten that shared safety window or extend it without a
corresponding Beeper contract change.
The two safe-unclaimed terminal codes are internal Bridge outcomes; the
Beeper-visible generic failure tool cannot publish or forge them.

## Responder ownership and the single Beeper

Each selected Codex Desktop responder task is the sole owner of its conversation,
project, model and reasoning settings, execution environment, approvals, tools,
Skills, plugins, browser, Computer Use, files, knowledge access, business
execution, and authoritative final answer.

The Bridge stores only authorization, stable scope-to-task bindings, durable
delivery state, and bounded attachment transport metadata. It does not answer,
reconstruct responder history, inject knowledge, or become another responder client.

Each installed Bridge namespace defines exactly one independent Beeper. It is
distinct from every Responder and every historical
Beeper record. It is never bound to a Feishu scope, shown or selected as a
business responder, used as its own responder, or allowed to perform user business or
supply the final answer. Its only product role is to use the admitted Desktop
task-coordination surface to communicate with a distinct Desktop responder.

The experiment creates and registers a new Beeper in the isolated
`beeper` namespace. It queues only that exact immutable ID and
does not make any historical record reusable.

## Retired namespace quarantine

Retired producer state confers no authority and may not be published into,
drained, repaired, resumed, redirected, or used as a grant. Executable artifacts
and incident-specific marker dependencies are removed; the maintained boundary
is generic namespace isolation, non-adoption, parser absence, and a zero-allow
project-rule tombstone. A future producer must use a distinct client contract,
queue/evidence namespace, fingerprint, and runtime attestation.

## Durable identity and transition invariants

Feishu scope identity is stable and never inferred from display text:

- direct chat: `p2p:<chat_id>`;
- group chat: `group:<chat_id>`;
- group topic: `group:<chat_id>:topic:<root-or-thread-id>`.

Task titles, project labels, and conversation names are untrusted display values.
Routing and authorization use exact scope, project, task, operation, event, and
generation identities.

The durable protocol obeys all of the following:

1. Operation and event idempotency keys are deterministic. One canonical event
   identity never changes meaning, and one physical generation accepts only its
   first terminal outcome.
2. Claim, stage, completion, failure, active-work lease, and release share the
   same request, fence, owner, responder, and generation identity. Every stale or
   mismatched token fails closed.
3. An ambiguous mutating result is terminal with `may_have_started=true` and is
   never automatically replayed. A new generation is allowed only for an
   explicit `retryable=true`, `may_have_started=false` outcome.
4. Read-only abandonment may use its separately bounded TTL only when the
   operation contract proves no mutation could have started. It never weakens a
   mutating claim.
5. Retention removes only authoritative terminal state. It never deletes an
   unresolved claim, an actively fenced read-only or final-callback stage, a
   pending outbound plan, or integrity material required to resolve the request.
6. Terminal cleanup may redact answer-bearing state only toward a conservative,
   answer-free, non-retryable outcome. Responder output and queue content never
   expand control-plane authority.

## Current admission and ideal `run_once`

The admitted experiment is deliberately narrower than the ideal contract. It
requires a fresh Beeper identity, an isolated state root, an exact CLI path,
version and digest, a durable non-resettable local dial budget, global local
serialization, an opaque page-only argv, and first-terminal sealing. It never
queues a business responder directly and has no automatic retry or rearm path. Its
single conditional deep-link assist loads only the registered Beeper and does
not create a second dispatch path.

At successful Feishu-to-responder binding, the Bridge appends one plain-language
notice that rare duplicate or missed execution remains possible and that
irreversible actions should be avoided. This is disclosure, not a repeated
confirmation gate.

The ideal production producer remains a materially different product-level
pre-dispatch `run_once`. Admission for one exact installed surface requires:

- one independently established Beeper identity and proof that no
  second identity can be admitted in the namespace;
- a distinct exact Desktop responder and the minimum operation-scoped Desktop
  task-coordination capability set;
- an atomic, durable, non-resettable grant consumed before dispatch with
  `max_model_turns=1` and `max_executions=1`;
- same-key coalescing plus rejection of second-key, queued, overlapping,
  duplicate, retry, and rearm dispatch paths;
- an immutable one-run/one-execution/one-Beeper-turn receipt and a bounded
  quiet window proving no second dispatch;
- closed answer-free evidence that binds product provenance, installed source,
  inventory, task-coordination policy, schemas, and a recomputed surface
  fingerprint without carrying task IDs, paths, prompts, messages, or answers;
- independent proof of the exact task-tool surface, lifecycle-Hook review,
  product-attested Final Callback origin, and live
  exact-source end-to-end behavior.

A static contract audit proves only declarative integrity. It always leaves
runtime attestation unobserved and `activation_allowed=false`. Missing product
origin keeps the production exactly-once claim unavailable. The experiment never
revives historical surfaces or makes existing held events retryable.

## MVP and production readiness

Readiness has two deliberately separate conclusions:

- **`mvp`** is ready when the current installed source/runtime
  identity and fresh Bridge snapshot are verified, and one fresh, already-bound
  ordinary Feishu message returns an accepted `final_callback` terminal through the
  current route and receives a definite successful Feishu send result. The fixed
  process marker does not independently attest one Beeper claim, one responder call,
  or product-level no-replay; those remain accepted current risks.
- **production `run_once`** remains blocked until the Codex product supplies the
  pre-dispatch hard cap, immutable runtime receipt, exact Beeper/task-tool
  provenance, and product-attested Final Callback caller/turn identity defined above.

MVP readiness is an operational closure under accepted risks, not
an exactly-once or production-equivalent claim. The local grant may still miss or
duplicate product work in rare failures, and the bearer capability proves token
possession rather than which responder turn called it. Those limitations remain in
the one-time binding warning and make irreversible actions inappropriate.

The machine projection is a fixed, answer-free, current-Bridge-process
observation. It is created only after the accepted `final_callback` terminal is sent
successfully, the first inbox claim is observed, and the Bridge inbox/outbox
answer material has been scrubbed. It contains no event, message, scope, responder,
answer, path, digest, capability, or timestamp. Bridge restart clears it, so a
newly loaded build needs a fresh canary. It does not inspect Beeper response
content, prove client rendering, bind a product receipt, or attest how many
Beeper claims, responder calls, or product model turns ran.

Current live-E2E evidence names the observed final source as
`final_callback_observed`. Hook-based final observation belongs to a retired
final transport and must not be used for the current route. Project `SessionStart` and
`SessionEnd` Hooks remain Bridge lifecycle controls only; their visible review
is separate from Final Callback provenance.

## Responder-owned Final Callback

The bundled Final Callback component is transport only; it does not dispatch a
Page or initiate business work. The
Beeper may claim and arm one page through its model-visible
Final Callback tools. Claiming creates a one-time bearer capability, persists only
its digest, and returns one ASCII JSON helper result containing the exact
authorized responder identity and wrapped page (`user_request` plus capability)
to the Beeper. The only content-bearing claim results are this
`claim_and_arm` response and a `claim_readonly` response containing one strictly
bounded catalog or exact-inspection request. Read-only completion, submit,
finish, fail, diagnostic, and error results stay answer-free; bounded catalog
display data crosses to the Bridge only through the sealed staging contract
below.

Any current send must bind one immutable operation to the exact request,
fence, Beeper, Responder, prompt, Dial generation, and Final Callback capability
before dispatch. The selected responder executes with its own context and tools,
then calls `submit_final_callback` once with that capability and its exact final.
The Beeper must not call that tool, extract or disclose the capability, relay a
native answer, or author a replacement. Completion atomically preserves the
first valid non-empty submission and records `final_callback_source=final_callback`.

This Final Callback proves possession of an unexpired one-time bearer capability. It
does not prove which product task or turn invoked the MCP tool, and no synthetic
turn ID may be recorded as product attestation. This limitation is part of the
current risk boundary and does not upgrade the local grant into product
`run_once`. Wrong, stale, expired, consumed, conflicting, or tampered submissions
fail closed and never cause another responder call.

The final string is preserved exactly through Final Callback staging, terminal receipt,
outbox, and Feishu transport. Empty or oversized results fail closed. Explicit
formatting, chunking, or attachment transforms use one immutable outbound plan;
safe retries reuse that plan and revalidate its sealed event/message/scope,
answer, and plan integrity envelope.

Native fields, `mode=steer`, shell, App Server, databases, transcripts, rollout
files, UI, OCR, clipboard, and a second responder client are never fallback answer
transports. `read_thread` and `wait_threads` lifecycle/native output are not
answer sources. Missing, conflicting, or unverifiable submission is an unknown outcome
and is not replayed. Terminal paths scrub answer-bearing staging and pending
integrity material after an authoritative receipt exists.

## Binding, recovery, and `/init`

Current v1 dispatches ordinary messages for an already-bound responder and
admits two read-only Beeper operations for `/init`: a bounded non-archived
catalog and one confirmed exact-task inspection. The Bridge performs the
resulting local binding atomically; the Beeper never binds a scope or starts a
business turn. `/init` is the only recognized Feishu slash command; other slash
inputs are rejected before responder routing.

The `/init` flow uses a ten-minute memory-only catalog snapshot, stable IDs,
initiator-bound group ownership, scope-limited visibility, untrusted display
labels, and confirmation before the one local binding mutation. It excludes all
deny-only historical Beeper IDs and the one Beeper. Only a
sanitized task title and project label may leave the catalog operation as
display content. Project roots/paths, summaries, prompts, messages, and other
task content are never admitted.

Catalog or inspection display data is written only to one sealed ephemeral
stage bound to the exact request, operation, dial generation, fence, snapshot,
and selection proof. After an answer-free terminal receipt, the Bridge verifies
those identities and consumes the stage once into the current in-memory wizard.
Late, duplicate, stale, partial, or tampered stages fail closed; interrupted
consumption cannot replay or commit the result and the stage is scrubbed or
terminally aged out. This stage is neither helper stdout nor durable
session/binding state.

The durable binding contains only stable task/host/project IDs and a bounded
operation receipt; it contains no display text or path. That receipt proves only
the local selection, inspection, and binding compare-and-swap shared the required
identities. It is not product caller/turn attestation, responder provenance, or
product `run_once`. Switching a binding never archives or deletes the displaced
responder.

Catalog and inspection uncertainty is terminal with
`may_have_started=false,retryable=false`; the event is never replayed, and a new
attempt requires a new Feishu message. The confirmed path binds one exact scope
and task only after the inspection result matches the frozen task, host, project,
snapshot, request, dial generation, and fence. Task/project creation, restore,
archive, compact, disconnect, and reply-mode changes remain unavailable. Any
future mutation requires its own closed operation/result contract and must not
reuse or widen this read-only catalog lane.

The public `bridge init` command only merges project policy. It is not the
Bridge `/init` handler and does not create a producer or add Beeper allow
rules.

## No alternate responder controller

The Python Bridge never starts App Server, calls a business responder RPC, reads
Codex databases or rollout files, or owns a responder turn. Its sole launch
exception is one bounded exact Desktop-bundled `codex queue` addressed to the
registered Beeper with an opaque page, followed only when still unclaimed by
the one exact Beeper-only deep-link assist defined above. CLI resume, named
pipes, responder deep links, UI automation, and similar responder-client paths remain
forbidden fallbacks.

A separately owner-requested standalone App Server may evaluate passive reading
of one exact Desktop task without `thread/resume`, `turn/start`, subscription, or
mutation. It never joins the resident Bridge, owns the turn, drives the UI, or
becomes a final transport. The current `thread/read(includeTurns=true)` response
schema is content-bearing and may return persisted answer, reasoning, tool
arguments, and tool results when available; it exposes no schema-guaranteed
metadata-only projection. An isolated implementation may discard those fields
and retain an answer-free receipt, but that receipt can at most contribute to a
future `observed_runtime_correlation` finding. It does not establish correlation
by itself, prove that the observer never received content, or provide product
caller/turn attestation. Activation remains closed until the gates in
`app-server-probe.md` are proven.

Missing registration or provenance, conflicting identity, unknown responder
outcome, or unavailable required capability fails closed without adding an
allow rule, changing a binding, or replaying work.
