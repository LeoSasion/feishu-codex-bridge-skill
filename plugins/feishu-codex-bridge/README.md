# Feishu Codex Bridge plugin

Current source contract: `4.2.0-alpha.66`.

This self-contained Codex plugin packages:

- the Feishu Bridge Skill and development guidance;
- the Bridge runtime, lifecycle commands and diagnostics;
- durable inbox/binding/outbox state and an initiator-bound `/init` catalog;
- responder-owned one-time Final Callback;
- source-only contracts and external release validation.

## Architecture

[Canonical terminology](references/terminology.md) defines the normative
**Bridge**, **Dial**, **Page**, **Beeper**, **Responder**, and **Final Callback**
vocabulary used by code, wire fields, commands, schemas, tests, and docs.

Each installed Bridge namespace has exactly one independent Beeper. It only
contacts distinct Desktop Responders through the supported Desktop task surface.
It is not Feishu-scope-bound, cannot be a business Responder,
does not execute user business and never supplies the authoritative final.

Each selected Desktop responder task retains sole ownership of its project,
context, model, tools, execution and final. Bridge, App Server, shell, UI,
database and rollout are not alternate responder controllers or final transports.
An explicitly authorized standalone App Server experiment may only observe one
exact task without resume or mutation; the current content-bearing `thread/read`
shape is not approved as an unattended live sensor. See
[App Server read-only boundary](references/app-server-probe.md). The one
Beeper-only deep-link assist loads no business responder and is never a final
transport.

This build admits one isolated Dial namespace,
`beeper`. The Bridge consumes a durable local grant and may
invoke the exact Desktop-bundled CLI once with only an opaque page, addressed
to one newly created Beeper task. Each new Feishu event triggers this admission
directly; the flow installs and requires no recurring Codex scheduled
automation. An ordinary-message Page makes one Desktop task-to-task call; only
the Responder may call `submit_final_callback`, and the result is returned with
`final_callback_source=final_callback`. A `/init` Page may instead use
only bounded `list_projects`/`list_threads` catalog coordination or one exact
read-only inspection; the Bridge alone commits the confirmed local binding.

If the accepted queue item remains durably unclaimed after a fixed grace, the
Bridge may open exactly `codex://threads/<registered Beeper UUID>` once to
load that Beeper. The URI carries no Page, user text, Responder ID, query, or
Final Callback capability and sends no second message. Load failure or a bounded
claim timeout seals the still-reserved Page before a late Beeper can reach a
Responder; the assist may briefly foreground the Beeper in Desktop.

This is not product-level `run_once`: CLI acceptance cannot prove that Codex
created exactly one model turn. A timeout or ambiguous result is terminal and
is never queued again. Retired Dial state remains non-executable and is never
adopted; details live only in the architecture quarantine contract. When a
Feishu scope is successfully bound, the user receives one
plain-language warning that rare duplicate or missed execution remains possible
and irreversible actions should be avoided.

The Final Callback component is transport, not a Responder client. A send claim
returns the Beeper one wrapped prompt containing the authorized `user_request`
and a one-time Final Callback capability; the Beeper necessarily sees it but
must not submit, disclose, relay, or synthesize the Responder-authored final.
The selected Responder executes the
request and calls `submit_final_callback` itself. Possession of that capability is
not product-level caller/turn attestation. Native fields, transcript/readback,
UI, database, OCR and clipboard are never answer fallbacks.

`/init` exposes only non-archived tasks, a ten-minute memory-only snapshot,
page-local numeric selection, confirmation, exact read-only reinspection, and an
atomic local bind. Its read-only completion remains answer-free: sanitized task
titles/project labels reach the Bridge only through one request/fence/snapshot-
bound sealed ephemeral stage that is verified and consumed once. Project roots
and paths are never admitted. The durable binding stores only stable IDs and a
bounded operation receipt, never display text or paths; that receipt is local
consistency evidence, not product caller/turn attestation or `run_once`.
Task/project creation, restore, archive, compact, disconnect, and reply-mode
changes remain unavailable.

## Readiness levels

`mvp` and production readiness are intentionally different:

- MVP is ready only after the current installed source/runtime
  identity is verified, the loaded Final Callback surface participates, and a fresh bound message completes the real route through
  the isolated Beeper, a Desktop responder-owned `submit_final_callback`,
  `final_callback_source=final_callback`, and a definite successful Feishu send result.
