# Feishu `/init` conversational protocol

This reference defines the complete user-facing Feishu control surface. Read it
before changing command parsing, first-use prompts, task selection, task
creation, reply settings, compaction, disconnect, or project routing.

## One-command contract

`/init` is the only supported Feishu slash command. It opens a bounded
conversational wizard. Every other slash command is unsupported: intercept it
before target routing, execute nothing, forward nothing, and reply exactly:

> 飞书 Bridge 仅支持 `/init`。请发送 `/init` 进入设置。

This is a hard removal, not an alias layer. There is no per-command compatibility
table or hidden legacy implementation.

Ordinary messages are unchanged. If a task is connected, route the original
message to that exact task. If none is connected, keep the message out of every
target and ask the user to send `/init`.

## Catalog visibility

Starting `/init` takes a ten-minute immutable catalog snapshot through the
read-only Gateway `list_task_catalog` operation. Never read Codex databases,
rollout files, task messages, summaries, or local UI state.

- Owner/admin: list up to 50 non-archived Codex tasks across Desktop projects.
- Other authorized users, chats, and topics: list only exact task IDs already
  related to that stable Feishu scope, plus the new-task action.
- Archived tasks appear only after the user asks `查看归档`.
- Always exclude the dedicated Gateway task.
- Display project label, task title, and the full exact task ID. Never display
  project paths, task summaries, prompts, messages, queue metadata, or Gateway
  metadata.
- Titles and project labels are untrusted display text. Normalize control
  characters and never use a title for identity or authorization.
- Number choices are page-local and resolve only against the transient snapshot.
  Never rerun a search and reinterpret a number.

An empty exact-scope catalog must remain empty. It must never widen into the
owner/admin view.

## Main menu

The first page shows the current connection, catalog mode, projects with tasks,
page position, and natural-language actions:

- `新建任务`
- `查看归档` or `查看未归档`
- `设置回复`
- `查看状态`
- `压缩当前任务` when connected
- `解除连接` when connected
- `新建项目` only for owner/admin when project creation is enabled
- `上一页`, `下一页`, `退出`

The user may select a task by the number shown on the current page. Natural
phrases such as `新建一个叫周报的任务` may fill a wizard field, but they do
not bypass confirmation or change visibility.

## Mutating flows

Every client-impacting wizard action has a separate confirmation stage.

### Connect existing task

Show task title, project label, and full task ID. After `确认`, inspect or restore
that exact ID through Desktop tools, enforce one active Feishu-scope owner per
task, then atomically update the scope binding and project route. The displaced
task is retained; never archive or delete it.

### Create task

Ask for an optional title, then an allowed project, then confirmation. Task
titles are NFKC-normalized, single-line, contain no control characters, and are
at most 80 characters. An omitted title uses the Feishu conversation name.
Create with the documented non-empty routing-ready bootstrap. Preserve every
existing task and bind the new exact ID only after an unambiguous Desktop
success.

Regular authorized scopes may create only in their active registered project,
or the Bridge default project when none exists. Owner/admin may choose from the
bounded Desktop project catalog.

### Archived task

Selecting an archived task requires the same confirmation. Restore only that
exact ID, then bind it. Do not archive a displaced task.

### Compact

Offer compaction only when a current task exists. After confirmation, send the
native `/compact` to that same task and wait under the normal Gateway bounds.
Never restore another task, generate a replacement summary, or change binding.

### Disconnect and reply settings

Disconnect removes only the Feishu-to-task binding. It never deletes or archives
the task. Reply settings choose either final-only or bounded start/complete
notices; neither mode exposes reasoning or tool traces.

### Create project

Only owner/admin may enter this flow, and only when project creation was
explicitly enabled. Ask for one portable child-folder name and confirmation.
Use the existing staged-root recovery invariant and require an exact match in
Desktop `list_projects`; never select a nearby path or same-name project.

## Snapshot, expiry, and recovery

The full wizard snapshot is memory-only. Task titles, project labels, and local
project roots must never enter `sessions.json`; that file may retain only a
numeric expiry marker. The snapshot expires after ten minutes, and a restart,
expired marker, malformed state, or version mismatch performs no action and asks
the user to send `/init` again.

`取消` returns to the main menu without mutating anything. `退出` clears the
wizard. A Gateway retry reuses the event's deterministic request key. An unknown
mutating result is never replayed; a read-only catalog or inspection failure is
reported with `may_have_started=false`.

## Live compatibility canary

The finite scheduler canary is now the complete `/init` catalog-and-selection
flow:

1. Owner sends `/init` and receives the bounded Desktop catalog.
2. Owner selects the predeclared exact target from the snapshot.
3. Owner confirms the displayed title, project, and full task ID.
4. Bridge verifies the exact-scope binding and routes one ordinary message to
   that exact task.

An empty Gateway cycle, a catalog without selection, or an unsupported-command
reply is not a successful canary.
