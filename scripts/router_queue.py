"""CLI used by the dedicated single-task Codex Desktop Gateway.

This helper only reads and writes the bridge queue. It never starts Codex and
never opens a target task.
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
from bridge_core.desktop_router import DesktopRouterQueue, RouterProtocolError


MAX_TERMINAL_TEXT_CHARS = 12_000
MAX_STRUCTURED_RESULT_CHARS = 250_000
MAX_STAGING_FILE_BYTES = 1_000_000
MAX_HOOK_EVENT_BYTES = 1_000_000
FINAL_RETURN_REGISTRY_SCHEMA_VERSION = 1


def _read_staged_text(path: Path, *, max_chars: int) -> str | None:
    try:
        if path.stat().st_size > MAX_STAGING_FILE_BYTES:
            raise RouterProtocolError("Desktop Gateway staged text is too large")
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    if len(text) <= max_chars:
        return text
    suffix = "\n[已截断]"
    return text[: max_chars - len(suffix)].rstrip() + suffix


def _read_staged_result(path: Path) -> dict[str, Any]:
    """Read one structured result without applying answer-text truncation."""

    try:
        if path.stat().st_size > MAX_STAGING_FILE_BYTES:
            raise RouterProtocolError("Desktop Gateway structured result is too large")
        raw_result = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RouterProtocolError(
            "Desktop Gateway structured result staging file is missing"
        ) from exc
    if len(raw_result) > MAX_STRUCTURED_RESULT_CHARS:
        raise RouterProtocolError("Desktop Gateway structured result is too large")
    try:
        result = json.loads(raw_result)
    except json.JSONDecodeError as exc:
        raise RouterProtocolError(
            "Desktop Gateway structured result is not valid JSON"
        ) from exc
    if not isinstance(result, dict):
        raise RouterProtocolError(
            "Desktop Gateway structured result must be one JSON object"
        )
    return result


def _runtime_dir(value: str) -> Path:
    configured = value.strip() or os.environ.get("CODEX_BRIDGE_RUNTIME_DIR", "").strip()
    if configured:
        return Path(configured).resolve()
    return (Path.cwd() / ".codex" / "feishu-bridge").resolve()


def _emit(payload: dict[str, Any]) -> None:
    # This stdout crosses a native Python -> PowerShell -> Desktop tool boundary
    # on Windows.  Keep the wire representation ASCII-only so the shell cannot
    # decode UTF-8 message bytes with an OEM/ANSI code page.  JSON consumers
    # recover the original Unicode after exactly one parse.
    print(json.dumps(payload, ensure_ascii=True, separators=(",", ":")))


def _assert_no_reparse_path_chain(path: Path) -> Path:
    absolute = Path(os.path.abspath(os.fspath(path)))
    if not absolute.is_absolute():
        raise RouterProtocolError("final-return registry runtime path is not absolute")
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
            raise RouterProtocolError("final-return registry refuses a reparse path")
    resolved = absolute.resolve(strict=True)
    if os.path.normcase(str(resolved)) != os.path.normcase(str(absolute)):
        raise RouterProtocolError(
            "final-return registry runtime path changed during resolution"
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
        raise RouterProtocolError(
            "final-return registry runtime path is unavailable"
        ) from exc
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RouterProtocolError("final-return registry requires a valid runtime manifest") from exc
    code_files = manifest.get("code_files") if isinstance(manifest, dict) else None
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != 1
        or not isinstance(code_files, dict)
    ):
        raise RouterProtocolError("final-return registry runtime manifest is unsupported")
    required = (
        "router_queue.py",
        "bridge_core/__init__.py",
        "bridge_core/config.py",
        "bridge_core/desktop_router.py",
    )
    for relative in required:
        expected = code_files.get(relative)
        if not isinstance(expected, str) or re.fullmatch(r"[a-f0-9]{64}", expected) is None:
            raise RouterProtocolError("final-return registry manifest is incomplete")
        try:
            candidate = _assert_no_reparse_path_chain(runtime / Path(relative))
            actual = _sha256_file(candidate)
        except (OSError, RuntimeError) as exc:
            raise RouterProtocolError(
                "final-return registry runtime file is unavailable"
            ) from exc
        if actual != expected:
            raise RouterProtocolError("final-return registry runtime integrity failed")
    return runtime


def _final_return_registry_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if not local_app_data:
        raise RouterProtocolError("LOCALAPPDATA is unavailable for final-return registry")
    root = Path(local_app_data).resolve()
    return root / "OpenAI" / "Codex" / "feishu-codex-final-return" / "registration.json"


def _read_final_return_registry() -> dict[str, Any] | None:
    path = _final_return_registry_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        raise RouterProtocolError("final-return registry is unreadable") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise RouterProtocolError("final-return registry is invalid")
    return payload


def _write_final_return_registry(payload: dict[str, Any]) -> None:
    path = _final_return_registry_path()
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


def _register_final_return_runtime(runtime_dir: Path, *, force: bool) -> dict[str, Any]:
    runtime = _verified_runtime_dir(runtime_dir)
    existing = _read_final_return_registry()
    existing_runtime = str((existing or {}).get("runtime_dir") or "").strip()
    same_runtime = False
    if existing_runtime:
        try:
            same_runtime = Path(existing_runtime).resolve() == runtime
        except (OSError, RuntimeError):
            same_runtime = False
        if not same_runtime and not force:
            raise RouterProtocolError(
                "a different Bridge runtime already owns the final-return plugin registry"
            )
    registered_at = time.time()
    if same_runtime:
        try:
            registered_at = float((existing or {}).get("registered_at") or registered_at)
        except (TypeError, ValueError):
            registered_at = time.time()
    _write_final_return_registry(
        {
            "schema_version": FINAL_RETURN_REGISTRY_SCHEMA_VERSION,
            "runtime_dir": str(runtime),
            "registered_at": registered_at,
            "updated_at": time.time(),
        }
    )
    return {"configured": True, "matches_runtime": True, "runtime_valid": True}


def _final_return_registry_status(runtime_dir: Path) -> dict[str, Any]:
    existing = _read_final_return_registry()
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
        except (OSError, RuntimeError, RouterProtocolError):
            runtime_valid = False
    return {
        "configured": True,
        "matches_runtime": matches,
        "runtime_valid": runtime_valid,
    }


def _unregister_final_return_runtime(runtime_dir: Path) -> dict[str, Any]:
    status = _final_return_registry_status(runtime_dir)
    if not status["configured"]:
        return status
    if not status["matches_runtime"]:
        raise RouterProtocolError("final-return registry belongs to a different runtime")
    path = _final_return_registry_path()
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    return {
        "configured": False,
        "matches_runtime": False,
        "runtime_valid": False,
    }


def _read_hook_event() -> dict[str, Any]:
    raw = sys.stdin.buffer.read(MAX_HOOK_EVENT_BYTES + 1)
    if len(raw) > MAX_HOOK_EVENT_BYTES:
        raise RouterProtocolError("final-return Hook event is too large")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RouterProtocolError("final-return Hook event is not strict UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise RouterProtocolError("final-return Hook event must be one JSON object")
    return payload


def _runtime_settings(runtime_dir: Path) -> dict[str, int]:
    values = {
        "heartbeat_ttl_seconds": 90,
        "claim_ttl_seconds": 7200,
        "retention_hours": 168,
        "wake_lease_ttl_seconds": 180,
        "scheduler_ttl_seconds": 300,
        "grace_wait_max_seconds": 30,
    }
    names = {
        "CODEX_BRIDGE_ROUTER_HEARTBEAT_TTL": ("heartbeat_ttl_seconds", 15, 3600),
        "CODEX_BRIDGE_ROUTER_CLAIM_TTL": ("claim_ttl_seconds", 60, 86400),
        "CODEX_BRIDGE_ROUTER_RETENTION_HOURS": ("retention_hours", 1, 8760),
        "CODEX_BRIDGE_ROUTER_WAKE_TTL": ("wake_lease_ttl_seconds", 60, 900),
        "CODEX_BRIDGE_GATEWAY_SCHEDULER_TTL": ("scheduler_ttl_seconds", 120, 3600),
        "CODEX_BRIDGE_ROUTER_GRACE_MAX_SECONDS": ("grace_wait_max_seconds", 0, 60),
    }
    env_path = runtime_dir / "bridge.env"
    try:
        lines = env_path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise RouterProtocolError(f"Bridge environment could not be read: {exc}") from exc
    seen_names: set[str] = set()
    file_values: dict[str, str] = {}
    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "\x00" in stripped:
            raise RouterProtocolError(
                f"Bridge environment line {line_number} contains a NUL byte"
            )
        if "=" not in stripped:
            raise RouterProtocolError(
                f"Bridge environment line {line_number} is not NAME=VALUE"
            )
        name, raw_value = stripped.split("=", 1)
        name = name.strip()
        if re.fullmatch(r"CODEX_BRIDGE_[A-Z0-9_]+", name) is None:
            raise RouterProtocolError(
                f"Bridge environment line {line_number} has an unsupported key"
            )
        normalized_name = name.casefold()
        if normalized_name in seen_names:
            raise RouterProtocolError(
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
            raise RouterProtocolError(
                f"Bridge environment value for {name} is not an integer"
            ) from exc
        if parsed < minimum or parsed > maximum:
            raise RouterProtocolError(
                f"Bridge environment value for {name} is outside {minimum}..{maximum}"
            )
        values[setting] = parsed
    semantic_issues = validate_bridge_env_values(file_values)
    if semantic_issues:
        raise RouterProtocolError(semantic_issues[0])
    return values


def _queue(args: argparse.Namespace) -> DesktopRouterQueue:
    runtime_dir = _runtime_dir(args.runtime_dir)
    return DesktopRouterQueue(runtime_dir, **_runtime_settings(runtime_dir))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Codex Desktop Gateway queue helper")
    parser.add_argument("--runtime-dir", default="")
    subcommands = parser.add_subparsers(dest="command", required=True)

    register = subcommands.add_parser("register")
    register.add_argument("--router-thread-id", required=True)
    register.add_argument("--host-id", default="")
    register.add_argument("--force", action="store_true")

    heartbeat = subcommands.add_parser("heartbeat")
    heartbeat.add_argument("--router-thread-id", required=True)
    heartbeat.add_argument("--host-id", default="")
    heartbeat.add_argument("--wake-id", required=True)
    heartbeat.add_argument("--fence-token", required=True)

    subcommands.add_parser("status")
    sentinel = subcommands.add_parser("sentinel-probe")
    sentinel.add_argument("--router-thread-id", required=True)
    sentinel.add_argument("--host-id", default="")
    sentinel.add_argument("--manual-ticket", default="")

    manual_authorize = subcommands.add_parser("manual-authorize")
    manual_authorize.add_argument("--router-thread-id", required=True)
    manual_authorize.add_argument("--host-id", default="")
    manual_authorize.add_argument("--expected-operation", required=True)
    manual_authorize.add_argument("--ttl-seconds", type=int, default=300)

    claim = subcommands.add_parser("claim")
    claim.add_argument("--router-thread-id", required=True)
    claim.add_argument("--host-id", default="")
    claim.add_argument("--wake-id", required=True)
    claim.add_argument("--fence-token", required=True)
    claim.add_argument("--wait-seconds", type=int, default=0)
    claim.add_argument("--release-on-empty", action="store_true")

    release = subcommands.add_parser("release")
    release.add_argument("--wake-id", required=True)
    release.add_argument("--fence-token", required=True)
    release.add_argument("--reason", default="drained")

    stage = subcommands.add_parser("stage-path")
    stage.add_argument("--request-id", required=True)
    stage.add_argument("--fence-token", required=True)

    complete = subcommands.add_parser("complete")
    complete.add_argument("--request-id", required=True)
    complete.add_argument("--thread-id", default="")
    complete.add_argument("--host-id", default="")
    complete.add_argument("--turn-id", default="")
    complete.add_argument("--cursor", default="")
    complete.add_argument("--archived-thread-id", action="append", default=[])
    complete.add_argument("--structured-result", action="store_true")
    complete.add_argument("--fence-token", required=True)

    fail = subcommands.add_parser("fail")
    fail.add_argument("--request-id", required=True)
    fail.add_argument("--code", required=True)
    fail.add_argument("--retryable", action="store_true")
    fail.add_argument("--may-have-started", action="store_true")
    fail.add_argument("--fence-token", required=True)

    arm_final = subcommands.add_parser("final-return-arm")
    arm_final.add_argument("--request-id", required=True)
    arm_final.add_argument("--fence-token", required=True)
    arm_final.add_argument("--thread-id", required=True)

    final_status = subcommands.add_parser("final-return-status")
    final_status.add_argument("--request-id", required=True)
    final_status.add_argument("--fence-token", required=True)
    final_status.add_argument("--thread-id", required=True)
    final_status.add_argument("--turn-id", required=True)

    final_native = subcommands.add_parser("final-return-native")
    final_native.add_argument("--request-id", required=True)
    final_native.add_argument("--fence-token", required=True)
    final_native.add_argument("--thread-id", required=True)
    final_native.add_argument("--turn-id", required=True)

    subcommands.add_parser("final-return-hook")
    final_register = subcommands.add_parser("final-return-register")
    final_register.add_argument("--force", action="store_true")
    subcommands.add_parser("final-return-unregister")
    subcommands.add_parser("final-return-registry-status")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "final-return-register":
            _emit(
                {
                    "ok": True,
                    **_register_final_return_runtime(
                        _runtime_dir(args.runtime_dir),
                        force=args.force,
                    ),
                }
            )
            return 0
        if args.command == "final-return-unregister":
            _emit(
                {
                    "ok": True,
                    **_unregister_final_return_runtime(_runtime_dir(args.runtime_dir)),
                }
            )
            return 0
        if args.command == "final-return-registry-status":
            _emit(
                {
                    "ok": True,
                    **_final_return_registry_status(_runtime_dir(args.runtime_dir)),
                }
            )
            return 0
        queue = _queue(args)
        if args.command == "register":
            queue.register(args.router_thread_id, args.host_id, force=args.force)
            _emit({"ok": True, **queue.status().as_dict()})
            return 0
        if args.command == "heartbeat":
            queue.renew_wake(
                args.wake_id,
                args.fence_token,
                args.router_thread_id,
                args.host_id,
            )
            _emit({"ok": True, **queue.status().as_dict()})
            return 0
        if args.command == "status":
            _emit({"ok": True, **queue.status().as_dict()})
            return 0
        if args.command == "sentinel-probe":
            probe = (
                queue.manual_probe(
                    args.manual_ticket,
                    args.router_thread_id,
                    args.host_id,
                )
                if args.manual_ticket
                else queue.sentinel_probe(args.router_thread_id, args.host_id)
            )
            _emit(
                {
                    "ok": True,
                    **probe,
                }
            )
            return 0
        if args.command == "manual-authorize":
            _emit(
                {
                    "ok": True,
                    **queue.authorize_manual_cycle(
                        args.router_thread_id,
                        args.host_id,
                        args.expected_operation,
                        ttl_seconds=args.ttl_seconds,
                    ),
                }
            )
            return 0
        if args.command == "claim":
            request = queue.claim(
                args.router_thread_id,
                args.host_id,
                wake_id=args.wake_id,
                fence_token=args.fence_token,
                wait_seconds=args.wait_seconds,
                release_on_empty=args.release_on_empty,
            )
            _emit({"ok": True, "request": request})
            return 0
        if args.command == "release":
            _emit(
                {
                    "ok": True,
                    **queue.release_wake(
                        args.wake_id,
                        args.fence_token,
                        reason=args.reason,
                    ),
                }
            )
            return 0
        if args.command == "stage-path":
            _emit(
                {
                    "ok": True,
                    "path": str(queue.stage_path(args.request_id, args.fence_token)),
                }
            )
            return 0
        if args.command == "complete":
            stage_path = queue.stage_path(args.request_id, args.fence_token)
            if args.structured_result:
                result = _read_staged_result(stage_path)
            else:
                text = _read_staged_text(
                    stage_path,
                    max_chars=MAX_TERMINAL_TEXT_CHARS,
                ) or ""
                result = {
                    "thread_id": args.thread_id.strip(),
                    "host_id": args.host_id.strip(),
                    "turn_id": args.turn_id.strip(),
                    "cursor": args.cursor.strip(),
                    "text": text,
                    "archived_thread_ids": [
                        item.strip() for item in args.archived_thread_id if item.strip()
                    ],
                }
            queue.complete(args.request_id, result, fence_token=args.fence_token)
            try:
                stage_path.unlink()
            except OSError:
                pass
            _emit({"ok": True, "request_id": args.request_id})
            return 0
        if args.command == "fail":
            stage_path = queue.stage_path(args.request_id, args.fence_token)
            message = _read_staged_text(stage_path, max_chars=4000) or args.code
            queue.fail(
                args.request_id,
                code=args.code,
                message=message,
                retryable=args.retryable,
                may_have_started=args.may_have_started,
                fence_token=args.fence_token,
            )
            try:
                stage_path.unlink()
            except OSError:
                pass
            _emit({"ok": True, "request_id": args.request_id})
            return 0
        if args.command == "final-return-arm":
            _emit(
                {
                    "ok": True,
                    **queue.arm_final_return(
                        args.request_id,
                        args.fence_token,
                        args.thread_id,
                    ),
                }
            )
            return 0
        if args.command == "final-return-status":
            _emit(
                {
                    "ok": True,
                    **queue.final_return_status(
                        args.request_id,
                        args.fence_token,
                        args.thread_id,
                        args.turn_id,
                    ),
                }
            )
            return 0
        if args.command == "final-return-native":
            _emit(
                {
                    "ok": True,
                    **queue.resolve_final_return_native(
                        args.request_id,
                        args.fence_token,
                        args.thread_id,
                        args.turn_id,
                    ),
                }
            )
            return 0
        if args.command == "final-return-hook":
            event = _read_hook_event()
            event_name = str(event.get("hook_event_name") or "")
            if event_name == "UserPromptSubmit":
                result = queue.bind_final_return_prompt(
                    str(event.get("session_id") or ""),
                    str(event.get("turn_id") or ""),
                    event.get("prompt"),
                )
            elif event_name == "Stop":
                if type(event.get("stop_hook_active")) is not bool:
                    raise RouterProtocolError("Stop Hook active marker must be boolean")
                result = queue.capture_final_return(
                    str(event.get("session_id") or ""),
                    str(event.get("turn_id") or ""),
                    event.get("last_assistant_message"),
                    stop_hook_active=event["stop_hook_active"],
                )
            else:
                raise RouterProtocolError("unsupported final-return Hook event")
            _emit({"ok": True, **result})
            return 0
    except RouterProtocolError as exc:
        _emit({"ok": False, "error": str(exc)})
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
