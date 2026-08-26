---
name: feishu-codex-bridge
description: "Configure and operate a local Feishu/Lark-to-Codex Desktop service-desk bridge. Use for Feishu CLI setup, bot permissions, explicit mount consent, resident private/group/topic listening, durable fenced task delivery, the single `/init` conversational task picker, and opt-in project routing. Never attach App Server to a target or inject knowledge context; each target task keeps its own project, model, tools, and context."
---

# Feishu Codex Bridge

Publish only this reusable Skill. Never publish project notes, vault content,
credentials, IDs, logs, queues, session maps, attachments, or runtime state.

## Current priorities

- **P0: exact Codex final reply return to Feishu.** Complete the existing
  Feishu -> Gateway -> target task path by returning that exact target turn's
  authoritative final through the fenced `feishu-codex-final-return` Hook
  transport. P0 includes Unicode fidelity, no replay after an uncertain send,
  and actual Listener delivery to Feishu.
- **P2: future official Desktop build compatibility.** Keep monitoring a
  positively different official build for native `latestAssistantMessage` and
  scheduler hard-cap compatibility, but do not wait for it before developing or
  testing the separately fenced P0 Hook transport. Never erase or reinterpret a
  current build marker.

## Architecture boundary

Use exactly three runtime roles:

```text
Feishu listener -> durable queue
Gateway scheduler heartbeat -> exact existing Desktop Gateway task
  -> one Gateway cycle starts with a metadata-only probe
  -> when pending: fenced claim and target routing in the same turn
  -> canonical target Codex task
  -> target final answer -> listener -> Feishu
```

- Keep every target task authoritative for its conversation, project, model,
  reasoning, approvals, Skills, plugins, browser, Computer Use, files, and
  knowledge access.
- Keep one dedicated Gateway task separate from targets. Combine Sentinel and
  Router control-plane phases in the same automation-origin turn. Never wake a
  second Router task and never create a new control task per scheduled cycle.
- Keep the Gateway, its scheduler heartbeat, and its active-work lease heartbeat
  conceptually separate. The scheduler creates a Gateway cycle; the helper
  subcommand `heartbeat` only renews an already fenced active-work lease.
- Make the empty cycle metadata-only. Run `sentinel-probe`; when
  `should_wake=false`, end immediately. When true, claim with the returned wake
  ID and fence in that same turn, then use Desktop task tools.
- Run each complete Gateway cycle in one Gateway model turn. Use separate
  bounded `functions.exec` cells only for the fixed queue-helper commands and
  top-level direct `mcp__codex_app` calls for Desktop task coordination. If a
  helper cell yields, resume only that exact cell with `functions.wait`.
  A successful claim is a commit point: never finish the model turn or defer
  its fence to another scheduled turn before terminal completion/failure and
  release.
- Treat task tools as direct MCP methods. After a claim, invoke the exact
  top-level `mcp__codex_app` method from the Gateway turn. Never call a Desktop
  app tool through `functions.exec`, `ALL_TOOLS`, or `tools[...]`; a listed
  dynamic alias may already be retired and is not capability evidence.
- Exact names help direct tool selection but do not grant tool eligibility. Do not optimize
  a Gateway onto a lightweight model before compatibility is proven. Create the
  candidate with model and reasoning overrides omitted, run the ordinary-turn
  preflight asset, and still require a live automation-origin `/init` catalog-
  and-selection canary through one predeclared exact target task.
- Keep the Python listener transport-only. Never locate or launch `codex.exe`,
  start App Server, mutate a target by RPC, edit Codex databases/rollouts, use
  deep links, named pipes, writer locks, or UI automation as fallback.
- Forward the target prompt unchanged and return only its authoritative final
  answer. Never solve, summarize, retrieve, rebuild history, or inject context
  in the Gateway.
- For every non-steer target send, take a zero-time `wait_threads` snapshot of
  only that exact target and retain only its cursor. Invoke `final-return-arm`
  for the claimed request/fence/target, then send once. The structured
  `UserPromptSubmit` Hook binds only the matching task and turn plus either the
  exact raw prompt hash or a strict Desktop delegation wrapper whose source is
  the Gateway recorded at arm time and whose inner input has that exact hash;
  the `Stop` Hook captures only that bound turn's latest final into fenced
  staging. After exact turn completion, accept either a matching captured Hook
  receipt or a native same-turn `latestAssistantMessage` in `final_answer`
  phase; fence the latter through `final-return-native` first. Preserve the
  original text. Never mistake the submission result, a pre-send message,
  `read_thread`, transcript, or another task's final for the answer.
- Send `/compact` to the target. Never author or persist a replacement summary.

Read [references/architecture.md](references/architecture.md) before changing
queue, binding, task creation, recovery, or project routing. Use these exact
reusable assets instead of reconstructing contracts from chat history:

Read [references/codex-wake-strategy.md](references/codex-wake-strategy.md)
before changing trigger, cadence, wake, liveness, or scheduler recovery. Its
event-driven path is future-only until an official Codex surface satisfies the
listed existing-task and tool-eligibility gates.

The open Codex Harness is not a fallback for this Desktop-owned Skill. Any
SDK-owned product belongs to the separate `feishu-codex-harness-bridge` Skill.
Read the routing boundary in
[references/harness-native-v2.md](references/harness-native-v2.md), then load
the sibling Skill when it is actually available. Never install its SDK, start
its worker, reuse a Desktop task ID, share a Feishu consumer, or migrate a queue
under this Skill.

- [assets/desktop-gateway-task.md](assets/desktop-gateway-task.md): full
  single-task Gateway contract with explicit initial-mount and existing-task
  rehydration modes;
- [assets/desktop-gateway-bootstrap.md](assets/desktop-gateway-bootstrap.md):
  read-only first-turn candidate capability check;
- [assets/desktop-gateway-model-preflight.md](assets/desktop-gateway-model-preflight.md):
  read-only ordinary-turn recheck after an approved Gateway model change;
- [assets/desktop-gateway-heartbeat.md](assets/desktop-gateway-heartbeat.md):
  paused existing-task scheduler automation settings and short prompt;
- [assets/desktop-gateway-manual-cycle.md](assets/desktop-gateway-manual-cycle.md):
  one-ticket, one-request owner-present diagnostic prompt used only while the
  scheduler remains paused;
- [assets/feishu-router.rules.template](assets/feishu-router.rules.template):
  fixed project-local queue-helper allow rule;
- [assets/AGENTS.feishu-codex-bridge.md](assets/AGENTS.feishu-codex-bridge.md):
  incrementally merged project policy.
- [plugins/feishu-codex-final-return](plugins/feishu-codex-final-return/README.md):
  repo-local P0 plugin with hidden MCP tools and exact `UserPromptSubmit`/`Stop`
  Hooks. Its marketplace entry is available, never silently installed or
  enabled.

