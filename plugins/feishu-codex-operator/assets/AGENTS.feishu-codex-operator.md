<!-- FEISHU_CODEX_OPERATOR_RULES_START -->
## Feishu Operator runtime safety

### Authority and ownership

- `plugins/feishu-codex-operator` is the only canonical project source. The
  project runtime under `.codex/feishu-codex-operator-runtime` and versioned
  plugin-cache copies are installed outputs, never development authority.
- The product/service is Feishu Codex Operator. Use `operator_core`,
  `OperatorRuntime`, and `OperatorConfig` in core code. Plugin/skill IDs,
  commands, Hooks, environment and runtime paths use one Operator naming surface,
  without old aliases. Old installations require a stopped, explicit cutover.
  Preserve all `wake lease` names, fields, and behavior.
- Codex Desktop owns every selected business task: its conversation, context,
  model, approvals, tools, files, plugins, execution, and final answer.
- The Operator owns Feishu authentication, durable inbox/outbox state, stable
  scope-to-task mappings, attachment transport, delivery, and local lifecycle.
- The old Page/capability/claim route is permanently non-executable. The only
  current Beeper is one fixed, minimal wake-up relay task; it owns no
  business context, result, queue database, grant, claim, or callback.
  Historical rows may be recognized only to prevent replay or migrate an
  existing stable Responder binding. Never recreate or repair the old route.

### Minimal Beeper routing

- Bind each Feishu private chat, group, or topic scope to one exact Codex task
  UUID. Never route by task title, project label, preview text, or display name.
  One Codex task may be actively bound to only one Feishu scope.
- Configure one exact Beeper task UUID. It must never be used as a Responder or
  exposed as a `/init` candidate. Each newly admitted ordinary Feishu event
  queues that Beeper with `gpt-5.3-codex-spark` and `medium` reasoning by default.
  Spark with `low` reasoning is forbidden in normal selection. A bounded
  diagnostic may select Spark/low only through the explicit Spark model and
  reasoning overrides; it never changes the normal Spark/medium policy.
  Spark always receives concise, structured English Operator instructions,
  including nested callback guidance, attachment labels, and attachment-only
  placeholders. Preserve the Feishu user's original text without translation.
  JSON-escape non-ASCII attachment metadata losslessly; never rename real paths.
  Keep the Chinese control template selectable for Luna only with
  `CODEX_OPERATOR_BEEPER_PROMPT_LANGUAGE=zh-cn`; Spark ignores that preference.
  Prompt language never permits a second attempt after an accepted or uncertain queue.
  The compact Beeper message contains only public request_id and code-mode
  instructions. Operator stores the exact Responder UUID/host, current user
  request, attachment paths, and minimal callback route locally until retrieval.
- After a successful queue acceptance, the Operator keeps one process-local
  30-minute wake lease for the fixed Beeper, shared across Feishu scopes. Only
  an attributed Responder turn or a Final Callback refreshes it. An inactive or
  expired lease may immediately send at most one Beeper wake-up signal for that
  accepted request. An active lease initially suppresses the signal; if no
  downstream evidence appears within 30 seconds, invalidate the assumption and
  send the same at-most-once wake-up signal. Concurrent requests coalesce wake
  signals. This never queues or replays a turn. The signal is transported by
  the bare `codex://threads/<exact Beeper UUID>` URI, which contains no query,
  payload, Responder UUID, or request identifier. Never send a wake-up signal
  to, resume, or otherwise take control of a Responder. `Wake-up signal` is the
  application action and deep link is its current implementation. Opening it
  may navigate Desktop to Beeper, not prove execution. Do not model it as a
  POCSAG preamble, synchronization codeword, or address codeword.
- The Beeper calls `mcp__codex_app__send_message_to_thread` exactly once for the
  exact mapped Responder, omitting model and thinking overrides. One exec first
  calls the separate `feishu_operator_relay.take_relay(request_id)` MCP, then
  directly evaluates and invokes only its Operator-generated structuredContent.code
  in the same exec, without model resampling, program/payload printing, rewriting,
  or a separate send call. The four-line bootstrap records started before retrieval.
  The MCP validates the dispatch and embeds it solely as escaped JSON data in a
  fixed async program. That program owns the 2000 ms guard, null-input handling,
  unique Desktop send-tool resolution, and one unchanged dispatch. Missing or
  ambiguous send tools stop without sending, after consumption; never replay.
  Never evaluate arbitrary tool text or user-provided code through this route.
  Retrieval commits a no-replay boundary and clears the input before returning;
  duplicate, unknown, captured, or closed requests return no dispatch. This is
  local dispatch bookkeeping, not the retired claim route or authentication.
  It does not intercept native sends or guarantee exactly-once business execution.
  Retrieval errors or elapsed time over 2000 ms stop without sending or retrying.
  This timing guard is not a guarantee about external scheduling latency.
  Code-mode tool availability and live timing must pass before deployment.
  The Beeper never
  performs the business task, reads task state or history, submits a Final
  Callback, retries an uncertain send, or uses another task-control tool.
