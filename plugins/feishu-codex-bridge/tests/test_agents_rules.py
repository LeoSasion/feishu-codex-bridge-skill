from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import codecs
import os
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Optional
import unittest


SKILL_ROOT = Path(__file__).resolve().parents[1]
MERGE_SCRIPT = SKILL_ROOT / "scripts" / "merge-agents-rules.ps1"
DISPATCHER = SKILL_ROOT / "scripts" / "feishu-codex-bridge.ps1"
INSTALLER = SKILL_ROOT / "scripts" / "install-feishu-codex-bridge.ps1"
START_HOOK = SKILL_ROOT / "scripts" / "start-feishu-codex-bridge.ps1"
STOP_HOOK = SKILL_ROOT / "scripts" / "stop-feishu-codex-bridge.ps1"
QUEUE_HELPER = SKILL_ROOT / "scripts" / "beeper_queue_cli.py"
FRAGMENT = SKILL_ROOT / "assets" / "AGENTS.feishu-codex-bridge.md"
ROOT_AGENTS = SKILL_ROOT.parents[1] / "AGENTS.md"
USAGE_GUIDE = SKILL_ROOT / "feishu-codex-bridge-skill.md"
UPGRADE_GUIDE = SKILL_ROOT / "upgrade-bridge.md"
TEST_TEMP_ROOT = Path(os.environ.get("FEISHU_BRIDGE_TEST_TMP", tempfile.gettempdir()))
if "FEISHU_BRIDGE_TEST_TMP" not in os.environ:
    TEST_TEMP_ROOT = TEST_TEMP_ROOT / "feishu-codex-bridge-tests"
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)


def powershell() -> Optional[str]:
    return shutil.which("pwsh") or shutil.which("powershell.exe")


@unittest.skipUnless(powershell(), "PowerShell is required")
class AgentsRulesMergeTests(unittest.TestCase):
    def test_dispatcher_has_utf8_bom_for_windows_powershell_51(self) -> None:
        self.assertTrue(DISPATCHER.read_bytes().startswith(codecs.BOM_UTF8))

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
            obsolete_marker = "obsolete-managed-rule-marker"
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

    def test_fragment_uses_canonical_source_and_latest_first_capabilities(self) -> None:
        fragment = FRAGMENT.read_text(encoding="utf-8")
        for marker in (
            "`plugins/feishu-codex-bridge` is the only project-local Bridge source root",
            "Detect capabilities latest-first",
            "Do not add or keep an executable compatibility branch solely for an older",
            "The owner has explicitly designated this",
            "must never be described as product-level `run_once`",
        ):
            self.assertIn(marker, fragment)
        self.assertNotIn("`assets/desktop-beeper-", fragment)

    def test_producer_is_isolated_and_historical_beeper_is_terminal(
        self,
    ) -> None:
        fragment = FRAGMENT.read_text(encoding="utf-8")
        # External P0-B snapshots intentionally contain only the canonical plugin
        # root. Check the project-managed mirror when that enclosing file exists.
        if ROOT_AGENTS.is_file():
            self.assertEqual(fragment, ROOT_AGENTS.read_text(encoding="utf-8"))
        for marker in (
            "`beeper`",
            "`producer_unavailable_no_retry`",
            "newly created\n  Beeper",
            "Every selected Desktop responder remains sole owner",
            "An outcome with `may_have_started=true` is terminal and never automatically",
            "Completion accepts only `final_callback_source=final_callback`",
            "Bridge-owned at-most-once attempt",
            "reply once in plain language",
            "The notice is informational, not a repeated",
        ):
            self.assertIn(marker, fragment)

        dispatcher = DISPATCHER.read_text(encoding="utf-8")
        for code_boundary in (
            "historical_beeper_rules_tombstoned",
            "historical_namespace_closed",
            "producer_unavailable_no_retry",
        ):
            self.assertIn(code_boundary, dispatcher)

        usage = USAGE_GUIDE.read_text(encoding="utf-8")
        usage_semantics = " ".join(usage.split())
        for marker in (
            "当前版本使用一个与历史路线隔离的本地 producer",
            "启用前的终态消息不会被接管或补发",
            "每个 installed Bridge namespace 恰好一个独立 Beeper",
            "每个 responder task 独占自己的项目、上下文、模型、工具、执行与 final",
            "Responder-owned Final Callback",
            "未知结果不重放",
            "飞书成功绑定 Desktop task 时只提示一次",
            "提示不要求再次确认",
            "普通开发按影响运行最小 focused gate",
            "Gate B 是 release gate",
            "Soak 只在 concurrency/persistence/retry/transport",
        ):
            self.assertIn(marker, usage_semantics)

        upgrade = UPGRADE_GUIDE.read_text(encoding="utf-8")
        stable_rules = (
            "R-AUTH",
            "R-PRODUCER",
            "R-BEEPER",
            "R-REPLAY",
            "R-FINAL",
            "R-READY",
            "R-DOC",
        )
        for marker in stable_rules:
            self.assertEqual(1, upgrade.count(f"| {marker} |"), marker)
        self.assertNotIn("| R-EVIDENCE |", upgrade)
        self.assertNotIn("| R-OWNER |", upgrade)
        self.assertIn("`bridge validate` 不读取或绑定其中的版本、数量、状态措辞", upgrade)


