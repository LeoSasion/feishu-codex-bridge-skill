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
  queues that Beeper with `gpt-5.6-luna` and `low` reasoning when
  `CODEX_OPERATOR_BEEPER_MODEL` is absent or blank. Explicit selection may use
  the deterministic local `beeper`, Spark, or Luna. Spark with `low`
  reasoning is forbidden in normal selection. A bounded diagnostic may select
  Spark/low only through the explicit Spark model and reasoning overrides.
  These selections apply only to the fixed Beeper task, never the Responder.
  The local `beeper` provider and private model catalog install with the
  Operator runtime. The catalog uses `visibility: list`, but visibility alone
  does not register a provider or change an existing Desktop task's default.
  Desktop dropdown/default integration remains pending supported verification;
  never replace native catalog entries/provider or send Spark/Luna to a third party.
  The optional Python Responses router may preserve native traffic to its fixed
  native backend and append explicit external model registrations. It uses no
  LiteLLM SDK or Chat Completions conversion. Its separate, reversible entry-point
  activation is never an automatic install step. Persistent lifecycle, native
  auxiliary-tool compatibility and live Desktop picker/default verification remain
  required before global activation; isolated tests alone are insufficient.
  Registry v2 may explicitly adapt external Responses tools using per-endpoint
  capabilities. Preserve v1/null passthrough, native traffic and local Beeper.
  An explicit upstream_response_mode=json endpoint contract may select one
  non-streaming upstream request before dispatch and serialize its fully validated
  successful JSON response as downstream SSE/WebSocket. Preserve exact text and
  tool identities, bounded reads, cancellation and rejection before call release.
  This buffers generation; never repair an observed stream, retry a failed request,
  infer the mode from a model name or present it as first-token timing.
  Before emitting JSON-derived events, validate each serialized event JSON against
  16 MiB and their combined UTF-8 bytes against 64 MiB, including repeated snapshots
  and metadata, using actual sequence numbers. Transport framing is outside this
  JSON-byte count. Reject before any event or call release; never truncate to fit.
  Text parts use the event bound; tool arguments retain their separate 2 MiB bound.
  An explicit completed_output_policy=require_message_or_tool endpoint contract
  may reject completed outputs lacking a validated call or nonempty assistant
  text/refusal. Preserve the allow_empty default, all text bytes and native/v1/null
  passthrough. Reasoning-only output never supplies a call or answer; this check
  cannot prove semantic correctness, effective reasoning settings or Desktop
  acceptance, and never triggers repair or retry.
  The adapter only converts protocol data; Desktop retains tool execution and
  permissions. Preserve tool source and call identities; reject unknown tools,
  unsupported grammar, opaque history and incomplete calls without repair or
  retry. Release executable calls only after a consistent successful terminal.
  Compare observed streamed text parts with their terminal snapshots before
  releasing buffered tools; preserve whitespace and reject contradictions without
  trimming, reconstruction or retry. Verification reports retain CLI and Desktop
  failures separately and together, distinguishing failed, missing and stale gates.
  Content parts follow their owning message or reasoning item; reasoning_text
  parts remain distinct from output_text and refusal, with no content conversion.
  Preserve absent/null/empty reasoning content distinctly. Validate textual reasoning
  collections in explicit history and terminal output before dispatch or call release;
  reject malformed collections and explicit unfinished status without repair or retry.
  A null snapshot cannot erase observed streamed text. Legacy reasoning text stays
  its original representation and never supplies an answer or an inferred call.
  New isolated CLI cases may explicitly select marker_line_v1 for bounded LF/CRLF
  lines around the synthetic report only. Preserve raw text, exact comparison,
  tool/file checks and per-case policy; never reclassify old failures or infer
  Desktop acceptance from this formatting compatibility.
  Current Codex named function results may legitimately omit call_id. A dated,
  explicit codec may encode only the registered codex_app.send_message_to_thread
  source as labelled user-message JSON, preserving the entire original object,
  text parts, absent/null identities and order. This is an explicit role/format
  representation, never a reconstructed call, authentication, system/developer
  instruction, new tool permission, XML extraction, execution or retry.
  Ordinary paired calls/results remain strict; opaque/nontext named results and
  unknown sources remain rejected. Endpoint and live Desktop checks are separate.
  Explicit text-only result serialization preserves every part and boundary;
  never flatten images or drop content. Reasoning-specific tool-choice profiles
  are checked before sending. Parallel permission may be narrowed to a supported
  single call, never widened beyond the caller's constraint.
  An explicitly selected additional_tools_v1 input codec may compile the dated
  Codex developer AdditionalTools envelope into the same request tool map.
  Retain its complete source object and input index in request memory; never
  parse ordinary message text into declarations, synthesize a privileged prompt,
  infer custom-tool registrations or bypass deferred loading and tool choice.
  Paired result identities must match their original calls before alias mapping.
  Unfinished historical calls and unsuccessful tool-search outputs are rejected;
  they never enable tools or cause retries. This codec is not a general Responses
  Lite implementation or hosted-search executor; native/v1/null routes stay opaque.
  Synthetic provider probes and isolated CLI execution are distinct evidence;
  neither satisfies live Desktop/global-entry acceptance by itself.
  Desktop verification is a separate read-only review of private dated evidence.
  Bind model identity, endpoint contract, adapter/verifier and CLI/Desktop versions;
  reject stale, ambiguous or altered evidence and keep prior failures visible.
  Reviewed assertions are not attestation. Guided cases cannot establish fresh-context
  acceptance; an assistant claim cannot prove approval or denial. Verification reports
  never modify catalogs, permissions, services or routing, and never imply global readiness.
  A separate explicit stopped label transaction may update only one selected adapted
  model display name after re-reading its current evidence. It must reserve the inactive
  router port, verify the exact Operator stopped and callbacks empty,
  require the preview registry digest, retain an original backup and write atomically.
  It never starts services, refreshes the live UI, broadens capabilities or activates routing.
  Private capability profiles bind the explicit endpoint/model contract and
  adapter revision to dated case outcomes; they are not live attestation.
  Preflight/readiness perform no inference or configuration mutation.
  Isolated CLI evaluation keeps approval checks and zero retries. A read-only
  patch preview never proves actual file-write approval. Keep profiles and
  receipts private. Timing records contain fixed stages/counts only.
  Router restart is explicit, deactivated and request-free; never replay.
  Owner-selected LM Studio startup discovery may append all listed chat models
  under one explicit private Responses policy. Metadata is not capability or
  Desktop acceptance. Exclude embeddings and known dedicated auxiliary draft
  architectures such as gemma4-assistant. Do not infer auxiliary status from
  small size, a display name or a main model having speculative decoding enabled.
  Report already-registered auxiliary rows for separate stopped cleanup; discovery
  never deletes them. Preserve existing rows and defaults,
  mark new rows unverified, and commit the validated batch atomically only
  with the owned entry deactivated, exact router/Operator
  stopped and callbacks empty. Never load, download, infer, transfer receipts,
  delete missing models or change approvals during discovery. First initialization
  must explain current-user Desktop/Start menu shortcut changes, original-file
  retention, taskbar limitations and the safe uninstall sequence before writing.
  An owner-requested initialization may then configure those entry points without
  another prompt. It does not activate global routing or infer a local-model
  policy. Preserve a journal before changing integrations; repeated upgrades
  retain the first original, and restoration verifies current fingerprints,
  backups and exact paths before changing files. Later user edits, linked paths
  or ambiguous ownership stop recovery without overwriting them.
  Safe uninstall first detaches the exact owned request-free router and callback
  registration, then restores managed entry/Hook/rule files and archives runtime
  data. Keep a standalone native launcher for otherwise unobservable taskbar pins.
  Preserve task history, model weights, credentials, unrelated settings and data.
  Do not claim that removing the Desktop plugin automatically runs this recovery;
  complete project recovery before plugin removal. Legacy installations require
  separately reviewed ownership evidence, never a fabricated first-install record.
  Desktop closure is not a policy prerequisite for model registry edits,
  including discovery, auxiliary cleanup and display-name updates. Preserve
  the applicable backup, digest, atomic-write and service lifecycle checks.
  Any remaining restart or refresh requirement must be established from the
  implementation and observed behavior, not inferred from this project rule.
  An explicit registry reload may publish one validated, digest-bound in-memory
  registry while Desktop and the router remain running. It does not authorize
  registry-file edits, model discovery, service control or permission changes.
  Use no polling or per-request file checks; bound reads to 1 MiB off the event
  loop and changed-version attempts to one per 30 seconds. Same-version requests
  are no-ops without disk I/O; concurrent, failed or uncertain attempts never retry.
  Existing requests and entire WebSocket connections retain their original
  registry snapshots. New requests see the new registry; Desktop catalog refresh
  remains separate evidence. Initial deployment into an older router still
  requires an explicit request-free router restart.
  Native Responses HTTP wire and decompressed input are bounded to 64 MiB.
  External/Beeper HTTP input, native auxiliary input and existing WebSocket
  limits remain 16 MiB. Preserve native bytes and encoding headers; never
  truncate input, remove attachments or replay a failed request to fit a bound.
  Local HTTP size rejection reports 413 with a fixed code, scope and limit;
  preserve upstream 413 responses distinctly. These are transport limits,
  not model context allowances or guarantees of upstream acceptance.
  Operator starts one loopback-only Responses API
  listener only when `beeper` is selected and passes its provider/catalog
  settings only to that Beeper queue. It never modifies global Codex config.
  The local provider accepts only model `beeper`, an exact current relay envelope
  with one public 32-hex request_id,
  and exactly one model-facing `exec` custom tool; it deterministically emits the
  four-line bootstrap without model weights or sampling. HTTP and stream retry
  counts are zero. Protocol-external input returns the fixed approved identity declaration
  as assistant text, never a tool call. Older messages cannot supply the current
  relay envelope. Provider failure, queue rejection, timeout, crash, or any
  uncertain local outcome is terminal and never falls back to another model.
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
- If Spark is explicitly selected and a fresh cache says its bucket is
  exhausted, queue Beeper once with `gpt-5.6-luna` and `low` reasoning instead.
  If an explicit Spark queue attempt returns a proven nonzero usage-limit or
  rate-limit rejection, refresh the cache and may make exactly one same-event
  Luna/low queue attempt. This is the only automatic CLI fallback; never fall
  back after timeout, crash, uncertain outcome, an accepted queue, a Luna
  rejection, or any local `beeper` outcome. Beeper still omits model and
  thinking when it sends to the Responder. `CODEX_OPERATOR_BEEPER_MODEL` may be
  blank, `beeper`, Spark, or Luna; blank resolves to Luna/low. A bounded Spark-only diagnostic
  may additionally set `CODEX_OPERATOR_BEEPER_REASONING_EFFORT=low` or `high`, but only
  together with an explicit Spark model override; empty reasoning keeps
  `beeper`/low, Spark/medium, or Luna/low according to the selected model.
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
  Select suites by the changed behavior using the canonical tests/README.md.
  Run full regression for cross-layer changes and before release/deployment;
  prose-only edits need the package audit, not CLI execution. Prefer behavioral
  boundary checks over repeated prose or source-spelling assertions. Keep private
  archives outside test discovery and retain historical failures as evidence.

### Change and publication discipline

- The Desktop search bridge direction is paused. Retain
  `references/retained-desktop-search-idea.md` under the canonical plugin,
  including its concise architecture flow and the README acknowledgment, until
  the owner explicitly requests their deletion. The owner authorized cleanup of
  the obsolete private ChatGPT Web draft code and patch on 2026-09-09; those
  snapshots need not be retained. Routine cleanup, upgrades or refactoring must
  not remove this idea or automatically resume its development, configuration
  or deployment.
  The owner's later 2026-09-09 request resumes ordinary tool protocol adaptation
  with CC Switch as a reference. This does not delete the retained search design
  or automatically enable its hosted-search replacement and MCP bridge draft.
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
