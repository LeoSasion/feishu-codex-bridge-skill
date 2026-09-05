"""On-demand read-only App Server transport for Operator metadata lanes."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import time
import uuid
from typing import Any

from .config import OperatorConfig
from .app_server import AppServerError, AppServerSession
from .beeper_relay import discover_codex_executable, looks_like_thread_id


DESKTOP_TASK_CATALOG_LIMIT = 50


class CatalogError(AppServerError):
    def __init__(self, message: str, *, code: str = "catalog_unavailable") -> None:
        super().__init__(message)
        self.code = code
        self.may_have_started = False
        self.retryable = False


@dataclass(frozen=True)
class DesktopProjectSummary:
    project_id: str
    label: str
    host_id: str = "local"
    kind: str = "local"


@dataclass(frozen=True)
class DesktopTaskSummary:
    thread_id: str
    title: str
    project_id: str
    host_id: str
    kind: str
    status: str
    archived: bool
    updated_at: float
    snapshot_fingerprint: str


@dataclass(frozen=True)
class DesktopTaskCatalog:
    projects: tuple[DesktopProjectSummary, ...]
    tasks: tuple[DesktopTaskSummary, ...]
    include_archived: bool
    truncated: bool
    snapshot_id: str
    snapshot_expires_at: float


@dataclass(frozen=True)
class ResponderInspection:
    responder_thread_id: str
    responder_host_id: str
    operation_receipt: str


class AppServerCatalog:
    """List and inspect stored tasks without starting or resuming a thread."""

    def __init__(self, config: OperatorConfig, *, executable: Path | None = None) -> None:
        self.config = config
        self._executable = executable

    @property
    def executable(self) -> Path:
        if self._executable is None:
            self._executable = discover_codex_executable(self.config.codex_executable)
        return self._executable

    @staticmethod
    def _project_identity(thread: dict[str, Any]) -> tuple[str, str]:
        project_id = str(thread.get("projectId") or "").strip()
        cwd = str(thread.get("cwd") or "").strip()
        if not project_id:
            project_id = "local-" + hashlib.sha256(cwd.encode("utf-8")).hexdigest()[:16]
        label = Path(cwd).name.strip() if cwd else "本机任务"
        return project_id, label or "本机任务"

    @staticmethod
    def _status(thread: dict[str, Any]) -> str:
        value = thread.get("status")
        if isinstance(value, dict):
            return str(value.get("type") or "unknown")[:80]
        return str(value or "unknown")[:80]

    @staticmethod
    def _snapshot_fingerprint(snapshot_id: str, thread: dict[str, Any], project_id: str) -> str:
        material = "\n".join(
            [
                snapshot_id,
                str(thread.get("id") or ""),
                project_id,
                str(thread.get("updatedAt") or 0),
            ]
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def list_task_catalog(
        self,
        *,
        visible_thread_ids: list[str] | None,
        include_archived: bool,
        limit: int = DESKTOP_TASK_CATALOG_LIMIT,
    ) -> DesktopTaskCatalog:
        if include_archived:
            raise CatalogError("archived tasks are outside /init")
        limit = max(1, min(int(limit), DESKTOP_TASK_CATALOG_LIMIT))
        with AppServerSession(self.executable, self.config.app_server_timeout_seconds, error_type=CatalogError) as api:
            result = api.request(
                "thread/list",
                {
                    "archived": False,
                    "limit": limit,
                    "sortKey": "updated_at",
                    "sortDirection": "desc",
                    "sourceKinds": ["cli", "vscode", "appServer"],
                    "useStateDbOnly": True,
                },
            )
        if not isinstance(result, dict) or not isinstance(result.get("data"), list):
            raise CatalogError("Desktop task catalog is invalid")
        snapshot_id = uuid.uuid4().hex
        visible = (
            {str(item).strip().lower() for item in visible_thread_ids}
            if visible_thread_ids is not None
            else None
        )
        projects: dict[str, DesktopProjectSummary] = {}
        tasks: list[DesktopTaskSummary] = []
        for raw in result["data"]:
            if not isinstance(raw, dict) or raw.get("ephemeral") is True:
                continue
            if raw.get("parentThreadId") not in (None, ""):
                continue
            thread_id = str(raw.get("id") or "").strip().lower()
            if not looks_like_thread_id(thread_id):
                continue
            if thread_id == self.config.beeper_thread_id:
                continue
            if visible is not None and thread_id not in visible:
                continue
            project_id, project_label = self._project_identity(raw)
            projects.setdefault(
                project_id,
                DesktopProjectSummary(project_id=project_id, label=project_label),
            )
            title = str(raw.get("name") or "未命名任务").strip()[:240]
            tasks.append(
                DesktopTaskSummary(
                    thread_id=thread_id,
                    title=title or "未命名任务",
                    project_id=project_id,
                    host_id="local",
                    kind="codex",
                    status=self._status(raw),
                    archived=False,
                    updated_at=float(raw.get("updatedAt") or 0),
                    snapshot_fingerprint=self._snapshot_fingerprint(snapshot_id, raw, project_id),
                )
            )
        return DesktopTaskCatalog(
            projects=tuple(projects.values()),
            tasks=tuple(tasks),
            include_archived=False,
            truncated=bool(result.get("nextCursor")),
            snapshot_id=snapshot_id,
            snapshot_expires_at=time.time() + 600,
        )

    def inspect_thread(
        self,
        thread_id: str,
        *,
        expected_project_id: str,
        expected_host_id: str,
        catalog_snapshot_id: str,
        snapshot_fingerprint: str,
    ) -> ResponderInspection:
        candidate = str(thread_id or "").strip().lower()
        if (
            not looks_like_thread_id(candidate)
            or candidate == self.config.beeper_thread_id
            or expected_host_id != "local"
        ):
            raise CatalogError("selected Desktop task identity is invalid")
        with AppServerSession(self.executable, self.config.app_server_timeout_seconds, error_type=CatalogError) as api:
            result = api.request(
                "thread/read",
                {"threadId": candidate, "includeTurns": False},
            )
        thread = result.get("thread") if isinstance(result, dict) else None
        if not isinstance(thread, dict):
            raise CatalogError("selected Desktop task could not be read")
        project_id, _ = self._project_identity(thread)
        if (
            str(thread.get("id") or "").strip().lower() != candidate
            or thread.get("ephemeral") is True
            or project_id != expected_project_id
            or thread.get("turns") not in ([], None)
            or self._snapshot_fingerprint(catalog_snapshot_id, thread, project_id) != snapshot_fingerprint
        ):
            raise CatalogError("selected Desktop task changed after the catalog snapshot")
        receipt = hashlib.sha256(
            (catalog_snapshot_id + "\n" + candidate + "\n" + project_id).encode("utf-8")
        ).hexdigest()[:32]
        return ResponderInspection(candidate, "local", receipt)

    def connection_status(self) -> str:
        try:
            self.executable
            return "app-server-readonly"
        except Exception:
            return "unavailable"
