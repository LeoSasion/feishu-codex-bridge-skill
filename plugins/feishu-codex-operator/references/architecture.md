# Architecture

## Runtime data flow

```text
Feishu scope + event
        |
        v
durable inbox / access policy / dedupe
        |
        v
scope -> exact Responder task UUID
        |
        v
shared account + Spark quota cache -> optional account/rateLimits/read refresh
        |
        v
open public request_id callback route
        |
        v
codex.exe queue --thread fixed-Beeper-UUID --model Spark-or-Luna --config model-policy
        |
        +--> one Luna/low fallback only after proven Spark/medium quota rejection
        |
        +--> adaptive Beeper wake-up policy
        |      inactive/expired wake lease -> send wake-up signal once
        |      active wake lease -> wait up to 30 seconds for downstream evidence
        |      no evidence -> send wake-up signal once
        |
        v
Beeper exec: take_relay(request_id) -> execute MCP-generated program -> Desktop send once
        |
        +--> metadata-only lifecycle observer (no turn items)
        |      active/inProgress -> no execution deadline
        |      stable terminal -> 20-second callback grace
        |      unavailable/ambiguous -> 300-second unknown window
        |
        v
Responder owns all business execution
        |
        v
submit_final_callback(request_id, exact final_answer)
        |
        v
sealed outbox -> Feishu reply -> terminal scrub
```

The Operator never reads native task output as the answer. The selected task is
the only Responder. Queue exit 0 is acceptance, not proof that a model turn
ran. The Operator keeps a process-local 30-minute wake lease for the fixed
Beeper, shared across every Feishu scope. An attributed new Responder turn or a
Final Callback refreshes the lease. An inactive or expired lease causes the
Operator to send one Beeper wake-up signal. An active lease initially suppresses
the signal; if neither downstream signal appears within 30 seconds, Operator
invalidates that heuristic and sends the same at-most-once wake-up signal.
Concurrent requests share a signaling cooldown, so they do not all navigate
Desktop. A wake-up signal never queues or replays the accepted relay turn. Its
bare `codex://threads/<exact Beeper UUID>` deep link contains no request data
and is never addressed to a Responder. Opening the deep link can navigate
Desktop to Beeper; acceptance of the URI is not proof of model execution.

The lease is deliberately process-local: a Operator restart starts with no active
wake lease. Codex
Desktop can restart while the resident Operator survives, so the 30-second
fallback remains necessary even inside the nominal lease window. This bounds a
stale wake assumption without returning to an unconditional foreground change.

The locally stored relay envelope contains only the exact Responder UUID/host and one
Responder prompt: current user text, necessary read-only attachment paths, and
the callback request_id. It contains no Operator history, summary, RAG, preview,
policy, Page, capability, or attestation data. Beeper does not execute or
summarize the request, read either task, submit the callback, or retry.
All Operator-authored Spark input is concise, structured English: outer relay
instructions, nested callback guidance, attachment labels, and attachment-only
placeholders. The extracted Feishu user text is preserved without translation.
Attachment metadata uses ASCII JSON with lossless Unicode escapes, not renamed
files. Beeper receives only a short code-mode instruction and public request_id.
The separate `feishu_operator_relay` MCP validates the prepared Desktop arguments
and returns a fixed async program as `structuredContent.code`. The four-line
Beeper bootstrap records `started`, retrieves it, then calls `eval(code)()` in
the same exec. It never prints or rewrites the program, resamples the model,
or copies its branches. User text is JSON-escaped data, not control code.
Only this project's generated program is executable through this route, never
arbitrary tool text. English control instructions
do not require an English final answer. Existing Desktop history is not rewritten.
Spark ignores the Chinese language preference at every reasoning effort. The
complete Chinese control template remains selectable for Luna only with
`CODEX_OPERATOR_BEEPER_PROMPT_LANGUAGE=zh-cn`. A proven Spark quota rejection
rebuilds only the Luna control wrapper, preserving the identical payload; language
selection never adds an automatic retry lane.

