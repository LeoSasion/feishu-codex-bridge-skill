"""Exercise the naming upgrade without a real project, task, or chat."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
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
                "-SkipRuntimeConfig",
            ]
            for _ in range(2):
                result = subprocess.run(
                    command, capture_output=True, text=True, timeout=60,
                )
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                self.assertTrue((runtime / "operator_core" / "runtime.py").is_file())
                for name, value in preserved.items():
                    self.assertEqual(value, (runtime / name).read_bytes(), name)
                manifest = json.loads((runtime / "runtime-manifest.json").read_text())
                self.assertIn("operator_core/runtime.py", manifest["code_files"])
                for relative in ("routing_cli.py", "operator_core/__init__.py", "operator_core/final_callback.py"):
                    self.assertEqual(
                        hashlib.sha256((runtime / relative).read_bytes()).hexdigest(),
                        manifest["code_files"][relative],
                    )
                self.assertIn("from operator_core import main", (runtime / "operator_main.py").read_text())
                self.assertFalse((runtime / "operator.pid").exists())
            self.assertTrue((runtime / "backups").is_dir())
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
            for name, value in preserved.items():
                self.assertEqual(value, (runtime / name).read_bytes(), name)


if __name__ == "__main__":
    unittest.main()
