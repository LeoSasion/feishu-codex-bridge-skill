from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import call, patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
BRIDGE_DISPATCHER = SKILL_ROOT / "scripts" / "feishu-codex-bridge.ps1"
SKILL_DOC = SKILL_ROOT / "skills" / "feishu-codex-bridge" / "SKILL.md"
USAGE_DOC = SKILL_ROOT / "feishu-codex-bridge-skill.md"
README_DOC = SKILL_ROOT / "README.md"
AGENTS_FRAGMENT = SKILL_ROOT / "assets" / "AGENTS.feishu-codex-bridge.md"
PERMISSIONS_HOOKS_DOC = SKILL_ROOT / "references" / "permissions-and-hooks.md"
COMMON_CHAT_PERMISSIONS_DOC = (
    SKILL_ROOT / "references" / "openclaw-common-chat-permissions.md"
)
COMMAND_UX_DOC = SKILL_ROOT / "references" / "feishu-command-ux.md"
ARCHITECTURE_DOC = SKILL_ROOT / "references" / "architecture.md"
BRIDGE_PLUGIN = SKILL_ROOT
FINAL_CALLBACK_RULES = SKILL_ROOT / "assets" / "feishu-beeper.rules.template"
BEEPER_SOURCE = SKILL_ROOT / "scripts" / "bridge_core" / "beeper_queue.py"
BEEPER_CLIENT_SOURCE = SKILL_ROOT / "scripts" / "bridge_core" / "beeper_client.py"
TEST_TEMP_ROOT = Path(os.environ.get("FEISHU_BRIDGE_TEST_TMP", tempfile.gettempdir()))
if "FEISHU_BRIDGE_TEST_TMP" not in os.environ:
    TEST_TEMP_ROOT = TEST_TEMP_ROOT / "feishu-codex-bridge-tests"
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from bridge_core.runtime import (  # noqa: E402
    BridgeRuntime,
    DESKTOP_PRODUCER_HOLD_ERROR,
    BEEPER_UNAVAILABLE_REPLY,
    DESKTOP_RESPONDER_TOOLS_UNAVAILABLE_REPLY,
    BINDING_RISK_NOTICE,
    LifecycleLeases,
)
from bridge_core.beeper_client import (  # noqa: E402
    BeeperError,
    BeeperNotLoaded,
    BeeperQueueUnavailable,
    DesktopProjectSummary,
    DesktopTaskCatalog,
    DesktopTaskSummary,
    ResponderActivation,
)
from beeper_queue_cli import build_parser as build_beeper_queue_cli_parser  # noqa: E402
from bridge_core.config import load_config  # noqa: E402
from bridge_core.lark import ReplyResult, build_reply_plan  # noqa: E402
from bridge_core.state import DurableState, SessionStore  # noqa: E402
import final_callback_mcp_server as final_callback_mcp  # noqa: E402