Read [references/p3-bounded-soak.md](references/p3-bounded-soak.md) before
preparing or accepting a P3 result. P3 is an external stopped-Listener soak of
fixed local queue/delivery invariants; it never contacts Desktop or Feishu and
never substitutes for a live build canary.

Only when the user explicitly asks whether Codex can take over the Feishu
frontend, control the client, or run a real client conversation test, read
[references/feishu-desktop-client.md](references/feishu-desktop-client.md).
Do not inspect client installation or login during generic setup, first use,
`bridge preflight`, permission configuration, mounting, or headless operation.

Internal Python names such as `DesktopRouterQueue`, `router_thread_id`,
`session_owner=desktop-router`, and `sentinel-probe` remain the durable protocol
v4 field names. They do not imply two control tasks.

## Approval boundary

- Except for the disclosed first-use dependency installation, configuration,
  and permission pass already covered
  by the scoped onboarding approval below, obtain fresh tool-level approval
  before every live client-impacting action: install, upgrade, start, stop, restart,
  hook/config/rule change, Codex/App Server invocation, process termination,
  Gateway creation/replacement/mount, final-return plugin installation or
  enablement, final-return runtime registration, exact Hook trust, scheduler
  creation/retarget, activation after material change, or live test.
- Name the exact target, interruption risk, and recovery path. Do not bundle
  deployment with restart or reuse standing/oral consent.
- One explicit first-use onboarding approval may cover the disclosed missing
  packages, verification, exactly one `lark-cli config init --new`, and one
  official split OAuth request defined by the `openclaw-common-chat` profile:
  `auth login --recommend`. The user must personally scan and approve each
  Feishu page. This approval lets the Skill initiate that exact flow and later
  complete its returned device code; it
  does not let the Skill approve its own scopes. It never permits browser or
  administrator actions, Bot tenant-scope changes, app publication, extra
  OAuth scopes, `bridge init`, Listener mounting, Codex restart, Gateway
  creation, or scheduler creation or activation.
- A frontend-takeover request is a separate conditional workflow, not part of
  ordinary onboarding. After detecting a missing client and disclosing the
  official source, package size, install scope, and login hand-off, one exact
  approval may cover that client installation and one launch only. Stop when
  the login QR/account screen appears. It does not authorize authentication,
  account choice, security/privacy prompts, or test-message sending.
- Treat an allowlisted Feishu message or slash command as authorization only for
  that single data-plane queue operation after mounting and activation.
- Never run dynamic bridge tests from Codex Desktop, even with approval. Run
  them only in external CI/terminal with the listener stopped. In Desktop, use
  source edits, AST parsing, `bridge validate`, `bridge status`, `bridge doctor`,
  and supplied logs/results.
- Explain `bridge init` before running it. It creates `AGENTS.md` when absent or
  replaces only the marked Skill block; it preserves unrelated rules.

### Approval compression

- Treat an explicitly stated ongoing goal as active until it is completed or a
  genuine gate is reached. Do not end a turn merely by announcing what the next
  step would be while safe, in-scope work remains. Continue the work and use
  concise commentary for progress; yield only for completion, a required human
  action or choice, a fresh authorization boundary, or a terminal blocker.
- Finish all relevant read-only discovery, exact path/identity resolution,
  source-only preparation, and risk analysis before asking for approval. Ask at
  the latest responsible moment, not once per preparatory step.
- One approval for one exact action includes its deterministic command
  rendering, bounded execution wait, same-action progress updates, and
  read-only postcondition checks such as status, doctor, hash, manifest, queue
  counts, and exact transaction status. Do not ask again merely to inspect or
  report the result.
- An approved execution unit may span multiple assistant turns, yielded tool
  cells, bounded waits, transport-only retries, and read-only verification.
  Resume it automatically until that exact unit completes or reaches its stated
  stopping condition; never insert generic `continue`, `next step`, or repeated
  `同意` prompts between those internal stages.
- When adjacent actions have the same exact target, risk, recovery path, and
  authorization boundary, and no rule below requires them to remain separate,
  freeze them as one ordered, bounded transaction and ask once for that whole
  enumerated unit. The reply authorizes only that displayed transaction, not
  later work with a different executable, target, effect, or rollback path.
- If an attempt provably failed before its exact disclosed executable or helper
  ran, fix only shell quoting or transport syntax and retry that same action
  under the existing approval. Resolve a different executable, file, target,
  scope, subcommand, risk, or recovery path before the approval; discovering
  such a change afterward requires a new checkpoint.
- Group source edits, documentation synchronization, AST parsing, static
  validation, and preparation of one maintained external command without
  generic `continue` or `next step` prompts. Supplying external evidence also
  authorizes its read-only verification, not a later deployment.
- At the start of a multi-checkpoint workflow, preview the remaining mandatory
  checkpoints compactly in the same message so the user can anticipate the
  whole path. Ask only for the current exact checkpoint when later checkpoints
  are required to remain separate, and do not make the user answer a chain of
  generic `同意下一步` questions. Never treat one reply or a sequence of earlier
  `同意` messages as standing consent for later actions, especially undisclosed
  or separately mandated ones.
- Preserve every explicit separation in this Skill: after first bootstrap,
  runtime, hooks/config/rules, upgrade, start/stop/restart, Gateway
  create/replace/mount, scheduler create/retarget/activate, temporary bind/stop/
  rollback/restart, `codex.exe` invocations, and each one-ticket manual
  diagnostic remain distinct whenever their rules say so. Compression removes
  redundant questions; it never bundles separate client impact. Compression
  removes redundant questions without converting separate effects into one
  authorization.

## Setup workflow

Use state branches, not a linear replay of a prior installation. Before any
mutation, identify the current branch with read-only evidence and load the
referenced contract for that branch.

### 1. Classify current state

1. For explanation-only work, do not install or mutate anything.
2. Run read-only `bridge preflight` for setup or migration diagnosis. It must
   not detect the Feishu Windows client; frontend inspection belongs only to
   the explicit takeover workflow below.
3. Before any Gateway action, inspect only bounded `bridge status`/`bridge
   doctor`, registration metadata, scheduler configuration/status, and Desktop
   task metadata. Never inspect a queued payload to reconstruct state.
4. Select exactly one branch:

   - **First mount**: no Gateway registration and no scheduler target exists.
   - **Existing Gateway**: exact registration and paused scheduler are coherent;
     use rehydration or maintenance, never initial mount.
   - **Known incompatible build**: a genuine automation-origin canary on this
     Desktop surface either terminalized `target_tool_unavailable` after helper
     claim with `may_have_started=false`, or ran more scheduler turns than its
     declared hard count. Keep it paused and do not change prompt, model, rule,
     or scheduler shape to retry.
   - **Unknown or conflicting state**: fail closed and ask for a bounded repair
     decision; do not create a replacement or use `--force`.

Treat the build as unchanged unless a different official Desktop build/surface
is positively identified. A model, prompt, context, or task change alone is
not a new compatibility surface.

