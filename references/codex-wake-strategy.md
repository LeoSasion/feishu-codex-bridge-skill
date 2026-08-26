# Codex-native wake strategy

This reference is for changes to Gateway triggering, cadence, liveness, or
recovery. It is not an instruction to mutate a live automation.

## Decision

The Desktop Gateway task and its wake mechanisms are separate:

- **Desktop Gateway task**: the one existing Codex Desktop control task that
  owns no user work and coordinates target tasks through Desktop task tools.
- **Gateway scheduler heartbeat**: the current Codex automation of kind
  `heartbeat`, attached through `targetThreadId` to that exact Gateway task. It
  creates one automation-origin **Gateway cycle** and carries no Feishu payload.
- **Active-work lease heartbeat**: the queue-helper subcommand `heartbeat`. It
  renews an already fenced claim while target work is running; it neither
  schedules nor wakes the Gateway.
- **Listener health signal**: the Listener process health snapshot. It is
  independent of the scheduler heartbeat and active-work lease heartbeat.
- **`sentinel-probe`**: the metadata-only first phase and compatibility command,
  not a separate Sentinel task.

The Gateway replaced the old separate Sentinel-to-Router hop. It did not
replace either the scheduler or active-work lease heartbeat.

## Cost and bounded activation

Every empty scheduled cycle still invokes the model, grows task history, and
may consume quota. Therefore setup activation is always finite. Use a recurrence
capped at three runs only when Desktop preserves and reads back the cap exactly;
otherwise keep it paused and do not activate. Supervision is not a substitute
for a hard cap. Exact readback is only a precondition: the controller must count
the actual automation-origin turns in the finite window. If the observed count
exceeds the declared cap, pause immediately, record
`scheduler_cap_unenforced` for that exact official build/surface, and forbid
another activation on it even if no user target was mutated.
Predeclare the exact target for the `/init` catalog-and-selection flow and start
the finite window only while the owner is present to complete it. A successful canary never
implies indefinite production consent. Production requires separate approvals
to change the recurrence while paused and, after exact readback, to activate it.
Before either approval, disclose cadence and observed model/context cost, and
make pause/recovery explicit.

The controlling task and Gateway task do not share an approval conversation.
The controller verifies fresh activation approval; genuine Desktop
automation-origin metadata is the delegated receipt for that scheduled turn.
An activation-unverified `NOTIFY` envelope, an automation directive in place of
a helper call, or a pre-probe execution-surface rejection pauses the remaining
canary while the durable request stays untouched. Do not repair these shapes by
broadening rules, manually invoking the helper, or repeating the prompt.

An exact, separately approved `execpolicy check` may establish whether the
rendered prefix matches without executing the helper. It does not authorize a
new canary. If a genuine scheduled turn advances helper freshness and claims
work but then terminalizes `target_tool_unavailable` with
`may_have_started=false` and no binding, the tested Desktop build lacks
automation-origin task-tool eligibility. Keep it paused; a prompt, model,
context, or task change is not a new official surface.

`read_thread` may expose such automation turns with an empty `items` list.
Correlate bounded scheduler freshness, terminal delivery metadata, and exact
binding state; never inspect the queued payload to fill the observability gap.
Only a positively different official Desktop build or wake surface may start a
fresh compatibility cycle, and it must independently pass the ordinary-turn
preflight plus live `/init` catalog-and-selection canary.

Official Desktop build `26.818.8289.0` preserved and read back `COUNT=3` but
created four automation-origin turns before it was paused. That exact heartbeat
scheduler surface is therefore blocked as `scheduler_cap_unenforced`. The same
canary's `/init` catalog request also exposed a Bridge-side stale bound: the
contract requested 100 tasks while the current `list_threads` schema accepts at
most 50. Source now clamps and documents 50, but fixing that request cannot make
an unenforced scheduler cap safe or authorize another canary on this build.

### Owner-present one-ticket diagnostic lane

