"""Read-only, explicitly selected shell fixtures for the disposable CLI evaluator.

This module never selects Desktop's terminal or executes a command. It admits
one fixed native exec_command call; the CLI retains execution and permissions.
"""

import hashlib
from contextlib import contextmanager
from pathlib import Path
import re
import secrets
import shlex
import shutil
import sys
import tempfile

from operator_core.responses_capabilities import RouterError
from operator_core.responses_profiles import TERMINAL_CASES
from operator_core.responses_tool_adapter import loads


ARGUMENT = "space 'quote' \"double\" $HOME $(literal) `tick` & | ; < > \\r\\n 中文"


def terminal_sandbox_settings(case, selection, *, platform=None):
    """Explicit child-only backend selection; no setup, downgrade or fallback."""
    if selection is None:
        return {}
    if ((platform or sys.platform) != "win32" or case not in TERMINAL_CASES
            or selection != "unelevated"):
        raise RouterError("invalid_terminal_windows_sandbox_selection")
    return {"windows.sandbox": selection}


@contextmanager
def terminal_workspace():
    """Synthetic data only, with normal directory inheritance for native reads.

    Python's Windows mode-0700 temporary directories have a protected DACL.
    Keep the CLI home private elsewhere; never change any existing directory ACL.
    """
    parent = Path(tempfile.gettempdir()).resolve()
    work = parent / ("operator-terminal-work-" + secrets.token_hex(12))
    work.mkdir(mode=0o755)
    try:
        yield work
    finally:
        if work.is_symlink() or work.resolve() != work or work.parent != parent:
            raise RouterError("terminal_workspace_cleanup_path_changed")
        shutil.rmtree(work)


def quote_argument(value, family):
    """Quote data for an explicit grammar, never infer that grammar from OS."""
    if not isinstance(value, str) or "\x00" in value:
        raise RouterError("invalid_terminal_fixture_argument")
    if family == "powershell":
        return "'" + value.replace("'", "''") + "'"
    if family == "bash":
        return shlex.quote(value)
    raise RouterError("unsupported_terminal_fixture_family")


def executable_digest(path):
    path = Path(path)
    if not path.is_absolute() or not path.is_file() or path.is_symlink():
        raise RouterError("terminal_fixture_requires_exact_executable")
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as handle:
        before = path.stat()
        while chunk := handle.read(65536):
            total += len(chunk)
            if total > 64 * 1024 * 1024:
                raise RouterError("terminal_fixture_executable_too_large")
            digest.update(chunk)
        after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or total != before.st_size:
        raise RouterError("terminal_fixture_executable_changed")
    return digest.hexdigest()


