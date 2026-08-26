<!-- FEISHU_CODEX_BRIDGE_RULES_START -->
## Feishu bridge runtime safety

- Treat exact Codex final-answer return to Feishu as P0. Waiting for a different
  official Codex Desktop build is P2 compatibility work only; preserve every
  current build/surface marker, but do not make native final-field availability
  a prerequisite for the separately fenced Hook return transport.
- Each selected Codex Desktop task is the sole owner of its conversation,
  project, model/reasoning settings, execution environment, approvals, tools,
  Skills, plugins, browser, Computer Use, files, knowledge access, and context.
  The bridge stores only authorization, stable scope-to-task bindings, durable
  delivery state, and bounded attachment transport metadata.
- Keep one dedicated Codex Desktop Gateway task separate from every target
  task. It combines the metadata-only Sentinel phase and routing phase in one
  automation-origin turn. It is a service desk, never an execution task.
- The Gateway may inspect, create, restore, message, wait for, and archive tasks
  only through Desktop task-coordination tools such as `list_threads`,
  `read_thread`, `create_thread`, `send_message_to_thread`, `wait_threads`, and
  `set_thread_archived`. It must never answer or retrieve for a target.
- Desktop task tools may be deferred, but they are callable only as top-level
  direct methods on the `mcp__codex_app` server. After a fenced claim, invoke
  the exact required direct method from the Gateway model turn. Never call a
  Desktop task method through `functions.exec`, `ALL_TOOLS`, or `tools[...]`;
  current builds may retain those dynamic aliases only to report that direct
  MCP is required. Only direct-method absence or explicit direct invocation
  failure permits `target_tool_unavailable`; a malformed result from an invoked
  read-only method is `invalid_gateway_result`. Shell, App Server, database,
  rollout, named-pipe, deep-link, and UI fallbacks remain prohibited.
- The Python listener must never locate or launch `codex.exe` or
  `codex app-server`. Never call `thread/resume`, `turn/start`, compaction RPCs,
  archive RPCs, or another target mutation from a detached App Server. If the
  Gateway or required task tools are unavailable, keep unclaimed work durable
  and fail closed; never become a second target client.
- Do not simulate routing with the Codex database, rollout files, writer locks,
  named pipes, deep links, or UI automation. Desktop-created tasks enter its
  task list; sidebar repaint may be asynchronous and must not be forced.
- Before any Gateway action, inspect only bounded registration, scheduler,
  Desktop build, and terminal canary metadata. Use the first-mount path only
  when no registration or scheduler target exists; use rehydration for a
  coherent existing Gateway; keep a build with a terminal incompatible canary
  paused. Unknown or conflicting state fails closed.
- Mount a new Gateway in two turns: create one dedicated candidate with model and
  reasoning overrides omitted and the read-only prompt from
  `assets/desktop-gateway-bootstrap.md`; require one bounded successful direct
  `mcp__codex_app.list_threads` call and one successful direct
  `mcp__codex_app.list_projects` call. Then send the rendered
  `assets/desktop-gateway-task.md` contract with
  `CONTRACT_TURN_MODE=INITIAL_MOUNT` and its returned task and host IDs. Use
  `REGISTRATION_ACTION=REGISTER_NEW` only when no registration exists; replacing
  an existing owner requires a separate approval and
  `REGISTRATION_ACTION=REPLACE_REGISTERED_GATEWAY`, whose exact command alone
  includes `--force`.
  The preflight must not touch the queue. The mounting turn may register only;
  it must not claim queue work. Passing the preflight does not replace the live
  automation-origin `/init` catalog-and-selection canary.
- A task-to-task mounting turn may be unable to present Desktop tool approval.
  If it returns `DONT_NOTIFY` without a registration tool call, the mount did
  not happen. Do not replay the delegated contract or run `register` from the
  controller. Require the owner to open that exact candidate and send a fresh
  direct confirmation for the already rendered registration action; then
  verify its one tool result and bounded registration metadata.
- If an explicit Gateway model change or Desktop context compaction occurs,
  keep the scheduler paused. Do not replay `INITIAL_MOUNT`. Verify the bounded
  registration metadata still names that exact existing task and host, then
  obtain fresh approval and resend the complete rendered contract with
  `CONTRACT_TURN_MODE=REHYDRATE_EXISTING` and
  `REGISTRATION_ACTION=NO_REGISTRATION`. That turn must invoke no tool or
  command, must not repeat `register`, probe, claim, or contact a target, and
  must finish exactly `DONT_NOTIFY`. Rehydration does not authorize later
  activation or certify the scheduled tool surface. Before a live canary,
  obtain another approval and use `assets/desktop-gateway-model-preflight.md`
  to repeat the bounded direct `mcp__codex_app.list_threads` plus
  `mcp__codex_app.list_projects` ordinary-turn check under the selected model.
