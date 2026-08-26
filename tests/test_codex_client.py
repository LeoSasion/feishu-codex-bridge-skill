from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest


SKILL_ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP_ROOT = Path(os.environ.get("FEISHU_BRIDGE_TEST_TMP", tempfile.gettempdir()))
if "FEISHU_BRIDGE_TEST_TMP" not in os.environ:
    TEST_TEMP_ROOT = TEST_TEMP_ROOT / "feishu-codex-bridge-tests"
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from bridge_core.codex_client import (  # noqa: E402
    CodexGatewayError,
    CodexSessionNotBound,
    CodexTargetUnavailable,
    CodexTurnInterrupted,
    DesktopRouterCodex,
    TurnHandle,
    create_codex_client,
    looks_like_thread_id,
)
from bridge_core.config import load_config  # noqa: E402
from bridge_core.desktop_router import (  # noqa: E402
    DesktopRouterQueue,
    RouterProtocolError,
)


ROUTER_THREAD_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
TARGET_THREAD_ID = "11111111-2222-3333-4444-555555555555"
DISPLACED_THREAD_ID = "66666666-7777-8888-9999-000000000000"


class CodexClientContractTests(unittest.TestCase):
    def configured(self, root: Path):
        return replace(
            load_config(),
            project_root=root,
            projects_root=root.parent,
            runtime_dir=root / ".codex" / "feishu-bridge",
            router_timeout_seconds=3,
            router_heartbeat_ttl_seconds=90,
        )

    def service_once(
        self,
        queue: DesktopRouterQueue,
        result: dict,
        captured: list[dict] | None = None,
    ) -> threading.Thread:
        def worker() -> None:
            deadline = time.monotonic() + 2
            wake: dict = {}
            while time.monotonic() < deadline:
                wake = queue.sentinel_probe(ROUTER_THREAD_ID, "host-local")
                if wake.get("should_wake"):
                    break
                time.sleep(0.02)
            self.assertTrue(wake.get("should_wake"), wake)
            request = queue.claim(
                ROUTER_THREAD_ID,
                "host-local",
                wake_id=str(wake["wake_id"]),
                fence_token=str(wake["fence_token"]),
            )
            self.assertIsNotNone(request)
            if captured is not None:
                captured.append(request or {})
            queue.complete(
                str(request["request_id"]),
                result,
                fence_token=str(wake["fence_token"]),
            )

        thread = threading.Thread(target=worker)
        thread.start()
        return thread

    def test_client_uses_only_desktop_router_queue(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            config = self.configured(Path(temporary))
            client = create_codex_client(config)
            self.assertIsInstance(client, DesktopRouterCodex)
            self.assertFalse(client.is_alive())
            self.assertEqual("desktop-router", client.session_owner)
            self.assertFalse(hasattr(client, "codex_cli"))

    def test_unbound_message_is_rejected_before_queue_submission(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            config = self.configured(Path(temporary))
            queue = DesktopRouterQueue(config.runtime_dir)
            client = DesktopRouterCodex(config, queue)
            with self.assertRaises(CodexSessionNotBound):
                client.route_message({}, "测试会话", "你好", client_message_id="message-1")
            self.assertEqual(0, queue.status().pending)

    def test_submit_protocol_conflict_is_unknown_for_mutating_operations(self) -> None:
        class ConflictQueue:
            @staticmethod
            def is_registered() -> bool:
                return True

            @staticmethod
            def submit(*args, **kwargs):
                del args, kwargs
                raise RouterProtocolError("terminal receipt is unreadable")

        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            client = DesktopRouterCodex(
                self.configured(Path(temporary)),
                ConflictQueue(),
            )
            with self.assertRaises(CodexTurnInterrupted):
                client._submit_and_wait(
                    "send_message_to_thread",
                    {"target_thread_id": TARGET_THREAD_ID, "prompt": "once"},
                    request_key="event-conflict",
                    completed_may_have_started=True,
                )
            with self.assertRaises(CodexGatewayError) as read_only:
                client._submit_and_wait(
                    "inspect_thread",
                    {"target_thread_id": TARGET_THREAD_ID},
                    request_key="inspect-conflict",
                    completed_may_have_started=False,
                )
            self.assertFalse(read_only.exception.may_have_started)

    def test_thread_id_validation_accepts_current_id_shapes(self) -> None:
        self.assertTrue(looks_like_thread_id(TARGET_THREAD_ID))
        self.assertTrue(looks_like_thread_id("thr_1234567890abcdefghijklmnop"))
        self.assertFalse(looks_like_thread_id("hello"))
        self.assertFalse(looks_like_thread_id("../../not-a-thread"))

    def test_archive_results_are_explicit_and_limited_to_requested_tasks(self) -> None:
        requested = (DISPLACED_THREAD_ID,)
        self.assertEqual((), DesktopRouterCodex._confirmed_archives({}, requested))
        self.assertEqual(
            requested,
            DesktopRouterCodex._confirmed_archives(
                {"archived_thread_ids": [DISPLACED_THREAD_ID]},
                requested,
            ),
        )
        with self.assertRaises(CodexTurnInterrupted):
            DesktopRouterCodex._confirmed_archives(
                {"archived_thread_ids": TARGET_THREAD_ID},
                requested,
            )
        with self.assertRaises(CodexTurnInterrupted):
            DesktopRouterCodex._confirmed_archives(
                {"archived_thread_ids": [TARGET_THREAD_ID]},
                requested,
            )

    def test_completed_target_identity_must_exactly_match_the_request(self) -> None:
        with self.assertRaises(CodexGatewayError) as read_only:
            DesktopRouterCodex._thread_result(
                {"thread_id": DISPLACED_THREAD_ID},
                expected_thread_id=TARGET_THREAD_ID,
                outcome_may_have_started=False,
            )
        self.assertFalse(read_only.exception.may_have_started)

        with self.assertRaises(CodexTurnInterrupted):
            DesktopRouterCodex._thread_result(
                {"thread_id": DISPLACED_THREAD_ID},
                expected_thread_id=TARGET_THREAD_ID,
                outcome_may_have_started=True,
            )

    def test_malformed_completed_mutation_result_is_unknown(self) -> None:
        with self.assertRaises(CodexTurnInterrupted):
            DesktopRouterCodex._result(
                {"status": "completed", "result": None},
                completed_may_have_started=True,
            )
        with self.assertRaises(CodexTurnInterrupted):
            DesktopRouterCodex._result(
                {
                    "status": "failed",
                    "error": {
                        "code": "temporary_failure",
                        "retryable": "true",
                        "may_have_started": "false",
                    },
                },
                completed_may_have_started=True,
            )

    def test_archived_or_missing_target_is_typed_and_never_router_retried(self) -> None:
        for code in ("target_archived", "target_not_found"):
            with self.subTest(code=code):
                with self.assertRaises(CodexTargetUnavailable) as caught:
                    DesktopRouterCodex._result(
                        {
                            "status": "failed",
                            "error": {
                                "code": code,
                                "message": "target ended before delivery",
                                "retryable": True,
                                "may_have_started": False,
                            },
                        }
                    )
                self.assertEqual(code, caught.exception.code)
                self.assertFalse(caught.exception.retryable)
                self.assertFalse(caught.exception.may_have_started)

    def test_unknown_delivery_takes_precedence_over_unavailable_target_code(self) -> None:
        with self.assertRaises(CodexTurnInterrupted):
            DesktopRouterCodex._result(
                {
                    "status": "failed",
                    "error": {
                        "code": "target_archived",
                        "message": "send outcome is unknown",
                        "retryable": False,
                        "may_have_started": True,
                    },
                }
            )

        with self.assertRaises(CodexTurnInterrupted):
            DesktopRouterCodex._result(
                {
                    "status": "failed",
                    "error": {
                        "code": "router_offline",
                        "message": "offline response conflicts with a started mutation",
                        "retryable": True,
                        "may_have_started": True,
                    },
                }
            )

    def test_terminal_gateway_codes_ignore_retryable_flag(self) -> None:
        for code in ("target_tool_unavailable", "project_not_registered"):
            with self.subTest(code=code):
                with self.assertRaises(CodexGatewayError) as caught:
                    DesktopRouterCodex._result(
                        {
                            "status": "failed",
                            "error": {
                                "code": code,
                                "message": "terminal control-plane failure",
                                "retryable": True,
                                "may_have_started": False,
                            },
                        }
                    )
                self.assertFalse(caught.exception.retryable)

    def test_bind_is_a_desktop_inspection_not_a_target_resume(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            config = self.configured(Path(temporary))
            queue = DesktopRouterQueue(config.runtime_dir)
            queue.register(ROUTER_THREAD_ID, "host-local")
            client = DesktopRouterCodex(config, queue)
            captured: list[dict] = []
            worker = self.service_once(
                queue,
                {"thread_id": TARGET_THREAD_ID, "host_id": "host-target"},
                captured,
            )
            activation = client.bind_thread(
                TARGET_THREAD_ID,
                "Alice",
                request_key="event-bind",
            )
            worker.join(3)
            self.assertEqual(TARGET_THREAD_ID, activation.thread_id)
            self.assertEqual("host-target", activation.host_id)
            self.assertEqual("inspect_thread", captured[0]["operation"])
            self.assertEqual(TARGET_THREAD_ID, captured[0]["payload"]["target_thread_id"])

    def test_task_catalog_preserves_exact_scope_visibility_and_metadata_bounds(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            root = Path(temporary)
            config = self.configured(root)
            queue = DesktopRouterQueue(config.runtime_dir)
            queue.register(ROUTER_THREAD_ID, "host-local")
            client = DesktopRouterCodex(config, queue)
            captured: list[dict] = []
            worker = self.service_once(
                queue,
                {
                    "catalog_version": 1,
                    "include_archived": False,
                    "truncated": False,
                    "projects": [
                        {
                            "project_id": "project-a",
                            "label": "Bridge",
                            "root": str(root.resolve()),
                            "host_id": "host-local",
                            "kind": "local",
                        }
                    ],
                    "tasks": [
                        {
                            "thread_id": TARGET_THREAD_ID,
                            "title": "目标任务",
                            "project_id": "project-a",
                            "host_id": "host-local",
                            "status": "idle",
                            "archived": False,
                            "updated_at": 10,
                        }
                    ],
                },
                captured,
            )
            catalog = client.list_task_catalog(
                visible_thread_ids=[TARGET_THREAD_ID],
                include_archived=False,
                request_key="event-catalog",
                limit=100,
            )
            worker.join(3)

            self.assertEqual("list_task_catalog", captured[0]["operation"])
            self.assertEqual("exact", captured[0]["payload"]["visibility"])
            self.assertEqual([TARGET_THREAD_ID], captured[0]["payload"]["thread_ids"])
            self.assertEqual(50, captured[0]["payload"]["limit"])
            self.assertEqual(TARGET_THREAD_ID, catalog.tasks[0].thread_id)
            self.assertEqual("project-a", catalog.projects[0].project_id)

    def test_task_catalog_rejects_the_dedicated_gateway_task(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            root = Path(temporary)
            config = self.configured(root)
            queue = DesktopRouterQueue(config.runtime_dir)
            queue.register(ROUTER_THREAD_ID, "host-local")
            client = DesktopRouterCodex(config, queue)
            worker = self.service_once(
                queue,
                {
                    "catalog_version": 1,
                    "include_archived": False,
                    "truncated": False,
                    "projects": [
                        {
                            "project_id": "project-a",
                            "label": "Bridge",
                            "root": str(root.resolve()),
                            "host_id": "host-local",
                            "kind": "local",
                        }
                    ],
                    "tasks": [
                        {
                            "thread_id": ROUTER_THREAD_ID,
                            "title": "Gateway",
                            "project_id": "project-a",
                            "host_id": "host-local",
                            "status": "idle",
                            "archived": False,
                            "updated_at": 10,
                        }
                    ],
                },
            )
            with self.assertRaises(CodexGatewayError) as caught:
                client.list_task_catalog(
                    visible_thread_ids=None,
                    include_archived=False,
                    request_key="event-catalog-gateway",
                )
            worker.join(3)
            self.assertEqual("invalid_gateway_result", caught.exception.code)
            self.assertFalse(caught.exception.may_have_started)

    def test_create_uses_exact_project_and_minimal_desktop_bootstrap(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            root = Path(temporary)
            config = self.configured(root)
            queue = DesktopRouterQueue(config.runtime_dir)
            queue.register(ROUTER_THREAD_ID, "host-local")
            client = DesktopRouterCodex(config, queue)
            captured: list[dict] = []
            worker = self.service_once(
                queue,
                {"thread_id": TARGET_THREAD_ID, "host_id": "host-target"},
                captured,
            )

            creation = client.create_thread(
                "Alice",
                request_key="event-create",
                archive_thread_ids=[DISPLACED_THREAD_ID],
                project_root=root,
            )
            worker.join(3)

            request = captured[0]
            self.assertEqual(TARGET_THREAD_ID, creation.thread_id)
            self.assertEqual("create_thread", request["operation"])
            self.assertEqual(str(root.resolve()), request["payload"]["project_root"])
            self.assertEqual("Alice", request["payload"]["title"])
            self.assertEqual([DISPLACED_THREAD_ID], request["payload"]["archive_thread_ids"])
            self.assertEqual((), creation.archived_thread_ids)
            self.assertIn("只确认路由已就绪", request["payload"]["initial_prompt"])
            self.assertNotIn("model", request["payload"])
            self.assertNotIn("thinking", request["payload"])

    def test_create_rejects_a_requested_displaced_id_as_the_new_task(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            root = Path(temporary)
            config = self.configured(root)
            queue = DesktopRouterQueue(config.runtime_dir)
            queue.register(ROUTER_THREAD_ID, "host-local")
            client = DesktopRouterCodex(config, queue)
            worker = self.service_once(
                queue,
                {"thread_id": DISPLACED_THREAD_ID, "host_id": "host-target"},
            )
            with self.assertRaises(CodexTurnInterrupted):
                client.create_thread(
                    "Alice",
                    request_key="event-create-conflict",
                    archive_thread_ids=[DISPLACED_THREAD_ID],
                    project_root=root,
                )
            worker.join(3)

    def test_message_uses_send_message_to_thread_and_preserves_transport_manifest(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            root = Path(temporary)
            config = self.configured(root)
            queue = DesktopRouterQueue(config.runtime_dir)
            queue.register(ROUTER_THREAD_ID, "host-local")
            client = DesktopRouterCodex(config, queue)
            image = root / "image.png"
            image.write_bytes(b"png")
            captured: list[dict] = []
            worker = self.service_once(
                queue,
                {
                    "thread_id": TARGET_THREAD_ID,
                    "host_id": "host-target",
                    "text": "目标任务最终回答",
                    "cursor": "cursor-1",
                },
                captured,
            )
            handles: list[TurnHandle] = []
            answer = client.route_message(
                {"thread_id": TARGET_THREAD_ID, "host_id": "host-target"},
                "Alice",
                "原始问题",
                client_message_id="message-1",
                local_images=[image],
                additional_context={"transport_attachments": "类型：file"},
                on_turn_started=handles.append,
            )
            worker.join(3)
            request = captured[0]
            self.assertEqual("send_message_to_thread", request["operation"])
            self.assertEqual(TARGET_THREAD_ID, request["payload"]["target_thread_id"])
            self.assertTrue(request["payload"]["prompt"].startswith("原始问题"))
            self.assertIn(str(image.resolve()), request["payload"]["prompt"])
            self.assertIn("类型：file", request["payload"]["prompt"])
            self.assertEqual("目标任务最终回答", answer.text)
            self.assertEqual(1, len(handles))

    def test_message_without_an_authoritative_final_is_unknown(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            config = self.configured(Path(temporary))
            queue = DesktopRouterQueue(config.runtime_dir)
            queue.register(ROUTER_THREAD_ID, "host-local")
            client = DesktopRouterCodex(config, queue)
            worker = self.service_once(
                queue,
                {"thread_id": TARGET_THREAD_ID, "text": ""},
            )
            with self.assertRaises(CodexTurnInterrupted):
                client.route_message(
                    {"thread_id": TARGET_THREAD_ID},
                    "Alice",
                    "原始问题",
                    client_message_id="message-empty-final",
                )
            worker.join(3)

    def test_compaction_is_routed_as_a_target_command(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            config = self.configured(Path(temporary))
            queue = DesktopRouterQueue(config.runtime_dir)
            queue.register(ROUTER_THREAD_ID, "host-local")
            client = DesktopRouterCodex(config, queue)
            captured: list[dict] = []
            worker = self.service_once(
                queue,
                {
                    "thread_id": TARGET_THREAD_ID,
                    "host_id": "host-target",
                    "archived_thread_ids": [DISPLACED_THREAD_ID],
                },
                captured,
            )
            activation = client.compact(
                TARGET_THREAD_ID,
                request_key="event-compact",
                host_id="host-target",
                archive_thread_ids=[DISPLACED_THREAD_ID],
            )
            worker.join(3)
            self.assertEqual(TARGET_THREAD_ID, activation.thread_id)
            self.assertEqual("compact_thread", captured[0]["operation"])
            self.assertEqual("/compact", captured[0]["payload"]["command"])
            self.assertEqual([DISPLACED_THREAD_ID], captured[0]["payload"]["archive_thread_ids"])
            self.assertEqual((DISPLACED_THREAD_ID,), activation.archived_thread_ids)

    def test_unknown_context_keys_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            config = self.configured(Path(temporary))
            queue = DesktopRouterQueue(config.runtime_dir)
            queue.register(ROUTER_THREAD_ID, "host-local")
            client = DesktopRouterCodex(config, queue)
            with self.assertRaises(CodexGatewayError):
                client.route_message(
                    {"thread_id": TARGET_THREAD_ID},
                    "Alice",
                    "问题",
                    client_message_id="message-context",
                    additional_context={"rag_context": "synthetic context"},
                )


if __name__ == "__main__":
    unittest.main()
