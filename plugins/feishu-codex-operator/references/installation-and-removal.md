# Initialization and safe removal

Before its first write, `operator init` explains that it will configure the
current user's desktop and Start menu `Codex拓展入口` shortcuts, preserve any same-name originals,
and add project rules. Runtime installation explains the same scope before
installing Hooks and code. This notice is part of initialization, not an extra
permission prompt after an owner has already requested that scope.

The launcher is built locally under `.codex/operator-desktop-entry`; no binary
is published. Its executable and Windows product/title metadata use `Codex拓展入口`;
official `Codex` shortcuts remain separate. It resolves the installed Codex application dynamically. An
ordinary new installation opens native Codex. It does not activate the optional
Responses router or infer an LM Studio endpoint/capability policy. A separately
reviewed local-model startup bundle can be attached through `operator desktop-entry
-StartupBundle <bundle>`. An existing running Desktop is only opened. A cold
launch performs the already-configured startup workflow once, with its original
service, digest and no-replay checks. No background polling is introduced.

Windows-generated MSIX shortcuts and taskbar pins are not rewritten. Users may
need to pin the new launcher once. Shortcut name collisions or ownership by
another project stop setup; an installer never takes ownership merely because
a file has the expected name. Old `Codex.exe` installations require a reviewed
rename; restoration still recognizes their journaled shortcut paths and retained
launcher. Upgrading a runtime does not replace shortcut settings.

Repeated entry setup checks the existing executable and entry script against
their build record before replacing either file or starting a new ownership
generation. Missing files, invalid records or changed fingerprints stop setup.
The configuration must still reference the recorded entry script. A changed
managed shortcut also stops before the launcher is rebuilt, even if its new
target happens to be native Codex. Unchanged upgrades and completed-uninstall
reactivation retain their original recovery behavior. These checks do not attest
to historical configuration fields that were never recorded as fingerprints.

## Original files and recovery

The private `.codex/operator-installation/ownership.json` journal records exact
target paths, original fingerprints and installed fingerprints. Original bytes
are retained separately before an intent is published or a target is modified.
Upgrades retain the first original. Writes are atomic per file, not a claim of
an atomic transaction across every file. An interrupted operation remains
recoverable from the journal, without automatically replaying a startup or task.

The journal covers the managed project rules file, Hook configuration, two Hook
scripts and selected current-user shortcuts. It does not own arbitrary files,
the native application, taskbar registry data, user task history or model weights.
Restore previews are read-only. Restore validates the complete file set first;
changed target files, altered backups, linked paths or unknown targets block the
operation. It never overwrites a later user edit to force a successful uninstall.
An existing installation with a missing ownership journal stops both recovery
and installation. A missing journal is never treated as an empty file set.

After a completed uninstall, ordinary `operator init` / `operator install`
can start another installation. The previous journal and uninstall receipt are
retained under private `operator-installation/history`; original-file backups
and archived runtime data remain intact. A partially completed uninstall, an
unavailable completion receipt or changed restored files stops reinstallation.
The retained taskbar launcher is checked before reactivation. Reinstallation
starts in native mode unless a reviewed startup bundle is explicitly supplied;
it does not reuse an old workflow that may refer to archived runtime state.
`operator init -StartupBundle <bundle>` preserves that explicit selection.

## Safe uninstall command

Run `operator uninstall` to review the exact recovery plan. Finish pending
callbacks and requests and stop the exact Operator first. If the optional global
router entry is active, close Desktop before restoring its configuration.

`operator uninstall -Apply` rechecks the current state, detaches only the exact
owned router entry, stops its request-free router once, and unregisters only
this runtime's Final Callback mapping. It then restores original files and
removes files created by this installation. Unrelated callback registrations,
settings, tasks and other applications remain untouched.

The detached runtime, including local state and retained data, is moved to a
unique `.codex/operator-uninstalled` archive, never recursively deleted. A small
launcher remains in native-only mode so existing taskbar pins still open Codex.
That mode uses the Windows app installation directly and survives plugin-source
removal; it does not start the router or depend on plugin code or PowerShell 7.
The retained archive and recovery journal remain private.

Only after the command reports success should the user remove the plugin in
Codex Desktop. The plugin declares no supported pre-uninstall handler; clicking
Remove in Desktop alone is not claimed to run project recovery. The setup notice
states this order before installation. Do not invent a removal Hook or modify
the app's uninstall implementation.

Older installations without an original ownership record require a separately
reviewed migration. Do not treat the latest runtime backup as the original, and
do not make old data appear to be a fresh-install receipt. A stopped recovery
plan can preserve original shortcut receipts and identify exact owned Hook/rule
fragments for removal, with its provenance stated separately.
