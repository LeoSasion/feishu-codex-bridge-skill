from __future__ import annotations

from dataclasses import replace
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


SKILL_ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP_ROOT = Path(os.environ.get("FEISHU_BRIDGE_TEST_TMP", tempfile.gettempdir()))
if "FEISHU_BRIDGE_TEST_TMP" not in os.environ:
    TEST_TEMP_ROOT = TEST_TEMP_ROOT / "feishu-codex-bridge-tests"
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from bridge_core.beeper_client import (  # noqa: E402
    BeeperError,
    BeeperNotLoaded,
    ResponderNotBound,
    ResponderUnavailable,
    ResponderOutcomeUnknown,
    HistoricalBeeperClient,
    BEEPER_LOAD_GRACE_SECONDS,
    BEEPER_POST_LOAD_CLAIM_SECONDS,
    BeeperClient,
    ResponderTurnHandle,
    create_beeper_client,
    looks_like_thread_id,
)
from bridge_core.config import load_config  # noqa: E402
from bridge_core.beeper_queue import (  # noqa: E402
    BeeperQueue,
    BEEPER_CLAIM_WAIT_MAX_SECONDS,
    BeeperQueueProtocolError,
    BeeperQueueStatus,
)


BEEPER_THREAD_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
RESPONDER_THREAD_ID = "11111111-2222-3333-4444-555555555555"
DISPLACED_THREAD_ID = "66666666-7777-8888-9999-000000000000"
SELECTION_PROOF = "e" * 64


