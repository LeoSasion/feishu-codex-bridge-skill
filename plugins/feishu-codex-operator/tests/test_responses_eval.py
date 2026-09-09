"""Disposable fixture verification and real CLI/fake model evaluation contracts."""
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_responses_tools import ROUTE
from test_responses_events import events_for, wire
from operator_responses_eval import EXPECTED, SOURCE, STOP_CASES, Fixture, evaluate, isolated_environment, verify_final_message, prompt_for, json_output_summary
from operator_core.responses_tool_adapter import dumps


class EvalFixtureTests(unittest.TestCase):
    def test_json_diagnostics_retain_only_fixed_counts(self):
        value = {"output": [{"type": kind, "name": "PRIVATE_TOOL_NAME", "id": "PRIVATE_ID",
                  "arguments": "PRIVATE_ARGUMENTS", "content": [{"text": "PRIVATE_TEXT"}]}
                 for kind in ("message", "reasoning", "custom_tool_call", "function_call", "PROVIDER_DEFINED_TYPE")]}
        before = deepcopy(value)
        summary = json_output_summary(value)
        self.assertEqual(summary, {"scope": "validated_json_snapshot_not_call_release",
            "output_counts": {"message": 1, "reasoning": 1, "function_call": 1, "custom_tool_call": 1, "other": 1}})
        self.assertEqual(value, before)

    def test_marker_line_policy_is_bounded_and_preserves_exact_bytes(self):
        from operator_core.responses_capabilities import RouterError
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "final.txt"
            self.assertFalse(verify_final_message(path, "TOKEN", "marker_line_v1")["verification_accepted"])
            for value, accepted in (("TOKEN", True), ("\n\nTOKEN", True), ("\r\nTOKEN\r\n", True),
                    ("\n" * 8 + "TOKEN" + "\n" * 8, True), ("\n" * 9 + "TOKEN", False),
                    (" TOKEN", False), ("TOKEN\t", False), ("\rTOKEN", False),
                    ("TOKEN\u00a0", False), ("TOKEN\nexplanation", False), ("TO\nKEN", False),
                    ("TOKEN\nTOKEN", False), ("\ufeffTOKEN", False)):
                with self.subTest(value=value):
                    path.write_bytes(value.encode("utf-8"))
                    report = verify_final_message(path, "TOKEN", "marker_line_v1")
                    self.assertEqual(report["verification_accepted"], accepted)
                    self.assertEqual(report["verification_exact"], value == "TOKEN")
                    self.assertEqual(path.read_bytes(), value.encode("utf-8"))
            with self.assertRaises(RouterError):
                verify_final_message(path, "TOKEN", "trim_everything")
        self.assertNotIn("eight empty", prompt_for("cli_nested"))
        self.assertIn("eight empty", prompt_for("cli_nested", "marker_line_v1"))

    def test_mcp_stdio_is_utf8_even_with_legacy_windows_encoding(self):
        import subprocess
        import sys
        import operator_responses_eval as module
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "work").mkdir()
            (root / "work/currency.py").write_bytes(SOURCE.encode("utf-8"))
            request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                "name": "fixture_step", "arguments": {"action": "read"}}}
            result = subprocess.run([sys.executable, module.__file__, "fixture", "--fixture-root", str(root),
                "--case", "cli_patchplan"], input=(dumps(request) + "\n").encode("utf-8"), capture_output=True,
                env={**os.environ, "PYTHONIOENCODING": "gbk"}, timeout=5)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(json.loads(result.stdout)["result"]["content"][0]["text"], SOURCE)

    def test_file_patch_preserves_all_other_bytes_and_does_not_execute_code(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "work").mkdir()
            path = root / "work/currency.py"
            path.write_bytes(SOURCE.encode())
            fixture = Fixture(root, "cli_workspace")
            self.assertTrue(fixture.call({"action": "verify"})["isError"])
            self.assertEqual(fixture.call({"action": "read"})["content"][0]["text"], SOURCE)
            self.assertFalse(fixture.call({"action": "replace", "old": "* 10)", "new": "* 100)"})["isError"])
            self.assertFalse(fixture.call({"action": "verify"})["isError"])
            self.assertEqual(path.read_bytes(), EXPECTED.encode())

    def test_error_requires_exact_returned_recovery_value(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory), "cli_tool_error")
            result = fixture.call({"action": "fail"})
            self.assertTrue(result["isError"])
            self.assertTrue(fixture.call({"action": "recover", "value": "guessed"})["isError"])
            self.assertFalse(fixture.call({"action": "recover", "value": fixture.challenge})["isError"])

    def test_cli_child_receives_no_inference_credentials(self):
        with patch.dict(os.environ, {"UNRELATED_API_KEY": "secret", "ACCESS_TOKEN": "secret", "GLM_API_KEY": "secret"}):
            environment = isolated_environment(Path("synthetic-home"))
        for name in ("UNRELATED_API_KEY", "ACCESS_TOKEN", "GLM_API_KEY"):
            self.assertNotIn(name, environment)

    def test_stop_fixtures_refuse_recovery_and_preserve_source(self):
        for case in STOP_CASES:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / "untouched.py"
                source.write_bytes(SOURCE.encode("utf-8"))
                fixture = Fixture(root, case)
                result = fixture.call({"action": "fail"})
                self.assertEqual(result["isError"], case == "cli_error_stop")
                failure = json.loads(result["content"][0]["text"])
                self.assertEqual(failure["error"], "synthetic_expected_error")
                if case == "cli_exit_stop":
                    self.assertEqual(failure["exit_code"], 1)
                self.assertTrue(fixture.call({"action": "recover", "value": fixture.challenge})["isError"])
                self.assertTrue(fixture.call({"action": "fail"})["isError"])
                audit = [json.loads(line) for line in (root / "fixture-audit.jsonl").read_text().splitlines()]
                self.assertEqual([r["accepted"] for r in audit], [True, False, False])
                self.assertEqual(source.read_bytes(), SOURCE.encode("utf-8"))
                self.assertEqual((root / "expected-marker").read_text(),
                                 "STOPPED synthetic_expected_error " + failure["marker"])

    def test_exact_verification_does_not_hide_whitespace_or_missing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "final.txt"
            self.assertFalse(verify_final_message(path, "TOKEN")["verification_exact"])
            for value in ("TOKEN", "\n\nTOKEN", "TOKEN\n", " TOKEN ", "WRONG"):
                with self.subTest(value=value):
                    path.write_bytes(value.encode("utf-8"))
                    report = verify_final_message(path, "TOKEN")
                    self.assertEqual(report["verification_exact"], value == "TOKEN")
                    self.assertEqual(report["verification_matched_after_trim"], value.strip() == "TOKEN")
                    self.assertEqual(path.read_bytes(), value.encode("utf-8"))
            path.write_bytes(b"\xff")
            with self.assertRaises(UnicodeDecodeError):
                verify_final_message(path, "TOKEN")


