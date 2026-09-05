"""Shared stdio transport for the three read-only App Server clients."""

from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
from typing import Any

from .config import OPERATOR_VERSION


class AppServerError(RuntimeError):
    """Transport failure; lane-specific policy belongs to each client."""


class AppServerSession:
    deadline: float | None = None

    def __init__(
        self, executable: Path, timeout_seconds: int, *,
        error_type: type[AppServerError] = AppServerError,
    ) -> None:
        self._error_type = error_type
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            self.process = subprocess.Popen(
                [str(executable), "app-server", "--listen", "stdio://"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="strict",
                bufsize=1,
                creationflags=flags,
            )
        except OSError as exc:
            raise self._error_type("read-only App Server could not start") from exc
        self.timeout_seconds = timeout_seconds
        self.deadline: float | None = None
        self._messages: queue.Queue[object] = queue.Queue()
        self._next_id = 1
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self) -> None:
        assert self.process.stdout is not None
        try:
            for line in self.process.stdout:
                if len(line) > 2_000_000:
                    raise self._error_type("App Server response is too large")
                self._messages.put(json.loads(line))
        except Exception as exc:
            self._messages.put(exc)
        finally:
            self._messages.put(None)

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self._write(message)

    def _write(self, message: dict[str, Any]) -> None:
        if self.process.stdin is None or self.process.poll() is not None:
            raise self._error_type("read-only App Server is unavailable")
        wire = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        try:
            self.process.stdin.write(wire + "\n")
            self.process.stdin.flush()
        except OSError as exc:
            raise self._error_type("read-only App Server write failed") from exc

    def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        request_id = str(self._next_id)
        self._next_id += 1
        message: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params is not None:
            message["params"] = params
        self._write(message)
        deadline = time.monotonic() + self.timeout_seconds
        if self.deadline is not None:
            deadline = min(deadline, self.deadline)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise self._error_type("read-only App Server request timed out")
            try:
                message = self._messages.get(timeout=remaining)
            except queue.Empty as exc:
                raise self._error_type("read-only App Server request timed out") from exc
            if message is None:
                raise self._error_type("read-only App Server exited early")
            if isinstance(message, Exception):
                raise self._error_type("read-only App Server returned invalid JSON") from message
            if not isinstance(message, dict):
                continue
            if str(message.get("id")) != request_id:
                # Notifications do not extend the fixed request deadline.
                continue
            if "error" in message:
                raise self._error_type("read-only App Server rejected the request")
            if "result" not in message:
                raise self._error_type("read-only App Server response is incomplete")
            return message["result"]

    def initialize(self) -> None:
        result = self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "feishu-codex-operator",
                    "title": "Feishu Codex Operator read-only client",
                    "version": OPERATOR_VERSION,
                },
                "capabilities": {"experimentalApi": False},
            },
        )
        if not isinstance(result, dict):
            raise self._error_type("read-only App Server initialization failed")
        self.notify("initialized")

    def close(self) -> None:
        try:
            if self.process.stdin is not None:
                self.process.stdin.close()
        except OSError:
            pass
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)

    def __enter__(self) -> AppServerSession:
        try:
            self.initialize()
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