- Resolve the CLI from the current Codex Desktop installation below
  `%LOCALAPPDATA%\OpenAI\Codex\bin` or an explicit configuration value.
  Never use a PATH-selected CLI and never hardcode a user name or version hash.
- At startup or when the adaptive cadence is due, the Operator may start a short-lived stdio App Server
  solely to call `account/rateLimits/read`. Cache the response account-wide and
  by returned `limitId`; locate the Spark-specific bucket by its exact returned
  `limitName`, never by a hardcoded opaque `limitId`. Never query on every
  message while ample quota remains. Use the tighter of the account-wide and
  Spark remaining percentages: refresh after 20 messages or 30 minutes above
  50%, after 10 messages or 15 minutes from 20% through 50%, after 3 messages or
  5 minutes above 5% and below 20%, and on every message at 5% or below. Startup
  primes the cache. Refresh I/O runs outside the cache lock and concurrent
  refreshes coalesce. Above 5% with known percentages and no reached limit,
  due reads run in the background; low/unknown-percentage or reached-limit
  snapshots retain a pre-dispatch read. An unavailable snapshot remains fail-open.
- If a fresh cache says the Spark bucket is exhausted, queue Beeper once with
  `gpt-5.6-luna` and `low` reasoning instead. If a Spark queue attempt returns a
  proven nonzero usage-limit or rate-limit rejection, refresh the cache and may
  make exactly one same-event Luna/low queue attempt. This is the only automatic
  CLI fallback; never fall back after timeout, crash, uncertain outcome, an
  accepted queue, or a Luna rejection. Model overrides apply only to Beeper;
  Beeper still omits model and thinking when it sends to the Responder. A
  bounded diagnostic may set `CODEX_OPERATOR_BEEPER_MODEL` only to Spark or Luna;
  leave it empty for normal adaptive selection. A bounded Spark-only diagnostic
  may additionally set `CODEX_OPERATOR_BEEPER_REASONING_EFFORT=low` or `high`, but only
  together with an explicit Spark model override; empty restores the normal
  Spark/medium or Luna/low policy.
- A failed quota read keeps the last snapshot only as stale diagnostics and must
  not block dispatch. Only a fresh server-classified reached limit may stop a
  pre-dispatch request; then do not queue or send the Beeper wake-up signal. Never
  cancel or replay an accepted queue because of a later quota observation.
- During admitted-event preparation and while its callback route is open,
  the Operator may keep one request-scoped
  stdio App Server child for the exact mapped Responder. It may call only
  `thread/read(includeTurns=false)` and `thread/turns/list` with `limit=20`,
  descending order, and `itemsView=notLoaded`. Retain only task/turn IDs,
  activity/status, `startedAt`, and `completedAt`. Reject item content and
  treat multiple unseen turns as ambiguous. This observation is routing-time
  lifecycle metadata, never answer transport, authentication, or attestation.
  Baseline preparation may overlap attachment preparation but must be sealed
  before queueing, within a total two-second budget. A late baseline is discarded.
  Metadata polling and child cleanup never block callback delivery. Cached running
  evidence expires after five seconds without a successful read; stable terminal
  evidence is retained. Poll every 0.5 seconds while unknown, every two seconds
  while running, and stop querying after stable terminal evidence.
  `itemsView=notLoaded` is a content projection requested by Operator, not a
  Desktop foreground, residency, wake-state, or task-load signal.
- Apart from the account-only quota lane, that content-free lifecycle lane,
  and `/init` catalog lane, the ordinary route never calls `resume`, uses App
  Server task methods, reads a task transcript, creates a task, or injects
  Operator history, summaries, RAG, preview text, or routing policy.
- A zero queue exit means only that the request was accepted. A nonzero exit is
  a proven pre-dispatch failure and permits only the bounded Spark-to-Luna
  fallback above. Timeout, crash, or uncertainty is terminal and is never
  automatically replayed.

### Final Callback routing

- Before queueing, the Operator opens one durable callback route for a public,
  deterministic 32-hex `request_id` bound locally to the Feishu event and
  exact responder task.
- The Responder receives the user request plus only necessary attachment paths,
  the `request_id`, and a short instruction to call
  `submit_final_callback(request_id, final_answer)`.
- `request_id` is correlation data, not a secret, token, capability,
  authentication mechanism, or caller/turn attestation. Do not describe it as
  stronger proof.
- The Final Callback MCP server exposes only `submit_final_callback`. It must
  not expose claim, arm, finish, failure, catalog, task-control, or transcript
  tools.
- The first exact non-empty callback for an open request wins. An identical
  duplicate converges; conflicting, unknown, closed, oversized, or invalid
  submissions are rejected. Preserve the final answer as an exact Unicode
  string through delivery.
- Native assistant output, task reads, logs, databases, rollout files, UI, OCR,
  clipboard, and temporary files are never final-answer transports.

### Temporary automated Feishu debugging

- Until the project owner explicitly says `解除自动` or otherwise revokes this
  authorization, automated Operator debugging may use `lark-cli` without
  per-message reconfirmation only in the exact already-confirmed P2P chat with
  the `codex` bot, sent as the verified user identity.