- If that exact preflight completes but controller `read_thread` returns an
  empty `items` array while the owner sees its exact JSON final in the opened
  Gateway, do not resend or assume eventual consistency. Classify it as
  `visible_preflight_pass_readback_unavailable`. The owner's exact
  transcription may satisfy only the ordinary-turn prerequisite for one
  separately approved finite live canary on a positively identified newer
  official build when: the build gate passed; registration and paused
  scheduler still name the exact Gateway and host; the owner personally sent
  the exact asset prompt; the JSON has exactly the required keys and all four
  boolean checks are true; the controller identifies
  the exact completed-but-empty turn; and a bounded control read of the
  predeclared non-Gateway target returns its exact ID with a stored user item
  and final agent item. This is not stored-final evidence, does not certify the
  automation-origin surface, and never authorizes activation or production.
  Keep the scheduler paused until current P0-B evidence and a fresh finite-
  canary approval exist; the canary must still prove `/init` selection, exact
  binding, ordinary delivery, and the target final return.
- Attach one paused heartbeat automation to that exact existing Gateway task
  through `targetThreadId`, using the short prompt in
  `assets/desktop-gateway-heartbeat.md` and a two-minute default cadence. Never
  target a new chat, a user target task, a legacy Router, or a separate Sentinel.
- Do not use `send_message_to_thread` to wake another control task. Each
  automation-origin Gateway turn runs one metadata-only `sentinel-probe`; when
  work exists, that same turn reserves the returned wake credentials, performs
  the zero-wait claim, calls the target, waits, and finalizes the queue request.
- The empty-queue probe may inspect only pending/claimed counts, wake
  generation, registration, and lease metadata. It never reads a Feishu body,
  attachment, binding, log, staging file, or knowledge source.
- Keep every heartbeat final exactly `DONT_NOTIFY`. The listener alone returns
  the target task's authoritative final answer to Feishu. Never expose Gateway
  reasoning, commentary, tool calls, local paths, queue IDs, or route metadata.
  If a higher-priority Desktop rule requires internal tool-progress commentary,
  keep it generic and state-only with none of those details; it must remain in
  the dedicated Gateway task and must never be forwarded to Feishu.
- Use owner-locked Gateway registration, deterministic operation/event
  idempotency, active-work heartbeat, and fenced wake leases. Reject stale wake
  IDs and fencing tokens at claim, stage, completion, failure, heartbeat, and
  release. Unknown target outcomes use `may_have_started=true` and are never
  automatically replayed.
- `inspect_thread` is read-only. Normalize direct `mcp__codex_app.read_thread` only as a
  native object or one JSON parse and require normalized `thread.id` to equal
  the claimed target. An absent, malformed, or mismatched result is
  non-retryable `invalid_gateway_result` with `may_have_started=false`; never
  use `target_result_unknown --may-have-started` for this operation. The helper
  must reject a may-have-started failure for any read-only operation.
- An unclaimed listener timeout leaves both the durable queue request and the
  Feishu event retryable under the same idempotency key. Control commands must
  not convert that state into a delivered terminal failure. The `/init` wizard's
  owner/admin new-project action may
  resume its exact staged child directory only for that same event key; a new
  event must never adopt it or overwrite its pending marker.
- A terminal failure may advance to the next deterministic request generation
  only when it explicitly has `retryable=true` and
  `may_have_started=false`. Each generation keeps one first terminal outcome;
  retention may only redact expired answer text to non-retryable unknown.
  Target lifecycle and unknown outcomes never advance. An expired
  `inspect_thread` and `list_task_catalog` claims are the read-only exceptions:
  terminalize either as
  retryable with `may_have_started=false` after its bounded five-minute
  abandonment TTL; every other expired claim retains the general long TTL and
  remains non-retryable and uncertain.
- Ordinary retention may delete only terminal claims; it must never erase a
  nonterminal claimed request before the claim/lease protocol resolves it.
- Start a non-empty cycle with a zero-wait fenced claim. After real work, allow
  one bounded 20-second grace claim to absorb bursts. Process at most eight
  requests, then release. Never keep an indefinite watcher.
