# Official online model candidates

These explicit registrations are development candidates for the existing
Responses adapter. They are packaged source assets, never automatically copied
into the installed registry, activated by installation, or advertised as Desktop
acceptance. Alpha.126 selects standard function tools in both source candidates;
endpoint, model, key variable and reasoning effort remain unchanged. The installed
registry still uses its previously selected contract. This source change is not
a live migration and requires a fresh context because the standard candidates
admit no historical custom exec calls. Older code-mode failures remain evidence.

On 2026-09-11, an inactive private Desktop trial snapshot was prepared from
alpha.129, with separate file-edit fixtures for the two standard candidates.
Seven isolated preparation and byte-verifier checks passed; lifecycle helpers
were syntax-checked but not activated. The installed alpha.123 runtime, registry,
entry and user configuration stayed unchanged, and no upstream request or
Desktop task was sent. This preparation is not Desktop acceptance: the earlier
PowerShell mock-provider result, Git Bash process failure and online CRLF
generation failures retain their original scope and outcomes. Fresh Desktop
model selection, execution, permissions and subsequent gates remain unverified.

Later on 2026-09-11, the owner completed the authorized restart and reported
that the model list appeared. Independent read-only checks found the exact
frozen alpha.129 router ready, with its owned configuration prefix and candidate
registry matching the trial records. This is startup evidence, not model tool
acceptance; the prepared Desktop file cases have not been dispatched. The
installed alpha.123 runtime and its registry remain separate. Alpha.130 adds
entry-setup preservation checks in source only; the active trial still uses its
unchanged frozen runtime and its recovery preflight remains valid.

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

Both source candidates now select `codex_tool_mode=standard`, with empty
`custom_tools` and `history_custom_tools` maps. Codex advertises ordinary function
tools directly; the catalog does not declare an unregistered freeform patch tool.
Function calls/results keep their exact identities. The dated `additional_tools_v1`
developer-envelope codec and exact named result codec for
`codex_app.send_message_to_thread` remain selected. Client tool
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

## Alpha.125 standard-function investigation (2026-09-10, unpublished)

