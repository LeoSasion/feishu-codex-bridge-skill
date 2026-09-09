"""Exercise the naming upgrade without a real project, task, or chat."""

import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
PWSH = shutil.which("pwsh")


@unittest.skipUnless(os.name == "nt" and PWSH, "Windows PowerShell installer")
class InstallUpgradeTests(unittest.TestCase):
    def test_repeated_upgrade_preserves_state_and_checks_code(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            runtime = project / ".codex" / "feishu-codex-operator-runtime"
            runtime.mkdir(parents=True)
            preserved = {
                "operator.env": b"CODEX_OPERATOR_LIFECYCLE_MODE=manual\n",
                "sessions.json": b'{"fixture": "binding"}',
                "state.sqlite3": b"isolated inbox fixture",
                "callbacks.sqlite3": b"isolated callback fixture",
            }
            for name, value in preserved.items():
                (runtime / name).write_bytes(value)
            command = [
                PWSH, "-NoProfile", "-File",
                str(ROOT / "scripts" / "install-feishu-codex-operator.ps1"),
                "-ProjectRoot", str(project), "-Force", "-SkipHooks",
                "-SkipRuntimeConfig", "-SkipDesktopEntry",
            ]
            for _ in range(2):
                result = subprocess.run(
                    command, capture_output=True, text=True, timeout=60,
                )
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                self.assertTrue((runtime / "operator_core" / "runtime.py").is_file())
                self.assertTrue((runtime / "operator_core" / "beeper_provider.py").is_file())
                catalog = json.loads(
                    (runtime / "operator_core" / "beeper_model_catalog.json").read_text()
                )
                self.assertEqual("beeper", catalog["models"][0]["slug"])
                self.assertEqual("list", catalog["models"][0]["visibility"])
                for name, value in preserved.items():
                    self.assertEqual(value, (runtime / name).read_bytes(), name)
                manifest = json.loads((runtime / "runtime-manifest.json").read_text())
                startup = (ROOT / 'scripts' / 'start-feishu-codex-operator.ps1').read_text(encoding='utf-8')
                manifest_check = startup.split('function Assert-OperatorRuntimeManifest {', 1)[1]
                expected_block = re.search(r'\$expectedFiles = @\((.*?)\n\s*\)', manifest_check, re.S).group(1)
                startup_files = re.findall(r"'([^']+)'", expected_block)
                self.assertEqual(set(manifest['code_files']), set(startup_files),
                                 'The startup guard must accept exactly the installed inventory')
                self.assertIn("operator_core/runtime.py", manifest["code_files"])
                for relative, digest in manifest["code_files"].items():
                    self.assertEqual(
                        hashlib.sha256((runtime / relative).read_bytes()).hexdigest(),
                        digest,
                    )
                self.assertFalse((runtime / "operator.pid").exists())
            self.assertTrue((runtime / "backups").is_dir())
            # Test the installed status output, not spelling in its PowerShell source.
            public_rate = {
                "status": "cached", "limit_id": "fixture-limit", "remaining_percent": 80,
                "window_duration_minutes": 300, "reset_at": 2000000000,
                "beeper_model": "gpt-5.6-luna", "beeper_reasoning_effort": "low",
                "beeper_limit_id": None, "beeper_remaining_percent": None,
                "beeper_window_duration_minutes": None, "beeper_reset_at": None,
            }
            health = {
                "status": "stopped", "operator_version": manifest["operator_version"],
                "session_owner": "responder", "responder_writer": "beeper-task-send",
                "responder_transport": "beeper-relay", "responder_status_observer": "app-server-metadata-readonly",
                "catalog_transport": "app-server-readonly", "event_consumer": False, "pid": os.getpid(),
                "beeper_wake_signal": {"lease_active": False, "lease_seconds": 1800, "fallback_delay_seconds": 30},
                "active_turns": 0, "unknown_status_timeout_seconds": 300, "callback_grace_seconds": 20,
                "callback_queue": {"pending": 0}, "started_at": time.time(), "updated_at": time.time(),
                "account_rate_limits": {**public_rate, "account_id": "private-fixture-value",
                                        "unexpected_data": "private-fixture-value"},
            }
            (runtime / "health.json").write_text(json.dumps(health), encoding="utf-8")
            status = subprocess.run(
                [PWSH, "-NoProfile", "-File",
                 str(ROOT / "scripts" / "feishu-codex-operator.ps1"),
                 "operator", "status", "-ProjectRoot", str(project), "-Json"],
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(0, status.returncode, status.stdout + status.stderr)
            report = json.loads(status.stdout)
            self.assertTrue(report["installed_manifest"]["valid"], report)
            self.assertFalse(report["runtime"]["running"], report)
            snapshot = report["health_snapshot"]
            self.assertTrue(snapshot["valid"], report)
            self.assertEqual(snapshot["account_rate_limits"], public_rate)
            self.assertEqual(snapshot["beeper_wake_signal"], health["beeper_wake_signal"])
            for key in ("unknown_status_timeout_seconds", "callback_grace_seconds", "responder_status_observer"):
                self.assertEqual(snapshot[key], health[key])
            self.assertNotIn("private-fixture-value", status.stdout)
            for name, value in preserved.items():
                self.assertEqual(value, (runtime / name).read_bytes(), name)


if __name__ == "__main__":
    unittest.main()
