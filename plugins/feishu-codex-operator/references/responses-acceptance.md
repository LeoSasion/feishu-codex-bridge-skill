# Responses registration and acceptance

## Alpha.123: exact probe results and bounded CLI diagnostics

Synthetic probe success now requires the final verification string to match
exactly. `verification_after_trim` remains diagnostic only. Space, LF, CRLF,
tab and nonbreaking-space wrappers fail without repair or a third request.
New completed probe receipts include `checked_at`, `cli_version: "none"`,
`synthetic_only`, and contract/adapter/evaluator SHA-256 bindings. The evaluator
digest covers profiles, CLI evaluation and the probe implementation. Profile
import checks it for every report, including non-CLI cases. All existing probe
case names can be retained in profiles, but none satisfies a required CLI gate.

The no-replay receipt filename and initial `may_have_started_no_retry` record
are unchanged. Incomplete reservations never become evidence. Old receipts are
not backfilled or reclassified; prior evaluator digests remain stale. These
bindings detect version mismatch, not model identity attestation or tampering by
an actor able to rewrite both the report and its self-reported digests.

The disposable CLI evaluator retains `requests` as client attempts and adds
explicit client/admitted/budget-rejected counts, upstream dispatch attempts, and
upstream header-response counts. Dispatch means entering the single HTTP proxy
attempt, not proven provider receipt or successful execution. Each admitted
round may retain a fixed JSON snapshot type-count summary after validation;
the summary contains no text, tool names, call IDs, arguments, URLs or keys.
Its scope is snapshot validation, not event/call release. Native upstream SSE
is not reread: its `json_snapshot` is null. No new business-route logging or
diagnostic endpoint is installed, and no additional HTTP request is made.

The existing extra-round CLI fixture now uses JSON upstream and checks that
an extra client request is refused before upstream dispatch, while preserving
the prior call's type counts. Request caps, exact file checks, stop rules and
historical failures remain unchanged. A new live diagnostic run is separate
evidence; these isolated changes do not resolve a prior model failure by themselves.

Disposable CLI configuration now disables `features.plugins` and
`features.remote_plugin`, confirmed by the installed official CLI 0.153.4
feature listing. The fixture's explicitly configured MCP server remains enabled.
This prevents unrelated plugin discovery/downloads in the isolated home; it does
not disable Desktop plugins or qualify their live integration. The exec CLI
regression stops its exact child before temporary-home cleanup on a timeout.
The original full-suite timeout and Windows file-lock failure remain recorded.

Version-prefixed sections and dated trial results below retain their original
scope. They are historical observations, not a statement that the current source
is installed or that every model/version passed Desktop acceptance. Current
source validation is recorded in [release audit](release-audit.md); recorded
Desktop gates still require matching model, contract and implementation versions.

## Alpha.109: text consistency and readable evidence status

The adapted SSE lane now compares observed UTF-8 text in message, refusal,
reasoning summary and reasoning content parts with text-done, part-done and
item-done snapshots. Duplicate endings, late deltas, changed part identities and
contradictory snapshots stop the stream; no buffered executable tool is released.
Text already streamed to the client cannot be retracted. Unmodified text still
streams immediately, including leading LF, CRLF, tabs and Unicode. Bounded
incremental digests avoid retaining a second copy of reasoning or message text.
No parser correction, whitespace trimming, inference or retry is added.

The combined verification JSON adds `cli_gates` and `recorded_failure_counts`.
`recorded_failures` now totals retained CLI and Desktop failures. The legacy
`missing_cli_checks` remains the list of CLI checks without a recorded pass;
consult `cli_gates` to distinguish failed, unknown, absent and outdated evidence.
Latest passing records do not erase older failures. Existing acceptance criteria
and the separate stopped label transaction still apply.

For the same current registration, profile, ledger and measured version arguments,
append `--format text` to `verification-status`. The output contains gate names,
reasons and counts, never artifact bodies, task text or credentials. JSON remains
the default; text mode is unavailable on mutation commands. The source command
can review records before a stopped runtime upgrade; old adapter/evaluator/verifier
bindings remain visibly stale and are not rewritten to manufacture a pass.

### Leading whitespace diagnosis

