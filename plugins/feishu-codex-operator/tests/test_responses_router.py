"""Adapted HTTP/WS integration against isolated loopback fake upstreams."""

import asyncio
from copy import deepcopy
from dataclasses import replace
import gzip
import json
import unittest
from unittest.mock import patch

from test_responses_tools import CAPABILITIES, ROUTE, BEEPER, EXEC, FUNCTION, CODE, prepare, response
from test_responses_events import events_for, wire
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
            if self.mode == "bad_arguments":
                item["arguments"] = '{"input":null}'
            result = response(*([] if self.mode == "missing_required" else [item]))
            if not body.get("stream"):
                return web.Response(body=dumps(result).encode(), content_type="application/json",
                                    headers={"ETag": '"upstream-bytes"', "Content-MD5": "old",
                                             "X-Fixture": "retained"})
            events = events_for(item)
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

    async def test_invalid_request_is_rejected_before_network(self):
        result = await self.client.post(self.endpoint,
            json={**self.payload, "previous_response_id": "opaque-prior-response"})
        self.assertEqual(result.status, 400)
        self.assertEqual(self.received, [])

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

    async def test_upstream_errors_are_unchanged_and_never_retried(self):
        self.mode = "error"
        result = await self.client.post(self.endpoint, json=self.payload)
        self.assertEqual(result.status, 429)
        self.assertEqual(await result.read(), self.error_body)
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