@unittest.skipUnless(os.environ.get("CODEX_OPERATOR_TEST_CLI"), "explicit current Desktop CLI required")
class CurrentCliEvalTests(unittest.IsolatedAsyncioTestCase):
    async def test_multiround_error_and_readonly_patch_plan_use_actual_cli(self):
        from aiohttp import web
        from aiohttp.test_utils import TestServer
        cases = [(case, "correct", "exact") for case in ("cli_multiround", "cli_tool_error", "cli_patchplan",
                                                "cli_error_stop", "cli_exit_stop")]
        # Boundary permutations live in EvalFixtureTests. Keep one real-CLI
        # policy comparison and exercise each stop violation under the more
        # permissive format policy so whitespace compatibility cannot mask it.
        cases += [("cli_exit_stop", "leading_lf", policy) for policy in ("exact", "marker_line_v1")]
        cases += [("cli_exit_stop", behavior, "marker_line_v1") for behavior in ("retry", "omit_error", "extra_round")]
        for case, behavior, policy in cases:
            with self.subTest(case=case, behavior=behavior, policy=policy):
                received = []
                async def upstream(request):
                    body = await request.json()
                    received.append(body)
                    previous = [item for item in body["input"] if item.get("type") == "function_call_output"]
                    count = len(previous)
                    if case == "cli_patchplan":
                        operations = [{"action": "read"}, {"action": "propose", "old": "* 10)", "new": "* 100)"}, {"action": "verify"}]
                    elif case in STOP_CASES:
                        operations = [{"action": "fail"}]
                    else:
                        operations = [{"action": "challenge" if case == "cli_multiround" else "fail"}, None]
                        if count == 1:
                            # Only this fake upstream's synthetic fixture result is inspected.
                            raw = dumps(previous[-1]["output"])
                            import re
                            challenge = re.search(r"[a-f0-9]{16}", raw).group()
                            operations[1] = {"action": "answer" if case == "cli_multiround" else "recover", "value": challenge}
                    if count < len(operations):
                        code = 'const t = ALL_TOOLS.find(t => t.name.endsWith("__fixture_step")); text(await tools[t.name](' + dumps(operations[count]) + '));'
                        if behavior == "retry":
                            code += ' text(await tools[t.name]({action:"fail"}));'
                        item = {"id": "fc_" + str(count), "type": "function_call", "call_id": "call_" + str(count),
                                "name": "exec", "status": "completed", "arguments": dumps({"input": code})}
                    else:
                        import re
                        marker = re.search(r"OPERATOR_EVAL_[a-f0-9]{16}", dumps(previous[-1]["output"])).group()
                        if case in STOP_CASES and behavior != "omit_error":
                            marker = "STOPPED synthetic_expected_error " + marker
                        if behavior == "leading_lf":
                            marker = "\n\n" + marker
                        item = {"id": "msg_end", "type": "message", "role": "assistant", "status": "completed",
                                "content": [{"type": "output_text", "text": marker, "annotations": []}]}
                        if behavior == "extra_round":
                            item = {"id": "fc_extra", "type": "function_call", "call_id": "call_extra",
                                    "name": "exec", "status": "completed",
                                    "arguments": dumps({"input": "text('synthetic extra round');"})}
                    if item["type"] == "function_call":
                        events = events_for(item)
                    else:
                        response = {"id": "resp_end", "object": "response", "status": "completed", "output": [item]}
                        events = [{"type": "response.created", "response": {**response, "status": "in_progress", "output": []}},
                                  {"type": "response.output_item.added", "output_index": 0, "item": {**item, "status": "in_progress", "content": []}},
                                  {"type": "response.output_text.delta", "output_index": 0, "item_id": item["id"], "content_index": 0, "delta": marker},
                                  {"type": "response.output_item.done", "output_index": 0, "item": item},
                                  {"type": "response.completed", "response": response}]
                    if body.get("stream") is False:
                        return web.json_response(events[-1]["response"])
                    return web.Response(body=wire(events), content_type="text/event-stream")
                app = web.Application()
                app.router.add_post("/v1/responses", upstream)
                server = TestServer(app)
                await server.start_server()
                row = deepcopy(ROUTE)
                row.update(api_base=str(server.make_url("/v1")), reasoning_efforts=["low"])
                row["responses"].update(parallel_tool_calls=False, text_tool_outputs="json_string")
                if behavior == "extra_round":
                    row["responses"]["upstream_response_mode"] = "json"
                try:
                    report = await evaluate(row, case, Path(os.environ["CODEX_OPERATOR_TEST_CLI"]), final_text_policy=policy)
                    accepted = behavior == "correct" or (policy == "marker_line_v1" and behavior == "leading_lf")
                    self.assertEqual(report["status"], "passed" if accepted else "failed", {"report": report, "synthetic_outputs": [
                        item["output"] for body in received for item in body["input"]
                        if item.get("type") == "function_call_output"]})
                    if case in STOP_CASES:
                        self.assertEqual(report["stopped_after_fixture_error"], behavior not in {"retry", "extra_round"})
                        self.assertEqual(report["fixture_calls_after_error"], 1 if behavior == "retry" else 0)
                        self.assertEqual(report["failure_report_verified"], behavior not in {"omit_error", "extra_round"})
                        self.assertEqual(report["request_budget_exceeded"], behavior == "extra_round")
                    if behavior == "leading_lf":
                        self.assertFalse(report["verification_exact"])
                        self.assertTrue(report["verification_matched_after_trim"])
                    self.assertEqual(report["client_requests"], report["requests"])
                    self.assertEqual(report["upstream_dispatch_attempts"], len(received))
                    self.assertEqual(report["upstream_header_responses"], len(received))
                    self.assertEqual(report["budget_rejected_client_requests"], 1 if behavior == "extra_round" else 0)
                    self.assertEqual(report["admitted_client_requests"], len(received))
                    if behavior == "extra_round":
                        self.assertEqual(report["client_requests"], len(received) + 1)
                        for dispatch in report["upstream_dispatches"]:
                            self.assertEqual(dispatch["transport_result"], "returned")
                            self.assertEqual(dispatch["json_snapshot"]["output_counts"], {
                                "message": 0, "reasoning": 0, "function_call": 0, "custom_tool_call": 1, "other": 0})
                    else:
                        self.assertTrue(all(d["json_snapshot"] is None for d in report["upstream_dispatches"]))
                finally:
                    await server.close()


if __name__ == "__main__":
    unittest.main()
