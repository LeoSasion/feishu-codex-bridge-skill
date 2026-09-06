# Upgrade and naming cutover

Version `4.2.0-alpha.96` adds LM Studio model-list discovery and explicit local
registration to the existing router CLI. Existing registry entries and global
configuration are preserved. Discovery is metadata only, not inference proof.
Stop the independent router and Operator before upgrading, as below.

Version `4.2.0-alpha.95` relays allowlisted native WebSocket handshake metadata
as connection-local `response.metadata` and `codex.response.metadata` events.
Handshake-only reasoning flags have no verified event equivalent. Original frames are
unchanged. Cookies and unrelated response headers are not relayed as events.
Desktop interpretation and picker/default selection still require live proof.

Version `4.2.0-alpha.94` raises the upstream header-field/status-line limit to
64 KiB, retaining a finite limit and no retry. A synthetic 16 KiB upstream
header reproduced a router-generated 502 with the previous default. This does
not establish the root cause of the historical Desktop failure or validate
WebSocket handshake metadata propagation. Global activation remains opt-in.

Version `4.2.0-alpha.93` adds content-free, in-memory router failure diagnostics
to status and standard safe error objects for local 502 failures. Native
upstream bodies and status codes remain unchanged. This is diagnostic support,
not proof of a fix for the failed live trial. No request is retried.

Version `4.2.0-alpha.92` adds recoverable model-cache invalidation on explicit
router entry switches and validators for the merged catalog. Close Desktop
before an entry switch. Cache backups remain private beside the original cache.
This does not fix or reproduce the live trial's 502, prove Desktop visibility,
or authorize reactivation. Installation alone never touches the global cache.

Version `4.2.0-alpha.91` adds native search/image passthrough, bounded gzip,
deflate and Zstandard routing inspection, and WebSocket redirect refusal.
Stop the independent router before upgrading its files, in addition to stopping
Operator; deactivate an active global entry first. Refresh its dedicated
environment from `model-router-requirements.txt` using binary wheels only.
Python below 3.14 needs the open-source `backports.zstd` decoder. No global
entry or model-default change is implied by this upgrade.

Version `4.2.0-alpha.90` adds independent background router start/status/stop.
Stop uses the authenticated loopback control endpoint, not an untrusted PID.
An activated Codex entry or in-flight requests block stop. The CLI catalog test
uses an isolated home without credentials or task methods. It proves catalog
acceptance by the current CLI, not visibility in the running Desktop picker.

Version `4.2.0-alpha.89` includes the optional Python Responses router source,
registry and reversible configuration helper. Upgrade copies these files but
does not activate a global router or change task defaults. See
[router setup and acceptance limits](references/model-router.md). The router
requires its own environment with the pinned transport dependency; it has no
LiteLLM SDK, Web backend or Chat Completions adapter.

## Existing Operator installation

1. Verify the exact installed process, Git/source identity, and zero pending
   callbacks or active requests; stop it before replacing files.
2. Run the isolated tests and release audit. Use `operator upgrade`, which
   preserves `operator.env`, mappings, and databases. The upgrade installs the
   local `beeper` provider and private model catalog automatically.
3. Check Final Callback registration, start, then require status/doctor/readiness.
   Offline checks do not establish real Feishu E2E delivery.

An existing blank `CODEX_OPERATOR_BEEPER_MODEL=` is intentionally preserved and
now resolves to Luna/low. Set it explicitly to `beeper`, Spark, or Luna only after
reviewing the corresponding queue policy; no global Codex model entry is added.
Alpha.88 installs the approved protocol-external identity response and a
`visibility: list` catalog entry. This is not proof of Desktop registration or
provider selection; those remain pending supported integration. Keep the current
working model until live local-provider routing has been verified.

## Previous Bridge installation

This is a coordinated cutover, not a compatibility mode. Do not start a second
service against the same chat or silently point an old MCP client at new code.

1. Stop the exact old installed service. Require no pending/captured callback,
   active inbox/outbox request, or actionable retry; preserve historical tombstones.
   Back up the old runtime, Hook config, callback registration, and project rules.
2. Install the new `feishu-codex-operator` plugin from the canonical marketplace
   and disable/remove the old plugin through Codex. Reload Desktop tasks so their
   Final Callback MCP uses the new plugin. Do not hand-edit plugin cache copies.
3. Install the new runtime at `.codex/feishu-codex-operator-runtime`, still stopped.
   Copy the idle `sessions.json`, `state.sqlite3`, and `callbacks.sqlite3` intact;
   include SQLite sidecars consistently and preserve attachments in the backup.
   Convert `bridge.env` into `operator.env`: map `CODEX_BRIDGE_*` keys to
   `CODEX_OPERATOR_*`, checking conflicts and path-valued settings individually.
   Do not copy old code, PID, health, lock files, or live leases.
4. Remove only the old project's Hook entries and archive its two Hook scripts;
   register the new scripts, review them in Desktop settings, and synchronize
   the new AGENTS managed block. Keep unrelated Hooks untouched.
5. Explicitly move Final Callback registration from the verified old runtime to
   the new one. Archive the old runtime only after validating the new inventory
   and preserved state. Start one new Operator, then check doctor/readiness and
   one authorized reversible E2E request.

If Desktop cannot reload yet, keep the old installation intact and do not
activate the new runtime. Old names belong only in this transition checklist
and retained historical state, never in the new execution path.

No upgrade implies permission to push, publish, change credentials, or replay
an accepted/uncertain request.