- Run the whole scheduled cycle in one Gateway model turn. Use separate bounded
  `functions.exec` cells only for fixed queue-helper commands, and invoke
  Desktop coordination through top-level direct `mcp__codex_app` calls between
  them. If a helper cell yields, resume only that exact cell with
  `functions.wait`. A successful claim is a commit point: never finish the model
  turn or leave its fence for a later scheduled turn before a terminal
  completion/failure and release. Refresh the active-work heartbeat before each
  bounded direct wait and at least once every 60 seconds.
- Treat an archived or missing bound target as a normal lifecycle event. Only
  after the Gateway returns `target_archived` or `target_not_found` with
  `may_have_started=false` may the listener create exactly one fresh Desktop
  task in that exact Feishu scope's current project, atomically replace the
  binding, and deliver the original message with a target-specific key. Never
  replay an uncertain result, match by title, or create repeatedly.
- For create, restore, and compact operations, archive only explicitly supplied
  displaced task IDs after the primary action succeeds. Return only IDs whose
  `set_thread_archived(archived=true)` calls explicitly succeeded. Never infer
  success from the request list, archive the active target, or claim an omitted
  ID was archived.
- A terminal live-canary result of `target_tool_unavailable` after a genuine
  helper claim, with `may_have_started=false` and no binding, marks that Desktop
  build as lacking automation-origin task-tool eligibility. Keep it paused and
  do not retry another prompt, model, or identical scheduler configuration.
  Treat it as the same build until a different official Desktop build/surface
  is positively identified; a model, prompt, context, or task change is not
  sufficient.
  `read_thread` may show these automation turns with empty `items`; correlate
  bounded scheduler freshness, terminal delivery metadata, and exact binding
  state instead of opening payloads or assuming no helper ran. A newer official
  Desktop surface must pass a fresh live canary. SDK/App Server remains a
  separate backend design, never a fallback for Desktop-owned threads.
- A finite canary must also verify its actual automation-origin turn count, not
  only the stored recurrence text. If the observed count exceeds the declared
  hard cap, pause immediately, record `scheduler_cap_unenforced` for that exact
  official Desktop build/surface, and forbid reactivation on it. Manual
  supervision or a later pause is not a hard cap. Every Desktop
  `list_threads` invocation must use an explicit limit no greater than 50.
- A scheduler blocked only by `scheduler_cap_unenforced`, or otherwise paused
  without a terminal task-tool marker, may use the source-defined one-ticket
  manual diagnostic lane without changing the build verdict. Never use it to
  bypass `target_tool_unavailable`. Each cycle needs
  a fresh owner approval in the controlling task for one expected operation.
  The controller alone runs the unallowlisted `manual-authorize`; the exact
  Gateway then consumes that task/host-bound, expiring ticket through
  `sentinel-probe --manual-ticket`, claims only the matching request, performs
  no grace claim, releases, and stops. The manual probe must not refresh
  scheduler freshness or certify automation compatibility or production.
- If two separately approved owner-observed `send_message_to_thread`
  diagnostics on the same exact official Desktop build and surface both show
  an intact prompt in the same target task, a completed target turn, no exposed
  `latestAssistantMessage`, and empty `read_thread.items`, record
  `target_final_readback_unavailable` for that build/surface. This proves
  ingress and same-task context only, not final-return compatibility. Block
  further manual `send_message_to_thread` diagnostics on that same
  build/surface; a model, prompt, Gateway task, target task, context reset, or
  delay is not a retest condition. Only a positively different official
  Desktop build/surface may repeat the native `latestAssistantMessage` canary.
  This build marker does not prohibit source development or one separately
  approved finite canary of the materially different exact-turn Hook return
  transport after its current P0-B/P3 gates, runtime deployment, plugin
  installation/enablement, runtime registration, and exact Hook trust all pass.
  That Hook canary leaves the native build verdict and paused scheduler intact.
  Never use UI, database, rollout, App Server, transcript, OCR, or clipboard
  extraction as a reply fallback.
- Every non-steer target send must first take one zero-time `wait_threads`
  snapshot for only the exact target and retain only its cursor, then invoke the
  fixed `final-return-arm` helper for the claimed request/fence/target before
  sending once. The enabled `feishu-codex-final-return` plugin's structured
  `UserPromptSubmit` Hook may bind only a matching task and turn plus either the
  exact raw prompt hash or a strict Desktop delegation wrapper whose source is
  the Gateway pinned at arm time and whose inner input has that exact hash;
  its `Stop` Hook may stage only that bound turn's latest non-empty final. Every
  unarmed or mismatched event is ignored. A later Stop continuation for that
  same task/turn replaces its provisional captured answer before completion.
