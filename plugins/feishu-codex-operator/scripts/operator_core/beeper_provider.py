"""Deterministic loopback Responses API used only by the fixed Beeper task."""

from __future__ import annotations

from collections import OrderedDict
import hashlib
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import threading
import time
from typing import Any


BEEPER_LOCAL_MODEL = "beeper"
BEEPER_PROVIDER_NAME = "beeper"
BEEPER_PROVIDER_MAX_BODY_BYTES = 8 * 1024 * 1024
BEEPER_PROVIDER_MAX_REMEMBERED_CALLS = 1024
BEEPER_TERMINAL_TEXT = "DONT_NOTIFY"
BEEPER_IDENTITY_TEXT = (
    "我是 Beeper，Feishu Codex Operator 的本地确定性中继程序。"
    "我仅按约定协议，将飞书请求转交给指定的 Codex 任务处理。"
    "我不具备通用问答或推理能力，不执行具体业务任务；请切换到其他模型进行对话。"
)
BEEPER_PROTOCOL_INSTRUCTIONS = (
    "You are the minimal Feishu Codex Operator Beeper.\n"
    "Your first action must be exactly one call to exec with the code below. "
    "No text before the call. Copy these four lines exactly.\n"
    "Execute only the Operator MCP-generated code; never print, rewrite, or expand it. "
    "After exec returns, stop on every outcome. Never send separately, retry, or call Final Callback.\n"
)
_REQUEST_ID_VALUE_PATTERN = re.compile(r"[a-f0-9]{32}")
_REQUEST_ID_MARKER_PATTERN = re.compile(
    r'\{"request_id":"([a-f0-9]{32})"\}',
)


class BeeperProviderError(RuntimeError):
    """The deterministic provider rejected an invalid or ambiguous request."""


def beeper_bootstrap(request_id: str) -> str:
    """Return the only program the local Beeper provider may ask Codex to run."""

    if _REQUEST_ID_VALUE_PATTERN.fullmatch(str(request_id or "")) is None:
        raise ValueError("invalid Beeper request_id")
    arguments = json.dumps({"request_id": request_id}, separators=(",", ":"))
    return (
        "const started=Date.now();\n"
        'const relay=ALL_TOOLS.find(t=>t.name.endsWith("__take_relay"));\n'
        "const result=await tools[relay.name](" + arguments + ");\n"
        "await eval(result.structuredContent.code)();\n"
    )


def _usage() -> dict[str, object]:
    return {
        "input_tokens": 0,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens": 0,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": 0,
    }


class _ResponseBuilder:
    def __init__(self, request_id: str, phase: str) -> None:
        digest = hashlib.sha256(f"beeper:{request_id}:{phase}".encode("ascii")).hexdigest()
        self.response_id = "resp_beeper_" + digest[:32]
        self.item_id = ("ctc_" if phase == "call" else "msg_") + digest[32:56]
        self.call_id = "call_beeper_" + hashlib.sha256(
            f"beeper:{request_id}:call".encode("ascii")
        ).hexdigest()[:32]
        self.created_at = int(time.time())
        self.phase = phase

    def response(self, *, status: str, output: list[dict[str, object]]) -> dict[str, object]:
        return {
            "id": self.response_id,
            "object": "response",
            "created_at": self.created_at,
            "status": status,
            "error": None,
            "incomplete_details": None,
            "instructions": None,
            "max_output_tokens": None,
            "model": BEEPER_LOCAL_MODEL,
            "output": output,
            "parallel_tool_calls": False,
            "previous_response_id": None,
            "reasoning": {"effort": "low", "summary": None},
            "store": False,
            "temperature": None,
            "text": {"format": {"type": "text"}},
            "tool_choice": "auto",
            "tools": [],
            "top_p": None,
            "truncation": "disabled",
            "usage": _usage() if status == "completed" else None,
        }

    def custom_call_item(self, tool_name: str, code: str, status: str) -> dict[str, object]:
        return {
            "id": self.item_id,
            "type": "custom_tool_call",
            "status": status,
            "call_id": self.call_id,
            "name": tool_name,
            "input": code,
        }

    def message_item(self, text: str, status: str) -> dict[str, object]:
        return {
            "id": self.item_id,
            "type": "message",
            "status": status,
            "role": "assistant",
            "content": [
                {
                    "type": "output_text",
                    "annotations": [],
                    "logprobs": [],
                    "text": text,
                }
            ],
        }