Before an ordinary-turn Gateway preflight or any finite live-canary activation,
run the read-only `bridge canary-gate`. It auto-detects the running Codex Desktop
package build when the shell can inspect it; otherwise obtain the exact package
build through a separately disclosed read-only process query and rerun with
`-DesktopBuild <package-version>`. `blocked` forbids another canary on that
build, and `unknown` fails closed. `pass` means only that no shipped terminal
incompatibility marker matches; it authorizes neither activation nor a claim and
must still be followed by the ordinary-turn preflight, current P0-B evidence,
   and the separately approved finite live canary. Never add a marker from a prompt
   failure, ordinary-turn result, model change, or uncertain target outcome. An
   observed scheduler run count above an exactly read-back hard cap is a valid
   surface marker because it proves that the finite safety boundary was not enforced.

### 2. First-use Feishu onboarding

Read [references/permissions-and-hooks.md](references/permissions-and-hooks.md)
and
[references/openclaw-common-chat-permissions.md](references/openclaw-common-chat-permissions.md)
before dependency, configuration, OAuth, Bot-scope, hook, or rule work.

- Inventory Python 3.10+, Node/npm/npx, `lark-cli`, and an independently
  runnable official Codex CLI without starting Codex. A WindowsApps packaged
  binary is not that CLI and must not be copied or have its ACL changed. Read
  the verified npm shim and `@openai/codex` `package.json` to record the exact
  CLI version; a host CLI version never upgrades a separately pinned SDK.
- One disclosed onboarding approval may cover only the exact missing
  prerequisites, verification, one `lark-cli config init --new`, and one
  `auth login --recommend` device flow. The user personally reviews and scans
  every Feishu page. No Bot tenant-scope, browser/admin, publication, mount,
  restart, Gateway, scheduler, or later write is included.
- On a migrated machine, treat keychain-backed secrets and tokens as
  machine-local. Before starting a new QR flow, perform at most one separately
  approved credential-visible `auth status --json --verify` check.
- Report user OAuth, Bot credential validity, and Bot tenant-scope audit as
  three independent results. QR user authorization does not replace developer
  console declaration or administrator approval for Bot scopes.
- Do not inspect or install the final-return plugin during generic Feishu CLI
  setup. When the user asks to enable or test P0 reply return, inspect the
  repo-local marketplace and current plugin state read-only. If
  `feishu-codex-final-return` or its Python prerequisite is missing, disclose
  the exact official/local source, scope, restart effect, and rollback, then ask
  for one exact installation or enablement approval and perform it
  automatically after consent. Stop at any visible trust or login prompt that
  requires the user.
- On a pre-P0 installed Bridge runtime, `bridge final-return-status` must return
  the answer-free `upgrade_required` contract instead of invoking an unsupported
  helper subcommand. Treat that result as a runtime deployment prerequisite,
  not as a plugin or Desktop-build failure; `bridge upgrade` remains its own
  approval checkpoint before runtime registration.

### 3. Mount listener and project policy

After the welcome text and explicit mount consent, keep each administrative
action separate:

1. explain and run `bridge init` for the incremental managed `AGENTS.md` block;
2. treat first `bridge install` as one disclosed bootstrap action containing
   runtime, the two lifecycle hooks, initial `bridge.env`, the integrity
   manifest, and Bridge-only `hooks.json` registration; it never merges project
   rules. For a pre-manifest upgrade, stop separately, run `bridge hooks` under
   its own approval, then run runtime-only `bridge upgrade` under another;
3. under a separate configuration approval, run
   `bridge access -AccessMode locked -OwnerOpenId <ou_...>` (and only the
   explicitly chosen admin/user/chat IDs). Fresh installs write locked and a
   missing access key remains locked; malformed or empty recognized boolean,
   enum, or integer values refuse startup. Do not start a canary or production while the allowlist is
   empty or explicit legacy `compat` is active;
4. install and enable the repo-local `feishu-codex-final-return` plugin only
   under its own approval; its marketplace policy is `AVAILABLE`, never
   auto-enabled. Separately register the exact integrity-valid installed runtime
   with `bridge final-return-register`. A different registered runtime fails
   closed and is never replaced implicitly;
5. validate BOM-less array-shaped hooks, then separately review and trust only
   the exact Bridge lifecycle Hook hashes and the plugin's exact
   `UserPromptSubmit` and `Stop` Hook hashes. The review UI is global and may open on CLI
   startup; judge the Bridge event rows individually and leave unrelated hooks
   pending. On Windows prefer a clean `cmd.exe` launch of the independent
   `codex.cmd` so PowerShell/PSReadLine publisher prompts do not obscure review;
6. render the exact project-local queue-helper rule and, under its own Codex
   invocation approval, validate intended argv with `execpolicy check`; and
7. restart Codex separately, then use read-only `bridge status`, `bridge
   doctor`, and `bridge final-return-status` to verify Listener, waiting-state,
   plugin registration, and installed-runtime integrity evidence.

When Windows sandbox, endpoint protection, proxy/network, Desktop state, or
update connectivity is the unresolved issue, a separately approved invocation
of the verified independent CLI may run bounded `codex doctor`. Treat it as a
diagnostic only: it must not repair, update, restart, or certify Desktop task
tool eligibility, and its output must be redacted before retention.

An install or upgrade writes an integrity manifest binding the installed Python
files and both lifecycle hooks. Manual `bridge start` additionally requires the
current Skill source to match the installed copy; SessionStart validates the
installed manifest before creating a lease. Missing, stale, or mismatched state
fails closed and requires a separately approved install or upgrade. A
runtime-only upgrade may keep hooks only when the installed start hook already
supports the same manifest schema.

Treat `bridge.pid` as an untrusted reference, not process identity. Status,
start, stop, install, upgrade, and external-test guards must require a Python
process whose command line contains the exact installed `bridge.py` path.
Never stop a non-Bridge PID after Windows reuses it; when a Python command line
cannot be verified, fail closed without stopping or starting another process.
After a process was already verified as the exact Listener and a graceful stop
was requested, command-line loss during the bounded exit wait is only a
transient observation: keep waiting without force-stopping it. Report success
only after the PID disappears or is verified as a different process; if it
remains unverifiable at the deadline, fail closed.
When restricted Desktop diagnostics report `Runtime: unknown`, rerun only the
same read-only status command in a clean external shell that can query
`Win32_Process`; PID, process name, or health freshness alone never proves
identity.

The disclosed first-bootstrap package above is the only indivisible
runtime+initial-hooks+initial-env action. After it exists, do not combine runtime,
hook/config/rule changes, restart, or process lifecycle under one approval.
Never run dynamic Bridge tests from Codex Desktop; prepare
the external command and inspect supplied evidence instead.

### 4. First Gateway mount and compatibility canary

Read [references/architecture.md](references/architecture.md) and
[references/codex-wake-strategy.md](references/codex-wake-strategy.md), then
use the exact assets listed above.

