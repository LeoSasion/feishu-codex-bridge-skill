# Optional Python Responses router

## Native HTTP request size (alpha.114)

Native `responses` and `responses/compact` HTTP requests accept up to 64 MiB
of wire bytes and up to 64 MiB after decompression. The old shared 16 MiB
HTTP cap rejected otherwise valid native requests locally, returning the
unhelpful `invalid_http_request` error. Requests beyond the new local cap
return HTTP 413 with `code: router_request_too_large`, a fixed `scope` and
`limit_bytes`. No prompt fragments or request bodies appear in that error.
Native bytes and encoding headers remain unchanged when forwarded. The JSON
inspection copy is released before forwarding; adapted requests reuse the
decoded bytes rather than decompressing twice.

External/Beeper HTTP input, native auxiliary HTTP input, WebSocket frames,
catalogs and bounded non-streamed responses keep their existing 16 MiB limits.
This is a transport-byte limit, not a model context/token allowance or a promise
that the upstream accepts a request. Upstream 413 responses remain unchanged
and distinct from local errors. No payload is truncated, history compacted,
attachment removed, model changed or failed request retried by this fix.
Registry reload does not replace executable code; an existing router process
needs an explicit request-free restart after this upgrade.

## Explicit registry reload (alpha.113)

After an owner-approved registry edit, `reload-registry --state-dir <state>
--port <port> --expected-registry-sha256 <reviewed-file-digest>` asks the exact
running router to load that version. This is a separate, explicit operation;
discovery, health checks and normal model requests never poll the registry.
The loopback control endpoint is `POST /<router-token>/v1/lifecycle/registry`
with exactly `{"expected_registry_sha256":"<sha256>"}`. Browser-origin requests,
extra fields and bodies larger than 1 KiB are rejected. No file path is accepted.

A changed digest causes one strict read, bounded to 1 MiB, and validation on
a worker thread. New read attempts are limited to one per 30 seconds; concurrent
attempts are rejected, and failed attempts also consume the interval. A request
for the already loaded digest is an immediate no-op without disk I/O. There is
no automatic retry, timer or upstream/model request. Normal requests add only
an in-memory registry snapshot binding, with no file checks or metadata scan.

The candidate replaces the in-memory registry only after validation and digest
comparison succeed. Existing HTTP requests and entire WebSocket connections
retain their previous registry/provider contract, including later turns on the
same connection. Newly admitted requests see the new registry. Removing a row
therefore does not revoke an existing connection; closing/reopening that
connection is a separate operation. Reload does not edit the registry, routing
entry, permissions, model files or defaults and never interrupts a request.

New catalog requests retain the native catalog and Beeper rows, merge the new
external rows and compute the ETag from the resulting bytes. Reload success
proves router memory changed, not that Desktop invalidated its catalog caches;
the response explicitly reports `desktop_refreshed: false`. Older router
processes lack this endpoint and need one request-free router restart after
upgrade before it can be used. Desktop closure is not a reload prerequisite.

This experimental component ships as source with the Operator runtime. It has
the `aiohttp` transport dependency and, below Python 3.14, `backports.zstd`
for native request decompression. There is no LiteLLM SDK, Chat Completions adapter,
browser backend, model weight, custom executable or compilation step in this
project. Install dependency wheels into a dedicated Python 3.11+ environment
using `pip install --only-binary=:all: -r scripts/model-router-requirements.txt`.
Third-party dependency wheels can contain open-source native extensions.

The [Responses tool compatibility plan](responses-tool-compatibility-plan.md)
tracks implementation and live acceptance for LM Studio, DeepSeek Flash and
GLM-5.3-Flash. Alpha.97 implements the capability, conversion and HTTP/WS paths.
Alpha.98 adds text-result serialization, reasoning-specific tool-choice checks,
serial-call compatibility and faster fragmented-stream parsing.
Protocol support and model quality are verified separately; installing this
code does not register a model or activate the global entry.

