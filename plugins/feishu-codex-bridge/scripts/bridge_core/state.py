"""Transactional runtime state, access policy, and session persistence."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import math
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any

from .legacy_identifiers import RETIRED_SESSION_OWNER


SCHEMA_VERSION = 7
RECOVERABLE_STATUSES = ("queued", "retryable_failed", "reply_pending")
INBOX_STATUSES = (
    "queued",
    "running",
    "control_sending",
    "reply_pending",
    "retryable_failed",
    "completed",
    "terminal_failed",
)
INTERRUPTED_REPLY = "桥接服务在本轮执行期间重启了。为避免重复执行操作，本轮没有自动重跑；请重新发送一次。"
OUTBOUND_ENVELOPE_DOMAIN = b"feishu-codex-bridge/outbound-envelope/v1\0"
OUTBOUND_FIDELITIES = frozenset(
    {"identity", "explicit_transform", "unknown", "not_applicable"}
)
OUTBOUND_TRANSFORMS = frozenset(
    {
        "attachment_marker",
        "attachment_omitted",
        "chunking",
        "empty_fallback",
        "markdown",
    }
)


class DurableState:
    """Small SQLite inbox/outbox that avoids replaying completed message bodies."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self._connection.row_factory = sqlite3.Row
        with self._lock:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA synchronous=NORMAL")
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS inbox_events (
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
                    outbound_fidelity TEXT NOT NULL DEFAULT 'not_applicable',
                    outbound_transforms_json TEXT NOT NULL DEFAULT '[]',
                    outbound_plan_json TEXT,
                    outbound_answer_sha256 TEXT,
                    outbound_answer_chars INTEGER,
                    outbound_plan_sha256 TEXT,
                    outbound_envelope_sha256 TEXT,
                    last_error TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS inbox_scope_status
                    ON inbox_events(scope, status, created_at);
                """
            )
            columns = {
                str(row[1])
                for row in self._connection.execute("PRAGMA table_info(inbox_events)").fetchall()
            }
            added_columns: set[str] = set()
            for name, declaration in (
                ("model_started", "INTEGER NOT NULL DEFAULT 0"),
                ("reply_attempts", "INTEGER NOT NULL DEFAULT 0"),
                ("next_attempt_at", "REAL NOT NULL DEFAULT 0"),
                ("thread_id", "TEXT"),
                ("turn_id", "TEXT"),
                ("outbound_fidelity", "TEXT NOT NULL DEFAULT 'not_applicable'"),
                ("outbound_transforms_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("outbound_plan_json", "TEXT"),
                ("outbound_answer_sha256", "TEXT"),
                ("outbound_answer_chars", "INTEGER"),
                ("outbound_plan_sha256", "TEXT"),
                ("outbound_envelope_sha256", "TEXT"),
            ):
                if name not in columns:
                    self._connection.execute(
                        f"ALTER TABLE inbox_events ADD COLUMN {name} {declaration}"
                    )
                    added_columns.add(name)
            if "outbound_fidelity" in added_columns:
                # A legacy terminal or outbox row may already have crossed the
                # Lark boundary, but the old schema retained no evidence about
                # that transformation. Never infer identity during migration.
                self._connection.execute(
                    "UPDATE inbox_events SET outbound_fidelity='unknown' "
                    "WHERE status IN ('reply_pending', 'completed', 'terminal_failed')"
                )
            self._connection.execute(
                "UPDATE inbox_events SET outbound_fidelity='unknown' "
                "WHERE outbound_fidelity IS NULL OR outbound_fidelity NOT IN "
                "('identity', 'explicit_transform', 'unknown', 'not_applicable')"
            )
            self._connection.execute(
                "UPDATE inbox_events SET outbound_transforms_json='[]' "
                "WHERE outbound_transforms_json IS NULL OR outbound_transforms_json=''"
            )
            self._connection.execute(
                "UPDATE inbox_events SET payload_json=NULL, answer=NULL, "
                "model_started=0, next_attempt_at=0, thread_id=NULL, turn_id=NULL, "
                "outbound_plan_json=NULL, outbound_answer_sha256=NULL, "
                "outbound_answer_chars=NULL, outbound_plan_sha256=NULL, "
                "outbound_envelope_sha256=NULL "
                "WHERE status IN ('completed','terminal_failed')"
            )
            self._connection.execute(
                "UPDATE inbox_events SET outbound_answer_sha256=NULL, "
                "outbound_answer_chars=NULL, outbound_plan_sha256=NULL, "
                "outbound_envelope_sha256=NULL "
                "WHERE status!='reply_pending'"
            )
            self._connection.execute(
                "INSERT INTO metadata(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )
            now = time.time()
            self._connection.execute(
                "UPDATE inbox_events SET status='terminal_failed', answer=NULL, "
                "payload_json=NULL, model_started=0, next_attempt_at=0, "
                "thread_id=NULL, turn_id=NULL, outbound_plan_json=NULL, "
                "outbound_answer_sha256=NULL, outbound_answer_chars=NULL, "
                "outbound_plan_sha256=NULL, outbound_envelope_sha256=NULL, "
                "last_error='control reply interrupted after single-attempt admission', "
                "updated_at=? WHERE status='control_sending'",
                (now,),
            )
            self._connection.execute(
                "UPDATE inbox_events SET status='queued', "
                "outbound_fidelity='not_applicable', outbound_transforms_json='[]', "
                "outbound_plan_json=NULL, outbound_answer_sha256=NULL, "
                "outbound_answer_chars=NULL, outbound_plan_sha256=NULL, "
                "outbound_envelope_sha256=NULL, "
                "updated_at=? "
                "WHERE status='running' AND COALESCE(model_started, 0)=0",
                (now,),
            )
            self._connection.execute(
                "UPDATE inbox_events SET status='reply_pending', answer=?, last_error=?, "
                "outbound_fidelity='not_applicable', outbound_transforms_json='[]', "
                "outbound_plan_json=NULL, outbound_answer_sha256=NULL, "
                "outbound_answer_chars=NULL, outbound_plan_sha256=NULL, "
                "outbound_envelope_sha256=NULL, "
                "updated_at=? "
                "WHERE status='running' AND COALESCE(model_started, 0)=1",
                (INTERRUPTED_REPLY, "bridge restarted after model turn started", now),
            )
            self._connection.execute(
                "DELETE FROM inbox_events WHERE status IN ('completed', 'terminal_failed') "
                "AND updated_at < ?",
                (now - 30 * 86400,),
            )
            self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def enqueue(self, event_id: str, message_id: str, scope: str, payload: dict[str, Any]) -> bool:
        now = time.time()
        with self._lock:
            cursor = self._connection.execute(
                """
                INSERT OR IGNORE INTO inbox_events(
                    event_id, message_id, scope, payload_json, status, created_at, updated_at
                ) VALUES(?, ?, ?, ?, 'queued', ?, ?)
                """,
                (event_id, message_id, scope, json.dumps(payload, ensure_ascii=False), now, now),
            )
            self._connection.commit()
            return cursor.rowcount == 1

    def get(self, event_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM inbox_events WHERE event_id=?", (event_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    @staticmethod
    def payload(row: dict[str, Any]) -> dict[str, Any] | None:
        raw = row.get("payload_json")
        if not isinstance(raw, str) or not raw:
            return None
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None

    def recoverable(self) -> list[dict[str, Any]]:
        placeholders = ",".join("?" for _ in RECOVERABLE_STATUSES)
        with self._lock:
            rows = self._connection.execute(
                f"SELECT * FROM inbox_events WHERE status IN ({placeholders}) "
                "AND COALESCE(next_attempt_at, 0)<=? ORDER BY created_at",
                (*RECOVERABLE_STATUSES, time.time()),
            ).fetchall()
        return [dict(row) for row in rows]

    def claim(self, event_id: str) -> bool:
        """Atomically reserve a queued event for one worker."""

        now = time.time()
        with self._lock:
            cursor = self._connection.execute(
                """
                UPDATE inbox_events
                SET status='running', answer=NULL, last_error=NULL,
                    attempts=attempts+1, model_started=0,
                    thread_id=NULL, turn_id=NULL, next_attempt_at=0,
                    outbound_fidelity='not_applicable', outbound_transforms_json='[]',
                    outbound_plan_json=NULL, outbound_answer_sha256=NULL,
                    outbound_answer_chars=NULL, outbound_plan_sha256=NULL,
                    outbound_envelope_sha256=NULL,
                    updated_at=?
                WHERE event_id=? AND status IN ('queued', 'retryable_failed')
                """,
                (now, event_id),
            )
            self._connection.commit()
            return cursor.rowcount == 1

    def mark_model_started(self, event_id: str, thread_id: str, turn_id: str) -> None:
        with self._lock:
            self._connection.execute(
                """
                UPDATE inbox_events
                SET model_started=1, thread_id=?, turn_id=?, updated_at=?
                WHERE event_id=? AND status='running'
                """,
                (thread_id, turn_id, time.time(), event_id),
            )
            self._connection.commit()

    def mark_responder_not_started(self, event_id: str) -> None:
        """Clear a provisional Beeper claim after a proven pre-delivery failure."""

        with self._lock:
            self._connection.execute(
                """
                UPDATE inbox_events
                SET model_started=0, thread_id=NULL, turn_id=NULL, updated_at=?
                WHERE event_id=? AND status='running'
                """,
                (time.time(), event_id),
            )
            self._connection.commit()

    @staticmethod
    def _encode_outbound_plan(outbound_plan: dict[str, Any] | None) -> str | None:
        if outbound_plan is None:
            return None
        if not isinstance(outbound_plan, dict):
            raise ValueError("outbound plan must be one JSON object")
        try:
            plan_json = json.dumps(
                outbound_plan,
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("outbound plan must be strict JSON") from exc
        if len(plan_json) > 250_000:
            raise ValueError("outbound plan is too large")
        return plan_json

    @staticmethod
    def _outbound_envelope_sha256(
        event_id: str,
        message_id: str,
        scope: str,
        answer: str,
        plan_json: str,
    ) -> str:
        """Hash one domain-separated, length-framed immutable outbox envelope."""

        values = (event_id, message_id, scope, answer)
        if any(not isinstance(value, str) or not value for value in values[:3]):
            raise ValueError("outbound envelope identity is invalid")
        if not isinstance(answer, str) or not isinstance(plan_json, str) or not plan_json:
            raise ValueError("outbound envelope content is invalid")
        digest = hashlib.sha256()
        digest.update(OUTBOUND_ENVELOPE_DOMAIN)
        for value, encoding in (
            (event_id, "utf-8"),
            (message_id, "utf-8"),
            (scope, "utf-8"),
            (answer, "utf-8"),
            (plan_json, "ascii"),
        ):
            encoded = value.encode(encoding)
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
        return digest.hexdigest()

    @classmethod
    def _outbound_seal(
        cls,
        event_id: str,
        message_id: str,
        scope: str,
        answer: str,
        plan_json: str,
    ) -> tuple[str, int, str, str]:
        if not isinstance(answer, str):
            raise ValueError("outbound answer must be text")
        try:
            plan_bytes = plan_json.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ValueError("outbound plan must be canonical ASCII JSON") from exc
        answer_sha256 = hashlib.sha256(answer.encode("utf-8")).hexdigest()
        plan_sha256 = hashlib.sha256(plan_bytes).hexdigest()
        envelope_sha256 = cls._outbound_envelope_sha256(
            event_id,
            message_id,
            scope,
            answer,
            plan_json,
        )
        return answer_sha256, len(answer), plan_sha256, envelope_sha256

    def mark_reply_pending(
        self,
        event_id: str,
        answer: str,
        outbound_plan: dict[str, Any] | None = None,
    ) -> bool:
        """Freeze the first outbound answer/plan exactly once from running."""

        plan_json = self._encode_outbound_plan(outbound_plan)
        if plan_json is None:
            raise ValueError("outbound plan is required")
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    "SELECT message_id, scope FROM inbox_events "
                    "WHERE event_id=? AND status='running'",
                    (event_id,),
                ).fetchone()
                if row is None:
                    self._connection.rollback()
                    return False
                answer_sha256, answer_chars, plan_sha256, envelope_sha256 = (
                    self._outbound_seal(
                        event_id,
                        str(row["message_id"]),
                        str(row["scope"]),
                        answer,
                        plan_json,
                    )
                )
                cursor = self._connection.execute(
                    """
                    UPDATE inbox_events
                    SET status='reply_pending', answer=?, last_error=NULL,
                        next_attempt_at=0, outbound_fidelity='not_applicable',
                        outbound_transforms_json='[]', outbound_plan_json=?,
                        outbound_answer_sha256=?, outbound_answer_chars=?,
                        outbound_plan_sha256=?, outbound_envelope_sha256=?, updated_at=?
                    WHERE event_id=? AND status='running'
                    """,
                    (
                        answer,
                        plan_json,
                        answer_sha256,
                        answer_chars,
                        plan_sha256,
                        envelope_sha256,
                        time.time(),
                        event_id,
                    ),
                )
                self._connection.commit()
                return cursor.rowcount == 1
            except Exception:
                self._connection.rollback()
                raise

    def admit_control(self, event_id: str) -> bool:
        """Consume control admission before any wizard, binding, or Beeper action."""

        with self._lock:
            cursor = self._connection.execute(
                """
                UPDATE inbox_events
                SET status='control_sending', payload_json=NULL, answer=NULL,
                    last_error='control reply single-attempt admission consumed',
                    next_attempt_at=0, model_started=0, thread_id=NULL, turn_id=NULL,
                    outbound_fidelity='not_applicable', outbound_transforms_json='[]',
                    outbound_plan_json=NULL, outbound_answer_sha256=NULL,
                    outbound_answer_chars=NULL, outbound_plan_sha256=NULL,
                    outbound_envelope_sha256=NULL, updated_at=?
                WHERE event_id=? AND status='running'
                """,
                (time.time(), event_id),
            )
            self._connection.commit()
            return cursor.rowcount == 1

    def begin_control_reply(self, event_id: str) -> bool:
        """Compatibility alias for callers that admit immediately before sending."""

        return self.admit_control(event_id)

    def finish_control_reply(
        self,
        event_id: str,
        *,
        delivered: bool,
        fidelity: str,
        transforms: tuple[str, ...] | list[str],
        error_code: str = "",
    ) -> bool:
        """Record one control send terminally; its text and plan never enter SQLite."""

        normalized = tuple(dict.fromkeys(str(item) for item in transforms))
        if fidelity not in OUTBOUND_FIDELITIES:
            fidelity = "unknown"
            normalized = ()
        if any(item not in OUTBOUND_TRANSFORMS for item in normalized):
            fidelity = "unknown"
            normalized = ()
        if fidelity == "explicit_transform" and not normalized:
            fidelity = "unknown"
        if fidelity != "explicit_transform":
            normalized = ()
        status = "completed" if delivered else "terminal_failed"
        failure = None
        if not delivered:
            suffix = error_code.strip()[:120]
            failure = "control reply failed after single-attempt admission"
            if suffix:
                failure += f" code={suffix}"
        with self._lock:
            cursor = self._connection.execute(
                """
                UPDATE inbox_events
                SET status=?, last_error=?, outbound_fidelity=?,
                    outbound_transforms_json=?, updated_at=?
                WHERE event_id=? AND status='control_sending'
                """,
                (
                    status,
                    failure,
                    fidelity,
                    json.dumps(normalized, ensure_ascii=True, separators=(",", ":")),
                    time.time(),
                    event_id,
                ),
            )
            self._connection.commit()
            return cursor.rowcount == 1

    def initialize_interrupted_reply_plan(
        self,
        event_id: str,
        expected_answer: str,
        outbound_plan: dict[str, Any],
    ) -> bool:
        """CAS-fill only the legacy startup interruption row that has no plan."""

        plan_json = self._encode_outbound_plan(outbound_plan)
        if plan_json is None:
            raise ValueError("outbound plan is required")
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    """
                    SELECT message_id, scope FROM inbox_events
                    WHERE event_id=? AND status='reply_pending'
                      AND answer=? AND outbound_plan_json IS NULL
                      AND outbound_answer_sha256 IS NULL
                      AND outbound_answer_chars IS NULL
                      AND outbound_plan_sha256 IS NULL
                      AND outbound_envelope_sha256 IS NULL
                      AND last_error='bridge restarted after model turn started'
                    """,
                    (event_id, expected_answer),
                ).fetchone()
                if row is None:
                    self._connection.rollback()
                    return False
                answer_sha256, answer_chars, plan_sha256, envelope_sha256 = (
                    self._outbound_seal(
                        event_id,
                        str(row["message_id"]),
                        str(row["scope"]),
                        expected_answer,
                        plan_json,
                    )
                )
                cursor = self._connection.execute(
                    """
                    UPDATE inbox_events
                    SET outbound_plan_json=?, outbound_answer_sha256=?,
                        outbound_answer_chars=?, outbound_plan_sha256=?,
                        outbound_envelope_sha256=?, last_error=NULL, updated_at=?
                    WHERE event_id=? AND status='reply_pending'
                      AND answer=? AND outbound_plan_json IS NULL
                      AND outbound_answer_sha256 IS NULL
                      AND outbound_answer_chars IS NULL
                      AND outbound_plan_sha256 IS NULL
                      AND outbound_envelope_sha256 IS NULL
                      AND last_error='bridge restarted after model turn started'
                    """,
                    (
                        plan_json,
                        answer_sha256,
                        answer_chars,
                        plan_sha256,
                        envelope_sha256,
                        time.time(),
                        event_id,
                        expected_answer,
                    ),
                )
                self._connection.commit()
                return cursor.rowcount == 1
            except Exception:
                self._connection.rollback()
                raise

    @staticmethod
    def outbound_plan(row: dict[str, Any]) -> dict[str, Any] | None:
        raw = row.get("outbound_plan_json")
        if not isinstance(raw, str) or not raw:
            return None
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None

    def verified_outbound(
        self,
        event_id: str,
        event: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        """Return only a fully sealed pending answer/plan for the exact event."""

        if not isinstance(event_id, str) or not event_id:
            raise ValueError("outbound event id is invalid")
        if not isinstance(event, dict):
            raise ValueError("outbound event must be one object")
        event_message_id = str(event.get("message_id") or event.get("id") or "").strip()
        event_identity = str(event.get("event_id") or event_message_id).strip()
        if not event_message_id or event_identity != event_id:
            raise ValueError("outbound event identity mismatch")
        with self._lock:
            selected = self._connection.execute(
                "SELECT * FROM inbox_events WHERE event_id=?",
                (event_id,),
            ).fetchone()
        if selected is None:
            raise ValueError("outbound event is missing")
        row = dict(selected)
        if str(row.get("status") or "") != "reply_pending":
            raise ValueError("outbound event is not reply pending")
        message_id = str(row.get("message_id") or "")
        scope = str(row.get("scope") or "")
        if event_message_id != message_id:
            raise ValueError("outbound message identity mismatch")
        if "_bridge_scope" in event:
            event_scope = event.get("_bridge_scope")
            if not isinstance(event_scope, str) or event_scope != scope:
                raise ValueError("outbound scope identity mismatch")

        answer = row.get("answer")
        raw_plan = row.get("outbound_plan_json")
        if not isinstance(answer, str) or not isinstance(raw_plan, str) or not raw_plan:
            raise ValueError("outbound envelope content is missing")
        try:
            plan = json.loads(raw_plan)
        except json.JSONDecodeError as exc:
            raise ValueError("outbound plan is invalid JSON") from exc
        if not isinstance(plan, dict):
            raise ValueError("outbound plan must be one object")
        canonical_plan = self._encode_outbound_plan(plan)
        if canonical_plan is None or canonical_plan != raw_plan:
            raise ValueError("outbound plan is not canonical")

        answer_sha256, answer_chars, plan_sha256, envelope_sha256 = (
            self._outbound_seal(
                event_id,
                message_id,
                scope,
                answer,
                canonical_plan,
            )
        )
        stored_answer_chars = row.get("outbound_answer_chars")
        comparisons = (
            (row.get("outbound_answer_sha256"), answer_sha256),
            (row.get("outbound_plan_sha256"), plan_sha256),
            (row.get("outbound_envelope_sha256"), envelope_sha256),
        )
        if (
            type(stored_answer_chars) is not int
            or stored_answer_chars != answer_chars
            or any(
                not isinstance(stored, str)
                or len(stored) != 64
                or any(character not in "0123456789abcdef" for character in stored)
                or not hmac.compare_digest(stored, expected)
                for stored, expected in comparisons
            )
        ):
            raise ValueError("outbound envelope failed integrity")
        return answer, plan

    def mark_outbound_result(
        self,
        event_id: str,
        fidelity: str,
        transforms: tuple[str, ...] | list[str],
    ) -> None:
        """Persist only fixed, answer-free source-to-Lark delivery metadata."""

        if fidelity not in OUTBOUND_FIDELITIES:
            raise ValueError("invalid outbound fidelity")
        normalized = tuple(dict.fromkeys(str(item) for item in transforms))
        if any(item not in OUTBOUND_TRANSFORMS for item in normalized):
            raise ValueError("invalid outbound transform")
        if fidelity == "explicit_transform" and not normalized:
            raise ValueError("explicit_transform requires at least one transform label")
        if fidelity != "explicit_transform" and normalized:
            raise ValueError("only explicit_transform may carry transform labels")
        with self._lock:
            self._connection.execute(
                """
                UPDATE inbox_events
                SET outbound_fidelity=?, outbound_transforms_json=?, updated_at=?
                WHERE event_id=? AND status='reply_pending'
                """,
                (
                    fidelity,
                    json.dumps(normalized, ensure_ascii=True, separators=(",", ":")),
                    time.time(),
                    event_id,
                ),
            )
            self._connection.commit()

    @staticmethod
    def outbound_transforms(row: dict[str, Any]) -> tuple[str, ...]:
        raw = row.get("outbound_transforms_json")
        if not isinstance(raw, str):
            return ()
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return ()
        if not isinstance(value, list):
            return ()
        return tuple(
            item for item in value if isinstance(item, str) and item in OUTBOUND_TRANSFORMS
        )

    def latest_delivery_fidelity(self) -> dict[str, Any]:
        """Return answer-free metadata for the newest completed Lark reply."""

        with self._lock:
            row = self._connection.execute(
                """
                SELECT outbound_fidelity, outbound_transforms_json
                FROM inbox_events
                WHERE status='completed'
                ORDER BY updated_at DESC, created_at DESC
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            return {"fidelity": "not_applicable", "transforms": []}
        record = dict(row)
        fidelity = str(record.get("outbound_fidelity") or "unknown")
        if fidelity not in OUTBOUND_FIDELITIES:
            fidelity = "unknown"
        transforms = list(self.outbound_transforms(record))
        if fidelity == "explicit_transform" and not transforms:
            fidelity = "unknown"
        if fidelity != "explicit_transform":
            transforms = []
        return {"fidelity": fidelity, "transforms": transforms}

    def mark_reply_retry(self, event_id: str, error: str) -> None:
        with self._lock:
            row = self._connection.execute(
                "SELECT reply_attempts FROM inbox_events WHERE event_id=?", (event_id,)
            ).fetchone()
            attempts = int(row[0]) + 1 if row else 1
            delay = min(300, 2 ** min(attempts, 8))
            self._connection.execute(
                """
                UPDATE inbox_events
                SET status='reply_pending', reply_attempts=?, last_error=?,
                    next_attempt_at=?, updated_at=?
                WHERE event_id=? AND status='reply_pending'
                """,
                (attempts, error[:1000], time.time() + delay, time.time(), event_id),
            )
            self._connection.commit()

    def mark_retryable(self, event_id: str, error: str) -> None:
        row = self.get(event_id) or {}
        attempts = int(row.get("attempts") or 1)
        delay = min(60, 2 ** min(attempts, 6))
        with self._lock:
            self._connection.execute(
                """
                UPDATE inbox_events
                SET status='retryable_failed', answer=NULL, last_error=?, next_attempt_at=?,
                    outbound_plan_json=NULL, outbound_answer_sha256=NULL,
                    outbound_answer_chars=NULL, outbound_plan_sha256=NULL,
                    outbound_envelope_sha256=NULL, updated_at=?
                WHERE event_id=? AND status NOT IN ('completed','terminal_failed')
                """,
                (error[:1000], time.time() + delay, time.time(), event_id),
            )
            self._connection.commit()

    def mark_terminal(self, event_id: str, error: str) -> None:
        self._update(event_id, status="terminal_failed", error=error[:1000], clear_payload=True)

    def mark_completed(self, event_id: str) -> None:
        self._update(event_id, status="completed", answer=None, error=None, clear_payload=True)

    def _update(
        self,
        event_id: str,
        *,
        status: str,
        answer: str | None = None,
        error: str | None = None,
        increment_attempts: bool = False,
        clear_payload: bool = False,
    ) -> None:
        fields = ["status=?", "answer=?", "last_error=?", "updated_at=?"]
        values: list[Any] = [status, answer, error, time.time()]
        if increment_attempts:
            fields.append("attempts=attempts+1")
        if clear_payload:
            fields.extend(
                [
                    "payload_json=NULL",
                    "answer=NULL",
                    "model_started=0",
                    "next_attempt_at=0",
                    "thread_id=NULL",
                    "turn_id=NULL",
                    "outbound_plan_json=NULL",
                    "outbound_answer_sha256=NULL",
                    "outbound_answer_chars=NULL",
                    "outbound_plan_sha256=NULL",
                    "outbound_envelope_sha256=NULL",
                ]
            )
        values.append(event_id)
        with self._lock:
            self._connection.execute(
                f"UPDATE inbox_events SET {', '.join(fields)} "
                "WHERE event_id=? AND status NOT IN ('completed','terminal_failed')",
                values,
            )
            self._connection.commit()

    def queue_count(self, scope: str | None = None) -> int:
        statuses = (
            "queued",
            "running",
            "control_sending",
            "reply_pending",
            "retryable_failed",
        )
        placeholders = ",".join("?" for _ in statuses)
        query = f"SELECT COUNT(*) FROM inbox_events WHERE status IN ({placeholders})"
        values: list[Any] = list(statuses)
        if scope is not None:
            query += " AND scope=?"
            values.append(scope)
        with self._lock:
            row = self._connection.execute(query, values).fetchone()
        return int(row[0]) if row else 0

    def status_counts(self) -> dict[str, int]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT status, COUNT(*) AS count FROM inbox_events GROUP BY status"
            ).fetchall()
        counts = {status: 0 for status in INBOX_STATUSES}
        for row in rows:
            counts[str(row["status"])] = int(row["count"])
        return counts

    def actionable_retryable_failed_count(self, excluded_error: str) -> int:
        """Count failed rows that may be scheduled again, without exposing content."""
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) AS count FROM inbox_events "
                "WHERE status='retryable_failed' "
                "AND COALESCE(last_error, '')<>?",
                (excluded_error,),
            ).fetchone()
        return int(row["count"] if row is not None else 0)

