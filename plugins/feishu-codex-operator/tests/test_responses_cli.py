"""Opt-in current CLI exec contract, isolated home and synthetic loopback only."""

import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_responses_tools import BEEPER, ROUTE
from test_responses_events import events_for, wire
from operator_core.model_registry import ModelRegistry
from operator_core.responses_tool_adapter import dumps

try:
    from aiohttp import web
    from aiohttp.test_utils import TestServer
    from operator_core.model_router import ModelRouter, prepare_request
except ImportError:
    web = None


MCP_FIXTURE = r'''
import json, sys
for line in sys.stdin:
    request = json.loads(line)
    if "id" not in request:
        continue
    method, params = request.get("method"), request.get("params", {})
    if method == "initialize":
        result = {"protocolVersion": params["protocolVersion"], "capabilities": {"tools": {}},
                  "serverInfo": {"name": "operator-synthetic-fixture", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": [{"name": "fixture_add", "description": "Add two synthetic small integers.",
            "inputSchema": {"type": "object", "properties": {"left": {"type": "integer"},
                            "right": {"type": "integer"}}, "required": ["left", "right"],
                            "additionalProperties": False}, "annotations": {"readOnlyHint": True}}]}
    elif method == "tools/call" and params.get("name") == "fixture_add":
        args = params.get("arguments", {})
        valid = set(args) == {"left", "right"} and all(type(v) is int and 0 <= v <= 99 for v in args.values())
        result = {"isError": not valid, "content": [{"type": "text", "text":
            "SYNTHETIC_NESTED_SUM_" + str(args["left"] + args["right"]) if valid else "invalid_fixture_input"}]}
    elif method == "ping":
        result = {}
    else:
        print(json.dumps({"jsonrpc": "2.0", "id": request["id"],
                          "error": {"code": -32601, "message": "fixture_method_not_found"}}), flush=True)
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
'''


@unittest.skipUnless(web is not None and os.environ.get("CODEX_OPERATOR_TEST_CLI"),
                     "explicit current Desktop CLI and optional router environment required")
