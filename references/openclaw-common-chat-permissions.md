# OpenClaw-style QR-first common chat authorization

Use this profile for first use. It follows the current OpenClaw/PersonalAgent
shape: create the bot through the official Feishu QR registration flow, keep
DMs owner-locked and groups allowlisted/@-gated, then use lark-cli device-flow
QR authorization for ordinary user-side CLI capabilities. This QR-first path
does not replace developer-console configuration for missing Bot tenant scopes.

## Approved profile: `openclaw-common-chat`

The profile consists of two ordered scans:

1. `lark-cli config init --new` — one-click PersonalAgent app creation and
   local CLI credential configuration.
2. `lark-cli auth login --recommend --no-wait --json` — the current CLI's
   recommended/common user scopes across its supported domains.

`--recommend` is not necessarily small or read-only. Its exact cross-domain
scope set changes with the installed CLI and may contain many write scopes.
Describe that breadth before starting the flow and treat the Feishu approval
page as the authoritative list the user personally reviews. Granting a scope
never authorizes a later operation by itself.

For each no-wait login, forward `verification_url` unchanged, generate a PNG
with `lark-cli auth qrcode`, display the link before the image, and end the
turn. Only after the user reports completion may the Skill run:

```powershell
lark-cli auth login --device-code <device_code>
```

Complete the device code before continuing. A restarted flow invalidates the
earlier code; never cache or reuse an expired URL/code.
Store each QR in its own system-temp directory and delete it on completion,
failure, denial, or expiry.
If the Desktop sandbox rejects that exact directory, request filesystem access
only for the per-run QR directory and retry generation once with the same URL.
Never restart the authorization merely to obtain a writable output path.

### Migrated or sandbox-hidden keychain diagnosis

Do not copy `.lark-cli` from another computer and do not treat a retained
`config.json`, active profile, App ID, or user name as proof of authentication.
The CLI may keep only metadata there while its App Secret and user token remain
in the old machine's OS keychain. However, Codex filesystem sandboxing can also
hide an otherwise valid Windows Credential Manager entry from the child CLI.
The same apparent diagnostic pattern can therefore have two causes:

- the app/profile and user metadata exist;
- `auth list` reports `no_token` or `whoami --as user` reports unavailable /
  `missing`;
- `whoami --as bot` reports unavailable / `not_configured`; and
- the Bot-scope audit cannot run because no Bot identity is usable.

Do not classify that pattern yet. Request access only to read the current
Windows credential entries and repeat `lark-cli auth status --json --verify`
once in a credential-visible process. If that result is `ready`/`valid`, do
not rerun config-init or OAuth; perform any owner Open ID extraction in the same
credential-visible process and keep the ID out of shared output. If it remains
`missing`/`not_configured`, classify it as missing local credentials rather
than a tenant-scope failure.

For confirmed missing local credentials after an earlier flow failed or
expired, stop and obtain fresh explicit approval before opening another QR
flow. With that approval, run exactly one official
`lark-cli config init --new`, let the user complete the QR page, then run the
normal `--recommend` user OAuth flow. The official re-init may recover the
same PersonalAgent profile; it is not safe to promise either reuse or creation
in advance. Inspect the result without printing real App/User IDs, then verify
user, Bot, and tenant scopes independently. Never copy old keychain entries,
remove/switch profiles, or recreate OAuth merely to work around an audit error.

### Windows QR temp-directory compatibility

Create the dedicated directory with a PowerShell form supported by Windows
PowerShell 5.1, such as `New-Item -ItemType Directory -Path <dir>`, and set
fail-fast behavior before changing directories. Confirm both directory creation
and `Push-Location` succeeded before running `lark-cli auth qrcode`; its relative
`--output` path resolves against the actual process working directory.

If the CLI reports `unsafe output path: cannot resolve symlinks: Access is
denied` for the system temp directory, request access only to that exact
per-run directory and retry QR generation once with the same opaque URL. Do not
restart config-init or OAuth. Windows may expose the same temp root once as an
8.3 path and once as a long path; cleanup validation must accept only a proven
equivalent temp root while retaining the exact per-run name/prefix check. If a
working-directory failure leaves a QR in the Skill/project, relocate it to the
verified temp directory immediately (or remove it if the flow failed) and
verify that no project artifact remains.

## QR authorization is not all Bot chat permissions

`auth login` grants **user OAuth** scopes. Even `--domain im` would grant the
CLI's user-side IM domain; it would not declare or approve Bot tenant scopes.
Do not add it automatically or present it as a Bot-permission repair. Granting
user scopes also does not authorize the Skill or Bridge to perform later
message, chat-administration, recall, urgent-delivery, member, moderation, or
other high-impact actions; each operation retains its normal intent and
confirmation gates.

The Bridge listener itself runs as the **Bot**. Bot tenant scopes are distinct
from the user OAuth granted by `auth login`. PersonalAgent QR registration
supplies the app/bot capabilities defined by Feishu's registration service, but
the Skill must not infer that every tenant scope exists merely because the QR
flows succeeded.

In particular, the default runtime remains locked to approved users/groups and
requires an @ mention in groups. Do not claim that the Bot can receive every
ordinary non-mention group message unless a live Bot-scope audit proves the
relevant tenant permission. `im:message.group_msg` is an administrator-approved
Bot capability, not evidence from user OAuth.

## Verification and missing Bot scopes

After the OAuth flow completes:

```powershell
lark-cli auth status --json --verify
lark-cli api GET /open-apis/application/v6/scopes --as bot
```

Report three independent outcomes:

1. user OAuth is valid;
2. Bot credentials are valid;
3. Bot tenant scopes were explicitly audited.

The first two do not prove the third. Do not use `auth scopes --json` as the
Bot audit after user login: it may return `tokenType: user` and user scopes.
The explicit `--as bot` application endpoint is the read-only Bot-scope audit;
inspect tenant scope names and `grant_status`.

A rate limit or temporary audit failure is not permission denial. Allow at most
two total audit attempts: for transient EOF/timeout, wait two seconds and retry
once; for 429, honor `Retry-After` only up to 30 seconds and retry once.
Otherwise report and stop. Never restart OAuth to bypass an audit failure.
If online `auth status --verify` times out, report local token validity and live
verification separately; only say `verified` when the returned value is true.

When a Bot command returns `missing_scopes` and `console_url`, forward the exact
`console_url` and wait for the user or tenant administrator. Do not run
`auth login` as a Bot-scope repair, do not call an administrator-approval API
automatically, and do not recreate the app. Prefer the developer-console UI
when the user does not want JSON; provide an exact batch-import payload only
when the user explicitly chooses that input method.
If the requested behavior genuinely requires a tenant scope unavailable to a
PersonalAgent app, state that limitation and offer the supported self-built-app
path as a separate, explicitly approved setup change.

Permissions do not authorize access-policy changes, event-subscription changes,
app publication, Listener mounting, Gateway creation, or scheduler heartbeat activation.