1. Only in the **First mount** branch, create one candidate with model and
   reasoning overrides omitted. Its first turn is exactly
   `assets/desktop-gateway-bootstrap.md`; require the stored JSON final, one
   successful direct `mcp__codex_app.list_threads` call with an explicit limit
   no greater than 50, and one successful direct
   `mcp__codex_app.list_projects` call.
2. Under a new approval, render `assets/desktop-gateway-task.md` in
   `INITIAL_MOUNT`. Use `REGISTER_NEW` only when registration is absent.
   Replacement requires a separately approved owner migration and is the only
   branch that may contain `--force`.
3. If a delegated mounting turn cannot present approval and performs no
   registration call, do not resend the contract or register from the
   controller. The owner opens that exact candidate and directly confirms the
   already rendered one-time action.
4. Under another approval, create or retarget one exact existing-task scheduler
   heartbeat, force/read it back as `PAUSED`, and verify that no turn slipped
   through. A setup canary is capped at three runs. Exact recurrence readback is
   necessary but not sufficient: count the actual turns, and if more than three
   run, pause immediately, record `scheduler_cap_unenforced` for that exact
   build/surface, and forbid reactivation. Supervision is not a hard cap.
5. For an active `/init` catalog, the mounted contract's source-exact
   `normalizeActiveDesktopCatalog` pseudocode is the deterministic normalizer.
   The Gateway first obtains native results through direct
   `mcp__codex_app.list_projects` and bounded `mcp__codex_app.list_threads`
   calls, then applies the exact mapping in the same model turn. It must not pass
   raw Desktop results through `functions.exec`, shell/stdin, generated source,
   or an intermediate file, and must not replace the mapping with different
   model-authored logic. A failed named validation condition yields only its
   stable content-free stage code.
6. Activate the finite canary only under a fresh approval and only while the
   owner is ready to send `/init`, select the predeclared exact target from the
   returned snapshot, and confirm it. A catalog without selection, an
   unsupported slash-command reply, an empty cycle, rehydration,
   ordinary-turn preflight, or aggregate
   completed count is not success. Verify the exact-scope binding and route one
   ordinary test message to that exact task.

If the same official Desktop build already has the known incompatible terminal
marker, this branch is forbidden. A newer official build must repeat the
ordinary-turn preflight and one fresh live canary; there is no SDK, App Server,
shell, database, rollout, or UI fallback.

When waiting for another Desktop build is not the user's priority, a
`scheduler_cap_unenforced` marker blocks only scheduler reactivation, not the
source-defined one-ticket diagnostic lane. Keep the scheduler paused. For each
expected queue operation, obtain a fresh owner approval in the controlling
task, invoke the installed helper's unallowlisted `manual-authorize` command,
then run `scripts/render_gateway_manual_cycle.py` against the current Skill
root and send its one exact self-contained output to the registered Gateway.
The renderer combines `assets/desktop-gateway-manual-cycle.md` with only the
matching operation section and the shared `## Complete or fail` section from
`assets/desktop-gateway-task.md`; reject any unresolved placeholder or wrong
heading instead of hand-assembling the prompt. The ticket
is bound to the exact task, host, and operation, expires within ten minutes,
and is consumed on first probe. The Gateway may claim only that one matching
request, must skip the grace claim, release explicitly, and stop.

This is not a generic manual copy of the heartbeat prompt. The prompt alone is
not authority; successful helper-side atomic ticket consumption is required.
The Gateway never invokes `manual-authorize`, and the controller never claims,
reads the Feishu payload, calls a target directly, or writes a binding. The
manual probe may inspect only request IDs and operation names, does not update
scheduler freshness, and does not clear a build marker or establish production
compatibility. Use a new ticket for each `/init` catalog, confirmed task
inspection, and ordinary-message delivery operation.

Accept an owner-present exact-turn Hook canary only with the black-box protocol
in [references/codex-wake-strategy.md](references/codex-wake-strategy.md). In
particular, require one send, one matching completed target turn, a captured
Hook receipt, completed Listener delivery, no new terminal failure, and the
expected reply in Feishu. To prove same-task context, use two sequential
ordinary messages with a fresh unique nonce: wait for the first terminal reply,
then obtain the separately required ticket for the recall message. Stop after
the exact recall reaches Feishu. Never inspect target history or use UI,
`read_thread`, database, rollout, OCR, or clipboard output as reply evidence.
The pass applies only to the tested source/runtime/Hook/build/configuration; it
does not clear native or scheduler markers, certify `/init`, or authorize
production. A temporary binding still requires its separately approved rollback
sequence after testing.

Stop ordinary-message diagnostics on the same Desktop build/surface after two
separately approved owner-observed deliveries prove all of the following: the
Unicode prompt is intact in the exact same target task; both target turns
complete; the second turn preserves first-turn context; `wait_threads` exposes
no `latestAssistantMessage`; and bounded `read_thread` returns empty `items`.
Record `target_final_readback_unavailable`. This is an ingress and context pass
but a native final-return failure. It blocks another native
`latestAssistantMessage` diagnostic on that same build/surface. Changing the
model, prompt, Gateway/target task, context, or delay is not new native evidence;
only a positively different official Desktop build/surface may run one fresh
native final-return canary. It does not block source work or one separately
approved finite canary of the materially different exact-turn Hook transport
after same-source P0-B/P3, runtime upgrade, plugin install/enable, runtime
registration, and exact Hook trust pass. That canary uses the one-ticket manual
lane while the scheduler stays paused and does not clear either build marker.
Never substitute UI, database, transcript, rollout, App Server, OCR, or
clipboard capture.
A later read-only `wait_threads(timeoutMs=0)` snapshot of an already completed
turn may establish a current result shape for source development, but it does
not prove a new send returned through the Gateway and does not clear either a
build marker or the prohibition on another same-surface mutating diagnostic.

### 5. Existing Gateway, model change, or compaction

Keep the scheduler paused. Verify that bounded registration metadata still
names the exact task and host, then use
`CONTRACT_TURN_MODE=REHYDRATE_EXISTING` and
`REGISTRATION_ACTION=NO_REGISTRATION`. That turn performs no tool or command
and ends exactly `DONT_NOTIFY`.

Only after an explicitly approved model change or context compaction, send the
ordinary-turn prompt from `assets/desktop-gateway-model-preflight.md` and
parse its exact stored final. The mounted contract must recognize this exact
prompt as the sole manual read-only exception to its fenced-claim gate. An
empty stored turn, `DONT_NOTIFY`, a missing bounded `list_threads` invocation,
or non-JSON final is a failed preflight: keep the scheduler paused and do not
infer compatibility. This does not repair a build already proven
automation-origin-incompatible, does not register, does not activate, and does
not replace the live canary.

