"""Isolated loopback tests. No Codex process, credential store or real provider."""

import asyncio
import gzip
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch, AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from operator_core.model_registry import ModelRegistry, RouterError
from operator_core.beeper_provider import BEEPER_IDENTITY_TEXT

try:
    import aiohttp
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from yarl import URL
    from operator_core.model_router import ModelRouter
except ImportError:
    aiohttp = None


CATALOG = {"models": [{"slug": "native-test", "display_name": "Native", "visibility": "list",
    "context_window": 123456, "comp_hash": "preserve", "model_messages": {"opaque": ["keep"]},
    "supported_reasoning_levels": [{"effort": "low", "description": "Low"}]}], "opaque": "retain"}
BEEPER = json.loads((Path(__file__).resolve().parents[1] /
    "scripts/operator_core/beeper_model_catalog.json").read_text(encoding="utf-8"))
TOKEN = "a" * 64
ROUTE = {"slug": "api/example", "display_name": "Example",
         "model": "upstream-test", "api_base": "http://127.0.0.1:1/v1", "api_key_env": "ROUTER_TEST_KEY",
         "context_window": 32000, "reasoning_efforts": ["low"]}


def registry(*routes):
    return ModelRegistry({"version": 1, "models": list(routes)}, BEEPER)


class RegistryTests(unittest.TestCase):
    def test_merge_preserves_native_and_does_not_mutate_inputs(self):
        original = deepcopy(CATALOG)
        result = registry(ROUTE).merge(CATALOG)
        self.assertEqual(CATALOG, original)
        self.assertEqual(result["models"][:1], original["models"])
        self.assertEqual([r["slug"] for r in result["models"]], ["native-test", "beeper", "api/example"])
        result["models"][0]["model_messages"]["opaque"].append("change")
        self.assertEqual(CATALOG, original)

    def test_collision_is_terminal(self):
        catalog = deepcopy(CATALOG)
        catalog["models"].append({"slug": "beeper"})
        with self.assertRaises(RouterError):
            registry().merge(catalog)

    def test_rejects_unsafe_or_duplicate_routes(self):
        for changes in ({"slug": "native-test"}, {"adapter": "web"},
                        {"api_base": "http://remote.example/v1"},
                        {"api_base": "https://secret@remote.example/v1"},
                        {"api_key_env": "bad-name"}, {"context_window": -1}):
            with self.subTest(changes=changes), self.assertRaises(RouterError):
                registry({**ROUTE, **changes})
        with self.assertRaises(RouterError):
            registry(ROUTE, ROUTE)


