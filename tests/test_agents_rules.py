from __future__ import annotations

import os
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Optional
import unittest


SKILL_ROOT = Path(__file__).resolve().parents[1]
MERGE_SCRIPT = SKILL_ROOT / "scripts" / "merge-agents-rules.ps1"
DISPATCHER = SKILL_ROOT / "scripts" / "feishu-codex-bridge.ps1"
START_HOOK = SKILL_ROOT / "scripts" / "start-feishu-codex-bridge.ps1"
STOP_HOOK = SKILL_ROOT / "scripts" / "stop-feishu-codex-bridge.ps1"
QUEUE_HELPER = SKILL_ROOT / "scripts" / "router_queue.py"
FRAGMENT = SKILL_ROOT / "assets" / "AGENTS.feishu-codex-bridge.md"
TEST_TEMP_ROOT = Path(os.environ.get("FEISHU_BRIDGE_TEST_TMP", tempfile.gettempdir()))
if "FEISHU_BRIDGE_TEST_TMP" not in os.environ:
    TEST_TEMP_ROOT = TEST_TEMP_ROOT / "feishu-codex-bridge-tests"
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)


def powershell() -> Optional[str]:
    return shutil.which("pwsh") or shutil.which("powershell.exe")


@unittest.skipUnless(powershell(), "PowerShell is required")
class AgentsRulesMergeTests(unittest.TestCase):
    def run_merge(self, root: Path, *, check: bool = False) -> subprocess.CompletedProcess[str]:
        command = [
            str(powershell()),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(MERGE_SCRIPT),
            "-ProjectRoot",
            str(root),
        ]
        if check:
            command.append("-Check")
        return subprocess.run(command, capture_output=True, text=True, check=False)

    def test_append_is_idempotent_and_preserves_existing_content(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            root = Path(temporary)
            agents = root / "AGENTS.md"
            original = "# Existing rules\n\n- keep this\n"
            agents.write_text(original, encoding="utf-8")

            first = self.run_merge(root)
            self.assertEqual(0, first.returncode, first.stderr)
            once = agents.read_text(encoding="utf-8")
            self.assertTrue(once.startswith(original))
            self.assertEqual(1, once.count("FEISHU_CODEX_BRIDGE_RULES_START"))
            self.assertEqual(1, once.count("FEISHU_CODEX_BRIDGE_RULES_END"))

            second = self.run_merge(root)
            self.assertEqual(0, second.returncode, second.stderr)
            self.assertEqual(once, agents.read_text(encoding="utf-8"))

    def test_update_replaces_only_the_managed_block(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            root = Path(temporary)
            agents = root / "AGENTS.md"
            obsolete_marker = "obsolete-managed-rule-sentinel"
            agents.write_text(
                "before\n<!-- FEISHU_CODEX_BRIDGE_RULES_START -->\n"
                f"{obsolete_marker}\n"
                "<!-- FEISHU_CODEX_BRIDGE_RULES_END -->\nafter\n",
                encoding="utf-8",
            )

            result = self.run_merge(root)
            self.assertEqual(0, result.returncode, result.stderr)
            merged = agents.read_text(encoding="utf-8")
            self.assertTrue(merged.startswith("before\n"))
            self.assertTrue(merged.endswith("\nafter\n"))
            self.assertIn(FRAGMENT.read_text(encoding="utf-8").strip(), merged)
            self.assertNotIn(obsolete_marker, merged)

    def test_malformed_markers_fail_without_writing(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            root = Path(temporary)
            agents = root / "AGENTS.md"
            original = "before\n<!-- FEISHU_CODEX_BRIDGE_RULES_START -->\nunfinished\n"
            agents.write_text(original, encoding="utf-8")

            result = self.run_merge(root)
            self.assertNotEqual(0, result.returncode)
            self.assertEqual(original, agents.read_text(encoding="utf-8"))


@unittest.skipUnless(powershell(), "PowerShell is required")
class MachineReadableDiagnosticsTests(unittest.TestCase):
    def run_diagnostic(self, action: str, *, json_output: bool = True) -> subprocess.CompletedProcess[str]:
        command = [
            str(powershell()),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(DISPATCHER),
            "bridge",
            action,
            "-ProjectRoot",
            str(SKILL_ROOT),
        ]
        if json_output:
            command.append("-Json")
        return subprocess.run(command, capture_output=True, text=True, check=False, timeout=120)

    def test_json_diagnostics_emit_one_versioned_object(self) -> None:
        for action in ("status", "doctor", "validate"):
            with self.subTest(action=action):
                result = self.run_diagnostic(action)
                lines = [line for line in result.stdout.splitlines() if line.strip()]
                self.assertEqual(1, len(lines), result.stdout)
                payload = json.loads(lines[0])
                self.assertEqual(1, payload["schema_version"])
                self.assertEqual(f"bridge.{action}", payload["command"])
                if action == "doctor" and payload["status"] == "fail":
                    self.assertEqual(2, result.returncode, result.stderr)
                else:
                    self.assertEqual(0, result.returncode, result.stderr)
                expected_statuses = {"pass", "warning", "fail"} if action == "doctor" else {"pass", "warning"}
                self.assertIn(payload["status"], expected_statuses)

        status = json.loads(self.run_diagnostic("status").stdout)
        self.assertIn("runtime", status)
        self.assertIn("installed_manifest", status)
        self.assertIn("health_snapshot", status)
        self.assertIn("issue_codes", status["installed_manifest"])
        self.assertIsInstance(status["installed_manifest"]["issue_codes"], list)

        doctor = json.loads(self.run_diagnostic("doctor").stdout)
        self.assertIn("source_runtime_parity", doctor)
        self.assertIn("access_policy", doctor)
        self.assertIn("agents_rules", doctor)
        self.assertIn("missing_count", doctor["source_runtime_parity"])
        self.assertIn("mismatched_count", doctor["source_runtime_parity"])
        self.assertIsInstance(doctor["hooks"]["issue_codes"], list)
        self.assertIsInstance(doctor["environment"]["issue_codes"], list)

        validation = json.loads(self.run_diagnostic("validate").stdout)
        self.assertEqual("pass", validation["status"])
        self.assertFalse(validation["child_process_started"])
        self.assertIsNone(validation["error"])

        forbidden_keys = {
            "path",
            "project_root",
            "message",
            "prompt",
            "answer",
            "owner_open_id",
            "admin_open_ids",
            "allowed_user_open_ids",
            "allowed_chat_ids",
            "task_id",
            "thread_id",
            "chat_id",
        }

        def assert_safe_keys(value: object) -> None:
            if isinstance(value, dict):
                self.assertTrue(forbidden_keys.isdisjoint(value.keys()), value)
                for child in value.values():
                    assert_safe_keys(child)
            elif isinstance(value, list):
                for child in value:
                    assert_safe_keys(child)

        for payload in (status, doctor, validation):
            assert_safe_keys(payload)
            serialized = json.dumps(payload, ensure_ascii=False)
            self.assertNotIn(str(SKILL_ROOT), serialized)
            self.assertNotRegex(serialized, r'"(?:ou|oc)_[^"]+"')

    def test_json_is_rejected_for_unrelated_actions(self) -> None:
        result = self.run_diagnostic("preflight")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("supported only", result.stderr + result.stdout)


@unittest.skipUnless(powershell(), "PowerShell is required")
class BridgePidIdentityTests(unittest.TestCase):
    def test_stop_waits_through_transient_unverifiable_exit_identity(self) -> None:
        stop_text = STOP_HOOK.read_text(encoding="utf-8")
        wait_start = stop_text.index("$deadline = (Get-Date).AddSeconds(20)")
        final_probe = stop_text.rindex(
            "$identity = Get-BridgeProcessIdentity -ProcessId $bridgePid",
            wait_start,
        )
        wait_block = stop_text[wait_start:final_probe]
        final_block = stop_text[final_probe:]

        self.assertIn("if (-not $identity.Verified)", wait_block)
        self.assertIn("Start-Sleep -Milliseconds 500", wait_block)
        self.assertIn("continue", wait_block)
        self.assertNotIn(
            "changed to an unverifiable Python process; refusing to force-stop it",
            wait_block,
        )
        self.assertIn(
            "changed to an unverifiable Python process; refusing to force-stop it",
            final_block,
        )

    def test_foreign_reused_pid_is_never_reported_or_stopped_as_listener(self) -> None:
        shell = str(powershell())
        holder = subprocess.Popen(
            [shell, "-NoProfile", "-Command", "Start-Sleep -Seconds 60"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
                project = Path(temporary)
                runtime = project / ".codex" / "feishu-bridge"
                hooks = project / ".codex" / "hooks"
                runtime.mkdir(parents=True)
                hooks.mkdir(parents=True)
                (runtime / "bridge.py").write_text("# identity canary\n", encoding="utf-8")
                pid_path = runtime / "bridge.pid"
                pid_path.write_text(str(holder.pid), encoding="ascii")

                status = subprocess.run(
                    [
                        shell,
                        "-NoProfile",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        str(DISPATCHER),
                        "bridge",
                        "status",
                        "-ProjectRoot",
                        str(project),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=30,
                )
                self.assertEqual(0, status.returncode, status.stderr)
                self.assertIn("Runtime: stopped", status.stdout)
                self.assertIn("not this Bridge Listener", status.stdout)

                danger = project / "unsafe-old-stop-hook-ran.txt"
                installed_stop = hooks / STOP_HOOK.name
                installed_stop.write_text(
                    f"Set-Content -LiteralPath '{danger}' -Value unsafe\n",
                    encoding="utf-8",
                )
                public_stop = subprocess.run(
                    [
                        shell,
                        "-NoProfile",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        str(DISPATCHER),
                        "bridge",
                        "stop",
                        "-ProjectRoot",
                        str(project),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=30,
                )
                self.assertEqual(0, public_stop.returncode, public_stop.stderr)
                self.assertIn("No process was stopped", public_stop.stdout)
                self.assertFalse(danger.exists())
                self.assertIsNone(holder.poll())
                self.assertFalse(pid_path.exists())

                shutil.copy2(STOP_HOOK, installed_stop)
                pid_path.write_text(str(holder.pid), encoding="ascii")
                direct_stop = subprocess.run(
                    [
                        shell,
                        "-NoProfile",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        str(installed_stop),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=30,
                )
                self.assertEqual(0, direct_stop.returncode, direct_stop.stderr)
                self.assertIn("No process was stopped", direct_stop.stdout)
                self.assertIsNone(holder.poll())
                self.assertFalse(pid_path.exists())
        finally:
            if holder.poll() is None:
                holder.terminate()
            try:
                holder.wait(timeout=10)
            except subprocess.TimeoutExpired:
                holder.kill()
                holder.wait(timeout=10)


@unittest.skipUnless(powershell(), "PowerShell is required")
class BridgeEnvEntrypointTests(unittest.TestCase):
    runtime_files = (
        "bridge.py",
        "router_queue.py",
        "bridge_core/__init__.py",
        "bridge_core/codex_client.py",
        "bridge_core/config.py",
        "bridge_core/desktop_router.py",
        "bridge_core/lark.py",
        "bridge_core/project_routing.py",
        "bridge_core/runtime.py",
        "bridge_core/state.py",
    )

    def stage_isolated_runtime(self, root: Path, env_text: str) -> Path:
        runtime = root / ".codex" / "feishu-bridge"
        hooks = root / ".codex" / "hooks"
        runtime.mkdir(parents=True)
        hooks.mkdir(parents=True)
        for relative in self.runtime_files:
            source = SKILL_ROOT / "scripts" / relative
            target = runtime / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        shutil.copy2(START_HOOK, hooks / START_HOOK.name)
        shutil.copy2(STOP_HOOK, hooks / STOP_HOOK.name)
        (runtime / "bridge.env").write_text(env_text, encoding="utf-8")
        return runtime

    @staticmethod
    def isolated_environment() -> dict[str, str]:
        environment = dict(os.environ)
        environment.pop("CODEX_BRIDGE_CHILD", None)
        for name in tuple(environment):
            if name.startswith("CODEX_BRIDGE_"):
                environment.pop(name, None)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        return environment

    def test_malformed_bridge_env_fails_closed_at_dispatcher_start_and_queue_entrypoints(
        self,
    ) -> None:
        invalid_cases = {
            "duplicate": (
                "CODEX_BRIDGE_ACCESS_MODE=locked\n"
                "CODEX_BRIDGE_ACCESS_MODE=compat\n",
                ("duplicate", "duplicate", "duplicate"),
            ),
            "empty_enum": (
                "CODEX_BRIDGE_ACCESS_MODE=\n",
                ("one of", "not one of", "supported choice"),
            ),
            "empty_boolean": (
                "CODEX_BRIDGE_ALLOW_PROJECT_CREATE=\n",
                ("boolean", "boolean", "boolean"),
            ),
            "invalid_boolean": (
                "CODEX_BRIDGE_ALLOW_PROJECT_CREATE=maybe\n",
                ("boolean", "boolean", "boolean"),
            ),
            "empty_integer": (
                "CODEX_BRIDGE_ROUTER_TIMEOUT=\n",
                ("integer", "integer", "integer"),
            ),
            "malformed_integer": (
                "CODEX_BRIDGE_ROUTER_TIMEOUT=thirty\n",
                ("integer", "integer", "integer"),
            ),
            "out_of_range_integer": (
                "CODEX_BRIDGE_ROUTER_TIMEOUT=29\n",
                (
                    "codex_bridge_router_timeout",
                    "codex_bridge_router_timeout",
                    "codex_bridge_router_timeout",
                ),
            ),
        }
        for case_name, (env_text, expected_errors) in invalid_cases.items():
            with self.subTest(case=case_name), tempfile.TemporaryDirectory(
                dir=TEST_TEMP_ROOT
            ) as temporary:
                project = Path(temporary)
                runtime = self.stage_isolated_runtime(project, env_text)
                env_path = runtime / "bridge.env"
                original_env = env_path.read_bytes()
                environment = self.isolated_environment()

                dispatcher = subprocess.run(
                    [
                        str(powershell()),
                        "-NoProfile",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        str(DISPATCHER),
                        "bridge",
                        "start",
                        "-ProjectRoot",
                        str(project),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    env=environment,
                    timeout=30,
                )
                self.assertNotEqual(0, dispatcher.returncode)
                dispatcher_output = (dispatcher.stdout + dispatcher.stderr).lower()
                self.assertIn(
                    "refusing to start with an invalid bridge.env",
                    dispatcher_output,
                )
                self.assertIn(
                    expected_errors[0],
                    dispatcher_output,
                )
                self.assertNotIn("runtime manifest is missing", dispatcher_output)
                self.assertNotIn("stale or incomplete installed", dispatcher_output)

                start_hook = subprocess.run(
                    [
                        str(powershell()),
                        "-NoProfile",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        str(project / ".codex" / "hooks" / START_HOOK.name),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    env=environment,
                    timeout=30,
                )
                self.assertNotEqual(0, start_hook.returncode)
                self.assertIn(
                    expected_errors[1],
                    (start_hook.stdout + start_hook.stderr).lower(),
                )

                queue_helper = subprocess.run(
                    [
                        sys.executable,
                        "-B",
                        str(QUEUE_HELPER),
                        "--runtime-dir",
                        str(runtime),
                        "status",
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    env=environment,
                    timeout=30,
                )
                self.assertNotEqual(0, queue_helper.returncode)
                self.assertIn(
                    expected_errors[2],
                    (queue_helper.stdout + queue_helper.stderr).lower(),
                )

                self.assertEqual(original_env, env_path.read_bytes())
                self.assertFalse((runtime / "bridge.pid").exists())
                self.assertEqual([], list((runtime / "leases").glob("*.json")))
                self.assertFalse((runtime / "desktop-router").exists())


if __name__ == "__main__":
    unittest.main()