@unittest.skipUnless(powershell(), "PowerShell is required")
class MachineReadableDiagnosticsTests(unittest.TestCase):
    @staticmethod
    def is_canonical_development_source() -> bool:
        marketplace = SKILL_ROOT.parents[1] / ".agents" / "plugins" / "marketplace.json"
        if not marketplace.is_file():
            return False
        scripts_path = str(SKILL_ROOT / "scripts")
        if scripts_path not in sys.path:
            sys.path.insert(0, scripts_path)
        try:
            import source_route_contract
        except ImportError:
            return False
        try:
            route = source_route_contract.evaluate(str(SKILL_ROOT), str(marketplace))
        except (OSError, ValueError, source_route_contract.SourceRouteError):
            return False
        return (
            route.get("status") == "pass"
            and route.get("role") == "canonical-development"
            and route.get("route_verified") is True
            and route.get("development_source_eligible") is True
            and route.get("installed_snapshot_diagnostic_only") is False
        )

    def run_diagnostic(
        self,
        action: str,
        *,
        json_output: bool = True,
        project_root: Path = SKILL_ROOT,
    ) -> subprocess.CompletedProcess[str]:
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
            str(project_root),
        ]
        if json_output:
            command.append("-Json")
        return subprocess.run(command, capture_output=True, text=True, check=False, timeout=120)

    def test_json_diagnostics_emit_one_versioned_object(self) -> None:
        canonical_development_source = self.is_canonical_development_source()
        dispatcher = DISPATCHER.read_text(encoding="utf-8")
        validate_start = dispatcher.index("function Invoke-BridgeValidate")
        validate_end = dispatcher.index(
            "function Update-ProcessPathFromEnvironment", validate_start
        )
        validate_source = dispatcher[validate_start:validate_end]
        for forbidden_handoff_dependency in (
            "Join-Path $repositoryRoot 'HANDOFF.md'",
            "$handoffText = if",
            "if ($handoffText)",
            "HANDOFF.md is missing current open-work marker",
        ):
            self.assertNotIn(forbidden_handoff_dependency, validate_source)

        actions = ("status", "doctor", "readiness", "validate")
        start_barrier = threading.Barrier(len(actions))

        def run_concurrently(action: str) -> subprocess.CompletedProcess[str]:
            start_barrier.wait(timeout=10)
            return self.run_diagnostic(action)

        with ThreadPoolExecutor(max_workers=len(actions)) as executor:
            results = dict(zip(actions, executor.map(run_concurrently, actions)))

        payloads: dict[str, dict[str, object]] = {}
        for action in actions:
            with self.subTest(action=action):
                result = results[action]
                lines = [line for line in result.stdout.splitlines() if line.strip()]
                self.assertEqual(1, len(lines), result.stdout)
                payload = json.loads(lines[0])
                payloads[action] = payload
                self.assertEqual(1, payload["schema_version"])
                self.assertEqual(f"bridge.{action}", payload["command"])
                if action == "readiness":
                    self.assertEqual(2, result.returncode, result.stderr)
                    self.assertEqual("blocked", payload["status"])
                elif action == "validate" and not canonical_development_source:
                    self.assertEqual(2, result.returncode, result.stderr)
                    self.assertEqual("fail", payload["status"])
                elif action == "doctor" and payload["status"] == "fail":
                    self.assertEqual(2, result.returncode, result.stderr)
                else:
                    self.assertEqual(0, result.returncode, result.stderr)
                if action == "readiness":
                    expected_statuses = {"blocked"}
                elif action == "validate" and not canonical_development_source:
                    expected_statuses = {"fail"}
                elif action == "doctor":
                    expected_statuses = {"pass", "warning", "fail"}
                else:
                    expected_statuses = {"pass", "warning"}
                self.assertIn(payload["status"], expected_statuses)

        status = payloads["status"]
        self.assertIn("runtime", status)
        self.assertIn("installed_manifest", status)
        self.assertIn("health_snapshot", status)
        latest_fidelity = status["health_snapshot"].get("latest_delivery_fidelity")
        if latest_fidelity is not None:
            self.assertEqual({"fidelity", "transforms"}, set(latest_fidelity))
            self.assertIn(
                latest_fidelity["fidelity"],
                {"identity", "explicit_transform", "unknown", "not_applicable"},
            )
        observation = status["health_snapshot"].get(
            "mvp_observation"
        )
        if observation is not None:
            self.assertEqual(
                {
                    "schema_version",
                    "status",
                    "answer_free",
                    "producer_namespace",
                    "final_callback_source",
                    "feishu_delivery_observed",
                    "known_delivery_fidelity_observed",
                    "single_inbox_claim_observed",
                    "bridge_outbox_scrubbed",
                },
                set(observation),
            )
        self.assertIn("issue_codes", status["installed_manifest"])
        self.assertIsInstance(status["installed_manifest"]["issue_codes"], list)

        doctor = payloads["doctor"]
        self.assertIn("source_runtime_parity", doctor)
        self.assertIn("access_policy", doctor)
        self.assertIn("agents_rules", doctor)
        self.assertIn("historical_beeper_rules", doctor)
        self.assertTrue(
            doctor["historical_beeper_rules"][
                "historical_beeper_rules_tombstoned"
            ]
        )
        self.assertFalse(
            doctor["historical_beeper_rules"]["allow_prefix_present"]
        )
        self.assertIn("missing_count", doctor["source_runtime_parity"])
        self.assertIn("mismatched_count", doctor["source_runtime_parity"])
        self.assertIsInstance(doctor["hooks"]["issue_codes"], list)
        self.assertIsInstance(doctor["environment"]["issue_codes"], list)

        readiness = payloads["readiness"]
        self.assertTrue(readiness["answer_free"])
        self.assertEqual("blocked", readiness["status"])
        self.assertFalse(readiness["production"]["eligible"])
        self.assertTrue(
            readiness["installation_integrity"]["checks"]
            ["historical_beeper_rules_tombstoned"]
        )
        self.assertEqual(
            {
                "installation_integrity",
                "mvp",
                "hook_review",
                "scheduler_surface",
                "task_tool_surface",
                "live_e2e",
                "future_surface_evidence",
                "terminal_markers",
                "production",
            },
            {
                key
                for key in readiness
                if key
                not in {"schema_version", "command", "status", "answer_free"}
            },
        )
        self.assertEqual(
            ["native_final_readback_unsupported"],
            readiness["terminal_markers"]["contract_forbidden_codes"],
        )
        self.assertFalse(readiness["mvp"]["production_equivalent"])
        self.assertEqual(
            [
                "product_run_once_unavailable",
                "final_callback_caller_turn_attestation_unavailable",
            ],
            readiness["mvp"]["accepted_risk_codes"],
        )
        self.assertFalse(readiness["production"]["eligible"])
        self.assertEqual("closed", readiness["terminal_markers"]["historical_state"])
        self.assertEqual("retired", readiness["terminal_markers"]["evidence_status"])
        self.assertEqual([], readiness["terminal_markers"]["historical_observed_codes"])
        self.assertTrue(
            readiness["terminal_markers"]["historical_namespace_closed"]
        )
        self.assertFalse(readiness["terminal_markers"]["exact_surface_attested"])
        self.assertFalse(readiness["hook_review"]["machine_verifiable"])
        self.assertFalse(readiness["hook_review"]["visible_review_observed"])
        self.assertEqual("blocked", readiness["scheduler_surface"]["status"])
        self.assertEqual("blocked", readiness["task_tool_surface"]["status"])
        self.assertIn(
            "run_once_runtime_attestation_unverified",
            readiness["scheduler_surface"]["blocker_codes"],
        )
        self.assertIn(
            "run_once_task_tool_surface_unverified",
            readiness["task_tool_surface"]["blocker_codes"],
        )
        self.assertFalse(readiness["scheduler_surface"]["exact_surface_attested"])
        self.assertFalse(
            readiness["scheduler_surface"]["automation_identity_attested"]
        )
        self.assertFalse(
            readiness["scheduler_surface"]["immutable_runtime_receipt_attested"]
        )
        self.assertFalse(
            readiness["scheduler_surface"]["single_beeper_attested"]
        )
        self.assertFalse(
            readiness["scheduler_surface"]["beeper_role_isolation_attested"]
        )
        self.assertFalse(readiness["scheduler_surface"]["historical_terminal_observed"])
        self.assertFalse(
            readiness["task_tool_surface"]["immutable_runtime_receipt_attested"]
        )
        self.assertFalse(
            readiness["task_tool_surface"]["desktop_responder_ownership_attested"]
        )
        self.assertFalse(
            readiness["task_tool_surface"]["task_coordination_policy_attested"]
        )
        self.assertFalse(
            readiness["task_tool_surface"]
            ["alternate_responder_client_exclusion_attested"]
        )
        for blocker in (
            "single_beeper_unverified",
            "beeper_role_isolation_unverified",
            "desktop_responder_ownership_unverified",
            "task_coordination_policy_unverified",
            "alternate_responder_client_exclusion_unverified",
        ):
            self.assertIn(blocker, readiness["production"]["blocker_codes"])
        self.assertFalse(readiness["live_e2e"]["exact_source_attested"])
        self.assertFalse(readiness["live_e2e"]["product_final_callback_attested"])
        self.assertEqual("unavailable", readiness["future_surface_evidence"]["status"])
        self.assertTrue(readiness["future_surface_evidence"]["answer_free"])
        self.assertFalse(
            readiness["future_surface_evidence"]["controller_provenance_supported"]
        )
        self.assertFalse(
            readiness["future_surface_evidence"]["production_gate_passed"]
        )
        self.assertEqual(
            ["run_once_evidence_unavailable"],
            readiness["future_surface_evidence"]["issue_codes"],
        )

        validation = payloads["validate"]
        self.assertEqual(
            "pass" if canonical_development_source else "fail",
            validation["status"],
        )
        self.assertFalse(validation["child_process_started"])
        self.assertEqual(
            None
            if canonical_development_source
            else {"code": "validation_failed"},
            validation["error"],
        )

        forbidden_keys = {
            "path",
            "project_root",
            "message",
            "prompt",
            "answer",
            "answer_sha256",
            "answer_chars",
            "prompt_sha256",
            "digest",
            "event_id",
            "message_id",
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

        for payload in (status, doctor, readiness, validation):
            assert_safe_keys(payload)
            serialized = json.dumps(payload, ensure_ascii=False)
            self.assertNotIn(str(SKILL_ROOT), serialized)
            self.assertNotRegex(serialized, r'"(?:ou|oc)_[^"]+"')

    def test_json_is_rejected_for_unrelated_actions(self) -> None:
        result = self.run_diagnostic("preflight")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("supported only", result.stderr + result.stdout)

    def test_health_process_identity_fences_pid_reuse_by_start_time(self) -> None:
        dispatcher = DISPATCHER.read_text(encoding="utf-8")
        for marker in (
            "$pidState.Identity.Process.StartTime.ToUniversalTime()",
            "$healthStartedAt -ge ([double]$verifiedProcessStartedAt - 1.0)",
        ):
            self.assertIn(marker, dispatcher)

    def test_status_strictly_sanitizes_mvp_health_observation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            project = Path(temporary)
            runtime = project / ".codex" / "feishu-codex-bridge-runtime"
            runtime.mkdir(parents=True)
            health = runtime / "health.json"
            observation = {
                "schema_version": 1,
                "status": "passed",
                "answer_free": True,
                "producer_namespace": "beeper",
                "final_callback_source": "final_callback",
                "feishu_delivery_observed": True,
                "known_delivery_fidelity_observed": True,
                "single_inbox_claim_observed": True,
                "bridge_outbox_scrubbed": True,
            }
            now = time.time()
            base_health = {
                "status": "online",
                "bridge_version": "4.2.0-alpha.64",
                "pid": 1,
                "started_at": now - 1,
                "updated_at": now,
                "runtime_manifest_sha256": "a" * 64,
                "event_consumer": True,
                "session_owner": "beeper",
                "beeper_state": "beeper-unavailable",
                "beeper_transport": "codex-queue",
                "active_turns": 0,
                "actionable_retryable_failed": 0,
                "beeper_queue": {
                    "dial_inflight": False,
                    "dial_lease_remaining_seconds": None,
                    "pending": 0,
                    "claimed": 0,
                },
                "queue": {
                    "queued": 0,
                    "running": 0,
                    "control_sending": 0,
                    "reply_pending": 0,
                    "retryable_failed": 0,
                    "completed": 0,
                    "terminal_failed": 0,
                },
            }
            health.write_text(
                json.dumps(
                    {
                        **base_health,
                        "mvp_observation": observation,
                    }
                ),
                encoding="utf-8",
            )

            accepted = json.loads(
                self.run_diagnostic("status", project_root=project).stdout
            )
            self.assertEqual(
                observation,
                accepted["health_snapshot"]["mvp_observation"],
            )
            self.assertTrue(accepted["health_snapshot"]["schema_current"])
            self.assertEqual(0, accepted["health_snapshot"]["beeper_pending"])
            self.assertEqual(0, accepted["health_snapshot"]["beeper_claimed"])
            self.assertEqual(
                0,
                accepted["health_snapshot"]["queue_counts"]["control_sending"],
            )
            self.assertIn(
                "[int]$queueCounts.control_sending -eq 0",
                DISPATCHER.read_text(encoding="utf-8"),
            )
            self.assertEqual(
                0,
                accepted["health_snapshot"]["actionable_retryable_failed"],
            )

            stale_health = {
                **base_health,
                "updated_at": now - 60,
                "mvp_observation": observation,
            }
            health.write_text(json.dumps(stale_health), encoding="utf-8")
            stale = json.loads(
                self.run_diagnostic("status", project_root=project).stdout
            )
            self.assertTrue(stale["health_snapshot"]["valid"])
            self.assertFalse(stale["health_snapshot"]["snapshot_fresh"])

            malformed_queue = dict(base_health["queue"])
            malformed_queue.pop("running")
            health.write_text(
                json.dumps(
                    {
                        **base_health,
                        "queue": malformed_queue,
                        "mvp_observation": observation,
                    }
                ),
                encoding="utf-8",
            )
            malformed = json.loads(
                self.run_diagnostic("status", project_root=project).stdout
            )
            self.assertIsNone(malformed["health_issue"])
            self.assertTrue(malformed["health_snapshot"]["valid"])
            self.assertFalse(malformed["health_snapshot"]["schema_current"])

            poisoned_beeper = {
                **base_health,
                "beeper_state": "secret-route-must-not-escape",
                "mvp_observation": observation,
            }
            health.write_text(json.dumps(poisoned_beeper), encoding="utf-8")
            poisoned = json.loads(
                self.run_diagnostic("status", project_root=project).stdout
            )
            self.assertEqual("invalid_health_snapshot", poisoned["health_issue"])
            self.assertFalse(poisoned["health_snapshot"]["valid"])
            self.assertIsNone(poisoned["health_snapshot"]["beeper_state"])
            self.assertIsNone(poisoned["health_snapshot"]["queue_counts"])
            self.assertNotIn("secret-route-must-not-escape", json.dumps(poisoned))

            poisoned_beeper = dict(base_health["beeper_queue"])
            poisoned_beeper["dial_lease_remaining_seconds"] = (
                "secret-age-must-not-escape"
            )
            health.write_text(
                json.dumps(
                    {
                        **base_health,
                        "beeper_queue": poisoned_beeper,
                        "mvp_observation": observation,
                    }
                ),
                encoding="utf-8",
            )
            poisoned_age = json.loads(
                self.run_diagnostic("status", project_root=project).stdout
            )
            self.assertEqual(
                "invalid_health_snapshot",
                poisoned_age["health_issue"],
            )
            self.assertIsNone(
                poisoned_age["health_snapshot"]["dial_lease_remaining_seconds"]
            )
            self.assertNotIn("secret-age-must-not-escape", json.dumps(poisoned_age))

            observation["prompt"] = "must-not-escape"
            health.write_text(
                json.dumps(
                    {
                        **base_health,
                        "mvp_observation": observation,
                    }
                ),
                encoding="utf-8",
            )
            rejected = json.loads(
                self.run_diagnostic("status", project_root=project).stdout
            )
            self.assertEqual("invalid_health_snapshot", rejected["health_issue"])
            self.assertFalse(rejected["health_snapshot"]["valid"])
            self.assertIsNone(
                rejected["health_snapshot"]["mvp_observation"]
            )
            self.assertNotIn("must-not-escape", json.dumps(rejected))

    def test_doctor_and_readiness_fail_closed_on_installed_historical_allow_rule(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            project = Path(temporary)
            rules = project / ".codex" / "rules" / "feishu-beeper.rules"
            rules.parent.mkdir(parents=True)
            untrusted_rule_text = "legacy_prompt_or_path_must_not_escape"
            rules.write_text(
                'prefix_rule(pattern=["python", "beeper_queue_cli.py", "claim"], '
                f'decision="allow") # {untrusted_rule_text}\n',
                encoding="utf-8",
            )

            doctor_result = self.run_diagnostic("doctor", project_root=project)
            self.assertEqual(2, doctor_result.returncode, doctor_result.stderr)
            doctor = json.loads(doctor_result.stdout)
            rule_state = doctor["historical_beeper_rules"]
            self.assertTrue(rule_state["present"])
            self.assertFalse(rule_state["historical_beeper_rules_tombstoned"])
            self.assertTrue(rule_state["allow_prefix_present"])
            self.assertIn(
                "historical_beeper_rules_allow_present", rule_state["issue_codes"]
            )

            readiness_result = self.run_diagnostic(
                "readiness", project_root=project
            )
            self.assertEqual(2, readiness_result.returncode, readiness_result.stderr)
            readiness = json.loads(readiness_result.stdout)
            self.assertFalse(
                readiness["installation_integrity"]["checks"]
                ["historical_beeper_rules_tombstoned"]
            )
            self.assertIn(
                "historical_beeper_rules_not_tombstoned",
                readiness["installation_integrity"]["issue_codes"],
            )
            self.assertIn(
                "historical_beeper_rules_allow_present",
                readiness["installation_integrity"]["issue_codes"],
            )
            serialized = json.dumps(
                {"doctor": doctor, "readiness": readiness}, ensure_ascii=False
            )
            self.assertNotIn(untrusted_rule_text, serialized)
            self.assertNotIn(str(rules), serialized)

            rules.write_bytes(
                (SKILL_ROOT / "assets" / "feishu-beeper.rules.template").read_bytes()
            )
            tombstoned_result = self.run_diagnostic(
                "doctor", project_root=project
            )
            tombstoned = json.loads(tombstoned_result.stdout)
            self.assertTrue(
                tombstoned["historical_beeper_rules"]
                ["historical_beeper_rules_tombstoned"]
            )
            self.assertFalse(
                tombstoned["historical_beeper_rules"]["allow_prefix_present"]
            )
            self.assertEqual(
                [], tombstoned["historical_beeper_rules"]["issue_codes"]
            )

    def test_run_once_readiness_evidence_is_strictly_bound_but_cannot_self_attest(
        self,
    ) -> None:
        def sha256_file(path: Path) -> str:
            return hashlib.sha256(path.read_bytes()).hexdigest()

        def sha256_text(value: str) -> str:
            return hashlib.sha256(value.encode("utf-8")).hexdigest()

        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            project = Path(temporary)
            runtime = project / ".codex" / "feishu-codex-bridge-runtime"
            hooks = project / ".codex" / "hooks"
            runtime.mkdir(parents=True)
            hooks.mkdir(parents=True)

            source_runtime_pairs = {
                "bridge.py": (
                    SKILL_ROOT / "scripts" / "bridge.py",
                    runtime / "bridge.py",
                ),
                "beeper_queue_cli.py": (
                    SKILL_ROOT / "scripts" / "beeper_queue_cli.py",
                    runtime / "beeper_queue_cli.py",
                ),
                "bridge_core/__init__.py": (
                    SKILL_ROOT / "scripts" / "bridge_core" / "__init__.py",
                    runtime / "bridge_core" / "__init__.py",
                ),
                "bridge_core/beeper_client.py": (
                    SKILL_ROOT / "scripts" / "bridge_core" / "beeper_client.py",
                    runtime / "bridge_core" / "beeper_client.py",
                ),
                "bridge_core/config.py": (
                    SKILL_ROOT / "scripts" / "bridge_core" / "config.py",
                    runtime / "bridge_core" / "config.py",
                ),
                "bridge_core/beeper_queue.py": (
                    SKILL_ROOT / "scripts" / "bridge_core" / "beeper_queue.py",
                    runtime / "bridge_core" / "beeper_queue.py",
                ),
                "bridge_core/legacy_identifiers.py": (
                    SKILL_ROOT / "scripts" / "bridge_core" / "legacy_identifiers.py",
                    runtime / "bridge_core" / "legacy_identifiers.py",
                ),
                "bridge_core/lark.py": (
                    SKILL_ROOT / "scripts" / "bridge_core" / "lark.py",
                    runtime / "bridge_core" / "lark.py",
                ),
                "bridge_core/runtime.py": (
                    SKILL_ROOT / "scripts" / "bridge_core" / "runtime.py",
                    runtime / "bridge_core" / "runtime.py",
                ),
                "bridge_core/state.py": (
                    SKILL_ROOT / "scripts" / "bridge_core" / "state.py",
                    runtime / "bridge_core" / "state.py",
                ),
                "start-hook": (
                    SKILL_ROOT / "scripts" / "start-feishu-codex-bridge.ps1",
                    hooks / "start-feishu-codex-bridge.ps1",
                ),
                "stop-hook": (
                    SKILL_ROOT / "scripts" / "stop-feishu-codex-bridge.ps1",
                    hooks / "stop-feishu-codex-bridge.ps1",
                ),
            }
            for source, installed in source_runtime_pairs.values():
                installed.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, installed)

            manifest = runtime / "runtime-manifest.json"
            manifest.write_text('{"schema_version":1}\n', encoding="utf-8")
            inventory = SKILL_ROOT / "assets" / "release-inventory.json"
            namespace = "feishu-codex-bridge.beeper-run-once.v1"
            source_runtime_material = [
                "schema_version=1",
                f"surface_namespace={namespace}",
            ]
            for label, (source, installed) in source_runtime_pairs.items():
                source_runtime_material.extend(
                    (
                        f"source.{label}.sha256={sha256_file(source)}",
                        f"runtime.{label}.sha256={sha256_file(installed)}",
                    )
                )
            source_runtime_sha256 = sha256_text("\n".join(source_runtime_material))

            surface_inputs = {
                "candidate-schema": SKILL_ROOT
                / "assets"
                / "desktop-beeper-run-once-candidate.schema.json",
                "runtime-attestation-schema": SKILL_ROOT
                / "assets"
                / "desktop-beeper-run-once-runtime-attestation.schema.json",
                "beeper-run-once-contract": SKILL_ROOT / "scripts" / "beeper_run_once_contract.py",
                "readiness-controller": DISPATCHER,
            }
            surface_material = [
                "schema_version=1",
                f"surface_namespace={namespace}",
            ]
            for label, path in surface_inputs.items():
                surface_material.append(f"{label}.sha256={sha256_file(path)}")
            surface_contract_sha256 = sha256_text("\n".join(surface_material))
            manifest_sha256 = sha256_file(manifest)
            inventory_sha256 = sha256_file(inventory)
            binding_sha256 = sha256_text(
                "\n".join(
                    (
                        "schema_version=1",
                        f"surface_namespace={namespace}",
                        f"runtime_manifest_sha256={manifest_sha256}",
                        f"release_inventory_sha256={inventory_sha256}",
                        f"source_runtime_sha256={source_runtime_sha256}",
                        f"surface_contract_sha256={surface_contract_sha256}",
                    )
                )
            )
            receipt_sha256 = "a" * 64
            evidence_lines = (
                "SCHEMA_VERSION=2",
                "EVIDENCE_KIND=feishu_codex_bridge_beeper_readiness_v2",
                f"SURFACE_NAMESPACE={namespace}",
                "CONTROLLER_PROVENANCE_KIND=unsupported_v1",
                f"PROVENANCE_RUNTIME_MANIFEST_SHA256={manifest_sha256}",
                f"PROVENANCE_RELEASE_INVENTORY_SHA256={inventory_sha256}",
                f"PROVENANCE_SOURCE_RUNTIME_SHA256={source_runtime_sha256}",
                f"PROVENANCE_SURFACE_CONTRACT_SHA256={surface_contract_sha256}",
                f"EVIDENCE_BINDING_SHA256={binding_sha256}",
                f"RUNTIME_ATTESTATION_RECEIPT_SHA256={receipt_sha256}",
                "RUNTIME_ATTESTATION_STATUS=pass",
                "RUNTIME_RECEIPT_IMMUTABLE=true",
                "BEEPER_TOPOLOGY_STATUS=pass",
                "BEEPER_ROLE_ISOLATION_STATUS=pass",
                "DESKTOP_RESPONDER_OWNERSHIP_STATUS=pass",
                "TASK_COORDINATION_POLICY_STATUS=pass",
                "ALTERNATE_RESPONDER_CLIENT_EXCLUSION_STATUS=pass",
                "HOOK_VISIBLE_REVIEW_STATUS=pass",
                "LIVE_E2E_STATUS=pass",
                "FINAL_CALLBACK_SOURCE=product_attested_final_callback",
                "NO_REPLAY_ATTESTED=true",
            )
            evidence = runtime / "run-once-readiness-evidence.env"
            evidence.write_text("\n".join(evidence_lines) + "\n", encoding="utf-8")

            shaped = self.run_diagnostic("readiness", project_root=project)
            self.assertEqual(2, shaped.returncode, shaped.stderr)
            shaped_payload = json.loads(shaped.stdout)
            new_surface = shaped_payload["future_surface_evidence"]
            self.assertEqual("unsupported_no_product_origin", new_surface["status"])
            self.assertTrue(new_surface["schema_valid"])
            self.assertTrue(new_surface["surface_namespace_isolated"])
            for binding in (
                "installed_manifest_bound",
                "release_inventory_bound",
                "source_runtime_bound",
                "surface_contract_bound",
                "evidence_binding_bound",
            ):
                self.assertTrue(new_surface[binding], binding)
            self.assertFalse(new_surface["controller_provenance_supported"])
            for attestation in (
                "closed_runtime_attestation",
                "immutable_runtime_receipt_attested",
                "single_beeper_attested",
                "beeper_role_isolation_attested",
                "desktop_responder_ownership_attested",
                "task_coordination_policy_attested",
                "alternate_responder_client_exclusion_attested",
                "task_tool_surface_attested",
                "hook_visible_review_attested",
                "exact_source_live_e2e_attested",
                "product_final_callback_attested",
                "no_replay_attested",
                "production_gate_passed",
            ):
                self.assertFalse(new_surface[attestation], attestation)
            self.assertIn(
                "unsupported_no_product_origin",
                shaped_payload["production"]["blocker_codes"],
            )
            self.assertEqual("blocked", shaped_payload["task_tool_surface"]["status"])
            self.assertFalse(
                shaped_payload["task_tool_surface"]["production_gate_passed"]
            )
            for blocker in (
                "single_beeper_unverified",
                "beeper_role_isolation_unverified",
                "desktop_responder_ownership_unverified",
                "task_coordination_policy_unverified",
                "alternate_responder_client_exclusion_unverified",
            ):
                self.assertIn(blocker, shaped_payload["production"]["blocker_codes"])
            serialized = json.dumps(shaped_payload, ensure_ascii=False)
            for secret_binding in (
                manifest_sha256,
                inventory_sha256,
                source_runtime_sha256,
                surface_contract_sha256,
                binding_sha256,
                receipt_sha256,
            ):
                self.assertNotIn(secret_binding, serialized)

            untrusted_text = "untrusted_prompt_or_path"
            evidence.write_text(
                "\n".join(evidence_lines) + f"\nPROMPT={untrusted_text}\n",
                encoding="utf-8",
            )
            invalid = self.run_diagnostic("readiness", project_root=project)
            self.assertEqual(2, invalid.returncode, invalid.stderr)
            invalid_payload = json.loads(invalid.stdout)
            self.assertEqual(
                "invalid", invalid_payload["future_surface_evidence"]["status"]
            )
            self.assertFalse(
                invalid_payload["future_surface_evidence"]["schema_valid"]
            )
            self.assertNotIn(
                untrusted_text, json.dumps(invalid_payload, ensure_ascii=False)
            )

            evidence.write_text("\n".join(evidence_lines) + "\n", encoding="utf-8")
            manifest.write_text(
                '{"schema_version":1,"revision":2}\n', encoding="utf-8"
            )
            stale = self.run_diagnostic("readiness", project_root=project)
            self.assertEqual(2, stale.returncode, stale.stderr)
            stale_payload = json.loads(stale.stdout)
            self.assertEqual(
                "stale_provenance",
                stale_payload["future_surface_evidence"]["status"],
            )
            self.assertFalse(
                stale_payload["future_surface_evidence"]["installed_manifest_bound"]
            )
            self.assertFalse(stale_payload["production"]["eligible"])

    def test_readiness_source_is_read_only_and_never_scans_payload_receipts(
        self,
    ) -> None:
        dispatcher = DISPATCHER.read_text(encoding="utf-8")
        readiness_start = dispatcher.index(
            "function Get-BridgeRunOnceReadinessBindings"
        )
        readiness_end = dispatcher.index("function Get-BridgeValidateContract")
        readiness_source = dispatcher[readiness_start:readiness_end]
        self.assertNotIn("function Get-BridgeReadinessEvidenceState", dispatcher)
        self.assertNotIn("Get-BridgeHistoricalForensicMaterialFingerprint", dispatcher)
        self.assertNotIn("readiness-state.env", dispatcher)
        self.assertNotIn("TERMINAL_MARKERS", dispatcher)
        self.assertIn("run-once-readiness-evidence.env", readiness_source)
        self.assertIn("Get-BridgeRunOnceReadinessBindings", readiness_source)
        self.assertIn("Get-BridgeRunOnceReadinessEvidenceState", readiness_source)
        self.assertIn("CONTROLLER_PROVENANCE_KIND", readiness_source)
        self.assertIn("unsupported_no_product_origin", readiness_source)
        self.assertIn("mvp_observation", readiness_source)
        self.assertIn("current_process_runtime_observation", readiness_source)
        self.assertIn("final_callback_observed", readiness_source)
        self.assertNotIn("hook_final_observed", readiness_source)
        self.assertNotIn("FINAL_CALLBACK_SOURCE=hook", readiness_source)
        self.assertIn("$values['SCHEMA_VERSION'] -cne '2'", readiness_source)
        self.assertIn(
            "feishu_codex_bridge_beeper_readiness_v2", readiness_source
        )
        self.assertIn(
            "feishu-codex-bridge.beeper-run-once.v1", readiness_source
        )
        for evidence_key in (
            "BEEPER_TOPOLOGY_STATUS",
            "BEEPER_ROLE_ISOLATION_STATUS",
            "DESKTOP_RESPONDER_OWNERSHIP_STATUS",
            "TASK_COORDINATION_POLICY_STATUS",
            "ALTERNATE_RESPONDER_CLIENT_EXCLUSION_STATUS",
        ):
            self.assertIn(evidence_key, readiness_source)
        self.assertNotIn("TASK_TOOL_ATTESTATION_STATUS", readiness_source)
        task_tool_attestation_start = readiness_source.index(
            "$taskToolAttested = ("
        )
        task_tool_attestation_end = readiness_source.index(
            "$hookReviewAttested", task_tool_attestation_start
        )
        task_tool_attestation = readiness_source[
            task_tool_attestation_start:task_tool_attestation_end
        ]
        for dependency in (
            "$closedRuntimeAttestation",
            "$singleBeeperAttested",
            "$beeperRoleIsolationAttested",
            "$desktopResponderOwnershipAttested",
            "$taskCoordinationPolicyAttested",
            "$alternateResponderClientExclusionAttested",
        ):
            self.assertIn(dependency, task_tool_attestation)
        task_tool_surface_start = readiness_source.index(
            "$taskToolSurfacePassed = ("
        )
        task_tool_surface_end = readiness_source.index(
            "[string[]]$schedulerBlockerCodes", task_tool_surface_start
        )
        task_tool_surface = readiness_source[
            task_tool_surface_start:task_tool_surface_end
        ]
        for dependency in (
            "closed_runtime_attestation",
            "immutable_runtime_receipt_attested",
            "single_beeper_attested",
            "beeper_role_isolation_attested",
            "desktop_responder_ownership_attested",
            "task_coordination_policy_attested",
            "alternate_responder_client_exclusion_attested",
            "task_tool_surface_attested",
        ):
            self.assertIn(dependency, task_tool_surface)
        self.assertNotIn("readiness-record", dispatcher)
        self.assertNotIn("[string[]]$TerminalMarker", dispatcher)
        self.assertNotIn("Open-BridgeReadinessRecordLock", dispatcher)
        self.assertNotIn("Invoke-BridgeReadinessRecord", dispatcher)
        self.assertNotIn(".readiness-state.env.lock", readiness_source)
        self.assertNotIn("[System.IO.FileShare]::None", readiness_source)
        self.assertNotIn("[System.IO.File]::Replace", readiness_source)
        self.assertNotIn("$temporaryStream.Flush($true)", readiness_source)
        self.assertNotIn("Set-Content", readiness_source)
        self.assertNotIn("Remove-Item", readiness_source)
        self.assertIn("historical_namespace_closed = $true", readiness_source)
        self.assertIn("historical_state = 'closed'", readiness_source)
        self.assertIn("evidence_status = 'retired'", readiness_source)
        self.assertIn("historical_observed_codes = @()", readiness_source)
        self.assertIn("historical_namespace_blocking_codes", readiness_source)
        self.assertIn("production_blocking_historical_codes = @()", readiness_source)
        self.assertIn("run_once_runtime_attestation_unverified", readiness_source)
        self.assertNotIn("Local\\FeishuCodexBridgeReadiness-", readiness_source)
        self.assertNotIn("[System.Threading.Mutex]", readiness_source)
        self.assertNotIn("desktop-router\\receipts", readiness_source)
        self.assertNotIn("ConvertFrom-Json", readiness_source)

        installer = (
            SKILL_ROOT / "scripts" / "install-feishu-codex-bridge.ps1"
        ).read_text(encoding="utf-8")
        cleanup_start = installer.index("function Remove-RetiredReadinessState")
        cleanup_end = installer.index(
            "function Write-BridgeRuntimeManifest", cleanup_start
        )
        cleanup_source = installer[cleanup_start:cleanup_end]
        for cleanup_guard in (
            "if (-not $Force) { return }",
            "$retiredStatePath = Join-Path $runtimeRoot 'readiness-state.env'",
            "$retiredStateInfo.PSIsContainer",
            "[System.IO.FileAttributes]::ReparsePoint",
            "$retiredStateInfo.DirectoryName.Equals($expectedRuntimePath",
            "Remove-Item -LiteralPath $retiredStateInfo.FullName -Force",
        ):
            self.assertIn(cleanup_guard, cleanup_source)
        polling_cleanup_start = cleanup_source.index(
            "function Remove-RetiredPollingState"
        )
        polling_cleanup = cleanup_source[polling_cleanup_start:]
        for cleanup_guard in (
            "if (-not $Force) { return }",
            "foreach ($queueRootName in @('desktop-router', 'beeper'))",
            "$retiredStatePath = Join-Path $queueRootPath 'heartbeat.json'",
            "[System.IO.FileAttributes]::ReparsePoint",
            "$retiredStateInfo.DirectoryName.Equals($expectedQueueRootPath",
            "Remove-Item -LiteralPath $retiredStateInfo.FullName -Force",
        ):
            self.assertIn(cleanup_guard, polling_cleanup)
        self.assertIn(
            "$retiredEnvironmentPattern = '^\\s*(?:CODEX_BRIDGE_ROUTER_HEARTBEAT_TTL|CODEX_BRIDGE_GATEWAY_SCHEDULER_TTL)\\s*='",
            installer,
        )
        self.assertIn("$terminologyMigration = [ordered]@{", installer)
        self.assertIn("'CODEX_BRIDGE_ROUTER_WAKE_TTL' = 'CODEX_BRIDGE_BEEPER_DIAL_TTL'", installer)
        self.assertIn("$candidate -match $retiredEnvironmentPattern", installer)
        install_branch_start = installer.index("if ($HooksOnly) {", cleanup_end)
        full_branch_start = installer.index("} else {", install_branch_start)
        install_branch_end = installer.index("\n}\n\nif ($SkipHooks)", full_branch_start)
        self.assertNotIn(
            "Remove-RetiredReadinessState",
            installer[install_branch_start:full_branch_start],
        )
        self.assertIn(
            "Remove-RetiredReadinessState",
            installer[full_branch_start:install_branch_end],
        )
        self.assertIn(
            "Remove-RetiredPollingState",
            installer[full_branch_start:install_branch_end],
        )
        health_migration_start = cleanup_source.index(
            "function Convert-RetiredStoppedHealthSnapshot"
        )
        health_migration = cleanup_source[health_migration_start:]
        for migration_guard in (
            "$healthInfo.PSIsContainer",
            "[System.IO.FileAttributes]::ReparsePoint",
            "Bridge health snapshot has an unsupported Beeper shape",
            "Retired Bridge health metadata is not exactly stopped and idle",
            "dial_lease_remaining_seconds = $null",
            "Write-MigratedStoppedHealthSnapshot -Health $health",
        ):
            self.assertIn(migration_guard, health_migration)
        self.assertIn("[System.IO.File]::WriteAllText", cleanup_source)
        self.assertIn(
            "Convert-RetiredStoppedHealthSnapshot",
            installer[install_branch_start:full_branch_start],
        )
        self.assertNotIn(
            "Update-InstalledStoppedHealthSnapshot",
            installer[install_branch_start:full_branch_start],
        )
        self.assertIn(
            "Update-InstalledStoppedHealthSnapshot",
            installer[full_branch_start:install_branch_end],
        )
        self.assertIn(
            "Installed Bridge health is not exactly stopped and idle",
            installer,
        )

    def _run_hook_only_installer(
        self, project: Path, health_payload: dict[str, object]
    ) -> subprocess.CompletedProcess[str]:
        runtime = project / ".codex" / "feishu-codex-bridge-runtime"
        hooks = project / ".codex" / "hooks"
        runtime.mkdir(parents=True)
        hooks.mkdir(parents=True)
        (runtime / "bridge.py").write_text("# installed fixture\n", encoding="utf-8")
        (runtime / "bridge.env").write_text(
            "CODEX_BRIDGE_ACCESS_MODE=locked\n", encoding="utf-8"
        )
        for hook_name in (
            "start-feishu-codex-bridge.ps1",
            "stop-feishu-codex-bridge.ps1",
        ):
            (hooks / hook_name).write_text("# installed fixture\n", encoding="utf-8")
        (runtime / "health.json").write_text(
            json.dumps(health_payload), encoding="utf-8"
        )
        return subprocess.run(
            [
                str(powershell()),
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(INSTALLER),
                "-ProjectRoot",
                str(project),
                "-HooksOnly",
                "-Force",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    @staticmethod
    def _retired_stopped_health(*, pending: int = 0) -> dict[str, object]:
        return {
            "status": "stopped",
            "event_consumer": False,
            "active_turns": 0,
            "actionable_retryable_failed": 0,
            "beeper_queue": {
                "scheduler_fresh": False,
                "scheduler_age_seconds": None,
                "work_heartbeat_fresh": False,
                "work_heartbeat_age_seconds": None,
                "dial_inflight": False,
                "pending": pending,
                "claimed": 0,
            },
            "queue": {
                "queued": 0,
                "running": 0,
                "control_sending": 0,
                "reply_pending": 0,
            },
        }

    @staticmethod
    def _pre_glossary_stopped_health(*, pending: int = 0) -> dict[str, object]:
        return {
            "bridge_version": "4.2.0-alpha.59",
            "status": "stopped",
            "pid": 5672,
            "started_at": 1.0,
            "updated_at": 2.0,
            "runtime_manifest_sha256": "a" * 64,
            "event_consumer": False,
            "desktop_router": {
                "wake_inflight": False,
                "wake_lease_remaining_seconds": None,
                "pending": pending,
                "claimed": 0,
            },
            "session_owner": "desktop-router",
            "codex_transport": "experimental-codex-queue",
            "gateway_state": "experimental-gateway-registered-load-unobserved",
            "target_writer": "desktop-task-only",
            "active_turns": 0,
            "queue": {
                "queued": 0,
                "running": 0,
                "control_sending": 0,
                "reply_pending": 0,
                "retryable_failed": 1,
                "completed": 44,
                "terminal_failed": 1,
            },
            "actionable_retryable_failed": 0,
            "latest_delivery_fidelity": {
                "fidelity": "identity",
                "transforms": [],
            },
            "experimental_mvp_observation": {
                "schema_version": 1,
                "status": "passed",
                "answer_free": True,
                "producer_namespace": "experimental-gateway-v1",
                "target_final_source": "target_mcp",
                "feishu_delivery_observed": True,
                "known_delivery_fidelity_observed": True,
                "single_inbox_claim_observed": True,
                "listener_outbox_scrubbed": True,
            },
            "access_mode": "locked",
            "access_configured": True,
            "last_event_at": 1.5,
        }

    @staticmethod
    def _prefixed_stopped_health(*, pending: int = 0) -> dict[str, object]:
        return {
            "bridge_version": "4.2.0-alpha.60",
            "status": "stopped",
            "pid": 13704,
            "started_at": 1.0,
            "updated_at": 2.0,
            "runtime_manifest_sha256": "b" * 64,
            "event_consumer": False,
            "beeper_queue": {
                "dial_inflight": False,
                "dial_lease_remaining_seconds": None,
                "pending": pending,
                "claimed": 0,
            },
            "session_owner": "beeper",
            "beeper_transport": "experimental-codex-queue",
            "beeper_state": "experimental-beeper-registered-load-unobserved",
            "responder_writer": "desktop-task-only",
            "active_turns": 0,
            "queue": {
                "queued": 0,
                "running": 0,
                "control_sending": 0,
                "reply_pending": 0,
                "retryable_failed": 1,
                "completed": 45,
                "terminal_failed": 1,
            },
            "actionable_retryable_failed": 0,
            "latest_delivery_fidelity": {
                "fidelity": "identity",
                "transforms": [],
            },
            "experimental_mvp_observation": {
                "schema_version": 1,
                "status": "passed",
                "answer_free": True,
                "producer_namespace": "experimental-beeper-v1",
                "final_callback_source": "final_callback",
                "feishu_delivery_observed": True,
                "known_delivery_fidelity_observed": True,
                "single_inbox_claim_observed": True,
                "bridge_outbox_scrubbed": True,
            },
            "access_mode": "locked",
            "access_configured": True,
            "last_event_at": 1.5,
        }

    def test_hook_only_migrates_exact_prefixed_stopped_health_shape(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            project = Path(temporary)
            result = self._run_hook_only_installer(
                project, self._prefixed_stopped_health()
            )
            self.assertEqual(0, result.returncode, result.stderr + result.stdout)
            migrated = json.loads(
                (project / ".codex" / "feishu-codex-bridge-runtime" / "health.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual("codex-queue", migrated["beeper_transport"])
            self.assertEqual(
                "beeper-registered-load-unobserved",
                migrated["beeper_state"],
            )
            self.assertNotIn("experimental_mvp_observation", migrated)
            self.assertIsNone(migrated["mvp_observation"])
            self.assertIn("unprefixed Beeper schema", result.stdout)

    def test_hook_only_refuses_nonidle_prefixed_health_without_rewrite(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            project = Path(temporary)
            original = self._prefixed_stopped_health(pending=1)
            result = self._run_hook_only_installer(project, original)
            self.assertNotEqual(0, result.returncode)
            health = project / ".codex" / "feishu-codex-bridge-runtime" / "health.json"
            self.assertEqual(original, json.loads(health.read_text(encoding="utf-8")))
            self.assertIn(
                "not exactly stopped and idle",
                result.stderr + result.stdout,
            )

    def test_hook_only_migrates_exact_pre_glossary_stopped_health_shape(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            project = Path(temporary)
            result = self._run_hook_only_installer(
                project, self._pre_glossary_stopped_health()
            )
            self.assertEqual(0, result.returncode, result.stderr + result.stdout)
            migrated = json.loads(
                (project / ".codex" / "feishu-codex-bridge-runtime" / "health.json").read_text(
                    encoding="utf-8"
                )
            )
            for retired_key in (
                "desktop_router",
                "codex_transport",
                "gateway_state",
                "target_writer",
            ):
                self.assertNotIn(retired_key, migrated)
            self.assertEqual("beeper", migrated["session_owner"])
            self.assertEqual("codex-queue", migrated["beeper_transport"])
            self.assertEqual(
                "beeper-registered-load-unobserved",
                migrated["beeper_state"],
            )
            self.assertEqual("desktop-task-only", migrated["responder_writer"])
            self.assertEqual(
                {
                    "dial_inflight": False,
                    "dial_lease_remaining_seconds": None,
                    "pending": 0,
                    "claimed": 0,
                },
                migrated["beeper_queue"],
            )
            self.assertIsNone(migrated["mvp_observation"])
            self.assertIn("canonical terminology schema", result.stdout)

    def test_hook_only_refuses_nonidle_pre_glossary_health_without_rewrite(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            project = Path(temporary)
            original = self._pre_glossary_stopped_health(pending=1)
            result = self._run_hook_only_installer(project, original)
            self.assertNotEqual(0, result.returncode)
            health = project / ".codex" / "feishu-codex-bridge-runtime" / "health.json"
            self.assertEqual(original, json.loads(health.read_text(encoding="utf-8")))
            self.assertIn(
                "not exactly stopped and idle",
                result.stderr + result.stdout,
            )

    def test_hook_only_migrates_exact_retired_stopped_health_shape(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            project = Path(temporary)
            result = self._run_hook_only_installer(
                project, self._retired_stopped_health()
            )
            self.assertEqual(0, result.returncode, result.stderr + result.stdout)
            migrated = json.loads(
                (project / ".codex" / "feishu-codex-bridge-runtime" / "health.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                {
                    "dial_inflight": False,
                    "dial_lease_remaining_seconds": None,
                    "pending": 0,
                    "claimed": 0,
                },
                migrated["beeper_queue"],
            )
            self.assertIn("current dial-lease schema", result.stdout)

    def test_hook_only_refuses_nonidle_retired_health_without_rewrite(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            project = Path(temporary)
            original = self._retired_stopped_health(pending=1)
            result = self._run_hook_only_installer(project, original)
            self.assertNotEqual(0, result.returncode)
            health = project / ".codex" / "feishu-codex-bridge-runtime" / "health.json"
            self.assertEqual(original, json.loads(health.read_text(encoding="utf-8")))
            self.assertIn(
                "not exactly stopped and idle",
                result.stderr + result.stdout,
            )


@unittest.skipUnless(powershell(), "PowerShell is required")
class RuntimeRootMigrationTests(unittest.TestCase):
    current_name = "feishu-codex-bridge-runtime"
    legacy_name = "feishu-bridge"

    @staticmethod
    def run_installer(project: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                str(powershell()),
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(INSTALLER),
                "-ProjectRoot",
                str(project),
                *arguments,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=60,
        )

    def stage_legacy_runtime(self, project: Path) -> tuple[Path, Path]:
        legacy = project / ".codex" / self.legacy_name
        hooks = project / ".codex" / "hooks"
        legacy.mkdir(parents=True)
        hooks.mkdir(parents=True)
        (legacy / "bridge.py").write_text("# legacy runtime code\n", encoding="utf-8")
        (legacy / "bridge.env").write_text(
            "CODEX_BRIDGE_ACCESS_MODE=locked\n",
            encoding="utf-8",
        )
        (legacy / "state.sqlite3").write_bytes(b"durable-state-canary")
        (legacy / "bridge.log").write_text("retained-log-canary\n", encoding="utf-8")
        current_fragment = ".codex\\feishu-codex-bridge-runtime"
        legacy_fragment = ".codex\\feishu-bridge"
        for source in (START_HOOK, STOP_HOOK):
            text = source.read_text(encoding="utf-8").replace(
                current_fragment,
                legacy_fragment,
            )
            (hooks / source.name).write_text(text, encoding="utf-8")
        return legacy, hooks

    def test_stopped_legacy_runtime_moves_once_and_preserves_durable_state(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            project = Path(os.path.realpath(temporary))
            legacy, hooks = self.stage_legacy_runtime(project)
            result = self.run_installer(
                project,
                "-Force",
                "-HooksOnly",
                "-MigrateLegacyRuntime",
            )
            self.assertEqual(0, result.returncode, result.stderr + result.stdout)
            current = project / ".codex" / self.current_name
            self.assertFalse(legacy.exists())
            self.assertTrue(current.is_dir())
            self.assertEqual(
                b"durable-state-canary",
                (current / "state.sqlite3").read_bytes(),
            )
            self.assertEqual(
                "CODEX_BRIDGE_ACCESS_MODE=locked\n",
                (current / "bridge.env").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                "retained-log-canary\n",
                (current / "bridge.log").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                "# legacy runtime code\n",
                (current / "bridge.py").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                START_HOOK.read_bytes(),
                (hooks / START_HOOK.name).read_bytes(),
            )
            self.assertFalse((current / "runtime-manifest.json").exists())
            self.assertIn(
                "Migrated the stopped Bridge runtime directory",
                result.stdout,
            )

    def test_existing_current_and_legacy_directories_fail_without_merge(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            project = Path(os.path.realpath(temporary))
            legacy, _hooks = self.stage_legacy_runtime(project)
            current = project / ".codex" / self.current_name
            current.mkdir()
            (current / "current-canary").write_text("current", encoding="utf-8")
            result = self.run_installer(
                project,
                "-Force",
                "-HooksOnly",
                "-MigrateLegacyRuntime",
            )
            self.assertNotEqual(0, result.returncode)
            self.assertTrue(legacy.is_dir())
            self.assertEqual(
                "current",
                (current / "current-canary").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "Refusing to choose or merge",
                result.stderr + result.stdout,
            )

    def test_direct_installer_never_adopts_legacy_runtime_implicitly(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            project = Path(os.path.realpath(temporary))
            legacy, _hooks = self.stage_legacy_runtime(project)
            result = self.run_installer(
                project,
                "-Force",
                "-SkipHooks",
                "-SkipRuntimeConfig",
            )
            self.assertNotEqual(0, result.returncode)
            self.assertTrue(legacy.is_dir())
            self.assertFalse(
                (project / ".codex" / self.current_name).exists()
            )
            self.assertIn(
                "use the canonical bridge upgrade command",
                result.stderr + result.stdout,
            )

    def test_running_legacy_runtime_refuses_migration_without_moving(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            project = Path(os.path.realpath(temporary))
            legacy, _hooks = self.stage_legacy_runtime(project)
            bridge_script = legacy / "bridge.py"
            bridge_script.write_text(
                "import time\ntime.sleep(60)\n",
                encoding="utf-8",
            )
            holder = subprocess.Popen(
                [sys.executable, str(bridge_script)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
            try:
                (legacy / "bridge.pid").write_text(
                    str(holder.pid),
                    encoding="ascii",
                )
                result = self.run_installer(
                    project,
                    "-Force",
                    "-HooksOnly",
                    "-MigrateLegacyRuntime",
                )
                self.assertNotEqual(
                    0,
                    result.returncode,
                    result.stderr + result.stdout,
                )
                self.assertIsNone(holder.poll())
                self.assertTrue(legacy.is_dir())
                self.assertFalse(
                    (project / ".codex" / self.current_name).exists()
                )
                self.assertIn(
                    "Bridge must be stopped before installation changes",
                    result.stderr + result.stdout,
                )
            finally:
                if holder.poll() is None:
                    holder.terminate()
                try:
                    holder.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    holder.kill()
                    holder.wait(timeout=10)


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

    def test_foreign_reused_pid_is_never_reported_or_stopped_as_bridge(self) -> None:
        shell = str(powershell())
        holder = subprocess.Popen(
            [shell, "-NoProfile", "-Command", "Start-Sleep -Seconds 60"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
                project = Path(temporary)
                runtime = project / ".codex" / "feishu-codex-bridge-runtime"
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
                self.assertIn("not this Bridge process", status.stdout)

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


@unittest.skipUnless(
    sys.platform == "win32" and powershell(),
    "Windows PowerShell and Job Objects are required",
)
class BridgeLifecycleDetachmentTests(unittest.TestCase):
    runtime_files = (
        "bridge.py",
        "beeper_queue_cli.py",
        "bridge_core/__init__.py",
        "bridge_core/config.py",
        "bridge_core/beeper_client.py",
        "bridge_core/beeper_queue.py",
        "bridge_core/legacy_identifiers.py",
        "bridge_core/lark.py",
        "bridge_core/runtime.py",
        "bridge_core/state.py",
    )

    def stage_canary_runtime(self, project: Path) -> tuple[Path, Path, Path]:
        runtime = project / ".codex" / "feishu-codex-bridge-runtime"
        hooks = project / ".codex" / "hooks"
        runtime.mkdir(parents=True)
        hooks.mkdir(parents=True)
        installed_start = hooks / START_HOOK.name
        installed_stop = hooks / STOP_HOOK.name
        shutil.copy2(START_HOOK, installed_start)
        shutil.copy2(STOP_HOOK, installed_stop)
        source_version = json.loads(
            (SKILL_ROOT / "assets" / "release-inventory.json").read_text(
                encoding="utf-8"
            )
        )["source_version"]

        canary = """\
from pathlib import Path
import os
import time

runtime = Path(__file__).resolve().parent
pid = str(os.getpid())
(runtime / "bridge.lock").write_text(pid, encoding="ascii")
(runtime / "bridge.pid").write_text(pid, encoding="ascii")
deadline = time.monotonic() + 30
while time.monotonic() < deadline and not (runtime / "stop.request").exists():
    time.sleep(0.05)
for name in ("bridge.pid", "bridge.lock"):
    path = runtime / name
    try:
        if path.read_text(encoding="ascii").strip() == pid:
            path.unlink()
    except OSError:
        pass
"""
        for relative in self.runtime_files:
            destination = runtime / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if relative == "bridge.py":
                destination.write_text(canary, encoding="utf-8")
            elif relative == "bridge_core/config.py":
                destination.write_text(
                    f'BRIDGE_VERSION = "{source_version}"\n',
                    encoding="utf-8",
                )
            else:
                destination.write_text("# lifecycle detachment canary\n", encoding="utf-8")

        private_marker = "lifecycle-detach-private-marker-must-not-cross-output"
        (runtime / "bridge.env").write_text(
            "CODEX_BRIDGE_ACCESS_MODE=locked\n"
            "CODEX_BRIDGE_LIFECYCLE_MODE=hooks\n"
            f"CODEX_BRIDGE_TEST_MARKER={private_marker}\n",
            encoding="utf-8",
        )
        code_files = {
            relative: hashlib.sha256((runtime / relative).read_bytes()).hexdigest()
            for relative in self.runtime_files
        }
        manifest = {
            "schema_version": 1,
            "bridge_version": source_version,
            "code_files": code_files,
            "start_hook_sha256": hashlib.sha256(installed_start.read_bytes()).hexdigest(),
            "stop_hook_sha256": hashlib.sha256(installed_stop.read_bytes()).hexdigest(),
        }
        (runtime / "runtime-manifest.json").write_text(
            json.dumps(manifest, sort_keys=True),
            encoding="utf-8",
        )
        return runtime, installed_start, installed_stop

    def test_bridge_survives_transient_kill_on_close_hook_job(self) -> None:
        scripts_path = str(SKILL_ROOT / "scripts")
        if scripts_path not in sys.path:
            sys.path.insert(0, scripts_path)
        from app_server_host import WindowsOwnedJob
        from bridge_core.runtime import is_process_running

        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            project = Path(temporary)
            runtime, installed_start, _installed_stop = self.stage_canary_runtime(project)
            returned = project / "hook-returned.txt"
            quote = lambda value: str(value).replace("'", "''")
            wrapper_command = (
                "$ErrorActionPreference='Stop'; "
                f"& '{quote(installed_start)}'; "
                f"Set-Content -LiteralPath '{quote(returned)}' -Value returned -Encoding ascii; "
                "Start-Sleep -Seconds 30"
            )
            environment = dict(os.environ)
            environment.pop("CODEX_BRIDGE_CHILD", None)
            for name in tuple(environment):
                if name.startswith("CODEX_BRIDGE_"):
                    environment.pop(name, None)
            wrapper = None
            job = WindowsOwnedJob()
            bridge_pid = 0
            try:
                wrapper = subprocess.Popen(
                    [
                        str(powershell()),
                        "-NoLogo",
                        "-NoProfile",
                        "-NonInteractive",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-Command",
                        wrapper_command,
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=environment,
                    creationflags=(
                        getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                        | getattr(subprocess, "CREATE_SUSPENDED", 0x00000004)
                    ),
                )
                job.assign(wrapper)
                job.resume(wrapper)

                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    pid_path = runtime / "bridge.pid"
                    if returned.exists() and pid_path.exists():
                        try:
                            bridge_pid = int(pid_path.read_text(encoding="ascii").strip())
                        except (OSError, ValueError):
                            bridge_pid = 0
                        if bridge_pid > 0 and is_process_running(bridge_pid):
                            break
                    if wrapper.poll() is not None:
                        break
                    time.sleep(0.1)
                self.assertTrue(returned.exists(), "start Hook did not return successfully")
                self.assertGreater(bridge_pid, 0)
                self.assertTrue(is_process_running(bridge_pid))

                job.close()
                wrapper.wait(timeout=10)
                time.sleep(0.5)
                self.assertTrue(
                    is_process_running(bridge_pid),
                    "Bridge inherited the transient Hook Job",
                )
                stdout, stderr = wrapper.communicate(timeout=5)
                combined = (stdout + stderr).decode("utf-8", errors="replace")
                self.assertNotIn(
                    "lifecycle-detach-private-marker-must-not-cross-output",
                    combined,
                )
            finally:
                job.close()
                if wrapper is not None and wrapper.poll() is None:
                    wrapper.kill()
                    wrapper.wait(timeout=10)
                if bridge_pid > 0:
                    if is_process_running(bridge_pid):
                        self.assertEqual(
                            str(bridge_pid),
                            (runtime / "bridge.pid").read_text(encoding="ascii").strip(),
                        )
                        (runtime / "stop.request").write_text("stop\n", encoding="ascii")
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline and is_process_running(bridge_pid):
                        time.sleep(0.1)
                    self.assertFalse(is_process_running(bridge_pid))


@unittest.skipUnless(powershell(), "PowerShell is required")
class BridgeEnvEntrypointTests(unittest.TestCase):
    runtime_files = (
        "bridge.py",
        "beeper_queue_cli.py",
        "bridge_core/__init__.py",
        "bridge_core/beeper_client.py",
        "bridge_core/config.py",
        "bridge_core/beeper_queue.py",
        "bridge_core/legacy_identifiers.py",
        "bridge_core/lark.py",
        "bridge_core/runtime.py",
        "bridge_core/state.py",
    )

    def stage_isolated_runtime(self, root: Path, env_text: str) -> Path:
        runtime = root / ".codex" / "feishu-codex-bridge-runtime"
        hooks = root / ".codex" / "hooks"
        runtime.mkdir(parents=True)
        hooks.mkdir(parents=True)
        for relative in self.runtime_files:
            source = SKILL_ROOT / "scripts" / relative
            destination = runtime / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
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
                ("duplicate", "duplicate", "local helper request rejected"),
            ),
            "empty_enum": (
                "CODEX_BRIDGE_ACCESS_MODE=\n",
                ("one of", "not one of", "local helper request rejected"),
            ),
            "empty_boolean": (
                "CODEX_BRIDGE_DOWNLOAD_RESOURCES=\n",
                ("boolean", "boolean", "local helper request rejected"),
            ),
            "invalid_boolean": (
                "CODEX_BRIDGE_DOWNLOAD_RESOURCES=maybe\n",
                ("boolean", "boolean", "local helper request rejected"),
            ),
            "empty_integer": (
                "CODEX_BRIDGE_BEEPER_TIMEOUT=\n",
                ("integer", "integer", "local helper request rejected"),
            ),
            "malformed_integer": (
                "CODEX_BRIDGE_BEEPER_TIMEOUT=thirty\n",
                ("integer", "integer", "local helper request rejected"),
            ),
            "out_of_range_integer": (
                "CODEX_BRIDGE_BEEPER_TIMEOUT=29\n",
                (
                    "codex_bridge_beeper_timeout",
                    "codex_bridge_beeper_timeout",
                    "local helper request rejected",
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
