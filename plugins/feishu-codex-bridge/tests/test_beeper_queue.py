from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP_ROOT = Path(os.environ.get("FEISHU_BRIDGE_TEST_TMP", tempfile.gettempdir()))
if "FEISHU_BRIDGE_TEST_TMP" not in os.environ:
    TEST_TEMP_ROOT = TEST_TEMP_ROOT / "feishu-codex-bridge-tests"
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from bridge_core.beeper_queue import (  # noqa: E402
    BeeperQueue,
    BeeperQueueProtocolError,
    _read_exact_final_answer,
)
from bridge_core.legacy_identifiers import RETIRED_QUEUE_ROOT_NAME  # noqa: E402
from beeper_queue_cli import (  # noqa: E402
    QUEUE_NAMESPACE,
    _emit,
    _minimal_claim,
    _read_final_callback_submission,
    _runtime_settings,
    main as beeper_queue_cli_main,
)


BEEPER_THREAD_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
OTHER_BEEPER_ID = "ffffffff-1111-2222-3333-444444444444"
RESPONDER_THREAD_ID = "11111111-2222-3333-4444-555555555555"
RESPONDER_TURN_ID = "77777777-8888-9999-aaaa-bbbbbbbbbbbb"
OTHER_TURN_ID = "cccccccc-dddd-eeee-ffff-000000000000"