- After the one send, wait with `afterCursor` equal to the baseline or next
  exact poll cursor. When the exact target's new `latestTurn` completes, query
  only that task/turn through `final-return-status`. If the Hook receipt is
  captured, complete from its fenced staging without reading or rewriting it in
  the Gateway. If the native wait result instead contains a non-empty
  `latestAssistantMessage` whose `turnId` matches that turn and whose phase is
  `final_answer`, first invoke `final-return-native` to fence any late Hook,
  then stage that original text unchanged. Never take a final from the
  submission result, baseline message, `read_thread`, another task, UI,
  database, transcript, rollout, OCR, or clipboard. If the first exact
  completed poll lacks both exact sources, pin its turn ID and cursor and
  continue exact-target waits plus Hook status checks for one bounded
  final-materialization grace of at most 20 additional seconds without
  re-sending. A different turn, conflicting receipt, invalid result, or expired
  grace is `target_result_unknown` with `may_have_started=true` and must not
  replay.
- The current Desktop task-send tool is text-only. Preserve the user's original
  text as the prompt prefix and append only a bounded, validated, read-only
  attachment manifest when required. Do not claim native typed-media delivery.
- Every native helper stdout crossing Python, PowerShell, and Desktop tools must
  be one ASCII-only JSON wire object. Carry non-ASCII prompt text as standard
  JSON escapes, parse exactly once inside the Gateway cell, and forward the
  resulting Unicode string unchanged. Never forward raw escape text or apply a
  second code-page conversion. External P0-B must include Chinese punctuation
  and emoji roundtrip coverage for this exact helper boundary.
- Never inject Feishu envelopes, route decisions, queue IDs, session state,
  Obsidian/RAG excerpts, generated summaries, logs, or reconstructed history.
  The target's project and existing knowledge workflow remain authoritative;
  the bridge has no model, sandbox, network, context-window, or knowledge-root
  override.
- The `/init` wizard's compact action sends `/compact` to the target through
  task-to-task communication. The Gateway never authors, stores, or injects a
  replacement summary. `/init` is the only supported Feishu slash command;
  every other slash input is rejected generically, never executed or forwarded.
- Bind by stable Feishu direct-chat, group-chat, and topic IDs, never titles.
  Same-name users or groups remain separate, and one target may be actively
  bound to only one Feishu scope.
- `/init` is the only Feishu slash command. It opens a bounded conversational
  wizard whose immutable snapshot lists projects, task titles, and exact task
  IDs. Owner/admin may list all Desktop tasks; other authorized scopes may see
  only exact-scope related tasks plus a new-task option. Archived tasks are a
  separate explicit view. Display project label, task title, and full ID but
  never paths, summaries, messages, or prompts. Keep the full snapshot in memory
  only; `sessions.json` may persist its numeric expiry marker but never titles,
  labels, or local project roots. Page-local numbers resolve only against that
  snapshot. Every mutation requires confirmation.
- Every slash input except `/init` is unsupported. Reject it generically before
  routing; do not keep per-command aliases or forward it to a target task.
- Current Desktop task creation requires a non-empty prompt. Use only the
  documented minimal routing-ready bootstrap to materialize a visible wizard-
  created task, then send the first real Feishu request as the next turn.
- New-project creation in `/init` is disabled by default and limited to a locked owner/admin and
  one portable child-folder name. The Gateway must exact-match the directory in
  Desktop `list_projects`; otherwise fail `project_not_registered`, remove only
  the just-created empty folder, and never use a fallback project.
- Treat every client-impacting action as a fresh approval checkpoint. Name the
  exact action, target process/task/file, interruption risk, and recovery path.
  Prior broad or oral consent never authorizes the next administrative action.
- Compress approval UX without widening authority. Before asking, finish
  relevant read-only discovery, exact path/identity resolution, source-only
  preparation, and risk analysis. One approval for one exact action includes
  deterministic command rendering, bounded waits, progress updates, and
  read-only postcondition checks; do not ask again merely to inspect status,
  hashes, manifests, queue counts, or the exact transaction result. If the
  disclosed executable/helper never ran, retry only shell quoting or transport
  syntax for that unchanged action without another prompt. A different
  executable, file, target, scope, subcommand, risk, or recovery path requires
  a new checkpoint. Group source edits, documentation sync, AST/static
  validation, and external-command preparation without generic `continue`
  prompts. Preview later mandatory checkpoints compactly, but each approval
  response authorizes only its one named current action; earlier `同意` messages
  are never standing consent.