If `wait_threads` reports the preflight completed but `read_thread` returns an
empty `items` array while the owner can see the exact JSON final in the opened
Gateway task, classify the result as
`visible_preflight_pass_readback_unavailable`. Do not resend the same
background prompt, retry after an arbitrary delay, or infer eventual
consistency. The owner's exact transcription may satisfy only the ordinary-turn
prerequisite for one separately approved finite live canary when all of these
conditions hold:

- `bridge canary-gate` passed for a positively identified newer official build;
- bounded metadata still names the exact registered Gateway and host, and its
  scheduler remains paused;
- the owner personally submitted the exact asset prompt in the opened Gateway;
- the transcribed JSON has exactly the required keys and all four boolean
  checks are `true`;
- controller `read_thread` identifies that exact completed turn but returns an
  empty `items` array; and
- a bounded read-only control on the predeclared non-Gateway target returns the
  exact target ID plus a stored user item and final agent item.

This exception does not turn the transcription into stored-final evidence,
certify automation-origin eligibility, authorize activation, or permit
production. Keep the scheduler paused until the current P0-B requirement and a
fresh live-canary approval are satisfied. The finite canary must still prove
the `/init` catalog selection, exact binding, ordinary target delivery, and
target final return; any unknown or failed result remains paused under the
normal terminal-marker rules.

### 6. Production and optional knowledge work

An always-on recurrence is forbidden until the exact Gateway/build/configuration
has passed the live `/init` catalog-and-selection canary. Production then requires two later
approvals: first replace the finite recurrence while paused and verify its full
readback; then activate that verified recurrence. Disclose cadence, observed
model/context cost, and pause/recovery before both requests.

Configure Obsidian or another knowledge workflow only after an explicit
knowledge-base request and only inside the target project. The bridge has no
knowledge root, retriever, index, or context injector.


## Optional Feishu frontend takeover

Activate this workflow only after the user explicitly asks about taking over
the Feishu frontend, controlling the Windows client, or automating a real
client conversation test. A generic Bridge setup, permissions request,
mounting request, or first use does not trigger it. Do not run client detection
from `bridge preflight`.

1. Read
   [references/feishu-desktop-client.md](references/feishu-desktop-client.md)
   and run `feishu desktop-status`.
2. If the client is missing, query current official package metadata and
   disclose source, observed download size, install scope, restart impact, and
   that the workflow stops for user authentication. After exact consent, run
   `feishu desktop-install -DesktopInstallConsent`. Accept only the official
   CDN, validate the returned hash and Authenticode signature, install from a
   dedicated system-temp directory, verify the installed executable, and
   remove the installer.
3. Launch exactly the verified executable once. Inspect one uniquely selected
   Feishu window. Process, registry, and cache state never prove login. If
   screenshot capture fails with `SetIsBorderRequired` or another capture API
   error, discard the stale state, reselect the unique window, and make one
   accessibility-text-only observation. Do not loop, guess coordinates, or
   fall back to custom UI automation. Accessibility output may contain the
   visible chat list: use only the minimum login/target evidence and never copy
   unrelated chat names or previews into logs, prompts, or Skill files. A
   successful observation does not prove input automation works. If focus or
   click reports that user input occurred after the observation, discard the
   stale state, re-observe once, and retry that same focus action only when the
   requested composer is still uniquely identified. If focus remains on the
   document root or click fails with `coordinate input geometry is unavailable`,
   do not type blindly; report the client as logged in but observation-only and
   ask the user to send the exact test command manually.
4. If a main workspace is visible, report logged in. If a QR/account page is
   visible, report logged out and stop so the user can scan or authenticate.
   An ambiguous splash/update/error/multi-account page is unknown. Do not
   automate authentication, credentials, CAPTCHA, account choice, or
   security/privacy prompts.
5. After the user reports login completion, re-observe the current window.
   Installing or logging in is not permission to send test messages; obtain
   fresh exact live-test approval.

First-use onboarding consent prompt:

> 检测到首次运行所需的以下依赖缺失：`<missing items；若无则写“无”>`。是否同意一次明确的首次 onboarding 授权：我只从逐项列明的官方来源安装并验证这些依赖；验证成功后自动启动一次 `feishu configure` 扫码创建 PersonalAgent 应用；配置完成后再发起一次官方飞书 CLI 常用用户权限扫码（`auth login --recommend`）？`--recommend` 是随 CLI 版本变化的跨业务域权限集合，可能数量很多且包含写权限；飞书授权页会展示本次准确范围，请你亲自审阅、扫码并批准。你回复完成后，我只负责续上对应 device code；scope 获批不代表允许我执行相应写入或高风险操作。Bot 租户权限与用户扫码授权是两层；二维码不能完全替代开发者后台的 Bot scope 声明/管理员审批。若 Bot 仍缺权限，我只转发 CLI 给出的开发者后台链接；你不想使用 JSON 时，走后台界面逐项配置，只有你明确选择批量导入时我才生成精确 JSON。这不是 Skill 自我授权，不允许我代做浏览器或管理员动作、追加上述范围外权限、发布应用、改变访问策略、运行 `bridge init`、挂载 Listener、重启 Codex、创建 Gateway 或启用 scheduler heartbeat。失败或过期时我会停止，不会静默重开。

Frontend-takeover install consent prompt:

> 你已明确提出接管飞书前端/操控客户端/真实会话自动测试。检测结果：飞书 Windows 客户端`<已安装路径/未安装>`。若未安装，是否同意我从飞书官网当前元数据指向的官方 CDN 下载约 `<size>` 的安装包，核对官网哈希和数字签名后静默安装，验证并删除临时包，再只启动一次客户端？我会在出现二维码或账号登录页时停下，由你本人完成扫码、账号选择、验证码及安全/隐私确认；此授权不包含发送测试消息。

Welcome text:

> 欢迎使用 Codex 飞书机器人。飞书 CLI 安装完成后，可以把私聊、群聊 @ 和群话题消息挂载到 Codex Desktop 项目。Listener 只负责接收、鉴权和持久排队；一个两分钟 Gateway 调度 heartbeat 只负责在专用 Desktop Gateway 现有任务中产生自动化回合。空轮只查队列元数据，有消息时该 Gateway 在同一回合领取并转发到目标 Codex 任务，不会用第二个 App Server 占用目标任务。目标任务继续使用自己的模型、推理、项目文件、Skills、插件、浏览器、Computer Use 和知识库。首次消息只提示发送 `/init`，随后用对话菜单按项目查看任务名称和完整 ID、选择或新建任务；旧斜杠命令不再执行。默认只回传最终答案。Listener 安装后，还需要你逐项同意创建 Gateway、挂载并注册合同、创建指向该现有任务的调度 heartbeat，以及激活它。是否同意挂载？

## Runtime contract

- Consume `im.message.receive_v1` through `lark-cli event consume`.
- Accept direct messages and group messages that mention the bot. Add a stable
  topic suffix so unrelated group topics do not share a binding.
- Bind by stable Feishu chat/topic scope and exact Codex task ID, never title.
  Same-name users/groups remain separate; one target may be actively bound to
  only one scope.