class BeeperResponsesEngine:
    """Create bounded deterministic Responses API payloads without model sampling."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._calls: OrderedDict[str, str] = OrderedDict()

    def _remember(self, call_id: str, request_id: str) -> None:
        with self._lock:
            self._calls[call_id] = request_id
            self._calls.move_to_end(call_id)
            while len(self._calls) > BEEPER_PROVIDER_MAX_REMEMBERED_CALLS:
                self._calls.popitem(last=False)

    def _request_id(self, payload: dict[str, object]) -> str:
        items = self._current_input(payload)
        users = [item for item in items if item.get("role") == "user"]
        if users:
            content = users[-1].get("content")
            if isinstance(content, list):
                # Only plain text is admitted; nested data or attachments are not instructions.
                if any(not isinstance(part, dict) or part.get("type") != "input_text"
                       or not isinstance(part.get("text"), str) for part in content):
                    raise BeeperProviderError("not a relay envelope")
                content = "".join(part["text"] for part in content)
            if isinstance(content, str):
                found = _REQUEST_ID_MARKER_PATTERN.findall(content)
                if len(found) == 1 and content == BEEPER_PROTOCOL_INSTRUCTIONS + beeper_bootstrap(found[0]):
                    return found[0]
            raise BeeperProviderError("not a relay envelope")
        # Incremental continuation must reference a call issued by this live engine.
        outputs = [item for item in items if item.get("type") == "custom_tool_call_output"]
        if len(items) == len(outputs) == 1:
            with self._lock:
                request_id = self._calls.get(str(outputs[0].get("call_id") or ""))
            if request_id:
                previous = payload.get("previous_response_id")
                if not previous or previous == _ResponseBuilder(request_id, "call").response_id:
                    return request_id
        raise BeeperProviderError("request_id is missing or ambiguous")

    @staticmethod
    def _current_input(payload: dict[str, object]) -> list[dict[str, object]]:
        raw = payload.get("input")
        if isinstance(raw, str):
            return [{"role": "user", "content": raw}]
        if not isinstance(raw, list) or any(not isinstance(item, dict) for item in raw):
            raise BeeperProviderError("invalid input")
        last_user = next((index for index in range(len(raw) - 1, -1, -1)
                          if raw[index].get("role") == "user"), 0)
        return raw[last_user:]

    @staticmethod
    def _exec_tool_name(payload: dict[str, object]) -> str:
        candidates: list[str] = []
        tools = payload.get("tools")
        if isinstance(tools, list):
            for tool in tools:
                if not isinstance(tool, dict):
                    continue
                name = str(tool.get("name") or "").strip()
                if str(tool.get("type") or "") == "custom" and name == "exec":
                    candidates.append(name)
        if candidates != ["exec"]:
            raise BeeperProviderError("exactly one exec custom tool is required")
        return candidates[0]

    def _has_matching_output(self, payload: dict[str, object], call_id: str) -> bool:
        matches = 0
        for item in self._current_input(payload):
            if str(item.get("type") or "") not in {
                "custom_tool_call_output",
                "function_call_output",
            }:
                continue
            if str(item.get("call_id") or "").strip() == call_id:
                matches += 1
        with self._lock:
            return matches == 1 and call_id in self._calls

    def _text_response(self, request_id: str, phase: str, text: str):
        builder = _ResponseBuilder(request_id, phase)
        item = builder.message_item(text, "completed")
        response = builder.response(status="completed", output=[item])
        return response, self._message_events(builder, item, response)

    def create(self, payload: object) -> tuple[dict[str, object], list[dict[str, object]]]:
        if not isinstance(payload, dict):
            raise BeeperProviderError("response request must be a JSON object")
        if str(payload.get("model") or "").strip() != BEEPER_LOCAL_MODEL:
            raise BeeperProviderError("only the beeper model is supported")
        try:
            request_id = self._request_id(payload)
        except BeeperProviderError:
            return self._text_response("identity", "identity", BEEPER_IDENTITY_TEXT)
        call_builder = _ResponseBuilder(request_id, "call")

        if self._has_matching_output(payload, call_builder.call_id):
            return self._text_response(request_id, "done", BEEPER_TERMINAL_TEXT)

        if any(item.get("type") in {"custom_tool_call_output", "function_call_output"}
               for item in self._current_input(payload)):
            return self._text_response("identity", "identity", BEEPER_IDENTITY_TEXT)
        try:
            tool_name = self._exec_tool_name(payload)
        except BeeperProviderError:
            return self._text_response("identity", "identity", BEEPER_IDENTITY_TEXT)
        code = beeper_bootstrap(request_id)
        builder = call_builder
        item = builder.custom_call_item(tool_name, code, "completed")
        self._remember(builder.call_id, request_id)
        response = builder.response(status="completed", output=[item])
        events = self._custom_call_events(builder, tool_name, code, item, response)
        return response, events

    @staticmethod
    def _custom_call_events(
        builder: _ResponseBuilder,
        tool_name: str,
        code: str,
        completed_item: dict[str, object],
        response: dict[str, object],
    ) -> list[dict[str, object]]:
        added_item = builder.custom_call_item(tool_name, "", "in_progress")
        return [
            {
                "type": "response.created",
                "sequence_number": 0,
                "response": builder.response(status="in_progress", output=[]),
            },
            {
                "type": "response.output_item.added",
                "sequence_number": 1,
                "output_index": 0,
                "item": added_item,
            },
            {
                "type": "response.custom_tool_call_input.delta",
                "sequence_number": 2,
                "output_index": 0,
                "item_id": builder.item_id,
                "delta": code,
            },
            {
                "type": "response.custom_tool_call_input.done",
                "sequence_number": 3,
                "output_index": 0,
                "item_id": builder.item_id,
                "input": code,
            },
            {
                "type": "response.output_item.done",
                "sequence_number": 4,
                "output_index": 0,
                "item": completed_item,
            },
            {
                "type": "response.completed",
                "sequence_number": 5,
                "response": response,
            },
        ]

    @staticmethod
    def _message_events(
        builder: _ResponseBuilder,
        completed_item: dict[str, object],
        response: dict[str, object],
    ) -> list[dict[str, object]]:
        empty_part = {"type": "output_text", "annotations": [], "logprobs": [], "text": ""}
        message_text = completed_item["content"][0]["text"]
        completed_part = {
            "type": "output_text",
            "annotations": [],
            "logprobs": [],
            "text": message_text,
        }
        added_item = dict(completed_item)
        added_item["status"] = "in_progress"
        added_item["content"] = []
        return [
            {
                "type": "response.created",
                "sequence_number": 0,
                "response": builder.response(status="in_progress", output=[]),
            },
            {
                "type": "response.output_item.added",
                "sequence_number": 1,
                "output_index": 0,
                "item": added_item,
            },
            {
                "type": "response.content_part.added",
                "sequence_number": 2,
                "output_index": 0,
                "content_index": 0,
                "item_id": builder.item_id,
                "part": empty_part,
            },
            {
                "type": "response.output_text.delta",
                "sequence_number": 3,
                "output_index": 0,
                "content_index": 0,
                "item_id": builder.item_id,
                "delta": message_text,
                "logprobs": [],
            },
            {
                "type": "response.output_text.done",
                "sequence_number": 4,
                "output_index": 0,
                "content_index": 0,
                "item_id": builder.item_id,
                "text": message_text,
                "logprobs": [],
            },
            {
                "type": "response.content_part.done",
                "sequence_number": 5,
                "output_index": 0,
                "content_index": 0,
                "item_id": builder.item_id,
                "part": completed_part,
            },
            {
                "type": "response.output_item.done",
                "sequence_number": 6,
                "output_index": 0,
                "item": completed_item,
            },
            {
                "type": "response.completed",
                "sequence_number": 7,
                "response": response,
            },
        ]


class BeeperProviderServer:
    """Own one short loopback HTTP listener for local Beeper turns."""

    def __init__(self, *, engine: BeeperResponsesEngine | None = None) -> None:
        self.engine = engine or BeeperResponsesEngine()
        self._lock = threading.RLock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        with self._lock:
            if self._server is None:
                raise BeeperProviderError("local Beeper provider is not running")
            host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/v1"

    def start(self) -> str:
        with self._lock:
            if self._server is not None:
                return self.base_url
            engine = self.engine

            class Handler(BaseHTTPRequestHandler):
                protocol_version = "HTTP/1.1"

                def log_message(self, _format: str, *_args: object) -> None:
                    return

                def _write_json(self, status: HTTPStatus, payload: object) -> None:
                    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(body)

                def do_GET(self) -> None:  # noqa: N802
                    if self.path.rstrip("/") == "/health":
                        self._write_json(HTTPStatus.OK, {"status": "ready", "model": BEEPER_LOCAL_MODEL})
                    else:
                        self._write_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

                def do_POST(self) -> None:  # noqa: N802
                    if self.path.split("?", 1)[0].rstrip("/") not in {"/responses", "/v1/responses"}:
                        self._write_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                        return
                    raw_length = self.headers.get("Content-Length", "")
                    try:
                        length = int(raw_length)
                    except ValueError:
                        length = -1
                    if length < 0 or length > BEEPER_PROVIDER_MAX_BODY_BYTES:
                        self._write_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "invalid_body_size"})
                        return
                    try:
                        payload = json.loads(self.rfile.read(length).decode("utf-8"))
                        response, events = engine.create(payload)
                    except (BeeperProviderError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                        self._write_json(
                            HTTPStatus.BAD_REQUEST,
                            {"error": {"type": "invalid_request_error", "message": str(exc)}},
                        )
                        return
                    if not bool(payload.get("stream")):
                        self._write_json(HTTPStatus.OK, response)
                        return
                    body = b"".join(
                        b"data: " + json.dumps(event, separators=(",", ":")).encode("utf-8") + b"\n\n"
                        for event in events
                    ) + b"data: [DONE]\n\n"
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(body)

            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            server.daemon_threads = True
            thread = threading.Thread(
                target=server.serve_forever,
                name="beeper-provider",
                daemon=True,
            )
            self._server = server
            self._thread = thread
            thread.start()
            return self.base_url

    def is_running(self) -> bool:
        with self._lock:
            return self._server is not None and self._thread is not None and self._thread.is_alive()

    def close(self) -> None:
        with self._lock:
            server = self._server
            thread = self._thread
            self._server = None
            self._thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=5)


def beeper_model_catalog_path(runtime_dir: Path) -> Path:
    return runtime_dir / "operator_core" / "beeper_model_catalog.json"
