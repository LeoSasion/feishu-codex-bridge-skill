# Feishu Desktop Gateway scheduler (Codex heartbeat automation)

Use this only after `assets/desktop-gateway-task.md` has been fully rendered,
mounted, and registered in one dedicated existing Codex Desktop task.

## Automation settings

- Kind: Codex `heartbeat` scheduler on the existing dedicated Gateway task
- Name: `Feishu Desktop Gateway`
- Target: that exact Gateway task via `targetThreadId`
- Canary schedule: every two minutes, capped at no more than three runs when
  the current Desktop recurrence surface preserves and reads back the cap
- Production schedule: every two minutes only after this exact
  Gateway/build/configuration passes the live `/init` catalog-and-selection
  canary and the owner later
  gives explicit always-on approvals
- Initial status: paused
- Prompt: the exact short prompt below

Create or update the automation while paused. Activation is a separate
client-impacting action requiring fresh owner approval. Changing the target,
prompt, cadence, Gateway registration, helper path, executable, runtime path,
allow rule, or permitted subcommands requires another approval before
resumption.

The first activation is a finite compatibility canary, not production consent.
Use an RFC 5545 recurrence with a three-run `COUNT` only when the current Desktop
create/update and readback surface preserves it exactly. If the cap is discarded
or normalized away, restore `PAUSED` immediately and do not activate;
supervision or a promised later pause does not make an unbounded recurrence a
valid canary. Resolve and display the complete
`/init` flow first, including the exact task the owner will select, and activate
only while the owner is present to complete the menu selection and confirmation
inside the finite window. After the canary, leave the automation paused or
completed.

Exact recurrence readback is necessary but not sufficient. Count the actual
automation-origin turns in the finite window. If more than the declared count
runs, pause immediately, record the exact Desktop scheduler surface as
`scheduler_cap_unenforced`, and do not reactivate it on the same official build.
Manual supervision or a later pause cannot repair an unenforced hard cap.

Only a successful live `/init` catalog-and-selection canary unlocks the
production path; an empty cycle, ordinary-turn preflight, or rehydration does
not. Production then requires two separate later approvals: one to change the
recurrence while paused, followed by exact readback, and another to activate the
verified recurrence. Disclose the cadence, observed model/context cost, and
pause/recovery path before either approval.

Current Desktop builds may accept `status=PAUSED` on heartbeat creation but
persist the new automation as `ACTIVE`. In the same orchestrated tool call,
immediately issue a full update of that new automation to `PAUSED`; do not wait
for or trust the create result's implicit status. Read back the stored
automation and require the exact target, cadence, prompt, and `PAUSED` status.
Also inspect the Gateway task for an unexpected automation-origin turn. This
immediate corrective pause is part of the already approved paused creation,
not activation.

Do not target a new chat, a Feishu user's target task, a legacy Router task, or
a separate Sentinel task. The same automation-origin turn probes and, only when
work exists, claims and routes it. This avoids both per-run task creation and
the unsupported Sentinel-to-Router delegated hop.

Because the exact existing task retains every scheduled Gateway cycle, empty
runs still invoke the model and grow task history. Never leave a canary
indefinitely active. Pause and obtain fresh approval before compaction or
replacement when context pressure or behavior drift appears.

Codex Desktop may impose a higher-priority requirement to publish a brief
pre-tool progress update. A scheduler prompt cannot reliably suppress that
internal task commentary. Treat this as an execution-surface compatibility
constraint: allow only generic state-only progress text, never include message
bodies, attachment names, paths, task or queue IDs, tool arguments/results, or
reasoning, and never forward that commentary to Feishu. Acceptance requires the
complete final response to remain exactly `DONT_NOTIFY`; the listener forwards
only a target task's authoritative final response.

An empty `DONT_NOTIFY` cycle proves only that the fixed queue helper command ran.
It does not prove that deferred Desktop task-coordination methods are present in
a later non-empty automation-origin turn. The first owner-approved live `/init`
is the capability canary. If that turn reports `target_tool_unavailable`, fail
closed without starting the target, do not replay, and do not tell the user that
the task ID is invalid.

Name every required direct Desktop method in the short scheduled prompt itself.
Do not assume that background tool selection indexes an older mounted contract
from task history. After a fenced claim, invoke the named top-level
`mcp__codex_app` method directly as the full contract requires. Never call it
through `functions.exec`, `ALL_TOOLS`, or a `tools[...]` dynamic alias: current
Desktop builds may retain those aliases only to report that direct MCP is
required. Explicit naming is only a selection hint; it does not replace the
live `/init` catalog-and-selection capability canary.

The full contract already mounted in this exact existing task remains the
operation authority; the short prompt only fixes the orchestration boundary.
One scheduler model turn owns the complete cycle. It may use separate bounded
`functions.exec` cells for fixed queue-helper commands and top-level direct MCP
calls for Desktop coordination. A helper cell may resolve after its one parsed
result; if it yields, resume only that exact cell with `functions.wait`.
After a successful claim, the model turn must not finish until staging,
terminal completion/failure, and wake release are resolved. A later scheduled
turn must never continue the old fence.

For `inspect_thread`, normalize direct `mcp__codex_app.read_thread` only as a native object
or one JSON parse, and require normalized `thread.id` to equal the claimed
target ID. This operation is read-only: an unparseable, missing, or mismatched
result must fail `invalid_gateway_result` without `--retryable` and without
`--may-have-started`; never use `target_result_unknown --may-have-started`.