- Keep Feishu delivery state in SQLite and bindings in `sessions.json` schema
  v4. Preserve old canonical target IDs during safe migration.
- Retry transient Feishu reply failures from `reply_pending`, but terminalize
  structured API code `230011` because the source message was withdrawn and can
  never accept a reply. Clear retained payload/answer and do not classify other
  codes as permanent without observed evidence.
- Persist each authorized event before checking Gateway freshness. Require a
  registered Gateway before Desktop-queue submission, but not an idle
  active-work lease heartbeat; sleeping between scheduled cycles is normal.
- Use deterministic operation/event request IDs. Reuse with different content
  must fail. Never replace a first terminal outcome with another success or a
  retryable result, and never replay a claim whose target action may have started.
- If a bounded listener wait expires before claim, keep both the queue request
  and Feishu event retryable under the same idempotency key. Wizard actions
  must not turn that durable pending state into a delivered failure. For the
  owner/admin new-project action inside `/init`, persist the exact staged child directory and resume it only
  for that same event key; a later event must never adopt the directory or
  overwrite its pending marker.
- Each queue request generation has one first terminal outcome. Retention may
  irreversibly redact expired answer text to non-retryable unknown, but never
  back to success or retryable. Only a terminal failure that explicitly says `retryable=true` and
  `may_have_started=false` advances the same operation/event key to its next
  deterministic generation. Target-lifecycle and unknown-result failures never
  advance. Expired `inspect_thread` and `list_task_catalog` claims are the
  read-only exceptions: their Gateway contracts cannot mutate a target, so
  terminalize them as safe to retry. Keep every other expired claim non-
  retryable and uncertain.
- Treat every `inspect_thread` and `list_task_catalog` result as read-only.
  Catalog `visibility=exact` must never widen, even for an empty ID list; exclude
  the dedicated Gateway task and never return summaries, messages, or prompts.
  Normalize the direct
  `mcp__codex_app.read_thread` return only as a native object or one JSON parse,
  require normalized `thread.id` to equal the claimed target, and never mark an
  unconfirmed result `may_have_started=true`. Fail an absent, malformed, or
  mismatched result as non-retryable `invalid_gateway_result` with
  `may_have_started=false`; the queue helper rejects a may-have-started failure
  for this read-only operation.
- For the current Desktop catalog envelopes, parse a string result at most once
  and map native fields explicitly: project `projectId/label/path/hostId/projectKind`
  becomes Bridge `project_id/label/root/host_id/kind`; task
  `id/title/projectId/hostId/status/updatedAt` becomes
  `thread_id/title/project_id/host_id/status/updated_at`. Ignore additive
  envelope metadata, omit projectless or unknown-project tasks, and never read
  or copy `summary` or `cwd`.
- Record pending generation in `wake.sqlite3`. Let one scheduled Gateway cycle
  reserve one wake lease; overlapping cycles see `wake_inflight` and end.
  Require the same fence for claim, active-work lease heartbeat, staging,
  completion, failure, and release.
- Use a zero-wait first claim, at most eight requests, and one post-work
  `claim --wait-seconds 20 --release-on-empty`. Never run an indefinite watcher.
- Keep `/init` wizard replies and allowlist decisions outside target history.
- Preserve original user text. Append only a bounded validated read-only
  `<feishu_transport_attachments>` manifest when needed; current Desktop send is
  text-only, not native typed-media delivery.
- Omit model/reasoning overrides. Never inject Feishu envelopes, queue IDs,
  session state, RAG excerpts, summaries, logs, or reconstructed history.
- Forward only target final text to Feishu. Do not forward Gateway commentary,
  tool calls, approvals, local paths, or routing metadata.
- Before an ordinary send, capture only the exact target's zero-time
  `wait_threads` cursor as a stale-final boundary, then arm the exact claim with
  `final-return-arm`. After the one send, carry that cursor through bounded
  waits. When the exact new `latestTurn` completes, query its exact task/turn
  through `final-return-status`. A captured Hook receipt is already in fenced
  staging; the Gateway neither reads nor rewrites it before `complete`. A native
  same-turn `latestAssistantMessage` in `final_answer` phase is accepted only
  after `final-return-native` fences late Hook capture, then its original text is
  staged unchanged. If the first exact completed poll lacks both sources, pin
  its turn ID and cursor and wait at most 20 additional seconds while repeating
  only exact-target waits plus Hook status, without re-sending. A different
  turn, conflict, invalid result, or expired grace is an uncertain started
  mutation, never a retry or `read_thread` fallback.

### Archived or missing target

Accept `target_archived` or `target_not_found` recovery only when the Gateway
proves `may_have_started=false`. For a newly handled event, create exactly one
fresh Desktop task in that exact scope's current project, atomically replace the
binding, and deliver the unchanged prompt once with a target-specific key. Stop
if the replacement also disappears. Never match another task by title, restore
an uncertain delivery, or revive a legacy retry storm.

For explicit restore, read the exact task ID even when listings omit it. If
readable and archived, call `set_thread_archived(archived=false)` and bind it.
For create, restore, or compact operations, archive only explicitly supplied
displaced IDs after the primary action succeeds. Treat only IDs returned from
successful `set_thread_archived(archived=true)` calls as archived; never echo a
request list, infer a missing result, or archive the active target.

## Feishu control surface

`/init` is the only supported Feishu slash command. It creates a ten-minute,
memory-only immutable catalog snapshot and enters a natural-language wizard. Read
[references/feishu-command-ux.md](references/feishu-command-ux.md) before
changing it.

- Owner/admin may see all bounded non-archived Desktop tasks; other authorized
  scopes see only their exact related task IDs and a new-task entry.
- Show project label, task title, and the full exact task ID. Archived tasks are
  an explicit secondary view. Never show project paths, summaries, messages,
  prompts, or Gateway metadata.
- Resolve page-local numbers only against the transient snapshot. Persist only
  its numeric expiry marker; never write titles, project labels, or local roots
  to `sessions.json`. Require a
  separate confirmation before connect/restore, create, compact, disconnect,
  reply-mode, or project-create mutations.
- New task preserves every old task; switching or disconnecting never archives
  or deletes it. Compaction stays on the same exact task.
- Every slash command other than `/init` is unsupported. Do not maintain aliases
  or per-command compatibility, execute nothing, and never forward it to a
  target. Return only the generic `/init` entry prompt.

## Project and attachment caveats

- Default to the bridge-mounted Desktop project. Existing targets keep their
  persisted project; forwarding never changes `cwd`.
- Require exact `list_projects` match for target creation. New-project creation
  inside `/init` is disabled by default, restricted to locked owner/admin, and creates only one
  portable direct child. On `project_not_registered`, remove only the newly
  created empty folder and never fall back to another project.
- Use the minimal non-empty routing-ready bootstrap required by current Desktop
  `create_thread`, then send the first real Feishu prompt as the next turn.
  Never force sidebar repaint.
