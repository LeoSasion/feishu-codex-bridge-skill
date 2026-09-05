# Permissions and Hooks

The plugin contributes two separate one-tool MCP servers: Final Callback
submits the final answer; the Beeper-only relay server consumes prepared input
and returns a fixed, data-escaped program without sending to Desktop itself.
Beeper executes that program in its existing exec context; only Desktop's native
send tool dispatches the request. This is not permission to evaluate arbitrary
tool text. It also contributes two project lifecycle
Hooks, and no UserPromptSubmit or Stop Hook.

Review Hooks in Codex Desktop under “设置 → 钩子”. Enable and trust only the
project SessionStart and SessionEnd rows after checking that their command paths
resolve below the current project's `.codex\hooks` directory. Do not use
“信任全部”.

SessionStart acquires an Operator lease and may start the installed Operator only
after source/runtime and manifest checks. SessionEnd releases that lease. Hooks
never route a business message, query a task, call Final Callback, or contact
Feishu directly.

Codex Desktop has no `/hooks` chat command. If CLI inspection is needed as a
fallback on Windows, open CMD, change to the actual project directory, and use
project-relative commands. Never copy a command containing a fixed user profile
path or Codex version hash.
For the CLI fallback, open Windows CMD in the actual project and run:

```cmd
powershell -NoProfile -Command "$operatorBin = Join-Path $env:LOCALAPPDATA 'OpenAI\Codex\bin'; $operatorCli = Get-ChildItem -LiteralPath $operatorBin -Directory | Get-ChildItem -Filter codex.exe -File | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1 -ExpandProperty FullName; if (-not $operatorCli) { throw 'Codex Desktop CLI not found' }; & $operatorCli"
```

The path is discovered from the current Desktop installation; do not use a
PATH-selected CLI or a copied username/version hash. Then use the CLI's Hook
inspection if available. Desktop settings remain the first choice.