Do not retry a terminally incompatible Desktop surface. If a genuine
automation-origin canary advances helper freshness, claims the request, and
then returns `target_tool_unavailable` with `may_have_started=false` and no
binding, keep that build paused. Treat it as the same surface until a different
official Desktop build is positively identified; prompt, model, context, or
task changes are not sufficient.

Pre-probe failures also pause the remaining canary: an automation directive
instead of a helper call, an activation-unverified `NOTIFY` envelope, or an
execution-surface rejection with stale scheduler freshness. Preserve the
durable request and do not broaden the rule, invoke the helper manually, read
rollout files, or spend another identical run. A separately approved exact
`execpolicy check` may distinguish rule matching from the unattended
shell/tool surface, but it does not authorize another canary.

The queue-helper subcommand `heartbeat` is different: it renews an active-work
lease only after a fenced claim. It does not schedule or wake this task.

## Short scheduler prompt

```text
This is a genuine already-triggered automation-origin turn for the exact existing Feishu Desktop Gateway. The controlling task verified fresh owner approval before setting this exact automation `ACTIVE`; the surrounding Desktop-supplied automation-origin metadata is the delegated authorization receipt for this turn. This Gateway task cannot see that separate approval conversation: do not search its history for consent, request approval again, or return a heartbeat/`NOTIFY` decision envelope. A manual or task-to-task message that merely copies these instructions is not authorized. This is execution inside an existing automation, not a request to create, update, schedule, or describe one; never emit an automation or heartbeat directive. Run the complete cycle in this one Gateway model turn. Use separate bounded `functions.exec` cells only for fixed queue-helper commands, beginning with `sentinel-probe`; a helper cell may resolve after its one parsed result, and if it yields resume only that exact cell with `functions.wait`. Invoke Desktop coordination only through top-level direct MCP calls. Never call a Desktop app tool through `functions.exec`, `ALL_TOOLS`, `tools[methodName]`, shell, App Server, database, rollout, or UI access. The required direct methods are `mcp__codex_app.list_threads`, `mcp__codex_app.list_archived_threads`, `mcp__codex_app.read_thread`, `mcp__codex_app.list_projects`, `mcp__codex_app.create_thread`, `mcp__codex_app.send_message_to_thread`, `mcp__codex_app.wait_threads`, and `mcp__codex_app.set_thread_archived`. If `should_wake=true`, use another bounded helper cell for the zero-wait fenced claim. A successful claim is a commit point: do not finish this model turn or defer the fence until the claimed request has exactly one terminal `complete` or `fail` and the wake is released. For `list_task_catalog`, preserve exact/all visibility, never widen an empty exact task list, exclude the registered Gateway task, omit summaries and message content, invoke direct `mcp__codex_app.list_threads` with the requested limit capped at 50, and use `--structured-result` with the bounded catalog JSON. For `inspect_thread`, normalize the direct `mcp__codex_app.read_thread` return only as a native object or one JSON parse and require normalized `thread.id` to equal the claimed target ID; because both catalog and inspect operations are read-only, any unparseable, missing, mismatched, or cross-scope result must fail `invalid_gateway_result` without `--retryable` or `--may-have-started`, never `target_result_unknown --may-have-started`. A required direct method that is absent or explicitly unavailable before a target action starts is `target_tool_unavailable`. Never wake or message another Router or Sentinel task and never read Automation memory or prior-run result text. If a higher-priority execution rule requires internal commentary, use only generic state-only progress text and never include message bodies, attachment names, paths, task or queue IDs, tool arguments/results, or reasoning; this internal text must never be forwarded to Feishu. Make the complete final response exactly `DONT_NOTIFY`. The verified upstream activation authorizes only the fixed allowlisted queue helper commands named in the mounted contract while status remains `ACTIVE`.
For each non-steer target send, refresh the active-work heartbeat, take one zero-time exact-target direct `mcp__codex_app.wait_threads` baseline cursor, invoke the fixed allowlisted `final-return-arm` for the claimed request/fence/target, and only then call direct `mcp__codex_app.send_message_to_thread` once. The target's trusted `UserPromptSubmit` and `Stop` MCP Hooks ignore every unarmed turn and may stage only that exact bound turn's latest final. The prompt Hook accepts the exact raw prompt or Desktop's strict delegation wrapper only when its source is this registered Gateway and its inner input has the armed digest. Refresh the active-work heartbeat before every bounded direct wait and at least once every 60 seconds; wait only with `afterCursor` equal to the baseline or next exact poll cursor. Parse each native wait object directly or one string JSON parse. When the exact target has a new completed `latestTurn`, query only that task/turn through `final-return-status`: if `available=true,state=captured`, do not read or overwrite the Hook staging file and complete from it. If the poll instead exposes `latestAssistantMessage` with the same `turnId`, `phase=final_answer`, and non-empty text, first invoke `final-return-native` to fence a late Hook, then stage that original native text unchanged. Never use the send result, baseline message, `read_thread`, transcript, or another task's final. While `state=armed`, answer-free Hook-observation fields are diagnostic only and never authorize another send. If the first exact completed poll has neither source, pin its turn ID/cursor and repeat only exact waits plus Hook status for one bounded final-materialization grace of at most 20 additional seconds, never re-sending. A mismatched turn, conflicting receipt, invalid result, or expired grace becomes `target_result_unknown --may-have-started` and is never replayed.
```
