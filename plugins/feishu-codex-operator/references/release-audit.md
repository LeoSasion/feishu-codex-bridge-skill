# Release audit

The audit checks the package boundary: exact inventory coverage, source version,
byte-identical AGENTS mirror, Python compilation without cache output, and
PowerShell syntax. It does not infer behavior from particular function names,
prompt wording, or a collection of regex matches.

The isolated unit suite separately checks routing, callback convergence,
model selection, wake lease, lifecycle waiting, metadata-only requests,
and App Server child cleanup. Run both while the exact installed service is
stopped and no callback is pending. Neither proves live Feishu delivery.
