# Feishu Desktop Gateway candidate bootstrap

Use the exact prompt below as the required non-empty first turn when creating a
Gateway candidate. Omit model and reasoning overrides so the task inherits the
user's current Codex defaults. An explicitly requested later model change uses
the paused rehydration and post-model-change preflight path; it is not an
initial-candidate override. This turn is a read-only capability preflight, not a
mount or scheduled Gateway cycle.

Do not register the candidate, probe or claim the Bridge queue, or create an
automation from this turn. A passing result proves only that this ordinary task
turn exposes the required coordination surface. The later automation-origin
`/init` catalog-and-selection canary remains mandatory.

After the candidate completes, treat a compact `wait_threads` result only as a
completion signal. Read the candidate's exact final with `read_thread` and parse
that text as JSON before accepting or rejecting it.

Current Codex Desktop exposes task coordination through the top-level
`mcp__codex_app` server. A name found in `functions.exec`'s `ALL_TOOLS` is not a
capability signal: current builds may retain that dynamic alias only to report
that the app tool has moved to direct MCP. This preflight therefore proves the
ordinary-turn surface by making two harmless direct MCP calls, not by counting
registry entries.

## Candidate bootstrap prompt

```text
This task is only a candidate Feishu Desktop Gateway. Perform one read-only capability preflight and then stop. Do not run any Bridge queue command; do not register, probe, claim, stage, complete, fail, release, create or modify an automation, message another task, or change any file or setting. Invoke the top-level Codex app MCP tools directly: call `mcp__codex_app.list_threads` once with an explicit limit no greater than 50, then call `mcp__codex_app.list_projects` once. Do not call either method through `functions.exec`, `ALL_TOOLS`, a `tools[...]` dynamic alias, shell, App Server, database, rollout, or UI access. Discard every returned title, summary, path, and task/project value; retain only whether each direct call succeeded. Make the final response exactly one JSON object with keys `direct_mcp_invoked`, `list_threads_invoked`, `list_projects_invoked`, and `compatible_for_mount_preflight`. Set `direct_mcp_invoked=true` only when both calls were attempted on the top-level `mcp__codex_app` server. Set `compatible_for_mount_preflight=true` only when both direct calls succeed. If either direct tool is absent or explicitly unavailable, return the same JSON contract with the applicable false fields. This result does not certify scheduled automation-origin tool availability or any mutating method.
```