Waiting for another Desktop build is P2 release-compatibility work, not a reason
to block the P0 exact final-return transport. On a build blocked only by
`scheduler_cap_unenforced`, keep the scheduler paused and use the source-defined
manual lane one request at a time. Each cycle requires fresh owner approval in
the controlling task and one helper-issued ticket bound to the registered
Gateway, host, expected operation, and a maximum ten-minute lifetime. The exact
Gateway consumes it with `sentinel-probe --manual-ticket`, claims only the
matching request, makes no grace claim, releases, and stops.

Ticket issuance (`manual-authorize`) is deliberately absent from the Gateway
allow rule. A copied prompt is not authority; only successful atomic ticket
consumption is. The manual probe reads request ID and operation metadata solely
to select the authorized work, never refreshes scheduler freshness, and cannot
clear a build marker or certify automation-origin/production readiness. It is
suited to stepping through `/init`, exact binding, and one ordinary message
while the user is present.

If that same build also carries `target_final_readback_unavailable`, do not use
the lane for a third native `latestAssistantMessage` experiment. After the
same-source P0-B/P3 gates, runtime deployment, repo-local plugin enablement,
runtime registration, and exact `UserPromptSubmit`/`Stop` Hook trust pass, the
lane may run one separately approved canary of the materially different fenced
Hook return transport. This does not change either build marker, scheduler
freshness, or production eligibility; it proves only whether an exact armed
target final can return through Listener delivery to Feishu.

#### Live exact-turn Hook acceptance

Treat this finite Hook canary as passed only when one bounded observation shows
all of the following for the same expected ordinary-message operation:

1. Before ticket issuance, the Listener has durably queued that operation and
   the scheduler is still paused. The controller finishes read-only discovery
   first and asks once only for this exact ticketed cycle.
2. The ticket is consumed by the registered Gateway, exactly one target send is
   made, and that exact bound target produces one new completed turn. No prompt
   replay, grace claim, controller-side send, or second target client occurs.
3. `final-return-status` reaches the matching `captured` Hook receipt for that
   task and turn. Native `latestAssistantMessage` may still be absent; it is not
   required for this materially different transport and is not a substitute for
   the Hook receipt in a Hook-specific canary.
4. The matching queue operation becomes completed, the bounded
   `terminal_failed` count does not increase, `reply_pending` returns to zero,
   and the Listener actually delivers the expected answer to Feishu. Aggregate
   counts alone are insufficient; the owner-visible deliberately unique answer
   closes the black-box end-to-end check.

For a same-task context proof, use two sequential ordinary messages with a new
nonce that is not present in project instructions. The first asks the target to
remember it and return a fixed acknowledgement. Wait until that reply reaches
Feishu and the first operation is terminal. The second asks for only the nonce;
because it is another one-ticket operation, obtain its own fresh exact approval
and ticket, then require the exact nonce to reach Feishu. Do not issue both
messages concurrently and do not use the target UI, `read_thread`, database,
rollout, OCR, clipboard, queue payload, or staged answer as comparison evidence.
Read-only postcondition checks belong to the already approved cycle and must not
trigger another generic `同意` request.

That two-message pass proves live Feishu ingress, durable fencing, one-send
target execution, exact-turn Hook capture, Listener delivery, Unicode fidelity,
and same-bound-task context continuity only for the tested
source/runtime/Hook/build/configuration. It does not validate `/init`, clear
`scheduler_cap_unenforced` or `target_final_readback_unavailable`, certify an
automation-origin scheduler, authorize a production recurrence, or generalize
to another Desktop build. Stop identical diagnostics after the pass. If a
temporary binding was used, preserve the paused scheduler and complete the
separately approved stop/rollback/restart sequence rather than treating the
canary as a public binding workflow.

## Current Codex choice

