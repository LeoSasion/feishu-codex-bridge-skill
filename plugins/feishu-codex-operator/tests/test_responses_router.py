"""Adapted HTTP/WS integration against isolated loopback fake upstreams."""

import asyncio
from copy import deepcopy
from dataclasses import replace
import gzip
import json
import unittest
from unittest.mock import patch

from test_responses_tools import CAPABILITIES, ROUTE, BEEPER, EXEC, FUNCTION, CODE, prepare, response
from test_responses_events import events_for, text_events, wire
from operator_core.model_registry import ModelRegistry
from operator_core.responses_capabilities import ResponsesCapabilities
from operator_core.responses_events import DONE, SSEDecoder
from operator_core.responses_tool_adapter import NAMED_OUTPUT_PREFIX, dumps

try:
    import aiohttp
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from operator_core.model_router import ModelRouter
except ImportError:
    aiohttp = None


@unittest.skipIf(aiohttp is None, "optional router environment is required")
class AdaptedRouterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.received, self.mode = [], "normal"
        self.scripted_items = None
        self.reasoning_tail = None
        self.cancelled, self.waiting = asyncio.Event(), asyncio.Event()
        self.error_body = b'{"error":{"message":"synthetic failure"}}'

        async def upstream(request):
            body = await request.json()
            self.received.append((body, dict(request.headers)))
            if self.mode == "error":
                return web.Response(status=429, body=self.error_body, content_type="application/json")
            if self.mode == "history_only":
                result = response({"type": "message", "id": "msg_history", "role": "assistant",
                    "status": "completed", "content": [{"type": "output_text", "text": "HISTORY_OK"}]})
                return web.Response(body=dumps(result).encode(), content_type="application/json")
            alias = next(tool["name"] for tool in body["tools"] if tool["type"] == "function")
            item = {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": alias,
                    "arguments": dumps({"input": CODE}), "status": "completed"}
            if self.scripted_items is not None:
                item = deepcopy(self.scripted_items.pop(0))
            if self.mode == "bad_arguments":
                item["arguments"] = '{"input":null}'
            result = response(*([] if self.mode == "missing_required" else [item]))
            if self.mode == "input_part_tail":
                result["output"].append({"id": "msg_invalid", "type": "message", "role": "assistant",
                    "status": "completed", "content": [{"type": "input_text", "text": "synthetic input-only part"}]})
            if self.reasoning_tail is not None:
                result["output"].append(deepcopy(self.reasoning_tail))
            if self.mode == "json_wait":
                output = web.StreamResponse(headers={"Content-Type": "application/json"})
                await output.prepare(request)
                await output.write(b'{')
                self.waiting.set()
                try:
                    await asyncio.Future()
                finally:
                    self.cancelled.set()
            if self.mode == "json_incomplete":
                result["status"] = "incomplete"
            if self.mode == "json_truncated":
                return web.Response(body=dumps(result).encode()[:-1], content_type="application/json")
            if self.mode == "json_sse":
                return web.Response(body=wire(events_for(item)), content_type="text/event-stream")
            if not body.get("stream"):
                return web.Response(body=dumps(result).encode(), content_type="application/json",
                                    headers={"ETag": '"upstream-bytes"', "Content-MD5": "old",
                                             "X-Fixture": "retained"})
            events = (text_events(item["content"][0]["text"], part_type=item["content"][0]["type"])
                      if item["type"] in {"message", "reasoning"} else events_for(item))
            if self.reasoning_tail is not None:
                tail = deepcopy(self.reasoning_tail)
                added = {**tail, "status": "in_progress", "summary": []}
                if "content" in added and added["content"] is not None:
                    added["content"] = []
                events.insert(-1, {"type": "response.output_item.added", "output_index": 1, "item": added})
                events.insert(-1, {"type": "response.output_item.done", "output_index": 1, "item": tail})
                events[-1]["response"] = result
            if self.mode in {"truncated", "wait"}:
                events = events[:-1]
            if self.mode == "final_mismatch":
                events[-1]["response"]["output"][0] = {**item, "arguments": '{"input":"changed"}'}
            if self.mode == "compressed":
                return web.Response(body=gzip.compress(wire(events)), content_type="text/event-stream",
                                    headers={"Content-Encoding": "gzip"})
            output = web.StreamResponse(headers={"Content-Type": "text/event-stream", "ETag": "old"})
            await output.prepare(request)
            # A short initial record ensures errors later in the stream are exercised.
            for event in events:
                await output.write(wire([event], newline=b"\r\n"))
            if self.mode == "wait":
                self.waiting.set()
                try:
                    await asyncio.Future()
                finally:
                    self.cancelled.set()
            await output.write_eof()
            return output

        app = web.Application(handler_args={"handler_cancellation": True})
        app.router.add_post("/v1/responses", upstream)
        self.upstream = TestServer(app)
        await self.upstream.start_server()
        base = str(self.upstream.make_url("/v1")).rstrip("/")
        route = {**deepcopy(ROUTE), "api_base": base, "api_key_env": "OPERATOR_FIXTURE_KEY"}
        self.router = ModelRouter(ModelRegistry({"version": 2, "models": [route]}, BEEPER), "b" * 64)
        self.client = TestClient(TestServer(self.router.app()))
        await self.client.start_server()
        self.endpoint = self.router.prefix + "/responses"
        self.payload = {"model": ROUTE["slug"], "tools": [EXEC], "input": "Synthetic tool request."}
        self.env = patch.dict("os.environ", {"OPERATOR_FIXTURE_KEY": "external-fixture-value"})
        self.env.start()

    async def asyncTearDown(self):
        self.env.stop()
        await self.client.close()
        await self.upstream.close()

    def enable_input_tools(self):
        route = self.router.registry.routes[ROUTE["slug"]]
        changes = {"input_tool_definitions": "additional_tools_v1",
                   "custom_tools": {"exec": "wrap", "functions.exec": "wrap"}}
        self.router.registry.routes[ROUTE["slug"]] = replace(route,
            responses=ResponsesCapabilities.parse({**CAPABILITIES, **changes}))
        return changes

    async def test_input_tool_declarations_complete_three_rounds_over_json_sse_and_ws(self):
        await self.assert_three_rounds()

    async def test_json_upstream_complete_three_rounds_over_json_sse_and_ws(self):
        await self.assert_three_rounds(json_mode=True)

    async def test_strict_completed_output_rejects_reasoning_only_once_on_every_transport(self):
        reason = text_events("private <tool_call>not executable</tool_call>", part_type="reasoning_text")[-1]["response"]["output"][0]
        route = self.router.registry.routes[ROUTE["slug"]]
        for upstream_mode in ("match_client", "json"):
            self.router.registry.routes[ROUTE["slug"]] = replace(route, responses=replace(
                route.responses, upstream_response_mode=upstream_mode, completed_output_policy="require_message_or_tool"))
            for transport in ("json", "sse", "ws"):
                with self.subTest(upstream=upstream_mode, transport=transport):
                    self.scripted_items = [reason]
                    before = len(self.received)
                    if transport == "ws":
                        ws = await self.client.ws_connect(self.endpoint)
                        await ws.send_json({**self.payload, "type": "response.create"})
                        seen = []
                        while True:
                            frame = await ws.receive(timeout=2)
                            if frame.type == aiohttp.WSMsgType.CLOSE:
                                break
                            self.assertEqual(frame.type, aiohttp.WSMsgType.TEXT)
                            seen.append(json.loads(frame.data))
                        await ws.close()
                        self.assertFalse(any(e["type"] == "response.completed" for e in seen))
                    else:
                        reply = await self.client.post(self.endpoint, json={**self.payload, "stream": transport == "sse"})
                        raw = await reply.read()
                        if upstream_mode == "json" or transport == "json":
                            self.assertEqual(reply.status, 502)
                            self.assertNotIn(b"private", raw)
                        self.assertNotIn(b"response.completed", raw)
                        self.assertNotIn(b'"type":"custom_tool_call"', raw)
                    self.assertEqual(len(self.received) - before, 1)
                    self.assertEqual(self.router.last_failure["protocol_reason"], "completed_response_without_message_or_tool")

    def enable_json_upstream(self):
        route = self.router.registry.routes[ROUTE["slug"]]
        self.router.registry.routes[ROUTE["slug"]] = replace(route,
            responses=replace(route.responses, upstream_response_mode="json"))

    async def test_reasoning_null_and_invalid_terminal_preserve_tool_gate_on_all_transports(self):
        route = self.router.registry.routes[ROUTE["slug"]]
        for upstream_mode in ("match_client", "json"):
            self.router.registry.routes[ROUTE["slug"]] = replace(route, responses=replace(
                route.responses, upstream_response_mode=upstream_mode))
            for fields in ({}, {"content": None}, {"content": []},
                           {"content": "private malformed reasoning"}, {"summary": None},
                           {"status": "incomplete"}):
                valid = fields in ({}, {"content": None}, {"content": []})
                self.reasoning_tail = {"type": "reasoning", "id": "rs_1", "summary": [], **fields}
                for transport in ("json", "sse", "ws"):
                    with self.subTest(upstream=upstream_mode, fields=fields, transport=transport):
                        before, events = len(self.received), []
                        if transport == "ws":
                            ws = await self.client.ws_connect(self.endpoint)
                            await ws.send_json({**self.payload, "type": "response.create"})
                            for _ in range(30):
                                frame = await ws.receive(timeout=2)
                                if frame.type == aiohttp.WSMsgType.CLOSE:
                                    break
                                self.assertEqual(frame.type, aiohttp.WSMsgType.TEXT)
                                events.append(json.loads(frame.data))
                                if events[-1]["type"] == "response.completed":
                                    break
                            else:
                                self.fail("Reasoning fixture did not terminate within its event bound")
                            await ws.close()
                            completed = next((e["response"] for e in events if e["type"] == "response.completed"), None)
                        else:
                            reply = await self.client.post(self.endpoint, json={**self.payload, "stream": transport == "sse"})
                            raw = await reply.read()
                            if not valid and reply.status == 502:
                                self.assertNotIn(b"custom_tool_call", raw)
                                self.assertNotIn(b"private malformed reasoning", raw)
                                completed = None
                            elif transport == "json":
                                self.assertEqual(reply.status, 200 if valid else 502)
                                completed = json.loads(raw) if valid else None
                            else:
                                self.assertEqual(reply.status, 200)
                                decoder = SSEDecoder()
                                events = [e for e in decoder.feed(raw) + decoder.finish() if e is not DONE]
                                completed = next((e["response"] for e in events if e["type"] == "response.completed"), None)
                        if valid:
                            self.assertIsNotNone(completed)
                            self.assertEqual(completed["output"][0]["input"], CODE)
                            self.assertEqual(completed["output"][1], self.reasoning_tail)
                        else:
                            self.assertIsNone(completed)
                            self.assertFalse(any(e.get("item", {}).get("type") in {"function_call", "custom_tool_call"}
                                                 for e in events))
                            self.assertIn(self.router.last_failure["protocol_reason"],
                                          {"invalid_reasoning_content", "unfinished_reasoning_item"})
                        self.assertEqual(len(self.received) - before, 1)

    async def test_invalid_reasoning_history_is_rejected_before_upstream(self):
        for fields in ({"content": "private malformed reasoning"}, {"summary": None}, {"status": "in_progress"}):
            payload = {**self.payload, "input": [{"type": "reasoning", "summary": [], **fields}]}
            reply = await self.client.post(self.endpoint, json=payload)
            self.assertEqual(reply.status, 400)
            self.assertNotIn(b"private malformed reasoning", await reply.read())
            ws = await self.client.ws_connect(self.endpoint)
            await ws.send_json({**payload, "type": "response.create"})
            self.assertEqual((await ws.receive(timeout=2)).type, aiohttp.WSMsgType.CLOSE)
            await ws.close()
        self.assertEqual(self.received, [])

    async def assert_three_rounds(self, json_mode=False):
        changes = self.enable_input_tools()
        if json_mode:
            self.enable_json_upstream()
            changes["upstream_response_mode"] = "json"
        block = {"type": "additional_tools", "role": "developer", "id": "at_fixture", "tools": [
            {"type": "namespace", "name": "functions", "tools": [EXEC]},
            {"type": "namespace", "name": "plugin_fixture", "tools": [FUNCTION]}]}
        message = {"role": "user", "content": "Synthetic protocol loop; no tool execution."}
        payload = {"model": ROUTE["slug"], "input": [block, message]}
        wire_request, context = prepare(payload, **changes)
        custom_alias = context.specs[("custom", "functions", "exec")].upstream_name
        function_alias = context.specs[("function", "plugin_fixture", "add")].upstream_name
        raw_custom = {"type": "function_call", "id": "fc_custom", "call_id": "call_custom",
                      "name": custom_alias, "arguments": dumps({"input": CODE}), "status": "completed"}
        raw_function = {"type": "function_call", "id": "fc_function", "call_id": "call_function",
                        "name": function_alias, "arguments": '{ "a": 17 }', "status": "completed"}
        final = text_events("LOOP_OK")[-1]["response"]["output"][0]
        custom_result = {"type": "custom_tool_call_output", "call_id": "call_custom", "name": "exec",
                         "output": "  synthetic 中文😀\r\n\n  "}
        function_result = {"type": "function_call_output", "call_id": "call_function", "name": "add",
                           "namespace": "plugin_fixture", "output": "17"}
        expected_rounds = None
        for transport in ("json", "sse", "ws"):
            with self.subTest(transport=transport):
                self.scripted_items = [raw_custom, raw_function, final]
                history, rounds = [deepcopy(block), deepcopy(message)], []
                ws = await self.client.ws_connect(self.endpoint) if transport == "ws" else None
                start = len(self.received)
                try:
                    for turn in range(3):
                        body = {**payload, "input": deepcopy(history), "tool_choice": "none" if turn == 2 else "auto"}
                        original = deepcopy(body)
                        if ws is not None:
                            await ws.send_json({**body, "type": "response.create"})
                            events = []
                            while True:
                                event = await ws.receive_json(timeout=2)
                                events.append(event)
                                if event["type"] == "response.completed":
                                    break
                            result = events[-1]["response"]
                        else:
                            reply = await self.client.post(self.endpoint, json={**body, "stream": transport == "sse"})
                            self.assertEqual(reply.status, 200)
                            if transport == "json":
                                result = await reply.json()
                            else:
                                decoder = SSEDecoder()
                                events = decoder.feed(await reply.read()) + decoder.finish()
                                self.assertIs(events.pop(), DONE)
                                result = events[-1]["response"]
                        self.assertEqual(body, original)
                        item = result["output"][0]
                        rounds.append(item)
                        sent = self.received[-1][0]
                        self.assertEqual(sent["stream"], False if json_mode else transport != "json")
                        self.assertEqual(sent["tools"], wire_request["tools"])
                        self.assertFalse(any(i.get("type") == "additional_tools" for i in sent["input"]))
                        self.assertEqual(sent["input"][0], message)
                        if turn == 0:
                            self.assertEqual((item["type"], item["namespace"], item["input"]),
                                             ("custom_tool_call", "functions", CODE))
                            history.extend([item, custom_result])
                        elif turn == 1:
                            self.assertEqual((item["type"], item["namespace"], item["name"]),
                                             ("function_call", "plugin_fixture", "add"))
                            self.assertEqual(sent["input"][1], raw_custom)
                            self.assertEqual(sent["input"][2]["name"], custom_alias)
                            self.assertEqual(sent["input"][2]["output"], custom_result["output"])
                            history.extend([item, function_result])
                        else:
                            self.assertEqual(item, final)
                            self.assertEqual(sent["input"][3], raw_function)
                            self.assertEqual(sent["input"][4], {"type": "function_call_output",
                                "call_id": "call_function", "name": function_alias, "output": "17"})
                    self.assertEqual(len(self.received) - start, 3)
                    self.assertEqual(self.scripted_items, [])
                    if expected_rounds is None:
                        expected_rounds = rounds
                    else:
                        self.assertEqual(rounds, expected_rounds)
                finally:
                    if ws is not None:
                        await ws.close()

    async def test_json_upstream_rejects_invalid_complete_body_before_any_tool_event(self):
        self.enable_json_upstream()
        for mode in ("bad_arguments", "json_truncated", "json_incomplete", "json_sse", "input_part_tail"):
            for transport in ("json", "sse", "ws"):
                with self.subTest(mode=mode, transport=transport):
                    self.mode = mode
                    before = len(self.received)
                    if transport != "ws":
                        reply = await self.client.post(self.endpoint, json={**self.payload, "stream": transport == "sse"})
                        self.assertEqual(reply.status, 502)
                        raw = await reply.read()
                    else:
                        ws = await self.client.ws_connect(self.endpoint)
                        await ws.send_json({**self.payload, "type": "response.create"})
                        frame = await ws.receive(timeout=2)
                        self.assertEqual(frame.type, aiohttp.WSMsgType.CLOSE)
                        raw = str(frame.data).encode()
                        await ws.close()
                    self.assertNotIn(b"custom_tool_call", raw)
                    self.assertNotIn(CODE.encode(), raw)
                    self.assertEqual(len(self.received) - before, 1)
                    self.assertFalse(self.received[-1][0]["stream"])
                    if mode == "input_part_tail":
                        self.assertEqual(self.router.last_failure["protocol_reason"], "invalid_output_message_content")

    async def test_json_upstream_disconnect_cancels_http_and_ws_without_retry(self):
        self.enable_json_upstream()
        self.mode = "json_wait"
        for transport in ("http", "ws"):
            self.cancelled.clear()
            self.waiting.clear()
            if transport == "http":
                pending = asyncio.create_task(self.client.post(self.endpoint, json={**self.payload, "stream": True}))
                await asyncio.wait_for(self.waiting.wait(), 2)
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            else:
                ws = await self.client.ws_connect(self.endpoint)
                await ws.send_json({**self.payload, "type": "response.create"})
                await asyncio.wait_for(self.waiting.wait(), 2)
                await ws.close()
            await asyncio.wait_for(self.cancelled.wait(), 2)
        self.assertEqual(len(self.received), 2)

    async def test_json_event_expansion_rejects_http_and_ws_before_any_call_without_retry(self):
        self.enable_json_upstream()
        item = {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "exec",
                "status": "completed", "arguments": dumps({"input": "text('界');\n" * 40})}
        self.assertLess(len(dumps(response(item)).encode("utf-8")), 2048)
        self.scripted_items = [deepcopy(item) for _ in range(3)]
        with patch("operator_core.responses_events.MAX_STREAM_BYTES", 2048):
            # The complete JSON fits; only its repeated event representation exceeds the budget.
            reply = await self.client.post(self.endpoint, json={**self.payload, "stream": False})
            self.assertEqual(reply.status, 200)
            self.assertEqual((await reply.json())["output"][0]["type"], "custom_tool_call")
            reply = await self.client.post(self.endpoint, json={**self.payload, "stream": True})
            self.assertEqual(reply.status, 502)
            raw = await reply.read()
            for forbidden in (b"response.created", b"custom_tool_call", b"response.completed"):
                self.assertNotIn(forbidden, raw)
            ws = await self.client.ws_connect(self.endpoint)
            await ws.send_json({**self.payload, "type": "response.create"})
            self.assertEqual((await ws.receive(timeout=2)).type, aiohttp.WSMsgType.CLOSE)
            await ws.close()
        self.assertEqual(len(self.received), 3)
        self.assertTrue(all(not body["stream"] for body, _ in self.received))

    async def test_json_upstream_preserves_http_error_and_existing_size_bound(self):
        self.enable_json_upstream()
        self.mode = "error"
        reply = await self.client.post(self.endpoint, json={**self.payload, "stream": True})
        self.assertEqual(reply.status, 429)
        self.assertEqual(await reply.read(), self.error_body)
        self.mode = "normal"
        # Smaller fixture bound admits the request but rejects the larger complete response.
        size = len(dumps(prepare(self.payload)[0]).encode()) + 150
        self.scripted_items = [text_events("X" * (size + 200))[-1]["response"]["output"][0]]
        with patch("operator_core.model_router.MAX_BODY", size):
            reply = await self.client.post(self.endpoint, json={**self.payload, "stream": True})
            self.assertEqual(reply.status, 502)
            self.assertNotIn(b"response.completed", await reply.read())
        self.assertEqual(len(self.received), 2)

    async def test_input_tools_opt_in_and_invalid_definitions_fail_before_upstream(self):
        block = {"type": "additional_tools", "role": "developer", "tools": [EXEC]}
        body = {**self.payload, "tools": [], "input": [block]}
        reply = await self.client.post(self.endpoint, json=body)
        self.assertEqual(reply.status, 400)
        self.assertEqual((await reply.json())["error"]["message"], "input_tool_definitions_not_supported")
        self.enable_input_tools()
        for broken in ({**block, "role": "user"}, {**block, "tools": [{"type": "web_search"}]},
                       {**block, "tools": [EXEC, {**EXEC, "description": "conflict"}]}):
            payload = {**body, "input": [broken]}
            reply = await self.client.post(self.endpoint, json=payload)
            self.assertEqual(reply.status, 400)
            ws = await self.client.ws_connect(self.endpoint)
            await ws.send_json({**payload, "type": "response.create"})
            self.assertEqual((await ws.receive(timeout=2)).type, aiohttp.WSMsgType.CLOSE)
            await ws.close()
        self.assertEqual(self.received, [])

    async def test_json_expanded_history_is_rejected_before_http_or_ws_dispatch(self):
        source = "\\" * (1024 * 1024)
        payload = {**self.payload, "input": [
            {"type": "custom_tool_call", "call_id": "bound", "name": "exec", "input": source},
            {"type": "custom_tool_call_output", "call_id": "bound", "output": "synthetic"}]}
        for stream in (False, True):
            reply = await self.client.post(self.endpoint, json={**payload, "stream": stream})
            self.assertEqual(reply.status, 400)
            self.assertEqual((await reply.json())["error"]["message"], "protocol_string_too_large")
        ws = await self.client.ws_connect(self.endpoint)
        await ws.send_json({**payload, "type": "response.create"})
        self.assertEqual((await ws.receive(timeout=2)).type, aiohttp.WSMsgType.CLOSE)
        await ws.close()
        self.assertEqual(self.received, [])

    async def test_input_tools_failed_streams_never_release_executable_calls(self):
        self.enable_input_tools()
        body = {"model": ROUTE["slug"], "stream": True, "input": [
            {"type": "additional_tools", "role": "developer", "tools": [
                {"type": "namespace", "name": "functions", "tools": [EXEC]}]}]}
        for mode in ("truncated", "final_mismatch", "bad_arguments"):
            self.mode = mode
            for transport in ("sse", "ws"):
                with self.subTest(mode=mode, transport=transport):
                    if transport == "sse":
                        reply = await self.client.post(self.endpoint, json=body)
                        received = bytearray()
                        try:
                            async for chunk in reply.content.iter_any():
                                received.extend(chunk)
                        except aiohttp.ClientPayloadError:
                            pass
                    else:
                        ws = await self.client.ws_connect(self.endpoint)
                        await ws.send_json({**body, "type": "response.create"})
                        received = bytearray()
                        while True:
                            message = await ws.receive(timeout=2)
                            if message.type == aiohttp.WSMsgType.TEXT:
                                received.extend(message.data.encode())
                            else:
                                self.assertEqual(message.type, aiohttp.WSMsgType.CLOSE)
                                break
                        await ws.close()
                    self.assertNotIn(b"custom_tool_call", received)
                    self.assertNotIn(b"function_call", received)
                    self.assertNotIn(b"response.completed", received)
        self.assertEqual(len(self.received), 6)

    async def test_nullable_tool_description_and_content_free_error_field(self):
        result = await self.client.post(self.endpoint, json={**self.payload,
            "tools": [{**EXEC, "description": None}]})
        self.assertEqual(result.status, 200)
        self.assertEqual((await result.json())["output"][0]["input"], CODE)
        self.assertEqual(len(self.received), 1)
        result = await self.client.post(self.endpoint, json={**self.payload,
            "tools": [{**EXEC, "description": {"private_fixture": "must-not-leak"}}]})
        self.assertEqual(result.status, 400)
        self.assertEqual(await result.json(), {"error": {"type": "invalid_request_error",
            "message": "invalid_protocol_string", "param": "tools.description"}})
        self.assertEqual(len(self.received), 1)

    async def test_hosted_tools_are_classified_before_name_validation_without_forwarding(self):
        for tool, message, field, code in (
            ({"type": "web_search"}, "unsupported_tool_type", "tools.type", "unsupported_tool_type"),
            ({"type": "web_search_preview"}, "unsupported_tool_type", "tools.type", "unsupported_tool_type"),
            ({"type": "file_search"}, "unsupported_tool_type", "tools.type", "unsupported_tool_type"),
            ({"type": "image_generation"}, "unsupported_tool_type", "tools.type", "unsupported_tool_type"),
            ({"type": "code_interpreter"}, "unsupported_tool_type", "tools.type", "unsupported_tool_type"),
            ({"type": "private_unsupported_kind"}, "unsupported_tool_type", "tools.type",
             "unsupported_tool_type"),
            ({"type": ["private_invalid_kind"]}, "unsupported_tool_type", "tools.type",
             "unsupported_tool_type"),
            ({**FUNCTION, "name": "private.invalid.name"}, "invalid_tool_name", "tools.name",
             "tool_definition_name_invalid"),
        ):
            payload = {**self.payload, "tools": [EXEC, tool]}
            before = deepcopy(payload)
            result = await self.client.post(self.endpoint, json=payload)
            self.assertEqual(result.status, 400)
            self.assertEqual(await result.json(), {"error": {"type": "invalid_request_error",
                "message": message, "param": field, "code": code}})
            self.assertEqual(payload, before)
            self.assertEqual(self.received, [])

    async def test_named_output_codec_is_explicit_lossless_and_does_not_retry(self):
        self.mode = 'history_only'
        item = {'type': 'function_call_output', 'namespace': 'codex_app', 'id': 'named_fixture_item',
                'internal_chat_message_metadata_passthrough': {'turn_id': 'turn_fixture',
                                                              'create_time': 1788700000.125},
                'name': 'send_message_to_thread', 'output': [
                    {'type': 'input_text', 'text': 'private_synthetic_first', 'part': 1},
                    {'type': 'input_text', 'text': '中文😀\r\nsecond', 'part': 2}]}
        payload = {**self.payload, 'tools': [], 'tool_choice': 'none', 'input': [item]}
        before = deepcopy(payload)
        refused = await self.client.post(self.endpoint, json=payload)
        self.assertEqual(refused.status, 400)
        self.assertEqual(await refused.json(), {'error': {'type': 'invalid_request_error',
            'message': 'named_function_output_not_registered'}})
        self.assertEqual(self.received, [])
        route = self.router.registry.routes[ROUTE['slug']]
        self.router.registry.routes[ROUTE['slug']] = replace(route,
            responses=ResponsesCapabilities.parse({**CAPABILITIES, 'named_function_outputs': {
                'codex_app.send_message_to_thread': 'user_message_json_v1'}}))
        result = await self.client.post(self.endpoint, json=payload)
        self.assertEqual(result.status, 200)
        self.assertEqual((await result.json())['output'][0]['content'][0]['text'], 'HISTORY_OK')
        self.assertEqual(len(self.received), 1)
        forwarded = self.received[0][0]
        self.assertEqual((forwarded['tools'], forwarded['tool_choice']), ([], 'none'))
        message = forwarded['input'][0]
        self.assertEqual(message['role'], 'user')
        self.assertEqual(json.loads(message['content'][0]['text'][len(NAMED_OUTPUT_PREFIX):]), item)
        self.assertEqual(payload, before)
        self.mode = 'error'
        failed = await self.client.post(self.endpoint, json=payload)
        self.assertEqual(failed.status, 429)
        self.assertEqual(await failed.read(), self.error_body)
        self.assertEqual(len(self.received), 2)
        self.assertEqual(self.received[1][0], forwarded)

    async def test_json_restores_call_and_recomputes_headers_without_credential_leak(self):
        result = await self.client.post(self.endpoint, json=self.payload,
            headers={"Authorization": "Bearer native-fixture-value", "ChatGPT-Account-Id": "native-fixture"})
        self.assertEqual(result.status, 200)
        data = await result.read()
        self.assertEqual(json.loads(data)["output"][0]["input"], CODE)
        self.assertEqual(int(result.headers["Content-Length"]), len(data))
        self.assertNotIn("ETag", result.headers)
        self.assertNotIn("Content-MD5", result.headers)
        self.assertEqual(result.headers["X-Fixture"], "retained")
        body, headers = self.received[0]
        self.assertEqual(body["model"], ROUTE["model"])
        self.assertEqual(body["tools"][0]["type"], "function")
        self.assertEqual(headers["Authorization"], "Bearer external-fixture-value")
        self.assertNotIn("ChatGPT-Account-Id", headers)
        self.assertEqual(len(self.received), 1)

    async def test_history_identity_diagnostics_are_content_free_and_never_repair_calls(self):
        call = {"type": "custom_tool_call", "name": "exec", "call_id": "private_call",
                "id": "private_item", "input": "private_source"}
        output = {"type": "custom_tool_call_output", "call_id": "private_call",
                  "output": "private_result"}
        cases = [
            ([], call, "history_tool_definitions_empty"),
            ([EXEC], {**call, "namespace": "private_scope"}, "history_tool_namespace_mismatch"),
            ([EXEC], {**call, "type": "function_call"}, "history_tool_kind_mismatch"),
            ([FUNCTION], call, "history_registered_custom_not_advertised"),
            ([EXEC], {**call, "name": "private_tool"}, "history_unregistered_tool_call"),
            ([EXEC], {**call, "name": None}, "history_tool_name_invalid"),
        ]
        for tools, item, code in cases:
            with self.subTest(code=code):
                payload = {**self.payload, "tools": tools, "input": [item, output]}
                original = deepcopy(payload)
                result = await self.client.post(self.endpoint, json=payload)
                self.assertEqual(result.status, 400)
                error = await result.json()
                self.assertEqual(error, {"error": {"type": "invalid_request_error",
                    "message": "undeclared_tool_call", "param": "input.tool_call", "code": code}})
                self.assertNotIn("private_", dumps(error))
                self.assertEqual(payload, original)
        self.assertEqual(self.received, [])

    async def test_empty_tool_history_requires_opt_in_and_is_forwarded_without_new_tools(self):
        self.mode = "history_only"
        parts = [{"type": "input_text", "text": "one"}, {"type": "input_text", "text": "中文😀"}]
        body = {**self.payload, "tools": [], "tool_choice": "none", "input": [
            {"type": "custom_tool_call", "name": "exec", "id": "ct_history",
             "call_id": "history_call", "input": CODE},
            {"type": "custom_tool_call_output", "call_id": "history_call", "output": parts}]}
        rejected = await self.client.post(self.endpoint, json=body)
        self.assertEqual(rejected.status, 400)
        self.assertEqual(self.received, [])
        route = self.router.registry.routes[ROUTE["slug"]]
        self.router.registry.routes[ROUTE["slug"]] = replace(route,
            responses=ResponsesCapabilities.parse({**CAPABILITIES,
                "history_custom_tools": {"exec": "codex_exec_v1"}, "text_tool_outputs": "json_string"}))
        result = await self.client.post(self.endpoint, json=body)
        self.assertEqual(result.status, 200)
        self.assertEqual((await result.json())["output"][0]["content"][0]["text"], "HISTORY_OK")
        self.assertEqual(len(self.received), 1)
        forwarded = self.received[0][0]
        self.assertEqual(forwarded["tools"], [])
        self.assertEqual(forwarded["tool_choice"], "none")
        self.assertEqual(json.loads(forwarded["input"][0]["arguments"]), {"input": CODE})
        self.assertEqual(json.loads(forwarded["input"][1]["output"]), parts)
        self.assertEqual(forwarded["input"][1]["call_id"], "history_call")

    async def test_http_sse_and_websocket_restore_same_events(self):
        result = await self.client.post(self.endpoint, json={**self.payload, "stream": True})
        self.assertEqual(result.status, 200)
        parser = SSEDecoder()
        events = parser.feed(await result.read()) + parser.finish()
        self.assertIs(events[-1], DONE)
        http_events = events[:-1]
        ws = await self.client.ws_connect(self.endpoint)
        await ws.send_json({**self.payload, "type": "response.create"})
        ws_events = []
        while True:
            event = await ws.receive_json(timeout=2)
            ws_events.append(event)
            if event["type"] == "response.completed":
                break
        await ws.close()
        self.assertEqual(http_events, ws_events)
        self.assertEqual(ws_events[-1]["response"]["output"][0]["input"], CODE)
        self.assertEqual(len(self.received), 2)

    async def test_gzip_requests_are_decoded_for_adaptation_and_duplicates_are_refused(self):
        result = await self.client.post(self.endpoint, data=gzip.compress(dumps(self.payload).encode()),
                                       headers={"Content-Encoding": "gzip", "Content-Type": "application/json"})
        self.assertEqual(result.status, 200)
        self.assertEqual((await result.json())["output"][0]["input"], CODE)
        duplicate = dumps(self.payload)[:-1] + ',"tools":[]}'
        result = await self.client.post(self.endpoint, data=duplicate,
                                       headers={"Content-Type": "application/json"})
        self.assertEqual(result.status, 400)
        self.assertEqual(len(self.received), 1)

    async def test_reasoning_choice_restriction_blocks_http_and_websocket_before_upstream(self):
        route = self.router.registry.routes[ROUTE["slug"]]
        caps = ResponsesCapabilities.parse({**CAPABILITIES,
            "tool_choice_by_reasoning": {"low": ["auto", "none"]}})
        self.router.registry.routes[ROUTE["slug"]] = replace(route, responses=caps)
        body = {**self.payload, "reasoning": {"effort": "low"}, "tool_choice": "required"}
        result = await self.client.post(self.endpoint, json=body)
        self.assertEqual(result.status, 400)
        ws = await self.client.ws_connect(self.endpoint)
        await ws.send_json({**body, "type": "response.create"})
        self.assertEqual((await ws.receive(timeout=2)).type, aiohttp.WSMsgType.CLOSE)
        await ws.close()
        self.assertEqual(self.received, [])
        result = await self.client.post(self.endpoint, json={**body, "tool_choice": "auto"})
        self.assertEqual(result.status, 200)
        self.assertEqual(len(self.received), 1)

    async def test_invalid_success_json_is_502_with_no_code_or_retry(self):
        for mode in ("missing_required", "bad_arguments"):
            self.mode = mode
            result = await self.client.post(self.endpoint,
                json={**self.payload, "tool_choice": "required"})
            self.assertEqual(result.status, 502)
            value = await result.json()
            self.assertEqual(value["error"]["code"], "router_protocol_no_retry")
            self.assertNotIn(CODE, dumps(value))
        self.assertEqual(len(self.received), 2)
        self.assertEqual(self.router.last_failure["category"], "protocol")

    async def test_truncated_or_conflicting_stream_never_delivers_completed_call(self):
        for mode in ("truncated", "final_mismatch", "bad_arguments"):
            self.mode = mode
            result = await self.client.post(self.endpoint, json={**self.payload, "stream": True})
            received = bytearray()
            try:
                async for chunk in result.content.iter_any():
                    received.extend(chunk)
            except aiohttp.ClientPayloadError:
                pass
            self.assertNotIn(b"custom_tool_call", received)
            self.assertNotIn(b'"type":"response.completed"', received)
        self.assertEqual(len(self.received), 3)

    async def test_unexpected_compressed_adapted_response_is_refused(self):
        self.mode = "compressed"
        result = await self.client.post(self.endpoint, json={**self.payload, "stream": True})
        self.assertEqual(result.status, 502)
        self.assertEqual(len(self.received), 1)

    async def test_http_disconnect_cancels_silent_upstream(self):
        self.mode = "wait"
        result = await self.client.post(self.endpoint, json={**self.payload, "stream": True})
        await asyncio.wait_for(self.waiting.wait(), 2)
        result.close()
        await asyncio.wait_for(self.cancelled.wait(), 2)
        self.assertEqual(len(self.received), 1)
        self.assertEqual(self.router.metrics.snapshot()["outcomes"]["cancelled"], 1)
        self.assertEqual(self.router.metrics.snapshot()["active"], 0)

    async def test_websocket_disconnect_cancels_silent_upstream(self):
        self.mode = "wait"
        ws = await self.client.ws_connect(self.endpoint)
        await ws.send_json({**self.payload, "type": "response.create"})
        await asyncio.wait_for(self.waiting.wait(), 2)
        await ws.close()
        await asyncio.wait_for(self.cancelled.wait(), 2)
        self.assertEqual(len(self.received), 1)

    async def test_websocket_provider_switch_is_refused_without_second_send(self):
        ws = await self.client.ws_connect(self.endpoint)
        await ws.send_json({**self.payload, "type": "response.create"})
        while (await ws.receive_json(timeout=2))["type"] != "response.completed":
            pass
        await ws.send_json({"type": "response.create", "model": "api/unregistered"})
        result = await ws.receive(timeout=2)
        self.assertEqual(result.type, aiohttp.WSMsgType.CLOSE)
        self.assertEqual(len(self.received), 1)
        await ws.close()


if __name__ == "__main__":
    unittest.main()
