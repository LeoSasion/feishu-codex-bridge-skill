"""Durable Page protocol between the Feishu Bridge and one Desktop Beeper.

The Bridge never opens a Responder directly. It writes one idempotent request;
the Beeper claims one Page and alerts the selected Responder through the app's
task-coordination surface. The Responder returns its exact answer through the
fenced Final Callback.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Callable
import uuid

from .legacy_identifiers import RETIRED_BEEPER_HOST_FIELD, RETIRED_QUEUE_ROOT_NAME

BEEPER_QUEUE_SCHEMA_VERSION = 2
THREAD_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,255}")
EXACT_SESSION_UUID_PATTERN = re.compile(
    r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}"
)
REQUEST_ID_PATTERN = re.compile(r"[a-f0-9]{32}")
DIAL_ID_PATTERN = re.compile(r"[a-f0-9]{32}")
FENCE_TOKEN_PATTERN = re.compile(r"[a-f0-9]{32}")
PAGE_PATTERN = re.compile(r"[a-f0-9]{32}")
FINAL_CALLBACK_CAPABILITY_PATTERN = re.compile(r"[a-f0-9]{32}")
SHA256_PATTERN = re.compile(r"[a-f0-9]{64}")
QUEUE_ROOT_NAME = "beeper"
ALLOWED_QUEUE_ROOT_NAMES = frozenset(
    {RETIRED_QUEUE_ROOT_NAME, QUEUE_ROOT_NAME}
)
WAIT_MAX_SECONDS = 3600
BEEPER_CLAIM_WAIT_MAX_SECONDS = 30
UNCLAIMED_FAILURE_CODES = frozenset(
    {"beeper_load_assist_failed", "beeper_claim_timeout"}
)
READ_OPERATIONS = frozenset(
    {"list_task_catalog", "inspect_thread"}
)
DIAL_OPERATIONS = frozenset(
    {*READ_OPERATIONS, "send_message_to_thread"}
)
SEND_CLAIM_STATES = frozenset(
    {"claiming", "claimed_armed", "finishing"}
)
READ_CLAIM_STATES = frozenset(
    {"claiming_readonly", "claimed_readonly", "completing_readonly"}
)
CLAIM_STATES = frozenset(
    {*SEND_CLAIM_STATES, *READ_CLAIM_STATES}
)
TASK_CATALOG_LIMIT = 50
EXACT_VISIBILITY_LIMIT = 20
THREAD_TOMBSTONE_LIMIT = 200
CATALOG_SNAPSHOT_TTL_SECONDS = 600
FINAL_CALLBACK_MAX_CHARS = 12_000
FINAL_CALLBACK_ARM_TTL_SECONDS = 600
FINAL_CALLBACK_SOURCES = frozenset(
    {"final_callback", "hook", "native", "unknown", "not_applicable"}
)
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
# The Beeper contract implements `inspect_thread` with Desktop read-only task
# inspection only.  If its fenced claim is abandoned, no responder mutation can
# have started and the deterministic request may safely advance a generation.
# Every other operation remains an uncertainty boundary after claim.
READ_ONLY_OPERATIONS = frozenset({"inspect_thread", "list_task_catalog"})


class BeeperQueueProtocolError(RuntimeError):
    """The local Desktop Beeper queue contains invalid or conflicting data."""


@dataclass(frozen=True)
class BeeperQueueStatus:
    registered: bool = False
    beeper_thread_id: str = ""
    beeper_host_id: str = ""
    pending: int = 0
    claimed: int = 0
    dial_generation: int = 0
    dial_inflight: bool = False
    dial_lease_remaining_seconds: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "registered": self.registered,
            "beeper_thread_id": self.beeper_thread_id,
            "beeper_host_id": self.beeper_host_id,
            "pending": self.pending,
            "claimed": self.claimed,
            "dial_generation": self.dial_generation,
            "dial_inflight": self.dial_inflight,
            "dial_lease_remaining_seconds": self.dial_lease_remaining_seconds,
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


def _atomic_write_final_answer(path: Path, final_answer: str) -> None:
    """Atomically publish one exact UTF-8 Final Callback answer."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp"
    )
    try:
        with temporary.open("x", encoding="utf-8", newline="") as stream:
            stream.write(final_answer)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass


def _read_exact_final_answer(path: Path) -> str:
    """Read a Final Callback answer without newline normalization."""

    with path.open("r", encoding="utf-8", newline="") as stream:
        return stream.read()


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


