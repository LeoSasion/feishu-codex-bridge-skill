# Optional Python Responses router

This experimental component ships as source with the Operator runtime. It has
the `aiohttp` transport dependency and, below Python 3.14, `backports.zstd`
for native request decompression. There is no LiteLLM SDK, vendor adapter,
browser backend, model weight, custom executable or compilation step in this
project. Install dependency wheels into a dedicated Python 3.11+ environment
using `pip install --only-binary=:all: -r scripts/model-router-requirements.txt`.
Third-party dependency wheels can contain open-source native extensions.

## Contract

Only OpenAI Responses endpoints are supported, not Chat Completions translation.
HTTP `POST /responses` forwards request fields unchanged except the registered
model alias. The upstream response body, SSE bytes and HTTP status are passed
through. Function/custom tools, tool results, reasoning and structured-output
fields are not reinterpreted or dropped. Actual feature availability still
depends on the upstream model. No summarization or silent capability fallback
occurs. Native HTTP request bytes and native WebSocket text frames are preserved.
Gzip, deflate and Zstandard requests are inspected through a bounded decoded
copy for model routing; the original compressed native bytes and encoding header
are forwarded. Both wire and decoded Responses request sizes are limited to
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

For external Codex WebSocket connections, `response.create` is converted only
at the transport layer to HTTP Responses SSE; JSON event objects are preserved.
Each connection is pinned to one model. The router does not implement the full
public Responses resource API (retrieve/delete/cancel), external compaction,
mid-stream steering, or auxiliary endpoints beyond the explicit list. Cross-provider
encrypted context is rejected, not stripped. Start a separate task when moving
between providers with incompatible opaque context.

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

References: [OpenAI Responses streaming](https://developers.openai.com/api/docs/guides/streaming-responses)
and [Codex configuration](https://learn.chatgpt.com/docs/config-file/config-reference).

## Development handoff to another Windows computer

At alpha.96 the LM Studio helper is implemented and isolated-tested; no real
LM Studio model has been registered or inferred against on the development host.
LM Studio is on the owner's other computer. Continue there by locating its local
server, listing model IDs, confirming configured context and reasoning support,
then registering one explicit model. Verify JSON, SSE and tool-call round trips
in a fresh disposable task before requesting any global entry activation.
DeepSeek and GLM are not configured as runtime routes.

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
