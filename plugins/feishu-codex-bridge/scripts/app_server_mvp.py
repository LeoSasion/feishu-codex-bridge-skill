from __future__ import annotations

import json
from pathlib import Path
from time import monotonic
from typing import Any, Protocol
from uuid import uuid4


SCHEMA_VERSION = 2
COMMAND = "app-server-read-only-probe"
MAX_FRAME_BYTES = 1_048_576
MAX_JSON_NESTING = 128
MAX_NOTIFICATIONS_PER_REQUEST = 100
MAX_STATUS_PAGES = 4
STATUS_PAGE_SIZE = 50
REQUEST_TIMEOUT_SECONDS = 15.0
APP_TOOLS_SERVER = "codex_app"
READ_ONLY_TOOLS = ("list_threads", "list_projects")
FAILURE_PHASES = frozenset(
    (
        "probe_cwd",
        "initialize",
        "control_thread_start",
        "mcp_status",
        "tool_catalog",
        "list_threads",
        "list_projects",
        "none",
    )
)
ALLOWED_REQUEST_METHODS = frozenset(
    (
        "initialize",
        "thread/start",
        "mcpServerStatus/list",
        "mcpServer/tool/call",
    )
)


class AppServerProtocolError(RuntimeError):
    """A fail-closed, answer-free protocol classification."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class LineTransport(Protocol):
    """Deadline-aware transport supplied by a future, separately audited host."""

    def write_line(self, line: str, timeout_seconds: float) -> None: ...

    def read_line(self, max_bytes: int, timeout_seconds: float) -> str | None: ...


class JsonLineRpcSession:
    """One-in-flight-request Codex App Server JSONL protocol engine."""

    def __init__(
        self,
        transport: LineTransport,
        *,
        epoch: str | None = None,
        max_frame_bytes: int = MAX_FRAME_BYTES,
        max_notifications_per_request: int = MAX_NOTIFICATIONS_PER_REQUEST,
        request_timeout_seconds: float = REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        if max_frame_bytes < 256:
            raise ValueError("max_frame_bytes_too_small")
        if max_notifications_per_request < 0:
            raise ValueError("notification_limit_invalid")
        if request_timeout_seconds <= 0:
            raise ValueError("request_timeout_invalid")
        self._transport = transport
        self._epoch = epoch or uuid4().hex
        if not self._epoch or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in self._epoch):
            raise ValueError("epoch_invalid")
        self._max_frame_bytes = max_frame_bytes
        self._max_notifications_per_request = max_notifications_per_request
        self._request_timeout_seconds = request_timeout_seconds
        self._sequence = 0
        self._state = "new"
        self._control_thread_id: str | None = None

    @property
    def control_thread_id(self) -> str | None:
        return self._control_thread_id

    def _next_id(self) -> str:
        self._sequence += 1
        return f"bridge:{self._epoch}:{self._sequence}"

    def _write(self, value: dict[str, Any], timeout_seconds: float) -> None:
        line = json.dumps(
            value,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ) + "\n"
        if len(line.encode("utf-8")) > self._max_frame_bytes:
            raise AppServerProtocolError("client_frame_too_large")
        if timeout_seconds <= 0:
            raise AppServerProtocolError("request_timeout")
        self._transport.write_line(line, timeout_seconds)

    def notify(self, method: str) -> None:
        if method != "initialized":
            raise AppServerProtocolError("client_notification_not_allowed")
        if self._state != "initialize_completed":
            raise AppServerProtocolError("client_protocol_state_invalid")
        self._write({"method": method}, self._request_timeout_seconds)
        self._state = "initialized"

    def _validate_request(self, method: str, params: dict[str, Any]) -> None:
        if method == "initialize":
            if self._state != "new":
                raise AppServerProtocolError("client_protocol_state_invalid")
            expected = {
                "capabilities": {"experimentalApi": False},
                "clientInfo": {
                    "name": "feishu-codex-bridge-app-server-probe",
                    "version": "1",
                },
            }
            if params != expected:
                raise AppServerProtocolError("initialize_params_not_allowed")
            return
        if method == "thread/start":
            if self._state != "initialized" or self._control_thread_id is not None:
                raise AppServerProtocolError("client_protocol_state_invalid")
            if set(params) != {"approvalPolicy", "cwd", "ephemeral", "sandbox"}:
                raise AppServerProtocolError("thread_start_params_not_allowed")
            if (
                params.get("approvalPolicy") != "never"
                or params.get("ephemeral") is not True
                or params.get("sandbox") != "read-only"
                or not isinstance(params.get("cwd"), str)
                or not params["cwd"]
            ):
                raise AppServerProtocolError("thread_start_params_not_allowed")
            return
        if method == "mcpServerStatus/list":
            if self._state != "control_thread_started":
                raise AppServerProtocolError("client_protocol_state_invalid")
            if not {"detail", "limit", "threadId"}.issubset(params) or not set(params).issubset(
                {"cursor", "detail", "limit", "threadId"}
            ):
                raise AppServerProtocolError("mcp_status_params_not_allowed")
            cursor = params.get("cursor")
            if (
                params.get("detail") != "toolsAndAuthOnly"
                or params.get("limit") != STATUS_PAGE_SIZE
                or params.get("threadId") != self._control_thread_id
                or (cursor is not None and (not isinstance(cursor, str) or not cursor))
            ):
                raise AppServerProtocolError("mcp_status_params_not_allowed")
            return
        if method == "mcpServer/tool/call":
            if self._state != "control_thread_started":
                raise AppServerProtocolError("client_protocol_state_invalid")
            if set(params) != {"arguments", "server", "threadId", "tool"}:
                raise AppServerProtocolError("mcp_tool_params_not_allowed")
            tool = params.get("tool")
            arguments = params.get("arguments")
            expected_arguments = {"limit": 50} if tool == "list_threads" else {}
            if (
                params.get("server") != APP_TOOLS_SERVER
                or params.get("threadId") != self._control_thread_id
                or tool not in READ_ONLY_TOOLS
                or arguments != expected_arguments
            ):
                raise AppServerProtocolError("mcp_tool_params_not_allowed")
            return
        raise AppServerProtocolError("client_method_not_allowed")

    def request(self, method: str, params: dict[str, Any]) -> Any:
        if method not in ALLOWED_REQUEST_METHODS:
            raise AppServerProtocolError("client_method_not_allowed")
        if not isinstance(params, dict):
            raise AppServerProtocolError("client_params_invalid")
        self._validate_request(method, params)
        request_id = self._next_id()
        deadline = monotonic() + self._request_timeout_seconds
        self._write(
            {"id": request_id, "method": method, "params": params},
            deadline - monotonic(),
        )

        notifications = 0
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise AppServerProtocolError("request_timeout")
            line = self._transport.read_line(
                self._max_frame_bytes,
                remaining,
            )
            if line is None:
                raise AppServerProtocolError("transport_closed")
            if not isinstance(line, str) or not line.endswith("\n"):
                raise AppServerProtocolError("server_frame_incomplete")
            if len(line.encode("utf-8")) > self._max_frame_bytes:
                raise AppServerProtocolError("server_frame_too_large")
            try:
                message = _load_strict_json_text(line)
            except (json.JSONDecodeError, ValueError) as exc:
                raise AppServerProtocolError("server_frame_invalid_json") from exc
            if not isinstance(message, dict):
                raise AppServerProtocolError("server_frame_invalid_shape")

            has_id = "id" in message
            has_method_field = "method" in message
            if has_id and has_method_field:
                if isinstance(message.get("method"), str):
                    raise AppServerProtocolError("server_request_unsupported")
                raise AppServerProtocolError("server_frame_invalid_shape")
            if not has_id and has_method_field:
                if (
                    not isinstance(message.get("method"), str)
                    or not set(message).issubset({"method", "params"})
                ):
                    raise AppServerProtocolError("server_frame_invalid_shape")
                notifications += 1
                if notifications > self._max_notifications_per_request:
                    raise AppServerProtocolError("server_notification_limit_exceeded")
                continue
            if not has_id:
                raise AppServerProtocolError("server_frame_invalid_shape")
            if message.get("id") != request_id:
                raise AppServerProtocolError("server_response_id_mismatch")

            has_result = "result" in message
            has_error = "error" in message
            if has_result == has_error:
                raise AppServerProtocolError("server_response_ambiguous")
            if has_error:
                if set(message) != {"error", "id"}:
                    raise AppServerProtocolError("server_response_ambiguous")
                error = message.get("error")
                if (
                    not isinstance(error, dict)
                    or not isinstance(error.get("code"), int)
                    or isinstance(error.get("code"), bool)
                ):
                    raise AppServerProtocolError("server_error_invalid_shape")
                raise AppServerProtocolError("server_request_failed")
            if set(message) != {"id", "result"}:
                raise AppServerProtocolError("server_response_ambiguous")
            response_result = message["result"]
            if method == "initialize":
                initialized = _require_object(
                    response_result,
                    "initialize_result_invalid",
                )
                for field in ("codexHome", "platformFamily", "platformOs", "userAgent"):
                    if not isinstance(initialized.get(field), str) or not initialized[field]:
                        raise AppServerProtocolError("initialize_result_invalid")
                self._state = "initialize_completed"
            elif method == "thread/start":
                started = _require_object(
                    response_result,
                    "thread_start_result_invalid",
                )
                thread = _require_object(
                    started.get("thread"),
                    "thread_start_result_invalid",
                )
                thread_id = thread.get("id")
                if (
                    not isinstance(thread_id, str)
                    or not thread_id
                    or thread.get("ephemeral") is not True
                    or thread.get("path") is not None
                ):
                    raise AppServerProtocolError("thread_start_result_invalid")
                self._control_thread_id = thread_id
                self._state = "control_thread_started"
            return response_result


def _safe_result() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "command": COMMAND,
        "status": "fail",
        "failure_phase": "probe_cwd",
        "control_thread_ephemeral": False,
        "control_thread_started": False,
        "codex_app_connected": False,
        "list_threads_available": False,
        "list_projects_available": False,
        "list_threads_result_valid": False,
        "list_projects_result_valid": False,
        "control_thread_hidden_from_desktop_catalog": False,
        "model_turn_started": False,
        "responder_mutation_attempted": False,
        "queue_claimed": False,
        "activation_allowed": False,
        "desktop_task_coordination_certified": False,
        "runtime_attestation_passed": False,
        "issues": [],
    }


def _require_object(value: Any, reason: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AppServerProtocolError(reason)
    return value


def _reject_duplicate_json_members(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON member")
        value[key] = item
    return value


def _reject_non_finite_json_constant(value: str) -> Any:
    raise ValueError(f"non-finite JSON constant: {value}")


def _reject_excessive_json_nesting(value: str) -> None:
    """Bound JSON nesting before decoder behavior can vary by Python build."""

    depth = 0
    in_string = False
    escaped = False
    for character in value:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > MAX_JSON_NESTING:
                raise ValueError("JSON nesting exceeds the supported limit")
        elif character in "]}":
            depth -= 1


def _load_strict_json_text(value: str) -> Any:
    try:
        _reject_excessive_json_nesting(value)
        return json.loads(
            value,
            object_pairs_hook=_reject_duplicate_json_members,
            parse_constant=_reject_non_finite_json_constant,
        )
    except RecursionError as exc:
        # Normalize adversarial nesting into the same bounded protocol failure as
        # every other invalid JSON frame.  Never let a decoder traceback escape
        # the answer-free probe envelope.
        raise ValueError("JSON nesting exceeds the supported limit") from exc


def _decode_tool_object(value: Any) -> dict[str, Any]:
    response = _require_object(value, "mcp_tool_result_invalid")
    is_error = response.get("isError")
    if is_error is not None and not isinstance(is_error, bool):
        raise AppServerProtocolError("mcp_tool_result_invalid")
    if is_error is True:
        raise AppServerProtocolError("mcp_tool_returned_error")

    content = response.get("content")
    structured = response.get("structuredContent")
    if structured is not None:
        if not isinstance(structured, dict) or content != []:
            raise AppServerProtocolError("mcp_tool_result_ambiguous")
        return structured

    if not isinstance(content, list) or len(content) != 1:
        raise AppServerProtocolError("mcp_tool_content_invalid")
    item = content[0]
    if not isinstance(item, dict) or item.get("type") != "text" or not isinstance(item.get("text"), str):
        raise AppServerProtocolError("mcp_tool_content_invalid")
    try:
        decoded = _load_strict_json_text(item["text"])
    except (json.JSONDecodeError, ValueError) as exc:
        raise AppServerProtocolError("mcp_tool_text_invalid_json") from exc
    return _require_object(decoded, "mcp_tool_payload_invalid")


def _validate_thread_catalog(value: dict[str, Any], control_thread_id: str) -> bool:
    threads = value.get("threads")
    pinned = value.get("pinnedThreads")
    if not isinstance(threads, list) or not isinstance(pinned, list):
        raise AppServerProtocolError("list_threads_result_invalid")
    seen_ids: set[str] = set()
    for item in (*threads, *pinned):
        if not isinstance(item, dict):
            raise AppServerProtocolError("list_threads_result_invalid")
        thread_id = item.get("id")
        if not isinstance(thread_id, str) or not thread_id or thread_id in seen_ids:
            raise AppServerProtocolError("list_threads_result_invalid")
        seen_ids.add(thread_id)
        if thread_id == control_thread_id:
            return False
    return True


def _validate_project_catalog(value: dict[str, Any]) -> None:
    projects = value.get("projects")
    if not isinstance(projects, list):
        raise AppServerProtocolError("list_projects_result_invalid")
    seen_ids: set[str] = set()
    for item in projects:
        if not isinstance(item, dict):
            raise AppServerProtocolError("list_projects_result_invalid")
        project_id = item.get("id")
        if not isinstance(project_id, str) or not project_id or project_id in seen_ids:
            raise AppServerProtocolError("list_projects_result_invalid")
        seen_ids.add(project_id)


def run_read_only_probe(
    transport: LineTransport,
    *,
    cwd: Path,
    epoch: str | None = None,
) -> dict[str, Any]:
    """Probe only the Bridge-owned Beeper and two read-only app tools."""

    result = _safe_result()
    control_thread_id: str | None = None
    failure_phase = "probe_cwd"
    try:
        try:
            resolved_cwd = cwd.resolve(strict=True)
        except OSError as exc:
            raise AppServerProtocolError("probe_cwd_unavailable") from exc
        if not resolved_cwd.is_dir():
            raise AppServerProtocolError("probe_cwd_invalid")
        session = JsonLineRpcSession(transport, epoch=epoch)

        failure_phase = "initialize"
        initialize = session.request(
            "initialize",
            {
                "capabilities": {"experimentalApi": False},
                "clientInfo": {
                    "name": "feishu-codex-bridge-app-server-probe",
                    "version": "1",
                },
            },
        )
        initialized = _require_object(initialize, "initialize_result_invalid")
        for field in ("codexHome", "platformFamily", "platformOs", "userAgent"):
            if not isinstance(initialized.get(field), str) or not initialized[field]:
                raise AppServerProtocolError("initialize_result_invalid")
        session.notify("initialized")

        failure_phase = "control_thread_start"
        started = _require_object(
            session.request(
                "thread/start",
                {
                    "approvalPolicy": "never",
                    "cwd": str(resolved_cwd),
                    "ephemeral": True,
                    "sandbox": "read-only",
                },
            ),
            "thread_start_result_invalid",
        )
        _require_object(started.get("thread"), "thread_start_result_invalid")
        control_thread_id = session.control_thread_id
        if control_thread_id is None:
            raise AppServerProtocolError("thread_start_result_invalid")
        result["control_thread_started"] = True
        result["control_thread_ephemeral"] = True

        failure_phase = "mcp_status"
        cursor: str | None = None
        seen_cursors: set[str] = set()
        codex_app: dict[str, Any] | None = None
        for _ in range(MAX_STATUS_PAGES):
            status_params: dict[str, Any] = {
                "detail": "toolsAndAuthOnly",
                "limit": STATUS_PAGE_SIZE,
                "threadId": control_thread_id,
            }
            if cursor is not None:
                status_params["cursor"] = cursor
            page = _require_object(
                session.request("mcpServerStatus/list", status_params),
                "mcp_status_result_invalid",
            )
            data = page.get("data")
            if not isinstance(data, list):
                raise AppServerProtocolError("mcp_status_result_invalid")
            for server in data:
                if not isinstance(server, dict):
                    raise AppServerProtocolError("mcp_status_result_invalid")
                if server.get("name") == APP_TOOLS_SERVER:
                    if codex_app is not None:
                        raise AppServerProtocolError("codex_app_status_duplicated")
                    codex_app = server
            next_cursor = page.get("nextCursor")
            if next_cursor is None:
                break
            if not isinstance(next_cursor, str) or not next_cursor or next_cursor in seen_cursors:
                raise AppServerProtocolError("mcp_status_cursor_invalid")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        else:
            raise AppServerProtocolError("mcp_status_page_limit_exceeded")

        if codex_app is None:
            raise AppServerProtocolError("codex_app_unavailable")
        if codex_app.get("runtimeStatus") != "connected":
            raise AppServerProtocolError("codex_app_not_connected")
        result["codex_app_connected"] = True
        failure_phase = "tool_catalog"
        tools = codex_app.get("tools")
        if not isinstance(tools, dict):
            raise AppServerProtocolError("codex_app_tools_invalid")
        for tool_name in READ_ONLY_TOOLS:
            if tool_name not in tools or not isinstance(tools[tool_name], dict):
                raise AppServerProtocolError(f"{tool_name}_unavailable")
            result[f"{tool_name}_available"] = True

        failure_phase = "list_threads"
        thread_payload = _decode_tool_object(
            session.request(
                "mcpServer/tool/call",
                {
                    "arguments": {"limit": 50},
                    "server": APP_TOOLS_SERVER,
                    "threadId": control_thread_id,
                    "tool": "list_threads",
                },
            )
        )
        result["control_thread_hidden_from_desktop_catalog"] = _validate_thread_catalog(
            thread_payload,
            control_thread_id,
        )
        if not result["control_thread_hidden_from_desktop_catalog"]:
            raise AppServerProtocolError("control_thread_visible_in_desktop_catalog")
        result["list_threads_result_valid"] = True

        failure_phase = "list_projects"
        project_payload = _decode_tool_object(
            session.request(
                "mcpServer/tool/call",
                {
                    "arguments": {},
                    "server": APP_TOOLS_SERVER,
                    "threadId": control_thread_id,
                    "tool": "list_projects",
                },
            )
        )
        _validate_project_catalog(project_payload)
        result["list_projects_result_valid"] = True
        result["status"] = "pass"
        result["failure_phase"] = "none"
    except (AppServerProtocolError, OSError) as exc:
        reason = exc.reason if isinstance(exc, AppServerProtocolError) else "transport_unavailable"
        result["failure_phase"] = failure_phase
        result["issues"] = [reason]
    finally:
        control_thread_id = None
    return result
