"""Desktop-router client for Feishu-to-Codex task-to-task delivery."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Any, Callable, Iterable, NoReturn

from .config import BridgeConfig
from .desktop_router import (
    DesktopRouterQueue,
    RouterProtocolError,
    RouterStatus,
    looks_like_thread_id,
)


DESKTOP_TASK_CATALOG_LIMIT = 50


class CodexGatewayError(RuntimeError):
    """A Desktop Gateway request failed without attaching to the target task."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "gateway_error",
        may_have_started: bool = False,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.may_have_started = may_have_started
        self.retryable = retryable


class DesktopRouterUnavailable(CodexGatewayError):
    """No dedicated Desktop Gateway task has been registered."""

    def __init__(self, message: str = "Desktop Gateway task is offline") -> None:
        super().__init__(
            message,
            code="router_unavailable",
            may_have_started=False,
            retryable=True,
        )


class CodexSessionNotBound(CodexGatewayError):
    """A Feishu scope has not selected a canonical Desktop task."""


class CodexTurnInterrupted(CodexGatewayError):
    """The target task may have started but no authoritative final arrived."""

    def __init__(self, message: str) -> None:
        super().__init__(
            message,
            code="target_result_unknown",
            may_have_started=True,
            retryable=False,
        )


class CodexTargetUnavailable(CodexGatewayError):
    """The selected target ended its lifecycle before delivery started."""

    CODES = frozenset({"target_archived", "target_not_found"})

    def __init__(self, message: str, *, code: str) -> None:
        stable_code = code if code in self.CODES else "target_not_found"
        super().__init__(
            message,
            code=stable_code,
            may_have_started=False,
            retryable=False,
        )


@dataclass(frozen=True)
class TurnHandle:
    thread_id: str
    turn_id: str


@dataclass(frozen=True)
class CodexAnswer:
    text: str
    thread_id: str
    turn_id: str
    host_id: str = ""


@dataclass(frozen=True)
class ThreadCreation:
    thread_id: str
    archived_thread_ids: tuple[str, ...] = ()
    host_id: str = ""


@dataclass(frozen=True)
class ThreadActivation:
    thread_id: str
    archived_thread_ids: tuple[str, ...] = ()
    host_id: str = ""


@dataclass(frozen=True)
class DesktopProjectSummary:
    project_id: str
    label: str
    root: str
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


@dataclass(frozen=True)
class DesktopTaskCatalog:
    projects: tuple[DesktopProjectSummary, ...]
    tasks: tuple[DesktopTaskSummary, ...]
    include_archived: bool
    truncated: bool = False


