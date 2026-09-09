"""Synthetic probe receipts, bounded metadata and no-retry contracts."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from test_responses_tools import ROUTE, response
from operator_responses_probe import probe, probe_input, probe_usage, reserve_receipt, source_difference
from operator_core.responses_profiles import profile_from_reports, inspect_profile

try:
    from aiohttp import web
    from aiohttp.test_utils import TestServer
except ImportError:
    web = None


class ProbeReceiptTests(unittest.TestCase):
    def test_source_difference_reports_only_counts_and_exact_byte_offset(self):
        for expected, actual, offset in (("中😀", "中😀", None), ("中😀", "中", 3),
                                         ("中", "中😀", 3), ("a\r\n", "a\n", 1)):
            with self.subTest(offset=offset):
                report = source_difference(expected, actual)
                self.assertEqual(report["first_different_utf8_byte"], offset)
                self.assertTrue(all(type(v) is int or v is None for v in report.values()))
        crlf = probe_input("unicode-json")
        lf = probe_input("unicode-json-lf")
        self.assertEqual(lf, crlf.replace("\r\n", "\n"))
        self.assertEqual(len(crlf.encode()), 53)
        self.assertEqual(len(lf.encode()), 52)

    def test_receipt_reserves_no_replay_boundary_before_a_result(self):
        with tempfile.TemporaryDirectory() as directory:
            target = reserve_receipt(Path(directory), ROUTE, "json", "synthetic-run")
            before = target.read_bytes()
            self.assertEqual(json.loads(before), {"status": "may_have_started_no_retry"})
            with self.assertRaises(FileExistsError):
                reserve_receipt(Path(directory), ROUTE, "json", "synthetic-run")
            self.assertEqual(target.read_bytes(), before)

    def test_usage_keeps_only_known_bounded_integer_counts(self):
        value = {"input_tokens": 2, "output_tokens": True, "total_tokens": 10000001,
                 "unexpected": "provider-controlled text", "input_tokens_details": "text",
                 "output_tokens_details": {"reasoning_tokens": 1, "other": "text"}}
        self.assertEqual(probe_usage(value), {"input_tokens": 2,
                         "output_tokens_details": {"reasoning_tokens": 1}})


@unittest.skipUnless(web, "optional aiohttp environment required")
class ProbeLoopTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, change_source=False, reject=False, case="json", registration=None, whitespace="",
                       source_transform=None):
        seen = []

        async def upstream(request):
            body = await request.json()
            seen.append(body)
            if reject:
                return web.json_response({"error": "synthetic rejection"}, status=400)
            if len(seen) == 1:
                if case == "named":
                    self.assertEqual(body["tool_choice"], {"type": "function", "name": "exec"})
                if case in {"unicode-json", "unicode-json-lf"}:
                    self.assertEqual(json.loads(body["input"][0]["content"].split("\n")[-1]), probe_input(case))
                if case in {"unicode-arguments", "unicode-arguments-lf"}:
                    self.assertEqual(json.loads(body["input"][0]["content"].split("\n")[-1]),
                                     {"input": probe_input(case)})
                source = "text(42);" if change_source else probe_input(case)
                if source_transform:
                    source = source_transform(source)
                return web.json_response(response({
                    "type": "function_call", "id": "fc_probe", "call_id": "call_probe",
                    "status": "completed", "name": "exec", "arguments": json.dumps({
                        "input": source})}))
            output = body["input"][-1]
            result = output["output"]
            if case == "structured":
                self.assertEqual(result[0]["type"], "input_text")
                result = result[0]["text"]
            verification = json.loads(result)["verification"]
            return web.json_response(response({
                "type": "message", "id": "msg_probe", "status": "completed", "role": "assistant",
                "content": [{"type": "output_text", "text": whitespace + verification + whitespace,
                             "annotations": []}]}))

        app = web.Application()
        app.router.add_post("/v1/responses", upstream)
        server = TestServer(app)
        await server.start_server()
        try:
            row = {**deepcopy(ROUTE if registration is None else registration),
                   "api_base": str(server.make_url("/v1")).rstrip("/"), "api_key_env": ""}
            if registration is None:
                row["responses"]["structured_tool_outputs"] = True
            report = await probe(row, case)
            profile = profile_from_reports("synthetic-probe", row, [report])
            self.assertEqual(profile["checks"][0]["status"], report["status"])
            self.assertEqual(profile["checks"][0]["cli_version"], "none")
            self.assertFalse(inspect_profile(profile)["isolated_cli_verified"])
            self.assertFalse(report["execution_performed"])
            self.assertEqual(report["requests"], len(seen))
            if change_source or reject or source_transform:
                self.assertEqual(report["status"], "failed")
                self.assertEqual(len(seen), 1)
            elif whitespace:
                self.assertEqual(report["status"], "failed")
                self.assertEqual(len(seen), 2)
                self.assertFalse(report["verification_exact"])
                self.assertTrue(report["verification_after_trim"])
            else:
                self.assertEqual(report["status"], "passed")
                self.assertEqual(len(seen), 2)
                self.assertTrue(report["input_exact"])
                self.assertTrue(report["verification_exact"])
            return report
        finally:
            await server.close()

    async def test_exact_call_continues_once_with_synthetic_output(self):
        await self.exercise()
        candidates = Path(__file__).resolve().parents[1] / "assets" / "responses"
        for model in ("deepseek-v4-flash", "glm-5.3-flash"):
            row = json.loads((candidates / (model + ".candidate.json")).read_text(encoding="utf-8"))
            for case in ("json", "sse"):
                with self.subTest(model=model, case=case):
                    await self.exercise(case=case, registration=row)

    async def test_model_source_change_never_continues(self):
        await self.exercise(change_source=True)
        for transform, first_byte, crlf, backslashes in (
                (lambda value: value.replace("\r\n", "\n"), 32, 0, 4),
                (lambda value: value.replace("\\\\path", "\\path"), 17, 1, 3)):
            with self.subTest(first_byte=first_byte):
                report = await self.exercise(case="unicode-json", source_transform=transform)
                detail = report["source_difference"]
                self.assertEqual(detail["first_different_utf8_byte"], first_byte)
                self.assertEqual(detail["actual_crlf_count"], crlf)
                self.assertEqual(detail["actual_backslash_count"], backslashes)
                self.assertTrue(all(type(v) is int or v is None for v in detail.values()))

    async def test_final_whitespace_is_diagnostic_only_and_never_passes(self):
        for whitespace in (" ", "\n", "\r\n", "\t", "\u00a0"):
            with self.subTest(whitespace=repr(whitespace)):
                await self.exercise(whitespace=whitespace)

    async def test_rejection_never_retries(self):
        await self.exercise(reject=True)

    async def test_explicit_character_named_and_structured_cases_keep_request_semantics(self):
        for case in ("unicode-json", "unicode-json-lf", "unicode-arguments", "unicode-arguments-lf",
                     "named", "structured", "long-lines"):
            with self.subTest(case=case):
                await self.exercise(case=case)
