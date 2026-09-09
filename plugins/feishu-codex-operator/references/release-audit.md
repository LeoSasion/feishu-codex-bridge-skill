# Release audit

The audit checks the package boundary: exact inventory coverage, source version,
byte-identical AGENTS mirror, Python compilation without cache output, and
PowerShell syntax. It does not infer behavior from particular function names,
prompt wording, or a collection of regex matches.

The isolated unit suite separately checks routing, callback convergence,
model selection, wake lease, lifecycle waiting, metadata-only requests,
and App Server child cleanup. Run both while the exact installed service is
stopped and no callback is pending. Neither proves live Feishu delivery.

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
