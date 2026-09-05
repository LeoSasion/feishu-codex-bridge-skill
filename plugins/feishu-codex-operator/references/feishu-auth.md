# Feishu authentication and chat permissions

Use this reference only for setup/authentication diagnosis. Operator runs as the
Bot; user OAuth, Bot credentials, and Bot tenant permissions are separate checks.

## Initial setup

When setup is requested, the existing CLI workflow is:

1. `lark-cli config init --new` for official Feishu PersonalAgent QR registration.
2. `lark-cli auth login --recommend --no-wait --json` for user OAuth.
3. After the user completes the official page,
   `lark-cli auth login --device-code <device_code>`.

`--recommend` can include broad write scopes and changes with the CLI. Explain
its breadth; the authorization page is the list the user reviews. Forward the
returned verification URL unchanged and keep each QR in a dedicated temporary
directory. Do not print or persist codes/tokens in project files, restart a flow
to repair an audit failure, or reuse expired URLs.

## Verify independently

```powershell
lark-cli auth status --json --verify
lark-cli api GET /open-apis/application/v6/scopes --as bot
```

Report user validity, Bot validity, and tenant-scope audit separately.
`auth scopes` may show the user's scopes and is not the Bot audit.
Check tenant `grant_status`. User OAuth does not grant missing Bot tenant scopes;
follow the returned `console_url` and wait for the user/admin when required.

Locked identities and group @ gating remain the defaults. Receiving arbitrary
non-mention group messages requires the appropriate Bot tenant permission; a
successful QR login does not establish it. Permission grants do not authorize
unrelated future message or administration operations.

## Windows failures worth distinguishing

- Metadata/App ID in a copied `.lark-cli` directory is not proof of credentials:
  the secret/token may still live in the old machine's OS keychain.
- The filesystem sandbox can also hide valid credentials from a child CLI.
  Before reinitializing, use the supported credential-visible process for one
  read-only `auth status --json --verify` check. Do not print credential contents.
  If valid, reuse the existing setup; if still missing, report missing local
  credentials rather than a tenant-permission failure.
- Create the per-run QR directory and confirm the working directory before QR
  generation. An inaccessible temp path is not an OAuth failure. Correct only
  that directory's access and retry QR rendering with the same URL; never
  restart authorization merely to get a writable location.
- Local token metadata, live identity verification, and client UI login are
  distinct evidence. A timeout is unknown, not denial. For a read-only transient
  audit error, retry at most once (2 seconds for EOF/timeout, bounded Retry-After
  up to 30 seconds for 429); otherwise report the failure.
- Clean up the exact temporary QR directory after success, failure, denial, or
  expiry. Do not copy keychain entries, switch profiles, or recreate the app as
  a generic repair.
