"""Small ASCII-JSON helper for Final Callback runtime registration and routing."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any

from operator_core.final_callback import FinalCallbackStore, FinalCallbackStoreError


REQUIRED_RUNTIME_FILES = (
    "routing_cli.py",
    "operator_core/__init__.py",
    "operator_core/final_callback.py",
)


class RoutingError(RuntimeError):
    pass


def _emit(payload: dict[str, Any]) -> None:
    wire = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    sys.stdout.buffer.write(wire.encode("ascii") + b"\n")
    sys.stdout.buffer.flush()


def _runtime_dir(value: str) -> Path:
    runtime = Path(value).resolve()
    if not runtime.is_absolute() or not runtime.is_dir():
        raise RoutingError("runtime directory is unavailable")
    return runtime


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify_runtime(runtime: Path) -> None:
    try:
        manifest = json.loads(
            (runtime / "runtime-manifest.json").read_text(encoding="utf-8-sig")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise RoutingError("runtime manifest is unavailable") from exc
    code_files = manifest.get("code_files") if isinstance(manifest, dict) else None
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1 or not isinstance(code_files, dict):
        raise RoutingError("runtime manifest is invalid")
    for relative in REQUIRED_RUNTIME_FILES:
        expected = code_files.get(relative)
        try:
            candidate = (runtime / relative).resolve(strict=True)
        except OSError as exc:
            raise RoutingError("runtime file is unavailable") from exc
        if (
            not isinstance(expected, str)
            or re.fullmatch(r"[a-f0-9]{64}", expected) is None
            or runtime not in candidate.parents
            or _sha256_file(candidate) != expected
        ):
            raise RoutingError("runtime integrity check failed")


def _registry_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if not local_app_data:
        raise RoutingError("LOCALAPPDATA is unavailable")
    return Path(local_app_data).resolve() / "OpenAI" / "Codex" / "feishu-codex-final-callback" / "registration.json"


def _read_registry() -> dict[str, Any] | None:
    try:
        value = json.loads(_registry_path().read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        raise RoutingError("Final Callback registry is unreadable") from exc
    if not isinstance(value, dict) or value.get("schema_version") not in {1, 2}:
        raise RoutingError("Final Callback registry is from an unsupported release")
    return value


def _write_registry(value: dict[str, Any]) -> None:
    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="") as stream:
            json.dump(value, stream, ensure_ascii=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass


def _status(runtime: Path) -> dict[str, Any]:
    registry = _read_registry()
    if registry is None:
        return {"configured": False, "matches_runtime": False, "runtime_valid": False}
    try:
        matches = Path(str(registry.get("runtime_dir") or "")).resolve() == runtime
    except OSError:
        matches = False
    valid = False
    if matches and registry.get("schema_version") == 2:
        try:
            _verify_runtime(runtime)
            valid = True
        except (OSError, RoutingError):
            pass
    return {"configured": True, "matches_runtime": matches, "runtime_valid": valid}


def _register(runtime: Path, *, force: bool) -> dict[str, Any]:
    _verify_runtime(runtime)
    existing = _read_registry()
    if existing is not None:
        existing_runtime = Path(str(existing.get("runtime_dir") or "")).resolve()
        if existing_runtime != runtime and not force:
            raise RoutingError("a different runtime already owns Final Callback routing")
    _write_registry({"schema_version": 2, "runtime_dir": str(runtime), "updated_at": time.time()})
    return {"configured": True, "matches_runtime": True, "runtime_valid": True}


def _unregister(runtime: Path) -> dict[str, Any]:
    status = _status(runtime)
    if status["configured"] and not status["matches_runtime"]:
        raise RoutingError("Final Callback routing belongs to a different runtime")
    try:
        _registry_path().unlink()
    except FileNotFoundError:
        pass
    return {"configured": False, "matches_runtime": False, "runtime_valid": False}


def _submit(runtime: Path) -> dict[str, Any]:
    raw = sys.stdin.buffer.read(2_000_001)
    if len(raw) > 2_000_000:
        raise RoutingError("Final Callback request is too large")
    try:
        request = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RoutingError("Final Callback request is invalid") from exc
    if not isinstance(request, dict) or set(request) != {"request_id", "final_answer"}:
        raise RoutingError("Final Callback request fields are invalid")
    try:
        return FinalCallbackStore(runtime / "callbacks.sqlite3").submit(
            request["request_id"], request["final_answer"]
        )
    except FinalCallbackStoreError as exc:
        raise RoutingError(str(exc)) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Feishu Final Callback routing helper")
    parser.add_argument("--runtime-dir", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    register = commands.add_parser("final-callback-register")
    register.add_argument("--force", action="store_true")
    commands.add_parser("final-callback-unregister")
    commands.add_parser("final-callback-registry-status")
    commands.add_parser("submit-final-callback")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        runtime = _runtime_dir(args.runtime_dir)
        if args.command == "final-callback-register":
            result = _register(runtime, force=args.force)
        elif args.command == "final-callback-unregister":
            result = _unregister(runtime)
        elif args.command == "final-callback-registry-status":
            result = _status(runtime)
        elif args.command == "submit-final-callback":
            result = _submit(runtime)
        else:
            raise RoutingError("unknown routing command")
        _emit({"ok": True, **result})
        return 0
    except (OSError, RoutingError) as exc:
        _emit({"ok": False, "error": str(exc)[:200]})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