When exact final-text checks fail but tool ordering succeeds, preserve both
observations. Inspect the model's active template and reasoning parser through
read-only configuration/template-rendering APIs before changing anything.
A template's `</think>` followed by LF separators and a parser that ends at the
bare tag can explain whitespace entering the answer, but synthetic template
rendering alone does not establish the bytes of a live model response. A scoped
parser change needs its own explicit model contract and live acceptance; never
strip arbitrary response text or mark after-trim comparisons as exact passes.
See [LM Studio prompt templates](https://lmstudio.ai/docs/app/advanced/prompt-template).

## Alpha.108: separate stopped label transaction

`verification-label` previews one exact adapted registration's display name from
current evidence. It reads the row directly from the registry, not from an old
profile or a saved verdict. `--apply` is a separate explicit mutation: reserve
the configured inactive router port, require the owned entry deactivated, verify
the installed router process and the exact Operator stopped, and require empty
callback state. Alpha.122 removes the extra `desktop_running` rejection that
remained through alpha.121. Desktop may stay open for preview and apply; its
installed package version must still match the supplied evidence binding.
This aligns label changes with the existing registry policy and does not remove
Operator/router lifecycle checks or imply live Desktop catalog refresh.
The configured-port reservation prevents selecting a different unused port to bypass
the running router check. It also
checks the installed Desktop package version against the supplied version.

```text
python scripts/operator_model_router.py verification-label --state-dir <private-state> --slug <exact-model-alias> --profile <private-cli-profile.json> --desktop-evidence <private-ledger.json> --cli-version <current-cli-version> --desktop-version <current-desktop-version> --model-sha256 <current-model-manifest-digest>
```

Review `before`, `after`, the verification gates and `registry_sha256`. To apply
after the separately controlled shutdown/deactivation, use the same arguments
with `--apply --expected-registry-sha256 <preview-registry-sha256>` and the exact
configured `--port` if it differs from the default. Apply rereads the evidence;
the preview is not authorization to skip failed or stale checks. Model digest
and CLI version remain caller-measured inputs, not automatic live attestation.

The registry edit uses the existing exclusive edit lock, compares the original
bytes, retains a content-addressed original backup and atomically replaces only
the selected row's display name. Other rows, model contracts, credentials,
defaults and capabilities are unchanged. Repeating an already-applied label is
a no-op. Valid but incomplete, failed or expired evidence recommends unverified
and can demote a previous verified label through this same stopped transaction.
Malformed or altered evidence stops without writing; it must be reviewed rather
than silently repaired. Failed-run history is never removed.

Process conditions are observed before evaluation and again before the commit;
these checks are not an operating-system guarantee against an independently
started application. Use the controlled startup/shutdown workflow. This command
does not stop or start anything, invalidate a live cache, refresh Desktop, enable
global routing, or participate in metadata-only discovery. Labels are snapshots
of reviewed local evidence, not live or official certification. The separate
read-only verification report remains the place to check current validity.

## Alpha.107: explicit local verification report

Alpha.107 also repairs the startup integrity inventory: alpha.106 installed the
LM Studio discovery module but omitted it from the startup guard's expected set.
The guard could therefore reject an otherwise complete runtime. The new install
regression compares the full installed manifest against the startup guard, and
both the discovery and verification modules are now present on every surface.

`verification-init` creates an empty private evidence ledger; `verification-status`
reads it without inference, service control, registry writes or permission changes.
A current isolated CLI profile plus all eight current Desktop gates permits the
report to recommend `[verified]`. This is local recorded-case verification, not
Codex certification or global routing readiness. The dropdown is not modified by
this read-only command; the separately implemented `verification-label` update
must re-evaluate current evidence before using its recommendation. Startup discovery still labels
new models unverified.

```text
python scripts/operator_model_router.py verification-init --registration <private-registration.json> --cli-version <installed-cli-version> --desktop-version <installed-desktop-version> --model-sha256 <local-model-file-sha256> --output <new-private-ledger.json>
python scripts/operator_model_router.py verification-status --registration <current-registration.json> --profile <private-cli-profile.json> --cli-version <installed-cli-version> --desktop-version <installed-desktop-version> --model-sha256 <current-local-model-file-sha256> --desktop-evidence <private-ledger.json>
```

The current `--registration` is required for status; omit the optional `--profile`
to inspect Desktop progress alone, with overall verification false. Model hashes and installed
versions are supplied by the caller, who must measure them rather than invent
values. For split or multi-file models, define and retain a deterministic full
model manifest digest; a single shard does not identify the model. Evidence from
before the model identity was measured cannot retrospectively prove that identity.

Required Desktop gates: fresh-context file edit with unchanged tests and exact
output, fresh-context multiround tools, tool-error stop, nonzero-exit stop,
cancellation, actual owner approval, actual denial without bypass, and task-model
persistence plus tool use after restart. A guided write is supporting evidence
only. Every record has a unique run and exact task/turn IDs, normal workspace
permissions, disabled networking, zero retries, UTC date, case-specific reviewed
assertions, and a SHA-256-pinned adjacent JSON artifact of the observable turn.
Raw tool evidence must substantiate the assertions; an assistant claiming success
or describing its own permissions is insufficient. Reviewers must retain separate
permission observations when those are not represented in the turn export.

The reader validates structure, dates, file boundaries, hashes, turn identity,
observable calls and stop ordering. Assertion truth and origin still require
review; hashes are drift detection, not authentication. A later failure blocks
an earlier pass; a later independent passing case does not erase failed-run
counts. One turn cannot be counted as multiple independent cases. Thirty-day
expiry and changes to model, endpoint contract, adapter, verifier, CLI or Desktop
invalidate the Desktop gates. Historical CLI evaluator binding remains required,
and exact text is never trimmed to convert failures into passes.

The CLI profile reader alone continues to report desktop_verified=false because
that format contains no Desktop evidence. The combined verification report is the
separate authority for recorded Desktop progress. Native auxiliary compatibility,
Operator delivery, login startup and global activation are not implied.


Alpha.100 preserves downstream compression negotiation. A synthetic regression
reproduced alpha.99 adding `gzip, deflate, zstd` when a client omitted
`Accept-Encoding`, which can make an opaque search stream undecodable by that
client. The fix suppresses the HTTP library's automatic header, preserving
explicit identity/gzip requests and their response bytes. Live search recovery
must be verified after deployment; the synthetic result alone does not establish
the cause of a Desktop error.

Alpha.99 adds portable capability profiles, offline preflight, an isolated CLI
evaluation command, explicit router restart, read-only readiness and content-free
timings. The native and Beeper routing contracts are unchanged. These additions
do not install a global entry or change a saved task's model.

## Capability profiles

A version-2 private profile contains one complete v2 registration, its contract SHA-256,
the adapter and evaluator source SHA-256 values and a bounded list of dated case outcomes and CLI
versions. The contract binds endpoint, model, context, reasoning and every
capability. Display name, alias and the environment-variable name for a key may
be changed without invalidating the contract. The key value is never included.
Changing adapter or evaluator source marks evidence stale. Version-1 profiles
remain readable historical records, but have no evaluator binding and cannot
establish current verification. These hashes detect configuration
drift; they are not authentication or proof that a provider has not changed.
Records describe past runs, not a new network check or universal model support.

```text
python scripts/operator_model_router.py preflight --registration <private-registration.json>
python scripts/operator_model_router.py preflight --profile <private-profile.json> --cli-version <current-cli-version>
python scripts/operator_model_router.py preflight --registration <private-registration.json> --request <synthetic-request.json>
```

Preflight performs no inference, credential lookup or registry mutation. It
validates the complete registration and checks that every declared reasoning
effort can both call tools with auto and finish with none. An optional synthetic
request exercises the adapter's actual capability checks, including unsupported
built-ins, opaque history and named/required combinations. The report states
the required client settings: disable web_search, use full explicit history and
set both retry counts to zero. It never changes those settings for an existing
task. Generic adapted registration also runs preflight before append.
Registration reserves the configured loopback port during the write, refusing
any existing listener. This avoids mistaking Windows connection timeouts for a
stopped service and prevents a start on that port during the registration edit.
Supply the same explicit port used by that state directory's router.

```text
python scripts/operator_model_router.py profile-build --profile-id <portable-id> --registration <private-registration.json> --evidence <private-receipt.json> --evidence <another-private-receipt.json> --output <new-private-profile.json>
python scripts/operator_model_router.py register-model --state-dir <private-state> --profile <private-profile.json>
```

Profile building accepts only records bound to the exact current contract and
adapter; every CLI record must also match the current evaluator. It strips
records to fixed metadata fields, retains the evaluator binding, and refuses to overwrite an
existing profile. Keep failed cases as well as subsequent independent runs.
`isolated_cli_verified` requires passing nested, multiround, tool-error recovery,
tool-error stop, nonzero-exit stop, read-only patch-plan and cancellation records.
Supplying the current CLI version
filters out other CLI versions. `desktop_verified` remains false; write-tool
approval is separately reported. Partial/stale profiles remain configuration
examples, not deployment approval. No live receipts or private profiles belong
in the release inventory.

## Auxiliary model discovery filtering (alpha.112)

Native LM Studio metadata may classify a dedicated MTP drafter as `type: llm`.
The observed Gemma drafter uses `architecture: gemma4-assistant`; it must not be
newly registered as a standalone chat model. Discovery excludes this known
architecture using metadata, without inferring from display names or parameter
counts. Other architectures retain the existing explicit discovery policy.
Small ordinary chat models and main models with MTP enabled are not excluded.

An auxiliary registration already present in the private registry is reported in
`excluded_existing_slugs` and left byte-for-byte intact by discovery. Removing it
is a separate stopped, backed-up operation, not deletion of a model file or an
automatic consequence of a missing catalog row. API access to the draft model
does not pair it with a main model or activate speculative decoding.

## Reasoning content-part compatibility (alpha.111)

LM Studio emits `response.content_part.added/done` for `reasoning_text` within
a reasoning output item. Alpha.109/110 incorrectly classified those events as
message-only and rejected them before tool release. Alpha.111 validates content
parts against their owning item: reasoning accepts reasoning text, while messages
accept output text or refusal. Summary events remain distinct. Every original
event and text boundary is preserved; incompatible types and snapshot mismatches
still fail closed. Fixed text-error categories are now included in diagnostics
without retaining provider text.

The first alpha.110 live CLI case stopped before any fixture execution. A separate
one-request, no-tool shape diagnostic identified `text_event_item_type_mismatch`.
Neither request was replayed. Corrected-version acceptance uses new disposable
fixtures and dated receipts, preserving earlier failures.

The corrected Qwen3.8 run passed all seven core isolated CLI cases using current
Desktop CLI 0.153.4 and `marker_line_v1`: 17 model requests, zero retries, exact
fixture sequences, both stop cases without subsequent fixture calls, unchanged
source in the patch-preview case, and observed cancellation. Six final reports
retained their two leading LF and still failed the separate exact-text comparison.
This establishes the selected model's scoped CLI result only. Desktop gates remain
pending; no family-wide capability inheritance or label promotion is performed.

## Cross-model final-report compatibility (alpha.110)

For a **new** isolated CLI case, `operator_responses_eval.py run` accepts
`--final-text-policy marker_line_v1`. This policy is model-independent and opt-in;
the default remains `exact`. Its prompt explicitly permits zero through eight
empty LF or CRLF lines on either side of the complete synthetic marker or STOPPED
report. Spaces, tabs, bare CR, extra text, repeated markers, internal changes and
more than eight boundary line endings fail. It never modifies the model's text,
tool arguments, file bytes, parser template, route or permissions.

Reports retain both `verification_exact` and `verification_accepted`, and record
the selected `final_text_policy`. Tool order, request budgets, error-stop behavior
and file checks remain required. Profiles preserve that policy per case and the
readable status displays it. Compatibility success is not an exact-text claim.
Changing the evaluator invalidates old evaluator bindings; old failures and raw
hashes remain unchanged and cannot be reclassified with this option. Fresh cases
are necessary. The mode neither removes visible blank lines nor establishes
Desktop approval, tool execution, model identity or global readiness.

## Bounded CLI evaluation

`cli_powershell` and `cli_bash` are additional, explicitly selected terminal
cases. Supply `--terminal-shell <absolute-executable-path>` with a standard-tool,
JSON-upstream registration. The harness does not discover or change Desktop's
terminal. It prepends only that executable's directory to the disposable CLI
child's PATH; the tool argument is preserved exactly. The requested executable
SHA256 is recorded, but is not proof of the process ultimately chosen by the CLI.

Each case admits one exact native `exec_command` call with `login=false`, a fixed
work directory and no permission override. PowerShell uses the native
`Get-Content -AsByteStream -LiteralPath`; Bash uses `cat --`. The
fixtures include spaces, apostrophes, Chinese paths, shell metacharacters and
literal backslashes as file data, plus LF, CRLF and BOM bytes in one input file. No arbitrary
model-authored script is executed. The harness checks the paired native result,
successful process status, exact expected console output, final marker and
unchanged source bytes. PowerShell's `decimal_bytes_v1` representation emits
ASCII decimal byte lines, including the BOM and original CR/LF bytes. Either
LF or CRLF may separate these numbers; this grammar never normalizes file data
or establishes Unicode console-text compatibility. Bash retains `utf8_text_v1`.
Both current `Output:` and legacy `Final output:` native envelopes are parsed
with an anchored grammar. A missing, nonzero or ambiguous exit status cannot pass.

On Windows, `--windows-sandbox unelevated` explicitly selects the native
restricted-token backend only for this disposable terminal evaluation. It is
never selected after a failure, never changes a user configuration or approval
policy, and does not run elevated setup. `--ignore-user-config` otherwise omits
the user's backend selection. The CLI home remains private; the separate new
synthetic work directory uses normal permission inheritance. The harness does
not change existing directory ACLs or bypass a native restriction. Nested
restricted-token creation can fail; retain that failure separately from a
policy refusal, a process failure and a byte mismatch.

There are at most two client model requests, zero retries and no extra tool
calls. A native policy rejection is a failed terminal case and is classified
separately from malformed arguments, process failures or differing output. Its
follow-up is stopped locally before another upstream dispatch. Permissions are
never expanded to obtain a passing result. Terminal reports and profile entries
retain their requested shell and Windows-backend identities; they do not satisfy core workflow checks,
Desktop terminal selection, arbitrary shell execution or write-approval gates.

```text
python scripts/operator_responses_eval.py run --registration <private-registration.json> --cli <current-desktop-cli> --case cli_nested --receipt-dir <private-receipts> --run-id <unique-case-run>
```

The executable must be explicitly resolved from the current Desktop installation.
Each run reserves a receipt before inference, uses a disposable Codex home and
work directory, ignores user configuration/rules, retains the read-only sandbox
and approval checks, disables web_search and analytics in that child, and uses
zero request/stream retries. Credentials are retained only by the router parent;
the CLI/MCP children do not receive inference keys. No saved task is created,
read, resumed or used to transport an Operator answer. Child output is bounded
to 16 MiB per pipe and retained only in memory during verification. Alpha.105
compares the CLI's disposable final-message file byte-for-byte as UTF-8 text;
console formatting is not the answer. A separate after-trim diagnostic never
turns a whitespace mismatch into a pass. The artifact is removed with the
disposable fixture and is never a business-answer transport.

| Case | Maximum model requests | Verified property |
|---|---:|---|
| cli_nested | 2 | exec invokes the synthetic MCP tool; final random marker matches |
| cli_multiround | 3 | A second call consumes the exact challenge returned by the first |
| cli_tool_error | 3 | The model consumes an intentional isError result and supplies its recovery value |
| cli_error_stop | 2 | One intentional isError failure, no further fixture call, final report includes the error and marker |
| cli_exit_stop | 2 | A successful wrapper contains exit_code=1; the model stops and reports the failure |
| cli_patchplan | 4 | Read an existing temporary file, propose a minimal patch in memory, verify all resulting bytes |
| cli_cancel | 1 | After upstream headers arrive, terminating the exact CLI child closes the local upstream connection |
| cli_workspace | 4 | Separate actual-write case; under the retained never-approve policy this may be refused |

Patch-plan verification compares the entire proposed source, including Unicode
and CRLF, and confirms the original file stayed unchanged. It does not claim
native apply_patch/exec_command or file-write approval has passed. The synthetic
MCP never executes model-authored Python. The actual-write case is not replaced
by a falsely read-only annotation or relaxed approval configuration.

Stop cases require a completed CLI, a final message, and the expected request
count before stopping can be verified. An unfinished run with one observed
fixture call is insufficient. They reject and audit every subsequent fixture operation, including a
second operation inside the same exec. Reporting the final marker alone fails;
the failure must be included. These checks observe the synthetic fixture only,
not every possible Desktop tool. They neither repair model code nor enforce a
production task's permissions. Transport retries and model-generated additional
tool calls are separate counts. Older profiles missing either stop case remain
incomplete, and historical trimmed-marker results are not exact-text evidence.

Cancellation proves local connection cleanup, not that a remote provider stopped
billing or generation. A cancelled or uncertain run is never reused. Start a new
run only for a distinct, explicitly intended diagnostic or changed implementation.

## Service and Desktop readiness

```text
python scripts/operator_model_router.py readiness --state-dir <private-state> --codex-config <exact-config.toml>
python scripts/operator_model_router.py restart --state-dir <private-state>
```

Readiness only inspects local configuration and the authenticated loopback health
endpoint. It never starts, repairs, queues, registers, activates or sends upstream.
It reports unknown Desktop picker/default/native-auxiliary gates honestly.
Restart is explicit, requires deactivation and no in-flight requests, acknowledges
one stop, proves the exact loopback port was released, then starts once. It retains
the token and registry and never stores or replays an inference request. Detached
start/stop/restart and owned configuration rollback have isolated process tests.
There is no Windows login startup or automatic crash restart in this release.

Complete these remaining Desktop checks in a controlled interactive session:

1. Close Desktop, activate the explicit private router entry, restart Desktop,
   and confirm native models and the intended external aliases in the picker.
2. Confirm selection/default persistence through exit and restart without changing
   the fixed Operator Beeper or any mapped Responder's model.
3. Exercise the approved native search/image auxiliary tools and native HTTP/WS
   route, keeping native credentials on the fixed native backend.
4. Exercise file-write approvals in a disposable task; do not weaken them.
5. Close Desktop, deactivate, restart and verify native configuration restoration.

The current implementation must not be globally activated merely because the
isolated suite passed. Desktop UI, native auxiliary tools and login/crash lifecycle
remain separate acceptance requirements.

## Timing interpretation

`status` exposes bounded process-local aggregate counts and min/max/last/total
milliseconds for request adaptation, response adaptation, upstream headers,
first byte, body consumption and total handler time, plus completed/failed/cancelled
counts and active handlers. There are no prompts, results, headers, URLs, model
names, task IDs, call IDs or arbitrary exception text in these metrics. Restart
clears them. Stage labels are fixed, so cardinality does not grow with traffic.

First byte is not first model token. Body consumption includes downstream
backpressure, and stage totals overlap; do not sum them. WebSocket total time is
connection lifetime. Router timings cannot measure Desktop tool execution.
The isolated evaluator separately reports fixture operation time, client startup,
gaps between model requests, and combined client/harness shutdown. These gaps
include CLI processing and tool transport, not only the fixture's own CPU time.

The first Unicode patch-plan run exposed a Windows fixture stdio encoding defect:
legacy encoding corrupted non-ASCII JSON and produced 11–31 second tool waits.
Using explicit UTF-8 bytes fixed it. DeepSeek and GLM then completed all four
requests with exact patch verification and millisecond-scale between-request
gaps. Earlier failed receipts remain private and are not model-quality failures.

## Live snapshot on 2026-09-06

Current Desktop CLI 0.153.4 completed all five isolated cases for each exact
configuration below. The profiles deliberately expose only auto/none, serial
calls, text input and wrapped exec, with text-part results serialized explicitly.
Other features and reasoning levels are not inferred from these successes.

| Endpoint/model | Effort | Nested | Multiround | Tool error | Patch plan | Local cancellation |
|---|---|---|---|---|---|---|
| DeepSeek / deepseek-v4-flash | none | pass | pass | pass | pass | pass |
| BigModel / glm-5.3-flash | low | pass | pass | pass | pass | pass |
| LM Studio / qwen3.6-27b-neo-code-here-2t-ot | low | pass | pass | pass | pass | pass |

The successful patch-plan samples took approximately 10.0, 19.5 and 9.6 seconds
end-to-end respectively. Adapter CPU totals were approximately 8.5, 6.6 and
14.0 ms. These are single diagnostic samples, not a speed ranking or throughput
benchmark. Client startup/shutdown and provider waits contribute to total time.
All cases used their configured request budgets with no retry. Earlier Unicode
fixture failures remain alongside the corrected runs in the private profiles.

At the end of the initial alpha.99 development run, readiness found the global
entry deactivated, no installed external models and the optional router stopped.
That development run did not change a runtime, task default or global entry.

## Controlled Desktop trial on 2026-09-06

The owner subsequently authorized installation and a reversible LM Studio trial.
Alpha.99 was installed, LM Studio registered, and detached start/restart verified.
After the owner closed Desktop and explicitly enabled the prepared trial entry,
the restarted Desktop fetched the augmented catalog. The owner confirmed seeing
`LM Studio Qwen3.6 27B` in its model picker. This establishes picker visibility,
not default persistence, native auxiliary compatibility or actual file approvals.

Native search returned a stream decoding error during this trial. The proxy
reported no upstream HTTP error, which does not establish semantic success.
Alpha.100 corrects an independently reproduced compression-negotiation defect;
the alpha.99 service was subsequently replaced while Desktop was closed.
Failed live observations are retained alongside the recovery check below.

The [official Codex configuration schema](https://developers.openai.com/codex/config-schema.json)
places request/stream retry limits on explicit provider definitions and states
that built-in provider IDs cannot be overridden. A disposable external-model
test configuration must therefore use its own explicit provider with zero
retries and disabled search. It must not replace the native provider or change
an existing business task. Native-picker visibility and the separately
configured external test task are distinct checks.

In a bounded current-CLI configuration read, the disposable project's model,
search and permission settings were effective, while its provider selection and
provider retry fields were absent. The same fields were effective in an isolated
user-level configuration control. No inference or task methods were used by this
comparison. Keep a provider candidate inactive until a supported Desktop scope
can load its required retry settings; a TOML file alone is not sufficient proof.

The alpha.100 LM Studio supplement passed nested execution, intentional tool
error handling, patch-plan verification and local cancellation. Its multiround
case completed both expected fixture operations but requested another model
round beyond the three-request limit. The fourth attempt was rejected before
upstream forwarding; the case is failed and was not rerun automatically. Its
new private profile retains four passes and one failure, so it does not report
the full isolated CLI set as verified. Alpha.99 observations remain historical.

The first manual update restored the native entry and stopped alpha.99, then
failed because its private launcher used Windows PowerShell 5.1. Its default
file decoder produced two syntax errors in the UTF-8 installer driver; explicit
UTF-8 parsing and PowerShell 7 both produced none. The private launchers now
select installed PowerShell 7 and reject older versions before configuration
changes. The canonical installer then completed under PowerShell 7, installing
alpha.100 with matching runtime hashes and preserving environment/session/state
files. Native search succeeded in a deactivated control before the next restart.

## Native search recovery on 2026-09-07

The owner ran the repaired launcher and restarted Desktop with the alpha.100
entry active. Native search and subsequent webpage reads succeeded through the
router without the earlier response-decoding error. Read-only status reported
the service ready with zero failures, and readiness confirmed the owned entry
active. This establishes the bounded native search recovery; image auxiliary
tools, default persistence, actual external Desktop file execution and Windows
login/crash lifecycle remain unverified. The readiness command still reports
unknown live gates because it does not ingest historical acceptance receipts.

The LM Studio process had no loaded model after this restart. The existing
registered model was loaded again and the loopback API server started; loading
alone does not establish inference or restart persistence.

The [official configuration reference](https://learn.chatgpt.com/zh-Hans/docs/config-file/config-reference)
confirms that project configuration ignores provider definitions and selection.
The [official profile documentation](https://learn.chatgpt.com/zh-Hans/docs/config-file/config-advanced)
documents CLI selection, which is not evidence of a Desktop task-level selector.
Read-only inspection of the installed Desktop's task-construction code did not
establish a supported external provider/profile selection surface. Keep the
prepared external candidate inactive pending a verified task scope; do not
replace the native provider or treat catalog visibility as retry-policy proof.

## Independent provider persistence experiment

A subsequent CLI 0.153.4 experiment used disposable Codex homes and static
loopback Responses fixtures. An explicitly selected task provider survived
closing and reopening App Server with `modelProvider: null`, both with an
implicit OpenAI default and with `model_provider = "openai"` in user config.
The additional provider definition remained in that user's configuration; the
native default was preserved. After resume, separate simulated HTTP 503 and
truncated-stream cases each made one request and failed without retry.
This establishes a possible configured-task handoff, not live Desktop execution.

An empty task did not survive the same close/resume sequence, even after naming
and unsubscribing. The prepared controlled workflow therefore initializes one
separate task with a bounded text-only CLI turn before asking Desktop to execute
the actual file test. It requires explicit task creation, retains normal
on-request approval and the disposable workspace, and never changes native
defaults, Beeper or a mapped Responder. The private setup script reserves a
receipt before creation, stops on unexpected tools/approvals or uncertainty,
and refuses a second creation attempt. A static fixture verified its initializer
and duplicate guard without model inference or a live Desktop task.

Only the additional user-level provider definition and disposable workspace
trust were installed for this trial. Effective configuration reads confirmed
zero provider retries, disabled workspace search and normal workspace approval;
the native default model, provider and router entry were unchanged. Reversal
owns an exact marked block and preserves unrelated configuration edits.
Actual Desktop tool execution, its loaded provider settings and persistence
remain required live checks; a successful synthetic setup does not satisfy them.

## First independent Desktop turn on 2026-09-07

After explicit owner authorization, one separate LM Studio task was initialized
with a tool-free CLI turn and returned the required marker. Desktop opened that
exact task and accepted one file-test prompt. The request failed before upstream
forwarding with `reasoning_summary_not_supported`; no file operation occurred and
the task was not replayed. Desktop's diagnostic for that turn recorded
`reasoningSummaryOverride=detailed`, `summary=detailed`, on-request approval and
workspace-write. Its parallel-summary feature overrides the model catalog's
default summary setting. Desktop also supplied an additional writable root;
the initializer's workspace-only scope is not proof of the later Desktop scope.

A separate tool-free loopback diagnostic found that the installed LM Studio
accepted and echoed `summary: detailed`, while returning empty summary parts.
A private candidate then enabled unchanged summary-parameter passthrough and
passed a two-request synthetic SSE tool/result roundtrip with no tool execution
or retry. This proves bounded parameter and protocol compatibility, not summary
generation or actual Desktop file execution. No adapter request field was
dropped and no summary was fabricated. The candidate changes the contract digest;
earlier CLI profiles are not verification of that changed contract.

The active registration remains unchanged until an explicit stopped cutover.
The prepared private updater verifies the candidate/evidence/adapter digests,
requires Desktop and Operator closed plus no pending callbacks, deactivates the
entry, stops the request-free router and reserves its port, backs up the exact
old registration, and changes only the reviewed summary capability. It then
starts the router and restores the owned trial entry without sending a task.
This remains a controlled trial, with actual file execution and other global
activation gates pending.

## Follow-up after the summary cutover

The owner completed the summary registration cutover and restarted Desktop.
Read-only checks confirmed the exact new registry and the additional zero-retry
provider. LM Studio again had no loaded model and was explicitly loaded before
the next case. One new Desktop file case failed with `invalid_protocol_string`
and no tool marker or file change. It was not replayed. The generic alpha.100
error does not identify its field, so the exact triggering value is unconfirmed.

Isolated current-CLI fixtures with code mode, synthetic direct namespaces and
multi-agent tool definitions did not reproduce that failure. Separate protocol
tests did reproduce alpha.100 rejecting null optional tool documentation.
Alpha.101 accepts that absent documentation, preserves original metadata and
call identities, still rejects non-string descriptions and null executable
values, and adds a fixed schema-field label to request string errors. This is a
verified compatibility improvement, not proof of the live failure's root cause.

A private external candidate explicitly adds `functions.exec: wrap` alongside
the existing bare `exec` registration. With null namespace/tool descriptions
and `summary: detailed`, its synthetic SSE call/result roundtrip passed in two
requests without executing the generated source. The grouped spelling is
explicitly registered rather than inferred for unknown tools. The candidate
does not change native traffic or widen caller tool permissions. Its changed
adapter/contract hashes invalidate previous profile currency.

The alpha.101 suite and current-CLI supplements cover 291 test cases, with the
two Windows symlink cases skipped. A catalog supplement initially failed because
its fixture already contained appended Beeper/external rows; an isolated native
catalog control corrected that setup and passed. The 86-file release audit also
passed. The installed alpha.100 service remains unchanged while Desktop is open;
a guarded stopped upgrade is prepared, and live Desktop acceptance remains open.

## Manual Desktop turn and history rejection on 2026-09-07

After alpha.101 installation, a forwarded Desktop case reported the fixed
`protocol.identifier` error. Structural inspection of only the disposable task
found named delegation outputs without call IDs; a synthetic same-shape preflight
reproduced that rejection. The original task was preserved. A supported bounded
fork through its completed initialization created a clean acceptance context,
without starting a turn, inference or modifying native configuration.

The owner then entered the file prompt directly in Desktop. Two custom `exec`
calls reached Desktop and returned JavaScript errors: Python source was not valid
JavaScript, followed by use of unavailable Node `require`. Neither read or changed
the fixture files. The following request failed with `undeclared_tool_call`.
The adapter rejects this in historical-call lookup, before forwarding; the exact
current-definition mismatch is not established from the available record.

The actual manual turn used `never` and `danger-full-access`, despite the fork's
initial on-request/workspace-write configuration. Desktop logs show its default
permission selection taking effect. This is not evidence of normal file approval,
and no subsequent live test should claim those initial settings persisted.

Two isolated current-CLI fixtures reproduced the two code errors and completed a
third synthetic response, both with and without concurrent reasoning summaries.
Neither reproduced the undeclared-tool failure; no model inference or live task
was contacted. Alpha.102 adds only fixed history-mismatch error categories to
support a subsequent controlled diagnosis. It does not repair executable source,
widen unknown-tool handling, or establish successful live file execution.

## Empty current tool catalog confirmed after alpha.102

The owner installed alpha.102 and manually sent a separate non-file diagnostic.
It failed before inference with `param: input.tool_call` and the fixed category
`history_tool_definitions_empty`. The request therefore contained prior custom
calls while no current tool definitions were available. No diagnostic exec call
ran. The adapter must distinguish explicitly registered historical codec data
from permission to issue a new call; the caller's reason for supplying no tools
has not been established and is not overridden.

Alpha.103 prepares opt-in history codecs for exact registered exec identities.
The current tool list stays empty; original source and call/result identities
remain intact, unknown or incomplete history stays rejected, and the response
cannot introduce executable calls. Endpoint probes and isolated tests are distinct
from the still-required actual Desktop file/approval acceptance.

The explicit private candidate passed one streamed LM Studio request with empty
current tools, `tool_choice: none`, and two synthetic failed exec calls/results.
It returned the required text marker without any executable output. A separate
read-only preflight using the stored disposable history reproduced the old
empty-definition rejection and passed with the candidate; source, call IDs and
complete result parts remained unchanged. This was not a raw request capture.
The alpha.103 suite completed 296 tests with two Windows symlink skips, and the
86-file release/source checks passed. The active runtime and registry are not
changed until the owner completes the guarded stopped cutover.

## Actual Desktop file execution after alpha.103

The owner installed the exact empty-history contract and restarted Desktop.
One manual text turn completed without a protocol error but stopped correctly
because Desktop still selected never/danger-full-access. It made no tool call.
After the owner selected normal approval in the UI, a fresh manual turn used
on-request/workspace-write with network disabled and the external model.
Desktop read both fixture files, changed only the average divisor from a constant
to the input length, and ran three existing tests: three passed, zero failed or
skipped, exit code zero. Byte comparisons confirmed the exact intended change;
test/rule/config files stayed unchanged and the parent did not make the repair.

Retain the quality failures: an incorrect test path, a shell write with exit 1
whose wrapper incorrectly printed success, and three rejected patch formats
before a valid patch completed. This is successful eventual file execution, not
an error-free workflow or reliable tool-result reporting throughout. No separate
approval prompt or denial was exercised. A context-compaction event was present,
but its backend attribution was not established. Default persistence, native
image auxiliary paths, lifecycle and cross-task execution remain separate gates.

## Named result schema correction and alpha.104 preparation

A read-only schema export from current official CLI 0.153.4 establishes that
`FunctionCallOutputResponseItem.call_id` is optional and nullable. Thus the earlier
statement that every named delegation output was malformed is too broad: the
adapter lacked this legitimate Codex schema form. Its paired-call validation
must remain strict. A separate raw LM Studio request containing a synthetic
named output received HTTP 400 in one attempt; no tool ran and no retry occurred.

Alpha.104 provides an explicit, default-off codec for this exact source. It
encodes the complete original named result as labelled user-message JSON, with
no XML extraction, generated ID or new tool. This is a declared representation
change, not native named-result compatibility or source authentication. Unknown
sources, opaque/nontext outputs and malformed or unmatched paired IDs remain
errors. Exact schema evidence and probe receipts stay private; installation and
actual Desktop delegation must be separately verified after a stopped cutover.

The first codec probe passed absent-ID strings and null-ID text-part arrays.
Stored-record preflight then identified plain turn/timestamp metadata not present
in that synthetic payload; the strict field allowlist rejected it. The revised
codec explicitly validates and preserves those two correlation fields and still
rejects unknown metadata. Two fresh metadata-specific LM Studio cases passed,
one request each, with complete text markers after trimming and no executable
output. All three stored named-result objects from the exact original disposable
task roundtripped unchanged under the candidate; the old registration rejected
them. That inspection covered stored result objects, not a raw live request.
Earlier probe receipts retain their original adapter digests and remain
historical. The final source suite includes 300 tests with two Windows symlink
skips; source/release checks cover the same 86-file inventory. No live task was
sent and no active registration was changed during this preparation.

## Actual alpha.104 delegation and alpha.105 evaluation corrections

After the owner installed alpha.104 and restarted Desktop, one fresh text-only
delegation completed under on-request/workspace-write with networking disabled.
It produced the correct marker with two leading LF characters and no tool call.
The protocol roundtrip passed; strict text acceptance failed. The original text
was retained, with no trim or replay. The task model survived the restart; this
does not establish global model defaults or complete provider persistence.

A separate delegated read-only hash check then executed actual Desktop commands.
The first command failed with exit code 1 due to PowerShell argument syntax.
Despite an explicit stop-on-error and single-command instruction, the model
issued a second command, which succeeded and returned the two expected hashes.
The files were unchanged. Its final report omitted the initial failure. Record
this as tool roundtrip success but failed stop-on-error and reporting acceptance,
not a successful strict run. The parent sent once and did not replay the case.

Alpha.105 adds the two explicit stop cases and corrects final-text verification,
which previously trimmed console output before setting verification_exact.
Profiles and CLI reports bind the evaluator revision as well as the protocol
adapter. Existing unbound profiles remain historical and incomplete.
Protocol conversion, model code, active registrations, approvals, and retries
are unchanged. These evaluation improvements do not fix the observed model
behavior or make the active alpha.104 runtime globally ready.

Final alpha.105 regression ran 305 tests with no failures and two Windows
symlink skips. The current-CLI fake-upstream cases cover a second fixture call
inside one exec, an extra model round without another fixture operation, omitted
failure text, and both leading and trailing LF. Release/source checks cover the
same 86-file inventory; the managed rule mirrors are unchanged.

Two new local-model baselines were each run once. The tool-error case requested
a third client round beyond the two-request budget and produced no final message;
only two requests reached the model. Its initial receipt overstated stopping
based on a single fixture call. The receipt is preserved with a correction note,
and the revised evaluator now requires completion before verifying a stop.
That earlier case has no current evaluator binding and was not rerun. Under the
final evaluator, the nonzero-exit case made one fixture call, stopped, and included
the failure in its report. Two leading LF characters still failed exact-text
verification. These are isolated CLI observations, not new Desktop acceptance
or proof of general instruction compliance. Neither case changed active routing
or justified global activation.

## Qwen3.8 comparison and alpha.106 discovery preparation

One bounded local comparison used the separately selected Qwen3.8 model and
the current alpha.105 evaluator under official CLI 0.153.4. Six tool cases each
followed the expected fixture sequence; both stop-on-error cases completed,
stopped without another fixture operation and reported the failure. All six
final messages contained exactly two leading LF characters, so all six failed
strict exact-text acceptance despite correct after-trim markers. A seventh
cancellation case passed. Each case ran once with zero HTTP/stream retries.
These isolated CLI observations do not establish actual Desktop file-write
approval, cross-task reliability or general instruction compliance. The earlier
Qwen3.6 failures remain preserved, and its active registration is unchanged.

Alpha.106 adds metadata-only batch discovery under an explicit shared policy.
New rows are labelled unverified; registration never transfers another model's
receipts or starts an acceptance task. Read-only preview found the existing
Qwen3.6 route, one new Qwen3.8 route and one excluded embedding model. Both chat
models reported exact loaded instances with 262144-token contexts at that time.
At that preview stage, applying the batch and checking the next Desktop dropdown
were still pending. The following 2026-09-08 record documents their completion
for that specific startup trial; it does not supply current-version acceptance.

## Completed alpha.106 startup and Qwen3.8 picker confirmation

On 2026-09-08, the owner exited Desktop and ran the prepared startup entry.
Its explicit local-service step started LM Studio on loopback, then discovery
found two chat models and excluded one embedding model. The stopped cutover
installed alpha.106, appended one Qwen3.8 registration and reopened Desktop.
Read-only verification matched all 30 installed runtime files against the
reviewed target, preserved the previous Qwen3.6 row and verified unchanged
Operator configuration, scope bindings, inbox database and callback database.
The router reported ready with two adapted registrations. Desktop's new model
cache included Qwen3.8 and the owner separately confirmed it in the dropdown.

This establishes the guarded startup upgrade, actual batch registration and
visible Qwen3.8 picker entry. It does not exercise Qwen3.8 tools in Desktop,
verify a new task default or establish native auxiliary compatibility. The
new model keeps its unverified label and inherits no other model's receipts.
Earlier failed launches and the stopped background-worker attempt remain
historical records; none sent or replayed a business task.

## Actual Qwen3.8 Desktop tool cases, 2026-09-08

Three distinct cases were sent once each to the existing disposable Desktop
task with the explicit Qwen3.8 selection. The workspace's on-request approval,
workspace-write sandbox and disabled networking configuration remained unchanged.
The first case executed exactly one command, returned the requested marker and
exit 7, stopped without another command and accurately reported the failure.

The second case made four successful command calls: target absence check,
exclusive-scope file creation, readback/hash, and one run of the existing tests.
All three tests passed. Its generated PowerShell single-quoted string wrote
literal backtick-n instead of the requested LF, so precise file-content acceptance
failed despite zero command exit codes. The final answer accurately disclosed
the mismatch. The failed sample remains unchanged; no repair or replay occurred.

A separate third case received two explicit byte-based commands. Both executed
once with exit 0; the readback assertion printed BYTE_MATCH and the matching
SHA-256. Independent filesystem verification confirmed the expected bytes and
only the authorized new file added outside Git metadata in that case. Additional
Git object files are recorded separately in the private receipt. Original code, tests,
instructions, workspace configuration and the prior failed sample were unchanged.
This validates following that supplied recipe, not corrected free-form generation.

Observable evidence came from the current official CLI's read-only full-turn
projection for the exact acceptance task because the app summary omitted items.
It did not start or resume tasks. Raw observable outputs and leading LF in final
text are retained privately; no strict final-format pass is claimed. These cases
did not exercise an approval prompt or denial, new-task defaults, or native
auxiliary tools. The registration keeps its unverified label and no other model's
receipts are transferred. This round changes acceptance documentation only;
runtime adaptation, routing, approvals and retry behavior are unchanged.

## Windows file-write guidance for local-model tasks

Supply these requirements visibly in the Desktop task when precise file content
matters. They are task instructions, not router-injected prompts or additional
tool permissions. They apply to the authorized files only and do not change
Desktop approval or sandbox handling.

- State the encoding, BOM policy, line endings and whether the final line has
  a terminator. On Windows, prefer Python byte I/O for exact UTF-8/LF content.
  PowerShell single-quoted strings treat backtick-n literally.
- For a new output, use exclusive creation and stop if the path already exists.
  For an existing file, keep the edit within the explicitly requested scope.
- Read the saved bytes back from disk and compare with the specified content.
  An exit code of zero alone is insufficient; a hash is useful only alongside
  a content assertion or independently known expected hash.
- Treat a content assertion failure like a command failure. Stop and report the
  observed result; do not overwrite the sample, retry, or weaken permissions.
- Report actual exits, verification results and test results separately. A
  passing test suite does not validate an unrelated output file's contents.

This guidance addresses an observed command-generation error. The adapter's
tool-restoration path validates framing and preserves source strings; it does
not interpret shell quoting, repair generated commands, or trim final text.
One passing guided case cannot establish general model reliability.

A subsequent actual Desktop case applied these visible principles to a new
two-line Chinese text file, without supplying complete commands in that turn.
Qwen3.8 generated two Python commands, each executed once with exit 0. Independent
verification matched all 48 bytes against the original requirement, including
UTF-8 without BOM and two LF terminators. The model's readback assertion and hash
also matched. Existing files and permission configuration were unchanged; only
the authorized output appeared outside separately recorded Git object additions.
This was the same disposable task with the earlier byte recipe in its history,
not a clean-context test. It supports this guided workflow only. Earlier failures
and leading-LF final text remain retained; the model stays unverified. No runtime
prompt injection, command rewriting or automatic retry was added.
