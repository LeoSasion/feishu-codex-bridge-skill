# P3 bounded stopped soak

P3 is a stopped, external, bounded soak for repeated concurrency, persistence, retry,
recovery, and transport invariants. It is release/nightly evidence, not an ordinary development-loop test or a live end-to-end check.

It never contacts Codex Desktop, a Beeper, a scheduler, App Server, Feishu, or the live
Bridge. It never publishes queue work, arms a turn, sends a responder message, or authorizes a runtime action.

## When to run it

Run P3 only when a change affects concurrency, persistence, retry/no-replay, fencing,
final-callback/outbox recovery, lifecycle Hooks, or transport, or when an explicit release/nightly plan covers those risks. A release candidate with no affected durability boundary does not run P3 merely because it is a release candidate.

Release/nightly execution enforces a minimum of 25 iterations, not merely a
suggested default. With ten scenarios, every accepted run therefore contains at
least 250 scenario executions. Development does not run P3: use the 56-test fast
lane or smaller focused Smoke, Contract, and Fault checks, then run the maintained
full Gate B before P3.

## Entry gate

P3 requires a fresh passing Gate B evidence-schema-v2 receipt for the exact same source and a pass from
Gate B's independent semantic validator. The supervisor receives the exact receipt file and lowercase whole-file SHA-256.

The P3 supervisor validates that receipt before and after the soak and executes only
from Gate B's retained `source-snapshot`. Every audited snapshot file stays pinned read-only through runner execution and post-validation.
The exact Bridge must remain verified stopped for the complete window. P3 holds
the same current-user lifecycle mutex for that whole window and records three
independent, answer-free stopped-state observations: pre-run, post-run, and final.
Each observation combines Bridge status, exact-process scanning, and PID-state
checks. It also requires a present, valid, stopped health snapshot with no event
consumer, active turn, dial, or pending Bridge delivery, then runs the exact
P0-bound Python against the retained current `beeper_queue_cli.py` and project runtime
to prove `beeper` has no pending, claimed, or in-flight dial.
The supervisor retains the closed answer-free captures, exact argv, and hashes.

All work, captures, and evidence remain outside canonical source and installed runtime.
The external clean-PowerShell supervisor owns bounded process isolation, a hard timeout, and rejection of live-surface contact.

## Authoritative scenario contract

The current machine registry remains authoritative and fixed at exactly ten ordered
scenarios per iteration. This document groups them by invariant family; it does not rename, remove, reorder, or claim to reduce them to a smaller suite.

The ten independently valuable invariant slots jointly cover:

1. grant/claim exclusivity under overlap;
2. duplicate callback convergence;
3. conflicting callback convergence;
4. terminal/release race convergence;
5. the bounded delayed-claim window;
6. unclaimed restart recovery;
7. pre-start restart requeue eligibility;
8. post-start restart no-replay;
9. retryable delivery disposition; and
10. terminal delivery disposition that must not be rescheduled.

Exact scenario names, order, test mapping, and count relations live in the runner,
evidence schema, and independent validator. Change those machine artifacts together; do not duplicate their registry in human documentation.

## Execution and evidence

Use the maintained combined Gate B/P3 one-shot wrapper for a fresh release window,
or the standalone P3 one-shot wrapper when the same-source Gate B receipt is already validated.
The wrappers own path resolution, unique work/evidence roots, exit-code gating, and semantic-validator sequencing.

The maintained contract permits an explicit bounded iteration count and timeout,
but rejects fewer than 25 iterations. The runner forbids child-process creation and records zero Desktop and Feishu contact.
The supervisor contains its owned child tree and publishes only a create-new evidence receipt.

The published receipt uses P3 evidence schema v2. In addition to the ordered
scenario results, it binds the full-window lifecycle mutex and the pre, post, and
final Bridge-stopped captures. The independent validator re-parses and re-hashes
those captures and cross-checks their lifecycle, current-user, Hook, configuration,
and Gate B relations; a schema-shape pass alone is insufficient.

After any nonzero result, stop and retain the bounded diagnostic paths. Do not parse
an absent envelope, invoke a validator with empty arguments, or rerun unchanged bytes merely to seek a green result.

## Acceptance

Accept P3 only when the supervisor envelope and independent validator both pass,
the whole-file receipt hash matches, all ten scenarios pass every requested iteration,
and there are no failures, errors, skips, timeouts, child-process attempts, or live-surface markers.

The validator must rehash the retained result and captures, recompute the ordered scenario
mapping and iteration relations, revalidate the bound Gate B evidence and current source manifest, and confirm the pinned snapshot window.
It must also verify the lifecycle mutex identity and all three Bridge-stopped
observations across the complete execution window.

JSON Schema validation checks shape and constants only. The independent semantic validator is mandatory for cross-field and retained-artifact relations.

P3 evidence is not a signature or cryptographic attestation. A pass does not certify
live Desktop task tools, Hook trust, exact Final Callback, Feishu delivery, Beeper activation, scheduling, Bridge startup, or production readiness,
and it cannot alter or reactivate retired producer state.