class DesktopRouterCodex:
    """Submit work to the durable queue consumed by one Desktop Gateway task.

    This class deliberately has no Codex executable path and no App Server RPC
    methods. A target task id appears only inside a queue request consumed by an
    automation-origin Gateway turn using Desktop `send_message_to_thread` and
    `wait_threads` task-coordination tools.
    """

    session_owner = "desktop-router"

    def __init__(
        self,
        config: BridgeConfig,
        queue: DesktopRouterQueue | None = None,
    ) -> None:
        self.config = config
        self.queue = queue or DesktopRouterQueue(
            config.runtime_dir,
            heartbeat_ttl_seconds=config.router_heartbeat_ttl_seconds,
            claim_ttl_seconds=config.router_claim_ttl_seconds,
            retention_hours=config.router_retention_hours,
            wake_lease_ttl_seconds=config.router_wake_ttl_seconds,
            scheduler_ttl_seconds=config.gateway_scheduler_ttl_seconds,
            grace_wait_max_seconds=config.router_grace_max_seconds,
        )

    def is_alive(self) -> bool:
        # A sleeping Gateway is healthy when its scheduler probe is fresh. A
        # busy Gateway remains healthy when the fenced active-work lease heartbeat is
        # fresh even if a new scheduled probe cannot run concurrently.
        status = self.queue.status()
        return status.registered and (
            status.scheduler_fresh
            or (status.wake_inflight and status.work_heartbeat_fresh)
        )

    def connection_status(self) -> str:
        """Return the protocol-v4 compatibility transport label."""
        status = self.queue.status()
        if not status.registered:
            return "desktop-gateway-unregistered"
        if not status.scheduler_fresh:
            return "desktop-gateway-heartbeat-stale"
        return "desktop-gateway-on-demand"

    def gateway_state(self, status: RouterStatus | None = None) -> str:
        """Return an unambiguous scheduler/work state for diagnostics."""
        status = status or self.queue.status()
        if not status.registered:
            return "unregistered"
        if status.wake_inflight:
            if status.work_heartbeat_fresh:
                return "work-active"
            return "work-heartbeat-stale"
        if not status.scheduler_fresh:
            return "scheduler-stale"
        return "scheduled-idle"

    def router_status(self) -> RouterStatus:
        return self.queue.status()

    def _ensure_registered(self) -> None:
        if not self.queue.is_registered():
            raise DesktopRouterUnavailable(
                "Desktop Gateway task is not registered; request remains in the Feishu inbox"
            )

    @staticmethod
    def _request_key(value: str) -> str:
        candidate = value.strip()
        if not candidate:
            raise CodexGatewayError("Desktop Gateway request requires an idempotency key")
        return candidate

    @staticmethod
    def _archive_targets(
        thread_ids: Iterable[str],
        *,
        keep_thread_id: str = "",
    ) -> tuple[str, ...]:
        result: list[str] = []
        for item in thread_ids:
            candidate = str(item or "").strip()
            if not candidate or candidate == keep_thread_id or candidate in result:
                continue
            if not looks_like_thread_id(candidate):
                raise CodexGatewayError("invalid archive task id")
            result.append(candidate)
        return tuple(result)

    @classmethod
    def _confirmed_archives(
        cls,
        result: dict[str, Any],
        requested: tuple[str, ...],
        *,
        keep_thread_id: str = "",
        outcome_may_have_started: bool = True,
    ) -> tuple[str, ...]:
        raw = result.get("archived_thread_ids", [])
        if not isinstance(raw, list):
            cls._invalid_completed_result(
                "Desktop Gateway returned an invalid archive result",
                may_have_started=outcome_may_have_started,
            )
        confirmed_list: list[str] = []
        for item in raw:
            if not isinstance(item, str):
                cls._invalid_completed_result(
                    "Desktop Gateway returned a non-text archive task id",
                    may_have_started=outcome_may_have_started,
                )
            candidate = item.strip()
            if not looks_like_thread_id(candidate) or candidate in confirmed_list:
                cls._invalid_completed_result(
                    "Desktop Gateway returned an invalid or duplicate archive task id",
                    may_have_started=outcome_may_have_started,
                )
            confirmed_list.append(candidate)
        confirmed = tuple(confirmed_list)
        allowed = set(requested)
        if keep_thread_id and keep_thread_id in confirmed:
            cls._invalid_completed_result(
                "Desktop Gateway reported the active task as archived",
                may_have_started=outcome_may_have_started,
            )
        if any(thread_id not in allowed for thread_id in confirmed):
            cls._invalid_completed_result(
                "Desktop Gateway reported an unrequested task as archived",
                may_have_started=outcome_may_have_started,
            )
        return confirmed

    @staticmethod
    def _invalid_completed_result(
        message: str,
        *,
        may_have_started: bool,
    ) -> NoReturn:
        if may_have_started:
            raise CodexTurnInterrupted(message)
        raise CodexGatewayError(
            message,
            code="invalid_gateway_result",
            may_have_started=False,
            retryable=False,
        )

    @classmethod
    def _result(
        cls,
        response: dict[str, Any],
        *,
        completed_may_have_started: bool = False,
    ) -> dict[str, Any]:
        status = response.get("status")
        if status == "completed":
            result = response.get("result")
            if not isinstance(result, dict):
                cls._invalid_completed_result(
                    "Desktop Gateway returned no result",
                    may_have_started=completed_may_have_started,
                )
            return result
        if status != "failed":
            cls._invalid_completed_result(
                "Desktop Gateway returned an invalid terminal status",
                may_have_started=completed_may_have_started,
            )
        error = response.get("error")
        if not isinstance(error, dict):
            cls._invalid_completed_result(
                "Desktop Gateway returned an invalid failure result",
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
                "Desktop Gateway returned malformed failure fields",
                may_have_started=completed_may_have_started,
            )
        message = str(details.get("message") or "Desktop Gateway request failed")
        code = raw_code.strip()
        may_have_started = raw_may_have_started
        retryable = raw_retryable
        if code in {
            "turn_interrupted",
            "target_result_unknown",
            "target_needs_attention",
        } or may_have_started:
            raise CodexTurnInterrupted(message)
        if code in {"router_offline", "router_not_registered"}:
            raise DesktopRouterUnavailable(message)
        if code in CodexTargetUnavailable.CODES:
            raise CodexTargetUnavailable(message, code=code)
        if code in {"target_tool_unavailable", "project_not_registered"}:
            retryable = False
        raise CodexGatewayError(
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
        self._ensure_registered()
        try:
            request_id = self.queue.submit(
                operation,
                payload,
                idempotency_key=self._request_key(request_key),
            )
        except RouterProtocolError as exc:
            if completed_may_have_started:
                raise CodexTurnInterrupted(
                    "Desktop Gateway queue state conflicts with a possibly-started target action"
                ) from exc
            raise CodexGatewayError(str(exc)) from exc
        response = self.queue.wait_for_response(
            request_id,
            self.config.router_timeout_seconds,
            on_claimed=(
                (lambda: on_submitted(request_id))
                if on_submitted is not None
                else None
            ),
        )
        if response is None:
            claimed = self.queue.was_claimed(request_id)
            if claimed:
                raise CodexTurnInterrupted(
                    "Desktop Gateway response timed out after the request was claimed"
                )
            raise CodexGatewayError(
                "Desktop Gateway response timed out",
                code="router_response_timeout",
                may_have_started=False,
                retryable=True,
            )
        return self._result(
            response,
            completed_may_have_started=completed_may_have_started,
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
                "Desktop Gateway returned an invalid target task id",
                may_have_started=outcome_may_have_started,
            )
        if expected_thread_id and thread_id != expected_thread_id:
            cls._invalid_completed_result(
                "Desktop Gateway returned a different target task id",
                may_have_started=outcome_may_have_started,
            )
        return thread_id, str(result.get("host_id") or "").strip()

    def bind_thread(self, thread_id: str, name: str, *, request_key: str) -> ThreadActivation:
        candidate = thread_id.strip()
        if not looks_like_thread_id(candidate):
            raise CodexGatewayError("invalid Codex task id")
        result = self._submit_and_wait(
            "inspect_thread",
            {
                "target_thread_id": candidate,
                "display_name": name.strip(),
            },
            request_key=request_key,
            completed_may_have_started=False,
        )
        resolved, host_id = self._thread_result(
            result,
            expected_thread_id=candidate,
            outcome_may_have_started=False,
        )
        return ThreadActivation(resolved, host_id=host_id)

    @classmethod
    def _catalog_result(
        cls,
        result: dict[str, Any],
        *,
        expected_thread_ids: set[str] | None,
        include_archived: bool,
        limit: int,
    ) -> DesktopTaskCatalog:
        if result.get("catalog_version") != 1:
            cls._invalid_completed_result(
                "Desktop Gateway returned an unsupported task catalog version",
                may_have_started=False,
            )
        if result.get("include_archived") is not include_archived:
            cls._invalid_completed_result(
                "Desktop Gateway returned a task catalog for the wrong archive mode",
                may_have_started=False,
            )
        raw_projects = result.get("projects")
        raw_tasks = result.get("tasks")
        if not isinstance(raw_projects, list) or not isinstance(raw_tasks, list):
            cls._invalid_completed_result(
                "Desktop Gateway task catalog has invalid collections",
                may_have_started=False,
            )
        if len(raw_projects) > limit or len(raw_tasks) > limit:
            cls._invalid_completed_result(
                "Desktop Gateway task catalog exceeds its requested bound",
                may_have_started=False,
            )

        projects: list[DesktopProjectSummary] = []
        project_ids: set[str] = set()
        for raw in raw_projects:
            if not isinstance(raw, dict):
                cls._invalid_completed_result(
                    "Desktop Gateway task catalog contains an invalid project",
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
                or not root
                or len(root) > 1024
                or project_id in project_ids
            ):
                cls._invalid_completed_result(
                    "Desktop Gateway task catalog contains invalid project metadata",
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
                    "Desktop Gateway task catalog contains an invalid task",
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
            ):
                cls._invalid_completed_result(
                    "Desktop Gateway task catalog contains invalid task metadata",
                    may_have_started=False,
                )
            seen_threads.add(thread_id)
            try:
                updated_at = float(raw.get("updated_at", 0) or 0)
            except (TypeError, ValueError):
                cls._invalid_completed_result(
                    "Desktop Gateway task catalog has an invalid task timestamp",
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
                )
            )

        if any(item.project_id not in project_ids for item in tasks):
            cls._invalid_completed_result(
                "Desktop Gateway task catalog contains an unknown project reference",
                may_have_started=False,
            )

        truncated = result.get("truncated")
        if not isinstance(truncated, bool):
            cls._invalid_completed_result(
                "Desktop Gateway task catalog has an invalid truncation flag",
                may_have_started=False,
            )
        return DesktopTaskCatalog(
            projects=tuple(projects),
            tasks=tuple(tasks),
            include_archived=include_archived,
            truncated=truncated,
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
                raise CodexGatewayError("exact-scope task catalog exceeds 20 task ids")
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
        gateway_thread_id = self.queue.status().router_thread_id
        if gateway_thread_id and any(
            item.thread_id == gateway_thread_id for item in catalog.tasks
        ):
            self._invalid_completed_result(
                "Desktop Gateway task catalog exposed the dedicated Gateway task",
                may_have_started=False,
            )
        return catalog

    def create_thread(
        self,
        name: str,
        *,
        request_key: str,
        archive_thread_ids: Iterable[str] = (),
        project_root: Path | None = None,
    ) -> ThreadCreation:
        selected_root = (project_root or self.config.project_root).resolve()
        archives = self._archive_targets(archive_thread_ids)
        result = self._submit_and_wait(
            "create_thread",
            {
                "title": name.strip(),
                "project_root": str(selected_root),
                "archive_thread_ids": list(archives),
                "initial_prompt": (
                    "这是由已授权飞书会话创建的空白任务。只确认路由已就绪；"
                    "不要执行项目工作，也不要自行加载旧会话上下文。"
                ),
            },
            request_key=request_key,
            completed_may_have_started=True,
        )
        thread_id, host_id = self._thread_result(
            result,
            outcome_may_have_started=True,
        )
        if thread_id in archives:
            self._invalid_completed_result(
                "Desktop Gateway returned a requested displaced task as the new task",
                may_have_started=True,
            )
        archived = self._confirmed_archives(
            result,
            archives,
            keep_thread_id=thread_id,
        )
        return ThreadCreation(
            thread_id,
            archived_thread_ids=archived,
            host_id=host_id,
        )

    def restore_thread(
        self,
        thread_id: str,
        *,
        request_key: str,
        archive_thread_ids: Iterable[str] = (),
    ) -> ThreadActivation:
        candidate = thread_id.strip()
        if not looks_like_thread_id(candidate):
            raise CodexGatewayError("invalid Codex task id")
        archives = self._archive_targets(archive_thread_ids, keep_thread_id=candidate)
        result = self._submit_and_wait(
            "restore_thread",
            {
                "target_thread_id": candidate,
                "archive_thread_ids": list(archives),
            },
            request_key=request_key,
            completed_may_have_started=True,
        )
        resolved, host_id = self._thread_result(
            result,
            expected_thread_id=candidate,
            outcome_may_have_started=True,
        )
        archived = self._confirmed_archives(
            result,
            archives,
            keep_thread_id=resolved,
        )
        return ThreadActivation(resolved, archived_thread_ids=archived, host_id=host_id)

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
            raise CodexGatewayError(
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

    def route_message(
        self,
        session: dict[str, Any],
        name: str,
        user_text: str,
        *,
        client_message_id: str,
        local_images: list[Path] | None = None,
        local_audio: list[Path] | None = None,
        additional_context: dict[str, str] | None = None,
        on_turn_started: Callable[[TurnHandle], None] | None = None,
    ) -> CodexAnswer:
        del name
        thread_id = str(session.get("thread_id") or "").strip()
        if not thread_id:
            raise CodexSessionNotBound("Feishu scope is not bound to a Codex task")
        if not looks_like_thread_id(thread_id):
            raise CodexGatewayError("bound Codex task id is invalid")
        prompt = self._transport_prompt(
            user_text,
            local_images=local_images,
            local_audio=local_audio,
            additional_context=additional_context,
        )

        def submitted(request_id: str) -> None:
            if on_turn_started is not None:
                on_turn_started(TurnHandle(thread_id=thread_id, turn_id=request_id))

        result = self._submit_and_wait(
            "send_message_to_thread",
            {
                "target_thread_id": thread_id,
                "host_id": str(session.get("host_id") or "").strip(),
                "prompt": prompt,
                "source": "feishu",
                "client_message_id": client_message_id,
            },
            request_key=client_message_id,
            completed_may_have_started=True,
            on_submitted=submitted,
        )
        resolved, host_id = self._thread_result(
            result,
            expected_thread_id=thread_id,
            outcome_may_have_started=True,
        )
        text = str(result.get("text") or "").strip()
        if not text:
            raise CodexTurnInterrupted(
                "target Codex task completed without an authoritative final answer"
            )
        return CodexAnswer(
            text=text,
            thread_id=resolved,
            turn_id=str(result.get("turn_id") or result.get("cursor") or client_message_id),
            host_id=host_id,
        )

    def compact(
        self,
        thread_id: str,
        *,
        request_key: str,
        host_id: str = "",
        archive_thread_ids: Iterable[str] = (),
    ) -> ThreadActivation:
        candidate = thread_id.strip()
        if not looks_like_thread_id(candidate):
            raise CodexGatewayError("invalid Codex task id")
        archives = self._archive_targets(archive_thread_ids, keep_thread_id=candidate)
        result = self._submit_and_wait(
            "compact_thread",
            {
                "target_thread_id": candidate,
                "host_id": host_id.strip(),
                "command": "/compact",
                "archive_thread_ids": list(archives),
            },
            request_key=request_key,
            completed_may_have_started=True,
        )
        resolved, resolved_host = self._thread_result(
            result,
            expected_thread_id=candidate,
            outcome_may_have_started=True,
        )
        archived = self._confirmed_archives(
            result,
            archives,
            keep_thread_id=resolved,
        )
        return ThreadActivation(
            resolved,
            archived_thread_ids=archived,
            host_id=resolved_host,
        )

    def steer(self, handle: TurnHandle, text: str, *, request_key: str) -> None:
        self._ensure_registered()
        try:
            self.queue.submit(
                "send_message_to_thread",
                {
                    "target_thread_id": handle.thread_id,
                    "prompt": text,
                    "source": "feishu-steer",
                    "parent_request_id": handle.turn_id,
                    "mode": "steer",
                },
                idempotency_key=self._request_key(request_key),
            )
        except RouterProtocolError as exc:
            raise CodexTurnInterrupted(
                "Desktop Gateway queue state conflicts with a possibly-started steer action"
            ) from exc

    @staticmethod
    def interrupt(handle: TurnHandle) -> NoReturn:
        del handle
        raise CodexGatewayError(
            "Codex Desktop task gateway does not expose cross-task interruption; no stop was sent"
        )

    def maintenance(self) -> None:
        self.queue.cleanup()

    def close(self) -> None:
        # There is no child Codex process and therefore no writer to release.
        return


def create_codex_client(config: BridgeConfig) -> DesktopRouterCodex:
    return DesktopRouterCodex(config)
