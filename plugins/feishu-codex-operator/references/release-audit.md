# Release audit

The audit checks the package boundary: exact inventory coverage, source version,
byte-identical AGENTS mirror, Python compilation without cache output, and
PowerShell syntax. It does not infer behavior from particular function names,
prompt wording, or a collection of regex matches.

The isolated unit suite separately checks routing, callback convergence,
model selection, wake lease, lifecycle waiting, metadata-only requests,
and App Server child cleanup. Run both while the exact installed service is
stopped and no callback is pending. Neither proves live Feishu delivery.

## Alpha.127 development validation (2026-09-10, unpublished)

Full isolated regression passed 422 methods in 80.208 seconds with official CLI
0.153.4. Five opt-in CLI methods ran, with only the two existing privilege-1314
Windows symlink subcases skipped. One new method verifies that input modality
support cannot authorize input-only assistant output. The existing three-transport
rejection matrix includes invalid assistant content after a valid call. The
probe's existing whitespace checks cover supported legacy JSON text forms.

A pre-fix isolated snapshot returned a tool call despite an assistant input_text
part. The fixed validator rejects it before call release on JSON, SSE and
WebSocket paths. Four new canonical live CLI cases passed (twelve requests):
workspace editing and error-stop for both providers. Four separately identified
private representation experiments failed before result continuation (four
requests); all showed unchanged argument JSON bytes across restoration and an
upstream mismatch already present. No failures were replayed or repaired.
The 107-file audit and rule mirror passed. Runtime/registry remain alpha.123
and unchanged; none of this establishes fresh Desktop acceptance.

## Alpha.126 development validation (2026-09-10, unpublished)

Full isolated regression passed 421 methods in 76.270 seconds with current official
CLI 0.153.4. All five opt-in CLI methods ran; only the two existing Windows symlink
subcases requiring privilege 1314 were skipped. Three new methods cover cancellation
outcome races, real CLI disconnect before buffered JSON headers, and standard-function
source probes with rejection of altered CRLF. Both source candidates now use the
explicit standard-function contract. Preflight passed without upstream requests.
The extension-entry rename also retains the existing shortcut lifecycle fixture,
which checks official-link preservation and the new executable's Windows metadata.

Fifteen new owner-authorized live cases produced 13 passes and two CRLF source
mismatches, with 35 routed request attempts including two local cancellations.
No failed case was retried. Three evaluator revisions are represented and remain
separate evidence; this is not a same-version profile or Desktop acceptance.
The installed alpha.123 runtime and live registry are unchanged. The 107-file
package audit, syntax checks and managed rule mirror passed.

## Alpha.125 development validation (2026-09-10, unpublished)

Full isolated regression passed 418 methods in 69.066 seconds. All four opt-in
CLI methods ran using current official CLI 0.153.4; only the two Windows symlink
subcases requiring privilege 1314 were skipped. The real CLI table now includes
one standard-function scenario alongside the existing exec cases (14 scenarios
total). Exact Operator absence and zero pending callbacks were verified first.
The 107-file package inventory, Python/PowerShell syntax, byte-identical rule
mirror and 27 local documentation links/anchors passed their read-only checks.
The installed manifest remains alpha.123 and its registry digest is unchanged.

Two new methods cover actual JSON-encoded argument bounds and HTTP/WS rejection
before any upstream dispatch. The encoded-history test first reproduced all three
missing checks: declared custom wrapping, history-only custom wrapping, and client
tool-search conversion. Each now accepts the exact 2 MiB boundary and rejects
expansion without source changes. Existing checks cover fixed wrapper diagnostics,
standard patch-tool catalog behavior, unchanged native/Beeper entries, and direct
MCP result consumption. No line-array codec or qualified-name option remains in
canonical source.

