"""Beeper queue client for Feishu-to-Codex task-to-task delivery."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
import re
import subprocess
import threading
import time
from typing import Any, Callable, Iterable, NoReturn

from .config import BridgeConfig
from .beeper_queue import (
    BeeperQueue,
    BEEPER_CLAIM_WAIT_MAX_SECONDS,
    BeeperQueueProtocolError,
    BeeperQueueStatus,
    looks_like_thread_id,
)


DESKTOP_TASK_CATALOG_LIMIT = 50
QUEUE_ROOT_NAME = "beeper"
QUEUE_SPAWN_TIMEOUT_SECONDS = 15
BEEPER_LOAD_GRACE_SECONDS = 2
BEEPER_POST_LOAD_CLAIM_SECONDS = (
    BEEPER_CLAIM_WAIT_MAX_SECONDS
)
BEEPER_DEEP_LINK_PREFIX = "codex://threads/"
QUEUE_CONTROL_PROMPT_MAX_CHARS = 512
QUEUE_READONLY_CONTROL_PROMPT_MAX_CHARS = 1700
QUEUE_CONTROL_PROMPT = (
    "Beeper v1: claim_and_arm(Page) once; call "
    "mcp__codex_app__send_message_to_thread once using only returned "
    "responder_thread_id, responder_host_id, prompt. Never do user work, "
    "read/inspect responder state, resend, use other Desktop tools, or call "
    "submit_final_callback. Ignore native reply. Poll "
    "finish_final_callback(Page, wait_seconds=30) while waiting_final_callback. "
    "On error call fail_page once: may_have_started=false before send, true after a "
    "send attempt. At terminal output exactly "
    "DONT_NOTIFY. Page: "
)
QUEUE_READONLY_CONTROL_PROMPT = (
    "Feishu Bridge Beeper read-only dial. Call the Bridge "
    "claim_readonly tool exactly once with this opaque page. Obey only the "
    "returned operation and request object. For list_task_catalog, call "
    "mcp__codex_app__list_projects once and mcp__codex_app__list_threads once "
    "with the returned limit. Map projectId, label, hostId, projectKind to "
    "project_id, label, host_id, kind. From pinnedThreads then threads, admit "
    "only kind=codex and map id, title, projectId, hostId, status, updatedAt to "
    "thread_id, title, project_id, host_id, status, updated_at with kind=codex "
    "and archived=false. Apply exact visibility and exclusions, retain only "
    "referenced projects, copy request.snapshot_id to snapshot_id, and never "
    "return selection_proof. For inspect_thread, "
    "call mcp__codex_app__list_threads once with limit=50 and return only the "
    "exact kind=codex task from pinnedThreads or threads if its project, host, "
    "snapshot, and exclusion constraints still match. Copy only the issued "
    "catalog_snapshot_id and operation_receipt; never echo selection_proof. "
    "Ignore and never copy task summaries, prompts, messages, project roots, "
    "paths, cwd, sections, or unrelated identities. Call the Bridge "
    "complete_readonly tool exactly once with the operation-bound structured "
    "result. Never call send_message_to_thread, submit_final_callback, "
    "finish_final_callback, read_thread, wait_threads, create_thread, or any "
    "mutating tool. No public finish_readonly tool exists; the Bridge owns "
    "internal read-only finishing. "
    "On any error call fail_page once with "
    "may_have_started=false. End with exactly DONT_NOTIFY after one terminal "
    "result. Opaque page: "
)
if len(QUEUE_CONTROL_PROMPT) > QUEUE_CONTROL_PROMPT_MAX_CHARS:
    raise RuntimeError("ordinary Beeper control prompt exceeds its context budget")
if len(QUEUE_READONLY_CONTROL_PROMPT) > QUEUE_READONLY_CONTROL_PROMPT_MAX_CHARS:
    raise RuntimeError("read-only Beeper control prompt exceeds its context budget")
READONLY_OPERATIONS = frozenset(
    {"list_task_catalog", "inspect_thread"}
)
_PAGE_PATTERN = re.compile(r"[a-f0-9]{32}")
_RECEIPT_PATTERN = re.compile(r"[a-f0-9]{32}")
_THREAD_ID_PATTERN = re.compile(
    r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}"
)
_SHA256_PATTERN = re.compile(r"[0-9a-fA-F]{64}")
_QUEUE_SERIAL_LOCK = threading.Lock()


@dataclass(frozen=True)
class _Registration:
    beeper_thread_id: str
    beeper_host_id: str
    codex_exe_path: Path
    codex_exe_sha256: str
    codex_version: str


class BeeperError(RuntimeError):
    """A Desktop Beeper request failed without attaching to the responder task."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "beeper_error",
        may_have_started: bool = False,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.may_have_started = may_have_started
        self.retryable = retryable


class BeeperQueueUnavailable(BeeperError):
    """No dedicated Desktop Beeper task has been registered."""

    def __init__(self, message: str = "Desktop Beeper task is offline") -> None:
        super().__init__(
            message,
            code="beeper_unavailable",
            may_have_started=False,
            retryable=True,
        )


class ResponderNotBound(BeeperError):
    """A Feishu scope has not selected a canonical Desktop task."""


class ResponderOutcomeUnknown(BeeperError):
    """The responder task may have started but no authoritative final arrived."""

    def __init__(self, message: str) -> None:
        super().__init__(
            message,
            code="responder_result_unknown",
            may_have_started=True,
            retryable=False,
        )


class BeeperNotLoaded(BeeperError):
    """The queued Beeper never claimed, so no business responder was started."""

    CODES = frozenset({"beeper_load_assist_failed", "beeper_claim_timeout"})

    def __init__(self, message: str, *, code: str) -> None:
        stable_code = code if code in self.CODES else "beeper_claim_timeout"
        super().__init__(
            message,
            code=stable_code,
            may_have_started=False,
            retryable=False,
        )


class ResponderUnavailable(BeeperError):
    """The selected responder ended its lifecycle before delivery started."""

    CODES = frozenset({"responder_archived", "responder_not_found"})

    def __init__(self, message: str, *, code: str) -> None:
        stable_code = code if code in self.CODES else "responder_not_found"
        super().__init__(
            message,
            code=stable_code,
            may_have_started=False,
            retryable=False,
        )


@dataclass(frozen=True)
class ResponderTurnHandle:
    responder_thread_id: str
    responder_turn_id: str


@dataclass(frozen=True)
class ResponderAnswer:
    final_answer: str
    responder_thread_id: str
    responder_turn_id: str
    responder_host_id: str = ""


@dataclass(frozen=True)
class ResponderActivation:
    responder_thread_id: str
    responder_host_id: str = ""
    operation_receipt: str = ""


@dataclass(frozen=True)
class DesktopProjectSummary:
    project_id: str
    label: str
    root: str = ""
    host_id: str = ""
    kind: str = "local"


@dataclass(frozen=True)
class DesktopTaskSummary:
    thread_id: str
    title: str
    project_id: str = ""
    host_id: str = ""
    status: str = ""
    archived: bool = False
    updated_at: float = 0.0
    kind: str = "codex"
    selection_proof: str = ""