Retrieval atomically clears the stored input and records `relay_started` before
returning. Another retrieval returns no dispatch, including after process loss;
it never restores or replays it. This is dispatch bookkeeping, not a credential,
the retired claim route, or an exactly-once business execution guarantee. A model
could still bypass this path; the native Desktop send tool is not intercepted.
Closed routes scrub any unconsumed input. The MCP performs local SQLite work
with a 100 ms busy timeout and never calls Desktop itself. Its generated program
checks elapsed time since `started` against 2000 ms, handles null input, resolves
one unique Desktop send tool from ALL_TOOLS, and sends the exact object once.
Missing or ambiguous send tools stop after consumption without sending.
An error, malformed
result, or late response stops the attempt; no automatic retry is added.
The Final Callback MCP schema is unchanged. Live capability and latency checks
are required before activation; a configured feature flag is not tool evidence.
The model may still alter the bootstrap or bypass it. Moving branches out of its
input reduces this failure surface; it is not an exactly-once guarantee.

The Operator explicitly selects `gpt-5.3-codex-spark` with `medium` reasoning for
Beeper by default. An exhausted Spark-specific bucket preselects
`gpt-5.6-luna` with `low` reasoning. A proven nonzero Spark usage/rate-limit
queue rejection may trigger one same-event Luna attempt because the first turn
was not accepted. Timeout, process failure, uncertainty, acceptance, and Luna
rejection never trigger another attempt. The model override never propagates to
the Responder task. Spark/low is excluded from normal selection; explicit
bounded diagnostics are described below.

There is no deadline while the exact Responder is explicitly observed as
active or its new turn is `inProgress`. A stable new-turn terminal state
(`completed`, `failed`, or `interrupted` with a positive `completedAt`) starts a
20-second callback-only grace. Every unavailable, ambiguous, malformed, or
transient observation uses a 300-second unknown-status window; if explicit
running evidence disappears, that window starts fresh at the loss of evidence.
The current standalone App Server may expose a live Desktop turn as
`interrupted` without `completedAt`; that combination is unknown, never
terminal. Expiry converges to the no-replay uncertainty path. For a bounded
diagnostic comparison only, `CODEX_OPERATOR_BEEPER_MODEL` may explicitly select
Spark or Luna; an empty value retains adaptive selection. A bounded low- or
high-effort Spark diagnostic additionally requires an explicit Spark model
override and `CODEX_OPERATOR_BEEPER_REASONING_EFFORT=low` or `high`; clearing both
values restores the normal Spark/medium or Luna/low policy.

## Responder lifecycle read lane

During admitted-event preparation, Operator opens a request-scoped stdio App
Server and captures a content-free baseline for the exact mapped Responder.
Preparation overlaps attachment downloads. The baseline must be sealed before
queueing within a total two-second budget; failure or late completion leaves
observation unknown, never a post-queue baseline. It may call only:

```text
thread/read(threadId, includeTurns=false)
thread/turns/list(threadId, limit=20, sortDirection=desc, itemsView=notLoaded)
```

The observer projects only task/turn IDs, task activity, turn status,
`startedAt`, and `completedAt`. Any returned item content, multiple unseen turns,
read failure, or protocol ambiguity disables positive inference and falls back
to unknown. The observer neither identifies the callback caller nor supplies
authentication; `request_id` remains the routing link. It never starts,
resumes, writes, or controls a task. One request-owned background reader polls
unknown state every 0.5 seconds and running state every two seconds. Running
evidence expires after five seconds without a successful read; a stable terminal
state is retained without further queries. Callback polling and delivery never
wait for metadata RPCs or child shutdown. Closing the route signals the reader
to stop; its bounded in-flight read and child cleanup finish in the background.
Service shutdown joins all owned readers.

`itemsView=notLoaded` is an App Server request for a content-free
representation; it is not a returned Desktop foreground, residency,
wake-state, or task-load flag. The adaptive wake policy therefore uses its
process-local lease plus observed downstream progress, not that field.

## Account rate-limit guard

One process-wide cache is shared by every Feishu scope and retains the returned
limit buckets by `limitId`. The `codex` bucket controls account-wide blocking.
The Spark bucket is resolved by the exact returned
`limitName=GPT-5.3-Codex-Spark`, not by its opaque `limitId`; it controls Beeper
model selection. The lower remaining percentage across the account-wide and
Spark buckets supplies the refresh cadence. Within each bucket, the window with
the lowest remaining percentage supplies its duration and matching reset time.
At startup the Operator performs one read-only `account/rateLimits/read`; later
ordinary messages refresh it adaptively:

- above 50% remaining: after 20 messages or 30 minutes;
- 20% through 50% remaining: after 10 messages or 15 minutes;
- above 5% and below 20% remaining: after 3 messages or 5 minutes;
- 5% or below: before every ordinary dispatch.

