"""Exact, disposable native shell calls; no live provider or saved task access."""

from copy import deepcopy
from itertools import product
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_responses_tools import ROUTE, prepare, response
from operator_core.responses_capabilities import RouterError
from operator_core.responses_tool_adapter import dumps, restore_response
from operator_responses_eval import evaluate, isolated_environment
from operator_terminal_fixture import (ARGUMENT, TerminalFixture, quote_argument, terminal_sandbox_settings,
                                       terminal_workspace)


class TerminalFixtureTests(unittest.TestCase):
    def test_native_workspace_has_separate_lifecycle_and_preserves_existing_temp_files(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory).resolve()
            original = parent / "existing.txt"
            original.write_bytes(b"untouched")
            with patch("operator_terminal_fixture.tempfile.gettempdir", return_value=str(parent)):
                with terminal_workspace() as work:
                    self.assertEqual(work.parent, parent)
                    self.assertTrue(work.is_dir())
                    (work / "synthetic.txt").write_bytes(b"fixture")
                self.assertFalse(work.exists())
                self.assertEqual(original.read_bytes(), b"untouched")

    def test_one_literal_file_read_preserves_internal_eols_and_data_metacharacters(self):
        for case in ("cli_powershell", "cli_bash"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                fixture = TerminalFixture(Path(directory), case, Path(sys.executable), "OPERATOR_EVAL_TEST")
                self.assertEqual(len(fixture.files), 1)
                path, raw = next(iter(fixture.files.items()))
                self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
                self.assertIn(ARGUMENT.encode("utf-8"), raw)
                self.assertIn(b"alpha\nbeta\nalpha\r\nbeta\r\n", raw)
                self.assertNotIn(ARGUMENT, fixture.arguments["cmd"])
                self.assertNotIn(";", fixture.arguments["cmd"])
                expected_command = ("Get-Content -AsByteStream -LiteralPath " if case == "cli_powershell"
                                    else "cat -- ") + quote_argument(str(path) if case == "cli_powershell"
                                    else path.as_posix(), fixture.family)
                self.assertEqual(fixture.arguments["cmd"], expected_command)
                expected_output = "\n".join(str(byte) for byte in raw) + "\n" if case == "cli_powershell" else raw.decode("utf-8")
                self.assertEqual(fixture.expected_outputs[0], expected_output)
                self.assertTrue(fixture.unchanged())

    def test_backend_selection_is_explicit_and_scoped_without_permission_overrides(self):
        self.assertEqual(terminal_sandbox_settings("cli_bash", None, platform="win32"), {})
        self.assertEqual(terminal_sandbox_settings("cli_bash", "unelevated", platform="win32"),
                         {"windows.sandbox": "unelevated"})
        for case, choice, platform in (("cli_bash", "disabled", "win32"),
                                       ("cli_bash", "elevated", "win32"),
                                       ("cli_nested", "unelevated", "win32"),
                                       ("cli_bash", "unelevated", "linux")):
            with self.subTest(case=case, choice=choice, platform=platform), self.assertRaisesRegex(
                    RouterError, "invalid_terminal_windows_sandbox_selection"):
                terminal_sandbox_settings(case, choice, platform=platform)

    def test_native_arguments_and_history_keep_exact_shell_and_newline_bytes(self):
        tool = {"type": "function", "name": "exec_command", "parameters": {"type": "object"}}
        for case in ("cli_powershell", "cli_bash"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                fixture = TerminalFixture(Path(directory), case, Path(sys.executable), "OPERATOR_EVAL_TEST")
                arguments = {**fixture.arguments, "cmd": fixture.arguments["cmd"] + "\r\n# " + ARGUMENT + "\n"}
                encoded = dumps(arguments)
                payload = {"input": "Synthetic argument preservation only; never execute.", "tools": [tool]}
                prepared, context = prepare(payload, codex_tool_mode="standard", custom_tools={})
                original = {"id": "fc_terminal", "type": "function_call", "name": "exec_command",
                            "call_id": "synthetic_terminal", "status": "completed", "arguments": encoded}
                restored = restore_response(response(original), context)["output"][0]
                self.assertEqual(restored["arguments"].encode("utf-8"), encoded.encode("utf-8"))
                payload["input"] = [restored, {"type": "function_call_output", "call_id": "synthetic_terminal", "output": "fixture only"}]
                prepared, _ = prepare(payload, codex_tool_mode="standard", custom_tools={})
                self.assertEqual(prepared["input"][0]["arguments"].encode("utf-8"), encoded.encode("utf-8"))
                self.assertTrue(fixture.unchanged())

    def test_unknown_grammar_and_shell_paths_are_not_guessed(self):
        for family in ("cmd", "default", "zsh"):
            with self.subTest(family=family), self.assertRaises(RouterError):
                quote_argument("argument", family)
        for value in (None, "a\x00b", 1):
            with self.subTest(value=value), self.assertRaises(RouterError):
                quote_argument(value, "bash")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RouterError, "requires_exact_executable"):
                TerminalFixture(Path(directory), "cli_bash", Path("bash"), "OPERATOR_EVAL_TEST")

    def test_wrong_command_shell_login_and_extra_calls_are_rejected_before_release(self):
        for field, value in (("shell", "wrong-shell"), ("login", True), ("login", 0), ("workdir", "wrong-directory"),
                             ("cmd", "echo changed"), ("sandbox_permissions", "require_escalated")):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                fixture = TerminalFixture(Path(directory), "cli_bash", Path(sys.executable), "OPERATOR_EVAL_TEST")
                args = {**fixture.arguments, field: value}
                call = {"type": "function_call", "name": "exec_command",
                        "call_id": "synthetic_call", "arguments": dumps(args)}
                with self.assertRaisesRegex(RouterError, "call_mismatch"):
                    fixture.validate_response({"output": [call]})
                self.assertFalse(fixture.call_validated)
                self.assertTrue(fixture.unchanged())
                call["arguments"] = dumps(fixture.arguments)
                fixture.validate_response({"output": [call]})
                with self.assertRaisesRegex(RouterError, "extra_or_unverified_call"):
                    fixture.validate_response({"output": [call]})

    def test_only_paired_successful_native_output_establishes_terminal_result(self):
        for case, variant in product(("cli_bash", "cli_powershell"),
                                     ("success", "current_output", "exit_failure", "fake_zero_exit", "changed_output",
                                      "marker_only", "policy_rejected", "token_failure")):
            with self.subTest(case=case, variant=variant), tempfile.TemporaryDirectory() as directory:
                fixture = TerminalFixture(Path(directory), case, Path(sys.executable), "OPERATOR_EVAL_TEST")
                fixture.validate_response({"output": [{"type": "function_call", "name": "exec_command",
                    "call_id": "synthetic_call", "arguments": dumps(fixture.arguments)}]})
                output = "Process exited with code 0\nFinal output:\n" + fixture.expected_outputs[0]
                if variant == "exit_failure":
                    output = output.replace("code 0", "code 1")
                elif variant == "fake_zero_exit":
                    output = output.replace("code 0", "code 1") + "Process exited with code 0\n"
                elif variant == "current_output":
                    output = "Chunk ID: abc123\nWall time: 0.15 seconds\n" + output.replace(
                        "Final output:\n", "Original token count: 100\nOutput:\n")
                elif variant == "changed_output":
                    output = output + "changed"
                elif variant == "marker_only":
                    output = "OPERATOR_EVAL_TEST"
                elif variant == "policy_rejected":
                    output = 'exec_command failed: CreateProcess { message: "Rejected: blocked by policy" }'
                elif variant == "token_failure":
                    output = 'exec_command failed: CreateProcess { message: "CreateRestrictedToken failed: 87" }'
                payload = {"input": [{"type": "function_call_output", "call_id": "synthetic_call", "output": output}]}
                if variant in {"success", "current_output"}:
                    fixture.validate_followup(payload)
                    self.assertTrue(fixture.result_verified)
                    payload["input"][0]["output"] = dumps([{"type": "text", "text": output}])
                    fixture.validate_followup(payload)
                    payload["input"][0]["output"] = dumps([
                        {"type": "text", "text": output}, {"type": "text", "text": "extra output"}])
                    with self.assertRaisesRegex(RouterError, "result_format_unverified"):
                        fixture.validate_followup(payload)
                else:
                    reason = {"policy_rejected": "policy_rejected", "token_failure": "sandbox_setup_failed",
                              "exit_failure": "process_failed", "fake_zero_exit": "process_failed"}.get(variant, "result_unverified")
                    with self.assertRaisesRegex(RouterError, reason):
                        fixture.validate_followup(payload)
                    self.assertFalse(fixture.result_verified)
                    self.assertEqual(fixture.policy_rejected, variant == "policy_rejected")
                payload["input"][0]["call_id"] = "wrong_call"
                with self.assertRaisesRegex(RouterError, "identity_mismatch"):
                    fixture.validate_followup(payload)
                self.assertTrue(fixture.unchanged())
                next(iter(fixture.files)).write_bytes(b"changed")
                self.assertFalse(fixture.unchanged())

    def test_selected_shell_path_is_scoped_to_disposable_child(self):
        with patch.dict(os.environ, {"PATH": "original-path", "GLM_API_KEY": "private-test-value"}):
            before = dict(os.environ)
            shell = Path(sys.executable)
            child = isolated_environment(Path("disposable-home"), terminal_shell=shell)
            self.assertEqual(child["PATH"], str(shell.parent) + os.pathsep + "original-path")
            self.assertNotIn("GLM_API_KEY", child)
            self.assertEqual(dict(os.environ), before)


@unittest.skipUnless(os.environ.get("CODEX_OPERATOR_TEST_CLI"), "explicit current Desktop CLI required")
class CurrentCliTerminalTests(unittest.IsolatedAsyncioTestCase):
    async def run_terminal(self, case, shell):
        from aiohttp import web
        from aiohttp.test_utils import TestServer
        received = []
        synthetic_outputs = []
        validate_followup = TerminalFixture.validate_followup
        def observe_followup(fixture, body):
            synthetic_outputs.extend(item["output"] for item in body.get("input", [])
                                     if item.get("type") == "function_call_output")
            return validate_followup(fixture, body)
        async def upstream(request):
            body = await request.json()
            received.append(body)
            if len(received) == 1:
                # All inspected text belongs to this generated synthetic fixture.
                prompt = next(item["content"] for item in reversed(body["input"])
                              if item.get("role") == "user")
                if isinstance(prompt, list):
                    prompt = "".join(part.get("text", "") for part in prompt)
                args = json.JSONDecoder().raw_decode(prompt.split("JSON arguments, preserving all characters "
                    "and using no other tools: ", 1)[1])[0]
                name = "exec_command"
                self.assertIn(name, [tool["name"] for tool in body["tools"]])
                item = {"id": "fc_terminal", "type": "function_call", "name": name,
                        "call_id": "synthetic_terminal", "status": "completed", "arguments": dumps(args)}
            else:
                outputs = [item["output"] for item in body["input"] if item.get("type") == "function_call_output"]
                if case == "cli_powershell":
                    text = outputs[0]
                    if text.startswith("["):
                        text = json.loads(text)[0]["text"]
                    raw = bytes(int(line) for line in text.split("\nOutput:\n", 1)[1].splitlines())
                    marker = re.search(r"OPERATOR_EVAL_[a-f0-9]{16}", raw.decode("utf-8-sig")).group()
                else:
                    marker = re.search(r"OPERATOR_EVAL_[a-f0-9]{16}", dumps(outputs)).group()
                item = {"id": "msg_terminal", "type": "message", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": marker, "annotations": []}]}
            return web.json_response({"id": "resp_terminal", "object": "response", "status": "completed", "output": [item]})
        app = web.Application()
        app.router.add_post("/v1/responses", upstream)
        server = TestServer(app)
        await server.start_server()
        row = deepcopy(ROUTE)
        row.update(api_base=str(server.make_url("/v1")), reasoning_efforts=["low"])
        row["responses"].update(codex_tool_mode="standard", custom_tools={}, parallel_tool_calls=False,
                                upstream_response_mode="json", text_tool_outputs="json_string")
        try:
            with patch.object(TerminalFixture, "validate_followup", observe_followup):
                report = await evaluate(row, case, Path(os.environ["CODEX_OPERATOR_TEST_CLI"]), terminal_shell=Path(shell),
                                        windows_sandbox=os.environ.get("CODEX_OPERATOR_TEST_WINDOWS_SANDBOX"))
            expected = os.environ.get("CODEX_OPERATOR_TEST_TERMINAL_OUTCOME", "passed")
            self.assertIn(expected, {"passed", "policy_rejected"})
            self.assertEqual(report["status"], "passed" if expected == "passed" else "failed",
                             {"report": report, "synthetic_outputs": synthetic_outputs})
            self.assertEqual(report["requests"], 2)
            self.assertTrue(report["terminal_call_validated"])
            self.assertEqual(report["terminal"]["requested_windows_sandbox"],
                             os.environ.get("CODEX_OPERATOR_TEST_WINDOWS_SANDBOX"))
            self.assertEqual(report["terminal_result_verified"], expected == "passed")
            self.assertEqual(report["terminal_policy_rejected"], expected == "policy_rejected")
            if expected == "policy_rejected":
                self.assertEqual(report["terminal_rejection"], "terminal_fixture_policy_rejected")
                self.assertEqual(len(received), 1)
            self.assertTrue(report["terminal_fixture_unchanged"])
            self.assertFalse(report["desktop_terminal_selection_verified"])
            self.assertFalse(report["arbitrary_shell_commands_verified"])
            self.assertRegex(report["terminal"]["requested_executable_sha256"], r"^[a-f0-9]{64}$")
        finally:
            await server.close()

    @unittest.skipUnless(os.environ.get("CODEX_OPERATOR_TEST_POWERSHELL"), "explicit PowerShell fixture executable required")
    async def test_native_powershell_reports_the_explicit_expected_execution_outcome(self):
        await self.run_terminal("cli_powershell", os.environ["CODEX_OPERATOR_TEST_POWERSHELL"])

    @unittest.skipUnless(os.environ.get("CODEX_OPERATOR_TEST_BASH"), "explicit Bash fixture executable required")
    async def test_native_bash_reports_the_explicit_expected_execution_outcome(self):
        await self.run_terminal("cli_bash", os.environ["CODEX_OPERATOR_TEST_BASH"])


if __name__ == "__main__":
    unittest.main()
