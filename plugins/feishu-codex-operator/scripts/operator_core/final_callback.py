"""Durable, token-free routing for Responder-owned Final Callback results."""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import closing
from pathlib import Path
import json
import re
import sqlite3
import threading
import time


REQUEST_ID_PATTERN = re.compile(r"[a-f0-9]{32}")
MAX_FINAL_ANSWER_CHARS = 12_000


class FinalCallbackStoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class FinalCallbackResult:
    request_id: str
    final_answer: str


class FinalCallbackStore:
    """Map one public request id to the first final submitted for that request.

    ``request_id`` is correlation data, not a credential.  The store deliberately
    does not attempt to attest the calling Codex task or turn.
    """

    def __init__(self, path: Path, *, retention_hours: int = 168,
                 busy_timeout_seconds: float = 5.0) -> None:
        self.path = path.resolve()
        self.retention_seconds = max(1, int(retention_hours)) * 3600
        self._lock = threading.RLock()
        self._busy_timeout = busy_timeout_seconds
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @staticmethod
    def validate_request_id(value: str) -> str:
        request_id = str(value or "").strip().lower()
        if REQUEST_ID_PATTERN.fullmatch(request_id) is None:
            raise FinalCallbackStoreError("invalid request id")
        return request_id

    @staticmethod
    def validate_final_answer(value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise FinalCallbackStoreError("final answer is empty")
        if len(value) > MAX_FINAL_ANSWER_CHARS:
            raise FinalCallbackStoreError("final answer is too large")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise FinalCallbackStoreError("final answer is not valid Unicode") from exc
        return value

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=self._busy_timeout)
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={int(self._busy_timeout * 1000)}")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS final_callback_requests (
                    request_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL UNIQUE,
                    responder_thread_id TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (
                        state IN ('pending', 'captured', 'closed')
                    ),
                    final_answer TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_final_callback_state "
                "ON final_callback_requests(state, updated_at)"
            )
            columns = {row[1] for row in connection.execute(
                "PRAGMA table_info(final_callback_requests)"
            )}
            if "relay_payload" not in columns:
                connection.execute("ALTER TABLE final_callback_requests ADD COLUMN relay_payload TEXT")
            if "relay_started" not in columns:
                connection.execute("ALTER TABLE final_callback_requests ADD COLUMN relay_started INTEGER NOT NULL DEFAULT 0")

    def open(self, request_id: str, event_id: str, responder_thread_id: str, *,
             relay_prompt: str | None = None, responder_host_id: str = "local") -> None:
        request_id = self.validate_request_id(request_id)
        event_id = str(event_id or "").strip()
        responder_thread_id = str(responder_thread_id or "").strip()
        if not event_id or not responder_thread_id:
            raise FinalCallbackStoreError("callback route is incomplete")
        now = time.time()
        payload = None if relay_prompt is None else json.dumps({
            "threadId": responder_thread_id, "hostId": responder_host_id,
            "prompt": relay_prompt,
        }, ensure_ascii=True, separators=(",", ":"))
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT event_id, responder_thread_id, state, relay_payload, relay_started "
                "FROM final_callback_requests WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["event_id"]) == event_id
                    and str(existing["responder_thread_id"]) == responder_thread_id
                    and str(existing["state"]) == "pending"
                    and (payload is None or (not existing["relay_started"]
                         and existing["relay_payload"] == payload))
                ):
                    return
                raise FinalCallbackStoreError("callback request id is already in use")
            connection.execute(
                "INSERT INTO final_callback_requests"
                "(request_id,event_id,responder_thread_id,state,final_answer,created_at,updated_at,relay_payload) "
                "VALUES(?,?,?,'pending',NULL,?,?,?)",
                (request_id, event_id, responder_thread_id, now, now, payload),
            )

    def take_relay(self, request_id: str) -> dict[str, str] | None:
        """Commit a no-replay boundary, then return exact Desktop tool arguments.

        This is dispatch bookkeeping, not authentication or exactly-once execution.
        A crash after consumption must never restore the payload or retry sending.
        """
        request_id = self.validate_request_id(request_id)
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT relay_payload FROM final_callback_requests WHERE request_id=? "
                "AND state='pending' AND relay_started=0", (request_id,),
            ).fetchone()
            if row is None or row["relay_payload"] is None:
                return None
            payload = json.loads(row["relay_payload"])
            connection.execute(
                "UPDATE final_callback_requests SET relay_started=1, relay_payload=NULL "
                "WHERE request_id=?", (request_id,),
            )
        return payload

    def submit(self, request_id: str, final_answer: object) -> dict[str, object]:
        request_id = self.validate_request_id(request_id)
        answer = self.validate_final_answer(final_answer)
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT state, final_answer FROM final_callback_requests WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if row is None:
                return {"accepted": False, "state": "unknown"}
            state = str(row["state"])
            if state == "pending":
                connection.execute(
                    "UPDATE final_callback_requests SET state='captured', final_answer=?, "
                    "updated_at=? WHERE request_id=? AND state='pending'",
                    (answer, time.time(), request_id),
                )
                return {"accepted": True, "state": "captured"}
            if state == "captured":
                if row["final_answer"] == answer:
                    return {"accepted": True, "state": "duplicate"}
                return {"accepted": False, "state": "conflict"}
            return {"accepted": False, "state": "closed"}

    def result(self, request_id: str) -> FinalCallbackResult | None:
        request_id = self.validate_request_id(request_id)
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT final_answer FROM final_callback_requests "
                "WHERE request_id=? AND state='captured'",
                (request_id,),
            ).fetchone()
        if row is None:
            return None
        answer = self.validate_final_answer(row["final_answer"])
        return FinalCallbackResult(request_id=request_id, final_answer=answer)

    def wait(self, request_id: str, timeout_seconds: float) -> FinalCallbackResult | None:
        deadline = time.monotonic() + max(0.0, float(timeout_seconds))
        while True:
            result = self.result(request_id)
            if result is not None:
                return result
            if time.monotonic() >= deadline:
                return None
            time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))

    def close(self, request_id: str) -> None:
        request_id = self.validate_request_id(request_id)
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                "UPDATE final_callback_requests SET state='closed', final_answer=NULL, relay_payload=NULL, "
                "updated_at=? WHERE request_id=? AND state IN ('pending','captured')",
                (time.time(), request_id),
            )

    def settle(self, request_id: str) -> FinalCallbackResult | None:
        """Atomically prefer a captured callback over a simultaneous timeout."""
        request_id = self.validate_request_id(request_id)
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT final_answer FROM final_callback_requests "
                "WHERE request_id=? AND state='captured'", (request_id,),
            ).fetchone()
            result = None if row is None else FinalCallbackResult(
                request_id, self.validate_final_answer(row["final_answer"]),
            )
            connection.execute(
                "UPDATE final_callback_requests SET state='closed', final_answer=NULL, relay_payload=NULL, "
                "updated_at=? WHERE request_id=? AND state IN ('pending','captured')",
                (time.time(), request_id),
            )
        return result

    def pending_count(self) -> int:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM final_callback_requests WHERE state='pending'"
            ).fetchone()
        return int(row["count"] if row is not None else 0)

    def cleanup(self) -> None:
        cutoff = time.time() - self.retention_seconds
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                "DELETE FROM final_callback_requests "
                "WHERE state IN ('captured','closed') AND updated_at < ?",
                (cutoff,),
            )
