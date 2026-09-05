# Feishu Codex Operator

Source version: `4.2.0-alpha.86`. Operator（接线员）maps each Feishu private
chat, group, or topic to one existing Codex Desktop task.

```text
Feishu -> Operator inbox and exact task mapping
       -> fixed Beeper -> Responder -> Final Callback -> Operator outbox -> Feishu
```

Desktop owns the Responder's context, model, tools, execution, and answer.
Beeper forwards once; it owns no business result. Operator uses a Beeper
wake-up signal (currently a Desktop deep link) only when needed. The existing
30-minute `wake lease` and delayed wake-up fallback are unchanged.

## Install and operate on Windows

Canonical source: `plugins/feishu-codex-operator`. Plugin and skill ID:
`feishu-codex-operator`. All runtime settings use `CODEX_OPERATOR_*`.

From the repository root, first configure Feishu authentication and one allowed
identity using the skill. Then, for a new installation:

```powershell
.\plugins\feishu-codex-operator\scripts\feishu-codex-operator.ps1 operator install -BeeperThreadId <task_uuid>
.\plugins\feishu-codex-operator\scripts\feishu-codex-operator.ps1 operator final-callback-register
.\plugins\feishu-codex-operator\scripts\feishu-codex-operator.ps1 operator start
```

Use `operator upgrade` for an existing Operator installation, after stopping
the exact service. See [Upgrade and migration](upgrade-operator.md) when moving
from the previous product name; old commands and import aliases are not supported.
Review SessionStart and SessionEnd in [Desktop settings](references/permissions-and-hooks.md).

In Feishu, send `/init`, select an existing task, and confirm. The catalog does
not create, resume, or send a turn to the selected task. Then send ordinary
messages. Final replies return only through `submit_final_callback`.
Rare execution omissions or duplicates remain possible; avoid irreversible requests.

## Architecture and maintenance

| Responsibility | Source / reference |
|---|---|
| Resident service and routing | `scripts/operator_main.py`, `operator_core/runtime.py` |
| Inbox/outbox and stable mappings | `operator_core/state.py` |
| Feishu transport and attachments | `operator_core/lark.py` |
| Minimal relay and wake lease | `operator_core/beeper_relay.py` |
| Callback storage and one-tool MCP | `operator_core/final_callback.py`, `final_callback_mcp_server.py` |
| Shared stdio transport | `operator_core/app_server.py` |
| Separate catalog, quota, lifecycle clients | `app_server_catalog.py`, `rate_limits.py`, `responder_observer.py` |

Paths in the table are below `scripts/`. Detailed policy belongs in
[Architecture](references/architecture.md); names belong in
[Terminology](references/terminology.md). Retained experiment outcomes belong in
[Beeper E2E lessons](references/beeper-e2e-lessons.md), not startup prompts.

Configuration, mappings, databases, and logs live under
`<project>/.codex/feishu-codex-operator-runtime`, not in source.
Use `operator status -Json`, `operator doctor -Json`, and
`operator readiness -Json` for read-only diagnostics.

## Validation

Only while the exact service is stopped and no callback is pending:

```powershell
python -B -m unittest discover -s .\plugins\feishu-codex-operator\tests -v
pwsh -NoProfile -File .\plugins\feishu-codex-operator\scripts\audit-feishu-codex-release.ps1
```

These checks use isolated local state, not a live Feishu chat or Desktop task.

## Response performance

Alpha.86 uses an MCP-generated program and a four-line Beeper bootstrap. Beeper must have an
`exec` tool exposing `tools.mcp__feishu_operator_relay__take_relay` and the Desktop
send tool. The separate relay MCP consumes the exact prepared input once and
returns `structuredContent.code`: a fixed async function with JSON-escaped data.
The same exec directly evaluates and invokes it without printing or model resampling.
The MCP owns dispatch validation, and its generated program owns the timing guard,
Desktop tool resolution, null-input handling, and one send. Responder
does not fetch its input. Final Callback remains a separate one-tool server.
The retrieval budget is 2 seconds; a late or uncertain retrieval never sends.
This guard does not guarantee a bound on external tool scheduling or model time.
Deployment requires a live capability and latency check, not CLI acceptance alone.

This replaces alpha.85's model-copied control branches. Missing or ambiguous
Desktop send tools stop the generated program without sending, but retrieval
has already committed the no-replay boundary. The model can still alter the
bootstrap; this is reduced exposure, not guaranteed execution. Only evaluate
programs from this Operator MCP in this closed route, never arbitrary tool text.

For the authorized project-only code-mode trial, enable this before Beeper is
loaded (Desktop restart may be needed):

```toml
[features]
code_mode_only = true
[features.code_mode]
enabled = true
```

Merge into the project's `.codex/config.toml`; do not overwrite existing settings
or change global configuration. This changes tool presentation for the project.

Alpha.83 makes all Operator-authored Spark input concise, structured English,
including nested callback and attachment instructions. Original user text is
not translated. The Chinese control template remains a Luna-only diagnostic;
Spark ignores that preference. No model, reasoning, or wake lease default changes.

Alpha.82 separates callback waiting from dispatch workers and metadata queries.
Quota refresh runs outside the cache lock, with background refresh at ample
quota and the existing gradient cadence. Two attachment workers download in
parallel; large-file hashing is streamed and inbox cleanup is periodic.
The default two dispatch workers can serve up to 16 open scopes while preserving
same-scope order. Beeper, model defaults, and wake lease behavior are unchanged.

The content-free `event_timing` records in `operator.log` separate local queue,
preparation, queue acceptance, callback wait, and Feishu delivery. See
[Scheduling and latency](references/architecture.md#scheduling-and-latency).
An isolated test pass is not a live E2E performance claim.
