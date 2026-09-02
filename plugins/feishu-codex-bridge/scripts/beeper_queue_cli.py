"""CLI used by the dedicated single-task Codex Desktop Beeper.

This helper only reads and writes the bridge queue. It never starts Codex and
never opens a responder task.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
from typing import Any

from bridge_core.config import validate_bridge_env_values
from bridge_core.beeper_queue import (
    BeeperQueue,
    BeeperQueueProtocolError,
    looks_like_thread_id,
)
from bridge_core.legacy_identifiers import RETIRED_QUEUE_ROOT_NAME


MAX_FINAL_CALLBACK_EVENT_BYTES = 1_000_000
MAX_READONLY_RESULT_BYTES = 1_000_000
FINAL_CALLBACK_REGISTRY_SCHEMA_VERSION = 1
RETIRED_QUEUE_NAMESPACE = RETIRED_QUEUE_ROOT_NAME
QUEUE_NAMESPACE = "beeper"
QUEUE_NAMESPACES = (RETIRED_QUEUE_NAMESPACE, QUEUE_NAMESPACE)
QUEUE_COMMANDS = frozenset(
    {
        "register",
        "registration",
        "claim-and-arm",
        "claim-readonly",
        "complete-readonly",
        "finish-readonly",
        "tombstone-thread",
        "list-thread-tombstones",
        "submit-final-callback",
        "finish-final-callback",
        "fail-page",
    }
)


def _runtime_dir(value: str) -> Path:
    configured = value.strip() or os.environ.get("CODEX_BRIDGE_RUNTIME_DIR", "").strip()
    if configured:
        return Path(configured).resolve()
    return (Path.cwd() / ".codex" / "feishu-codex-bridge-runtime").resolve()


def _emit(payload: dict[str, Any]) -> None:
    # This stdout crosses a native Python -> PowerShell -> Desktop tool boundary
    # on Windows.  Keep the wire representation ASCII-only so the shell cannot
    # decode UTF-8 message bytes with an OEM/ANSI code page.  JSON consumers
    # recover the original Unicode after exactly one parse.
    print(json.dumps(payload, ensure_ascii=True, separators=(",", ":")))


def _answer_free_terminal(payload: dict[str, Any]) -> dict[str, Any]:
    """Expose only fixed control state to the Beeper MCP transport."""

    status = str(payload.get("status") or "")
    terminal = payload.get("terminal")
    waiting_states = {"waiting_final_callback", "waiting_readonly"}
    if status not in {*waiting_states, "completed", "failed"}:
        raise BeeperQueueProtocolError("controller state is invalid")
    if type(terminal) is not bool or terminal != (status not in waiting_states):
        raise BeeperQueueProtocolError("controller terminal marker is invalid")
    return {"ok": True, "status": status, "terminal": terminal}


def _minimal_claim(payload: dict[str, Any]) -> dict[str, Any]:
    """Return only the fields the Beeper needs for its one responder send."""

    status = payload.get("status")
    responder_thread_id = payload.get("responder_thread_id")
    responder_host_id = payload.get("responder_host_id")
    prompt = payload.get("prompt")
    if status != "claimed_armed":
        raise BeeperQueueProtocolError("current claim control state is invalid")
    if not isinstance(responder_thread_id, str) or not looks_like_thread_id(
        responder_thread_id
    ):
        raise BeeperQueueProtocolError("current claim responder identity is invalid")
    responder_thread_id = responder_thread_id.strip()
    if not isinstance(responder_host_id, str) or len(responder_host_id) > 200:
        raise BeeperQueueProtocolError(
            "current claim Responder host identity is invalid"
        )
    if not isinstance(prompt, str) or not prompt or len(prompt) > 250_000:
        raise BeeperQueueProtocolError("current claim prompt is invalid")
    return {
        "ok": True,
        "status": "claimed_armed",
        "responder_thread_id": responder_thread_id,
        "responder_host_id": responder_host_id,
        "prompt": prompt,
    }


def _exact_thread_uuid(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise BeeperQueueProtocolError(f"current {label} is invalid")
    candidate = value.strip().lower()
    if re.fullmatch(
        r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}",
        candidate,
    ) is None:
        raise BeeperQueueProtocolError(f"current {label} is invalid")
    return candidate


def _minimal_read_claim(payload: dict[str, Any]) -> dict[str, Any]:
    """Expose one closed read request without queue/fence/controller metadata."""

    if payload.get("status") != "claimed_readonly":
        raise BeeperQueueProtocolError("current read claim state is invalid")
    operation = payload.get("operation")
    request = payload.get("request")
    if operation not in {"list_task_catalog", "inspect_thread"} or not isinstance(
        request, dict
    ):
        raise BeeperQueueProtocolError("current read claim operation is invalid")
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
            raise BeeperQueueProtocolError("current catalog claim schema is invalid")
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
            raise BeeperQueueProtocolError("current catalog claim is invalid")
        normalized_threads = tuple(
            _exact_thread_uuid(item, "catalog task id") for item in thread_ids
        )
        normalized_excluded = tuple(
            _exact_thread_uuid(item, "excluded task id") for item in excluded
        )
        if (
            normalized_threads != tuple(sorted(set(normalized_threads)))
            or normalized_excluded != tuple(sorted(set(normalized_excluded)))
            or (
                request["visibility"] == "all"
                and normalized_threads
            )
            or any(item in normalized_excluded for item in normalized_threads)
        ):
            raise BeeperQueueProtocolError("current catalog claim is not canonical")
    else:
        if set(request) != {
            "responder_thread_id",
            "display_name",
            "catalog_snapshot_id",
            "expected_project_id",
            "expected_host_id",
            "selection_proof",
            "excluded_thread_ids",
            "operation_receipt",
        }:
            raise BeeperQueueProtocolError("current inspection claim schema is invalid")
        responder_thread_id = _exact_thread_uuid(
            request.get("responder_thread_id"), "inspection responder task id"
        )
        excluded = request.get("excluded_thread_ids")
        if (
            not isinstance(request.get("display_name"), str)
            or len(request["display_name"]) > 240
            or not isinstance(request.get("catalog_snapshot_id"), str)
            or re.fullmatch(r"[a-f0-9]{32}", request["catalog_snapshot_id"])
            is None
            or not isinstance(request.get("expected_project_id"), str)
            or not request["expected_project_id"]
            or len(request["expected_project_id"]) > 200
            or not isinstance(request.get("expected_host_id"), str)
            or len(request["expected_host_id"]) > 200
            or not isinstance(request.get("selection_proof"), str)
            or re.fullmatch(r"[a-f0-9]{64}", request["selection_proof"])
            is None
            or not isinstance(excluded, list)
            or len(excluded) > 201
            or not isinstance(request.get("operation_receipt"), str)
            or re.fullmatch(r"[a-f0-9]{32}", request["operation_receipt"])
            is None
        ):
            raise BeeperQueueProtocolError("current inspection claim is invalid")
        normalized_excluded = tuple(
            _exact_thread_uuid(item, "excluded task id") for item in excluded
        )
        if (
            normalized_excluded != tuple(sorted(set(normalized_excluded)))
            or responder_thread_id in normalized_excluded
        ):
            raise BeeperQueueProtocolError("current inspection claim is not canonical")
    return {
        "ok": True,
        "status": "claimed_readonly",
        "operation": operation,
        "request": request,
    }


def _assert_no_reparse_path_chain(path: Path) -> Path:
    absolute = Path(os.path.abspath(os.fspath(path)))
    if not absolute.is_absolute():
        raise BeeperQueueProtocolError("final-callback registry runtime path is not absolute")
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        metadata = current.stat(follow_symlinks=False)
        attributes = getattr(metadata, "st_file_attributes", 0)
        if current.is_symlink() or attributes & getattr(
            stat,
            "FILE_ATTRIBUTE_REPARSE_POINT",
            0x400,
        ):
            raise BeeperQueueProtocolError("final-callback registry refuses a reparse path")
    resolved = absolute.resolve(strict=True)
    if os.path.normcase(str(resolved)) != os.path.normcase(str(absolute)):
        raise BeeperQueueProtocolError(
            "final-callback registry runtime path changed during resolution"
        )
    return resolved


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verified_runtime_dir(runtime_dir: Path) -> Path:
    try:
        runtime = _assert_no_reparse_path_chain(runtime_dir)
        manifest_path = _assert_no_reparse_path_chain(
            runtime / "runtime-manifest.json"
        )
    except (OSError, RuntimeError) as exc:
        raise BeeperQueueProtocolError(
            "final-callback registry runtime path is unavailable"
        ) from exc
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BeeperQueueProtocolError("final-callback registry requires a valid runtime manifest") from exc
    code_files = manifest.get("code_files") if isinstance(manifest, dict) else None
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != 1
        or not isinstance(code_files, dict)
    ):
        raise BeeperQueueProtocolError("final-callback registry runtime manifest is unsupported")
    required = (
        "beeper_queue_cli.py",
        "bridge_core/__init__.py",
        "bridge_core/config.py",
        "bridge_core/beeper_queue.py",
        "bridge_core/legacy_identifiers.py",
    )
    for relative in required:
        expected = code_files.get(relative)
        if not isinstance(expected, str) or re.fullmatch(r"[a-f0-9]{64}", expected) is None:
            raise BeeperQueueProtocolError("final-callback registry manifest is incomplete")
        try:
            candidate = _assert_no_reparse_path_chain(runtime / Path(relative))
            actual = _sha256_file(candidate)
        except (OSError, RuntimeError) as exc:
            raise BeeperQueueProtocolError(
                "final-callback registry runtime file is unavailable"
            ) from exc
        if actual != expected:
            raise BeeperQueueProtocolError("final-callback registry runtime integrity failed")
    return runtime


def _final_callback_registry_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if not local_app_data:
        raise BeeperQueueProtocolError("LOCALAPPDATA is unavailable for final-callback registry")
    root = Path(local_app_data).resolve()
    # Compatibility namespace for already registered runtimes; this is not the
    # outer Codex plugin package ID and must migrate atomically with the callback server.
    return root / "OpenAI" / "Codex" / "feishu-codex-final-callback" / "registration.json"


def _read_final_callback_registry() -> dict[str, Any] | None:
    path = _final_callback_registry_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        raise BeeperQueueProtocolError("final-callback registry is unreadable") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise BeeperQueueProtocolError("final-callback registry is invalid")
    return payload


def _write_final_callback_registry(payload: dict[str, Any]) -> None:
    path = _final_callback_registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="") as stream:
            json.dump(payload, stream, ensure_ascii=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass


def _register_final_callback_runtime(runtime_dir: Path, *, force: bool) -> dict[str, Any]:
    runtime = _verified_runtime_dir(runtime_dir)
    existing = _read_final_callback_registry()
    existing_runtime = str((existing or {}).get("runtime_dir") or "").strip()
    same_runtime = False
    if existing_runtime:
        try:
            same_runtime = Path(existing_runtime).resolve() == runtime
        except (OSError, RuntimeError):
            same_runtime = False
        if not same_runtime and not force:
            raise BeeperQueueProtocolError(
                "a different Bridge runtime already owns the Final Callback registry"
            )
    registered_at = time.time()
    if same_runtime:
        try:
            registered_at = float((existing or {}).get("registered_at") or registered_at)
        except (TypeError, ValueError):
            registered_at = time.time()
    _write_final_callback_registry(
        {
            "schema_version": FINAL_CALLBACK_REGISTRY_SCHEMA_VERSION,
            "runtime_dir": str(runtime),
            "registered_at": registered_at,
            "updated_at": time.time(),
        }
    )
    return {"configured": True, "matches_runtime": True, "runtime_valid": True}


def _final_callback_registry_status(runtime_dir: Path) -> dict[str, Any]:
    existing = _read_final_callback_registry()
    if existing is None:
        return {
            "configured": False,
            "matches_runtime": False,
            "runtime_valid": False,
        }
    existing_runtime = str(existing.get("runtime_dir") or "").strip()
    if not existing_runtime or not Path(existing_runtime).is_absolute():
        return {"configured": True, "matches_runtime": False, "runtime_valid": False}
    try:
        matches = Path(existing_runtime).resolve() == runtime_dir.resolve()
    except (OSError, RuntimeError):
        matches = False
    runtime_valid = False
    if matches:
        try:
            _verified_runtime_dir(runtime_dir)
            runtime_valid = True
        except (OSError, RuntimeError, BeeperQueueProtocolError):
            runtime_valid = False
    return {
        "configured": True,
        "matches_runtime": matches,
        "runtime_valid": runtime_valid,
    }


def _unregister_final_callback_runtime(runtime_dir: Path) -> dict[str, Any]:
    status = _final_callback_registry_status(runtime_dir)
    if not status["configured"]:
        return status
    if not status["matches_runtime"]:
        raise BeeperQueueProtocolError("final-callback registry belongs to a different runtime")
    path = _final_callback_registry_path()
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    return {
        "configured": False,
        "matches_runtime": False,
        "runtime_valid": False,
    }


def _read_final_callback_submission() -> dict[str, Any]:
    raw = sys.stdin.buffer.read(MAX_FINAL_CALLBACK_EVENT_BYTES + 1)
    if len(raw) > MAX_FINAL_CALLBACK_EVENT_BYTES:
        raise BeeperQueueProtocolError("Final Callback submission is too large")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BeeperQueueProtocolError(
            "Final Callback submission is not strict UTF-8 JSON"
        ) from exc
    if not isinstance(payload, dict) or set(payload) != {"final_callback_capability", "final_answer"}:
        raise BeeperQueueProtocolError(
            "Final Callback submission must be one closed JSON object"
        )
    return payload


def _read_readonly_result() -> dict[str, Any]:
    raw = sys.stdin.buffer.read(MAX_READONLY_RESULT_BYTES + 1)
    if len(raw) > MAX_READONLY_RESULT_BYTES:
        raise BeeperQueueProtocolError(
            "Beeper read result is too large"
        )
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BeeperQueueProtocolError(
            "Beeper read result is not strict UTF-8 JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise BeeperQueueProtocolError(
            "Beeper read result must be one JSON object"
        )
    return payload


def _runtime_settings(runtime_dir: Path) -> dict[str, int]:
    values = {
        "claim_ttl_seconds": 7200,
        "retention_hours": 168,
        "dial_lease_ttl_seconds": 180,
        "grace_wait_max_seconds": 30,
    }
    names = {
        "CODEX_BRIDGE_BEEPER_CLAIM_TTL": ("claim_ttl_seconds", 60, 86400),
        "CODEX_BRIDGE_BEEPER_RETENTION_HOURS": ("retention_hours", 1, 8760),
        "CODEX_BRIDGE_BEEPER_DIAL_TTL": ("dial_lease_ttl_seconds", 60, 900),
        "CODEX_BRIDGE_BEEPER_GRACE_MAX_SECONDS": ("grace_wait_max_seconds", 0, 60),
    }
    env_path = runtime_dir / "bridge.env"
    try:
        lines = env_path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise BeeperQueueProtocolError(f"Bridge environment could not be read: {exc}") from exc
    seen_names: set[str] = set()
    file_values: dict[str, str] = {}
    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "\x00" in stripped:
            raise BeeperQueueProtocolError(
                f"Bridge environment line {line_number} contains a NUL byte"
            )
        if "=" not in stripped:
            raise BeeperQueueProtocolError(
                f"Bridge environment line {line_number} is not NAME=VALUE"
            )
        name, raw_value = stripped.split("=", 1)
        name = name.strip()
        if re.fullmatch(r"CODEX_BRIDGE_[A-Z0-9_]+", name) is None:
            raise BeeperQueueProtocolError(
                f"Bridge environment line {line_number} has an unsupported key"
            )
        normalized_name = name.casefold()
        if normalized_name in seen_names:
            raise BeeperQueueProtocolError(
                f"Bridge environment contains a duplicate key at line {line_number}: {name}"
            )
        seen_names.add(normalized_name)
        file_values[name] = raw_value.strip()
        specification = names.get(name)
        if specification is None:
            continue
        setting, minimum, maximum = specification
        try:
            parsed = int(raw_value.strip())
        except ValueError as exc:
            raise BeeperQueueProtocolError(
                f"Bridge environment value for {name} is not an integer"
            ) from exc
        if parsed < minimum or parsed > maximum:
            raise BeeperQueueProtocolError(
                f"Bridge environment value for {name} is outside {minimum}..{maximum}"
            )
        values[setting] = parsed
    semantic_issues = validate_bridge_env_values(file_values)
    if semantic_issues:
        raise BeeperQueueProtocolError(semantic_issues[0])
    return values


def _queue(
    args: argparse.Namespace,
    *,
    root_name: str | None = None,
) -> BeeperQueue:
    runtime_dir = _runtime_dir(args.runtime_dir)
    namespace = root_name or args.queue_namespace
    if namespace not in QUEUE_NAMESPACES:
        raise BeeperQueueProtocolError("unsupported Desktop Beeper queue namespace")
    return BeeperQueue(
        runtime_dir,
        root_name=namespace,
        **_runtime_settings(runtime_dir),
    )


def _require_namespace(args: argparse.Namespace) -> None:
    if args.queue_namespace != QUEUE_NAMESPACE:
        raise BeeperQueueProtocolError(
            "current command requires queue namespace beeper"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Codex Desktop Beeper queue helper")
    parser.add_argument("--runtime-dir", default="")
    parser.add_argument(
        "--queue-namespace",
        choices=QUEUE_NAMESPACES,
        default=RETIRED_QUEUE_NAMESPACE,
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    subcommands.add_parser("status")
    final_register = subcommands.add_parser("final-callback-register")
    final_register.add_argument("--force", action="store_true")
    subcommands.add_parser("final-callback-unregister")
    subcommands.add_parser("final-callback-registry-status")

    register = subcommands.add_parser("register")
    register.add_argument("--beeper-thread-id", required=True)
    register.add_argument("--beeper-host-id", default="")
    register.add_argument("--codex-exe-path", required=True)
    register.add_argument("--codex-exe-sha256", required=True)
    register.add_argument("--codex-version", required=True)

    subcommands.add_parser("registration")

    claim = subcommands.add_parser("claim-and-arm")
    claim.add_argument("--page", required=True)

    read_claim = subcommands.add_parser(
        "claim-readonly"
    )
    read_claim.add_argument("--page", required=True)

    read_complete = subcommands.add_parser(
        "complete-readonly"
    )
    read_complete.add_argument("--page", required=True)

    read_finish = subcommands.add_parser(
        "finish-readonly"
    )
    read_finish.add_argument("--page", required=True)
    read_finish.add_argument(
        "--wait-seconds",
        type=int,
        choices=range(0, 31),
        default=0,
        metavar="0..30",
    )

    tombstone = subcommands.add_parser(
        "tombstone-thread"
    )
    tombstone.add_argument("--thread-id", required=True)
    subcommands.add_parser("list-thread-tombstones")

    subcommands.add_parser("submit-final-callback")

    finish = subcommands.add_parser("finish-final-callback")
    finish.add_argument("--page", required=True)
    finish.add_argument(
        "--wait-seconds",
        type=int,
        choices=range(0, 31),
        default=0,
        metavar="0..30",
    )

    fail_page = subcommands.add_parser("fail-page")
    fail_page.add_argument("--page", required=True)
    fail_page.add_argument("--code", required=True)
    fail_page.add_argument("--may-have-started", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "final-callback-register":
            _emit(
                {
                    "ok": True,
                    **_register_final_callback_runtime(
                        _runtime_dir(args.runtime_dir),
                        force=args.force,
                    ),
                }
            )
            return 0
        if args.command == "final-callback-unregister":
            _emit(
                {
                    "ok": True,
                    **_unregister_final_callback_runtime(_runtime_dir(args.runtime_dir)),
                }
            )
            return 0
        if args.command == "final-callback-registry-status":
            _emit(
                {
                    "ok": True,
                    **_final_callback_registry_status(_runtime_dir(args.runtime_dir)),
                }
            )
            return 0
        if args.command in QUEUE_COMMANDS:
            _require_namespace(args)
        queue = _queue(args)
        if args.command == "register":
            queue.register(
                args.beeper_thread_id,
                args.beeper_host_id,
                args.codex_exe_path,
                args.codex_exe_sha256,
                args.codex_version,
            )
            _emit({"ok": True, **queue.registration()})
            return 0
        if args.command == "registration":
            _emit({"ok": True, **queue.registration()})
            return 0
        if args.command == "claim-and-arm":
            result = queue.claim_and_arm(args.page)
            _emit(_minimal_claim(result))
            return 0
        if args.command == "claim-readonly":
            result = queue.claim_readonly(args.page)
            _emit(_minimal_read_claim(result))
            return 0
        if args.command == "complete-readonly":
            result = queue.complete_readonly(
                args.page,
                _read_readonly_result(),
            )
            _emit(_answer_free_terminal(result))
            return 0
        if args.command == "finish-readonly":
            result = queue.finish_readonly(
                args.page,
                args.wait_seconds,
            )
            _emit(_answer_free_terminal(result))
            return 0
        if args.command == "tombstone-thread":
            thread_ids = queue.add_thread_tombstone(args.thread_id)
            _emit({"ok": True, "thread_ids": list(thread_ids)})
            return 0
        if args.command == "list-thread-tombstones":
            _emit(
                {
                    "ok": True,
                    "thread_ids": list(queue.thread_tombstones()),
                }
            )
            return 0
        if args.command == "submit-final-callback":
            submission = _read_final_callback_submission()
            result = queue.submit_final_callback(
                str(submission.get("final_callback_capability") or ""),
                submission.get("final_answer"),
            )
            _emit({"ok": True, **result})
            return 0
        if args.command == "finish-final-callback":
            result = queue.finish_final_callback(
                args.page,
                args.wait_seconds,
            )
            _emit(_answer_free_terminal(result))
            return 0
        if args.command == "fail-page":
            result = queue.fail_page(
                args.page,
                args.code,
                args.may_have_started,
            )
            _emit(_answer_free_terminal(result))
            return 0
        if args.command == "status":
            _emit({"ok": True, **queue.status().as_dict()})
            return 0
    except BeeperQueueProtocolError:
        # Protocol failures may wrap filesystem or SQLite diagnostics.  Never
        # echo their dynamic text across the native helper boundary.
        _emit({"ok": False, "error": "local helper request rejected"})
        return 2
    except Exception:
        # Native helper stdout is a strict one-object ASCII JSON transport.
        # Operational failures (for example an atomic staging write error)
        # must not escape as a traceback or disclose a local path.
        _emit({"ok": False, "error": "local helper operation failed"})
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
