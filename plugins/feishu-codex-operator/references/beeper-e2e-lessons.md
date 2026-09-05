# Beeper E2E lessons

Read this reference when changing the Beeper control prompt, model, reasoning
effort, wake-up behavior, lifecycle observation, or live E2E procedure. The
numbers below are bounded observations from one Windows host on 2026-09-04, not
general model guarantees.

## Observed outcomes

The configuration labels in the historical table below are requested settings,
not verified Desktop execution settings. Do not use these rows to compare
reasoning efforts until the matching turn metadata has been checked. Queue
arguments, Operator health, and model policy logs are not execution evidence.

| Beeper configuration | Exact Feishu replies | Observation |
|---|---:|---|
| Spark/low, Chinese control prompt | 1/2 | Small sample with a high failure rate. |
| Spark/medium, Chinese control prompt | 2/3 | One request reached the uncertainty path. |
| Spark/high, Chinese control prompt | 4/5 | One stable terminal turn produced no Final Callback. |
| Luna/low, Chinese control prompt | 3/3 | Clean small sample, not evidence of universal superiority. |
| Spark/medium, English control prompt | 4/4 | All four produced exact replies before the batch was intentionally stopped. |
| Spark/low, English control prompt | 5/5 | Times were 31.846, 26.487, 27.576, 31.903, and 29.459 seconds; mean 29.454 seconds, median 29.459 seconds. |

### Effective settings and code-relay feasibility (2026-09-05)

The alpha.83 five-message batch returned five related Feishu replies, four
exact. Durations were 23.969, 21.714, 15.558, 18.130, and 24.208 seconds.
Operator requested Spark/medium, but all five Desktop `turn_context` records
reported Spark/low, including `collaboration_mode.settings.reasoning_effort`.
These are NOT five medium samples. The fifth Beeper first sent a corrupted
marker, then sent a corrected second message. The first callback captured the
corrupted marker; the corrected Desktop final did not replace it. Operator
queued once, but Beeper sent twice. Count both exactness and duplicate-send
failures rather than treating a related bot reply as success.

Three bounded, non-business probes tested same-cell JavaScript orchestration:
the normal queue, a queue with `--enable code_mode --enable code_mode_only`,
and a Desktop task-tool send explicitly selecting Spark/medium. The first two
executed at low; the third executed at medium. All three returned an unavailable
marker and made no JavaScript orchestration call. This establishes failure of
these probes on this installation, not universal model incompatibility.

Two further owner-authorized project-only probes enabled
`[features.code_mode] enabled=true`, then also `features.code_mode_only=true`.
The Desktop-bundled CLI reported these features enabled, but the existing
Desktop Beeper again returned unavailable markers without an orchestration
call. Both turns actually ran Spark/medium. The second was an ordinary CLI
queue after the earlier Desktop task-tool calibration, establishing that the
saved medium setting survived this queue on this host. It does not establish
that arbitrary queue config overrides are applied. The only JavaScript tool
named by the final probe was the browser CUA tool, not a Desktop-task-tool
executor. Temporary project configuration was removed after the probes.
After a Desktop restart, enabling project code mode before the not-yet-loaded
Beeper was first loaded exposed exec. The first probe dropped the tools prefix;
the corrected probe actually confirmed both nested shell and Desktop send
functions at Spark/medium. A shell-based retrieval probe then took 4.5 seconds
and stopped without sending, failing the 2-second budget. Alpha.84 instead uses
a separate input-only relay MCP in the same exec. Its offline protocol tests
pass, but the live Beeper must reload the newly installed MCP before timing and
Feishu E2E can establish acceptance. Do not call this a measured performance gain.
CLI feature output alone does not prove a live Desktop task's tool inventory.

