from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import math
from pathlib import Path
from queue import Empty, Full, Queue
import subprocess
import sys
from threading import Event, Lock, Thread
from time import monotonic
from typing import Any, BinaryIO, Callable


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from app_server_contract import (  # noqa: E402
    audit_contract,
    load_strict_json_value,
)
from app_server_mvp import (  # noqa: E402
    COMMAND as CORE_COMMAND,
    FAILURE_PHASES as CORE_FAILURE_PHASES,
    MAX_FRAME_BYTES,
    SCHEMA_VERSION as CORE_SCHEMA_VERSION,
    run_read_only_probe,
)


SCHEMA_VERSION = 2
COMMAND = "app-server-live-read-only-probe"
PROVENANCE_RECIPE = "app-server-diagnostic-attestation-v2"
PROVENANCE_SOURCE_DIGEST_FIELDS = (
    "attestation_host_sha256",
    "probe_core_sha256",
    "static_auditor_sha256",
    "codex_executable_sha256",
    "schema_tree_bytes_sha256",
    "schema_tree_canonical_sha256",
    "desktop_mcp_manifest_sha256",
    "desktop_mcp_manifest_canonical_sha256",
    "desktop_mcp_server_sha256",
)
DEFAULT_HARD_TIMEOUT_SECONDS = 45.0
MAX_HARD_TIMEOUT_SECONDS = 120.0
SHUTDOWN_RESERVE_SECONDS = 3.0
MAX_BUFFERED_LINES = 128
MAX_SESSION_FRAMES = 256
MAX_SESSION_BYTES = 8 * 1_048_576
MAX_STATIC_PIN_PATHS = 2048
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
GENERIC_READ = 0x80000000
FILE_SHARE_READ = 0x00000001
OPEN_EXISTING = 3
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
SAFE_CORE_EVIDENCE_FIELDS = (
    "control_thread_started",
    "control_thread_ephemeral",
    "codex_app_connected",
    "list_threads_available",
    "list_projects_available",
    "list_threads_result_valid",
    "list_projects_result_valid",
    "control_thread_hidden_from_desktop_catalog",
)
CORE_EVIDENCE_ORDER = (
    "control_thread_started",
    "control_thread_ephemeral",
    "codex_app_connected",
    "list_threads_available",
    "list_projects_available",
    "control_thread_hidden_from_desktop_catalog",
    "list_threads_result_valid",
    "list_projects_result_valid",
)
CORE_INVARIANT_FALSE_FIELDS = (
    "model_turn_started",
    "responder_mutation_attempted",
    "queue_claimed",
    "activation_allowed",
    "desktop_task_coordination_certified",
    "runtime_attestation_passed",
)
HOST_FAILURE_PHASES = frozenset(
    (
        "host_input",
        "host_executable",
        "probe_cwd",
        "static_contract",
        "platform",
        "job_create",
        "child_start",
        "child_containment",
        "transport",
        "child_shutdown",
        "runtime_validation",
        "runtime_provenance",
        "none",
        *CORE_FAILURE_PHASES,
    )
)


class _IoCounters(ctypes.Structure):
    _fields_ = (
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    )


class WindowsSourcePins:
    """Hold static probe inputs open without write/delete sharing through launch."""

    def __init__(self) -> None:
        self._handles: list[int] = []

    def pin(self, paths: list[Path]) -> None:
        if sys.platform != "win32":
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        )
        kernel32.CreateFileW.restype = wintypes.HANDLE
        invalid_handle = ctypes.c_void_p(-1).value
        try:
            for path in paths:
                flags = FILE_FLAG_BACKUP_SEMANTICS if path.is_dir() else 0
                handle = kernel32.CreateFileW(
                    str(path),
                    GENERIC_READ,
                    FILE_SHARE_READ,
                    None,
                    OPEN_EXISTING,
                    flags,
                    None,
                )
                handle_value = getattr(handle, "value", handle)
                handle_value = int(handle_value) if handle_value is not None else invalid_handle
                if handle_value == invalid_handle:
                    raise ctypes.WinError(ctypes.get_last_error())
                self._handles.append(handle_value)
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        if sys.platform != "win32":
            self._handles.clear()
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        while self._handles:
            kernel32.CloseHandle(self._handles.pop())


class _JobObjectBasicLimitInformation(ctypes.Structure):
    _fields_ = (
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    )