@dataclass(frozen=True)
class DesktopTaskCatalog:
    projects: tuple[DesktopProjectSummary, ...]
    tasks: tuple[DesktopTaskSummary, ...]
    include_archived: bool
    truncated: bool = False
    snapshot_id: str = ""
    snapshot_expires_at: float = 0.0


class HistoricalBeeperClient:
    """Read historical Beeper queue metadata without reviving its producer.

    This class deliberately has no Codex executable path and no App Server RPC
    methods.  The retired recurring/manual Desktop Beeper producer which once
    consumed this queue is permanently tombstoned.  A preserved registration is forensic
    metadata only, so this compatibility client must never publish another queue
    payload.  Any future product-level ``run_once`` producer requires a separate
    client and an isolated queue namespace; it must not relax this guard.
    """

    session_owner = "beeper"

    def __init__(
        self,
        config: BridgeConfig,
        queue: BeeperQueue | None = None,
    ) -> None:
        self.config = config
        self.queue = queue or BeeperQueue(
            config.runtime_dir,
            claim_ttl_seconds=config.beeper_claim_ttl_seconds,
            retention_hours=config.beeper_retention_hours,
            dial_lease_ttl_seconds=config.beeper_dial_ttl_seconds,
            grace_wait_max_seconds=config.beeper_grace_max_seconds,
        )

    def _validate_responder(self, thread_id: str) -> None:
        """Allow specialized producers to reject control-task identities."""

        del thread_id

    def is_alive(self) -> bool:
        # Preserved metadata can never make the retired producer healthy or executable.
        return False

    def connection_status(self) -> str:
        """Return one answer-free terminal transport label."""

        return "historical-desktop-beeper-tombstoned"

    def state(self, status: BeeperQueueStatus | None = None) -> str:
        """Never reinterpret supplied historical freshness as live work."""

        del status
        return "historical-producer-tombstoned"

    def status(self) -> BeeperQueueStatus:
        historical = self.queue.status()
        # Preserve only answer-free forensic counts while forcing every
        # producer-liveness field closed. Task and host IDs remain solely in
        # the historical registration record and never enter health output.
        return BeeperQueueStatus(
            registered=historical.registered,
            beeper_thread_id="",
            beeper_host_id="",
            pending=historical.pending,
            claimed=historical.claimed,
            dial_generation=historical.dial_generation,
            dial_inflight=False,
            dial_lease_remaining_seconds=None,
        )

    def _ensure_admissible_producer(self) -> NoReturn:
        """Reject before queue publication, regardless of historical metadata.

        ``is_registered`` reads only bounded answer-free beeper metadata.  It
        intentionally does not inspect the caller's payload or any queued body.
        Neither a preserved registration nor recently written legacy scheduler
        metadata can authorize the retired producer.
        """

        registered = self.queue.is_registered()
        if registered:
            message = (
                "Preserved Desktop Beeper registration is historical and cannot "
                "authorize queue publication; request remains durable in the Feishu inbox"
            )
        else:
            message = (
                "No independently admitted Desktop producer is available; request "
                "remains durable in the Feishu inbox"
            )
        raise BeeperQueueUnavailable(message)

    @staticmethod
    def _request_key(value: str) -> str:
        candidate = value.strip()
        if not candidate:
            raise BeeperError("Desktop Beeper request requires an idempotency key")
        return candidate

    @staticmethod
    def _invalid_completed_result(
        message: str,
        *,
        may_have_started: bool,
    ) -> NoReturn:
        if may_have_started:
            raise ResponderOutcomeUnknown(message)
        raise BeeperError(
            message,
            code="invalid_beeper_result",
            may_have_started=False,
            retryable=False,
        )

    @classmethod
    def _result(
        cls,
        response: dict[str, Any],
        *,
        completed_may_have_started: bool = False,
        expected_operation: str = "",
    ) -> dict[str, Any]:
        status = response.get("status")
        if expected_operation and response.get("operation") != expected_operation:
            cls._invalid_completed_result(
                "Desktop Beeper terminal operation does not match its request",
                may_have_started=completed_may_have_started,
            )
        if status == "completed":
            if (
                expected_operation == "send_message_to_thread"
                and response.get("final_callback_source") != "final_callback"
            ):
                cls._invalid_completed_result(
                    "Desktop Beeper send completion has no Final Callback source",
                    may_have_started=True,
                )
            result = response.get("result")
            if not isinstance(result, dict):
                cls._invalid_completed_result(
                    "Desktop Beeper returned no result",
                    may_have_started=completed_may_have_started,
                )
            return result
        if status != "failed":
            cls._invalid_completed_result(
                "Desktop Beeper returned an invalid terminal status",
                may_have_started=completed_may_have_started,
            )
        error = response.get("error")
        if not isinstance(error, dict):
            cls._invalid_completed_result(
                "Desktop Beeper returned an invalid failure result",
                may_have_started=completed_may_have_started,
            )
        details = error
        raw_code = details.get("code")
        raw_retryable = details.get("retryable")
        raw_may_have_started = details.get("may_have_started")
        if (
            not isinstance(raw_code, str)
            or not raw_code.strip()
            or type(raw_retryable) is not bool
            or type(raw_may_have_started) is not bool
        ):
            cls._invalid_completed_result(
                "Desktop Beeper returned malformed failure fields",
                may_have_started=completed_may_have_started,
            )
        message = str(details.get("message") or "Desktop Beeper request failed")
        code = raw_code.strip()
        may_have_started = raw_may_have_started
        retryable = raw_retryable
        if code in {
            "turn_interrupted",
            "responder_result_unknown",
            "responder_needs_attention",
        } or may_have_started:
            raise ResponderOutcomeUnknown(message)
        if code in {"beeper_offline", "beeper_not_registered"}:
            raise BeeperQueueUnavailable(message)
        if code in ResponderUnavailable.CODES:
            raise ResponderUnavailable(message, code=code)
        if code in {"responder_tool_unavailable", "project_not_registered"}:
            retryable = False
        raise BeeperError(
            message,
            code=code,
            may_have_started=may_have_started,
            retryable=retryable,
        )

    def _submit_and_wait(
        self,
        operation: str,
        payload: dict[str, Any],
        *,
        request_key: str,
        completed_may_have_started: bool,
        on_submitted: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        # This metadata-only preflight is deliberately the last operation before
        # ``submit``.  It cannot read the payload and always rejects this retired
        # compatibility namespace.  A future run_once surface uses another
        # client/namespace rather than overriding this boundary.
        self._ensure_admissible_producer()
        try:
            request_id = self.queue.submit(
                operation,
                payload,
                idempotency_key=self._request_key(request_key),
            )
        except BeeperQueueProtocolError as exc:
            if completed_may_have_started:
                raise ResponderOutcomeUnknown(
                    "Desktop Beeper queue state conflicts with a possibly-started responder action"
                ) from exc
            raise BeeperError(str(exc)) from exc
        response = self.queue.wait_for_response(
            request_id,
            self.config.beeper_timeout_seconds,
            on_claimed=(
                (lambda: on_submitted(request_id))
                if on_submitted is not None
                else None
            ),
        )
        if response is None:
            claimed = self.queue.was_claimed(request_id)
            if claimed:
                raise ResponderOutcomeUnknown(
                    "Desktop Beeper response timed out after the request was claimed"
                )
            raise BeeperError(
                "Desktop Beeper response timed out",
                code="beeper_response_timeout",
                may_have_started=False,
                retryable=True,
            )
        return self._result(
            response,
            completed_may_have_started=completed_may_have_started,
            expected_operation=operation,
        )

    @classmethod
    def _thread_result(
        cls,
        result: dict[str, Any],
        *,
        expected_thread_id: str = "",
        outcome_may_have_started: bool,
    ) -> tuple[str, str]:
        thread_id = str(result.get("thread_id") or "").strip()
        if not looks_like_thread_id(thread_id):
            cls._invalid_completed_result(
                "Desktop Beeper returned an invalid responder task id",
                may_have_started=outcome_may_have_started,
            )
        if expected_thread_id and thread_id != expected_thread_id:
            cls._invalid_completed_result(
                "Desktop Beeper returned a different responder task id",
                may_have_started=outcome_may_have_started,
            )
        return thread_id, str(result.get("host_id") or "").strip()

    @classmethod
    def _responder_result(
        cls,
        result: dict[str, Any],
        *,
        expected_responder_thread_id: str,
    ) -> tuple[str, str, str]:
        if set(result) != {
            "responder_thread_id",
            "responder_host_id",
            "responder_turn_id",
            "final_answer",
        }:
            cls._invalid_completed_result(
                "Desktop Beeper returned a non-canonical Responder result",
                may_have_started=True,
            )
        responder_thread_id = str(result.get("responder_thread_id") or "").strip()
        if not looks_like_thread_id(responder_thread_id):
            cls._invalid_completed_result(
                "Desktop Beeper returned an invalid Responder task id",
                may_have_started=True,
            )
        if responder_thread_id != expected_responder_thread_id:
            cls._invalid_completed_result(
                "Desktop Beeper returned a different Responder task id",
                may_have_started=True,
            )
        responder_turn_id = str(result.get("responder_turn_id") or "").strip()
        if responder_turn_id:
            cls._invalid_completed_result(
                "Final Callback cannot attest a Responder turn id",
                may_have_started=True,
            )
        return (
            responder_thread_id,
            responder_turn_id,
            str(result.get("responder_host_id") or "").strip(),
        )

    def bind_thread(
        self,
        thread_id: str,
        name: str,
        *,
        request_key: str,
        expected_project_id: str = "",
        expected_host_id: str = "",
        catalog_snapshot_id: str = "",
        selection_proof: str = "",
        excluded_thread_ids: Iterable[str] = (),
    ) -> ResponderActivation:
        candidate = thread_id.strip()
        if not looks_like_thread_id(candidate):
            raise BeeperError("invalid Codex task id")
        exclusions = sorted(
            {
                value
                for item in excluded_thread_ids
                if looks_like_thread_id(value := str(item).strip())
            }
        )
        result = self._submit_and_wait(
            "inspect_thread",
            {
                "responder_thread_id": candidate,
                "display_name": name.strip(),
                "catalog_snapshot_id": catalog_snapshot_id.strip(),
                "expected_project_id": expected_project_id.strip(),
                "expected_host_id": expected_host_id.strip(),
                "selection_proof": selection_proof.strip(),
                "excluded_thread_ids": exclusions,
            },
            request_key=request_key,
            completed_may_have_started=False,
        )
        resolved, host_id = self._thread_result(
            result,
            expected_thread_id=candidate,
            outcome_may_have_started=False,
        )
        expected_project = expected_project_id.strip()
        expected_host = expected_host_id.strip()
        if expected_project and str(result.get("project_id") or "").strip() != expected_project:
            self._invalid_completed_result(
                "Desktop Beeper returned a different responder project id",
                may_have_started=False,
            )
        if expected_host and host_id != expected_host:
            self._invalid_completed_result(
                "Desktop Beeper returned a different responder host id",
                may_have_started=False,
            )
        return ResponderActivation(
            resolved,
            responder_host_id=host_id,
            operation_receipt=str(result.get("operation_receipt") or "").strip(),
        )

    @classmethod
    def _catalog_result(
        cls,
        result: dict[str, Any],
        *,
        expected_thread_ids: set[str] | None,
        include_archived: bool,
        limit: int,
        excluded_thread_ids: set[str] | None = None,
        require_snapshot_id: bool = False,
        reject_project_paths: bool = False,
    ) -> DesktopTaskCatalog:
        if result.get("catalog_version") != 1:
            cls._invalid_completed_result(
                "Desktop Beeper returned an unsupported task catalog version",
                may_have_started=False,
            )
        if result.get("include_archived") is not include_archived:
            cls._invalid_completed_result(
                "Desktop Beeper returned a task catalog for the wrong archive mode",
                may_have_started=False,
            )
        raw_projects = result.get("projects")
        raw_tasks = result.get("tasks")
        if not isinstance(raw_projects, list) or not isinstance(raw_tasks, list):
            cls._invalid_completed_result(
                "Desktop Beeper task catalog has invalid collections",
                may_have_started=False,
            )
        if len(raw_projects) > limit or len(raw_tasks) > limit:
            cls._invalid_completed_result(
                "Desktop Beeper task catalog exceeds its requested bound",
                may_have_started=False,
            )

        projects: list[DesktopProjectSummary] = []
        project_ids: set[str] = set()
        for raw in raw_projects:
            if not isinstance(raw, dict):
                cls._invalid_completed_result(
                    "Desktop Beeper task catalog contains an invalid project",
                    may_have_started=False,
                )
            project_id = str(raw.get("project_id") or "").strip()
            label = str(raw.get("label") or "").strip()
            root = str(raw.get("root") or "").strip()
            if (
                not project_id
                or len(project_id) > 200
                or not label
                or len(label) > 160
                or (not reject_project_paths and not root)
                or len(root) > 1024
                or project_id in project_ids
                or (
                    reject_project_paths
                    and any(key in raw for key in {"root", "path", "project_root"})
                )
            ):
                cls._invalid_completed_result(
                    "Desktop Beeper task catalog contains invalid project metadata",
                    may_have_started=False,
                )
            project_ids.add(project_id)
            projects.append(
                DesktopProjectSummary(
                    project_id=project_id,
                    label=label,
                    root=root,
                    host_id=str(raw.get("host_id") or "").strip()[:200],
                    kind=str(raw.get("kind") or "").strip()[:40] or "local",
                )
            )

        tasks: list[DesktopTaskSummary] = []
        seen_threads: set[str] = set()
        for raw in raw_tasks:
            if not isinstance(raw, dict):
                cls._invalid_completed_result(
                    "Desktop Beeper task catalog contains an invalid task",
                    may_have_started=False,
                )
            thread_id = str(raw.get("thread_id") or "").strip()
            title = str(raw.get("title") or "").strip()
            archived = raw.get("archived")
            if (
                not looks_like_thread_id(thread_id)
                or thread_id in seen_threads
                or not title
                or len(title) > 240
                or not isinstance(archived, bool)
                or (archived and not include_archived)
                or (expected_thread_ids is not None and thread_id not in expected_thread_ids)
                or (
                    excluded_thread_ids is not None
                    and thread_id in excluded_thread_ids
                )
            ):
                cls._invalid_completed_result(
                    "Desktop Beeper task catalog contains invalid task metadata",
                    may_have_started=False,
                )
            seen_threads.add(thread_id)
            try:
                updated_at = float(raw.get("updated_at", 0) or 0)
            except (TypeError, ValueError):
                cls._invalid_completed_result(
                    "Desktop Beeper task catalog has an invalid task timestamp",
                    may_have_started=False,
                )
            tasks.append(
                DesktopTaskSummary(
                    thread_id=thread_id,
                    title=title,
                    project_id=str(raw.get("project_id") or "").strip()[:200],
                    host_id=str(raw.get("host_id") or "").strip()[:200],
                    status=str(raw.get("status") or "").strip()[:80],
                    archived=archived,
                    updated_at=max(0.0, updated_at),
                    kind=str(raw.get("kind") or "codex").strip()[:40],
                    selection_proof=str(raw.get("selection_proof") or "").strip(),
                )
            )

        if any(item.project_id not in project_ids for item in tasks):
            cls._invalid_completed_result(
                "Desktop Beeper task catalog contains an unknown project reference",
                may_have_started=False,
            )

        truncated = result.get("truncated")
        if not isinstance(truncated, bool):
            cls._invalid_completed_result(
                "Desktop Beeper task catalog has an invalid truncation flag",
                may_have_started=False,
            )
        snapshot_id = str(result.get("snapshot_id") or "").strip()
        if require_snapshot_id and _RECEIPT_PATTERN.fullmatch(snapshot_id) is None:
            cls._invalid_completed_result(
                "Desktop Beeper task catalog has an invalid snapshot id",
                may_have_started=False,
            )
        raw_snapshot_expires_at = result.get("snapshot_expires_at", 0.0)
        if type(raw_snapshot_expires_at) not in {int, float}:
            cls._invalid_completed_result(
                "Desktop Beeper task catalog has an invalid snapshot expiry",
                may_have_started=False,
            )
        snapshot_expires_at = float(raw_snapshot_expires_at)
        if require_snapshot_id and (
            not math.isfinite(snapshot_expires_at)
            or snapshot_expires_at <= time.time()
        ):
            cls._invalid_completed_result(
                "Desktop Beeper task catalog snapshot is already expired",
                may_have_started=False,
            )
        return DesktopTaskCatalog(
            projects=tuple(projects),
            tasks=tuple(tasks),
            include_archived=include_archived,
            truncated=truncated,
            snapshot_id=snapshot_id,
            snapshot_expires_at=snapshot_expires_at,
        )

    def list_task_catalog(
        self,
        *,
        visible_thread_ids: Iterable[str] | None,
        include_archived: bool,
        request_key: str,
        limit: int = DESKTOP_TASK_CATALOG_LIMIT,
    ) -> DesktopTaskCatalog:
        bounded_limit = max(1, min(int(limit), DESKTOP_TASK_CATALOG_LIMIT))
        expected: set[str] | None
        if visible_thread_ids is None:
            expected = None
            visibility = "all"
            exact_ids: list[str] = []
        else:
            expected = {
                candidate
                for item in visible_thread_ids
                if looks_like_thread_id(candidate := str(item).strip())
            }
            if len(expected) > 20:
                raise BeeperError("exact-scope task catalog exceeds 20 task ids")
            visibility = "exact"
            exact_ids = sorted(expected)
        result = self._submit_and_wait(
            "list_task_catalog",
            {
                "visibility": visibility,
                "thread_ids": exact_ids,
                "include_archived": bool(include_archived),
                "limit": bounded_limit,
            },
            request_key=request_key,
            completed_may_have_started=False,
        )
        catalog = self._catalog_result(
            result,
            expected_thread_ids=expected,
            include_archived=bool(include_archived),
            limit=bounded_limit,
        )
        beeper_thread_id = self.queue.status().beeper_thread_id
        if beeper_thread_id and any(
            item.thread_id == beeper_thread_id for item in catalog.tasks
        ):
            self._invalid_completed_result(
                "Desktop Beeper task catalog exposed the dedicated Beeper task",
                may_have_started=False,
            )
        return catalog

    @staticmethod
    def _transport_prompt(
        user_text: str,
        *,
        local_images: list[Path] | None,
        local_audio: list[Path] | None,
        additional_context: dict[str, str] | None,
    ) -> str:
        context = additional_context or {}
        unsupported = set(context) - {"transport_attachments"}
        if unsupported:
            raise BeeperError(
                "bridge context accepts only a transport attachment manifest"
            )
        entries: list[str] = []
        for kind, paths in (("image", local_images or []), ("audio", local_audio or [])):
            for path in paths:
                entries.append(f"- {kind}: {path.resolve()}")
        manifest = str(context.get("transport_attachments") or "").strip()
        if manifest:
            entries.append(manifest)
        if not entries:
            return user_text
        return (
            user_text
            + "\n\n<feishu_transport_attachments>\n"
            + "这些是本轮飞书消息携带的本机只读附件引用，仅按用户任务需要读取：\n"
            + "\n".join(entries)
            + "\n</feishu_transport_attachments>"
        )

    def alert_responder(
        self,
        session: dict[str, Any],
        name: str,
        user_text: str,
        *,
        client_message_id: str,
        local_images: list[Path] | None = None,
        local_audio: list[Path] | None = None,
        additional_context: dict[str, str] | None = None,
        on_turn_started: Callable[[ResponderTurnHandle], None] | None = None,
    ) -> ResponderAnswer:
        del name
        thread_id = str(session.get("thread_id") or "").strip()
        if not thread_id:
            raise ResponderNotBound("Feishu scope is not bound to a Codex task")
        if not looks_like_thread_id(thread_id):
            raise BeeperError("bound Codex task id is invalid")
        self._validate_responder(thread_id)
        prompt = self._transport_prompt(
            user_text,
            local_images=local_images,
            local_audio=local_audio,
            additional_context=additional_context,
        )

        def submitted(request_id: str) -> None:
            if on_turn_started is not None:
                # A Beeper claim establishes conservative may-have-started
                # state, but the Final Callback transport has no product turn ID.
                on_turn_started(
                    ResponderTurnHandle(
                        responder_thread_id=thread_id,
                        responder_turn_id="",
                    )
                )

        result = self._submit_and_wait(
            "send_message_to_thread",
            {
                "responder_thread_id": thread_id,
                "responder_host_id": str(session.get("host_id") or "").strip(),
                "prompt": prompt,
                "source": "feishu",
                "client_message_id": client_message_id,
            },
            request_key=client_message_id,
            completed_may_have_started=True,
            on_submitted=submitted,
        )
        resolved, responder_turn_id, responder_host_id = self._responder_result(
            result,
            expected_responder_thread_id=thread_id,
        )
        final_answer = result.get("final_answer")
        if not isinstance(final_answer, str) or not final_answer.strip():
            raise ResponderOutcomeUnknown(
                "Responder completed without an authoritative Final Callback answer"
            )
        return ResponderAnswer(
            # Whitespace is part of the authoritative final answer. Use a
            # trimmed view only for the non-empty validation above and forward
            # the original string unchanged.
            final_answer=final_answer,
            responder_thread_id=resolved,
            # The Final Callback proves only possession of its fenced capability.
            # It does not attest a product turn identity.
            responder_turn_id=responder_turn_id,
            responder_host_id=responder_host_id,
        )

    def steer(self, handle: ResponderTurnHandle, text: str, *, request_key: str) -> None:
        del handle, text, request_key
        raise BeeperError(
            "Codex Desktop Beeper has no fenced in-flight steer lane; no responder send was submitted"
        )

    @staticmethod
    def interrupt(handle: ResponderTurnHandle) -> NoReturn:
        del handle
        raise BeeperError(
            "Codex Desktop task beeper does not expose cross-task interruption; no stop was sent"
        )

    def maintenance(self) -> None:
        self.queue.cleanup()

    def close(self) -> None:
        # There is no child Codex process and therefore no writer to release.
        return


class BeeperClient(HistoricalBeeperClient):
    """Dial one fixed Desktop Beeper task through ``codex queue`` exactly once.

    The bounded local producer keeps its grant, queue, and registration in a
    namespace that cannot be mistaken for the retired Desktop producer.  The
    bridge puts the business payload only in the fenced Bridge queue.  The
    process argument list contains one fixed control prompt plus an opaque
    single-use page; it never contains the responder task or business text.
    """

    def __init__(
        self,
        config: BridgeConfig,
        queue: BeeperQueue,
        registration: dict[str, Any],
        *,
        runner: Callable[..., Any] | None = None,
        activator: Callable[[str], Any] | None = None,
    ) -> None:
        super().__init__(config, queue)
        self._registration = self._validated_registration(
            registration,
            verify_executable=True,
        )
        self._runner = runner or subprocess.run
        self._activator = activator or self._open_beeper_deep_link

    def _validate_responder(self, thread_id: str) -> None:
        if thread_id in set(self._exclusions()):
            raise ResponderUnavailable(
                "The selected Responder is a Beeper or retired Beeper",
                code="responder_not_found",
            )

    @staticmethod
    def _open_beeper_deep_link(uri: str) -> None:
        if os.name != "nt" or not hasattr(os, "startfile"):
            raise OSError("Codex Desktop deep-link activation is unavailable")
        os.startfile(uri)  # type: ignore[attr-defined]

    @staticmethod
    def _plain_metadata(value: Any, *, maximum: int) -> str:
        if not isinstance(value, str):
            raise ValueError("current producer metadata is invalid")
        candidate = value.strip()
        if (
            not candidate
            or len(candidate) > maximum
            or any(ord(character) < 32 or ord(character) == 127 for character in candidate)
        ):
            raise ValueError("current producer metadata is invalid")
        return candidate

    @classmethod
    def _validated_registration(
        cls,
        raw: Any,
        *,
        verify_executable: bool,
    ) -> _Registration:
        if not isinstance(raw, dict) or raw.get("valid") is not True:
            raise ValueError("current producer registration is unavailable")
        beeper_thread_id = cls._plain_metadata(raw.get("beeper_thread_id"), maximum=64)
        if not looks_like_thread_id(beeper_thread_id):
            raise ValueError("Beeper task id is invalid")
        raw_beeper_host_id = raw.get("beeper_host_id")
        if not isinstance(raw_beeper_host_id, str):
            raise ValueError("current producer host metadata is invalid")
        beeper_host_id = raw_beeper_host_id.strip()
        if len(beeper_host_id) > 256 or any(
            ord(character) < 32 or ord(character) == 127
            for character in beeper_host_id
        ):
            raise ValueError("current producer host metadata is invalid")
        version = cls._plain_metadata(raw.get("codex_version"), maximum=128)
        if any(ord(character) > 126 for character in version):
            raise ValueError("current Codex version metadata is invalid")
        raw_sha256 = cls._plain_metadata(raw.get("codex_exe_sha256"), maximum=64)
        if _SHA256_PATTERN.fullmatch(raw_sha256) is None:
            raise ValueError("current Codex executable digest is invalid")

        raw_path = cls._plain_metadata(raw.get("codex_exe_path"), maximum=2048)
        candidate = Path(raw_path)
        if not candidate.is_absolute() or candidate.name.casefold() != "codex.exe":
            raise ValueError("current Codex executable path is invalid")
        try:
            attributes = int(getattr(os.lstat(candidate), "st_file_attributes", 0))
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise ValueError("current Codex executable is unavailable") from exc
        if (
            candidate.is_symlink()
            or attributes & 0x400
            or not resolved.is_file()
            or os.path.normcase(os.path.normpath(str(candidate)))
            != os.path.normcase(os.path.normpath(str(resolved)))
        ):
            raise ValueError("current Codex executable path is not exact")

        expected_sha256 = raw_sha256.lower()
        if verify_executable and cls._file_sha256(resolved) != expected_sha256:
            raise ValueError("current Codex executable digest changed")
        return _Registration(
            beeper_thread_id=beeper_thread_id,
            beeper_host_id=beeper_host_id,
            codex_exe_path=resolved,
            codex_exe_sha256=expected_sha256,
            codex_version=version,
        )

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        try:
            with path.open("rb") as stream:
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
        except OSError as exc:
            raise ValueError("current Codex executable cannot be verified") from exc
        return digest.hexdigest()

    def _registration_matches(self, *, verify_executable: bool) -> bool:
        try:
            current = self._validated_registration(
                self.queue.registration(),
                verify_executable=verify_executable,
            )
        except (OSError, BeeperQueueProtocolError, TypeError, ValueError):
            return False
        return current == self._registration

    def is_alive(self) -> bool:
        return self._registration_matches(verify_executable=False)

    def connection_status(self) -> str:
        return "codex-queue"

    def state(self, status: BeeperQueueStatus | None = None) -> str:
        del status
        return (
            "beeper-registered-load-unobserved"
            if self.is_alive()
            else "beeper-unavailable"
        )

    def status(self) -> BeeperQueueStatus:
        current = self.queue.status()
        registered = self.is_alive()
        return BeeperQueueStatus(
            # Registration and executable integrity do not attest that the
            # Beeper task is currently loaded or able to claim a page.
            registered=registered,
            beeper_thread_id="",
            beeper_host_id="",
            pending=current.pending,
            claimed=current.claimed,
            dial_generation=current.dial_generation,
            dial_inflight=current.dial_inflight,
            dial_lease_remaining_seconds=current.dial_lease_remaining_seconds,
        )

    @staticmethod
    def _page(reservation: Any, request_id: str) -> str:
        if not isinstance(reservation, dict):
            raise ValueError("current dial reservation is invalid")
        if str(reservation.get("request_id") or "").strip() != request_id:
            raise ValueError("current dial reservation identity changed")
        if str(reservation.get("status") or "").strip() != "reserved":
            raise ValueError("current dial reservation was not consumed")
        raw_page = reservation.get("page")
        if raw_page is None:
            raw_page = reservation.get("page_id")
        if not isinstance(raw_page, str):
            raise ValueError("current dial page is invalid")
        page = raw_page.strip()
        if _PAGE_PATTERN.fullmatch(page) is None:
            raise ValueError("current dial page is invalid")
        return page

    def _exclusions(self) -> tuple[str, ...]:
        try:
            raw = self.queue.excluded_thread_ids()
        except (OSError, BeeperQueueProtocolError, TypeError, ValueError) as exc:
            raise BeeperQueueUnavailable(
                "Beeper exclusion metadata is unavailable"
            ) from exc
        if not isinstance(raw, (list, tuple)):
            raise BeeperQueueUnavailable(
                "Beeper exclusion metadata is invalid"
            )
        values: list[str] = []
        for item in raw:
            candidate = str(item or "").strip()
            if (
                _THREAD_ID_PATTERN.fullmatch(candidate) is None
                or candidate in values
            ):
                raise BeeperQueueUnavailable(
                    "Beeper exclusion metadata is invalid"
                )
            values.append(candidate)
        if self._registration.beeper_thread_id not in values:
            raise BeeperQueueUnavailable(
                "Beeper exclusion metadata omits its Beeper"
            )
        return tuple(sorted(values))

    @staticmethod
    def _display_name(value: str) -> str:
        normalized = "".join(
            " " if ord(character) < 32 or ord(character) == 127 else character
            for character in str(value)
        ).strip()
        return normalized[:160]

    def list_task_catalog(
        self,
        *,
        visible_thread_ids: Iterable[str] | None,
        include_archived: bool,
        request_key: str,
        limit: int = DESKTOP_TASK_CATALOG_LIMIT,
    ) -> DesktopTaskCatalog:
        if include_archived is not False:
            raise BeeperError(
                "Current task catalog currently permits only non-archived tasks",
                code="unsupported_readonly_request",
                may_have_started=False,
                retryable=False,
            )
        if type(limit) is not int:
            raise BeeperError(
                "Current task catalog limit is invalid",
                code="invalid_readonly_request",
                may_have_started=False,
                retryable=False,
            )
        bounded_limit = max(1, min(limit, DESKTOP_TASK_CATALOG_LIMIT))
        exclusions = self._exclusions()
        excluded = set(exclusions)
        expected: set[str] | None
        if visible_thread_ids is None:
            expected = None
            visibility = "all"
            exact_ids: list[str] = []
        else:
            requested: set[str] = set()
            for item in visible_thread_ids:
                candidate = str(item or "").strip()
                if _THREAD_ID_PATTERN.fullmatch(candidate) is None:
                    raise BeeperError(
                        "Current exact-scope task catalog contains an invalid task id",
                        code="invalid_readonly_request",
                        may_have_started=False,
                        retryable=False,
                    )
                requested.add(candidate)
            if len(requested) > 20:
                raise BeeperError(
                    "exact-scope task catalog exceeds 20 task ids",
                    code="invalid_readonly_request",
                    may_have_started=False,
                    retryable=False,
                )
            expected = requested - excluded
            visibility = "exact"
            exact_ids = sorted(expected)
        result = self._submit_and_wait(
            "list_task_catalog",
            {
                "catalog_version": 1,
                "visibility": visibility,
                "thread_ids": exact_ids,
                "include_archived": False,
                "limit": bounded_limit,
                "excluded_thread_ids": list(exclusions),
            },
            request_key=request_key,
            completed_may_have_started=False,
        )
        if set(result) != {
            "catalog_version",
            "snapshot_id",
            "snapshot_expires_at",
            "include_archived",
            "truncated",
            "projects",
            "tasks",
        }:
            self._invalid_completed_result(
                "Current task catalog result contains unsupported fields",
                may_have_started=False,
            )
        raw_projects = result.get("projects")
        raw_tasks = result.get("tasks")
        if not isinstance(raw_projects, list) or any(
            not isinstance(item, dict)
            or set(item) != {"project_id", "label", "host_id", "kind"}
            for item in raw_projects
        ):
            self._invalid_completed_result(
                "Current task catalog project fields are invalid",
                may_have_started=False,
            )
        if not isinstance(raw_tasks, list) or any(
            not isinstance(item, dict)
            or set(item)
            != {
                "thread_id",
                "title",
                "project_id",
                "host_id",
                "kind",
                "status",
                "archived",
                "updated_at",
                "selection_proof",
            }
            for item in raw_tasks
        ):
            self._invalid_completed_result(
                "Current task catalog task fields are invalid",
                may_have_started=False,
            )
        catalog = self._catalog_result(
            result,
            expected_thread_ids=expected,
            include_archived=False,
            limit=bounded_limit,
            excluded_thread_ids=excluded,
            require_snapshot_id=True,
            reject_project_paths=True,
        )
        if any(
            _THREAD_ID_PATTERN.fullmatch(item.thread_id) is None
            or not item.host_id
            or item.kind != "codex"
            or re.fullmatch(r"[a-f0-9]{64}", item.selection_proof) is None
            for item in catalog.tasks
        ):
            self._invalid_completed_result(
                "Current task catalog contains an invalid exact task identity",
                may_have_started=False,
            )
        if {
            item.project_id for item in catalog.projects
        } != {item.project_id for item in catalog.tasks}:
            self._invalid_completed_result(
                "Current task catalog exposed an unrelated project",
                may_have_started=False,
            )
        return catalog

    def bind_thread(
        self,
        thread_id: str,
        name: str,
        *,
        request_key: str,
        expected_project_id: str = "",
        expected_host_id: str = "",
        catalog_snapshot_id: str = "",
        selection_proof: str = "",
        excluded_thread_ids: Iterable[str] = (),
    ) -> ResponderActivation:
        candidate = thread_id.strip()
        project_id = expected_project_id.strip()
        host_id = expected_host_id.strip()
        snapshot_id = catalog_snapshot_id.strip()
        proof = selection_proof.strip()
        authoritative_exclusions = self._exclusions()
        # Callers cannot weaken or widen the controller-owned deny set.  The
        # optional argument exists only for signature compatibility with the
        # generic client and must either be empty or exactly authoritative.
        supplied_exclusions = tuple(
            sorted({str(item or "").strip() for item in excluded_thread_ids})
        )
        if supplied_exclusions and supplied_exclusions != authoritative_exclusions:
            raise BeeperError(
                "Current responder exclusions do not match current authority",
                code="invalid_readonly_request",
                may_have_started=False,
                retryable=False,
            )
        if (
            _THREAD_ID_PATTERN.fullmatch(candidate) is None
            or candidate in authoritative_exclusions
            or not project_id
            or len(project_id) > 200
            or any(ord(character) < 32 or ord(character) == 127 for character in project_id)
            or not host_id
            or len(host_id) > 200
            or any(ord(character) < 32 or ord(character) == 127 for character in host_id)
            or _RECEIPT_PATTERN.fullmatch(snapshot_id) is None
            or re.fullmatch(r"[a-f0-9]{64}", proof) is None
        ):
            raise BeeperError(
                "Current responder inspection constraints are invalid",
                code="invalid_readonly_request",
                may_have_started=False,
                retryable=False,
            )
        result = self._submit_and_wait(
            "inspect_thread",
            {
                "responder_thread_id": candidate,
                "display_name": self._display_name(name),
                "catalog_snapshot_id": snapshot_id,
                "expected_project_id": project_id,
                "expected_host_id": host_id,
                "selection_proof": proof,
                "excluded_thread_ids": list(authoritative_exclusions),
            },
            request_key=request_key,
            completed_may_have_started=False,
        )
        if set(result) != {
            "thread_id",
            "project_id",
            "host_id",
            "archived",
            "catalog_snapshot_id",
            "operation_receipt",
        }:
            self._invalid_completed_result(
                "Current responder inspection result contains unsupported fields",
                may_have_started=False,
            )
        resolved, resolved_host = self._thread_result(
            result,
            expected_thread_id=candidate,
            outcome_may_have_started=False,
        )
        receipt = str(result.get("operation_receipt") or "").strip()
        if (
            str(result.get("project_id") or "").strip() != project_id
            or resolved_host != host_id
            or result.get("archived") is not False
            or str(result.get("catalog_snapshot_id") or "").strip() != snapshot_id
            or _RECEIPT_PATTERN.fullmatch(receipt) is None
        ):
            self._invalid_completed_result(
                "Current responder inspection did not match its catalog snapshot",
                may_have_started=False,
            )
        return ResponderActivation(
            resolved,
            responder_host_id=resolved_host,
            operation_receipt=receipt,
        )

    @staticmethod
    def _terminal_identity_matches(
        response: Any,
        *,
        request_id: str,
        payload: dict[str, Any],
        operation: str = "send_message_to_thread",
    ) -> bool:
        if not isinstance(response, dict):
            return False
        if str(response.get("request_id") or "").strip() != request_id:
            return False
        if str(response.get("operation") or "").strip() != operation:
            return False
        if operation in READONLY_OPERATIONS:
            return True
        responder_thread_id = str(payload.get("responder_thread_id") or "").strip()
        returned_thread_id = str(response.get("responder_thread_id") or "").strip()
        returned_host = str(response.get("responder_host_id") or "").strip()
        if not returned_thread_id:
            # A duplicate request reads the authoritative queue receipt rather
            # than the page-decorated finish payload.  Its responder-sealed result
            # carries the same exact responder identity without the outer fields.
            result = response.get("result")
            if isinstance(result, dict):
                returned_thread_id = str(
                    result.get("responder_thread_id") or ""
                ).strip()
                returned_host = str(result.get("responder_host_id") or "").strip()
        if returned_thread_id != responder_thread_id:
            return False
        requested_host = str(payload.get("responder_host_id") or "").strip()
        return not requested_host or returned_host == requested_host

    def _accepted_terminal(
        self,
        response: Any,
        *,
        request_id: str,
        payload: dict[str, Any],
        operation: str = "send_message_to_thread",
    ) -> dict[str, Any]:
        if not self._terminal_identity_matches(
            response,
            request_id=request_id,
            payload=payload,
            operation=operation,
        ):
            if operation in READONLY_OPERATIONS:
                self._invalid_completed_result(
                    "Beeper returned a mismatched read-only terminal",
                    may_have_started=False,
                )
            raise ResponderOutcomeUnknown(
                "Beeper did not publish the fenced Final Callback result"
            )
        if operation == "send_message_to_thread":
            if (
                response.get("status") != "completed"
                or response.get("final_callback_source") != "final_callback"
            ):
                raise ResponderOutcomeUnknown(
                    "Beeper did not publish the fenced Final Callback result"
                )
            return self._result(
                response,
                completed_may_have_started=True,
                expected_operation=operation,
            )
        if (
            response.get("status") == "completed"
            and response.get("final_callback_source") != "not_applicable"
        ):
            self._invalid_completed_result(
                "Beeper read-only completion has an invalid source",
                may_have_started=False,
            )
        try:
            return self._result(
                response,
                completed_may_have_started=False,
                expected_operation=operation,
            )
        except BeeperError as exc:
            # Read-only catalog/inspection never contacts a business responder and
            # never creates a retry generation.  Even malformed or unknown
            # controller outcomes remain a terminal safe failure.
            raise BeeperError(
                str(exc),
                code=exc.code,
                may_have_started=False,
                retryable=False,
            ) from exc

    def _decoded_terminal(
        self,
        response: Any,
        *,
        request_id: str,
        payload: dict[str, Any],
        operation: str = "send_message_to_thread",
    ) -> dict[str, Any]:
        unclaimed_code = self._unclaimed_failure_code(response)
        if unclaimed_code:
            raise BeeperNotLoaded(
                "The registered Beeper did not claim the queued request",
                code=unclaimed_code,
            )
        return self._accepted_terminal(
            response,
            request_id=request_id,
            payload=payload,
            operation=operation,
        )

    def _record_unknown(
        self,
        page: str,
        *,
        operation: str = "send_message_to_thread",
    ) -> None:
        try:
            self.queue.fail_page(
                page,
                code=(
                    "readonly_result_unknown"
                    if operation in READONLY_OPERATIONS
                    else "responder_result_unknown"
                ),
                may_have_started=operation not in READONLY_OPERATIONS,
            )
        except Exception:
            # The single-use reservation is already consumed.  A failed
            # auxiliary terminal write must never reopen or respawn it.
            return

    def _finish_existing_readonly(self, request_id: str) -> dict[str, Any]:
        try:
            return self.queue.finish_readonly_request(request_id, 0)
        except (OSError, BeeperQueueProtocolError, TypeError, ValueError) as exc:
            raise BeeperError(
                "Current existing read-only result could not be handed off safely",
                code="readonly_result_unknown",
                may_have_started=False,
                retryable=False,
            ) from exc

    @staticmethod
    def _unclaimed_failure_code(response: Any) -> str:
        if not isinstance(response, dict) or response.get("status") != "failed":
            return ""
        error = response.get("error")
        details = error if isinstance(error, dict) else {}
        code = str(details.get("code") or "").strip()
        if (
            code in BeeperNotLoaded.CODES
            and details.get("may_have_started") is False
        ):
            return code
        return ""

    def _seal_unclaimed_or_continue(
        self,
        page: str,
        *,
        code: str,
        request_id: str,
        payload: dict[str, Any],
        operation: str = "send_message_to_thread",
    ) -> dict[str, Any] | None:
        sealed = self.queue.fail_page_if_unclaimed(page, code)
        if sealed is None:
            return None
        return self._decoded_terminal(
            sealed,
            request_id=request_id,
            payload=payload,
            operation=operation,
        )

    def _submit_and_wait(
        self,
        operation: str,
        payload: dict[str, Any],
        *,
        request_key: str,
        completed_may_have_started: bool,
        on_submitted: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        del completed_may_have_started
        if operation not in {
            "send_message_to_thread",
            *READONLY_OPERATIONS,
        }:
            raise BeeperQueueUnavailable(
                "Beeper accepts only ordinary sends and bounded read-only setup"
            )
        readonly = operation in READONLY_OPERATIONS

        with _QUEUE_SERIAL_LOCK:
            try:
                request_id = self.queue.submit(
                    operation,
                    payload,
                    idempotency_key=self._request_key(request_key),
                )
            except (OSError, BeeperQueueProtocolError, TypeError, ValueError) as exc:
                if readonly:
                    raise BeeperError(
                        "Current read-only queue state is not safe to dispatch",
                        code="readonly_admission_failed",
                        may_have_started=False,
                        retryable=False,
                    ) from exc
                raise ResponderOutcomeUnknown(
                    "Beeper queue state is not safe to dispatch"
                ) from exc

            existing = self.queue.response(request_id)
            if existing is not None:
                if readonly:
                    existing = self._finish_existing_readonly(request_id)
                return self._decoded_terminal(
                    existing,
                    request_id=request_id,
                    payload=payload,
                    operation=operation,
                )

            try:
                reservation = self.queue.reserve_exact(request_id)
                page = self._page(reservation, request_id)
            except (OSError, BeeperQueueProtocolError, TypeError, ValueError) as exc:
                existing = self.queue.response(request_id)
                if existing is not None:
                    if readonly:
                        existing = self._finish_existing_readonly(request_id)
                    return self._decoded_terminal(
                        existing,
                        request_id=request_id,
                        payload=payload,
                        operation=operation,
                    )
                if readonly:
                    raise BeeperError(
                        "Current read-only grant was already consumed or unavailable",
                        code="readonly_admission_failed",
                        may_have_started=False,
                        retryable=False,
                    ) from exc
                raise ResponderOutcomeUnknown(
                    "Beeper grant was already consumed or unavailable"
                ) from exc

            try:
                if not self._registration_matches(verify_executable=True):
                    raise ValueError("current producer identity changed")
                argv = [
                    str(self._registration.codex_exe_path),
                    "queue",
                    "--thread",
                    self._registration.beeper_thread_id,
                    "--message",
                    (
                        QUEUE_READONLY_CONTROL_PROMPT
                        if readonly
                        else QUEUE_CONTROL_PROMPT
                    )
                    + page,
                ]
                completed = self._runner(
                    argv,
                    shell=False,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=QUEUE_SPAWN_TIMEOUT_SECONDS,
                    check=False,
                )
                if int(getattr(completed, "returncode", -1)) != 0:
                    raise RuntimeError("current queue command was not accepted")
                claim_state = self.queue.wait_for_beeper_claim(
                    page,
                    BEEPER_LOAD_GRACE_SECONDS,
                )
                if claim_state == "reserved":
                    uri = (
                        BEEPER_DEEP_LINK_PREFIX
                        + self._registration.beeper_thread_id
                    )
                    try:
                        self._activator(uri)
                    except Exception:
                        accepted = self._seal_unclaimed_or_continue(
                            page,
                            code="beeper_load_assist_failed",
                            request_id=request_id,
                            payload=payload,
                            operation=operation,
                        )
                        if accepted is not None:
                            return accepted
                        raise
                    claim_state = self.queue.wait_for_beeper_claim(
                        page,
                        BEEPER_POST_LOAD_CLAIM_SECONDS,
                    )
                if claim_state == "reserved":
                    accepted = self._seal_unclaimed_or_continue(
                        page,
                        code="beeper_claim_timeout",
                        request_id=request_id,
                        payload=payload,
                        operation=operation,
                    )
                    if accepted is not None:
                        return accepted
                    claim_state = self.queue.wait_for_beeper_claim(
                        page,
                        0,
                    )
                if on_submitted is not None and claim_state in {
                    "claiming",
                    "claimed_armed",
                    "claimed_readonly",
                    "finishing",
                }:
                    on_submitted(request_id)
                if readonly:
                    response = self.queue.finish_readonly(
                        page,
                        self.config.beeper_timeout_seconds,
                    )
                else:
                    response = self.queue.finish_final_callback(
                        page,
                        self.config.beeper_timeout_seconds,
                    )
            except BeeperNotLoaded:
                raise
            except Exception as exc:
                self._record_unknown(page, operation=operation)
                if readonly:
                    raise BeeperError(
                        "Beeper read-only dial did not finish reliably",
                        code="readonly_result_unknown",
                        may_have_started=False,
                        retryable=False,
                    ) from exc
                raise ResponderOutcomeUnknown(
                    "Beeper dial may have started but did not finish reliably"
                ) from exc

            if (
                response is None
                or not isinstance(response, dict)
                or response.get("terminal") is not True
            ):
                accepted = self._seal_unclaimed_or_continue(
                    page,
                    code="beeper_claim_timeout",
                    request_id=request_id,
                    payload=payload,
                    operation=operation,
                )
                if accepted is not None:
                    return accepted
                self._record_unknown(page, operation=operation)
                if readonly:
                    raise BeeperError(
                        "Beeper timed out without a read-only result",
                        code="readonly_result_unknown",
                        may_have_started=False,
                        retryable=False,
                    )
                raise ResponderOutcomeUnknown(
                    "Beeper timed out without a Final Callback"
                )
            decoded = self._decoded_terminal(
                response,
                request_id=request_id,
                payload=payload,
                operation=operation,
            )
            # A very fast Final Callback can publish the current reservation's
            # terminal receipt before the claim-state poll observes an
            # intermediate state.  Validate that receipt first, then record the
            # conservative may-have-started boundary exactly once.  Responses
            # found before this call reserved a new page never reach here.
            if on_submitted is not None and claim_state == "terminal":
                on_submitted(request_id)
            return decoded


def create_beeper_client(config: BridgeConfig) -> HistoricalBeeperClient:
    registration_file = (
        config.runtime_dir / QUEUE_ROOT_NAME / "registration.json"
    )
    if registration_file.is_file() and not registration_file.is_symlink():
        try:
            queue = BeeperQueue(
                config.runtime_dir,
                root_name=QUEUE_ROOT_NAME,
                claim_ttl_seconds=config.beeper_claim_ttl_seconds,
                retention_hours=config.beeper_retention_hours,
                dial_lease_ttl_seconds=config.beeper_dial_ttl_seconds,
                grace_wait_max_seconds=config.beeper_grace_max_seconds,
            )
            registration = queue.registration()
            return BeeperClient(config, queue, registration)
        except (OSError, BeeperQueueProtocolError, TypeError, ValueError):
            # Invalid or stale current metadata never changes the default
            # historical hold behavior and never promotes another executable.
            pass
    return HistoricalBeeperClient(config)