The two-minute Gateway scheduler heartbeat remains the intended primary trigger
and recovery sweep because the supported Desktop surface available to this
Skill does not expose an authenticated external event trigger for the exact
existing Gateway task. The latest observed scheduled surface ran the helper but
split orchestration across model stages in an earlier canary. A later canary
proved the repaired single-cell cycle but exposed a second contract defect:
two read-only `inspect_thread` calls were terminalized as
`target_result_unknown` with `may_have_started=true` after the nested
`read_thread` result was not confirmed. No binding appeared. That is neither an
invalid task ID nor evidence of `target_tool_unavailable`; a read-only inspect
cannot start or mutate a target. The repaired contract now normalizes only a
native object or one JSON parse, requires exact normalized `thread.id`, fails
invalid shapes as `invalid_gateway_result` with `may_have_started=false`, and
has the helper reject any may-have-started read-only failure. Treat this as
unverified until a fresh approved finite canary passes. Every cycle still
starts from durable queue metadata and never carries a Feishu body in the
automation prompt.

This is deliberately different from embedding an agent runtime inside a
resident channel process. A Python Listener-to-target call, detached App Server,
or UI wake would create a second execution client or bypass the Desktop task
surface, so none is a fallback.

### CLI 0.149 `codex queue` assessment

Codex CLI 0.149 added `codex queue` for existing local or remote sessions
and fixed idle-session wake behavior. The published surface does not establish
that it targets an exact Codex Desktop task, preserves Desktop task-tool
eligibility, or produces an authenticated automation-origin Gateway turn.
Therefore it is not a current Bridge wake path and does not replace the
scheduler heartbeat, Gateway, or active-work lease heartbeat.

Evaluate it only in a separately approved, isolated CLI-owned experiment with
no Feishu consumer, Desktop binding, production queue, or user payload. A future
official Desktop-compatible queue surface must still satisfy every event-wake
condition below; similarity of the command name or successful delivery to a CLI
session is insufficient.

See the official [Codex changelog](https://developers.openai.com/codex/changelog)
for the 0.149 release boundary.

## Future Codex-native event wake

Adopt an event-driven primary wake only after Codex provides an official surface
that satisfies every condition below:

1. It authenticates and targets the exact existing Gateway task and host.
2. It creates a turn with the same Desktop task-tool eligibility as the proven
   automation-origin Gateway cycle.
3. It accepts only a payload-free wake indication; the durable queue remains the
   source of truth.
4. Duplicate, delayed, or failed wakes are harmless under the existing
   generation, wake lease, fence, and deterministic request IDs.
5. It does not change the target task, project, model, reasoning, approvals, or
   knowledge workflow.

If those conditions become true, the preferred Codex design is:

```text
durable queue commit
  -> payload-free official wake -> exact existing Gateway task
  -> sentinel-probe -> fenced claim -> target task
low-frequency scheduler heartbeat
  -> recovery watchdog for missed wakes
active-work lease heartbeat
  -> renews only an already claimed cycle
```

The official wake would replace the scheduler as the primary latency path, not
replace the Gateway or active-work lease heartbeat. Wake failure leaves work
durable for the watchdog. It never permits Listener-to-target delivery, payload
transport in the wake, or App Server fallback. This is a future compatibility
path, not a current capability or authorization.

Changing a live trigger, prompt, cadence, target, helper, or allow rule remains
a separately approved administrative action. Prove any future wake with the
same empty-cycle check and real `/init` catalog-and-selection capability canary
before reducing the scheduler cadence.

## What the comparisons contribute

OpenClaw shows the low-latency advantage of resident channel ingress and direct
in-process dispatch, but its runtime owns the agent execution surface. That
ownership model does not fit a bridge whose canonical executor is an existing
Codex Desktop task. See the official
[Feishu channel](https://github.com/openclaw/openclaw/blob/main/docs/channels/feishu.md)
and
[channel plugin SDK](https://github.com/openclaw/openclaw/blob/main/docs/plugins/sdk-channel-plugins.md).

Hermes Relay contributes the more applicable pattern: persist before wake, keep
the wake payload-free, acknowledge durable delivery, replay after reconnect,
and close the going-idle race. Its native Gateway still owns agent execution,
so the Bridge borrows the delivery invariants rather than the execution model.
See the official
[Relay connector contract](https://github.com/NousResearch/hermes-agent/blob/main/docs/relay-connector-contract.md)
and
[Relay guide](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/messaging/relay.md).

These projects are evidence, not a compatibility target. The final design is
chosen for Codex Desktop ownership and tool eligibility.