- Administrative actions include bridge install, upgrade, start, stop, restart,
  hook/config changes, installing or enabling the final-return plugin,
  registering or unregistering its runtime, trusting its exact
  `UserPromptSubmit` or `Stop` Hook, any `codex.exe` or App Server invocation,
  changing the allow rule, creating/replacing/mounting the Gateway task,
  creating/retargeting its heartbeat automation, activating/resuming it after a
  material change, and stopping any Codex or Python process. Do not group
  multiple actions under one approval; upgrade and restart are always separate.
- The first `bridge install` is one explicitly disclosed bootstrap action:
  Listener runtime, both lifecycle hooks, initial `bridge.env`, the integrity
  manifest, and Bridge-only `hooks.json` registration. It never merges project
  rules. After installation, public `bridge upgrade` is runtime-only;
  `bridge hooks`, runtime config, `bridge init`, hook trust review, and restart
  are separately named and approved actions. A pre-manifest hook migration runs
  only while the Listener is stopped: `bridge hooks` invalidates the old
  manifest, then a separately approved upgrade signs the matching runtime.
- Fresh installs write locked and a missing access key remains locked; malformed
  or empty recognized boolean, enum, or integer values refuse startup. Locked
  access denies every Feishu event until an identity is configured. Before a live canary or
  production, separately approve `bridge access -AccessMode locked` with at
  least one validated `ou_...` or `oc_...` ID. `compat` is explicit legacy
  migration behavior, never a production default.
- Manual start and restart require current source/runtime parity, and every
  SessionStart validates the installed version/file/hook manifest before
  creating a lease. Missing or mismatched integrity state fails closed; it does
  not authorize an install, upgrade, hook refresh, or restart.
- Treat `bridge.pid` as an untrusted reference, not process identity. Status,
  start, stop, install, upgrade, and external-test guards must verify a Python
  process whose command line contains the exact installed `bridge.py` path.
  Never stop a reused foreign PID; an unverifiable Python identity fails closed
  without stopping it or starting a second Listener.
- After the owner explicitly mounts and starts the bridge, an allowlisted
  Feishu `/init` wizard action or ordinary message authorizes only that single
  data-plane queue operation; it does not require another Desktop approval.
- A temporary binding is permitted only as an owner-requested diagnostic that
  isolates ordinary delivery from a frozen `/init` control-plane defect. First
  exact-match the Desktop task, host, project, and currently unbound Feishu
  scope with read-only tools. Stop the verified Listener under a fresh approval
  and use only the maintained `scripts/temporary_binding_transaction.py`; never
  edit `sessions.json` directly. The helper must hold the maintenance lock,
  persist an integrity-pinned unbound baseline and transaction marker, and
  neither contact Codex nor touch message or queue payloads. Binding, restart,
  the later stop, exact rollback, and final restart are separate approvals.
  Rollback only if the transaction ID, target task, active route, and every
  retained project route still match; otherwise fail closed without a broad
  unbind. This diagnostic is not a second public binding workflow.
- Fresh owner approval to activate or resume the exact existing Gateway
  heartbeat authorizes subsequent scheduled `sentinel-probe`, `claim`,
  `release`, `heartbeat`, `final-return-arm`, `final-return-status`,
  `final-return-native`, `stage-path`, `complete`, and `fail` commands through
  the already-loaded fixed project-local allow rule while status remains
  `ACTIVE`. It does not authorize `final-return-hook`, plugin runtime
  registration, another path, script, process, fallback, or control task. Any
  change to target task, prompt, cadence, registered ID, helper, executable,
  runtime path, rule, or subcommand set requires new approval before resumption.
- That fresh approval is verified in the controlling task before activation;
  the dedicated Gateway does not share that approval conversation. In a
  genuine turn from the exact active automation, Desktop-supplied
  automation-origin metadata is the delegated authorization receipt for the
  allowlisted commands above. Do not search Gateway history for the owner's
  consent, ask again, or return a heartbeat/`NOTIFY` decision envelope. A
  manual or task-to-task copy of the scheduler prompt is not authorized. The
  sole manual exception is an exact rendering of
  `assets/desktop-gateway-manual-cycle.md`; its message is not authority, and
  work may begin only after the fixed helper atomically consumes its one-time
  ticket. The Gateway must never invoke `manual-authorize`. If a
  genuine scheduled turn reports activation unverified before
  `sentinel-probe`, pause it, keep work durable, and repair the handoff under a
  new approval.