Refresh I/O runs outside the shared cache lock. Only one refresh can be in
flight; ample-quota messages use the cache and start the due read in the
background. Low/no-known-percentage or explicitly reached snapshots retain a
pre-dispatch wait, outside the lock. Messages arriving during a refresh count
toward the next cadence. Health reads never wait for network I/O. An unknown
execution timeout schedules explanatory quota refresh in the background so it
does not delay the failure notice; a proven Spark rejection still waits for
the bounded fallback decision.

`/init`, callback handling, health diagnostics, and delivery retries do not
increment this cadence. A failed read is fail-open: the old snapshot becomes
stale diagnostic data and cannot block dispatch. Only a newly read, explicit
server reached classification can stop a request before callback-route creation
and queueing. Once queue exit 0 has accepted a request, later quota evidence can
only improve a timeout explanation; it never cancels or replays that request.
When no snapshot has ever been available, retries are limited to once per 3
ordinary messages or 5 minutes so an App Server outage cannot cause one read per
message.

The quota reader is a separate short-lived stdio App Server child. It does not
call any task method, persist an account ID, or write raw CLI output to logs.

## Callback route

`request_id = sha256("feishu:" + event_id)[:32]`. It is deterministic and
public. The callback database binds it locally to the event and selected task so
the Operator can wait for the result. The first exact answer wins; an identical
duplicate converges and a conflict is rejected. This is routing and
idempotency, not authentication.

## `/init` read lane

A separate App Server process exists only for the duration of one catalog or
inspection request:

```text
initialize -> initialized -> thread/list
initialize -> initialized -> thread/read(includeTurns=false)
```

It is not resident, does not listen on a TCP port, and is not used to dispatch
ordinary messages or transport final replies. It never creates or resumes a
task. Titles come from the optional task `name`, never from content-bearing
`preview`. This task catalog lane is separate from both the account-only quota
read and the request-scoped lifecycle read lane.

## Persistence

- `state.sqlite3`: Feishu inbox/outbox and delivery state.
- `sessions.json`: stable scope-to-task mappings and bounded prior IDs.
- `callbacks.sqlite3`: pending/captured/closed callback routes.
- `health.json`: answer-free health metadata.
- `operator.log`: bounded runtime logs without message or final content.

All live files are under the installed runtime, never the source tree.

## Module boundaries

`runtime.py` coordinates the inbox, relay, callback, and outbox.
`app_server.py` owns only stdio framing and child lifecycle; catalog, quota,
and observation clients own their separate read policies. They do not import
each other's private transport. Initialization failure closes the child before
returning an error. No protocol method or dispatch policy changes are implied.

`/init` returns a `ResponderInspection`; a snapshot fingerprint checks whether
the chosen task metadata changed. Neither object activates a task or attests
who later submits a callback.

## Scheduling and latency

`CODEX_OPERATOR_MAX_CONCURRENT_TURNS` retains its configuration key and 2-worker
default (1..4), but now bounds preparation, queueing, and delivery work rather
than the duration of model execution. `dispatch.py` drives suspended relays on
one callback-pump thread; `runtime.py` resumes each event on a worker only when
its result is ready. A scope keeps one active event through delivery, then
returns to the end of the ready queue. At most 16 scopes are open concurrently;
additional scopes remain durable and queued, bounding request-owned readers.
Accepted work is never resubmitted during continuation or shutdown.

`lark.py` owns two shared attachment workers. Downloads overlap while manifest
order is preserved. Hashing reads 1 MiB blocks, not whole files. Retained-byte
accounting is incremental, including deduplicated files exactly once. Cleanup
and size reconciliation run at most every five minutes outside the message
path. A scan overlapping new downloads cannot overwrite incremental accounting.

`telemetry.py` emits one content-free `event_timing` record per settled admitted
event, using monotonic milliseconds. Stages distinguish scheduler wait,
admission, quota, progress notice, materials, remaining observer-baseline wait,
queue acceptance, callback wait, delivery scheduling, and Feishu delivery.
Total time starts at local scheduling, not the user's remote send timestamp.
Records include durable status but no event/task IDs, prompts, answers, or local
paths. Compare median and P95 by status and stage; callback wait still includes
Desktop/Beeper/Responder execution and is not a measurement of model inference
alone. Offline concurrency tests demonstrate non-blocking behavior, not live
Feishu latency improvement.
