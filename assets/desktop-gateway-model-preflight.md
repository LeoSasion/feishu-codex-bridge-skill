# Feishu Desktop Gateway post-model-change preflight

Use this only for the exact existing registered Gateway after an explicitly
approved model change, a successful `REHYDRATE_EXISTING` contract turn, and
readback that its scheduler remains paused. Send the prompt as a manual
task-to-task turn with the owner's exact selected Gateway model. Preserve the
current reasoning setting unless the owner explicitly selected another one.

This checks only the ordinary-turn Desktop coordination surface under the new
model. It must not inspect the Bridge queue or perform a Gateway cycle, and a
passing result never certifies the later automation-origin surface. Current
Desktop task tools must be called directly on the top-level `mcp__codex_app`
server; `functions.exec` registry entries are not accepted as capability
evidence because they may be retired aliases.

Do not use this preflight to retry a Desktop build already terminalized as
automation-origin-incompatible. A different model, prompt, context, or task is
not proof of a new official Desktop surface.

After completion, use `read_thread` to retrieve and JSON-parse the exact stored
final. Treat `wait_threads` only as a completion signal because compact wait
snapshots may normalize structured text.

If the completion signal succeeds but `read_thread` returns an empty `items`
array, do not resend this prompt. When the owner can see and transcribe the
exact JSON final from the opened Gateway task, record
`visible_preflight_pass_readback_unavailable`. The transcription may satisfy
only the ordinary-turn prerequisite for one separately approved finite live
canary when the build gate passed for a positively identified newer official
build, the exact Gateway remains registered with its scheduler paused, the
owner personally submitted this exact prompt, the JSON contract is exact and
fully true, the controller identifies the exact
completed-but-empty turn, and a bounded control read proves stored user and
final agent items remain readable on the predeclared target. It is not stored-
final evidence and does not certify automation-origin eligibility or authorize
activation or production. A delayed read, a duplicate background delivery, or
visible UI output alone is insufficient.

## Post-model-change preflight prompt

```text
This is a read-only post-model-change capability preflight for the exact existing registered Feishu Desktop Gateway. It is a manual task-to-task turn. It is the sole manual task-to-task exception explicitly permitted by the fully mounted Gateway contract after `REHYDRATE_EXISTING`; it is not a mounting turn or an automation-origin Gateway cycle. The mounted contract's fenced-claim gate applies to routed work and does not block these two read-only direct MCP calls. Do not run any Bridge queue command; do not register, probe, claim, stage, complete, fail, release, create or modify an automation, message another task, or change any file or setting. Invoke the top-level Codex app MCP tools directly: call `mcp__codex_app.list_threads` once with an explicit limit no greater than 50, then call `mcp__codex_app.list_projects` once. Do not call either through `functions.exec`, `ALL_TOOLS`, a `tools[...]` dynamic alias, shell, App Server, database, rollout, or UI access. Discard every returned title, summary, path, and task/project value; retain only whether each direct call succeeded. Make the final response exactly one JSON object with keys `direct_mcp_invoked`, `list_threads_invoked`, `list_projects_invoked`, and `compatible_for_model_canary`. Do not finish with `DONT_NOTIFY` or an empty response; if either direct method is absent, explicitly unavailable, or otherwise cannot be invoked, return the same JSON contract with the applicable false fields. Set `direct_mcp_invoked=true` only when both calls were attempted on the top-level `mcp__codex_app` server. Set `compatible_for_model_canary=true` only when both direct calls succeed. This result does not certify scheduled automation-origin tool availability, any mutating method, or authorize scheduler activation.
```
