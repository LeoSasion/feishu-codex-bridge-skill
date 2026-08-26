from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP_ROOT = Path(os.environ.get("FEISHU_BRIDGE_TEST_TMP", tempfile.gettempdir()))
if "FEISHU_BRIDGE_TEST_TMP" not in os.environ:
    TEST_TEMP_ROOT = TEST_TEMP_ROOT / "feishu-codex-bridge-tests"
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from bridge_core.desktop_router import (  # noqa: E402
    DesktopRouterQueue,
    RouterProtocolError,
)
from router_queue import (  # noqa: E402
    MAX_STRUCTURED_RESULT_CHARS,
    _emit,
    _read_staged_result,
    _runtime_settings,
)


ROUTER_THREAD_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
OTHER_ROUTER_ID = "ffffffff-1111-2222-3333-444444444444"
TARGET_THREAD_ID = "11111111-2222-3333-4444-555555555555"
TARGET_TURN_ID = "77777777-8888-9999-aaaa-bbbbbbbbbbbb"
OTHER_TURN_ID = "cccccccc-dddd-eeee-ffff-000000000000"


class DesktopRouterQueueTests(unittest.TestCase):
    def reserve(self, queue: DesktopRouterQueue) -> dict:
        wake = queue.sentinel_probe(ROUTER_THREAD_ID, "host-one")
        self.assertTrue(wake["should_wake"], wake)
        return wake

    def claim(self, queue: DesktopRouterQueue, wake: dict):
        return queue.claim(
            ROUTER_THREAD_ID,
            "host-one",
            wake_id=wake["wake_id"],
            fence_token=wake["fence_token"],
        )

    def test_queue_helper_rejects_ambiguous_environment(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            runtime_dir = Path(temporary)
            env_path = runtime_dir / "bridge.env"
            env_path.write_text(
                "CODEX_BRIDGE_ACCESS_MODE=locked\n"
                "CODEX_BRIDGE_ACCESS_MODE=compat\n",
                encoding="utf-8",
            )
            with self.assertRaises(RouterProtocolError):
                _runtime_settings(runtime_dir)

            env_path.write_text(
                "CODEX_BRIDGE_GATEWAY_SCHEDULER_TTL=999999\n",
                encoding="utf-8",
            )
            with self.assertRaises(RouterProtocolError):
                _runtime_settings(runtime_dir)

            env_path.write_text(
                "CODEX_BRIDGE_ROUTER_HEARTBEAT_TTL=not-an-integer\n",
                encoding="utf-8",
            )
            with self.assertRaises(RouterProtocolError):
                _runtime_settings(runtime_dir)

            env_path.write_text(
                "CODEX_BRIDGE_ALLOW_PROJECT_CREATE=typo\n",
                encoding="utf-8",
            )
            with self.assertRaises(RouterProtocolError):
                _runtime_settings(runtime_dir)

    def test_queue_helper_stdout_is_ascii_json_with_lossless_unicode_roundtrip(self) -> None:
        expected = {"ok": True, "request": {"payload": {"prompt": "你好，你是谁？🙂"}}}
        output = io.StringIO()
        with redirect_stdout(output):
            _emit(expected)

        wire = output.getvalue().strip()
        self.assertTrue(wire.isascii())
        self.assertIn(r"\u4f60\u597d", wire)
        self.assertIn(r"\ud83d\ude42", wire)
        self.assertEqual(expected, json.loads(wire))

    def test_structured_result_reader_is_strict_and_never_truncates(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            path = Path(temporary) / "result.json"
            expected = {"catalog_version": 1, "projects": [], "tasks": []}
            path.write_text(json.dumps(expected), encoding="utf-8")
            self.assertEqual(expected, _read_staged_result(path))

            path.write_text("[]", encoding="utf-8")
            with self.assertRaises(RouterProtocolError):
                _read_staged_result(path)

            path.write_text("{" + (" " * MAX_STRUCTURED_RESULT_CHARS), encoding="utf-8")
            with self.assertRaises(RouterProtocolError):
                _read_staged_result(path)

    def test_final_return_hook_roundtrip_keeps_last_same_turn_stop_unicode(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue = DesktopRouterQueue(Path(temporary))
            queue.register(ROUTER_THREAD_ID, "host-one")
            prompt = "你好，你是谁？——请保留中文标点与 emoji 🙂"
            request_id = queue.submit(
                "send_message_to_thread",
                {"target_thread_id": TARGET_THREAD_ID, "prompt": prompt},
                idempotency_key="event-final-return-unicode",
            )
            wake = self.reserve(queue)
            self.claim(queue, wake)

            armed = queue.arm_final_return(
                request_id,
                wake["fence_token"],
                TARGET_THREAD_ID,
                now=100,
            )
            self.assertEqual("armed", armed["state"])
            self.assertEqual(
                {"accepted": True, "state": "bound"},
                queue.bind_final_return_prompt(
                    TARGET_THREAD_ID,
                    TARGET_TURN_ID,
                    prompt,
                    now=101,
                ),
            )

            provisional = "第一版回答：处理中……🧭"
            final = "最终回答：你好！我是 Codex。✅"
            self.assertEqual(
                {"accepted": True, "state": "captured"},
                queue.capture_final_return(
                    TARGET_THREAD_ID,
                    TARGET_TURN_ID,
                    provisional,
                    stop_hook_active=False,
                    now=102,
                ),
            )
            self.assertEqual(
                {"accepted": True, "state": "captured"},
                queue.capture_final_return(
                    TARGET_THREAD_ID,
                    TARGET_TURN_ID,
                    final,
                    stop_hook_active=True,
                    now=103,
                ),
            )
            # A duplicate delivery of the same final remains idempotent.
            self.assertEqual(
                {"accepted": True, "state": "captured"},
                queue.capture_final_return(
                    TARGET_THREAD_ID,
                    TARGET_TURN_ID,
                    final,
                    stop_hook_active=True,
                    now=104,
                ),
            )
            self.assertEqual(
                {"available": True, "state": "captured"},
                queue.final_return_status(
                    request_id,
                    wake["fence_token"],
                    TARGET_THREAD_ID,
                    TARGET_TURN_ID,
                ),
            )
            self.assertEqual(
                final,
                queue.stage_path(request_id, wake["fence_token"]).read_text(
                    encoding="utf-8"
                ),
            )
            queue.complete(
                request_id,
                {
                    "thread_id": TARGET_THREAD_ID,
                    "host_id": "host-one",
                    "turn_id": TARGET_TURN_ID,
                    "cursor": "cursor-final-return",
                    "text": final,
                    "archived_thread_ids": [],
                },
                fence_token=wake["fence_token"],
            )
            with queue._wake_session() as connection:
                terminal = connection.execute(
                    "SELECT state FROM final_return_receipts WHERE request_id=?",
                    (request_id,),
                ).fetchone()
            self.assertEqual("completed", terminal["state"])
            self.assertEqual(
                {"accepted": False, "state": "ignored"},
                queue.capture_final_return(
                    TARGET_THREAD_ID,
                    TARGET_TURN_ID,
                    "late answer must not replace the delivered final",
                    stop_hook_active=True,
                    now=105,
                ),
            )

    def test_final_return_hooks_ignore_unarmed_or_mismatched_turns(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue = DesktopRouterQueue(Path(temporary))
            queue.register(ROUTER_THREAD_ID, "host-one")
            prompt = "exact prompt"
            request_id = queue.submit(
                "send_message_to_thread",
                {"target_thread_id": TARGET_THREAD_ID, "prompt": prompt},
                idempotency_key="event-final-return-identity",
            )
            wake = self.reserve(queue)
            self.claim(queue, wake)
            queue.arm_final_return(
                request_id,
                wake["fence_token"],
                TARGET_THREAD_ID,
                now=200,
            )

            self.assertEqual(
                {"accepted": False, "state": "ignored"},
                queue.bind_final_return_prompt(
                    OTHER_ROUTER_ID,
                    TARGET_TURN_ID,
                    prompt,
                    now=201,
                ),
            )
            self.assertEqual(
                {"accepted": False, "state": "ignored"},
                queue.bind_final_return_prompt(
                    TARGET_THREAD_ID,
                    TARGET_TURN_ID,
                    "different prompt",
                    now=201,
                ),
            )
            queue.bind_final_return_prompt(
                TARGET_THREAD_ID,
                TARGET_TURN_ID,
                prompt,
                now=202,
            )
            self.assertEqual(
                {"accepted": False, "state": "ignored"},
                queue.capture_final_return(
                    TARGET_THREAD_ID,
                    OTHER_TURN_ID,
                    "wrong turn answer",
                    stop_hook_active=False,
                    now=203,
                ),
            )
            self.assertEqual(
                {"available": False, "state": "bound"},
                queue.final_return_status(
                    request_id,
                    wake["fence_token"],
                    TARGET_THREAD_ID,
                    TARGET_TURN_ID,
                ),
            )
            with self.assertRaises(RouterProtocolError):
                queue.resolve_final_return_native(
                    request_id,
                    wake["fence_token"],
                    TARGET_THREAD_ID,
                    OTHER_TURN_ID,
                    now=204,
                )
            self.assertFalse(queue.stage_path(request_id, wake["fence_token"]).exists())

    def test_final_return_hook_accepts_only_registered_gateway_delegation(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue = DesktopRouterQueue(Path(temporary))
            queue.register(ROUTER_THREAD_ID, "host-one")
            prompt = "你好，你是谁？——保留 <标签>、& 与 emoji 🙂"
            request_id = queue.submit(
                "send_message_to_thread",
                {"target_thread_id": TARGET_THREAD_ID, "prompt": prompt},
                idempotency_key="event-final-return-delegation",
            )
            wake = self.reserve(queue)
            self.claim(queue, wake)
            queue.arm_final_return(
                request_id,
                wake["fence_token"],
                TARGET_THREAD_ID,
                now=250,
            )

            wrong_source = (
                "<codex_delegation>\n"
                f"  <source_thread_id>{OTHER_ROUTER_ID}</source_thread_id>\n"
                f"  <input>{prompt}</input>\n"
                "</codex_delegation>"
            )
            self.assertEqual(
                {"accepted": False, "state": "ignored"},
                queue.bind_final_return_prompt(
                    TARGET_THREAD_ID,
                    TARGET_TURN_ID,
                    wrong_source,
                    now=251,
                ),
            )
            self.assertEqual(
                {
                    "available": False,
                    "state": "armed",
                    "prompt_hook_seen": True,
                    "prompt_hook_turn_matches": True,
                    "prompt_match_mode": "none",
                    "prompt_hook_rejection": "gateway_mismatch",
                },
                queue.final_return_status(
                    request_id,
                    wake["fence_token"],
                    TARGET_THREAD_ID,
                    TARGET_TURN_ID,
                ),
            )

            delegated = (
                "<codex_delegation>\r\n"
                f"  <source_thread_id>{ROUTER_THREAD_ID}</source_thread_id>\r\n"
                f"  <input>{prompt}</input>\r\n"
                "</codex_delegation>"
            )
            self.assertEqual(
                {"accepted": True, "state": "bound"},
                queue.bind_final_return_prompt(
                    TARGET_THREAD_ID,
                    TARGET_TURN_ID,
                    delegated,
                    now=252,
                ),
            )
            self.assertEqual(
                {"accepted": True, "state": "captured"},
                queue.capture_final_return(
                    TARGET_THREAD_ID,
                    TARGET_TURN_ID,
                    "最终回答：我是 Codex。✅",
                    now=253,
                ),
            )
            self.assertEqual(
                {"available": True, "state": "captured"},
                queue.final_return_status(
                    request_id,
                    wake["fence_token"],
                    TARGET_THREAD_ID,
                    TARGET_TURN_ID,
                ),
            )

    def test_native_final_return_fences_late_hook_capture(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue = DesktopRouterQueue(Path(temporary))
            queue.register(ROUTER_THREAD_ID, "host-one")
            prompt = "native exact final"
            request_id = queue.submit(
                "send_message_to_thread",
                {"target_thread_id": TARGET_THREAD_ID, "prompt": prompt},
                idempotency_key="event-final-return-native",
            )
            wake = self.reserve(queue)
            self.claim(queue, wake)
            queue.arm_final_return(
                request_id,
                wake["fence_token"],
                TARGET_THREAD_ID,
                now=300,
            )
            self.assertEqual(
                {"resolved": True, "state": "native"},
                queue.resolve_final_return_native(
                    request_id,
                    wake["fence_token"],
                    TARGET_THREAD_ID,
                    TARGET_TURN_ID,
                    now=301,
                ),
            )
            self.assertEqual(
                {"accepted": False, "state": "ignored"},
                queue.bind_final_return_prompt(
                    TARGET_THREAD_ID,
                    TARGET_TURN_ID,
                    prompt,
                    now=302,
                ),
            )
            self.assertEqual(
                {"accepted": False, "state": "ignored"},
                queue.capture_final_return(
                    TARGET_THREAD_ID,
                    TARGET_TURN_ID,
                    "late Hook text",
                    stop_hook_active=False,
                    now=303,
                ),
            )
            self.assertEqual(
                {"available": False, "state": "native"},
                queue.final_return_status(
                    request_id,
                    wake["fence_token"],
                    TARGET_THREAD_ID,
                    TARGET_TURN_ID,
                ),
            )

    def test_final_return_arm_expires_only_unbound_stale_owner(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue = DesktopRouterQueue(Path(temporary))
            queue.register(ROUTER_THREAD_ID, "host-one")
            request_id = queue.submit(
                "send_message_to_thread",
                {"target_thread_id": TARGET_THREAD_ID, "prompt": "fresh"},
                idempotency_key="event-final-return-expired-arm",
            )
            wake = self.reserve(queue)
            self.claim(queue, wake)
            with queue._wake_session() as connection:
                connection.execute(
                    """
                    INSERT INTO final_return_receipts(
                        request_id, fence_token, thread_id, prompt_sha256, state,
                        created_at, updated_at, expires_at
                    ) VALUES(?, ?, ?, ?, 'armed', ?, ?, ?)
                    """,
                    (
                        "a" * 32,
                        "b" * 32,
                        TARGET_THREAD_ID,
                        "c" * 64,
                        1,
                        1,
                        2,
                    ),
                )

            self.assertEqual(
                "armed",
                queue.arm_final_return(
                    request_id,
                    wake["fence_token"],
                    TARGET_THREAD_ID,
                    now=1000,
                )["state"],
            )
            with queue._wake_session() as connection:
                stale = connection.execute(
                    "SELECT state FROM final_return_receipts WHERE request_id=?",
                    ("a" * 32,),
                ).fetchone()
            self.assertEqual("expired", stale["state"])

    def test_catalog_completion_requires_exact_structured_contract(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue = DesktopRouterQueue(Path(temporary))
            queue.register(ROUTER_THREAD_ID, "host-one")
            request_id = queue.submit(
                "list_task_catalog",
                {"visible_thread_ids": [], "include_archived": False, "limit": 100},
                idempotency_key="event-catalog-contract",
            )
            wake = self.reserve(queue)
            self.claim(queue, wake)
            with self.assertRaises(RouterProtocolError):
                queue.complete(
                    request_id,
                    {"projects": [], "tasks": []},
                    fence_token=wake["fence_token"],
                )
            queue.complete(
                request_id,
                {"catalog_version": 1, "projects": [], "tasks": []},
                fence_token=wake["fence_token"],
            )
            self.assertEqual("completed", queue.response(request_id)["status"])

    def test_registration_and_heartbeat_are_owner_locked(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue = DesktopRouterQueue(Path(temporary), heartbeat_ttl_seconds=30)
            queue.register(ROUTER_THREAD_ID, "host-one")
            self.assertTrue(queue.status().ready)
            with self.assertRaises(RouterProtocolError):
                queue.heartbeat(OTHER_ROUTER_ID, "host-two")
            with self.assertRaises(RouterProtocolError):
                queue.register(OTHER_ROUTER_ID, "host-two")

    def test_wake_sessions_close_database_handles(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue = DesktopRouterQueue(Path(temporary))
            opened_connections: list[sqlite3.Connection] = []
            open_connection = queue._wake_connection

            def tracked_connection() -> sqlite3.Connection:
                connection = open_connection()
                opened_connections.append(connection)
                return connection

            with patch.object(queue, "_wake_connection", side_effect=tracked_connection):
                queue.status()

            self.assertGreaterEqual(len(opened_connections), 1)
            for connection in opened_connections:
                with self.assertRaises(sqlite3.ProgrammingError):
                    connection.execute("SELECT 1")

    def test_submit_is_idempotent_for_same_operation_and_key(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue = DesktopRouterQueue(Path(temporary))
            payload = {"target_thread_id": TARGET_THREAD_ID, "prompt": "hello"}
            first = queue.submit(
                "send_message_to_thread",
                payload,
                idempotency_key="event-1",
            )
            second = queue.submit(
                "send_message_to_thread",
                payload,
                idempotency_key="event-1",
            )
            self.assertEqual(first, second)
            self.assertEqual(1, queue.status().pending)
            with self.assertRaises(RouterProtocolError):
                queue.submit(
                    "send_message_to_thread",
                    {**payload, "prompt": "different"},
                    idempotency_key="event-1",
                )

    def test_concurrent_conflicting_producers_publish_one_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            root = Path(temporary)
            first_queue = DesktopRouterQueue(root)
            second_queue = DesktopRouterQueue(root)
            first_queue.register(ROUTER_THREAD_ID, "host-one")
            second_at_publish = threading.Event()
            allow_second_publish = threading.Event()
            from bridge_core import desktop_router

            original_publish = desktop_router._atomic_write_json_exclusive

            def synchronized_publish(path, payload):
                if (
                    path.parent.name == "pending"
                    and payload.get("payload", {}).get("prompt") == "second"
                ):
                    second_at_publish.set()
                    self.assertTrue(allow_second_publish.wait(timeout=10))
                return original_publish(path, payload)

            payloads = (
                {"target_thread_id": TARGET_THREAD_ID, "prompt": "first"},
                {"target_thread_id": TARGET_THREAD_ID, "prompt": "second"},
            )

            def submit(queue, payload):
                try:
                    return queue.submit(
                        "send_message_to_thread",
                        payload,
                        idempotency_key="event-concurrent-conflict",
                    )
                except RouterProtocolError as exc:
                    return exc

            with patch(
                "bridge_core.desktop_router._atomic_write_json_exclusive",
                side_effect=synchronized_publish,
            ), ThreadPoolExecutor(max_workers=2) as pool:
                second_future = pool.submit(submit, second_queue, payloads[1])
                self.assertTrue(second_at_publish.wait(timeout=10))
                first_result = submit(first_queue, payloads[0])
                wake = self.reserve(first_queue)
                claimed = self.claim(first_queue, wake)
                allow_second_publish.set()
                second_result = second_future.result(timeout=10)

            self.assertIsInstance(first_result, str)
            self.assertIsInstance(second_result, RouterProtocolError)
            self.assertEqual(first_result, claimed["request_id"])
            self.assertEqual(0, first_queue.status().pending)
            self.assertEqual(1, first_queue.status().claimed)
            self.assertEqual(1, len(list(first_queue.pending_dir.glob("*.json"))))
            self.assertEqual(1, len(list(first_queue.claimed_dir.glob("*.json"))))
            pending = next(first_queue.pending_dir.glob("*.json"))
            published = json.loads(pending.read_text(encoding="utf-8"))
            self.assertEqual(payloads[0], published["payload"])

    def test_identical_producer_overlap_cannot_republish_claimed_request(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            root = Path(temporary)
            first_queue = DesktopRouterQueue(root)
            second_queue = DesktopRouterQueue(root)
            first_queue.register(ROUTER_THREAD_ID, "host-one")
            second_at_publish = threading.Event()
            allow_second_publish = threading.Event()
            from bridge_core import desktop_router

            original_publish = desktop_router._atomic_write_json_exclusive
            payload = {"target_thread_id": TARGET_THREAD_ID, "prompt": "same"}

            def synchronized_publish(path, body):
                if (
                    path.parent.name == "pending"
                    and threading.current_thread().name == "delayed-producer"
                ):
                    second_at_publish.set()
                    self.assertTrue(allow_second_publish.wait(timeout=10))
                return original_publish(path, body)

            def delayed_submit():
                return second_queue.submit(
                    "send_message_to_thread",
                    payload,
                    idempotency_key="event-concurrent-identical",
                )

            with patch(
                "bridge_core.desktop_router._atomic_write_json_exclusive",
                side_effect=synchronized_publish,
            ):
                def run_delayed_submit():
                    try:
                        delayed_results.append(delayed_submit())
                    except BaseException as exc:  # surfaced on the test thread below
                        delayed_errors.append(exc)

                producer = threading.Thread(
                    target=run_delayed_submit,
                    name="delayed-producer",
                    daemon=True,
                )
                delayed_results = []
                delayed_errors = []
                producer.start()
                self.assertTrue(second_at_publish.wait(timeout=10))
                first_result = first_queue.submit(
                    "send_message_to_thread",
                    payload,
                    idempotency_key="event-concurrent-identical",
                )
                wake = self.reserve(first_queue)
                claimed = self.claim(first_queue, wake)
                allow_second_publish.set()
                producer.join(timeout=10)
                self.assertFalse(producer.is_alive())

            self.assertEqual([], delayed_errors)
            self.assertEqual([first_result], delayed_results)
            self.assertEqual(first_result, claimed["request_id"])
            self.assertIsNone(self.claim(first_queue, wake))
            self.assertEqual(1, len(list(first_queue.pending_dir.glob("*.json"))))
            self.assertEqual(1, len(list(first_queue.claimed_dir.glob("*.json"))))
            self.assertEqual(0, first_queue.status().pending)
            self.assertEqual(1, first_queue.status().claimed)

    def test_wake_database_lock_preserves_pending_and_reconciles_once(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue = DesktopRouterQueue(Path(temporary))
            queue.register(ROUTER_THREAD_ID, "host-one")
            with patch.object(
                queue,
                "_record_pending_request",
                side_effect=sqlite3.OperationalError("database is locked"),
            ) as record_pending_mock:
                request_id = queue.submit(
                    "send_message_to_thread",
                    {"target_thread_id": TARGET_THREAD_ID, "prompt": "durable"},
                    idempotency_key="event-sqlite-lock",
                )
            record_pending_mock.assert_called_once()

            self.assertTrue(queue._path(queue.pending_dir, request_id).exists())
            wake = self.reserve(queue)
            request = self.claim(queue, wake)
            self.assertEqual(request_id, request["request_id"])
            self.assertEqual(0, queue.status().pending)
            self.assertEqual(1, wake["wake_generation"])
            with queue._wake_session() as connection:
                rows = connection.execute(
                    "SELECT request_id, generation FROM wake_requests"
                ).fetchall()
            self.assertEqual([(request_id, 1)], [(row[0], row[1]) for row in rows])

    def test_explicit_safe_failure_advances_one_retry_generation(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue = DesktopRouterQueue(Path(temporary))
            queue.register(ROUTER_THREAD_ID, "host-one")
            payload = {"target_thread_id": TARGET_THREAD_ID, "prompt": "hello"}
            first = queue.submit(
                "send_message_to_thread",
                payload,
                idempotency_key="event-safe-retry",
            )
            wake = self.reserve(queue)
            request = self.claim(queue, wake)
            self.assertEqual(first, request["request_id"])
            queue.fail(
                first,
                code="temporary_target_gate",
                message="no target action started",
                retryable=True,
                may_have_started=False,
                fence_token=wake["fence_token"],
            )

            second = queue.submit(
                "send_message_to_thread",
                payload,
                idempotency_key="event-safe-retry",
            )
            self.assertNotEqual(first, second)
            self.assertEqual(
                second,
                queue.submit(
                    "send_message_to_thread",
                    payload,
                    idempotency_key="event-safe-retry",
                ),
            )
            self.assertEqual(1, queue.status().pending)

    def test_target_lifecycle_failure_never_advances_retry_generation(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue = DesktopRouterQueue(Path(temporary))
            queue.register(ROUTER_THREAD_ID, "host-one")
            payload = {"target_thread_id": TARGET_THREAD_ID, "prompt": "hello"}
            first = queue.submit(
                "send_message_to_thread",
                payload,
                idempotency_key="event-dead-target",
            )
            wake = self.reserve(queue)
            request = self.claim(queue, wake)
            self.assertEqual(first, request["request_id"])
            queue.fail(
                first,
                code="target_not_found",
                message="target ended before delivery",
                retryable=True,
                may_have_started=False,
                fence_token=wake["fence_token"],
            )

            self.assertEqual(
                first,
                queue.submit(
                    "send_message_to_thread",
                    payload,
                    idempotency_key="event-dead-target",
                ),
            )

    def test_retry_generation_requires_explicit_json_booleans(self) -> None:
        safe = {
            "status": "failed",
            "error": {
                "code": "temporary_target_gate",
                "retryable": True,
                "may_have_started": False,
            },
        }
        self.assertTrue(DesktopRouterQueue._response_allows_retry(safe))
        for error in (
            {"retryable": True, "may_have_started": False},
            {"code": "", "retryable": True, "may_have_started": False},
            {"retryable": "true", "may_have_started": False},
            {"retryable": True, "may_have_started": "false"},
            {
                "code": "target_needs_attention",
                "retryable": True,
                "may_have_started": False,
            },
            {
                "code": "target_tool_unavailable",
                "retryable": True,
                "may_have_started": False,
            },
            {
                "code": "project_not_registered",
                "retryable": True,
                "may_have_started": False,
            },
        ):
            with self.subTest(error=error):
                self.assertFalse(
                    DesktopRouterQueue._response_allows_retry(
                        {"status": "failed", "error": error}
                    )
                )

        malformed = {
            "schema_version": 4,
            "request_id": "a" * 32,
            "operation": "send_message_to_thread",
            "fingerprint": "f" * 64,
            "status": "failed",
            "error": {
                "code": 7,
                "retryable": True,
                "may_have_started": False,
            },
        }
        compacted = DesktopRouterQueue._compacted_terminal_receipt(malformed)
        self.assertEqual("target_result_unknown", compacted["error"]["code"])
        self.assertFalse(DesktopRouterQueue._response_allows_retry(compacted))

    def test_claim_and_completion_keep_authoritative_result(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue = DesktopRouterQueue(Path(temporary))
            queue.register(ROUTER_THREAD_ID, "host-one")
            request_id = queue.submit(
                "inspect_thread",
                {"target_thread_id": TARGET_THREAD_ID},
                idempotency_key="bind-1",
            )
            wake = self.reserve(queue)
            request = self.claim(queue, wake)
            self.assertEqual(request_id, request["request_id"])
            self.assertTrue(queue.was_claimed(request_id))
            queue.complete(
                request_id,
                {"thread_id": TARGET_THREAD_ID, "host_id": "host-target"},
                fence_token=wake["fence_token"],
            )
            response = queue.response(request_id)
            self.assertEqual("completed", response["status"])
            self.assertEqual(TARGET_THREAD_ID, response["result"]["thread_id"])
            self.assertEqual(0, queue.status().claimed)
            self.assertTrue(response["fingerprint"])
            self.assertEqual(
                request_id,
                queue.submit(
                    "inspect_thread",
                    {"target_thread_id": TARGET_THREAD_ID},
                    idempotency_key="bind-1",
                ),
            )
            with self.assertRaises(RouterProtocolError):
                queue.submit(
                    "inspect_thread",
                    {"target_thread_id": OTHER_ROUTER_ID},
                    idempotency_key="bind-1",
                )

            queue.fail(
                request_id,
                code="late_failure",
                message="must not replace success",
                retryable=False,
                may_have_started=True,
            )
            self.assertEqual("completed", queue.response(request_id)["status"])

    def test_failed_response_records_retry_and_uncertainty_separately(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue = DesktopRouterQueue(Path(temporary))
            queue.register(ROUTER_THREAD_ID)
            request_id = queue.submit(
                "compact_thread",
                {"target_thread_id": TARGET_THREAD_ID, "command": "/compact"},
                idempotency_key="compact-1",
            )
            wake = self.reserve(queue)
            self.claim(queue, wake)
            queue.fail(
                request_id,
                code="target_result_unknown",
                message="target may have started",
                retryable=False,
                may_have_started=True,
                fence_token=wake["fence_token"],
            )
            error = queue.response(request_id)["error"]
            self.assertFalse(error["retryable"])
            self.assertTrue(error["may_have_started"])

    def test_read_only_failure_rejects_may_have_started_flag(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue = DesktopRouterQueue(Path(temporary))
            queue.register(ROUTER_THREAD_ID)
            request_id = queue.submit(
                "inspect_thread",
                {"target_thread_id": TARGET_THREAD_ID},
                idempotency_key="bind-read-result-shape",
            )
            wake = self.reserve(queue)
            self.claim(queue, wake)

            with self.assertRaises(RouterProtocolError):
                queue.fail(
                    request_id,
                    code="target_result_unknown",
                    message="read result could not be normalized",
                    retryable=False,
                    may_have_started=True,
                    fence_token=wake["fence_token"],
                )
            self.assertIsNone(queue.response(request_id))

            queue.fail(
                request_id,
                code="invalid_gateway_result",
                message="read result could not be normalized",
                retryable=False,
                may_have_started=False,
                fence_token=wake["fence_token"],
            )
            error = queue.response(request_id)["error"]
            self.assertEqual("invalid_gateway_result", error["code"])
            self.assertFalse(error["retryable"])
            self.assertFalse(error["may_have_started"])

    def test_stale_claim_fails_closed_instead_of_replaying(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue = DesktopRouterQueue(
                Path(temporary),
                heartbeat_ttl_seconds=15,
                claim_ttl_seconds=60,
            )
            queue.register(ROUTER_THREAD_ID)
            request_id = queue.submit(
                "send_message_to_thread",
                {"target_thread_id": TARGET_THREAD_ID, "prompt": "do this once"},
                idempotency_key="event-once",
            )
            wake = self.reserve(queue)
            request = self.claim(queue, wake)
            expired = queue.expire_stale_claims(now=float(request["claimed_at"]) + 61)

            self.assertEqual(1, expired)
            error = queue.response(request_id)["error"]
            self.assertEqual("router_claim_expired", error["code"])
            self.assertFalse(error["retryable"])
            self.assertTrue(error["may_have_started"])
            self.assertIsNone(
                queue.claim(
                    ROUTER_THREAD_ID,
                    "host-one",
                    wake_id=wake["wake_id"],
                    fence_token=wake["fence_token"],
                )
            )

    def test_stale_read_only_claim_advances_retry_generation(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue = DesktopRouterQueue(
                Path(temporary),
                heartbeat_ttl_seconds=15,
                claim_ttl_seconds=7200,
                read_claim_ttl_seconds=300,
            )
            queue.register(ROUTER_THREAD_ID)
            payload = {"target_thread_id": TARGET_THREAD_ID}
            first = queue.submit(
                "inspect_thread",
                payload,
                idempotency_key="bind-read-retry",
            )
            wake = self.reserve(queue)
            request = self.claim(queue, wake)

            expired = queue.expire_stale_claims(
                now=float(request["claimed_at"]) + 301
            )

            self.assertEqual(1, expired)
            error = queue.response(first)["error"]
            self.assertEqual("router_read_claim_expired", error["code"])
            self.assertTrue(error["retryable"])
            self.assertFalse(error["may_have_started"])

            second = queue.submit(
                "inspect_thread",
                payload,
                idempotency_key="bind-read-retry",
            )
            self.assertNotEqual(first, second)
            self.assertEqual(1, queue.status().pending)
            retry = json.loads(
                queue._path(queue.pending_dir, second).read_text(encoding="utf-8")
            )
            self.assertEqual(1, retry["retry_generation"])

    def test_mutating_claim_keeps_long_ttl_when_read_claim_would_expire(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue = DesktopRouterQueue(
                Path(temporary),
                heartbeat_ttl_seconds=15,
                claim_ttl_seconds=7200,
                read_claim_ttl_seconds=300,
            )
            queue.register(ROUTER_THREAD_ID)
            request_id = queue.submit(
                "send_message_to_thread",
                {"target_thread_id": TARGET_THREAD_ID, "prompt": "do this once"},
                idempotency_key="long-mutation-ttl",
            )
            wake = self.reserve(queue)
            request = self.claim(queue, wake)

            self.assertEqual(
                0,
                queue.expire_stale_claims(now=float(request["claimed_at"]) + 301),
            )
            self.assertIsNone(queue.response(request_id))
            self.assertTrue(queue.was_claimed(request_id))

    def test_orphan_terminal_receipt_recovers_as_unknown_and_survives_cleanup(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue = DesktopRouterQueue(
                Path(temporary),
                retention_hours=1,
                claim_ttl_seconds=60,
            )
            queue.register(ROUTER_THREAD_ID)
            payload = {"target_thread_id": TARGET_THREAD_ID, "prompt": "once"}
            request_id = queue.submit(
                "send_message_to_thread",
                payload,
                idempotency_key="event-orphan-receipt",
            )
            wake = self.reserve(queue)
            request = self.claim(queue, wake)
            receipt = queue._finalization_path(request_id)
            receipt.touch()

            self.assertIsNone(queue.response(request_id))
            active_time = float(request["claimed_at"]) + queue.claim_ttl_seconds + 1
            self.assertEqual(0, queue.expire_stale_claims(now=active_time))
            expired_time = float(request["claimed_at"]) + max(
                queue.claim_ttl_seconds,
                queue.heartbeat_ttl_seconds,
                queue.wake_lease_ttl_seconds,
            ) + 2
            self.assertEqual(
                1,
                queue.expire_stale_claims(now=expired_time),
            )
            response = queue.response(request_id)
            self.assertEqual("target_result_unknown", response["error"]["code"])
            self.assertTrue(response["error"]["may_have_started"])
            queue.cleanup(now=expired_time + 7200)

            self.assertTrue(receipt.exists())
            self.assertEqual(
                request_id,
                queue.submit(
                    "send_message_to_thread",
                    payload,
                    idempotency_key="event-orphan-receipt",
                ),
            )
            self.assertEqual(0, queue.status().pending)

    def test_retry_generation_ancestry_survives_response_cleanup(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue = DesktopRouterQueue(Path(temporary), retention_hours=1)
            queue.register(ROUTER_THREAD_ID)
            payload = {"target_thread_id": TARGET_THREAD_ID, "prompt": "retry once"}
            first = queue.submit(
                "send_message_to_thread",
                payload,
                idempotency_key="event-retained-ancestry",
            )
            wake = self.reserve(queue)
            request = self.claim(queue, wake)
            queue.fail(
                first,
                code="temporary_target_gate",
                message="safe before target start",
                retryable=True,
                may_have_started=False,
                fence_token=wake["fence_token"],
            )
            second = queue.submit(
                "send_message_to_thread",
                payload,
                idempotency_key="event-retained-ancestry",
            )
            queue.cleanup(now=float(request["claimed_at"]) + 7200)

            self.assertFalse(queue._path(queue.responses_dir, first).exists())
            self.assertTrue(queue._finalization_path(first).exists())
            self.assertEqual(
                second,
                queue.submit(
                    "send_message_to_thread",
                    payload,
                    idempotency_key="event-retained-ancestry",
                ),
            )
            self.assertEqual(1, queue.status().pending)

    def test_expired_answer_text_becomes_a_small_unknown_tombstone(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue = DesktopRouterQueue(Path(temporary), retention_hours=1)
            queue.register(ROUTER_THREAD_ID)
            payload = {"target_thread_id": TARGET_THREAD_ID, "prompt": "answer"}
            request_id = queue.submit(
                "send_message_to_thread",
                payload,
                idempotency_key="event-expired-answer",
            )
            wake = self.reserve(queue)
            request = self.claim(queue, wake)
            queue.complete(
                request_id,
                {"thread_id": TARGET_THREAD_ID, "text": "private final body"},
                fence_token=wake["fence_token"],
            )
            # Simulate the released legacy layout: empty .final fence and full
            # response cache, with no separate receipt payload yet.
            queue._receipt_payload_path(request_id).unlink()
            queue.cleanup(now=float(request["claimed_at"]) + 7200)

            response = queue.response(request_id)
            self.assertEqual("failed", response["status"])
            self.assertEqual("target_result_unknown", response["error"]["code"])
            self.assertNotIn("private final body", json.dumps(response))
            self.assertEqual("", queue._finalization_path(request_id).read_text())
            self.assertEqual(
                request_id,
                queue.submit(
                    "send_message_to_thread",
                    payload,
                    idempotency_key="event-expired-answer",
                ),
            )

    def test_receipt_is_authoritative_over_disposable_response_cache(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue = DesktopRouterQueue(Path(temporary))
            queue.register(ROUTER_THREAD_ID)
            request_id = queue.submit(
                "inspect_thread",
                {"target_thread_id": TARGET_THREAD_ID},
                idempotency_key="event-receipt-priority",
            )
            wake = self.reserve(queue)
            self.claim(queue, wake)
            queue.complete(
                request_id,
                {"thread_id": TARGET_THREAD_ID},
                fence_token=wake["fence_token"],
            )
            cache = queue._path(queue.responses_dir, request_id)
            cached = json.loads(cache.read_text(encoding="utf-8"))
            cached["status"] = "failed"
            cache.write_text(json.dumps(cached), encoding="utf-8")

            self.assertEqual("completed", queue.response(request_id)["status"])
            cache.unlink()
            self.assertEqual(0, queue.status().claimed)

    def test_receipt_payload_without_marker_is_authoritative_and_not_replayed(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue = DesktopRouterQueue(Path(temporary))
            queue.register(ROUTER_THREAD_ID)
            payload = {"target_thread_id": TARGET_THREAD_ID, "prompt": "once"}
            request_id = queue.submit(
                "send_message_to_thread",
                payload,
                idempotency_key="event-receipt-only",
            )
            wake = self.reserve(queue)
            self.claim(queue, wake)
            with patch(
                "bridge_core.desktop_router.os.open",
                side_effect=OSError("injected marker publication failure"),
            ) as marker_open_mock:
                queue.complete(
                    request_id,
                    {"thread_id": TARGET_THREAD_ID, "text": "answer"},
                    fence_token=wake["fence_token"],
                )
            marker_open_mock.assert_called_once()
            self.assertFalse(queue._finalization_path(request_id).exists())
            queue._path(queue.responses_dir, request_id).unlink()

            self.assertEqual("completed", queue.response(request_id)["status"])
            self.assertEqual(
                request_id,
                queue.submit(
                    "send_message_to_thread",
                    payload,
                    idempotency_key="event-receipt-only",
                ),
            )
            self.assertEqual(0, queue.status().pending)

    def test_receipt_payload_survives_marker_descriptor_close_failure(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue = DesktopRouterQueue(Path(temporary))
            queue.register(ROUTER_THREAD_ID)
            request_id = queue.submit(
                "send_message_to_thread",
                {"target_thread_id": TARGET_THREAD_ID, "prompt": "once"},
                idempotency_key="event-receipt-close-fault",
            )
            wake = self.reserve(queue)
            self.claim(queue, wake)
            real_close = os.close

            def close_then_fail(descriptor):
                real_close(descriptor)
                raise OSError("injected descriptor close failure")

            with patch(
                "bridge_core.desktop_router.os.close",
                side_effect=close_then_fail,
            ) as marker_close_mock:
                queue.complete(
                    request_id,
                    {"thread_id": TARGET_THREAD_ID, "text": "answer"},
                    fence_token=wake["fence_token"],
                )

            marker_close_mock.assert_called_once()
            self.assertTrue(queue._finalization_path(request_id).exists())
            queue._path(queue.responses_dir, request_id).unlink()
            self.assertEqual("completed", queue.response(request_id)["status"])
            self.assertEqual(
                request_id,
                queue.submit(
                    "send_message_to_thread",
                    {"target_thread_id": TARGET_THREAD_ID, "prompt": "once"},
                    idempotency_key="event-receipt-close-fault",
                ),
            )
            self.assertEqual(0, queue.status().pending)
            self.assertEqual(0, queue.status().claimed)

    def test_concurrent_terminal_finalizers_preserve_first_receipt(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue = DesktopRouterQueue(Path(temporary))
            queue.register(ROUTER_THREAD_ID)
            request_id = queue.submit(
                "send_message_to_thread",
                {"target_thread_id": TARGET_THREAD_ID, "prompt": "once"},
                idempotency_key="event-finalizer-race",
            )
            wake = self.reserve(queue)
            self.claim(queue, wake)
            barrier = threading.Barrier(2)
            from bridge_core import desktop_router

            original_publish = desktop_router._atomic_write_json_exclusive
            receipt_path = queue._receipt_payload_path(request_id)

            def synchronized_publish(path, payload):
                if path == receipt_path:
                    barrier.wait(timeout=5)
                return original_publish(path, payload)

            def complete():
                try:
                    queue.complete(
                        request_id,
                        {"thread_id": TARGET_THREAD_ID, "text": "answer"},
                        fence_token=wake["fence_token"],
                    )
                except RouterProtocolError:
                    pass

            def fail():
                try:
                    queue.fail(
                        request_id,
                        code="late_failure",
                        message="raced",
                        retryable=False,
                        may_have_started=True,
                        fence_token=wake["fence_token"],
                    )
                except RouterProtocolError:
                    pass

            with patch(
                "bridge_core.desktop_router._atomic_write_json_exclusive",
                side_effect=synchronized_publish,
            ), ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(lambda action: action(), (complete, fail)))

            first_bytes = receipt_path.read_bytes()
            first = json.loads(first_bytes)
            self.assertIn(first["status"], {"completed", "failed"})
            if first["status"] == "completed":
                queue.fail(
                    request_id,
                    code="later_failure",
                    message="ignored",
                    retryable=False,
                    may_have_started=True,
                    fence_token=wake["fence_token"],
                )
            else:
                with self.assertRaises(RouterProtocolError):
                    queue.complete(
                        request_id,
                        {"thread_id": TARGET_THREAD_ID, "text": "later"},
                        fence_token=wake["fence_token"],
                    )
            self.assertEqual(first_bytes, receipt_path.read_bytes())

    def test_fresh_router_heartbeat_protects_a_long_running_claim(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue = DesktopRouterQueue(
                Path(temporary),
                heartbeat_ttl_seconds=90,
                claim_ttl_seconds=60,
            )
            queue.register(ROUTER_THREAD_ID)
            request_id = queue.submit(
                "send_message_to_thread",
                {"target_thread_id": TARGET_THREAD_ID, "prompt": "long task"},
                idempotency_key="event-long",
            )
            wake = self.reserve(queue)
            request = self.claim(queue, wake)

            expired = queue.expire_stale_claims(now=float(request["claimed_at"]) + 61)

            self.assertEqual(0, expired)
            self.assertIsNone(queue.response(request_id))
            self.assertTrue(queue.was_claimed(request_id))

    def test_exclusive_claim_publication_keeps_canonical_pending(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue = DesktopRouterQueue(Path(temporary))
            queue.register(ROUTER_THREAD_ID, "host-one")
            request_id = queue.submit(
                "inspect_thread",
                {"target_thread_id": TARGET_THREAD_ID},
                idempotency_key="claim-publication-window",
            )
            wake = self.reserve(queue)
            request = self.claim(queue, wake)
            pending = queue._path(queue.pending_dir, request_id)
            claimed = queue._path(queue.claimed_dir, request_id)

            self.assertEqual(0, queue.expire_stale_claims())
            self.assertIsNone(queue.response(request_id))
            self.assertTrue(queue.was_claimed(request_id))
            self.assertTrue(pending.exists())
            self.assertTrue(claimed.exists())
            self.assertEqual(wake["fence_token"], request["fence_token"])
            self.assertEqual(
                wake["fence_token"],
                json.loads(claimed.read_text(encoding="utf-8"))["fence_token"],
            )

    def test_retention_never_deletes_a_nonterminal_long_running_claim(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue = DesktopRouterQueue(
                Path(temporary),
                claim_ttl_seconds=86400,
                retention_hours=1,
            )
            queue.register(ROUTER_THREAD_ID)
            request_id = queue.submit(
                "send_message_to_thread",
                {"target_thread_id": TARGET_THREAD_ID, "prompt": "long task"},
                idempotency_key="event-retention-vs-claim",
            )
            wake = self.reserve(queue)
            request = self.claim(queue, wake)

            queue.cleanup(now=float(request["claimed_at"]) + 7200)

            self.assertTrue(queue.was_claimed(request_id))
            self.assertIsNone(queue.response(request_id))

    def test_sentinel_reserves_once_without_returning_message_payload(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue = DesktopRouterQueue(Path(temporary))
            queue.register(ROUTER_THREAD_ID, "host-one")
            queue.submit(
                "send_message_to_thread",
                {"target_thread_id": TARGET_THREAD_ID, "prompt": "private body"},
                idempotency_key="sentinel-metadata",
            )

            first = queue.sentinel_probe(ROUTER_THREAD_ID, "host-one")
            second = queue.sentinel_probe(ROUTER_THREAD_ID, "host-one")

            self.assertTrue(first["should_wake"])
            self.assertEqual("wake_inflight", second["reason"])
            self.assertNotIn("private body", repr(first))
            self.assertNotIn("fence_token", second)

    def test_concurrent_sentinel_probes_reserve_exactly_one_wake(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            root = Path(temporary)
            first_queue = DesktopRouterQueue(root)
            second_queue = DesktopRouterQueue(root)
            first_queue.register(ROUTER_THREAD_ID, "host-one")
            first_queue.submit(
                "send_message_to_thread",
                {"target_thread_id": TARGET_THREAD_ID, "prompt": "private body"},
                idempotency_key="concurrent-sentinel",
            )
            barrier = threading.Barrier(3)

            def probe(queue: DesktopRouterQueue) -> dict:
                barrier.wait(timeout=10)
                return queue.sentinel_probe(ROUTER_THREAD_ID, "host-one")

            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(probe, queue) for queue in (first_queue, second_queue)]
                barrier.wait(timeout=10)
                results = [future.result(timeout=10) for future in futures]

            reserved = [result for result in results if result["should_wake"]]
            rejected = [result for result in results if not result["should_wake"]]
            self.assertEqual(1, len(reserved), results)
            self.assertEqual(1, len(rejected), results)
            self.assertEqual("wake_inflight", rejected[0]["reason"])
            self.assertNotIn("private body", repr(results))
            self.assertNotIn("fence_token", rejected[0])

    def test_manual_ticket_claims_exactly_one_matching_operation(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue = DesktopRouterQueue(Path(temporary))
            queue.register(ROUTER_THREAD_ID, "host-one")
            ordinary_id = queue.submit(
                "send_message_to_thread",
                {"target_thread_id": TARGET_THREAD_ID, "prompt": "private body"},
                idempotency_key="manual-ordinary",
            )
            catalog_id = queue.submit(
                "list_task_catalog",
                {"visible_thread_ids": [], "include_archived": False, "limit": 50},
                idempotency_key="manual-catalog",
            )
            ticket = queue.authorize_manual_cycle(
                ROUTER_THREAD_ID,
                "host-one",
                "list_task_catalog",
            )
            from bridge_core import desktop_router

            original_read_json = desktop_router._read_json

            def reject_pending_payload_read(path):
                if path.parent.name == "pending":
                    self.fail(f"manual probe opened pending payload: {path}")
                return original_read_json(path)

            with patch.object(
                desktop_router,
                "_read_json",
                side_effect=reject_pending_payload_read,
            ):
                wake = queue.manual_probe(
                    ticket["ticket_id"],
                    ROUTER_THREAD_ID,
                    "host-one",
                )
            request = self.claim(queue, wake)

            self.assertTrue(wake["should_wake"])
            self.assertEqual("manual_ticket", wake["reason"])
            self.assertEqual(catalog_id, request["request_id"])
            self.assertEqual("list_task_catalog", request["operation"])
            self.assertEqual("manual_ticket", request["wake_origin"])
            self.assertNotIn("private body", repr(wake))
            self.assertIsNone(self.claim(queue, wake))
            self.assertFalse(queue.was_claimed(ordinary_id))
            self.assertFalse(queue.status().sentinel_fresh)

            with self.assertRaises(RouterProtocolError):
                queue.manual_probe(
                    ticket["ticket_id"],
                    ROUTER_THREAD_ID,
                    "host-one",
                )

    def test_manual_ticket_is_task_bound_expiring_and_single_use_on_empty(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue = DesktopRouterQueue(Path(temporary))
            queue.register(ROUTER_THREAD_ID, "host-one")
            ticket = queue.authorize_manual_cycle(
                ROUTER_THREAD_ID,
                "host-one",
                "inspect_thread",
                ttl_seconds=30,
                now=200.0,
            )

            with self.assertRaises(RouterProtocolError):
                queue.manual_probe(
                    ticket["ticket_id"],
                    ROUTER_THREAD_ID,
                    "host-two",
                    now=201.0,
                )

            result = queue.manual_probe(
                ticket["ticket_id"],
                ROUTER_THREAD_ID,
                "host-one",
                now=202.0,
            )
            self.assertFalse(result["should_wake"])
            self.assertEqual("expected_request_not_pending", result["reason"])
            self.assertTrue(result["manual_ticket_consumed"])
            with self.assertRaises(RouterProtocolError):
                queue.manual_probe(
                    ticket["ticket_id"],
                    ROUTER_THREAD_ID,
                    "host-one",
                    now=203.0,
                )

            expired = queue.authorize_manual_cycle(
                ROUTER_THREAD_ID,
                "host-one",
                "inspect_thread",
                ttl_seconds=30,
                now=300.0,
            )
            with self.assertRaises(RouterProtocolError):
                queue.manual_probe(
                    expired["ticket_id"],
                    ROUTER_THREAD_ID,
                    "host-one",
                    now=331.0,
                )

    def test_stale_fence_cannot_finalize_after_wake_replacement(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue = DesktopRouterQueue(Path(temporary), wake_lease_ttl_seconds=60)
            queue.register(ROUTER_THREAD_ID, "host-one")
            request_id = queue.submit(
                "inspect_thread",
                {"target_thread_id": TARGET_THREAD_ID},
                idempotency_key="fenced-result",
            )
            wake = self.reserve(queue)
            self.claim(queue, wake)
            queue.release_wake(wake["wake_id"], wake["fence_token"], reason="test")

            with self.assertRaises(RouterProtocolError):
                queue.complete(
                    request_id,
                    {"thread_id": TARGET_THREAD_ID},
                    fence_token=wake["fence_token"],
                )

    def test_legacy_unfenced_claim_is_terminalized_as_uncertain(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue = DesktopRouterQueue(Path(temporary))
            queue.register(ROUTER_THREAD_ID, "host-one")
            request_id = queue.submit(
                "send_message_to_thread",
                {"target_thread_id": TARGET_THREAD_ID, "prompt": "do this once"},
                idempotency_key="legacy-unfenced",
            )
            wake = self.reserve(queue)
            request = self.claim(queue, wake)
            claim_path = queue.claimed_dir / f"{request_id}.json"
            legacy = json.loads(claim_path.read_text(encoding="utf-8"))
            legacy.pop("fence_token", None)
            claim_path.write_text(
                json.dumps(legacy, ensure_ascii=False),
                encoding="utf-8",
            )

            with self.assertRaises(RouterProtocolError):
                queue.stage_path(request_id, wake["fence_token"])
            with self.assertRaises(RouterProtocolError):
                queue.complete(
                    request_id,
                    {"thread_id": TARGET_THREAD_ID},
                    fence_token=wake["fence_token"],
                )

            self.assertEqual(
                0,
                queue.expire_stale_claims(now=float(request["claimed_at"])),
            )
            expired_time = float(request["claimed_at"]) + max(
                queue.claim_ttl_seconds,
                queue.heartbeat_ttl_seconds,
                queue.wake_lease_ttl_seconds,
            ) + 2
            self.assertEqual(
                1,
                queue.expire_stale_claims(now=expired_time),
            )
            error = queue.response(request_id)["error"]
            self.assertEqual("legacy_unfenced_claim", error["code"])
            self.assertFalse(error["retryable"])
            self.assertTrue(error["may_have_started"])

    def test_legacy_unfenced_read_claim_advances_retry_generation(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue = DesktopRouterQueue(Path(temporary))
            queue.register(ROUTER_THREAD_ID, "host-one")
            payload = {"target_thread_id": TARGET_THREAD_ID}
            request_id = queue.submit(
                "inspect_thread",
                payload,
                idempotency_key="legacy-unfenced-read",
            )
            wake = self.reserve(queue)
            request = self.claim(queue, wake)
            claim_path = queue.claimed_dir / f"{request_id}.json"
            legacy = json.loads(claim_path.read_text(encoding="utf-8"))
            legacy.pop("fence_token", None)
            claim_path.write_text(
                json.dumps(legacy, ensure_ascii=False),
                encoding="utf-8",
            )

            expired_time = float(request["claimed_at"]) + max(
                queue.claim_ttl_seconds,
                queue.heartbeat_ttl_seconds,
                queue.wake_lease_ttl_seconds,
            ) + 2
            self.assertEqual(1, queue.expire_stale_claims(now=expired_time))
            error = queue.response(request_id)["error"]
            self.assertEqual("router_read_claim_expired", error["code"])
            self.assertTrue(error["retryable"])
            self.assertFalse(error["may_have_started"])

            retry_id = queue.submit(
                "inspect_thread",
                payload,
                idempotency_key="legacy-unfenced-read",
            )
            self.assertNotEqual(request_id, retry_id)


if __name__ == "__main__":
    unittest.main()