class ConfigDefaultsTests(unittest.TestCase):
    def test_runtime_root_names_the_installed_role(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary, patch.dict(
            os.environ,
            {"CODEX_BRIDGE_PROJECT_ROOT": temporary},
            clear=True,
        ):
            self.assertEqual(
                Path(temporary).resolve()
                / ".codex"
                / "feishu-codex-bridge-runtime",
                load_config().runtime_dir,
            )

    def test_plain_text_is_the_exact_return_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual("text", load_config().reply_format)

    def test_missing_access_mode_is_locked_and_invalid_is_rejected(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual("locked", load_config().access_mode)
        with patch.dict(os.environ, {"CODEX_BRIDGE_ACCESS_MODE": "invalid"}, clear=True):
            with self.assertRaises(ValueError):
                load_config()

    def test_invalid_or_out_of_range_integer_is_rejected(self) -> None:
        for value in ("not-an-integer", "1_000", "29", "86401"):
            with self.subTest(value=value), patch.dict(
                os.environ,
                {"CODEX_BRIDGE_BEEPER_TIMEOUT": value},
                clear=True,
            ):
                with self.assertRaises(ValueError):
                    load_config()


class LifecycleLeaseTests(unittest.TestCase):
    def test_payload_accepts_windows_powershell_utf8_bom(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            path = Path(temporary) / "lease.json"
            expected = {
                "version": 1,
                "lease_id": "manual-test",
                "source": "manual",
                "status": "active",
                "host_pid": 0,
            }
            path.write_text(json.dumps(expected), encoding="utf-8-sig")

            self.assertEqual(expected, LifecycleLeases._payload(path))

    def test_viability_is_source_and_host_bound_fail_closed(self) -> None:
        leases = object.__new__(LifecycleLeases)
        cases = (
            ({"source": "manual", "status": "active", "host_pid": 0}, True),
            ({"source": "manual", "status": "released", "host_pid": 0}, False),
            ({"source": "manual", "status": "active", "host_pid": 41}, False),
            ({"source": "hook", "status": "active", "host_pid": 41}, True),
            ({"source": "hook", "status": "released", "host_pid": 41}, True),
            ({"source": "hook", "status": "active", "host_pid": 0}, False),
            ({"source": "hook", "status": "active", "host_pid": True}, False),
            (
                {"source": "hook", "status": "active", "host_pid": 0x100000000},
                False,
            ),
            ({"source": "hook", "status": "active", "host_pid": "bad"}, False),
            ({"source": "hook", "status": "active", "host_pid": "41"}, False),
            ({"source": "unknown", "status": "active", "host_pid": 0}, False),
            ({"source": "manual", "status": "unknown", "host_pid": 0}, False),
            ({"source": "hook", "status": "active"}, False),
        )
        with patch("bridge_core.runtime.is_process_running", return_value=True):
            for payload, expected in cases:
                with self.subTest(payload=payload):
                    self.assertEqual(expected, leases._is_viable(payload))

        with patch("bridge_core.runtime.is_process_running", return_value=False):
            self.assertFalse(
                leases._is_viable(
                    {"source": "hook", "status": "active", "host_pid": 41}
                )
            )


class HealthSnapshotTests(unittest.TestCase):
    @staticmethod
    def _runtime(path: Path) -> BridgeRuntime:
        runtime = object.__new__(BridgeRuntime)
        runtime.config = SimpleNamespace(health_file=path)
        runtime._health_payload = lambda status: {"status": status}
        return runtime

    def test_health_snapshot_retries_transient_windows_reader_lock(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            health_file = Path(temporary) / "health.json"
            runtime = self._runtime(health_file)
            real_replace = os.replace
            attempts = 0

            def replace_after_two_reader_locks(source, destination):
                nonlocal attempts
                attempts += 1
                if attempts <= 2:
                    raise PermissionError("simulated Windows reader lock")
                return real_replace(source, destination)

            with patch(
                "bridge_core.runtime.os.replace",
                side_effect=replace_after_two_reader_locks,
            ), patch("bridge_core.runtime.time.sleep") as sleep:
                runtime.write_health("online")

            self.assertEqual({"status": "online"}, json.loads(health_file.read_text()))
            self.assertEqual(3, attempts)
            self.assertEqual([call(0.01), call(0.02)], sleep.call_args_list)
            self.assertEqual([], list(health_file.parent.glob(".health.json.*.tmp")))

    def test_health_snapshot_keeps_previous_value_after_bounded_reader_lock(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            health_file = Path(temporary) / "health.json"
            health_file.write_text('{"status":"previous"}', encoding="utf-8")
            runtime = self._runtime(health_file)

            with patch(
                "bridge_core.runtime.os.replace",
                side_effect=PermissionError("simulated persistent Windows reader lock"),
            ) as replace, patch("bridge_core.runtime.time.sleep"):
                runtime.write_health("online")

            self.assertEqual(
                {"status": "previous"},
                json.loads(health_file.read_text(encoding="utf-8")),
            )
            self.assertEqual(6, replace.call_count)
            self.assertEqual([], list(health_file.parent.glob(".health.json.*.tmp")))

    def test_health_payload_projects_only_answer_free_latest_delivery_fidelity(self) -> None:
        class FakeBeeperStatus:
            beeper_thread_id = "thread-secret-must-not-persist"
            host_id = "host-secret-must-not-persist"
            dial_inflight = False
            dial_lease_remaining_seconds = None
            pending = 0
            claimed = 0

        class FakeBeeper:
            @staticmethod
            def status():
                return FakeBeeperStatus()

            @staticmethod
            def connection_status():
                return "codex-queue"

            @staticmethod
            def state(beeper):
                del beeper
                return "beeper-unavailable"

        runtime = object.__new__(BridgeRuntime)
        runtime._scheduler_lock = threading.RLock()
        runtime._active_turns = {}
        runtime._mvp_observation = None
        runtime.beeper = FakeBeeper()
        runtime.consumer = SimpleNamespace(is_ready=lambda: True)
        runtime.state = SimpleNamespace(
            status_counts=lambda: {"completed": 1},
            actionable_retryable_failed_count=lambda _excluded: 0,
            latest_delivery_fidelity=lambda: {
                "fidelity": "identity",
                "transforms": [],
            },
        )
        runtime.config = SimpleNamespace(
            access_mode="locked",
        )
        runtime.access = SimpleNamespace(configured=True)
        runtime.started_at = 1.0
        runtime._runtime_manifest_sha256 = "a" * 64
        runtime.last_event_at = 2.0
        runtime.last_error = ""

        payload = runtime._health_payload("online")

        self.assertEqual(
            {"fidelity": "identity", "transforms": []},
            payload["latest_delivery_fidelity"],
        )
        self.assertEqual(
            {"fidelity", "transforms"},
            set(payload["latest_delivery_fidelity"]),
        )
        self.assertIsNone(payload["mvp_observation"])
        self.assertEqual("a" * 64, payload["runtime_manifest_sha256"])
        self.assertEqual(0, payload["actionable_retryable_failed"])
        self.assertEqual(
            {
                "dial_inflight": False,
                "dial_lease_remaining_seconds": None,
                "pending": 0,
                "claimed": 0,
            },
            payload["beeper_queue"],
        )
        self.assertNotIn("last_error", payload)
        self.assertNotIn(
            "thread-secret-must-not-persist",
            json.dumps(payload, ensure_ascii=False),
        )
        self.assertNotIn(
            "host-secret-must-not-persist",
            json.dumps(payload, ensure_ascii=False),
        )


class ReplyDeliveryTests(unittest.TestCase):
    def test_final_callback_delivery_records_only_answer_free_process_observation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            state = DurableState(Path(temporary) / "state.sqlite3")
            config = load_config()
            event = {
                "event_id": "event-1",
                "message_id": "message-1",
                "_bridge_scope": "p2p:chat-1",
            }
            answer = "青岚🚀  \r\n"
            plan = build_reply_plan(answer, config)
            state.enqueue("event-1", "message-1", "p2p:chat-1", event)
            state.claim("event-1")
            state.mark_model_started("event-1", "thread-secret", "turn-secret")

            runtime = object.__new__(BridgeRuntime)
            runtime.state = state
            runtime.lark_cli = "lark-cli"
            runtime.config = config
            runtime._scheduler_lock = threading.RLock()
            runtime._mvp_observation = None
            runtime.started_at = float(state.get("event-1")["created_at"]) - 1.0
            result = ReplyResult(
                True,
                retryable=False,
                outbound_fidelity=plan.outbound_fidelity,
                outbound_transforms=plan.outbound_transforms,
            )
            try:
                with patch(
                    "bridge_core.runtime.reply_to_message",
                    return_value=result,
                ) as reply:
                    runtime._deliver(
                        "event-1",
                        event,
                        answer,
                        authoritative_source="final_callback",
                    )
                self.assertEqual(answer, reply.call_args.args[2])
                self.assertEqual(plan, reply.call_args.kwargs["plan"])

                self.assertEqual(
                    {
                        "schema_version": 1,
                        "status": "passed",
                        "answer_free": True,
                        "producer_namespace": "beeper",
                        "final_callback_source": "final_callback",
                        "feishu_delivery_observed": True,
                        "known_delivery_fidelity_observed": True,
                        "single_inbox_claim_observed": True,
                        "bridge_outbox_scrubbed": True,
                    },
                    runtime._mvp_observation,
                )
                serialized = json.dumps(
                    runtime._mvp_observation,
                    ensure_ascii=False,
                )
                for secret in (answer, "event-1", "message-1", "chat-1", "thread-secret", "turn-secret"):
                    self.assertNotIn(secret, serialized)
                terminal = state.get("event-1")
                self.assertEqual("completed", terminal["status"])
                for field in (
                    "payload_json",
                    "answer",
                    "thread_id",
                    "turn_id",
                    "outbound_plan_json",
                    "outbound_answer_sha256",
                    "outbound_answer_chars",
                    "outbound_plan_sha256",
                    "outbound_envelope_sha256",
                ):
                    self.assertIsNone(terminal[field], field)
            finally:
                state.close()

    def test_preexisting_inbox_row_cannot_create_fresh_process_observation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            state = DurableState(Path(temporary) / "state.sqlite3")
            config = load_config()
            event = {
                "event_id": "event-before-restart",
                "message_id": "message-before-restart",
                "_bridge_scope": "p2p:chat-before-restart",
            }
            answer = "freshness boundary"
            plan = build_reply_plan(answer, config)
            state.enqueue(
                "event-before-restart",
                "message-before-restart",
                "p2p:chat-before-restart",
                event,
            )
            state.claim("event-before-restart")
            state.mark_model_started(
                "event-before-restart",
                "thread-before-restart",
                "turn-before-restart",
            )

            runtime = object.__new__(BridgeRuntime)
            runtime.state = state
            runtime.lark_cli = "lark-cli"
            runtime.config = config
            runtime._scheduler_lock = threading.RLock()
            runtime._mvp_observation = None
            runtime.started_at = (
                float(state.get("event-before-restart")["created_at"]) + 1.0
            )
            result = ReplyResult(
                True,
                retryable=False,
                outbound_fidelity=plan.outbound_fidelity,
                outbound_transforms=plan.outbound_transforms,
            )
            try:
                with patch(
                    "bridge_core.runtime.reply_to_message",
                    return_value=result,
                ):
                    runtime._deliver(
                        "event-before-restart",
                        event,
                        answer,
                        authoritative_source="final_callback",
                    )
                self.assertIsNone(runtime._mvp_observation)
                self.assertEqual(
                    "completed",
                    state.get("event-before-restart")["status"],
                )
            finally:
                state.close()

    def test_non_responder_delivery_cannot_create_mvp_observation(
        self,
    ) -> None:
        calls = []

        class FakeState:
            @staticmethod
            def mark_outbound_result(event_id, fidelity, transforms):
                calls.append((event_id, fidelity, tuple(transforms)))

            @staticmethod
            def mark_completed(event_id):
                calls.append(("completed", event_id))

            @staticmethod
            def mark_reply_retry(event_id, error):
                calls.append(("retry", event_id, error))

        runtime = object.__new__(BridgeRuntime)
        runtime.state = FakeState()
        runtime._scheduler_lock = threading.RLock()
        runtime._mvp_observation = None
        runtime._record_reply_result(
            "event-1",
            ReplyResult(
                True,
                retryable=False,
                outbound_fidelity="identity",
            ),
            "unused",
        )

        self.assertIsNone(runtime._mvp_observation)
        self.assertEqual(
            [("event-1", "identity", ()), ("completed", "event-1")],
            calls,
        )
        calls.clear()
        runtime._record_reply_result(
            "event-1",
            ReplyResult(False, retryable=True),
            "retry",
            authoritative_source="final_callback",
        )

        self.assertIsNone(runtime._mvp_observation)
        self.assertEqual(
            [("event-1", "unknown", ()), ("retry", "event-1", "retry")],
            calls,
        )
        calls.clear()
        with patch("bridge_core.runtime.logger.warning") as warning:
            runtime._record_reply_result(
                "event-1",
                ReplyResult(
                    True,
                    retryable=False,
                    outbound_fidelity="identity",
                ),
                "unused",
                authoritative_source="final_callback",
            )
        warning.assert_called_once()
        self.assertIsNone(runtime._mvp_observation)
        self.assertEqual(
            [("event-1", "identity", ()), ("completed", "event-1")],
            calls,
        )

        class StaleReceiptState:
            def __init__(self):
                self.reads = 0

            @staticmethod
            def mark_outbound_result(event_id, fidelity, transforms):
                del event_id, fidelity, transforms

            @staticmethod
            def mark_completed(event_id):
                del event_id

            def get(self, event_id):
                del event_id
                self.reads += 1
                if self.reads == 1:
                    return {
                        "status": "reply_pending",
                        "attempts": 1,
                        "reply_attempts": 0,
                        "model_started": 0,
                        "thread_id": None,
                    }
                return {
                    "status": "completed",
                    "outbound_fidelity": "identity",
                    "model_started": 0,
                }

        runtime.state = StaleReceiptState()
        runtime._record_reply_result(
            "event-1",
            ReplyResult(
                True,
                retryable=False,
                outbound_fidelity="identity",
            ),
            "unused",
            authoritative_source="final_callback",
        )
        self.assertIsNone(runtime._mvp_observation)

    def test_outbox_freeze_conflict_never_sends_or_overwrites(self) -> None:
        class FakeState:
            @staticmethod
            def mark_reply_pending(event_id, answer, outbound_plan):
                return False

        runtime = object.__new__(BridgeRuntime)
        runtime.state = FakeState()
        runtime.lark_cli = "lark-cli"
        runtime.config = load_config()
        with patch("bridge_core.runtime.reply_to_message") as reply:
            runtime._deliver(
                "event-1",
                {"event_id": "event-1", "message_id": "message-1"},
                "answer",
            )
        reply.assert_not_called()

    def test_terminal_reply_result_is_not_rescheduled(self) -> None:
        calls = []

        class FakeState:
            @staticmethod
            def mark_reply_pending(event_id, answer, outbound_plan):
                calls.append(("pending", event_id, answer, outbound_plan))
                return True

            @staticmethod
            def verified_outbound(event_id, event):
                self.assertEqual("event-1", event_id)
                self.assertEqual("message-1", event["message_id"])
                return "answer", calls[0][3]

            @staticmethod
            def mark_completed(event_id):
                calls.append(("completed", event_id))

            @staticmethod
            def mark_outbound_result(event_id, fidelity, transforms):
                calls.append(("fidelity", event_id, fidelity, tuple(transforms)))

            @staticmethod
            def mark_reply_retry(event_id, error):
                calls.append(("retry", event_id, error))

            @staticmethod
            def mark_terminal(event_id, error):
                calls.append(("terminal", event_id, error))

        runtime = object.__new__(BridgeRuntime)
        runtime.state = FakeState()
        runtime.lark_cli = "lark-cli"
        runtime.config = load_config()
        result = ReplyResult(False, retryable=False, error_code="230011")
        with patch("bridge_core.runtime.reply_to_message", return_value=result):
            runtime._deliver(
                "event-1",
                {"event_id": "event-1", "message_id": "message-1"},
                "answer",
            )

        self.assertEqual("pending", calls[0][0])
        self.assertEqual(("fidelity", "event-1", "unknown", ()), calls[1])
        self.assertEqual("terminal", calls[2][0])
        self.assertNotIn("retry", [call[0] for call in calls])

    def test_reply_exception_records_unknown_before_retry(self) -> None:
        calls = []

        class FakeState:
            @staticmethod
            def mark_reply_pending(event_id, answer, outbound_plan):
                calls.append(("pending", event_id, answer, outbound_plan))
                return True

            @staticmethod
            def verified_outbound(event_id, event):
                self.assertEqual("event-1", event_id)
                self.assertEqual("message-1", event["message_id"])
                return "answer", calls[0][3]

            @staticmethod
            def mark_outbound_result(event_id, fidelity, transforms):
                calls.append(("fidelity", event_id, fidelity, tuple(transforms)))

            @staticmethod
            def mark_reply_retry(event_id, error):
                calls.append(("retry", event_id, error))

            @staticmethod
            def mark_completed(event_id):
                raise AssertionError(event_id)

            @staticmethod
            def mark_terminal(event_id, error):
                raise AssertionError((event_id, error))

        runtime = object.__new__(BridgeRuntime)
        runtime.state = FakeState()
        runtime.lark_cli = "lark-cli"
        runtime.config = load_config()
        with patch("bridge_core.runtime.reply_to_message", side_effect=OSError("offline")):
            runtime._deliver(
                "event-1",
                {"event_id": "event-1", "message_id": "message-1"},
                "answer",
            )

        self.assertEqual(("fidelity", "event-1", "unknown", ()), calls[1])
        self.assertEqual("retry", calls[2][0])

    def test_pending_empty_answer_retries_the_explicit_fallback(self) -> None:
        calls = []

        class FakeState:
            @staticmethod
            def outbound_plan(row):
                return row.get("outbound_plan")

            @staticmethod
            def verified_outbound(event_id, event):
                self.assertEqual("event-1", event_id)
                self.assertEqual("message-1", event["message_id"])
                return "", outbound_plan

            @staticmethod
            def mark_outbound_result(event_id, fidelity, transforms):
                calls.append(("fidelity", event_id, fidelity, tuple(transforms)))

            @staticmethod
            def mark_completed(event_id):
                calls.append(("completed", event_id))

            @staticmethod
            def mark_reply_retry(event_id, error):
                raise AssertionError((event_id, error))

            @staticmethod
            def mark_terminal(event_id, error):
                raise AssertionError((event_id, error))

        runtime = object.__new__(BridgeRuntime)
        runtime.state = FakeState()
        runtime.lark_cli = "lark-cli"
        runtime.config = load_config()
        result = ReplyResult(
            True,
            retryable=False,
            outbound_fidelity="explicit_transform",
            outbound_transforms=("empty_fallback",),
        )
        outbound_plan = build_reply_plan("", runtime.config).to_payload()
        with patch("bridge_core.runtime.reply_to_message", return_value=result) as reply:
            runtime._deliver_pending(
                {
                    "event_id": "event-1",
                    "answer": "",
                    "outbound_plan": outbound_plan,
                },
                {"event_id": "event-1", "message_id": "message-1"},
            )

        self.assertEqual("", reply.call_args.args[2])
        self.assertEqual(
            ("fidelity", "event-1", "explicit_transform", ("empty_fallback",)),
            calls[0],
        )
        self.assertEqual(("completed", "event-1"), calls[1])

    def test_outbox_integrity_failure_never_calls_reply_to_message(self) -> None:
        terminal = []

        class FakeState:
            @staticmethod
            def mark_reply_pending(event_id, answer, outbound_plan):
                del event_id, answer, outbound_plan
                return True

            @staticmethod
            def verified_outbound(event_id, event):
                del event_id, event
                raise ValueError("tampered")

            @staticmethod
            def outbound_plan(row):
                return row.get("outbound_plan_json")

            @staticmethod
            def mark_terminal(event_id, error):
                terminal.append((event_id, error))

        runtime = object.__new__(BridgeRuntime)
        runtime.state = FakeState()
        runtime.lark_cli = "lark-cli"
        runtime.config = load_config()
        event = {"event_id": "event-1", "message_id": "message-1"}

        with patch("bridge_core.runtime.reply_to_message") as reply:
            runtime._deliver("event-1", event, "answer")
            runtime._deliver_pending(
                {
                    "event_id": "event-1",
                    "answer": "answer",
                    "outbound_plan_json": "sealed-but-tampered",
                },
                event,
            )

        reply.assert_not_called()
        self.assertEqual(
            [
                ("event-1", "reply_pending outbound envelope integrity failed"),
                ("event-1", "reply_pending outbound envelope integrity failed"),
            ],
            terminal,
        )

    def test_verified_restart_outbox_preserves_unicode_crlf_and_chunks(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            path = Path(temporary) / "state.sqlite3"
            event = {
                "event_id": "event-1",
                "message_id": "message-1",
                "_bridge_scope": "p2p:chat-1",
            }
            answer = ("青岚🚀  " * 140) + "\r\n第二行尾部  \n"
            with patch.dict(
                os.environ,
                {"CODEX_BRIDGE_MAX_REPLY_CHARS": "500"},
                clear=False,
            ):
                config = load_config()
            plan = build_reply_plan(answer, config)
            self.assertGreater(len(plan.pieces), 1)

            state = DurableState(path)
            state.enqueue("event-1", "message-1", "p2p:chat-1", event)
            state.claim("event-1")
            state.mark_reply_pending("event-1", answer, plan.to_payload())
            state.close()

            recovered = DurableState(path)
            runtime = object.__new__(BridgeRuntime)
            runtime.state = recovered
            runtime.lark_cli = "lark-cli"
            runtime.config = config
            result = ReplyResult(
                True,
                retryable=False,
                outbound_fidelity=plan.outbound_fidelity,
                outbound_transforms=plan.outbound_transforms,
            )
            try:
                with patch(
                    "bridge_core.runtime.reply_to_message",
                    return_value=result,
                ) as reply:
                    runtime._deliver_pending(recovered.get("event-1"), event)

                self.assertEqual(answer, reply.call_args.args[2])
                self.assertEqual(plan, reply.call_args.kwargs["plan"])
                terminal = recovered.get("event-1")
                self.assertEqual("completed", terminal["status"])
                self.assertIsNone(terminal["answer"])
                self.assertIsNone(terminal["outbound_envelope_sha256"])
            finally:
                recovered.close()


class BindingPromptTests(unittest.TestCase):
    def test_unbound_prompt_has_only_the_init_entry(self) -> None:
        answer = BridgeRuntime._unbound_answer({"name": "群聊·研发"})
        self.assertEqual("还没有连接 Codex 任务。请发送 `/init` 进入对话式设置。", answer)

    def test_bind_reports_missing_desktop_tools_without_blaming_thread_id(self) -> None:
        class FakeBeeper:
            @staticmethod
            def bind_thread(
                thread_id,
                name,
                *,
                request_key,
                expected_project_id,
                expected_host_id,
                catalog_snapshot_id,
                selection_proof,
            ):
                del (
                    thread_id,
                    name,
                    request_key,
                    expected_project_id,
                    expected_host_id,
                    catalog_snapshot_id,
                    selection_proof,
                )
                raise BeeperError(
                    "Required Desktop coordination method is unavailable to this task.",
                    code="responder_tool_unavailable",
                )

        class FakeSessions:
            @staticmethod
            def find_project_route_by_thread(scope, thread_id):
                del scope, thread_id
                return None

            @staticmethod
            def find_scope_by_thread(thread_id):
                del thread_id
                return None

        runtime = object.__new__(BridgeRuntime)
        runtime.beeper = FakeBeeper()
        runtime.sessions = FakeSessions()

        answer, committed = runtime._bind_existing_thread(
            "p2p:chat-a",
            {"name": "Alice", "active_project_id": ""},
            "11111111-2222-3333-4444-555555555555",
            "event-1:catalog-connect",
            expected_thread_id="",
            catalog_snapshot_id="a" * 32,
            catalog_task={
                "thread_id": "11111111-2222-3333-4444-555555555555",
                "title": "测试任务",
                "project_id": "project-a",
                "host_id": "local",
                "kind": "codex",
                "archived": False,
                "selection_proof": "c" * 64,
            },
            catalog_project={
                "project_id": "project-a",
                "label": "Bridge",
                "root": str(Path.cwd()),
                "host_id": "local",
                "kind": "local",
            },
        )

        self.assertFalse(committed)
        self.assertEqual(DESKTOP_RESPONDER_TOOLS_UNAVAILABLE_REPLY, answer)
        self.assertIn("不是任务 ID 格式错误", answer)
        self.assertIn("不会自动执行或重试", answer)
        self.assertIn("不会切换到旧路线", answer)
        self.assertNotIn("请核对会话 ID", answer)

    def test_successful_existing_binding_includes_one_plain_language_risk_notice(self) -> None:
        thread_id = "11111111-2222-3333-4444-555555555555"

        class FakeBeeper:
            @staticmethod
            def bind_thread(
                candidate,
                name,
                *,
                request_key,
                expected_project_id,
                expected_host_id,
                catalog_snapshot_id,
                selection_proof,
            ):
                del (
                    name,
                    request_key,
                    expected_project_id,
                    expected_host_id,
                    catalog_snapshot_id,
                    selection_proof,
                )
                return ResponderActivation(
                    candidate,
                    responder_host_id="local",
                    operation_receipt="b" * 32,
                )

        class FakeSessions:
            @staticmethod
            def find_scope_by_thread(candidate):
                del candidate
                return None

            @staticmethod
            def bind_thread_if_current(scope, candidate, **values):
                del scope, candidate, values

        runtime = object.__new__(BridgeRuntime)
        runtime.beeper = FakeBeeper()
        runtime.sessions = FakeSessions()
        answer, committed = runtime._bind_existing_thread(
            "p2p:chat-a",
            {"name": "Alice"},
            thread_id,
            "event-1:catalog-connect",
            expected_thread_id="",
            catalog_snapshot_id="a" * 32,
            catalog_task={
                "thread_id": thread_id,
                "title": "测试任务",
                "project_id": "project-a",
                "host_id": "local",
                "kind": "codex",
                "archived": False,
                "selection_proof": "c" * 64,
            },
            catalog_project={
                "project_id": "project-a",
                "label": "Bridge",
                "root": str(Path.cwd()),
                "host_id": "local",
                "kind": "local",
            },
        )

        self.assertTrue(committed)
        self.assertEqual(1, answer.count(BINDING_RISK_NOTICE))
        self.assertIn("仍可能重复执行或没有执行", answer)
        self.assertIn("不可撤销", answer)


class ProducerFailClosedRuntimeTests(unittest.TestCase):
    def test_fail_closed_replies_are_exact_and_never_advise_legacy_reactivation(
        self,
    ) -> None:
        self.assertEqual(
            "桥接已收到请求。Beeper 尚未配置或当前不可用，因此这条消息不会自动执行或重试。"
            "旧消息与历史 Beeper 均不会恢复或补发。",
            BEEPER_UNAVAILABLE_REPLY,
        )
        self.assertEqual(
            "Beeper 当前无法使用所需的 Desktop 任务协调工具，因此没有核验、绑定或发送到 Responder。"
            "这不是任务 ID 格式错误；本次操作不会自动执行或重试，也不会切换到旧路线。",
            DESKTOP_RESPONDER_TOOLS_UNAVAILABLE_REPLY,
        )
        runtime_source = (
            SKILL_ROOT / "scripts" / "bridge_core" / "runtime.py"
        ).read_text(encoding="utf-8")
        for retired_instruction in (
            "完成 Beeper 挂载",
            "恢复历史定时 Beeper",
            "修复 Beeper 工具可用性",
            "Beeper：在线等待",
            "Beeper：调度暂停或超时",
        ):
            with self.subTest(retired_instruction=retired_instruction):
                self.assertNotIn(retired_instruction, runtime_source)

    def test_held_producer_request_is_durable_and_never_rescheduled(self) -> None:
        rows = [
            {
                "event_id": "event-held",
                "scope": "p2p:held",
                "last_error": DESKTOP_PRODUCER_HOLD_ERROR,
            },
            {
                "event_id": "event-transient",
                "scope": "p2p:transient",
                "last_error": "transient_pre_turn_failure",
            },
        ]
        terminal = []

        class FakeState:
            @staticmethod
            def recoverable():
                return list(rows)

            @staticmethod
            def payload(row):
                del row
                return {"message_id": "message-1"}

            @staticmethod
            def mark_terminal(event_id, error):
                terminal.append((event_id, error))

        runtime = object.__new__(BridgeRuntime)
        runtime.state = FakeState()
        scheduled = []
        runtime._schedule = lambda event_id, scope: scheduled.append((event_id, scope))

        runtime._reschedule_recoverable()

        self.assertEqual([("event-transient", "p2p:transient")], scheduled)
        self.assertEqual([], terminal)

    def test_hold_records_answer_free_marker_and_notifies_only_once(self) -> None:
        class FakeState:
            def __init__(self, attempts):
                self.attempts = attempts
                self.retryable = []

            def get(self, event_id):
                del event_id
                return {"attempts": self.attempts}

            def mark_retryable(self, event_id, error):
                self.retryable.append((event_id, error))

        runtime = object.__new__(BridgeRuntime)
        runtime.lark_cli = Path("lark-cli")
        runtime.config = SimpleNamespace()
        event = {"message_id": "message-1"}

        first_state = FakeState(1)
        runtime.state = first_state
        with patch("bridge_core.runtime.reply_to_message") as reply:
            runtime._hold_for_desktop_producer("event-1", event)
        self.assertEqual(
            [("event-1", DESKTOP_PRODUCER_HOLD_ERROR)],
            first_state.retryable,
        )
        self.assertEqual(BEEPER_UNAVAILABLE_REPLY, reply.call_args.args[2])
        self.assertEqual(
            "producer-unavailable",
            reply.call_args.kwargs["idempotency_namespace"],
        )

        repeated_state = FakeState(2)
        runtime.state = repeated_state
        with patch("bridge_core.runtime.reply_to_message") as repeated_reply:
            runtime._hold_for_desktop_producer("event-1", event)
        self.assertEqual(
            [("event-1", DESKTOP_PRODUCER_HOLD_ERROR)],
            repeated_state.retryable,
        )
        repeated_reply.assert_not_called()

    @staticmethod
    def _ordinary_message_runtime(failure: Exception):
        event = {"message_id": "message-1", "chat_type": "p2p"}

        class FakeState:
            def __init__(self):
                self.row = {
                    "event_id": "event-1",
                    "scope": "p2p:chat-a",
                    "status": "queued",
                    "attempts": 1,
                    "last_error": "",
                }
                self.retained_payload = dict(event)
                self.retryable = []
                self.responder_not_started = []

            def get(self, event_id):
                del event_id
                return dict(self.row)

            def payload(self, row):
                del row
                return dict(self.retained_payload)

            def recoverable(self):
                return [dict(self.row)]

            def claim(self, event_id):
                del event_id
                self.row["status"] = "running"
                return True

            def mark_retryable(self, event_id, error):
                self.retryable.append((event_id, error))
                self.row["status"] = "retryable_failed"
                self.row["last_error"] = error

            def mark_responder_not_started(self, event_id):
                self.responder_not_started.append(event_id)

        class FailingBeeper:
            @staticmethod
            def alert_responder(*args, **kwargs):
                del args, kwargs
                raise failure

        runtime = object.__new__(BridgeRuntime)
        runtime.state = FakeState()
        runtime.beeper = FailingBeeper()
        runtime.lark_cli = Path("lark-cli")
        runtime.config = SimpleNamespace()
        runtime.bot_open_id = ""
        runtime._scheduler_lock = threading.RLock()
        runtime._active_turns = {}
        runtime._access_decision = lambda value: (True, "owner")
        runtime.sessions = SimpleNamespace(
            get=lambda scope: {
                "thread_id": "thread-a",
                "name": "Alice",
                "reply_mode": "quiet",
            }
        )
        runtime._ensure_session = lambda *args: {
            "thread_id": "thread-a",
            "name": "Alice",
            "reply_mode": "quiet",
        }
        delivered = []
        runtime._deliver = lambda *args: delivered.append(args)
        return runtime, event, delivered

    def test_ordinary_message_beeper_unavailable_is_held_without_retry(self) -> None:
        runtime, event, delivered = self._ordinary_message_runtime(
            BeeperQueueUnavailable()
        )
        with patch("bridge_core.runtime.extract_message_text", return_value="hello"), patch(
            "bridge_core.runtime.download_message_resources", return_value=[]
        ), patch(
            "bridge_core.runtime.build_turn_material",
            return_value=("hello", [], [], ""),
        ), patch("bridge_core.runtime.reply_to_message") as reply:
            runtime._process_event("event-1", "p2p:chat-a")

        self.assertEqual(
            [("event-1", DESKTOP_PRODUCER_HOLD_ERROR)],
            runtime.state.retryable,
        )
        self.assertEqual([], delivered)
        self.assertEqual(BEEPER_UNAVAILABLE_REPLY, reply.call_args.args[2])
        self.assertEqual(
            event,
            runtime.state.payload(runtime.state.get("event-1")),
        )

        scheduled = []
        runtime._schedule = lambda *args: scheduled.append(args)
        runtime._reschedule_recoverable()
        self.assertEqual([], scheduled)

    def test_ordinary_message_terminal_task_tool_failure_is_not_retried(self) -> None:
        runtime, _event, delivered = self._ordinary_message_runtime(
            BeeperError(
                "missing",
                code="responder_tool_unavailable",
                retryable=True,
            )
        )
        with patch("bridge_core.runtime.extract_message_text", return_value="hello"), patch(
            "bridge_core.runtime.download_message_resources", return_value=[]
        ), patch(
            "bridge_core.runtime.build_turn_material",
            return_value=("hello", [], [], ""),
        ), patch("bridge_core.runtime.reply_to_message") as reply:
            runtime._process_event("event-1", "p2p:chat-a")

        self.assertEqual([], runtime.state.retryable)
        self.assertEqual(1, len(delivered))
        self.assertEqual(DESKTOP_RESPONDER_TOOLS_UNAVAILABLE_REPLY, delivered[0][2])
        reply.assert_not_called()

    def test_unclaimed_beeper_reports_safe_terminal_without_replay(self) -> None:
        runtime, _event, delivered = self._ordinary_message_runtime(
            BeeperNotLoaded(
                "Beeper did not claim",
                code="beeper_claim_timeout",
            )
        )
        with patch("bridge_core.runtime.extract_message_text", return_value="hello"), patch(
            "bridge_core.runtime.download_message_resources", return_value=[]
        ), patch(
            "bridge_core.runtime.build_turn_material",
            return_value=("hello", [], [], ""),
        ), patch("bridge_core.runtime.reply_to_message") as reply:
            runtime._process_event("event-1", "p2p:chat-a")

        self.assertEqual([], runtime.state.retryable)
        self.assertEqual(["event-1"], runtime.state.responder_not_started)
        self.assertEqual(1, len(delivered))
        self.assertIn("Responder 尚未开始", delivered[0][2])
        self.assertIn("不会自动重跑", delivered[0][2])
        reply.assert_not_called()


class InitWizardTests(unittest.TestCase):
    TASK_ONE = "11111111-2222-3333-4444-555555555555"
    TASK_TWO = "66666666-7777-8888-9999-aaaaaaaaaaaa"

    def test_selection_requires_codex_kind_and_fenced_proof(self) -> None:
        task = {
            "thread_id": self.TASK_ONE,
            "project_id": "project-a",
            "host_id": "local",
            "kind": "codex",
            "archived": False,
            "selection_proof": "c" * 64,
        }
        project = {"project_id": "project-a", "host_id": "local"}
        wizard = {"catalog": {"snapshot_id": "a" * 32}}
        self.assertIsNotNone(BridgeRuntime._wizard_selection(wizard, task, project))
        task["selection_proof"] = "C" * 64
        self.assertIsNone(BridgeRuntime._wizard_selection(wizard, task, project))
        task["selection_proof"] = "c" * 64
        task["kind"] = "chatgpt"
        self.assertIsNone(BridgeRuntime._wizard_selection(wizard, task, project))

    def test_only_init_dispatches_and_unknown_slash_input_is_rejected(self) -> None:
        runtime = object.__new__(BridgeRuntime)
        calls = []
        runtime._begin_init_wizard = lambda *args, **kwargs: calls.append(args) or "menu"
        self.assertEqual(
            "menu",
            runtime._command_answer(
                "init", "", "scope", {}, "owner", "ou-owner", "event-init"
            ),
        )
        self.assertEqual(1, len(calls))
        self.assertEqual(
            "飞书 Bridge 仅支持 `/init`。请发送 `/init` 进入设置。",
            runtime._command_answer(
                "init",
                "unexpected argument",
                "scope",
                {},
                "owner",
                "ou-owner",
                "event-init-with-argument",
            ),
        )
        self.assertEqual(
            "飞书 Bridge 仅支持 `/init`。请发送 `/init` 进入设置。",
            runtime._command_answer(
                "unknown",
                "untrusted argument",
                "scope",
                {},
                "owner",
                "ou-owner",
                "event-unknown",
            ),
        )
        self.assertEqual(1, len(calls))

    @staticmethod
    def _catalog(
        root: Path,
        *,
        project_label: str = "Bridge",
        task_title: str = "现有任务",
    ) -> DesktopTaskCatalog:
        return DesktopTaskCatalog(
            projects=(
                DesktopProjectSummary(
                    "project-a",
                    project_label,
                    root=str(root),
                    host_id="local",
                    kind="local",
                ),
            ),
            tasks=(
                DesktopTaskSummary(
                    InitWizardTests.TASK_ONE,
                    task_title,
                    "project-a",
                    "local",
                    "idle",
                    False,
                    10,
                    kind="codex",
                    selection_proof="c" * 64,
                ),
            ),
            include_archived=False,
            truncated=False,
            snapshot_id="a" * 32,
            snapshot_expires_at=time.time() + 600,
        )

    def _assert_init_display_marker_is_plain_text(
        self,
        *,
        project_label: str = "Bridge",
        task_title: str = "现有任务",
    ) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            root = Path(temporary)
            marker = "[[feishu-file:control-secret.txt]]"
            (root / "control-secret.txt").write_text("must not be attached", encoding="utf-8")
            catalog = self._catalog(
                root,
                project_label=project_label,
                task_title=task_title,
            )

            class FakeBeeper:
                @staticmethod
                def list_task_catalog(**kwargs):
                    del kwargs
                    return catalog

            class FakeSessions:
                def __init__(self):
                    self.session = {"name": "Alice"}

                def update(self, scope, values):
                    del scope
                    self.session.update(values)
                    return dict(self.session)

            class FakeState:
                finished = []

                @staticmethod
                def begin_control_reply(event_id):
                    return event_id == "event-init-marker"

                def finish_control_reply(self, event_id, **kwargs):
                    self.finished.append((event_id, kwargs))
                    return True

            runtime = object.__new__(BridgeRuntime)
            runtime.config = SimpleNamespace(
                project_root=root,
                max_reply_chars=2800,
                reply_format="markdown",
            )
            runtime.beeper = FakeBeeper()
            runtime.sessions = FakeSessions()
            runtime.state = FakeState()
            runtime.lark_cli = "lark-cli"
            answer = runtime._begin_init_wizard(
                "p2p:chat",
                {"name": "Alice"},
                "owner",
                "ou-owner",
                "event-init-marker",
            )
            self.assertIn(marker, answer)

            captured = []

            def fake_reply(*args, **kwargs):
                del args
                plan = kwargs["plan"]
                captured.append(plan)
                return ReplyResult(
                    True,
                    retryable=False,
                    outbound_fidelity=plan.outbound_fidelity,
                    outbound_transforms=plan.outbound_transforms,
                )

            event = {
                "event_id": "event-init-marker",
                "message_id": "message-init-marker",
            }
            with patch("bridge_core.runtime.reply_to_message", side_effect=fake_reply):
                runtime._deliver_control_once("event-init-marker", event, answer)

            self.assertEqual(1, len(captured))
            plan = captured[0]
            self.assertTrue(plan.pieces)
            self.assertTrue(all(kind == "text" for kind, _, _ in plan.pieces))
            self.assertIn(marker, "".join(value for _, value, _ in plan.pieces))
            self.assertNotIn("attachment_marker", plan.outbound_transforms)
            self.assertNotIn("attachment_omitted", plan.outbound_transforms)
            self.assertEqual(1, len(runtime.state.finished))

    def test_init_task_title_marker_is_never_an_attachment(self) -> None:
        self._assert_init_display_marker_is_plain_text(
            task_title="[[feishu-file:control-secret.txt]]"
        )

    def test_init_project_label_marker_is_never_an_attachment(self) -> None:
        self._assert_init_display_marker_is_plain_text(
            project_label="[[feishu-file:control-secret.txt]]"
        )

    def test_owner_catalog_is_bounded_and_never_renders_project_paths(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            root = Path(temporary)
            captured = []

            class FakeBeeper:
                def list_task_catalog(self, **kwargs):
                    captured.append(kwargs)
                    return InitWizardTests._catalog(root)

            class FakeSessions:
                def __init__(self):
                    self.session = {"name": "Alice"}

                @staticmethod
                def related_thread_ids(scope):
                    raise AssertionError(scope)

                def update(self, scope, values):
                    del scope
                    self.session.update(values)
                    return dict(self.session)

            runtime = object.__new__(BridgeRuntime)
            runtime.config = SimpleNamespace(project_root=root)
            runtime.beeper = FakeBeeper()
            runtime.sessions = FakeSessions()
            answer = runtime._begin_init_wizard(
                "p2p:chat",
                {"name": "Alice"},
                "owner",
                "ou-owner",
                "event-init",
            )

            self.assertIsNone(captured[0]["visible_thread_ids"])
            self.assertEqual(50, captured[0]["limit"])
            self.assertIn("项目：Bridge", answer)
            self.assertIn("现有任务", answer)
            self.assertIn(self.TASK_ONE, answer)
            self.assertNotIn(str(root), answer)
            self.assertNotIn("新建任务", answer)
            self.assertNotIn("新建项目", answer)
            self.assertNotIn("查看归档", answer)
            self.assertNotIn("压缩当前任务", answer)
            self.assertNotIn("解除连接", answer)
            self.assertNotIn("设置回复", answer)
            persisted = json.dumps(runtime.sessions.session, ensure_ascii=False)
            self.assertNotIn('"init_wizard":', persisted)
            self.assertNotIn('"catalog":', persisted)
            self.assertNotIn(str(root), persisted)
            transient = json.dumps(runtime._init_wizards, ensure_ascii=False)
            self.assertNotIn(str(root), transient)
            self.assertIn("ou-owner", transient)

    def test_regular_catalog_passes_only_exact_scope_task_ids(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            root = Path(temporary)
            captured = []

            class FakeBeeper:
                def list_task_catalog(self, **kwargs):
                    captured.append(kwargs)
                    return InitWizardTests._catalog(root)

            class FakeSessions:
                def __init__(self):
                    self.session = {"name": "Alice", "thread_id": InitWizardTests.TASK_ONE}

                def related_thread_ids(inner_self, scope):
                    self.assertEqual("p2p:chat", scope)
                    return [InitWizardTests.TASK_ONE]

                def update(self, scope, values):
                    del scope
                    self.session.update(values)
                    return dict(self.session)

            runtime = object.__new__(BridgeRuntime)
            runtime.config = SimpleNamespace(project_root=root)
            runtime.beeper = FakeBeeper()
            runtime.sessions = FakeSessions()
            runtime._begin_init_wizard(
                "p2p:chat",
                dict(runtime.sessions.session),
                "guest",
                "ou-user",
                "event-init",
            )

            self.assertEqual([self.TASK_ONE], captured[0]["visible_thread_ids"])

    def test_snapshot_number_requires_confirmation_before_binding(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            root = Path(temporary)

            class FakeBeeper:
                @staticmethod
                def list_task_catalog(**kwargs):
                    del kwargs
                    return InitWizardTests._catalog(root)

            class FakeSessions:
                def __init__(self):
                    self.session = {"name": "Alice"}

                @staticmethod
                def related_thread_ids(scope):
                    del scope
                    return []

                def update(self, scope, values):
                    del scope
                    self.session.update(values)
                    return dict(self.session)

            runtime = object.__new__(BridgeRuntime)
            runtime.config = SimpleNamespace(project_root=root)
            runtime.beeper = FakeBeeper()
            runtime.sessions = FakeSessions()
            runtime._begin_init_wizard(
                "p2p:chat",
                dict(runtime.sessions.session),
                "owner",
                "ou-owner",
                "event-init",
            )
            self.assertTrue(runtime._wizard_owned_by("p2p:chat", "ou-owner", "owner"))
            self.assertFalse(runtime._wizard_owned_by("p2p:chat", "ou-other", "owner"))
            untouched = runtime._handle_init_wizard_reply(
                "p2p:chat",
                dict(runtime.sessions.session),
                "owner",
                "ou-other",
                "1",
                "event-other-member",
            )
            self.assertIn("其他群成员", untouched)
            hidden = runtime._handle_init_wizard_reply(
                "p2p:chat",
                dict(runtime.sessions.session),
                "owner",
                "ou-owner",
                "新建任务",
                "event-hidden-action",
            )
            self.assertIn("没有识别", hidden)
            self.assertEqual("catalog", runtime._wizard("p2p:chat")["stage"])
            selected = runtime._handle_init_wizard_reply(
                "p2p:chat",
                dict(runtime.sessions.session),
                "owner",
                "ou-owner",
                "1",
                "event-select",
            )
            self.assertIn("回复“确认”", selected)
            bound = []
            runtime._bind_existing_thread = (
                lambda *args, **kwargs: (
                    bound.append((args, kwargs)) or ("connected", True)
                )
            )
            result = runtime._handle_init_wizard_reply(
                "p2p:chat",
                dict(runtime.sessions.session),
                "owner",
                "ou-owner",
                "确认",
                "event-confirm",
            )
            self.assertEqual("connected", result)
            self.assertEqual(self.TASK_ONE, bound[0][0][2])
            self.assertEqual("现有任务", bound[0][1]["catalog_task"]["title"])

    def test_runtime_exposes_no_project_create_or_responder_recovery_route(self) -> None:
        for name in (
            "_create_and_bind_thread",
            "_project_new",
            "_compact_and_continue",
            "_replace_unavailable_responder",
            "_should_auto_replace_unavailable_responder",
            "_responder_delivery_request_key",
        ):
            with self.subTest(name=name):
                self.assertFalse(hasattr(BridgeRuntime, name))

    def test_expired_snapshot_performs_no_action(self) -> None:
        class FakeSessions:
            def __init__(self):
                self.session = {"init_wizard_expires_at": 1}

            def update(self, scope, values):
                del scope
                self.session.update(values)
                return dict(self.session)

        runtime = object.__new__(BridgeRuntime)
        runtime.sessions = FakeSessions()
        runtime._init_wizards = {
            "p2p:chat": {"version": 1, "expires_at": 1, "stage": "confirm_connect"}
        }
        runtime._wizard_lock = threading.RLock()
        runtime._bind_existing_thread = lambda *args, **kwargs: self.fail((args, kwargs))
        answer = runtime._handle_init_wizard_reply(
            "p2p:chat",
            dict(runtime.sessions.session),
            "owner",
            "ou-owner",
            "确认",
            "event-expired",
        )
        self.assertIn("设置已过期", answer)
        self.assertEqual(0.0, runtime.sessions.session["init_wizard_expires_at"])
        self.assertFalse(runtime._init_wizards)


class CommandBeeperStateTests(unittest.TestCase):
    def test_missing_task_tools_returns_specific_fail_closed_reply(self) -> None:
        runtime = object.__new__(BridgeRuntime)
        delivered = []
        runtime._deliver_control_once = lambda *args, **kwargs: delivered.append(args)
        runtime._handle_command_beeper_error(
            "event-tools",
            {"message": "test"},
            BeeperError(
                "missing",
                code="responder_tool_unavailable",
                may_have_started=False,
            ),
        )
        self.assertEqual(1, len(delivered))
        self.assertEqual(DESKTOP_RESPONDER_TOOLS_UNAVAILABLE_REPLY, delivered[0][2])
        self.assertIn("不会自动执行或重试", delivered[0][2])

    def test_missing_producer_terminalizes_read_only_command_without_retry(self) -> None:
        class FakeState:
            @staticmethod
            def mark_retryable(event_id, error):
                raise AssertionError((event_id, error))

        runtime = object.__new__(BridgeRuntime)
        runtime.state = FakeState()
        delivered = []
        runtime._deliver_control_once = lambda *args, **kwargs: delivered.append(args)

        runtime._handle_command_beeper_error(
            "event-producer",
            {"message_id": "message-1"},
            BeeperQueueUnavailable(),
        )

        self.assertEqual(1, len(delivered))
        self.assertEqual(BEEPER_UNAVAILABLE_REPLY, delivered[0][2])

    def test_retryable_read_only_error_is_delivered_terminally(self) -> None:
        class FakeState:
            @staticmethod
            def mark_retryable(event_id, message):
                raise AssertionError((event_id, message))

        runtime = object.__new__(BridgeRuntime)
        runtime.state = FakeState()
        delivered = []
        runtime._deliver_control_once = lambda *args, **kwargs: delivered.append(args)

        runtime._handle_command_beeper_error(
            "event-1",
            {"message": "test"},
            BeeperError("still pending", retryable=True),
        )

        self.assertEqual(1, len(delivered))
        self.assertIn("不会自动重试", delivered[0][2])

    def test_uncertain_command_is_terminal_without_replay(self) -> None:
        class FakeState:
            @staticmethod
            def mark_retryable(event_id, message):
                raise AssertionError((event_id, message))

        runtime = object.__new__(BridgeRuntime)
        runtime.state = FakeState()
        delivered = []
        runtime._deliver_control_once = lambda *args, **kwargs: delivered.append(args)

        runtime._handle_command_beeper_error(
            "event-2",
            {"message": "test"},
            BeeperError("unknown", may_have_started=True),
        )

        self.assertEqual(1, len(delivered))
        self.assertIn("不会自动重跑", delivered[0][2])


class StableConversationScopeTests(unittest.TestCase):
    def test_policy_role_changes_do_not_fork_one_group_binding(self) -> None:
        class FakeAccess:
            role = "owner"

            @classmethod
            def decide(cls, **kwargs):
                del kwargs
                return SimpleNamespace(role=cls.role, allowed=True)

        runtime = object.__new__(BridgeRuntime)
        runtime.config = SimpleNamespace(project_root=Path.cwd())
        runtime.access = FakeAccess()
        event = {"chat_type": "group", "chat_id": "oc_group"}

        owner_scope = runtime._policy_scope(event)[0]
        FakeAccess.role = "guest"
        guest_scope = runtime._policy_scope(event)[0]

        self.assertEqual("group:oc_group", owner_scope)
        self.assertEqual(owner_scope, guest_scope)

    @staticmethod
    def _event_runtime(*, session, role, sender, text, wizard=None):
        event = {"chat_type": "group", "chat_id": "oc_group", "message_id": "m1"}

        class FakeState:
            status = "queued"
            control_admissions = 0

            @staticmethod
            def get(event_id):
                del event_id
                return {"status": FakeState.status}

            @staticmethod
            def payload(row):
                del row
                return event

            @staticmethod
            def claim(event_id):
                del event_id
                FakeState.status = "running"
                return True

            @staticmethod
            def admit_control(event_id):
                del event_id
                if FakeState.status != "running":
                    return False
                FakeState.status = "control_sending"
                FakeState.control_admissions += 1
                return True

        class FakeSessions:
            @staticmethod
            def get(scope):
                del scope
                return dict(session)

            def update(self, scope, values):
                del scope
                session.update(values)
                return dict(session)

            def bind_thread(self, scope, thread_id, values=None):
                del scope
                if values:
                    session.update(values)
                session["thread_id"] = thread_id
                return dict(session)

        runtime = object.__new__(BridgeRuntime)
        runtime.state = FakeState()
        runtime.sessions = FakeSessions()
        runtime._access_decision = lambda value: (True, role)
        runtime._ensure_session = lambda *args: dict(session)
        runtime._wizard_lock = threading.RLock()
        runtime._init_wizards = {"group:oc_group": dict(wizard)} if wizard else {}
        runtime.bot_open_id = ""
        delivered = []
        runtime._deliver_control_once = (
            lambda *args, **kwargs: delivered.append((*args, kwargs))
        )
        return runtime, event, delivered, sender, text

    @staticmethod
    def _durable_wizard_runtime(root: Path, *, stage: str):
        scope = "group:oc_group"
        event_id = "event-control"
        event = {
            "chat_type": "group",
            "chat_id": "oc_group",
            "message_id": "message-control",
        }
        state_path = root / "state.sqlite3"
        session_path = root / "sessions.json"
        state = DurableState(state_path)
        state.enqueue(event_id, event["message_id"], scope, event)
        sessions = SessionStore(session_path)
        expires_at = time.time() + 300
        sessions.update(
            scope,
            {
                "name": "Group",
                "thread_id": InitWizardTests.TASK_TWO,
                "host_id": "local",
                "desktop_project_id": "project-old",
                "reply_mode": "quiet",
                "init_wizard_expires_at": expires_at,
            },
        )
        task = {
            "thread_id": InitWizardTests.TASK_ONE,
            "title": "New responder",
            "project_id": "project-a",
            "host_id": "local",
            "kind": "codex",
            "archived": False,
            "selection_proof": "c" * 64,
        }
        project = {
            "project_id": "project-a",
            "label": "Bridge",
            "host_id": "local",
            "kind": "local",
        }
        wizard = {
            "version": 1,
            "stage": stage,
            "expires_at": expires_at,
            "initiator_open_id": "ou-owner",
            "initiator_role": "owner",
            "expected_thread_id": InitWizardTests.TASK_TWO,
            "page": 0,
            "catalog": {
                "snapshot_id": "a" * 32,
                "snapshot_expires_at": expires_at,
                "projects": [project],
                "tasks": [task],
                "truncated": False,
            },
        }
        if stage == "confirm_connect":
            wizard["selected_task"] = task
            wizard["selected_project"] = project

        observed = {"beeper": [], "session": []}
        original_update = sessions.update
        original_bind = sessions.bind_thread_if_current

        def checked_update(*args, **kwargs):
            observed["session"].append(state.get(event_id)["status"])
            return original_update(*args, **kwargs)

        def checked_bind(*args, **kwargs):
            observed["session"].append(state.get(event_id)["status"])
            return original_bind(*args, **kwargs)

        sessions.update = checked_update
        sessions.bind_thread_if_current = checked_bind

        class FakeBeeper:
            def __init__(self):
                self.alert_calls = []

            def bind_thread(self, *args, **kwargs):
                observed["beeper"].append(state.get(event_id)["status"])
                return ResponderActivation(
                    InitWizardTests.TASK_ONE,
                    responder_host_id="local",
                    operation_receipt="d" * 32,
                )

            def alert_responder(self, *args, **kwargs):
                self.alert_calls.append((args, kwargs))
                return SimpleNamespace(
                    final_answer="unexpected",
                    responder_thread_id=InitWizardTests.TASK_TWO,
                    responder_host_id="local",
                )

        runtime = object.__new__(BridgeRuntime)
        runtime.state = state
        runtime.sessions = sessions
        runtime.beeper = FakeBeeper()
        runtime._access_decision = lambda value: (True, "owner")
        runtime._ensure_session = lambda current_scope, *args: sessions.get(
            current_scope
        )
        runtime._wizard_lock = threading.RLock()
        runtime._init_wizards = {scope: wizard}
        runtime.bot_open_id = ""
        delivered = []
        runtime._deliver_control_once = (
            lambda *args, **kwargs: delivered.append((*args, kwargs))
        )
        return (
            runtime,
            state_path,
            session_path,
            event_id,
            scope,
            delivered,
            observed,
        )

    def test_other_group_member_cannot_replace_active_init_wizard(self) -> None:
        wizard = {
            "version": 1,
            "stage": "catalog",
            "expires_at": time.time() + 300,
            "initiator_open_id": "ou-owner",
            "initiator_role": "owner",
        }
        runtime, _event, delivered, sender, text = self._event_runtime(
            session={"init_wizard_expires_at": wizard["expires_at"]},
            role="guest",
            sender="ou-guest",
            text="/init",
            wizard=wizard,
        )
        runtime._command_answer = lambda *args, **kwargs: self.fail((args, kwargs))
        with patch("bridge_core.runtime.extract_sender_open_id", return_value=sender), patch(
            "bridge_core.runtime.extract_message_text", return_value=text
        ):
            runtime._process_event("event-1", "group:oc_group")

        self.assertEqual(1, len(delivered))
        self.assertIn("其他群成员", delivered[0][2])
        self.assertEqual(1, runtime.state.control_admissions)
        self.assertTrue(delivered[0][3]["admitted"])
        self.assertEqual("ou-owner", runtime._wizard("group:oc_group")["initiator_open_id"])

    def test_other_group_member_wizard_token_is_not_routed_as_business(self) -> None:
        wizard = {
            "version": 1,
            "stage": "confirm_connect",
            "expires_at": time.time() + 300,
            "initiator_open_id": "ou-owner",
            "initiator_role": "owner",
        }
        runtime, _event, delivered, sender, text = self._event_runtime(
            session={
                "init_wizard_expires_at": wizard["expires_at"],
                "thread_id": "responder",
                "reply_mode": "quiet",
            },
            role="guest",
            sender="ou-guest",
            text="确认",
            wizard=wizard,
        )
        alert_calls = []
        runtime.beeper = SimpleNamespace(
            alert_responder=lambda *args, **kwargs: alert_calls.append((args, kwargs))
        )
        runtime._deliver = lambda *args, **kwargs: None
        with patch("bridge_core.runtime.extract_sender_open_id", return_value=sender), patch(
            "bridge_core.runtime.extract_message_text", return_value=text
        ), patch(
            "bridge_core.runtime.download_message_resources", return_value=[]
        ), patch(
            "bridge_core.runtime.build_turn_material",
            return_value=(text, [], [], ""),
        ):
            runtime._process_event("event-foreign-token", "group:oc_group")

        self.assertEqual([], alert_calls)
        self.assertEqual(1, runtime.state.control_admissions)
        self.assertEqual(1, len(delivered))
        self.assertIn("没有作为业务消息发送", delivered[0][2])

    def test_initiator_role_change_cannot_turn_wizard_token_into_business(self) -> None:
        wizard = {
            "version": 1,
            "stage": "confirm_connect",
            "expires_at": time.time() + 300,
            "initiator_open_id": "ou-owner",
            "initiator_role": "owner",
        }
        runtime, _event, delivered, sender, text = self._event_runtime(
            session={
                "init_wizard_expires_at": wizard["expires_at"],
                "thread_id": "responder",
            },
            role="guest",
            sender="ou-owner",
            text="取消",
            wizard=wizard,
        )
        alert_calls = []
        runtime.beeper = SimpleNamespace(
            alert_responder=lambda *args, **kwargs: alert_calls.append((args, kwargs))
        )
        runtime._deliver = lambda *args, **kwargs: None
        with patch("bridge_core.runtime.extract_sender_open_id", return_value=sender), patch(
            "bridge_core.runtime.extract_message_text", return_value=text
        ):
            runtime._process_event("event-role-change", "group:oc_group")

        self.assertEqual([], alert_calls)
        self.assertEqual(1, runtime.state.control_admissions)
        self.assertEqual(1, len(delivered))
        self.assertIn("访问角色已经变化", delivered[0][2])

    def test_other_group_member_ordinary_message_still_routes_during_wizard(self) -> None:
        wizard = {
            "version": 1,
            "stage": "catalog",
            "expires_at": time.time() + 300,
            "initiator_open_id": "ou-owner",
            "initiator_role": "owner",
        }
        runtime, _event, delivered, sender, text = self._event_runtime(
            session={
                "name": "Group",
                "init_wizard_expires_at": wizard["expires_at"],
                "thread_id": "responder",
                "host_id": "local",
                "reply_mode": "quiet",
            },
            role="guest",
            sender="ou-guest",
            text="请处理这条普通业务消息",
            wizard=wizard,
        )
        alert_calls = []

        def alert_responder(*args, **kwargs):
            alert_calls.append((args, kwargs))
            return SimpleNamespace(
                final_answer="done",
                responder_thread_id="responder",
                responder_host_id="local",
            )

        runtime.beeper = SimpleNamespace(alert_responder=alert_responder)
        runtime.config = SimpleNamespace()
        runtime.lark_cli = "lark"
        business_replies = []
        runtime._deliver = lambda *args, **kwargs: business_replies.append(args)
        with patch("bridge_core.runtime.extract_sender_open_id", return_value=sender), patch(
            "bridge_core.runtime.extract_message_text", return_value=text
        ), patch(
            "bridge_core.runtime.download_message_resources", return_value=[]
        ), patch(
            "bridge_core.runtime.build_turn_material",
            return_value=(text, [], [], ""),
        ):
            runtime._process_event("event-ordinary", "group:oc_group")

        self.assertEqual(1, len(alert_calls))
        self.assertEqual(0, runtime.state.control_admissions)
        self.assertEqual([], delivered)
        self.assertEqual(1, len(business_replies))

    def test_binding_commit_control_crash_is_terminal_after_reopen(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            root = Path(temporary)
            (
                runtime,
                state_path,
                session_path,
                event_id,
                scope,
                delivered,
                observed,
            ) = self._durable_wizard_runtime(root, stage="confirm_connect")
            with patch(
                "bridge_core.runtime.extract_sender_open_id",
                return_value="ou-owner",
            ), patch(
                "bridge_core.runtime.extract_message_text",
                return_value="确认",
            ):
                runtime._process_event(event_id, scope)

            self.assertEqual("control_sending", runtime.state.get(event_id)["status"])
            self.assertIsNone(runtime.state.get(event_id)["payload_json"])
            self.assertEqual(["control_sending"], observed["beeper"])
            self.assertEqual(["control_sending"], observed["session"])
            self.assertEqual(1, len(delivered))
            self.assertTrue(delivered[0][3]["admitted"])
            persisted = SessionStore(session_path).get(scope)
            self.assertEqual(InitWizardTests.TASK_ONE, persisted["thread_id"])
            self.assertEqual(0.0, persisted["init_wizard_expires_at"])
            self.assertEqual([], runtime.beeper.alert_calls)
            runtime.state.close()

            reopened = DurableState(state_path)
            try:
                self.assertEqual("terminal_failed", reopened.get(event_id)["status"])
                self.assertEqual([], reopened.recoverable())
                runtime.state = reopened
                runtime._process_event(event_id, scope)
                self.assertEqual([], runtime.beeper.alert_calls)
            finally:
                reopened.close()

    def test_cancel_or_exit_control_crash_is_terminal_after_reopen(self) -> None:
        for token in ("取消", "退出"):
            with self.subTest(token=token), tempfile.TemporaryDirectory(
                dir=TEST_TEMP_ROOT
            ) as temporary:
                root = Path(temporary)
                (
                    runtime,
                    state_path,
                    session_path,
                    event_id,
                    scope,
                    delivered,
                    observed,
                ) = self._durable_wizard_runtime(root, stage="catalog")
                with patch(
                    "bridge_core.runtime.extract_sender_open_id",
                    return_value="ou-owner",
                ), patch(
                    "bridge_core.runtime.extract_message_text",
                    return_value=token,
                ):
                    runtime._process_event(event_id, scope)

                self.assertEqual(
                    "control_sending",
                    runtime.state.get(event_id)["status"],
                )
                self.assertEqual([], observed["beeper"])
                self.assertEqual(["control_sending"], observed["session"])
                self.assertEqual(1, len(delivered))
                self.assertFalse(runtime._init_wizards)
                self.assertEqual(
                    0.0,
                    SessionStore(session_path).get(scope)["init_wizard_expires_at"],
                )
                self.assertEqual([], runtime.beeper.alert_calls)
                runtime.state.close()

                reopened = DurableState(state_path)
                try:
                    self.assertEqual(
                        "terminal_failed",
                        reopened.get(event_id)["status"],
                    )
                    self.assertEqual([], reopened.recoverable())
                    runtime.state = reopened
                    runtime._process_event(event_id, scope)
                    self.assertEqual([], runtime.beeper.alert_calls)
                finally:
                    reopened.close()

    def test_malformed_init_marker_never_falls_through_to_business_routing(self) -> None:
        session = {"init_wizard_expires_at": "corrupt", "thread_id": "responder"}
        runtime, _event, delivered, sender, text = self._event_runtime(
            session=session,
            role="owner",
            sender="ou-owner",
            text="确认",
        )
        runtime.beeper = SimpleNamespace(
            alert_responder=lambda *args, **kwargs: self.fail((args, kwargs))
        )
        with patch("bridge_core.runtime.extract_sender_open_id", return_value=sender), patch(
            "bridge_core.runtime.extract_message_text", return_value=text
        ):
            runtime._process_event("event-2", "group:oc_group")

        self.assertEqual(1, len(delivered))
        self.assertIn("已损坏", delivered[0][2])
        self.assertEqual(0.0, session["init_wizard_expires_at"])

    def test_stale_init_marker_never_falls_through_to_business_routing(self) -> None:
        session = {"init_wizard_expires_at": 1.0, "thread_id": "responder"}
        runtime, _event, delivered, sender, text = self._event_runtime(
            session=session,
            role="owner",
            sender="ou-owner",
            text="确认",
        )
        runtime.beeper = SimpleNamespace(
            alert_responder=lambda *args, **kwargs: self.fail((args, kwargs))
        )
        with patch("bridge_core.runtime.extract_sender_open_id", return_value=sender), patch(
            "bridge_core.runtime.extract_message_text", return_value=text
        ):
            runtime._process_event(
                "event-stale",
                "group:oc_group:policy:retired-fingerprint",
            )

        self.assertEqual(1, len(delivered))
        self.assertIn("设置已过期或 Bridge 已重启", delivered[0][2])
        self.assertEqual(0.0, session["init_wizard_expires_at"])

    def test_unexpected_init_exception_is_terminal_and_never_scheduler_retried(self) -> None:
        session = {"init_wizard_expires_at": 0.0, "thread_id": "responder"}
        runtime, _event, delivered, sender, text = self._event_runtime(
            session=session,
            role="owner",
            sender="ou-owner",
            text="/init",
        )
        runtime._wizard_pending = lambda scope: False
        runtime._command_answer = lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("unexpected implementation fault")
        )
        runtime.beeper = SimpleNamespace(
            alert_responder=lambda *args, **kwargs: self.fail((args, kwargs))
        )
        with patch("bridge_core.runtime.extract_sender_open_id", return_value=sender), patch(
            "bridge_core.runtime.extract_message_text", return_value=text
        ):
            runtime._process_event("event-init-fault", "group:oc_group")

        self.assertEqual(1, len(delivered))
        self.assertIn("已终止", delivered[0][2])
        self.assertIn("没有执行或自动重试", delivered[0][2])

    def test_recoverable_policy_scope_and_new_scope_share_one_serial_queue(self) -> None:
        runtime = object.__new__(BridgeRuntime)
        runtime._scheduler_lock = threading.RLock()
        runtime._scheduled = set()
        runtime._scope_queues = __import__("collections").defaultdict(
            __import__("collections").deque
        )
        runtime._scope_active = set()
        submissions = []
        runtime._executor = SimpleNamespace(
            submit=lambda callback, scope: submissions.append((callback, scope)) or object()
        )
        runtime._track_future = lambda future: None

        runtime._schedule("event-old", "group:oc_group:policy:retired")
        runtime._schedule("event-new", "group:oc_group")

        self.assertEqual(["group:oc_group"], list(runtime._scope_queues))
        self.assertEqual(
            ["event-old", "event-new"],
            list(runtime._scope_queues["group:oc_group"]),
        )
        self.assertEqual(1, len(submissions))
        self.assertEqual("group:oc_group", submissions[0][1])

    def test_ensure_session_never_recreates_a_policy_suffixed_scope(self) -> None:
        calls = []

        class FakeSessions:
            @staticmethod
            def consolidate_scope(scope):
                calls.append(("consolidate", scope))
                return {"name": "Group", "updated_at": 1.0}

            @staticmethod
            def update(scope, values):
                calls.append(("update", scope))
                return dict(values)

        runtime = object.__new__(BridgeRuntime)
        runtime.sessions = FakeSessions()
        session = runtime._ensure_session(
            "group:oc_group:policy:retired",
            {},
            "owner",
            "fingerprint",
        )

        self.assertEqual(
            [("consolidate", "group:oc_group"), ("update", "group:oc_group")],
            calls,
        )
        self.assertEqual("beeper", session["session_owner"])


class DesktopBeeperPromptContractTests(unittest.TestCase):
    def test_final_callback_mcp_exposes_only_controller_tools(self) -> None:
        tools = {tool["name"]: tool for tool in final_callback_mcp.TOOLS}
        self.assertEqual(
            {
                "claim_and_arm",
                "claim_readonly",
                "complete_readonly",
                "submit_final_callback",
                "finish_final_callback",
                "fail_page",
            },
            set(tools),
        )
        for name in tools:
            self.assertNotIn("_meta", tools[name])
            self.assertFalse(tools[name]["annotations"]["readOnlyHint"])
        wait_schema = tools["finish_final_callback"]["inputSchema"]["properties"][
            "wait_seconds"
        ]
        self.assertEqual(0, wait_schema["minimum"])
        self.assertEqual(30, wait_schema["maximum"])
        submit_schema = tools["submit_final_callback"]["inputSchema"]
        self.assertEqual(
            ["final_callback_capability", "final_answer"],
            submit_schema["required"],
        )
        self.assertFalse(submit_schema["additionalProperties"])
        claim_readonly_schema = tools["claim_readonly"]["inputSchema"]
        self.assertEqual(["page"], claim_readonly_schema["required"])
        self.assertFalse(claim_readonly_schema["additionalProperties"])
        complete_readonly_schema = tools["complete_readonly"]["inputSchema"]
        self.assertEqual(
            ["page", "result"],
            complete_readonly_schema["required"],
        )
        self.assertFalse(complete_readonly_schema["additionalProperties"])
        readonly_result_schemas = complete_readonly_schema["properties"]["result"][
            "oneOf"
        ]
        self.assertEqual(2, len(readonly_result_schemas))
        self.assertTrue(
            all(schema["additionalProperties"] is False for schema in readonly_result_schemas)
        )

    def test_final_callback_mcp_routes_bounded_helper_calls(self) -> None:
        page = "a" * 32
        final_callback_capability = "b" * 32
        final_answer = " 前导🚀\r\n精确 Final Callback 答案 🙂 "
        snapshot_id = "c" * 32
        operation_receipt = "d" * 32
        responder_thread_id = "11111111-2222-3333-4444-555555555555"
        excluded_thread_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        catalog_title = "现有任务🚀"
        readonly_claim = {
            "ok": True,
            "status": "claimed_readonly",
            "operation": "list_task_catalog",
            "request": {
                "catalog_version": 1,
                "visibility": "all",
                "thread_ids": [],
                "include_archived": False,
                "limit": 50,
                "excluded_thread_ids": [excluded_thread_id],
                "snapshot_id": snapshot_id,
            },
        }
        catalog_result = {
            "catalog_version": 1,
            "snapshot_id": snapshot_id,
            "include_archived": False,
            "truncated": False,
            "projects": [
                {
                    "project_id": "project-a",
                    "label": "Bridge 项目",
                    "host_id": "local",
                    "kind": "local",
                }
            ],
            "tasks": [
                {
                    "thread_id": responder_thread_id,
                    "title": catalog_title,
                    "project_id": "project-a",
                    "host_id": "local",
                    "kind": "codex",
                    "status": "idle",
                    "archived": False,
                    "updated_at": 123.5,
                }
            ],
        }
        inspection_result = {
            "thread_id": responder_thread_id,
            "project_id": "project-a",
            "host_id": "local",
            "archived": False,
            "catalog_snapshot_id": snapshot_id,
            "operation_receipt": operation_receipt,
        }
        responses = (
            {
                "ok": True,
                "status": "claimed_armed",
                "responder_thread_id": responder_thread_id,
                "responder_host_id": "responder-host",
                "prompt": "你好🙂",
            },
            readonly_claim,
            {"ok": True, "status": "completed", "terminal": True},
            {"ok": True, "accepted": True, "state": "captured"},
            {
                "ok": True,
                "status": "completed",
                "terminal": True,
            },
            {"ok": True, "status": "failed", "terminal": True},
        )
        with patch.object(
            final_callback_mcp,
            "_invoke_helper_command",
            side_effect=responses,
        ) as invoke:
            self.assertEqual(
                responses[0],
                final_callback_mcp._call_tool("claim_and_arm", {"page": page}),
            )
            self.assertEqual(
                responses[1],
                final_callback_mcp._call_tool("claim_readonly", {"page": page}),
            )
            self.assertEqual(
                {"ok": True, "status": "completed", "terminal": True},
                final_callback_mcp._call_tool(
                    "complete_readonly",
                    {"page": page, "result": catalog_result},
                ),
            )
            self.assertEqual(
                responses[3],
                final_callback_mcp._call_tool(
                    "submit_final_callback",
                    {
                        "final_callback_capability": final_callback_capability,
                        "final_answer": final_answer,
                    },
                ),
            )
            self.assertEqual(
                responses[4],
                final_callback_mcp._call_tool(
                    "finish_final_callback",
                    {"page": page, "wait_seconds": 30},
                ),
            )
            self.assertEqual(
                responses[5],
                final_callback_mcp._call_tool(
                    "fail_page",
                    {
                        "page": page,
                        "code": "responder_result_unknown",
                        "may_have_started": True,
                    },
                ),
            )

        self.assertEqual(
            call("claim-and-arm", ["--page", page]),
            invoke.call_args_list[0],
        )
        self.assertEqual(
            call("claim-readonly", ["--page", page]),
            invoke.call_args_list[1],
        )
        catalog_wire = json.dumps(
            catalog_result,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        self.assertEqual(
            call(
                "complete-readonly",
                ["--page", page],
                input_bytes=catalog_wire,
            ),
            invoke.call_args_list[2],
        )
        complete_argv = "\0".join(invoke.call_args_list[2].args[1])
        for content_marker in ("catalog_version", catalog_title, "selection_proof"):
            self.assertNotIn(content_marker, complete_argv)
        self.assertEqual(
            catalog_result,
            json.loads(invoke.call_args_list[2].kwargs["input_bytes"].decode("utf-8")),
        )
        self.assertEqual(
            call(
                "submit-final-callback",
                [],
                input_bytes=json.dumps(
                    {
                        "final_callback_capability": final_callback_capability,
                        "final_answer": final_answer,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8"),
            ),
            invoke.call_args_list[3],
        )
        self.assertNotIn(final_answer, invoke.call_args_list[3].args[1])
        self.assertNotIn(final_callback_capability, invoke.call_args_list[3].args[1])
        self.assertEqual(
            call(
                "finish-final-callback",
                ["--page", page, "--wait-seconds", "30"],
                timeout_seconds=36,
            ),
            invoke.call_args_list[4],
        )
        self.assertEqual(
            call(
                "fail-page",
                [
                    "--page",
                    page,
                    "--code",
                    "responder_result_unknown",
                    "--may-have-started",
                ],
            ),
            invoke.call_args_list[5],
        )

        invalid_arguments = (
            ("claim_and_arm", {"page": "short"}),
            ("claim_readonly", {"page": "short"}),
            (
                "submit_final_callback",
                {"final_callback_capability": "short", "final_answer": "answer"},
            ),
            (
                "submit_final_callback",
                {"final_callback_capability": final_callback_capability, "final_answer": "   "},
            ),
            (
                "finish_final_callback",
                {"page": page, "wait_seconds": 31},
            ),
            (
                "finish_final_callback",
                {"page": page, "wait_seconds": True},
            ),
            (
                "fail_page",
                {
                    "page": page,
                    "code": "contains space",
                    "may_have_started": False,
                },
            ),
        )
        for name, arguments in invalid_arguments:
            with self.subTest(name=name, arguments=arguments):
                with self.assertRaises(final_callback_mcp.FinalCallbackError):
                    final_callback_mcp._call_tool(name, arguments)

        invalid_readonly_results = (
            (
                "project_root",
                {
                    **catalog_result,
                    "projects": [
                        {**catalog_result["projects"][0], "root": "forbidden-root"}
                    ],
                },
            ),
            (
                "task_path",
                {
                    **catalog_result,
                    "tasks": [
                        {**catalog_result["tasks"][0], "path": "forbidden-path"}
                    ],
                },
            ),
            (
                "nonfinite_updated_at",
                {
                    **catalog_result,
                    "tasks": [
                        {**catalog_result["tasks"][0], "updated_at": float("nan")}
                    ],
                },
            ),
            (
                "inspection_catalog_field_mix",
                {**inspection_result, "tasks": []},
            ),
            (
                "selection_proof_must_not_be_echoed",
                {**inspection_result, "selection_proof": "not-a-proof"},
            ),
        )
        for case, result in invalid_readonly_results:
            with self.subTest(case=case), patch.object(
                final_callback_mcp,
                "_invoke_helper_command",
            ) as helper:
                with self.assertRaises(final_callback_mcp.FinalCallbackError):
                    final_callback_mcp._call_tool(
                        "complete_readonly",
                        {"page": page, "result": result},
                    )
                helper.assert_not_called()

        for reserved_code in (
            "beeper_claim_timeout",
            "beeper_load_assist_failed",
        ):
            with self.subTest(reserved_code=reserved_code), patch.object(
                final_callback_mcp,
                "_invoke_helper_command",
            ) as helper:
                with self.assertRaises(final_callback_mcp.FinalCallbackError):
                    final_callback_mcp._call_tool(
                        "fail_page",
                        {
                            "page": page,
                            "code": reserved_code,
                            "may_have_started": False,
                        },
                    )
                helper.assert_not_called()

    def test_final_callback_mcp_rejects_metadata_bearing_helper_output(self) -> None:
        leaked_claim = {
            "ok": True,
            "status": "claimed_armed",
            "responder_thread_id": "11111111-2222-3333-4444-555555555555",
            "responder_host_id": "responder-host",
            "prompt": "wrapped prompt",
            "page": "a" * 32,
        }
        leaked_terminal = {
            "ok": True,
            "status": "completed",
            "terminal": True,
            "final_callback_source": "final_callback",
        }
        leaked_submission = {
            "ok": True,
            "accepted": True,
            "state": "captured",
            "request_id": "b" * 32,
        }
        for validator, payload in (
            (final_callback_mcp._minimal_claim_result, leaked_claim),
            (final_callback_mcp._answer_free_terminal_result, leaked_terminal),
            (final_callback_mcp._answer_free_submission_result, leaked_submission),
        ):
            with self.subTest(validator=validator.__name__):
                with self.assertRaises(final_callback_mcp.FinalCallbackError):
                    validator(payload)

        page = "a" * 32
        read_claim = {
            "ok": True,
            "status": "claimed_readonly",
            "operation": "inspect_thread",
            "request": {
                "responder_thread_id": "11111111-2222-3333-4444-555555555555",
                "display_name": "",
                "catalog_snapshot_id": "c" * 32,
                "expected_project_id": "project-a",
                "expected_host_id": "local",
                "selection_proof": "d" * 64,
                "excluded_thread_ids": [
                    "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
                ],
                "operation_receipt": "e" * 32,
            },
        }
        invalid_read_claims = (
            ("extra_top_level_metadata", {**read_claim, "page": page}),
            (
                "invalid_selection_proof",
                {
                    **read_claim,
                    "request": {
                        **read_claim["request"],
                        "selection_proof": "not-a-proof",
                    },
                },
            ),
        )
        for case, helper_output in invalid_read_claims:
            with self.subTest(case=case), patch.object(
                final_callback_mcp,
                "_invoke_helper_command",
                return_value=helper_output,
            ) as helper:
                with self.assertRaises(final_callback_mcp.FinalCallbackError):
                    final_callback_mcp._call_tool(
                        "claim_readonly",
                        {"page": page},
                    )
                helper.assert_called_once_with(
                    "claim-readonly",
                    ["--page", page],
                )

    def test_final_callback_mcp_accepts_supported_non_uuid_responder_id(self) -> None:
        responder_thread_id = "thr_1234567890abcdefghijklmnop"
        payload = {
            "ok": True,
            "status": "claimed_armed",
            "responder_thread_id": responder_thread_id,
            "responder_host_id": "responder-host",
            "prompt": "wrapped prompt",
        }
        self.assertEqual(payload, final_callback_mcp._minimal_claim_result(payload))

    def test_helper_invocation_is_fixed_to_namespace(self) -> None:
        runtime = TEST_TEMP_ROOT / "bridge-runtime"
        helper = runtime / "beeper_queue_cli.py"
        completed = SimpleNamespace(returncode=0, stdout=b'{"ok":true}', stderr=b"")
        with (
            patch.object(
                final_callback_mcp,
                "_verified_runtime",
                return_value=(runtime, helper),
            ),
            patch.object(
                final_callback_mcp.subprocess,
                "run",
                return_value=completed,
            ) as run,
        ):
            self.assertEqual(
                {"ok": True},
                final_callback_mcp._invoke_helper_command(
                    "claim-and-arm",
                    ["--page", "a" * 32],
                ),
            )
        invocation = run.call_args.args[0]
        namespace_index = invocation.index("--queue-namespace")
        self.assertEqual(
            final_callback_mcp.QUEUE_NAMESPACE,
            invocation[namespace_index + 1],
        )
        self.assertEqual(
            "claim-and-arm",
            invocation[namespace_index + 2],
        )

    def test_final_callback_answer_crosses_helper_only_on_utf8_stdin(self) -> None:
        runtime = TEST_TEMP_ROOT / "bridge-runtime"
        helper = runtime / "beeper_queue_cli.py"
        final_answer = " 前导🚀\r\n第二行 🙂 "
        final_callback_capability = "c" * 32
        wire = json.dumps(
            {"final_callback_capability": final_callback_capability, "final_answer": final_answer},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        completed = SimpleNamespace(
            returncode=0,
            stdout=b'{"ok":true,"accepted":true,"state":"captured"}',
            stderr=b"",
        )
        with (
            patch.object(
                final_callback_mcp,
                "_verified_runtime",
                return_value=(runtime, helper),
            ),
            patch.object(
                final_callback_mcp.subprocess,
                "run",
                return_value=completed,
            ) as run,
        ):
            result = final_callback_mcp._invoke_helper_command(
                "submit-final-callback",
                [],
                input_bytes=wire,
            )
        self.assertTrue(result["accepted"])
        invocation = run.call_args.args[0]
        self.assertNotIn(final_answer, invocation)
        self.assertNotIn(final_callback_capability, invocation)
        self.assertEqual(wire, run.call_args.kwargs["input"])
        self.assertNotIn(final_answer.encode("utf-8"), completed.stdout)
        self.assertNotIn(final_callback_capability.encode("ascii"), completed.stdout)

    def test_bridge_plugin_bundles_responder_owned_mcp_final_callback(self) -> None:
        manifest = json.loads(
            (BRIDGE_PLUGIN / ".codex-plugin" / "plugin.json").read_text(
                encoding="utf-8"
            )
        )
        hooks_path = BRIDGE_PLUGIN / "hooks" / "hooks.json"
        mcp = json.loads(
            (BRIDGE_PLUGIN / ".mcp.json").read_text(encoding="utf-8")
        )
        server = (
            BRIDGE_PLUGIN / "scripts" / "final_callback_mcp_server.py"
        ).read_text(encoding="utf-8")
        beeper = (
            BRIDGE_PLUGIN / "assets" / "beeper-task.md"
        ).read_text(encoding="utf-8")

        self.assertFalse((BRIDGE_PLUGIN / "HANDOFF.md").exists())
        self.assertEqual("feishu-codex-bridge", manifest["name"])
        self.assertEqual("./.mcp.json", manifest["mcpServers"])
        self.assertFalse(hooks_path.exists())
        self.assertIn("feishu_final_callback", mcp["mcpServers"])
        for marker in (
            '"name": "submit_final_callback"',
            '"name": "finish_final_callback"',
            "ensure_ascii=False",
            "input_bytes=wire",
            '"submit-final-callback"',
            "runtime-manifest.json",
        ):
            self.assertIn(marker, server)
        self.assertNotIn("bind_user_prompt", server)
        self.assertNotIn("capture_stop_final", server)
        self.assertNotIn("last_assistant_message", server)
        self.assertNotIn("transcript_path", server)
        self.assertNotIn("read_thread", server)
        self.assertIn("returns `terminal=true` with status `completed` or `failed`", beeper)
        self.assertIn("not receive or verify final-source metadata", beeper)
        self.assertNotIn("reports the `final_callback_source=final_callback`", beeper)

    def test_historical_routes_are_absent_and_zero_allowlist_is_enforced(self) -> None:
        rules = FINAL_CALLBACK_RULES.read_text(encoding="utf-8")
        dispatcher = BRIDGE_DISPATCHER.read_text(encoding="utf-8")
        self.assertIn("HISTORICAL_BEEPER_RULES_TOMBSTONE_V1", rules)
        self.assertIn("intentionally defines zero `prefix_rule` entries", rules)
        self.assertNotIn("prefix_rule(", rules)
        self.assertNotIn('decision = "allow"', rules)
        for marker in (
            "status = 'upgrade_required'",
            "required_runtime_capability = 'p0_exact_final_callback'",
            "The installed Bridge runtime predates P0 exact final-callback registration",
        ):
            self.assertIn(marker, dispatcher)
        subcommands = next(
            action.choices
            for action in build_beeper_queue_cli_parser()._actions
            if action.dest == "command"
        )
        self.assertEqual(
            {
                "status",
                "final-callback-register",
                "final-callback-unregister",
                "final-callback-registry-status",
                "register",
                "registration",
                "claim-and-arm",
                "claim-readonly",
                "complete-readonly",
                "finish-readonly",
                "tombstone-thread",
                "list-thread-tombstones",
                "submit-final-callback",
                "finish-final-callback",
                "fail-page",
            },
            set(subcommands),
        )
        for operational_doc in (
            README_DOC,
            USAGE_DOC,
            SKILL_DOC,
            PERMISSIONS_HOOKS_DOC,
            COMMON_CHAT_PERMISSIONS_DOC,
            COMMAND_UX_DOC,
            ARCHITECTURE_DOC,
            AGENTS_FRAGMENT,
        ):
            text = operational_doc.read_text(encoding="utf-8")
            self.assertNotRegex(
                text,
                r"```[^\r\n]*\r?\n(?:(?!```).)*(?:beeper_queue_cli\.py|desktop-beeper-[\w-]+\.md)(?:(?!```).)*```",
            )

        skill = SKILL_DOC.read_text(encoding="utf-8")
        permissions = PERMISSIONS_HOOKS_DOC.read_text(encoding="utf-8")
        usage = USAGE_DOC.read_text(encoding="utf-8")
        skill_section_match = re.search(
            r"## 可见 Hook 审核入口(.*?)(?:\r?\n## |\Z)",
            skill,
            flags=re.DOTALL,
        )
        permissions_section_match = re.search(
            r"## 8\. Hook file and trust(.*?)(?:\r?\n## |\Z)",
            permissions,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(skill_section_match)
        self.assertIsNotNone(permissions_section_match)
        for section_match in (skill_section_match, permissions_section_match):
            cmd_blocks = re.findall(
                r"```cmd\r?\n(.*?)```",
                section_match.group(1),
                flags=re.DOTALL,
            )
            self.assertEqual(2, len(cmd_blocks))
            discovery_block, launch_block = cmd_blocks
            self.assertEqual(
                ['cd /d "<project-root>"', "where codex.cmd"],
                discovery_block.splitlines(),
            )
            self.assertEqual(
                [
                    'set "CODEX_BRIDGE_CHILD=1"',
                    "codex.cmd",
                    'set "CODEX_BRIDGE_CHILD="',
                ],
                launch_block.splitlines(),
            )
            for cmd_block in cmd_blocks:
                self.assertNotRegex(cmd_block, r"(?i)(?:[a-z]:\\|\\\\)")
                self.assertNotIn("codex.cmd /hooks", cmd_block)
        for text in (skill, permissions, usage):
            self.assertIn("Codex Desktop", text)
            self.assertIn("`/hooks`", text)
            self.assertIn("Windows CMD", text)
            self.assertIn("Trust all", text)
        self.assertIn("首选 Codex Desktop 的“设置 → 钩子”", skill)
        self.assertIn("Windows CMD 作为备选", skill)
        self.assertIn("Codex Desktop Settings >", permissions)
        self.assertIn("Use Windows CMD only as a fallback", permissions)
        self.assertIn("首选 Codex Desktop“设置 → 钩子”", usage)
        self.assertIn("在交互式 Codex CLI 内输入 `/hooks`", skill)
        self.assertRegex(permissions, r"`/hooks`\s+is an interactive CLI command")

    def test_current_send_is_final_callback_sealed_and_unsupported_steer_is_rejected(self) -> None:
        beeper = BEEPER_SOURCE.read_text(encoding="utf-8")
        client = BEEPER_CLIENT_SOURCE.read_text(encoding="utf-8")
        for marker in (
            "_seal_current_final_callback",
            "state='completing'",
            "transport_mode='final_callback'",
            "Final Callback capability",
            "Beeper sends cannot use legacy native staging",
            "send completion answer failed captured Responder integrity",
            "WHEN state='completing' AND resolution_source='final_callback'",
            "_reject_unsupported_send_mode",
            "Desktop Beeper steer is unsupported; no responder action was queued",
        ):
            self.assertIn(marker, beeper)
        for marker in (
            'expected_operation == "send_message_to_thread"',
            'response.get("final_callback_source") != "final_callback"',
            "Desktop Beeper send completion has no Final Callback source",
            "Desktop Beeper terminal operation does not match its request",
            "no fenced in-flight steer lane; no responder send was submitted",
        ):
            self.assertIn(marker, client)

if __name__ == "__main__":
    unittest.main()
