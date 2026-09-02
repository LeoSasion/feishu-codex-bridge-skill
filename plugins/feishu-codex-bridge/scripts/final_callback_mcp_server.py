"""Responder-owned Final Callback MCP component for the Feishu Bridge."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any


# Internal transport identity retained for registration compatibility. The
# containing Codex plugin package is named ``feishu-codex-bridge``.
SERVER_NAME = "feishu-codex-final-callback"
SERVER_VERSION = "0.1.0"
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
MAX_MCP_MESSAGE_BYTES = 2_000_000
MAX_HELPER_OUTPUT_BYTES = 8_000_000
QUEUE_NAMESPACE = "beeper"
INTERNAL_UNCLAIMED_FAILURE_CODES = frozenset(
    {"beeper_load_assist_failed", "beeper_claim_timeout"}
)
RESPONDER_THREAD_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,255}")
REQUIRED_RUNTIME_FILES = (
    "beeper_queue_cli.py",
    "bridge_core/__init__.py",
    "bridge_core/config.py",
    "bridge_core/beeper_queue.py",
    "bridge_core/legacy_identifiers.py",
)


class FinalCallbackError(RuntimeError):
    pass


TOOLS: list[dict[str, Any]] = [
    {
        "name": "claim_and_arm",
        "title": "Claim one Feishu Beeper Page",
        "description": (
            "Atomically consume one opaque Beeper Page and arm "
            "one Responder-owned Final Callback capability before one Responder send."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "page": {
                    "type": "string",
                    "pattern": "^[A-Fa-f0-9]{32}$",
                    "minLength": 32,
                    "maxLength": 32,
                },
            },
            "required": ["page"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "submit_final_callback",
        "title": "Submit one Feishu Final Callback",
        "description": (
            "Submit the exact final answer from the selected Desktop Responder using "
            "the single-use Final Callback capability embedded in its delivered prompt."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "final_callback_capability": {
                    "type": "string",
                    "pattern": "^[A-Fa-f0-9]{32}$",
                    "minLength": 32,
                    "maxLength": 32,
                },
                "final_answer": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 12000,
                },
            },
            "required": ["final_callback_capability", "final_answer"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "claim_readonly",
        "title": "Claim one read-only Feishu Beeper Page",
        "description": (
            "Claim one operation-bound catalog or exact-task inspection Page. "
            "This tool never arms a Final Callback or starts Responder business work."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "page": {
                    "type": "string",
                    "pattern": "^[A-Fa-f0-9]{32}$",
                    "minLength": 32,
                    "maxLength": 32,
                },
            },
            "required": ["page"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "complete_readonly",
        "title": "Complete one read-only Feishu Beeper Page",
        "description": (
            "Submit one closed structured catalog or exact-task inspection "
            "result. Paths, summaries, prompts, messages, and answers are rejected."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "page": {
                    "type": "string",
                    "pattern": "^[A-Fa-f0-9]{32}$",
                    "minLength": 32,
                    "maxLength": 32,
                },
                "result": {
                    "oneOf": [
                        {
                            "type": "object",
                            "properties": {
                                "catalog_version": {"const": 1},
                                "snapshot_id": {
                                    "type": "string",
                                    "pattern": "^[a-f0-9]{32}$",
                                },
                                "include_archived": {"const": False},
                                "truncated": {"type": "boolean"},
                                "projects": {
                                    "type": "array",
                                    "maxItems": 50,
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "project_id": {"type": "string", "minLength": 1, "maxLength": 200},
                                            "label": {"type": "string", "minLength": 1, "maxLength": 160},
                                            "host_id": {"type": "string", "maxLength": 200},
                                            "kind": {"type": "string", "minLength": 1, "maxLength": 40},
                                        },
                                        "required": ["project_id", "label", "host_id", "kind"],
                                        "additionalProperties": False,
                                    },
                                },
                                "tasks": {
                                    "type": "array",
                                    "maxItems": 50,
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "thread_id": {"type": "string", "pattern": "^[a-f0-9-]{36}$"},
                                            "title": {"type": "string", "minLength": 1, "maxLength": 240},
                                            "project_id": {"type": "string", "minLength": 1, "maxLength": 200},
                                            "host_id": {"type": "string", "maxLength": 200},
                                            "kind": {"const": "codex"},
                                            "status": {"type": "string", "maxLength": 80},
                                            "archived": {"const": False},
                                            "updated_at": {"type": "number", "minimum": 0},
                                        },
                                        "required": ["thread_id", "title", "project_id", "host_id", "kind", "status", "archived", "updated_at"],
                                        "additionalProperties": False,
                                    },
                                },
                            },
                            "required": ["catalog_version", "snapshot_id", "include_archived", "truncated", "projects", "tasks"],
                            "additionalProperties": False,
                        },
                        {
                            "type": "object",
                            "properties": {
                                "thread_id": {"type": "string", "pattern": "^[a-f0-9-]{36}$"},
                                "project_id": {"type": "string", "minLength": 1, "maxLength": 200},
                                "host_id": {"type": "string", "maxLength": 200},
                                "archived": {"const": False},
                                "catalog_snapshot_id": {"type": "string", "pattern": "^[a-f0-9]{32}$"},
                                "operation_receipt": {"type": "string", "pattern": "^[a-f0-9]{32}$"},
                            },
                            "required": ["thread_id", "project_id", "host_id", "archived", "catalog_snapshot_id", "operation_receipt"],
                            "additionalProperties": False,
                        },
                    ]
                },
            },
            "required": ["page", "result"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "finish_final_callback",
        "title": "Finish one Feishu Final Callback",
        "description": (
            "Wait for and seal only the Final Callback submission for one claimed "
            "Beeper Page. The tool never accepts a native assistant answer."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "page": {
                    "type": "string",
                    "pattern": "^[A-Fa-f0-9]{32}$",
                    "minLength": 32,
                    "maxLength": 32,
                },
                "wait_seconds": {"type": "integer", "minimum": 0, "maximum": 30},
            },
            "required": ["page", "wait_seconds"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "fail_page",
        "title": "Fail one Feishu Beeper Page",
        "description": (
            "Terminalize one claimed Beeper Page without retrying "
            "an operation that may have started."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "page": {
                    "type": "string",
                    "pattern": "^[A-Fa-f0-9]{32}$",
                    "minLength": 32,
                    "maxLength": 32,
                },
                "code": {"type": "string", "minLength": 1, "maxLength": 80},
                "may_have_started": {"type": "boolean"},
            },
            "required": ["page", "code", "may_have_started"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
]


def _registry_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if not local_app_data:
        raise FinalCallbackError("final-callback registry is not configured")
    # Keep this compatibility namespace aligned with beeper_queue_cli.py; it is not
    # the outer Codex plugin package ID.
    return (
        Path(local_app_data).resolve()
        / "OpenAI"
        / "Codex"
        / "feishu-codex-final-callback"
        / "registration.json"
    )


def _assert_no_reparse_path_chain(path: Path) -> Path:
    absolute = Path(os.path.abspath(os.fspath(path)))
    if not absolute.is_absolute():
        raise FinalCallbackError("final-callback runtime path is not absolute")
    current = Path(absolute.anchor)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    for part in absolute.parts[1:]:
        current = current / part
        metadata = current.stat(follow_symlinks=False)
        attributes = getattr(metadata, "st_file_attributes", 0)
        if current.is_symlink() or attributes & reparse_flag:
            raise FinalCallbackError("final-callback runtime path is not an ordinary path")
    resolved = absolute.resolve(strict=True)
    if os.path.normcase(str(resolved)) != os.path.normcase(str(absolute)):
        raise FinalCallbackError("final-callback runtime path changed during resolution")
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
        raise FinalCallbackError("final-callback registry is not configured") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise FinalCallbackError("final-callback registry is unreadable") from exc
    if not isinstance(registration, dict) or registration.get("schema_version") != 1:
        raise FinalCallbackError("final-callback registry is invalid")
    raw_runtime = registration.get("runtime_dir")
    if not isinstance(raw_runtime, str) or not re.fullmatch(r"[A-Za-z]:\\[^\x00]+", raw_runtime):
        raise FinalCallbackError("final-callback runtime identity is invalid")
    try:
        runtime = _assert_no_reparse_path_chain(Path(raw_runtime))
        manifest_path = _assert_no_reparse_path_chain(
            runtime / "runtime-manifest.json"
        )
    except (OSError, RuntimeError) as exc:
        raise FinalCallbackError("final-callback runtime path is unavailable") from exc
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FinalCallbackError("final-callback runtime manifest is unreadable") from exc
    code_files = manifest.get("code_files") if isinstance(manifest, dict) else None
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != 1
        or not isinstance(code_files, dict)
    ):
        raise FinalCallbackError("final-callback runtime manifest is invalid")
    for relative in REQUIRED_RUNTIME_FILES:
        expected = code_files.get(relative)
        if not isinstance(expected, str) or re.fullmatch(r"[a-f0-9]{64}", expected) is None:
            raise FinalCallbackError("final-callback runtime manifest is incomplete")
        try:
            candidate = _assert_no_reparse_path_chain(runtime / Path(relative))
            actual = _sha256_file(candidate)
        except (OSError, RuntimeError) as exc:
            raise FinalCallbackError("final-callback runtime file is unavailable") from exc
        if actual != expected:
            raise FinalCallbackError("final-callback runtime integrity check failed")
    return runtime, runtime / "beeper_queue_cli.py"


def _helper_environment() -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(("PYTHON", "CODEX_BRIDGE_", "FEISHU_", "LARK"))
    }
    environment.update({"PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    return environment


def _invoke_helper_command(
    command: str,
    arguments: list[str],
    *,
    input_bytes: bytes = b"",
    timeout_seconds: int = 6,
    verified_runtime: tuple[Path, Path] | None = None,
) -> dict[str, Any]:
    runtime, helper = verified_runtime or _verified_runtime()
    invocation = [
        sys.executable,
        "-S",
        "-B",
        str(helper),
        "--runtime-dir",
        str(runtime),
        "--queue-namespace",
        QUEUE_NAMESPACE,
        command,
        *arguments,
    ]
    try:
        completed = subprocess.run(
            invocation,
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(runtime),
            env=_helper_environment(),
            timeout=timeout_seconds,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise FinalCallbackError("final-callback helper invocation failed") from exc
    if len(completed.stdout) > MAX_HELPER_OUTPUT_BYTES:
        raise FinalCallbackError("final-callback helper output is too large")
    try:
        result = json.loads(completed.stdout.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FinalCallbackError("final-callback helper output is invalid") from exc
    if completed.returncode != 0 or not isinstance(result, dict) or result.get("ok") is not True:
        raise FinalCallbackError("final-callback helper rejected the request")
    return result


def _require_arguments(arguments: Any, required: set[str]) -> dict[str, Any]:
    if not isinstance(arguments, dict) or set(arguments) != required:
        raise FinalCallbackError("invalid tool arguments")
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
        raise FinalCallbackError(f"invalid tool field: {name}")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise FinalCallbackError(f"invalid Unicode tool field: {name}") from exc
    return value


def _require_page(values: dict[str, Any]) -> str:
    page = _require_text(values, "page", minimum=32, maximum=32)
    if re.fullmatch(r"[A-Fa-f0-9]{32}", page) is None:
        raise FinalCallbackError("invalid Beeper page")
    return page.lower()


def _require_final_callback_capability(values: dict[str, Any]) -> str:
    capability = _require_text(values, "final_callback_capability", minimum=32, maximum=32)
    if re.fullmatch(r"[A-Fa-f0-9]{32}", capability) is None:
        raise FinalCallbackError("invalid Final Callback capability")
    return capability.lower()


def _require_code(values: dict[str, Any]) -> str:
    code = _require_text(values, "code", maximum=80)
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,79}", code) is None:
        raise FinalCallbackError("invalid Beeper failure code")
    return code


def _responder_thread_id(value: Any) -> str:
    """Mirror bridge_core.looks_like_thread_id without importing mutable runtime."""

    if not isinstance(value, str):
        raise FinalCallbackError("current claim helper response is invalid")
    candidate = value.strip()
    if RESPONDER_THREAD_PATTERN.fullmatch(candidate) is None or not (
        len(candidate) >= 24
        or "-" in candidate
        or candidate.startswith(("thr_", "thread_"))
    ):
        raise FinalCallbackError("current claim helper response is invalid")
    return candidate


def _exact_thread_uuid(value: Any) -> str:
    if not isinstance(value, str) or re.fullmatch(
        r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}",
        value,
    ) is None:
        raise FinalCallbackError("current read task id is invalid")
    return value


def _closed_text(
    value: Any,
    *,
    maximum: int,
    allow_empty: bool = False,
) -> str:
    if (
        not isinstance(value, str)
        or len(value) > maximum
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        or (not allow_empty and not value)
    ):
        raise FinalCallbackError("current read text field is invalid")
    return value


def _closed_read_claim_request(operation: str, request: Any) -> dict[str, Any]:
    if not isinstance(request, dict):
        raise FinalCallbackError("current read claim helper response is invalid")
    if operation == "list_task_catalog":
        if set(request) != {
            "catalog_version",
            "visibility",
            "thread_ids",
            "include_archived",
            "limit",
            "excluded_thread_ids",
            "snapshot_id",
        }:
            raise FinalCallbackError("current catalog claim schema is invalid")
        thread_ids = request.get("thread_ids")
        excluded = request.get("excluded_thread_ids")
        if (
            request.get("catalog_version") != 1
            or request.get("visibility") not in {"all", "exact"}
            or not isinstance(thread_ids, list)
            or len(thread_ids) > 20
            or request.get("include_archived") is not False
            or type(request.get("limit")) is not int
            or not 1 <= request["limit"] <= 50
            or not isinstance(excluded, list)
            or len(excluded) > 201
            or not isinstance(request.get("snapshot_id"), str)
            or re.fullmatch(r"[a-f0-9]{32}", request["snapshot_id"]) is None
        ):
            raise FinalCallbackError("current catalog claim is invalid")
        normalized_threads = tuple(_exact_thread_uuid(item) for item in thread_ids)
        normalized_excluded = tuple(_exact_thread_uuid(item) for item in excluded)
        if (
            normalized_threads != tuple(sorted(set(normalized_threads)))
            or normalized_excluded != tuple(sorted(set(normalized_excluded)))
            or (request["visibility"] == "all" and normalized_threads)
            or any(item in normalized_excluded for item in normalized_threads)
        ):
            raise FinalCallbackError("current catalog claim is not canonical")
        return request
    if operation != "inspect_thread" or set(request) != {
        "responder_thread_id",
        "display_name",
        "catalog_snapshot_id",
        "expected_project_id",
        "expected_host_id",
        "selection_proof",
        "excluded_thread_ids",
        "operation_receipt",
    }:
        raise FinalCallbackError("current inspection claim schema is invalid")
    responder_thread_id = _exact_thread_uuid(request.get("responder_thread_id"))
    _closed_text(request.get("display_name"), maximum=240, allow_empty=True)
    _closed_text(request.get("expected_project_id"), maximum=200)
    _closed_text(request.get("expected_host_id"), maximum=200, allow_empty=True)
    snapshot_id = request.get("catalog_snapshot_id")
    selection_proof = request.get("selection_proof")
    operation_receipt = request.get("operation_receipt")
    excluded = request.get("excluded_thread_ids")
    if (
        not isinstance(snapshot_id, str)
        or re.fullmatch(r"[a-f0-9]{32}", snapshot_id) is None
        or not isinstance(selection_proof, str)
        or re.fullmatch(r"[a-f0-9]{64}", selection_proof) is None
        or not isinstance(operation_receipt, str)
        or re.fullmatch(r"[a-f0-9]{32}", operation_receipt) is None
        or not isinstance(excluded, list)
        or len(excluded) > 201
    ):
        raise FinalCallbackError("current inspection claim is invalid")
    normalized_excluded = tuple(_exact_thread_uuid(item) for item in excluded)
    if (
        normalized_excluded != tuple(sorted(set(normalized_excluded)))
        or responder_thread_id in normalized_excluded
    ):
        raise FinalCallbackError("current inspection claim is not canonical")
    return request


def _minimal_read_claim_result(payload: dict[str, Any]) -> dict[str, Any]:
    if set(payload) != {"ok", "status", "operation", "request"} or payload.get(
        "ok"
    ) is not True:
        raise FinalCallbackError("current read claim helper response is invalid")
    if payload.get("status") != "claimed_readonly":
        raise FinalCallbackError("current read claim helper response is invalid")
    operation = payload.get("operation")
    if operation not in {"list_task_catalog", "inspect_thread"}:
        raise FinalCallbackError("current read claim helper response is invalid")
    request = _closed_read_claim_request(operation, payload.get("request"))
    return {
        "ok": True,
        "status": "claimed_readonly",
        "operation": operation,
        "request": request,
    }


def _closed_readonly_result(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise FinalCallbackError("current read result is invalid")
    if set(value) == {
        "catalog_version",
        "snapshot_id",
        "include_archived",
        "truncated",
        "projects",
        "tasks",
    }:
        projects = value.get("projects")
        tasks = value.get("tasks")
        if (
            value.get("catalog_version") != 1
            or not isinstance(value.get("snapshot_id"), str)
            or re.fullmatch(r"[a-f0-9]{32}", value["snapshot_id"]) is None
            or value.get("include_archived") is not False
            or type(value.get("truncated")) is not bool
            or not isinstance(projects, list)
            or len(projects) > 50
            or not isinstance(tasks, list)
            or len(tasks) > 50
        ):
            raise FinalCallbackError("current catalog result is invalid")
        for project in projects:
            if not isinstance(project, dict) or set(project) != {
                "project_id",
                "label",
                "host_id",
                "kind",
            }:
                raise FinalCallbackError("current catalog project is invalid")
            _closed_text(project.get("project_id"), maximum=200)
            _closed_text(project.get("label"), maximum=160)
            _closed_text(project.get("host_id"), maximum=200, allow_empty=True)
            _closed_text(project.get("kind"), maximum=40)
        for task in tasks:
            if not isinstance(task, dict) or set(task) != {
                "thread_id",
                "title",
                "project_id",
                "host_id",
                "kind",
                "status",
                "archived",
                "updated_at",
            }:
                raise FinalCallbackError("current catalog task is invalid")
            _exact_thread_uuid(task.get("thread_id"))
            _closed_text(task.get("title"), maximum=240)
            _closed_text(task.get("project_id"), maximum=200)
            _closed_text(task.get("host_id"), maximum=200, allow_empty=True)
            _closed_text(task.get("status"), maximum=80, allow_empty=True)
            updated_at = task.get("updated_at")
            if (
                task.get("kind") != "codex"
                or task.get("archived") is not False
                or type(updated_at) not in {int, float}
                or not math.isfinite(float(updated_at))
                or float(updated_at) < 0
            ):
                raise FinalCallbackError("current catalog task is invalid")
        return value
    if set(value) != {
        "thread_id",
        "project_id",
        "host_id",
        "archived",
        "catalog_snapshot_id",
        "operation_receipt",
    }:
        raise FinalCallbackError("current inspection result schema is invalid")
    _exact_thread_uuid(value.get("thread_id"))
    _closed_text(value.get("project_id"), maximum=200)
    _closed_text(value.get("host_id"), maximum=200, allow_empty=True)
    if value.get("archived") is not False:
        raise FinalCallbackError("current inspection result is invalid")
    for name in ("catalog_snapshot_id", "operation_receipt"):
        candidate = value.get(name)
        if not isinstance(candidate, str) or re.fullmatch(
            r"[a-f0-9]{32}", candidate
        ) is None:
            raise FinalCallbackError("current inspection result is invalid")
    return value


def _answer_free_submission_result(payload: dict[str, Any]) -> dict[str, Any]:
    if set(payload) != {"ok", "accepted", "state"} or payload.get("ok") is not True:
        raise FinalCallbackError("Final Callback helper response is invalid")
    accepted = payload.get("accepted")
    state = payload.get("state")
    if type(accepted) is not bool or state not in {"captured", "conflict", "expired"}:
        raise FinalCallbackError("Final Callback helper response is invalid")
    if accepted != (state == "captured"):
        raise FinalCallbackError("Final Callback helper response is inconsistent")
    return {"ok": True, "accepted": accepted, "state": state}


def _minimal_claim_result(payload: dict[str, Any]) -> dict[str, Any]:
    expected = {
        "ok",
        "status",
        "responder_thread_id",
        "responder_host_id",
        "prompt",
    }
    if set(payload) != expected or payload.get("ok") is not True:
        raise FinalCallbackError("current claim helper response is invalid")
    if payload.get("status") != "claimed_armed":
        raise FinalCallbackError("current claim helper response is invalid")
    responder_thread_id = _responder_thread_id(payload.get("responder_thread_id"))
    responder_host_id = payload.get("responder_host_id")
    prompt = payload.get("prompt")
    if (
        not isinstance(responder_host_id, str)
        or len(responder_host_id) > 200
        or not isinstance(prompt, str)
        or not prompt
        or len(prompt) > 250_000
    ):
        raise FinalCallbackError("current claim helper response is invalid")
    return {
        "ok": True,
        "status": "claimed_armed",
        "responder_thread_id": responder_thread_id,
        "responder_host_id": responder_host_id,
        "prompt": prompt,
    }


def _answer_free_terminal_result(payload: dict[str, Any]) -> dict[str, Any]:
    if set(payload) != {"ok", "status", "terminal"} or payload.get("ok") is not True:
        raise FinalCallbackError("Final Callback control response is invalid")
    status = payload.get("status")
    terminal = payload.get("terminal")
    waiting_states = {"waiting_final_callback", "waiting_readonly"}
    if status not in {*waiting_states, "completed", "failed"}:
        raise FinalCallbackError("Final Callback control response is invalid")
    if type(terminal) is not bool or terminal != (status not in waiting_states):
        raise FinalCallbackError("Final Callback control response is inconsistent")
    return {"ok": True, "status": status, "terminal": terminal}


def _call_tool(name: str, arguments: Any) -> dict[str, Any]:
    if name == "claim_and_arm":
        values = _require_arguments(arguments, {"page"})
        page = _require_page(values)
        return _minimal_claim_result(
            _invoke_helper_command(
                "claim-and-arm",
                ["--page", page],
            )
        )
    if name == "claim_readonly":
        values = _require_arguments(arguments, {"page"})
        page = _require_page(values)
        return _minimal_read_claim_result(
            _invoke_helper_command(
                "claim-readonly",
                ["--page", page],
            )
        )
    if name == "complete_readonly":
        values = _require_arguments(arguments, {"page", "result"})
        page = _require_page(values)
        result = _closed_readonly_result(values.get("result"))
        wire = json.dumps(
            result,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return _answer_free_terminal_result(
            _invoke_helper_command(
                "complete-readonly",
                ["--page", page],
                input_bytes=wire,
            )
        )
    if name == "submit_final_callback":
        values = _require_arguments(arguments, {"final_callback_capability", "final_answer"})
        final_callback_capability = _require_final_callback_capability(values)
        final_answer = _require_text(values, "final_answer", maximum=12_000)
        if not final_answer.strip():
            raise FinalCallbackError("Final Callback answer is empty")
        wire = json.dumps(
            {"final_callback_capability": final_callback_capability, "final_answer": final_answer},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return _answer_free_submission_result(
            _invoke_helper_command(
                "submit-final-callback",
                [],
                input_bytes=wire,
            )
        )
    if name == "finish_final_callback":
        values = _require_arguments(arguments, {"page", "wait_seconds"})
        page = _require_page(values)
        wait_seconds = values.get("wait_seconds")
        if type(wait_seconds) is not int or not 0 <= wait_seconds <= 30:
            raise FinalCallbackError("invalid final-callback wait duration")
        return _answer_free_terminal_result(
            _invoke_helper_command(
                "finish-final-callback",
                ["--page", page, "--wait-seconds", str(wait_seconds)],
                timeout_seconds=wait_seconds + 6,
            )
        )
    if name == "fail_page":
        values = _require_arguments(
            arguments,
            {"page", "code", "may_have_started"},
        )
        page = _require_page(values)
        code = _require_code(values)
        if code in INTERNAL_UNCLAIMED_FAILURE_CODES:
            raise FinalCallbackError(
                "unclaimed Beeper failures are reserved for the Bridge CAS"
            )
        may_have_started = values.get("may_have_started")
        if type(may_have_started) is not bool:
            raise FinalCallbackError("invalid may-have-started marker")
        helper_arguments = ["--page", page, "--code", code]
        if may_have_started:
            helper_arguments.append("--may-have-started")
        return _answer_free_terminal_result(
            _invoke_helper_command("fail-page", helper_arguments)
        )
    raise FinalCallbackError("unknown final-callback tool")


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
        except FinalCallbackError as exc:
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