@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    role: str
    reason: str


class AccessPolicy:
    def __init__(
        self,
        *,
        mode: str,
        owner_open_id: str,
        admin_open_ids: frozenset[str],
        allowed_user_open_ids: frozenset[str],
        allowed_chat_ids: frozenset[str],
    ) -> None:
        self.mode = mode
        self.owner_open_id = owner_open_id
        self.admin_open_ids = admin_open_ids
        self.allowed_user_open_ids = allowed_user_open_ids
        self.allowed_chat_ids = allowed_chat_ids

    @property
    def configured(self) -> bool:
        return bool(
            self.owner_open_id
            or self.admin_open_ids
            or self.allowed_user_open_ids
            or self.allowed_chat_ids
        )

    def decide(self, *, sender_open_id: str, chat_id: str, chat_type: str) -> AccessDecision:
        if self.mode == "compat" and not self.configured:
            return AccessDecision(True, "compat", "compatibility mode")
        if sender_open_id and sender_open_id == self.owner_open_id:
            return AccessDecision(True, "owner", "owner")
        if sender_open_id and sender_open_id in self.admin_open_ids:
            return AccessDecision(True, "admin", "admin")
        if chat_type == "p2p" and sender_open_id in self.allowed_user_open_ids:
            return AccessDecision(True, "guest", "allowed user")
        if chat_type == "group" and chat_id in self.allowed_chat_ids:
            return AccessDecision(True, "guest", "allowed chat")
        return AccessDecision(False, "denied", "not on the bridge allowlist")