class BeeperQueueTests(unittest.TestCase):
    @staticmethod
    def final_callback_envelope(wrapped_prompt: str) -> dict[str, str]:
        marker = "Envelope: "
        if marker not in wrapped_prompt:
            raise AssertionError("Final Callback prompt has no JSON envelope")
        envelope = json.loads(wrapped_prompt.split(marker, 1)[1])
        if not isinstance(envelope, dict):
            raise AssertionError("Final Callback envelope is not an object")
        return envelope

    def queue(
        self,
        runtime_dir: Path,
        **queue_options,
    ) -> tuple[BeeperQueue, Path, str]:
        executable = runtime_dir / "codex.exe"
        executable.write_bytes(b"codex-cli")
        digest = hashlib.sha256(executable.read_bytes()).hexdigest()
        queue = BeeperQueue(
            runtime_dir,
            root_name="beeper",
            **queue_options,
        )
        queue.register(
            BEEPER_THREAD_ID,
            "host-one",
            str(executable),
            digest,
            "codex-cli 0.test",
        )
        return queue, executable, digest

    def claim(self, queue: BeeperQueue, dial: dict):
        claim = queue.claim_and_arm(dial)
        envelope = self.final_callback_envelope(str(claim["prompt"]))
        dial["_test_final_callback_capability"] = envelope["final_callback_capability"]
        request = json.loads(
            queue._path(queue.claimed_dir, str(dial["request_id"])).read_text(
                encoding="utf-8"
            )
        )
        with queue._dial_session() as connection:
            state = connection.execute(
                "SELECT * FROM dial_state WHERE singleton=1"
            ).fetchone()
            page = connection.execute(
                "SELECT * FROM pages WHERE page_id=?",
                (str(dial["page_id"]),),
            ).fetchone()
        self.assertTrue(
            queue._claim_matches_live_dial(
                request,
                state,
                page,
                time.time(),
                expected_request_id=str(request["request_id"]),
            ),
            {
                "request": request,
                "state": dict(state) if state is not None else None,
                "page": dict(page) if page is not None else None,
            },
        )
        return request

    def capture_final_callback(
        self,
        queue: BeeperQueue,
        dial: dict,
        request_id: str,
        prompt: str,
        final: str,
        *,
        turn_id: str = RESPONDER_TURN_ID,
    ) -> None:
        del prompt, turn_id
        final_callback_capability = str(dial.pop("_test_final_callback_capability"))
        self.assertEqual(
            {"accepted": True, "state": "captured"},
            queue.submit_final_callback(final_callback_capability, final),
        )

    def claim_catalog(
        self,
        queue: BeeperQueue,
        *,
        idempotency_key: str,
    ) -> tuple[str, dict, dict, dict]:
        request_id = queue.submit(
            "list_task_catalog",
            {
                "catalog_version": 1,
                "visibility": "all",
                "thread_ids": [],
                "include_archived": False,
                "limit": 50,
                "excluded_thread_ids": list(
                    queue.excluded_thread_ids()
                ),
            },
            idempotency_key=idempotency_key,
        )
        reservation = queue.reserve_exact(request_id)
        claimed = queue.claim_readonly(reservation)
        snapshot_id = claimed["request"]["snapshot_id"]
        result = {
            "catalog_version": 1,
            "snapshot_id": snapshot_id,
            "include_archived": False,
            "truncated": False,
            "projects": [
                {
                    "project_id": "project-one",
                    "label": "Bridge 项目",
                    "host_id": "host-one",
                    "kind": "local",
                }
            ],
            "tasks": [
                {
                    "thread_id": RESPONDER_THREAD_ID,
                    "title": "业务任务",
                    "project_id": "project-one",
                    "host_id": "host-one",
                    "kind": "codex",
                    "status": "idle",
                    "archived": False,
                    "updated_at": 10.0,
                }
            ],
        }
        return request_id, reservation, claimed, result

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

    def test_queue_helper_operational_failure_is_one_answer_free_ascii_object(self) -> None:
        secret_path = "sensitive-local-staging-location"
        for error, expected_message in (
            (OSError(secret_path), "local helper operation failed"),
            (BeeperQueueProtocolError(secret_path), "local helper request rejected"),
        ):
            with self.subTest(error_type=type(error).__name__):
                class FailingQueue:
                    @staticmethod
                    def registration():
                        raise error

                output = io.StringIO()
                with patch(
                    "beeper_queue_cli._queue", return_value=FailingQueue()
                ), redirect_stdout(output):
                    exit_code = beeper_queue_cli_main(
                        [
                            "--queue-namespace",
                            QUEUE_NAMESPACE,
                            "registration",
                        ]
                    )

                wire = output.getvalue()
                self.assertEqual(2, exit_code)
                self.assertTrue(wire.isascii())
                self.assertEqual(1, len(wire.splitlines()))
                self.assertEqual(
                    {"ok": False, "error": expected_message},
                    json.loads(wire),
                )
                self.assertNotIn(secret_path, wire)

    def test_helper_requires_explicit_isolated_namespace(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = beeper_queue_cli_main(["registration"])
        self.assertEqual(2, exit_code)
        self.assertEqual(
            {
                "ok": False,
                "error": "local helper request rejected",
            },
            json.loads(output.getvalue()),
        )

    def test_helper_routes_only_bounded_controller_commands(self) -> None:
        page = "a" * 32

        class FakeQueue:
            def __init__(self) -> None:
                self.calls: list[tuple[str, tuple[object, ...]]] = []

            def register(self, *arguments):
                self.calls.append(("register", arguments))
                return {"registered": True}

            def registration(self):
                self.calls.append(("status", ()))
                return {"valid": True}

            def claim_and_arm(self, *arguments):
                self.calls.append(("claim", arguments))
                return {
                    "status": "claimed_armed",
                    "responder_thread_id": RESPONDER_THREAD_ID,
                    "responder_host_id": "responder-host",
                    "prompt": "精确提示🙂",
                }

            def submit_final_callback(self, *arguments):
                self.calls.append(("submit", arguments))
                return {"accepted": True, "state": "captured"}

            def finish_final_callback(self, *arguments):
                self.calls.append(("finish", arguments))
                return {"status": "completed", "terminal": True}

            def fail_page(self, *arguments):
                self.calls.append(("fail", arguments))
                return {"status": "failed", "terminal": True}

        fake = FakeQueue()
        prefix = ["--queue-namespace", QUEUE_NAMESPACE]
        invocations = (
            (
                [
                    *prefix,
                    "register",
                    "--beeper-thread-id",
                    BEEPER_THREAD_ID,
                    "--beeper-host-id",
                    "host-one",
                    "--codex-exe-path",
                    str(TEST_TEMP_ROOT / "Codex" / "codex.exe"),
                    "--codex-exe-sha256",
                    "b" * 64,
                    "--codex-version",
                    "0.150.1",
                ],
                "register",
            ),
            ([*prefix, "registration"], "status"),
            (
                [*prefix, "claim-and-arm", "--page", page],
                "claim",
            ),
            (
                [*prefix, "submit-final-callback"],
                "submit",
            ),
            (
                [
                    *prefix,
                    "finish-final-callback",
                    "--page",
                    page,
                    "--wait-seconds",
                    "30",
                ],
                "finish",
            ),
            (
                [
                    *prefix,
                    "fail-page",
                    "--page",
                    page,
                    "--code",
                    "responder_result_unknown",
                    "--may-have-started",
                ],
                "fail",
            ),
        )
        with (
            patch("beeper_queue_cli._queue", return_value=fake) as queue_factory,
            patch(
                "beeper_queue_cli._read_final_callback_submission",
                return_value={
                    "final_callback_capability": "c" * 32,
                    "final_answer": "精确 final 🙂",
                },
            ),
        ):
            for arguments, expected_call in invocations:
                with self.subTest(command=expected_call):
                    before = len(fake.calls)
                    output = io.StringIO()
                    with redirect_stdout(output):
                        self.assertEqual(0, beeper_queue_cli_main(arguments))
                    self.assertTrue(output.getvalue().isascii())
                    result = json.loads(output.getvalue())
                    self.assertTrue(result["ok"])
                    if expected_call == "claim":
                        self.assertEqual(
                            {
                                "ok",
                                "status",
                                "responder_thread_id",
                                "responder_host_id",
                                "prompt",
                            },
                            set(result),
                        )
                    elif expected_call == "submit":
                        self.assertEqual({"ok", "accepted", "state"}, set(result))
                    elif expected_call in {"finish", "fail"}:
                        self.assertEqual({"ok", "status", "terminal"}, set(result))
                    observed = [name for name, _ in fake.calls[before:]]
                    self.assertEqual(
                        ["register", "status"]
                        if expected_call == "register"
                        else [expected_call],
                        observed,
                    )
            self.assertTrue(
                all(
                    item.kwargs.get("root_name") is None
                    for item in queue_factory.call_args_list
                )
            )

        self.assertEqual(
            (
                BEEPER_THREAD_ID,
                "host-one",
                str(TEST_TEMP_ROOT / "Codex" / "codex.exe"),
                "b" * 64,
                "0.150.1",
            ),
            fake.calls[0][1],
        )
        self.assertEqual(("c" * 32, "精确 final 🙂"), fake.calls[4][1])
        self.assertEqual((page, 30), fake.calls[5][1])
        self.assertEqual(
            (page, "responder_result_unknown", True),
            fake.calls[6][1],
        )

    def test_final_callback_submission_is_fixed_to_namespace(self) -> None:
        class FakeQueue:
            @staticmethod
            def submit_final_callback(final_callback_capability, final_answer):
                if final_callback_capability != "d" * 32 or final_answer != "精确回传🚀":
                    raise AssertionError("callback payload changed")
                return {"accepted": True, "state": "captured"}

        output = io.StringIO()
        with (
            patch("beeper_queue_cli._queue", return_value=FakeQueue()) as queue_factory,
            patch(
                "beeper_queue_cli._read_final_callback_submission",
                return_value={
                    "final_callback_capability": "d" * 32,
                    "final_answer": "精确回传🚀",
                },
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(
                0,
                beeper_queue_cli_main(
                    [
                        "--queue-namespace",
                        QUEUE_NAMESPACE,
                        "submit-final-callback",
                    ]
                ),
            )
        self.assertEqual(
            QUEUE_NAMESPACE,
            queue_factory.call_args.args[0].queue_namespace,
        )
        self.assertEqual("captured", json.loads(output.getvalue())["state"])

    def test_final_callback_submission_reader_preserves_exact_unicode(self) -> None:
        payload = {
            "final_callback_capability": "e" * 32,
            "final_answer": " 前导🚀\r\n第二行 🙂 ",
        }
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        class Buffer:
            def read(self, _limit):
                return encoded

        with patch.object(sys, "stdin", type("Input", (), {"buffer": Buffer()})()):
            self.assertEqual(payload, _read_final_callback_submission())

    def test_minimal_claim_preserves_supported_non_uuid_responder_id(self) -> None:
        responder_thread_id = "thr_1234567890abcdefghijklmnop"
        self.assertEqual(
            {
                "ok": True,
                "status": "claimed_armed",
                "responder_thread_id": responder_thread_id,
                "responder_host_id": "responder-host",
                "prompt": "wrapped prompt",
            },
            _minimal_claim(
                {
                    "status": "claimed_armed",
                    "responder_thread_id": responder_thread_id,
                    "responder_host_id": "responder-host",
                    "prompt": "wrapped prompt",
                }
            ),
        )

    def test_final_callback_rejects_oversize_instead_of_publishing_partial_text(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue = BeeperQueue(Path(temporary))
            with self.assertRaises(BeeperQueueProtocolError):
                queue._bounded_final_answer("答" * 12_001)

    def test_final_callback_roundtrip_preserves_exact_unicode(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue, _, _ = self.queue(Path(temporary))
            prompt = "你好，你是谁？——请保留中文标点与 emoji 🙂"
            final = " 最终回答：你好！\r\n我是 Codex。✅ "
            request_id = queue.submit(
                "send_message_to_thread",
                {"responder_thread_id": RESPONDER_THREAD_ID, "prompt": prompt},
                idempotency_key="event-final-callback-unicode",
            )
            dial = queue.reserve_exact(request_id)
            self.claim(queue, dial)
            self.capture_final_callback(queue, dial, request_id, prompt, final)

            self.assertEqual(
                final,
                _read_exact_final_answer(
                    queue.stage_path(request_id, dial["fence_token"])
                ),
            )
            completion = queue.complete(
                request_id,
                {
                    "responder_thread_id": RESPONDER_THREAD_ID,
                    "responder_host_id": "host-one",
                    "responder_turn_id": "",
                    "final_answer": final,
                },
                fence_token=dial["fence_token"],
            )
            self.assertEqual({"final_callback_source": "final_callback"}, completion)
            with queue._dial_session() as connection:
                terminal = connection.execute(
                    """
                    SELECT state, resolution_source, prompt_sha256,
                           answer_sha256, answer_chars
                    FROM final_callback_receipts WHERE request_id=?
                    """,
                    (request_id,),
                ).fetchone()
            self.assertEqual("completed", terminal["state"])
            self.assertEqual("final_callback", terminal["resolution_source"])
            self.assertEqual("", terminal["prompt_sha256"])
            self.assertEqual("", terminal["answer_sha256"])
            self.assertEqual(0, terminal["answer_chars"])
            response = queue.response(request_id)
            self.assertEqual("final_callback", response["final_callback_source"])

    def test_retired_hook_mutations_are_fixed_ignored_before_database_access(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue = BeeperQueue(Path(temporary))
            with patch.object(
                queue,
                "_dial_session",
                side_effect=AssertionError("retired Hook path accessed database"),
            ):
                self.assertEqual(
                    {"accepted": False, "state": "ignored"},
                    queue.bind_final_callback_prompt(
                        RESPONDER_THREAD_ID,
                        RESPONDER_TURN_ID,
                        "retired prompt",
                    ),
                )
                self.assertEqual(
                    {"accepted": False, "state": "ignored"},
                    queue.capture_final_callback(
                        RESPONDER_THREAD_ID,
                        RESPONDER_TURN_ID,
                        "retired answer",
                        stop_hook_active=True,
                    ),
                )

    def test_final_callback_resolution_source_migrates_legacy_database(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            runtime_dir = Path(temporary)
            beeper_root = runtime_dir / RETIRED_QUEUE_ROOT_NAME
            beeper_root.mkdir(parents=True)
            connection = sqlite3.connect(beeper_root / "dial.sqlite3")
            try:
                connection.execute(
                    """
                    CREATE TABLE final_callback_receipts (
                        request_id TEXT PRIMARY KEY,
                        fence_token TEXT NOT NULL,
                        thread_id TEXT NOT NULL,
                        beeper_thread_id TEXT NOT NULL DEFAULT '',
                        prompt_sha256 TEXT NOT NULL,
                        state TEXT NOT NULL,
                        session_id TEXT NOT NULL DEFAULT '',
                        turn_id TEXT NOT NULL DEFAULT '',
                        prompt_hook_seen INTEGER NOT NULL DEFAULT 0,
                        prompt_hook_turn_id TEXT NOT NULL DEFAULT '',
                        prompt_match_mode TEXT NOT NULL DEFAULT '',
                        prompt_hook_rejection TEXT NOT NULL DEFAULT '',
                        answer_sha256 TEXT NOT NULL DEFAULT '',
                        answer_chars INTEGER NOT NULL DEFAULT 0,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL,
                        expires_at REAL NOT NULL
                    )
                    """
                )
                connection.commit()
            finally:
                connection.close()
            queue = BeeperQueue(runtime_dir)
            with queue._dial_session() as connection:
                columns = {
                    str(row[1]): str(row[4] or "")
                    for row in connection.execute(
                        "PRAGMA table_info(final_callback_receipts)"
                    ).fetchall()
                }
            self.assertIn("resolution_source", columns)
            self.assertEqual("''", columns["resolution_source"])

    def test_final_callback_migration_scrubs_terminal_integrity_material(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            runtime_dir = Path(temporary)
            first = BeeperQueue(runtime_dir)
            with first._dial_session() as connection:
                connection.execute(
                    """
                    INSERT INTO final_callback_receipts(
                        request_id, fence_token, thread_id, prompt_sha256, state,
                        answer_sha256, answer_chars, resolution_source,
                        created_at, updated_at, expires_at
                    ) VALUES(?, ?, ?, ?, 'completed', ?, ?, 'hook', 1, 2, 3)
                    """,
                    (
                        "f" * 64,
                        "1" * 64,
                        RESPONDER_THREAD_ID,
                        "2" * 64,
                        "3" * 64,
                        7,
                    ),
                )
            reopened = BeeperQueue(runtime_dir)
            with reopened._dial_session() as connection:
                row = connection.execute(
                    """
                    SELECT prompt_sha256, answer_sha256, answer_chars,
                           resolution_source
                    FROM final_callback_receipts WHERE request_id=?
                    """,
                    ("f" * 64,),
                ).fetchone()
            self.assertEqual("", row["prompt_sha256"])
            self.assertEqual("", row["answer_sha256"])
            self.assertEqual(0, row["answer_chars"])
            self.assertEqual("hook", row["resolution_source"])

    def test_startup_reconciles_authoritative_receipt_after_scrub_crash_window(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            runtime = Path(temporary)
            queue, _, _ = self.queue(runtime)
            prompt = "精确问题"
            request_id = queue.submit(
                "send_message_to_thread",
                {"responder_thread_id": RESPONDER_THREAD_ID, "prompt": prompt},
                idempotency_key="event-terminal-reconcile",
            )
            dial = queue.reserve_exact(request_id)
            self.claim(queue, dial)
            self.capture_final_callback(
                queue, dial, request_id, prompt, " 保留首尾空白 "
            )
            queue._seal_current_final_callback(
                request_id,
                dial["fence_token"],
                {
                    "responder_thread_id": RESPONDER_THREAD_ID,
                    "responder_host_id": "",
                    "responder_turn_id": "",
                    "final_answer": " 保留首尾空白 ",
                },
            )
            self.assertTrue(
                queue._finalize_response(
                    request_id,
                    {
                        "schema_version": 1,
                        "request_id": request_id,
                        "operation": "send_message_to_thread",
                        "status": "completed",
                        "final_callback_source": "final_callback",
                        "result": {
                            "responder_thread_id": RESPONDER_THREAD_ID,
                            "responder_host_id": "",
                            "responder_turn_id": "",
                            "final_answer": " 保留首尾空白 ",
                        },
                        "completed_at": 1,
                    },
                )
            )
            with queue._dial_session() as connection:
                before = connection.execute(
                    "SELECT state, answer_sha256 FROM final_callback_receipts "
                    "WHERE request_id=?",
                    (request_id,),
                ).fetchone()
            self.assertEqual("completing", before["state"])
            self.assertTrue(before["answer_sha256"])

            recovered = BeeperQueue(
                runtime, root_name="beeper"
            )
            with recovered._dial_session() as connection:
                after = connection.execute(
                    "SELECT state, resolution_source, prompt_sha256, "
                    "answer_sha256, answer_chars FROM final_callback_receipts "
                    "WHERE request_id=?",
                    (request_id,),
                ).fetchone()
            self.assertEqual("completed", after["state"])
            self.assertEqual("final_callback", after["resolution_source"])
            self.assertEqual("", after["prompt_sha256"])
            self.assertEqual("", after["answer_sha256"])
            self.assertEqual(0, after["answer_chars"])

    def test_legacy_terminal_receipt_without_final_callback_source_remains_valid(self) -> None:
        legacy = {
            "schema_version": 1,
            "request_id": "a" * 64,
            "operation": "send_message_to_thread",
            "status": "completed",
            "result": {"text": "legacy answer"},
        }
        self.assertTrue(
            BeeperQueue._valid_terminal_receipt("a" * 64, legacy)
        )
        compacted = BeeperQueue._compacted_terminal_receipt(legacy)
        self.assertNotIn("final_callback_source", compacted)

    def test_tampered_captured_stage_is_rejected_before_terminal_receipt(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue, _, _ = self.queue(Path(temporary))
            prompt = "exact question"
            final = " exact Final Callback\r\nwith trailing space "
            request_id = queue.submit(
                "send_message_to_thread",
                {"responder_thread_id": RESPONDER_THREAD_ID, "prompt": prompt},
                idempotency_key="event-tampered-responder-stage",
            )
            dial = queue.reserve_exact(request_id)
            self.claim(queue, dial)
            self.capture_final_callback(queue, dial, request_id, prompt, final)
            queue.stage_path(request_id, dial["fence_token"]).write_text(
                "tampered final",
                encoding="utf-8",
                newline="",
            )

            with self.assertRaisesRegex(
                BeeperQueueProtocolError,
                "send completion answer failed captured Responder integrity",
            ):
                queue.complete(
                    request_id,
                    {
                        "responder_thread_id": RESPONDER_THREAD_ID,
                        "responder_host_id": "host-one",
                        "responder_turn_id": "",
                        "final_answer": final,
                    },
                    fence_token=dial["fence_token"],
                )

            self.assertIsNone(queue.response(request_id))
            with queue._dial_session() as connection:
                state = connection.execute(
                    "SELECT state FROM final_callback_receipts WHERE request_id=?",
                    (request_id,),
                ).fetchone()["state"]
            self.assertEqual("captured", state)

    def test_final_callback_completion_seal_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue, _, _ = self.queue(Path(temporary))
            prompt = "seal this answer"
            final = " exact sealed final "
            request_id = queue.submit(
                "send_message_to_thread",
                {"responder_thread_id": RESPONDER_THREAD_ID, "prompt": prompt},
                idempotency_key="event-idempotent-responder-seal",
            )
            dial = queue.reserve_exact(request_id)
            self.claim(queue, dial)
            self.capture_final_callback(queue, dial, request_id, prompt, final)
            exact_result = {
                "responder_thread_id": RESPONDER_THREAD_ID,
                "responder_host_id": "",
                "responder_turn_id": "",
                "final_answer": final,
            }

            self.assertEqual(
                "final_callback",
                queue._seal_current_final_callback(
                    request_id,
                    dial["fence_token"],
                    exact_result,
                ),
            )
            self.assertEqual(
                "final_callback",
                queue._seal_current_final_callback(
                    request_id,
                    dial["fence_token"],
                    exact_result,
                ),
            )
            with self.assertRaises(BeeperQueueProtocolError):
                queue._seal_current_final_callback(
                    request_id,
                    dial["fence_token"],
                    {**exact_result, "final_answer": "different final"},
                )
            with queue._dial_session() as connection:
                sealed = connection.execute(
                    "SELECT state, resolution_source, answer_sha256, answer_chars "
                    "FROM final_callback_receipts WHERE request_id=?",
                    (request_id,),
                ).fetchone()
            self.assertEqual("completing", sealed["state"])
            self.assertEqual("final_callback", sealed["resolution_source"])
            self.assertTrue(sealed["answer_sha256"])
            self.assertEqual(len(final), sealed["answer_chars"])

            completion = queue.complete(
                request_id,
                exact_result,
                fence_token=dial["fence_token"],
            )
            self.assertEqual({"final_callback_source": "final_callback"}, completion)
            self.assertFalse(queue.stage_path(request_id, dial["fence_token"]).exists())
            with queue._dial_session() as connection:
                terminal = connection.execute(
                    "SELECT state, prompt_sha256, answer_sha256, answer_chars "
                    "FROM final_callback_receipts WHERE request_id=?",
                    (request_id,),
                ).fetchone()
            self.assertEqual("completed", terminal["state"])
            self.assertEqual("", terminal["prompt_sha256"])
            self.assertEqual("", terminal["answer_sha256"])
            self.assertEqual(0, terminal["answer_chars"])

    def test_failed_receipt_winner_terminalizes_and_scrubs_sealed_responder(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue, _, _ = self.queue(Path(temporary))
            prompt = "one race only"
            final = "authoritative Final Callback"
            request_id = queue.submit(
                "send_message_to_thread",
                {"responder_thread_id": RESPONDER_THREAD_ID, "prompt": prompt},
                idempotency_key="event-failed-receipt-wins",
            )
            dial = queue.reserve_exact(request_id)
            self.claim(queue, dial)
            self.capture_final_callback(queue, dial, request_id, prompt, final)
            exact_result = {
                "responder_thread_id": RESPONDER_THREAD_ID,
                "responder_host_id": "",
                "responder_turn_id": "",
                "final_answer": final,
            }
            queue._seal_current_final_callback(
                request_id,
                dial["fence_token"],
                exact_result,
            )

            # Model the crash window where a failed terminal receipt wins but
            # final-callback bookkeeping was not yet terminalized.
            with patch.object(queue, "_terminalize_final_callback"):
                queue.fail(
                    request_id,
                    code="raced_failure",
                    message="failed receipt won",
                    retryable=False,
                    may_have_started=True,
                    fence_token=dial["fence_token"],
                )
            with queue._dial_session() as connection:
                before = connection.execute(
                    "SELECT state, answer_sha256 FROM final_callback_receipts "
                    "WHERE request_id=?",
                    (request_id,),
                ).fetchone()
            self.assertEqual("completing", before["state"])
            self.assertTrue(before["answer_sha256"])

            with self.assertRaises(BeeperQueueProtocolError):
                queue.complete(
                    request_id,
                    exact_result,
                    fence_token=dial["fence_token"],
                )
            with queue._dial_session() as connection:
                terminal = connection.execute(
                    "SELECT state, resolution_source, prompt_sha256, "
                    "answer_sha256, answer_chars FROM final_callback_receipts "
                    "WHERE request_id=?",
                    (request_id,),
                ).fetchone()
            self.assertEqual("failed", terminal["state"])
            self.assertEqual("final_callback", terminal["resolution_source"])
            self.assertEqual("", terminal["prompt_sha256"])
            self.assertEqual("", terminal["answer_sha256"])
            self.assertEqual(0, terminal["answer_chars"])
            self.assertFalse(queue.stage_path(request_id, dial["fence_token"]).exists())

    def test_response_reconciles_a_published_receipt_without_reopening_queue(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue, _, _ = self.queue(Path(temporary))
            prompt = "publish then reconcile"
            final = "exact Final Callback receipt body"
            request_id = queue.submit(
                "send_message_to_thread",
                {"responder_thread_id": RESPONDER_THREAD_ID, "prompt": prompt},
                idempotency_key="event-response-reconciles-terminal-responder",
            )
            dial = queue.reserve_exact(request_id)
            request = self.claim(queue, dial)
            self.capture_final_callback(queue, dial, request_id, prompt, final)
            exact_result = {
                "responder_thread_id": RESPONDER_THREAD_ID,
                "responder_host_id": "",
                "responder_turn_id": "",
                "final_answer": final,
            }
            queue._seal_current_final_callback(
                request_id,
                dial["fence_token"],
                exact_result,
            )
            self.assertTrue(
                queue._finalize_response(
                    request_id,
                    {
                        "schema_version": 1,
                        "request_id": request_id,
                        "operation": "send_message_to_thread",
                        "fingerprint": request["fingerprint"],
                        "status": "completed",
                        "final_callback_source": "final_callback",
                        "result": exact_result,
                        "completed_at": time.time(),
                    },
                )
            )
            stage_path = queue.stage_path(request_id, dial["fence_token"])
            self.assertTrue(stage_path.exists())

            response = queue.response(request_id)

            self.assertEqual("completed", response["status"])
            self.assertFalse(stage_path.exists())
            with queue._dial_session() as connection:
                terminal = connection.execute(
                    "SELECT state, resolution_source, prompt_sha256, "
                    "answer_sha256, answer_chars FROM final_callback_receipts "
                    "WHERE request_id=?",
                    (request_id,),
                ).fetchone()
            self.assertEqual("completed", terminal["state"])
            self.assertEqual("final_callback", terminal["resolution_source"])
            self.assertEqual("", terminal["prompt_sha256"])
            self.assertEqual("", terminal["answer_sha256"])
            self.assertEqual(0, terminal["answer_chars"])

    def test_cleanup_removes_old_staging_only_after_terminal_receipt_exists(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue, _, _ = self.queue(
                Path(temporary), retention_hours=1
            )
            prompt = "terminal cleanup branch"
            final = "terminal receipt owns this final"
            request_id = queue.submit(
                "send_message_to_thread",
                {"responder_thread_id": RESPONDER_THREAD_ID, "prompt": prompt},
                idempotency_key="event-clean-terminal-orphan-stage",
            )
            dial = queue.reserve_exact(request_id)
            request = self.claim(queue, dial)
            self.capture_final_callback(queue, dial, request_id, prompt, final)
            exact_result = {
                "responder_thread_id": RESPONDER_THREAD_ID,
                "responder_host_id": "",
                "responder_turn_id": "",
                "final_answer": final,
            }
            queue._seal_current_final_callback(
                request_id,
                dial["fence_token"],
                exact_result,
            )
            self.assertTrue(
                queue._finalize_response(
                    request_id,
                    {
                        "schema_version": 1,
                        "request_id": request_id,
                        "operation": "send_message_to_thread",
                        "fingerprint": request["fingerprint"],
                        "status": "completed",
                        "final_callback_source": "final_callback",
                        "result": exact_result,
                        "completed_at": time.time(),
                    },
                )
            )
            stage_path = queue.stage_path(request_id, dial["fence_token"])
            current_time = time.time()
            os.utime(stage_path, (current_time - 7200, current_time - 7200))

            queue.cleanup(now=current_time)

            self.assertFalse(stage_path.exists())
            with queue._dial_session() as connection:
                terminal = connection.execute(
                    "SELECT state, prompt_sha256, answer_sha256, answer_chars "
                    "FROM final_callback_receipts WHERE request_id=?",
                    (request_id,),
                ).fetchone()
            self.assertEqual("completed", terminal["state"])
            self.assertEqual("", terminal["prompt_sha256"])
            self.assertEqual("", terminal["answer_sha256"])
            self.assertEqual(0, terminal["answer_chars"])

    def test_catalog_is_staged_answer_free_then_consumed_once(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue, _, _ = self.queue(Path(temporary))
            request_id, reservation, _claimed, result = self.claim_catalog(
                queue,
                idempotency_key="catalog-private-consume",
            )

            beeper_terminal = queue.complete_readonly(
                reservation,
                result,
            )

            self.assertEqual("completed", beeper_terminal["status"])
            self.assertEqual(
                {
                    "catalog_version": 1,
                    "snapshot_id": reservation["snapshot_id"],
                    "catalog_staged": True,
                },
                beeper_terminal["result"],
            )
            self.assertNotIn("业务任务", json.dumps(beeper_terminal, ensure_ascii=False))
            stage = queue._path(queue.catalog_staging_dir, request_id)
            self.assertTrue(stage.exists())

            # A late Beeper failure is only an answer-free acknowledgement;
            # it must not steal the Bridge's one private catalog consume.
            late = queue.fail_page(
                reservation,
                "late_beeper_cleanup",
                False,
            )
            self.assertEqual("completed", late["status"])
            self.assertTrue(stage.exists())

            bridge_terminal = queue.finish_readonly(reservation, 0)
            self.assertEqual("业务任务", bridge_terminal["result"]["tasks"][0]["title"])
            self.assertRegex(
                bridge_terminal["result"]["tasks"][0]["selection_proof"],
                r"^[a-f0-9]{64}$",
            )
            self.assertGreater(
                bridge_terminal["result"]["snapshot_expires_at"],
                time.time(),
            )
            self.assertFalse(stage.exists())

            consumed = queue.finish_readonly(reservation, 0)
            self.assertTrue(consumed["result"]["catalog_staged"])
            self.assertNotIn("tasks", consumed["result"])

    def test_catalog_tamper_is_rejected_and_scrubbed(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue, _, _ = self.queue(Path(temporary))
            request_id, reservation, _claimed, result = self.claim_catalog(
                queue,
                idempotency_key="catalog-tamper",
            )
            queue.complete_readonly(reservation, result)
            stage = queue._path(queue.catalog_staging_dir, request_id)
            staged = json.loads(stage.read_text(encoding="utf-8"))
            # Keep the payload schema-valid so this specifically proves that a
            # content change with a stale HMAC seal is rejected.
            staged["result"]["tasks"][0]["title"] = "篡改后的合法标题"
            stage.write_text(
                json.dumps(staged, ensure_ascii=False),
                encoding="utf-8",
            )

            with self.assertRaises(BeeperQueueProtocolError):
                queue.finish_readonly(reservation, 0)

            self.assertFalse(stage.exists())
            self.assertEqual([], list(queue.catalog_staging_dir.glob("*.consuming")))

    def test_catalog_interrupted_consume_is_not_replayed_and_ages_out(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            runtime_dir = Path(temporary)
            queue, _, _ = self.queue(runtime_dir)
            request_id, reservation, _claimed, result = self.claim_catalog(
                queue,
                idempotency_key="catalog-restart-scrub",
            )
            queue.complete_readonly(reservation, result)
            stage = queue._path(queue.catalog_staging_dir, request_id)
            interrupted = queue.catalog_staging_dir / f"{request_id}.crash.consuming"
            os.replace(stage, interrupted)

            restarted = BeeperQueue(
                runtime_dir,
                root_name="beeper",
            )
            terminal = restarted.finish_readonly(reservation, 0)

            self.assertTrue(terminal["result"]["catalog_staged"])
            self.assertNotIn("tasks", terminal["result"])
            self.assertTrue(interrupted.exists())
            restarted.cleanup(now=time.time() + 601)
            self.assertFalse(interrupted.exists())
            self.assertEqual([], list(restarted.catalog_staging_dir.iterdir()))

    def test_catalog_cleanup_preserves_fresh_consumer_and_scrubs_stale_one(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue = BeeperQueue(Path(temporary))
            current_time = time.time()
            fresh = queue.catalog_staging_dir / f"{'a' * 32}.fresh.consuming"
            stale = queue.catalog_staging_dir / f"{'b' * 32}.stale.consuming"
            fresh.write_text("fresh", encoding="utf-8")
            stale.write_text("stale", encoding="utf-8")
            stale_time = current_time - 601
            os.utime(stale, (stale_time, stale_time))

            queue.cleanup(now=current_time)

            self.assertTrue(fresh.exists())
            self.assertFalse(stale.exists())

    def test_beeper_and_tombstones_cannot_be_business_responders(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue, _, _ = self.queue(Path(temporary))
            excluded = queue.excluded_thread_ids()
            self.assertIn(BEEPER_THREAD_ID, excluded)
            for index, responder in enumerate(excluded):
                with self.subTest(responder=responder), self.assertRaises(BeeperQueueProtocolError):
                    queue.submit(
                        "send_message_to_thread",
                        {"responder_thread_id": responder, "prompt": "must never route"},
                        idempotency_key=f"excluded-responder-{index}",
                    )

    def test_dial_sessions_close_database_handles(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue = BeeperQueue(Path(temporary))
            opened_connections: list[sqlite3.Connection] = []
            open_connection = queue._dial_connection

            def tracked_connection() -> sqlite3.Connection:
                connection = open_connection()
                opened_connections.append(connection)
                return connection

            with patch.object(queue, "_dial_connection", side_effect=tracked_connection):
                queue.status()

            self.assertGreaterEqual(len(opened_connections), 1)
            for connection in opened_connections:
                with self.assertRaises(sqlite3.ProgrammingError):
                    connection.execute("SELECT 1")

    def test_status_exposes_only_event_dial_lease_and_queue_counts(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue, _, _ = self.queue(Path(temporary))
            idle = queue.status()
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
                set(idle.as_dict()),
            )
            self.assertFalse(idle.dial_inflight)
            self.assertIsNone(idle.dial_lease_remaining_seconds)

            request_id = queue.submit(
                "send_message_to_thread",
                {"responder_thread_id": RESPONDER_THREAD_ID, "prompt": "one event"},
                idempotency_key="status-event-dial",
            )
            reservation = queue.reserve_exact(request_id)
            active = queue.status()
            self.assertTrue(active.dial_inflight)
            self.assertGreater(active.dial_lease_remaining_seconds or 0, 0)
            self.assertEqual(1, active.pending)
            self.assertEqual(0, active.claimed)

            self.claim(queue, reservation)
            claimed = queue.status()
            self.assertTrue(claimed.dial_inflight)
            self.assertEqual(0, claimed.pending)
            self.assertEqual(1, claimed.claimed)

    def test_live_exact_dial_protects_its_matching_claim(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue, _, _ = self.queue(
                Path(temporary),
                claim_ttl_seconds=60,
                dial_lease_ttl_seconds=180,
            )
            request_id = queue.submit(
                "send_message_to_thread",
                {"responder_thread_id": RESPONDER_THREAD_ID, "prompt": "long task"},
                idempotency_key="exact-live-dial-protection",
            )
            reservation = queue.reserve_exact(request_id)
            request = self.claim(queue, reservation)

            self.assertEqual(
                0,
                queue.expire_stale_claims(
                    now=float(request["claimed_at"]) + queue.claim_ttl_seconds + 1
                ),
            )
            self.assertTrue(queue.was_claimed(request_id))
            self.assertIsNone(queue.response(request_id))

    def test_mismatched_claim_identity_cannot_inherit_live_dial_protection(self) -> None:
        claim_mutations = {
            "request_id": lambda request: request.__setitem__(
                "request_id", "f" * 32
            ),
            "dial_id": lambda request: request.__setitem__("dial_id", "e" * 32),
            "fence_token": lambda request: request.__setitem__(
                "fence_token", "d" * 32
            ),
            "dial_generation": lambda request: request.__setitem__(
                "dial_generation", int(request["dial_generation"]) + 1
            ),
            "dial_origin": lambda request: request.__setitem__(
                "dial_origin", "retired-producer"
            ),
        }
        state_mutations = {
            "authorized_request_id": (
                "UPDATE dial_state SET authorized_request_id=? WHERE singleton=1",
                ("c" * 32,),
            ),
            "authorized_operation": (
                "UPDATE dial_state SET authorized_operation=? WHERE singleton=1",
                ("inspect_thread",),
            ),
            "generation": (
                "UPDATE dial_state SET generation=generation+1 WHERE singleton=1",
                (),
            ),
        }
        variants = [
            *(('claim', name, mutation) for name, mutation in claim_mutations.items()),
            *(('state', name, mutation) for name, mutation in state_mutations.items()),
        ]
        for mutation_kind, name, mutation in variants:
            with self.subTest(identity=name), tempfile.TemporaryDirectory(
                dir=TEST_TEMP_ROOT
            ) as temporary:
                queue, _, _ = self.queue(
                    Path(temporary),
                    claim_ttl_seconds=60,
                    dial_lease_ttl_seconds=180,
                )
                request_id = queue.submit(
                    "send_message_to_thread",
                    {"responder_thread_id": RESPONDER_THREAD_ID, "prompt": "exact"},
                    idempotency_key=f"mismatched-live-dial-{name}",
                )
                reservation = queue.reserve_exact(request_id)
                request = self.claim(queue, reservation)
                if mutation_kind == "claim":
                    mutation(request)
                    queue._path(queue.claimed_dir, request_id).write_text(
                        json.dumps(request, ensure_ascii=False),
                        encoding="utf-8",
                    )
                else:
                    statement, arguments = mutation
                    with queue._dial_session() as connection:
                        connection.execute(statement, arguments)

                self.assertEqual(
                    1,
                    queue.expire_stale_claims(
                        now=float(request["claimed_at"])
                        + queue.claim_ttl_seconds
                        + 1
                    ),
                )
                response = queue.response(request_id)
                self.assertIsNotNone(response)
                self.assertEqual("failed", response["status"])
                self.assertEqual("beeper_claim_expired", response["error"]["code"])
                self.assertFalse(response["error"]["retryable"])
                self.assertTrue(response["error"]["may_have_started"])

    def test_expired_dial_does_not_protect_matching_claim(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue, _, _ = self.queue(
                Path(temporary),
                claim_ttl_seconds=60,
                dial_lease_ttl_seconds=60,
            )
            request_id = queue.submit(
                "send_message_to_thread",
                {"responder_thread_id": RESPONDER_THREAD_ID, "prompt": "expires"},
                idempotency_key="expired-exact-dial",
            )
            reservation = queue.reserve_exact(request_id)
            request = self.claim(queue, reservation)

            self.assertEqual(
                1,
                queue.expire_stale_claims(
                    now=float(request["claimed_at"])
                    + max(
                        queue.claim_ttl_seconds,
                        queue.dial_lease_ttl_seconds,
                    )
                    + 1
                ),
            )
            self.assertEqual(
                "beeper_claim_expired",
                queue.response(request_id)["error"]["code"],
            )

    def test_submit_is_idempotent_for_same_operation_and_key(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue = BeeperQueue(Path(temporary))
            payload = {"responder_thread_id": RESPONDER_THREAD_ID, "prompt": "hello"}
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
            with self.assertRaises(BeeperQueueProtocolError):
                queue.submit(
                    "send_message_to_thread",
                    {**payload, "prompt": "different"},
                    idempotency_key="event-1",
                )

    def test_retry_generation_requires_explicit_json_booleans(self) -> None:
        safe = {
            "status": "failed",
            "error": {
                "code": "temporary_responder_gate",
                "retryable": True,
                "may_have_started": False,
            },
        }
        self.assertTrue(BeeperQueue._response_allows_retry(safe))
        for error in (
            {"retryable": True, "may_have_started": False},
            {"code": "", "retryable": True, "may_have_started": False},
            {"retryable": "true", "may_have_started": False},
            {"retryable": True, "may_have_started": "false"},
            {
                "code": "responder_needs_attention",
                "retryable": True,
                "may_have_started": False,
            },
            {
                "code": "responder_tool_unavailable",
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
                    BeeperQueue._response_allows_retry(
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
        compacted = BeeperQueue._compacted_terminal_receipt(malformed)
        self.assertEqual("responder_result_unknown", compacted["error"]["code"])
        self.assertFalse(BeeperQueue._response_allows_retry(compacted))

    def test_orphan_readonly_finalization_is_safe_unknown_and_not_replayed(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue, _, _ = self.queue(
                Path(temporary),
                claim_ttl_seconds=60,
                read_claim_ttl_seconds=60,
                dial_lease_ttl_seconds=60,
            )
            request_id, reservation, _claimed, _result = self.claim_catalog(
                queue,
                idempotency_key="readonly-orphan-finalization",
            )
            queue._finalization_path(request_id).touch()
            with queue._dial_session() as connection:
                row = connection.execute(
                    "SELECT claimed_at FROM pages WHERE page_id=?",
                    (reservation["page"],),
                ).fetchone()
            expired_at = float(row["claimed_at"]) + 62

            self.assertEqual(1, queue.expire_stale_claims(now=expired_at))

            terminal = queue.response(request_id)
            self.assertEqual("readonly_result_unknown", terminal["error"]["code"])
            self.assertFalse(terminal["error"]["may_have_started"])
            self.assertFalse(terminal["error"]["retryable"])
            self.assertEqual(
                request_id,
                queue.submit(
                    "list_task_catalog",
                    {
                        "catalog_version": 1,
                        "visibility": "all",
                        "thread_ids": [],
                        "include_archived": False,
                        "limit": 50,
                        "excluded_thread_ids": list(
                            queue.excluded_thread_ids()
                        ),
                    },
                    idempotency_key="readonly-orphan-finalization",
                ),
            )
            self.assertEqual(0, queue.status().pending)

    def test_readonly_claim_expiry_is_terminal_and_not_replayed(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue, _, _ = self.queue(
                Path(temporary),
                claim_ttl_seconds=60,
                read_claim_ttl_seconds=60,
                dial_lease_ttl_seconds=60,
            )
            request_id, reservation, _claimed, _result = self.claim_catalog(
                queue,
                idempotency_key="readonly-claim-expiry",
            )
            with queue._dial_session() as connection:
                row = connection.execute(
                    "SELECT claimed_at FROM pages WHERE page_id=?",
                    (reservation["page"],),
                ).fetchone()

            self.assertEqual(
                1,
                queue.expire_stale_claims(now=float(row["claimed_at"]) + 62),
            )

            terminal = queue.response(request_id)
            self.assertEqual("readonly_result_unknown", terminal["error"]["code"])
            self.assertFalse(terminal["error"]["may_have_started"])
            self.assertFalse(terminal["error"]["retryable"])
            self.assertEqual(
                request_id,
                queue.submit(
                    "list_task_catalog",
                    {
                        "catalog_version": 1,
                        "visibility": "all",
                        "thread_ids": [],
                        "include_archived": False,
                        "limit": 50,
                        "excluded_thread_ids": list(
                            queue.excluded_thread_ids()
                        ),
                    },
                    idempotency_key="readonly-claim-expiry",
                ),
            )
            self.assertEqual(0, queue.status().pending)

    def test_expired_answer_text_becomes_a_small_unknown_tombstone(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue, _, _ = self.queue(
                Path(temporary), retention_hours=1
            )
            payload = {"responder_thread_id": RESPONDER_THREAD_ID, "prompt": "answer"}
            request_id = queue.submit(
                "send_message_to_thread",
                payload,
                idempotency_key="event-expired-answer",
            )
            dial = queue.reserve_exact(request_id)
            request = self.claim(queue, dial)
            self.capture_final_callback(
                queue,
                dial,
                request_id,
                payload["prompt"],
                "private final body",
            )
            queue.complete(
                request_id,
                {
                    "responder_thread_id": RESPONDER_THREAD_ID,
                    "responder_host_id": "",
                    "responder_turn_id": "",
                    "final_answer": "private final body",
                },
                fence_token=dial["fence_token"],
            )
            # Simulate the released legacy layout: empty .final fence and full
            # response cache, with no separate receipt payload yet.
            queue._receipt_payload_path(request_id).unlink()
            queue.cleanup(now=float(request["claimed_at"]) + 7200)

            response = queue.response(request_id)
            self.assertEqual("failed", response["status"])
            self.assertEqual("responder_result_unknown", response["error"]["code"])
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

    def test_receipt_payload_without_marker_is_authoritative_and_not_replayed(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue, _, _ = self.queue(Path(temporary))
            payload = {"responder_thread_id": RESPONDER_THREAD_ID, "prompt": "once"}
            request_id = queue.submit(
                "send_message_to_thread",
                payload,
                idempotency_key="event-receipt-only",
            )
            dial = queue.reserve_exact(request_id)
            self.claim(queue, dial)
            self.capture_final_callback(
                queue,
                dial,
                request_id,
                payload["prompt"],
                "answer",
            )
            with patch(
                "bridge_core.beeper_queue.os.open",
                side_effect=OSError("injected marker publication failure"),
            ) as marker_open_mock:
                queue.complete(
                    request_id,
                    {
                        "responder_thread_id": RESPONDER_THREAD_ID,
                        "responder_host_id": "",
                        "responder_turn_id": "",
                        "final_answer": "answer",
                    },
                    fence_token=dial["fence_token"],
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
            queue, _, _ = self.queue(Path(temporary))
            request_id = queue.submit(
                "send_message_to_thread",
                {"responder_thread_id": RESPONDER_THREAD_ID, "prompt": "once"},
                idempotency_key="event-receipt-close-fault",
            )
            dial = queue.reserve_exact(request_id)
            self.claim(queue, dial)
            self.capture_final_callback(
                queue,
                dial,
                request_id,
                "once",
                "answer",
            )
            real_close = os.close

            def close_then_fail(descriptor):
                real_close(descriptor)
                raise OSError("injected descriptor close failure")

            with patch(
                "bridge_core.beeper_queue.os.close",
                side_effect=close_then_fail,
            ) as marker_close_mock:
                queue.complete(
                    request_id,
                    {
                        "responder_thread_id": RESPONDER_THREAD_ID,
                        "responder_host_id": "",
                        "responder_turn_id": "",
                        "final_answer": "answer",
                    },
                    fence_token=dial["fence_token"],
                )

            marker_close_mock.assert_called_once()
            self.assertTrue(queue._finalization_path(request_id).exists())
            queue._path(queue.responses_dir, request_id).unlink()
            self.assertEqual("completed", queue.response(request_id)["status"])
            self.assertEqual(
                request_id,
                queue.submit(
                    "send_message_to_thread",
                    {"responder_thread_id": RESPONDER_THREAD_ID, "prompt": "once"},
                    idempotency_key="event-receipt-close-fault",
                ),
            )
            self.assertEqual(0, queue.status().pending)
            self.assertEqual(0, queue.status().claimed)

    def test_concurrent_terminal_finalizers_preserve_first_receipt(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue, _, _ = self.queue(Path(temporary))
            request_id = queue.submit(
                "send_message_to_thread",
                {"responder_thread_id": RESPONDER_THREAD_ID, "prompt": "once"},
                idempotency_key="event-finalizer-race",
            )
            dial = queue.reserve_exact(request_id)
            self.claim(queue, dial)
            self.capture_final_callback(
                queue,
                dial,
                request_id,
                "once",
                "answer",
            )
            barrier = threading.Barrier(2)
            from bridge_core import beeper_queue

            original_publish = beeper_queue._atomic_write_json_exclusive
            receipt_path = queue._receipt_payload_path(request_id)

            def synchronized_publish(path, payload):
                if path == receipt_path:
                    barrier.wait(timeout=5)
                return original_publish(path, payload)

            def complete():
                try:
                    queue.complete(
                        request_id,
                        {
                            "responder_thread_id": RESPONDER_THREAD_ID,
                            "responder_host_id": "",
                            "responder_turn_id": "",
                            "final_answer": "answer",
                        },
                        fence_token=dial["fence_token"],
                    )
                except BeeperQueueProtocolError:
                    pass

            def fail():
                try:
                    queue.fail(
                        request_id,
                        code="late_failure",
                        message="raced",
                        retryable=False,
                        may_have_started=True,
                        fence_token=dial["fence_token"],
                    )
                except BeeperQueueProtocolError:
                    pass

            with patch(
                "bridge_core.beeper_queue._atomic_write_json_exclusive",
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
                    fence_token=dial["fence_token"],
                )
            else:
                with self.assertRaises(BeeperQueueProtocolError):
                    queue.complete(
                        request_id,
                        {
                            "responder_thread_id": RESPONDER_THREAD_ID,
                            "responder_host_id": "",
                            "responder_turn_id": "",
                            "final_answer": "later",
                        },
                        fence_token=dial["fence_token"],
                    )
            self.assertEqual(first_bytes, receipt_path.read_bytes())
            with queue._dial_session() as connection:
                terminal = connection.execute(
                    "SELECT state, prompt_sha256, answer_sha256, answer_chars "
                    "FROM final_callback_receipts WHERE request_id=?",
                    (request_id,),
                ).fetchone()
            self.assertEqual(first["status"], terminal["state"])
            self.assertEqual("", terminal["prompt_sha256"])
            self.assertEqual("", terminal["answer_sha256"])
            self.assertEqual(0, terminal["answer_chars"])

    def test_retention_preserves_nonterminal_captured_stage_until_terminal(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue, _, _ = self.queue(
                Path(temporary),
                claim_ttl_seconds=86400,
                retention_hours=1,
                dial_lease_ttl_seconds=86400,
            )
            prompt = "long exact responder task"
            final = " retained exact Final Callback\r\n"
            request_id = queue.submit(
                "send_message_to_thread",
                {"responder_thread_id": RESPONDER_THREAD_ID, "prompt": prompt},
                idempotency_key="event-retain-nonterminal-responder-stage",
            )
            dial = queue.reserve_exact(request_id)
            self.claim(queue, dial)
            self.capture_final_callback(queue, dial, request_id, prompt, final)
            stage_path = queue.stage_path(request_id, dial["fence_token"])
            current_time = time.time()
            os.utime(stage_path, (current_time - 7200, current_time - 7200))

            queue.cleanup(now=current_time)

            self.assertTrue(queue.was_claimed(request_id))
            self.assertTrue(stage_path.exists())
            queue.complete(
                request_id,
                {
                    "responder_thread_id": RESPONDER_THREAD_ID,
                    "responder_host_id": "",
                    "responder_turn_id": "",
                    "final_answer": final,
                },
                fence_token=dial["fence_token"],
            )
            self.assertFalse(stage_path.exists())

    def test_namespace_and_registration_are_closed_and_immutable(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            runtime_dir = Path(temporary)
            default = BeeperQueue(runtime_dir)
            self.assertEqual(RETIRED_QUEUE_ROOT_NAME, default.root.name)
            with self.assertRaises(BeeperQueueProtocolError):
                BeeperQueue(runtime_dir, root_name="arbitrary-beeper")

            queue, executable, digest = self.queue(runtime_dir)
            self.assertEqual("beeper", queue.root.name)
            self.assertEqual(
                {
                    "valid": True,
                    "beeper_thread_id": BEEPER_THREAD_ID,
                    "beeper_host_id": "host-one",
                    "codex_exe_path": str(executable),
                    "codex_exe_sha256": digest,
                    "codex_version": "codex-cli 0.test",
                },
                queue.registration(),
            )
            queue.register(
                BEEPER_THREAD_ID,
                "host-one",
                str(executable),
                digest,
                "codex-cli 0.test",
            )
            with self.assertRaises(BeeperQueueProtocolError):
                queue.register(
                    OTHER_BEEPER_ID,
                    "host-one",
                    str(executable),
                    digest,
                    "codex-cli 0.test",
                )
            executable.write_bytes(b"changed-after-registration")
            self.assertFalse(queue.registration()["valid"])
            with self.assertRaises(BeeperQueueProtocolError):
                default.registration()

    def test_final_callback_finish_is_exactly_once(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue, _, _ = self.queue(Path(temporary))
            prompt = "只执行一次：保留 Unicode 🚀"
            final = " 精确 MCP 最终答复\r\n第二行 🙂 "
            request_id = queue.submit(
                "send_message_to_thread",
                {
                    "responder_thread_id": RESPONDER_THREAD_ID,
                    "responder_host_id": "responder-host",
                    "prompt": prompt,
                    "client_message_id": "client-one",
                },
                idempotency_key="success",
            )
            reservation = queue.reserve_exact(request_id)
            self.assertEqual("reserved", reservation["status"])
            self.assertEqual(reservation["page"], reservation["page_id"])
            self.assertEqual(request_id, reservation["request_id"])
            self.assertEqual(BEEPER_THREAD_ID, reservation["beeper_thread_id"])
            with self.assertRaises(BeeperQueueProtocolError):
                queue.reserve_exact(request_id)

            claimed = queue.claim_and_arm(reservation)
            self.assertEqual("claimed_armed", claimed["status"])
            self.assertEqual(RESPONDER_THREAD_ID, claimed["responder_thread_id"])
            self.assertEqual("responder-host", claimed["responder_host_id"])
            envelope = self.final_callback_envelope(claimed["prompt"])
            self.assertEqual("feishu-final-callback-v1", envelope["protocol"])
            self.assertEqual(prompt, envelope["user_request"])
            final_callback_capability = envelope["final_callback_capability"]
            self.assertRegex(final_callback_capability, r"^[a-f0-9]{32}$")
            self.assertIn("submit_final_callback", claimed["prompt"])
            self.assertNotEqual(prompt, claimed["prompt"])
            with queue._dial_session() as connection:
                receipt = connection.execute(
                    "SELECT * FROM final_callback_receipts WHERE request_id=?",
                    (request_id,),
                ).fetchone()
                columns = {
                    str(row["name"])
                    for row in connection.execute(
                        "PRAGMA table_info(final_callback_receipts)"
                    ).fetchall()
                }
            self.assertEqual("armed", receipt["state"])
            self.assertEqual("final_callback", receipt["transport_mode"])
            self.assertEqual(
                hashlib.sha256(final_callback_capability.encode("ascii")).hexdigest(),
                receipt["final_callback_capability_sha256"],
            )
            self.assertNotIn("final_callback_capability", columns)
            self.assertNotIn(final_callback_capability, json.dumps(dict(receipt), default=str))
            with self.assertRaises(BeeperQueueProtocolError):
                queue.claim_and_arm(reservation)

            with self.assertRaises(BeeperQueueProtocolError):
                queue.submit_final_callback("f" * 32, final)
            self.assertEqual(
                {"accepted": True, "state": "captured"},
                queue.submit_final_callback(final_callback_capability, final),
            )
            # An exact re-delivery is idempotent and never creates another final.
            self.assertEqual(
                {"accepted": True, "state": "captured"},
                queue.submit_final_callback(final_callback_capability.upper(), final),
            )
            terminal = queue.finish_final_callback(reservation, 0)
            self.assertTrue(terminal["terminal"])
            self.assertEqual("completed", terminal["status"])
            self.assertEqual("send_message_to_thread", terminal["operation"])
            self.assertEqual("final_callback", terminal["final_callback_source"])
            self.assertEqual(final, terminal["result"]["final_answer"])
            self.assertEqual("", terminal["result"]["responder_turn_id"])
            self.assertEqual(RESPONDER_THREAD_ID, terminal["responder_thread_id"])
            self.assertEqual("responder-host", terminal["responder_host_id"])
            self.assertEqual(
                terminal,
                queue.finish_final_callback(reservation, 0),
            )
            with queue._dial_session() as connection:
                dial = connection.execute(
                    "SELECT * FROM dial_state WHERE singleton=1"
                ).fetchone()
                page = connection.execute(
                    "SELECT state FROM pages WHERE page_id=?",
                    (reservation["page"],),
                ).fetchone()
                terminal_receipt = connection.execute(
                    "SELECT * FROM final_callback_receipts WHERE request_id=?",
                    (request_id,),
                ).fetchone()
            self.assertEqual("idle", dial["status"])
            self.assertEqual("", dial["dial_id"])
            self.assertEqual("completed", page["state"])
            self.assertEqual("completed", terminal_receipt["state"])
            self.assertEqual("final_callback", terminal_receipt["resolution_source"])
            self.assertEqual("", terminal_receipt["final_callback_capability_sha256"])
            self.assertEqual("", terminal_receipt["answer_sha256"])
            self.assertFalse(
                (
                    queue.staging_dir
                    / f"{request_id}.{reservation['fence_token'][:16]}.txt"
                ).exists()
            )

    def test_final_callback_conflict_fails_closed_and_scrubs_capability(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue, _, _ = self.queue(Path(temporary))
            request_id = queue.submit(
                "send_message_to_thread",
                {
                    "responder_thread_id": RESPONDER_THREAD_ID,
                    "responder_host_id": "responder-host",
                    "prompt": "只允许一个 final",
                },
                idempotency_key="responder-conflict",
            )
            reservation = queue.reserve_exact(request_id)
            claimed = queue.claim_and_arm(reservation)
            final_callback_capability = self.final_callback_envelope(claimed["prompt"])["final_callback_capability"]
            self.assertEqual(
                {"accepted": True, "state": "captured"},
                queue.submit_final_callback(final_callback_capability, "第一份 final"),
            )
            self.assertEqual(
                {"accepted": False, "state": "conflict"},
                queue.submit_final_callback(final_callback_capability, "不同的第二份 final"),
            )
            with queue._dial_session() as connection:
                receipt = connection.execute(
                    "SELECT * FROM final_callback_receipts WHERE request_id=?",
                    (request_id,),
                ).fetchone()
            self.assertEqual("conflict", receipt["state"])
            self.assertEqual("", receipt["final_callback_capability_sha256"])
            self.assertEqual("", receipt["answer_sha256"])
            self.assertFalse(
                queue.stage_path(request_id, reservation["fence_token"]).exists()
            )
            terminal = queue.finish_final_callback(reservation, 0)
            self.assertEqual("failed", terminal["status"])
            self.assertEqual(
                "final_callback_conflict",
                terminal["error"]["code"],
            )
            self.assertTrue(terminal["error"]["may_have_started"])

    def test_exact_duplicate_rejects_tampered_final_callback_stage(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue, _, _ = self.queue(Path(temporary))
            request_id = queue.submit(
                "send_message_to_thread",
                {"responder_thread_id": RESPONDER_THREAD_ID, "prompt": "完整性测试"},
                idempotency_key="responder-stage-tamper",
            )
            reservation = queue.reserve_exact(request_id)
            claimed = queue.claim_and_arm(reservation)
            final_callback_capability = self.final_callback_envelope(claimed["prompt"])["final_callback_capability"]
            self.assertEqual(
                {"accepted": True, "state": "captured"},
                queue.submit_final_callback(final_callback_capability, "原始 final"),
            )
            queue.stage_path(request_id, reservation["fence_token"]).write_text(
                "被篡改的 final",
                encoding="utf-8",
            )
            self.assertEqual(
                {"accepted": False, "state": "conflict"},
                queue.submit_final_callback(final_callback_capability, "原始 final"),
            )
            with queue._dial_session() as connection:
                receipt = connection.execute(
                    "SELECT * FROM final_callback_receipts WHERE request_id=?",
                    (request_id,),
                ).fetchone()
            self.assertEqual("conflict", receipt["state"])
            self.assertEqual("", receipt["final_callback_capability_sha256"])
            self.assertFalse(
                queue.stage_path(request_id, reservation["fence_token"]).exists()
            )

    def test_expired_final_callback_capability_is_rejected_and_never_captured(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue, _, _ = self.queue(Path(temporary))
            request_id = queue.submit(
                "send_message_to_thread",
                {"responder_thread_id": RESPONDER_THREAD_ID, "prompt": "过期后不可回传"},
                idempotency_key="responder-expired",
            )
            reservation = queue.reserve_exact(request_id)
            claimed = queue.claim_and_arm(reservation)
            final_callback_capability = self.final_callback_envelope(claimed["prompt"])["final_callback_capability"]
            with queue._dial_session() as connection:
                connection.execute(
                    "UPDATE final_callback_receipts SET expires_at=0 WHERE request_id=?",
                    (request_id,),
                )
            self.assertEqual(
                {"accepted": False, "state": "expired"},
                queue.submit_final_callback(final_callback_capability, "过期 final", now=time.time()),
            )
            with queue._dial_session() as connection:
                receipt = connection.execute(
                    "SELECT * FROM final_callback_receipts WHERE request_id=?",
                    (request_id,),
                ).fetchone()
            self.assertEqual("expired", receipt["state"])
            self.assertEqual("", receipt["final_callback_capability_sha256"])
            self.assertFalse(
                queue.stage_path(request_id, reservation["fence_token"]).exists()
            )

    def test_timeout_is_nonterminal_and_fail_never_rearms(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue, _, _ = self.queue(Path(temporary))
            request_id = queue.submit(
                "send_message_to_thread",
                {
                    "responder_thread_id": RESPONDER_THREAD_ID,
                    "responder_host_id": "responder-host",
                    "prompt": "不会自动重试",
                },
                idempotency_key="timeout",
            )
            reservation = queue.reserve_exact(request_id)
            queue.claim_and_arm(reservation["page"])
            self.assertEqual(
                "claimed_armed",
                queue.wait_for_beeper_claim(reservation["page"], 0),
            )
            self.assertIsNone(
                queue.fail_page_if_unclaimed(
                    reservation["page"],
                    "beeper_claim_timeout",
                )
            )
            self.assertIsNone(queue.response(request_id))

            waiting = queue.finish_final_callback(
                reservation["page"],
                0,
            )
            self.assertEqual("waiting_final_callback", waiting["status"])
            self.assertFalse(waiting["terminal"])
            self.assertIsNone(queue.response(request_id))
            self.assertTrue(queue.status().dial_inflight)

            terminal = queue.fail_page(
                reservation,
                "responder_result_unknown",
                True,
            )
            self.assertTrue(terminal["terminal"])
            self.assertEqual("failed", terminal["status"])
            self.assertEqual("responder_result_unknown", terminal["error"]["code"])
            self.assertFalse(terminal["error"]["retryable"])
            self.assertTrue(terminal["error"]["may_have_started"])
            self.assertFalse(queue.status().dial_inflight)
            with self.assertRaises(BeeperQueueProtocolError):
                queue.reserve_exact(request_id)

    def test_unclaimed_timeout_is_safe_and_denies_late_claim(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue, _, _ = self.queue(Path(temporary))
            request_id = queue.submit(
                "send_message_to_thread",
                {
                    "responder_thread_id": RESPONDER_THREAD_ID,
                    "responder_host_id": "responder-host",
                    "prompt": "Beeper 未 claim 前不得泄露",
                },
                idempotency_key="unclaimed-claim-timeout",
            )
            reservation = queue.reserve_exact(request_id)
            self.assertEqual(
                "reserved",
                queue.wait_for_beeper_claim(reservation, 0),
            )

            terminal = queue.fail_page_if_unclaimed(
                reservation,
                "beeper_claim_timeout",
            )

            self.assertIsNotNone(terminal)
            self.assertEqual("failed", terminal["status"])
            self.assertEqual("beeper_claim_timeout", terminal["error"]["code"])
            self.assertFalse(terminal["error"]["may_have_started"])
            self.assertFalse(terminal["error"]["retryable"])
            self.assertEqual(
                "terminal",
                queue.wait_for_beeper_claim(reservation, 0),
            )
            with self.assertRaises(BeeperQueueProtocolError):
                queue.claim_and_arm(reservation)
            self.assertEqual(
                terminal,
                queue.fail_page_if_unclaimed(
                    reservation,
                    "beeper_claim_timeout",
                ),
            )
            self.assertFalse(queue.status().dial_inflight)

    def test_load_failure_is_safe_and_denies_late_claim(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue, _, _ = self.queue(Path(temporary))
            request_id = queue.submit(
                "send_message_to_thread",
                {"responder_thread_id": RESPONDER_THREAD_ID, "prompt": "deep link 失败"},
                idempotency_key="unclaimed-load-failure",
            )
            reservation = queue.reserve_exact(request_id)

            terminal = queue.fail_page_if_unclaimed(
                reservation["page"],
                "beeper_load_assist_failed",
            )

            self.assertIsNotNone(terminal)
            self.assertEqual("beeper_load_assist_failed", terminal["error"]["code"])
            self.assertFalse(terminal["error"]["may_have_started"])
            with self.assertRaises(BeeperQueueProtocolError):
                queue.claim_and_arm(reservation["page"])

    def test_claim_wait_rechecks_terminal_after_release_race(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue, _, _ = self.queue(Path(temporary))
            request_id = queue.submit(
                "send_message_to_thread",
                {"responder_thread_id": RESPONDER_THREAD_ID, "prompt": "release race"},
                idempotency_key="claim-wait-release-race",
            )
            reservation = queue.reserve_exact(request_id)
            original_assert = queue._assert_dial_identity
            fired = False

            def publish_before_assert(record: dict) -> None:
                nonlocal fired
                if not fired:
                    fired = True
                    with patch.object(
                        queue,
                        "_assert_dial_identity",
                        original_assert,
                    ):
                        queue.fail_page(
                            reservation,
                            "synthetic_preclaim_terminal",
                            False,
                        )
                original_assert(record)

            with patch.object(
                queue,
                "_assert_dial_identity",
                publish_before_assert,
            ):
                self.assertEqual(
                    "terminal",
                    queue.wait_for_beeper_claim(reservation, 0),
                )
            self.assertTrue(fired)
            self.assertEqual(
                "synthetic_preclaim_terminal",
                queue.response(request_id)["error"]["code"],
            )

    def test_finish_rechecks_terminal_after_release_race(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue, _, _ = self.queue(Path(temporary))
            request_id = queue.submit(
                "send_message_to_thread",
                {"responder_thread_id": RESPONDER_THREAD_ID, "prompt": "finish race"},
                idempotency_key="finish-release-race",
            )
            reservation = queue.reserve_exact(request_id)
            original_assert = queue._assert_dial_identity
            fired = False

            def publish_before_assert(record: dict) -> None:
                nonlocal fired
                if not fired:
                    fired = True
                    with patch.object(
                        queue,
                        "_assert_dial_identity",
                        original_assert,
                    ):
                        queue.fail_page(
                            reservation,
                            "synthetic_preclaim_terminal",
                            False,
                        )
                original_assert(record)

            with patch.object(
                queue,
                "_assert_dial_identity",
                publish_before_assert,
            ):
                terminal = queue.finish_final_callback(reservation, 0)
            self.assertTrue(fired)
            self.assertTrue(terminal["terminal"])
            self.assertEqual(
                "synthetic_preclaim_terminal",
                terminal["error"]["code"],
            )
            self.assertFalse(terminal["error"]["may_have_started"])

    def test_unclaimed_crash_state_reconciles_on_restart(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            runtime_dir = Path(temporary)
            queue, _, _ = self.queue(runtime_dir)
            request_id = queue.submit(
                "send_message_to_thread",
                {"responder_thread_id": RESPONDER_THREAD_ID, "prompt": "crash recovery"},
                idempotency_key="unclaimed-crash-recovery",
            )
            reservation = queue.reserve_exact(request_id)
            with queue._dial_session() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "UPDATE pages SET state='unclaimed_claim_timeout' "
                    "WHERE page_id=? AND state='reserved'",
                    (reservation["page"],),
                )

            restarted = BeeperQueue(
                runtime_dir,
                root_name="beeper",
            )
            terminal = restarted.response(request_id)
            self.assertIsNotNone(terminal)
            self.assertEqual("beeper_claim_timeout", terminal["error"]["code"])
            self.assertFalse(terminal["error"]["may_have_started"])
            self.assertFalse(restarted.status().dial_inflight)
            with self.assertRaises(BeeperQueueProtocolError):
                restarted.claim_and_arm(reservation)

    def test_claim_wait_rejects_nonfinite_input(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue, _, _ = self.queue(Path(temporary))
            request_id = queue.submit(
                "send_message_to_thread",
                {"responder_thread_id": RESPONDER_THREAD_ID, "prompt": "finite only"},
                idempotency_key="finite-waits",
            )
            reservation = queue.reserve_exact(request_id)
            with self.assertRaises(BeeperQueueProtocolError):
                queue.wait_for_beeper_claim(reservation, float("nan"))
            with self.assertRaises(BeeperQueueProtocolError):
                queue.finish_final_callback(reservation, float("nan"))

    def test_generic_fail_cannot_forge_unclaimed_safe_codes(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue, _, _ = self.queue(Path(temporary))
            request_id = queue.submit(
                "send_message_to_thread",
                {"responder_thread_id": RESPONDER_THREAD_ID, "prompt": "forgery denied"},
                idempotency_key="safe-code-forgery",
            )
            reservation = queue.reserve_exact(request_id)
            queue.claim_and_arm(reservation)
            for code in ("beeper_claim_timeout", "beeper_load_assist_failed"):
                with self.subTest(code=code), self.assertRaises(BeeperQueueProtocolError):
                    queue.fail_page(reservation, code, False)
            self.assertIsNone(queue.response(request_id))
            queue.fail_page(reservation, "responder_result_unknown", True)

    def test_unclaimed_failure_cas_and_claim_are_exclusive(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            cases = (
                (
                    "send",
                    "send_message_to_thread",
                    {
                        "responder_thread_id": RESPONDER_THREAD_ID,
                        "responder_host_id": "responder-host",
                        "prompt": "CAS 只能有一个赢家",
                    },
                    "claimed_armed",
                    True,
                ),
                (
                    "readonly",
                    "list_task_catalog",
                    {
                        "catalog_version": 1,
                        "visibility": "all",
                        "thread_ids": [],
                        "include_archived": False,
                        "limit": 50,
                    },
                    "claimed_readonly",
                    False,
                ),
            )
            for suffix, operation, base_payload, expected_status, may_have_started in cases:
                with self.subTest(operation=operation):
                    runtime_dir = Path(temporary) / suffix
                    runtime_dir.mkdir()
                    queue, _, _ = self.queue(runtime_dir)
                    payload = dict(base_payload)
                    if operation == "list_task_catalog":
                        payload["excluded_thread_ids"] = list(
                            queue.excluded_thread_ids()
                        )
                    request_id = queue.submit(
                        operation,
                        payload,
                        idempotency_key=f"unclaimed-cas-race-{suffix}",
                    )
                    reservation = queue.reserve_exact(request_id)
                    barrier = threading.Barrier(2)

                    def claim_once() -> tuple[str, dict | None]:
                        barrier.wait()
                        try:
                            if operation == "send_message_to_thread":
                                claimed = queue.claim_and_arm(reservation)
                            else:
                                claimed = queue.claim_readonly(reservation)
                            return "claimed", claimed
                        except BeeperQueueProtocolError:
                            return "rejected", None

                    def fail_unclaimed_once() -> dict | None:
                        barrier.wait()
                        return queue.fail_page_if_unclaimed(
                            reservation,
                            "beeper_claim_timeout",
                        )

                    with ThreadPoolExecutor(max_workers=2) as executor:
                        claim_future = executor.submit(claim_once)
                        fail_future = executor.submit(fail_unclaimed_once)
                        claim_outcome, claimed = claim_future.result(timeout=3)
                        terminal = fail_future.result(timeout=3)

                    if claim_outcome == "claimed":
                        self.assertIsNotNone(claimed)
                        self.assertEqual(expected_status, claimed["status"])
                        self.assertIsNone(terminal)
                        self.assertIsNone(queue.response(request_id))
                        queue.fail_page(
                            reservation,
                            "responder_result_unknown",
                            may_have_started,
                        )
                    else:
                        self.assertEqual("rejected", claim_outcome)
                        self.assertIsNone(claimed)
                        self.assertIsNotNone(terminal)
                        self.assertEqual("failed", terminal["status"])
                        self.assertEqual(
                            "beeper_claim_timeout",
                            terminal["error"]["code"],
                        )
                        self.assertFalse(terminal["error"]["may_have_started"])
                        with self.assertRaises(BeeperQueueProtocolError):
                            if operation == "send_message_to_thread":
                                queue.claim_and_arm(reservation)
                            else:
                                queue.claim_readonly(reservation)

    def test_finish_waits_for_delayed_beeper_claim(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue, _, _ = self.queue(Path(temporary))
            prompt = "Beeper 稍后才消费 page"
            final = "延迟 claim 后的精确 Final Callback"
            request_id = queue.submit(
                "send_message_to_thread",
                {"responder_thread_id": RESPONDER_THREAD_ID, "prompt": prompt},
                idempotency_key="delayed-claim",
            )
            reservation = queue.reserve_exact(request_id)
            with ThreadPoolExecutor(max_workers=1) as executor:
                waiter = executor.submit(
                    queue.finish_final_callback,
                    reservation,
                    2,
                )
                time.sleep(0.05)
                claimed = queue.claim_and_arm(reservation["page"])
                self.assertEqual("claimed_armed", claimed["status"])
                final_callback_capability = self.final_callback_envelope(claimed["prompt"])[
                    "final_callback_capability"
                ]
                queue.submit_final_callback(
                    final_callback_capability,
                    final,
                )
                terminal = waiter.result(timeout=3)
            self.assertEqual("completed", terminal["status"])
            self.assertEqual("final_callback", terminal["final_callback_source"])
            self.assertEqual("", terminal["result"]["responder_turn_id"])
            self.assertEqual(final, terminal["result"]["final_answer"])

    def test_page_failure_before_claim_consumes_page_permanently(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            queue, _, _ = self.queue(Path(temporary))
            request_id = queue.submit(
                "send_message_to_thread",
                {"responder_thread_id": RESPONDER_THREAD_ID, "prompt": "never disclosed"},
                idempotency_key="pre-claim-failure",
            )
            reservation = queue.reserve_exact(request_id)
            terminal = queue.fail_page(
                reservation["page"],
                "queue_trigger_failed",
                False,
            )
            self.assertEqual("failed", terminal["status"])
            self.assertFalse(terminal["error"]["may_have_started"])
            with self.assertRaises(BeeperQueueProtocolError):
                queue.claim_and_arm(reservation)
            with self.assertRaises(BeeperQueueProtocolError):
                queue.reserve_exact(request_id)


if __name__ == "__main__":
    unittest.main()