After the next reload, the input MCP was available in Beeper: an unknown-route
probe took 258 ms. The first alpha.84 real sample used verified Spark/medium,
retrieved input in 80 ms and returned the exact Feishu marker in 31.335 s.
The second sample failed before retrieval: Spark replaced the supplied MCP
namespace with a nonexistent placeholder. No Desktop send occurred; the
batch stopped without replay. Fast successful retrieval alone is therefore
insufficient acceptance evidence. Alpha.85 resolves both functions from the
live ALL_TOOLS inventory by unique suffix before retrieval, instead of asking
the model to preserve long MCP namespaces. Missing or ambiguous inventory
matches stop before consumption. This remains model-executed code, not a
guarantee that the model follows the program. New live samples are required.

Alpha.85 subsequently returned three exact replies in four new real samples
at verified Spark/medium (21.282, 16.730, and 26.348 s). The fourth took 307.600 s
to return an uncertainty notice, not the requested marker. Its 955 ms retrieval
had consumed the correct route, but Spark changed `data.dispatch` to `data.status`
in the branch and skipped sending. The generated diagnostic label incorrectly
said already_consumed_or_closed; durable state proved input had been consumed.
The fifth sample was not sent. Do not infer store state from a model-rewritten
diagnostic label, or hide this failure behind three fast successes.

Alpha.86 moves dispatch validation and fixed control-program generation to the
input MCP. Its code owns timing, null handling, Desktop resolution, and one send;
the model sees only a four-line bootstrap and no control branches. Execute the
returned program directly in the same exec without printing it or resampling.
Quoted text, Unicode separators, and code-like user input must round-trip as
JSON data only. Older MCP payloads must stop rather than fall back to model
copying. A bootstrap can still be rewritten: verify the loaded MCP contract,
same-exec program invocation, timing, and actual Feishu replies before activation.

Do not activate an opaque-payload code relay until a live Beeper invocation
demonstrates both local payload retrieval and Desktop sending in one code
execution, without model resampling between them. A standalone plugin MCP
does not automatically gain access to Desktop's task tools. Fetching payload
in a separate Responder model round trip is not an equivalent implementation:
local I/O timing cannot establish a two-second incremental latency bound.
Keep the existing route until this execution prerequisite and the full-path
latency gate pass. Never replay accepted probe or business messages.

### Wake-up signal observations

The following 2026-09-05 probes bypassed the Feishu entry and delivery edges so
the only changed variable was whether Operator sent the Beeper wake-up signal.
They still used the real
`codex queue -> Beeper -> Responder -> Final Callback` path, one new request per
sample, with no replay:

| Desktop/Beeper condition | Wake-up signal | Exact callbacks | Duration |
|---|---:|---:|---:|
| Reported idle for more than 30 minutes | suppressed | 0/1 | unknown after 98.179 s |
| Same inactive sequence, new request | sent | 1/1 | 66.843 s; likely included earlier accepted backlog |
| Beeper visible and freshly active | suppressed | 1/1 | 17.463 s |
| Beeper active but another task visible | suppressed | 2/2 | 27.094 s, 22.497 s |
| Codex Desktop restarted, new request | suppressed | 0/1 | unknown after 96.703 s |
| Same restart sequence, new request | sent | 1/1 | 41.343 s; likely included earlier accepted backlog |
| Alpha.78 fresh relay client, inactive wake lease | sent immediately | 1/1 | 21.868 s |
| Same alpha.78 client, immediately following active lease | suppressed; no fallback needed | 1/1 | 19.194 s |

These small samples support an active/inactive wake-lease distinction but do
not prove that 30 minutes is the product's exact eviction threshold. They do
show that foreground visibility is unnecessary after a task becomes active,
and that Codex Desktop
can restart while the separate Operator process survives. Therefore a wake lease
alone is insufficient: retain a short no-downstream-evidence fallback that
sends one wake-up signal for the already-accepted Beeper request without
requeueing it.

The alpha.78 smoke pair used the real queue, Beeper, Responder, and Final
Callback path with one accepted request per sample. The inactive-lease sample
attempted exactly one Beeper wake-up signal; the following active-lease sample
attempted none. This
confirms the implemented state transition on this host, but it is still a
two-sample smoke check rather than a statistical reliability claim.

