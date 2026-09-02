<!-- FEISHU_CODEX_BRIDGE_RULES_START -->
## Feishu bridge runtime safety

### Source and capability authority

- `plugins/feishu-codex-bridge` is the only project-local Bridge source root.
  Never fall back to retired root-level copies, an installed cache, or an
  external test snapshot for development, release, or execution authority.
- Canonical source, installed runtime, plugin cache, retained evidence, and
  product-origin runtime evidence are distinct roles. Content freshness or a
  larger version number does not promote one role into another.
- Detect capabilities latest-first against the exact currently installed Codex
  Desktop and independent official CLI. Generate version-bound Schema from that
  CLI when protocol evidence is required.
- Do not add or keep an executable compatibility branch solely for an older
  Desktop release. Retained legacy parsers may classify old receipts only and
  must fail before mutable state. The owner has explicitly designated this
  project as non-production, so the isolated CLI-queue producer below is the one
  admitted exception; it must never be described as product-level `run_once`.
- This managed block and its plugin asset mirror must remain byte-identical.

### Current producer and historical namespace

- The build may use exactly one isolated producer namespace:
  `beeper`. It may act only on events created after that
  namespace is enabled. Existing `producer_unavailable_no_retry` rows are never
  adopted, published, dispatched, or replayed.
- Each newly authorized Feishu event directly triggers at most one local queue
  attempt under that namespace. No recurring Codex scheduled automation,
  polling producer, or periodic Beeper dial is installed or required.
- Every retired producer lifecycle and control surface is permanently
  non-executable. Its namespace, rules, prompts, helpers, tasks, receipts, and
  assets are answer-free forensic material, never authority.
- Do not create, repair, activate, invoke, or mutate a historical producer or
  translate current work into its queue. Retired records stay terminal and
  historical allow rules stay tombstoned with zero executable prefix.
- The local producer consumes one durable Bridge-owned Dial budget before spawning
  the exact Desktop-bundled CLI once. The only permitted invocation is
  `codex queue --thread <exact Beeper UUID> --message <opaque page>`; session
  names, responder task IDs, user text, retries, `exec`, `resume`, App Server, and
  PATH-selected CLIs are forbidden.
- Only after that durable grant is consumed, the exact queue process exits 0,
  a fixed grace elapses, and the same page is still durably `reserved`, the
  Bridge may open `codex://threads/<exact Beeper UUID>` exactly once as a
  Beeper cold-load assist. The URI contains only that registered Beeper UUID:
  no page, user text, responder ID, query, Final Callback capability, or other payload.
  The assist sends no second message, never queues or retries again, and may
  foreground the Beeper in Codex Desktop. Assist failure or a bounded claim
  timeout atomically terminalizes the still-reserved page before any late
  claim can reach a responder; neither outcome is replayed.
- CLI exit 0 proves only queue acceptance. Nonzero exit, timeout, crash, or
  ambiguous completion is terminal and never re-queued. This is a pragmatic
  Bridge-owned at-most-once attempt, not product-enforced exactly-once.

### Single Beeper and Desktop responder ownership

- Each installed Bridge namespace has exactly one independent, newly created
  Beeper with an exact immutable identity. The Bridge may register and queue
  only that role; it must not reuse a
  historical Beeper.
- The Beeper must not reuse a historical Beeper, bind a Feishu scope,
  appear as a business Responder, alert itself, execute user business, or author
  the authoritative final.
- Its only permitted product role is the closed, operation-scoped Desktop
  task-coordination surface used to communicate with distinct Desktop threads.
  Unapproved methods and broader tool subsets fail closed.
- Every selected Desktop responder remains sole owner of its conversation, project,
  model/reasoning settings, approvals, tools, Skills, plugins, browser, Computer
  Use, files, knowledge access, execution, context, and final answer.
- Bridge, Beeper, shell, App Server, SDK, database, rollout, named pipe, UI,
  OCR, clipboard, and transcript extraction must never become
  an alternate responder client or reply fallback. The exact CLI queue exception and its one
  exact-UUID deep-link cold-load assist may address only the Beeper, never a
  business responder, and the deep link is never a final transport.
- App Server Schema generation is read-only version evidence, not permission to
  launch, resume, attach to, or mutate a responder. Any separately owner-requested
  read-only capability probe must follow `references/app-server-probe.md` and
  still returns no activation authority.

### Identity, fencing, replay, and retention

