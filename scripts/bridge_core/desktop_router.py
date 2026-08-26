"""Durable file protocol between the Feishu listener and one Desktop Gateway.

The listener never opens a Codex target thread.  It writes one idempotent
request here; a dedicated task running inside Codex Desktop claims the request,
uses Desktop task-coordination tools, and writes the result back.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Callable
import uuid


ROUTER_SCHEMA_VERSION = 1
THREAD_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,255}")
REQUEST_ID_PATTERN = re.compile(r"[a-f0-9]{32}")
WAKE_ID_PATTERN = re.compile(r"[a-f0-9]{32}")
FENCE_TOKEN_PATTERN = re.compile(r"[a-f0-9]{32}")
MANUAL_TICKET_PATTERN = re.compile(r"[a-f0-9]{32}")
FINAL_RETURN_MAX_CHARS = 12_000
FINAL_RETURN_ARM_TTL_SECONDS = 600
CODEX_DELEGATION_PROMPT_PATTERN = re.compile(
    r"\A<codex_delegation>\r?\n"
    r"[ \t]*<source_thread_id>(?P<source_thread_id>[^<>\r\n]+)"
    r"</source_thread_id>\r?\n"
    r"[ \t]*<input>(?P<input>.*)</input>\r?\n"
    r"[ \t]*</codex_delegation>\Z",
    re.DOTALL,
)
# `archive_threads` is retained only so a pre-upgrade durable request can drain.
# Current producers archive explicit displaced IDs inside create/restore/compact
# results and must not submit a new standalone archive operation.
ALLOWED_OPERATIONS = frozenset(
    {
        "list_task_catalog",
        "inspect_thread",
        "create_thread",
        "restore_thread",
        "send_message_to_thread",
        "compact_thread",
        "archive_threads",
    }
)
# The Gateway contract implements `inspect_thread` with Desktop read-only task
# inspection only.  If its fenced claim is abandoned, no target mutation can
# have started and the deterministic request may safely advance a generation.
# Every other operation remains an uncertainty boundary after claim.
READ_ONLY_OPERATIONS = frozenset({"inspect_thread", "list_task_catalog"})


class RouterProtocolError(RuntimeError):
    """The local Desktop Gateway queue contains invalid or conflicting data."""


@dataclass(frozen=True)
class RouterStatus:
    # `ready` and `heartbeat_age_seconds` are protocol-v4 compatibility names
    # for the active-work lease heartbeat. Scheduler freshness comes from the
    # most recent metadata-only `sentinel-probe`.
    ready: bool
    registered: bool = False
    router_thread_id: str = ""
    host_id: str = ""
    heartbeat_age_seconds: float | None = None
    pending: int = 0
    claimed: int = 0
    wake_generation: int = 0
    wake_inflight: bool = False
    wake_lease_remaining_seconds: float | None = None
    sentinel_fresh: bool = False
    sentinel_age_seconds: float | None = None

    @property
    def scheduler_fresh(self) -> bool:
        return self.sentinel_fresh

    @property
    def scheduler_age_seconds(self) -> float | None:
        return self.sentinel_age_seconds

    @property
    def work_heartbeat_fresh(self) -> bool:
        return self.ready

    @property
    def work_heartbeat_age_seconds(self) -> float | None:
        return self.heartbeat_age_seconds

    def as_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "registered": self.registered,
            "router_thread_id": self.router_thread_id,
            "host_id": self.host_id,
            "heartbeat_age_seconds": self.heartbeat_age_seconds,
            "pending": self.pending,
            "claimed": self.claimed,
            "wake_generation": self.wake_generation,
            "wake_inflight": self.wake_inflight,
            "wake_lease_remaining_seconds": self.wake_lease_remaining_seconds,
            "sentinel_fresh": self.sentinel_fresh,
            "sentinel_age_seconds": self.sentinel_age_seconds,
            "scheduler_fresh": self.scheduler_fresh,
            "scheduler_age_seconds": self.scheduler_age_seconds,
            "work_heartbeat_fresh": self.work_heartbeat_fresh,
            "work_heartbeat_age_seconds": self.work_heartbeat_age_seconds,
        }


def looks_like_thread_id(value: str) -> bool:
    candidate = value.strip()
    if not THREAD_ID_PATTERN.fullmatch(candidate):
        return False
    return len(candidate) >= 24 or "-" in candidate or candidate.startswith(("thr_", "thread_"))


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp"
    )
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _atomic_write_json_exclusive(path: Path, payload: dict[str, Any]) -> bool:
    """Publish complete JSON only when no authoritative file exists yet."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp"
    )
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        try:
            # The hard link is an atomic create-if-absent publication of the
            # already closed complete file; readers never see partial JSON.
            os.link(temporary, path)
        except FileExistsError:
            return False
        return True
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass


def _atomic_write_text(path: Path, text: str) -> None:
    """Atomically publish exact UTF-8 text for the fenced answer handoff."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp"
    )
    try:
        with temporary.open("x", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass


MAX_RETRY_GENERATIONS = 64
COMPACT_RESULT_OPERATIONS = frozenset(
    {
        "list_task_catalog",
        "inspect_thread",
        "create_thread",
        "restore_thread",
        "compact_thread",
    }
)


def _request_id(
    operation: str,
    idempotency_key: str | None,
    retry_generation: int = 0,
) -> str:
    if idempotency_key:
        retry_suffix = f"\0retry:{retry_generation}" if retry_generation else ""
        material = f"{operation}\0{idempotency_key}{retry_suffix}".encode("utf-8")
        return hashlib.sha256(material).hexdigest()[:32]
    return uuid.uuid4().hex


class DesktopRouterQueue:
    """Atomic, bounded queue consumed only by the single Desktop Gateway task."""

    def __init__(
        self,
        runtime_dir: Path,
        *,
        heartbeat_ttl_seconds: int = 90,
        claim_ttl_seconds: int = 1800,
        read_claim_ttl_seconds: int = 300,
        retention_hours: int = 168,
        wake_lease_ttl_seconds: int = 180,
        scheduler_ttl_seconds: int | None = None,
        grace_wait_max_seconds: int = 30,
    ) -> None:
        self.root = runtime_dir / "desktop-router"
        self.pending_dir = self.root / "pending"
        self.claimed_dir = self.root / "claimed"
        self.responses_dir = self.root / "responses"
        self.staging_dir = self.root / "staging"
        self.receipts_dir = self.root / "receipts"
        self.registration_file = self.root / "registration.json"
        self.heartbeat_file = self.root / "heartbeat.json"
        self.wake_db = self.root / "wake.sqlite3"
        self.heartbeat_ttl_seconds = max(15, heartbeat_ttl_seconds)
        self.claim_ttl_seconds = max(60, claim_ttl_seconds)
        # `inspect_thread` cannot mutate a Desktop task, so it does not need the
        # long uncertainty window reserved for possibly-started target work.
        # Cap its abandonment window at the general claim TTL so direct callers
        # with a deliberately shorter test/runtime TTL keep their stricter bound.
        self.read_claim_ttl_seconds = min(
            self.claim_ttl_seconds,
            max(60, read_claim_ttl_seconds),
        )
        self.retention_seconds = max(3600, retention_hours * 3600)
        self.wake_lease_ttl_seconds = max(60, wake_lease_ttl_seconds)
        # Older direct callers used the wake-lease TTL for both meanings. Keep
        # that fallback while allowing installed Bridges to configure scheduler
        # freshness independently from active-work lease duration.
        resolved_scheduler_ttl = (
            wake_lease_ttl_seconds
            if scheduler_ttl_seconds is None
            else scheduler_ttl_seconds
        )
        self.scheduler_ttl_seconds = max(60, resolved_scheduler_ttl)
        self.grace_wait_max_seconds = max(0, min(grace_wait_max_seconds, 60))
        for directory in (
            self.pending_dir,
            self.claimed_dir,
            self.responses_dir,
            self.staging_dir,
            self.receipts_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self._initialize_wake_db()

    @staticmethod
    def _validate_request_id(request_id: str) -> str:
        candidate = request_id.strip().lower()
        if not REQUEST_ID_PATTERN.fullmatch(candidate):
            raise RouterProtocolError("invalid Desktop Gateway request id")
        return candidate

    def _path(self, directory: Path, request_id: str) -> Path:
        return directory / f"{self._validate_request_id(request_id)}.json"

    def _finalization_path(self, request_id: str) -> Path:
        return self.receipts_dir / f"{self._validate_request_id(request_id)}.final"

    def _receipt_payload_path(self, request_id: str) -> Path:
        return self.receipts_dir / f"{self._validate_request_id(request_id)}.json"

    def _has_terminal_fence(self, request_id: str) -> bool:
        return self._receipt_payload_path(request_id).exists() or self._finalization_path(
            request_id
        ).exists()

    def _terminal_result_exists(self, request_id: str) -> bool:
        """Check terminal publication by filename only for metadata probes."""

        return self._receipt_payload_path(request_id).exists() or self._path(
            self.responses_dir,
            request_id,
        ).exists()

    @staticmethod
    def _valid_terminal_receipt(
        request_id: str,
        payload: dict[str, Any] | None,
    ) -> bool:
        return bool(
            payload is not None
            and payload.get("request_id") == request_id
            and payload.get("status") in {"completed", "failed"}
        )

    @staticmethod
    def _compacted_terminal_receipt(payload: dict[str, Any]) -> dict[str, Any]:
        """Drop expired answer text while preserving idempotency semantics."""

        if payload.get("compacted_at") is not None:
            return payload
        base = {
            "schema_version": payload.get("schema_version", ROUTER_SCHEMA_VERSION),
            "request_id": payload.get("request_id"),
            "operation": payload.get("operation"),
            "fingerprint": payload.get("fingerprint"),
            "completed_at": payload.get("completed_at"),
            "compacted_at": time.time(),
        }
        if payload.get("status") == "failed":
            source = payload.get("error")
            error = source if isinstance(source, dict) else {}
            raw_code = error.get("code")
            raw_retryable = error.get("retryable")
            raw_may_have_started = error.get("may_have_started")
            valid_failure = bool(
                isinstance(raw_code, str)
                and raw_code.strip()
                and type(raw_retryable) is bool
                and type(raw_may_have_started) is bool
            )
            if valid_failure:
                code = raw_code.strip()[:80]
                retryable = raw_retryable
                may_have_started = raw_may_have_started
                message = str(error.get("message") or code).strip()[:256]
            else:
                code = "target_result_unknown"
                retryable = False
                may_have_started = True
                message = "Malformed terminal failure was compacted fail-closed"
            return {
                **base,
                "status": "failed",
                "error": {
                    "code": code,
                    "message": message,
                    "retryable": retryable,
                    "may_have_started": may_have_started,
                },
            }
        operation = str(payload.get("operation") or "")
        if payload.get("status") == "completed" and operation in COMPACT_RESULT_OPERATIONS:
            source = payload.get("result")
            result = source if isinstance(source, dict) else {}
            compact_result = {
                key: str(result.get(key) or "").strip()
                for key in ("thread_id", "host_id", "turn_id", "cursor")
            }
            archives = result.get("archived_thread_ids")
            compact_result["archived_thread_ids"] = [
                str(item).strip()
                for item in (archives if isinstance(archives, list) else [])[:32]
                if str(item).strip()
            ]
            return {**base, "status": "completed", "result": compact_result}
        # A completed answer body is useful only within the response-retention
        # window. Afterwards retain a small conservative tombstone: never replay
        # a target mutation merely because its final text aged out.
        return {
            **base,
            "status": "failed",
            "error": {
                "code": "target_result_unknown",
                "message": (
                    "The completed target result exceeded the response-retention "
                    "window; its action was not replayed"
                ),
                "retryable": False,
                "may_have_started": True,
            },
        }

    def _receipt_response(self, request_id: str) -> dict[str, Any] | None:
        """Read a fully published durable terminal tombstone."""

        request_id = self._validate_request_id(request_id)
        marker = self._finalization_path(request_id)
        receipt_path = self._receipt_payload_path(request_id)
        receipt = _read_json(receipt_path)
        return receipt if self._valid_terminal_receipt(request_id, receipt) else None

    def _migrate_cached_response(
        self,
        request_id: str,
        cached: dict[str, Any],
        *,
        source_mtime: float | None = None,
    ) -> dict[str, Any] | None:
        """Publish the released empty-fence/cache layout as a durable receipt."""

        request_id = self._validate_request_id(request_id)
        if not self._valid_terminal_receipt(request_id, cached):
            return None
        receipt_path = self._receipt_payload_path(request_id)
        try:
            created = _atomic_write_json_exclusive(receipt_path, cached)
            if created and source_mtime is not None:
                os.utime(receipt_path, (source_mtime, source_mtime))
            marker = self._finalization_path(request_id)
            try:
                descriptor = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                descriptor = -1
            if descriptor >= 0:
                os.close(descriptor)
        except OSError:
            return None
        return self._receipt_response(request_id)

    def _recover_interrupted_finalization(
        self,
        request_id: str,
        request: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Turn a stale orphan fence into a permanent unknown-outcome result."""

        request_id = self._validate_request_id(request_id)
        existing = self._receipt_response(request_id)
        if existing is not None:
            return existing
        details = request or {}
        recovered = {
            "schema_version": ROUTER_SCHEMA_VERSION,
            "request_id": request_id,
            "operation": details.get("operation"),
            "fingerprint": details.get("fingerprint"),
            "status": "failed",
            "error": {
                "code": "target_result_unknown",
                "message": (
                    "Desktop Gateway finalization was interrupted after its terminal "
                    "fence was created; the target outcome is unknown and was not replayed"
                ),
                "retryable": False,
                "may_have_started": True,
            },
            "completed_at": time.time(),
        }
        receipt_path = self._receipt_payload_path(request_id)
        if not _atomic_write_json_exclusive(receipt_path, recovered):
            existing = self._receipt_response(request_id)
            if existing is not None:
                return existing
            raise RouterProtocolError(
                "Desktop Gateway terminal receipt exists but is unreadable"
            )
        try:
            _atomic_write_json(self._path(self.responses_dir, request_id), recovered)
        except OSError:
            pass
        return recovered

    def _finalize_response(self, request_id: str, payload: dict[str, Any]) -> bool:
        """Write the first terminal tombstone and a disposable response cache."""

        request_id = self._validate_request_id(request_id)
        marker = self._finalization_path(request_id)
        receipt_path = self._receipt_payload_path(request_id)
        if marker.exists() and not receipt_path.exists():
            # Legacy/interrupted empty markers are recovered only after the
            # retained claim and active-work lease become stale.
            return False
        if not _atomic_write_json_exclusive(receipt_path, payload):
            return False
        try:
            descriptor = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            descriptor = -1
        except OSError:
            # The complete receipt is already the authoritative terminal
            # publication.  A compatibility marker failure must not turn that
            # committed outcome into an apparent helper failure or replay.
            descriptor = -1
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                # The receipt was committed before the compatibility marker.
                # A descriptor-close fault cannot revoke that outcome.
                pass
        try:
            _atomic_write_json(self._path(self.responses_dir, request_id), payload)
        except OSError:
            # The receipt is authoritative. response() can read it directly and
            # ordinary maintenance may safely discard/rebuild this cache.
            pass
        return True

    def _wake_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.wake_db), timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    @contextmanager
    def _wake_session(self) -> Iterator[sqlite3.Connection]:
        """Run one transaction and release its database handle deterministically."""

        connection = self._wake_connection()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize_wake_db(self) -> None:
        with self._wake_session() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS wake_requests (
                    generation INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL UNIQUE,
                    operation TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS wake_state (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    wake_id TEXT NOT NULL DEFAULT '',
                    generation INTEGER NOT NULL DEFAULT 0,
                    fence_token TEXT NOT NULL DEFAULT '',
                    lease_until REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'idle',
                    last_released_generation INTEGER NOT NULL DEFAULT 0,
                    last_probe_at REAL NOT NULL DEFAULT 0,
                    wake_origin TEXT NOT NULL DEFAULT 'scheduler',
                    authorized_request_id TEXT NOT NULL DEFAULT '',
                    authorized_operation TEXT NOT NULL DEFAULT '',
                    updated_at REAL NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS manual_cycle_tickets (
                    ticket_id TEXT PRIMARY KEY,
                    router_thread_id TEXT NOT NULL,
                    host_id TEXT NOT NULL DEFAULT '',
                    expected_operation TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    consumed_at REAL NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS final_return_receipts (
                    request_id TEXT PRIMARY KEY,
                    fence_token TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    gateway_thread_id TEXT NOT NULL DEFAULT '',
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
                );
                CREATE INDEX IF NOT EXISTS final_return_thread_state
                    ON final_return_receipts(thread_id, state);
                INSERT OR IGNORE INTO wake_state(singleton) VALUES(1);
                """
            )
            columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(wake_state)").fetchall()
            }
            if "last_probe_at" not in columns:
                connection.execute(
                    "ALTER TABLE wake_state ADD COLUMN last_probe_at REAL NOT NULL DEFAULT 0"
                )
            if "wake_origin" not in columns:
                connection.execute(
                    "ALTER TABLE wake_state ADD COLUMN wake_origin TEXT NOT NULL DEFAULT 'scheduler'"
                )
            if "authorized_request_id" not in columns:
                connection.execute(
                    "ALTER TABLE wake_state ADD COLUMN authorized_request_id TEXT NOT NULL DEFAULT ''"
                )
            if "authorized_operation" not in columns:
                connection.execute(
                    "ALTER TABLE wake_state ADD COLUMN authorized_operation TEXT NOT NULL DEFAULT ''"
                )
            wake_request_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(wake_requests)").fetchall()
            }
            if "operation" not in wake_request_columns:
                connection.execute(
                    "ALTER TABLE wake_requests ADD COLUMN operation TEXT NOT NULL DEFAULT ''"
                )
            final_return_columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(final_return_receipts)"
                ).fetchall()
            }
            final_return_migrations = {
                "gateway_thread_id": (
                    "ALTER TABLE final_return_receipts "
                    "ADD COLUMN gateway_thread_id TEXT NOT NULL DEFAULT ''"
                ),
                "prompt_hook_seen": (
                    "ALTER TABLE final_return_receipts "
                    "ADD COLUMN prompt_hook_seen INTEGER NOT NULL DEFAULT 0"
                ),
                "prompt_hook_turn_id": (
                    "ALTER TABLE final_return_receipts "
                    "ADD COLUMN prompt_hook_turn_id TEXT NOT NULL DEFAULT ''"
                ),
                "prompt_match_mode": (
                    "ALTER TABLE final_return_receipts "
                    "ADD COLUMN prompt_match_mode TEXT NOT NULL DEFAULT ''"
                ),
                "prompt_hook_rejection": (
                    "ALTER TABLE final_return_receipts "
                    "ADD COLUMN prompt_hook_rejection TEXT NOT NULL DEFAULT ''"
                ),
            }
            for column, statement in final_return_migrations.items():
                if column not in final_return_columns:
                    connection.execute(statement)

    @staticmethod
    def _validate_wake_id(wake_id: str) -> str:
        candidate = wake_id.strip().lower()
        if not WAKE_ID_PATTERN.fullmatch(candidate):
            raise RouterProtocolError("invalid Desktop Gateway wake id")
        return candidate

    @staticmethod
    def _validate_fence_token(fence_token: str) -> str:
        candidate = fence_token.strip().lower()
        if not FENCE_TOKEN_PATTERN.fullmatch(candidate):
            raise RouterProtocolError("invalid Desktop Gateway fence token")
        return candidate

    @staticmethod
    def _validate_manual_ticket(ticket_id: str) -> str:
        candidate = ticket_id.strip().lower()
        if not MANUAL_TICKET_PATTERN.fullmatch(candidate):
            raise RouterProtocolError("invalid manual Gateway cycle ticket")
        return candidate

    @staticmethod
    def _wake_is_live(row: sqlite3.Row, now: float) -> bool:
        return (
            str(row["status"] or "") in {"reserved", "active"}
            and float(row["lease_until"] or 0) > now
        )

    def _record_pending_request(
        self,
        request_id: str,
        created_at: float,
        operation: str = "",
    ) -> int:
        normalized_operation = operation.strip()
        if normalized_operation and normalized_operation not in ALLOWED_OPERATIONS:
            raise RouterProtocolError("invalid Desktop Gateway wake operation")
        with self._wake_session() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO wake_requests(
                    request_id, operation, created_at
                ) VALUES(?, ?, ?)
                """,
                (self._validate_request_id(request_id), normalized_operation, created_at),
            )
            if normalized_operation:
                connection.execute(
                    """
                    UPDATE wake_requests
                    SET operation=?
                    WHERE request_id=? AND operation=''
                    """,
                    (normalized_operation, request_id),
                )
            row = connection.execute(
                "SELECT generation, operation FROM wake_requests WHERE request_id=?",
                (request_id,),
            ).fetchone()
        if row is None:
            raise RouterProtocolError("could not record Desktop Gateway wake generation")
        recorded_operation = str(row["operation"] or "")
        if normalized_operation and recorded_operation != normalized_operation:
            raise RouterProtocolError(
                "Desktop Gateway wake metadata conflicts with request operation"
            )
        return int(row["generation"])

    def _reconcile_pending_requests(
        self,
        connection: sqlite3.Connection,
        pending_paths: list[Path],
    ) -> None:
        for path in pending_paths:
            try:
                request_id = self._validate_request_id(path.stem)
                created_at = path.stat().st_mtime
            except (OSError, RouterProtocolError):
                continue
            connection.execute(
                """
                INSERT OR IGNORE INTO wake_requests(
                    request_id, operation, created_at
                ) VALUES(?, '', ?)
                """,
                (request_id, created_at),
            )

    def _actionable_pending_paths(self) -> list[Path]:
        """Return canonical requests that have neither a claim nor a terminal result.

        The pending file is the immutable, stable publication anchor for a
        request.  A claim must never move or delete it: keeping that pathname
        occupied prevents a second producer from republishing the same
        deterministic request id while the first claim is in flight.
        """

        actionable: list[Path] = []
        for path in self.pending_dir.glob("*.json"):
            try:
                request_id = self._validate_request_id(path.stem)
                if self._terminal_result_exists(request_id):
                    continue
                if self._path(self.claimed_dir, request_id).exists():
                    continue
            except (OSError, RouterProtocolError):
                continue
            actionable.append(path)
        return actionable

    def _wake_snapshot(
        self,
        now: float,
    ) -> tuple[int, bool, float | None, bool, float | None]:
        try:
            with self._wake_session() as connection:
                generation_row = connection.execute(
                    "SELECT COALESCE(MAX(generation), 0) AS generation FROM wake_requests"
                ).fetchone()
                state = connection.execute(
                    "SELECT * FROM wake_state WHERE singleton=1"
                ).fetchone()
        except sqlite3.Error:
            return 0, False, None, False, None
        generation = int(generation_row["generation"] if generation_row else 0)
        try:
            last_probe_at = float(state["last_probe_at"] or 0) if state is not None else 0
        except (KeyError, TypeError, ValueError, IndexError):
            last_probe_at = 0
        sentinel_age = max(0.0, now - last_probe_at) if last_probe_at else None
        sentinel_fresh = bool(
            sentinel_age is not None and sentinel_age <= self.scheduler_ttl_seconds
        )
        if state is None or not self._wake_is_live(state, now):
            return generation, False, None, sentinel_fresh, sentinel_age
        remaining = max(0.0, float(state["lease_until"] or 0) - now)
        return generation, True, remaining, sentinel_fresh, sentinel_age

    def sentinel_probe(
        self,
        router_thread_id: str,
        host_id: str = "",
        now: float | None = None,
    ) -> dict[str, Any]:
        """Reserve one metadata-only wake without reading any queue payload."""

        current_time = time.time() if now is None else now
        registration = _read_json(self.registration_file) or {}
        registered_id = str(registration.get("router_thread_id") or "").strip()
        registered_host = str(registration.get("host_id") or "").strip()
        expected_id = router_thread_id.strip()
        expected_host = host_id.strip()
        if expected_id and registered_id and expected_id != registered_id:
            raise RouterProtocolError(
                "active-work lease heartbeat is pinned to a different registered Gateway task"
            )
        if expected_host and registered_host and expected_host != registered_host:
            raise RouterProtocolError("Gateway host does not match registration")
        router_thread_id = registered_id
        host_id = registered_host or expected_host
        pending_paths = sorted(self._actionable_pending_paths(), key=lambda path: path.name)
        claimed_count = sum(
            1
            for path in self.claimed_dir.glob("*.json")
            if not self._terminal_result_exists(path.stem)
        )
        try:
            connection = self._wake_connection()
            connection.execute("BEGIN IMMEDIATE")
            self._reconcile_pending_requests(connection, pending_paths)
            generation_row = connection.execute(
                "SELECT COALESCE(MAX(generation), 0) AS generation FROM wake_requests"
            ).fetchone()
            generation = int(generation_row["generation"] if generation_row else 0)
            state = connection.execute(
                "SELECT * FROM wake_state WHERE singleton=1"
            ).fetchone()
            if state is None:
                raise RouterProtocolError("Desktop Gateway wake state is missing")
            connection.execute(
                "UPDATE wake_state SET last_probe_at=?, updated_at=? WHERE singleton=1",
                (current_time, current_time),
            )
            if self._wake_is_live(state, current_time):
                connection.commit()
                return {
                    "should_wake": False,
                    "reason": "wake_inflight",
                    "pending_count": len(pending_paths),
                    "claimed_count": claimed_count,
                    "wake_generation": generation,
                    "lease_remaining_seconds": max(
                        0.0, float(state["lease_until"] or 0) - current_time
                    ),
                }
            if not router_thread_id:
                connection.execute(
                    "UPDATE wake_state SET status='idle', wake_id='', fence_token='', "
                    "lease_until=0, wake_origin='scheduler', authorized_request_id='', "
                    "authorized_operation='', updated_at=? WHERE singleton=1",
                    (current_time,),
                )
                connection.commit()
                return {
                    "should_wake": False,
                    "reason": "router_not_registered",
                    "pending_count": len(pending_paths),
                    "claimed_count": claimed_count,
                    "wake_generation": generation,
                }
            if not pending_paths:
                connection.execute(
                    "UPDATE wake_state SET status='idle', wake_id='', fence_token='', "
                    "lease_until=0, wake_origin='scheduler', authorized_request_id='', "
                    "authorized_operation='', updated_at=? WHERE singleton=1",
                    (current_time,),
                )
                connection.commit()
                return {
                    "should_wake": False,
                    "reason": "empty",
                    "pending_count": 0,
                    "claimed_count": claimed_count,
                    "wake_generation": generation,
                }
            wake_id = uuid.uuid4().hex
            fence_token = uuid.uuid4().hex
            lease_until = current_time + self.wake_lease_ttl_seconds
            connection.execute(
                """
                UPDATE wake_state
                SET wake_id=?, generation=?, fence_token=?, lease_until=?,
                    status='reserved', wake_origin='scheduler',
                    authorized_request_id='', authorized_operation='', updated_at=?
                WHERE singleton=1
                """,
                (wake_id, generation, fence_token, lease_until, current_time),
            )
            connection.commit()
            return {
                "should_wake": True,
                "reason": "pending",
                "pending_count": len(pending_paths),
                "claimed_count": claimed_count,
                "wake_generation": generation,
                "wake_id": wake_id,
                "fence_token": fence_token,
                "lease_until": lease_until,
                "router_thread_id": router_thread_id,
                "host_id": host_id,
            }
        except sqlite3.Error as exc:
            try:
                connection.rollback()
            except (NameError, sqlite3.Error):
                pass
            raise RouterProtocolError(f"Desktop Gateway wake database failed: {exc}") from exc
        finally:
            try:
                connection.close()
            except NameError:
                pass

    def authorize_manual_cycle(
        self,
        router_thread_id: str,
        host_id: str,
        expected_operation: str,
        *,
        ttl_seconds: int = 300,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Issue one expiring owner-approved ticket without reading queue payloads."""

        operation = expected_operation.strip()
        if operation not in ALLOWED_OPERATIONS:
            raise RouterProtocolError("manual Gateway cycle operation is not allowed")
        registered, resolved_host = self._registered_router(router_thread_id, host_id)
        current_time = time.time() if now is None else now
        bounded_ttl = max(30, min(int(ttl_seconds), 600))
        ticket_id = uuid.uuid4().hex
        try:
            with self._wake_session() as connection:
                connection.execute("BEGIN IMMEDIATE")
                state = connection.execute(
                    "SELECT * FROM wake_state WHERE singleton=1"
                ).fetchone()
                if state is None:
                    raise RouterProtocolError("Desktop Gateway wake state is missing")
                if self._wake_is_live(state, current_time):
                    raise RouterProtocolError(
                        "cannot authorize a manual Gateway cycle while another wake is active"
                    )
                connection.execute(
                    """
                    INSERT INTO manual_cycle_tickets(
                        ticket_id, router_thread_id, host_id, expected_operation,
                        created_at, expires_at, consumed_at
                    ) VALUES(?, ?, ?, ?, ?, ?, 0)
                    """,
                    (
                        ticket_id,
                        registered,
                        resolved_host,
                        operation,
                        current_time,
                        current_time + bounded_ttl,
                    ),
                )
        except sqlite3.Error as exc:
            raise RouterProtocolError(
                f"Desktop Gateway manual-cycle database failed: {exc}"
            ) from exc
        return {
            "ticket_id": ticket_id,
            "router_thread_id": registered,
            "host_id": resolved_host,
            "expected_operation": operation,
            "expires_at": current_time + bounded_ttl,
        }

    def manual_probe(
        self,
        ticket_id: str,
        router_thread_id: str,
        host_id: str = "",
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Consume one ticket and reserve a wake for exactly one matching request.

        Unlike the scheduler's metadata-only probe, this approved diagnostic
        reads only request IDs and operation names. It never returns request
        bodies and it deliberately does not refresh scheduler freshness.
        """

        ticket_id = self._validate_manual_ticket(ticket_id)
        registered, resolved_host = self._registered_router(router_thread_id, host_id)
        current_time = time.time() if now is None else now
        sortable_pending: list[tuple[float, str, Path]] = []
        for path in self._actionable_pending_paths():
            try:
                sortable_pending.append((path.stat().st_mtime, path.name, path))
            except OSError:
                continue
        pending_paths = [item[2] for item in sorted(sortable_pending)]
        try:
            connection = self._wake_connection()
            connection.execute("BEGIN IMMEDIATE")
            ticket = connection.execute(
                "SELECT * FROM manual_cycle_tickets WHERE ticket_id=?",
                (ticket_id,),
            ).fetchone()
            if ticket is None:
                raise RouterProtocolError("manual Gateway cycle ticket is unknown")
            if float(ticket["consumed_at"] or 0) > 0:
                raise RouterProtocolError("manual Gateway cycle ticket was already consumed")
            if float(ticket["expires_at"] or 0) <= current_time:
                connection.execute(
                    "UPDATE manual_cycle_tickets SET consumed_at=? WHERE ticket_id=?",
                    (current_time, ticket_id),
                )
                connection.commit()
                raise RouterProtocolError("manual Gateway cycle ticket has expired")
            if str(ticket["router_thread_id"] or "") != registered:
                raise RouterProtocolError("manual Gateway cycle ticket belongs to another task")
            ticket_host = str(ticket["host_id"] or "")
            if ticket_host and resolved_host and ticket_host != resolved_host:
                raise RouterProtocolError("manual Gateway cycle ticket belongs to another host")
            expected_operation = str(ticket["expected_operation"] or "")
            if expected_operation not in ALLOWED_OPERATIONS:
                raise RouterProtocolError("manual Gateway cycle ticket operation is invalid")

            state = connection.execute(
                "SELECT * FROM wake_state WHERE singleton=1"
            ).fetchone()
            if state is None:
                raise RouterProtocolError("Desktop Gateway wake state is missing")
            connection.execute(
                "UPDATE manual_cycle_tickets SET consumed_at=? WHERE ticket_id=?",
                (current_time, ticket_id),
            )
            if self._wake_is_live(state, current_time):
                connection.commit()
                return {
                    "should_wake": False,
                    "reason": "wake_inflight",
                    "pending_count": len(pending_paths),
                    "claimed_count": sum(
                        1
                        for path in self.claimed_dir.glob("*.json")
                        if not self._terminal_result_exists(path.stem)
                    ),
                    "manual_ticket_consumed": True,
                }

            self._reconcile_pending_requests(connection, pending_paths)
            selected_path: Path | None = None
            for path in pending_paths:
                operation_row = connection.execute(
                    "SELECT operation FROM wake_requests WHERE request_id=?",
                    (self._validate_request_id(path.stem),),
                ).fetchone()
                if (
                    operation_row is not None
                    and str(operation_row["operation"] or "") == expected_operation
                ):
                    selected_path = path
                    break
            if selected_path is None:
                connection.commit()
                return {
                    "should_wake": False,
                    "reason": "expected_request_not_pending",
                    "pending_count": len(pending_paths),
                    "claimed_count": sum(
                        1
                        for path in self.claimed_dir.glob("*.json")
                        if not self._terminal_result_exists(path.stem)
                    ),
                    "expected_operation": expected_operation,
                    "manual_ticket_consumed": True,
                }

            generation_row = connection.execute(
                "SELECT COALESCE(MAX(generation), 0) AS generation FROM wake_requests"
            ).fetchone()
            generation = int(generation_row["generation"] if generation_row else 0)
            wake_id = uuid.uuid4().hex
            fence_token = uuid.uuid4().hex
            lease_until = current_time + self.wake_lease_ttl_seconds
            request_id = self._validate_request_id(selected_path.stem)
            connection.execute(
                """
                UPDATE wake_state
                SET wake_id=?, generation=?, fence_token=?, lease_until=?,
                    status='reserved', wake_origin='manual_ticket',
                    authorized_request_id=?, authorized_operation=?, updated_at=?
                WHERE singleton=1
                """,
                (
                    wake_id,
                    generation,
                    fence_token,
                    lease_until,
                    request_id,
                    expected_operation,
                    current_time,
                ),
            )
            connection.commit()
            return {
                "should_wake": True,
                "reason": "manual_ticket",
                "pending_count": len(pending_paths),
                "claimed_count": sum(
                    1
                    for path in self.claimed_dir.glob("*.json")
                    if not self._terminal_result_exists(path.stem)
                ),
                "wake_generation": generation,
                "wake_id": wake_id,
                "fence_token": fence_token,
                "lease_until": lease_until,
                "router_thread_id": registered,
                "host_id": resolved_host,
                "expected_operation": expected_operation,
                "manual_ticket_consumed": True,
            }
        except sqlite3.Error as exc:
            try:
                connection.rollback()
            except (NameError, sqlite3.Error):
                pass
            raise RouterProtocolError(
                f"Desktop Gateway manual-cycle database failed: {exc}"
            ) from exc
        finally:
            try:
                connection.close()
            except NameError:
                pass

    def stage_path(self, request_id: str, fence_token: str = "") -> Path:
        request_id = self._validate_request_id(request_id)
        request = _read_json(self._path(self.claimed_dir, request_id))
        if request is None:
            raise RouterProtocolError("Desktop Gateway request is not a valid claim")
        supplied = self._validate_fence_token(fence_token)
        self._validate_claim_fence(request, supplied)
        return self.staging_dir / f"{request_id}.{supplied[:16]}.txt"

    @staticmethod
    def _validate_turn_id(turn_id: str) -> str:
        candidate = turn_id.strip()
        if not THREAD_ID_PATTERN.fullmatch(candidate):
            raise RouterProtocolError("invalid Codex target turn id")
        return candidate

    @staticmethod
    def _prompt_sha256(prompt: str) -> str:
        if not isinstance(prompt, str) or not prompt:
            raise RouterProtocolError("final-return prompt must be a non-empty string")
        return hashlib.sha256(prompt.encode("utf-8")).hexdigest()

    @classmethod
    def _match_final_return_prompt(
        cls,
        prompt: str,
        expected_prompt_sha256: str,
        expected_gateway_thread_id: str,
    ) -> tuple[str, str]:
        """Match a raw or Desktop-delegated prompt without normalizing its input."""

        if not isinstance(prompt, str) or not prompt:
            return "", "invalid_prompt"
        if cls._prompt_sha256(prompt) == expected_prompt_sha256:
            return "raw", ""
        match = CODEX_DELEGATION_PROMPT_PATTERN.fullmatch(prompt)
        if match is None:
            rejection = (
                "malformed_delegation"
                if prompt.startswith("<codex_delegation>")
                else "prompt_mismatch"
            )
            return "", rejection
        source_thread_id = match.group("source_thread_id").strip()
        if source_thread_id != expected_gateway_thread_id:
            return "", "gateway_mismatch"
        delegated_input = match.group("input")
        if cls._prompt_sha256(delegated_input) != expected_prompt_sha256:
            return "", "prompt_mismatch"
        return "delegated", ""

    @staticmethod
    def _bounded_final_text(text: str) -> str:
        if not isinstance(text, str) or not text.strip():
            raise RouterProtocolError("final-return answer must be non-empty text")
        if len(text) <= FINAL_RETURN_MAX_CHARS:
            return text
        suffix = "\n[已截断]"
        return text[: FINAL_RETURN_MAX_CHARS - len(suffix)].rstrip() + suffix

    def _claimed_final_return_request(
        self,
        request_id: str,
        fence_token: str,
    ) -> tuple[str, str, dict[str, Any]]:
        request_id = self._validate_request_id(request_id)
        supplied_fence = self._validate_fence_token(fence_token)
        request = _read_json(self._path(self.claimed_dir, request_id))
        if request is None:
            raise RouterProtocolError("final-return request is not a valid claim")
        self._validate_claim_fence(request, supplied_fence)
        if str(request.get("operation") or "") != "send_message_to_thread":
            raise RouterProtocolError("final-return receipts support ordinary message sends only")
        payload = request.get("payload")
        if not isinstance(payload, dict) or str(payload.get("mode") or "") == "steer":
            raise RouterProtocolError("final-return receipt cannot arm a steer request")
        return request_id, supplied_fence, request

    def arm_final_return(
        self,
        request_id: str,
        fence_token: str,
        thread_id: str,
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Arm one exact claimed prompt before the Gateway submits it once."""

        request_id, supplied_fence, request = self._claimed_final_return_request(
            request_id,
            fence_token,
        )
        target_thread_id = thread_id.strip()
        if not looks_like_thread_id(target_thread_id):
            raise RouterProtocolError("invalid final-return target task id")
        payload = request.get("payload")
        assert isinstance(payload, dict)
        if str(payload.get("target_thread_id") or "").strip() != target_thread_id:
            raise RouterProtocolError("final-return target does not match the claimed request")
        prompt_sha256 = self._prompt_sha256(payload.get("prompt"))
        registration = _read_json(self.registration_file) or {}
        gateway_thread_id = str(registration.get("router_thread_id") or "").strip()
        if not looks_like_thread_id(gateway_thread_id):
            raise RouterProtocolError(
                "final-return arm requires one valid registered Desktop Gateway task"
            )
        current_time = time.time() if now is None else now
        expires_at = current_time + FINAL_RETURN_ARM_TTL_SECONDS
        try:
            with self._wake_session() as connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT * FROM final_return_receipts WHERE request_id=?",
                    (request_id,),
                ).fetchone()
                if existing is not None:
                    if (
                        str(existing["fence_token"] or "") != supplied_fence
                        or str(existing["thread_id"] or "") != target_thread_id
                        or str(existing["gateway_thread_id"] or "")
                        != gateway_thread_id
                        or str(existing["prompt_sha256"] or "") != prompt_sha256
                    ):
                        raise RouterProtocolError(
                            "final-return request was re-armed with conflicting identity"
                        )
                    return {
                        "armed": True,
                        "state": str(existing["state"] or ""),
                        "expires_at": float(existing["expires_at"] or 0),
                    }
                connection.execute(
                    """
                    UPDATE final_return_receipts
                    SET state='expired', updated_at=?
                    WHERE thread_id=? AND state='armed' AND expires_at < ?
                    """,
                    (current_time, target_thread_id, current_time),
                )
                conflict = connection.execute(
                    """
                    SELECT request_id FROM final_return_receipts
                    WHERE thread_id=? AND state IN ('armed','bound','captured','native')
                    LIMIT 1
                    """,
                    (target_thread_id,),
                ).fetchone()
                if conflict is not None:
                    raise RouterProtocolError(
                        "another final-return request already owns this target task"
                    )
                connection.execute(
                    """
                    INSERT INTO final_return_receipts(
                        request_id, fence_token, thread_id, gateway_thread_id,
                        prompt_sha256, state,
                        created_at, updated_at, expires_at
                    ) VALUES(?, ?, ?, ?, ?, 'armed', ?, ?, ?)
                    """,
                    (
                        request_id,
                        supplied_fence,
                        target_thread_id,
                        gateway_thread_id,
                        prompt_sha256,
                        current_time,
                        current_time,
                        expires_at,
                    ),
                )
        except sqlite3.Error as exc:
            raise RouterProtocolError(f"final-return database failed: {exc}") from exc
        return {"armed": True, "state": "armed", "expires_at": expires_at}

    def bind_final_return_prompt(
        self,
        session_id: str,
        turn_id: str,
        prompt: str,
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Bind UserPromptSubmit only to a matching active Gateway arm."""

        target_thread_id = session_id.strip()
        if not looks_like_thread_id(target_thread_id):
            return {"accepted": False, "state": "ignored"}
        target_turn_id = self._validate_turn_id(turn_id)
        current_time = time.time() if now is None else now
        try:
            with self._wake_session() as connection:
                connection.execute("BEGIN IMMEDIATE")
                rows = connection.execute(
                    """
                    SELECT * FROM final_return_receipts
                    WHERE thread_id=? AND state IN ('armed','bound','captured')
                    """,
                    (target_thread_id,),
                ).fetchall()
                if len(rows) != 1:
                    return {"accepted": False, "state": "ignored"}
                row = rows[0]
                match_mode, rejection = self._match_final_return_prompt(
                    prompt,
                    str(row["prompt_sha256"] or ""),
                    str(row["gateway_thread_id"] or ""),
                )
                connection.execute(
                    """
                    UPDATE final_return_receipts
                    SET prompt_hook_seen=1, prompt_hook_turn_id=?,
                        prompt_match_mode=?, prompt_hook_rejection=?, updated_at=?
                    WHERE request_id=?
                    """,
                    (
                        target_turn_id,
                        match_mode,
                        rejection,
                        current_time,
                        str(row["request_id"]),
                    ),
                )
                if rejection:
                    return {"accepted": False, "state": "ignored"}
                if (
                    str(row["session_id"] or "") not in {"", target_thread_id}
                    or str(row["turn_id"] or "") not in {"", target_turn_id}
                ):
                    connection.execute(
                        """
                        UPDATE final_return_receipts
                        SET prompt_hook_rejection='turn_mismatch', updated_at=?
                        WHERE request_id=?
                        """,
                        (current_time, str(row["request_id"])),
                    )
                    return {"accepted": False, "state": "ignored"}
                if float(row["expires_at"] or 0) < current_time:
                    return {"accepted": False, "state": "expired"}
                request = _read_json(
                    self._path(self.claimed_dir, str(row["request_id"] or ""))
                )
                if request is None:
                    return {"accepted": False, "state": "ignored"}
                self._validate_claim_fence(request, str(row["fence_token"] or ""))
                state = str(row["state"] or "")
                if state == "armed":
                    connection.execute(
                        """
                        UPDATE final_return_receipts
                        SET state='bound', session_id=?, turn_id=?, updated_at=?
                        WHERE request_id=? AND state='armed'
                        """,
                        (
                            target_thread_id,
                            target_turn_id,
                            current_time,
                            str(row["request_id"]),
                        ),
                    )
                    state = "bound"
                return {"accepted": True, "state": state}
        except sqlite3.Error as exc:
            raise RouterProtocolError(f"final-return database failed: {exc}") from exc

    def capture_final_return(
        self,
        session_id: str,
        turn_id: str,
        answer: str | None,
        *,
        stop_hook_active: bool = False,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Capture one exact Stop final without exposing it on helper stdout."""

        if type(stop_hook_active) is not bool:
            raise RouterProtocolError("final-return Stop continuation marker must be boolean")
        if not isinstance(answer, str) or not answer.strip():
            return {"accepted": False, "state": "ignored"}
        target_thread_id = session_id.strip()
        if not looks_like_thread_id(target_thread_id):
            return {"accepted": False, "state": "ignored"}
        target_turn_id = self._validate_turn_id(turn_id)
        bounded_answer = self._bounded_final_text(answer)
        answer_sha256 = hashlib.sha256(bounded_answer.encode("utf-8")).hexdigest()
        current_time = time.time() if now is None else now
        try:
            with self._wake_session() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """
                    SELECT * FROM final_return_receipts
                    WHERE thread_id=? AND session_id=? AND turn_id=?
                      AND state IN ('bound','captured')
                    LIMIT 1
                    """,
                    (target_thread_id, target_thread_id, target_turn_id),
                ).fetchone()
                if row is None:
                    return {"accepted": False, "state": "ignored"}
                request = _read_json(
                    self._path(self.claimed_dir, str(row["request_id"] or ""))
                )
                if request is None:
                    return {"accepted": False, "state": "ignored"}
                self._validate_claim_fence(request, str(row["fence_token"] or ""))
                if str(row["state"] or "") == "captured":
                    if str(row["answer_sha256"] or "") == answer_sha256:
                        return {"accepted": True, "state": "captured"}
                # A Stop hook may deliberately continue one Codex turn.  Its
                # later Stop event keeps the same task/turn identity and has
                # stop_hook_active=true.  Replace the provisional same-turn
                # text so the completed turn resolves to its last Stop value.
                stage_path = self.stage_path(
                    str(row["request_id"]),
                    str(row["fence_token"]),
                )
                _atomic_write_text(stage_path, bounded_answer)
                connection.execute(
                    """
                    UPDATE final_return_receipts
                    SET state='captured', answer_sha256=?, answer_chars=?, updated_at=?
                    WHERE request_id=? AND state IN ('bound','captured')
                    """,
                    (
                        answer_sha256,
                        len(bounded_answer),
                        current_time,
                        str(row["request_id"]),
                    ),
                )
                return {"accepted": True, "state": "captured"}
        except sqlite3.Error as exc:
            raise RouterProtocolError(f"final-return database failed: {exc}") from exc

    def final_return_status(
        self,
        request_id: str,
        fence_token: str,
        thread_id: str,
        turn_id: str,
    ) -> dict[str, Any]:
        """Read only exact-match receipt state; never emit answer text."""

        request_id, supplied_fence, _ = self._claimed_final_return_request(
            request_id,
            fence_token,
        )
        target_thread_id = thread_id.strip()
        target_turn_id = self._validate_turn_id(turn_id)
        try:
            with self._wake_session() as connection:
                row = connection.execute(
                    "SELECT * FROM final_return_receipts WHERE request_id=?",
                    (request_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise RouterProtocolError(f"final-return database failed: {exc}") from exc
        if row is None:
            return {"available": False, "state": "unarmed"}
        if (
            str(row["fence_token"] or "") != supplied_fence
            or str(row["thread_id"] or "") != target_thread_id
        ):
            raise RouterProtocolError("final-return receipt identity mismatch")
        state = str(row["state"] or "")
        if state in {"bound", "captured", "native"} and (
            str(row["session_id"] or "") != target_thread_id
            or str(row["turn_id"] or "") != target_turn_id
        ):
            return {"available": False, "state": "turn_mismatch"}
        if state != "captured":
            result = {"available": False, "state": state or "invalid"}
            if state == "armed":
                hook_turn_id = str(row["prompt_hook_turn_id"] or "")
                result.update(
                    {
                        "prompt_hook_seen": bool(row["prompt_hook_seen"]),
                        "prompt_hook_turn_matches": bool(hook_turn_id)
                        and hook_turn_id == target_turn_id,
                        "prompt_match_mode": str(row["prompt_match_mode"] or "none"),
                        "prompt_hook_rejection": str(
                            row["prompt_hook_rejection"] or "none"
                        ),
                    }
                )
            return result
        stage_path = self.stage_path(request_id, supplied_fence)
        try:
            staged = stage_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise RouterProtocolError("captured final-return staging file is missing") from exc
        if (
            hashlib.sha256(staged.encode("utf-8")).hexdigest()
            != str(row["answer_sha256"] or "")
            or len(staged) != int(row["answer_chars"] or -1)
        ):
            raise RouterProtocolError("captured final-return staging file failed integrity")
        return {"available": True, "state": "captured"}

    def resolve_final_return_native(
        self,
        request_id: str,
        fence_token: str,
        thread_id: str,
        turn_id: str,
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Fence late Stop capture before Gateway stages a native same-turn final."""

        request_id, supplied_fence, _ = self._claimed_final_return_request(
            request_id,
            fence_token,
        )
        target_thread_id = thread_id.strip()
        target_turn_id = self._validate_turn_id(turn_id)
        current_time = time.time() if now is None else now
        try:
            with self._wake_session() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM final_return_receipts WHERE request_id=?",
                    (request_id,),
                ).fetchone()
                if row is None:
                    raise RouterProtocolError("final-return request was not armed")
                if (
                    str(row["fence_token"] or "") != supplied_fence
                    or str(row["thread_id"] or "") != target_thread_id
                ):
                    raise RouterProtocolError("final-return receipt identity mismatch")
                state = str(row["state"] or "")
                if state == "captured":
                    raise RouterProtocolError(
                        "captured Hook final must be resolved before native fallback"
                    )
                if state not in {"armed", "bound", "native"}:
                    raise RouterProtocolError("final-return receipt cannot use native fallback")
                if state == "bound" and (
                    str(row["session_id"] or "") != target_thread_id
                    or str(row["turn_id"] or "") != target_turn_id
                ):
                    raise RouterProtocolError("native final does not match the Hook-bound turn")
                connection.execute(
                    """
                    UPDATE final_return_receipts
                    SET state='native', session_id=?, turn_id=?, updated_at=?
                    WHERE request_id=?
                    """,
                    (target_thread_id, target_turn_id, current_time, request_id),
                )
        except sqlite3.Error as exc:
            raise RouterProtocolError(f"final-return database failed: {exc}") from exc
        return {"resolved": True, "state": "native"}

    def _terminalize_final_return(self, request_id: str, state: str) -> None:
        try:
            with self._wake_session() as connection:
                connection.execute(
                    """
                    UPDATE final_return_receipts
                    SET state=?, updated_at=? WHERE request_id=?
                    """,
                    (state, time.time(), self._validate_request_id(request_id)),
                )
        except (sqlite3.Error, RouterProtocolError):
            # The queue terminal receipt is authoritative. Hook bookkeeping
            # cannot revoke a committed target outcome.
            pass

    def register(self, router_thread_id: str, host_id: str = "", *, force: bool = False) -> None:
        candidate = router_thread_id.strip()
        if not looks_like_thread_id(candidate):
            raise RouterProtocolError("invalid Desktop Gateway task id")
        current = _read_json(self.registration_file) or {}
        current_thread = str(current.get("router_thread_id") or "").strip()
        if current_thread and current_thread != candidate and not force:
            raise RouterProtocolError(
                "a different Desktop Gateway task is already registered; explicit replacement is required"
            )
        now = time.time()
        _atomic_write_json(
            self.registration_file,
            {
                "schema_version": ROUTER_SCHEMA_VERSION,
                "router_thread_id": candidate,
                "host_id": host_id.strip(),
                "registered_at": float(current.get("registered_at", now) or now),
                "updated_at": now,
            },
        )
        with self._wake_session() as connection:
            connection.execute(
                """
                UPDATE wake_state
                SET wake_id='', fence_token='', lease_until=0, status='idle',
                    last_probe_at=0, wake_origin='scheduler',
                    authorized_request_id='', authorized_operation='', updated_at=?
                WHERE singleton=1
                """,
                (now,),
            )
        self.heartbeat(candidate, host_id)

    def heartbeat(self, router_thread_id: str, host_id: str = "") -> None:
        candidate = router_thread_id.strip()
        registration = _read_json(self.registration_file) or {}
        registered = str(registration.get("router_thread_id") or "").strip()
        if not registered:
            raise RouterProtocolError("Desktop Gateway task is not registered")
        if candidate != registered:
            raise RouterProtocolError(
                "active-work lease heartbeat came from a different Desktop Gateway task"
            )
        registered_host = str(registration.get("host_id") or "").strip()
        supplied_host = host_id.strip()
        if registered_host and supplied_host and registered_host != supplied_host:
            raise RouterProtocolError(
                "active-work lease heartbeat host does not match the registered Desktop host"
            )
        _atomic_write_json(
            self.heartbeat_file,
            {
                "schema_version": ROUTER_SCHEMA_VERSION,
                "router_thread_id": candidate,
                "host_id": supplied_host or registered_host,
                "updated_at": time.time(),
            },
        )

    def status(self, now: float | None = None) -> RouterStatus:
        current_time = time.time() if now is None else now
        registration = _read_json(self.registration_file) or {}
        heartbeat = _read_json(self.heartbeat_file) or {}
        router_thread_id = str(registration.get("router_thread_id") or "").strip()
        heartbeat_thread_id = str(heartbeat.get("router_thread_id") or "").strip()
        try:
            updated_at = float(heartbeat.get("updated_at", 0) or 0)
        except (TypeError, ValueError):
            updated_at = 0
        age = max(0.0, current_time - updated_at) if updated_at else None
        (
            generation,
            wake_inflight,
            wake_remaining,
            sentinel_fresh,
            sentinel_age,
        ) = self._wake_snapshot(current_time)
        registered = bool(router_thread_id)
        ready = bool(
            registered
            and heartbeat_thread_id == router_thread_id
            and age is not None
            and age <= self.heartbeat_ttl_seconds
        )
        return RouterStatus(
            ready=ready,
            registered=registered,
            router_thread_id=router_thread_id,
            host_id=str(heartbeat.get("host_id") or registration.get("host_id") or "").strip(),
            heartbeat_age_seconds=age,
            pending=len(self._actionable_pending_paths()),
            claimed=sum(
                1
                for path in self.claimed_dir.glob("*.json")
                if not self._terminal_result_exists(path.stem)
            ),
            wake_generation=generation,
            wake_inflight=wake_inflight,
            wake_lease_remaining_seconds=wake_remaining,
            sentinel_fresh=sentinel_fresh,
            sentinel_age_seconds=sentinel_age,
        )

    def is_registered(self) -> bool:
        return self.status().registered

    @staticmethod
    def _fingerprint(payload: dict[str, Any]) -> str:
        wire = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(wire.encode("utf-8")).hexdigest()

    @staticmethod
    def _response_allows_retry(response: dict[str, Any]) -> bool:
        if str(response.get("status") or "") != "failed":
            return False
        error = response.get("error")
        details = error if isinstance(error, dict) else {}
        raw_code = details.get("code")
        if not isinstance(raw_code, str) or not raw_code.strip():
            return False
        code = raw_code.strip()
        if code in {
            "target_archived",
            "target_not_found",
            "target_result_unknown",
            "turn_interrupted",
            "target_needs_attention",
            "target_tool_unavailable",
            "project_not_registered",
        }:
            return False
        return (
            details.get("retryable") is True
            and details.get("may_have_started") is False
        )

    def submit(
        self,
        operation: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> str:
        if operation not in ALLOWED_OPERATIONS:
            raise RouterProtocolError(f"unsupported Desktop Gateway operation: {operation}")
        retry_generation = 0
        # Generation zero is the initial request; at most 64 later retry
        # generations may follow explicitly safe terminal failures.
        while retry_generation <= MAX_RETRY_GENERATIONS:
            request_id = _request_id(operation, idempotency_key, retry_generation)
            body = {
                "schema_version": ROUTER_SCHEMA_VERSION,
                "request_id": request_id,
                "operation": operation,
                "payload": payload,
            }
            if retry_generation:
                body["retry_generation"] = retry_generation
            fingerprint = self._fingerprint(body)
            request = {
                **body,
                "fingerprint": fingerprint,
                "created_at": time.time(),
            }

            # A terminal response takes precedence over the retained claimed
            # request file. Only an explicit safe-to-retry failure advances to
            # the next deterministic request generation.
            existing_response = self.response(request_id)
            if existing_response is not None:
                existing = existing_response
                if existing.get("fingerprint") != fingerprint:
                    raise RouterProtocolError(
                        "completed Desktop Gateway request was reused with different content"
                    )
                if self._response_allows_retry(existing):
                    if not idempotency_key:
                        raise RouterProtocolError(
                            "retryable Desktop Gateway request has no idempotency key"
                        )
                    retry_generation += 1
                    continue
                return request_id

            found_request = False
            for directory in (self.pending_dir, self.claimed_dir):
                existing_path = self._path(directory, request_id)
                existing = _read_json(existing_path)
                if existing is None:
                    if existing_path.exists():
                        raise RouterProtocolError(
                            "Desktop Gateway request state exists but is unreadable"
                        )
                    continue
                if existing.get("fingerprint") != fingerprint:
                    raise RouterProtocolError(
                        "idempotent Desktop Gateway request was reused with different content"
                    )
                found_request = True
                if directory == self.pending_dir:
                    try:
                        self._record_pending_request(
                            request_id,
                            float(existing.get("created_at", time.time()) or time.time()),
                            operation,
                        )
                    except sqlite3.Error:
                        # The pending file is authoritative. Metadata-probe reconciliation
                        # repairs a missed generation record on its next fresh run.
                        pass
            if found_request:
                return request_id

            if self._has_terminal_fence(request_id):
                raise RouterProtocolError(
                    "Desktop Gateway request has a terminal receipt without a readable response"
                )
            pending_path = self._path(self.pending_dir, request_id)
            if not _atomic_write_json_exclusive(pending_path, request):
                # Another producer published this deterministic request between
                # our existence check and publication. Reconcile against the
                # winner; never overwrite a different fingerprint.
                concurrent_response = self.response(request_id)
                if concurrent_response is not None:
                    if concurrent_response.get("fingerprint") != fingerprint:
                        raise RouterProtocolError(
                            "completed Desktop Gateway request was reused with different content"
                        )
                    if self._response_allows_retry(concurrent_response):
                        if not idempotency_key:
                            raise RouterProtocolError(
                                "retryable Desktop Gateway request has no idempotency key"
                            )
                        retry_generation += 1
                        continue
                    return request_id
                found_request = False
                for directory in (self.pending_dir, self.claimed_dir):
                    existing_path = self._path(directory, request_id)
                    existing = _read_json(existing_path)
                    if existing is None:
                        if existing_path.exists():
                            raise RouterProtocolError(
                                "Desktop Gateway request state exists but is unreadable"
                            )
                        continue
                    if existing.get("fingerprint") != fingerprint:
                        raise RouterProtocolError(
                            "idempotent Desktop Gateway request was reused with different content"
                        )
                    found_request = True
                    if directory == self.pending_dir:
                        try:
                            self._record_pending_request(
                                request_id,
                                float(existing.get("created_at", time.time()) or time.time()),
                                operation,
                            )
                        except sqlite3.Error:
                            pass
                if found_request:
                    return request_id
                if self._has_terminal_fence(request_id):
                    raise RouterProtocolError(
                        "Desktop Gateway request has a terminal receipt without a readable response"
                    )
                raise RouterProtocolError(
                    "concurrent Desktop Gateway request publication could not be reconciled"
                )
            try:
                self._record_pending_request(
                    request_id,
                    float(request["created_at"]),
                    operation,
                )
            except sqlite3.Error:
                # Never discard a durable request because wake metadata was briefly
                # locked. The metadata-only Gateway probe repairs this from the filename.
                pass
            return request_id
        raise RouterProtocolError("Desktop Gateway request exceeded safe retry generations")

    def _registered_router(self, router_thread_id: str, host_id: str) -> tuple[str, str]:
        candidate = router_thread_id.strip()
        registration = _read_json(self.registration_file) or {}
        registered = str(registration.get("router_thread_id") or "").strip()
        if not registered:
            raise RouterProtocolError("Desktop Gateway task is not registered")
        if candidate != registered:
            raise RouterProtocolError("wake came from a different Desktop Gateway task")
        registered_host = str(registration.get("host_id") or "").strip()
        supplied_host = host_id.strip()
        if registered_host and supplied_host and registered_host != supplied_host:
            raise RouterProtocolError("wake host does not match the registered Desktop host")
        return registered, supplied_host or registered_host

    def _activate_wake(
        self,
        wake_id: str,
        fence_token: str,
        router_thread_id: str,
        host_id: str,
    ) -> int:
        wake_id = self._validate_wake_id(wake_id)
        fence_token = self._validate_fence_token(fence_token)
        registered, resolved_host = self._registered_router(router_thread_id, host_id)
        now = time.time()
        try:
            with self._wake_session() as connection:
                connection.execute("BEGIN IMMEDIATE")
                state = connection.execute(
                    "SELECT * FROM wake_state WHERE singleton=1"
                ).fetchone()
                if (
                    state is None
                    or str(state["wake_id"] or "") != wake_id
                    or str(state["fence_token"] or "") != fence_token
                    or not self._wake_is_live(state, now)
                ):
                    raise RouterProtocolError("Desktop Gateway wake lease is stale or invalid")
                generation = int(state["generation"] or 0)
                connection.execute(
                    """
                    UPDATE wake_state
                    SET status='active', lease_until=?, updated_at=?
                    WHERE singleton=1
                    """,
                    (now + self.wake_lease_ttl_seconds, now),
                )
                connection.commit()
        except sqlite3.Error as exc:
            raise RouterProtocolError(f"Desktop Gateway wake database failed: {exc}") from exc
        self.heartbeat(registered, resolved_host)
        return generation

    def renew_wake(
        self,
        wake_id: str,
        fence_token: str,
        router_thread_id: str,
        host_id: str = "",
    ) -> None:
        self._activate_wake(wake_id, fence_token, router_thread_id, host_id)

    def release_wake(
        self,
        wake_id: str,
        fence_token: str,
        *,
        reason: str = "drained",
    ) -> dict[str, Any]:
        wake_id = self._validate_wake_id(wake_id)
        fence_token = self._validate_fence_token(fence_token)
        now = time.time()
        try:
            with self._wake_session() as connection:
                connection.execute("BEGIN IMMEDIATE")
                state = connection.execute(
                    "SELECT * FROM wake_state WHERE singleton=1"
                ).fetchone()
                if (
                    state is None
                    or str(state["wake_id"] or "") != wake_id
                    or str(state["fence_token"] or "") != fence_token
                ):
                    raise RouterProtocolError("stale Desktop Gateway cannot release this wake")
                generation = int(state["generation"] or 0)
                connection.execute(
                    """
                UPDATE wake_state
                SET wake_id='', fence_token='', lease_until=0, status='idle',
                    wake_origin='scheduler', authorized_request_id='',
                    authorized_operation='',
                    last_released_generation=MAX(last_released_generation, ?),
                        updated_at=?
                    WHERE singleton=1
                    """,
                    (generation, now),
                )
                connection.commit()
        except sqlite3.Error as exc:
            raise RouterProtocolError(f"Desktop Gateway wake database failed: {exc}") from exc
        return {
            "released": True,
            "reason": reason.strip()[:80] or "drained",
            "wake_generation": generation,
            "pending_count": len(self._actionable_pending_paths()),
        }

    def claim(
        self,
        router_thread_id: str,
        host_id: str = "",
        *,
        wake_id: str,
        fence_token: str,
        wait_seconds: int = 0,
        release_on_empty: bool = False,
    ) -> dict[str, Any] | None:
        self.expire_stale_claims()
        wake_generation = self._activate_wake(
            wake_id,
            fence_token,
            router_thread_id,
            host_id,
        )
        try:
            with self._wake_session() as connection:
                wake_state = connection.execute(
                    "SELECT * FROM wake_state WHERE singleton=1"
                ).fetchone()
        except sqlite3.Error as exc:
            raise RouterProtocolError(f"Desktop Gateway wake database failed: {exc}") from exc
        authorized_request_id = (
            str(wake_state["authorized_request_id"] or "") if wake_state is not None else ""
        )
        authorized_operation = (
            str(wake_state["authorized_operation"] or "") if wake_state is not None else ""
        )
        bounded_wait = max(0, min(wait_seconds, self.grace_wait_max_seconds))
        deadline = time.monotonic() + bounded_wait
        next_renewal = time.monotonic() + min(30, max(5, self.wake_lease_ttl_seconds // 2))
        while True:
            sortable_candidates: list[tuple[float, str, Path]] = []
            for path in self._actionable_pending_paths():
                try:
                    sortable_candidates.append((path.stat().st_mtime, path.name, path))
                except OSError:
                    continue
            candidates = [item[2] for item in sorted(sortable_candidates)]
            for source in candidates:
                if authorized_request_id and source.stem != authorized_request_id:
                    continue
                target = self.claimed_dir / source.name
                request = _read_json(source)
                if request is None:
                    if source.exists():
                        self.fail(
                            source.stem,
                            code="invalid_request",
                            message="Desktop Gateway request JSON is invalid",
                            retryable=False,
                            may_have_started=False,
                            enforce_fence=False,
                        )
                    continue
                if authorized_operation and str(request.get("operation") or "") != authorized_operation:
                    raise RouterProtocolError(
                        "manual Gateway cycle request no longer matches its authorized operation"
                    )
                claimed_request = {
                    **request,
                    "claimed_at": time.time(),
                    "router_thread_id": router_thread_id.strip(),
                    "router_host_id": host_id.strip(),
                    "wake_id": self._validate_wake_id(wake_id),
                    "wake_generation": wake_generation,
                    "wake_origin": (
                        str(wake_state["wake_origin"] or "scheduler")
                        if wake_state is not None
                        else "scheduler"
                    ),
                    "fence_token": self._validate_fence_token(fence_token),
                }
                # Publish the complete fenced claim with create-if-absent
                # semantics.  The immutable pending anchor remains in place,
                # so neither a second producer nor a second claimant can create
                # another executable copy of this deterministic request.
                if not _atomic_write_json_exclusive(target, claimed_request):
                    continue
                if self._terminal_result_exists(source.stem):
                    # A terminal failure may have won immediately before the
                    # claim CAS.  It is authoritative and no target may start.
                    continue
                return claimed_request
            if time.monotonic() >= deadline:
                if release_on_empty:
                    self.release_wake(wake_id, fence_token, reason="grace_timeout")
                return None
            if time.monotonic() >= next_renewal:
                self.renew_wake(
                    wake_id,
                    fence_token,
                    router_thread_id,
                    host_id,
                )
                next_renewal = time.monotonic() + min(
                    30, max(5, self.wake_lease_ttl_seconds // 2)
                )
            time.sleep(0.25)

    def _validate_claim_fence(
        self,
        request: dict[str, Any],
        fence_token: str,
        *,
        require_live: bool = True,
    ) -> None:
        claimed_fence = str(request.get("fence_token") or "").strip()
        if not claimed_fence:
            raise RouterProtocolError(
                "legacy Desktop Gateway claim has no fence and cannot be finalized"
            )
        supplied = self._validate_fence_token(fence_token)
        if supplied != claimed_fence:
            raise RouterProtocolError("stale Desktop Gateway cannot finalize this request")
        wake_id = self._validate_wake_id(str(request.get("wake_id") or ""))
        now = time.time()
        try:
            with self._wake_session() as connection:
                state = connection.execute(
                    "SELECT * FROM wake_state WHERE singleton=1"
                ).fetchone()
        except sqlite3.Error as exc:
            raise RouterProtocolError(f"Desktop Gateway wake database failed: {exc}") from exc
        if (
            state is None
            or str(state["wake_id"] or "") != wake_id
            or str(state["fence_token"] or "") != supplied
            or (require_live and not self._wake_is_live(state, now))
        ):
            raise RouterProtocolError("Desktop Gateway wake lease is no longer authoritative")

    def complete(
        self,
        request_id: str,
        result: dict[str, Any],
        *,
        fence_token: str = "",
    ) -> None:
        request_id = self._validate_request_id(request_id)
        existing_response = self.response(request_id)
        if existing_response is not None:
            if existing_response.get("status") == "completed":
                self._terminalize_final_return(request_id, "completed")
                return
            raise RouterProtocolError("Desktop Gateway request already has a failed response")
        request = _read_json(self._path(self.claimed_dir, request_id))
        if request is None:
            raise RouterProtocolError("Desktop Gateway request is not claimed")
        self._validate_claim_fence(request, fence_token)
        operation = str(request.get("operation") or "")
        if operation == "list_task_catalog":
            if result.get("catalog_version") != 1:
                raise RouterProtocolError(
                    "Desktop Gateway task catalog completion must use catalog version 1"
                )
        elif "catalog_version" in result:
            raise RouterProtocolError(
                "Desktop Gateway structured task catalog cannot complete another operation"
            )
        written = self._finalize_response(
            request_id,
            {
                "schema_version": ROUTER_SCHEMA_VERSION,
                "request_id": request_id,
                "operation": request.get("operation"),
                "fingerprint": request.get("fingerprint"),
                "status": "completed",
                "result": result,
                "completed_at": time.time(),
            },
        )
        if not written:
            existing_response = self.response(request_id)
            if existing_response is not None and existing_response.get("status") == "completed":
                self._terminalize_final_return(request_id, "completed")
                return
            raise RouterProtocolError(
                "Desktop Gateway terminal finalization is fenced without a published completion"
            )
        self._terminalize_final_return(request_id, "completed")

    def fail(
        self,
        request_id: str,
        *,
        code: str,
        message: str,
        retryable: bool,
        may_have_started: bool,
        fence_token: str = "",
        enforce_fence: bool = True,
    ) -> None:
        request_id = self._validate_request_id(request_id)
        existing_response = self.response(request_id)
        if existing_response is not None:
            terminal_state = (
                "completed"
                if existing_response.get("status") == "completed"
                else "failed"
            )
            self._terminalize_final_return(request_id, terminal_state)
            return
        request = _read_json(self._path(self.claimed_dir, request_id)) or _read_json(
            self._path(self.pending_dir, request_id)
        ) or {}
        if enforce_fence:
            self._validate_claim_fence(request, fence_token)
        operation = str(request.get("operation") or "")
        if operation in READ_ONLY_OPERATIONS and may_have_started:
            raise RouterProtocolError(
                "read-only Desktop Gateway operation cannot be finalized with "
                "may_have_started=true"
            )
        written = self._finalize_response(
            request_id,
            {
                "schema_version": ROUTER_SCHEMA_VERSION,
                "request_id": request_id,
                "operation": operation,
                "fingerprint": request.get("fingerprint"),
                "status": "failed",
                "error": {
                    "code": code.strip()[:80] or "router_error",
                    "message": message.strip()[:4000] or "Desktop Gateway request failed",
                    "retryable": bool(retryable),
                    "may_have_started": bool(may_have_started),
                },
                "completed_at": time.time(),
            },
        )
        if not written:
            existing_response = self.response(request_id)
            if existing_response is None:
                raise RouterProtocolError(
                    "Desktop Gateway terminal finalization is fenced without a published failure"
                )
            terminal_state = (
                "completed"
                if existing_response.get("status") == "completed"
                else "failed"
            )
            self._terminalize_final_return(request_id, terminal_state)
            return
        self._terminalize_final_return(request_id, "failed")

    def response(self, request_id: str) -> dict[str, Any] | None:
        request_id = self._validate_request_id(request_id)
        receipt = self._receipt_response(request_id)
        if receipt is not None:
            return receipt
        receipt_path = self._receipt_payload_path(request_id)
        if receipt_path.exists():
            # A corrupt authoritative tombstone is fail-closed. Never allow a
            # disposable cache to make its terminal state appear to flip.
            return None
        cached = _read_json(self._path(self.responses_dir, request_id))
        if cached is None:
            return None
        # Migrate the legacy empty-marker + response-cache layout to a durable
        # payload. The receipt remains authoritative on every later read.
        cache_path = self._path(self.responses_dir, request_id)
        try:
            cache_mtime = cache_path.stat().st_mtime
        except OSError:
            cache_mtime = None
        authoritative = self._migrate_cached_response(
            request_id,
            cached,
            source_mtime=cache_mtime,
        )
        return authoritative if authoritative is not None else cached

    def wait_for_response(
        self,
        request_id: str,
        timeout_seconds: int,
        *,
        poll_seconds: float = 0.25,
        on_claimed: Callable[[], None] | None = None,
    ) -> dict[str, Any] | None:
        deadline = time.monotonic() + max(1, timeout_seconds)
        claimed_notified = False
        while time.monotonic() < deadline:
            if not claimed_notified and self.was_claimed(request_id):
                claimed_notified = True
                if on_claimed is not None:
                    on_claimed()
            response = self.response(request_id)
            if response is not None:
                return response
            time.sleep(max(0.05, min(poll_seconds, 1.0)))
        if not claimed_notified and self.was_claimed(request_id) and on_claimed is not None:
            on_claimed()
        return self.response(request_id)

    def was_claimed(self, request_id: str) -> bool:
        return self._path(self.claimed_dir, request_id).exists()

    def expire_stale_claims(self, now: float | None = None) -> int:
        """Fail abandoned claims without ever replaying a possibly-started turn."""

        current_time = time.time() if now is None else now
        status = self.status(now=current_time)
        router_ready = status.ready and status.wake_inflight
        expired = 0
        for path in self.claimed_dir.glob("*.json"):
            request_id = path.stem
            if self.response(request_id) is not None:
                continue
            # Current claims are published fully fenced in one exclusive CAS.
            # A live wake also protects genuinely legacy or damaged unfenced
            # claim records while their authoritative owner may still be active.
            if router_ready:
                continue
            request = _read_json(path)
            if request is None:
                if self._has_terminal_fence(request_id):
                    self._recover_interrupted_finalization(request_id, None)
                else:
                    self.fail(
                        request_id,
                        code="invalid_claim",
                        message="Desktop Gateway claim JSON is invalid",
                        retryable=False,
                        may_have_started=True,
                        enforce_fence=False,
                    )
                expired += 1
                continue
            if not str(request.get("fence_token") or "").strip():
                if self._has_terminal_fence(request_id):
                    self._recover_interrupted_finalization(request_id, request)
                else:
                    operation = str(request.get("operation") or "").strip()
                    if operation in READ_ONLY_OPERATIONS:
                        self.fail(
                            request_id,
                            code="router_read_claim_expired",
                            message=(
                                "Pre-fence Desktop Gateway read-only claim lost its owner; "
                                "no target mutation was authorized, so a retry generation is safe"
                            ),
                            retryable=True,
                            may_have_started=False,
                            enforce_fence=False,
                        )
                    else:
                        self.fail(
                            request_id,
                            code="legacy_unfenced_claim",
                            message=(
                                "Pre-fence Desktop Gateway claim cannot be finalized or replayed; "
                                "the target action may have started"
                            ),
                            retryable=False,
                            may_have_started=True,
                            enforce_fence=False,
                        )
                expired += 1
                continue
            try:
                claimed_at = float(request.get("claimed_at", 0) or 0)
            except (TypeError, ValueError):
                claimed_at = 0
            operation = str(request.get("operation") or "").strip()
            expiry_ttl = (
                self.read_claim_ttl_seconds
                if operation in READ_ONLY_OPERATIONS
                else self.claim_ttl_seconds
            )
            if claimed_at and current_time - claimed_at <= expiry_ttl:
                continue
            if self._has_terminal_fence(request_id):
                self._recover_interrupted_finalization(request_id, request)
            else:
                if operation in READ_ONLY_OPERATIONS:
                    self.fail(
                        request_id,
                        code="router_read_claim_expired",
                        message=(
                            "Desktop Gateway stopped after claiming a read-only request; "
                            "no target mutation was authorized, so a retry generation is safe"
                        ),
                        retryable=True,
                        may_have_started=False,
                        enforce_fence=False,
                    )
                else:
                    self.fail(
                        request_id,
                        code="router_claim_expired",
                        message=(
                            "Desktop Gateway stopped after claiming this request; "
                            "the target action may have started, so it was not replayed"
                        ),
                        retryable=False,
                        may_have_started=True,
                        enforce_fence=False,
                    )
            expired += 1
        return expired

    def cleanup(self, now: float | None = None) -> None:
        self.expire_stale_claims(now=now)
        cutoff = (time.time() if now is None else now) - self.retention_seconds
        # Released protocol-v4 runtimes used an empty .final fence plus a
        # response cache. Migrate that real legacy layout before cache cleanup;
        # no unpublished development-only receipt formats are carried forward.
        for marker in self.receipts_dir.glob("*.final"):
            request_id = marker.stem
            if REQUEST_ID_PATTERN.fullmatch(request_id) is None:
                continue
            receipt_path = self._receipt_payload_path(request_id)
            if receipt_path.exists():
                continue
            response_path = self._path(self.responses_dir, request_id)
            cached = _read_json(response_path)
            if self._valid_terminal_receipt(request_id, cached):
                try:
                    response_mtime = response_path.stat().st_mtime
                except OSError:
                    response_mtime = None
                self._migrate_cached_response(
                    request_id,
                    cached,
                    source_mtime=response_mtime,
                )
        for path in self.receipts_dir.glob("*.json"):
            if REQUEST_ID_PATTERN.fullmatch(path.stem) is None:
                continue
            try:
                if path.stat().st_mtime >= cutoff:
                    continue
                receipt = _read_json(path)
                if receipt is None:
                    continue
                compacted = self._compacted_terminal_receipt(receipt)
                if compacted != receipt:
                    _atomic_write_json(path, compacted)
            except OSError:
                continue
        for directory in (self.receipts_dir, self.pending_dir, self.claimed_dir):
            for path in directory.iterdir():
                if not path.is_file() or re.fullmatch(
                    r"\.[a-f0-9]{32}\.json\.[0-9]+\.[0-9]+\.tmp",
                    path.name,
                ) is None:
                    continue
                try:
                    if path.stat().st_mtime < cutoff:
                        path.unlink()
                except OSError:
                    continue
        for directory in (self.responses_dir, self.staging_dir):
            for path in directory.iterdir():
                if not path.is_file():
                    continue
                try:
                    if path.stat().st_mtime < cutoff:
                        path.unlink()
                except OSError:
                    continue
        for directory in (self.claimed_dir, self.pending_dir):
            for path in directory.glob("*.json"):
                try:
                    if (
                        path.stat().st_mtime < cutoff
                        and self._terminal_result_exists(path.stem)
                    ):
                        path.unlink()
                except (OSError, RouterProtocolError):
                    continue
        # Terminal receipts become compact idempotency tombstones instead of
        # being deleted. Retry generations therefore never lose their ancestry
        # or recreate generation zero after downtime, while expired answer text
        # does not remain in the permanent receipt set.
        try:
            with self._wake_session() as connection:
                connection.execute(
                    "DELETE FROM manual_cycle_tickets WHERE expires_at < ?",
                    ((time.time() if now is None else now),),
                )
                connection.execute(
                    """
                    DELETE FROM final_return_receipts
                    WHERE updated_at < ?
                      AND state IN ('completed','failed','conflict','expired')
                    """,
                    (cutoff,),
                )
                rows = connection.execute(
                    "SELECT request_id FROM wake_requests WHERE created_at < ?",
                    (cutoff,),
                ).fetchall()
                for row in rows:
                    request_id = str(row["request_id"] or "")
                    if any(
                        self._path(directory, request_id).exists()
                        for directory in (
                            self.pending_dir,
                            self.claimed_dir,
                            self.responses_dir,
                        )
                    ):
                        continue
                    connection.execute(
                        "DELETE FROM wake_requests WHERE request_id=?",
                        (request_id,),
                    )
        except (sqlite3.Error, RouterProtocolError):
            pass
