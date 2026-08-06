# Permissions, authentication, and hooks

The official Feishu CLI installation guide is [Feishu CLI installation guide](https://open.feishu.cn/document/no_class/mcp-archive/feishu-cli-installation-guide). Its installation sequence is `npm install -g @larksuite/cli` followed by `npx -y skills add https://open.feishu.cn --skill -y`; app configuration and login remain separate user-assisted steps.

## Feishu readiness

1. Configure `lark-cli` for the correct Feishu/Lark brand and language.
2. Complete user or bot authentication in the user's own Feishu tenant.
3. Verify with `lark-cli auth status --json --verify`; do not infer readiness from a browser page alone.
4. Subscribe the app to `im.message.receive_v1` and install the app in every group that should be handled.
5. Start with the known least-privilege scopes:
   - `im:message.group_at_msg:readonly` for group @ messages.
   - `im:message:send_as_bot` for bot replies.
6. If sender or group names cannot be resolved, use the exact failing `lark-cli` endpoint error to identify the additional read scope. Do not request all permissions as a shortcut.

The skill does not submit permissions, approve OAuth dialogs, or expose tokens. The user must complete those actions in Feishu.

## Codex hook merge

The generated hook entries are:

- `SessionStart` matcher `startup|resume` -> `start-feishu-codex-bridge.ps1`.
- `SessionEnd` -> `stop-feishu-codex-bridge.ps1`.

The installer preserves existing hook entries and detects duplicates by script path. Inspect the resulting `.codex\hooks.json` after installation, especially if the project already uses other SessionStart or SessionEnd commands.

The start hook launches the bridge hidden, prefers `.venv\Scripts\python.exe`, removes stale stop requests, and loads optional `bridge.env` values. The stop hook writes a stop request, waits for graceful shutdown, and force-stops only the bridge PID if it does not exit within the bounded wait.

## Safe diagnostics

```powershell
lark-cli auth status --json --verify
Get-Content .codex\feishu-bridge\bridge.log -Tail 80
Get-Content .codex\feishu-bridge\sessions.json
Get-Content .codex\hooks.json
```

Avoid printing `bridge.env` if it contains any tenant-specific secret or token.