- Production remains blocked until a product-level pre-dispatch `run_once`, an
  immutable product receipt, closed task-tool provenance, and product-attested
  Final Callback caller/turn identity are available.

The MVP result accepts the disclosed possibility of rare duplicate or missed work
and is not production-equivalent or exactly-once. The final evidence is
`final_callback_observed`; the observation is answer-free, process-local and
cleared by Bridge restart. It does not independently prove one Beeper claim,
one responder call, or product no-replay. Hook-based final observation describes a retired
transport and is not part of the current readiness route. Project `SessionStart` and
`SessionEnd` Hooks continue to manage Bridge leases only.

## Package layout

- `skills/feishu-codex-bridge/SKILL.md`: intent guide and stop conditions.
- `feishu-codex-bridge-skill.md`: installation, configuration and diagnostics.
- `upgrade-bridge.md`: latest-first evolution, validation impact and release flow.
- `references/architecture.md`: ownership, durable state and recovery invariants.
- `references/beeper-run-once-candidate.md`: ideal product `run_once` contract and the experiment gap.
- `references/release-audit.md`: Gate A/B and external evidence.
- `references/p3-bounded-soak.md`: release soak contract.
- `scripts/`: runtime, lifecycle, validators and source-only auditors.
- `assets/`: closed schemas, tombstones, policy template and release inventory.
- `.mcp.json`: Beeper control tools and Responder-owned Final Callback.
- project `SessionStart`/`SessionEnd` Hooks: Bridge lease lifecycle only; the
  plugin itself contributes no `UserPromptSubmit` or `Stop` Hook.
- `tests/`: contract and regression tests.

Repository-local `HANDOFF.md`, experiment logs, credentials, queues, runtime
state and installed cache snapshots are intentionally outside the plugin package.

The three local roles have deliberately different roots:

```text
plugins/feishu-codex-bridge/              canonical source
.codex/feishu-codex-bridge-runtime/       installed code, config, logs and durable state
~/.codex/plugins/cache/...                versioned Codex plugin cache
```

An upgrade from the retired `.codex/feishu-bridge` runtime name begins with the
stopped `bridge hooks` transaction. It moves that directory once, preserves
configuration, SQLite state and logs, refreshes the path-bound lifecycle Hooks,
and invalidates the old manifest so startup stays fail-closed until `bridge upgrade`
installs and signs the matching runtime. Both directories existing is a hard error;
they are never merged.

## Development policy

Development is latest-first. Resolve the current independent official Codex CLI
and Desktop identity, regenerate version-bound protocol artifacts, and detect
capability shape. Do not preserve old Desktop branches or fall back to historical
Beeper/App Server writer paths when a capability changes.

Use the smallest validation implied by the change:

- documentation-only: Gate A;
- schema or capability adapter: focused Contract plus release Gate B;
- runtime, lifecycle Hook or transport: focused Smoke/Fault plus release Gate B;
- concurrency, persistence, fencing, retry, outbox or Final Callback transport: Gate B plus Soak;
- MVP claim: current exact-source responder-owned MCP live E2E plus
  a definite successful Feishu send result;
- production claim: separate lifecycle-Hook/task-tool/callback/runtime/live evidence,
  product-level `run_once`, and `bridge readiness -Json`.

Gate B and Soak run only in the audited clean external supervisor while the
Bridge is stopped. They never contact live Desktop, Beeper, Bridge or
Feishu and cannot establish production readiness.

## Marketplace lifecycle

The repository Marketplace is `.agents/plugins/marketplace.json`. Install from
that exact route:

```powershell
codex plugin marketplace add <repo-root>
codex plugin add feishu-codex-bridge@feishu-codex-bridge
```

Source edits never modify installed cache snapshots. Finalize code, contracts,
tests, inventory and source version, update the unique cachebuster once, then
freeze and validate those exact bytes. Only after required Gate A, Gate B and
impact-selected P3 pass may the runtime/plugin be upgraded from the same
Marketplace. Compare canonical and installed manifests and verify the loaded
Skill/MCP/lifecycle Hooks in a new task.

The integrated plugin owns the `feishu_final_callback` MCP key and its current
runtime registration namespace.

Use `bridge doctor` for installation diagnostics and `bridge readiness -Json`
for the separate `mvp` and production conclusions. A successful
fresh local-producer canary may make the MVP ready while production stays
blocked; it never creates an exactly-once claim.
