# Feishu Desktop frontend takeover: detection, installation, and login

Use this reference only after the user explicitly asks about taking over the
Feishu frontend, controlling the client, or automating a real client
conversation test. Do not inspect installation or login during generic setup,
first use, `operator preflight`, permission work, mounting, or headless Operator
operation. The Operator runtime and Feishu CLI do not require the desktop client.

## State model

Report these states separately:

1. **Not installed**: no Feishu process, known executable, or uninstall entry.
2. **Installed, not running**: an executable is present but no client process is
   visible.
3. **Running, login unknown**: a process or window exists, but its contents have
   not been inspected.
4. **Logged out**: the current client window visibly shows a QR, phone, account,
   or other authentication screen.
5. **Logged in**: the current client window visibly shows the main workspace
   with navigation or a chat list.

Installation, a running process, registry state, and cached files do not prove
login. Never infer the active account from copied cache or token files.

## Install boundary

Use the official page [https://www.feishu.cn/download](https://www.feishu.cn/download)
and the current Windows package metadata returned by the official
`/api/package_info` endpoint. Before installation, disclose the source,
user/machine install scope, approximate download size when known, PATH/restart
impact, and that the user must complete authentication. When installation is in
the current request, resolve those details and run the install automatically
without a second consent prompt:

```powershell
feishu-codex-operator.ps1 feishu desktop-install
```

Only run this command when installation is included in the user's request.

The helper selects Windows x64 or native Windows metadata, accepts only HTTPS
downloads from `feishucdn.com`, validates the API-provided MD5 and a valid
Authenticode signature, uses the official documented
`--command=quiet_install` mode, verifies the installed executable, and removes
its dedicated system-temp directory. Do not silently switch to a mirror or
retain the installer after success or failure.

If the client is already present, do not reinstall merely to determine login.
Run:

```powershell
feishu-codex-operator.ps1 feishu desktop-status
```

## Login inspection

When Windows app control is available, launch the exact returned executable and
inspect one current client window. Observation is read-only:

- a main workspace means logged in;
- a QR/account screen means logged out;
- an ambiguous splash, update, error, permission, or multi-account screen means
  unknown.

If the app-control launch attempt times out, do not loop or claim the client
failed to start. When one launch is in the current request, start the exact
verified executable once through the platform launcher, then re-list windows.
If screenshot capture fails with `SetIsBorderRequired` or another
capture-interface error, discard the stale window state, reselect the unique
Feishu window, and retry once with accessibility text only. Text such as
`passport`, `扫码登录`, or `请使用 飞书/豆包移动端 扫码` is sufficient evidence
of logged-out state; never guess from a blank capture.

Accessibility output can include the visible chat list and message previews.
Inspect only the minimum text needed to classify login and identify the exact
user-requested test responder. Do not persist, summarize, quote, or inject
unrelated chats into Operator logs, responder prompts, tests, or Skill files.

Accessibility-only capture may prove login while still providing no actionable
element geometry. If an input-area click returns
`coordinate input geometry is unavailable`, or focus remains on the document
root, do not call `type_text`, guess coordinates, or switch to custom UI
automation. Classify the frontend as **logged in, observation-only**, provide
the exact command for the user to send manually, and continue Operator-side
observation after the user reports sending it.

If Computer Use rejects an input because user activity made the observation
stale, do not reuse the old element index. Re-observe once, locate only the
exact requested chat composer, and retry the same focus action once only when
that composer is still unique. This refresh does not widen an observation-only
request into typing or sending. Stop on a second stale-state error or any
geometry failure.

Do not automate an authentication dialog, credentials, CAPTCHA, security
prompt, privacy permission, account choice, or QR authorization. The user must
complete this genuine platform-owned interaction. After the user reports
completion, refresh the same window and continue automatically.

Installing or logging in does not widen the current request to sending a
message. When the user explicitly requested a live conversation test, that
request covers the exact test send: keep it within the named dedicated test
contact/group, execute it without a second approval prompt, and verify both the
visible reply and Operator logs. Otherwise stop before the representational
action and request that outcome explicitly.

## Stable Windows compatibility lessons

- Query current official metadata for every install. Never pin a historical
  URL, version, byte count, checksum, signer observation, or download time.
- If the exact official metadata request is blocked by the sandbox, use the
  supported network-access path for that request only. Do not start a generic
  consent loop or switch to a mirror.
- A verified installation and one visible process/window do not prove login.
- Screenshot capture may fail with `SetIsBorderRequired ... 0x80004002`. After
  reselecting the unique window, allow one accessibility-text-only observation.
  Minimize the read because it may expose visible chat names or previews.
- Accessibility can prove a QR/login page or main workspace without proving safe
  input. If composer focus fails with `coordinate input geometry is
  unavailable` or remains on the document root, classify the client as
  observation-only. Never type blindly; ask the user to send the exact test
  message manually.
- A stale observation permits at most one fresh observation and one retry of the
  same uniquely identified focus action. It does not repair missing geometry.
- Installing, logging in, or observing the client never widens the requested
  scope to sending. A live send proceeds automatically only when the current
  request explicitly includes that exact representational action.
