# Harness sibling boundary

This is a routing note for the Desktop-owned `feishu-codex-bridge`, not a
second Harness design contract.

The maintained Harness design belongs to the separate
`feishu-codex-harness-bridge` Skill. When the user explicitly asks to evaluate
an SDK-owned backend, load that Skill from the current environment and follow
its `SKILL.md`. If it is unavailable, stop and ask the user to provide or
install it; do not reconstruct it from this note or old chat history.

The Desktop Bridge must preserve these boundaries:

- Harness is not a fallback for a failed Desktop queue item.
- A Desktop task ID is never an SDK thread ID.
- The two products do not share queues, bindings, state/auth homes, runtime
  data, processes, logs, or a Feishu consumer.
- Backend evaluation, dependency installation, authentication, process start,
  migration, and live canary are separate actions under the sibling Skill.
- A same-app cutover is explicit and occurs only after the old consumer is
  stopped and reconciled; there is no automatic failover.

Current sibling status is disabled: it retains one frozen external
non-instantiating compatibility probe but has no auth diagnostic, turn runner,
resident worker, or Feishu consumer. Historical v1 auth/owner/WFP designs are
not current implementation guidance.

For current product behavior, consult the official
[Codex SDK](https://learn.chatgpt.com/docs/codex-sdk) and
[App Server](https://learn.chatgpt.com/docs/app-server) documentation rather
than preserving version-specific conclusions here.
