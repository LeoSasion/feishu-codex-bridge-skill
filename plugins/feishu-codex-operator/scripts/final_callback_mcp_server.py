"""One-tool MCP server for routing Responder-owned final replies to Feishu."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any


SERVER_NAME = "feishu-codex-final-callback"
SERVER_VERSION = "0.2.0"
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
MAX_MCP_MESSAGE_BYTES = 2_000_000
REQUIRED_RUNTIME_FILES = (
    "routing_cli.py",
    "operator_core/__init__.py",
    "operator_core/final_callback.py",
)


class FinalCallbackError(RuntimeError):
    pass


TOOLS: list[dict[str, Any]] = [
    {
        "name": "submit_final_callback",
        "title": "Submit one Feishu Final Callback",
        "description": (
            "Route the selected Desktop task's exact final reply to the pending "
            "Feishu request identified by request_id. request_id is correlation data, "
            "not authentication or caller attestation."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "request_id": {
                    "type": "string",
                    "pattern": "^[A-Fa-f0-9]{32}$",
                    "minLength": 32,
                    "maxLength": 32,
                },
                "final_answer": {"type": "string", "minLength": 1, "maxLength": 12000},
            },
            "required": ["request_id", "final_answer"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    }
]


def _registry_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if not local_app_data:
        raise FinalCallbackError("Final Callback routing is not configured")
    return Path(local_app_data).resolve() / "OpenAI" / "Codex" / "feishu-codex-final-callback" / "registration.json"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verified_runtime() -> tuple[Path, Path]:
    try:
        registration = json.loads(_registry_path().read_text(encoding="utf-8-sig"))
        runtime = Path(registration["runtime_dir"]).resolve(strict=True)
        manifest = json.loads((runtime / "runtime-manifest.json").read_text(encoding="utf-8-sig"))
    except (KeyError, OSError, json.JSONDecodeError) as exc:
        raise FinalCallbackError("Final Callback routing is unavailable") from exc
    code_files = manifest.get("code_files") if isinstance(manifest, dict) else None
    if registration.get("schema_version") != 2 or not isinstance(code_files, dict):
        raise FinalCallbackError("Final Callback routing is invalid")
    for relative in REQUIRED_RUNTIME_FILES:
        try:
            candidate = (runtime / relative).resolve(strict=True)
        except OSError as exc:
            raise FinalCallbackError("Final Callback runtime file is unavailable") from exc
        expected = code_files.get(relative)
        if (
            runtime not in candidate.parents
            or not isinstance(expected, str)
            or re.fullmatch(r"[a-f0-9]{64}", expected) is None
            or _sha256_file(candidate) != expected
        ):
            raise FinalCallbackError("Final Callback runtime integrity check failed")
    return runtime, runtime / "routing_cli.py"


def _invoke(request_id: str, final_answer: str) -> dict[str, Any]:
    runtime, helper = _verified_runtime()
    wire = json.dumps(
        {"request_id": request_id, "final_answer": final_answer},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(("PYTHON", "CODEX_OPERATOR_", "FEISHU_", "LARK"))
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
                "submit-final-callback",
            ],
            input=wire,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=str(runtime),
            env=environment,
            timeout=6,
            check=False,
            shell=False,
        )
        result = json.loads(completed.stdout.decode("ascii"))
    except (OSError, subprocess.SubprocessError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FinalCallbackError("Final Callback routing failed") from exc
    if completed.returncode != 0 or not isinstance(result, dict) or result.get("ok") is not True:
        raise FinalCallbackError("Final Callback routing rejected the request")
    return {key: result[key] for key in ("accepted", "state") if key in result}


def _call_tool(name: str, arguments: Any) -> dict[str, Any]:
    if name != "submit_final_callback" or not isinstance(arguments, dict):
        raise FinalCallbackError("unknown Final Callback tool")
    if set(arguments) != {"request_id", "final_answer"}:
        raise FinalCallbackError("invalid Final Callback arguments")
    request_id = arguments.get("request_id")
    answer = arguments.get("final_answer")
    if not isinstance(request_id, str) or re.fullmatch(r"[A-Fa-f0-9]{32}", request_id) is None:
        raise FinalCallbackError("invalid request id")
    if not isinstance(answer, str) or not answer.strip() or len(answer) > 12_000:
        raise FinalCallbackError("invalid final answer")
    return {"ok": True, **_invoke(request_id.lower(), answer)}


def _tool_result(payload: dict[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=True, separators=(",", ":"))}],
        "structuredContent": payload,
    }
    if is_error:
        result["isError"] = True
    return result


def _handle(message: dict[str, Any], *, tool_handler=None, tools=None,
            server_name: str = SERVER_NAME) -> dict[str, Any] | None:
    method = message.get("method")
    rpc_id = message.get("id")
    if rpc_id is None:
        return None
    if method == "initialize":
        params = message.get("params")
        requested = params.get("protocolVersion") if isinstance(params, dict) else None
        protocol = requested if requested in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0]
        result = {
            "protocolVersion": protocol,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": server_name, "version": SERVER_VERSION},
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS if tools is None else tools}
    elif method == "tools/call":
        params = message.get("params")
        try:
            if not isinstance(params, dict):
                raise FinalCallbackError("invalid tools/call parameters")
            payload = (tool_handler or _call_tool)(str(params.get("name") or ""), params.get("arguments"))
            result = _tool_result(payload)
        except FinalCallbackError as exc:
            result = _tool_result({"ok": False, "error": str(exc)[:200]}, is_error=True)
    else:
        return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32601, "message": "Method not found."}}
    return {"jsonrpc": "2.0", "id": rpc_id, "result": result}


def main(**handler_options) -> int:
    while True:
        raw = sys.stdin.buffer.readline(MAX_MCP_MESSAGE_BYTES + 1)
        if not raw:
            return 0
        if len(raw) > MAX_MCP_MESSAGE_BYTES:
            return 2
        try:
            message = json.loads(raw.decode("utf-8"))
            if not isinstance(message, dict):
                raise ValueError
            response = _handle(message, **handler_options)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error."}}
        if response is not None:
            wire = json.dumps(response, ensure_ascii=True, separators=(",", ":"))
            sys.stdout.buffer.write(wire.encode("ascii") + b"\n")
            sys.stdout.buffer.flush()


if __name__ == "__main__":
    raise SystemExit(main())