The English prompt correlated with better results in these samples, including
Spark/low, but language, model effort, host load, Desktop state, and time were
not independently controlled. Current policy requires English for all
Operator-authored Spark input, not only the outer Beeper instructions. Preserve
the original user text and losslessly encode attachment metadata. Keep the
Chinese control template for explicit Luna diagnostics, not Spark. Earlier
Chinese Spark samples above remain historical evidence, not a current option.
Do not turn a language choice into an automatic retry. Keep Spark/medium as the normal policy until a
larger paired comparison justifies changing it; Spark/low remains a bounded
explicit diagnostic.

## What success means

Do not treat queue exit zero, a visible Desktop answer, a completed Responder
turn, or a message merely seen inside Codex as E2E success. Success requires the
exact marker to return to the same Feishu chat from the bot, tied to the exact
sent message, after the Final Callback and Operator delivery path.

For each live sample:

1. Confirm one exact owner P2P binding, verify the current `lark-cli` user
   identity matches it, and confirm the Operator is ready with zero active turns
   and zero pending callbacks.
2. Send one harmless plain-text request with a unique marker and a deterministic
   idempotency key. Never retry a send whose outcome is uncertain.
3. Run samples sequentially. Wait for the prior request to settle before sending
   the next one, and observe only a bounded recent window without downloading
   resources.
4. Match the bot reply to the sent message and require byte-exact marker text.
   Record elapsed time and the terminal path, including exact failure text.
5. Restore empty diagnostic overrides, restart, and verify the normal selected
   policy before declaring the experiment complete.

## Failure and debugging lessons

- Preserve one Beeper wake-up signal after a successful queue when the wake
  lease is inactive. In observed inactive states, queueing without the signal
  did not reliably begin until the Beeper task was made active in Desktop. A
  process-local 30-minute wake lease suppresses frequent signals, but only
  attributed Responder activity or a Final Callback refreshes it. If a request
  with an active lease has no downstream evidence for 30 seconds, invalidate
  the assumption and send the signal once. It must never carry request data,
  requeue the request, or target the Responder.
- `itemsView=notLoaded` is the observer's requested content-free projection,
  not a returned foreground, residency, wake-state, or task-load signal. Do not
  use it as the wake-lease source of truth.
- Separate execution time from callback time. Explicit running evidence has no
  deadline; a stable terminal turn gets only the short callback grace; unknown
  state uses the longer unknown window. A single 3600-second timeout concealed
  failures and made iteration needlessly slow.
- High reasoning is not a delivery guarantee: one Spark/high sample completed
  without a callback. Conversely, five Spark/low English successes do not prove
  that low is safe as the normal default.
- The lifecycle observer is metadata-only. It can distinguish running,
  terminal, and unknown states but cannot replace Final Callback or use Desktop
  output as answer transport.
- Immediately after restart, readiness can remain false while the initial quota
  read and event consumer start. Wait for a fresh health snapshot; do not label
  this short startup interval as a relay failure.
- A new diagnostic value must be admitted consistently by Python config,
  PowerShell validation, health parsing, tests, docs, and the release audit.
  Otherwise the Operator may run correctly while status reports an invalid health
  snapshot.
- Before running isolated tests, verify the exact Operator is stopped rather than
  trusting that a stop command addressed the intended project. When invoking
  the controller outside the repository root, pass the exact `-ProjectRoot`.
- Prompt-localization tests should prove the envelope and payload invariants.
  If a wording-only change trips a banned-word assertion, distinguish actual
  injected history from harmless control vocabulary; never relax the no-history
  transport contract merely to make the test pass.

## Comparison discipline

Use the same Responder, Feishu scope, request text, prompt language, model,
reasoning effort, timeout policy, and success definition for paired comparisons.
Verify effective Beeper model and reasoning effort from bounded, explicit
diagnostic metadata for each sample; report unknown if unavailable. Do not
add transcript inspection to the ordinary runtime or use it as answer transport.
Report the numerator and denominator plus individual durations. Treat fewer than
roughly 20 samples per variant as directional evidence. Preserve failures in the
result rather than rerunning them, because replay would bias the success rate and
can duplicate user work.
