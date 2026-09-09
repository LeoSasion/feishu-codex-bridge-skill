# Feishu Codex Operator

Published plugin: `1.1.0`; development source runtime: `4.2.0-alpha.123`. Operator（接线员）maps each Feishu private
chat, group, or topic to one existing Codex Desktop task.

Local-model CLI evaluation supports explicit `--final-text-policy marker_line_v1`
for bounded blank lines around synthetic final reports. Default exact checks and
all raw model text, tool/file checks and historical failures remain preserved.
See [acceptance rules](references/responses-acceptance.md#cross-model-final-report-compatibility-alpha110).

Alpha.122 aligns verification-label updates with the existing registry policy:
Desktop may remain open. The exact Operator/router must still be stopped,
callbacks empty and current evidence, preview digest and backup checks satisfied.
This does not refresh Desktop's model list or activate routing.

```text
Feishu -> Operator inbox and exact task mapping
       -> fixed Beeper -> Responder -> Final Callback -> Operator outbox -> Feishu
```

Desktop owns the Responder's context, model, tools, execution, and answer.
Beeper forwards once; it owns no business result. Operator uses a Beeper
wake-up signal (currently a Desktop deep link) only when needed. The existing
30-minute `wake lease` and delayed wake-up fallback are unchanged.

## 参考与致谢

DeepSeek V4 Flash 与 GLM 5.3 Flash 的官方在线 Responses 候选配置已纳入源码，
包含环境变量入口、当前工具适配策略及隔离验证方式，见
[官方在线模型接入](references/official-online-models.md)。安装不会自动注册或启用它们。

工具声明转换、保留原始身份并将调用交回当前 Codex 任务执行的设计，参考了
[miuuyy/codex-chatgpt-web](https://github.com/miuuyy/codex-chatgpt-web)。
感谢 miuuyy 分享这套思路，帮助我们解决工具调用方案设计中的难题。
普通工具协议适配也参考了 [CC Switch](https://github.com/farion1231/cc-switch) 的
工具上下文与 Responses 还原实现。2026-09-09 按所有者要求恢复普通工具研发，
alpha.121 保留推理内容的合法空值，统一历史与完成响应的结构检查；异常历史不发上游，
异常完成响应不释放工具。详见[推理项校验](references/model-router.md#reasoning-content-validation-alpha121)。
alpha.120 在 JSON 转成 SSE/WebSocket 前检查每个事件及展开后的总大小，超限时不发出任何事件；
同时修复长回复误用工具参数大小限制的问题。详见[事件大小检查](references/model-router.md#generated-event-bounds-alpha120)。
alpha.119 增加显式的完成输出检查，识别只有思考内容、没有回复或工具调用的
“完成”响应并返回固定诊断；不会把思考文本变成工具调用。详见[输出检查](references/model-router.md#completed-output-policy-alpha119)。
alpha.118 增加按端点显式选择的完整 JSON 上游模式，可在严格验证完整响应后
输出 SSE/WebSocket；用于流式快照不一致的端点，不会重试或修补失败的流。
结果会在生成结束后一次出现，详见 [Gemma 兼容说明](references/model-router.md#gemma-and-complete-json-upstream-alpha118)。
alpha.117 增加显式启用的 Codex developer `additional_tools` 输入声明转换；原始类型、
namespace、call_id 与结果仍通过同一映射往返，执行和审批由 Desktop 负责。
源码隔离测试覆盖 JSON/SSE/WebSocket 三轮往返，不代表已完成新一轮真实 Desktop 验收。
配置和版本依据见 [工具协议说明](references/model-router.md#input-tool-declarations-alpha117)。
新增 Desktop 搜索桥接方向已暂停，仅作为[保留思路](references/retained-desktop-search-idea.md)
记录，未经所有者明确要求不得删除或自动恢复开发；该搜索能力尚未启用。

## Install and operate on Windows

Run these scripts in PowerShell 7 (`pwsh`). Windows PowerShell 5.1 can misread
the UTF-8 scripts using its legacy text encoding and fail before installation.

Canonical source: `plugins/feishu-codex-operator`. Plugin and skill ID:
`feishu-codex-operator`. All runtime settings use `CODEX_OPERATOR_*`.

From the repository root, first configure Feishu authentication and one allowed
identity using the skill. Then, for a new installation:

```powershell
.\plugins\feishu-codex-operator\scripts\feishu-codex-operator.ps1 operator init
.\plugins\feishu-codex-operator\scripts\feishu-codex-operator.ps1 operator install -BeeperThreadId <task_uuid>
.\plugins\feishu-codex-operator\scripts\feishu-codex-operator.ps1 operator final-callback-register
.\plugins\feishu-codex-operator\scripts\feishu-codex-operator.ps1 operator start
```

Use `operator upgrade` for an existing Operator installation, after stopping
the exact service. See [Upgrade and migration](upgrade-operator.md) when moving
from the previous product name; old commands and import aliases are not supported.
Review SessionStart and SessionEnd in [Desktop settings](references/permissions-and-hooks.md).

Initialization explains and configures the current user's Codex launch shortcuts,
with original-file backups. New installations can preview recovery with
`operator uninstall`, then use `operator uninstall -Apply` before removing the
plugin in Desktop. User edits block conflicting restoration; data is retained.
See [initialization and safe removal](references/installation-and-removal.md).

`CODEX_OPERATOR_BEEPER_MODEL` accepts `beeper`, `gpt-5.3-codex-spark`, or
`gpt-5.6-luna`. Missing or blank selects Luna/low. `beeper` is a deterministic
loopback Responses API installed with the runtime. Its private catalog now uses
`visibility: list`; this makes it eligible for a picker that loads this catalog,
but does not register it in the running Desktop. Provider settings are passed
only to Beeper queue commands and do not modify global Codex config.

The optional [Python Responses router](references/model-router.md) now ships
with the runtime: it appends model registrations, passes native traffic to its
native backend and routes external aliases to configured Responses endpoints.
Alpha.107 adds read-only Desktop evidence gates with model/version binding and
explicit missing, failed and stale outcomes; it never promotes a catalog label
from a model-generated success claim. Alpha.106 adds opt-in LM Studio batch discovery from its local model inventory.
Alpha.108 adds an explicit stopped transaction to preview and update one
verification label from fresh evidence, with registry comparison, a retained
original backup and unchanged model contracts. It does not run during discovery
or update a live Desktop catalog.
Alpha.109 checks streamed text, refusal and reasoning parts against their final
snapshots before releasing buffered tools. It preserves whitespace verbatim and
rejects inconsistent text rather than trimming it. Verification reports now
separate CLI failures, missing evidence and version drift, retain combined CLI
and Desktop failure counts, and offer `verification-status --format text`.
An explicit shared Responses policy supplies new, unverified registrations;
embeddings are excluded and existing registrations are preserved. A prepared
startup entry can synchronize before opening Desktop, with the owned router
entry deactivated and its service stopped. Owners may explicitly connect their
desktop and Start menu shortcuts to the quiet unified launcher. A cold launch
runs the reviewed startup workflow; an already running Desktop is only opened.
See [unified launch entry](references/model-router.md#owner-selected-unified-launch-entry-alpha114).
Alpha.97 adds explicit registry v2 Responses tool compatibility: custom/function
mapping, namespace and client tool-search handling, success-gated JSON/SSE tool
restoration, and shared HTTP/WebSocket adaptation. v1 routes keep their existing
passthrough behavior. Alpha.99 adds [capability profiles, preflight and real-CLI
acceptance](references/responses-acceptance.md), phase timings and explicit service
restart. Alpha.98 adds explicit text-part result serialization for
string-only endpoints, reasoning-specific tool-choice checks, serial-call
compatibility and incremental SSE line scanning. Registration never performs inference or activates Codex;
the separate synthetic probe command requires an explicit model and receipt path.
It has no LiteLLM dependency or Chat Completions conversion. Detached background
start/status/stop and current CLI catalog loading are tested. General global
readiness, Windows login startup and current model/version-specific Desktop
acceptance remain open. The dated picker trials below establish only their
recorded scope; the installer does not activate the global request-routing entry. Native
catalog rows are preserved and Spark/Luna are never sent to external providers.
In the owner's controlled alpha.106 startup trial, the runtime upgrade and
one-model LM Studio batch append completed; Qwen3.8 appeared in Desktop's
catalog cache and the owner confirmed it in the dropdown. Qwen3.6 and protected
Operator state were preserved. This verifies that startup path and picker
visibility. Three subsequent actual Desktop cases verified stopping at exit 7,
file/tool roundtrips with three existing tests passing, and an explicit byte
recipe with exact readback. Free-form PowerShell generation wrote a literal
backtick-n instead of LF and honestly reported the mismatch. That failed sample
is retained; Qwen3.8 remains unverified and general global readiness is pending.
A visible Windows file-write guide and a subsequent guided Chinese-text case
verified exact UTF-8/LF output in the same disposable task. See the
[acceptance notes](references/responses-acceptance.md#windows-file-write-guidance-for-local-model-tasks)
for the workflow and its limits; no runtime command rewriting is performed.
Alpha.91 adds opaque forwarding for native search/image endpoints and preserves
compressed native request bytes. These transport contracts are isolated-test
evidence; live native tool and Desktop picker acceptance are still required.

Outside the exact current relay envelope, the local API returns this fixed text
in both JSON and SSE, without issuing any tool call:

> 我是 Beeper，Feishu Codex Operator 的本地确定性中继程序。我仅按约定协议，将飞书请求转交给指定的 Codex 任务处理。我不具备通用问答或推理能力，不执行具体业务任务；请切换到其他模型进行对话。

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
| Local Beeper model/provider | `operator_core/beeper_model_catalog.json`, `operator_core/beeper_provider.py` |
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

Use [the test maintenance guide](tests/README.md) to choose the affected modules.
Run the full suite for cross-layer changes and before release/deployment; prose-only
edits need the package audit rather than CLI execution. Keep explicit CLI skips visible.

Only while the exact service is stopped and no callback is pending:

```powershell
python -B -m unittest discover -s .\plugins\feishu-codex-operator\tests -v
pwsh -NoProfile -File .\plugins\feishu-codex-operator\scripts\audit-feishu-codex-release.ps1
```

These checks use isolated local state, not a live Feishu chat or Desktop task.

## Response performance

Alpha.87 introduced the local `beeper` model to emit the fixed four-line
bootstrap deterministically, without model weights or sampling. It accepts only
the exact request identifier and model-facing `exec` custom tool, binds to loopback,
and has zero configured request/stream retries. Any local-provider failure or
uncertainty is terminal; it never falls back. Spark and Luna remain explicit
choices, while a missing or blank selection now defaults to Luna/low.

Alpha.86 introduced the MCP-generated program and four-line Beeper bootstrap. Beeper must have an
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
Spark ignores that preference. Alpha.87 changes only the Beeper default to
Luna/low; the Responder model and wake lease are unchanged.

Alpha.82 separates callback waiting from dispatch workers and metadata queries.
Quota refresh runs outside the cache lock, with background refresh at ample
quota and the existing gradient cadence. Two attachment workers download in
parallel; large-file hashing is streamed and inbox cleanup is periodic.
The default two dispatch workers can serve up to 16 open scopes while preserving
same-scope order. Wake lease behavior is unchanged.

The content-free `event_timing` records in `operator.log` separate local queue,
preparation, queue acceptance, callback wait, and Feishu delivery. See
[Scheduling and latency](references/architecture.md#scheduling-and-latency).
An isolated test pass is not a live E2E performance claim.
