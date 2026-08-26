# Permissions, authentication, and lifecycle

## Feishu setup

Follow the current
[official Feishu CLI installation guide](https://open.feishu.cn/document/no_class/mcp-archive/feishu-cli-installation-guide.md):

```powershell
npm install -g @larksuite/cli
npx -y skills add https://open.feishu.cn --skill -y
lark-cli config init --new
lark-cli auth status --json --verify
```

The bridge receives and replies as the bot. Do not substitute user OAuth for a
missing bot permission. First use follows the exact QR-first
[`openclaw-common-chat`](openclaw-common-chat-permissions.md) profile:

```powershell
lark-cli config init --new
lark-cli auth login --recommend --no-wait --json
# complete its device code after the user scans
```

The first QR creates/configures the PersonalAgent app. The next QR grants the
CLI's current common user OAuth set. Generate and display the official QR and
let the user approve it in Feishu. Do not copy tokens or hard-code the evolving
scope set. Do not automatically request `--domain im`: it is broad user OAuth,
not full Bot chat permission.
Warn before launch that `--recommend` is version-dependent, cross-domain, and
may contain many write scopes. The Feishu page is the authoritative scope list
the user personally reviews.

OAuth scope grants do not authorize later writes. Message recall, urgent
delivery, membership/manager changes, moderation, and other high-impact
operations retain their normal exact-intent and confirmation gates.

Bot tenant scopes remain separate. Private-message success does not prove group
delivery: verify group installation, explicit mention, event subscription, and
bot identity. Keep locked allowlists and the @-mention gate by default. Do not
claim non-mention group intake merely because user OAuth succeeded;
`im:message.group_msg` is a Bot tenant capability, not a user-login result.
QR authorization cannot completely replace developer-console scope declaration
and administrator approval for a full Bot chat profile.

Treat the consumer as ready only after lark-cli emits
`[event] ready event_key=im.message.receive_v1`. On a structured startup error
or missing marker after the configured timeout, reconnect with bounds rather
than reporting a false healthy state.

## Mount consent and separate approvals

On first use, run read-only `bridge preflight`. When prerequisites are missing,
list the exact packages, official sources, install scope, elevation need,
PATH/restart impact, and the following configure step. One explicit first-use
onboarding approval may authorize installation of only those listed
prerequisites, verification with one retry, one
`lark-cli config init --new` launch, and the exact recommended split OAuth flow in
`openclaw-common-chat`. It continues the user's scoped approval and is not
Skill self-consent; the user still personally scans and approves each page.
Stage standalone installers and package archives in a dedicated per-run system
temp directory, never inside the Skill or target project, and remove them after
verification, refusal, failure, or cancellation.
On Windows, include an independently runnable Codex CLI in this prerequisite
inventory. Discover it without starting a Codex process: accept a verified
`codex.cmd` shim backed by an installed `@openai/codex` package, and treat a
`codex.exe` found only below
`Program Files\WindowsApps\OpenAI.Codex_*\app\resources` as a Desktop package
resource rather than a usable CLI. `bridge preflight` must not run
`codex --version`. If the independent CLI is missing, disclose the official npm
source and global install scope; after onboarding approval, install it with
`npm install -g @openai/codex` and verify its shim plus `package.json` without
launching it. Never change WindowsApps permissions or copy its packaged binary.
Record the exact package version from metadata. Do not infer that a newer host
CLI upgrades Desktop or an SDK's pinned runtime, and do not claim a security fix
is present on another surface without version-specific evidence.
Every later Codex invocation, including `/hooks` review or `execpolicy check`,
remains a separate approval checkpoint.
When PATH changes are part of that disclosed bootstrap, verify the
stored user PATH and a fresh child process; success in the installer process
alone is insufficient. Start the child through the current shell's absolute
executable path rather than a PATH lookup. If Desktop sandbox registry
isolation prevents a cross-task PATH update, stop retrying registry writes,
disclose that global PATH remains unchanged, and use the Bridge wrapper with
exact verified executable paths. It authorizes only the disclosed recommended
user-login request; it does not authorize browser or administrator actions,
out-of-profile scopes, Bot tenant-scope changes, app publication, project
rules, hooks, Listener startup, Codex restart, Gateway creation, or automation.
Launch the blocking config-init command in the background. Forward any
verification or configuration URL unchanged and generate the required PNG QR
code with `lark-cli auth qrcode`; the user completes browser actions. Store the
PNG in a dedicated per-run directory beneath the system temp directory, never
inside the Skill or target project. Retain it only while configuration is
pending and remove it after success, failure, or expiry. On
Windows, a `.cmd` shim can reinterpret `&` inside an opaque URL. In that case,
resolve the same installed official CLI JavaScript entry and invoke it with the
verified `node.exe` using a true argv array. Never interpolate or reconstruct
the URL in a shell command. On failure or expiry, stop instead of silently
creating another configuration flow.
If Desktop sandboxing rejects the exact system-temp QR directory, request file
access only for that per-run directory and retry generation once against the
same opaque URL. Do not relocate the QR into the Skill/project and do not
restart the config or OAuth request.
After config succeeds, run the recommended OAuth split flow. The user must see
the URL and PNG before the turn ends; after the user reports completion, the
Skill runs `auth login --device-code` itself.
After it completes, run `auth status --json --verify`. Treat user OAuth validity,
Bot credential validity, and Bot tenant-scope audit as three separate results.
Do not call `auth scopes --json` a Bot audit after user login because it may
return `tokenType: user`. Audit Bot scopes with the explicit read-only request
`lark-cli api GET /open-apis/application/v6/scopes --as bot` and inspect tenant
scope names plus `grant_status`. For 429, retry at most once and wait no more
than 30 seconds when honoring `Retry-After`; for transient EOF/timeout, wait two
seconds and retry once. The audit gets at most two total attempts. Otherwise
report and stop. Never restart OAuth to bypass an audit failure. If online
`auth status --verify` times out, separate local token validity from live
verification and do not claim `verified`. If a Bot operation reports
`missing_scopes`, forward the CLI's exact `console_url`; do not use
`auth login` as a Bot repair, auto-submit an administrator approval, or recreate
the app merely to change permissions. Prefer the console UI when the user does
not want JSON; offer exact batch-import JSON only if the user chooses it.

Installing Feishu CLI is not consent to a resident listener. Before
`bridge install`, display the Skill welcome text and require an explicit reply
such as `同意挂载`, `确认`, or `是`.

Mount these live components under separate approvals:

1. Install Listener runtime and lifecycle hooks in the selected project. A
   fresh install writes `locked`; a missing access key remains locked. A
   malformed or empty recognized boolean, enum, or integer value refuses startup
   instead of being guessed.
2. Under a separate approval, run
   `bridge access -AccessMode locked -OwnerOpenId <ou_...>` plus only explicitly
   chosen admin/user/chat IDs. Do not start a canary or production with an empty
   allowlist or explicit legacy `compat` mode.
3. Install and enable the repo-local `feishu-codex-final-return` plugin under its
   own approval. Then separately run `bridge final-return-register`; it accepts
   only the exact installed runtime with a valid integrity manifest and never
   replaces a different registered runtime implicitly.
4. Read-only validate `.codex/hooks.json` as BOM-less UTF-8 JSON, then use a
   separately approved visible Codex review to trust only the exact Bridge
   `SessionStart` and `SessionEnd` Hook hashes plus the plugin's exact
   `UserPromptSubmit` and `Stop` Hook hashes. Never use `Trust all`.
5. Render the project-local exact queue-helper allow rule.
6. Restart Codex separately so the trusted project loads that rule and plugin;
   verify Listener PID, health, log state, and read-only
   `bridge final-return-status` without mutating them.
7. Create one dedicated Desktop Gateway candidate with model and reasoning
   overrides omitted, using the read-only first-turn prompt from
   `assets/desktop-gateway-bootstrap.md`. Require one successful bounded direct
   `mcp__codex_app.list_threads` call and one successful direct
   `mcp__codex_app.list_projects` call. Do not register or touch the queue from
   this preflight; it does not prove scheduled-turn availability.
8. Render and send `assets/desktop-gateway-task.md` as the second turn and
   register that exact task/host. The turn must execute the registration command
   once; a bare `DONT_NOTIFY` acknowledgement is not success. Verify the tool
   record and zero-exit JSON (`ok`, `registered`, exact task and host). Do not
   probe or claim during mounting. Run `register` with exact-command escalated
   execution on its first attempt because it writes registration, the initial
   active-work lease heartbeat, and wake metadata. When task history omits tool
   output, verify only the
   bounded registration metadata and `bridge status`; never infer success from
   final text alone.
9. Create or update a paused Gateway scheduler heartbeat targeting that exact existing task with
   `assets/desktop-gateway-heartbeat.md` and a two-minute cadence.
   Some Desktop builds persist heartbeat creation as `ACTIVE` even when the
   create request says `PAUSED`; immediately issue a full paused update in the
   same orchestrated call, then read back the stored config and verify no
   automation-origin Gateway turn occurred.
10. Only when this exact official Desktop surface has no terminal incompatibility
   marker, activate one finite canary under a separate approval while the owner
   is ready to complete `/init`, select and confirm the predeclared exact target,
   and send one ordinary test message. Require exact-scope binding and the target
   final, then leave the scheduler paused/completed.
   Production requires two later approvals: change/read back the paused
   recurrence, then activate that verified recurrence.

The installer writes `.codex/feishu-bridge/runtime-manifest.json` with the
installed runtime version plus SHA-256 bindings for every installed Python file
and both lifecycle hooks. The start hook validates it before writing a lease;
manual `bridge start` also requires source/runtime parity. A missing or
mismatched manifest is a fail-closed diagnostic, not permission to reinstall,
restart, or bypass the hook. `bridge access` changes only access-policy keys in
`bridge.env`; it never refreshes code, hooks, or project rules.

The first `bridge install` is the one disclosed indivisible bootstrap containing
runtime, both lifecycle hooks, initial `bridge.env`, the integrity manifest, and
Bridge-only `hooks.json` registration; it never merges project rules. After that,
the public `bridge upgrade` path is runtime-only. For a pre-manifest installed
hook, first stop the Listener separately and run `bridge hooks` under its own
approval. It backs up/replaces both Bridge hook scripts and their `hooks.json`
entries, invalidates the old manifest without signing a new one, and leaves
start fail-closed. A separately approved `bridge upgrade` then writes the
matching runtime manifest. Hook trust review and restart remain separate.

Do not create a separate Sentinel or Router task. The same automation-origin
Gateway turn performs the metadata probe and, only when pending work exists,
the fenced claim and target routing. Never attach the automation to a new chat,
target task, old control task, or project-wide destination.

Registration is owner-locked. Replacing it with `register --force`, changing
the automation target/prompt/cadence, or changing helper/rule paths is a new
administrative action. The installer creates none of these Desktop components
silently.

Final-return plugin installation/enablement, `bridge final-return-register`,
trusting its exact `UserPromptSubmit` and `Stop` Hooks, and Codex restart are
also separate actions. A missing plugin is detected only when P0 reply return is
requested; after the user approves the exact local source, scope, restart risk,
and rollback, the Skill may install or enable it automatically. It must stop at
the visible Hook review so the user can inspect and trust the exact rows.

## Access modes

Fresh installs write `locked`, and a missing access key still resolves to
`locked`. A present but empty or malformed recognized boolean, enum, or integer
value refuses startup.
Explicit `compat` preserves legacy behavior by accepting every sender when no
identity is configured; use it only as a short, separately approved migration
state, never for a canary or production. `locked` denies every sender/chat
outside the owner/admin/user/chat allowlists:

```text
CODEX_BRIDGE_ACCESS_MODE=locked
CODEX_BRIDGE_OWNER_OPEN_ID=ou_...
CODEX_BRIDGE_ADMIN_OPEN_IDS=ou_...,ou_...
CODEX_BRIDGE_ALLOWED_USER_OPEN_IDS=ou_...
CODEX_BRIDGE_ALLOWED_CHAT_IDS=oc_...
```

Keep denial generic. Authorization identity and routing identity are separate;
same-name people do not share a binding because scopes use stable chat/topic
IDs.

## Codex Desktop policy

The listener writes durable requests; it never launches Codex or becomes a
target writer. The dedicated Gateway consumes those requests only on its
approved scheduled automation turns and uses Desktop task tools to inspect,
create, restore, message, wait for, and archive tasks.

Desktop task tools may be lazy. After a fenced claim, invoke the exact required
top-level `mcp__codex_app` method directly from the Gateway model turn. Never
call it through `functions.exec`, `ALL_TOOLS`, or `tools[...]`; current builds
may retain a retired dynamic alias that is not capability evidence. If the
direct method is unavailable, fail closed. Never substitute App Server, shell,
database, rollout, named-pipe, deep-link, or UI access.

The bridge has no target model, reasoning, sandbox, network, context-window,
Desktop-refresh, App Server, or Obsidian setting. Target project and persisted
settings remain authoritative. Current task send is text-only; attachments are
bounded validated local references, not native typed media. Return only target
final text to Feishu.

## Gateway probe, fencing, and uncertainty

Registration stores one Gateway task ID and optional host. A scheduled probe or
active-work lease heartbeat from another identity is rejected. Once registered,
the listener persists work even while the Gateway sleeps.

Each scheduled cycle runs one `sentinel-probe`, reading only counts, monotonic
generation, registration, and lease state. Empty cycles end `DONT_NOTIFY` and
never load a payload or call task tools. One non-empty cycle reserves one wake
ID/fence; overlap sees `wake_inflight` and ends.

A higher-priority Desktop rule may require internal progress commentary before
tool use. Do not promise that the task history will contain no commentary;
constrain it to generic state-only text with no payloads, paths, identifiers,
tool details, or reasoning, and never forward it to Feishu. The scheduled cycle's
complete final response still must be exactly `DONT_NOTIFY`.

Do not accept an empty scheduled cycle as proof of end-to-end Desktop task-tool
availability. A first approved `/init` catalog-and-selection canary must verify an exact existing task
from the automation-origin Gateway turn. If the deferred tool is absent or its
invocation explicitly fails, return `target_tool_unavailable`, confirm that the
target never started, and report a Gateway capability problem—not a bad task
ID. Do not replay or use App Server, shell, database, or UI fallbacks.

Repeat every required top-level `mcp__codex_app` method name in the short
scheduled prompt itself. Do not assume a background run's tool selector will
recover names only present in an older task turn. Exact naming is a selection
hint, not an availability guarantee, so it never removes the canary requirement. A
retry on the same official Desktop build is not a new canary. After
`target_tool_unavailable`, keep that build paused; changing the model, task,
prompt, context, cadence, registration, or automation target does not create a
new surface. Only a positively different official build/surface may begin a new
compatibility cycle, under its own approvals.

A native `target_final_readback_unavailable` marker likewise blocks only another
native final-field canary on that build. It does not block one separately
approved exact-turn Hook-transport canary after the same source passes P0-B/P3,
the runtime and plugin are deployed, runtime registration matches, and both
plugin Hooks are trusted. Use the one-ticket manual lane, keep the scheduler
paused, and retain the native build verdict.

The same turn starts with a zero-wait claim, then uses one bounded 20-second
grace claim after real work. It processes at most eight requests and never waits
indefinitely. No wake message is sent to another Router.

Require the same fence for claim, staging, completion, failure, active-work
lease heartbeat, and release. Refresh that lease heartbeat between bounded
waits. If a claim exceeds the claim TTL without a result and the active-work
lease heartbeat is stale, mark it
possibly started and withhold automatic replay. Response, terminal-claim, and
staging caches and answer text use the configured retention window; a
nonterminal claim is never removed by ordinary retention. Afterwards, compact
terminal receipts remain as durable idempotency tombstones so a cleaned retry
ancestor cannot recreate an earlier generation.

Activation of the exact existing scheduler heartbeat authorizes the fixed allowlisted
`sentinel-probe`, `claim`, `release`, `heartbeat`, `final-return-arm`,
`final-return-status`, `final-return-native`, `stage-path`, `complete`, and
`fail` commands while status remains active. It does not authorize
`final-return-hook`, plugin registration, another script, path, process, task,
App Server, or Codex lifecycle action.

## Listener hook leases

Merge only bridge-owned hook entries:

- `SessionStart` matcher `startup|resume` runs
  `start-feishu-codex-bridge.ps1 -HookInvocation`.
- `SessionEnd` runs `stop-feishu-codex-bridge.ps1 -HookInvocation` with a
  three-second timeout.

Hooks manage only Listener leases. They do not create, stop, resume, probe,
claim, or replace the Gateway or targets. The start hook refreshes a hashed
lease and starts the listener only when no verified Listener process exists.
The stop hook releases only its matching session lease; a direct manual stop
releases all local leases and stops only a Python process whose command line
contains the exact installed `bridge.py` path after a bounded graceful wait.
The PID file alone is never identity: a reused foreign PID is left untouched,
and an unreadable Python command line fails closed. If that command line becomes
temporarily unreadable only after the exact Listener was verified and graceful
shutdown began, wait through the existing deadline without force-stopping it;
success still requires an absent PID or a verified reused non-Bridge PID.

Parse hook JSON from stdin and require the expected event and session ID.
Malformed input fails closed. Read PowerShell 5.1 UTF-8-BOM lease JSON with
`utf-8-sig`.

## Exact-turn final-return plugin Hooks

The separate `feishu-codex-final-return` plugin is P0 reply transport, not a
Listener lifecycle hook and not a router. Its hidden local MCP tools are absent
from the model-visible tool surface. `UserPromptSubmit` passes structured
`session_id`, `turn_id`, and `prompt`; `Stop` passes that same task/turn identity,
`stop_hook_active`, and `last_assistant_message`. The plugin verifies the
separately registered installed runtime and integrity manifest, then invokes
only `final-return-hook` with strict UTF-8 JSON stdin. Helper stdout is one
ASCII-only answer-free JSON object.

The Gateway must create a matching fenced `final-return-arm` before sending.
The prompt event may be the exact raw input or Desktop's strict delegation
wrapper, but the latter is accepted only when its source equals the Gateway
pinned at arm time and its inner input hashes to the original prompt. Unarmed,
wrong-source, mismatched-prompt, or wrong-turn events are ignored. Answer-free
status diagnostics distinguish an unseen Hook from an observed rejection without
persisting or emitting prompt text. A later Stop for the same turn
may replace a provisional same-turn capture when another Stop hook continued the
turn. The plugin never reads a transcript, calls a target, contacts Feishu, or
routes a request. Hook failures do not widen authority or trigger replay.

Write `.codex/hooks.json` as UTF-8 without a BOM. Windows PowerShell 5.1
`Set-Content -Encoding utf8` adds a BOM that Codex may reject before hook
review, so use `System.Text.UTF8Encoding($false)` with `WriteAllText` and an
atomic replace. Lease JSON is a separate runtime format and remains
BOM-tolerant. For an existing live-file repair, first retain a byte-for-byte
backup, remove only the BOM, and prove parsed-JSON equivalence. A protected
project `.codex` tree may require exact-path filesystem approval; do not use a
broader write grant or relocate the active config.

Every event value is a matcher-group JSON array, even when it contains one
entry. In PowerShell, initialize `$entries = @()` outside conditional pipeline
output before appending; assigning an empty array through an `if` expression
can yield `$null`, after which the first `+=` becomes a scalar object. Codex
reports that invalid legacy shape as `invalid type: map`.

The current Codex CLI provides a `/hooks` browser, and startup may open the same
review surface. Desktop surfaces can vary; use only the visible review control
actually available. Project/rule trust does not imply hook trust: non-managed
hooks require visible review of their exact content hash. Inspect and trust only
the Bridge `SessionStart` and `SessionEnd` entries and, when P0 reply return is
being enabled, the exact plugin `UserPromptSubmit` and `Stop` entries. Run the one-time review
with `CODEX_BRIDGE_CHILD=1`; both lifecycle scripts exit before lease or process
mutation in that environment. On Windows, prefer launching the verified
independent `codex.cmd` from clean `cmd.exe /d` rather than an interactive
Windows PowerShell host; the latter may load PSReadLine and display a publisher
trust prompt before Codex, which is unrelated to Bridge hook trust. If the hook
review opens automatically at startup, review that visible surface directly and
do not type `/hooks` again. The review list is global: evaluate each Bridge or
final-return plugin event row individually, never infer failure from unrelated
pending rows, and never use `Trust all`. Verify the project/plugin source,
absolute path, `-HookInvocation`, matcher, and timeout for each event, and accept
unrelated hooks remaining pending. Require the expected Bridge and plugin rows
to show active with zero review count, then prove Listener PID, health, log, and leases remain
absent before exiting the protected review;
never trust unrelated pending hooks to get past a Bridge parse failure. Any
repair, upgrade, matcher, command, timeout, or path change invalidates the
reviewed hash and requires a new separately approved review. If visible hook
trust controls are unavailable, leave trust unverified and request approval for
a manual listener start. One ended task must not stop a listener still leased
by other live tasks. After a restart, absent Listener PID, health signal, and
log output means activation failed; do not claim readiness or start manually
without new exact approval.

Inside a restricted Desktop shell, read-only `bridge status` may be able to see
the Python PID but not query `Win32_Process.CommandLine`. Report `Runtime:
unknown` in that case and direct the operator to rerun the same read-only status
command in a clean external shell with command-line query access. Never convert
PID existence, health-file freshness, or process name alone into verified
Listener identity, and never use that degraded evidence for stop/start/upgrade
decisions.

The dispatcher resolves `-ProjectRoot` independently of its own script path.
Its default is the caller's current directory, so an absolute
`<script-root>\feishu-codex-bridge.ps1` does **not** select that script's project. Every
command prepared for a clean or user-operated external shell must therefore
include the exact `-ProjectRoot`, including read-only `bridge status`, `bridge
doctor`, and `bridge validate`. Render the two positional tokens literally as
`bridge <action>`; a bare `upgrade`, `status`, or similar word is parsed as the
scope and must fail with usage output. If the reported runtime unexpectedly
looks absent, verify the explicit project root before reasoning about PID or
health state.

When a command is transmitted through chat for manual paste, prefer one
physical command line with absolute paths. Do not require the operator to keep
PowerShell variables alive across reopened shells, and do not use backtick
continuations that chat formatting can split or decorate. Use a maintained
wrapper when a workflow needs several dependent steps.

Before Gateway registration, a deliberately started Listener reports
`degraded` and `desktop-gateway-unregistered`. Treat that as a healthy waiting
state only when the runtime PID is live, `event_consumer` is true, and logs show
both `[event] ready` and the Feishu WebSocket connected signal. Do not restart
or repair a listener solely because the Gateway is not registered yet.

## Administrative approvals and diagnostics

Require fresh tool-level approval for install, upgrade, start, stop, restart,
hook/config/rule changes, Codex/App Server invocation, Gateway creation,
replacement or mounting, scheduler creation/retarget/activation after changes,
process termination, and dynamic testing. Name exact target, interruption risk,
and recovery. Never combine deployment and restart.

For a rule diagnostic on Windows, use the verified independent CLI entry. If a
Desktop-packaged `codex.exe` returns access denied before process start, the
rules engine did not run; do not interpret that as `forbidden`, retry through
Computer Use, alter WindowsApps ACLs, or copy the binary. Install the official
independent npm CLI only after the prerequisite-install approval, then obtain a
fresh Codex-invocation approval and run the exact `execpolicy check`. The check
must emit `decision: allow` and the intended matched prefix; it never executes
the helper command following `--`.

Under another exact Codex-invocation approval, the verified independent CLI may
run bounded `codex doctor` when diagnosing Windows sandbox, endpoint protection,
proxy/network, Desktop state, or update connectivity. It is read-only evidence,
not repair or upgrade authorization, and a pass does not prove that an
automation-origin Desktop turn can use task tools. Retain only a redacted
summary; never retain credentials, diagnostic payloads, or unrelated machine
inventory.

After mounting, each allowlisted Feishu message or command authorizes only its
own data-plane queue operation. It does not grant administrative standing
permission.

Never run dynamic bridge tests inside Codex Desktop. Use external terminal/CI
with Listener stopped. Safe in-client diagnostics are:

```powershell
lark-cli auth status --json --verify
powershell -ExecutionPolicy Bypass -File .\scripts\feishu-codex-bridge.ps1 bridge validate -ProjectRoot <path>
powershell -ExecutionPolicy Bypass -File .\scripts\feishu-codex-bridge.ps1 bridge status -ProjectRoot <path>
powershell -ExecutionPolicy Bypass -File .\scripts\feishu-codex-bridge.ps1 bridge doctor -ProjectRoot <path>
Get-Content <path>\.codex\feishu-bridge\health.json
Get-Content <path>\.codex\feishu-bridge\bridge.log -Tail 80
```

Do not print `bridge.env`, queue payloads, session maps, or attachment manifests
in shared output; they may contain tenant IDs, task IDs, local paths, or user
message data.