class _JobObjectExtendedLimitInformation(ctypes.Structure):
    _fields_ = (
        ("BasicLimitInformation", _JobObjectBasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    )


class WindowsOwnedJob:
    """A private Job Object that kills the one owned child when closed."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise OSError("unsupported_platform")
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        )
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = (
            wintypes.HANDLE,
            wintypes.HANDLE,
        )
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
        ntdll.NtResumeProcess.argtypes = (wintypes.HANDLE,)
        ntdll.NtResumeProcess.restype = ctypes.c_long

        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise OSError("job_create_failed")
        self._kernel32 = kernel32
        self._ntdll = ntdll
        self._handle: int | None = int(handle)
        limits = _JobObjectExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        configured = kernel32.SetInformationJobObject(
            wintypes.HANDLE(self._handle),
            JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        )
        if not configured:
            self.close()
            raise OSError("job_configure_failed")

    def assign(self, process: subprocess.Popen[bytes]) -> None:
        if self._handle is None:
            raise OSError("job_closed")
        process_handle = getattr(process, "_handle", None)
        if process_handle is None:
            raise OSError("process_handle_unavailable")
        assigned = self._kernel32.AssignProcessToJobObject(
            wintypes.HANDLE(self._handle),
            wintypes.HANDLE(int(process_handle)),
        )
        if not assigned:
            raise OSError("job_assign_failed")

    def terminate(self) -> None:
        if self._handle is None:
            return
        if not self._kernel32.TerminateJobObject(
            wintypes.HANDLE(self._handle),
            1,
        ):
            raise OSError("job_terminate_failed")

    def resume(self, process: subprocess.Popen[bytes]) -> None:
        process_handle = getattr(process, "_handle", None)
        if process_handle is None:
            raise OSError("process_handle_unavailable")
        status = self._ntdll.NtResumeProcess(wintypes.HANDLE(int(process_handle)))
        if status != 0:
            raise OSError("process_resume_failed")

    def close(self) -> None:
        handle = self._handle
        self._handle = None
        if handle is not None:
            self._kernel32.CloseHandle(wintypes.HANDLE(handle))


class StdioLineTransport:
    """Bounded binary stdio transport with one host-wide absolute deadline."""

    def __init__(
        self,
        process: subprocess.Popen[bytes],
        *,
        io_deadline: float,
    ) -> None:
        if process.stdin is None or process.stdout is None:
            raise OSError("stdio_unavailable")
        self._stdin: BinaryIO = process.stdin
        self._stdout: BinaryIO = process.stdout
        self._io_deadline = io_deadline
        self._lines: Queue[bytes | None] = Queue(maxsize=MAX_BUFFERED_LINES)
        self._overflow = Event()
        self._frame_count = 0
        self._byte_count = 0
        self._write_lock = Lock()
        self._reader = Thread(
            target=self._read_stdout,
            name="feishu-app-server-stdout",
            daemon=True,
        )
        self._reader.start()

    def _read_stdout(self) -> None:
        while True:
            try:
                value = self._stdout.readline(MAX_FRAME_BYTES + 2)
            except OSError:
                value = b""
            if value:
                self._frame_count += 1
                self._byte_count += len(value)
                if (
                    self._frame_count > MAX_SESSION_FRAMES
                    or self._byte_count > MAX_SESSION_BYTES
                ):
                    self._overflow.set()
                    return
            try:
                self._lines.put_nowait(value if value else None)
            except Full:
                self._overflow.set()
                return
            if not value:
                return

    def _remaining(self, requested: float) -> float:
        remaining = min(requested, self._io_deadline - monotonic())
        if remaining <= 0:
            raise OSError("host_deadline_exceeded")
        return remaining

    def write_line(self, line: str, timeout_seconds: float) -> None:
        data = line.encode("utf-8", errors="strict")
        remaining = self._remaining(timeout_seconds)
        completed = Event()
        failure: list[BaseException] = []

        def _write() -> None:
            try:
                with self._write_lock:
                    self._stdin.write(data)
                    self._stdin.flush()
            except BaseException as exc:  # kept inside the bounded worker
                failure.append(exc)
            finally:
                completed.set()

        Thread(
            target=_write,
            name="feishu-app-server-stdin",
            daemon=True,
        ).start()
        if not completed.wait(remaining):
            raise OSError("transport_write_timeout")
        if failure:
            raise OSError("transport_write_failed") from failure[0]

    def read_line(self, max_bytes: int, timeout_seconds: float) -> str | None:
        if self._overflow.is_set():
            raise OSError("transport_buffer_overflow")
        remaining = self._remaining(timeout_seconds)
        try:
            value = self._lines.get(timeout=remaining)
        except Empty as exc:
            raise OSError("transport_read_timeout") from exc
        if self._overflow.is_set():
            raise OSError("transport_buffer_overflow")
        if value is None:
            return None
        if len(value) > max_bytes:
            raise OSError("transport_frame_too_large")
        try:
            return value.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise OSError("transport_frame_invalid_utf8") from exc


def _safe_result() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "command": COMMAND,
        "status": "fail",
        "failure_phase": "host_input",
        "probe_cwd_absolute": False,
        "probe_cwd_empty": False,
        "probe_cwd_identity_verified": False,
        "probe_cwd_no_reparse": False,
        "app_server_process_started": False,
        "app_server_process_stopped": False,
        "owned_job_assigned_before_resume": False,
        "child_forced_stop": False,
        "hard_timeout_enforced": True,
        "control_thread_started": False,
        "control_thread_ephemeral": False,
        "codex_app_connected": False,
        "list_threads_available": False,
        "list_projects_available": False,
        "list_threads_result_valid": False,
        "list_projects_result_valid": False,
        "control_thread_hidden_from_desktop_catalog": False,
        "model_turn_started": False,
        "responder_mutation_attempted": False,
        "queue_claimed": False,
        "fallback_attempted": False,
        "desktop_task_coordination_certified": False,
        "read_only_desktop_task_coordination_attested": False,
        "runtime_attestation_passed": False,
        "activation_allowed": False,
        "provenance": {
            "recipe": PROVENANCE_RECIPE,
            "static_inputs_bound": False,
            "runtime_source_bound": False,
            "bound": False,
            "attestation_host_sha256": None,
            "probe_core_sha256": None,
            "static_auditor_sha256": None,
            "codex_executable_sha256": None,
            "schema_tree_bytes_sha256": None,
            "schema_tree_canonical_sha256": None,
            "desktop_mcp_manifest_sha256": None,
            "desktop_mcp_manifest_canonical_sha256": None,
            "desktop_mcp_server_sha256": None,
            "static_contract_canonical_sha256": None,
            "binding_sha256": None,
        },
        "issues": [],
    }


def _resolve_executable(value: Path) -> Path:
    resolved = _resolve_non_reparse_path(
        value,
        require_directory=False,
        issue="codex_executable_invalid",
    )
    if resolved.name.casefold() != "codex.exe":
        raise OSError("codex_executable_invalid")
    if "windowsapps" in {part.casefold() for part in resolved.parts}:
        raise OSError("windowsapps_executable_forbidden")
    return resolved


def _path_is_reparse_point(value: Path) -> bool:
    try:
        attributes = getattr(value.lstat(), "st_file_attributes", 0)
    except OSError:
        raise
    return value.is_symlink() or bool(attributes & FILE_ATTRIBUTE_REPARSE_POINT)


def _path_chain_has_reparse_point(value: Path) -> bool:
    absolute = value.absolute()
    parts = absolute.parts
    if not parts:
        raise OSError("probe_cwd_invalid")
    current = Path(parts[0])
    if _path_is_reparse_point(current):
        return True
    for part in parts[1:]:
        current /= part
        if _path_is_reparse_point(current):
            return True
    return False


def _resolve_non_reparse_path(
    value: Path,
    *,
    require_directory: bool,
    issue: str,
) -> Path:
    if not value.is_absolute():
        raise OSError(issue)
    if _path_chain_has_reparse_point(value):
        raise OSError(issue)
    resolved = value.resolve(strict=True)
    if _path_chain_has_reparse_point(resolved) or not value.samefile(resolved):
        raise OSError(issue)
    if require_directory:
        if not resolved.is_dir():
            raise OSError(issue)
    elif not resolved.is_file():
        raise OSError(issue)
    return resolved


def _static_pin_paths(
    *,
    executable: Path,
    schema_root: Path,
    desktop_mcp_manifest: Path,
    desktop_mcp_server: Path,
) -> list[Path]:
    paths = [
        SCRIPT_DIR / "app_server_host.py",
        SCRIPT_DIR / "app_server_mvp.py",
        SCRIPT_DIR / "app_server_contract.py",
        executable,
        schema_root,
        desktop_mcp_manifest,
        desktop_mcp_server,
    ]
    for candidate in sorted(schema_root.rglob("*"), key=lambda item: str(item).casefold()):
        resolved = _resolve_non_reparse_path(
            candidate,
            require_directory=candidate.is_dir(),
            issue="schema_root_invalid",
        )
        if not resolved.is_relative_to(schema_root):
            raise OSError("schema_root_invalid")
        paths.append(resolved)
        if len(paths) > MAX_STATIC_PIN_PATHS:
            raise OSError("static_input_count_exceeded")
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _directory_identity(value: Path) -> tuple[int, int, int]:
    stat_result = value.stat()
    return (
        int(stat_result.st_dev),
        int(stat_result.st_ino),
        int(stat_result.st_ctime_ns),
    )


def _resolve_probe_cwd(
    value: Path,
    evidence: dict[str, Any],
) -> Path:
    if not value.is_absolute():
        raise OSError("probe_cwd_not_absolute")
    evidence["probe_cwd_absolute"] = True
    if _path_chain_has_reparse_point(value):
        raise OSError("probe_cwd_reparse_forbidden")
    resolved = value.resolve(strict=True)
    if _path_chain_has_reparse_point(resolved):
        raise OSError("probe_cwd_reparse_forbidden")
    if not value.samefile(resolved):
        raise OSError("probe_cwd_identity_mismatch")
    evidence["probe_cwd_no_reparse"] = True
    if not resolved.is_dir():
        raise OSError("probe_cwd_invalid")
    try:
        if next(resolved.iterdir(), None) is not None:
            raise OSError("probe_cwd_not_empty")
    except OSError:
        raise
    evidence["probe_cwd_empty"] = True
    return resolved


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _schema_tree_digests(schema_root: Path) -> dict[str, str]:
    canonical_records: list[dict[str, str]] = []
    byte_records: list[dict[str, str]] = []
    for path in sorted(
        schema_root.rglob("*.json"),
        key=lambda item: item.relative_to(schema_root).as_posix(),
    ):
        relative_name = path.relative_to(schema_root).as_posix()
        name_digest = hashlib.sha256(relative_name.encode("utf-8")).hexdigest()
        canonical_records.append(
            {
                "relative_name_sha256": name_digest,
                "document_canonical_sha256": _canonical_sha256(
                    load_strict_json_value(path)
                ),
            }
        )
        byte_records.append(
            {
                "relative_name_sha256": name_digest,
                "document_sha256": _sha256(path),
            }
        )
    return {
        "schema_tree_bytes_sha256": _canonical_sha256(byte_records),
        "schema_tree_canonical_sha256": _canonical_sha256(canonical_records),
    }


def _static_input_digests(
    *,
    executable: Path,
    schema_root: Path,
    desktop_mcp_manifest: Path,
    desktop_mcp_server: Path,
) -> dict[str, str]:
    return {
        "attestation_host_sha256": _sha256(SCRIPT_DIR / "app_server_host.py"),
        "probe_core_sha256": _sha256(SCRIPT_DIR / "app_server_mvp.py"),
        "static_auditor_sha256": _sha256(SCRIPT_DIR / "app_server_contract.py"),
        "codex_executable_sha256": _sha256(executable),
        **_schema_tree_digests(schema_root),
        "desktop_mcp_manifest_sha256": _sha256(desktop_mcp_manifest),
        "desktop_mcp_manifest_canonical_sha256": _canonical_sha256(
            load_strict_json_value(desktop_mcp_manifest)
        ),
        "desktop_mcp_server_sha256": _sha256(desktop_mcp_server),
    }


def _provenance_binding(
    *,
    source_digests: dict[str, str],
    static_contract: dict[str, Any],
) -> dict[str, Any]:
    material: dict[str, Any] = {
        "recipe": PROVENANCE_RECIPE,
        "static_inputs_bound": True,
        "runtime_source_bound": False,
        **source_digests,
        "static_contract_canonical_sha256": _canonical_sha256(static_contract),
    }
    return {
        **material,
        # The supplied static files are pinned and mutually bound, but the
        # current App Server surface exposes no source identity for the live
        # codex_app MCP instance.  Co-hashing caller-supplied files must never
        # be promoted into a claim that those exact bytes served the calls.
        "bound": False,
        "binding_sha256": _canonical_sha256(material),
    }


def _static_preflight(
    *,
    executable: Path,
    expected_codex_sha256: str,
    schema_root: Path,
    desktop_mcp_manifest: Path,
    desktop_mcp_server: Path,
) -> dict[str, Any]:
    expected = expected_codex_sha256.casefold()
    if len(expected) != 64 or any(ch not in "0123456789abcdef" for ch in expected):
        raise OSError("codex_hash_invalid")
    if _sha256(executable) != expected:
        raise OSError("codex_hash_mismatch")
    resolved_schema = _resolve_non_reparse_path(
        schema_root,
        require_directory=True,
        issue="schema_root_invalid",
    )
    plugin_root = SCRIPT_DIR.parent
    if resolved_schema == plugin_root or resolved_schema.is_relative_to(plugin_root):
        raise OSError("schema_root_inside_source")
    manifest = _resolve_non_reparse_path(
        desktop_mcp_manifest,
        require_directory=False,
        issue="desktop_mcp_source_invalid",
    )
    server = _resolve_non_reparse_path(
        desktop_mcp_server,
        require_directory=False,
        issue="desktop_mcp_source_invalid",
    )
    before = _static_input_digests(
        executable=executable,
        schema_root=resolved_schema,
        desktop_mcp_manifest=manifest,
        desktop_mcp_server=server,
    )
    if before["codex_executable_sha256"] != expected:
        raise OSError("codex_hash_mismatch")
    static = audit_contract(
        schema_root=resolved_schema,
        desktop_mcp_manifest=manifest,
        desktop_mcp_server=server,
    )
    if (
        static.get("status") != "pass"
        or static.get("read_only_mvp_protocol_available") is not True
        or static.get("ephemeral_thread_path_nullable") is not True
        or static.get("desktop_task_coordination_certified") is not False
        or static.get("activation_allowed") is not False
    ):
        raise OSError("static_contract_failed")
    after = _static_input_digests(
        executable=executable,
        schema_root=resolved_schema,
        desktop_mcp_manifest=manifest,
        desktop_mcp_server=server,
    )
    if after != before:
        raise OSError("static_input_changed_during_audit")
    return _provenance_binding(
        source_digests=before,
        static_contract=static,
    )


def _provenance_inputs_still_current(
    *,
    provenance: dict[str, Any],
    executable: Path,
    schema_root: Path,
    desktop_mcp_manifest: Path,
    desktop_mcp_server: Path,
) -> bool:
    current_executable = _resolve_executable(executable)
    current_schema_root = _resolve_non_reparse_path(
        schema_root,
        require_directory=True,
        issue="schema_root_invalid",
    )
    current_manifest = _resolve_non_reparse_path(
        desktop_mcp_manifest,
        require_directory=False,
        issue="desktop_mcp_source_invalid",
    )
    current_server = _resolve_non_reparse_path(
        desktop_mcp_server,
        require_directory=False,
        issue="desktop_mcp_source_invalid",
    )
    current = _static_input_digests(
        executable=current_executable,
        schema_root=current_schema_root,
        desktop_mcp_manifest=current_manifest,
        desktop_mcp_server=current_server,
    )
    return all(
        current.get(key) == provenance.get(key)
        for key in PROVENANCE_SOURCE_DIGEST_FIELDS
    )


def _stop_owned_child(
    process: subprocess.Popen[bytes],
    job: WindowsOwnedJob,
    *,
    hard_deadline: float,
    contained: bool,
) -> tuple[bool, bool]:
    forced = False
    try:
        if process.stdin is not None:
            process.stdin.close()
    except OSError:
        pass
    graceful_wait = min(
        SHUTDOWN_RESERVE_SECONDS,
        max(0.0, hard_deadline - monotonic()),
    )
    if process.poll() is None and graceful_wait > 0:
        try:
            process.wait(timeout=graceful_wait)
        except subprocess.TimeoutExpired:
            pass
    if process.poll() is None:
        forced = True
        if contained:
            try:
                job.terminate()
            except OSError:
                pass
        else:
            try:
                process.terminate()
            except OSError:
                pass
        final_wait = max(0.0, hard_deadline - monotonic())
        if final_wait > 0:
            try:
                process.wait(timeout=final_wait)
            except subprocess.TimeoutExpired:
                pass
    if process.poll() is None:
        forced = True
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            pass
    stopped = process.poll() is not None
    job.close()
    if not stopped:
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            pass
        stopped = process.poll() is not None
    return stopped, forced


def _copy_safe_core_evidence(
    result: dict[str, Any],
    core: dict[str, Any],
) -> None:
    for key in SAFE_CORE_EVIDENCE_FIELDS:
        result[key] = core.get(key) is True


def _core_partial_evidence_matches_phase(core: dict[str, Any]) -> bool:
    values = [core.get(key) is True for key in CORE_EVIDENCE_ORDER]
    first_false = next(
        (index for index, value in enumerate(values) if not value),
        len(values),
    )
    if any(values[first_false:]):
        return False
    phase = core.get("failure_phase")
    if phase in {"probe_cwd", "initialize", "control_thread_start"}:
        return first_false == 0
    if phase == "mcp_status":
        return first_false == 2
    if phase == "tool_catalog":
        return 3 <= first_false <= 4
    if phase == "list_threads":
        return first_false == 5
    if phase == "list_projects":
        return first_false == 7
    if phase == "none":
        return first_false == len(values)
    return False


def _core_envelope_valid(core: Any) -> bool:
    if not isinstance(core, dict):
        return False
    if (
        core.get("schema_version") != CORE_SCHEMA_VERSION
        or core.get("command") != CORE_COMMAND
        or core.get("status") not in {"pass", "fail"}
        or core.get("failure_phase") not in CORE_FAILURE_PHASES
    ):
        return False
    boolean_fields = (*SAFE_CORE_EVIDENCE_FIELDS, *CORE_INVARIANT_FALSE_FIELDS)
    if any(not isinstance(core.get(key), bool) for key in boolean_fields):
        return False
    if any(core.get(key) is not False for key in CORE_INVARIANT_FALSE_FIELDS):
        return False
    if not _core_partial_evidence_matches_phase(core):
        return False
    issues = core.get("issues")
    if (
        not isinstance(issues, list)
        or not all(isinstance(item, str) and item for item in issues)
        or len(issues) > 1
    ):
        return False
    if core["status"] == "pass":
        return (
            core["failure_phase"] == "none"
            and issues == []
            and all(core.get(key) is True for key in SAFE_CORE_EVIDENCE_FIELDS)
        )
    return core["failure_phase"] != "none" and len(issues) == 1


def run_live_read_only_probe(
    *,
    codex_executable: Path,
    expected_codex_sha256: str,
    probe_cwd: Path,
    schema_root: Path,
    desktop_mcp_manifest: Path,
    desktop_mcp_server: Path,
    hard_timeout_seconds: float = DEFAULT_HARD_TIMEOUT_SECONDS,
    process_factory: Callable[..., subprocess.Popen[bytes]] | None = None,
    job_factory: Callable[[], WindowsOwnedJob] | None = None,
) -> dict[str, Any]:
    """Launch one owned App Server and attest only two read-only Desktop tools."""

    result = _safe_result()
    if (
        not math.isfinite(hard_timeout_seconds)
        or
        hard_timeout_seconds <= SHUTDOWN_RESERVE_SECONDS
        or hard_timeout_seconds > MAX_HARD_TIMEOUT_SECONDS
    ):
        result["issues"] = ["host_timeout_invalid"]
        return result

    result["failure_phase"] = "host_executable"
    try:
        executable = _resolve_executable(codex_executable)
    except OSError:
        result["issues"] = ["host_preflight_failed"]
        return result

    result["failure_phase"] = "probe_cwd"
    try:
        resolved_cwd = _resolve_probe_cwd(probe_cwd, result)
        cwd_identity = _directory_identity(resolved_cwd)
    except OSError:
        result["issues"] = ["host_preflight_failed"]
        return result

    source_pins = WindowsSourcePins()
    result["failure_phase"] = "static_contract"
    try:
        resolved_schema = _resolve_non_reparse_path(
            schema_root,
            require_directory=True,
            issue="schema_root_invalid",
        )
        resolved_manifest = _resolve_non_reparse_path(
            desktop_mcp_manifest,
            require_directory=False,
            issue="desktop_mcp_source_invalid",
        )
        resolved_server = _resolve_non_reparse_path(
            desktop_mcp_server,
            require_directory=False,
            issue="desktop_mcp_source_invalid",
        )
        base_pin_paths = [
            SCRIPT_DIR / "app_server_host.py",
            SCRIPT_DIR / "app_server_mvp.py",
            SCRIPT_DIR / "app_server_contract.py",
            executable,
            resolved_cwd,
            resolved_schema,
            resolved_manifest,
            resolved_server,
        ]
        source_pins.pin(base_pin_paths)
        source_pins.pin(
            [
                path
                for path in _static_pin_paths(
                    executable=executable,
                    schema_root=resolved_schema,
                    desktop_mcp_manifest=resolved_manifest,
                    desktop_mcp_server=resolved_server,
                )
                if path not in base_pin_paths
            ]
        )
        result["provenance"] = _static_preflight(
            executable=executable,
            expected_codex_sha256=expected_codex_sha256,
            schema_root=resolved_schema,
            desktop_mcp_manifest=resolved_manifest,
            desktop_mcp_server=resolved_server,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, RecursionError):
        source_pins.close()
        result["issues"] = ["host_preflight_failed"]
        return result
    if sys.platform != "win32" and job_factory is None:
        source_pins.close()
        result["failure_phase"] = "platform"
        result["issues"] = ["unsupported_platform"]
        return result

    result["failure_phase"] = "probe_cwd"
    try:
        revalidated_cwd = _resolve_probe_cwd(probe_cwd, result)
        if (
            _directory_identity(revalidated_cwd) != cwd_identity
            or not resolved_cwd.samefile(revalidated_cwd)
        ):
            raise OSError("probe_cwd_identity_changed")
    except OSError:
        source_pins.close()
        result["issues"] = ["host_preflight_failed"]
        return result
    result["probe_cwd_identity_verified"] = True

    result["failure_phase"] = "static_contract"
    try:
        inputs_current = _provenance_inputs_still_current(
            provenance=result["provenance"],
            executable=executable,
            schema_root=resolved_schema,
            desktop_mcp_manifest=resolved_manifest,
            desktop_mcp_server=resolved_server,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, RecursionError):
        inputs_current = False
    if not inputs_current:
        source_pins.close()
        result["issues"] = ["host_preflight_failed"]
        return result

    factory = process_factory or subprocess.Popen
    create_job = job_factory or WindowsOwnedJob
    hard_deadline = monotonic() + hard_timeout_seconds
    io_deadline = hard_deadline - SHUTDOWN_RESERVE_SECONDS
    process: subprocess.Popen[bytes] | None = None
    job: WindowsOwnedJob | None = None
    core: dict[str, Any] | None = None
    job_assigned = False
    post_runtime_inputs_current: bool | None = None
    host_phase = "job_create"
    try:
        job = create_job()
        creationflags = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "CREATE_SUSPENDED", 0x00000004)
        )
        host_phase = "child_start"
        # This is intentionally the last operation before CreateProcess.  It
        # closes mutations that race the earlier, more expensive provenance
        # re-hash.  A second check while the child is still suspended below
        # closes mutations performed by a launch wrapper itself.
        launch_cwd = _resolve_probe_cwd(probe_cwd, result)
        if (
            _directory_identity(launch_cwd) != cwd_identity
            or not resolved_cwd.samefile(launch_cwd)
            or not _provenance_inputs_still_current(
                provenance=result["provenance"],
                executable=executable,
                schema_root=resolved_schema,
                desktop_mcp_manifest=resolved_manifest,
                desktop_mcp_server=resolved_server,
            )
        ):
            raise OSError("launch_inputs_changed")
        process = factory(
            [str(executable), "app-server", "--listen", "stdio://"],
            cwd=str(resolved_cwd),
            env=None,
            shell=False,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
            creationflags=creationflags,
        )
        result["app_server_process_started"] = True
        host_phase = "child_start"
        suspended_cwd = _resolve_probe_cwd(probe_cwd, result)
        if (
            _directory_identity(suspended_cwd) != cwd_identity
            or not resolved_cwd.samefile(suspended_cwd)
            or not _provenance_inputs_still_current(
                provenance=result["provenance"],
                executable=executable,
                schema_root=resolved_schema,
                desktop_mcp_manifest=resolved_manifest,
                desktop_mcp_server=resolved_server,
            )
        ):
            raise OSError("suspended_launch_inputs_changed")
        host_phase = "child_containment"
        job.assign(process)
        job_assigned = True
        job.resume(process)
        result["owned_job_assigned_before_resume"] = True
        host_phase = "transport"
        transport = StdioLineTransport(process, io_deadline=io_deadline)
        core = run_read_only_probe(transport, cwd=resolved_cwd)
    except (
        OSError,
        UnicodeError,
        ValueError,
        RecursionError,
        subprocess.SubprocessError,
    ):
        result["failure_phase"] = host_phase
        result["issues"] = [
            "desktop_task_port_unavailable"
            if result["app_server_process_started"]
            else "app_server_start_failed"
        ]
    finally:
        try:
            if process is not None and job is not None:
                try:
                    stopped, forced = _stop_owned_child(
                        process,
                        job,
                        hard_deadline=hard_deadline,
                        contained=job_assigned,
                    )
                except OSError:
                    stopped, forced = False, True
                result["app_server_process_stopped"] = stopped
                result["child_forced_stop"] = forced
            elif job is not None:
                job.close()

            # Keep every source handle, including the exact probe CWD
            # directory handle, open until the owned child is confirmed
            # stopped.  Revalidate while those handles are still held so a
            # late CWD fill or any static-input drift cannot inherit the
            # earlier pre-resume evidence.
            if process is not None and result["app_server_process_stopped"]:
                try:
                    shutdown_cwd = _resolve_probe_cwd(probe_cwd, result)
                    post_runtime_inputs_current = (
                        _directory_identity(shutdown_cwd) == cwd_identity
                        and resolved_cwd.samefile(shutdown_cwd)
                        and _provenance_inputs_still_current(
                            provenance=result["provenance"],
                            executable=executable,
                            schema_root=resolved_schema,
                            desktop_mcp_manifest=resolved_manifest,
                            desktop_mcp_server=resolved_server,
                        )
                    )
                except (
                    OSError,
                    UnicodeError,
                    json.JSONDecodeError,
                    ValueError,
                    RecursionError,
                ):
                    post_runtime_inputs_current = False
        finally:
            source_pins.close()

    core_valid = core is not None and _core_envelope_valid(core)
    if core_valid:
        _copy_safe_core_evidence(result, core)

    if result["app_server_process_started"] and not result["app_server_process_stopped"]:
        result["failure_phase"] = "child_shutdown"
        result["issues"] = ["owned_child_stop_failed"]
        return result

    if post_runtime_inputs_current is False:
        result["failure_phase"] = "runtime_validation"
        result["issues"] = ["runtime_attestation_invalid"]
        return result

    if core is None:
        if not result["issues"]:
            result["issues"] = ["desktop_task_port_unavailable"]
        return result

    if not core_valid:
        result["failure_phase"] = "runtime_validation"
        result["issues"] = ["runtime_attestation_invalid"]
        return result
    if core["status"] != "pass":
        result["failure_phase"] = core["failure_phase"]
        result["issues"] = ["desktop_task_port_unavailable"]
        return result

    # The protocol calls succeeded, but the current runtime catalog does not
    # expose a digest or immutable identity that proves the serving codex_app
    # MCP instance was built from the separately supplied static files.  Keep
    # the useful safe partial booleans and fail closed instead of turning a
    # co-hash into runtime provenance.
    result["failure_phase"] = "runtime_provenance"
    result["issues"] = ["runtime_provenance_unavailable"]
    return result


class _AnswerFreeArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise ValueError("host_input_invalid")


def _build_parser() -> argparse.ArgumentParser:
    parser = _AnswerFreeArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--codex-executable", required=True, type=Path)
    parser.add_argument("--expected-codex-sha256", required=True)
    parser.add_argument("--probe-cwd", required=True, type=Path)
    parser.add_argument("--schema-root", required=True, type=Path)
    parser.add_argument("--desktop-mcp-manifest", required=True, type=Path)
    parser.add_argument("--desktop-mcp-server", required=True, type=Path)
    parser.add_argument(
        "--hard-timeout-seconds",
        type=float,
        default=DEFAULT_HARD_TIMEOUT_SECONDS,
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _build_parser().parse_args(argv)
        result = run_live_read_only_probe(
            codex_executable=args.codex_executable,
            expected_codex_sha256=args.expected_codex_sha256,
            probe_cwd=args.probe_cwd,
            schema_root=args.schema_root,
            desktop_mcp_manifest=args.desktop_mcp_manifest,
            desktop_mcp_server=args.desktop_mcp_server,
            hard_timeout_seconds=args.hard_timeout_seconds,
        )
    except Exception:
        # The diagnostic wire is deliberately answer-free.  Unexpected local
        # failures are normalized instead of leaking a traceback, path, or
        # remote payload through stderr/stdout.
        result = _safe_result()
        result["issues"] = [
            "host_input_invalid" if "args" not in locals() else "host_internal_failure"
        ]
    print(
        json.dumps(
            result,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0 if result["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