@unittest.skipIf(aiohttp is None, "optional router environment is required")
class RouterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.received = []
        self.endpoints = []
        self.handshakes = []
        self.redirect_ws = False
        self.response_headers = {}
        self.ws_headers = {}
        self.status = 200
        self.json_response = False
        self.stream_body = b'data: {"type":"response.completed"}\n\ndata: [DONE]\n\n'

        async def upstream(request):
            if request.path.endswith("/models"):
                return web.json_response(CATALOG)
            if request.method == "GET":
                self.handshakes.append(request.path)
                if self.redirect_ws:
                    return web.Response(status=302, headers={"Location": self.base + "/leak"})
                ws = web.WebSocketResponse()
                ws.headers.update(self.ws_headers)
                if request.headers.get("X-Test-State"):
                    ws.headers["X-Codex-Turn-State"] = request.headers["X-Test-State"]
                await ws.prepare(request)
                async for message in ws:
                    if message.type == aiohttp.WSMsgType.TEXT:
                        self.received.append((message.data.encode(), dict(request.headers)))
                        await ws.send_str(message.data)
                return ws
            body = await request.read()
            self.endpoints.append(request.raw_path)
            self.received.append((body, dict(request.headers)))
            if self.json_response:
                return web.json_response({"id": "resp_test", "object": "response", "created_at": 1,
                    "model": "gpt-5", "status": "completed", "output": [], "usage": {
                    "input_tokens": 1, "output_tokens": 1, "total_tokens": 2}})
            if self.status == 302:
                return web.Response(status=302, headers={"Location": self.base + "/leak"})
            return web.Response(status=self.status, body=self.stream_body,
                                headers={"Content-Type": "text/event-stream", "X-Proof": "preserved",
                                         **self.response_headers})

        app = web.Application(handler_args={"auto_decompress": False})
        app.router.add_route("*", "/{path:.*}", upstream)
        self.upstream = TestServer(app)
        await self.upstream.start_server()
        self.base = str(self.upstream.make_url("" )).rstrip("/")

        self.router = ModelRouter(registry({**ROUTE, "api_base": self.base + "/v1"},
            {**ROUTE, "slug": "local/example", "api_key_env": "", "api_base": self.base + "/v1"}),
            TOKEN, native_base=self.base)
        self.client = TestClient(TestServer(self.router.app()))
        await self.client.start_server()
        self.headers = {"Authorization": "Bearer native-test-secret", "ChatGPT-Account-Id": "native-account",
                        "Content-Type": "application/json", "X-Test": "preserve-native"}
        self.prefix = self.router.prefix
        self.env = patch.dict(os.environ, {"ROUTER_TEST_KEY": "external-test-secret"})
        self.env.start()

    async def asyncTearDown(self):
        self.env.stop()
        await self.client.close()
        await self.upstream.close()

    async def test_local_failures_have_safe_standard_errors_and_no_retry(self):
        cases = [(RuntimeError("private prompt secret"), "internal"),
                 (asyncio.TimeoutError("private endpoint secret"), "timeout"),
                 (aiohttp.ClientConnectionError("private credential secret"), "transport")]
        for exc, category in cases:
            with self.subTest(category=category):
                before = self.router.failure_count
                with patch.object(self.router, "local_response", new=AsyncMock(side_effect=exc)) as invoke:
                    response = await self.client.post(self.prefix + "/responses", json={"model": "beeper", "input": "hello"})
                    self.assertEqual(response.status, 502)
                    result = await response.json()
                    self.assertEqual(result["error"]["code"], "router_" + category + "_no_retry")
                    self.assertEqual(invoke.await_count, 1)
                self.assertEqual(self.router.failure_count, before + 1)
                self.assertEqual(self.router.last_failure, {"phase": "local_response", "category": category,
                                                          "upstream_status": None})
                self.assertNotIn("secret", json.dumps([result, self.router.last_failure]))

    async def test_large_upstream_header_is_preserved(self):
        value = "x" * 16384
        self.response_headers = {"X-Codex-Turn-State": value}
        response = await self.client.post(self.prefix + "/responses", headers=self.headers,
            json={"model": "native-test", "input": "synthetic"}, max_field_size=65536)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["X-Codex-Turn-State"], value)
        self.assertEqual(await response.read(), self.stream_body)
        self.assertEqual(len(self.received), 1)

    async def test_oversized_upstream_header_fails_without_retry(self):
        self.response_headers = {"X-Synthetic-Large": "x" * 65537}
        response = await self.client.post(self.prefix + "/responses", headers=self.headers,
            json={"model": "native-test", "input": "synthetic"})
        self.assertEqual(response.status, 502)
        self.assertEqual((await response.json())["error"]["code"], "router_transport_no_retry")
        self.assertEqual(len(self.received), 1)

    async def test_upstream_502_is_preserved_and_distinguished(self):
        self.status = 502
        self.stream_body = b"upstream original error"
        response = await self.client.post(self.prefix + "/responses", headers=self.headers,
                                          json={"model": "native-test", "input": "test"})
        self.assertEqual(response.status, 502)
        self.assertEqual(await response.read(), self.stream_body)
        self.assertEqual(self.router.last_failure, {"phase": "http_connect", "category": "upstream_http",
                                                  "upstream_status": 502})
        self.assertEqual(len(self.received), 1)

    async def test_catalog_validator_tracks_augmented_body(self):
        import hashlib
        first = await self.client.get(self.prefix + "/models", headers=self.headers)
        body = await first.read()
        etag = first.headers["ETag"]
        self.assertEqual(etag, '"' + hashlib.sha256(body).hexdigest() + '"')
        self.assertEqual(first.headers["Cache-Control"], "no-cache")
        self.assertIn("Authorization", first.headers["Vary"])
        again = await self.client.get(self.prefix + "/models", headers={**self.headers, "If-None-Match": etag})
        self.assertEqual(again.status, 200)
        self.assertEqual(again.headers["ETag"], etag)
        await again.read()
        with patch.dict(CATALOG, {"opaque": "changed"}):
            changed = await self.client.get(self.prefix + "/models", headers=self.headers)
            self.assertNotEqual(changed.headers["ETag"], etag)
            await changed.read()

    async def test_catalog_and_native_http_are_lossless(self):
        response = await self.client.get(self.prefix + "/models?client_version=0.1.0", headers=self.headers)
        catalog = await response.json()
        self.assertEqual(CATALOG["models"], catalog["models"][:1])
        raw = b'{ "model" : "native-test", "input": [], "future_field": {"opaque":"yes"} }'
        response = await self.client.post(self.prefix + "/responses", data=raw, headers=self.headers)
        self.assertEqual(200, response.status)
        self.assertEqual(b'data: {"type":"response.completed"}\n\ndata: [DONE]\n\n', await response.read())
        self.assertEqual(raw, self.received[0][0])
        self.assertEqual("Bearer native-test-secret", self.received[0][1]["Authorization"])
        self.assertEqual("preserved", response.headers["X-Proof"])

    async def test_external_route_never_receives_native_credentials(self):
        response = await self.client.post(self.prefix + "/responses", json={"model": "api/example", "input": "hello"}, headers=self.headers)
        await response.read()
        body, headers = self.received[0]
        self.assertEqual("upstream-test", json.loads(body)["model"])
        self.assertEqual("Bearer external-test-secret", headers["Authorization"])
        self.assertNotIn("ChatGPT-Account-Id", headers)
        self.assertNotIn("X-Test", headers)

    async def test_unknown_model_never_dispatches(self):
        response = await self.client.post(self.prefix + "/responses", json={"model": "api/unknown", "input": "hello"}, headers=self.headers)
        self.assertEqual(400, response.status)
        self.assertEqual([], self.received)

    async def test_native_auxiliary_endpoints_preserve_opaque_payloads(self):
        for endpoint, body, content_type in (
            ("alpha/search", b'{ "query":"example", "future":true }', "application/json"),
            ("images/generations", b'{"model":"image-model","prompt":"example"}', "application/json"),
            ("images/edits", b'--boundary\r\nContent-Disposition: form-data; name="image"\r\n\r\n\x00\xff\r\n--boundary--\r\n',
             "multipart/form-data; boundary=boundary")):
            response = await self.client.post(URL(self.prefix + "/" + endpoint + "?opaque=a%2Fb", encoded=True), data=body,
                headers={**self.headers, "Content-Type": content_type})
            self.assertEqual(200, response.status)
            await response.read()
            self.assertEqual(body, self.received[-1][0])
            self.assertEqual(content_type, self.received[-1][1]["Content-Type"])
            self.assertEqual("Bearer native-test-secret", self.received[-1][1]["Authorization"])
            self.assertEqual("/" + endpoint + "?opaque=a%2Fb", self.endpoints[-1])

    async def test_auxiliary_routes_reject_missing_auth_wrong_method_and_unknown_path(self):
        response = await self.client.post(self.prefix + "/alpha/search", json={"query": "example"})
        self.assertEqual(400, response.status)
        response = await self.client.get(self.prefix + "/images/edits", headers=self.headers)
        self.assertEqual(405, response.status)
        response = await self.client.post(self.prefix + "/images/unknown", json={}, headers=self.headers)
        self.assertEqual(404, response.status)
        self.assertEqual([], self.received)

    async def test_native_compressed_request_and_response_bytes_are_preserved(self):
        try:
            from compression import zstd
        except ImportError:
            from backports import zstd
        raw = b'{ "model":"native-test", "input":[], "future_field":"retain" }'
        self.stream_body = gzip.compress(self.stream_body)
        self.response_headers = {"Content-Encoding": "gzip"}
        for encoding, body in (("gzip", gzip.compress(raw)), ("zstd", zstd.compress(raw))):
            response = await self.client.post(self.prefix + "/responses", data=body,
                headers={**self.headers, "Content-Encoding": encoding}, auto_decompress=False)
            self.assertEqual(200, response.status, await response.text() if response.status != 200 else "")
            self.assertEqual(self.stream_body, await response.read())
            self.assertEqual(body, self.received[-1][0])
            self.assertEqual(encoding, self.received[-1][1]["Content-Encoding"])

    async def test_compressed_external_requests_still_isolate_credentials(self):
        raw = json.dumps({"model": "api/example", "input": "unchanged"}).encode()
        response = await self.client.post(self.prefix + "/responses", data=gzip.compress(raw),
            headers={**self.headers, "Content-Encoding": "gzip"})
        self.assertEqual(200, response.status)
        await response.read()
        body, headers = self.received[0]
        self.assertEqual("unchanged", json.loads(body)["input"])
        self.assertEqual("Bearer external-test-secret", headers["Authorization"])
        self.assertNotIn("Content-Encoding", headers)
        self.assertNotIn("ChatGPT-Account-Id", headers)

    async def test_incomplete_and_oversized_compressed_requests_never_dispatch(self):
        from operator_core import model_router
        for raw in (b"not-gzip", gzip.compress(b'{"model":"native-test"}')[:-2], gzip.compress(b"x" * 2048)):
            with patch.object(model_router, "MAX_BODY", 1024):
                response = await self.client.post(self.prefix + "/responses", data=raw,
                    headers={**self.headers, "Content-Encoding": "gzip"})
            self.assertEqual(400, response.status)
        self.assertEqual([], self.received)

    async def test_identity_works_without_native_credentials(self):
        response = await self.client.post(self.prefix + "/responses", json={"model": "beeper", "input": "hello"})
        self.assertEqual(BEEPER_IDENTITY_TEXT, (await response.json())["output"][0]["content"][0]["text"])
        self.assertEqual([], self.received)

    async def test_request_fields_cannot_override_destination_or_key(self):
        response = await self.client.post(self.prefix + "/responses", json={
            "model": "api/example", "input": "hello", "api_key": "injected",
            "api_base": "https://untrusted.invalid", "num_retries": 99})
        await response.read()
        self.assertEqual(1, len(self.received))
        self.assertEqual("Bearer external-test-secret", self.received[0][1]["Authorization"])

    async def test_local_endpoint_has_no_native_authorization(self):
        response = await self.client.post(self.prefix + "/responses", json={
            "model": "local/example", "input": "hello"}, headers=self.headers)
        await response.read()
        self.assertEqual(200, response.status)
        self.assertNotIn("Authorization", self.received[0][1])

    async def test_standard_nonstreaming_response_and_request_fields(self):
        self.json_response = True
        payload = {"model": "api/example", "input": [{"type": "function_call_output",
            "call_id": "call_example", "output": "原样结果"}], "instructions": "unchanged",
            "tools": [{"type": "function", "name": "example", "parameters": {"type": "object"}}],
            "reasoning": {"effort": "low"}, "text": {"format": {"type": "json_object"}},
            "previous_response_id": "resp_previous", "store": False, "stream": False,
            "future_field": {"keep": True}}
        response = await self.client.post(self.prefix + "/responses", json=payload)
        result = await response.json()
        self.assertEqual(200, response.status, result)
        self.assertEqual("completed", result["status"])
        self.assertEqual(1, len(self.received))
        self.assertEqual("Bearer external-test-secret", self.received[0][1]["Authorization"])
        self.assertEqual({**payload, "model": "upstream-test"}, json.loads(self.received[0][0]))

    async def test_redirect_and_rate_limit_never_retry(self):
        for status in (302, 429):
            self.status = status
            before = len(self.received)
            response = await self.client.post(self.prefix + "/responses", json={"model": "api/example", "input": "hello"})
            await response.read()
            self.assertEqual(400 if status == 302 else 429, response.status)
            self.assertEqual(before + 1, len(self.received))

    async def test_native_compaction_is_opaque(self):
        payload = {"model": "native-test", "input": [{"type": "compaction", "encrypted_content": "opaque"}]}
        response = await self.client.post(self.prefix + "/responses/compact", json=payload, headers=self.headers)
        await response.read()
        self.assertEqual(payload, json.loads(self.received[0][0]))
        response = await self.client.post(self.prefix + "/responses", json={**payload, "model": "local/example"})
        self.assertEqual(400, response.status)
        self.assertEqual(1, len(self.received))

    async def test_token_and_browser_origin_protection(self):
        response = await self.client.get("/v1/models")
        self.assertEqual(404, response.status)
        response = await self.client.get(self.prefix + "/models", headers={"Origin": "http://untrusted.example"})
        self.assertEqual(400, response.status)

    async def test_native_websocket_forwards_only_native_handshake_metadata(self):
        self.ws_headers = {"X-Codex-Turn-State": "synthetic-state", "X-Models-Etag": "synthetic-etag",
                           "Set-Cookie": "private-cookie", "X-Unrelated": "private-value"}
        raw = '{"type":"response.create","model":"native-test","input":[]}'
        async with self.client.ws_connect(self.prefix + "/responses", headers=self.headers) as ws:
            await ws.send_str(raw)
            metadata = await asyncio.wait_for(ws.receive_json(), 3)
            self.assertEqual(metadata, {"type": "response.metadata", "headers": {
                "x-codex-turn-state": "synthetic-state"}})
            catalog_metadata = await asyncio.wait_for(ws.receive_json(), 3)
            self.assertEqual(catalog_metadata, {"type": "codex.response.metadata", "headers": {
                "x-models-etag": "synthetic-etag"}})
            self.assertEqual(raw, (await asyncio.wait_for(ws.receive(), 3)).data)
        self.assertEqual(1, len(self.received))
        self.assertIsNone(self.router.last_failure)

    async def test_concurrent_websocket_metadata_is_connection_local(self):
        async def exchange(state):
            async with self.client.ws_connect(self.prefix + "/responses",
                    headers={**self.headers, "X-Test-State": state}) as ws:
                await ws.send_json({"type": "response.create", "model": "native-test", "input": []})
                metadata = await asyncio.wait_for(ws.receive_json(), 3)
                self.assertEqual(metadata["headers"], {"x-codex-turn-state": state})
                self.assertEqual("response.create", (await asyncio.wait_for(ws.receive_json(), 3))["type"])
        await asyncio.gather(exchange("state-a"), exchange("state-b"))
        self.assertEqual(2, len(self.received))

    async def test_native_websocket_preserves_first_message(self):
        raw = '{ "type":"response.create", "model":"native-test", "input":[] }'
        async with self.client.ws_connect(self.prefix + "/responses", headers=self.headers) as ws:
            await ws.send_str(raw)
            message = await asyncio.wait_for(ws.receive(), 3)
            self.assertEqual(raw, message.data)
        self.assertEqual(raw.encode(), self.received[0][0])

    async def test_native_websocket_redirect_is_not_followed(self):
        self.redirect_ws = True
        async with self.client.ws_connect(self.prefix + "/responses", headers=self.headers) as ws:
            await ws.send_json({"type": "response.create", "model": "native-test", "input": []})
            message = await asyncio.wait_for(ws.receive(), 3)
            self.assertEqual(aiohttp.WSMsgType.CLOSE, message.type)
            self.assertEqual(1011, message.data)
        self.assertEqual(["/responses"], self.handshakes)
        self.assertEqual([], self.received)

    async def test_native_websocket_never_sends_a_later_external_model_to_native(self):
        async with self.client.ws_connect(self.prefix + "/responses", headers=self.headers) as ws:
            await ws.send_json({"type": "response.create", "model": "native-test", "input": []})
            await asyncio.wait_for(ws.receive(), 3)
            await ws.send_json({"type": "response.create", "model": "api/example", "input": []})
            message = await asyncio.wait_for(ws.receive(), 3)
            self.assertEqual(aiohttp.WSMsgType.CLOSE, message.type)
        self.assertEqual(1, len(self.received))

    async def test_external_tool_events_are_preserved_over_http_and_websocket(self):
        events = [
            {"type": "response.created", "response": {"id": "resp_test", "status": "in_progress"}},
            {"type": "response.output_item.added", "output_index": 0, "item": {
                "type": "function_call", "id": "fc_test", "call_id": "call_test", "name": "example"}},
            {"type": "response.function_call_arguments.delta", "item_id": "fc_test",
                "output_index": 0, "delta": '{"text":"你好"}'},
            {"type": "response.completed", "response": {"id": "resp_test", "status": "completed"}}]
        self.stream_body = b"".join(b"event: " + e["type"].encode() + b"\ndata: " +
            json.dumps(e, ensure_ascii=False).encode() + b"\n\n" for e in events)
        response = await self.client.post(self.prefix + "/responses", json={
            "model": "api/example", "input": "hello", "stream": True})
        self.assertEqual(self.stream_body, await response.read())
        async with self.client.ws_connect(self.prefix + "/responses", headers=self.headers) as ws:
            await ws.send_json({"type": "response.create", "model": "api/example", "input": "hello"})
            for event in events:
                self.assertEqual(event, await asyncio.wait_for(ws.receive_json(), 3))
        self.assertEqual(2, len(self.received))

    async def test_truncated_external_websocket_stream_closes_without_retry(self):
        self.stream_body = b'data: {"type":"response.created"}\n\ndata: [DONE]\n\n'
        async with self.client.ws_connect(self.prefix + "/responses") as ws:
            await ws.send_json({"type": "response.create", "model": "api/example", "input": "hello"})
            self.assertEqual("response.created", (await ws.receive_json())["type"])
            message = await asyncio.wait_for(ws.receive(), 3)
            self.assertEqual(aiohttp.WSMsgType.CLOSE, message.type)
            self.assertEqual(1011, message.data)
        self.assertEqual(1, len(self.received))

    async def test_beeper_websocket_emits_identity(self):
        async with self.client.ws_connect(self.prefix + "/responses") as ws:
            await ws.send_json({"type": "response.create", "model": "beeper", "input": "hello"})
            text = ""
            for _ in range(8):
                event = await asyncio.wait_for(ws.receive_json(), 3)
                if event["type"] == "response.output_text.delta":
                    text += event["delta"]
            self.assertEqual(BEEPER_IDENTITY_TEXT, text)


if __name__ == "__main__":
    unittest.main()