class QueueForTests:
    """In-memory protocol double; the subprocess runner remains independently mocked."""

    def __init__(self, registration: dict[str, object]) -> None:
        self._registration = dict(registration)
        self.requests: dict[str, dict[str, object]] = {}
        self.operations: dict[str, str] = {}
        self.responses: dict[str, dict[str, object]] = {}
        self.pages: dict[str, str] = {}
        self.snapshot_ids: dict[str, str] = {}
        self.operation_receipts: dict[str, str] = {}
        self.reserved: set[str] = set()
        self.failures: list[dict[str, object]] = []
        self.finish_waiting = False
        self.finish_readonly_request_error: Exception | None = None
        self.finish_readonly_request_calls: list[str] = []
        self.final_callback_answer = "Final Callback 最终回答🚀"
        self.claim_state = "claimed_armed"
        self.claim_states: list[str] = []
        self.claim_waits: list[int | float] = []
        self.unclaimed_failures: list[dict[str, object]] = []
        self.status_value = BeeperQueueStatus(
            registered=True,
            pending=0,
            claimed=0,
        )

    def registration(self) -> dict[str, object]:
        return dict(self._registration)

    def status(self) -> BeeperQueueStatus:
        return self.status_value

    @staticmethod
    def excluded_thread_ids() -> tuple[str, ...]:
        return (BEEPER_THREAD_ID,)

    def submit(
        self,
        operation: str,
        payload: dict[str, object],
        *,
        idempotency_key: str,
    ) -> str:
        request_id = hashlib.sha256(
            f"{operation}\0{idempotency_key}".encode("utf-8")
        ).hexdigest()[:32]
        existing = self.requests.get(request_id)
        if existing is not None and existing != payload:
            raise BeeperQueueProtocolError("conflicting test request")
        self.requests[request_id] = dict(payload)
        self.operations[request_id] = operation
        return request_id

    def response(self, request_id: str) -> dict[str, object] | None:
        response = self.responses.get(request_id)
        return dict(response) if response is not None else None

    def wait_for_beeper_claim(
        self,
        page: str,
        wait_seconds: int | float,
    ) -> str:
        if page not in self.pages:
            raise BeeperQueueProtocolError("test page is unknown")
        self.claim_waits.append(wait_seconds)
        if self.claim_states:
            self.claim_state = self.claim_states.pop(0)
        return self.claim_state

    def fail_page_if_unclaimed(
        self,
        page: str,
        code: str,
    ) -> dict[str, object] | None:
        if self.claim_state != "reserved":
            return None
        self.unclaimed_failures.append({"page": page, "code": code})
        response = self.fail_page(page, code, False)
        self.claim_state = "terminal"
        return response

    def reserve_exact(self, request_id: str) -> dict[str, object]:
        if request_id in self.reserved:
            raise BeeperQueueProtocolError("test grant was already consumed")
        self.reserved.add(request_id)
        page = request_id
        self.pages[page] = request_id
        operation = self.operations[request_id]
        if operation == "list_task_catalog":
            self.snapshot_ids[page] = "c" * 32
        if operation == "inspect_thread":
            self.operation_receipts[page] = "d" * 32
        return {
            "status": "reserved",
            "page": page,
            "page_id": page,
            "request_id": request_id,
            "operation": operation,
            "snapshot_id": self.snapshot_ids.get(page, ""),
            "operation_receipt": self.operation_receipts.get(page, ""),
        }

    def finish_readonly(
        self,
        page: str,
        wait_seconds: int,
    ) -> dict[str, object]:
        del wait_seconds
        request_id = self.pages[page]
        operation = self.operations[request_id]
        payload = self.requests[request_id]
        if self.finish_waiting:
            return {
                "status": "waiting_readonly",
                "terminal": False,
                "page": page,
                "request_id": request_id,
                "operation": operation,
            }
        if operation == "list_task_catalog":
            result: dict[str, object] = {
                "catalog_version": 1,
                "snapshot_id": self.snapshot_ids[page],
                "snapshot_expires_at": time.time() + 600,
                "include_archived": False,
                "truncated": False,
                "projects": [
                    {
                        "project_id": "project-a",
                        "label": "Bridge",
                        "host_id": "host-responder",
                        "kind": "local",
                    }
                ],
                "tasks": [
                    {
                        "thread_id": RESPONDER_THREAD_ID,
                        "title": "Responder 任务",
                        "project_id": "project-a",
                        "host_id": "host-responder",
                        "kind": "codex",
                        "status": "idle",
                        "archived": False,
                        "updated_at": 10,
                        "selection_proof": SELECTION_PROOF,
                    }
                ],
            }
        elif operation == "inspect_thread":
            result = {
                "thread_id": str(payload["responder_thread_id"]),
                "project_id": str(payload["expected_project_id"]),
                "host_id": str(payload["expected_host_id"]),
                "archived": False,
                "catalog_snapshot_id": str(payload["catalog_snapshot_id"]),
                "operation_receipt": self.operation_receipts[page],
            }
        else:
            raise AssertionError("test read-only operation is unsupported")
        response: dict[str, object] = {
            "operation": operation,
            "status": "completed",
            "terminal": True,
            "final_callback_source": "not_applicable",
            "page": page,
            "request_id": request_id,
            "result": result,
        }
        self.responses[request_id] = {
            key: value
            for key, value in response.items()
            if key not in {"terminal", "page"}
        }
        return dict(response)

    def finish_readonly_request(
        self,
        request_id: str,
        wait_seconds: int,
    ) -> dict[str, object]:
        del wait_seconds
        self.finish_readonly_request_calls.append(request_id)
        if self.finish_readonly_request_error is not None:
            raise self.finish_readonly_request_error
        response = self.responses[request_id]
        page = next(
            page_id
            for page_id, candidate_request_id in self.pages.items()
            if candidate_request_id == request_id
        )
        return {
            **response,
            "terminal": True,
            "page": page,
            "request_id": request_id,
        }

    def finish_final_callback(
        self,
        page: str,
        wait_seconds: int,
    ) -> dict[str, object]:
        del wait_seconds
        request_id = self.pages[page]
        payload = self.requests[request_id]
        responder_thread_id = str(payload["responder_thread_id"])
        responder_host_id = str(payload.get("responder_host_id") or "")
        if self.finish_waiting:
            return {
                "status": "waiting_final_callback",
                "terminal": False,
                "page": page,
                "request_id": request_id,
                "responder_thread_id": responder_thread_id,
                "responder_host_id": responder_host_id,
            }
        response: dict[str, object] = {
            "operation": "send_message_to_thread",
            "status": "completed",
            "terminal": True,
            "final_callback_source": "final_callback",
            "page": page,
            "request_id": request_id,
            "responder_thread_id": responder_thread_id,
            "responder_host_id": responder_host_id,
            "result": {
                "responder_thread_id": responder_thread_id,
                "responder_host_id": responder_host_id,
                "responder_turn_id": "",
                "final_answer": self.final_callback_answer,
            },
        }
        self.responses[request_id] = {
            key: value
            for key, value in response.items()
            if key
            not in {
                "terminal",
                "page",
                "responder_thread_id",
                "responder_host_id",
            }
        }
        return dict(response)

    def fail_page(
        self,
        page: str,
        code: str,
        may_have_started: bool,
    ) -> dict[str, object]:
        request_id = self.pages[page]
        existing = self.responses.get(request_id)
        if existing is not None:
            return dict(existing)
        payload = self.requests[request_id]
        operation = self.operations[request_id]
        failure = {
            "page": page,
            "code": code,
            "may_have_started": may_have_started,
        }
        self.failures.append(failure)
        response: dict[str, object] = {
            "operation": operation,
            "status": "failed",
            "terminal": True,
            "page": page,
            "request_id": request_id,
            "responder_thread_id": str(payload.get("responder_thread_id") or ""),
            "responder_host_id": str(
                payload.get("responder_host_id")
                or payload.get("expected_host_id")
                or ""
            ),
            "error": {
                "code": code,
                "message": "test terminal unknown",
                "retryable": False,
                "may_have_started": may_have_started,
            },
        }
        self.responses[request_id] = response
        self.claim_state = "terminal"
        return dict(response)


