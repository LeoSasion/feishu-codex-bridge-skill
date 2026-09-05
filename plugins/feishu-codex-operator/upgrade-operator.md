# Upgrade and naming cutover

## Existing Operator installation

1. Verify the exact installed process, Git/source identity, and zero pending
   callbacks or active requests; stop it before replacing files.
2. Run the isolated tests and release audit. Use `operator upgrade`, which
   preserves `operator.env`, mappings, and databases.
3. Check Final Callback registration, start, then require status/doctor/readiness.
   Offline checks do not establish real Feishu E2E delivery.

## Previous Bridge installation

This is a coordinated cutover, not a compatibility mode. Do not start a second
service against the same chat or silently point an old MCP client at new code.

1. Stop the exact old installed service. Require no pending/captured callback,
   active inbox/outbox request, or actionable retry; preserve historical tombstones.
   Back up the old runtime, Hook config, callback registration, and project rules.
2. Install the new `feishu-codex-operator` plugin from the canonical marketplace
   and disable/remove the old plugin through Codex. Reload Desktop tasks so their
   Final Callback MCP uses the new plugin. Do not hand-edit plugin cache copies.
3. Install the new runtime at `.codex/feishu-codex-operator-runtime`, still stopped.
   Copy the idle `sessions.json`, `state.sqlite3`, and `callbacks.sqlite3` intact;
   include SQLite sidecars consistently and preserve attachments in the backup.
   Convert `bridge.env` into `operator.env`: map `CODEX_BRIDGE_*` keys to
   `CODEX_OPERATOR_*`, checking conflicts and path-valued settings individually.
   Do not copy old code, PID, health, lock files, or live leases.
4. Remove only the old project's Hook entries and archive its two Hook scripts;
   register the new scripts, review them in Desktop settings, and synchronize
   the new AGENTS managed block. Keep unrelated Hooks untouched.
5. Explicitly move Final Callback registration from the verified old runtime to
   the new one. Archive the old runtime only after validating the new inventory
   and preserved state. Start one new Operator, then check doctor/readiness and
   one authorized reversible E2E request.

If Desktop cannot reload yet, keep the old installation intact and do not
activate the new runtime. Old names belong only in this transition checklist
and retained historical state, never in the new execution path.

No upgrade implies permission to push, publish, change credentials, or replay
an accepted/uncertain request.
