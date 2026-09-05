# Feishu command UX

`/init` is the only reserved command.

It lists at most 50 active stored Codex tasks, shows eight per page, binds the
wizard to its initiating Feishu user, expires after ten minutes, and requires a
confirmation before changing the local mapping. Selection is by exact UUID,
never by title. The configured minimal Beeper is filtered out and cannot be
selected.

All other slash commands receive a generic unsupported-command reply. Ordinary
messages require an existing binding and are relayed once through the fixed
minimal Beeper to that task.
Successful binding displays one risk notice: delivery can rarely be missed or
duplicated, so the Operator should not be used for irreversible actions.
