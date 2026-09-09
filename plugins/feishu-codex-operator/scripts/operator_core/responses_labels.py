"""Explicit stopped label transaction, separate from read-only verification.

No inference, task control, service restart, permission change or cache edit.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess

from . import model_router_config as settings
from .model_registry import ModelRegistry, RouterError
from .responses_verification import inspect_ledger


PROCESS_CHECK = r"""
$ErrorActionPreference = 'Stop'
$packages = @(Get-AppxPackage -Name 'OpenAI.Codex')
if ($packages.Count -ne 1) { throw 'Desktop installation is ambiguous' }
$prefix = [IO.Path]::GetFullPath($packages[0].InstallLocation).TrimEnd('\') + '\'
$desktop = 0
$operator = 0
$router = 0
$operatorPath = [IO.Path]::GetFullPath((Join-Path $env:CODEX_OPERATOR_LABEL_RUNTIME 'operator_main.py'))
$routerPath = [IO.Path]::GetFullPath((Join-Path $env:CODEX_OPERATOR_LABEL_RUNTIME 'operator_model_router.py'))
foreach ($p in @(Get-CimInstance Win32_Process)) {
    if ($p.Name -ieq 'ChatGPT.exe') {
        if ([string]::IsNullOrWhiteSpace($p.ExecutablePath) -or
            [IO.Path]::GetFullPath($p.ExecutablePath).StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
            $desktop++
        }
    }
    if ($p.Name -match '^python') {
        if ([string]::IsNullOrWhiteSpace($p.CommandLine)) { throw 'Python process identity unavailable' }
        if ($p.CommandLine.Replace('/', '\').IndexOf($operatorPath, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
            $operator++
        }
        if ($p.CommandLine.Replace('/', '\').IndexOf($routerPath, [StringComparison]::OrdinalIgnoreCase) -ge 0 -and
            $p.CommandLine -match 'operator_model_router\.py"?\s+serve(?:\s|$)') {
            $router++
        }
    }
}
[ordered]@{desktop_running=$desktop;operator_running=$operator;router_running=$router;desktop_version=[string]$packages[0].Version} |
    ConvertTo-Json -Compress
"""


def assert_stopped(state, desktop_version):
    """Observe the exact installed Windows lifecycle; never stop anything."""
    state = Path(state)
    if (os.name != 'nt' or state.resolve() != state.absolute()
            or state.name != 'model-router' or state.parent.name != 'feishu-codex-operator-runtime'
            or state.parent.parent.name != '.codex'):
        raise RouterError('label_update_requires_standard_windows_runtime')
    journal = state / 'codex-entry.json'
    if journal.exists() or journal.is_symlink():
        raise RouterError('deactivate_before_label_update')
    runtime = state.parent
    if not (runtime / 'operator_main.py').is_file():
        raise RouterError('label_runtime_identity_unavailable')
    pwsh = Path(os.environ.get('ProgramFiles', '')) / 'PowerShell' / '7' / 'pwsh.exe'
    if not pwsh.is_absolute() or not pwsh.is_file():
        raise RouterError('label_lifecycle_powershell_unavailable')
    env = dict(os.environ, CODEX_OPERATOR_LABEL_RUNTIME=str(runtime))
    try:
        result = subprocess.run([str(pwsh), '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', PROCESS_CHECK],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=10, env=env,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode or len(result.stdout) > 4096:
            raise ValueError('invalid process observation')
        observed = settings.loads(result.stdout.decode('utf-8-sig'))
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        raise RouterError('label_lifecycle_observation_failed') from exc
    if (not isinstance(observed, dict)
            or set(observed) != {'desktop_running', 'operator_running', 'router_running', 'desktop_version'}
            or any(type(observed[k]) is not int or observed[k] < 0
                   for k in ('desktop_running', 'operator_running', 'router_running'))):
        raise RouterError('label_lifecycle_observation_failed')
    if observed['desktop_running'] or observed['operator_running'] or observed['router_running']:
        raise RouterError('desktop_operator_and_router_must_be_stopped_for_label_update')
    if observed['desktop_version'] != desktop_version:
        raise RouterError('desktop_version_changed_before_label_update')
    db = runtime / 'callbacks.sqlite3'
    if db.is_symlink() or not db.is_file():
        raise RouterError('label_callback_state_unavailable')
    try:
        connection = sqlite3.connect(db.as_uri() + '?mode=ro', uri=True, timeout=1)
        try:
            pending = connection.execute("select count(*) from final_callback_requests where state in ('pending','captured')").fetchone()[0]
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise RouterError('label_callback_state_unavailable') from exc
    if pending:
        raise RouterError('callbacks_must_be_empty_for_label_update')


def _snapshot(state, slug):
    target = Path(state) / 'registry.json'
    if target.is_symlink() or not target.is_file():
        raise RouterError('registry_requires_regular_file')
    with target.open('rb') as handle:
        raw = handle.read(1048577)
    if len(raw) > 1048576:
        raise RouterError('registration_file_too_large')
    value = settings.loads(raw)
    catalog = json.loads(Path(__file__).with_name('beeper_model_catalog.json').read_text(encoding='utf-8'))
    ModelRegistry(value, catalog)
    matches = [r for r in value['models'] if r['slug'] == slug]
    if len(matches) != 1 or not matches[0].get('responses') or slug == 'beeper':
        raise RouterError('label_update_requires_one_adapted_registration')
    return raw, value, matches[0], catalog


def _evaluate(raw, row, ledger, profile_path, versions):
    # Always read the original ledger/profile again, never trust a saved verdict.
    profile = settings.read_registration(Path(profile_path)) if profile_path else None
    status = inspect_ledger(Path(ledger), row, profile=profile, **versions)
    before = row.get('display_name', row['slug'])
    base = re.sub(r'\s*\[(?:unverified|verified)\]$', '', before).rstrip()
    if not base or re.search(r'\[(?:unverified|verified)\]', base):
        raise RouterError('ambiguous_existing_verification_label')
    after = base + ' ' + status['recommended_label']
    return {'slug': row['slug'], 'registry_sha256': hashlib.sha256(raw).hexdigest(),
            'before': before, 'after': after, 'change_required': before != after,
            'verification': status, 'applied': False}


def label_update(state, slug, ledger, profile_path, versions, *, apply=False, expected_sha256=None):
    """Caller reserves the inactive router port for the entire apply operation.

    Preview is read-only and may run with Desktop open. Apply additionally
    checks stopped lifecycle twice and exclusively locks the registry. Versions
    and model digest must still be independently measured by the caller.
    """
    state = Path(state).absolute()
    if not apply:
        raw, _, row, _ = _snapshot(state, slug)
        return _evaluate(raw, row, ledger, profile_path, versions)
    if not isinstance(expected_sha256, str) or not re.fullmatch(r'[a-f0-9]{64}', expected_sha256):
        raise RouterError('expected_registry_digest_required_for_label_update')
    assert_stopped(state, versions['desktop_version'])
    lock = state / 'registry-edit.lock'
    with lock.open('xb') as handle:
        try:
            raw, value, row, catalog = _snapshot(state, slug)
            if hashlib.sha256(raw).hexdigest() != expected_sha256:
                raise RouterError('registry_changed_since_label_preview')
            result = _evaluate(raw, row, ledger, profile_path, versions)
            if not result['change_required']:
                return result
            updated = deepcopy(value)
            next(r for r in updated['models'] if r['slug'] == slug)['display_name'] = result['after']
            ModelRegistry(updated, catalog)
            assert_stopped(state, versions['desktop_version'])
            target = state / 'registry.json'
            if target.is_symlink() or target.read_bytes() != raw:
                raise RouterError('registry_changed_during_label_update')
            # Content-addressed original, created exclusively and never overwritten.
            backup = state / ('verification-label-before-' + expected_sha256 + '.json')
            if backup.exists():
                if backup.is_symlink() or backup.read_bytes() != raw:
                    raise RouterError('label_backup_conflict')
            else:
                with backup.open('xb') as out:
                    out.write(raw)
                    out.flush()
                    os.fsync(out.fileno())
            settings.atomic_write(target, (json.dumps(updated, ensure_ascii=True, indent=2) + '\n').encode())
            result.update(applied=True, backup_name=backup.name)
            return result
        finally:
            handle.close()
            lock.unlink()
