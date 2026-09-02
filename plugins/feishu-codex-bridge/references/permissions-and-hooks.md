# Permissions, authentication, Hooks, and Final Callbacks

This reference owns the stable permission, access, integrity, lifecycle, and
Hook/callback boundaries. It addresses the currently installed Codex Desktop and official
CLIs; it does not preserve executable branches for older product versions.

## 1. Feishu identities and permissions

Follow the current
[official Feishu CLI installation guide](https://open.feishu.cn/document/no_class/mcp-archive/feishu-cli-installation-guide.md).
The detailed QR-first permission profile lives only in
[openclaw-common-chat-permissions.md](openclaw-common-chat-permissions.md).

```powershell
npm install -g @larksuite/cli
npx -y skills add https://open.feishu.cn --skill -y
lark-cli config init --new
lark-cli auth login --recommend --no-wait --json
lark-cli auth status --json --verify
```

Keep these identities separate:

- User OAuth authorizes the current user only for the scopes shown on the
  Feishu-owned authorization page. It does not grant Bot tenant scopes.
- Bot credential validity proves authentication, not chat permissions.
- Bot tenant scopes and `grant_status` determine Bot capability. Audit them
  read-only with the explicit Bot identity described in the permission profile.
- Private-chat success does not prove group intake. Group installation,
  mention policy, event subscription, Bot identity, and tenant permission are
  independent gates.

Do not copy tokens, hard-code an evolving scope list, or use another OAuth flow
to repair a Bot-scope failure. Forward the CLI-provided console URL when
administrator action is required. The consumer is ready only after the current
CLI emits `[event] ready event_key=im.message.receive_v1`; timeout or a
structured startup error fails closed.

An explicit user request for a high-impact Feishu operation is the authority
for that operation. A broad OAuth grant never becomes standing permission for
recall, urgency, moderation, membership, publication, or credential changes.

## 2. Scoped setup and current-product discovery

An owner request to install, configure, upgrade, or diagnose this Bridge
authorizes the normal in-scope implementation transactions. Before each
mutation resolve the exact responder, executable, source, version, install scope,
process identity, interruption risk, and recovery path; verify its postcondition
before the next transaction. Ask the user only for an irreducible OAuth, UAC,
identity, or visible Hook-review interaction.

Use dedicated per-run system-temp directories for installers and QR images.
Pass opaque URLs as argv values, never through an interpolating shell. Remove
temporary material after completion, failure, refusal, or expiry. Publishing,
credential changes, cross-project mutation, and destructive cleanup remain
outside scope unless the user requested that exact outcome.

The prerequisite inventory includes an independently runnable official Codex
CLI for schema work and the exact currently installed Desktop-bundled CLI for
the isolated queue experiment. Discover both without launching a responder:

- Accept a verified `codex.cmd` shim backed by the official
  `@openai/codex` package and read its package metadata.
- A binary found only under the Desktop package's WindowsApps resources is not
  an independent schema-generation CLI. The experiment may use the byte-matched
  Desktop-materialized CLI only after recording its exact path, digest and
  version in the isolated registration.
- If absent, install the current-user official package with
  `npm install -g @openai/codex`, then verify the shim and package metadata.
- Never modify WindowsApps ACLs or copy the packaged binary.
- Detect current capability and generated Schema shape rather than inferring
  them from a remembered version. Capability absence fails closed.

Every actual Codex invocation is a separate bounded transaction. Schema
generation is read-only provenance; it never authorizes App Server launch,
responder attachment, or a second responder client. The queue exception accepts only
`queue --thread <exact Beeper UUID> --message <opaque page>` with no shell.
After queue exit 0 and a fixed grace, the same still-reserved page may cause
one `codex://threads/<exact Beeper UUID>` open. The URI has no query or payload,
never names a responder, and is not a second queue or final transport.

## 3. Installation, access, and integrity

Mount the current product as separately verified transactions:

1. Install the Bridge runtime, `SessionStart` and `SessionEnd` scripts,
   initial locked configuration, integrity manifest, and Bridge-owned Hook
   entries.
2. Configure at least one exact validated identity with
   `bridge access -AccessMode locked`.
3. Install the repo-local overall `feishu-codex-bridge` plugin, verify its
   installed snapshot and bundled final-callback component, then run
   `bridge final-callback-register` against the exact manifest-valid runtime.
4. Validate BOM-less project `.codex/hooks.json`, review the exact lifecycle-Hook
   hashes, and trust only Bridge `SessionStart` and `SessionEnd` rows individually.
   The Final Callback component contributes no `UserPromptSubmit` or `Stop` Hook. Never use
   `Trust all`.
5. Restart Codex only when needed to load changed plugin or Hook bytes, then
   verify answer-free status and preserve the intended Bridge state.

The first `bridge install` is the one disclosed indivisible bootstrap:
runtime, both lifecycle Hooks, initial `bridge.env`, integrity manifest, and
Bridge-only Hook registration. Later `bridge upgrade` is runtime-only.
`bridge access` changes policy keys only.

For a pre-manifest Hook migration, stop the verified Bridge, run
`bridge hooks`, and then upgrade. The Hook refresh
invalidates the old manifest without signing a new one, so startup remains fail closed until the
matching runtime upgrade signs the new bytes. Missing parity, an absent
manifest, or any hash mismatch is a diagnostic—not permission to reinstall,
restart, bypass review, or sign unrelated files.

Fresh or missing access configuration resolves to `locked`. Empty or malformed
recognized values refuse startup. Locked mode denies every sender or chat not
present in the owner/admin/user/chat allowlists:

```text
CODEX_BRIDGE_ACCESS_MODE=locked
CODEX_BRIDGE_OWNER_OPEN_ID=ou_...
CODEX_BRIDGE_ADMIN_OPEN_IDS=ou_...,ou_...
CODEX_BRIDGE_ALLOWED_USER_OPEN_IDS=ou_...
CODEX_BRIDGE_ALLOWED_CHAT_IDS=oc_...
```

`compat` is an explicit short legacy migration state and is never a production
default. Keep denial generic. Authorization identity and routing identity are
separate; bindings use stable chat/topic and Desktop task IDs, never names.

## 4. Current producer and responder ownership

The only executable producer is the isolated `beeper` local
attempt. It consumes a durable non-resettable grant before one bounded spawn of
the exact registered Desktop-bundled CLI. Only a new Beeper UUID and opaque
page may cross argv. Exit 0 proves queue acceptance only; nonzero exit,
timeout, crash, or ambiguity is terminal and never automatically re-queued.
Pre-enable terminal rows remain untouched.

If the accepted page remains `reserved` after a fixed grace, the Bridge may
open that exact Beeper deep link once. Failure or bounded claim timeout must
win an atomic `reserved`-to-terminal CAS before reporting
`may_have_started=false`; a late claim then fails before prompt disclosure. If
the claim wins first, the safe-unclaimed result is unavailable and the normal
conservative no-replay boundary applies. The assist may foreground Desktop.
`beeper_load_assist_failed` and `beeper_claim_timeout` are Bridge-internal
CAS outcomes and are rejected by the Beeper-visible generic failure tool.

The historical producer namespace is a non-executable forensic tombstone.
Historical producer details are not an operational contract; the rule audit
must prove that no retired producer prefix is allowed or matched. No
configuration, restart, Hook trust, Gate B, soak, retained receipt, or source
review can reactivate that namespace.
No further native-field attempt is permitted.

Each installed Bridge namespace owns exactly one newly created current
Beeper. It coordinates only through an approved,
closed Desktop task surface and never binds a Feishu scope, becomes a business
Responder, addresses itself, executes user business, or authors the final.

Every selected Desktop responder remains the sole owner of its conversation,
project, context, model, tools, approvals, execution, and final. Bridge,
Beeper, shell, CLI, App Server, SDK, database, rollout, named pipe, deep link,
UI, OCR, clipboard, and transcript extraction must never become an alternate
responder client or reply fallback. The admitted exact Beeper-only deep link loads
no business responder and is never a reply transport.

## 5. Ideal product `run_once`

A future product-level pre-dispatch `run_once` must be materially different
from every historical producer. It needs:

- an isolated client, queue/rules/marker namespace, and immutable Beeper
  identity;
- exact current-product provenance and a closed task-tool allowlist;
- one durable, non-resettable grant consumed before dispatch;
- one execution, one responder model turn, and suppression of overlap, duplicate,
  queued, retry, or second dispatch;
- an immutable request/fence/responder/turn receipt and bounded quiet window;
- a product-attested exact-Responder Final Callback transport whose caller and turn cannot be
  forged by another model-visible actor;
- closed answer-free runtime attestation.

Source contracts, tests, owner intent, lifecycle-Hook review, and the Final
Callback bearer capability do not turn the local
experiment into product exactly-once. An outcome with `may_have_started=true` is
terminal and never replayed. Only an explicit `retryable=true` plus
`may_have_started=false` result may advance to another deterministic
generation.

## 6. Bridge lifecycle Hooks

Merge only Bridge-owned entries:

- `SessionStart` with matcher `startup|resume` invokes
  `start-feishu-codex-bridge.ps1 -HookInvocation`.
- `SessionEnd` invokes
  `stop-feishu-codex-bridge.ps1 -HookInvocation` with a bounded timeout.

These Hooks manage Bridge leases only. They never create, resume, inspect,
claim for, or stop a Beeper or responder. Parse stdin JSON and require the
expected event plus session identity. Malformed input fails closed.

The start Hook validates the installed manifest before writing a hashed lease.
Hook leases bind the live Codex Desktop backend ancestor, never the transient
PowerShell Hook wrapper. It starts only when no verified Bridge exists. A
bounded internal helper is created through `Win32_Process.Create` with
breakaway/no-window flags, receives only the manifest-bound Hook path and lease
hash, and revalidates the exact active lease, runtime manifest, environment and
Bridge identity before its sole Python spawn. WMI failure, expiry, a released
lease, or a missing Desktop host fails closed without falling back to direct
Hook-child launch. The parent Hook releases the lifecycle mutex before its
bounded exact-PID observation. The stop Hook releases only its matching lease;
a manual stop releases all local leases.

`bridge.pid` is an untrusted reference. Start, stop, upgrade, and status
decisions require a Python process whose command line contains the exact
installed `bridge.py` path. A reused PID or unreadable identity is never
force-stopped and cannot justify starting a second Bridge.
Python liveness checks on Windows use read-only
`OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE)` plus a zero-time
`WaitForSingleObject`;
POSIX-style `kill(pid, 0)` is never used on Windows because CPython maps it to
termination rather than a permission-free existence probe. Liveness remains a
secondary guard and never substitutes for the command-line identity check.

## 7. Responder-owned Final Callback

The Final Callback component is transport, not a Responder client. Its MCP
surface has six closed operations:

- Beeper-only `claim_and_arm` claims one Page, creates one Final Callback
  capability, persists only its digest, and returns the exact Responder plus wrapped
  prompt.
- Beeper-only `claim_readonly` claims one catalog or inspection Page without
  creating a Final Callback capability and returns only its bounded operation fields.
- Beeper-only `complete_readonly` accepts one strictly shaped structured result
  over UTF-8 JSON stdin and returns only answer-free terminal state.
- Responder-only `submit_final_callback` accepts the capability and exact final over
  strict UTF-8 JSON stdin.
- Beeper-only `finish_final_callback` waits for and seals the submitted result.
- Beeper-only `fail_page` records the bounded terminal failure.

The selected Desktop responder receives the capability only inside the opaque
wrapped business prompt. It performs the business request with its own project,
context, tools, and approvals, then calls `submit_final_callback` exactly once with
the exact final string. The Beeper must never call that tool, parse or disclose
the capability, relay native answer text, or author the final. The plugin does
not call a responder, read a transcript, route, or contact Feishu.

A claimed current page binds one exact request/fence/Beeper/responder/
prompt/dial generation before its single responder call. The token is an
current one-time bearer capability: possession authorizes submission, but
ordinary MCP provides no product-attested caller or responder-turn identity. No
synthetic turn ID may be presented as attestation. Unarmed, wrong, stale,
expired, consumed, conflicting, tampered, or empty submissions fail closed
without replay.

Completion accepts only `final_callback_source=final_callback`. Native assistant
fields, `read_thread`, `wait_threads`, App Server, shell, UI, database, rollout,
OCR, clipboard, and temporary files are never authoritative. Preserve the final
as the exact Unicode string; trimming may only reject empty output. Freeze the
outbound plan before first delivery and never reformat or resend an uncertain
result. Helper stdout remains one ASCII-only JSON object. Only `claim_and_arm`
may return the exact authorized responder identity and wrapped page containing
`user_request` plus the Final Callback capability, and only `claim_readonly` may
return bounded catalog/inspection parameters. Every submit, complete, finish,
fail, diagnostic, and error result is answer-free and never exposes the final,
capability, paths, digests, or route data.

## 8. Hook file and trust

Write `.codex/hooks.json` as BOM-less UTF-8 with an atomic replace. Every event
value is a matcher-group JSON array, including a single entry. Lease JSON is a
separate BOM-tolerant runtime format.

Hook trust binds exact bytes, matcher, command, path, and timeout. Review only
Bridge `SessionStart` and `SessionEnd`. After installing the Final Callback build,
the plugin contains no `hooks/hooks.json` and adds no `UserPromptSubmit` or
`Stop` row. Any such plugin row belongs to an older installed snapshot and must
not be trusted as current source.

Use the currently supported visible Hook surface. Run trust-only review with
`CODEX_BRIDGE_CHILD=1`; lifecycle scripts must exit before lease or process
mutation. Trust each exact Bridge row, allow unrelated rows to remain pending,
and never infer Hook trust from project or rule trust. Any byte, path, matcher,
command, or timeout change requires a new exact review.

Prefer the supported automated trust path. If only a visible platform review is
available, request that single interaction and continue afterward. If no
trustworthy review surface exists, report a blocker; do not bypass it with a
manual Bridge start.

## 9. Diagnostics and evidence

`bridge status`, `bridge doctor`, `bridge readiness`, and
`bridge validate` are read-only. Always pass the exact `-ProjectRoot` in an
external shell. Restricted Desktop process visibility reports
`Runtime: unknown`; PID existence, process name, or health freshness alone
never becomes process identity.

Dynamic tests must not run inside Codex Desktop. They use the audited external
supervisor with the verified Bridge stopped. Gate B, soak, Schema generation,
final-callback validation, lifecycle-Hook review, and package parity remain
distinct from production readiness and
live Feishu-to-Desktop delivery.

Diagnostics and shared output stay answer-free. Never print credentials,
allowlists, messages, queue/session state, task IDs, local paths, attachment
manifests, prompt/final-answer text, or retained payloads.