Alpha.99 adds [private capability profiles, offline preflight and bounded CLI
evaluation](responses-acceptance.md), including multi-round/error/cancel and
read-only patch-plan cases. Service status adds fixed phase timings; readiness
is read-only and restart remains an explicit, deactivated operation.

Alpha.105 adds separate stop-on-tool-error and stop-on-nonzero-exit evaluations,
and compares untrimmed final-message text. A completed protocol roundtrip or a
later successful command does not erase earlier tool errors or prove compliance
with a stop instruction. The adapter does not rewrite model code or final text.

## Contract

Only OpenAI Responses endpoints are supported, not Chat Completions translation.
For registry v1 (or v2 rows with `responses: null`), HTTP `POST /responses`
forwards request fields unchanged except the registered
model alias. The upstream response body, SSE bytes and HTTP status are passed
through. Function/custom tools, tool results, reasoning and structured-output
fields are not reinterpreted or dropped. Actual feature availability still
depends on the upstream model. No summarization or silent capability fallback
occurs. Native HTTP request bytes and native WebSocket text frames are preserved.
Gzip, deflate and Zstandard requests are inspected through a bounded decoded
copy for model routing; the original compressed native bytes and encoding header
are forwarded. Alpha.100 also preserves the absence of `Accept-Encoding` on
opaque native requests: the HTTP client must not advertise extra compression
formats on behalf of a downstream client. Explicit encoding preferences remain
unchanged, and catalog/adapted requests retain their explicit identity choice.
Both wire and decoded Responses request sizes are limited to
16 MiB; larger requests fail explicitly rather than being truncated.

Native-only `POST /alpha/search`, `/images/generations` and `/images/edits`
forward opaque bodies (including multipart), query encodings and native headers
to the fixed native backend. They require native authorization and never use an
external route. Unknown auxiliary endpoints are refused. The endpoint names
were checked in the current Desktop CLI 0.153.4; their wire preservation is
tested locally, not proof of successful live searches or image generation.

Codex-facing `GET /models` is a Codex catalog, not the public OpenAI model-list
schema: it retains native entries and appends `beeper` and explicit `api/...` or
`local/...` registrations. It needs incoming native authorization to discover
the native catalog. External response requests do not require native credentials.
Native credentials go only to the fixed Codex native backend; external services
receive only their explicitly configured environment-variable key. No redirects,
application-level retries or provider fallback are performed. The listener is
loopback-only, uses a random endpoint token and rejects browser origins.

For external Codex WebSocket connections, `response.create` uses HTTP Responses
SSE upstream. v1/null routes preserve event objects; explicitly adapted routes
use the same validation and restoration as adapted HTTP streams.
Each connection is pinned to one model. The router does not implement the full
public Responses resource API (retrieve/delete/cancel), external compaction,
mid-stream steering, or auxiliary endpoints beyond the explicit list. Cross-provider
encrypted context is rejected, not stripped. Start a separate task when moving
between providers with incompatible opaque context.

### Explicit tool adaptation (registry v2)

Each v2 model row has all v1 fields plus `responses`, either null (legacy
passthrough) or an explicit capability object. No fields are guessed from a
vendor name. The current capability contract is:

```json
{
  "protocol": "responses-tools-v1",
  "function_tools": true,
  "custom_tools": {"exec": "wrap"},
  "tool_choice": ["auto", "none"],
  "named_tool_choice": "reject",
  "parallel_tool_calls": false,
  "tool_search": false,
  "input_modalities": ["text"],
  "structured_tool_outputs": false,
  "developer_role": "native",
  "reasoning_input": true,
  "reasoning_summary": false,
  "previous_response_id": false,
  "text_verbosity": false,
  "codex_tool_mode": "code_mode_only"
}
```

This is a conservative configuration example, not evidence that any model
supports every declared setting. `custom_tools` maps an exact name (or
`namespace.name`) to `wrap` or `native`. Wrapped custom tools become functions
with exactly one string `input` property. Deterministic aliases and per-request
maps restore the original tool type, name, namespace, call IDs, source and result.
Ordinary function arguments remain unchanged. Namespace tools are flattened
without collisions. Client `tool_search` can be wrapped as a function when
enabled; its returned definitions are added to upstream tools and retained as
data in the search result. Deferred definitions are not callable until loaded.
Server-side search and unknown built-in tool types are refused, never dropped.