def policy_fingerprint(
    *,
    workspace: Path,
    role: str,
    bot_profile: str,
) -> str:
    canonical = json.dumps(
        {
            "workspace": str(workspace.resolve()).casefold(),
            "role": role,
            "bot_profile": bot_profile,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


class SessionStore:
    """Thread-safe atomic JSON binding store; message bodies never enter it."""

    SCHEMA_VERSION = 5
    SESSION_OWNER = "beeper"
    MAX_PROJECT_ROUTES = 20
    TEMPORARY_BINDING_FIELD = "temporary_binding_transaction"
    PROJECT_STATE_FIELDS = frozenset(
        {
            "previous_thread_ids",
        }
    )

    def __init__(self, path: Path, max_sessions: int = 500) -> None:
        self.path = path
        self.max_sessions = max_sessions
        self._lock = threading.RLock()
        self._needs_migration = False
        self._sessions = self._load()
        if self._needs_migration:
            with self._lock:
                self._save_locked()

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        raw = payload.get("sessions", {}) if isinstance(payload, dict) else {}
        if not isinstance(raw, dict):
            return {}

        sessions = {
            str(key): dict(value)
            for key, value in raw.items()
            if isinstance(value, dict)
        }
        # Catalog snapshots must never survive a process boundary.  Preserve
        # only a positive tombstone when an older build persisted one so the
        # next user message is rejected as a stale wizard reply instead of
        # falling through to the bound business task.
        for session in sessions.values():
            persisted_wizard = session.pop("init_wizard", None)
            if persisted_wizard is None:
                continue
            marker_candidates = [session.get("init_wizard_expires_at")]
            if isinstance(persisted_wizard, dict):
                marker_candidates.append(persisted_wizard.get("expires_at"))
            stale_marker = 1.0
            for candidate in marker_candidates:
                try:
                    numeric = float(candidate or 0)
                except (TypeError, ValueError):
                    continue
                if math.isfinite(numeric) and numeric > 0:
                    stale_marker = max(stale_marker, numeric)
            session["init_wizard_expires_at"] = stale_marker
            self._needs_migration = True
        if (
            payload.get("schema_version") == self.SCHEMA_VERSION
            and payload.get("session_owner") == self.SESSION_OWNER
        ):
            return sessions

        if (
            payload.get("schema_version") == 4
            and payload.get("session_owner") == RETIRED_SESSION_OWNER
        ):
            migrated_sessions: dict[str, dict[str, Any]] = {}
            for scope, value in sessions.items():
                data = dict(value)
                data["session_owner"] = self.SESSION_OWNER
                migrated_sessions[scope] = data
            self._needs_migration = True
            return migrated_sessions

        if (
            payload.get("schema_version") == 3
            and payload.get("session_owner") == "personal-remote"
        ):
            # Version 3 stored valid persisted responder ids but reached them by
            # attaching a second App Server. Preserve those bindings while
            # changing only their transport owner; the Desktop Beeper will
            # resolve missing host ids through read_thread/list_threads.
            migrated_beeper_sessions: dict[str, dict[str, Any]] = {}
            for scope, value in sessions.items():
                data = dict(value)
                data["session_owner"] = self.SESSION_OWNER
                data.pop("binding_migrated", None)
                migrated_beeper_sessions[scope] = data
            self._needs_migration = True
            return migrated_beeper_sessions

        # Older session schemas may contain aliases that are not canonical
        # Desktop task ids. Keep harmless metadata but require explicit rebinding.
        migrated: dict[str, dict[str, Any]] = {}
        for scope, value in sessions.items():
            data = dict(value)
            previous = [str(item) for item in data.get("previous_thread_ids", []) if item]
            old_thread_id = str(data.pop("thread_id", "") or "").strip()
            legacy_session_id = str(data.pop("session_id", "") or "").strip()
            if not old_thread_id:
                old_thread_id = legacy_session_id
            if old_thread_id and old_thread_id not in previous:
                previous.append(old_thread_id)
            if previous:
                data["previous_thread_ids"] = previous[-10:]
            data["session_owner"] = self.SESSION_OWNER
            data["binding_migrated"] = True
            migrated[scope] = data
        self._needs_migration = True
        return migrated

    def get(self, scope: str) -> dict[str, Any]:
        with self._lock:
            return dict(self._sessions.get(scope, {}))

    def resolve_scope_hash(self, scope_hash: str) -> str:
        """Resolve one exact persisted scope without exposing it to callers."""

        candidate = scope_hash.strip().lower()
        if len(candidate) != 12 or any(character not in "0123456789abcdef" for character in candidate):
            raise ValueError("scope hash must be exactly 12 lowercase hexadecimal characters")
        with self._lock:
            matches = [
                scope
                for scope in self._sessions
                if hashlib.sha256(scope.encode("utf-8")).hexdigest()[:12] == candidate
            ]
        if len(matches) != 1:
            raise ValueError("scope hash did not resolve to exactly one persisted session")
        return matches[0]

    @staticmethod
    def _canonical_hash(value: dict[str, Any]) -> str:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def begin_temporary_binding(
        self,
        scope: str,
        *,
        thread_id: str,
        host_id: str,
        transaction_id: str,
        project_root_sha256: str,
    ) -> dict[str, Any]:
        """Create one reversible diagnostic binding from an unbound baseline."""

        candidate_thread = thread_id.strip()
        candidate_host = host_id.strip()
        candidate_transaction = transaction_id.strip().lower()
        candidate_project_hash = project_root_sha256.strip().lower()
        if not candidate_thread:
            raise ValueError("thread_id is required")
        if not candidate_host:
            raise ValueError("host_id is required")
        if (
            len(candidate_transaction) != 32
            or any(character not in "0123456789abcdef" for character in candidate_transaction)
        ):
            raise ValueError("transaction_id must be exactly 32 lowercase hexadecimal characters")
        if (
            len(candidate_project_hash) != 64
            or any(character not in "0123456789abcdef" for character in candidate_project_hash)
        ):
            raise ValueError("project root digest must be one SHA-256 value")

        with self._lock:
            current = dict(self._sessions.get(scope, {}))
            if not current:
                raise ValueError("temporary binding requires one existing persisted session")
            if current.get(self.TEMPORARY_BINDING_FIELD):
                raise ValueError("this Feishu scope already has a temporary binding transaction")
            if str(current.get("thread_id") or "").strip():
                raise ValueError("temporary binding requires an unbound Feishu scope")
            if str(current.get("active_project_id") or "").strip():
                raise ValueError("temporary binding requires no active project route")
            raw_routes = current.get("project_routes", {})
            if isinstance(raw_routes, dict) and raw_routes:
                raise ValueError("temporary binding requires no retained project routes")
            if self._thread_has_unrelated_project_owner_locked(scope, candidate_thread):
                raise ValueError("thread belongs to another Feishu conversation")
            for other_scope, other_session in self._sessions.items():
                if other_scope != scope and str(other_session.get("thread_id") or "").strip() == candidate_thread:
                    raise ValueError("thread is already bound to another Feishu scope")

            baseline = json.loads(json.dumps(current, ensure_ascii=False))
            marker = {
                "schema_version": 1,
                "transaction_id": candidate_transaction,
                "thread_id": candidate_thread,
                "host_id": candidate_host,
                "project_root_sha256": candidate_project_hash,
                "baseline_sha256": self._canonical_hash(baseline),
                "baseline": baseline,
                "created_at": time.time(),
            }
            current[self.TEMPORARY_BINDING_FIELD] = marker
            current["thread_id"] = candidate_thread
            current["host_id"] = candidate_host
            current.pop("session_id", None)
            current.pop("binding_migrated", None)
            current["session_owner"] = self.SESSION_OWNER
            current["updated_at"] = time.time()
            self._sessions[scope] = current
            self._save_locked()
            return {
                "transaction_id": candidate_transaction,
                "thread_id": candidate_thread,
                "host_id": candidate_host,
                "baseline_sha256": marker["baseline_sha256"],
            }

    def rollback_temporary_binding(
        self,
        scope: str,
        *,
        transaction_id: str,
    ) -> dict[str, Any]:
        """Restore the exact unbound baseline when the transaction still owns it."""

        candidate_transaction = transaction_id.strip().lower()
        with self._lock:
            current = dict(self._sessions.get(scope, {}))
            marker = current.get(self.TEMPORARY_BINDING_FIELD)
            if not isinstance(marker, dict):
                raise ValueError("no temporary binding transaction exists for this Feishu scope")
            if str(marker.get("transaction_id") or "").strip().lower() != candidate_transaction:
                raise ValueError("temporary binding transaction id does not match")
            responder_thread = str(marker.get("thread_id") or "").strip()
            if not responder_thread or str(current.get("thread_id") or "").strip() != responder_thread:
                raise ValueError("temporary binding responder changed; refusing rollback")

            raw_routes = current.get("project_routes", {})
            routes = raw_routes if isinstance(raw_routes, dict) else {}
            if any(
                not isinstance(route, dict)
                or str(route.get("thread_id") or "").strip() != responder_thread
                for route in routes.values()
            ):
                raise ValueError("temporary binding project routes changed; refusing rollback")
            active_project_id = str(current.get("active_project_id") or "").strip()
            if active_project_id and active_project_id not in routes:
                raise ValueError("temporary binding active project changed; refusing rollback")

            baseline = marker.get("baseline")
            if not isinstance(baseline, dict):
                raise ValueError("temporary binding baseline is missing")
            if baseline.get(self.TEMPORARY_BINDING_FIELD):
                raise ValueError("temporary binding baseline is recursive")
            if str(baseline.get("thread_id") or "").strip():
                raise ValueError("temporary binding baseline is not unbound")
            expected_hash = str(marker.get("baseline_sha256") or "").strip().lower()
            if self._canonical_hash(baseline) != expected_hash:
                raise ValueError("temporary binding baseline integrity check failed")

            restored = json.loads(json.dumps(baseline, ensure_ascii=False))
            restored["updated_at"] = time.time()
            self._sessions[scope] = restored
            self._save_locked()
            return {
                "transaction_id": candidate_transaction,
                "thread_id": responder_thread,
                "restored_unbound": True,
            }

    @staticmethod
    def canonical_scope(scope: str) -> str:
        """Remove only the final access-policy suffix from a bridge scope."""

        return scope.rsplit(":policy:", 1)[0]

    @staticmethod
    def _conversation_scope(scope: str) -> str:
        """Compatibility alias for internal persisted-route migration code."""

        return SessionStore.canonical_scope(scope)

    @staticmethod
    def _safe_timestamp(value: Any) -> float:
        try:
            candidate = float(value or 0)
        except (TypeError, ValueError):
            return 0.0
        return candidate if math.isfinite(candidate) else 0.0

    def _consolidate_scope_locked(self, scope: str) -> tuple[dict[str, Any], bool]:
        """Collapse retired policy-suffixed variants into one conversation binding."""

        canonical = self._conversation_scope(scope)
        related_keys = [
            candidate
            for candidate in self._sessions
            if self._conversation_scope(candidate) == canonical
        ]
        if not related_keys:
            return {}, False
        if related_keys == [canonical]:
            return dict(self._sessions[canonical]), False

        ordered = sorted(
            related_keys,
            key=lambda candidate: self._safe_timestamp(
                self._sessions[candidate].get("updated_at", 0)
            ),
        )
        consolidated = dict(self._sessions[ordered[-1]])
        active_records = [
            (candidate, str(self._sessions[candidate].get("thread_id") or "").strip())
            for candidate in ordered
            if str(self._sessions[candidate].get("thread_id") or "").strip()
        ]
        active_ids = list(dict.fromkeys(thread_id for _, thread_id in active_records))
        all_ids: list[str] = []
        for candidate in ordered:
            session = self._sessions[candidate]
            for raw in (*list(session.get("previous_thread_ids", [])), session.get("thread_id")):
                thread_id = str(raw or "").strip()
                if thread_id and thread_id not in all_ids:
                    all_ids.append(thread_id)

        for key in (
            "thread_id",
            "host_id",
            "desktop_project_id",
            "binding_operation_receipt",
            self.TEMPORARY_BINDING_FIELD,
        ):
            consolidated.pop(key, None)
        if len(active_ids) == 1:
            selected_id = active_ids[0]
            selected = next(
                self._sessions[candidate]
                for candidate, thread_id in reversed(active_records)
                if thread_id == selected_id
            )
            consolidated["thread_id"] = selected_id
            for key in ("host_id", "desktop_project_id", "binding_operation_receipt"):
                value = selected.get(key)
                if value not in {None, ""}:
                    consolidated[key] = value
            all_ids = [thread_id for thread_id in all_ids if thread_id != selected_id]
        elif len(active_ids) > 1:
            # Conflicting historical policy variants cannot choose ownership on
            # the user's behalf. Preserve only bounded history and require /init.
            consolidated["binding_migrated"] = True
        if all_ids:
            consolidated["previous_thread_ids"] = all_ids[-10:]
        else:
            consolidated.pop("previous_thread_ids", None)
        marker_values: list[float] = []
        marker_corrupt = False
        for candidate in related_keys:
            raw_marker = self._sessions[candidate].get("init_wizard_expires_at", 0)
            if type(raw_marker) not in {int, float}:
                marker_corrupt = True
                continue
            marker = self._safe_timestamp(raw_marker)
            if marker < 0 or (raw_marker and marker == 0):
                marker_corrupt = True
            elif marker > 0:
                marker_values.append(marker)
        if marker_corrupt:
            consolidated["init_wizard_expires_at"] = -1.0
        elif marker_values:
            consolidated["init_wizard_expires_at"] = max(marker_values)
        else:
            consolidated["init_wizard_expires_at"] = 0.0
        consolidated["session_owner"] = self.SESSION_OWNER
        consolidated["updated_at"] = time.time()
        for candidate in related_keys:
            self._sessions.pop(candidate, None)
        self._sessions[canonical] = consolidated
        return dict(consolidated), True

    def consolidate_scope(self, scope: str) -> dict[str, Any]:
        """Return the one canonical session for a stable Feishu conversation."""

        with self._lock:
            session, changed = self._consolidate_scope_locked(scope)
            if changed:
                self._save_locked()
            return session

    @classmethod
    def _capture_project_state(cls, session: dict[str, Any]) -> dict[str, Any]:
        return {
            key: session[key]
            for key in cls.PROJECT_STATE_FIELDS
            if key in session
        }

    def _project_routes_locked(self, scope: str) -> dict[str, dict[str, Any]]:
        """Merge route metadata for one exact Feishu conversation."""

        conversation_scope = self._conversation_scope(scope)
        related = sorted(
            (
                session
                for candidate_scope, session in self._sessions.items()
                if self._conversation_scope(candidate_scope) == conversation_scope
            ),
            key=lambda session: float(session.get("updated_at", 0) or 0),
        )
        routes: dict[str, dict[str, Any]] = {}
        for session in related:
            raw_routes = session.get("project_routes", {})
            if not isinstance(raw_routes, dict):
                continue
            for route_id, raw_route in raw_routes.items():
                if not isinstance(raw_route, dict):
                    continue
                candidate = dict(raw_route)
                candidate["id"] = str(candidate.get("id") or route_id)
                if not candidate["id"]:
                    continue
                existing = routes.get(candidate["id"])
                if existing is not None and float(existing.get("updated_at", 0) or 0) > float(
                    candidate.get("updated_at", 0) or 0
                ):
                    continue
                routes[candidate["id"]] = candidate

            active_id = str(session.get("active_project_id") or "").strip()
            active_thread_id = str(session.get("thread_id") or "").strip()
            if active_id and active_id in routes and active_thread_id:
                active_route = dict(routes[active_id])
                active_route["thread_id"] = active_thread_id
                active_route["session_state"] = self._capture_project_state(session)
                active_route["updated_at"] = max(
                    float(active_route.get("updated_at", 0) or 0),
                    float(session.get("updated_at", 0) or 0),
                )
                routes[active_id] = active_route
        return routes

    def project_routes(self, scope: str) -> list[dict[str, Any]]:
        """Return bounded project routes for one Feishu conversation."""

        with self._lock:
            active_id = str(self._sessions.get(scope, {}).get("active_project_id") or "")
            routes = [dict(route) for route in self._project_routes_locked(scope).values()]
            for route in routes:
                route["active"] = route.get("id") == active_id
            routes.sort(
                key=lambda route: (
                    not bool(route.get("active")),
                    str(route.get("name") or "").casefold(),
                    str(route.get("id") or ""),
                )
            )
            return routes

    def find_project_route(self, scope: str, selector: str) -> dict[str, Any] | None:
        """Resolve one route by opaque id or exact display name."""

        candidate = selector.strip()
        if not candidate:
            return None
        with self._lock:
            routes = self._project_routes_locked(scope)
            if candidate in routes:
                return dict(routes[candidate])
            matches = [
                dict(route)
                for route in routes.values()
                if str(route.get("name") or "").casefold() == candidate.casefold()
            ]
            if len(matches) > 1:
                raise ValueError("project name is ambiguous; use its project id")
            return matches[0] if matches else None

    def active_project_route(self, scope: str) -> dict[str, Any] | None:
        """Return the newest active route across access-policy variants."""

        conversation_scope = self._conversation_scope(scope)
        with self._lock:
            routes = self._project_routes_locked(scope)
            related = sorted(
                (
                    session
                    for candidate_scope, session in self._sessions.items()
                    if self._conversation_scope(candidate_scope) == conversation_scope
                    and str(session.get("active_project_id") or "").strip()
                ),
                key=lambda session: float(session.get("updated_at", 0) or 0),
                reverse=True,
            )
            for session in related:
                active_id = str(session.get("active_project_id") or "").strip()
                if active_id in routes:
                    return dict(routes[active_id])
        return None

    def find_project_route_by_thread(
        self,
        scope: str,
        thread_id: str,
    ) -> dict[str, Any] | None:
        candidate = thread_id.strip()
        if not candidate:
            return None
        with self._lock:
            for route in self._project_routes_locked(scope).values():
                if str(route.get("thread_id") or "").strip() == candidate:
                    return dict(route)
        return None

    def _thread_has_unrelated_project_owner_locked(self, scope: str, thread_id: str) -> bool:
        conversation_scope = self._conversation_scope(scope)
        for candidate_scope, session in self._sessions.items():
            if self._conversation_scope(candidate_scope) == conversation_scope:
                continue
            if str(session.get("thread_id") or "").strip() == thread_id:
                return True
            raw_routes = session.get("project_routes", {})
            if not isinstance(raw_routes, dict):
                continue
            if any(
                str(route.get("thread_id") or "").strip() == thread_id
                for route in raw_routes.values()
                if isinstance(route, dict)
            ):
                return True
        return False

    def record_project_route(
        self,
        scope: str,
        *,
        project_id: str,
        name: str,
        root: str,
        thread_id: str,
        managed: bool,
        registered: bool = False,
        activate: bool = True,
        state_values: dict[str, Any] | None = None,
        binding_values: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record and optionally activate one isolated project/thread route."""

        route_id = project_id.strip()
        route_name = name.strip()
        route_root = root.strip()
        candidate_thread = thread_id.strip()
        if not route_id or not route_name or not route_root or not candidate_thread:
            raise ValueError("project id, name, root, and thread id are required")

        with self._lock:
            if self._thread_has_unrelated_project_owner_locked(scope, candidate_thread):
                raise ValueError("thread belongs to another Feishu conversation")

            routes = self._project_routes_locked(scope)
            for other_id, route in routes.items():
                if other_id == route_id:
                    continue
                if str(route.get("name") or "").casefold() == route_name.casefold():
                    raise ValueError("project name already exists for this Feishu conversation")
                if str(route.get("root") or "").casefold() == route_root.casefold():
                    raise ValueError("project directory already has a route")
                if str(route.get("thread_id") or "").strip() == candidate_thread:
                    raise ValueError("Codex thread already has a project route")
            if route_id not in routes and len(routes) >= self.MAX_PROJECT_ROUTES:
                raise ValueError(f"at most {self.MAX_PROJECT_ROUTES} project routes are allowed")

            now = time.time()
            current = dict(self._sessions.get(scope, {}))
            displaced_thread = str(current.get("thread_id") or "").strip()
            active_id = str(current.get("active_project_id") or "").strip()
            if active_id and active_id in routes:
                active_route = dict(routes[active_id])
                active_thread = str(current.get("thread_id") or "").strip()
                if active_thread:
                    active_route["thread_id"] = active_thread
                active_route["session_state"] = self._capture_project_state(current)
                active_route["updated_at"] = now
                routes[active_id] = active_route

            route = dict(routes.get(route_id, {}))
            route.update(
                {
                    "id": route_id,
                    "name": route_name,
                    "root": route_root,
                    "thread_id": candidate_thread,
                    "managed": bool(managed),
                    "registered": bool(registered),
                    "created_at": float(route.get("created_at", now) or now),
                    "updated_at": now,
                }
            )
            if not active_id and str(current.get("thread_id") or "").strip() == candidate_thread:
                route["session_state"] = self._capture_project_state(current)
            routes[route_id] = route

            if activate:
                conversation_scope = self._conversation_scope(scope)
                for related_scope, related_session in list(self._sessions.items()):
                    if (
                        related_scope == scope
                        or self._conversation_scope(related_scope) != conversation_scope
                    ):
                        continue
                    related = dict(related_session)
                    related.pop("thread_id", None)
                    related.pop("session_id", None)
                    related.pop("active_project_id", None)
                    related["updated_at"] = now
                    self._sessions[related_scope] = related

                for key in self.PROJECT_STATE_FIELDS:
                    current.pop(key, None)
                route_state = route.get("session_state", {})
                if isinstance(route_state, dict):
                    current.update(
                        {
                            key: value
                            for key, value in route_state.items()
                            if key in self.PROJECT_STATE_FIELDS
                        }
                    )
                if state_values:
                    current.update(
                        {
                            key: value
                            for key, value in state_values.items()
                            if key in self.PROJECT_STATE_FIELDS
                        }
                    )
                if binding_values:
                    reserved = {
                        "thread_id",
                        "session_id",
                        "active_project_id",
                        "project_routes",
                        "session_owner",
                        "updated_at",
                    }
                    current.update(
                        {
                            key: value
                            for key, value in binding_values.items()
                            if key not in reserved
                        }
                    )
                if (
                    displaced_thread
                    and displaced_thread != candidate_thread
                    and (not active_id or active_id == route_id)
                ):
                    previous = [
                        str(item).strip()
                        for item in current.get("previous_thread_ids", [])
                        if str(item).strip() and str(item).strip() != displaced_thread
                    ]
                    previous.append(displaced_thread)
                    current["previous_thread_ids"] = previous[-10:]
                current["thread_id"] = candidate_thread
                current["active_project_id"] = route_id
                current.pop("session_id", None)
                current.pop("binding_migrated", None)
                current["session_owner"] = self.SESSION_OWNER

            current["project_routes"] = routes
            current["updated_at"] = now
            self._sessions[scope] = current
            self._save_locked()
            return dict(current)

    def sync_active_project(self, scope: str) -> dict[str, Any]:
        """Persist the active route after normal thread or signal mutations."""

        with self._lock:
            current = dict(self._sessions.get(scope, {}))
            active_id = str(current.get("active_project_id") or "").strip()
            thread_id = str(current.get("thread_id") or "").strip()
            if not active_id or not thread_id:
                return current
            routes = self._project_routes_locked(scope)
            route = routes.get(active_id)
            if not route:
                return current
            updated_route = dict(route)
            updated_route["thread_id"] = thread_id
            updated_route["session_state"] = self._capture_project_state(current)
            updated_route["updated_at"] = time.time()
            routes[active_id] = updated_route
            current["project_routes"] = routes
            current["updated_at"] = time.time()
            self._sessions[scope] = current
            self._save_locked()
            return dict(current)

    def related_thread_ids(self, scope: str) -> list[str]:
        """Return known thread ids for only this exact Feishu conversation."""

        conversation_scope = self._conversation_scope(scope)
        with self._lock:
            related = sorted(
                (
                    session
                    for candidate_scope, session in self._sessions.items()
                    if self._conversation_scope(candidate_scope) == conversation_scope
                ),
                key=lambda session: float(session.get("updated_at", 0) or 0),
            )
            result: list[str] = []
            for session in related:
                routes = session.get("project_routes", {})
                route_thread_ids = (
                    [
                        route.get("thread_id")
                        for route in routes.values()
                        if isinstance(route, dict)
                    ]
                    if isinstance(routes, dict)
                    else []
                )
                candidates = [
                    *list(session.get("previous_thread_ids", [])),
                    *route_thread_ids,
                    session.get("thread_id"),
                ]
                for item in candidates:
                    thread_id = str(item or "").strip()
                    if not thread_id:
                        continue
                    if thread_id in result:
                        result.remove(thread_id)
                    result.append(thread_id)
            return result[-20:]

    def update(self, scope: str, values: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            current = dict(self._sessions.get(scope, {}))
            current.update(values)
            current["updated_at"] = time.time()
            self._sessions[scope] = current
            self._save_locked()
            return dict(current)

    def replace(self, scope: str, session: dict[str, Any]) -> None:
        with self._lock:
            data = dict(session)
            data["updated_at"] = time.time()
            self._sessions[scope] = data
            self._save_locked()

    def bind_thread(
        self,
        scope: str,
        thread_id: str,
        values: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Atomically make one Codex thread the active binding for one scope."""

        candidate = thread_id.strip()
        if not candidate:
            raise ValueError("thread_id is required")
        with self._lock:
            for other_scope, other_session in self._sessions.items():
                if other_scope != scope and other_session.get("thread_id") == candidate:
                    raise ValueError(f"thread is already bound to scope {other_scope}")
            current = dict(self._sessions.get(scope, {}))
            old_thread_id = str(current.get("thread_id") or "").strip()
            if old_thread_id and old_thread_id != candidate:
                previous = [
                    str(item)
                    for item in current.get("previous_thread_ids", [])
                    if item
                ]
                if old_thread_id not in previous:
                    previous.append(old_thread_id)
                current["previous_thread_ids"] = previous[-10:]
                current.pop("desktop_project_id", None)
                current.pop("binding_operation_receipt", None)
            if values:
                current.update(values)
            current["thread_id"] = candidate
            current.pop("session_id", None)
            current.pop("binding_migrated", None)
            current["session_owner"] = self.SESSION_OWNER
            current["updated_at"] = time.time()
            self._sessions[scope] = current
            self._save_locked()
            return dict(current)

    def bind_thread_if_current(
        self,
        scope: str,
        thread_id: str,
        *,
        expected_thread_id: str,
        host_id: str,
        project_id: str,
        operation_receipt: str,
    ) -> dict[str, Any]:
        """CAS one inspected Desktop task into the active scope binding.

        Catalog display metadata is deliberately excluded.  Historical project
        routes are not ownership claims: uniqueness is checked only against
        another scope's current active ``thread_id``.
        """

        candidate = thread_id.strip()
        expected = expected_thread_id.strip()
        candidate_host = host_id.strip()
        candidate_project = project_id.strip()
        receipt = operation_receipt.strip()
        if not candidate or len(candidate) > 200:
            raise ValueError("thread_id is required and must be bounded")
        if not candidate_host or len(candidate_host) > 256:
            raise ValueError("host_id is required and must be bounded")
        if not candidate_project or len(candidate_project) > 200:
            raise ValueError("project_id is required and must be bounded")
        if (
            len(receipt) != 32
            or any(character not in "0123456789abcdef" for character in receipt)
        ):
            raise ValueError("operation_receipt must be 32 lowercase hexadecimal characters")
        if any(
            ord(character) < 0x20 or ord(character) == 0x7F
            for value in (candidate, expected, candidate_host, candidate_project)
            for character in value
        ):
            raise ValueError("binding identifiers cannot contain control characters")

        with self._lock:
            # Runtime consolidates retired policy variants before starting a
            # wizard. Keep this CAS exact and fail closed if an un-migrated
            # variant still owns the candidate.
            scope = self._conversation_scope(scope)
            had_session = scope in self._sessions
            original = dict(self._sessions.get(scope, {}))
            current = dict(original)
            existing_receipt = str(
                current.get("binding_operation_receipt") or ""
            ).strip()
            if existing_receipt == receipt:
                if (
                    str(current.get("thread_id") or "").strip() != candidate
                    or str(current.get("host_id") or "").strip() != candidate_host
                    or str(current.get("desktop_project_id") or "").strip()
                    != candidate_project
                ):
                    raise ValueError("binding receipt conflicts with persisted identity")
                return dict(current)

            active_thread = str(current.get("thread_id") or "").strip()
            if active_thread != expected:
                raise ValueError("active binding changed after the catalog snapshot")
            if current.get(self.TEMPORARY_BINDING_FIELD):
                raise ValueError("temporary binding transaction is still active")
            for other_scope, other_session in self._sessions.items():
                if (
                    self._conversation_scope(other_scope) != scope
                    and (
                        str(other_session.get("thread_id") or "").strip() == candidate
                        or str(
                            other_session.get("binding_operation_receipt") or ""
                        ).strip()
                        == receipt
                    )
                ):
                    raise ValueError(
                        "thread or operation receipt is already active in another Feishu scope"
                    )

            if active_thread and active_thread != candidate:
                previous = [
                    str(item).strip()
                    for item in current.get("previous_thread_ids", [])
                    if str(item).strip() and str(item).strip() != active_thread
                ]
                previous.append(active_thread)
                current["previous_thread_ids"] = previous[-10:]

            # New `/init` bindings have no path-bearing project route. Existing
            # routes remain retained history, but none is promoted to active and
            # no label or root is copied from the transient catalog snapshot.
            current.pop("active_project_id", None)
            current["thread_id"] = candidate
            current["host_id"] = candidate_host
            current["desktop_project_id"] = candidate_project
            current["binding_operation_receipt"] = receipt
            # The wizard is process-local. Clear its only durable marker in the
            # same atomic write as the confirmed binding so a later save cannot
            # fail after commit and misreport that the binding did not change.
            current["init_wizard_expires_at"] = 0.0
            current.pop("session_id", None)
            current.pop("binding_migrated", None)
            current["session_owner"] = self.SESSION_OWNER
            current["updated_at"] = time.time()
            self._sessions[scope] = current
            try:
                self._save_locked()
            except Exception:
                if had_session:
                    self._sessions[scope] = original
                else:
                    self._sessions.pop(scope, None)
                raise
            return dict(current)

    def replace_thread(
        self,
        scope: str,
        thread_id: str,
        values: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make a fresh thread canonical across all policy variants of one scope."""

        candidate = thread_id.strip()
        if not candidate:
            raise ValueError("thread_id is required")
        conversation_scope = self._conversation_scope(scope)
        with self._lock:
            for other_scope, other_session in self._sessions.items():
                if (
                    self._conversation_scope(other_scope) != conversation_scope
                    and other_session.get("thread_id") == candidate
                ):
                    raise ValueError(f"thread is already bound to scope {other_scope}")

            history = self.related_thread_ids(scope)
            project_routes = self._project_routes_locked(scope)
            source_active_project_id = str(
                self._sessions.get(scope, {}).get("active_project_id") or ""
            ).strip()
            if not source_active_project_id:
                related_with_project = sorted(
                    (
                        session
                        for related_scope, session in self._sessions.items()
                        if self._conversation_scope(related_scope) == conversation_scope
                        and str(session.get("active_project_id") or "").strip()
                    ),
                    key=lambda session: float(session.get("updated_at", 0) or 0),
                    reverse=True,
                )
                if related_with_project:
                    source_active_project_id = str(
                        related_with_project[0].get("active_project_id") or ""
                    ).strip()
            now = time.time()
            for related_scope, related_session in list(self._sessions.items()):
                if (
                    related_scope == scope
                    or self._conversation_scope(related_scope) != conversation_scope
                ):
                    continue
                source = dict(related_session)
                source.pop("thread_id", None)
                source.pop("session_id", None)
                source["updated_at"] = now
                self._sessions[related_scope] = source

            current = dict(self._sessions.get(scope, {}))
            if values:
                current.update(values)
            previous = [item for item in history if item != candidate]
            if previous:
                current["previous_thread_ids"] = previous[-10:]
            else:
                current.pop("previous_thread_ids", None)
            current["thread_id"] = candidate
            current.pop("desktop_project_id", None)
            current.pop("binding_operation_receipt", None)
            if project_routes:
                current["project_routes"] = project_routes
            if source_active_project_id and source_active_project_id in project_routes:
                current["active_project_id"] = source_active_project_id
            current.pop("session_id", None)
            current.pop("binding_migrated", None)
            current["session_owner"] = self.SESSION_OWNER
            current["updated_at"] = now
            self._sessions[scope] = current
            self._save_locked()
            return dict(current)

    def reset_thread(self, scope: str) -> bool:
        with self._lock:
            current = self._sessions.get(scope)
            if not current or not current.get("thread_id"):
                return False
            previous = list(current.get("previous_thread_ids", []))
            previous.append(str(current["thread_id"]))
            current["previous_thread_ids"] = previous[-10:]
            current.pop("thread_id", None)
            current.pop("session_id", None)
            current.pop("desktop_project_id", None)
            current.pop("binding_operation_receipt", None)
            current["session_owner"] = self.SESSION_OWNER
            current["updated_at"] = time.time()
            self._save_locked()
            return True

    def unbind_thread(self, scope: str) -> bool:
        return self.reset_thread(scope)

    def find_scope_by_thread(self, thread_id: str) -> str | None:
        with self._lock:
            for scope, session in self._sessions.items():
                if session.get("thread_id") == thread_id:
                    return scope
                routes = session.get("project_routes", {})
                if isinstance(routes, dict) and any(
                    isinstance(route, dict)
                    and str(route.get("thread_id") or "").strip() == thread_id
                    for route in routes.values()
                ):
                    return scope
        return None

    def _save_locked(self) -> None:
        recent = sorted(
            self._sessions.items(),
            key=lambda item: float(item[1].get("updated_at", 0) or 0),
            reverse=True,
        )[: self.max_sessions]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "schema_version": self.SCHEMA_VERSION,
                    "session_owner": self.SESSION_OWNER,
                    "sessions": dict(recent),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.replace(self.path)