CC Switch's native tool catalogs suggested a second approach to the unstable
custom `exec` wrapper: expose ordinary function tools directly. This is still the
explicit Responses adapter, with source/call identity checks and successful
terminal validation. It uses neither Chat Completions conversion nor an opaque
history bypass. The reference is the pinned
[CC Switch catalog implementation](https://github.com/farion1231/cc-switch/blob/2d54e261c8a2f9e5b791e566c83048796bb2364b/src-tauri/src/codex_config.rs#L2027).
Its GLM presets do not establish Flash compatibility. DeepSeek's
[Responses tool table](https://api-docs.deepseek.com/guides/responses_api/)
only supports the custom name `apply_patch`; it does not permit sending a native
custom `exec` under an arbitrary alias.

The standard-function trial retains the exact DeepSeek endpoint, model and none
effort, with these explicitly selected capability fields:

```json
{
  "codex_tool_mode": "standard",
  "custom_tools": {},
  "history_custom_tools": {}
}
```

These are changes to a complete registration's `responses` object, not a complete
registration or an instruction to mutate a running model. Use a fresh isolated
context: this contract does not admit old custom-call history. The existing
code-mode candidate and its failed receipts remain separate.

Alpha.125 fixes the standard catalog inheriting Beeper's freeform patch declaration
when no `apply_patch` custom tool is registered. Such a standard entry now sets
`apply_patch_tool_type` to null. Explicit patch registrations, code-mode entries,
Beeper and native/v1/null catalog behavior remain unchanged. Standard CLI cases
call the registered fixture function directly and record `codex_tool_mode`;
code-mode cases continue through exec. Neither route executes tools in the router.
A direct-function result does not establish raw exec source-copying fidelity.

The investigation produced these distinct observations, all from separately
reserved synthetic runs with zero retries:

| Contract / investigation | Result |
| --- | --- |
| Experimental line-array source codec | Both CRLF probes failed. DeepSeek's workspace case also failed before any tool operation; GLM's workspace case passed. The codec was removed from canonical source; private patch and failures retained. |
| Existing DeepSeek code-mode contract | New exit-stop and error-recovery cases failed before any fixture operation. The latter returned one `cmd` field instead of the declared `input`. |
| Private qualified tool-name experiment | DeepSeek exit-stop passed; recovery returned empty arguments and CRLF copying failed. The experiment was not promoted to a capability or candidate. |
| Private standard-function prototype, DeepSeek none | Error recovery and disposable file modification passed, including exact final answers and exact file bytes. These receipts bind the private prototype and are not canonical-adapter acceptance. |
| Canonical standard-function evaluator, DeepSeek none | Nested function call, multi-round result consumption, both stop cases, read-only patch preview and cancellation passed. |
| Existing GLM code-mode contract / low | New nonzero-exit stop, error recovery and read-only patch preview passed. Cancellation remained failed because the response had already completed before the cancel attempt. |

The GLM cancellation receipt shows one completed router request, zero cancelled
router requests and no router failure. An inactive request after child termination
is insufficient cancellation evidence; the evaluator kept the case failed.
It does not demonstrate a broken disconnect handler. Loopback HTTP and WebSocket
cancellation remain independently covered. Patch preview never proves file-write
approval; fixture-only file modification is not Desktop approval evidence.

Two direct no-tool DeepSeek high-effort observations returned a nonempty
`encrypted_content` string in a reasoning item. Only fixed types, sizes and
comparison flags were retained, never the state or reasoning text. A separate
native v2/null CLI trial completed its first synthetic MCP operation, then failed
at the existing opaque-history check before a second upstream request. Switching
to native routing cannot bypass this rule. A same-endpoint/model signed-state
proposal is awaiting explicit owner authorization; no such exception is implemented
or activated. High effort is still not admitted by either live candidate.

A separate confirmed defect affected wrapped custom history and client tool-search
history: raw input could fit within 2 MiB while its serialized function arguments
exceeded that bound. Alpha.125 checks the actual encoded arguments before dispatch,
including history-only custom mappings. It never truncates or normalizes the source.
Wrong-wrapper diagnostics now retain only a field count and fixed known-field
presence flags, not arbitrary keys, values or inferred replacement arguments.

These developmental reports span adapter/evaluator revisions. They do not form one
uniform-version capability profile or override prior failures. Exact CRLF source
copying, high-effort history and fresh Desktop acceptance remain unresolved. The
installed alpha.123 runtime, both live registrations and the default model have not
been changed by this investigation.
## Alpha.126 direct function validation (2026-09-10, unpublished)

The source candidates now select the standard function contract investigated in
alpha.125. This avoids asking either model to wrap MCP calls inside custom exec
source. It does not rewrite model-generated source, grant tools or permissions,
strip opaque reasoning state, or migrate existing tasks and live registrations.
Native/v1/null routes, local Beeper and explicitly selected code-mode contracts
retain their behavior. Both candidates remain labelled unverified.

The source probe previously always declared custom exec, even when given an
explicit standard registration. It now declares a synthetic `source_check`
function in standard mode, verifies exactly one input string, and pairs a
synthetic function result with its original call ID. It performs no source or
tool execution. Code-mode probes retain their original custom-call path. Both
paths stop after any input mismatch and retain exact final-answer comparison.

The cancellation evaluator previously waited for upstream response headers.
GLM's buffered JSON often delivered headers together with its completed answer,
so that trigger could miss the active request. The new isolated evaluator
observes aiohttp's local outgoing body-write boundary without reading its callback
payload, URL or headers. It cancels only the disposable CLI child, and passes
only when both the recorded dispatch and router outcome are cancelled, with no
completed/failed request or second request. Inactivity alone cannot pass. This
proves local disconnect propagation, not provider receipt, stopped generation or
billing. Production routing and timing stages are unchanged. Historical late
cancellation failures are not reclassified.

The new owner-authorized observations are distinct dated receipts with zero retries:

| New case | DeepSeek V4 Flash / none | GLM 5.3 Flash / low |
| --- | --- | --- |
| Standard CLI file read/change/verify | Passed; exact file and final answer | Passed; exact file and final answer |
| Standard CLI tool-error recovery | Passed | Passed |
| Standard CLI multi-round result consumption | Prior alpha.125 evidence | Passed |
| Standard CLI tool-error stop / nonzero-exit stop | Prior alpha.125 evidence | Both passed, no extra operation |
| Standard CLI read-only patch preview | Prior alpha.125 evidence | Passed; original file unchanged |
| Standard CLI direct nested fixture operation | Prior alpha.125 evidence | Passed |
| Standard CLI cancellation at local body-write boundary | Passed local cancellation, no response headers | Passed local cancellation, no response headers |
| Direct function Unicode + LF source probe | Passed, exact 52 bytes and final verification | Passed, exact 52 bytes and final verification |
| Direct function Unicode + CRLF source probe | Failed, 53 → 52 bytes, first difference at byte 33 | Failed, 53 → 52 bytes, first difference at byte 32 |

The DeepSeek CRLF failure had no LF and did not match after CRLF-to-LF comparison;
the GLM failure did match that diagnostic comparison. Neither result was normalized,
executed or followed with a synthetic tool result. Standard functions therefore do
not establish arbitrary source-copying fidelity. Existing or required line endings
must still be checked byte-for-byte; the successful LF probes do not waive CRLF.

These cases span the evaluator changes above, so they are not a single-version
capability profile. Isolated CLI/fixture passes do not establish fresh Desktop
acceptance or real file-write approval. High-effort opaque-history handling remains
unimplemented pending separate owner authorization. The installed alpha.123 runtime,
registry and native default remain unchanged; no Codex restart was required.

## Alpha.127 output validation and source boundary (2026-09-10, unpublished)

An isolated reproduction found that the ordinary JSON terminal validator reused
input-content validation for assistant messages. A snapshot containing a valid
tool call followed by an assistant `input_text` part could therefore return the
call. JSON-to-event projection rejected such parts later, leaving inconsistent
validation between transports. The terminal validator now rejects input-only or
reasoning parts inside an assistant message before returning any call. Input
image support never authorizes output `input_image`. The fixed diagnostic is
`invalid_output_message_content`; it includes no provider text. Legal incoming
user content, supported legacy text, native/v1/null routes and local Beeper retain
their behavior. Malformed output is neither repaired nor retried.

The probe's final verifier also handled only `output_text` lists. It now reads
already-supported plain JSON assistant strings and legacy `text` parts without
trimming them. It does not promote reasoning or refusal to the expected marker,
normalize text, or enable a new event representation. Existing JSON-to-event
restrictions remain unchanged. Exact final comparison still rejects extra CRLF.

Four new private source-representation experiments compared validated upstream
function arguments with restored arguments. These are observation-only private
variants, not new canonical capabilities or acceptance cases:

| Representation (one attempt per provider) | DeepSeek none | GLM low |
| --- | --- | --- |
| Minimal two-character value separated by CRLF | Failed: 8 → 10 bytes, no CR/LF, two backslashes | Failed: same fixed counts |
| Existing synthetic source, CRLF expressed as Unicode JSON escapes in the prompt | Failed: 53 → 52 bytes, CRLF became LF | Failed: same fixed counts |

In all four failures, the upstream source already differed from the request and
the argument JSON bytes remained exactly unchanged through restoration. The
experiments distinguish the upstream output from local adaptation; they do not
identify whether model generation or provider processing changed the text. No
mismatched source was executed, no synthetic result was sent after the mismatch,
and no failed request was retried. The alternative prompt representation was not
promoted to a production workaround.

With the new terminal validator, four new canonical CLI cases passed: file
read/change/verify and stop-after-tool-error for each provider, totaling twelve
requests. The file cases preserve the complete expected CRLF file bytes by
changing only the selected substring; they do not require the model to reproduce
every newline. These passes and the four failed private probes are separate
evidence and do not replace older results or full fresh Desktop acceptance.

Source is alpha.127. The installed alpha.123 runtime, registry, default model,
credentials and running Desktop were unchanged. The opaque high-effort proposal
remains unimplemented pending its separate authorization.

## Native terminal evaluator fixes (alpha.129, 2026-09-10)

The prior blanket policy refusal also occurred for a single file read. The
isolated CLI ignored user configuration and did not select a Windows sandbox
implementation. An explicit `--windows-sandbox unelevated` option now supplies
the restricted-token backend only to that disposable child; read-only and
never-approve settings remain fixed. The official [Windows sandbox reference](https://learn.chatgpt.com/docs/windows/windows-sandbox)
describes this backend. No elevated setup, global configuration or automatic
backend fallback is introduced.

The development cases exposed independent issues: nested token creation failed;
Python's protected temporary-directory ACL blocked native file reads; and the
validator expected the older `Final output:` label instead of current `Output:`.
The harness now separates a private CLI home from a new synthetic workspace with
normal inheritance, parses either native envelope and validates the exit status.
It never repairs an existing directory ACL or an observed tool result.

PowerShell then read the file successfully but its console text lost Chinese
characters. A process-local encoding experiment was rejected by constrained
language mode; that failed experiment is retained and its prelude was removed.
The final case uses native `Get-Content -AsByteStream` with exact ASCII decimal
byte output. This new case passed using PowerShell 7, preserving BOM, Chinese,
quotes, literal backslashes and mixed LF/CRLF bytes. It proves the bounded native
byte read with a mock provider, not Unicode console-text compatibility, online
model generation, arbitrary shell commands or Desktop acceptance.

Git Bash still failed creating an MSYS signal pipe inside the native sandbox
(Win32 error 5). Its negative process exit remains a failed case. It is not an
API text-corruption result and is not repaired by this adapter. Prior provider
CRLF-regeneration failures remain unchanged. No online inference was requested
during these terminal fixes; installed runtime and live registration are unchanged.

## Terminal-specific evaluation (alpha.128, 2026-09-10, historical)

File-fixture success is independent of the terminal: the existing workspace
case uses a dedicated MCP fixture, not PowerShell or Bash. New `cli_powershell`
and `cli_bash` cases test a fixed native read-only command with an explicit shell
executable, quoted paths/arguments and separate console/file-byte comparisons.
See [the terminal case contract](responses-acceptance.md#bounded-cli-evaluation).
The production adapter still forwards shell arguments unchanged and never
selects a user's terminal or repairs model-authored source.

Current CLI 0.153.4 exposed `exec_command` as a top-level function. A development
fixture incorrectly assumed a `functions` namespace and was corrected against
the observed declaration. An absolute Git Bash tool argument initially appeared
as the Windows system `bash.exe` in the native rejection text. The evaluator now
also scopes the requested executable's directory to the disposable child's PATH;
it does not alter global PATH or claim actual process identity from an argument.

Both native terminal cases were refused by the current read-only execution
policy, including the reduced builtin-only fixture. These are CLI-policy
failures observed with a mock provider, not API character-corruption results or
successful terminal execution. Refusal-boundary tests require that failure,
preserve the files, and stop before another upstream dispatch. The original
development failures remain in the private audit logs. No policy was relaxed,
no live provider was contacted, and no Desktop task or default was changed.

Terminal reports bind the requested shell digest and stay separate from core
CLI, exact-source and Desktop gates. Prior CRLF/escape failures remain unresolved;
their status and byte comparisons are unchanged. Official documentation describes
[shell environment configuration](https://learn.chatgpt.com/docs/config-file/config-reference)
and the [Windows sandbox](https://learn.chatgpt.com/docs/windows/windows-sandbox),
but does not establish the exact executable-resolution behavior observed here.
