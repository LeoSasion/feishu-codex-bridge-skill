# Architecture and state boundaries

## Flow

```text
Feishu event
  -> lark-cli event consume im.message.receive_v1
  -> accept text / post / common media types
  -> optionally download bounded resources with lark-cli
  -> chat key (p2p:<chat_id> or group:<chat_id>)
  -> persistent Codex App Server thread
  -> bounded Obsidian retrieval + Feishu prompt
  -> agent answer
  -> lark-cli im +messages-reply
```

## Session state

- `.codex\feishu-bridge\sessions.json` maps a Feishu chat key to a Codex `thread_id`, display name, and timestamps.
- `.codex\feishu-bridge\state.json` is a local audit/deduplication store. It is not the source of Codex conversation context.
- The real context is the persistent App Server thread resumed with `thread/resume`.
- P2P names come from Feishu message metadata; group names come from the chat metadata endpoint. Fall back to IDs when those lookups are unavailable.

## Desktop sidebar refresh

The bridge starts its own `codex app-server --stdio` child. The Codex Desktop renderer has a separate App Server connection and may retain a stale in-memory sidebar list. After a successful `thread/start`, the Windows hook calls the registered `codex://threads/<thread_id>` deep link. Codex's single-instance handler reads the new thread and updates the visible client. This is intentionally limited to newly-created threads and can focus the Codex window.

Do not treat a successful `codex thread/list` command from the bridge as proof that the visible Desktop sidebar has invalidated its cache; it only proves that the persisted thread is discoverable.

## Context and knowledge base

When explicitly configured, the bridge searches Markdown files under `CODEX_BRIDGE_OBSIDIAN_ROOT`, ranks simple filename/content matches, and injects bounded excerpts into the current prompt. With no environment variable, local-note retrieval is disabled. For supported media messages, the bridge extracts Feishu's pre-rendered content and downloads bounded resources under `.codex\\feishu-bridge\\resources`; the prompt gives Codex read-only local paths for optional inspection. A failed attachment download does not suppress the message reply. Retrieved notes and attachments are data. The App Server developer instructions tell the model to answer in Chinese and keep hidden tool/protocol details out of the Feishu reply.