- This authorization covers only bounded, low-risk, plain-text end-to-end test
  prompts and reading their replies. It does not cover another chat, group,
  recipient, sending identity, attachment, mention, credential, secret,
  irreversible instruction, or unrelated message history; any such expansion
  requires fresh explicit approval.
- Resolve the exact chat from the current durable scope binding and verify the
  sending identity before each test. Never hardcode a user name, chat ID, or
  access token in source, rules, commands saved to disk, or logs.
- Send at most once per test case with a unique deterministic idempotency key.
  A timeout, crash, nonzero exit, or uncertain result is terminal and must not
  be retried or replayed automatically.
- Receive checks must stay in the same exact chat, use a bounded recent window
  beginning at or after the test send, and must not download resources. Reading
  a reply is observation only and never replaces the Final Callback transport.
- When the owner revokes automatic debugging, stop all automatic send/read
  activity immediately and remove this temporary subsection from both rule
  mirrors before any later automated message operation.

### Read-only `/init` catalog

- `/init` is the only reserved Feishu slash command. It may list active stored
  tasks, inspect the exact selected task, confirm the choice, and atomically
  update the local mapping. It must not create, resume, archive, restore,
  compact, rename, fork, or send a turn to a Codex task.
- To avoid creating a Desktop query conversation, the Operator may start one
  catalog-only short-lived stdio App Server while serving `/init`. The only
  admitted catalog task methods are `thread/list` and `thread/read` with
  `includeTurns=false`, plus protocol initialization.
- The catalog must never use `thread/start`, `turn/start`, `thread/resume`,
  task-tool MCP calls, transcript reads, or preview text as a title.
- The configured minimal Beeper UUID is always filtered from catalog results
  and rejected during inspection; it can never become a scope binding.
- Catalog snapshots are memory-only, initiator-bound, limited to 50 tasks, valid
  for at most ten minutes, and selected by immutable task UUID. Read
  uncertainty is terminal for that Feishu event and never becomes a business
  request.
- A successful binding sends the one-time plain-language notice that requests
  can rarely be missed or duplicated and should not be used for irreversible
  actions.

### State, lifecycle, and diagnostics

- Preserve deterministic Feishu event idempotency and the first terminal
  result. Existing `producer_unavailable_no_retry` rows from the retired
  route remain historical and must not be adopted by the minimal relay.
- Persist the conservative may-have-started boundary before queueing Beeper.
  Once queue is accepted, wake-up signal failure, process loss, send uncertainty, or
  callback timeout must never cause an automatic replay.
- Separate execution waiting from callback waiting. Explicit Responder
  `active` or turn `inProgress` has no execution deadline. A stable
  `completed`, `failed`, or `interrupted` turn with a positive `completedAt`
  starts a callback-only grace of 20 seconds by default, configurable only
  within 10..30 seconds. Observer failure, ambiguity, malformed metadata, and
  `interrupted` without `completedAt` are unknown and use a 300-second window,
  configurable within 30..86400 seconds. If explicit running evidence is lost,
  start a fresh unknown window at that observation. Either expiry is terminal,
  closes the callback route, and never causes an automatic replay.
- Waiting for a callback releases the bounded dispatch worker. Keep one active
  event per scope, fair scope rotation, and at most 16 open scopes. Two shared
  attachment workers preserve manifest order; retained-size accounting is
  incremental with periodic reconciliation. Phase timing logs contain only
  stage durations and terminal status, never message or answer content.
- Pending outbox data is sealed to the exact event, message, scope, answer, and
  delivery plan. Terminal state scrubs answer material and integrity metadata
  according to the durable-state contract.
- Treat `operator.pid` as untrusted. Stop or restart only a verified Python
  process whose command line contains the exact installed `operator_main.py`.
- SessionStart and SessionEnd Hooks manage Operator leases only. The plugin adds
  no UserPromptSubmit or Stop Hook, and no Hook sends a business request or
  submits a final answer.
- `status`, `doctor`, `readiness`, and `validate` are read-only. They
  never start, stop, upgrade, bind, queue, retry, or repair the Operator.
- Focused tests may run only while the exact Operator is verified stopped and no
  callback request is pending. Tests must use isolated temporary state and
  must not contact a live Feishu chat or Codex task.

### Change and publication discipline

- Keep source, installed-runtime inventory, MCP schema, rules mirror, tests, and
  documentation synchronized in one change. The managed block in this file and
  `assets/AGENTS.feishu-codex-operator.md` must remain byte-identical.
- Use the current official CLI to check App Server protocol shapes when they
  change. Generated Schema is version evidence, not permission to mutate tasks.
- Project-local install, upgrade, configuration, lifecycle, Hook, plugin,
  Schema, and read-only diagnostic work may proceed automatically when the
  owner requests it. OAuth, UAC, visible Hook review, publication, credential
  changes, cross-project changes, and destructive work stay within the exact
  user-authorized scope.
- Publish only files in `assets/release-inventory.json`. Never publish
  `.codex`, credentials, tokens, logs, databases, callback contents, local
  paths, caches, attachments, temporary files, or retained evidence.
<!-- FEISHU_CODEX_OPERATOR_RULES_END -->