- Treat flattened lark-cli content as rendered text. Send multiline Markdown as
  exact Feishu `post` JSON with one row per source line.
- Constrain inbound resources by count, type, size, quota, extension,
  containment, and TTL. Validate outbound media as project-relative files.

## Temporary binding diagnostic

Use a temporary binding only when the owner explicitly asks to isolate ordinary
message delivery from a broken `/init` control flow. It is a maintenance
diagnostic, not a second end-user binding workflow.

- First use Desktop read-only task tools to exact-match the target title, full
  task ID, host, project ID, and project root. Require the selected Feishu scope
  to be currently unbound and the target to have no other Feishu owner.
- Never edit `sessions.json` by hand. Use
  `scripts\temporary_binding_transaction.py`, which resolves only one 12-digit
  scope hash, records an integrity-pinned unbound baseline and transaction ID,
  and holds the bridge maintenance lock while changing state.
- Stop the verified Listener under its own fresh approval before binding. The
  bind itself, the later restart, the second stop, the rollback, and the final
  restart are distinct client-impacting actions and retain distinct approvals.
- Send exactly the agreed ordinary test message. Do not use the temporary bind
  to bypass access policy, attach a target already owned by another scope, or
  mutate the target through a controller-side fallback.
- Roll back only when the transaction ID, exact target ID, active route, and all
  retained project routes still belong to that transaction. A mismatch fails
  closed for manual review; never perform a broad unbind or reconstruct the
  baseline from memory.
- `status` is read-only. `bind` and `rollback` require
  `--listener-stopped-acknowledged`; they do not start, stop, upgrade, contact
  Codex, inspect messages, or touch the Gateway queue.

For every Windows helper boundary, keep stdout control JSON ASCII-only and
carry non-ASCII text as standard JSON `\uXXXX` escapes. The Gateway parses each
wire object exactly once and forwards the resulting Unicode string unchanged.
External P0-B must pin a Chinese punctuation plus emoji roundtrip; an
ASCII-only fixture is insufficient evidence for real Feishu delivery.

## Local commands and defaults

Use `scripts\feishu-codex-bridge.ps1`:

### Machine-readable diagnostics

- `bridge status -Json`, `bridge doctor -Json`, and `bridge validate -Json`
  remain read-only and each emit exactly one compact JSON object. The contract
  starts with `schema_version=1`, `command`, and `status`; consumers must reject
  an unsupported schema version instead of guessing from text.
- Keep the existing human-readable output as the default. In JSON status,
  report the installed manifest version separately from the last health
  snapshot version because a stopped Listener can leave an older snapshot.
- Expose only operational booleans, enums, counts, stable issue codes/names,
  and versions needed for diagnosis. Never include Feishu or Codex task IDs,
  message/prompt/answer text, credentials, access-list values, or local paths.
- `bridge validate -Json` must not launch tests or another process. It reports
  `child_process_started=false`; a failed static gate returns `status=fail`, a
  stable error code, and a nonzero process exit code.

When rendering a dispatcher command for any shell whose current directory is
not already proven to be the target project, always pass
`-ProjectRoot <exact-project-root>`: the absolute `-File` path selects the
dispatcher, not the project. Preserve both positional tokens as
`bridge <action>` (for example `bridge status`, never bare `status`). For a
human-run external P0-B, provide the maintained one-shot wrapper as one
stateless physical command line with absolute PowerShell, Python, script, and
all three root paths; do not rely on variables, backtick continuation, or a
prior shell session.

For PowerShell snippets, never interpolate a variable immediately followed by
`:` as `"$name:..."`; PowerShell parses that as a scoped-variable expression.
Use `"${name}:..."` or the `-f` format operator. Do not ask the user to create
predictable work/evidence directories first: the maintained one-shot wrappers
own unique directory creation and stop before JSON parsing or validation after
any nonzero child exit.

| Intent | Command |
| --- | --- |
| Install CLI | `feishu install` |
| Configure/login/check | `feishu configure`, `feishu login -Recommend -NoWait`, `feishu login -DeviceCode ...`, `feishu doctor` |
| Merge project rules | `bridge init -ProjectRoot <path>` |
| First Listener bootstrap | `bridge install` |
| Refresh hooks only | `bridge hooks` |
| Upgrade source runtime only | `bridge upgrade` |
| P0 final-return registration status | `bridge final-return-status` (read-only; no answer text, task ID, or local path) |
| Register P0 Hook runtime | `bridge final-return-register` (separate approval; exact installed manifest-valid runtime only) |
| Remove P0 Hook runtime registration | `bridge final-return-unregister` (separate approval; exact matching runtime only) |
| Listener lifecycle | `bridge start`, `bridge stop`, `bridge restart` |
| Read-only cold-start preflight | `bridge preflight` |
| Read-only diagnostics | `bridge status`, `bridge doctor`, `bridge logs`; add `-Json` to status/doctor for the schema-v1 object |
| Static validation | `bridge validate` or plain `bridge test`; add `-Json` to validate for the schema-v1 object |
| Full source release audit | `scripts\audit-feishu-codex-release.ps1 -DesktopRoot <path> -HarnessRoot <path>` |
| One-shot external P0-B then P3 | `scripts\invoke-external-p0b-p3-once.ps1 -PythonExecutable <python.exe> -Iterations 25 -TimeoutSeconds 300 -ExternalSuiteAcknowledged`（只在 Desktop 外、Listener 已停止时运行；P0-B 双门禁通过后才把精确 evidence path/SHA 交给 P3；成功只输出一个汇总 JSON，失败保留并显示原始子阶段诊断） |
| One-shot external P0-B | `scripts\invoke-external-p0b-once.ps1 -PythonExecutable <python.exe> -ExternalTestRunnerAcknowledged`（只在 Desktop 外运行；自动创建唯一目录，supervisor 成功后才运行 semantic validator；成功 envelope 追加下一步所需的 `evidence_path`） |
| External P0-B supervisor | `scripts\run-external-p0b.ps1`（Desktop 外，以 clean `pwsh -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File` 启动；只用普通本地 drive 路径，参数与审批见 release audit reference） |
| External evidence acceptance | `scripts\validate-external-p0b-evidence.ps1`（同样由 clean `pwsh -File` 启动并重算物理路径隔离；JSON Schema 只检查形状，本脚本重算语义关系） |
| One-shot external P3 soak | `scripts\invoke-external-p3-soak-once.ps1 -PythonExecutable <same-python.exe> -P0EvidencePath <p0b.json> -ExpectedP0EvidenceSha256 <sha256> -Iterations 25 -TimeoutSeconds 300 -ExternalSoakAcknowledged`（只在 Desktop 外；Listener 停止；成功时依次输出 supervisor 与独立 validator 两个 JSON） |
| External P3 supervisor/validator | `scripts\run-external-p3-soak.ps1` 与 `scripts\validate-external-p3-soak-evidence.ps1`（复用同版 P0-B retained snapshot；固定十场景、禁止子进程与 Desktop/Feishu live contact） |
| External P1 migration lab | `scripts\external-p1-migration-lab.ps1`（Desktop 外；`prepare`、只读 `observe`、可恢复 `rollback`；`bridge hooks` 与 `bridge upgrade` 保持两个独立审批动作） |
| External dynamic tests only | `bridge test -RunTests -ExternalTestRunnerAcknowledged` with `FEISHU_BRIDGE_EXTERNAL_TEST_RUNNER=1` |
| Access policy | `bridge access -AccessMode locked ...` |
| Project creation | `bridge projects -ProjectCreate on\|off [-ProjectsRoot <existing-parent>]` |
| One-ticket manual diagnostic | controller-only installed `router_queue.py ... manual-authorize --router-thread-id <exact> --host-id <exact> --expected-operation <exact> --ttl-seconds 300`; fresh owner approval every time, never Gateway allowlisted |
| Reversible temporary binding | `scripts\temporary_binding_transaction.py --runtime-dir <exact-runtime> --scope-hash <12-hex> bind ...` then, after the test and a separate approved stop, `... rollback --transaction-id <exact>`; never edit `sessions.json` directly |

