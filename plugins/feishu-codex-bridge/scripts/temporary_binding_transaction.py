"""Create or roll back one owner-requested diagnostic Feishu binding.

This maintenance helper never calls Codex, reads message bodies, or edits the
queue.  The controller must first verify the exact Desktop task and project by
read-only task tools.  Mutations require a stopped Bridge and are protected by
the same bridge lock filename used by the Bridge.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import uuid
from typing import Any

from bridge_core.runtime import is_process_running
from bridge_core.state import SessionStore


THREAD_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,191}")


def _emit(payload: dict[str, Any], *, error: bool = False) -> None:
    print(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        file=sys.stderr if error else sys.stdout,
    )


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


class MaintenanceLock:
    """Fail closed unless the normal Bridge has released its runtime lock."""

    def __init__(self, runtime_dir: Path) -> None:
        self.runtime_dir = runtime_dir
        self.lock_path = runtime_dir / "bridge.lock"
        self.pid_path = runtime_dir / "bridge.pid"
        self.acquired = False

    def __enter__(self) -> "MaintenanceLock":
        health = _read_json(self.runtime_dir / "health.json")
        health_status = str((health or {}).get("status") or "").strip().lower()
        if health_status and health_status != "stopped":
            raise ValueError("Bridge health is not stopped")
        if self.pid_path.exists():
            try:
                pid = int(self.pid_path.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                raise ValueError("Bridge pid reference is present but invalid") from None
            if is_process_running(pid):
                raise ValueError("Bridge pid is still active")
            raise ValueError("stale Bridge pid reference must be repaired before maintenance")
        try:
            descriptor = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise ValueError("Bridge or another maintenance action still owns the bridge lock") from None
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(str(os.getpid()))
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            try:
                self.lock_path.unlink()
            except OSError:
                pass
            raise
        self.acquired = True
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        if not self.acquired:
            return
        try:
            if self.lock_path.read_text(encoding="utf-8").strip() == str(os.getpid()):
                self.lock_path.unlink()
        except OSError:
            pass
        self.acquired = False


def _runtime_dir(value: str) -> Path:
    candidate = Path(value).resolve()
    if not candidate.is_dir():
        raise ValueError("bridge runtime directory does not exist")
    return candidate


def _project_root_digest(value: str) -> str:
    root = Path(value).resolve()
    if not root.is_dir():
        raise ValueError("verified Desktop project directory does not exist")
    return hashlib.sha256(str(root).casefold().encode("utf-8")).hexdigest()


def _scope(store: SessionStore, scope_hash: str) -> str:
    return store.resolve_scope_hash(scope_hash)


def bind(args: argparse.Namespace) -> dict[str, Any]:
    if not args.bridge_stopped_acknowledged:
        raise ValueError("binding requires an explicit stopped-Bridge acknowledgement")
    thread_id = args.thread_id.strip()
    if not THREAD_ID_PATTERN.fullmatch(thread_id):
        raise ValueError("responder thread id has an unsupported format")
    transaction_id = (args.transaction_id or uuid.uuid4().hex).strip().lower()
    runtime_dir = _runtime_dir(args.runtime_dir)
    with MaintenanceLock(runtime_dir):
        store = SessionStore(runtime_dir / "sessions.json")
        result = store.begin_temporary_binding(
            _scope(store, args.scope_hash),
            thread_id=thread_id,
            host_id=args.host_id,
            transaction_id=transaction_id,
            project_root_sha256=_project_root_digest(args.expected_project_root),
        )
    return {
        "schema_version": 1,
        "status": "bound",
        "scope_hash": args.scope_hash.strip().lower(),
        **result,
    }


def rollback(args: argparse.Namespace) -> dict[str, Any]:
    if not args.bridge_stopped_acknowledged:
        raise ValueError("rollback requires an explicit stopped-Bridge acknowledgement")
    runtime_dir = _runtime_dir(args.runtime_dir)
    with MaintenanceLock(runtime_dir):
        store = SessionStore(runtime_dir / "sessions.json")
        result = store.rollback_temporary_binding(
            _scope(store, args.scope_hash),
            transaction_id=args.transaction_id,
        )
    return {
        "schema_version": 1,
        "status": "rolled_back",
        "scope_hash": args.scope_hash.strip().lower(),
        **result,
    }


def status(args: argparse.Namespace) -> dict[str, Any]:
    runtime_dir = _runtime_dir(args.runtime_dir)
    payload = _read_json(runtime_dir / "sessions.json") or {}
    sessions = payload.get("sessions", {})
    if not isinstance(sessions, dict):
        raise ValueError("sessions file is invalid")
    candidate_hash = args.scope_hash.strip().lower()
    matches = [
        session
        for scope, session in sessions.items()
        if hashlib.sha256(str(scope).encode("utf-8")).hexdigest()[:12] == candidate_hash
        and isinstance(session, dict)
    ]
    if len(matches) != 1:
        raise ValueError("scope hash did not resolve to exactly one persisted session")
    session = matches[0]
    marker = session.get(SessionStore.TEMPORARY_BINDING_FIELD)
    return {
        "schema_version": 1,
        "status": "present" if isinstance(marker, dict) else "absent",
        "scope_hash": candidate_hash,
        "thread_id": str(session.get("thread_id") or ""),
        "transaction_id": str((marker or {}).get("transaction_id") or "")
        if isinstance(marker, dict)
        else "",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reversible Feishu bridge diagnostic binding")
    parser.add_argument("--runtime-dir", required=True)
    parser.add_argument("--scope-hash", required=True)
    subcommands = parser.add_subparsers(dest="command", required=True)

    bind_parser = subcommands.add_parser("bind")
    bind_parser.add_argument("--thread-id", required=True)
    bind_parser.add_argument("--host-id", required=True)
    bind_parser.add_argument("--expected-project-root", required=True)
    bind_parser.add_argument("--transaction-id", default="")
    bind_parser.add_argument("--bridge-stopped-acknowledged", action="store_true")

    rollback_parser = subcommands.add_parser("rollback")
    rollback_parser.add_argument("--transaction-id", required=True)
    rollback_parser.add_argument("--bridge-stopped-acknowledged", action="store_true")

    subcommands.add_parser("status")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "bind":
            result = bind(args)
        elif args.command == "rollback":
            result = rollback(args)
        else:
            result = status(args)
    except (OSError, ValueError) as exc:
        _emit({"schema_version": 1, "status": "failed", "error": str(exc)}, error=True)
        return 1
    _emit(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