- Bind Feishu direct chat, group, and topic scopes by stable IDs and Desktop
  responders by exact task ID. Never match by title or display name; a responder may be
  actively bound to only one Feishu scope.
- Derive deterministic operation/event idempotency keys. One physical generation
  accepts its first terminal result only; duplicate keys resolve to the same
  immutable execution rather than another turn.
- Claim, stage, active-work lease renewal, completion, failure, and release must
  bind the same request, owner, dial generation, and fencing token. Reject every
  stale or mismatched identity before state mutation.
- The current producer consumes one durable non-resettable local grant
  before the CLI spawn, serializes all Beeper work, suppresses duplicate local
  spawn attempts, and leaves no automatic retry or rearm path. It cannot prove
  that the Codex product created only one model turn.
- An outcome with `may_have_started=true` is terminal and never automatically
  replayed. A new generation is possible only when the exact terminal result is
  `retryable=true` and `may_have_started=false`; responder lifecycle and other
  uncertain mutations do not advance.
- Retention may delete terminal state only. Never remove an unresolved claim or
  fenced Final Callback staging without its exact readable authoritative terminal receipt.
  Redaction may only move a receipt toward conservative non-retryable unknown.
- When a Feishu scope is bound to a Desktop task, reply once in plain language
  that Bridge work may rarely duplicate or be missed and should not
  be used for irreversible actions. The notice is informational, not a repeated
  confirmation gate. Archived/not-found recovery and automatic replay remain
  unavailable after an uncertain result.

### Responder-owned MCP authoritative final

- P0 is the selected responder turn's authoritative final returned to Feishu.
  Any admitted send must arm and bind one exact request/fence/Beeper/responder/
  prompt and one one-time Final Callback capability before its single responder call.
- The selected Desktop responder must call `submit_final_callback` once with the
  exact final string and the issued capability. The Beeper must never call that
  tool, relay a native answer, synthesize a final, or disclose the capability.
- Completion accepts only `final_callback_source=final_callback`. A Final Callback proves
  possession of the one-time bearer capability, not product-level caller or
  turn attestation. Wrong, stale, expired, conflicting, tampered, empty, or
  already-consumed submissions fail closed under the no-replay rule.
- Native assistant fields,
  `read_thread`, shell, UI, database, transcript, rollout, OCR, clipboard, and
  temporary files are never authoritative final transports.
- Preserve the final as an exact Unicode string. A trimmed view may reject empty
  output, but must not normalize, rewrite, truncate, or partially deliver it.
  Freeze one immutable outbound plan before first send; uncertain attachment or
  chunk delivery is not reformatted and retried.
- Pending outbox data must be sealed to the exact event/message/scope, final
  digest/length, and canonical plan; verify before every safe attempt and scrub
  answer, integrity material, and plan at every terminal state.
- Native helper stdout crossing Python, PowerShell, MCP, or Desktop tools is one
  ASCII-only JSON object. The only content-bearing claim responses are
  `claim_and_arm`, which may return the exact authorized responder identity and
  wrapped prompt (`user_request` plus one-time Final Callback capability), and
  `claim_readonly`, which may return only one strictly bounded catalog or exact
  inspection request. The Beeper must not use or disclose the Final Callback
  capability. Submit/complete/finish/fail, diagnostics, and errors remain
  answer-free and never expose final text, capabilities, paths, digests, or
  route metadata.

### Bridge, access, and lifecycle integrity

- Bridge performs Feishu authentication, durable inbox/outbox state, stable
  bindings, bounded attachment metadata, and local control replies. Its sole
  Codex launch permission is one verified Desktop-bundled `codex queue` process
  addressing the registered Beeper with an opaque Page after the durable
  local grant is consumed, plus the conditional one-shot exact-UUID Beeper
  cold-load assist defined above. It never calls a business responder RPC, starts
  App Server, reads Codex databases/rollouts, retrieves knowledge, or answers.
- Fresh or missing access configuration resolves to `locked`; malformed known
  values refuse startup. Production assessment requires at least one validated
  owner/admin/user/chat identity. `compat` is explicit legacy migration only.
- Treat `bridge.pid` as an untrusted reference. Lifecycle commands must verify a
  Python process whose command line contains the exact installed `bridge.py`;
  never stop a reused or unverifiable foreign PID or start a second Bridge.