Key defaults: Router result timeout 3600 seconds, active-work lease heartbeat TTL 90,
mutating claim TTL 7200, read-only `inspect_thread` claim TTL 300, wake lease
TTL 180, scheduler freshness TTL 300, grace cap 30,
response-cache retention 168 hours, durable terminal idempotency receipts, two
concurrent cross-scope listener workers, and project creation off. The bridge intentionally
has no model, sandbox, network, context-window, Desktop-refresh, or Obsidian/RAG
setting.

The public `bridge upgrade` command changes runtime code only; hooks, runtime
configuration, project rules, and restart are separately named actions. When an
older installed start hook predates the manifest schema, first stop the Listener
and use `bridge hooks`. That atomic action backs up and replaces both Bridge hook
scripts plus their `hooks.json` entries, invalidates the old manifest without
signing a new runtime, and leaves start fail-closed until a separately approved
`bridge upgrade` writes the matching manifest. `bridge init` backs up an existing
`AGENTS.md` under `.codex/feishu-bridge/backups/` before atomically replacing
only the marked managed block.

`bridge access` is narrower than install: it updates only the access mode and
explicitly supplied owner/admin/user/chat allowlist values in `bridge.env`.
It never installs code, refreshes hooks, or merges project rules, and its later
restart remains a separate approval.

## Validation and migration

- Before continuing development on another computer, read
  [HANDOFF.md](HANDOFF.md). It records the exact source/runtime split, known
  compatibility gaps, security exclusions, and clean-room bootstrap order.
- Read [references/release-audit.md](references/release-audit.md) before
  changing the release inventory, fault/race tests, or accepting external
  P0-B evidence. Counts are derived from the inventory, never authoritative.
  The exact independent `plugins/human-authorization-relay` tree and exact root
  `.tmp` tree are coexistence boundaries and receive no Bridge certification.
  The repo-local `plugins/feishu-codex-final-return` source and
  `.agents/plugins/marketplace.json` are explicitly inventoried P0 release
  files; never exclude the whole `plugins` or `.agents` tree.
- Never accept a P0-B receipt from its envelope or JSON Schema alone. Require
  the independent semantic validator to pin and rehash the retained snapshot,
  structured `unittest.TestResult`, captures, current audited source, lifecycle
  hooks, interpreter, and bounded runtime/control state. Its pass is current-
  environment evidence, not a signature or cryptographic attestation.
- Read [references/p3-bounded-soak.md](references/p3-bounded-soak.md) before a
  P3 run. Require a fresh independently validated P0-B receipt for the exact
  source, reuse only its retained snapshot, keep the Listener stopped, and run
  the ten fixed scenarios with explicit iteration and timeout caps. Accept only
  the independent P3 validator; its pass proves neither live delivery nor a
  Desktop build's final-return compatibility. Before accepting the P0-B gate,
  require its extracted-helper regression to pass both an ordinary file and an
  ordinary directory through both P3 path-chain implementations, and require
  its isolated child-guard probe to preserve `Popen` as a subclassable class,
  import Python 3.13 `asyncio`, and reject construction before process creation.
  Also require the extracted validator pin helper to accept a new empty
  `List[FileStream]`, retain a zero-byte first file, and dispose that handle.
  Require the extracted timestamp helper to preserve seven-digit fractional
  ticks for both a JSON-deserialized `DateTime` and the original round-trip
  string; do not widen the 0.01-second duration tolerance to hide conversion loss.
- Read [references/p1-isolated-migration.md](references/p1-isolated-migration.md)
  before an alpha.2 to alpha.4 rehearsal. Default to current-user project-level
  isolation on a disposable VM; never create a Windows account implicitly.
  Preparation, hook refresh, runtime upgrade, observation, and rollback retain
  separate approval boundaries, and no stage may start the Listener.
- Treat source `4.2.0-alpha.30` as the current breaking control-plane, Feishu
  command-UX, machine-readable diagnostics, exact-turn Hook final return,
  strict registered-Gateway delegation matching, bounded native final-
  materialization grace, and bounded P3 soak contract.
  Do not run it against the old separate Sentinel/Router automation without an
  explicitly approved migration.
- Make `bridge validate` require the Gateway assets and reject the legacy
  two-task contract files or unsafe App Server/deep-link transports.
- After every source or contract edit, run AST parsing and `bridge validate`
  before asking the owner to run external P0-B. Keep the contract markers used
  by `bridge validate` aligned with the external prompt-contract tests so a
  wording drift fails in the fast Desktop-safe gate instead of wasting an
  external P0-B run.
- After that source's P0-B supervisor and semantic validator pass, P3 may be
  run from the external one-shot wrapper without another source rewrite. Stop
  after any nonzero stage and inspect only the retained path; never continue
  envelope parsing with empty values. When both gates are planned consecutively,
  prefer the maintained combined wrapper; do not merge native stderr into the
  captured JSON stream or replace the detailed child failure with a bare exit code.
- Keep plain `bridge test` static. Never launch the dynamic suite in Desktop.
- For a live migration, classify the exact Desktop build first. Separately
  approve: pause old automation; choose/create and mount/register one Gateway;
  create or retarget a scheduler while paused; and, only on a surface without a
  terminal incompatibility marker, activate one finite canary while the owner
  completes `/init`, selects and confirms the predeclared exact target, and sends
  one ordinary test message. A current build carrying only the native final-
  readback marker may instead run one exact Hook-transport canary through the
  one-ticket manual lane after all P0 deployment/trust gates; this never clears
  the marker or authorizes scheduler activation. Require exact-scope binding and
  the target final, then leave it paused/completed. Production requires two later
  approvals: change/read back the paused recurrence, then activate it.
- Do not claim production readiness from listener health alone.
