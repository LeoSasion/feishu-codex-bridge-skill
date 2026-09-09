"""Stopped label updates with isolated registries and reviewed synthetic evidence."""
from copy import deepcopy
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import test_responses_verification as fixtures
from operator_core.model_registry import RouterError
from operator_core.responses_labels import PROCESS_CHECK, label_update, assert_stopped


class LabelTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.VerificationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        f.fill()
        f.inspect()
        self.root = f.root
        self.state = self.root / '.codex' / 'feishu-codex-operator-runtime' / 'model-router'
        self.state.mkdir(parents=True)
        self.state = self.state.resolve()
        self.row = deepcopy(fixtures.ROUTE)
        self.row['display_name'] = 'Fixture model [unverified]'
        other = deepcopy(self.row)
        other.update(slug='local/other', model='other-model', display_name='Other unchanged')
        self.value = {'version': 2, 'models': [self.row, other]}
        self.target = self.state / 'registry.json'
        self.target.write_text(json.dumps(self.value), encoding='utf-8')
        self.before = self.target.read_bytes()
        self.expected = hashlib.sha256(self.before).hexdigest()
        self.profile = self.root / 'profile.json'
        self.profile.write_text(json.dumps(f.cli), encoding='utf-8')

    def run_update(self, **kwargs):
        return label_update(self.state, self.row['slug'], self.fixture.path, self.profile,
                            self.fixture.versions, **kwargs)

    def apply(self, **kwargs):
        with patch('operator_core.responses_labels.assert_stopped') as guard:
            result = self.run_update(apply=True, expected_sha256=self.expected, **kwargs)
        return result, guard.call_count

    def test_preview_is_readonly_and_exact_alias_only(self):
        with patch('operator_core.responses_labels.assert_stopped', side_effect=AssertionError('preview must not stop')):
            result = self.run_update()
        self.assertEqual(result['after'], 'Fixture model [verified]')
        self.assertFalse(result['applied'])
        self.assertEqual(self.before, self.target.read_bytes())
        self.assertEqual([p.name for p in self.state.iterdir()], ['registry.json'])

    def test_apply_changes_only_display_and_retains_original_backup(self):
        result, checks = self.apply()
        self.assertTrue(result['applied'])
        self.assertEqual(checks, 2)
        actual = json.loads(self.target.read_text())
        expected = deepcopy(self.value)
        expected['models'][0]['display_name'] = 'Fixture model [verified]'
        self.assertEqual(actual, expected)
        self.assertEqual((self.state / result['backup_name']).read_bytes(), self.before)
        self.assertFalse((self.state / 'registry-edit.lock').exists())
        self.expected = hashlib.sha256(self.target.read_bytes()).hexdigest()
        result, _ = self.apply()
        self.assertFalse(result['applied'])

    def test_new_failed_evidence_demotes_without_deleting_history(self):
        self.apply()
        self.expected = hashlib.sha256(self.target.read_bytes()).hexdigest()
        self.fixture.ledger['records'][0]['status'] = 'failed'
        self.fixture.inspect()
        result, _ = self.apply()
        self.assertEqual(result['after'], 'Fixture model [unverified]')
        self.assertTrue(result['applied'])
        self.assertEqual(result['verification']['recorded_failures'], 1)

    def test_evidence_is_rechecked_after_preview_and_edited_artifact_refused(self):
        self.assertIn('[verified]', self.run_update()['after'])
        artifact = self.root / self.fixture.ledger['records'][0]['artifact']
        artifact.write_bytes(artifact.read_bytes() + b' ')
        with self.assertRaisesRegex(RouterError, 'artifact_changed'):
            self.apply()
        self.assertEqual(self.before, self.target.read_bytes())
        self.assertFalse((self.state / 'registry-edit.lock').exists())

    def test_changed_registry_and_unknown_alias_refused(self):
        self.target.write_bytes(self.before + b' ')
        with self.assertRaisesRegex(RouterError, 'changed_since'):
            self.apply()
        self.target.write_bytes(self.before)
        with self.assertRaisesRegex(RouterError, 'one_adapted'):
            label_update(self.state, 'missing', self.fixture.path, self.profile, self.fixture.versions)

    def test_lock_and_backup_conflicts_leave_registry_unchanged(self):
        lock = self.state / 'registry-edit.lock'
        lock.write_text('other transaction')
        with self.assertRaises(FileExistsError):
            self.apply()
        self.assertEqual(lock.read_text(), 'other transaction')
        lock.unlink()
        (self.state / f'verification-label-before-{self.expected}.json').write_text('conflict')
        with self.assertRaisesRegex(RouterError, 'backup_conflict'):
            self.apply()
        self.assertEqual(self.before, self.target.read_bytes())

    def test_lifecycle_failure_never_writes_and_second_check_catches_race(self):
        for effects in ([RouterError('not_stopped')], [None, RouterError('not_stopped')]):
            with patch('operator_core.responses_labels.assert_stopped', side_effect=effects):
                with self.assertRaisesRegex(RouterError, 'not_stopped'):
                    self.run_update(apply=True, expected_sha256=self.expected)
            self.assertEqual(self.before, self.target.read_bytes())

    def test_actual_guard_rejects_active_entry_before_process_query(self):
        (self.state / 'codex-entry.json').write_text('{}')
        with patch('operator_core.responses_labels.subprocess.run', side_effect=AssertionError('must not launch')):
            with self.assertRaises(RouterError):
                assert_stopped(self.state, self.fixture.versions['desktop_version'])

    @unittest.skipUnless(os.name == 'nt', 'Windows lifecycle observation')
    def test_guard_requires_stopped_processes_matching_version_and_empty_callbacks(self):
        runtime = self.state.parent
        (runtime / 'operator_main.py').write_text('# isolated fixture')
        with closing(sqlite3.connect(runtime / 'callbacks.sqlite3')) as db:
            db.execute('create table final_callback_requests (state text)')
            db.commit()
        version = self.fixture.versions['desktop_version']
        def check(observed):
            response = SimpleNamespace(returncode=0, stdout=json.dumps(observed).encode())
            with patch('operator_core.responses_labels.subprocess.run', return_value=response):
                assert_stopped(self.state, version)
        good = dict(desktop_running=0, operator_running=0, router_running=0, desktop_version=version)
        check(good)
        for patch_value in ({'desktop_running': 1}, {'operator_running': 1}, {'router_running': 1},
                            {'desktop_version': 'changed'}, {'desktop_running': False}):
            with self.assertRaises(RouterError):
                check(good | patch_value)
        with closing(sqlite3.connect(runtime / 'callbacks.sqlite3')) as db:
            db.execute("insert into final_callback_requests values ('captured')")
            db.commit()
        with self.assertRaisesRegex(RouterError, 'callbacks_must_be_empty'):
            check(good)

    def test_missing_cli_profile_keeps_label_unverified(self):
        result = label_update(self.state, self.row['slug'], self.fixture.path, None, self.fixture.versions)
        self.assertFalse(result['change_required'])
        self.assertEqual(result['after'], 'Fixture model [unverified]')

    @unittest.skipUnless(os.name == 'nt', 'Windows PowerShell process classifier')
    def test_actual_powershell_classifier_distinguishes_router_from_label_command(self):
        pwsh = Path(os.environ['ProgramFiles']) / 'PowerShell/7/pwsh.exe'
        prefix = r'''
function Get-AppxPackage { param($Name) [pscustomobject]@{InstallLocation='C:\fixture\desktop';Version='26.9.8'} }
function Get-CimInstance {
    [pscustomobject]@{Name='ChatGPT.exe';ExecutablePath='C:\fixture\desktop\app\ChatGPT.exe';CommandLine='desktop'}
    [pscustomobject]@{Name='ChatGPT.exe';ExecutablePath='C:\unrelated\ChatGPT.exe';CommandLine='unrelated'}
    [pscustomobject]@{Name='python.exe';CommandLine='python "C:\fixture\runtime\operator_model_router.py" serve --port 54321'}
    [pscustomobject]@{Name='python.exe';CommandLine='python "C:\fixture\runtime\operator_model_router.py" verification-label'}
    [pscustomobject]@{Name='python.exe';CommandLine='python C:\fixture\runtime\operator_main.py'}
}
'''
        env = dict(os.environ, CODEX_OPERATOR_LABEL_RUNTIME=r'C:\fixture\runtime')
        result = subprocess.run([str(pwsh), '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', prefix + PROCESS_CHECK],
                                capture_output=True, timeout=15, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), dict(desktop_running=1, operator_running=1,
                                                       router_running=1, desktop_version='26.9.8'))

    def test_atomic_failure_retains_original_and_releases_lock(self):
        with patch('operator_core.responses_labels.settings.atomic_write', side_effect=OSError('simulated')):
            with self.assertRaises(OSError):
                self.apply()
        self.assertEqual(self.before, self.target.read_bytes())
        self.assertFalse((self.state / 'registry-edit.lock').exists())

    def test_cli_preview_and_missing_digest_apply(self):
        script = Path(__file__).resolve().parents[1] / 'scripts/operator_model_router.py'
        command = [sys.executable, '-B', str(script), 'verification-label', '--state-dir', str(self.state),
            '--slug', self.row['slug'], '--profile', str(self.profile), '--desktop-evidence', str(self.fixture.path),
            '--cli-version', self.fixture.versions['cli_version'], '--desktop-version', self.fixture.versions['desktop_version'],
            '--model-sha256', self.fixture.versions['model_sha256']]
        result = subprocess.run(command, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)['applied'])
        result = subprocess.run(command + ['--apply'], capture_output=True, text=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            result = subprocess.run(command + ['--apply', '--expected-registry-sha256', self.expected,
                '--port', str(listener.getsockname()[1])], capture_output=True, text=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('router_port_in_use', result.stderr)
        self.assertEqual(self.before, self.target.read_bytes())


if __name__ == '__main__':
    unittest.main()