class BeeperQueue:
    """Atomic, bounded queue consumed only by the single Desktop Beeper task."""

    def __init__(
        self,
        runtime_dir: Path,
        *,
        claim_ttl_seconds: int = 1800,
        read_claim_ttl_seconds: int = 300,
        retention_hours: int = 168,
        dial_lease_ttl_seconds: int = 180,
        grace_wait_max_seconds: int = 30,
        root_name: str = RETIRED_QUEUE_ROOT_NAME,
    ) -> None:
        if root_name not in ALLOWED_QUEUE_ROOT_NAMES:
            raise BeeperQueueProtocolError("unsupported Desktop Beeper queue namespace")
        self.root_name = root_name
        self.root = runtime_dir / root_name
        self.pending_dir = self.root / "pending"
        self.claimed_dir = self.root / "claimed"
        self.responses_dir = self.root / "responses"
        self.staging_dir = self.root / "staging"
        self.catalog_staging_dir = self.root / "catalog-staging"
        self.receipts_dir = self.root / "receipts"
        self.registration_file = self.root / "registration.json"
        self.thread_tombstones_file = (
            self.root / "thread-tombstones.json"
        )
        self.dial_db = self.root / "dial.sqlite3"
        self.claim_ttl_seconds = max(60, claim_ttl_seconds)
        # `inspect_thread` cannot mutate a Desktop task, so it does not need the
        # long uncertainty window reserved for possibly-started responder work.
        # Cap its abandonment window at the general claim TTL so direct callers
        # with a deliberately shorter test/runtime TTL keep their stricter bound.
        self.read_claim_ttl_seconds = min(
            self.claim_ttl_seconds,
            max(60, read_claim_ttl_seconds),
        )
        self.retention_seconds = max(3600, retention_hours * 3600)
        self.dial_lease_ttl_seconds = max(60, dial_lease_ttl_seconds)
        self.grace_wait_max_seconds = max(0, min(grace_wait_max_seconds, 60))
        for directory in (
            self.pending_dir,
            self.claimed_dir,
            self.responses_dir,
            self.staging_dir,
            self.catalog_staging_dir,
            self.receipts_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self._initialize_dial_db()
        catalog_consume_cutoff = (
            time.time() - CATALOG_SNAPSHOT_TTL_SECONDS
        )
        for interrupted in self.catalog_staging_dir.glob("*.consuming"):
            try:
                # Queue helpers construct this object independently and may
                # overlap the Bridge's private consume.  A fresh rename can
                # therefore have a live owner; only aged crash debris is safe
                # to scrub during construction.
                if interrupted.stat().st_mtime < catalog_consume_cutoff:
                    interrupted.unlink()
            except OSError:
                pass
        # A terminal queue receipt is published before the auxiliary return row
        # is scrubbed. Reconcile that intentionally small crash window at
        # startup so a captured/completing (or legacy native) row cannot retain
        # digests or block the responder forever after the authoritative terminal
        # outcome exists.
        self._reconcile_terminal_final_callbacks()
        if self.root_name == QUEUE_ROOT_NAME:
            self._reconcile_unclaimed_failures()

    @staticmethod
    def _validate_request_id(request_id: str) -> str:
        candidate = request_id.strip().lower()
        if not REQUEST_ID_PATTERN.fullmatch(candidate):
            raise BeeperQueueProtocolError("invalid Desktop Beeper request id")
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
        final_callback_source = (
            payload.get("final_callback_source") if payload is not None else None
        )
        return bool(
            payload is not None
            and payload.get("request_id") == request_id
            and payload.get("status") in {"completed", "failed"}
            and (
                final_callback_source is None
                or final_callback_source in FINAL_CALLBACK_SOURCES
            )
        )

    @staticmethod
    def _compacted_terminal_receipt(payload: dict[str, Any]) -> dict[str, Any]:
        """Drop expired answer text while preserving idempotency semantics."""

        if payload.get("compacted_at") is not None:
            return payload
        base = {
            "schema_version": payload.get("schema_version", BEEPER_QUEUE_SCHEMA_VERSION),
            "request_id": payload.get("request_id"),
            "operation": payload.get("operation"),
            "fingerprint": payload.get("fingerprint"),
            "completed_at": payload.get("completed_at"),
            "compacted_at": time.time(),
        }
        final_callback_source = payload.get("final_callback_source")
        if final_callback_source in FINAL_CALLBACK_SOURCES:
            base["final_callback_source"] = final_callback_source
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
                code = "responder_result_unknown"
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
        # a Responder mutation merely because its final answer aged out.
        return {
            **base,
            "status": "failed",
            "error": {
                "code": "responder_result_unknown",
                "message": (
                    "The completed responder result exceeded the response-retention "
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
        authoritative = self._receipt_response(request_id)
        if authoritative is not None:
            self._terminalize_final_callback(
                request_id,
                "completed" if authoritative.get("status") == "completed" else "failed",
            )
        return authoritative

    def _recover_interrupted_finalization(
        self,
        request_id: str,
        request: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Turn a stale orphan fence into a permanent unknown-outcome result."""

        request_id = self._validate_request_id(request_id)
        existing = self._receipt_response(request_id)
        if existing is not None:
            self._terminalize_final_callback(
                request_id,
                "completed" if existing.get("status") == "completed" else "failed",
            )
            return existing
        details = request or {}
        operation = str(details.get("operation") or "")
        readonly = operation in READ_ONLY_OPERATIONS
        recovered = {
            "schema_version": BEEPER_QUEUE_SCHEMA_VERSION,
            "request_id": request_id,
            "operation": details.get("operation"),
            "fingerprint": details.get("fingerprint"),
            "status": "failed",
            "final_callback_source": self._final_callback_resolution_source(
                request_id,
                operation,
            ),
            "error": {
                "code": (
                    "readonly_result_unknown" if readonly else "responder_result_unknown"
                ),
                "message": (
                    "Desktop Beeper read-only finalization was interrupted after its "
                    "terminal fence was created; no mutation was admitted and the read "
                    "was not replayed"
                    if readonly
                    else "Desktop Beeper finalization was interrupted after its terminal "
                    "fence was created; the responder outcome is unknown and was not replayed"
                ),
                "retryable": False,
                "may_have_started": not readonly,
            },
            "completed_at": time.time(),
        }
        receipt_path = self._receipt_payload_path(request_id)
        if not _atomic_write_json_exclusive(receipt_path, recovered):
            existing = self._receipt_response(request_id)
            if existing is not None:
                self._terminalize_final_callback(
                    request_id,
                    "completed" if existing.get("status") == "completed" else "failed",
                )
                return existing
            raise BeeperQueueProtocolError(
                "Desktop Beeper terminal receipt exists but is unreadable"
            )
        try:
            _atomic_write_json(self._path(self.responses_dir, request_id), recovered)
        except OSError:
            pass
        self._terminalize_final_callback(request_id, "failed")
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

    def _dial_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.dial_db), timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    @contextmanager
    def _dial_session(self) -> Iterator[sqlite3.Connection]:
        """Run one transaction and release its database handle deterministically."""

        connection = self._dial_connection()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize_dial_db(self) -> None:
        with self._dial_session() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS dial_requests (
                    generation INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL UNIQUE,
                    operation TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS dial_state (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    dial_id TEXT NOT NULL DEFAULT '',
                    generation INTEGER NOT NULL DEFAULT 0,
                    fence_token TEXT NOT NULL DEFAULT '',
                    lease_until REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'idle',
                    last_released_generation INTEGER NOT NULL DEFAULT 0,
                    dial_origin TEXT NOT NULL DEFAULT '',
                    authorized_request_id TEXT NOT NULL DEFAULT '',
                    authorized_operation TEXT NOT NULL DEFAULT '',
                    updated_at REAL NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS final_callback_receipts (
                    request_id TEXT PRIMARY KEY,
                    fence_token TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    beeper_thread_id TEXT NOT NULL DEFAULT '',
                    prompt_sha256 TEXT NOT NULL,
                    transport_mode TEXT NOT NULL DEFAULT 'hook',
                    final_callback_capability_sha256 TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL,
                    session_id TEXT NOT NULL DEFAULT '',
                    turn_id TEXT NOT NULL DEFAULT '',
                    prompt_hook_seen INTEGER NOT NULL DEFAULT 0,
                    prompt_hook_turn_id TEXT NOT NULL DEFAULT '',
                    prompt_match_mode TEXT NOT NULL DEFAULT '',
                    prompt_hook_rejection TEXT NOT NULL DEFAULT '',
                    answer_sha256 TEXT NOT NULL DEFAULT '',
                    answer_chars INTEGER NOT NULL DEFAULT 0,
                    resolution_source TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS final_callback_thread_state
                    ON final_callback_receipts(thread_id, state);
                INSERT OR IGNORE INTO dial_state(singleton) VALUES(1);
                """
            )
            columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(dial_state)").fetchall()
            }
            if "dial_origin" not in columns:
                connection.execute(
                    "ALTER TABLE dial_state ADD COLUMN dial_origin TEXT NOT NULL DEFAULT ''"
                )
            if "authorized_request_id" not in columns:
                connection.execute(
                    "ALTER TABLE dial_state ADD COLUMN authorized_request_id TEXT NOT NULL DEFAULT ''"
                )
            if "authorized_operation" not in columns:
                connection.execute(
                    "ALTER TABLE dial_state ADD COLUMN authorized_operation TEXT NOT NULL DEFAULT ''"
                )
            dial_request_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(dial_requests)").fetchall()
            }
            if "operation" not in dial_request_columns:
                connection.execute(
                    "ALTER TABLE dial_requests ADD COLUMN operation TEXT NOT NULL DEFAULT ''"
                )
            final_callback_columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(final_callback_receipts)"
                ).fetchall()
            }
            final_callback_migrations = {
                "beeper_thread_id": (
                    "ALTER TABLE final_callback_receipts "
                    "ADD COLUMN beeper_thread_id TEXT NOT NULL DEFAULT ''"
                ),
                "prompt_hook_seen": (
                    "ALTER TABLE final_callback_receipts "
                    "ADD COLUMN prompt_hook_seen INTEGER NOT NULL DEFAULT 0"
                ),
                "prompt_hook_turn_id": (
                    "ALTER TABLE final_callback_receipts "
                    "ADD COLUMN prompt_hook_turn_id TEXT NOT NULL DEFAULT ''"
                ),
                "prompt_match_mode": (
                    "ALTER TABLE final_callback_receipts "
                    "ADD COLUMN prompt_match_mode TEXT NOT NULL DEFAULT ''"
                ),
                "prompt_hook_rejection": (
                    "ALTER TABLE final_callback_receipts "
                    "ADD COLUMN prompt_hook_rejection TEXT NOT NULL DEFAULT ''"
                ),
                "resolution_source": (
                    "ALTER TABLE final_callback_receipts "
                    "ADD COLUMN resolution_source TEXT NOT NULL DEFAULT ''"
                ),
                "final_callback_capability_sha256": (
                    "ALTER TABLE final_callback_receipts "
                    "ADD COLUMN final_callback_capability_sha256 TEXT NOT NULL DEFAULT ''"
                ),
                "transport_mode": (
                    "ALTER TABLE final_callback_receipts "
                    "ADD COLUMN transport_mode TEXT NOT NULL DEFAULT 'hook'"
                ),
            }
            for column, statement in final_callback_migrations.items():
                if column not in final_callback_columns:
                    connection.execute(statement)
            if self.root_name == QUEUE_ROOT_NAME:
                # This page namespace is deliberately absent from the
                # historical/default queue.  Its UNIQUE request id is the
                # permanent, non-resettable grant consumed before the one CLI
                # dial trigger is attempted.
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS pages (
                        page_id TEXT PRIMARY KEY,
                        request_id TEXT NOT NULL UNIQUE,
                        dial_id TEXT NOT NULL UNIQUE,
                        fence_token TEXT NOT NULL,
                        dial_generation INTEGER NOT NULL,
                        operation TEXT NOT NULL DEFAULT 'send_message_to_thread',
                        responder_thread_id TEXT NOT NULL DEFAULT '',
                        responder_host_id TEXT NOT NULL DEFAULT '',
                        snapshot_id TEXT NOT NULL DEFAULT '',
                        operation_receipt TEXT NOT NULL DEFAULT '',
                        state TEXT NOT NULL,
                        created_at REAL NOT NULL,
                        claimed_at REAL NOT NULL DEFAULT 0,
                        terminal_at REAL NOT NULL DEFAULT 0
                    );
                    CREATE INDEX IF NOT EXISTS page_state
                        ON pages(state, created_at);
                    """
                )
                columns = {
                    str(row[1])
                    for row in connection.execute(
                        "PRAGMA table_info(pages)"
                    ).fetchall()
                }
                if "responder_thread_id" not in columns:
                    connection.execute(
                        "ALTER TABLE pages "
                        "ADD COLUMN responder_thread_id TEXT NOT NULL DEFAULT ''"
                    )
                if "responder_host_id" not in columns:
                    connection.execute(
                        "ALTER TABLE pages "
                        "ADD COLUMN responder_host_id TEXT NOT NULL DEFAULT ''"
                    )
                if "operation" not in columns:
                    connection.execute(
                        "ALTER TABLE pages "
                        "ADD COLUMN operation TEXT NOT NULL "
                        "DEFAULT 'send_message_to_thread'"
                    )
                if "snapshot_id" not in columns:
                    connection.execute(
                        "ALTER TABLE pages "
                        "ADD COLUMN snapshot_id TEXT NOT NULL DEFAULT ''"
                    )
                if "operation_receipt" not in columns:
                    connection.execute(
                        "ALTER TABLE pages "
                        "ADD COLUMN operation_receipt TEXT NOT NULL DEFAULT ''"
                    )
                connection.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS snapshot_id
                    ON pages(snapshot_id)
                    WHERE snapshot_id<>''
                    """
                )
                connection.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS operation_receipt
                    ON pages(operation_receipt)
                    WHERE operation_receipt<>''
                    """
                )
            # Active claims use bounded digests and a character count to verify
            # the fenced staging file.  They have no purpose after the request
            # reaches a terminal state, and short final answers can make a
            # retained reusable digest unnecessarily revealing.  Keep only
            # answer-free provenance once terminal.
            connection.execute(
                """
                UPDATE final_callback_receipts
                SET prompt_sha256='', final_callback_capability_sha256='',
                    answer_sha256='', answer_chars=0
                WHERE state IN ('completed','failed','conflict','expired')
                """
            )

    @staticmethod
    def _validate_dial_id(dial_id: str) -> str:
        candidate = dial_id.strip().lower()
        if not DIAL_ID_PATTERN.fullmatch(candidate):
            raise BeeperQueueProtocolError("invalid Desktop Beeper dial id")
        return candidate

    @staticmethod
    def _validate_fence_token(fence_token: str) -> str:
        candidate = fence_token.strip().lower()
        if not FENCE_TOKEN_PATTERN.fullmatch(candidate):
            raise BeeperQueueProtocolError("invalid Desktop Beeper fence token")
        return candidate

    @staticmethod
    def _validate_page(page_id: str) -> str:
        candidate = page_id.strip().lower()
        if not PAGE_PATTERN.fullmatch(candidate):
            raise BeeperQueueProtocolError("invalid Beeper page")
        return candidate

    def _require_root(self) -> None:
        if self.root_name != QUEUE_ROOT_NAME:
            raise BeeperQueueProtocolError(
                "Beeper lifecycle requires its isolated namespace"
            )

    @staticmethod
    def _dial_is_live(row: sqlite3.Row, now: float) -> bool:
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
            raise BeeperQueueProtocolError("invalid Desktop Beeper dial operation")
        with self._dial_session() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO dial_requests(
                    request_id, operation, created_at
                ) VALUES(?, ?, ?)
                """,
                (self._validate_request_id(request_id), normalized_operation, created_at),
            )
            if normalized_operation:
                connection.execute(
                    """
                    UPDATE dial_requests
                    SET operation=?
                    WHERE request_id=? AND operation=''
                    """,
                    (normalized_operation, request_id),
                )
            row = connection.execute(
                "SELECT generation, operation FROM dial_requests WHERE request_id=?",
                (request_id,),
            ).fetchone()
        if row is None:
            raise BeeperQueueProtocolError("could not record Desktop Beeper dial generation")
        recorded_operation = str(row["operation"] or "")
        if normalized_operation and recorded_operation != normalized_operation:
            raise BeeperQueueProtocolError(
                "Desktop Beeper dial metadata conflicts with request operation"
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
            except (OSError, BeeperQueueProtocolError):
                continue
            connection.execute(
                """
                INSERT OR IGNORE INTO dial_requests(
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
            except (OSError, BeeperQueueProtocolError):
                continue
            actionable.append(path)
        return actionable

    def _dial_snapshot(
        self,
        now: float,
    ) -> tuple[int, bool, float | None]:
        try:
            with self._dial_session() as connection:
                generation_row = connection.execute(
                    "SELECT COALESCE(MAX(generation), 0) AS generation FROM dial_requests"
                ).fetchone()
                state = connection.execute(
                    "SELECT * FROM dial_state WHERE singleton=1"
                ).fetchone()
        except sqlite3.Error:
            return 0, False, None
        generation = int(generation_row["generation"] if generation_row else 0)
        if state is None or not self._dial_is_live(state, now):
            return generation, False, None
        remaining = max(0.0, float(state["lease_until"] or 0) - now)
        return generation, True, remaining

    def stage_path(self, request_id: str, fence_token: str = "") -> Path:
        request_id = self._validate_request_id(request_id)
        request = _read_json(self._path(self.claimed_dir, request_id))
        if request is None:
            raise BeeperQueueProtocolError("Desktop Beeper request is not a valid claim")
        supplied = self._validate_fence_token(fence_token)
        self._validate_claim_fence(
            request,
            supplied,
            expected_request_id=request_id,
        )
        return self.staging_dir / f"{request_id}.{supplied[:16]}.txt"

    @staticmethod
    def _validate_turn_id(turn_id: str) -> str:
        candidate = turn_id.strip()
        if not THREAD_ID_PATTERN.fullmatch(candidate):
            raise BeeperQueueProtocolError("invalid Codex responder turn id")
        return candidate

    @staticmethod
    def _prompt_sha256(prompt: str) -> str:
        if not isinstance(prompt, str) or not prompt:
            raise BeeperQueueProtocolError("final-callback prompt must be a non-empty string")
        try:
            encoded = prompt.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise BeeperQueueProtocolError(
                "final-callback prompt is not strict Unicode text"
            ) from exc
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def _match_final_callback_prompt(
        cls,
        prompt: str,
        expected_prompt_sha256: str,
        expected_beeper_thread_id: str,
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
        if source_thread_id != expected_beeper_thread_id:
            return "", "beeper_mismatch"
        delegated_input = match.group("input")
        if cls._prompt_sha256(delegated_input) != expected_prompt_sha256:
            return "", "prompt_mismatch"
        return "delegated", ""

    @staticmethod
    def _bounded_final_answer(final_answer: str) -> str:
        if not isinstance(final_answer, str) or not final_answer.strip():
            raise BeeperQueueProtocolError("final-callback answer must be non-empty text")
        try:
            final_answer.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise BeeperQueueProtocolError(
                "final-callback answer is not strict Unicode text"
            ) from exc
        if len(final_answer) <= FINAL_CALLBACK_MAX_CHARS:
            return final_answer
        raise BeeperQueueProtocolError(
            "final-callback answer exceeds the exact bounded transport limit"
        )

    @classmethod
    def _read_staged_final_answer(cls, path: Path) -> str:
        """Read one exact UTF-8 stage without allowing an oversized tamper."""

        try:
            if path.stat().st_size > FINAL_CALLBACK_MAX_CHARS * 4:
                raise BeeperQueueProtocolError(
                    "captured responder staging file exceeds its exact bound"
                )
            final_answer = _read_exact_final_answer(path)
        except (OSError, UnicodeError) as exc:
            raise BeeperQueueProtocolError(
                "captured responder staging file is unavailable"
            ) from exc
        return cls._bounded_final_answer(final_answer)

    @staticmethod
    def _validate_final_callback_capability(final_callback_capability: str) -> str:
        candidate = str(final_callback_capability or "").strip().lower()
        if FINAL_CALLBACK_CAPABILITY_PATTERN.fullmatch(candidate) is None:
            raise BeeperQueueProtocolError("Final Callback capability is invalid")
        return candidate

    @classmethod
    def _final_callback_prompt(cls, user_prompt: str, final_callback_capability: str) -> str:
        """Wrap one opaque business prompt with the Responder-owned Final Callback contract."""

        if not isinstance(user_prompt, str) or not user_prompt:
            raise BeeperQueueProtocolError("Final Callback prompt must carry user text")
        capability = cls._validate_final_callback_capability(final_callback_capability)
        envelope = json.dumps(
            {
                "protocol": "feishu-final-callback-v1",
                "final_callback_capability": capability,
                "user_request": user_prompt,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return (
            "Feishu Bridge responder delivery. Execute only the user_request in the "
            "JSON envelope below as this Desktop task, using this task's normal "
            "context, tools, Skills, approvals, and files. Complete every needed "
            "tool call before submitting. Once the final response is fully fixed, call "
            "mcp__feishu_final_callback__submit_final_callback exactly once with the "
            "envelope final_callback_capability and final_answer set to the exact Unicode final "
            "response you will show. After the tool accepts it, call no more tools "
            "and emit exactly the same final_answer without changing it. Do not ask "
            "the Beeper to answer, do not use "
            "read_thread or another task as a final source, and do not repeat the "
            "business operation if submission is uncertain.\n"
            f"Envelope: {envelope}"
        )

    def _claimed_final_callback_request(
        self,
        request_id: str,
        fence_token: str,
    ) -> tuple[str, str, dict[str, Any]]:
        request_id = self._validate_request_id(request_id)
        supplied_fence = self._validate_fence_token(fence_token)
        request = _read_json(self._path(self.claimed_dir, request_id))
        if request is None:
            raise BeeperQueueProtocolError("final-callback request is not a valid claim")
        self._validate_claim_fence(
            request,
            supplied_fence,
            expected_request_id=request_id,
        )
        if str(request.get("operation") or "") != "send_message_to_thread":
            raise BeeperQueueProtocolError("final-callback receipts support ordinary message sends only")
        payload = request.get("payload")
        if not isinstance(payload, dict):
            raise BeeperQueueProtocolError("final-callback request payload is invalid")
        self._reject_unsupported_send_mode("send_message_to_thread", payload)
        return request_id, supplied_fence, request

    def arm_final_callback(
        self,
        request_id: str,
        fence_token: str,
        thread_id: str,
        final_callback_capability_sha256: str = "",
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Arm one exact claimed prompt before the Beeper submits it once."""

        capability_sha256 = str(final_callback_capability_sha256 or "").strip().lower()
        if not capability_sha256:
            raise BeeperQueueProtocolError(
                "retired Hook final transport cannot arm a current request"
            )
        if SHA256_PATTERN.fullmatch(capability_sha256) is None:
            raise BeeperQueueProtocolError("Final Callback capability hash is invalid")
        request_id, supplied_fence, request = self._claimed_final_callback_request(
            request_id,
            fence_token,
        )
        responder_thread_id = thread_id.strip()
        if not looks_like_thread_id(responder_thread_id):
            raise BeeperQueueProtocolError("invalid final-callback responder task id")
        payload = request.get("payload")
        assert isinstance(payload, dict)
        if str(payload.get("responder_thread_id") or "").strip() != responder_thread_id:
            raise BeeperQueueProtocolError("final-callback responder does not match the claimed request")
        transport_mode = "final_callback"
        prompt_sha256 = self._prompt_sha256(payload.get("prompt"))
        registration = _read_json(self.registration_file) or {}
        beeper_thread_id = str(registration.get("beeper_thread_id") or "").strip()
        if not looks_like_thread_id(beeper_thread_id):
            raise BeeperQueueProtocolError(
                "final-callback arm requires one valid registered Desktop Beeper task"
            )
        current_time = time.time() if now is None else now
        expires_at = current_time + FINAL_CALLBACK_ARM_TTL_SECONDS
        try:
            with self._dial_session() as connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT * FROM final_callback_receipts WHERE request_id=?",
                    (request_id,),
                ).fetchone()
                if existing is not None:
                    if (
                        str(existing["fence_token"] or "") != supplied_fence
                        or str(existing["thread_id"] or "") != responder_thread_id
                        or str(existing["beeper_thread_id"] or "")
                        != beeper_thread_id
                        or str(existing["prompt_sha256"] or "") != prompt_sha256
                        or str(existing["final_callback_capability_sha256"] or "")
                        != capability_sha256
                        or str(existing["transport_mode"] or "") != transport_mode
                    ):
                        raise BeeperQueueProtocolError(
                            "final-callback request was re-armed with conflicting identity"
                        )
                    return {
                        "armed": True,
                        "state": str(existing["state"] or ""),
                        "expires_at": float(existing["expires_at"] or 0),
                    }
                connection.execute(
                    """
                    UPDATE final_callback_receipts
                    SET state='expired', prompt_sha256='',
                        final_callback_capability_sha256='', answer_sha256='',
                        answer_chars=0, updated_at=?
                    WHERE thread_id=? AND state='armed' AND expires_at < ?
                    """,
                    (current_time, responder_thread_id, current_time),
                )
                conflict = connection.execute(
                    """
                    SELECT request_id FROM final_callback_receipts
                    WHERE thread_id=?
                      AND state IN ('armed','bound','captured','completing','native')
                    LIMIT 1
                    """,
                    (responder_thread_id,),
                ).fetchone()
                if conflict is not None:
                    raise BeeperQueueProtocolError(
                        "another final-callback request already owns this responder task"
                    )
                connection.execute(
                    """
                    INSERT INTO final_callback_receipts(
                        request_id, fence_token, thread_id, beeper_thread_id,
                        prompt_sha256, transport_mode,
                        final_callback_capability_sha256, state,
                        created_at, updated_at, expires_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, 'armed', ?, ?, ?)
                    """,
                    (
                        request_id,
                        supplied_fence,
                        responder_thread_id,
                        beeper_thread_id,
                        prompt_sha256,
                        transport_mode,
                        capability_sha256,
                        current_time,
                        current_time,
                        expires_at,
                    ),
                )
        except sqlite3.Error as exc:
            raise BeeperQueueProtocolError(f"final-callback database failed: {exc}") from exc
        return {"armed": True, "state": "armed", "expires_at": expires_at}

    def bind_final_callback_prompt(
        self,
        session_id: str,
        turn_id: str,
        prompt: str,
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Reject the retired UserPromptSubmit final transport without state access."""

        del session_id, turn_id, prompt, now
        return {"accepted": False, "state": "ignored"}

    def capture_final_callback(
        self,
        session_id: str,
        turn_id: str,
        answer: str | None,
        *,
        stop_hook_active: bool = False,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Reject the retired Stop-final transport without state access."""

        del session_id, turn_id, answer, stop_hook_active, now
        return {"accepted": False, "state": "ignored"}

    def submit_final_callback(
        self,
        final_callback_capability: str,
        answer: str,
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Capture one responder-authored final through its single-use MCP capability."""

        self._require_root()
        registration = self.registration()
        if registration["valid"] is not True:
            raise BeeperQueueProtocolError(
                "Final Callback registration is no longer authoritative"
            )
        capability = self._validate_final_callback_capability(final_callback_capability)
        capability_sha256 = hashlib.sha256(capability.encode("ascii")).hexdigest()
        bounded_answer = self._bounded_final_answer(answer)
        answer_sha256 = hashlib.sha256(bounded_answer.encode("utf-8")).hexdigest()
        current_time = time.time() if now is None else now
        stage_path: Path | None = None
        try:
            with self._dial_session() as connection:
                connection.execute("BEGIN IMMEDIATE")
                rows = connection.execute(
                    """
                    SELECT r.*, t.page_id, t.dial_id, t.dial_generation,
                           t.responder_thread_id AS page_responder_thread_id,
                           t.responder_host_id AS page_responder_host_id,
                           t.state AS page_state
                    FROM final_callback_receipts AS r
                    JOIN pages AS t
                      ON t.request_id = r.request_id
                    WHERE r.final_callback_capability_sha256=?
                      AND r.transport_mode='final_callback'
                      AND r.state IN ('armed','captured')
                    """,
                    (capability_sha256,),
                ).fetchall()
                if len(rows) != 1:
                    raise BeeperQueueProtocolError("Final Callback capability is invalid")
                row = rows[0]
                request_id = str(row["request_id"] or "")
                fence_token = str(row["fence_token"] or "")
                request = _read_json(self._path(self.claimed_dir, request_id))
                if request is None:
                    raise BeeperQueueProtocolError("Final Callback request is unavailable")
                payload = request.get("payload")
                if (
                    str(request.get("request_id") or "") != request_id
                    or str(request.get("fence_token") or "") != fence_token
                    or str(request.get("dial_id") or "")
                    != str(row["dial_id"] or "")
                    or type(request.get("dial_generation")) is not int
                    or request.get("dial_generation")
                    != int(row["dial_generation"] or 0)
                    or str(request.get("dial_origin") or "")
                    != "page"
                    or str(request.get("beeper_thread_id") or "")
                    != str(row["beeper_thread_id"] or "")
                    or str(request.get("beeper_thread_id") or "")
                    != str(registration["beeper_thread_id"] or "")
                    or str(request.get("beeper_host_id") or "")
                    != str(registration["beeper_host_id"] or "")
                    or str(request.get("operation") or "") != "send_message_to_thread"
                    or not isinstance(payload, dict)
                    or str(payload.get("responder_thread_id") or "").strip()
                    != str(row["thread_id"] or "")
                    or str(payload.get("responder_thread_id") or "").strip()
                    != str(row["page_responder_thread_id"] or "")
                    or str(payload.get("responder_host_id") or "").strip()
                    != str(row["page_responder_host_id"] or "")
                    or self._prompt_sha256(payload.get("prompt"))
                    != str(row["prompt_sha256"] or "")
                ):
                    raise BeeperQueueProtocolError(
                        "Final Callback request identity is invalid"
                    )
                dial = connection.execute(
                    "SELECT * FROM dial_state WHERE singleton=1"
                ).fetchone()
                if (
                    dial is None
                    or str(dial["dial_id"] or "") != str(row["dial_id"] or "")
                    or str(dial["fence_token"] or "") != fence_token
                    or int(dial["generation"] or 0)
                    != int(row["dial_generation"] or 0)
                    or str(dial["dial_origin"] or "") != "page"
                    or str(dial["authorized_request_id"] or "") != request_id
                    or str(dial["authorized_operation"] or "")
                    != "send_message_to_thread"
                    or not self._dial_is_live(dial, current_time)
                    or str(row["page_state"] or "")
                    not in {"claimed_armed", "finishing"}
                ):
                    raise BeeperQueueProtocolError(
                        "Final Callback capability is no longer authoritative"
                    )
                state = str(row["state"] or "")
                if state == "armed" and float(row["expires_at"] or 0) <= current_time:
                    connection.execute(
                        """
                        UPDATE final_callback_receipts
                        SET state='expired', prompt_sha256='',
                            final_callback_capability_sha256='', answer_sha256='',
                            answer_chars=0, resolution_source='unknown', updated_at=?
                        WHERE request_id=? AND state='armed'
                          AND final_callback_capability_sha256=?
                          AND transport_mode='final_callback'
                        """,
                        (current_time, request_id, capability_sha256),
                    )
                    return {"accepted": False, "state": "expired"}
                if state == "captured":
                    stage_path = (
                        self.staging_dir / f"{request_id}.{fence_token[:16]}.txt"
                    )
                    try:
                        staged_answer = self._read_staged_final_answer(stage_path)
                    except BeeperQueueProtocolError:
                        staged_answer = None
                    if (
                        str(row["resolution_source"] or "") == "final_callback"
                        and str(row["answer_sha256"] or "") == answer_sha256
                        and int(row["answer_chars"] or -1) == len(bounded_answer)
                        and staged_answer == bounded_answer
                    ):
                        return {"accepted": True, "state": "captured"}
                    connection.execute(
                        """
                        UPDATE final_callback_receipts
                        SET state='conflict', prompt_sha256='',
                            final_callback_capability_sha256='', answer_sha256='',
                            answer_chars=0, resolution_source='unknown', updated_at=?
                        WHERE request_id=? AND state='captured'
                          AND final_callback_capability_sha256=?
                          AND transport_mode='final_callback'
                        """,
                        (current_time, request_id, capability_sha256),
                    )
                    try:
                        stage_path.unlink()
                    except OSError:
                        pass
                    return {"accepted": False, "state": "conflict"}
                stage_path = (
                    self.staging_dir / f"{request_id}.{fence_token[:16]}.txt"
                )
                _atomic_write_final_answer(stage_path, bounded_answer)
                cursor = connection.execute(
                    """
                    UPDATE final_callback_receipts
                    SET state='captured', resolution_source='final_callback',
                        session_id=thread_id, turn_id='', answer_sha256=?,
                        answer_chars=?, updated_at=?
                    WHERE request_id=? AND state='armed'
                      AND final_callback_capability_sha256=?
                      AND transport_mode='final_callback' AND fence_token=?
                    """,
                    (
                        answer_sha256,
                        len(bounded_answer),
                        current_time,
                        request_id,
                        capability_sha256,
                        fence_token,
                    ),
                )
                if cursor.rowcount != 1:
                    raise BeeperQueueProtocolError(
                        "Final Callback capability changed before capture"
                    )
                return {"accepted": True, "state": "captured"}
        except BeeperQueueProtocolError:
            if stage_path is not None:
                try:
                    stage_path.unlink()
                except OSError:
                    pass
            raise
        except sqlite3.Error as exc:
            if stage_path is not None:
                try:
                    stage_path.unlink()
                except OSError:
                    pass
            raise BeeperQueueProtocolError(f"final-callback database failed: {exc}") from exc
        except OSError as exc:
            if stage_path is not None:
                try:
                    stage_path.unlink()
                except OSError:
                    pass
            raise BeeperQueueProtocolError(
                "Final Callback staging could not be written"
            ) from exc

    def final_callback_status(
        self,
        request_id: str,
        fence_token: str,
        thread_id: str,
        turn_id: str,
    ) -> dict[str, Any]:
        """Read only exact-match receipt state; never emit answer text."""

        request_id, supplied_fence, _ = self._claimed_final_callback_request(
            request_id,
            fence_token,
        )
        responder_thread_id = thread_id.strip()
        responder_turn_id = self._validate_turn_id(turn_id)
        try:
            with self._dial_session() as connection:
                row = connection.execute(
                    "SELECT * FROM final_callback_receipts WHERE request_id=?",
                    (request_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise BeeperQueueProtocolError(f"final-callback database failed: {exc}") from exc
        if row is None:
            return {"available": False, "state": "unarmed"}
        if (
            str(row["fence_token"] or "") != supplied_fence
            or str(row["thread_id"] or "") != responder_thread_id
        ):
            raise BeeperQueueProtocolError("final-callback receipt identity mismatch")
        state = str(row["state"] or "")
        if state in {"bound", "captured", "completing", "native"} and (
            str(row["session_id"] or "") != responder_thread_id
            or str(row["turn_id"] or "") != responder_turn_id
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
                        and hook_turn_id == responder_turn_id,
                        "prompt_match_mode": str(row["prompt_match_mode"] or "none"),
                        "prompt_hook_rejection": str(
                            row["prompt_hook_rejection"] or "none"
                        ),
                    }
                )
            return result
        stage_path = self.stage_path(request_id, supplied_fence)
        try:
            staged = _read_exact_final_answer(stage_path)
        except OSError as exc:
            raise BeeperQueueProtocolError("captured Final Callback staging file is missing") from exc
        if (
            hashlib.sha256(staged.encode("utf-8")).hexdigest()
            != str(row["answer_sha256"] or "")
            or len(staged) != int(row["answer_chars"] or -1)
        ):
            raise BeeperQueueProtocolError("captured Final Callback staging file failed integrity")
        return {"available": True, "state": "captured"}

    def resolve_final_callback_native(
        self,
        request_id: str,
        fence_token: str,
        thread_id: str,
        turn_id: str,
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Fence a legacy native receipt; Beeper sends must never call this."""

        request_id, supplied_fence, _ = self._claimed_final_callback_request(
            request_id,
            fence_token,
        )
        responder_thread_id = thread_id.strip()
        responder_turn_id = self._validate_turn_id(turn_id)
        current_time = time.time() if now is None else now
        try:
            with self._dial_session() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM final_callback_receipts WHERE request_id=?",
                    (request_id,),
                ).fetchone()
                if row is None:
                    raise BeeperQueueProtocolError("final-callback request was not armed")
                if (
                    str(row["fence_token"] or "") != supplied_fence
                    or str(row["thread_id"] or "") != responder_thread_id
                ):
                    raise BeeperQueueProtocolError("final-callback receipt identity mismatch")
                if str(row["beeper_thread_id"] or ""):
                    raise BeeperQueueProtocolError(
                        "Beeper sends cannot use legacy native staging"
                    )
                state = str(row["state"] or "")
                if state == "captured":
                    raise BeeperQueueProtocolError(
                        "captured Hook final cannot be replaced by legacy native staging"
                    )
                if state not in {"armed", "bound", "native"}:
                    raise BeeperQueueProtocolError("final-callback receipt cannot use legacy native staging")
                if state == "bound" and (
                    str(row["session_id"] or "") != responder_thread_id
                    or str(row["turn_id"] or "") != responder_turn_id
                ):
                    raise BeeperQueueProtocolError(
                        "legacy native final does not match the Hook-bound turn"
                    )
                connection.execute(
                    """
                    UPDATE final_callback_receipts
                    SET state='native', resolution_source='native',
                        session_id=?, turn_id=?, updated_at=?
                    WHERE request_id=?
                    """,
                    (responder_thread_id, responder_turn_id, current_time, request_id),
                )
        except sqlite3.Error as exc:
            raise BeeperQueueProtocolError(f"final-callback database failed: {exc}") from exc
        return {"resolved": True, "state": "native"}

    @staticmethod
    def _terminal_final_callback_source(
        operation: str,
        payload: dict[str, Any],
    ) -> str:
        source = str(payload.get("final_callback_source") or "")
        if source in FINAL_CALLBACK_SOURCES:
            return source
        return "unknown" if operation == "send_message_to_thread" else "not_applicable"

    def _final_callback_resolution_source(self, request_id: str, operation: str) -> str:
        """Return one answer-free source enum before terminal state overwrites it."""

        if operation != "send_message_to_thread":
            return "not_applicable"
        try:
            with self._dial_session() as connection:
                row = connection.execute(
                    """
                    SELECT state, resolution_source
                    FROM final_callback_receipts WHERE request_id=?
                    """,
                    (self._validate_request_id(request_id),),
                ).fetchone()
        except (sqlite3.Error, BeeperQueueProtocolError):
            return "unknown"
        if row is None:
            return "unknown"
        state = str(row["state"] or "")
        source = str(row["resolution_source"] or "")
        if state in {"captured", "completing"} and source in {
            "final_callback",
            "hook",
        }:
            return source
        if state == "native":
            return "native"
        if state not in {"completed", "failed"}:
            return "unknown"
        if source in {"final_callback", "hook", "native"}:
            return source
        return "unknown"

    def _seal_current_final_callback(
        self,
        request_id: str,
        fence_token: str,
        result: dict[str, Any],
    ) -> str:
        """Atomically freeze one exact Final Callback value before publication."""

        request_id = self._validate_request_id(request_id)
        supplied_fence = self._validate_fence_token(fence_token)
        if set(result) != {
            "responder_thread_id",
            "responder_host_id",
            "responder_turn_id",
            "final_answer",
        }:
            raise BeeperQueueProtocolError(
                "send completion has a non-canonical Responder result schema"
            )
        responder_thread_id = str(result.get("responder_thread_id") or "").strip()
        if not looks_like_thread_id(responder_thread_id):
            raise BeeperQueueProtocolError(
                "send completion requires the exact responder task"
            )
        responder_turn_id = str(result.get("responder_turn_id") or "").strip()
        final_answer = result.get("final_answer")
        if not isinstance(final_answer, str):
            raise BeeperQueueProtocolError(
                "send completion requires an exact Final Callback answer"
            )
        bounded_answer = self._bounded_final_answer(final_answer)
        answer_sha256 = hashlib.sha256(bounded_answer.encode("utf-8")).hexdigest()
        stage_path = self.stage_path(request_id, supplied_fence)
        current_time = time.time()
        try:
            with self._dial_session() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM final_callback_receipts WHERE request_id=?",
                    (request_id,),
                ).fetchone()
                if row is None:
                    raise BeeperQueueProtocolError(
                        "send completion requires one captured responder receipt"
                    )
                source = str(row["resolution_source"] or "")
                identity_matches = (
                    str(row["fence_token"] or "") == supplied_fence
                    and str(row["thread_id"] or "") == responder_thread_id
                    and str(row["session_id"] or "") == responder_thread_id
                )
                if source == "hook":
                    raise BeeperQueueProtocolError(
                        "retired Hook final receipt is historical classification only"
                    )
                if source == "final_callback":
                    # MCP does not provide a trustworthy caller turn identity.
                    # The bearer capability proves only possession of the one
                    # fenced Final Callback capability, so do not invent a turn id.
                    identity_matches = (
                        identity_matches
                        and str(row["transport_mode"] or "") == "final_callback"
                        and not responder_turn_id
                        and SHA256_PATTERN.fullmatch(
                            str(row["final_callback_capability_sha256"] or "")
                        )
                        is not None
                        and SHA256_PATTERN.fullmatch(
                            str(row["prompt_sha256"] or "")
                        )
                        is not None
                    )
                else:
                    identity_matches = False
                if not identity_matches:
                    raise BeeperQueueProtocolError(
                        "send completion does not match the captured responder identity"
                    )
                state = str(row["state"] or "")
                if state not in {"captured", "completing"}:
                    raise BeeperQueueProtocolError(
                        "current send completion accepts only a captured Final Callback source"
                    )
                staged_answer = self._read_staged_final_answer(stage_path)
                if (
                    str(row["answer_sha256"] or "") != answer_sha256
                    or int(row["answer_chars"] or -1) != len(bounded_answer)
                    or staged_answer != bounded_answer
                ):
                    raise BeeperQueueProtocolError(
                        "send completion answer failed captured Responder integrity"
                    )
                cursor = connection.execute(
                    """
                    UPDATE final_callback_receipts
                    SET state='completing', resolution_source=?, updated_at=?
                    WHERE request_id=? AND state IN ('captured','completing')
                      AND resolution_source=?
                    """,
                    (source, current_time, request_id, source),
                )
                if cursor.rowcount != 1:
                    raise BeeperQueueProtocolError(
                        "captured Final Callback source changed before completion sealing"
                    )
        except sqlite3.Error as exc:
            raise BeeperQueueProtocolError(f"final-callback database failed: {exc}") from exc
        return source

    def _terminalize_final_callback(self, request_id: str, state: str) -> None:
        stage_path: Path | None = None
        try:
            with self._dial_session() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT fence_token FROM final_callback_receipts WHERE request_id=?",
                    (self._validate_request_id(request_id),),
                ).fetchone()
                cursor = connection.execute(
                    """
                    UPDATE final_callback_receipts
                    SET resolution_source=CASE
                            WHEN state='conflict' THEN 'unknown'
                            WHEN state='captured' AND resolution_source='final_callback'
                                THEN 'final_callback'
                            WHEN state='captured' THEN 'hook'
                            WHEN state='completing' AND resolution_source='final_callback'
                                THEN 'final_callback'
                            WHEN state='completing' THEN 'hook'
                            WHEN state='native' THEN 'native'
                            WHEN resolution_source IN
                                ('final_callback','hook','native','unknown','not_applicable')
                                THEN resolution_source
                            ELSE 'unknown'
                        END,
                        state=?, prompt_sha256='', final_callback_capability_sha256='',
                        answer_sha256='',
                        answer_chars=0, updated_at=?
                    WHERE request_id=?
                    """,
                    (
                        state,
                        time.time(),
                        self._validate_request_id(request_id),
                    ),
                )
                if cursor.rowcount == 1 and row is not None:
                    try:
                        fence_token = self._validate_fence_token(
                            str(row["fence_token"] or "")
                        )
                        stage_path = (
                            self.staging_dir
                            / f"{self._validate_request_id(request_id)}.{fence_token[:16]}.txt"
                        )
                    except BeeperQueueProtocolError:
                        stage_path = None
        except (sqlite3.Error, BeeperQueueProtocolError):
            # The queue terminal receipt is authoritative. Return bookkeeping
            # cannot revoke a committed responder outcome.
            pass
        if stage_path is not None:
            try:
                stage_path.unlink()
            except OSError:
                pass

    def _reconcile_terminal_final_callbacks(self) -> None:
        """Scrub final-callback bookkeeping for each authoritative terminal receipt."""

        for path in self.receipts_dir.glob("*.json"):
            request_id = path.stem
            if REQUEST_ID_PATTERN.fullmatch(request_id) is None:
                continue
            receipt = _read_json(path)
            if not self._valid_terminal_receipt(request_id, receipt):
                continue
            assert receipt is not None
            terminal_state = (
                "completed" if receipt.get("status") == "completed" else "failed"
            )
            self._terminalize_final_callback(request_id, terminal_state)

    @staticmethod
    def _empty_registration() -> dict[str, Any]:
        return {
            "valid": False,
            "beeper_thread_id": "",
            "beeper_host_id": "",
            "codex_exe_path": "",
            "codex_exe_sha256": "",
            "codex_version": "",
        }

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while True:
                block = stream.read(1024 * 1024)
                if not block:
                    break
                digest.update(block)
        return digest.hexdigest()

    @staticmethod
    def _metadata_text(value: str, label: str, limit: int) -> str:
        if not isinstance(value, str):
            raise BeeperQueueProtocolError(f"Beeper {label} is invalid")
        candidate = value.strip()
        if (
            not candidate
            or len(candidate) > limit
            or any(
                ord(character) < 32 or ord(character) == 127
                for character in candidate
            )
        ):
            raise BeeperQueueProtocolError(f"Beeper {label} is invalid")
        return candidate

    @staticmethod
    def _exact_executable(path: Path) -> Path:
        if path.name.casefold() != "codex.exe":
            raise BeeperQueueProtocolError(
                "Beeper executable path is not the exact Codex CLI"
            )
        try:
            attributes = int(getattr(os.lstat(path), "st_file_attributes", 0))
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise BeeperQueueProtocolError(
                "Beeper executable cannot be resolved"
            ) from exc
        if (
            path.is_symlink()
            or attributes & 0x400
            or not resolved.is_file()
            or os.path.normcase(os.path.normpath(str(path)))
            != os.path.normcase(os.path.normpath(str(resolved)))
        ):
            raise BeeperQueueProtocolError(
                "Beeper executable path is not exact"
            )
        return resolved

    def registration(self) -> dict[str, Any]:
        """Return closed, current producer metadata for the isolated namespace."""

        self._require_root()
        invalid = self._empty_registration()
        registration = _read_json(self.registration_file)
        if registration is None:
            return invalid
        registration_schema = registration.get("schema_version")
        if registration_schema not in {1, BEEPER_QUEUE_SCHEMA_VERSION} or (
            registration.get("namespace") != QUEUE_ROOT_NAME
        ):
            return invalid
        try:
            beeper_thread_id = self._metadata_text(
                registration.get("beeper_thread_id"), "task id", 256
            )
            if EXACT_SESSION_UUID_PATTERN.fullmatch(beeper_thread_id) is None:
                return invalid
            raw_beeper_host_id = registration.get("beeper_host_id")
            if raw_beeper_host_id is None and registration_schema == 1:
                # Read-only recognition lets an installed pre-glossary registration
                # prove identity during a one-way upgrade; new writes never use it.
                raw_beeper_host_id = registration.get(RETIRED_BEEPER_HOST_FIELD)
            if raw_beeper_host_id is None:
                return invalid
            beeper_host_id = str(raw_beeper_host_id or "").strip()
            if len(beeper_host_id) > 256 or any(
                ord(character) < 32 or ord(character) == 127
                for character in beeper_host_id
            ):
                return invalid
            codex_exe_path = self._metadata_text(
                registration.get("codex_exe_path"), "executable path", 4096
            )
            executable = Path(codex_exe_path)
            if not executable.is_absolute():
                return invalid
            executable = self._exact_executable(executable)
            codex_exe_sha256 = str(
                registration.get("codex_exe_sha256") or ""
            ).strip().lower()
            if SHA256_PATTERN.fullmatch(codex_exe_sha256) is None:
                return invalid
            if self._file_sha256(executable) != codex_exe_sha256:
                return invalid
            codex_version = self._metadata_text(
                registration.get("codex_version"), "version", 128
            )
            if any(ord(character) > 126 for character in codex_version):
                return invalid
        except (OSError, BeeperQueueProtocolError):
            return invalid
        return {
            "valid": True,
            "beeper_thread_id": beeper_thread_id,
            "beeper_host_id": beeper_host_id,
            "codex_exe_path": codex_exe_path,
            "codex_exe_sha256": codex_exe_sha256,
            "codex_version": codex_version,
        }

    def register(
        self,
        beeper_thread_id: str,
        beeper_host_id: str,
        codex_exe_path: str,
        codex_exe_sha256: str,
        codex_version: str,
    ) -> None:
        """Register one immutable exact Beeper and attested CLI binary."""

        self._require_root()
        candidate = self._metadata_text(
            beeper_thread_id, "task id", 256
        )
        if EXACT_SESSION_UUID_PATTERN.fullmatch(candidate) is None:
            raise BeeperQueueProtocolError("invalid Beeper task id")
        if not isinstance(beeper_host_id, str):
            raise BeeperQueueProtocolError("Beeper host id is invalid")
        resolved_host = beeper_host_id.strip()
        if len(resolved_host) > 256 or any(
            ord(character) < 32 or ord(character) == 127
            for character in resolved_host
        ):
            raise BeeperQueueProtocolError("Beeper host id is invalid")
        executable_text = self._metadata_text(
            codex_exe_path, "executable path", 4096
        )
        executable = Path(executable_text)
        if not executable.is_absolute():
            raise BeeperQueueProtocolError(
                "Beeper executable is not an absolute current file"
            )
        executable = self._exact_executable(executable)
        if not isinstance(codex_exe_sha256, str):
            raise BeeperQueueProtocolError("Beeper executable digest is invalid")
        supplied_sha256 = codex_exe_sha256.strip().lower()
        if SHA256_PATTERN.fullmatch(supplied_sha256) is None:
            raise BeeperQueueProtocolError("Beeper executable digest is invalid")
        try:
            actual_sha256 = self._file_sha256(executable)
        except OSError as exc:
            raise BeeperQueueProtocolError(
                "Beeper executable cannot be attested"
            ) from exc
        if actual_sha256 != supplied_sha256:
            raise BeeperQueueProtocolError(
                "Beeper executable digest does not match the current file"
            )
        version = self._metadata_text(codex_version, "version", 128)
        if any(ord(character) > 126 for character in version):
            raise BeeperQueueProtocolError(
                "Beeper version must be bounded ASCII metadata"
            )
        expected = {
            "valid": True,
            "beeper_thread_id": candidate,
            "beeper_host_id": resolved_host,
            "codex_exe_path": executable_text,
            "codex_exe_sha256": supplied_sha256,
            "codex_version": version,
        }
        if self.registration_file.exists():
            if self.registration() == expected:
                return
            raise BeeperQueueProtocolError(
                "Beeper registration is immutable and already exists"
            )
        now = time.time()
        payload = {
            "schema_version": BEEPER_QUEUE_SCHEMA_VERSION,
            "namespace": QUEUE_ROOT_NAME,
            "beeper_thread_id": candidate,
            "beeper_host_id": resolved_host,
            "codex_exe_path": executable_text,
            "codex_exe_sha256": supplied_sha256,
            "codex_version": version,
            "registered_at": now,
            "updated_at": now,
        }
        if not _atomic_write_json_exclusive(self.registration_file, payload):
            if self.registration() == expected:
                return
            raise BeeperQueueProtocolError(
                "Beeper registration changed concurrently"
            )

    @staticmethod
    def _validate_thread_uuid(thread_id: str) -> str:
        if not isinstance(thread_id, str):
            raise BeeperQueueProtocolError(
                "Beeper excluded task id is invalid"
            )
        candidate = thread_id.strip().lower()
        if EXACT_SESSION_UUID_PATTERN.fullmatch(candidate) is None:
            raise BeeperQueueProtocolError(
                "Beeper excluded task id is not an exact UUID"
            )
        return candidate

    def thread_tombstones(self) -> tuple[str, ...]:
        """Return the append-only task deny list for the isolated namespace."""

        self._require_root()
        payload = _read_json(self.thread_tombstones_file)
        if payload is None:
            if self.thread_tombstones_file.exists():
                raise BeeperQueueProtocolError(
                    "Beeper task tombstones are unreadable"
                )
            return ()
        if (
            set(payload) != {"schema_version", "thread_ids"}
            or payload.get("schema_version") != 1
            or not isinstance(payload.get("thread_ids"), list)
            or len(payload["thread_ids"]) > THREAD_TOMBSTONE_LIMIT
        ):
            raise BeeperQueueProtocolError(
                "Beeper task tombstones are invalid"
            )
        normalized = tuple(
            self._validate_thread_uuid(item)
            for item in payload["thread_ids"]
        )
        if tuple(sorted(set(normalized))) != normalized:
            raise BeeperQueueProtocolError(
                "Beeper task tombstones are not canonical"
            )
        return normalized

    def add_thread_tombstone(self, thread_id: str) -> tuple[str, ...]:
        """Append one exact UUID to the deny-only historical task set."""

        self._require_root()
        candidate = self._validate_thread_uuid(thread_id)
        existing = self.thread_tombstones()
        if candidate in existing:
            return existing
        if len(existing) >= THREAD_TOMBSTONE_LIMIT:
            raise BeeperQueueProtocolError(
                "Beeper task tombstone limit was reached"
            )
        updated = tuple(sorted((*existing, candidate)))
        _atomic_write_json(
            self.thread_tombstones_file,
            {"schema_version": 1, "thread_ids": list(updated)},
        )
        # Re-read the published file so a malformed or conflicting write never
        # becomes silent catalog authority.
        confirmed = self.thread_tombstones()
        if candidate not in confirmed:
            raise BeeperQueueProtocolError(
                "Beeper task tombstone was not retained"
            )
        return confirmed

    def excluded_thread_ids(self) -> tuple[str, ...]:
        """Return Beeper plus append-only historical task deny IDs."""

        denied = set(self.thread_tombstones())
        registration = self.registration()
        if registration["valid"] is True:
            denied.add(str(registration["beeper_thread_id"]))
        return tuple(sorted(denied))

    def _page_id(self, page: str | dict[str, Any]) -> str:
        if isinstance(page, str):
            return self._validate_page(page)
        if not isinstance(page, dict):
            raise BeeperQueueProtocolError("Beeper page is invalid")
        raw_page = page.get("page") or page.get("page_id")
        if not isinstance(raw_page, str):
            raise BeeperQueueProtocolError("Beeper page is missing")
        return self._validate_page(raw_page)

    def _page_record(
        self,
        page: str | dict[str, Any],
    ) -> dict[str, Any]:
        self._require_root()
        page_id = self._page_id(page)
        try:
            with self._dial_session() as connection:
                row = connection.execute(
                    "SELECT * FROM pages WHERE page_id=?",
                    (page_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise BeeperQueueProtocolError(
                f"Beeper page database failed: {exc}"
            ) from exc
        if row is None:
            raise BeeperQueueProtocolError("Beeper page is unknown")
        record = {key: row[key] for key in row.keys()}
        if isinstance(page, dict):
            expected_fields = {
                "request_id": str(record["request_id"]),
                "dial_id": str(record["dial_id"]),
                "fence_token": str(record["fence_token"]),
                "dial_generation": int(record["dial_generation"]),
            }
            for key, expected in expected_fields.items():
                if key in page and page[key] != expected:
                    raise BeeperQueueProtocolError(
                        "Beeper page identity was tampered"
                    )
        return record

    def _request(self, request_id: str) -> dict[str, Any]:
        request_id = self._validate_request_id(request_id)
        request = _read_json(self._path(self.claimed_dir, request_id)) or _read_json(
            self._path(self.pending_dir, request_id)
        )
        if request is None:
            raise BeeperQueueProtocolError("Beeper request is not readable")
        if str(request.get("request_id") or "") != request_id:
            raise BeeperQueueProtocolError("Beeper request identity is invalid")
        operation = str(request.get("operation") or "")
        if operation not in DIAL_OPERATIONS:
            raise BeeperQueueProtocolError(
                "Beeper request operation is not admitted"
            )
        payload = request.get("payload")
        if not isinstance(payload, dict):
            raise BeeperQueueProtocolError("Beeper request payload is invalid")
        if operation == "send_message_to_thread":
            self._reject_unsupported_send_mode(operation, payload)
            payload_fields = set(payload)
            if not {"responder_thread_id", "prompt"}.issubset(
                payload_fields
            ) or not payload_fields.issubset(
                {
                    "responder_thread_id",
                    "responder_host_id",
                    "prompt",
                    "source",
                    "client_message_id",
                }
            ):
                raise BeeperQueueProtocolError(
                    "Beeper Responder request schema is invalid"
                )
            responder_thread_id = str(payload.get("responder_thread_id") or "").strip()
            if EXACT_SESSION_UUID_PATTERN.fullmatch(responder_thread_id) is None:
                raise BeeperQueueProtocolError("Beeper responder task id is invalid")
            if responder_thread_id in self.excluded_thread_ids():
                raise BeeperQueueProtocolError(
                    "Beeper responder task is reserved or tombstoned"
                )
            # Desktop coordination host identity is optional.  An omitted host
            # means "use the task's owning host" and is represented by the
            # canonical empty string throughout the Beeper protocol.
            responder_host_id = payload.get("responder_host_id", "")
            if (
                not isinstance(responder_host_id, str)
                or len(responder_host_id) > 200
                or any(
                    ord(character) < 32 or ord(character) == 127
                    for character in responder_host_id
                )
            ):
                raise BeeperQueueProtocolError(
                    "Beeper Responder host identity is invalid"
                )
            prompt = payload.get("prompt")
            if not isinstance(prompt, str) or not prompt:
                raise BeeperQueueProtocolError("Beeper prompt is invalid")
            return request
        excluded = payload.get("excluded_thread_ids")
        if not isinstance(excluded, list):
            raise BeeperQueueProtocolError(
                "Beeper read request exclusions are invalid"
            )
        normalized_excluded = tuple(
            self._validate_thread_uuid(item) for item in excluded
        )
        if (
            tuple(sorted(set(normalized_excluded))) != normalized_excluded
            or normalized_excluded != self.excluded_thread_ids()
        ):
            raise BeeperQueueProtocolError(
                "Beeper read request exclusions are not authoritative"
            )
        if operation == "list_task_catalog":
            if set(payload) != {
                "catalog_version",
                "visibility",
                "thread_ids",
                "include_archived",
                "limit",
                "excluded_thread_ids",
            }:
                raise BeeperQueueProtocolError(
                    "Beeper catalog request schema is invalid"
                )
            visibility = payload.get("visibility")
            thread_ids = payload.get("thread_ids")
            limit = payload.get("limit")
            if (
                payload.get("catalog_version") != 1
                or visibility not in {"all", "exact"}
                or not isinstance(thread_ids, list)
                or len(thread_ids) > EXACT_VISIBILITY_LIMIT
                or payload.get("include_archived") is not False
                or type(limit) is not int
                or not 1 <= limit <= TASK_CATALOG_LIMIT
            ):
                raise BeeperQueueProtocolError(
                    "Beeper catalog request is invalid"
                )
            normalized_thread_ids = tuple(
                self._validate_thread_uuid(item) for item in thread_ids
            )
            if (
                tuple(sorted(set(normalized_thread_ids))) != normalized_thread_ids
                or (visibility == "all" and normalized_thread_ids)
                or any(item in normalized_excluded for item in normalized_thread_ids)
            ):
                raise BeeperQueueProtocolError(
                    "Beeper catalog visibility is invalid"
                )
            return request
        if set(payload) != {
            "responder_thread_id",
            "display_name",
            "catalog_snapshot_id",
            "expected_project_id",
            "expected_host_id",
            "selection_proof",
            "excluded_thread_ids",
        }:
            raise BeeperQueueProtocolError(
                "Beeper inspection request schema is invalid"
            )
        responder_thread_id = self._validate_thread_uuid(
            payload.get("responder_thread_id")
        )
        if responder_thread_id in normalized_excluded:
            raise BeeperQueueProtocolError(
                "Beeper inspection responder is excluded"
            )
        display_name = payload.get("display_name")
        snapshot_id = payload.get("catalog_snapshot_id")
        project_id = payload.get("expected_project_id")
        host_id = payload.get("expected_host_id")
        selection_proof = payload.get("selection_proof")
        if (
            not isinstance(display_name, str)
            or len(display_name) > 240
            or any(ord(character) < 32 or ord(character) == 127 for character in display_name)
            or not isinstance(snapshot_id, str)
            or REQUEST_ID_PATTERN.fullmatch(snapshot_id) is None
            or not isinstance(project_id, str)
            or not project_id.strip()
            or len(project_id) > 200
            or any(ord(character) < 32 or ord(character) == 127 for character in project_id)
            or not isinstance(host_id, str)
            or len(host_id) > 200
            or any(ord(character) < 32 or ord(character) == 127 for character in host_id)
            or not isinstance(selection_proof, str)
            or re.fullmatch(r"[a-f0-9]{64}", selection_proof) is None
        ):
            raise BeeperQueueProtocolError(
                "Beeper inspection request is invalid"
            )
        return request

    @staticmethod
    def _responder_fields(request: dict[str, Any]) -> tuple[str, str]:
        payload = request.get("payload")
        if not isinstance(payload, dict):
            return "", ""
        return (
            str(payload.get("responder_thread_id") or "").strip(),
            str(
                payload.get("responder_host_id")
                or payload.get("expected_host_id")
                or ""
            ).strip(),
        )

    @staticmethod
    def _read_text(
        value: Any,
        label: str,
        maximum: int,
        *,
        allow_empty: bool = False,
    ) -> str:
        if not isinstance(value, str) or len(value) > maximum:
            raise BeeperQueueProtocolError(
                f"Beeper {label} is invalid"
            )
        if value != value.strip() or any(
            ord(character) < 32 or ord(character) == 127 for character in value
        ):
            raise BeeperQueueProtocolError(
                f"Beeper {label} is invalid"
            )
        if not allow_empty and not value:
            raise BeeperQueueProtocolError(
                f"Beeper {label} is invalid"
            )
        return value

    def _validate_catalog_result(
        self,
        record: dict[str, Any],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(result, dict) or set(result) != {
            "catalog_version",
            "snapshot_id",
            "include_archived",
            "truncated",
            "projects",
            "tasks",
        }:
            raise BeeperQueueProtocolError(
                "Beeper catalog result schema is invalid"
            )
        snapshot_id = result.get("snapshot_id")
        if (
            result.get("catalog_version") != 1
            or result.get("include_archived") is not False
            or type(result.get("truncated")) is not bool
            or not isinstance(snapshot_id, str)
            or REQUEST_ID_PATTERN.fullmatch(snapshot_id) is None
            or snapshot_id != str(record.get("snapshot_id") or "")
        ):
            raise BeeperQueueProtocolError(
                "Beeper catalog result identity is invalid"
            )
        request = self._request(str(record["request_id"]))
        payload = request.get("payload")
        assert isinstance(payload, dict)
        limit = int(payload["limit"])
        raw_projects = result.get("projects")
        raw_tasks = result.get("tasks")
        if (
            not isinstance(raw_projects, list)
            or not isinstance(raw_tasks, list)
            or len(raw_projects) > limit
            or len(raw_tasks) > limit
        ):
            raise BeeperQueueProtocolError(
                "Beeper catalog collections exceed their bound"
            )
        project_ids: set[str] = set()
        for raw in raw_projects:
            if not isinstance(raw, dict) or set(raw) != {
                "project_id",
                "label",
                "host_id",
                "kind",
            }:
                raise BeeperQueueProtocolError(
                    "Beeper catalog project schema is invalid"
                )
            project_id = self._read_text(
                raw.get("project_id"), "catalog project id", 200
            )
            self._read_text(
                raw.get("label"), "catalog project label", 160
            )
            self._read_text(
                raw.get("host_id"),
                "catalog project host id",
                200,
                allow_empty=True,
            )
            self._read_text(
                raw.get("kind"), "catalog project kind", 40
            )
            if project_id in project_ids:
                raise BeeperQueueProtocolError(
                    "Beeper catalog project id is duplicated"
                )
            project_ids.add(project_id)
        requested_ids = set(payload["thread_ids"])
        excluded_ids = set(payload["excluded_thread_ids"])
        seen_thread_ids: set[str] = set()
        task_project_ids: set[str] = set()
        validated_tasks: list[dict[str, Any]] = []
        for raw in raw_tasks:
            if not isinstance(raw, dict) or set(raw) != {
                "thread_id",
                "title",
                "project_id",
                "host_id",
                "kind",
                "status",
                "archived",
                "updated_at",
            }:
                raise BeeperQueueProtocolError(
                    "Beeper catalog task schema is invalid"
                )
            thread_id = self._validate_thread_uuid(
                raw.get("thread_id")
            )
            project_id = self._read_text(
                raw.get("project_id"), "catalog task project id", 200
            )
            self._read_text(
                raw.get("title"), "catalog task title", 240
            )
            self._read_text(
                raw.get("host_id"),
                "catalog task host id",
                200,
                allow_empty=True,
            )
            if raw.get("kind") != "codex":
                raise BeeperQueueProtocolError(
                    "Beeper catalog task kind is not Codex"
                )
            self._read_text(
                raw.get("status"),
                "catalog task status",
                80,
                allow_empty=True,
            )
            updated_at = raw.get("updated_at")
            if (
                raw.get("archived") is not False
                or project_id not in project_ids
                or thread_id in seen_thread_ids
                or thread_id in excluded_ids
                or (
                    payload["visibility"] == "exact"
                    and thread_id not in requested_ids
                )
                or type(updated_at) not in {int, float}
                or not math.isfinite(float(updated_at))
                or float(updated_at) < 0
            ):
                raise BeeperQueueProtocolError(
                    "Beeper catalog task identity is invalid"
                )
            seen_thread_ids.add(thread_id)
            task_project_ids.add(project_id)
            proof_material = "\0".join(
                (
                    "catalog-selection-v1",
                    str(snapshot_id),
                    thread_id,
                    project_id,
                    str(raw.get("host_id") or ""),
                )
            ).encode("utf-8")
            fence_token = str(record.get("fence_token") or "")
            if FENCE_TOKEN_PATTERN.fullmatch(fence_token) is None:
                raise BeeperQueueProtocolError(
                    "Beeper catalog selection authority is invalid"
                )
            validated_tasks.append(
                {
                    **raw,
                    "selection_proof": hmac.new(
                        bytes.fromhex(fence_token),
                        proof_material,
                        hashlib.sha256,
                    ).hexdigest(),
                }
            )
        if project_ids != task_project_ids:
            raise BeeperQueueProtocolError(
                "Beeper catalog exposed unrelated projects"
            )
        return {
            **result,
            "tasks": validated_tasks,
            "snapshot_expires_at": (
                time.time() + CATALOG_SNAPSHOT_TTL_SECONDS
            ),
        }

    def _catalog_snapshot_record(
        self,
        snapshot_id: str,
    ) -> dict[str, Any]:
        if not isinstance(snapshot_id, str) or REQUEST_ID_PATTERN.fullmatch(
            snapshot_id
        ) is None:
            raise BeeperQueueProtocolError(
                "Beeper catalog snapshot id is invalid"
            )
        try:
            with self._dial_session() as connection:
                rows = connection.execute(
                    """
                    SELECT * FROM pages
                    WHERE operation='list_task_catalog' AND snapshot_id=?
                      AND state='completed'
                    """,
                    (snapshot_id,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise BeeperQueueProtocolError(
                f"Beeper catalog snapshot database failed: {exc}"
            ) from exc
        if len(rows) != 1:
            raise BeeperQueueProtocolError(
                "Beeper catalog snapshot is not authoritative"
            )
        record = {key: rows[0][key] for key in rows[0].keys()}
        snapshot_basis = float(record.get("terminal_at") or 0)
        if snapshot_basis <= 0:
            snapshot_basis = float(record.get("created_at") or 0)
        snapshot_age = time.time() - snapshot_basis
        if (
            not math.isfinite(snapshot_age)
            or snapshot_age < 0
            or snapshot_age > CATALOG_SNAPSHOT_TTL_SECONDS
        ):
            raise BeeperQueueProtocolError(
                "Beeper catalog snapshot has expired"
            )
        return record

    def _validate_inspect_result(
        self,
        record: dict[str, Any],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(result, dict) or set(result) != {
            "thread_id",
            "project_id",
            "host_id",
            "archived",
            "catalog_snapshot_id",
            "operation_receipt",
        }:
            raise BeeperQueueProtocolError(
                "Beeper inspection result schema is invalid"
            )
        request = self._request(str(record["request_id"]))
        payload = request.get("payload")
        assert isinstance(payload, dict)
        thread_id = self._validate_thread_uuid(result.get("thread_id"))
        project_id = self._read_text(
            result.get("project_id"), "inspection project id", 200
        )
        host_id = self._read_text(
            result.get("host_id"),
            "inspection host id",
            200,
            allow_empty=True,
        )
        snapshot_id = result.get("catalog_snapshot_id")
        operation_receipt = result.get("operation_receipt")
        if (
            thread_id != payload["responder_thread_id"]
            or project_id != payload["expected_project_id"]
            or host_id != payload["expected_host_id"]
            or result.get("archived") is not False
            or snapshot_id != payload["catalog_snapshot_id"]
            or not isinstance(snapshot_id, str)
            or REQUEST_ID_PATTERN.fullmatch(snapshot_id) is None
            or not isinstance(operation_receipt, str)
            or REQUEST_ID_PATTERN.fullmatch(operation_receipt) is None
            or operation_receipt
            != str(record.get("operation_receipt") or "")
        ):
            raise BeeperQueueProtocolError(
                "Beeper inspection result identity is invalid"
            )
        self._assert_snapshot_identity(payload)
        return result

    def _assert_snapshot_identity(
        self,
        payload: dict[str, Any],
    ) -> None:
        # The full catalog crosses the fenced queue only long enough for the
        # Bridge to consume it, then is redacted from durable receipts.  The
        # trusted Bridge keeps membership in its process-local wizard; the
        # controller retains only this short-lived snapshot identity and
        # independently re-checks the selected task's exact current identity.
        record = self._catalog_snapshot_record(
            str(payload.get("catalog_snapshot_id") or "")
        )
        proof_material = "\0".join(
            (
                "catalog-selection-v1",
                str(payload.get("catalog_snapshot_id") or ""),
                str(payload.get("responder_thread_id") or ""),
                str(payload.get("expected_project_id") or ""),
                str(payload.get("expected_host_id") or ""),
            )
        ).encode("utf-8")
        supplied = str(payload.get("selection_proof") or "")
        fence_token = str(record.get("fence_token") or "")
        if (
            re.fullmatch(r"[a-f0-9]{64}", supplied) is None
            or FENCE_TOKEN_PATTERN.fullmatch(fence_token) is None
            or not hmac.compare_digest(
                supplied,
                hmac.new(
                    bytes.fromhex(fence_token),
                    proof_material,
                    hashlib.sha256,
                ).hexdigest(),
            )
        ):
            raise BeeperQueueProtocolError(
                "Beeper inspection selection proof is invalid"
            )

    def _validate_readonly_result(
        self,
        record: dict[str, Any],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        operation = str(record.get("operation") or "")
        if operation == "list_task_catalog":
            return self._validate_catalog_result(record, result)
        if operation == "inspect_thread":
            return self._validate_inspect_result(record, result)
        raise BeeperQueueProtocolError(
            "Beeper page is not a read-only operation"
        )

    def _terminal_payload(
        self,
        record: dict[str, Any],
        response: dict[str, Any],
    ) -> dict[str, Any]:
        operation = str(record.get("operation") or "send_message_to_thread")
        responder_thread_id = str(record.get("responder_thread_id") or "").strip()
        responder_host_id = str(record.get("responder_host_id") or "").strip()
        if not responder_thread_id and operation == "send_message_to_thread":
            request = self._request(str(record["request_id"]))
            responder_thread_id, responder_host_id = self._responder_fields(request)
        return {
            **response,
            "terminal": True,
            "page": str(record["page_id"]),
            "page_id": str(record["page_id"]),
            "operation": operation,
            "responder_thread_id": responder_thread_id,
            "responder_host_id": responder_host_id,
        }

    def _finish_read_terminal(
        self,
        record: dict[str, Any],
        response: dict[str, Any],
    ) -> dict[str, Any]:
        """Consume a catalog blob once; the immutable receipt stays answer-free."""

        in_memory_response = response
        if (
            str(record.get("operation") or "") == "list_task_catalog"
            and response.get("status") == "completed"
        ):
            catalog = self._consume_catalog_blob(record)
            if catalog is not None:
                in_memory_response = {**response, "result": catalog}
        terminal_state = (
            "completed" if response.get("status") == "completed" else "failed"
        )
        self._mark_terminal(record, terminal_state)
        self._release(record, "terminal")
        return self._terminal_payload(record, in_memory_response)

    def _stage_catalog_blob(
        self,
        record: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        request_id = self._validate_request_id(str(record["request_id"]))
        path = self._path(self.catalog_staging_dir, request_id)
        body = {
            "request_id": request_id,
            "page_id": str(record["page_id"]),
            "result": result,
        }
        fence_token = str(record.get("fence_token") or "")
        if FENCE_TOKEN_PATTERN.fullmatch(fence_token) is None:
            raise BeeperQueueProtocolError(
                "Beeper catalog staging fence is invalid"
            )
        canonical = json.dumps(
            body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        payload = {
            **body,
            "seal": hmac.new(
                bytes.fromhex(fence_token),
                b"feishu-codex-bridge/catalog-staging/v1\0" + canonical,
                hashlib.sha256,
            ).hexdigest(),
        }
        if not _atomic_write_json_exclusive(path, payload):
            existing = _read_json(path)
            if existing != payload:
                raise BeeperQueueProtocolError(
                    "Beeper catalog staging conflicts with prior data"
                )

    def _consume_catalog_blob(
        self,
        record: dict[str, Any],
    ) -> dict[str, Any] | None:
        request_id = self._validate_request_id(str(record["request_id"]))
        source = self._path(self.catalog_staging_dir, request_id)
        consuming = self.catalog_staging_dir / (
            f"{request_id}.{uuid.uuid4().hex}.consuming"
        )
        try:
            os.replace(source, consuming)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise BeeperQueueProtocolError(
                f"Beeper catalog staging could not be consumed: {exc}"
            ) from exc
        try:
            payload = _read_json(consuming)
            result = payload.get("result") if isinstance(payload, dict) else None
            fence_token = str(record.get("fence_token") or "")
            body = (
                {
                    "request_id": payload.get("request_id"),
                    "page_id": payload.get("page_id"),
                    "result": result,
                }
                if isinstance(payload, dict)
                else {}
            )
            try:
                canonical = json.dumps(
                    body,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            except (TypeError, ValueError, OverflowError) as exc:
                raise BeeperQueueProtocolError(
                    "Beeper catalog staging payload is not canonical"
                ) from exc
            expected_seal = (
                hmac.new(
                    bytes.fromhex(fence_token),
                    b"feishu-codex-bridge/catalog-staging/v1\0" + canonical,
                    hashlib.sha256,
                ).hexdigest()
                if FENCE_TOKEN_PATTERN.fullmatch(fence_token) is not None
                else ""
            )
            if (
                not isinstance(payload, dict)
                or set(payload) != {"request_id", "page_id", "result", "seal"}
                or payload.get("request_id") != request_id
                or payload.get("page_id") != str(record["page_id"])
                or not isinstance(result, dict)
                or not isinstance(payload.get("seal"), str)
                or not hmac.compare_digest(str(payload.get("seal")), expected_seal)
            ):
                raise BeeperQueueProtocolError(
                    "Beeper catalog staging identity or seal is invalid"
                )
            return result
        finally:
            try:
                consuming.unlink()
            except OSError:
                pass

    @classmethod
    def _dial_identity_matches(
        cls,
        state: sqlite3.Row | None,
        *,
        request_id: str,
        operation: str,
        dial_id: str,
        fence_token: str,
        dial_generation: Any,
        dial_origin: str,
        now: float,
        require_active: bool,
    ) -> bool:
        """Match one request to its exact, live event-triggered dial lease."""

        return bool(
            state is not None
            and request_id
            and operation in DIAL_OPERATIONS
            and dial_origin == "page"
            and type(dial_generation) is int
            and dial_generation > 0
            and str(state["dial_id"] or "") == dial_id
            and str(state["fence_token"] or "") == fence_token
            and int(state["generation"] or 0) == dial_generation
            and str(state["dial_origin"] or "") == dial_origin
            and str(state["authorized_request_id"] or "") == request_id
            and str(state["authorized_operation"] or "") == operation
            and (not require_active or str(state["status"] or "") == "active")
            and cls._dial_is_live(state, now)
        )

    def _claim_matches_live_dial(
        self,
        request: dict[str, Any],
        state: sqlite3.Row | None,
        page: sqlite3.Row | None,
        now: float,
        *,
        expected_request_id: str | None = None,
    ) -> bool:
        request_id = str(request.get("request_id") or "").strip()
        operation = str(request.get("operation") or "").strip()
        dial_id = str(request.get("dial_id") or "").strip()
        fence_token = str(request.get("fence_token") or "").strip()
        dial_generation = request.get("dial_generation")
        dial_origin = str(request.get("dial_origin") or "").strip()
        admissible_page_states = (
            READ_CLAIM_STATES
            if operation in READ_OPERATIONS
            else SEND_CLAIM_STATES
        )
        return bool(
            self.root_name == QUEUE_ROOT_NAME
            and (expected_request_id is None or request_id == expected_request_id)
            and page is not None
            and str(page["request_id"] or "") == request_id
            and str(page["operation"] or "") == operation
            and str(page["dial_id"] or "") == dial_id
            and str(page["fence_token"] or "") == fence_token
            and type(dial_generation) is int
            and int(page["dial_generation"] or 0) == dial_generation
            and str(page["state"] or "") in admissible_page_states
            and self._dial_identity_matches(
                state,
                request_id=request_id,
                operation=operation,
                dial_id=dial_id,
                fence_token=fence_token,
                dial_generation=dial_generation,
                dial_origin=dial_origin,
                now=now,
                require_active=True,
            )
        )

    def _assert_dial_identity(
        self,
        record: dict[str, Any],
    ) -> None:
        try:
            with self._dial_session() as connection:
                state = connection.execute(
                    "SELECT * FROM dial_state WHERE singleton=1"
                ).fetchone()
        except sqlite3.Error as exc:
            raise BeeperQueueProtocolError(
                f"Beeper dial database failed: {exc}"
            ) from exc
        if not self._dial_identity_matches(
            state,
            request_id=str(record["request_id"]),
            operation=str(record.get("operation") or "send_message_to_thread"),
            dial_id=str(record["dial_id"]),
            fence_token=str(record["fence_token"]),
            dial_generation=record["dial_generation"],
            dial_origin="page",
            now=time.time(),
            require_active=False,
        ):
            raise BeeperQueueProtocolError(
                "Beeper page no longer owns its exact dial"
            )

    def _mark_terminal(
        self,
        record: dict[str, Any],
        state: str,
    ) -> None:
        if state not in {"completed", "failed"}:
            raise BeeperQueueProtocolError("Beeper terminal state is invalid")
        try:
            with self._dial_session() as connection:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    """
                    UPDATE pages
                    SET state=?, terminal_at=CASE
                            WHEN terminal_at > 0 THEN terminal_at ELSE ? END
                    WHERE page_id=?
                      AND state IN (
                          'reserved','claiming','claimed_armed','finishing',
                          'claiming_readonly','claimed_readonly',
                          'completing_readonly',
                          'unclaimed_load_failed','unclaimed_claim_timeout',?
                      )
                    """,
                    (state, time.time(), str(record["page_id"]), state),
                )
                if cursor.rowcount != 1:
                    raise BeeperQueueProtocolError(
                        "Beeper page terminal transition failed"
                    )
        except sqlite3.Error as exc:
            raise BeeperQueueProtocolError(
                f"Beeper page database failed: {exc}"
            ) from exc

    def _release(self, record: dict[str, Any], reason: str) -> None:
        try:
            self.release_dial(
                str(record["dial_id"]),
                str(record["fence_token"]),
                reason=reason,
            )
            return
        except BeeperQueueProtocolError:
            pass
        try:
            with self._dial_session() as connection:
                state = connection.execute(
                    "SELECT * FROM dial_state WHERE singleton=1"
                ).fetchone()
        except sqlite3.Error as exc:
            raise BeeperQueueProtocolError(
                f"Beeper dial database failed: {exc}"
            ) from exc
        if not (
            state is not None
            and str(state["dial_id"] or "") == ""
            and str(state["fence_token"] or "") == ""
            and int(state["last_released_generation"] or 0)
            >= int(record["dial_generation"])
        ):
            raise BeeperQueueProtocolError(
                "Beeper terminal dial could not be released"
            )

    def reserve_exact(self, request_id: str) -> dict[str, Any]:
        """Consume the permanent one-trigger grant for one exact pending request."""

        self._require_root()
        request_id = self._validate_request_id(request_id)
        registration = self.registration()
        if registration["valid"] is not True:
            raise BeeperQueueProtocolError("Beeper registration is not valid")
        if self.response(request_id) is not None:
            raise BeeperQueueProtocolError("Beeper request is already terminal")
        if self._path(self.claimed_dir, request_id).exists():
            raise BeeperQueueProtocolError("Beeper request is already claimed")
        pending_path = self._path(self.pending_dir, request_id)
        request = self._request(request_id)
        if not pending_path.exists():
            raise BeeperQueueProtocolError("Beeper request is not pending")
        operation = str(request.get("operation") or "")
        if operation not in DIAL_OPERATIONS:
            raise BeeperQueueProtocolError(
                "Beeper request operation is not admitted"
            )
        payload = request.get("payload")
        assert isinstance(payload, dict)
        if operation == "inspect_thread":
            self._assert_snapshot_identity(payload)
        responder_thread_id, responder_host_id = self._responder_fields(request)
        created_at = float(request.get("created_at", time.time()) or time.time())
        page_id = uuid.uuid4().hex
        dial_id = uuid.uuid4().hex
        fence_token = uuid.uuid4().hex
        snapshot_id = uuid.uuid4().hex if operation == "list_task_catalog" else ""
        operation_receipt = uuid.uuid4().hex if operation == "inspect_thread" else ""
        current_time = time.time()
        try:
            with self._dial_session() as connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT page_id FROM pages WHERE request_id=?",
                    (request_id,),
                ).fetchone()
                if existing is not None:
                    raise BeeperQueueProtocolError(
                        "Beeper request already consumed its one trigger grant"
                    )
                state = connection.execute(
                    "SELECT * FROM dial_state WHERE singleton=1"
                ).fetchone()
                if state is None:
                    raise BeeperQueueProtocolError("Beeper dial state is missing")
                if (
                    str(state["status"] or "") != "idle"
                    or str(state["dial_id"] or "")
                    or str(state["fence_token"] or "")
                ):
                    raise BeeperQueueProtocolError(
                        "Beeper has an unresolved one-shot dial"
                    )
                if (
                    not pending_path.exists()
                    or self._path(self.claimed_dir, request_id).exists()
                    or self._terminal_result_exists(request_id)
                ):
                    raise BeeperQueueProtocolError(
                        "Beeper request changed before reservation"
                    )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO dial_requests(
                        request_id, operation, created_at
                    ) VALUES(?, ?, ?)
                    """,
                    (request_id, operation, created_at),
                )
                connection.execute(
                    """
                    UPDATE dial_requests SET operation=?
                    WHERE request_id=? AND operation=''
                    """,
                    (operation, request_id),
                )
                generation_row = connection.execute(
                    "SELECT generation, operation FROM dial_requests WHERE request_id=?",
                    (request_id,),
                ).fetchone()
                if (
                    generation_row is None
                    or str(generation_row["operation"] or "")
                    != operation
                ):
                    raise BeeperQueueProtocolError(
                        "Beeper request operation metadata is invalid"
                    )
                generation = int(generation_row["generation"])
                connection.execute(
                    """
                    INSERT INTO pages(
                        page_id, request_id, dial_id, fence_token,
                        dial_generation, operation, responder_thread_id,
                        responder_host_id, snapshot_id, operation_receipt,
                        state, created_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'reserved', ?)
                    """,
                    (
                        page_id,
                        request_id,
                        dial_id,
                        fence_token,
                        generation,
                        operation,
                        responder_thread_id,
                        responder_host_id,
                        snapshot_id,
                        operation_receipt,
                        current_time,
                    ),
                )
                connection.execute(
                    """
                    UPDATE dial_state
                    SET dial_id=?, generation=?, fence_token=?, lease_until=?,
                        status='reserved', dial_origin='page',
                        authorized_request_id=?,
                        authorized_operation=?, updated_at=?
                    WHERE singleton=1
                    """,
                    (
                        dial_id,
                        generation,
                        fence_token,
                        current_time + self.dial_lease_ttl_seconds,
                        request_id,
                        operation,
                        current_time,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise BeeperQueueProtocolError(
                "Beeper request already consumed its one trigger grant"
            ) from exc
        except sqlite3.Error as exc:
            raise BeeperQueueProtocolError(
                f"Beeper page database failed: {exc}"
            ) from exc
        return {
            "status": "reserved",
            "page": page_id,
            "page_id": page_id,
            "request_id": request_id,
            "dial_id": dial_id,
            "fence_token": fence_token,
            "dial_generation": generation,
            "operation": operation,
            "snapshot_id": snapshot_id,
            "operation_receipt": operation_receipt,
            "beeper_thread_id": registration["beeper_thread_id"],
            "beeper_host_id": registration["beeper_host_id"],
            "codex_exe_path": registration["codex_exe_path"],
            "codex_exe_sha256": registration["codex_exe_sha256"],
            "codex_version": registration["codex_version"],
        }

    def claim_and_arm(
        self,
        page: str | dict[str, Any],
    ) -> dict[str, Any]:
        """Claim once and arm a Final Callback capability before disclosing the prompt."""

        record = self._page_record(page)
        if str(record.get("operation") or "") != "send_message_to_thread":
            raise BeeperQueueProtocolError(
                "Beeper final-callback claim requires a send page"
            )
        registration = self.registration()
        if registration["valid"] is not True:
            raise BeeperQueueProtocolError("Beeper registration is not valid")
        self._assert_dial_identity(record)
        try:
            with self._dial_session() as connection:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    """
                    UPDATE pages
                    SET state='claiming'
                    WHERE page_id=? AND state='reserved'
                    """,
                    (str(record["page_id"]),),
                )
                if cursor.rowcount != 1:
                    raise BeeperQueueProtocolError(
                        "Beeper page cannot disclose its prompt twice"
                    )
        except sqlite3.Error as exc:
            raise BeeperQueueProtocolError(
                f"Beeper page database failed: {exc}"
            ) from exc
        try:
            request = self.claim(
                str(registration["beeper_thread_id"]),
                str(registration["beeper_host_id"]),
                dial_id=str(record["dial_id"]),
                fence_token=str(record["fence_token"]),
                wait_seconds=0,
                release_on_empty=False,
            )
            if request is None or str(request.get("request_id") or "") != str(
                record["request_id"]
            ):
                raise BeeperQueueProtocolError(
                    "Beeper exact request could not be claimed"
                )
            request = self._request(str(record["request_id"]))
            payload = request["payload"]
            assert isinstance(payload, dict)
            responder_thread_id = str(payload["responder_thread_id"]).strip()
            final_callback_capability = uuid.uuid4().hex
            self.arm_final_callback(
                str(record["request_id"]),
                str(record["fence_token"]),
                responder_thread_id,
                hashlib.sha256(final_callback_capability.encode("ascii")).hexdigest(),
            )
            with self._dial_session() as connection:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    """
                    UPDATE pages
                    SET state='claimed_armed', claimed_at=?
                    WHERE page_id=? AND state='claiming'
                    """,
                    (time.time(), str(record["page_id"])),
                )
                if cursor.rowcount != 1:
                    raise BeeperQueueProtocolError(
                        "Beeper page arm transition failed"
                    )
        except Exception:
            try:
                self._publish_failure(
                    record,
                    "claim_or_arm_failed",
                    False,
                )
            except BeeperQueueProtocolError:
                pass
            raise
        return {
            "status": "claimed_armed",
            "page": str(record["page_id"]),
            "page_id": str(record["page_id"]),
            "request_id": str(record["request_id"]),
            "responder_thread_id": responder_thread_id,
            "responder_host_id": str(
                payload.get("responder_host_id") or ""
            ).strip(),
            "prompt": self._final_callback_prompt(
                payload["prompt"], final_callback_capability
            ),
            "client_message_id": str(payload.get("client_message_id") or ""),
        }

    def claim_readonly(
        self,
        page: str | dict[str, Any],
    ) -> dict[str, Any]:
        """Claim one operation-bound read Page without arming a Final Callback."""

        record = self._page_record(page)
        operation = str(record.get("operation") or "")
        if operation not in READ_OPERATIONS:
            raise BeeperQueueProtocolError(
                "Beeper read claim requires a read-only page"
            )
        registration = self.registration()
        if registration["valid"] is not True:
            raise BeeperQueueProtocolError("Beeper registration is not valid")
        self._assert_dial_identity(record)
        try:
            with self._dial_session() as connection:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    """
                    UPDATE pages
                    SET state='claiming_readonly'
                    WHERE page_id=? AND state='reserved' AND operation=?
                    """,
                    (str(record["page_id"]), operation),
                )
                if cursor.rowcount != 1:
                    raise BeeperQueueProtocolError(
                        "Beeper read page cannot be claimed twice"
                    )
        except sqlite3.Error as exc:
            raise BeeperQueueProtocolError(
                f"Beeper page database failed: {exc}"
            ) from exc
        try:
            request = self.claim(
                str(registration["beeper_thread_id"]),
                str(registration["beeper_host_id"]),
                dial_id=str(record["dial_id"]),
                fence_token=str(record["fence_token"]),
                wait_seconds=0,
                release_on_empty=False,
            )
            if request is None or str(request.get("request_id") or "") != str(
                record["request_id"]
            ):
                raise BeeperQueueProtocolError(
                    "Beeper exact read request could not be claimed"
                )
            request = self._request(str(record["request_id"]))
            payload = request.get("payload")
            assert isinstance(payload, dict)
            control_request = dict(payload)
            if operation == "list_task_catalog":
                snapshot_id = str(record.get("snapshot_id") or "")
                if REQUEST_ID_PATTERN.fullmatch(snapshot_id) is None:
                    raise BeeperQueueProtocolError(
                        "Beeper catalog snapshot identity is invalid"
                    )
                control_request["snapshot_id"] = snapshot_id
            else:
                operation_receipt = str(record.get("operation_receipt") or "")
                if REQUEST_ID_PATTERN.fullmatch(operation_receipt) is None:
                    raise BeeperQueueProtocolError(
                        "Beeper inspection receipt identity is invalid"
                    )
                self._assert_snapshot_identity(payload)
                control_request["operation_receipt"] = operation_receipt
            with self._dial_session() as connection:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    """
                    UPDATE pages
                    SET state='claimed_readonly', claimed_at=?
                    WHERE page_id=? AND state='claiming_readonly'
                      AND operation=?
                    """,
                    (time.time(), str(record["page_id"]), operation),
                )
                if cursor.rowcount != 1:
                    raise BeeperQueueProtocolError(
                        "Beeper read claim transition failed"
                    )
        except Exception:
            try:
                self._publish_failure(
                    record,
                    "read_claim_failed",
                    False,
                )
            except BeeperQueueProtocolError:
                pass
            raise
        return {
            "status": "claimed_readonly",
            "page": str(record["page_id"]),
            "page_id": str(record["page_id"]),
            "request_id": str(record["request_id"]),
            "operation": operation,
            "request": control_request,
        }

    def _publish_failure(
        self,
        record: dict[str, Any],
        code: str,
        may_have_started: bool,
    ) -> dict[str, Any]:
        operation = str(record.get("operation") or "send_message_to_thread")
        if operation in READ_OPERATIONS and may_have_started:
            raise BeeperQueueProtocolError(
                "Beeper read failure cannot be uncertain"
            )
        existing = self.response(str(record["request_id"]))
        if existing is not None:
            terminal_state = (
                "completed" if existing.get("status") == "completed" else "failed"
            )
            self._mark_terminal(record, terminal_state)
            self._release(record, "terminal")
            return self._terminal_payload(record, existing)
        self._assert_dial_identity(record)
        self.fail(
            str(record["request_id"]),
            code=code,
            message="The isolated one-shot Desktop execution did not produce a trusted final",
            retryable=False,
            may_have_started=may_have_started,
            enforce_fence=False,
        )
        response = self.response(str(record["request_id"]))
        if response is None:
            raise BeeperQueueProtocolError(
                "Beeper failure has no authoritative terminal response"
            )
        terminal_state = "completed" if response.get("status") == "completed" else "failed"
        self._mark_terminal(record, terminal_state)
        self._release(record, "terminal")
        return self._terminal_payload(record, response)

    def fail_page(
        self,
        page: str | dict[str, Any],
        code: str,
        may_have_started: bool,
    ) -> dict[str, Any]:
        """Publish one conservative terminal failure and release the exact dial."""

        if not isinstance(code, str) or not code.strip():
            raise BeeperQueueProtocolError("Beeper failure code is invalid")
        if code.strip()[:80] in UNCLAIMED_FAILURE_CODES:
            raise BeeperQueueProtocolError(
                "Beeper unclaimed failure requires the reserved-page CAS"
            )
        if type(may_have_started) is not bool:
            raise BeeperQueueProtocolError(
                "Beeper may-have-started marker must be boolean"
            )
        record = self._page_record(page)
        operation = str(record.get("operation") or "send_message_to_thread")
        if operation in READ_OPERATIONS and may_have_started:
            raise BeeperQueueProtocolError(
                "Beeper read failure must use may_have_started=false"
            )
        response = self.response(str(record["request_id"]))
        if response is not None:
            # Beeper-facing late failure is only an answer-free terminal ack.
            # Catalog content is reserved for the Bridge's private consume.
            terminal_state = (
                "completed" if response.get("status") == "completed" else "failed"
            )
            self._mark_terminal(record, terminal_state)
            self._release(record, "terminal")
            return self._terminal_payload(record, response)
        if operation == "send_message_to_thread":
            try:
                with self._dial_session() as connection:
                    receipt = connection.execute(
                        """
                        SELECT state FROM final_callback_receipts
                        WHERE request_id=? AND transport_mode='final_callback'
                          AND resolution_source='final_callback'
                        """,
                        (str(record["request_id"]),),
                    ).fetchone()
            except sqlite3.Error as exc:
                raise BeeperQueueProtocolError(
                    f"Beeper final-callback database failed: {exc}"
                ) from exc
            if receipt is not None:
                receipt_state = str(receipt["state"] or "")
                if receipt_state in {"captured", "completing"}:
                    # A completing receipt has already crossed the exact-final
                    # sealing boundary. Finish that value instead of racing it
                    # with a different terminal failure.
                    return self.finish_final_callback(page, 0)
        return self._publish_failure(
            record,
            code.strip()[:80],
            may_have_started,
        )

    def wait_for_beeper_claim(
        self,
        page: str | dict[str, Any],
        wait_seconds: int | float,
    ) -> str:
        """Observe only whether the queued Beeper has crossed the claim boundary."""

        if type(wait_seconds) not in {int, float}:
            raise BeeperQueueProtocolError("Beeper claim wait is invalid")
        bounded_wait = float(wait_seconds)
        if (
            not math.isfinite(bounded_wait)
            or bounded_wait < 0
            or bounded_wait > BEEPER_CLAIM_WAIT_MAX_SECONDS
        ):
            raise BeeperQueueProtocolError("Beeper claim wait is out of range")
        deadline = time.monotonic() + bounded_wait
        while True:
            record = self._page_record(page)
            response = self.response(str(record["request_id"]))
            if response is not None:
                return "terminal"
            try:
                self._assert_dial_identity(record)
            except BeeperQueueProtocolError:
                # Terminal publication precedes dial release. If release wins
                # between the response read and identity check, re-read the
                # authoritative receipt rather than misclassifying it as an
                # ambiguous dispatch failure.
                if self.response(str(record["request_id"])) is not None:
                    return "terminal"
                raise
            state = str(record["state"] or "")
            if state not in {
                "reserved",
                "claiming",
                "claimed_armed",
                "finishing",
                "claiming_readonly",
                "claimed_readonly",
                "completing_readonly",
                "unclaimed_load_failed",
                "unclaimed_claim_timeout",
            }:
                raise BeeperQueueProtocolError(
                    "Beeper page has no readable claim state"
                )
            if state != "reserved" or time.monotonic() >= deadline:
                return state
            time.sleep(min(0.1, max(0.01, deadline - time.monotonic())))

    def fail_page_if_unclaimed(
        self,
        page: str | dict[str, Any],
        code: str,
    ) -> dict[str, Any] | None:
        """Atomically seal a still-reserved page before publishing a safe failure."""

        state_by_code = {
            "beeper_load_assist_failed": "unclaimed_load_failed",
            "beeper_claim_timeout": "unclaimed_claim_timeout",
        }
        if code not in state_by_code:
            raise BeeperQueueProtocolError(
                "Beeper unclaimed failure code is invalid"
            )
        record = self._page_record(page)
        response = self.response(str(record["request_id"]))
        if response is not None:
            terminal_state = (
                "completed" if response.get("status") == "completed" else "failed"
            )
            self._mark_terminal(record, terminal_state)
            self._release(record, "terminal")
            return self._terminal_payload(record, response)
        terminalizing_state = state_by_code[code]
        current_state = str(record["state"] or "")
        if current_state == terminalizing_state:
            return self._publish_failure(record, code, False)
        if current_state != "reserved":
            return None
        try:
            with self._dial_session() as connection:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    """
                    UPDATE pages SET state=?
                    WHERE page_id=? AND request_id=? AND state='reserved'
                    """,
                    (
                        terminalizing_state,
                        str(record["page_id"]),
                        str(record["request_id"]),
                    ),
                )
                if cursor.rowcount != 1:
                    return None
        except sqlite3.Error as exc:
            raise BeeperQueueProtocolError(
                f"Beeper page database failed: {exc}"
            ) from exc
        return self._publish_failure(record, code, False)

    def _reconcile_unclaimed_failures(self) -> None:
        """Finish a crash-interrupted safe unclaimed terminal transition."""

        state_codes = {
            "unclaimed_load_failed": "beeper_load_assist_failed",
            "unclaimed_claim_timeout": "beeper_claim_timeout",
        }
        try:
            with self._dial_session() as connection:
                rows = connection.execute(
                    """
                    SELECT * FROM pages
                    WHERE state IN ('unclaimed_load_failed','unclaimed_claim_timeout')
                    ORDER BY created_at, page_id
                    """
                ).fetchall()
        except sqlite3.Error as exc:
            raise BeeperQueueProtocolError(
                f"Beeper page database failed: {exc}"
            ) from exc
        for row in rows:
            record = {key: row[key] for key in row.keys()}
            state = str(record["state"] or "")
            self._publish_failure(record, state_codes[state], False)

    def complete_readonly(
        self,
        page: str | dict[str, Any],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate and seal one operation-bound read-only controller result."""

        record = self._page_record(page)
        operation = str(record.get("operation") or "")
        if operation not in READ_OPERATIONS:
            raise BeeperQueueProtocolError(
                "Beeper read completion requires a read-only page"
            )
        response = self.response(str(record["request_id"]))
        if response is not None:
            terminal_state = (
                "completed" if response.get("status") == "completed" else "failed"
            )
            self._mark_terminal(record, terminal_state)
            self._release(record, "terminal")
            return self._terminal_payload(record, response)
        registration = self.registration()
        if registration["valid"] is not True:
            return self._publish_failure(
                record, "registration_changed", False
            )
        self._assert_dial_identity(record)
        try:
            validated = self._validate_readonly_result(record, result)
        except BeeperQueueProtocolError:
            return self._publish_failure(
                record, "readonly_result_invalid", False
            )
        terminal_result = validated
        if operation == "list_task_catalog":
            try:
                self._stage_catalog_blob(record, validated)
            except BeeperQueueProtocolError:
                return self._publish_failure(
                    record, "catalog_staging_failed", False
                )
            terminal_result = {
                "catalog_version": 1,
                "snapshot_id": str(record.get("snapshot_id") or ""),
                "catalog_staged": True,
            }
        try:
            with self._dial_session() as connection:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    """
                    UPDATE pages SET state='completing_readonly'
                    WHERE page_id=? AND state='claimed_readonly'
                      AND operation=?
                    """,
                    (str(record["page_id"]), operation),
                )
                if cursor.rowcount != 1:
                    raise BeeperQueueProtocolError(
                        "Beeper read completion transition is stale"
                    )
            self.complete(
                str(record["request_id"]),
                terminal_result,
                fence_token=str(record["fence_token"]),
            )
        except (sqlite3.Error, BeeperQueueProtocolError):
            response = self.response(str(record["request_id"]))
            if response is None:
                if operation == "list_task_catalog":
                    try:
                        self._path(
                            self.catalog_staging_dir,
                            str(record["request_id"]),
                        ).unlink()
                    except OSError:
                        pass
                return self._publish_failure(
                    record, "readonly_completion_failed", False
                )
        response = self.response(str(record["request_id"]))
        if response is None:
            raise BeeperQueueProtocolError(
                "Beeper read completion has no terminal response"
            )
        terminal_state = (
            "completed" if response.get("status") == "completed" else "failed"
        )
        self._mark_terminal(record, terminal_state)
        self._release(record, "readonly_completed")
        return self._terminal_payload(record, response)

    def finish_readonly_request(
        self,
        request_id: str,
        wait_seconds: int | float,
    ) -> dict[str, Any]:
        """Resume one already-admitted read by request id without another dial."""

        self._require_root()
        request_id = self._validate_request_id(request_id)
        try:
            with self._dial_session() as connection:
                rows = connection.execute(
                    "SELECT page_id, operation FROM pages "
                    "WHERE request_id=?",
                    (request_id,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise BeeperQueueProtocolError(
                f"Beeper page database failed: {exc}"
            ) from exc
        if (
            len(rows) != 1
            or str(rows[0]["operation"] or "") not in READ_OPERATIONS
        ):
            raise BeeperQueueProtocolError(
                "Beeper read request has no exact page"
            )
        return self.finish_readonly(
            str(rows[0]["page_id"]),
            wait_seconds,
        )

    def finish_readonly(
        self,
        page: str | dict[str, Any],
        wait_seconds: int | float,
    ) -> dict[str, Any]:
        """Wait boundedly for a read terminal; every failure stays non-uncertain."""

        if type(wait_seconds) not in {int, float}:
            raise BeeperQueueProtocolError("Beeper read wait is invalid")
        bounded_wait = float(wait_seconds)
        if (
            not math.isfinite(bounded_wait)
            or bounded_wait < 0
            or bounded_wait > WAIT_MAX_SECONDS
        ):
            raise BeeperQueueProtocolError("Beeper read wait is out of range")
        record = self._page_record(page)
        operation = str(record.get("operation") or "")
        if operation not in READ_OPERATIONS:
            raise BeeperQueueProtocolError(
                "Beeper read wait requires a read-only page"
            )
        unclaimed_failure_codes = {
            "unclaimed_load_failed": "beeper_load_assist_failed",
            "unclaimed_claim_timeout": "beeper_claim_timeout",
        }
        initial_state = str(record["state"] or "")
        if initial_state in unclaimed_failure_codes:
            return self._publish_failure(
                record, unclaimed_failure_codes[initial_state], False
            )
        response = self.response(str(record["request_id"]))
        if response is not None:
            return self._finish_read_terminal(record, response)
        if self.registration()["valid"] is not True:
            return self._publish_failure(
                record, "registration_changed", False
            )
        try:
            self._assert_dial_identity(record)
        except BeeperQueueProtocolError:
            raced = self.response(str(record["request_id"]))
            if raced is None:
                raise
            return self._finish_read_terminal(record, raced)
        deadline = time.monotonic() + bounded_wait
        next_renewal = 0.0
        registration = self.registration()
        while True:
            record = self._page_record(page)
            response = self.response(str(record["request_id"]))
            if response is not None:
                return self._finish_read_terminal(record, response)
            if time.monotonic() >= next_renewal:
                try:
                    self._assert_dial_identity(record)
                    self.renew_dial(
                        str(record["dial_id"]),
                        str(record["fence_token"]),
                        str(registration["beeper_thread_id"]),
                        str(registration["beeper_host_id"]),
                    )
                except BeeperQueueProtocolError:
                    return self._publish_failure(
                        record, "readonly_dial_lost", False
                    )
                next_renewal = time.monotonic() + min(
                    30.0, max(5.0, self.dial_lease_ttl_seconds / 2)
                )
            page_state = str(record["state"] or "")
            if page_state in unclaimed_failure_codes:
                return self._publish_failure(
                    record, unclaimed_failure_codes[page_state], False
                )
            if page_state not in {
                "reserved",
                "claiming_readonly",
                "claimed_readonly",
                "completing_readonly",
            }:
                raise BeeperQueueProtocolError(
                    "Beeper read page has no readable state"
                )
            if time.monotonic() >= deadline:
                return {
                    "status": "waiting_readonly",
                    "terminal": False,
                    "page": str(record["page_id"]),
                    "page_id": str(record["page_id"]),
                    "request_id": str(record["request_id"]),
                    "operation": operation,
                }
            time.sleep(min(0.1, max(0.01, deadline - time.monotonic())))

    def finish_final_callback(
        self,
        page: str | dict[str, Any],
        wait_seconds: int | float,
    ) -> dict[str, Any]:
        """Wait boundedly for one Final Callback, complete, and release."""

        if type(wait_seconds) not in {int, float}:
            raise BeeperQueueProtocolError("Beeper final-callback wait is invalid")
        bounded_wait = float(wait_seconds)
        if (
            not math.isfinite(bounded_wait)
            or bounded_wait < 0
            or bounded_wait > WAIT_MAX_SECONDS
        ):
            raise BeeperQueueProtocolError("Beeper final-callback wait is out of range")
        record = self._page_record(page)
        if str(record.get("operation") or "") != "send_message_to_thread":
            raise BeeperQueueProtocolError(
                "Beeper final-callback wait requires a send page"
            )
        unclaimed_failure_codes = {
            "unclaimed_load_failed": "beeper_load_assist_failed",
            "unclaimed_claim_timeout": "beeper_claim_timeout",
        }
        initial_state = str(record["state"] or "")
        if initial_state in unclaimed_failure_codes:
            return self._publish_failure(
                record, unclaimed_failure_codes[initial_state], False
            )
        response = self.response(str(record["request_id"]))
        if response is not None:
            terminal_state = (
                "completed" if response.get("status") == "completed" else "failed"
            )
            self._mark_terminal(record, terminal_state)
            self._release(record, "terminal")
            return self._terminal_payload(record, response)
        registration = self.registration()
        if registration["valid"] is not True:
            return self._publish_failure(
                record, "registration_changed", True
            )
        try:
            self._assert_dial_identity(record)
        except BeeperQueueProtocolError:
            # Completion publishes its terminal receipt before releasing the
            # dial. Preserve a result that wins this narrow read/assert race.
            raced_response = self.response(str(record["request_id"]))
            if raced_response is None:
                raise
            terminal_state = (
                "completed"
                if raced_response.get("status") == "completed"
                else "failed"
            )
            self._mark_terminal(record, terminal_state)
            self._release(record, "terminal")
            return self._terminal_payload(record, raced_response)
        deadline = time.monotonic() + bounded_wait
        next_renewal = 0.0
        while True:
            # `codex queue` reports only that the control message was accepted.
            # The Bridge may enter this bounded wait before the Beeper task
            # consumes the page.  Refresh only answer-free page state here;
            # this waiter never claims, triggers, or reads the queued prompt.
            record = self._page_record(page)
            response = self.response(str(record["request_id"]))
            if response is not None:
                terminal_state = (
                    "completed" if response.get("status") == "completed" else "failed"
                )
                self._mark_terminal(record, terminal_state)
                self._release(record, "terminal")
                return self._terminal_payload(record, response)
            if time.monotonic() >= next_renewal:
                try:
                    self._assert_dial_identity(record)
                    self.renew_dial(
                        str(record["dial_id"]),
                        str(record["fence_token"]),
                        str(registration["beeper_thread_id"]),
                        str(registration["beeper_host_id"]),
                    )
                except BeeperQueueProtocolError:
                    return self._publish_failure(
                        record, "dial_lost", True
                    )
                next_renewal = time.monotonic() + min(
                    30.0, max(5.0, self.dial_lease_ttl_seconds / 2)
                )
            page_state = str(record["state"] or "")
            if page_state in unclaimed_failure_codes:
                return self._publish_failure(
                    record, unclaimed_failure_codes[page_state], False
                )
            if page_state not in {
                "reserved",
                "claiming",
                "claimed_armed",
                "finishing",
            }:
                raise BeeperQueueProtocolError(
                    "Beeper page has no readable terminal response"
                )
            receipt = None
            if page_state in {"claimed_armed", "finishing"}:
                try:
                    with self._dial_session() as connection:
                        receipt = connection.execute(
                            "SELECT * FROM final_callback_receipts WHERE request_id=?",
                            (str(record["request_id"]),),
                        ).fetchone()
                except sqlite3.Error as exc:
                    raise BeeperQueueProtocolError(
                        f"Beeper final-callback database failed: {exc}"
                    ) from exc
            if receipt is None and page_state in {"claimed_armed", "finishing"}:
                return self._publish_failure(
                    record, "final_callback_missing", True
                )
            receipt_state = (
                str(receipt["state"] or "") if receipt is not None else ""
            )
            if receipt is not None and (
                str(receipt["request_id"] or "") != str(record["request_id"])
                or str(receipt["fence_token"] or "") != str(record["fence_token"])
                or str(receipt["transport_mode"] or "") != "final_callback"
                or str(receipt["thread_id"] or "")
                != str(record["responder_thread_id"] or "")
                or str(receipt["beeper_thread_id"] or "")
                != str(registration["beeper_thread_id"] or "")
            ):
                return self._publish_failure(
                    record, "final_callback_identity_invalid", True
                )
            if (
                receipt is not None
                and receipt_state == "armed"
                and float(receipt["expires_at"] or 0) <= time.time()
            ):
                try:
                    with self._dial_session() as connection:
                        connection.execute("BEGIN IMMEDIATE")
                        cursor = connection.execute(
                            """
                            UPDATE final_callback_receipts
                            SET state='expired', prompt_sha256='',
                                final_callback_capability_sha256='', answer_sha256='',
                                answer_chars=0, resolution_source='unknown', updated_at=?
                            WHERE request_id=? AND state='armed'
                              AND transport_mode='final_callback'
                              AND fence_token=?
                              AND final_callback_capability_sha256=?
                            """,
                            (
                                time.time(),
                                str(record["request_id"]),
                                str(record["fence_token"]),
                                str(receipt["final_callback_capability_sha256"] or ""),
                            ),
                        )
                    if cursor.rowcount == 1:
                        receipt_state = "expired"
                    else:
                        # A concurrent Final Callback may have captured the
                        # exact final after this waiter's read but before the
                        # expiry CAS. Re-read it instead of discarding a winner.
                        continue
                except sqlite3.Error as exc:
                    raise BeeperQueueProtocolError(
                        f"Beeper final-callback database failed: {exc}"
                    ) from exc
            if receipt_state in {"captured", "completing"}:
                try:
                    with self._dial_session() as connection:
                        connection.execute("BEGIN IMMEDIATE")
                        cursor = connection.execute(
                            """
                            UPDATE pages SET state='finishing'
                            WHERE page_id=?
                              AND state IN ('claimed_armed','finishing')
                            """,
                            (str(record["page_id"]),),
                        )
                        if cursor.rowcount != 1:
                            raise BeeperQueueProtocolError(
                                "Beeper final-callback transition is stale"
                            )
                    if (
                        str(receipt["resolution_source"] or "") != "final_callback"
                        or str(receipt["session_id"] or "")
                        != str(receipt["thread_id"] or "")
                        or str(receipt["turn_id"] or "")
                        or SHA256_PATTERN.fullmatch(
                            str(receipt["final_callback_capability_sha256"] or "")
                        )
                        is None
                    ):
                        raise BeeperQueueProtocolError(
                            "Beeper final-callback identity is invalid"
                        )
                    responder_thread_id = str(receipt["thread_id"] or "")
                    stage_path = self.stage_path(
                        str(record["request_id"]), str(record["fence_token"])
                    )
                    final_answer = self._read_staged_final_answer(stage_path)
                    if (
                        hashlib.sha256(final_answer.encode("utf-8")).hexdigest()
                        != str(receipt["answer_sha256"] or "")
                        or len(final_answer) != int(receipt["answer_chars"] or -1)
                    ):
                        raise BeeperQueueProtocolError(
                            "Beeper Final Callback staging integrity failed"
                        )
                    request = self._request(str(record["request_id"]))
                    _, responder_host_id = self._responder_fields(request)
                    self.complete(
                        str(record["request_id"]),
                        {
                            "responder_thread_id": responder_thread_id,
                            "responder_host_id": responder_host_id,
                            "responder_turn_id": "",
                            "final_answer": final_answer,
                        },
                        fence_token=str(record["fence_token"]),
                    )
                except (OSError, sqlite3.Error, BeeperQueueProtocolError):
                    return self._publish_failure(
                        record, "final_callback_invalid", True
                    )
                response = self.response(str(record["request_id"]))
                if response is None:
                    raise BeeperQueueProtocolError(
                        "Beeper completion has no terminal response"
                    )
                self._mark_terminal(record, "completed")
                self._release(record, "completed")
                return self._terminal_payload(record, response)
            if receipt_state in {"conflict", "expired", "failed"}:
                return self._publish_failure(
                    record, f"final_callback_{receipt_state}", True
                )
            if time.monotonic() >= deadline:
                request = self._request(str(record["request_id"]))
                responder_thread_id, responder_host_id = self._responder_fields(
                    request
                )
                return {
                    "status": "waiting_final_callback",
                    "terminal": False,
                    "page": str(record["page_id"]),
                    "page_id": str(record["page_id"]),
                    "request_id": str(record["request_id"]),
                    "responder_thread_id": responder_thread_id,
                    "responder_host_id": responder_host_id,
                }
            time.sleep(min(0.1, max(0.01, deadline - time.monotonic())))

    def status(self, now: float | None = None) -> BeeperQueueStatus:
        current_time = time.time() if now is None else now
        registration = _read_json(self.registration_file) or {}
        beeper_thread_id = str(registration.get("beeper_thread_id") or "").strip()
        raw_beeper_host_id = registration.get("beeper_host_id")
        if raw_beeper_host_id is None:
            raw_beeper_host_id = registration.get(RETIRED_BEEPER_HOST_FIELD)
        generation, dial_inflight, dial_remaining = self._dial_snapshot(current_time)
        registered = bool(beeper_thread_id)
        return BeeperQueueStatus(
            registered=registered,
            beeper_thread_id=beeper_thread_id,
            beeper_host_id=str(raw_beeper_host_id or "").strip(),
            pending=len(self._actionable_pending_paths()),
            claimed=sum(
                1
                for path in self.claimed_dir.glob("*.json")
                if not self._terminal_result_exists(path.stem)
            ),
            dial_generation=generation,
            dial_inflight=dial_inflight,
            dial_lease_remaining_seconds=dial_remaining,
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
    def _reject_unsupported_send_mode(
        operation: str,
        payload: dict[str, Any],
    ) -> None:
        if operation != "send_message_to_thread":
            return
        if not isinstance(payload, dict):
            raise BeeperQueueProtocolError("Desktop Beeper send payload must be an object")
        if (
            "mode" in payload
            or payload.get("source") == "feishu-steer"
            or "parent_request_id" in payload
        ):
            raise BeeperQueueProtocolError(
                "Desktop Beeper steer is unsupported; no responder action was queued"
            )

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
            "responder_archived",
            "responder_not_found",
            "responder_result_unknown",
            "turn_interrupted",
            "responder_needs_attention",
            "responder_tool_unavailable",
            "project_not_registered",
        }:
            return False
        return (
            details.get("retryable") is True
            and details.get("may_have_started") is False
        )

    def _operation_response_allows_retry(
        self,
        operation: str,
        response: dict[str, Any],
    ) -> bool:
        # The isolated namespace consumes one permanent local grant per
        # deterministic request. Even a forged or legacy retryable read receipt
        # must not manufacture another generation or a second CLI queue attempt.
        if (
            self.root_name == QUEUE_ROOT_NAME
            and operation in DIAL_OPERATIONS
        ):
            return False
        return self._response_allows_retry(response)

    def submit(
        self,
        operation: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> str:
        if operation not in ALLOWED_OPERATIONS:
            raise BeeperQueueProtocolError(f"unsupported Desktop Beeper operation: {operation}")
        self._reject_unsupported_send_mode(operation, payload)
        if (
            self.root_name == QUEUE_ROOT_NAME
            and operation == "send_message_to_thread"
        ):
            responder_thread_id = str(payload.get("responder_thread_id") or "").strip()
            if (
                EXACT_SESSION_UUID_PATTERN.fullmatch(responder_thread_id) is None
                or responder_thread_id in self.excluded_thread_ids()
            ):
                raise BeeperQueueProtocolError(
                    "Beeper responder task is invalid or excluded"
                )
        retry_generation = 0
        # Generation zero is the initial request; at most 64 later retry
        # generations may follow explicitly safe terminal failures.
        while retry_generation <= MAX_RETRY_GENERATIONS:
            request_id = _request_id(operation, idempotency_key, retry_generation)
            body = {
                "schema_version": BEEPER_QUEUE_SCHEMA_VERSION,
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
                    raise BeeperQueueProtocolError(
                        "completed Desktop Beeper request was reused with different content"
                    )
                if self._operation_response_allows_retry(operation, existing):
                    if not idempotency_key:
                        raise BeeperQueueProtocolError(
                            "retryable Desktop Beeper request has no idempotency key"
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
                        raise BeeperQueueProtocolError(
                            "Desktop Beeper request state exists but is unreadable"
                        )
                    continue
                if existing.get("fingerprint") != fingerprint:
                    raise BeeperQueueProtocolError(
                        "idempotent Desktop Beeper request was reused with different content"
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
                raise BeeperQueueProtocolError(
                    "Desktop Beeper request has a terminal receipt without a readable response"
                )
            pending_path = self._path(self.pending_dir, request_id)
            if not _atomic_write_json_exclusive(pending_path, request):
                # Another producer published this deterministic request between
                # our existence check and publication. Reconcile against the
                # winner; never overwrite a different fingerprint.
                concurrent_response = self.response(request_id)
                if concurrent_response is not None:
                    if concurrent_response.get("fingerprint") != fingerprint:
                        raise BeeperQueueProtocolError(
                            "completed Desktop Beeper request was reused with different content"
                        )
                    if self._operation_response_allows_retry(
                        operation, concurrent_response
                    ):
                        if not idempotency_key:
                            raise BeeperQueueProtocolError(
                                "retryable Desktop Beeper request has no idempotency key"
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
                            raise BeeperQueueProtocolError(
                                "Desktop Beeper request state exists but is unreadable"
                            )
                        continue
                    if existing.get("fingerprint") != fingerprint:
                        raise BeeperQueueProtocolError(
                            "idempotent Desktop Beeper request was reused with different content"
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
                    raise BeeperQueueProtocolError(
                        "Desktop Beeper request has a terminal receipt without a readable response"
                    )
                raise BeeperQueueProtocolError(
                    "concurrent Desktop Beeper request publication could not be reconciled"
                )
            try:
                self._record_pending_request(
                    request_id,
                    float(request["created_at"]),
                    operation,
                )
            except sqlite3.Error:
                # Never discard a durable request because dial metadata was briefly
                # locked. The metadata-only Beeper probe repairs this from the filename.
                pass
            return request_id
        raise BeeperQueueProtocolError("Desktop Beeper request exceeded safe retry generations")

    def _registered_beeper(
        self,
        beeper_thread_id: str,
        beeper_host_id: str,
    ) -> tuple[str, str]:
        candidate = beeper_thread_id.strip()
        registration = self.registration()
        registered = str(registration.get("beeper_thread_id") or "").strip()
        if not registered:
            raise BeeperQueueProtocolError("Desktop Beeper task is not registered")
        if candidate != registered:
            raise BeeperQueueProtocolError("dial came from a different Desktop Beeper task")
        registered_host = str(registration.get("beeper_host_id") or "").strip()
        supplied_host = beeper_host_id.strip()
        if registered_host and supplied_host and registered_host != supplied_host:
            raise BeeperQueueProtocolError("dial host does not match the registered Desktop host")
        return registered, supplied_host or registered_host

    def _activate_dial(
        self,
        dial_id: str,
        fence_token: str,
        beeper_thread_id: str,
        beeper_host_id: str,
    ) -> int:
        dial_id = self._validate_dial_id(dial_id)
        fence_token = self._validate_fence_token(fence_token)
        self._registered_beeper(beeper_thread_id, beeper_host_id)
        now = time.time()
        try:
            with self._dial_session() as connection:
                connection.execute("BEGIN IMMEDIATE")
                state = connection.execute(
                    "SELECT * FROM dial_state WHERE singleton=1"
                ).fetchone()
                if (
                    state is None
                    or str(state["dial_id"] or "") != dial_id
                    or str(state["fence_token"] or "") != fence_token
                    or not self._dial_is_live(state, now)
                ):
                    raise BeeperQueueProtocolError("Desktop Beeper dial lease is stale or invalid")
                generation = int(state["generation"] or 0)
                connection.execute(
                    """
                    UPDATE dial_state
                    SET status='active', lease_until=?, updated_at=?
                    WHERE singleton=1
                    """,
                    (now + self.dial_lease_ttl_seconds, now),
                )
                connection.commit()
        except sqlite3.Error as exc:
            raise BeeperQueueProtocolError(f"Desktop Beeper dial database failed: {exc}") from exc
        return generation

    def renew_dial(
        self,
        dial_id: str,
        fence_token: str,
        beeper_thread_id: str,
        beeper_host_id: str = "",
    ) -> None:
        self._activate_dial(
            dial_id,
            fence_token,
            beeper_thread_id,
            beeper_host_id,
        )

    def release_dial(
        self,
        dial_id: str,
        fence_token: str,
        *,
        reason: str = "drained",
    ) -> dict[str, Any]:
        dial_id = self._validate_dial_id(dial_id)
        fence_token = self._validate_fence_token(fence_token)
        now = time.time()
        try:
            with self._dial_session() as connection:
                connection.execute("BEGIN IMMEDIATE")
                state = connection.execute(
                    "SELECT * FROM dial_state WHERE singleton=1"
                ).fetchone()
                if (
                    state is None
                    or str(state["dial_id"] or "") != dial_id
                    or str(state["fence_token"] or "") != fence_token
                ):
                    raise BeeperQueueProtocolError("stale Desktop Beeper cannot release this dial")
                generation = int(state["generation"] or 0)
                connection.execute(
                    """
                UPDATE dial_state
                SET dial_id='', fence_token='', lease_until=0, status='idle',
                    dial_origin='', authorized_request_id='',
                    authorized_operation='',
                    last_released_generation=MAX(last_released_generation, ?),
                        updated_at=?
                    WHERE singleton=1
                    """,
                    (generation, now),
                )
                connection.commit()
        except sqlite3.Error as exc:
            raise BeeperQueueProtocolError(f"Desktop Beeper dial database failed: {exc}") from exc
        return {
            "released": True,
            "reason": reason.strip()[:80] or "drained",
            "dial_generation": generation,
            "pending_count": len(self._actionable_pending_paths()),
        }

    def claim(
        self,
        beeper_thread_id: str,
        beeper_host_id: str = "",
        *,
        dial_id: str,
        fence_token: str,
        wait_seconds: int = 0,
        release_on_empty: bool = False,
    ) -> dict[str, Any] | None:
        self.expire_stale_claims()
        dial_generation = self._activate_dial(
            dial_id,
            fence_token,
            beeper_thread_id,
            beeper_host_id,
        )
        try:
            with self._dial_session() as connection:
                dial_state = connection.execute(
                    "SELECT * FROM dial_state WHERE singleton=1"
                ).fetchone()
        except sqlite3.Error as exc:
            raise BeeperQueueProtocolError(f"Desktop Beeper dial database failed: {exc}") from exc
        authorized_request_id = (
            str(dial_state["authorized_request_id"] or "") if dial_state is not None else ""
        )
        authorized_operation = (
            str(dial_state["authorized_operation"] or "") if dial_state is not None else ""
        )
        bounded_wait = max(0, min(wait_seconds, self.grace_wait_max_seconds))
        deadline = time.monotonic() + bounded_wait
        next_renewal = time.monotonic() + min(30, max(5, self.dial_lease_ttl_seconds // 2))
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
                claimed_path = self.claimed_dir / source.name
                request = _read_json(source)
                if request is None:
                    if source.exists():
                        self.fail(
                            source.stem,
                            code="invalid_request",
                            message="Desktop Beeper request JSON is invalid",
                            retryable=False,
                            may_have_started=False,
                            enforce_fence=False,
                        )
                    continue
                if authorized_operation and str(request.get("operation") or "") != authorized_operation:
                    raise BeeperQueueProtocolError(
                        "manual Beeper cycle request no longer matches its authorized operation"
                    )
                claimed_request = {
                    **request,
                    "claimed_at": time.time(),
                    "beeper_thread_id": beeper_thread_id.strip(),
                    "beeper_host_id": beeper_host_id.strip(),
                    "dial_id": self._validate_dial_id(dial_id),
                    "dial_generation": dial_generation,
                    "dial_origin": (
                        str(dial_state["dial_origin"] or "")
                        if dial_state is not None
                        else ""
                    ),
                    "fence_token": self._validate_fence_token(fence_token),
                }
                # Publish the complete fenced claim with create-if-absent
                # semantics.  The immutable pending anchor remains in place,
                # so neither a second producer nor a second claimant can create
                # another executable copy of this deterministic request.
                if not _atomic_write_json_exclusive(claimed_path, claimed_request):
                    continue
                if self._terminal_result_exists(source.stem):
                    # A terminal failure may have won immediately before the
                    # claim CAS.  It is authoritative and no responder may start.
                    continue
                return claimed_request
            if time.monotonic() >= deadline:
                if release_on_empty:
                    self.release_dial(dial_id, fence_token, reason="grace_timeout")
                return None
            if time.monotonic() >= next_renewal:
                self.renew_dial(
                    dial_id,
                    fence_token,
                    beeper_thread_id,
                    beeper_host_id,
                )
                next_renewal = time.monotonic() + min(
                    30, max(5, self.dial_lease_ttl_seconds // 2)
                )
            time.sleep(0.25)

    def _validate_claim_fence(
        self,
        request: dict[str, Any],
        fence_token: str,
        *,
        expected_request_id: str,
    ) -> None:
        claimed_fence = str(request.get("fence_token") or "").strip()
        if not claimed_fence:
            raise BeeperQueueProtocolError(
                "legacy Desktop Beeper claim has no fence and cannot be finalized"
            )
        supplied = self._validate_fence_token(fence_token)
        if supplied != claimed_fence:
            raise BeeperQueueProtocolError("stale Desktop Beeper cannot finalize this request")
        self._validate_dial_id(str(request.get("dial_id") or ""))
        now = time.time()
        try:
            with self._dial_session() as connection:
                connection.execute("BEGIN")
                state = connection.execute(
                    "SELECT * FROM dial_state WHERE singleton=1"
                ).fetchone()
                page = connection.execute(
                    "SELECT * FROM pages WHERE request_id=?",
                    (expected_request_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise BeeperQueueProtocolError(f"Desktop Beeper dial database failed: {exc}") from exc
        if not self._claim_matches_live_dial(
            request,
            state,
            page,
            now,
            expected_request_id=expected_request_id,
        ):
            raise BeeperQueueProtocolError("Desktop Beeper dial lease is no longer authoritative")

    def complete(
        self,
        request_id: str,
        result: dict[str, Any],
        *,
        fence_token: str = "",
    ) -> dict[str, str]:
        request_id = self._validate_request_id(request_id)
        existing_response = self.response(request_id)
        if existing_response is not None:
            terminal_state = (
                "completed"
                if existing_response.get("status") == "completed"
                else "failed"
            )
            self._terminalize_final_callback(request_id, terminal_state)
            if existing_response.get("status") == "completed":
                operation = str(existing_response.get("operation") or "")
                return {
                    "final_callback_source": self._terminal_final_callback_source(
                        operation,
                        existing_response,
                    )
                }
            raise BeeperQueueProtocolError("Desktop Beeper request already has a failed response")
        request = _read_json(self._path(self.claimed_dir, request_id))
        if request is None:
            raise BeeperQueueProtocolError("Desktop Beeper request is not claimed")
        self._validate_claim_fence(
            request,
            fence_token,
            expected_request_id=request_id,
        )
        operation = str(request.get("operation") or "")
        payload = request.get("payload")
        if not isinstance(payload, dict):
            raise BeeperQueueProtocolError("Desktop Beeper claimed payload is invalid")
        self._reject_unsupported_send_mode(operation, payload)
        if operation == "list_task_catalog":
            if result.get("catalog_version") != 1:
                raise BeeperQueueProtocolError(
                    "Desktop Beeper task catalog completion must use catalog version 1"
                )
        elif "catalog_version" in result:
            raise BeeperQueueProtocolError(
                "Desktop Beeper structured task catalog cannot complete another operation"
            )
        if operation == "send_message_to_thread":
            final_callback_source = self._seal_current_final_callback(
                request_id,
                fence_token,
                result,
            )
        else:
            final_callback_source = self._final_callback_resolution_source(
                request_id,
                operation,
            )
        written = self._finalize_response(
            request_id,
            {
                "schema_version": BEEPER_QUEUE_SCHEMA_VERSION,
                "request_id": request_id,
                "operation": request.get("operation"),
                "fingerprint": request.get("fingerprint"),
                "status": "completed",
                "final_callback_source": final_callback_source,
                "result": result,
                "completed_at": time.time(),
            },
        )
        if not written:
            existing_response = self.response(request_id)
            if existing_response is not None:
                terminal_state = (
                    "completed"
                    if existing_response.get("status") == "completed"
                    else "failed"
                )
                self._terminalize_final_callback(request_id, terminal_state)
                if existing_response.get("status") == "completed":
                    return {
                        "final_callback_source": self._terminal_final_callback_source(
                            str(existing_response.get("operation") or ""),
                            existing_response,
                        )
                    }
                raise BeeperQueueProtocolError(
                    "Desktop Beeper request already has a failed response"
                )
            raise BeeperQueueProtocolError(
                "Desktop Beeper terminal finalization is fenced without a published completion"
            )
        self._terminalize_final_callback(request_id, "completed")
        return {"final_callback_source": final_callback_source}

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
            self._terminalize_final_callback(request_id, terminal_state)
            return
        request = _read_json(self._path(self.claimed_dir, request_id)) or _read_json(
            self._path(self.pending_dir, request_id)
        ) or {}
        if enforce_fence:
            self._validate_claim_fence(
                request,
                fence_token,
                expected_request_id=request_id,
            )
        operation = str(request.get("operation") or "")
        if operation in READ_ONLY_OPERATIONS and may_have_started:
            raise BeeperQueueProtocolError(
                "read-only Desktop Beeper operation cannot be finalized with "
                "may_have_started=true"
            )
        if (
            self.root_name == QUEUE_ROOT_NAME
            and operation in READ_OPERATIONS
        ):
            retryable = False
        final_callback_source = self._final_callback_resolution_source(
            request_id,
            operation,
        )
        written = self._finalize_response(
            request_id,
            {
                "schema_version": BEEPER_QUEUE_SCHEMA_VERSION,
                "request_id": request_id,
                "operation": operation,
                "fingerprint": request.get("fingerprint"),
                "status": "failed",
                "final_callback_source": final_callback_source,
                "error": {
                    "code": code.strip()[:80] or "beeper_error",
                    "message": message.strip()[:4000] or "Desktop Beeper request failed",
                    "retryable": bool(retryable),
                    "may_have_started": bool(may_have_started),
                },
                "completed_at": time.time(),
            },
        )
        if not written:
            existing_response = self.response(request_id)
            if existing_response is None:
                raise BeeperQueueProtocolError(
                    "Desktop Beeper terminal finalization is fenced without a published failure"
                )
            terminal_state = (
                "completed"
                if existing_response.get("status") == "completed"
                else "failed"
            )
            self._terminalize_final_callback(request_id, terminal_state)
            return
        self._terminalize_final_callback(request_id, "failed")

    def response(self, request_id: str) -> dict[str, Any] | None:
        request_id = self._validate_request_id(request_id)
        receipt = self._receipt_response(request_id)
        if receipt is not None:
            self._terminalize_final_callback(
                request_id,
                "completed" if receipt.get("status") == "completed" else "failed",
            )
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

    def _fail_expired_read_claim(
        self,
        request_id: str,
        *,
        pre_fence: bool,
    ) -> None:
        current = self.root_name == QUEUE_ROOT_NAME
        self.fail(
            request_id,
            code=(
                "readonly_result_unknown"
                if current
                else "beeper_read_claim_expired"
            ),
            message=(
                "Beeper read-only ownership expired; no mutation was "
                "admitted and the read will not be replayed"
                if current
                else (
                    "Pre-fence Desktop Beeper read-only claim lost its owner; no "
                    "responder mutation was authorized, so a retry generation is safe"
                    if pre_fence
                    else "Desktop Beeper stopped after claiming a read-only request; "
                    "no responder mutation was authorized, so a retry generation is safe"
                )
            ),
            retryable=not current,
            may_have_started=False,
            enforce_fence=False,
        )

    def expire_stale_claims(self, now: float | None = None) -> int:
        """Fail abandoned claims without ever replaying a possibly-started turn."""

        current_time = time.time() if now is None else now
        try:
            with self._dial_session() as connection:
                connection.execute("BEGIN")
                dial_state = connection.execute(
                    "SELECT * FROM dial_state WHERE singleton=1"
                ).fetchone()
                claim_states = tuple(sorted(CLAIM_STATES))
                page_rows = connection.execute(
                    "SELECT * FROM pages WHERE state IN ("
                    + ",".join("?" for _ in claim_states)
                    + ")",
                    claim_states,
                ).fetchall()
                pages_by_request = {
                    str(row["request_id"] or ""): row for row in page_rows
                }
        except sqlite3.Error:
            dial_state = None
            pages_by_request = {}
        expired = 0
        for path in self.claimed_dir.glob("*.json"):
            request_id = path.stem
            if self.response(request_id) is not None:
                continue
            request = _read_json(path)
            # Only this exact event-triggered request may inherit protection
            # from the one live dial.  A different, malformed, legacy, or stale
            # claim cannot hide behind global Beeper liveness.
            if request is not None and self._claim_matches_live_dial(
                request,
                dial_state,
                pages_by_request.get(request_id),
                current_time,
                expected_request_id=request_id,
            ):
                continue
            if request is None:
                if self._has_terminal_fence(request_id):
                    self._recover_interrupted_finalization(request_id, None)
                else:
                    self.fail(
                        request_id,
                        code="invalid_claim",
                        message="Desktop Beeper claim JSON is invalid",
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
                        self._fail_expired_read_claim(
                            request_id,
                            pre_fence=True,
                        )
                    else:
                        self.fail(
                            request_id,
                            code="legacy_unfenced_claim",
                            message=(
                                "Pre-fence Desktop Beeper claim cannot be finalized or replayed; "
                                "the responder action may have started"
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
                    self._fail_expired_read_claim(
                        request_id,
                        pre_fence=False,
                    )
                else:
                    self.fail(
                        request_id,
                        code="beeper_claim_expired",
                        message=(
                            "Desktop Beeper stopped after claiming this request; "
                            "the responder action may have started, so it was not replayed"
                        ),
                        retryable=False,
                        may_have_started=True,
                        enforce_fence=False,
                    )
            expired += 1
        return expired

    def cleanup(self, now: float | None = None) -> None:
        self.expire_stale_claims(now=now)
        current_time = time.time() if now is None else now
        cutoff = current_time - self.retention_seconds
        catalog_cutoff = current_time - CATALOG_SNAPSHOT_TTL_SECONDS
        for path in self.catalog_staging_dir.iterdir():
            if not path.is_file():
                continue
            try:
                # A Bridge worker may currently own a freshly renamed
                # ``.consuming`` file while maintenance runs in parallel.  Age,
                # not the suffix, distinguishes an abandoned consume here.
                if path.stat().st_mtime < catalog_cutoff:
                    path.unlink()
            except OSError:
                continue
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
        for path in self.responses_dir.iterdir():
            if not path.is_file():
                continue
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                continue
        # Staging may hold the only exact final answer for a long-running claim.
        # Age alone can never make that nonterminal answer disposable.  Remove
        # only a well-formed staging file whose request already has a readable,
        # authoritative terminal receipt.
        for path in self.staging_dir.iterdir():
            if not path.is_file():
                continue
            match = re.fullmatch(
                r"(?P<request_id>[a-f0-9]{32})\.[a-f0-9]{16}\.txt",
                path.name,
            )
            if match is None:
                continue
            try:
                if path.stat().st_mtime >= cutoff:
                    continue
                request_id = match.group("request_id")
                receipt = self._receipt_response(request_id)
                if receipt is None:
                    continue
                self._terminalize_final_callback(
                    request_id,
                    "completed" if receipt.get("status") == "completed" else "failed",
                )
                path.unlink()
            except (OSError, BeeperQueueProtocolError):
                continue
        for directory in (self.claimed_dir, self.pending_dir):
            for path in directory.glob("*.json"):
                try:
                    if (
                        path.stat().st_mtime < cutoff
                        and self._terminal_result_exists(path.stem)
                    ):
                        path.unlink()
                except (OSError, BeeperQueueProtocolError):
                    continue
        # Terminal receipts become compact idempotency tombstones instead of
        # being deleted. Retry generations therefore never lose their ancestry
        # or recreate generation zero after downtime, while expired answer text
        # does not remain in the permanent receipt set.
        try:
            with self._dial_session() as connection:
                connection.execute(
                    """
                    DELETE FROM final_callback_receipts
                    WHERE updated_at < ?
                      AND state IN ('completed','failed','conflict','expired')
                    """,
                    (cutoff,),
                )
                rows = connection.execute(
                    "SELECT request_id FROM dial_requests WHERE created_at < ?",
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
                        "DELETE FROM dial_requests WHERE request_id=?",
                        (request_id,),
                    )
        except (sqlite3.Error, BeeperQueueProtocolError):
            pass