class CurrentCliExecTests(unittest.IsolatedAsyncioTestCase):
    async def test_current_cli_nested_tool_capability_rejection_roundtrip(self):
        await self.exercise(
            'await tools.view_image({path: FIXTURE_PATH});',
            "view_image is not allowed because you do not support image inputs")

    async def test_current_cli_exec_nested_mcp_and_serial_capability_roundtrip(self):
        await self.exercise(
            'const tool = ALL_TOOLS.find(tool => tool.name.endsWith("__fixture_add")); '
            'if (!tool) throw new Error("synthetic_nested_tool_missing"); '
            'text(await tools[tool.name]({left:17, right:25}));',
            "SYNTHETIC_NESTED_SUM_42", nested_mcp=True, serial_only=True, json_mode=True)

    async def exercise(self, code, expected, nested_mcp=False, serial_only=False, json_mode=False):
        executable = Path(os.environ["CODEX_OPERATOR_TEST_CLI"]).resolve(strict=True)
        received, wire_tools = [], []
        request_retries = 0

        async def upstream(request):
            nonlocal request_retries
            body = await request.json()
            received.append(body)
            if len(received) > 2:
                request_retries += 1
                return web.json_response({"error": "synthetic_no_retry"}, status=409)
            if len(received) == 1:
                tools = [tool for tool in body.get("tools", [])
                         if tool.get("description", "").startswith("Tool exec.")]
                if len(tools) != 1:
                    return web.json_response({"error": "synthetic_exec_missing"}, status=400)
                item = {"id": "fc_cli", "type": "function_call", "call_id": "call_cli",
                        "name": tools[0]["name"], "status": "completed",
                        "arguments": dumps({"input": code})}
                if json_mode:
                    return web.json_response({"id": "resp_cli_call", "object": "response",
                                              "status": "completed", "output": [item]})
                return web.Response(body=wire(events_for(item)), content_type="text/event-stream")
            outputs = [item for item in body.get("input", [])
                       if item.get("type") == "function_call_output" and item.get("call_id") == "call_cli"]
            if len(outputs) != 1 or expected not in dumps(outputs[0].get("output")):
                return web.json_response({"error": "synthetic_exec_output_missing"}, status=400)
            # This assistant output belongs only to the disposable synthetic CLI
            # test. It is never an Operator/Feishu final-answer transport.
            item = {"id": "msg_cli", "type": "message", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": "SYNTHETIC_EXEC_VERIFIED", "annotations": []}]}
            response = {"id": "resp_cli_done", "object": "response", "status": "completed", "output": [item]}
            if json_mode:
                return web.json_response(response)
            events = [
                {"type": "response.created", "response": {**response, "status": "in_progress", "output": []}},
                {"type": "response.output_item.added", "output_index": 0,
                 "item": {**item, "status": "in_progress", "content": []}},
                {"type": "response.output_text.delta", "item_id": "msg_cli", "output_index": 0,
                 "content_index": 0, "delta": "SYNTHETIC_EXEC_VERIFIED"},
                {"type": "response.output_item.done", "output_index": 0, "item": item},
                {"type": "response.completed", "response": response},
            ]
            return web.Response(body=wire(events), content_type="text/event-stream")

        app = web.Application()
        app.router.add_post("/v1/responses", upstream)
        server = TestServer(app)
        await server.start_server()
        row = {**deepcopy(ROUTE), "api_base": str(server.make_url("/v1")).rstrip("/")}
        row["responses"].update(structured_tool_outputs=True)
        if nested_mcp:
            # Real CLI returns a content-part list; the string-only fixture uses
            # the same explicit compatibility mode needed by local endpoints.
            row["responses"].update(structured_tool_outputs=False, text_tool_outputs="json_string")
        if serial_only:
            row["responses"]["parallel_tool_calls"] = False
        if json_mode:
            row["responses"]["upstream_response_mode"] = "json"
        registry = ModelRegistry({"version": 2, "models": [row]}, BEEPER)
        router = ModelRouter(registry, "c" * 64)
        gateway = TestServer(router.app())
        await gateway.start_server()
        child = None
        try:
            with tempfile.TemporaryDirectory(prefix="operator-cli-exec-") as directory:
                root = Path(directory)
                work, isolated_home = root / "work", root / "home"
                work.mkdir()
                isolated_home.mkdir()
                fixture = work / "synthetic.png"
                code = code.replace("FIXTURE_PATH", dumps(str(fixture)))
                native = {"models": [{**BEEPER["models"][0], "slug": "synthetic-native"}]}
                catalog = root / "catalog.json"
                catalog.write_text(dumps(registry.merge(native)), encoding="utf-8")
                endpoint = str(gateway.make_url(router.prefix)).rstrip("/")
                settings = {
                    "model": row["slug"], "model_provider": "operator_fixture",
                    "model_reasoning_effort": "low", "model_catalog_json": str(catalog),
                    "approval_policy": "never",
                    "web_search": "disabled",
                    "model_providers.operator_fixture.name": "Operator synthetic fixture",
                    "model_providers.operator_fixture.base_url": endpoint,
                    "model_providers.operator_fixture.wire_api": "responses",
                    "model_providers.operator_fixture.env_key": "OPERATOR_FIXTURE_API_KEY",
                    "model_providers.operator_fixture.request_max_retries": 0,
                    "model_providers.operator_fixture.stream_max_retries": 0,
                    "model_providers.operator_fixture.supports_websockets": False,
                }
                if nested_mcp:
                    settings.update({
                        "mcp_servers.operator_fixture.command": sys.executable,
                        "mcp_servers.operator_fixture.args": ["-u", "-c", MCP_FIXTURE],
                    })
                command = [str(executable), "exec", "--ephemeral", "--ignore-user-config",
                           "--ignore-rules", "--skip-git-repo-check", "--color", "never",
                           "--sandbox", "read-only", "-C", str(work)]
                for key, value in settings.items():
                    command += ["-c", key + "=" + dumps(value)]
                command += ["Synthetic protocol fixture. Execute exactly this code once using exec, then stop: " + code]
                environment = {k: v for k, v in os.environ.items() if not k.upper().startswith(
                    ("CODEX_", "OPENAI_", "CHATGPT_", "DEEPSEEK_", "GLM_"))}
                environment.update(CODEX_HOME=str(isolated_home), OPERATOR_FIXTURE_API_KEY="synthetic-key")

                def inspect(payload, *args):
                    # Tool definitions only; no prompt, history, paths, auth or output.
                    wire_tools.append([{"type": t.get("type"), "name": t.get("name"),
                                        "format": t.get("format")} for t in payload.get("tools", [])])
                    try:
                        return prepare_request(payload, *args)
                    except ValueError as error:
                        wire_tools.append({"validation_reason": str(error),
                                           "parallel": payload.get("parallel_tool_calls")})
                        raise

                with patch("operator_core.model_router.prepare_request", side_effect=inspect):
                    child = await asyncio.create_subprocess_exec(*command, cwd=str(work), env=environment,
                        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        creationflags=0x08000000 if os.name == "nt" else 0)
                    stdout, stderr = await asyncio.wait_for(child.communicate(), timeout=45)
                details = {"exit_code": child.returncode, "request_count": len(received),
                           "wire_tools": wire_tools, "diagnostics": router.last_failure,
                           "synthetic_outputs": [item.get("output") for body in received[1:]
                               for item in body.get("input", []) if item.get("call_id") == "call_cli"
                               and item.get("type") == "function_call_output"],
                           "stderr_tail": stderr.decode("utf-8", errors="replace")[-2500:]}
                self.assertEqual(child.returncode, 0, details)
                self.assertEqual(request_retries, 0)
                self.assertEqual(len(received), 2, details)
                self.assertTrue(all(body["stream"] is (not json_mode) for body in received))
                if serial_only:
                    self.assertTrue(all(body["parallel_tool_calls"] is False for body in received))
                self.assertIn(b"SYNTHETIC_EXEC_VERIFIED", stdout)
        finally:
            if child is not None and child.returncode is None:
                child.kill()
                await child.wait()
            await gateway.close()
            await server.close()


if __name__ == "__main__":
    unittest.main()
