# One-ticket Desktop Gateway diagnostic cycle

`MANUAL_DIAGNOSTIC_CYCLE_V1`

This is one owner-present diagnostic turn sent to the already registered
Desktop Gateway task while its scheduler remains paused. The message itself is
not authority. The only delegated authorization receipt is successful atomic
consumption of the helper-issued ticket below by the fixed installed runtime.

- Gateway task: `{{GATEWAY_THREAD_ID}}`
- Host: `{{HOST_ID}}`
- Expected operation: `{{EXPECTED_OPERATION}}`
- One-time ticket: `{{MANUAL_TICKET}}`
- Source contract: `{{BRIDGE_VERSION}}`

Start with one bounded `functions.exec` cell whose only external action is the
following fixed helper command; do not call ordinary `sentinel-probe`,
`status`, `manual-authorize`, another executable, or another runtime. The cell
may resolve after returning the parsed probe result:

```powershell
& '{{PYTHON}}' '{{RUNTIME_DIR}}\router_queue.py' --runtime-dir '{{RUNTIME_DIR}}' sentinel-probe --router-thread-id '{{GATEWAY_THREAD_ID}}' --host-id '{{HOST_ID}}' --manual-ticket '{{MANUAL_TICKET}}'
```

The helper must report `manual_ticket_consumed=true`. If it rejects the ticket,
reports `should_wake=false`, names another Gateway/host, or returns an expected
operation other than `{{EXPECTED_OPERATION}}`, invoke no Desktop task tool and
finish exactly `DONT_NOTIFY`.

Every helper stdout value in this turn is one ASCII-only JSON wire object.
Parse it exactly once. The parsed strings are authoritative Unicode values;
never pass raw `\uXXXX` wire text to a Desktop task and never apply a second
encode/decode step.

When it reports `should_wake=true`, keep the returned wake ID and fence only in
this Gateway model turn and make exactly one zero-wait claim with another
bounded helper cell using the source-exact operation contract embedded below.
The claim must name operation `{{EXPECTED_OPERATION}}`; otherwise use the
fenced failure path with `manual_operation_mismatch`, `retryable=false`, and
`may_have_started=false`.

A successful claim is a commit point for this model turn. Do not emit a final
response or defer the fence to a later task turn. Invoke Desktop coordination
only as top-level direct `mcp__codex_app` tool calls. Never invoke a Desktop task method from `functions.exec`,
`ALL_TOOLS`, `tools[...]`, shell, App Server,
database, rollout, or UI access. Use separate short `functions.exec` cells only
for the fixed queue-helper commands. If one of those cells yields, resume that
same cell with `functions.wait`; otherwise let it finish and continue the same
model turn. Refresh the active-work heartbeat before every bounded direct
`mcp__codex_app.wait_threads` call and at least once every 60 seconds.

Process that one request only through the embedded operation-specific direct
MCP contract, including bounded waits, active-work heartbeats, staging, and
exactly one terminal `complete` or `fail`. Then explicitly release the wake
with reason `manual_single_request`. A top-level Desktop method that is absent
or explicitly unavailable before a target action starts is
`target_tool_unavailable`; a malformed result from an invoked read-only method
is `invalid_gateway_result`.

## Source-exact operation contract

The controlling task rendered this section from the audited
`assets/desktop-gateway-task.md` operation and completion sections in the same
source version as this template. It is self-contained because a background
Gateway turn may not retain an earlier rehydration prompt. If the placeholder
below is unresolved, names another operation, or lacks `## Complete or fail`,
invoke no Desktop task tool, fail the claimed request as
`invalid_gateway_result` without `--retryable` or `--may-have-started`, release
the wake, and finish exactly `DONT_NOTIFY`. Do not fall back to remembered or
model-authored operation logic.

{{OPERATION_CONTRACT}}

## End source-exact operation contract

Do not make the 20-second grace claim and do not claim a second request. Do not
create, update, activate, resume, or inspect an automation. This turn does not
refresh scheduler freshness, certify automation-origin compatibility, or
authorize production. Never forward Gateway commentary, ticket data, local
paths, task metadata, or queue metadata to Feishu. If a higher-priority rule
requires internal progress commentary, keep it generic and state-only. The
complete final response must be exactly `DONT_NOTIFY`.
