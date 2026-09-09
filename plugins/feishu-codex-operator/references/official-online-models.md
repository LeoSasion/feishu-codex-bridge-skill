# Official online model candidates

These explicit registrations are development candidates for the existing
Responses adapter. They are packaged source assets, never automatically copied
into the installed registry, activated by installation, or advertised as Desktop
acceptance. The registrations retain their alpha.122 endpoint contracts;
development source alpha.124 clarifies wrapped-tool argument escaping and extends
the probe diagnostics described below. It does not change candidate defaults.

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

## Alpha.123 follow-up diagnostic (2026-09-09)

After the owner requested repairs, one separately reserved DeepSeek CLI workspace
case ran with the new evaluator and the unchanged endpoint contract. It passed:
four client requests, four upstream dispatches/header responses, no budget
rejection, exact file bytes and exact final answer. The validated JSON snapshots
contained one custom call in each of rounds 1–3 and a message with no call in
round 4; round 2 also contained a message. No output text or call identity was
retained by the new diagnostic summary.

This single observation did not reproduce the former fifth request. It does
not establish its root cause or prove that plugin isolation fixed model stopping
behavior. The alpha.122 failure stays failed; it is not overwritten or folded
into the fourteen-case historical total. Unicode/CRLF failures were not rerun or
reclassified. Full current CLI and Desktop acceptance remain incomplete.

## Alpha.124 escaping investigation (2026-09-10, unpublished)

The wrapped custom-tool description now separates outer JSON argument encoding
from escapes inside the original source. It preserves the caller's description,
tool identity and raw input. No output rewriting, line-ending normalization,
additional permission, retry or model-default change is introduced. Protocol
tests confirm that escaped source still travels unchanged through the adapter.

The probe adds three explicitly named cases alongside the original CRLF case:
`unicode-json-lf` changes only the requested separator to LF; `unicode-arguments`
supplies the requested source in an `input` field of a JSON object; and
`unicode-arguments-lf` combines that representation with an explicit LF separator.
All keep exact source and final-answer checks. Receipts record the first differing
UTF-8 byte offset and fixed CRLF/LF/backslash counts, never source text. These
synthetic variants distinguish representation effects; none substitutes for a
failed earlier case or for Desktop acceptance.

Eight new, separately reserved live cases ran once each, totalling thirteen
upstream requests and no retry. They are developmental observations with the
exact adapter/evaluator digests in each private receipt, not one uniform-version
acceptance suite:

| Case | DeepSeek V4 Flash | GLM 5.3 Flash |
| --- | --- | --- |
| JSON string, LF source; prior wrapper description | none: failed, 52 → 51 bytes | low: failed, 52 → 51 bytes |
| JSON argument object, CRLF source; clarified description | none: failed, 53 → 52 bytes | low: failed, 53 → 52 bytes |
| JSON argument object, LF source; clarified description | none: passed, exact 52 bytes and final value | low: passed, exact 52 bytes and final value |
| Isolated CLI file modification; clarified description | none: passed, four requests, exact file and final answer | Not run in this set |
| JSON string, CRLF source; explicit high effort | Failed before call release: nonempty opaque upstream context | Not run |

Both JSON-string/LF failures first differed at zero-based byte 43: one fewer
backslash and one additional actual LF. With the argument-object representation
and clarified description, backslash counts and content matched in both CRLF
cases, but CRLF became LF at byte 32. This comparison changed both prompt
representation and the tool description; it does not isolate their individual
effects. The two LF passes support that precise guided representation only.
They do not establish general source-copying reliability or repair the CRLF
failures. Required CRLF must never be silently converted to LF.

The high-effort DeepSeek response was completed upstream but contained a nonempty
`encrypted_content` field in a message/reasoning item. The adapter rejected it
with `opaque_upstream_context_not_supported`, returning local 502 before any
executable call was released. No ciphertext was recorded or stripped. The
candidate remains at none; increasing effort is not a verified fix. Its CLI
workspace case still used the four-request budget, and the older fifth-request
failure remains unresolved despite this further passing observation.

This work references CC Switch commit
[`2d54e261`](https://github.com/farion1231/cc-switch/blob/2d54e261c8a2f9e5b791e566c83048796bb2364b/src/config/codexProviderPresets.ts#L1147).
Its DeepSeek native catalog uses low/high/max; its GLM native presets name
glm-5.3 and glm-5-turbo, not glm-5.3-flash. The latter's exact contract must not
inherit another model's declarations. Current DeepSeek
[Responses documentation](https://api-docs.deepseek.com/api/create-response/)
describes none as disabling thinking and high as enabling high effort; that
parameter support alone does not prove compatibility with this adapter.

For new files whose line endings are not otherwise constrained, an explicit
UTF-8/LF requirement and byte readback provide a useful workflow. Preserve any
existing or user-required CRLF policy and verify it exactly. Model-generated code
must not be repaired, re-executed or declared correct merely because it ran.
Full current CLI and Desktop acceptance remain incomplete. The installed
alpha.123 runtime and both live candidate registrations were left unchanged.
