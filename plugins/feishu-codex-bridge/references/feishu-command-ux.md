# Feishu `/init` current UX contract

This reference defines the identity, visibility, confirmation, and no-replay
rules for binding. Current v1 now admits one deliberately small executable
subset: a non-archived read-only catalog, immutable selection, confirmed exact
task inspection, and an atomic local binding. Historical Beeper surfaces remain
permanently tombstoned.

## One-command contract

`/init` is the only recognized Feishu slash command. Every other slash command
is unsupported: intercept it before responder routing, execute nothing, forward
nothing, and reply exactly:

> 飞书 Bridge 仅支持 `/init`。请发送 `/init` 进入设置。

This is a hard removal, not an alias layer. There is no per-command compatibility
table or hidden legacy implementation.

An ordinary message is never published through a retired producer surface. New
accepted work may consume one isolated local grant and queue one opaque page to
the newly registered Beeper. Pre-enable terminal rows are never adopted or
retried.

## Catalog visibility

The isolated producer lets `/init` take a ten-minute immutable catalog snapshot
through the new Beeper's bounded read-only catalog operation. It must never
reuse the tombstoned Beeper command or read Codex databases, rollout files,
task messages, or local UI state. Desktop list responses can contain untrusted
summaries; the Beeper must ignore them and never return or persist them.

- Owner/admin: list up to 50 non-archived Codex tasks across Desktop projects.
- Other authorized users, chats, and topics: list only exact task IDs already
  related to that stable Feishu scope.
- Archived tasks are not admitted.
- Always exclude every historical Beeper and the installed Bridge namespace's
  Beeper. None may be displayed,
  selected, restored, compacted, archived, or bound as a business responder.
- Display project label, task title, and the full exact task ID. Never display
  project paths, task summaries, prompts, messages, queue metadata, or Beeper
  metadata.
- Titles and project labels are untrusted display text. Normalize control
  characters and never use a title for identity or authorization.
- Number choices are page-local and resolve only against the transient snapshot.
  Never rerun a search and reinterpret a number.

An empty exact-scope catalog must remain empty. It must never widen into the
owner/admin view.

## Main menu

The first page may show the current connection, projects with tasks, page
position, and only `上一页`, `下一页`, and `退出`. A number selects one task on
the current immutable page. `取消` returns from confirmation without mutation.
The wizard is bound to the initiating Feishu user and role; another group member
cannot continue, confirm, or replace that snapshot with an ordinary message.

New task/project, archived-task restore, compact, disconnect, and reply-setting
actions are intentionally absent from this minimum release.

## Confirm and bind one existing task

The single client-impacting wizard action keeps its confirmation stage and the
isolated pre-dispatch local grant. After a binding succeeds, append one
plain-language notice that rare duplicate or missed execution remains possible
and irreversible actions should be avoided. That notice is informational and
must not add a second confirmation stage.

The experiment may show task title, project label, and full task ID.
After `确认`, it must inspect only that exact non-archived ID through its
bounded operation-scoped read-only coordination surface, enforce one active
Feishu-scope owner per task, then atomically update the local binding. The
displaced task is retained; never archive or delete it.

Of catalog display text, only the sanitized task title and project label may
cross the helper boundary. They travel with the stable protocol IDs inside one
sealed ephemeral staging artifact bound to the exact request, operation, fence,
and snapshot. The Bridge verifies and consumes that stage once for the current
wizard, then it is scrubbed or terminally aged out. A late, duplicate, stale,
partial, or tampered stage cannot be consumed or committed. Project roots and
paths are never admitted.

The durable binding may contain only stable task/host/project IDs and a bounded
operation receipt. It stores no display text or path. The receipt proves only
that the local inspected selection and binding compare-and-swap used the same
operation identities; it is not product-level caller/turn attestation and does
not provide product `run_once`.

Task/project creation, restore, archive, compact, disconnect, and reply-mode
changes are unavailable. They have no hidden menu, compatibility branch, or
alternate execution route in the current contract.

## Snapshot, expiry, and recovery invariants

After its one fenced stage consumption, the full wizard snapshot and its task
titles/project labels are memory-only. They never enter the durable binding or
`sessions.json`; that file may retain only a numeric expiry marker. Project roots
and paths are rejected rather than staged. The snapshot expires after ten
minutes, and a restart, expired marker, malformed state, or version mismatch
performs no action and asks the user to send `/init` again.

`取消` returns to the main menu without mutating anything. `退出` clears the
wizard. The current producer must use one deterministic event key and
must never replay an unknown mutating result; a genuinely read-only catalog or
inspection failure remains `may_have_started=false`. Historical clients publish
nothing and cannot create a retry generation, recover a responder, or adopt a
temporary binding.

## Current live boundary

After exact runtime/plugin installation, fresh Beeper creation and isolated
registration, one fresh Feishu event may exercise the experiment. Never replay
an older held row or manufacture success from historical receipts. Gate B/Soak
validate isolated source semantics and do not certify live delivery or product
exactly-once behavior.
