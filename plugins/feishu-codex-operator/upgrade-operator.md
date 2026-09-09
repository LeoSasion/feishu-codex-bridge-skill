# Upgrade and naming cutover

Version notes describe when behavior was introduced; they do not establish the
installed version. Verify that separately using read-only status and the runtime
manifest. Older stopped-install instructions are not a general requirement to
close Desktop for registry edits. Use the current
[registration and reload contract](references/model-router.md#registration-and-operation);
the alpha.122 correction to the former label-specific Desktop guard is recorded in
[acceptance](references/responses-acceptance.md#alpha108-separate-stopped-label-transaction).

Plugin `1.1.0` packages runtime source `4.2.0-alpha.122`. Label updates now allow
Desktop to remain open while preserving the stopped Operator/router checks,
empty callbacks, exact package version, preview digest, original backup and atomic
write. The current CLI success fixture also exercises JSON-upstream projection
through a real exec/MCP continuation with serial calls, without adding another
CLI scenario. Current installation state is separate from this source release;
an older installation without its ownership journal still needs reviewed migration.

Version `4.2.0-alpha.121` preserves nullable reasoning content through JSON and
SSE/WS and validates reasoning history and terminal output using the same
bounded content checks. Invalid history is rejected before contacting upstream;
an invalid or unfinished terminal reasoning item prevents tool release. Omitted,
null and empty content remain distinct, and observed streamed text cannot be
erased with null. No reasoning-to-answer conversion or retry is added. The wire
shape was checked with a newly generated official CLI 0.153.4 schema.
Source validation does not install this code or update any registry. An owned,
request-free stopped router is required for deployment; registry reload cannot
replace code, and previous adapter-bound evidence stays historical.

Version `4.2.0-alpha.120` checks the complete generated event projection before
emitting JSON-upstream SSE or WebSocket output: at most 16 MiB per serialized
event JSON and 64 MiB in total, including repeated snapshots. Exact boundaries
use actual sequence numbers. Text parts no longer inherit the smaller 2 MiB
tool argument limit. Over-limit output releases no event, is not truncated and
is never retried. Source, inventory, tests and rule mirrors are synchronized;
the adapter digest changes, so prior acceptance evidence stays historical.
This source has not been installed into the running router. Deploy executable
changes only with the exact owned router request-free and stopped; registry
reload cannot replace code. No registry or capability policy is changed here.

Version `4.2.0-alpha.119` adds optional
`responses.completed_output_policy: "require_message_or_tool"` for an explicitly
selected adapted endpoint. A completed response containing only reasoning or
empty messages fails with the fixed diagnostic
`completed_response_without_message_or_tool`; it never promotes reasoning to
an answer or tool call or retries the request. The default is `"allow_empty"`,
preserving earlier contracts. Enabling the policy changes the evidence digest;
it does not establish model coding quality or Desktop acceptance. Install code
only with the owned router request-free and stopped; this source change has not
updated existing local registrations or services.

Version `4.2.0-alpha.118` adds opt-in `responses.upstream_response_mode: "json"`.
The first upstream request is non-streaming; only a validated complete JSON
response is serialized into downstream SSE/WS. The default remains
`"match_client"`, including strict byte comparisons for observed upstream streams.
This is a transport choice, not an automatic fallback or repaired stream.
The endpoint contract/evidence changes and first output waits for completion.
See [Gemma compatibility](references/model-router.md#gemma-and-complete-json-upstream-alpha118).
Deploy executable changes only with the exact router request-free and stopped;
registry reload cannot install code. Template repair and provider protocol tests
do not establish Desktop execution or approval acceptance. LM Studio per-model
template overrides are separate, locally journaled repairs, never auto-installed
or automatically removed by the plugin.

Version `4.2.0-alpha.117` distinguishes unsupported hosted search declarations
from malformed function names before contacting an external model. The fixed
error codes expose no tool names or request content. Hosted search remains
unsupported on adapted routes; this change does not replay failed tasks or
establish live Desktop/search acceptance.

The same alpha.117 source introduced opt-in
`responses.input_tool_definitions: "additional_tools_v1"`. This lifts the
exact dated Codex developer `additional_tools` envelopes into
the existing tool map, retaining source envelopes in request memory. Default
registrations still reject this input form; custom identities such as
`functions.exec` still require explicit registration. Paired result names and
namespaces are checked and mapped consistently; unfinished historical calls
and unsuccessful tool-discovery outputs are rejected before any upstream send.
See [the protocol contract](references/model-router.md#input-tool-declarations-alpha117).
This source change requires a new adapter/evidence binding and a request-free
router restart when deployed; registry reload cannot replace executable code.
It does not enable hosted search, alter approvals, or rerun failed tasks.

Version `4.2.0-alpha.116` blocks recovery when an existing installation loses its
ownership journal, supports installation after completed safe removal while
retaining the previous recovery records, and preserves an explicit
`operator init -StartupBundle` selection. Reinstallation validates restored files
and the retained native launcher; it does not replay tasks or infer an old local
model startup policy. Partial removal must finish before a new installation.

Version `4.2.0-alpha.115` integrates the disclosed current-user launch entry
into initialization and adds journal-based `operator uninstall` preview and
`operator uninstall -Apply`. Original integration files survive upgrades; changed
files block restoration. Owned routing is detached without replay, runtime data
is archived, and retained taskbar launchers can open native Codex independently
of plugin source. New-device defaults do not activate the optional router.
Old installations without original ownership evidence require a reviewed
migration. Remove the Desktop plugin only after project recovery succeeds.
See [installation and removal](references/installation-and-removal.md).

Version `4.2.0-alpha.114` separates the native Responses HTTP request bound
from external and WebSocket bounds. Native HTTP wire and decompressed input
are bounded to 64 MiB; other existing 16 MiB bounds remain. Local over-limit
errors now identify `router_request_too_large`, scope and limit bytes instead
of `invalid_http_request`. Native request bytes/encoding and upstream 413 errors
are preserved. Synthetic 17 MiB requests reproduce the old failure and pass
after the change; this is not acceptance or a replay of any failed real task.
The adapter revision changes, and old verification receipts remain historical.

An optional owner-selected unified Desktop entry is also available. It hides
helper consoles, opens an existing Desktop without running synchronization, and
uses the reviewed stopped startup workflow for a cold launch. Concurrent
launches coalesce; a failed workflow records a fresh log and is not retried.
Shortcut changes require explicit owner selection and retained originals. Native
MSIX Start entries and taskbar pins are separate Windows entries; creating a
shortcut does not redirect them. This entry does not hot-replace router code.

Version `4.2.0-alpha.113` adds explicit `reload-registry` with a reviewed SHA-256
digest. It performs no polling, allows at most one changed-version read attempt
per 30 seconds, validates off the event loop and atomically publishes only the
in-memory registry. Existing requests and WebSocket connections retain their
original snapshots; new requests receive the new registry. Repeated requests
for an already loaded version perform no disk read. No model, task, approval,
entry or file is modified by reload. Desktop cache refresh is separately unverified.
Older processes require one request-free router restart to load this code.
The router/registry revision changes acceptance bindings; preserve old evidence
as historical and do not transfer verified status or automatically rerun cases.

Version `4.2.0-alpha.112` excludes the dedicated `gemma4-assistant` draft
architecture from new LM Studio chat registrations. Existing auxiliary entries
are reported in `excluded_existing_slugs` for separate stopped cleanup; discovery
does not delete existing registrations or model files. Ordinary small chat models
and main models with speculative decoding enabled remain eligible. No automatic
draft pairing or inference is performed.

Version `4.2.0-alpha.111` fixes rejection of reasoning text carried by
`response.content_part.added/done`. Content parts are validated against their
owning output item, with raw text and successful-terminal tool release preserved.
Text validation failures now retain fixed, content-free diagnostic categories.
Earlier failed requests remain stopped; new acceptance binds the changed adapter.

Version `4.2.0-alpha.110` adds opt-in cross-model synthetic marker-line compatibility.
It preserves raw responses, strict file/tool checks, historical failures and per-case report policy.
New evaluation runs are required; this does not remove visible blank lines or verify Desktop.

Version `4.2.0-alpha.109` rejects inconsistent streamed text snapshots before
buffered tools are released, while preserving all original whitespace. The
read-only verification report distinguishes failed CLI cases from absent or
stale evidence and retains separate and combined CLI/Desktop failure counts.
`verification-status --format text` renders the same gates for local review.
Adapter, evaluator and verifier revisions invalidate prior current-binding
claims; retain historical receipts and do not relabel or rerun them automatically.
This does not repair a model's leading whitespace or change its prompt template.

Version `4.2.0-alpha.108` adds a separate stopped transaction for verification labels.
Alpha.107 added a read-only, version-bound Desktop verification report.
Alpha.106 introduced opt-in LM Studio batch discovery. The local
native model inventory supplies identities, display names and context limits;
one explicitly configured policy supplies the Responses contract for new rows.
Preview is read-only. Applying requires a stopped router and deactivated entry,
and validates the whole batch before one atomic write. Existing registrations,
including models no longer listed, stay unchanged. Embeddings are excluded.
New models remain unverified and inherit no acceptance receipts. Discovery
does not load, download or call a model. A separately prepared startup launcher
may run this sequence before opening Desktop; it does not change normal Codex
shortcuts, permissions or task defaults. Install with Desktop closed, the exact
router and Operator stopped, and no pending callbacks. Live dropdown refresh
and each new model's Desktop tool behavior still require separate acceptance.

Version `4.2.0-alpha.105` strengthens isolated evaluation with explicit stop-on-error
and nonzero-exit cases. It compares the original final-message text without
trimming; after-trim matching is diagnostic only. Missing stop cases keep older
profiles incomplete. Profile version 2 additionally binds the evaluator source;
version 1 remains readable but cannot claim current verification. This changes evaluation criteria, not the protocol codec,
model behavior, permissions or retries. The active runtime need not be restarted
to run the canonical source evaluator. A later runtime upgrade still requires
the normal stopped cutover; do not rerun older one-shot update bundles. Preserve
the alpha.104 Desktop failures as historical evidence, not passing receipts.

Version `4.2.0-alpha.104` adds an optional `named_function_outputs` codec for the
current Codex schema's named function results with absent or null call IDs.
The exact `codex_app.send_message_to_thread: user_message_json_v1` opt-in encodes
the whole result as labelled user-message JSON. It preserves source metadata,
text parts and identities without creating a call, parsing delegation XML or
granting tools. The user role is an explicit representation change, not native
named-result support or source authentication. Ordinary paired results remain
strict; unknown sources, nontext and opaque fields remain rejected. Defaults,
native/v1 traffic and execution approvals do not change. Raw LM Studio named
input was rejected in one bounded probe; this codec is separately verified,
never an automatic fallback after failure. Install only with Desktop closed,
the owned entry deactivated, exact router/Operator stopped and callbacks empty.
The new contract requires a fresh private receipt and live Desktop acceptance;
earlier failures and alpha.103 file/test evidence remain historical.

Version `4.2.0-alpha.103` adds an optional explicit `history_custom_tools` codec
for registered Codex exec identities when a request declares no current tools.
It preserves the original call IDs, namespaces, source and complete paired
results without adding executable tool definitions. Unknown or incomplete
history, nonempty mismatching catalogs, unsupported formats and new unadvertised
calls remain rejected. The default remains strict, and native/v1 routes do not
change. The private endpoint contract must opt in after bounded verification.
Install with Desktop closed and the owned entry deactivated, router/Operator
stopped, and no pending callbacks. The contract and adapter digests change;
previous profiles and failed Desktop cases remain historical evidence.

Version `4.2.0-alpha.102` adds content-free diagnostic categories for rejected
historical tool identities: absent definitions, namespace/type mismatch, missing
registered custom tools, unknown names and invalid names. These fixed labels
never include tool names, source, results, paths or identifiers. Calls remain
rejected; the adapter neither repairs history nor executes or retries anything.
This is diagnostic preparation, not a claim that Desktop tool execution is fixed.
Install only after Desktop is closed, the owned entry is deactivated, and the
exact router and Operator are stopped without pending callbacks. Registration
and permission settings remain unchanged; earlier adapter profiles are historical.

Version `4.2.0-alpha.101` accepts null optional tool descriptions in the Responses
adapter while preserving original metadata and rejecting non-text values.
Executable inputs, identifiers and message content retain strict validation.
Request string errors can return one fixed schema-field label, without payload
values or tool names. Namespace/call identities and native routing are unchanged.
This addresses a reproduced compatibility gap; the live Desktop rejection still
requires a controlled follow-up to establish its exact cause and recovery.
Stop the exact router and Operator with no pending callback before installing.
Changed adapter hashes make earlier capability profiles historical evidence.

Version `4.2.0-alpha.100` fixes unsolicited compression negotiation in the
native HTTP proxy. An absent downstream `Accept-Encoding` stays absent;
explicit preferences and opaque response bytes remain unchanged. Regression
coverage includes search and Responses with absent, identity and gzip headers.
The live Desktop search decode error remains an acceptance failure until a
controlled restart verifies recovery. Deactivate and stop the exact router
before installing. Adapter hashes change, so old capability profiles remain
historical evidence until re-evaluated against the installed revision.

Version `4.2.0-alpha.99` adds offline registration preflight, private capability
profiles tied to exact configurations and adapter source, bounded real-CLI
evaluation, content-free phase timings, read-only readiness and explicit restart.
The evaluator includes nested/multiround/error/cancel and read-only patch-plan
cases, keeps approvals, fixes Windows UTF-8 stdio and bounds child output.
Install inventory includes the new modules and evaluator; production MCP tools
and Hooks are unchanged. See [acceptance instructions](references/responses-acceptance.md).
Stop the exact Operator/router and ensure no pending callback before upgrading.
Installation does not register profiles, copy private evidence, enable Windows
startup or activate global routing. Actual Desktop/file-write acceptance remains.

Version `4.2.0-alpha.98` fixes fragmented SSE scan performance and adds two
optional Responses capability fields. `text_tool_outputs: "json_string"`
serializes complete text-only result-part arrays without losing boundaries or
metadata. `tool_choice_by_reasoning` explicitly restricts choices for each
reasoning effort before network I/O. Existing v2 entries retain their defaults.
An endpoint without parallel calls narrows the client's parallel permission to
one call per response; it never relaxes an explicit false setting or retries.
Current CLI tests now cover successful nested MCP execution and serial-only
capabilities. Model generation limits and live Desktop acceptance remain separate.
Stop the router and Operator before installing changed files; no global entry
or installed model registration is changed automatically.

Version `4.2.0-alpha.97` implements opt-in Responses tool adaptation. Installation
copies the capability validator, pure tool adapter, SSE state machine and explicit
synthetic probe CLI. Existing registry v1 routes retain their behavior. Adding a
v2 route upgrades the registry atomically and marks old rows with `responses: null`;
it never silently enables their adapter or replaces an existing alias.
The native and deterministic Beeper paths remain independent. There are no new
MCP tools or Hooks. Explicit probes use disposable loopback routing and synthetic
input, never execute returned code, and refuse an existing attempt receipt.
Stop the router and Operator before replacing code. Global activation still
requires the existing live Desktop and native-tool acceptance gates.

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