Supported custom formats are plain text and the exact current Codex exec Lark
framing grammar. The latter accepts nonempty source with an optional exec
pragma; validation does not parse or execute JavaScript. Other grammars are
rejected before sending, including unverified apply_patch grammars. Describing
a grammar in a function definition does not enable constrained decoding.

`tool_choice` lists supported string choices. Named selection is `native`,
`required` (expose only the named tool and use required), or `reject`. The
required mapping needs explicit required support. Successful HTTP status with
no required call, a wrong named tool, or prohibited parallel calls is a protocol
failure, not a reason to weaken the request. Returned source is never repaired.
`parallel_tool_calls=true` permits parallel calls; it does not require them.
For a registered serial-only endpoint, the adapter sends false and accepts at
most one returned tool call. An explicit client false is always retained.
Current CLI 0.153.4 was observed sending true even with a serial-only catalog.
This restriction is applied before the first send and never retries a request.
See the [Responses parameter definition](https://developers.openai.com/api/reference/cli/resources/responses/methods/create).
Developer roles are preserved by `native`, explicitly mapped by `system`/`user`,
or refused by `reject`. This mapping is a provider-specific semantic choice.

Alpha.98 accepts two optional fields in the capability object (omission preserves
alpha.97 behavior). `text_tool_outputs` defaults to `native`. Explicit
`json_string` requires `structured_tool_outputs=false` and converts text-only
tool-result part arrays to a JSON string containing the complete original list.
Part order, text, Unicode, CRLF, metadata and boundaries are retained; existing
string outputs are unchanged. It never concatenates parts, drops unsupported
parts or serializes image/file data into apparent text. This resolves the
observed LM Studio 400 on the second request with a text-part list.

`tool_choice_by_reasoning` defaults to an empty object (no additional constraint).
When supplied, it is an allowlist keyed by an explicitly registered effort or
`unspecified` for requests that omit effort. Missing profiles and disallowed
choices fail before the upstream call. Values may contain only globally allowed
`auto`, `none`, `required`, and `named` when named selection is enabled. For
example, an explicitly verified route may restrict thinking choices as follows:

```json
{
  "tool_choice_by_reasoning": {
    "unspecified": ["auto", "none"],
    "none": ["auto", "none", "required", "named"],
    "low": ["auto", "none"]
  }
}
```

This is a partial capability-object example, not a complete model registration.
It does not change reasoning effort, convert required to auto, or perform a
fallback. For the tested LM Studio model, omit required from `tool_choice` and
keep `named_tool_choice="reject"`: forced-name testing returned 400 and required
previously returned no call. Do not declare either mode based on auto success.

Reasoning effort must be in the route's list, which now accepts `max` as well
as the existing values. Summary, verbosity, reasoning history, structured tool
results and image content each require the respective capability. The initial
image surface is inline/URL `input_image`, not files, video or uploaded file IDs.
Stateful history is unsupported in adapted routes: `previous_response_id` must
be false and every tool call/result must appear in explicit input history.
No conversation store is created. Null or empty-string encrypted-content fields
are preserved; nonempty opaque history and compaction are rejected. Requesting
`include: ["reasoning.encrypted_content"]` is not itself opaque history; the
request is preserved and any actual opaque response remains refused.

Adapted JSON must end successfully before returning tools. Adapted SSE passes
text incrementally but holds all tool calls until a consistent successful
`response.completed`. It validates added/delta/done and final output items,
regenerates monotonic sequence numbers, and never emits complete tool calls
from failed, incomplete, malformed or truncated streams. HTTP and WebSocket
disconnects cancel the one upstream connection even while it is silent.
This is not an exactly-once execution guarantee; downstream network failure
after delivery remains uncertain and never triggers a retry.

Limits: request wire/decoded/rewritten bodies and JSON/SSE records 16 MiB;
one serialized tool argument/input 2 MiB; one adapted stream 64 MiB cumulatively;
1024 declared tools/output items. Limits fail explicitly. Rewritten responses
drop stale length, encoding, ETag and digest headers. Unexpected compressed
adapted responses are refused after requesting identity encoding. Native and
v1 passthrough bytes retain their existing behavior.

Adapted model catalog entries use project-owned defaults and explicit capability
fields, rather than inheriting new native model flags. `code_mode_only` requests
Codex's exec presentation; it does not itself prove that Desktop exposed or
executed that tool. Original native catalog rows remain unchanged.

## Registration and operation

### LM Studio local registration

Start LM Studio's local server separately. The helper never installs LM Studio,
downloads or loads weights, sends inference, or changes the global Codex entry.
Use `lmstudio-models --state-dir <state>` to perform one bounded read-only
`GET http://127.0.0.1:1234/v1/models`. Optional `--api-base` must use a literal
loopback address and `/v1`; redirects and environment proxies are refused.
For authenticated servers, pass `--api-key-env LM_STUDIO_API_KEY`, never a key.
The list includes visible models, which can include unloaded JIT models; it is
not evidence of a loaded model, Responses support or tool compatibility.

With the router stopped and global entry deactivated, register one exact ID:

```text
operator_model_router.py lmstudio-register --state-dir <state> --model <exact-id> --slug local/lmstudio --context-window <configured-context> --reasoning-effort <supported-effort>
```

Context and reasoning must be supplied from the selected model's actual settings.
The helper confirms the ID is listed, validates the full registry and appends it
atomically. Identical registrations converge; conflicting aliases are refused.
No existing route is replaced. Restart the router separately to load changes.
Do not manually edit the registry concurrently. An abandoned registry-edit.lock
requires explicit inspection; it is never automatically removed by a later run.
Actual JSON, SSE, tool-call round trips and Desktop acceptance remain live gates.
See [LM Studio model listing](https://lmstudio.ai/docs/developer/openai-compat/models)
and [Responses](https://lmstudio.ai/docs/developer/openai-compat/responses).

Pass `--responses-capabilities <explicit-json-file>` to `lmstudio-register` to
append a v2 route with tool adaptation. For another Responses provider, put one
complete model row in a private JSON file and run:

```text
operator_model_router.py register-model --state-dir <state> --registration <one-model-row.json>
```

The generic command performs no upstream request. It requires the router stopped
and the global entry deactivated, and refuses a conflicting existing alias.
Adding v2 converts old rows to explicit null passthrough atomically. Restart
separately to load registrations.

### Opt-in synchronization before Desktop startup

Alpha.106 adds `lmstudio-sync --state-dir <state> --discovery-policy <private-json>`.
Without `--apply`, this is a read-only preview, also usable while the router is
running. It makes one bounded local `GET /api/v1/models`, whose native metadata
distinguishes chat models, embeddings and loaded contexts. See the official
[LM Studio inventory schema](https://lmstudio.ai/docs/developer/rest/list).

The private policy has exactly `version: 1`, `api_base`, `api_key_env`,
`context_window`, `reasoning_efforts` and `responses`. The final field must be
an explicit supported Responses capability object; do not infer tool support
from model names or metadata. This is a shared requested contract for newly
discovered models, not verification that each model implements it. Credentials
remain in the named environment variable, never the policy file.

The scanner adds all new chat models within the registry's 100-row limit, using
stable aliases derived from endpoint and model key. It excludes embeddings,
retains existing rows and does not remove models missing from a later scan.
New labels include `[unverified]` and inherit no test receipts. Context is capped
by the policy, reported model maximum and exact loaded instance when available;
an unloaded or differently named instance is not reported as verified loaded.
Discovery never loads, downloads or sends inference to a model.

With Desktop closed, the owned entry deactivated and router stopped, add
`--apply` to validate and append the whole batch atomically. Any malformed model,
alias conflict, capacity error or changed registry stops the batch without a
partial write. A no-op keeps registry bytes unchanged. Restart the router and
restore only the previously owned entry separately before opening Desktop.
No task model, approval setting or native catalog row is changed.

A separately prepared local startup entry can perform that sequence on each
launch, after verifying the exact Operator is stopped and callbacks are empty.
The synchronization workflow itself refuses an already running Desktop. The
installer never activates global routing or installs
a login trigger automatically; live dropdown refresh remains separate evidence.

### Owner-selected unified launch entry (alpha.114)

`scripts/operator_desktop_entry.cs` builds a small Windows application in the
private startup-bundle directory. It runs the canonical PowerShell entry with
the helper console hidden; the compiled binary is not a release artifact. The bundle
must be immediately below the owning project's `.codex` directory, and the
PowerShell script must be in that project's canonical plugin scripts directory.
It resolves the current `OpenAI.Codex` package at each launch, without a fixed
package version or executable hash in shortcuts. The graphical bridge accepts
only an optional `--check-only` argument and does not require administrator rights.

If that package's Desktop process is already running, the entry only opens the
installed application. On a cold launch it checks the private workflow digest
in `startup-sync-plan.json`, then runs that workflow once. Existing stopped
upgrade, entry-ownership, callback, backup and atomic-write checks still apply.
A per-project mutex coalesces overlapping launches. There is no polling service
or per-request overhead. Successful launches show no helper console; failures
stop, retain a unique log and show a dialog. PowerShell `-CheckOnly` observes
which branch would run without creating logs or launching anything.

The owner must select which desktop/Start menu shortcuts to connect. Keep their
original files and a private change receipt; do not change unrelated links.
Windows-generated MSIX Start entries and taskbar pins can remain separate from
the new shortcut. A user may need to pin the prepared launcher itself once;
pinning the running native application does not establish launcher coverage.
The wrapper does not modify application binaries, protocol handlers, taskbar
registry data, permissions or task defaults. Router code replacement and live
Desktop catalog invalidation are separate from this launch convenience.

Run `operator_model_router.py init --state-dir <private-state-directory>` once.
Edit its `registry.json`; keys are environment variable names, never secrets:

```json
{
  "version": 1,
  "models": [{
    "slug": "api/my-openai-model",
    "display_name": "My OpenAI model",
    "model": "REPLACE_WITH_AVAILABLE_MODEL_ID",
    "api_base": "https://api.openai.com/v1",
    "api_key_env": "MY_OPENAI_API_KEY",
    "context_window": 32000,
    "reasoning_efforts": ["low"]
  }]
}
```

Set model ID, context and reasoning to the actual provider's capabilities; the
sample values are not automatic discovery. Local endpoints use a `local/...`
slug, a loopback `/v1` base and may set `api_key_env` to an empty string.
Run `operator_model_router.py start --state-dir <same-directory>` in the dedicated
environment to launch a hidden detached process; `serve` remains a foreground
alternative. Repeated `start` converges on the existing verified service. `status`
checks without starting or changing anything. `stop` uses an authenticated
loopback endpoint and refuses while requests are in flight or the global entry
is activated. It never kills a PID from a file. Registry changes require a stop
and start. There is no automatic crash restart or Windows login startup yet.
Never print the private token or keys.

`status` includes bounded process-local diagnostics: a failure count and the
most recent failure's fixed phase/category and optional upstream HTTP status.
There are no exception messages, URLs, headers, model IDs, task IDs, prompts or
answers in this snapshot, and restart clears it. Status performs no upstream
request. Locally generated 502 errors use a standard error object with a safe
code for TLS, timeout, transport or internal failure. Upstream error bodies and
statuses remain byte-preserved. This instrumentation never retries a request.
Adapted protocol failures additionally include an allowlisted reason code and,
when available, response status, output count, token count and presence flags.
Provider error messages, arguments and response text are never retained there.
Upstream HTTP header fields and status lines are bounded at 64 KiB (rather than
the transport library's 8190-byte default); the library's header-count bound
remains unchanged. A synthetic 16 KiB response header reproduced a local 502
before this change and is now preserved. Headers above the bound still fail
without retry. This is an isolated compatibility fix, not evidence that the
earlier Desktop failure had that cause. Incoming request-header limits are
unchanged. Native WebSocket handshake metadata from an explicit five-header
allowlist (turn state, models ETag, model and two safety-buffering hints)
is relayed through `response.metadata` for turn state/model and
`codex.response.metadata` for catalog/safety hints, before the original response
frames. The local handshake must precede model selection from the first frame;
the metadata event bridges that ordering without rebuilding original frames.
Cookies and unrelated headers are excluded. Capture is connection-local and
not retained in diagnostics. The handshake-only `x-reasoning-included` flag has
no verified event equivalent and is not synthesized. Live Desktop interpretation
and handshake-only flag compatibility remain separate gates.

An explicit `activate --state-dir <same-directory> --codex-config <exact-path>`
adds a reversible `openai_base_url` prefix after checking health. It does not
replace `model_provider`, native model rows or task defaults. Existing custom
providers, base URLs, catalogs or selected profiles block activation instead of
being overwritten. `deactivate` removes only the exact owned prefix, retaining
later unrelated edits. Close Desktop before switching either direction, then
restart it. Each actual switch moves the adjacent `models_cache.json` to a
unique `models_cache.router-backup-*.json` in the same directory, so the old
catalog cannot mask the new entry point. Backups remain private and recoverable;
they are never automatically restored across providers or published. A failed
config write restores the cache only if no concurrent cache has appeared.
Repeated activation does not invalidate an already-active cache. Catalog replies
provide a SHA-256 ETag of the complete merged body, `Cache-Control: no-cache`
and account-specific Vary headers; conditional requests receive a fresh full
catalog, never an upstream-only 304.

The first live Desktop trial did not show Beeper and its native Responses request
returned 502. Cache invalidation and validators address a confirmed integration
gap, not a proven root cause of that separate transport failure. A subsequent
owner-confirmed Desktop trial displayed Beeper and returned its exact approved
identity response. The global entry was then explicitly deactivated and the
native configuration restored. This proves that bounded picker/identity trial,
not persistent task defaults, full native-tool compatibility or LM Studio use.

Global activation is not part of automatic installation. Windows login startup,
live Desktop dropdown/default selection and native auxiliary-tool
compatibility remain acceptance gates; do not activate based only on the local
tests. The existing Operator Luna/low default and optional Spark/local Beeper
queue route remain independent and unchanged. Do not stop an activated gateway
before restoring its Codex entry point.

## Verification

Isolated tests cover native catalog preservation, byte-preserving native HTTP
and WebSocket requests, standard Responses fields and tool events, JSON/SSE,
external credential isolation, terminal stream truncation, no redirect/retry,
and reversible configuration. These are fake-endpoint contract tests, not a
paid OpenAI call, live Desktop picker proof, or a model-quality benchmark.

The opt-in `test_model_catalog_cli.py` additionally runs the explicitly supplied
current Desktop CLI with an isolated temporary Codex home and a read-only native
catalog snapshot. Only initialize and model/list are called. Set
`CODEX_OPERATOR_TEST_CLI` to the verified current executable and
`CODEX_OPERATOR_TEST_CATALOG` to the existing model catalog cache to run it.
On CLI 0.153.4, the augmented native/beeper/external catalog is accepted and the
added entries are returned as visible. This is current CLI evidence, not a claim
that the running Desktop has reloaded its model list.

The current 0.153.4 generated App Server schema represents reasoning effort as a
nonempty string, so `max` is accepted as an explicitly registered model effort.
The schema generation is local version evidence, not live model verification.

### Explicit synthetic provider probe

```text
operator_responses_probe.py --registration <one-v2-model-row.json> --case sse --receipt-dir <private-directory> --run-id <unique-diagnostic-id>
```

Cases are json, sse, required, named, structured, unicode, unicode-json, long
and long-lines. The additional cases test named selection, text-part outputs,
explicit JSON-escaped source representation and distinct numbered source lines.
They are separate diagnostics, not replacements for failed literal-source tests.
A case makes at most two
Responses requests: a custom exec request, then its synthetic result continuation
only if the original source was returned exactly. The command does not execute
that source or contact any Codex task/Feishu chat. It starts a temporary loopback
router, never loads global Codex configuration, and reads only the registered
credential environment variable. It writes a may-have-started receipt before
sending, then records content-free outcomes and timing. An existing receipt,
timeout, crash, HTTP rejection or malformed result never triggers a retry.
Probe receipts remain private and are not included in release inventories.

The result distinguishes exact verification text from text matched after
trimming; a trimmed match must not be described as an exact final-answer match.
Neither outcome proves actual Desktop tool execution.

References: [OpenAI Responses streaming](https://developers.openai.com/api/docs/guides/streaming-responses)
and [Codex configuration](https://learn.chatgpt.com/docs/config-file/config-reference).

### Current CLI and provider results (2026-09-06)

The opt-in `test_responses_cli.py` uses an ephemeral current CLI 0.153.4, an
isolated Codex home, ignored user config/rules, a read-only sandbox and a fake
loopback upstream. It proves real `exec` evaluation of `text(17 + 25);` and exact
tool-result continuation. A second case proves a nested image-tool capability
rejection reaches the next model request without weakening the restriction.
Alpha.98 additionally proves successful `exec -> tools -> synthetic MCP` result
continuation and current CLI serial-only capability handling. It does not prove
a successful native file tool in Desktop: a separate isolated file-access
diagnostic was denied by Windows. The catalog keeps
automatic Node tool review required; execution permissions remain with Codex.

A subsequent bounded ephemeral CLI 0.153.4 trial used the real LM Studio model,
the text-result serialization mode and serial-call adaptation. It completed
`CLI -> LM Studio -> exec -> synthetic MCP -> tool result -> LM Studio` in two
model requests (about 22.8 seconds), with an exact random marker available only
from the MCP result. No saved task, global configuration, retry or Feishu
transport was involved. This is a real model/CLI tool loop, still distinct from
Desktop picker/default and native auxiliary-tool acceptance.

Current CLI exposes `exec` with the exact framing grammar, `wait` and
`request_user_input`. It also adds built-in `web_search` by default, even when
the model catalog's search flag is false. The isolated test explicitly sets
`web_search="disabled"`. Adapted external routes still refuse that built-in;
users must select a fresh task/configuration with it disabled, or retain the
native route. Full Desktop tool availability and native auxiliary integration
remain required acceptance work.

Live synthetic probes reached LM Studio 0.4.15 with the already-installed
`qwen3.6-27b-neo-code-here-2t-ot`, DeepSeek `deepseek-v4-flash` at
`https://api.deepseek.com`, and GLM `glm-5.3-flash` at
`https://open.bigmodel.cn/api/v1`. All three produced a wrapped exec call and
consumed a synthetic tool result. DeepSeek's verified profile uses effort
`none`; its earlier low-effort required request returned 400. These are separate
explicit diagnostic cases, never fallback attempts. See the
[verification matrix](responses-tool-compatibility-plan.md#实测结果2026-09-06)
for exact input, whitespace, long-source and failed cases.

Temporary credentials were supplied only to probe-process memory. No online
route, global entry or task default was changed. These observations do not
qualify every capability in the candidate configurations for production use.

## Development handoff to another Windows computer

Alpha.101 accepts null optional namespace/tool documentation without relaxing
executable input validation. Decorated descriptions treat null as absent; original
tool metadata stays intact. A rejected string may include a fixed `error.param`
label (`tools.description`, `content.text`, `custom_tool.input`,
`function_call.arguments`, `protocol.identifier`, or `protocol.string`). These
labels contain no request values, paths or tool names. They are diagnostics,
never permission to repair or replay a rejected request.

Alpha.99 contains the Responses adapter, profile/preflight helpers and bounded
probe/evaluation commands. Continue
with the documented model-specific limits and fresh disposable contexts;
DeepSeek and GLM are not registered as installed runtime routes. Do not infer
global-entry readiness from the isolated suite or these live synthetic probes.

Prefer a fresh clone for source development. A complete directory copy is a
private backup, not a portable installation: `.codex` can contain credentials,
databases, attachment data, machine-specific paths and Python environments.
Never publish that directory or upload it as a public transfer archive. Preserve
the original backup; rebuild dependencies and install from canonical source on
the destination instead of executing the copied environment. Do not start a
second Operator against the same Feishu scope. A production cutover needs the
old service stopped, zero pending callbacks and explicit destination bindings;
do not replay historical requests or assume copied task UUIDs exist there.
Copying this repository does not itself transfer the Desktop conversation.
This section records the development checkpoint so a new conversation can
continue without reading runtime databases or private answer material.

### Rejected history diagnostics (alpha.102)

An `undeclared_tool_call` HTTP error includes `param: input.tool_call` and one
fixed `history_*` code describing empty definitions, namespace/type mismatch,
an absent explicitly registered custom tool, or an unknown/invalid tool name.
No request values or names are emitted. This does not match tools by guesswork,
restore missing declarations, alter caller permissions or retry a rejected request.

### Explicit history with an empty tool catalog (alpha.103)

`history_custom_tools` is an optional object, default `{}`. Each key must be an
exact already-registered custom `exec` or one-level namespace-qualified `exec`
identity, and each value must be `codex_exec_v1`. For example:
`{"exec":"codex_exec_v1","functions.exec":"codex_exec_v1"}`. Both identities
must also exist in `custom_tools`; their existing native/wrap modes still apply.

This opt-in is consulted only when no current tool definitions exist. It maps
complete historical custom calls/results using the same stable aliases and
framing validation without putting those identities into the executable catalog.
Missing/empty/null call inputs, unknown identities, conflicting call/result pairs,
opaque history and unsupported formats remain errors. Nonempty current catalogs
still require exact declared identity matches. Current `tools` and `tool_choice`
are preserved; returned executable calls are rejected when none were advertised.
Text-part result encoding remains the separate explicit lossless JSON option.
No source is executed, repaired, summarized, or retried by this history codec.

### Explicit named function results (alpha.104)

The current CLI 0.153.4 generated `FunctionCallOutputResponseItem` schema requires
only `type` and `output`; `call_id`, `name` and `namespace` are optional/nullable.
The paired function-call and custom-output types still require a call ID.
Desktop can use a named result for a delegated input without a preceding call.
This is a distinct schema form, not proof that a missing paired-call ID can be
repaired. LM Studio rejected the raw named form in a single local probe.

The optional capability defaults to `{}`. Its only accepted entry is:

```json
"named_function_outputs": {
  "codex_app.send_message_to_thread": "user_message_json_v1"
}
```

This explicitly selects a **user-role message representation**: one labelled
text part holds JSON of the complete original named result. The codec preserves
absent versus null fields, original namespace/name/item ID, every text-result
part and its metadata, Unicode and ordering. It does not extract or interpret
delegation XML, make a call ID, claim authentication from source metadata, or
create a system/developer message. It adds no executable definition or permission.
The model still decides how to interpret the data under Desktop instructions.

The codec is chosen before the first upstream request and only for the exact
registered source with an absent/null call ID. A nonempty call ID uses strict
paired history checks. Empty or malformed IDs, unknown fields/sources, images,
audio, encrypted content and dangling paired calls remain rejected. Existing
history codecs and caller tool-choice limits apply independently. Repeated named
objects retain their original order; the adapter never deduplicates or replays.
Unknown codecs are rejected and v1/null/native passthrough remains unchanged.

The current schema's plain `internal_chat_message_metadata_passthrough.turn_id`
and Desktop's observed finite `create_time` timestamp are retained in that same
JSON object. Absent/null metadata and turn IDs retain their original shape;
other metadata fields and nested/opaque values are rejected. The metadata is
correlation data, never authentication or a routing instruction.

The representation must be tested against the exact endpoint/model and adapter
digest. A local text probe is not live Desktop delegation, tool execution,
approval-dialog behavior or Feishu delivery evidence. Model command/patch errors
remain model quality failures; this codec does not rewrite generated source or
turn ignored tool failures into a successful evaluation.
