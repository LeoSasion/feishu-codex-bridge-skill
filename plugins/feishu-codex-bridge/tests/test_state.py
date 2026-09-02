from __future__ import annotations

import json
from pathlib import Path
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP_ROOT = Path(os.environ.get("FEISHU_BRIDGE_TEST_TMP", tempfile.gettempdir()))
if "FEISHU_BRIDGE_TEST_TMP" not in os.environ:
    TEST_TEMP_ROOT = TEST_TEMP_ROOT / "feishu-codex-bridge-tests"
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from bridge_core.legacy_identifiers import RETIRED_SESSION_OWNER  # noqa: E402
from bridge_core.state import (  # noqa: E402
    AccessPolicy,
    DurableState,
    INTERRUPTED_REPLY,
    SessionStore,
)


class DurableStateTests(unittest.TestCase):
    def test_dedup_completion_and_payload_erasure(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            state = DurableState(Path(temporary) / "state.sqlite3")
            try:
                self.assertTrue(state.enqueue("e1", "m1", "scope", {"content": "secret"}))
                self.assertFalse(state.enqueue("e1", "m1", "scope", {"content": "duplicate"}))
                self.assertTrue(state.claim("e1"))
                state.mark_reply_pending(
                    "e1",
                    "answer",
                    {
                        "schema_version": 1,
                        "pieces": [["text", "answer", ""]],
                        "fidelity": "identity",
                        "transforms": [],
                    },
                )
                state.mark_outbound_result("e1", "identity", ())
                state.mark_completed("e1")
                row = state.get("e1")
                self.assertEqual("completed", row["status"])
                self.assertIsNone(row["payload_json"])
                self.assertIsNone(row["answer"])
                self.assertIsNone(row["outbound_plan_json"])
                self.assertEqual("identity", row["outbound_fidelity"])
                self.assertEqual((), state.outbound_transforms(row))
                self.assertEqual(
                    {"fidelity": "identity", "transforms": []},
                    state.latest_delivery_fidelity(),
                )
            finally:
                state.close()

    def test_actionable_retry_count_excludes_only_the_historical_hold(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            state = DurableState(Path(temporary) / "state.sqlite3")
            try:
                for event_id, message_id, error in (
                    ("held", "message-held", "producer_unavailable_no_retry"),
                    ("actionable", "message-actionable", "temporary failure"),
                ):
                    self.assertTrue(
                        state.enqueue(event_id, message_id, "scope", {"text": event_id})
                    )
                    self.assertTrue(state.claim(event_id))
                    state.mark_retryable(event_id, error)

                self.assertEqual(
                    1,
                    state.actionable_retryable_failed_count(
                        "producer_unavailable_no_retry"
                    ),
                )
                self.assertEqual(
                    {
                        "queued",
                        "running",
                        "control_sending",
                        "reply_pending",
                        "retryable_failed",
                        "completed",
                        "terminal_failed",
                    },
                    set(state.status_counts()),
                )
            finally:
                state.close()

    def test_control_reply_content_is_never_durable_or_recoverable(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            path = Path(temporary) / "state.sqlite3"
            state = DurableState(path)
            try:
                self.assertTrue(
                    state.enqueue("control", "message-control", "scope", {"text": "/init"})
                )
                self.assertTrue(state.claim("control"))
                self.assertTrue(state.admit_control("control"))
                self.assertFalse(state.admit_control("control"))
                admitted = state.get("control")
                self.assertEqual("control_sending", admitted["status"])
                self.assertIsNone(admitted["payload_json"])
                self.assertIsNone(admitted["answer"])
                self.assertEqual([], state.recoverable())
                self.assertTrue(
                    state.finish_control_reply(
                        "control",
                        delivered=True,
                        fidelity="identity",
                        transforms=(),
                    )
                )
                self.assertEqual("completed", state.get("control")["status"])

                self.assertTrue(
                    state.enqueue("crash", "message-crash", "scope", {"text": "/init"})
                )
                self.assertTrue(state.claim("crash"))
                self.assertTrue(state.admit_control("crash"))
            finally:
                state.close()

            reopened = DurableState(path)
            try:
                crashed = reopened.get("crash")
                self.assertEqual("terminal_failed", crashed["status"])
                self.assertIsNone(crashed["payload_json"])
                self.assertNotIn("crash", {row["event_id"] for row in reopened.recoverable()})
            finally:
                reopened.close()

    def test_reply_retry_and_reopen_preserve_answer_free_transform_metadata(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            path = Path(temporary) / "state.sqlite3"
            state = DurableState(path)
            state.enqueue("e1", "m1", "scope", {"content": "secret"})
            state.claim("e1")
            plan = {
                "schema_version": 1,
                "pieces": [["text", "answer", ""]],
                "fidelity": "identity",
                "transforms": [],
            }
            state.mark_reply_pending("e1", "answer", plan)
            state.mark_outbound_result(
                "e1",
                "explicit_transform",
                ("chunking", "markdown"),
            )
            state.mark_reply_retry("e1", "network failure")
            state.close()

            recovered = DurableState(path)
            try:
                row = recovered.get("e1")
                self.assertEqual("reply_pending", row["status"])
                self.assertEqual("explicit_transform", row["outbound_fidelity"])
                self.assertEqual(plan, recovered.outbound_plan(row))
                verified_answer, verified_plan = recovered.verified_outbound(
                    "e1",
                    {
                        "event_id": "e1",
                        "message_id": "m1",
                        "_bridge_scope": "scope",
                    },
                )
                self.assertEqual("answer", verified_answer)
                self.assertEqual(plan, verified_plan)
                self.assertEqual(
                    ("chunking", "markdown"),
                    recovered.outbound_transforms(row),
                )
                self.assertEqual(
                    {"fidelity": "not_applicable", "transforms": []},
                    recovered.latest_delivery_fidelity(),
                )
            finally:
                recovered.close()

    def test_legacy_delivery_rows_migrate_to_unknown_without_inference(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            path = Path(temporary) / "state.sqlite3"
            connection = sqlite3.connect(path)
            connection.executescript(
                """
                CREATE TABLE inbox_events (
                    event_id TEXT PRIMARY KEY,
                    message_id TEXT NOT NULL UNIQUE,
                    scope TEXT NOT NULL,
                    payload_json TEXT,
                    status TEXT NOT NULL,
                    answer TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    reply_attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at REAL NOT NULL DEFAULT 0,
                    model_started INTEGER NOT NULL DEFAULT 0,
                    thread_id TEXT,
                    turn_id TEXT,
                    last_error TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                """
            )
            # Keep legacy rows inside the production 30-day terminal-retention window.
            current_time = time.time()
            for index, status in enumerate(("completed", "reply_pending", "queued"), 1):
                connection.execute(
                    "INSERT INTO inbox_events VALUES(?, ?, 'scope', NULL, ?, NULL, "
                    "0, 0, 0, 0, NULL, NULL, NULL, ?, ?)",
                    (
                        f"e{index}",
                        f"m{index}",
                        status,
                        current_time - index,
                        current_time - index,
                    ),
                )
            connection.commit()
            connection.close()

            state = DurableState(path)
            try:
                self.assertEqual("unknown", state.get("e1")["outbound_fidelity"])
                self.assertEqual("unknown", state.get("e2")["outbound_fidelity"])
                self.assertEqual("not_applicable", state.get("e3")["outbound_fidelity"])
                self.assertEqual(
                    {"fidelity": "unknown", "transforms": []},
                    state.latest_delivery_fidelity(),
                )
                schema = state._connection.execute(
                    "SELECT value FROM metadata WHERE key='schema_version'"
                ).fetchone()[0]
                self.assertEqual("7", schema)
            finally:
                state.close()

    def test_fidelity_metadata_rejects_unbounded_or_digest_like_values(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            state = DurableState(Path(temporary) / "state.sqlite3")
            try:
                state.enqueue("e1", "m1", "scope", {})
                with self.assertRaises(ValueError):
                    state.mark_outbound_result("e1", "sha256:secret", ())
                with self.assertRaises(ValueError):
                    state.mark_outbound_result(
                        "e1",
                        "explicit_transform",
                        ("answer-derived-label",),
                    )
                with self.assertRaises(ValueError):
                    state.mark_outbound_result("e1", "explicit_transform", ())
            finally:
                state.close()

    def test_late_fidelity_write_cannot_modify_a_terminal_row(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            state = DurableState(Path(temporary) / "state.sqlite3")
            try:
                state.enqueue("e1", "m1", "scope", {})
                state.claim("e1")
                state.mark_reply_pending(
                    "e1",
                    "answer",
                    {
                        "schema_version": 1,
                        "pieces": [["text", "answer", ""]],
                        "fidelity": "identity",
                        "transforms": [],
                    },
                )
                state.mark_completed("e1")
                state.mark_outbound_result("e1", "identity", ())
                self.assertEqual("not_applicable", state.get("e1")["outbound_fidelity"])
            finally:
                state.close()

    def test_terminal_row_cannot_be_reopened_or_replaced_by_late_outbox_writes(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            state = DurableState(Path(temporary) / "state.sqlite3")
            try:
                state.enqueue("e1", "m1", "scope", {"content": "secret"})
                state.claim("e1")
                state.mark_reply_pending(
                    "e1",
                    "answer",
                    {
                        "schema_version": 1,
                        "pieces": [["text", "answer", ""]],
                        "fidelity": "identity",
                        "transforms": [],
                    },
                )
                state.mark_terminal("e1", "fixed terminal")
                self.assertFalse(state.mark_reply_pending(
                    "e1",
                    "late answer",
                    {
                        "schema_version": 1,
                        "pieces": [["text", "late answer", ""]],
                        "fidelity": "identity",
                        "transforms": [],
                    },
                ))
                state.mark_reply_retry("e1", "late retry")
                state.mark_retryable("e1", "late model retry")
                state.mark_completed("e1")
                row = state.get("e1")
                self.assertEqual("terminal_failed", row["status"])
                self.assertIsNone(row["answer"])
                self.assertIsNone(row["outbound_plan_json"])
                self.assertIsNone(row["outbound_answer_sha256"])
                self.assertIsNone(row["outbound_answer_chars"])
                self.assertIsNone(row["outbound_plan_sha256"])
                self.assertIsNone(row["outbound_envelope_sha256"])
                self.assertEqual("fixed terminal", row["last_error"])
            finally:
                state.close()

    def test_outbound_answer_and_plan_are_frozen_once(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            state = DurableState(Path(temporary) / "state.sqlite3")
            try:
                state.enqueue("e1", "m1", "scope", {})
                state.claim("e1")
                first = {
                    "schema_version": 1,
                    "pieces": [["text", "first", ""]],
                    "fidelity": "identity",
                    "transforms": [],
                }
                second = {
                    "schema_version": 1,
                    "pieces": [["text", "second", ""]],
                    "fidelity": "identity",
                    "transforms": [],
                }
                self.assertTrue(state.mark_reply_pending("e1", "first", first))
                self.assertFalse(state.mark_reply_pending("e1", "second", second))
                row = state.get("e1")
                self.assertEqual("first", row["answer"])
                self.assertEqual(first, state.outbound_plan(row))
            finally:
                state.close()

    def test_outbound_envelope_binds_event_message_scope_answer_and_plan(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            state = DurableState(Path(temporary) / "state.sqlite3")
            try:
                answer = "  青岚🚀\r\n第二行尾部  \n"
                plan = {
                    "schema_version": 1,
                    "pieces": [["text", answer, ""]],
                    "fidelity": "identity",
                    "transforms": [],
                }
                event = {
                    "event_id": "e1",
                    "message_id": "m1",
                    "_bridge_scope": "scope:一",
                }
                state.enqueue("e1", "m1", "scope:一", {"content": "secret"})
                state.claim("e1")
                self.assertTrue(state.mark_reply_pending("e1", answer, plan))

                verified_answer, verified_plan = state.verified_outbound("e1", event)
                self.assertEqual(answer, verified_answer)
                self.assertEqual(plan, verified_plan)
                row = state.get("e1")
                self.assertEqual(len(answer), row["outbound_answer_chars"])
                for column in (
                    "outbound_answer_sha256",
                    "outbound_plan_sha256",
                    "outbound_envelope_sha256",
                ):
                    self.assertEqual(64, len(row[column]))

                for mismatched_event in (
                    {**event, "event_id": "other"},
                    {**event, "message_id": "other"},
                    {**event, "_bridge_scope": "scope:二"},
                ):
                    with self.subTest(event=mismatched_event):
                        with self.assertRaises(ValueError):
                            state.verified_outbound("e1", mismatched_event)

                no_scope = {"event_id": "e1", "message_id": "m1"}
                self.assertEqual(answer, state.verified_outbound("e1", no_scope)[0])
            finally:
                state.close()

    def test_outbound_envelope_tamper_fails_and_terminal_scrubs_seal(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            state = DurableState(Path(temporary) / "state.sqlite3")
            try:
                answer = "answer🚀"
                plan = {
                    "schema_version": 1,
                    "pieces": [["text", answer, ""]],
                    "fidelity": "identity",
                    "transforms": [],
                }
                event = {
                    "event_id": "e1",
                    "message_id": "m1",
                    "_bridge_scope": "scope",
                }
                state.enqueue("e1", "m1", "scope", {})
                state.claim("e1")
                state.mark_reply_pending("e1", answer, plan)
                original = state.get("e1")

                mutations = (
                    ("answer", "different"),
                    (
                        "outbound_plan_json",
                        '{"pieces":[["text","answer\\ud83d\\ude80",""]],'
                        '"schema_version":1,"fidelity":"identity","transforms":[]}',
                    ),
                    ("outbound_answer_chars", len(answer) + 1),
                    ("outbound_answer_sha256", "0" * 64),
                    ("outbound_plan_sha256", "1" * 64),
                    ("outbound_envelope_sha256", "2" * 64),
                )
                for column, value in mutations:
                    with self.subTest(column=column):
                        state._connection.execute(
                            f"UPDATE inbox_events SET {column}=? WHERE event_id='e1'",
                            (value,),
                        )
                        state._connection.commit()
                        with self.assertRaises(ValueError):
                            state.verified_outbound("e1", event)
                        state._connection.execute(
                            f"UPDATE inbox_events SET {column}=? WHERE event_id='e1'",
                            (original[column],),
                        )
                        state._connection.commit()

                state._connection.execute(
                    "UPDATE inbox_events SET scope='changed-scope' WHERE event_id='e1'"
                )
                state._connection.commit()
                with self.assertRaises(ValueError):
                    state.verified_outbound(
                        "e1",
                        {**event, "_bridge_scope": "changed-scope"},
                    )
                state._connection.execute(
                    "UPDATE inbox_events SET scope=? WHERE event_id='e1'",
                    (original["scope"],),
                )
                state._connection.commit()

                state._connection.execute(
                    "UPDATE inbox_events SET message_id='changed-message' "
                    "WHERE event_id='e1'"
                )
                state._connection.commit()
                with self.assertRaises(ValueError):
                    state.verified_outbound(
                        "e1",
                        {**event, "message_id": "changed-message"},
                    )
                state._connection.execute(
                    "UPDATE inbox_events SET message_id=? WHERE event_id='e1'",
                    (original["message_id"],),
                )
                state._connection.commit()

                state.mark_terminal("e1", "integrity failure")
                terminal = state.get("e1")
                for column in (
                    "answer",
                    "outbound_plan_json",
                    "outbound_answer_sha256",
                    "outbound_answer_chars",
                    "outbound_plan_sha256",
                    "outbound_envelope_sha256",
                ):
                    self.assertIsNone(terminal[column])
            finally:
                state.close()

    def test_legacy_unsealed_reply_pending_cannot_be_verified(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            state = DurableState(Path(temporary) / "state.sqlite3")
            try:
                plan = {
                    "schema_version": 1,
                    "pieces": [["text", "answer", ""]],
                    "fidelity": "identity",
                    "transforms": [],
                }
                state.enqueue("e1", "m1", "scope", {})
                state.claim("e1")
                state.mark_reply_pending("e1", "answer", plan)
                state._connection.execute(
                    "UPDATE inbox_events SET outbound_answer_sha256=NULL, "
                    "outbound_answer_chars=NULL, outbound_plan_sha256=NULL, "
                    "outbound_envelope_sha256=NULL WHERE event_id='e1'"
                )
                state._connection.commit()

                with self.assertRaises(ValueError):
                    state.verified_outbound(
                        "e1",
                        {
                            "event_id": "e1",
                            "message_id": "m1",
                            "_bridge_scope": "scope",
                        },
                    )
            finally:
                state.close()

    def test_claim_clears_every_stray_outbound_seal(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            state = DurableState(Path(temporary) / "state.sqlite3")
            try:
                state.enqueue("e1", "m1", "scope", {})
                state._connection.execute(
                    "UPDATE inbox_events SET outbound_plan_json='{}', "
                    "outbound_answer_sha256=?, outbound_answer_chars=7, "
                    "outbound_plan_sha256=?, outbound_envelope_sha256=? "
                    "WHERE event_id='e1'",
                    ("0" * 64, "1" * 64, "2" * 64),
                )
                state._connection.commit()

                self.assertTrue(state.claim("e1"))
                row = state.get("e1")
                for column in (
                    "outbound_plan_json",
                    "outbound_answer_sha256",
                    "outbound_answer_chars",
                    "outbound_plan_sha256",
                    "outbound_envelope_sha256",
                ):
                    self.assertIsNone(row[column])
            finally:
                state.close()

    def test_only_exact_startup_interruption_can_cas_fill_a_missing_plan(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            path = Path(temporary) / "state.sqlite3"
            state = DurableState(path)
            state.enqueue("e1", "m1", "scope", {})
            state.claim("e1")
            state.mark_model_started("e1", "thread", "turn")
            state.close()

            recovered = DurableState(path)
            try:
                plan = {
                    "schema_version": 1,
                    "pieces": [["text", INTERRUPTED_REPLY, ""]],
                    "fidelity": "identity",
                    "transforms": [],
                }
                self.assertFalse(
                    recovered.initialize_interrupted_reply_plan(
                        "e1", "wrong answer", plan
                    )
                )
                self.assertTrue(
                    recovered.initialize_interrupted_reply_plan(
                        "e1", INTERRUPTED_REPLY, plan
                    )
                )
                self.assertFalse(
                    recovered.initialize_interrupted_reply_plan(
                        "e1", INTERRUPTED_REPLY, plan
                    )
                )
                self.assertEqual(plan, recovered.outbound_plan(recovered.get("e1")))
                answer, verified_plan = recovered.verified_outbound(
                    "e1",
                    {
                        "event_id": "e1",
                        "message_id": "m1",
                        "_bridge_scope": "scope",
                    },
                )
                self.assertEqual(INTERRUPTED_REPLY, answer)
                self.assertEqual(plan, verified_plan)
            finally:
                recovered.close()

    def test_startup_scrubs_legacy_terminal_payload_answer_and_plan(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            path = Path(temporary) / "state.sqlite3"
            state = DurableState(path)
            state.enqueue("e1", "m1", "scope", {"content": "secret"})
            state.claim("e1")
            state.mark_reply_pending(
                "e1",
                "answer",
                {
                    "schema_version": 1,
                    "pieces": [["text", "answer", ""]],
                    "fidelity": "identity",
                    "transforms": [],
                },
            )
            with state._lock:
                state._connection.execute(
                    "UPDATE inbox_events SET status='completed' WHERE event_id='e1'"
                )
                state._connection.commit()
            state.close()

            recovered = DurableState(path)
            try:
                row = recovered.get("e1")
                self.assertEqual("completed", row["status"])
                self.assertIsNone(row["payload_json"])
                self.assertIsNone(row["answer"])
                self.assertIsNone(row["outbound_plan_json"])
                self.assertIsNone(row["outbound_answer_sha256"])
                self.assertIsNone(row["outbound_answer_chars"])
                self.assertIsNone(row["outbound_plan_sha256"])
                self.assertIsNone(row["outbound_envelope_sha256"])
            finally:
                recovered.close()

    def test_restart_does_not_rerun_a_started_model_turn(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            path = Path(temporary) / "state.sqlite3"
            state = DurableState(path)
            self.assertTrue(state.enqueue("e1", "m1", "scope", {"content": "work"}))
            self.assertTrue(state.claim("e1"))
            state.mark_model_started("e1", "thread", "turn")
            state.close()

            recovered = DurableState(path)
            try:
                row = recovered.get("e1")
                self.assertEqual("reply_pending", row["status"])
                self.assertEqual(INTERRUPTED_REPLY, row["answer"])
            finally:
                recovered.close()

    def test_restart_requeues_work_that_never_started_model(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            path = Path(temporary) / "state.sqlite3"
            state = DurableState(path)
            state.enqueue("e1", "m1", "scope", {"content": "work"})
            state.claim("e1")
            state.close()
            recovered = DurableState(path)
            try:
                self.assertEqual("queued", recovered.get("e1")["status"])
            finally:
                recovered.close()

    def test_proven_pre_delivery_failure_clears_provisional_model_start(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            path = Path(temporary) / "state.sqlite3"
            state = DurableState(path)
            state.enqueue("e1", "m1", "scope", {"content": "work"})
            state.claim("e1")
            state.mark_model_started("e1", "thread-old", "beeper-request")
            state.mark_responder_not_started("e1")
            row = state.get("e1")
            self.assertEqual(0, row["model_started"])
            self.assertIsNone(row["thread_id"])
            self.assertIsNone(row["turn_id"])
            state.close()

            recovered = DurableState(path)
            try:
                self.assertEqual("queued", recovered.get("e1")["status"])
            finally:
                recovered.close()


class AccessAndSessionTests(unittest.TestCase):
    def test_empty_locked_access_policy_denies_every_sender(self) -> None:
        policy = AccessPolicy(
            mode="locked",
            owner_open_id="",
            admin_open_ids=frozenset(),
            allowed_user_open_ids=frozenset(),
            allowed_chat_ids=frozenset(),
        )
        decision = policy.decide(
            sender_open_id="ou_unlisted",
            chat_id="oc_unlisted",
            chat_type="p2p",
        )
        self.assertFalse(decision.allowed)

    def test_locked_access_policy(self) -> None:
        policy = AccessPolicy(
            mode="locked",
            owner_open_id="owner",
            admin_open_ids=frozenset({"admin"}),
            allowed_user_open_ids=frozenset({"user"}),
            allowed_chat_ids=frozenset({"chat"}),
        )
        self.assertEqual("owner", policy.decide(sender_open_id="owner", chat_id="", chat_type="p2p").role)
        self.assertTrue(policy.decide(sender_open_id="user", chat_id="", chat_type="p2p").allowed)
        self.assertTrue(policy.decide(sender_open_id="stranger", chat_id="chat", chat_type="group").allowed)
        self.assertFalse(policy.decide(sender_open_id="stranger", chat_id="other", chat_type="group").allowed)

    def test_reset_preserves_previous_thread_identifier(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            store = SessionStore(Path(temporary) / "sessions.json")
            store.bind_thread("scope", "thread-1", {"name": "Alice"})
            self.assertTrue(store.reset_thread("scope"))
            session = store.get("scope")
            self.assertNotIn("thread_id", session)
            self.assertEqual(["thread-1"], session["previous_thread_ids"])

    def test_legacy_session_file_keeps_metadata_but_requires_rebinding(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            path = Path(temporary) / "sessions.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "session_owner": "desktop",
                        "sessions": {
                            "scope": {
                                "thread_id": "thread-legacy",
                                "session_id": "legacy-alias",
                                "name": "Alice",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            store = SessionStore(path)
            session = store.get("scope")
            self.assertEqual("Alice", session["name"])
            self.assertNotIn("thread_id", session)
            self.assertNotIn("session_id", session)
            self.assertEqual(["thread-legacy"], session["previous_thread_ids"])
            self.assertTrue(session["binding_migrated"])
            migrated = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(5, migrated["schema_version"])
            self.assertEqual("beeper", migrated["session_owner"])

    def test_version_three_responder_binding_is_preserved_for_beeper_queue(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            path = Path(temporary) / "sessions.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 3,
                        "session_owner": "personal-remote",
                        "sessions": {
                            "scope": {
                                "thread_id": "thread-valid",
                                "name": "Alice",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            store = SessionStore(path)
            self.assertEqual("thread-valid", store.get("scope")["thread_id"])
            migrated = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(5, migrated["schema_version"])
            self.assertEqual("beeper", migrated["session_owner"])

    def test_persisted_catalog_snapshot_is_redacted_on_load(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            path = Path(temporary) / "sessions.json"
            private_root = str(Path(temporary) / "private-project")
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 4,
                        "session_owner": RETIRED_SESSION_OWNER,
                        "sessions": {
                            "scope": {
                                "init_wizard": {
                                    "catalog": {
                                        "projects": [{"root": private_root}],
                                        "tasks": [{"title": "private title"}],
                                    }
                                }
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            store = SessionStore(path)

            session = store.get("scope")
            self.assertNotIn("init_wizard", session)
            self.assertGreater(session["init_wizard_expires_at"], 0.0)
            persisted = path.read_text(encoding="utf-8")
            self.assertNotIn(private_root, persisted)
            self.assertNotIn("private title", persisted)

    def test_expired_init_marker_remains_a_stale_reply_tombstone(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            store = SessionStore(Path(temporary) / "sessions.json")
            store.update("scope", {"init_wizard_expires_at": 10.0})
            reopened = SessionStore(Path(temporary) / "sessions.json")
            self.assertEqual(10.0, reopened.get("scope")["init_wizard_expires_at"])

    def test_binding_is_unique_and_rebinding_preserves_previous_id(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            store = SessionStore(Path(temporary) / "sessions.json")
            store.bind_thread("scope-a", "thread-a", {"name": "Alice"})
            with self.assertRaises(ValueError):
                store.bind_thread("scope-b", "thread-a", {"name": "Group"})
            rebound = store.bind_thread("scope-a", "thread-b")
            self.assertEqual("thread-b", rebound["thread_id"])
            self.assertEqual(["thread-a"], rebound["previous_thread_ids"])

    def test_catalog_binding_is_cas_persisted_without_display_or_path_metadata(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            path = Path(temporary) / "sessions.json"
            store = SessionStore(path)
            store.bind_thread("scope", "thread-old", {"name": "Alice"})
            store.record_project_route(
                "scope",
                project_id="legacy-route",
                name="Legacy Label",
                root=str(Path(temporary) / "legacy-root"),
                thread_id="thread-old",
                managed=False,
            )

            bound = store.bind_thread_if_current(
                "scope",
                "thread-new",
                expected_thread_id="thread-old",
                host_id="local",
                project_id="opaque-project-id",
                operation_receipt="a" * 32,
            )

            self.assertEqual("thread-new", bound["thread_id"])
            self.assertEqual("local", bound["host_id"])
            self.assertEqual("opaque-project-id", bound["desktop_project_id"])
            self.assertEqual("a" * 32, bound["binding_operation_receipt"])
            self.assertEqual(["thread-old"], bound["previous_thread_ids"])
            self.assertNotIn("active_project_id", bound)
            self.assertIn("legacy-route", bound["project_routes"])
            persisted = path.read_text(encoding="utf-8")
            self.assertNotIn("New Catalog Label", persisted)
            self.assertNotIn("new-catalog-root", persisted)

    def test_catalog_binding_uniqueness_ignores_retained_routes_but_not_active_binding(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            store = SessionStore(Path(temporary) / "sessions.json")
            store.bind_thread("scope-a", "thread-old")
            store.bind_thread("scope-b", "thread-active")
            store.record_project_route(
                "scope-b",
                project_id="retained-route",
                name="Retained",
                root=str(Path(temporary) / "retained"),
                thread_id="thread-candidate",
                managed=False,
                activate=False,
            )
            store.bind_thread_if_current(
                "scope-a",
                "thread-candidate",
                expected_thread_id="thread-old",
                host_id="local",
                project_id="opaque-project",
                operation_receipt="b" * 32,
            )
            with self.assertRaises(ValueError):
                store.bind_thread_if_current(
                    "scope-b",
                    "thread-candidate",
                    expected_thread_id="thread-active",
                    host_id="local",
                    project_id="opaque-project",
                    operation_receipt="c" * 32,
                )

    def test_catalog_binding_rolls_back_memory_when_persistence_fails(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            store = SessionStore(Path(temporary) / "sessions.json")
            store.bind_thread("scope", "thread-old", {"host_id": "old-host"})
            with patch.object(store, "_save_locked", side_effect=OSError("locked")):
                with self.assertRaises(OSError):
                    store.bind_thread_if_current(
                        "scope",
                        "thread-new",
                        expected_thread_id="thread-old",
                        host_id="local",
                        project_id="opaque-project",
                        operation_receipt="d" * 32,
                    )
            current = store.get("scope")
            self.assertEqual("thread-old", current["thread_id"])
            self.assertEqual("old-host", current["host_id"])
            self.assertNotIn("binding_operation_receipt", current)

    def test_same_display_name_in_distinct_chats_keeps_distinct_bindings(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            store = SessionStore(Path(temporary) / "sessions.json")
            store.bind_thread("p2p:chat-a", "thread-a", {"name": "Same Display Name"})
            store.bind_thread("p2p:chat-b", "thread-b", {"name": "Same Display Name"})

            self.assertEqual("thread-a", store.get("p2p:chat-a")["thread_id"])
            self.assertEqual("thread-b", store.get("p2p:chat-b")["thread_id"])
            self.assertEqual("p2p:chat-a", store.find_scope_by_thread("thread-a"))
            self.assertEqual("p2p:chat-b", store.find_scope_by_thread("thread-b"))

    def test_replace_consolidates_policy_scopes_and_preserves_history(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            store = SessionStore(Path(temporary) / "sessions.json")
            old_scope = "p2p:chat-1"
            new_scope = "p2p:chat-1:policy:owner-policy"
            store.bind_thread(old_scope, "thread-old", {"name": "Alice"})
            store.bind_thread(new_scope, "thread-duplicate", {"name": "Alice"})

            self.assertEqual(
                ["thread-old", "thread-duplicate"],
                store.related_thread_ids(new_scope),
            )
            replaced = store.replace_thread(new_scope, "thread-new", {"role": "owner"})

            self.assertEqual("thread-new", replaced["thread_id"])
            self.assertEqual(
                ["thread-old", "thread-duplicate"],
                replaced["previous_thread_ids"],
            )
            self.assertNotIn("thread_id", store.get(old_scope))

    def test_canonical_scope_consolidates_role_variants_without_forking_binding(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            store = SessionStore(Path(temporary) / "sessions.json")
            store.update(
                "group:chat-1:policy:owner",
                {"thread_id": "thread-one", "host_id": "host", "role": "owner"},
            )
            store.update(
                "group:chat-1:policy:guest",
                {"thread_id": "thread-one", "host_id": "host", "role": "guest"},
            )

            canonical = store.consolidate_scope("group:chat-1")

            self.assertEqual("thread-one", canonical["thread_id"])
            self.assertEqual("thread-one", store.get("group:chat-1")["thread_id"])
            self.assertEqual({}, store.get("group:chat-1:policy:owner"))
            self.assertEqual({}, store.get("group:chat-1:policy:guest"))

    def test_related_responder_history_is_bounded_and_keeps_current(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            store = SessionStore(Path(temporary) / "sessions.json")
            store.update(
                "group:chat-1",
                {
                    "previous_thread_ids": [f"thread-{index}" for index in range(30)],
                    "thread_id": "thread-current",
                },
            )

            related = store.related_thread_ids("group:chat-1")

            self.assertEqual(20, len(related))
            self.assertEqual("thread-current", related[-1])

    def test_project_routes_switch_threads_without_mixing_project_history(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            store = SessionStore(Path(temporary) / "sessions.json")
            scope = "group:chat-1"
            store.bind_thread(
                scope,
                "thread-default",
                {"previous_thread_ids": ["thread-default-old"]},
            )
            store.record_project_route(
                scope,
                project_id="default-id",
                name="Bridge Project",
                root=str(Path(temporary) / "bridge"),
                thread_id="thread-default",
                managed=False,
            )
            switched = store.record_project_route(
                scope,
                project_id="new-id",
                name="New Project",
                root=str(Path(temporary) / "new"),
                thread_id="thread-new",
                managed=True,
                binding_values={
                    "name": "群聊",
                    "host_id": "host-new",
                    "thread_id": "forbidden-override",
                    "active_project_id": "forbidden-project",
                },
            )

            self.assertEqual("thread-new", switched["thread_id"])
            self.assertEqual("new-id", switched["active_project_id"])
            self.assertNotIn("previous_thread_ids", switched)
            self.assertEqual("群聊", switched["name"])
            self.assertEqual("host-new", switched["host_id"])

            default_route = store.find_project_route(scope, "default-id")
            restored = store.record_project_route(
                scope,
                project_id=default_route["id"],
                name=default_route["name"],
                root=default_route["root"],
                thread_id=default_route["thread_id"],
                managed=False,
            )
            self.assertEqual("thread-default", restored["thread_id"])
            self.assertEqual(["thread-default-old"], restored["previous_thread_ids"])

    def test_same_project_names_in_distinct_chats_keep_distinct_routes(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            store = SessionStore(Path(temporary) / "sessions.json")
            for scope, thread_id, route_id, suffix in (
                ("group:one", "thread-one", "route-one", "one"),
                ("group:two", "thread-two", "route-two", "two"),
            ):
                store.record_project_route(
                    scope,
                    project_id=route_id,
                    name="同名项目",
                    root=str(Path(temporary) / suffix),
                    thread_id=thread_id,
                    managed=True,
                )

            self.assertEqual("thread-one", store.find_project_route("group:one", "同名项目")["thread_id"])
            self.assertEqual("thread-two", store.find_project_route("group:two", "同名项目")["thread_id"])

    def test_temporary_binding_restores_exact_unbound_baseline_after_delivery_route(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            store = SessionStore(Path(temporary) / "sessions.json")
            scope = "p2p:chat-one:policy:owner"
            baseline = store.update(
                scope,
                {
                    "name": "Alice",
                    "role": "owner",
                    "policy_fingerprint": "policy-one",
                    "reply_mode": "quiet",
                },
            )
            scope_hash = __import__("hashlib").sha256(scope.encode("utf-8")).hexdigest()[:12]
            self.assertEqual(scope, store.resolve_scope_hash(scope_hash))

            bound = store.begin_temporary_binding(
                scope,
                thread_id="thread-temporary-responder",
                host_id="local",
                transaction_id="a" * 32,
                project_root_sha256="b" * 64,
            )
            self.assertEqual("a" * 32, bound["transaction_id"])
            store.record_project_route(
                scope,
                project_id="route-one",
                name="Bridge",
                root=str(Path(temporary) / "bridge"),
                thread_id=bound["thread_id"],
                managed=False,
                activate=True,
            )
            store.bind_thread(scope, bound["thread_id"], {"host_id": "local"})

            restored = store.rollback_temporary_binding(scope, transaction_id="a" * 32)
            self.assertTrue(restored["restored_unbound"])
            session = store.get(scope)
            self.assertNotIn("thread_id", session)
            self.assertNotIn("active_project_id", session)
            self.assertNotIn("project_routes", session)
            self.assertNotIn(SessionStore.TEMPORARY_BINDING_FIELD, session)
            for key in ("name", "role", "policy_fingerprint", "reply_mode"):
                self.assertEqual(baseline[key], session[key])

    def test_temporary_binding_fails_closed_after_responder_or_transaction_changes(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            store = SessionStore(Path(temporary) / "sessions.json")
            scope = "p2p:chat-one"
            store.update(scope, {"name": "Alice"})
            store.begin_temporary_binding(
                scope,
                thread_id="thread-responder",
                host_id="local",
                transaction_id="c" * 32,
                project_root_sha256="d" * 64,
            )
            with self.assertRaises(ValueError):
                store.rollback_temporary_binding(scope, transaction_id="e" * 32)
            store.update(scope, {"thread_id": "thread-different"})
            with self.assertRaises(ValueError):
                store.rollback_temporary_binding(scope, transaction_id="c" * 32)


if __name__ == "__main__":
    unittest.main()
