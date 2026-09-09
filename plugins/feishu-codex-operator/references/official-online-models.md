# Official online model candidates

These explicit registrations are development candidates for the existing
Responses adapter. They are packaged source assets, never automatically copied
into the installed registry, activated by installation, or advertised as Desktop
acceptance. Runtime implementation remains alpha.122.

| Candidate | Official Responses base | Key environment variable | Effort |
| --- | --- | --- | --- |
| [DeepSeek V4 Flash](../assets/responses/deepseek-v4-flash.candidate.json) | `https://api.deepseek.com` | `DEEPSEEK_API_KEY` | `none` |
| [GLM 5.3 Flash](../assets/responses/glm-5.3-flash.candidate.json) | `https://open.bigmodel.cn/api/v1` | `GLM_API_KEY` | `low` |

Official documentation was checked on 2026-09-09:
[DeepSeek Responses](https://api-docs.deepseek.com/guides/responses_api/),
[DeepSeek request reference](https://api-docs.deepseek.com/api/create-response/),
[GLM Codex integration](https://docs.bigmodel.cn/cn/coding-plan/tool/codex), and
[GLM model reference](https://docs.bigmodel.cn/cn/guide/models/vlm/glm-5.3-flash).
The GLM Codex guide describes Coding Plan credentials; ordinary platform keys
and team-plan keys must not be assumed interchangeable. An HTTP rejection stops
the test without trying a different endpoint or credential. Neither candidate
uses Chat Completions conversion or a third-party model gateway.

## Explicit contract

Both candidates select one complete JSON upstream request per model round,
including when the client requests SSE or WebSocket. The adapter validates the
terminal snapshot before projecting downstream events. This buffers generation;
a downstream SSE pass does not establish upstream streaming or first-token
latency. There is no stream repair or automatic retry.

Both wrap the exact `exec` and `functions.exec` custom identities, retain paired
history, and select the dated `additional_tools_v1` developer-envelope codec and
the exact named result codec for `codex_app.send_message_to_thread`. Client tool
search definitions may be adapted after successful loading; provider-hosted web
search stays disabled. Text tool results retain JSON part boundaries. Tools
still execute under Codex permissions, not inside the router.

Only text input, serial calls, `auto` and `none` selection are admitted. A
completed result must contain a validated call or nonempty assistant text/refusal.
DeepSeek explicitly maps developer messages to user messages to reflect its
documented role handling. It ignores the upstream parallel flag; the adapter
still rejects multiple calls when the request admits only one. The GLM role
representation remains native. No unsupported image is flattened into text.

Effort choices retain the earlier tested values. Current documentation may
advertise additional choices, but this does not upgrade the earlier DeepSeek
low/required rejection into a pass. New model names, reasoning levels, parallel
calls and modalities need separately selected contracts and fresh evidence.

## Run from the canonical plugin directory

Use the project's Python environment and an explicitly configured key in the
probe process. Never place a real key in these JSON files, command examples,
logs or receipts. Preflight needs no key and makes no network request:

```text
python -B scripts/operator_model_router.py preflight --registration assets/responses/deepseek-v4-flash.candidate.json
python -B scripts/operator_model_router.py preflight --registration assets/responses/glm-5.3-flash.candidate.json
```

With the exact Operator stopped and no pending callback, run an owner-authorized
synthetic case. Set a new run ID for a genuinely new case; never change it to
replay an uncertain or failed attempt:

```text
python -B scripts/operator_responses_probe.py --registration assets/responses/deepseek-v4-flash.candidate.json --case json --receipt-dir <private-receipts> --run-id <unique-case-id>
```

Use the GLM candidate path for its separate test. Each case permits at most two
model requests, supplies a synthetic result, and never executes generated code.
`sse` checks downstream event projection; `unicode-json` asks for exact escaped
Unicode/CRLF preservation. Both exact and diagnostic comparisons stay visible.
The existing isolated CLI evaluator and separate Desktop evidence review remain
the acceptance gates; a synthetic probe is not a completed Desktop tool loop.

The bundled candidate roundtrip runs inside the existing probe regression method
against loopback JSON upstreams, for JSON and SSE clients. It validates adapter
integration with these exact capability combinations without contacting a model.
Private live results are recorded separately and do not change the candidates'
unverified display labels or the installed runtime.

## Dated live observations (2026-09-09, source alpha.122)

The owner supplied temporary process-memory credentials for these official
endpoints. Both keys authenticated successfully. Neither key was written into
the candidates or retained as project configuration. Official CLI 0.153.4 was
resolved from the current Desktop installation; each CLI case used a disposable
home and synthetic fixture, with zero retries and an explicit request budget.

| Case | DeepSeek V4 Flash / none | GLM 5.3 Flash / low |
| --- | --- | --- |
| JSON custom call and synthetic result | Passed, exact source and answer | Passed, exact source and answer |
| Downstream SSE from JSON upstream | Passed, exact source and answer | Passed, exact source and answer |
| Escaped Unicode/CRLF exact source | Failed: 53 requested UTF-8 bytes became 50; newline normalization did not explain all differences | Failed: 53 bytes became 52; matches only after CRLF normalization |
| CLI exec to nested MCP | Passed | Passed |
| CLI multi-round challenge/result | Passed | Passed |
| CLI stop after fixture tool error | Passed, no later fixture operation | Passed, no later fixture operation |
| CLI disposable file modification | Failed overall: file bytes and operations correct, but a fifth client request exceeded the four-request budget and no final answer was produced | Passed, exact file and final answer |

The fifth DeepSeek client request was rejected locally before upstream dispatch;
four upstream requests completed. This is not evidence of five provider calls or
an automatic retry. The Unicode cases stopped after one model request and did
not execute the changed source. Adapter byte-preservation regressions pass;
the probes expose source-generation differences, not permission to repair them.

Eleven of fourteen selected live cases passed; all three failures remain visible.
The focused adapter/events/router/probe suite passed 101 methods, including the
candidate loopback checks. Package inventory and syntax audit passed with 107
plugin files. Tool-error recovery, command-exit stop, patch preview, cancellation,
full AdditionalTools live coverage and Desktop acceptance still need separate
current evidence. No online registration, global entry or default model changed.