- If an execution surface cannot present an approval prompt, do not perform an
  administrative action. Explain the risk and wait. Source-only edits and
  read-only diagnostics that do not spawn Codex or alter live runtime may
  proceed normally.
- If a genuine scheduled turn reports that the execution surface rejected
  `sentinel-probe`, pause it and require evidence that no helper call occurred,
  scheduler freshness stayed stale, the request stayed pending, and no binding
  appeared. Do not broaden the rule, invoke the helper manually, or inspect
  rollout files. `codex execpolicy check` may test the exact rendered rule and
  argv without executing the helper, but because it invokes `codex.exe` it
  requires its own fresh approval. A passing rule shifts diagnosis to the
  unattended shell/tool surface; it does not authorize another canary.
- On Windows, do not treat a Desktop package resource under
  `Program Files\WindowsApps\OpenAI.Codex_*\app\resources` as an independent
  Codex CLI. Preflight may inspect an installed `@openai/codex` shim and package
  metadata but must not start Codex. If the independent CLI is missing, install
  the official npm package only after the disclosed dependency-install
  approval. Never change WindowsApps ACLs, copy the packaged binary, or automate
  a terminal through Computer Use. Every `/hooks` or `execpolicy check`
  invocation remains a separate administrative approval.
- Treat `bridge validate`, `bridge status`, and `bridge doctor` as default
  diagnostics. They must not start, stop, upgrade, or restart a live bridge.
- Their optional `-Json` mode must emit exactly one compact object with
  `schema_version=1`, a stable command name, and a stable status. Preserve the
  human-readable default, separate the installed manifest version from a stale
  health-snapshot version, and never expose messages, prompts, answers,
  credentials, access-list values, Feishu/Codex task IDs, or local paths.
- Never run bridge dynamic tests from inside Codex Desktop. This includes
  `bridge test -RunTests`, direct `unittest`/`pytest`, or tests that spawn a fake
  App Server. Use an external terminal or CI while the live bridge is stopped;
  in Desktop, prepare commands and inspect supplied results only.
- P0-B must use the audited clean-PowerShell external supervisor and its
  structured `unittest.TestResult` driver. Never accept an evidence envelope or
  JSON Schema result alone; require the independent semantic validator to pin
  and rehash retained artifacts and recompute cross-field relations. Its pass is
  current-environment evidence, not a signature or cryptographic attestation.
- P3 is a stopped, external, bounded soak only after a fresh P0-B receipt for
  the exact same source passes its independent semantic validator. Run the fixed
  ten-scenario contract from P0-B's retained source snapshot, pin every audited
  Desktop snapshot file read-only for the run, and use an explicit iteration
  cap and hard timeout. Forbid child processes and all live Desktop,
  Gateway, scheduler, Listener, and Feishu contact. Accept only a create-new
  receipt plus the independent P3 semantic validator; a passing soak is not a
  live-delivery or Desktop-build compatibility result and authorizes no start,
  activation, production scheduling, or repeat of a terminally blocked canary.
- SessionStart/SessionEnd lifecycle hooks manage only the listener lease. They
  do not create, stop, resume, probe, claim, or replace the Gateway or any
  target. The separate `feishu-codex-final-return` plugin's
  `UserPromptSubmit`/`Stop` MCP Hooks may only invoke its hidden local tool,
  verify the registered installed runtime, and bind/capture an already armed
  exact turn; they never route, submit, read transcripts, or expose answer text
  on helper stdout. Use the current Codex CLI `/hooks` browser or an equivalent
  visible startup review to inspect exact Hook hashes; do not type `/hooks` into
  a task surface that does not expose that command. Run trust-only review with
  `CODEX_BRIDGE_CHILD=1`, trust the exact Bridge events individually, and never
  use `Trust all`. On Windows, prefer a clean `cmd.exe` launch of the independent
  CLI to avoid unrelated PowerShell/PSReadLine publisher prompts; startup may
  open the global hook review automatically, and unrelated pending rows may
  remain untrusted.
- Do not install or connect Obsidian during normal Feishu setup. Configure the
  target project's own knowledge workflow only after an explicit knowledge-base
  request; the bridge exposes no Obsidian/RAG path.
<!-- FEISHU_CODEX_BRIDGE_RULES_END -->
