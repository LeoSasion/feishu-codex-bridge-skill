# Feishu Codex final return

This repo-local plugin provides the P0 reply-return transport for Feishu Codex Bridge.
It does not route prompts or inspect Codex transcripts. The Gateway arms one
fenced request before sending; `UserPromptSubmit` binds the actual task and turn,
accepting either the exact raw prompt or Desktop's strict delegation wrapper only
when its source is the Gateway pinned at arm time and its inner input is byte-
equivalent under UTF-8 hashing. `Stop` captures only that exact turn's final assistant text through a hidden
local MCP tool. A later Stop continuation for the same turn replaces provisional
text; unarmed and mismatched turns are ignored. The Gateway consumes the result
only after the exact target turn is reported completed.

Installation, enablement, runtime registration, exact Hook trust review, and
Codex restart remain separately approved actions. A newer Codex Desktop build is
only a P2 compatibility canary for the native `wait_threads` final field; it is
not a prerequisite for this plugin's source development.

Use `bridge final-return-status` for answer-free read-only registration state,
`bridge final-return-register` to bind the exact manifest-valid installed
runtime, and `bridge final-return-unregister` for exact rollback. None of these
commands installs the plugin, trusts Hooks, or restarts Codex.
On an installed runtime that predates this transport, the status command returns
`upgrade_required` without dispatching an unsupported helper subcommand; deploy
the runtime under the separate upgrade approval before registration.
