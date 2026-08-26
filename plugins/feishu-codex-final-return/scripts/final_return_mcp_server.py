"""Hidden MCP tools used only by Feishu Bridge exact-turn lifecycle hooks."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any


SERVER_NAME = "feishu-codex-final-return"
SERVER_VERSION = "0.1.0"
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
MAX_MCP_MESSAGE_BYTES = 2_000_000
MAX_HELPER_OUTPUT_BYTES = 64_000
REQUIRED_RUNTIME_FILES = (
    "router_queue.py",
    "bridge_core/__init__.py",
    "bridge_core/config.py",
    "bridge_core/desktop_router.py",
)


class FinalReturnError(RuntimeError):
    pass


def _hidden_meta() -> dict[str, Any]:
    # Lifecycle hooks may call cataloged tools that are intentionally absent
    # from the model-visible tool surface.
    return {"ui": {"visibility": []}}


TOOLS: list[dict[str, Any]] = [
    {
        "name": "bind_user_prompt",
        "title": "Bind Gateway-armed user prompt",
        "description": "Bind one exact UserPromptSubmit event to an already armed Bridge request.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "minLength": 8, "maxLength": 255},
                "turn_id": {"type": "string", "minLength": 8, "maxLength": 255},
                "cwd": {"type": "string", "minLength": 1, "maxLength": 32767},
                "prompt": {"type": "string", "minLength": 1, "maxLength": 1000000},
            },
            "required": ["session_id", "turn_id", "cwd", "prompt"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "_meta": _hidden_meta(),
    },
    {
        "name": "capture_stop_final",
        "title": "Capture Gateway-bound Stop final",
        "description": "Capture one exact Stop final only for a previously bound Bridge turn.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "minLength": 8, "maxLength": 255},
                "turn_id": {"type": "string", "minLength": 8, "maxLength": 255},
                "cwd": {"type": "string", "minLength": 1, "maxLength": 32767},
                "stop_hook_active": {"type": "boolean"},
                "last_assistant_message": {
                    "type": ["string", "null"],
                    "maxLength": 1000000
                },
            },
            "required": [
                "session_id",
                "turn_id",
                "cwd",
                "stop_hook_active",
                "last_assistant_message"
            ],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "_meta": _hidden_meta(),
    },
]


def _registry_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if not local_app_data:
        raise FinalReturnError("final-return registry is not configured")
    return (
        Path(local_app_data).resolve()
        / "OpenAI"
        / "Codex"
        / "feishu-codex-final-return"
        / "registration.json"
    )


def _assert_no_reparse_path_chain(path: Path) -> Path:
    absolute = Path(os.path.abspath(os.fspath(path)))
    if not absolute.is_absolute():
        raise FinalReturnError("final-return runtime path is not absolute")
    current = Path(absolute.anchor)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    for part in absolute.parts[1:]:
        current = current / part
        metadata = current.stat(follow_symlinks=False)
        attributes = getattr(metadata, "st_file_attributes", 0)
        if current.is_symlink() or attributes & reparse_flag:
            raise FinalReturnError("final-return runtime path is not an ordinary path")
    resolved = absolute.resolve(strict=True)
    if os.path.normcase(str(resolved)) != os.path.normcase(str(absolute)):
        raise FinalReturnError("final-return runtime path changed during resolution")
    return resolved


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verified_runtime() -> tuple[Path, Path]:
    try:
        registration = json.loads(_registry_path().read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise FinalReturnError("final-return registry is not configured") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise FinalReturnError("final-return registry is unreadable") from exc
    if not isinstance(registration, dict) or registration.get("schema_version") != 1:
        raise FinalReturnError("final-return registry is invalid")
    raw_runtime = registration.get("runtime_dir")
    if not isinstance(raw_runtime, str) or not re.fullmatch(r"[A-Za-z]:\\[^\x00]+", raw_runtime):
        raise FinalReturnError("final-return runtime identity is invalid")
    try:
        runtime = _assert_no_reparse_path_chain(Path(raw_runtime))
        manifest_path = _assert_no_reparse_path_chain(
            runtime / "runtime-manifest.json"
        )
    except (OSError, RuntimeError) as exc:
        raise FinalReturnError("final-return runtime path is unavailable") from exc
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FinalReturnError("final-return runtime manifest is unreadable") from exc
    code_files = manifest.get("code_files") if isinstance(manifest, dict) else None
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != 1
        or not isinstance(code_files, dict)
    ):
        raise FinalReturnError("final-return runtime manifest is invalid")
    for relative in REQUIRED_RUNTIME_FILES:
        expected = code_files.get(relative)
        if not isinstance(expected, str) or re.fullmatch(r"[a-f0-9]{64}", expected) is None:
            raise FinalReturnError("final-return runtime manifest is incomplete")
        try:
            candidate = _assert_no_reparse_path_chain(runtime / Path(relative))
            actual = _sha256_file(candidate)
        except (OSError, RuntimeError) as exc:
            raise FinalReturnError("final-return runtime file is unavailable") from exc
        if actual != expected:
            raise FinalReturnError("final-return runtime integrity check failed")
    return runtime, runtime / "router_queue.py"


def _invoke_helper(event: dict[str, Any]) -> dict[str, Any]:
    try:
        runtime, helper = _verified_runtime()
    except FinalReturnError as exc:
        if str(exc) == "final-return registry is not configured":
            return {"ok": True, "accepted": False, "state": "unconfigured"}
        raise
    wire = json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(wire) > 1_000_000:
        raise FinalReturnError("final-return Hook event is too large")
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(("PYTHON", "CODEX_BRIDGE_", "FEISHU_", "LARK"))
    }
    environment.update({"PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-S",
                "-B",
                str(helper),
                "--runtime-dir",
                str(runtime),
                "final-return-hook",
            ],
            input=wire,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(runtime),
            env=environment,
            timeout=6,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise FinalReturnError("final-return helper invocation failed") from exc
    if len(completed.stdout) > MAX_HELPER_OUTPUT_BYTES:
        raise FinalReturnError("final-return helper output is too large")
    try:
        result = json.loads(completed.stdout.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FinalReturnError("final-return helper output is invalid") from exc
    if completed.returncode != 0 or not isinstance(result, dict) or result.get("ok") is not True:
        raise FinalReturnError("final-return helper rejected the Hook event")
    return {
        "ok": True,
        "accepted": result.get("accepted") is True,
        "state": str(result.get("state") or "ignored")[:32],
    }


def _require_arguments(arguments: Any, required: set[str]) -> dict[str, Any]:
    if not isinstance(arguments, dict) or set(arguments) != required:
        raise FinalReturnError("invalid Hook tool arguments")
    return arguments


def _require_text(
    values: dict[str, Any],
    name: str,
    *,
    minimum: int = 1,
    maximum: int,
) -> str:
    value = values.get(name)
    if not isinstance(value, str) or not minimum <= len(value) <= maximum:
        raise FinalReturnError(f"invalid Hook tool field: {name}")
    return value


def _call_tool(name: str, arguments: Any) -> dict[str, Any]:
    common = {"session_id", "turn_id", "cwd"}
    if name == "bind_user_prompt":
        values = _require_arguments(arguments, common | {"prompt"})
        session_id = _require_text(values, "session_id", minimum=8, maximum=255)
        turn_id = _require_text(values, "turn_id", minimum=8, maximum=255)
        cwd = _require_text(values, "cwd", maximum=32767)
        prompt = _require_text(values, "prompt", maximum=1_000_000)
        result = _invoke_helper(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": session_id,
                "turn_id": turn_id,
                "cwd": cwd,
                "prompt": prompt,
            }
        )
        if result.get("ok") is not True:
            raise FinalReturnError("final-return prompt binding failed")
        return {"continue": True}
    if name == "capture_stop_final":
        values = _require_arguments(
            arguments,
            common | {"stop_hook_active", "last_assistant_message"},
        )
        session_id = _require_text(values, "session_id", minimum=8, maximum=255)
        turn_id = _require_text(values, "turn_id", minimum=8, maximum=255)
        cwd = _require_text(values, "cwd", maximum=32767)
        stop_hook_active = values.get("stop_hook_active")
        last_assistant_message = values.get("last_assistant_message")
        if type(stop_hook_active) is not bool:
            raise FinalReturnError("invalid Hook tool field: stop_hook_active")
        if last_assistant_message is not None and (
            not isinstance(last_assistant_message, str)
            or len(last_assistant_message) > 1_000_000
        ):
            raise FinalReturnError("invalid Hook tool field: last_assistant_message")
        result = _invoke_helper(
            {
                "hook_event_name": "Stop",
                "session_id": session_id,
                "turn_id": turn_id,
                "cwd": cwd,
                "stop_hook_active": stop_hook_active,
                "last_assistant_message": last_assistant_message,
            }
        )
        if result.get("ok") is not True:
            raise FinalReturnError("final-return Stop capture failed")
        return {"continue": True}
    raise FinalReturnError("unknown final-return Hook tool")


def _tool_result(payload: dict[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
            }
        ],
        "structuredContent": payload,
    }
    if is_error:
        result["isError"] = True
    return result


def _handle(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    request_id = message.get("id")
    if method in {"notifications/initialized", "notifications/cancelled"}:
        return None
    if request_id is None:
        return None
    if method == "initialize":
        params = message.get("params")
        requested = params.get("protocolVersion") if isinstance(params, dict) else None
        protocol = requested if requested in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0]
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": protocol,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = message.get("params")
        if not isinstance(params, dict) or not isinstance(params.get("name"), str):
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32602, "message": "Invalid tools/call parameters."},
            }
        try:
            payload = _call_tool(params["name"], params.get("arguments", {}))
            result = _tool_result(payload)
        except FinalReturnError as exc:
            result = _tool_result(
                {"ok": False, "error": str(exc)[:200]},
                is_error=True,
            )
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": "Method not found."},
    }


def main() -> int:
    while True:
        raw = sys.stdin.buffer.readline(MAX_MCP_MESSAGE_BYTES + 1)
        if not raw:
            return 0
        if len(raw) > MAX_MCP_MESSAGE_BYTES:
            return 2
        try:
            message = json.loads(raw.decode("utf-8"))
            if not isinstance(message, dict):
                raise ValueError("message must be an object")
            response = _handle(message)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error."},
            }
        if response is not None:
            wire = json.dumps(response, ensure_ascii=True, separators=(",", ":"))
            sys.stdout.buffer.write(wire.encode("ascii") + b"\n")
            sys.stdout.buffer.flush()


if __name__ == "__main__":
    raise SystemExit(main())
