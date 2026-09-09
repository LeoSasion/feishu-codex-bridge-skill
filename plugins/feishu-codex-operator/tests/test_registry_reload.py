"""Explicit reload with isolated HTTP/WS clients; no real models or Desktop."""
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from operator_core.model_registry import ModelRegistry, RouterError
from operator_core.model_router import ModelRouter
from operator_core import model_router_config as settings
import operator_model_router as service

TOKEN = "a" * 64
ROUTE = {"slug": "local/old", "display_name": "Original", "model": "old",
         "api_base": "http://127.0.0.1:1/v1", "api_key_env": "",
         "context_window": 32000, "reasoning_efforts": ["low"]}


class SnapshotRouter(ModelRouter):
    """Use real admission and sockets; expose only the captured registry IDs."""
    async def handle(self, request):
        if request.path.endswith("/snapshot-ws"):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            async for _ in ws:
                await ws.send_json(list(self.registry.routes))
            return ws
        if request.path.endswith("/snapshot-wait"):
            self.entered.set()
            await self.release.wait()
            return web.json_response(list(self.registry.routes))
        return await super().handle(request)


class RegistryReloadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="operator-reload-test-")
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)
        self.path = self.state / "registry.json"
        self.old_digest = self.write_registry([ROUTE])
        registry, digest = ModelRegistry.load_snapshot(self.path)
        self.router = SnapshotRouter(registry, TOKEN, registry_sha256=digest)
        self.router.entered, self.router.release = asyncio.Event(), asyncio.Event()
        app = self.router.app()

        @web.middleware
        async def control(request, handler):
            if request.path == self.router.prefix + "/lifecycle/registry":
                return await service.registry_reload_response(self.router, self.state, request)
            return await handler(request)

        app.middlewares.insert(0, control)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    def write_registry(self, rows):
        raw = json.dumps({"version": 1, "models": rows}).encode()
        settings.atomic_write(self.path, raw)
        return hashlib.sha256(raw).hexdigest()

    async def reload(self, digest, **kwargs):
        return await self.client.post(self.router.prefix + "/lifecycle/registry",
            json={"expected_registry_sha256": digest}, **kwargs)

    async def test_active_http_and_websocket_keep_snapshots_new_requests_see_removal(self):
        ws = await self.client.ws_connect(self.router.prefix + "/snapshot-ws")
        await ws.send_str("before")
        self.assertEqual(await ws.receive_json(), ["local/old"])
        waiting = asyncio.create_task(self.client.get(self.router.prefix + "/snapshot-wait"))
        await self.router.entered.wait()
        digest = self.write_registry([])
        with patch.object(ModelRegistry, "load_snapshot", wraps=ModelRegistry.load_snapshot) as loader:
            result = await self.reload(digest)
            self.assertEqual(result.status, 200)
            self.assertTrue((await result.json())["changed"])
            self.assertEqual(loader.call_count, 1)
            self.router.release.set()
            old_response = await waiting
            self.assertEqual(await old_response.json(), ["local/old"])
            await ws.send_str("after")
            self.assertEqual(await ws.receive_json(), ["local/old"])
            for _ in range(10):
                fresh = await self.client.get(self.router.prefix + "/health")
                self.assertEqual((await fresh.json())["external_models"], 0)
            repeated = await self.reload(digest)
            self.assertFalse((await repeated.json())["changed"])
            self.assertEqual(loader.call_count, 1, "normal requests and same-digest reload perform no registry reads")
        await ws.close()

    async def test_native_catalog_preserved_and_new_request_uses_new_external_rows(self):
        native = {"models": [{"slug": "native-test", "visibility": "list", "opaque": ["keep"]}], "extra": "keep"}
        async def models(request): return web.json_response(native)
        app = web.Application(); app.router.add_get("/models", models)
        upstream = TestServer(app); await upstream.start_server(); self.addAsyncCleanup(upstream.close)
        self.router.native_base = str(upstream.make_url("" )).rstrip("/")
        headers = {"Authorization": "Bearer synthetic"}
        before = await self.client.get(self.router.prefix + "/models", headers=headers)
        before_json = await before.json()
        row = deepcopy(ROUTE); row.update(slug="local/new", model="new")
        result = await self.reload(self.write_registry([row]))
        self.assertEqual(result.status, 200)
        after = await self.client.get(self.router.prefix + "/models", headers=headers)
        value = await after.json()
        self.assertEqual(value["models"][0], native["models"][0])
        self.assertEqual(value["extra"], "keep")
        self.assertEqual(value["models"][1], before_json["models"][1], "Beeper stays unchanged")
        self.assertEqual(value["models"][-1]["slug"], "local/new")
        self.assertNotEqual(before.headers["ETag"], after.headers["ETag"])

    async def test_changed_digest_is_throttled_and_same_digest_noop_has_no_io(self):
        with patch("operator_core.model_router.perf_counter", return_value=100):
            first = await self.router.reload_registry(self.path, self.old_digest)
            self.assertFalse(first["changed"])
            digest = self.write_registry([])
            await self.router.reload_registry(self.path, digest)
            next_digest = self.write_registry([ROUTE])
            with self.assertRaisesRegex(RouterError, "throttled"):
                await self.router.reload_registry(self.path, next_digest)
        with patch("operator_core.model_router.perf_counter", return_value=130):
            result = await self.router.reload_registry(self.path, next_digest)
            self.assertTrue(result["changed"])

    async def test_invalid_candidate_preserves_memory_and_does_not_retry(self):
        raw = b'{"version":1,"models":[],"models":[]}'
        self.path.write_bytes(raw)
        result = await self.reload(hashlib.sha256(raw).hexdigest())
        self.assertEqual(result.status, 400)
        self.assertEqual(self.router.registry_sha256, self.old_digest)
        self.assertIn("local/old", self.router.registry.routes)
        result = await self.reload("b" * 64)
        self.assertEqual(result.status, 409)
        self.assertIn("throttled", (await result.json())["error"])

    async def test_digest_mismatch_preserves_memory(self):
        result = await self.reload("b" * 64)
        self.assertEqual(result.status, 409)
        self.assertEqual((await result.json())["error"], "registry_reload_digest_mismatch")
        self.assertEqual(self.router.registry_sha256, self.old_digest)

    async def test_io_is_off_event_loop_and_concurrent_reload_is_rejected(self):
        started, release = threading.Event(), threading.Event()
        loader = ModelRegistry.load_snapshot
        def blocked(*args):
            started.set()
            if not release.wait(5): raise RuntimeError("test release timeout")
            return loader(*args)
        digest = self.write_registry([])
        with patch.object(ModelRegistry, "load_snapshot", side_effect=blocked):
            task = asyncio.create_task(self.router.reload_registry(self.path, digest))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 2))
                with self.assertRaisesRegex(RouterError, "busy"):
                    await self.router.reload_registry(self.path, digest)
                health = await asyncio.wait_for(self.client.get(self.router.prefix + "/health"), 1)
                self.assertEqual((await health.json())["external_models"], 1)
            finally:
                release.set()
                await task

    async def test_control_rejects_browser_methods_and_extra_fields(self):
        endpoint = self.router.prefix + "/lifecycle/registry"
        for headers in ({"Origin": "http://example.invalid"}, {"Sec-Fetch-Site": "same-origin"}):
            result = await self.reload(self.old_digest, headers=headers)
            self.assertEqual(result.status, 403)
        result = await self.client.get(endpoint)
        self.assertEqual(result.status, 405)
        for payload in ({}, {"expected_registry_sha256": "invalid"},
                        {"expected_registry_sha256": self.old_digest, "path": "elsewhere"}):
            result = await self.client.post(endpoint, json=payload)
            self.assertEqual(result.status, 400)
        result = await self.client.post(endpoint, data=b"x" * 1025, headers={"Content-Type": "application/json"})
        self.assertEqual(result.status, 400)
        self.assertEqual(self.router.registry_sha256, self.old_digest)

    async def test_oversized_registry_is_rejected(self):
        raw = b" " * 1048577; self.path.write_bytes(raw)
        result = await self.reload(hashlib.sha256(raw).hexdigest())
        self.assertEqual(result.status, 400)
        self.assertEqual(self.router.registry_sha256, self.old_digest)

    async def test_cancelled_read_never_publishes_later(self):
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        loader = ModelRegistry.load_snapshot
        def blocked(*args):
            started.set()
            try:
                if not release.wait(5): raise RuntimeError("test release timeout")
                return loader(*args)
            finally:
                finished.set()
        digest = self.write_registry([])
        with patch.object(ModelRegistry, "load_snapshot", side_effect=blocked):
            task = asyncio.create_task(self.router.reload_registry(self.path, digest))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 2))
                task.cancel()
                with self.assertRaises(asyncio.CancelledError): await task
            finally:
                release.set()
                self.assertTrue(await asyncio.to_thread(finished.wait, 2))
        self.assertEqual(self.router.registry_sha256, self.old_digest)
        self.assertIn("local/old", self.router.registry.routes)

    def test_client_detects_old_router_before_sending_reload(self):
        with patch.object(service, "control", return_value={"status": "ready", "diagnostics": {}}), \
                patch.object(service, "build_opener") as sender:
            with self.assertRaisesRegex(RouterError, "requires_upgraded"):
                service.reload_registry(self.state, 4317, self.old_digest)
            sender.assert_not_called()


if __name__ == "__main__": unittest.main()