- Manual start/restart and every SessionStart require current source/runtime,
  installed manifest, and lifecycle-Hook integrity. A mismatch fails closed and does not
  authorize install, upgrade, Hook refresh, or restart.
- SessionStart/SessionEnd Hooks manage Bridge leases only; after the Final Callback
  plugin is installed they are the only Bridge rows that require visible Hook
  trust. The plugin contributes no `UserPromptSubmit` or `Stop` Hook. No Hook
  routes a business request, submits a final, reads transcripts, or contacts Feishu.
- Review exact lifecycle-Hook hashes in the supported visible Hook surface,
  trust individual Bridge events only, and never use `Trust all`. Unrelated rows
  may remain untrusted.

### Commands, `/init`, and automatic project work

- `/init` is the only reserved Feishu slash command. It admits only
  a non-archived bounded task catalog and, after an exact snapshot selection and
  explicit confirmation, one read-only task inspection followed by an atomic
  local binding. New task/project creation, restore, archive, compact, and every
  other Desktop mutation remain unavailable. Every successful binding includes
  the one-time plain-language risk notice. Reject every other slash command
  generically.
- The `/init` catalog excludes all deny-only historical Beeper records and the
  one Beeper. It uses immutable ten-minute memory-only snapshots,
  stable IDs, initiator-bound group ownership, scope-limited visibility,
  confirmation before local binding, and no title matching. Read-only catalog or
  inspection uncertainty is terminal with `may_have_started=false`; only a new
  Feishu event may start a new attempt.
- Within an owner-requested Bridge project task, perform normal in-scope install,
  upgrade, configuration, dependency, lifecycle, Hook, plugin, Schema, and
  read-only diagnostic work automatically. Do not request per-command approval
  or invoke an external authorization relay.
- Automatic execution never widens scope and never authorizes a historical
  producer or live canary. Resolve the exact executable, path, version, process
  identity, interruption risk, and recovery path before mutation; use bounded
  waits and read-only postconditions.
- OAuth, UAC, visible Hook review, and external identity confirmation may require
  the minimum irreducible human interaction. Publishing, credential changes,
  cross-project mutation, and destructive work still require that exact outcome
  to be in user scope.

### Diagnostics, evidence, and tests

- `bridge status`, `bridge doctor`, `bridge readiness`, and `bridge validate`
  are read-only diagnostics; they never start, stop, upgrade, repair, rehydrate,
  activate, or retry a producer. Optional JSON output is one compact answer-free
  schema-v1 object with no content, credentials, identities, or local paths.
- `doctor` reports installation health only. Production eligibility is decided
  solely by answer-free `bridge readiness -Json`, which keeps installation,
  visible lifecycle-Hook review, run-once runtime topology, task-tool policy, responder
  ownership, historical preservation, and exact-source live E2E as separate gates.
- Focused unit tests may run locally only while the exact Bridge
  is verified stopped and no live Beeper request exists. Gate B and soak remain
  external-supervisor-only release evidence; never run a fake App Server.
- Gate B requires its independent semantic validator. Soak requires a fresh
  exact-source Gate B receipt and its own validator, fixed scenarios, bounded
  iterations, hard timeout, no child/live contact, and a retained pinned snapshot.
- Gate B and soak prove isolated local behavior only. They do not prove live
  Feishu delivery, Desktop task tools, scheduler hard cap, product provenance,
  lifecycle-Hook review, Final Callback caller/turn provenance, cryptographic
  attestation, or activation.
- Keep external artifacts under one explicit bounded non-reparse artifact root,
  separate from source, project, harness, installed runtime, and drive root.
- Generate App Server Schema into a fresh external directory with the exact
  independent official CLI. Never reuse old, checked-in, other-machine, SDK, or
  WindowsApps-derived Schema and never change WindowsApps ACLs.

### Publication and detailed contracts

- Publish only files admitted by the release inventory. Never publish `.codex`,
  caches, credentials, tokens, logs, queue/session state, attachments, runtime
  data, knowledge content, local development logs, local paths, or answer material.
- `upgrade-bridge.md` owns stable R-* principles and evidence/release flow;
  detailed contracts live in `references/architecture.md`, scheduler/run-once,
  permissions/Hooks, command UX, App Server, and release-audit references.
- Historical marker meanings and incident evidence stay in their single index or
  tombstone reference; do not reproduce old command, method, asset, or canary
  enumerations in operational rules.
<!-- FEISHU_CODEX_BRIDGE_RULES_END -->