Online exploration and its failures are recorded separately in
[official online models](official-online-models.md#alpha125-standard-function-investigation-2026-09-10-unpublished).
The standard-function DeepSeek route has useful isolated evidence, but the private
prototype and canonical evaluator receipts are version-distinct. GLM's late
cancellation attempt remains failed rather than being converted into a pass.
High-effort opaque history remains rejected; the proposed limited exception has
not been authorized or implemented. No live registry, runtime, service, entry,
permission or default-model change was made.

## Alpha.124 development validation (2026-09-10, unpublished)

Full isolated regression passed 416 methods in 67.454 seconds. All four opt-in
CLI methods ran using current official CLI 0.153.4 and the retained native-only
catalog fixture; only the two Windows symlink subcases requiring privilege 1314
were skipped. The focused probe/profile suite passed 19 methods, and the
adapter/events/router/probe/profile suite passed 114 methods before the final
LF argument-object variant was added to the existing probe loop.
Package inventory, Python/PowerShell syntax and rule-mirror audit passed for
107 plugin files. Installed inventory and startup checks still match alpha.123;
the live model registry digest is unchanged.

Only one test method was added, covering content-free byte-offset and character
count diagnostics. Existing probe methods cover the new explicit prompt and
line-ending variants, exact roundtrip, rejection without a second request, and
profile import. The first checks reproduced the missing case/diagnostic support
and profile allowlist omission before implementation. The custom-tool change
clarifies outer JSON escaping versus source-language escapes; it does not repair
model output or alter protocol validation, retries, model defaults or permissions.

Eight new bounded online cases made thirteen upstream requests: three passed and
five failed. Their distinct versions, conditions and limits are recorded in
[official online models](official-online-models.md#alpha124-escaping-investigation-2026-09-10-unpublished).
All historical failures remain visible. Exact Operator absence and zero pending
callbacks were verified before testing; isolated fixtures did not contact Feishu
or saved Codex tasks. Installed alpha.123, active registrations and routing remain
unchanged. This development source is neither deployed nor published.

## Alpha.123 development validation (2026-09-09, unpublished)

Final full regression passed 415 methods in 70.933 seconds. The four opt-in
CLI methods ran against the installed official CLI 0.153.4; only the two Windows
symlink subcases requiring privilege 1314 were skipped. Package inventory,
Python/PowerShell syntax and rule-mirror audit passed for 107 plugin files.
This source is not a new published release or an installed-runtime upgrade.

The initial full run is retained: 415 methods in 164.306 seconds, one CLI timeout
followed by a Windows temporary-file lock error, and the same two skips. The
fixture now stops its exact child before home cleanup and disables unrelated
plugin discovery/download in disposable CLI configuration. Eleven targeted CLI
and evaluation tests passed after that change, before the final full run.

The exact-text regression first reproduced all five whitespace false passes;
they now fail correctly with unchanged diagnostic comparisons. Only two methods
were added: the whitespace regression and the fixed-count diagnostic privacy
check. Existing probe/profile/extra-round CLI cases cover the remaining changes.
One separately reserved live DeepSeek workspace diagnostic passed under the new
evaluator, without reproducing the old extra request. Its narrow scope and
retained failures are recorded in [official online models](official-online-models.md).

## v1.1.0 / alpha.122 release validation (2026-09-09)

The full isolated suite ran 413 test methods in 86.703 seconds with no failures.
All four opt-in CLI methods ran using official Desktop CLI 0.153.4 and the
retained native-only catalog fixture. The only skips were two Windows symlink
subcases with privilege error 1314. The success exec/MCP roundtrip now exercises
explicit JSON upstream and serial calls; the image-capability rejection retains
the upstream SSE path. There are still 13 CLI scenarios, without new duplicates.

The label regression first reproduced the unnecessary Desktop-running rejection;
after the fix, 35 label/verification/reload tests and both focused CLI cases passed.
Stopped Operator/router, exact Desktop package version, empty callbacks, evidence,
preview digest and original-backup checks remain. Desktop itself may stay open.

The package audit passes for 104 plugin files, matching rule mirrors and Python/
PowerShell syntax. Together with three repository entry files, the release boundary
contains 107 files. File-list and private-path/credential-pattern checks found no
extra source files or matching private material. These checks and isolated tests
do not install the release, activate model routing or establish live Desktop
acceptance for any changed model/adapter contract.

## Regression maintenance (2026-09-09)

This test/documentation cleanup leaves runtime source at alpha.121. The suite
changes from 33 files / 422 methods to 32 files / 413 methods, with 112 fewer
test-source lines. Real CLI scenarios decrease from 21 to 13: two exec roundtrips,
ten evaluation scenarios and one catalog check. These are four opt-in unittest
methods, not thirteen separate test methods or a count of every child process.

The full isolated suite ran 413 methods in 92.191 seconds with no failures,
using the installed official CLI 0.153.4 for every opt-in case and the retained
native-only catalog fixture. Only the two Windows symlink subcases skipped with
privilege error 1314. The 104-file release audit, language syntax, rule mirror
equality and whitespace checks passed. This is an observed run, not a controlled
performance benchmark or live Desktop acceptance.

[Test maintenance](../tests/README.md) records each removed check's surviving
coverage and the single guide for selecting suites. Status sanitization now runs
against an actual isolated installation; every installed manifest file is hashed.
Repeated prose assertions and redundant CLI combinations are removed without
changing runtime behavior, model contracts, Desktop evidence gates or historical
failure records. Early development stages and paused search design are no longer
presented as another daily regression queue. Local archives remain outside test
discovery; source cleanup did not deploy code or change live registrations.

## Alpha.121 source validation (2026-09-09)

The full isolated suite ran 422 test methods in 149.447 seconds with no failures.
All six opt-in CLI cases ran with the installed official CLI 0.153.4; the catalog
case used the retained native-only fixture described below. Only the two Windows
symlink subcases remained skipped for privilege error 1314. The 104-file release
audit, Python/PowerShell syntax, rule mirror equality and whitespace checks passed.

A fresh official CLI schema confirmed nullable reasoning content. Regression
cases reproduced null-content rejection and missing malformed-reasoning checks
before the fix. Tests now cover history rejection before upstream I/O, preservation
of absent/null/empty content, prevention of observed-text erasure, and terminal
tool gating over JSON, SSE and WebSocket with both upstream modes. No live model,
Desktop business task, Feishu message or deployment was used for this validation.
Prior failures and deployment limitations remain recorded separately.

## Alpha.120 source validation (2026-09-09)

The isolated suite ran 416 test methods with no failures. Its eight skips were
six explicitly gated CLI tests and two Windows symlink subcases. The six CLI
tests were subsequently exercised with the installed official CLI 0.153.4:
five execution/evaluation tests passed, and the catalog check passed using a
retained native-only fixture. An earlier catalog attempt against the already
augmented live cache failed with `model_catalog_collision`; that failure remains
in private evidence and was not reclassified. The retained fixture is not proof
of current live Desktop catalog state. The two symlink subcases remain skipped
because Windows returned privilege error 1314.

The release audit passed for 104 listed files, matching rule mirrors and both
language syntax checks. The JSON event regression tests first reproduced three
failures in the prior source, then passed with the fix; HTTP/WS checks confirm
that expanded-budget rejection sends no event and performs no retry. Source
validation did not install runtime code, change a registry or restart a service.
## Alpha.128 terminal evaluation (2026-09-10)

- Adds two explicitly selected native CLI shell cases with fixed read-only
  commands, typed argument admission, separate console/file-byte checks and
  requested executable digests. Per-child PATH preparation does not alter
  Desktop's terminal or user configuration. Profiles retain terminal identities
  and failures without granting core, Desktop or write-approval acceptance.
- Mock-provider native execution was rejected by the current read-only CLI
  policy. The refusal-boundary run requires failed execution reports and no
  second upstream dispatch. It is not a successful terminal compatibility run.
  The earlier namespace, generic-result and policy failures remain private.
- The first full run found the new helper missing from startup's manifest
  allowlist. Installation, status and startup inventories are now synchronized.
  Eighteen focused checks then passed. Final full regression: 430 methods in
  74.210 seconds, with two existing Windows symlink privilege skips. Seven
  opt-in CLI methods ran; the two new terminal methods verify policy refusal.
- The 109-file release inventory, syntax and byte-identical rule mirror passed
  audit. Source is alpha.128; installed alpha.123, live registry, credentials,
  model defaults and services were not changed. No live inference was requested.
## Alpha.129 native terminal harness fixes (2026-09-10)

- Adds explicit child-only selection of the unelevated Windows backend, while
  keeping read-only/never approval settings. A normally inherited synthetic
  workspace is separate from the private CLI home. No host configuration,
  permission-rule change, elevated setup or automatic fallback is introduced.
- Replaces the compound print/read fixture with one literal read, handles the
  current native output envelope and validates the actual exit status. PowerShell
  reads decimal bytes so BOM, Unicode and mixed EOLs remain exact under a
  constrained shell. Successful byte reading does not attest to Unicode console
  text, arbitrary commands, live provider generation or Desktop behavior.
- The mock-provider PowerShell case passed. Git Bash's MSYS signal-pipe failure
  remains failed. Earlier policy, nested-token, private-directory, text-output
  and constrained-language failures remain private and retain their own evaluator
  revisions. No observed result was normalized or retroactively accepted.
- Full regression: 433 methods in 74.280 seconds; six opt-in CLI methods ran.
  The three skips are the two existing WinError 1314 symlink subcases and the
  explicitly unselected, already-failed Bash execution case. The skip does not
  clear that failure. All executed checks passed. The 109-file inventory, syntax
  and byte-identical rules mirror passed audit.
- Source is alpha.129; installed alpha.123 and the live registry are unchanged.
  No online inference, Desktop restart, deployment or publication occurred.

## Alpha.130 entry setup preservation review (2026-09-11)

- Reproduced a repeated setup overwriting a later edit to the launcher executable
  in the disposable installation fixture. Setup now checks the existing binary,
  entry script and required build/configuration records before replacing files
  or starting another ownership generation. Existing records do not attest to
  historical configuration fields that were never fingerprinted.
- Changed or missing managed shortcuts stop before rebuilding, including a
  shortcut whose edited target is native Codex. Pending installation intents
  retain their existing recovery semantics. No new backup-copy mechanism or
  runtime module was added.
- Full regression ran 433 methods in 79.135 seconds: executed checks passed,
  with the two existing Windows symlink privilege skips and the explicitly
  unselected Bash case. Six opt-in CLI methods ran, using mock upstreams.
  The final missing-shortcut guard and missing-file fixture variants were then
  checked by all 11 installation/restoration methods in 29.588 seconds. This is
  full regression followed by a focused final correction, not a claim that the
  earlier full run included the last correction. Prior Bash and live model
  generation failures remain failures.
- The 109-file inventory, syntax and identical rule mirror passed audit.
  The active frozen alpha.129 trial passed its read-only recovery preflight;
  the installed alpha.123 runtime was not upgraded. No saved task, live model
  test, restart, deployment, commit or push was performed in this review.

### Development checkpoint validation (2026-09-11)

After owner approval to save the development checkpoint, the complete final
source was tested again: 433 methods in 77.617 seconds, with all executed checks
passing and the same three recorded skips. This run includes the final
missing-shortcut guard and missing-file variants. Six opt-in CLI methods ran
against mock upstreams. Publication scope was checked against the release
inventory, including the four earlier local commits in this development branch.
The checkpoint preserves the outstanding Desktop acceptance and earlier Bash
and live-generation failures; it is not a stable release or a deployment.