class BeeperClientContractTests(unittest.TestCase):
    def configured(self, root: Path):
        return replace(
            load_config(),
            project_root=root,
            runtime_dir=root / ".codex" / "feishu-codex-bridge-runtime",
            beeper_timeout_seconds=3,
        )

    def fixture(
        self,
        root: Path,
        runner,
        *,
        activator=None,
    ) -> tuple[BeeperClient, QueueForTests]:
        executable = root / "codex.exe"
        executable.write_bytes(b"fixed current Desktop CLI fixture")
        registration: dict[str, object] = {
            "valid": True,
            "beeper_thread_id": BEEPER_THREAD_ID,
            "beeper_host_id": "host-responder",
            "codex_exe_path": str(executable.resolve()),
            "codex_exe_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
            "codex_version": "codex-cli 0.151.0-alpha.7.2",
        }
        queue = QueueForTests(registration)
        client = BeeperClient(
            self.configured(root),
            queue,  # type: ignore[arg-type]
            registration,
            runner=runner,
            activator=activator or (lambda uri: None),
        )
        return client, queue

    def test_client_uses_only_beeper_queue_queue(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            config = self.configured(Path(temporary))
            client = create_beeper_client(config)
            self.assertIsInstance(client, HistoricalBeeperClient)
            self.assertFalse(client.is_alive())
            self.assertEqual("beeper", client.session_owner)
            self.assertFalse(hasattr(client, "codex_cli"))

    def test_client_is_selected_only_for_valid_exact_metadata(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            root = Path(temporary)
            config = self.configured(root)
            calls: list[list[str]] = []

            def runner(argv, **kwargs):
                del kwargs
                calls.append(list(argv))
                return subprocess.CompletedProcess(argv, 0)

            _, queue = self.fixture(root, runner)
            marker = config.runtime_dir / "beeper" / "registration.json"
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("{}", encoding="utf-8")
            with mock.patch(
                "bridge_core.beeper_client.BeeperQueue",
                return_value=queue,
            ):
                selected = create_beeper_client(config)
            self.assertIsInstance(selected, BeeperClient)
            self.assertTrue(selected.is_alive())
            self.assertEqual([], calls)

            queue._registration["codex_exe_sha256"] = "0" * 64
            with mock.patch(
                "bridge_core.beeper_client.BeeperQueue",
                return_value=queue,
            ):
                rejected = create_beeper_client(config)
            self.assertIs(type(rejected), HistoricalBeeperClient)

    def test_argv_contains_only_fixed_control_and_opaque_page(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            calls: list[tuple[list[str], dict[str, object]]] = []

            def runner(argv, **kwargs):
                calls.append((list(argv), dict(kwargs)))
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    stdout=b"deceptive native Beeper reply must be ignored",
                )

            client, queue = self.fixture(Path(temporary), runner)
            body = "绝密业务正文：请只回复蓝鲸42🚀"
            message_id = "feishu-message-private-1"
            started: list[ResponderTurnHandle] = []
            answer = client.alert_responder(
                {"thread_id": RESPONDER_THREAD_ID, "host_id": "host-responder"},
                "Alice",
                body,
                client_message_id=message_id,
                on_turn_started=started.append,
            )

            self.assertEqual("Final Callback 最终回答🚀", answer.final_answer)
            # The Final Callback capability does not attest a product turn identity.
            self.assertEqual("", answer.responder_turn_id)
            self.assertEqual([ResponderTurnHandle(RESPONDER_THREAD_ID, "")], started)
            request_id = next(iter(queue.responses))
            self.assertEqual(
                "", queue.responses[request_id]["result"]["responder_turn_id"]
            )
            self.assertEqual(1, len(calls))
            argv, kwargs = calls[0]
            self.assertEqual("queue", argv[1])
            self.assertEqual(["--thread", BEEPER_THREAD_ID], argv[2:4])
            self.assertEqual("--message", argv[4])
            self.assertEqual(6, len(argv))
            self.assertTrue(argv[5].endswith(next(iter(queue.pages))))
            self.assertIn("ignore every native reply", argv[5])
            self.assertIn("Never call submit_final_callback in this Beeper", argv[5])
            self.assertIn("Do not", argv[5])
            self.assertNotIn("read_thread(", argv[5])
            joined = "\0".join(argv)
            self.assertNotIn(body, joined)
            self.assertNotIn(RESPONDER_THREAD_ID, joined)
            self.assertNotIn(message_id, joined)
            self.assertIs(kwargs["shell"], False)
            self.assertEqual(15, kwargs["timeout"])
            self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
            self.assertIs(kwargs["stdout"], subprocess.DEVNULL)
            self.assertIs(kwargs["stderr"], subprocess.DEVNULL)

    def test_claimed_beeper_never_invokes_load_assist(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            calls: list[list[str]] = []
            activations: list[str] = []

            def runner(argv, **kwargs):
                del kwargs
                calls.append(list(argv))
                return subprocess.CompletedProcess(argv, 0)

            client, queue = self.fixture(
                Path(temporary),
                runner,
                activator=activations.append,
            )
            answer = client.alert_responder(
                {"thread_id": RESPONDER_THREAD_ID, "host_id": "host-responder"},
                "Alice",
                "Beeper 已加载",
                client_message_id="claimed-without-load-assist",
            )

            self.assertEqual("Final Callback 最终回答🚀", answer.final_answer)
            self.assertEqual(
                "beeper-registered-load-unobserved",
                client.state(),
            )
            queue.status_value = BeeperQueueStatus(
                registered=True,
                pending=2,
                claimed=1,
                dial_generation=4,
                dial_inflight=True,
                dial_lease_remaining_seconds=8.0,
            )
            status = client.status()
            self.assertTrue(status.registered)
            self.assertEqual(2, status.pending)
            self.assertEqual(1, status.claimed)
            self.assertTrue(status.dial_inflight)
            self.assertEqual(8.0, status.dial_lease_remaining_seconds)
            self.assertEqual(
                {
                    "registered",
                    "beeper_thread_id",
                    "beeper_host_id",
                    "pending",
                    "claimed",
                    "dial_generation",
                    "dial_inflight",
                    "dial_lease_remaining_seconds",
                },
                set(status.as_dict()),
            )
            self.assertEqual(1, len(calls))
            self.assertEqual([], activations)
            self.assertEqual(1, len(queue.claim_waits))

    def test_catalog_uses_strict_readonly_lane_without_paths(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            calls: list[list[str]] = []

            def runner(argv, **kwargs):
                del kwargs
                calls.append(list(argv))
                return subprocess.CompletedProcess(argv, 0)

            client, queue = self.fixture(Path(temporary), runner)
            catalog = client.list_task_catalog(
                visible_thread_ids=None,
                include_archived=False,
                request_key="catalog",
                limit=50,
            )

            self.assertEqual("c" * 32, catalog.snapshot_id)
            self.assertGreater(catalog.snapshot_expires_at, time.time())
            self.assertEqual("", catalog.projects[0].root)
            self.assertEqual(RESPONDER_THREAD_ID, catalog.tasks[0].thread_id)
            self.assertEqual("codex", catalog.tasks[0].kind)
            self.assertEqual(SELECTION_PROOF, catalog.tasks[0].selection_proof)
            request_id = next(iter(queue.requests))
            self.assertEqual("list_task_catalog", queue.operations[request_id])
            self.assertEqual(
                {
                    "catalog_version": 1,
                    "visibility": "all",
                    "thread_ids": [],
                    "include_archived": False,
                    "limit": 50,
                    "excluded_thread_ids": [BEEPER_THREAD_ID],
                },
                queue.requests[request_id],
            )
            self.assertEqual(1, len(calls))
            self.assertIn("claim_readonly", calls[0][5])
            self.assertNotIn(RESPONDER_THREAD_ID, "\0".join(calls[0]))

    def test_inspection_is_snapshot_bound_and_readonly(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            calls: list[list[str]] = []

            def runner(argv, **kwargs):
                del kwargs
                calls.append(list(argv))
                return subprocess.CompletedProcess(argv, 0)

            client, queue = self.fixture(Path(temporary), runner)
            activation = client.bind_thread(
                RESPONDER_THREAD_ID,
                "Alice",
                request_key="inspect",
                expected_project_id="project-a",
                expected_host_id="host-responder",
                catalog_snapshot_id="c" * 32,
                selection_proof=SELECTION_PROOF,
            )

            self.assertEqual(RESPONDER_THREAD_ID, activation.responder_thread_id)
            self.assertEqual("host-responder", activation.responder_host_id)
            self.assertEqual("d" * 32, activation.operation_receipt)
            request_id = next(iter(queue.requests))
            self.assertEqual("inspect_thread", queue.operations[request_id])
            self.assertEqual(
                {
                    "responder_thread_id": RESPONDER_THREAD_ID,
                    "display_name": "Alice",
                    "catalog_snapshot_id": "c" * 32,
                    "expected_project_id": "project-a",
                    "expected_host_id": "host-responder",
                    "selection_proof": SELECTION_PROOF,
                    "excluded_thread_ids": [BEEPER_THREAD_ID],
                },
                queue.requests[request_id],
            )
            self.assertEqual(1, len(calls))
            self.assertIn("claim_readonly", calls[0][5])
            self.assertNotIn(RESPONDER_THREAD_ID, "\0".join(calls[0]))
            with self.assertRaises(BeeperError) as invalid_proof:
                client.bind_thread(
                    RESPONDER_THREAD_ID,
                    "Alice",
                    request_key="inspect-uppercase-proof",
                    expected_project_id="project-a",
                    expected_host_id="host-responder",
                    catalog_snapshot_id="c" * 32,
                    selection_proof="E" * 64,
                )
            self.assertEqual("invalid_readonly_request", invalid_proof.exception.code)
            self.assertEqual(1, len(queue.requests))

    def test_readonly_unknown_is_safe_terminal_and_not_retried(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            calls: list[list[str]] = []

            def runner(argv, **kwargs):
                del kwargs
                calls.append(list(argv))
                return subprocess.CompletedProcess(argv, 9)

            client, queue = self.fixture(Path(temporary), runner)
            for _ in range(2):
                with self.assertRaises(BeeperError) as caught:
                    client.list_task_catalog(
                        visible_thread_ids=None,
                        include_archived=False,
                        request_key="catalog-unknown",
                    )
                self.assertFalse(caught.exception.may_have_started)
                self.assertFalse(caught.exception.retryable)
            self.assertEqual(1, len(calls))
            self.assertEqual(1, len(queue.failures))
            self.assertEqual("readonly_result_unknown", queue.failures[0]["code"])
            self.assertFalse(queue.failures[0]["may_have_started"])

    def test_existing_catalog_handoff_failure_never_spawns_or_retries(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            calls: list[list[str]] = []

            def runner(argv, **kwargs):
                del kwargs
                calls.append(list(argv))
                return subprocess.CompletedProcess(argv, 0)

            client, queue = self.fixture(Path(temporary), runner)
            first = client.list_task_catalog(
                visible_thread_ids=None,
                include_archived=False,
                request_key="existing-catalog-handoff",
            )
            self.assertEqual("c" * 32, first.snapshot_id)
            request_id = next(iter(queue.responses))
            queue.finish_readonly_request_error = BeeperQueueProtocolError(
                "synthetic existing catalog handoff failure"
            )

            for _ in range(2):
                with self.assertRaises(BeeperError) as caught:
                    client.list_task_catalog(
                        visible_thread_ids=None,
                        include_archived=False,
                        request_key="existing-catalog-handoff",
                    )
                self.assertEqual("readonly_result_unknown", caught.exception.code)
                self.assertFalse(caught.exception.may_have_started)
                self.assertFalse(caught.exception.retryable)

            self.assertEqual(1, len(calls))
            self.assertEqual(1, len(queue.reserved))
            self.assertEqual(
                [request_id, request_id],
                queue.finish_readonly_request_calls,
            )

    def test_current_terminal_race_marks_started_but_duplicate_receipt_does_not(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            calls: list[list[str]] = []

            def runner(argv, **kwargs):
                del kwargs
                calls.append(list(argv))
                return subprocess.CompletedProcess(argv, 0)

            client, queue = self.fixture(Path(temporary), runner)
            queue.claim_states = ["terminal"]
            first_started: list[ResponderTurnHandle] = []
            second_started: list[ResponderTurnHandle] = []
            session = {"thread_id": RESPONDER_THREAD_ID, "host_id": "host-responder"}

            first = client.alert_responder(
                session,
                "Alice",
                "fast Final Callback",
                client_message_id="terminal-race-message",
                on_turn_started=first_started.append,
            )
            second = client.alert_responder(
                session,
                "Alice",
                "fast Final Callback",
                client_message_id="terminal-race-message",
                on_turn_started=second_started.append,
            )

            self.assertEqual(first, second)
            self.assertEqual([ResponderTurnHandle(RESPONDER_THREAD_ID, "")], first_started)
            self.assertEqual([], second_started)
            self.assertEqual(1, len(calls))

    def test_reserved_beeper_loads_exact_uri_once_without_requeue(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            calls: list[list[str]] = []
            activations: list[str] = []

            def runner(argv, **kwargs):
                del kwargs
                calls.append(list(argv))
                return subprocess.CompletedProcess(argv, 0)

            client, queue = self.fixture(
                Path(temporary),
                runner,
                activator=activations.append,
            )
            queue.claim_states = ["reserved", "claimed_armed"]
            session = {"thread_id": RESPONDER_THREAD_ID, "host_id": "host-responder"}
            body = "不得进入 deep link 的业务正文🚀"
            message_id = "cold-load-feishu-message"

            first = client.alert_responder(
                session,
                "Alice",
                body,
                client_message_id=message_id,
            )
            second = client.alert_responder(
                session,
                "Alice",
                body,
                client_message_id=message_id,
            )

            self.assertEqual(first, second)
            self.assertEqual(1, len(calls))
            self.assertEqual(
                [f"codex://threads/{BEEPER_THREAD_ID}"],
                activations,
            )
            self.assertNotIn(body, activations[0])
            self.assertNotIn(RESPONDER_THREAD_ID, activations[0])
            self.assertNotIn(message_id, activations[0])
            self.assertNotIn(next(iter(queue.pages)), activations[0])
            self.assertNotIn("?", activations[0])
            self.assertEqual(
                [
                    BEEPER_LOAD_GRACE_SECONDS,
                    BEEPER_CLAIM_WAIT_MAX_SECONDS,
                ],
                queue.claim_waits,
            )
            self.assertEqual(
                BEEPER_CLAIM_WAIT_MAX_SECONDS,
                BEEPER_POST_LOAD_CLAIM_SECONDS,
            )

    def test_load_assist_failure_is_safe_and_terminal(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            calls: list[list[str]] = []
            activations: list[str] = []

            def runner(argv, **kwargs):
                del kwargs
                calls.append(list(argv))
                return subprocess.CompletedProcess(argv, 0)

            def fail_activation(uri: str) -> None:
                activations.append(uri)
                raise OSError("synthetic deep-link failure")

            client, queue = self.fixture(
                Path(temporary),
                runner,
                activator=fail_activation,
            )
            queue.claim_states = ["reserved"]
            with self.assertRaises(BeeperNotLoaded) as caught:
                client.alert_responder(
                    {"thread_id": RESPONDER_THREAD_ID, "host_id": "host-responder"},
                    "Alice",
                    "加载助手失败不可重放",
                    client_message_id="load-assist-failed",
                )

            self.assertEqual("beeper_load_assist_failed", caught.exception.code)
            self.assertFalse(caught.exception.may_have_started)
            self.assertFalse(caught.exception.retryable)
            self.assertEqual(1, len(calls))
            self.assertEqual([f"codex://threads/{BEEPER_THREAD_ID}"], activations)
            self.assertEqual(
                [{"page": next(iter(queue.pages)), "code": "beeper_load_assist_failed"}],
                queue.unclaimed_failures,
            )
            self.assertEqual(1, len(queue.failures))
            self.assertFalse(queue.failures[0]["may_have_started"])

    def test_post_load_claim_timeout_is_safe_and_terminal(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            calls: list[list[str]] = []
            activations: list[str] = []

            def runner(argv, **kwargs):
                del kwargs
                calls.append(list(argv))
                return subprocess.CompletedProcess(argv, 0)

            client, queue = self.fixture(
                Path(temporary),
                runner,
                activator=activations.append,
            )
            queue.claim_states = ["reserved", "reserved"]
            with self.assertRaises(BeeperNotLoaded) as caught:
                client.alert_responder(
                    {"thread_id": RESPONDER_THREAD_ID, "host_id": "host-responder"},
                    "Alice",
                    "Beeper 在加载后仍未 claim",
                    client_message_id="post-load-claim-timeout",
                )

            self.assertEqual("beeper_claim_timeout", caught.exception.code)
            self.assertFalse(caught.exception.may_have_started)
            self.assertFalse(caught.exception.retryable)
            self.assertEqual(1, len(calls))
            self.assertEqual([f"codex://threads/{BEEPER_THREAD_ID}"], activations)
            self.assertEqual(
                [
                    BEEPER_LOAD_GRACE_SECONDS,
                    BEEPER_CLAIM_WAIT_MAX_SECONDS,
                ],
                queue.claim_waits,
            )
            self.assertEqual(1, len(queue.failures))
            self.assertEqual("beeper_claim_timeout", queue.failures[0]["code"])
            self.assertFalse(queue.failures[0]["may_have_started"])
            with self.assertRaises(BeeperNotLoaded) as duplicate:
                client.alert_responder(
                    {"thread_id": RESPONDER_THREAD_ID, "host_id": "host-responder"},
                    "Alice",
                    "Beeper 在加载后仍未 claim",
                    client_message_id="post-load-claim-timeout",
                )
            self.assertEqual("beeper_claim_timeout", duplicate.exception.code)
            self.assertEqual(1, len(calls))
            self.assertEqual(1, len(activations))

    def test_unsafe_terminal_is_never_downgraded_to_not_loaded(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            client, _queue = self.fixture(
                Path(temporary),
                lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0),
            )
            request_id = "a" * 32
            payload = {
                "responder_thread_id": RESPONDER_THREAD_ID,
                "responder_host_id": "host-responder",
                "prompt": "unsafe terminal",
            }
            response = {
                "operation": "send_message_to_thread",
                "status": "failed",
                "request_id": request_id,
                "responder_thread_id": RESPONDER_THREAD_ID,
                "responder_host_id": "host-responder",
                "error": {
                    "code": "responder_result_unknown",
                    "retryable": False,
                    "may_have_started": True,
                },
            }
            with self.assertRaises(ResponderOutcomeUnknown) as caught:
                client._decoded_terminal(
                    response,
                    request_id=request_id,
                    payload=payload,
                )
            self.assertTrue(caught.exception.may_have_started)

    def test_same_request_never_spawns_twice(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            calls: list[list[str]] = []

            def runner(argv, **kwargs):
                del kwargs
                calls.append(list(argv))
                return subprocess.CompletedProcess(argv, 0)

            client, queue = self.fixture(Path(temporary), runner)
            session = {"thread_id": RESPONDER_THREAD_ID, "host_id": "host-responder"}
            first = client.alert_responder(
                session,
                "Alice",
                "同一个请求",
                client_message_id="same-feishu-request",
            )
            second = client.alert_responder(
                session,
                "Alice",
                "同一个请求",
                client_message_id="same-feishu-request",
            )
            self.assertEqual(first, second)
            self.assertEqual(1, len(calls))
            self.assertEqual(1, len(queue.reserved))

    def test_unknown_spawn_outcomes_are_terminal_and_not_retried(self) -> None:
        outcomes = (
            ("nonzero", 9),
            (
                "timeout",
                subprocess.TimeoutExpired(cmd=["codex.exe", "queue"], timeout=15),
            ),
            ("exception", OSError("synthetic process failure")),
        )
        for label, outcome in outcomes:
            with self.subTest(outcome=label):
                with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
                    calls: list[list[str]] = []

                    def runner(argv, **kwargs):
                        del kwargs
                        calls.append(list(argv))
                        if isinstance(outcome, int):
                            return subprocess.CompletedProcess(argv, outcome)
                        raise outcome

                    client, queue = self.fixture(Path(temporary), runner)
                    session = {
                        "thread_id": RESPONDER_THREAD_ID,
                        "host_id": "host-responder",
                    }
                    for _ in range(2):
                        with self.assertRaises(ResponderOutcomeUnknown) as caught:
                            client.alert_responder(
                                session,
                                "Alice",
                                "绝不重复的业务请求",
                                client_message_id=f"unknown-{label}",
                            )
                        self.assertTrue(caught.exception.may_have_started)
                        self.assertFalse(caught.exception.retryable)
                    self.assertEqual(1, len(calls))
                    self.assertEqual(1, len(queue.failures))
                    self.assertEqual("responder_result_unknown", queue.failures[0]["code"])
                    self.assertIs(queue.failures[0]["may_have_started"], True)

    def test_final_callback_timeout_is_terminal_and_not_retried(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            calls: list[list[str]] = []

            def runner(argv, **kwargs):
                del kwargs
                calls.append(list(argv))
                return subprocess.CompletedProcess(argv, 0)

            client, queue = self.fixture(Path(temporary), runner)
            queue.finish_waiting = True
            session = {"thread_id": RESPONDER_THREAD_ID, "host_id": "host-responder"}
            for _ in range(2):
                with self.assertRaises(ResponderOutcomeUnknown) as caught:
                    client.alert_responder(
                        session,
                        "Alice",
                        "Final Callback 没有按时到达",
                        client_message_id="waiting-final-callback-request",
                    )
                self.assertTrue(caught.exception.may_have_started)
                self.assertFalse(caught.exception.retryable)
            self.assertEqual(1, len(calls))
            self.assertEqual(1, len(queue.failures))
            self.assertEqual("responder_result_unknown", queue.failures[0]["code"])
            self.assertTrue(queue.failures[0]["may_have_started"])

    def test_unbound_message_is_rejected_before_queue_submission(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            calls: list[list[str]] = []

            def runner(argv, **kwargs):
                del kwargs
                calls.append(list(argv))
                return subprocess.CompletedProcess(argv, 0)

            client, queue = self.fixture(Path(temporary), runner)
            with self.assertRaises(ResponderNotBound):
                client.alert_responder({}, "测试会话", "你好", client_message_id="message-1")
            self.assertEqual({}, queue.requests)
            self.assertEqual([], calls)

    def test_steer_is_fail_closed_without_queue_submission(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            calls: list[list[str]] = []

            def runner(argv, **kwargs):
                del kwargs
                calls.append(list(argv))
                return subprocess.CompletedProcess(argv, 0)

            client, queue = self.fixture(Path(temporary), runner)
            with self.assertRaises(BeeperError) as caught:
                client.steer(
                    ResponderTurnHandle(RESPONDER_THREAD_ID, "a" * 32),
                    "追加约束",
                    request_key="event-steer-disabled",
                )
            self.assertIn("no fenced in-flight steer lane", str(caught.exception))
            self.assertEqual({}, queue.requests)
            self.assertEqual([], calls)

    def test_thread_id_validation_accepts_current_id_shapes(self) -> None:
        self.assertTrue(looks_like_thread_id(RESPONDER_THREAD_ID))
        self.assertTrue(looks_like_thread_id("thr_1234567890abcdefghijklmnop"))
        self.assertFalse(looks_like_thread_id("hello"))
        self.assertFalse(looks_like_thread_id("../../not-a-thread"))

    def test_completed_responder_identity_must_exactly_match_the_request(self) -> None:
        with self.assertRaises(BeeperError) as read_only:
            HistoricalBeeperClient._thread_result(
                {"thread_id": DISPLACED_THREAD_ID},
                expected_thread_id=RESPONDER_THREAD_ID,
                outcome_may_have_started=False,
            )
        self.assertFalse(read_only.exception.may_have_started)

        with self.assertRaises(ResponderOutcomeUnknown):
            HistoricalBeeperClient._thread_result(
                {"thread_id": DISPLACED_THREAD_ID},
                expected_thread_id=RESPONDER_THREAD_ID,
                outcome_may_have_started=True,
            )

    def test_malformed_completed_mutation_result_is_unknown(self) -> None:
        with self.assertRaises(ResponderOutcomeUnknown):
            HistoricalBeeperClient._result(
                {"status": "completed", "result": None},
                completed_may_have_started=True,
            )
        with self.assertRaises(ResponderOutcomeUnknown):
            HistoricalBeeperClient._result(
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

    def test_completed_send_requires_top_level_final_callback_source(self) -> None:
        result = {
            "responder_thread_id": RESPONDER_THREAD_ID,
            "responder_turn_id": "",
            "final_answer": "must not leak through a non-Final Callback receipt",
        }
        for source in (None, "", "hook", "native", "unknown", "not_applicable"):
            with self.subTest(source=source):
                response = {
                    "status": "completed",
                    "operation": "send_message_to_thread",
                    "result": result,
                }
                if source is not None:
                    response["final_callback_source"] = source
                with self.assertRaises(ResponderOutcomeUnknown) as caught:
                    HistoricalBeeperClient._result(
                        response,
                        completed_may_have_started=True,
                        expected_operation="send_message_to_thread",
                    )
                self.assertTrue(caught.exception.may_have_started)

        self.assertEqual(
            result,
            HistoricalBeeperClient._result(
                {
                    "status": "completed",
                    "operation": "send_message_to_thread",
                    "final_callback_source": "final_callback",
                    "result": result,
                },
                completed_may_have_started=True,
                expected_operation="send_message_to_thread",
            ),
        )

    def test_failed_terminal_operation_mismatch_is_rejected_before_interpretation(self) -> None:
        with self.assertRaises(ResponderOutcomeUnknown) as caught:
            HistoricalBeeperClient._result(
                {
                    "status": "failed",
                    "operation": "inspect_thread",
                    "error": {
                        "code": "invalid_beeper_result",
                        "message": "wrong operation receipt",
                        "retryable": False,
                        "may_have_started": False,
                    },
                },
                completed_may_have_started=True,
                expected_operation="send_message_to_thread",
            )
        self.assertTrue(caught.exception.may_have_started)

    def test_archived_or_missing_responder_is_typed_and_never_beeper_retried(self) -> None:
        for code in ("responder_archived", "responder_not_found"):
            with self.subTest(code=code):
                with self.assertRaises(ResponderUnavailable) as caught:
                    HistoricalBeeperClient._result(
                        {
                            "status": "failed",
                            "error": {
                                "code": code,
                                "message": "responder ended before delivery",
                                "retryable": True,
                                "may_have_started": False,
                            },
                        }
                    )
                self.assertEqual(code, caught.exception.code)
                self.assertFalse(caught.exception.retryable)
                self.assertFalse(caught.exception.may_have_started)

    def test_unknown_delivery_takes_precedence_over_unavailable_responder_code(self) -> None:
        with self.assertRaises(ResponderOutcomeUnknown):
            HistoricalBeeperClient._result(
                {
                    "status": "failed",
                    "error": {
                        "code": "responder_archived",
                        "message": "send outcome is unknown",
                        "retryable": False,
                        "may_have_started": True,
                    },
                }
            )

        with self.assertRaises(ResponderOutcomeUnknown):
            HistoricalBeeperClient._result(
                {
                    "status": "failed",
                    "error": {
                        "code": "beeper_offline",
                        "message": "offline response conflicts with a started mutation",
                        "retryable": True,
                        "may_have_started": True,
                    },
                }
            )

    def test_terminal_beeper_codes_ignore_retryable_flag(self) -> None:
        for code in ("responder_tool_unavailable", "project_not_registered"):
            with self.subTest(code=code):
                with self.assertRaises(BeeperError) as caught:
                    HistoricalBeeperClient._result(
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

    def test_message_uses_send_message_to_thread_and_preserves_transport_manifest(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            root = Path(temporary)
            calls: list[tuple[list[str], dict[str, object]]] = []

            def runner(argv, **kwargs):
                calls.append((list(argv), dict(kwargs)))
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    stdout=b"native Beeper answer must stay ignored",
                )

            client, queue = self.fixture(root, runner)
            image = root / "image.png"
            image.write_bytes(b"png")
            queue.final_callback_answer = " \tResponder 最终回答🚀\r\n第二行尾部  \n"
            handles: list[ResponderTurnHandle] = []
            answer = client.alert_responder(
                {"thread_id": RESPONDER_THREAD_ID, "host_id": "host-responder"},
                "Alice",
                "原始问题",
                client_message_id="message-1",
                local_images=[image],
                additional_context={"transport_attachments": "类型：file"},
                on_turn_started=handles.append,
            )
            request_id = next(iter(queue.requests))
            request = queue.requests[request_id]
            self.assertEqual("send_message_to_thread", queue.operations[request_id])
            self.assertEqual(RESPONDER_THREAD_ID, request["responder_thread_id"])
            self.assertTrue(str(request["prompt"]).startswith("原始问题"))
            self.assertIn(str(image.resolve()), str(request["prompt"]))
            self.assertIn("类型：file", str(request["prompt"]))
            self.assertEqual(" \tResponder 最终回答🚀\r\n第二行尾部  \n", answer.final_answer)
            self.assertEqual([ResponderTurnHandle(RESPONDER_THREAD_ID, "")], handles)
            self.assertEqual("final_callback", queue.responses[request_id]["final_callback_source"])
            self.assertEqual(
                "", queue.responses[request_id]["result"]["responder_turn_id"]
            )
            self.assertEqual(1, len(calls))
            self.assertIs(calls[0][1]["stdout"], subprocess.DEVNULL)

    def test_unknown_context_keys_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            calls: list[list[str]] = []

            def runner(argv, **kwargs):
                del kwargs
                calls.append(list(argv))
                return subprocess.CompletedProcess(argv, 0)

            client, queue = self.fixture(Path(temporary), runner)
            with self.assertRaises(BeeperError):
                client.alert_responder(
                    {"thread_id": RESPONDER_THREAD_ID, "host_id": "host-responder"},
                    "Alice",
                    "问题",
                    client_message_id="message-context",
                    additional_context={"rag_context": "synthetic context"},
                )
            self.assertEqual({}, queue.requests)
            self.assertEqual([], calls)


if __name__ == "__main__":
    unittest.main()
