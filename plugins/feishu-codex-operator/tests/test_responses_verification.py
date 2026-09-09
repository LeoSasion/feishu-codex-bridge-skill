"""Evidence gate regressions using isolated local fixtures; no inference/tasks."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_responses_tools import ROUTE
from operator_core.model_registry import RouterError
from operator_core.responses_profiles import CORE_CHECKS, make_profile
from operator_core.responses_verification import REQUIRED, empty_ledger, inspect_ledger, format_verification_report


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / 'evidence.json'
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        self.versions = dict(cli_version='0.153.4', desktop_version='26.9.8', model_sha256='1' * 64)
        self.ledger = empty_ledger(deepcopy(ROUTE), **self.versions)
        self.cli = make_profile('synthetic-verification', deepcopy(ROUTE), [
            dict(case=c, status='passed', checked_at=self.stamp(), cli_version='0.153.4')
            for c in sorted(CORE_CHECKS)])

    def stamp(self, days=0):
        return (self.now + timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%SZ')

    def record(self, case, **overrides):
        n = len(self.ledger['records']) + 1
        turn = f'00000000-0000-0000-0000-{n:012d}'
        artifact = self.root / f'turn-{n}.json'
        commands = [dict(type='commandExecution', id=f'exec-{n}-{i}', status='completed', exitCode=0)
                    for i in range(2)]
        if case in ('desktop_exit_stop', 'desktop_tool_error_stop'):
            commands[-1].update(status='failed', exitCode=7)
        artifact.write_text(json.dumps(dict(id=turn,
            status='interrupted' if case == 'desktop_cancel' else 'completed', items=commands)), encoding='utf-8')
        record = dict(case=case, status='passed', checked_at=self.stamp(), run_id=f'case-{n}',
                      thread_id='11111111-1111-1111-1111-111111111111', turn_id=turn,
                      context='fresh', approval_policy='on-request', sandbox_mode='workspace-write',
                      network_access=False, request_retries=0, stream_retries=0,
                      artifact=artifact.name, artifact_sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),
                      assertions=dict.fromkeys(REQUIRED[case], True))
        record.update(overrides)
        self.ledger['records'].append(record)
        return record

    def inspect(self, **kwargs):
        self.path.write_text(json.dumps(self.ledger), encoding='utf-8')
        return inspect_ledger(self.path, deepcopy(ROUTE), now=self.now, profile=self.cli,
                              **(self.versions | kwargs))

    def fill(self):
        for case in REQUIRED:
            self.record(case)

    def test_empty_is_missing_not_verified(self):
        result = self.inspect()
        self.assertFalse(result['verified'])
        self.assertEqual(set(result['gates']), set(REQUIRED))
        self.assertTrue(all(g['reasons'] == ['missing'] for g in result['gates'].values()))

    def test_all_current_records_can_verify_only_scoped_report(self):
        self.fill()
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        result = self.inspect()
        self.assertTrue(result['verified'])
        self.assertTrue(result['desktop_verified'])
        self.assertFalse(result['global_ready'])
        self.assertFalse(result['catalog_changed'])
        self.assertEqual(result['upstream_requests'], 0)
        for name, raw in before.items():
            self.assertEqual(raw, (self.root / name).read_bytes())

    def test_guidance_and_incomplete_assertions_do_not_count_as_fresh_pass(self):
        r = self.record('desktop_file_edit', context='guided')
        self.assertIn('fresh_context_required', self.inspect()['gates']['desktop_file_edit']['reasons'])
        r['context'] = 'fresh'
        r['assertions']['tests_passed'] = False
        self.assertIn('assertions_incomplete', self.inspect()['gates']['desktop_file_edit']['reasons'])

    def test_permissions_retries_and_expiry_fail_closed(self):
        self.fill()
        r = self.ledger['records'][0]
        for key, value in [('approval_policy', 'never'), ('sandbox_mode', 'danger-full-access'),
                           ('network_access', True), ('request_retries', 1), ('stream_retries', 1)]:
            old = r[key]
            r[key] = value
            self.assertFalse(self.inspect()['verified'], key)
            r[key] = old
        r['checked_at'] = self.stamp(-31)
        self.assertFalse(self.inspect()['verified'])

    def test_model_runtime_and_verifier_changes_invalidate(self):
        self.fill()
        for key in self.ledger['binding']:
            old = self.ledger['binding'][key]
            self.ledger['binding'][key] = '0' * 64 if key.endswith('sha256') else 'changed'
            result = self.inspect()
            self.assertFalse(result['verified'], key)
            self.assertEqual(result['binding_changed'], [key])
            self.ledger['binding'][key] = old

    def test_cli_failures_or_stale_evaluator_prevent_overall_pass(self):
        self.fill()
        self.cli['checks'][0]['status'] = 'failed'
        result = self.inspect()
        self.assertTrue(result['desktop_verified'])
        self.assertFalse(result['verified'])
        self.cli['checks'][0]['status'] = 'passed'
        self.cli['evaluator_sha256'] = '0' * 64
        self.assertFalse(self.inspect()['verified'])

    def test_report_distinguishes_failures_missing_versions_and_retained_history(self):
        first, second, third = self.cli['checks'][:3]
        first['status'] = 'failed'
        second['cli_version'] = '0.153.3'
        self.cli['checks'].remove(third)
        self.record('desktop_file_edit', status='failed')
        result = self.inspect()
        self.assertEqual(result['recorded_failures'], 2)
        self.assertEqual(result['recorded_failure_counts'], {'desktop': 1, 'isolated_cli': 1})
        self.assertEqual(result['cli_gates'][first['case']]['reasons'], ['failed'])
        self.assertEqual(result['cli_gates'][second['case']]['reasons'], ['cli_version_changed'])
        self.assertEqual(result['cli_gates'][third['case']]['reasons'], ['missing'])
        self.assertEqual(result['gates']['desktop_file_edit']['reasons'], ['failed'])
        text = format_verification_report(result)
        self.assertIn('Recorded failures retained: 2 (CLI: 1; Desktop: 1)', text)
        self.assertIn(first['case'] + ': failed', text)
        self.assertIn(third['case'] + ': missing', text)
        self.assertNotIn(str(self.root), text)
        self.assertNotIn('turn-1.json', text)
        old = dict(first, checked_at=self.stamp(-1))
        self.cli['checks'].append(old)
        first['status'] = 'passed'
        result = self.inspect()
        self.assertTrue(result['cli_gates'][first['case']]['passed'])
        self.assertEqual(result['recorded_failures'], 2)
        self.cli['adapter_sha256'] = '0' * 64
        self.assertIn('adapter_changed', self.inspect()['cli_gates'][first['case']]['reasons'])

    def test_final_claim_without_tools_and_extra_call_after_failure_do_not_pass(self):
        r = self.record('desktop_exit_stop')
        path = self.root / r['artifact']
        obj = json.loads(path.read_text())
        for items in ([dict(type='agentMessage', text='passed')],
                      obj['items'] + [dict(type='commandExecution', id='extra', status='completed', exitCode=0)]):
            obj['items'] = items
            path.write_text(json.dumps(obj), encoding='utf-8')
            r['artifact_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertFalse(self.inspect()['gates']['desktop_exit_stop']['passed'])

    def test_new_failure_blocks_old_pass_and_failed_history_is_retained(self):
        self.fill()
        self.ledger['records'][0]['checked_at'] = self.stamp(-1)
        self.record('desktop_file_edit', status='failed')
        self.assertFalse(self.inspect()['verified'])
        self.assertEqual(self.inspect()['recorded_failures'], 1)
        self.ledger['records'][-1]['checked_at'] = self.stamp(-1)
        self.ledger['records'][0]['checked_at'] = self.stamp()
        self.assertTrue(self.inspect()['verified'])
        self.assertEqual(self.inspect()['recorded_failures'], 1)

    def test_changed_missing_cross_turn_and_path_escape_artifacts_rejected(self):
        r = self.record('desktop_file_edit')
        raw = (self.root / r['artifact']).read_bytes()
        (self.root / r['artifact']).write_bytes(raw + b' ')
        with self.assertRaisesRegex(RouterError, 'artifact_changed'):
            self.inspect()
        (self.root / r['artifact']).write_bytes(raw)
        r['turn_id'] = '22222222-2222-2222-2222-222222222222'
        with self.assertRaisesRegex(RouterError, 'turn_mismatch'):
            self.inspect()
        for name in ('../outside.json', 'C:/outside.json', 'evidence.json', 'missing.json'):
            r['artifact'] = name
            with self.assertRaises(RouterError):
                self.inspect()

    def test_duplicate_turn_ambiguous_time_and_invalid_types_rejected(self):
        r = self.record('desktop_file_edit')
        for key, value in [('request_retries', False), ('assertions', {'tests_passed': 'true'}),
                           ('checked_at', self.stamp(1)), ('extra', 'private')]:
            old = deepcopy(r)
            r[key] = value
            with self.assertRaises(RouterError):
                self.inspect()
            r.clear()
            r.update(old)
        self.record('desktop_file_edit')
        with self.assertRaisesRegex(RouterError, 'ambiguous'):
            self.inspect()
        self.ledger['records'][-1]['case'] = 'desktop_multiround'
        self.ledger['records'][-1]['assertions'] = dict.fromkeys(REQUIRED['desktop_multiround'], True)
        self.ledger['records'][-1]['turn_id'] = r['turn_id']
        with self.assertRaisesRegex(RouterError, 'duplicate'):
            self.inspect()

    def test_cli_init_status_and_exclusive_output_no_service(self):
        script = Path(__file__).resolve().parents[1] / 'scripts' / 'operator_model_router.py'
        row = self.root / 'registration.json'
        row.write_text(json.dumps(ROUTE), encoding='utf-8')
        common = ['--registration', str(row), '--cli-version', '0.153.4', '--desktop-version', '26.9.8',
                  '--model-sha256', '1' * 64]
        def run(action, *args):
            return subprocess.run([sys.executable, '-B', str(script), action, *common, *args],
                                  capture_output=True, text=True, timeout=15)
        self.assertEqual(run('verification-init', '--output', str(self.path)).returncode, 0)
        before = self.path.read_bytes()
        self.assertNotEqual(run('verification-init', '--output', str(self.path)).returncode, 0)
        result = run('verification-status', '--desktop-evidence', str(self.path))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)['verified'])
        text_result = run('verification-status', '--desktop-evidence', str(self.path), '--format', 'text')
        self.assertEqual(text_result.returncode, 0, text_result.stderr)
        self.assertIn('Local model verification: [unverified]', text_result.stdout)
        self.assertIn('current cli profile required', text_result.stdout)
        self.assertIn('desktop_approval_deny: missing', text_result.stdout)
        self.assertNotEqual(run('verification-init', '--output', str(self.root / 'unused.json'),
                                '--format', 'text').returncode, 0)
        self.assertFalse((self.root / 'unused.json').exists())
        self.assertEqual(before, self.path.read_bytes())
        profile_path = self.root / 'cli-profile.json'
        profile_path.write_text(json.dumps(self.cli), encoding='utf-8')
        result = run('verification-status', '--desktop-evidence', str(self.path), '--profile', str(profile_path))
        self.assertEqual(result.returncode, 0, result.stderr)
        changed = deepcopy(ROUTE)
        changed['model'] = 'different-current-model'
        row.write_text(json.dumps(changed), encoding='utf-8')
        result = run('verification-status', '--desktop-evidence', str(self.path), '--profile', str(profile_path))
        self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