class TerminalFixture:
    def __init__(self, work, case, executable, marker, *, windows_sandbox=None):
        self.work = Path(work)
        self.family = TERMINAL_CASES[case]
        self.executable = Path(executable)
        self.identity = {"family": self.family, "requested_executable_sha256": executable_digest(self.executable),
                         "requested_windows_sandbox": windows_sandbox}
        self.marker = marker
        folder = self.work / "fixture space 'quote' 中文"
        folder.mkdir()
        # One literal file read. Metacharacters are file data, never executable
        # syntax. Byte output avoids dependency on PowerShell console encoding.
        # The random marker is available only by reading this synthetic file.
        path = folder / "read 'fixture' 中文.txt"
        raw = ("\ufeff" + ARGUMENT + "\nalpha\nbeta\nalpha\r\nbeta\r\n"
               "中文\r\nsecond\nlast\r\n" + marker).encode("utf-8")
        self.files = {path: raw}
        for path, data in self.files.items():
            path.write_bytes(data)
        def quote(value):
            return quote_argument(value, self.family)
        if self.family == "powershell":
            command = "Get-Content -AsByteStream -LiteralPath " + quote(str(path))
            # Decimal byte lines are an explicit, ASCII-only read representation.
            # They include the BOM and all CR/LF bytes, without .NET calls or
            # console changes that constrained-language PowerShell may reject.
            self.expected_outputs = [newline.join(str(byte) for byte in raw) + newline
                                     for newline in ("\n", "\r\n")]
        else:
            command = "cat -- " + quote(path.as_posix())
            self.expected_outputs = [raw.decode("utf-8")]
        self.arguments = {"cmd": command, "shell": str(self.executable),
                          "login": False, "workdir": str(self.work), "yield_time_ms": 10000,
                          "max_output_tokens": 1000}
        self.call_id = None
        self.call_validated = False
        self.result_verified = False
        self.rejection = None
        self.policy_rejected = False
        self.exit_code = None

    def reject(self, reason):
        self.rejection = reason
        raise RouterError(reason)

    def validate_response(self, value):
        calls = [item for item in value["output"] if item["type"] in
                 {"function_call", "custom_tool_call", "tool_search_call"}]
        if not self.call_validated:
            if (len(calls) != 1 or calls[0]["type"] != "function_call"
                    or calls[0].get("namespace") is not None or calls[0].get("name") != "exec_command"):
                self.reject("terminal_fixture_call_mismatch")
            arguments = loads(calls[0]["arguments"])
            if (not isinstance(arguments, dict) or set(arguments) != set(self.arguments)
                    or any(type(arguments[key]) is not type(expected) or arguments[key] != expected
                           for key, expected in self.arguments.items())):
                self.reject("terminal_fixture_call_mismatch")
            self.call_id = calls[0]["call_id"]
            self.call_validated = True
        elif calls or not self.result_verified:
            self.reject("terminal_fixture_extra_or_unverified_call")

    def validate_followup(self, body):
        """Check only the paired synthetic native tool result, never message text."""
        if not self.call_validated:
            return
        results = [item for item in body.get("input", []) if item.get("type") == "function_call_output"]
        if len(results) != 1 or results[0].get("call_id") != self.call_id:
            self.reject("terminal_fixture_result_identity_mismatch")
        output = results[0].get("output")
        if isinstance(output, str) and output.startswith("["):
            # The explicit json_string codec may serialize text-part lists. This
            # decodes that data only; it never parses messages into tool results.
            output = loads(output)
        if isinstance(output, str):
            parts = [output]
        elif isinstance(output, list) and all(isinstance(part, dict) and part.get("type") in
                {"text", "input_text", "output_text"} and isinstance(part.get("text"), str) for part in output):
            parts = [part["text"] for part in output]
        else:
            self.reject("terminal_fixture_result_format_unverified")
        if len(parts) != 1:
            self.reject("terminal_fixture_result_format_unverified")
        if any(part.startswith("exec_command failed: CreateProcess ") and "blocked by policy" in part for part in parts):
            self.policy_rejected = True
            self.reject("terminal_fixture_policy_rejected")
        if any(part.startswith("exec_command failed:") for part in parts):
            if "CreateRestrictedToken failed:" in parts[0]:
                self.reject("terminal_fixture_sandbox_setup_failed")
            self.reject("terminal_fixture_execution_failed")
        # Dated native result envelopes: both the current Output label and the
        # legacy Final output label. Parse metadata separately; never trim data.
        result = re.fullmatch(r"(?:Chunk ID: [a-zA-Z0-9_-]+\n)?"
            r"(?:Wall time: [0-9]+(?:\.[0-9]+)? seconds\n)?"
            r"Process exited with code (?P<exit>-?[0-9]+)\n"
            r"(?:Original token count: [0-9]+\n)?(?:Output|Final output):\n(?P<output>[\s\S]*)", parts[0])
        if result is not None:
            self.exit_code = int(result["exit"])
            if self.exit_code != 0:
                self.reject("terminal_fixture_process_failed")
        self.result_verified = result is not None and result["output"] in self.expected_outputs
        if not self.result_verified:
            self.reject("terminal_fixture_result_unverified")

    def unchanged(self):
        try:
            return all(path.is_file() and not path.is_symlink() and path.stat().st_size == len(data)
                       and path.read_bytes() == data for path, data in self.files.items())
        except OSError:
            return False

    def report(self):
        return {"terminal": dict(self.identity), "terminal_call_validated": self.call_validated,
                "terminal_result_verified": self.result_verified,
                "terminal_fixture_unchanged": self.unchanged(), "terminal_rejection": self.rejection,
                "terminal_exit_code": self.exit_code,
                "terminal_policy_rejected": self.policy_rejected,
                "terminal_evidence_scope": "fixed_read_only_native_cli_command",
                "terminal_read_representation": "decimal_bytes_v1" if self.family == "powershell" else "utf8_text_v1",
                "unicode_console_text_verified": False,
                "shell_resolution_scope": "explicit_tool_argument_and_child_path_only",
                "shell_process_identity_verified": False, "desktop_terminal_selection_verified": False,
                "arbitrary_shell_commands_verified": False}
