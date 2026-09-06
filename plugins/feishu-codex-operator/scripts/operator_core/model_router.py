"""Loopback Responses router. No task control, history store, retries or fallback."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import zlib
from contextvars import ContextVar

import aiohttp
from aiohttp import web
from yarl import URL

from .beeper_provider import BeeperResponsesEngine
from .model_registry import ModelRegistry, RouterError

NATIVE_BASE = "https://chatgpt.com/backend-api/codex"
MAX_BODY = 16 * 1024 * 1024
MAX_UPSTREAM_HEADER = 64 * 1024
NATIVE_AUXILIARY = {"alpha/search", "images/generations", "images/edits"}
RESPONSE_STARTED = web.RequestKey("response_started", bool)
PHASE = ContextVar("router_phase", default="request")
WS_METADATA = ContextVar("router_ws_metadata", default=None)
WS_METADATA_HEADERS = {"x-codex-turn-state", "x-models-etag", "openai-model",
                       "x-codex-safety-buffering-enabled",
                       "x-codex-safety-buffering-faster-model"}
HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
               "te", "trailer", "transfer-encoding", "upgrade", "host", "content-length"}


def reject_opaque_context(payload):
    items = payload.get("input")
    if isinstance(items, list) and any(isinstance(item, dict) and
            (item.get("encrypted_content") or item.get("type") == "compaction") for item in items):
        raise RouterError("opaque_cross_provider_context_not_supported")


def clean_headers(headers) -> dict[str, str]:
    excluded = HOP_HEADERS | {part.strip().lower() for part in headers.get("Connection", "").split(",")}
    return {k: v for k, v in headers.items() if k.lower() not in excluded}


def encode(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def decode_request(body: bytes, encoding: str):
    """Inspect a bounded decoded copy for routing; keep native wire bytes intact."""
    encoding = encoding.strip().lower()
    if encoding == "zstd":
        try:
            from compression import zstd
        except ImportError:
            from backports import zstd
        decoder = zstd.ZstdDecompressor()
        try:
            data = decoder.decompress(body, max_length=MAX_BODY + 1)
        except zstd.ZstdError as exc:
            raise RouterError("invalid_compressed_request") from exc
    elif encoding in {"gzip", "deflate"}:
        decoder = zlib.decompressobj(31 if encoding == "gzip" else 15)
        try:
            data = decoder.decompress(body, MAX_BODY + 1)
        except zlib.error as exc:
            raise RouterError("invalid_compressed_request") from exc
    elif encoding == "identity":
        return json.loads(body)
    else:
        raise RouterError("unsupported_content_encoding")
    if len(data) > MAX_BODY or not decoder.eof or decoder.unused_data:
        raise RouterError("oversized_or_incomplete_compressed_request")
    return json.loads(data)


class ModelRouter:
    def __init__(self, registry: ModelRegistry, token: str, *, native_base: str = NATIVE_BASE) -> None:
        if not re.fullmatch(r"[a-f0-9]{64}", token):
            raise RouterError("router_token_requires_64_hex_characters")
        self.registry = registry
        self.prefix = "/" + token + "/v1"
        self.native_base = native_base.rstrip("/")
        self.beeper = BeeperResponsesEngine()
        self.native_models: dict[str, set[str]] = {}
        self.session: aiohttp.ClientSession | None = None
        self.slots = asyncio.Semaphore(16)
        self.failure_count = 0
        self.last_failure = None

    def record_failure(self, exc=None, *, status=None):
        # Fixed categories only: never retain exception messages, request data or URLs.
        category = "upstream_http" if status is not None else "internal"
        if isinstance(exc, aiohttp.ClientSSLError):
            category = "tls"
        elif isinstance(exc, asyncio.TimeoutError):
            category = "timeout"
        elif isinstance(exc, aiohttp.ClientError):
            category = "transport"
        elif isinstance(exc, (RouterError, ValueError, KeyError, TypeError)):
            category = "protocol"
        self.failure_count += 1
        self.last_failure = {"phase": PHASE.get(), "category": category,
                             "upstream_status": status}
        return category

    def app(self) -> web.Application:
        app = web.Application(client_max_size=MAX_BODY, handler_args={"auto_decompress": False})
        app.router.add_route("*", "/{path:.*}", self.admit)
        app.cleanup_ctx.append(self.lifecycle)
        return app

    async def lifecycle(self, _app):
        async def refuse_redirect(_session, _context, _params):
            # ws_connect follows redirects internally; stop before a second handshake.
            raise RouterError("upstream_redirect_refused")

        trace = aiohttp.TraceConfig()
        trace.on_request_redirect.append(refuse_redirect)
        async def capture_handshake(_session, _context, params):
            metadata = WS_METADATA.get()
            if metadata is not None and params.response.status == 101:
                metadata.update({name.lower(): value for name, value in params.response.headers.items()
                                 if name.lower() in WS_METADATA_HEADERS})
        trace.on_request_end.append(capture_handshake)
        # Do not honor proxy environment variables or decompress opaque native payloads.
        async with aiohttp.ClientSession(auto_decompress=False, trust_env=False, trace_configs=[trace],
                max_line_size=MAX_UPSTREAM_HEADER, max_field_size=MAX_UPSTREAM_HEADER,
                timeout=aiohttp.ClientTimeout(total=None, connect=15, sock_read=300)) as session:
            self.session = session
            yield

    async def admit(self, request):
        if self.slots.locked():
            return web.json_response({"error": "router_busy_no_retry"}, status=503)
        async with self.slots:
            return await self.handle(request)

    @staticmethod
    def account(headers) -> str:
        auth = headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or not auth[7:]:
            raise RouterError("native_authorization_required")
        return hashlib.sha256(auth.encode()).hexdigest()

    async def catalog(self, headers, query="") -> dict:
        PHASE.set("catalog")
        account = self.account(headers)
        forwarded = clean_headers(headers)
        forwarded = {k: v for k, v in forwarded.items()
                     if k.lower() not in {"if-none-match", "content-type", "content-encoding"}}
        if not query:
            version = re.search(r"/(\d+\.\d+\.\d+)", headers.get("User-Agent", ""))
            if version:
                query = "?client_version=" + version.group(1)
        forwarded["Accept-Encoding"] = "identity"
        async with self.session.get(URL(self.native_base + "/models" + query, encoded=True),
                                    headers=forwarded, allow_redirects=False) as response:
            if response.status != 200:
                self.record_failure(status=response.status)
                raise RouterError("native_catalog_unavailable")
            data = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                data.extend(chunk)
                if len(data) > MAX_BODY:
                    raise RouterError("native_catalog_too_large")
            catalog = json.loads(data)
        merged = self.registry.merge(catalog)
        # Account hashes and model IDs only; never retain credentials or task context.
        if len(self.native_models) >= 16:
            self.native_models.clear()
        self.native_models[account] = {row["slug"] for row in catalog["models"]}
        return merged

    async def is_native(self, model, headers) -> bool:
        if not isinstance(model, str):
            raise RouterError("model_required")
        if model == "beeper" or model in self.registry.routes:
            return False
        if model.startswith(("api/", "local/", "chatgpt-web/")):
            raise RouterError("model_not_registered")
        account = self.account(headers)
        if account not in self.native_models:
            await self.catalog(headers)
        if model not in self.native_models[account]:
            raise RouterError("model_not_registered")
        return True

    async def handle(self, request: web.Request) -> web.StreamResponse:
        PHASE.set("request")
        try:
            if request.headers.get("Origin") or request.headers.get("Sec-Fetch-Site"):
                raise RouterError("browser_requests_not_supported")
            if request.path == self.prefix + "/health" and request.method == "GET":
                return web.json_response({"status": "ready", "external_models": len(self.registry.routes)})
            if not request.path.startswith(self.prefix + "/"):
                return web.json_response({"error": "not_found"}, status=404)
            endpoint = request.path[len(self.prefix) + 1:]
            query = ("?" + request.rel_url.raw_query_string) if request.query_string else ""
            if endpoint == "models" and request.method == "GET":
                body = encode(await self.catalog(request.headers, query))
                # The validator describes the augmented catalog, never just the upstream.
                etag = '"' + hashlib.sha256(body).hexdigest() + '"'
                return web.Response(body=body, content_type="application/json",
                                    headers={"ETag": etag, "Cache-Control": "no-cache",
                                             "Vary": "Authorization, ChatGPT-Account-Id"})
            if endpoint in NATIVE_AUXILIARY:
                if request.method != "POST":
                    return web.json_response({"error": "method_not_allowed"}, status=405)
                self.account(request.headers)
                # Dedicated native tools have their own model IDs and multipart bodies.
                # Never infer an external provider or parse/rebuild their opaque payload.
                return await self.proxy(request, URL(self.native_base + "/" + endpoint + query, encoded=True),
                                        await request.read(), clean_headers(request.headers))
            if endpoint not in {"responses", "responses/compact"}:
                return web.json_response({"error": "endpoint_not_supported"}, status=404)
            if request.method == "GET" and endpoint == "responses":
                return await self.websocket(request, query)
            if request.method != "POST":
                return web.json_response({"error": "method_not_allowed"}, status=405)
            body = await request.read()
            PHASE.set("decode")
            payload = decode_request(body, request.headers.get("Content-Encoding", "identity"))
            if not isinstance(payload, dict):
                raise RouterError("invalid_request")
            if await self.is_native(payload.get("model"), request.headers):
                return await self.proxy(request, URL(self.native_base + "/" + endpoint + query, encoded=True),
                                        body, clean_headers(request.headers))
            if endpoint != "responses":
                raise RouterError("external_compaction_not_supported")
            route = self.registry.routes.get(payload["model"])
            if route:
                reject_opaque_context(payload)
            if route:
                key = route.key()
                headers = {"Content-Type": "application/json", "Accept-Encoding": "identity"}
                if key:
                    headers["Authorization"] = "Bearer " + key
                return await self.proxy(request, route.api_base.rstrip("/") + "/responses",
                                        encode({**payload, "model": route.model}), headers)
            stream = bool(payload.get("stream"))
            PHASE.set("local_response")
            if not stream:
                result = await self.local_response(payload)
                return web.Response(body=encode(result), content_type="application/json")
            iterator = self.events(payload)
            # Surface failures before emitting HTTP 200. Never fabricate a completed stream.
            first = await anext(iterator)
            output = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"})
            await output.prepare(request)
            request[RESPONSE_STARTED] = True
            try:
                await output.write(b"data: " + encode(first) + b"\n\n")
                async for event in iterator:
                    await output.write(b"data: " + encode(event) + b"\n\n")
                await output.write(b"data: [DONE]\n\n")
                await output.write_eof()
            finally:
                await iterator.aclose()
            return output
        except (RouterError, ValueError, KeyError, TypeError) as exc:
            if request.get(RESPONSE_STARTED):
                if request.transport:
                    request.transport.close()
                return web.StreamResponse()
            message = str(exc) if isinstance(exc, RouterError) else "invalid_request"
            return web.json_response({"error": {"type": "invalid_request_error", "message": message}}, status=400)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            category = self.record_failure(exc)
            if request.get(RESPONSE_STARTED):
                if request.transport:
                    request.transport.close()
                return web.StreamResponse()
            return web.json_response({"error": {"type": "server_error",
                "code": "router_" + category + "_no_retry",
                "message": "Router " + category + " failure; request was not retried."}}, status=502)
        except web.HTTPException as exc:
            return web.json_response({"error": "invalid_http_request"}, status=exc.status)
        except Exception as exc:
            self.record_failure(exc)
            # Never expose request fragments, endpoint keys or provider details.
            if request.get(RESPONSE_STARTED):
                if request.transport:
                    request.transport.close()
                return web.StreamResponse()
            return web.json_response({"error": {"type": "server_error",
                "code": "router_internal_no_retry",
                "message": "Router internal failure; request was not retried."}}, status=502)

    async def proxy(self, request, url, body, headers):
        PHASE.set("http_connect")
        async with self.session.post(url, data=body, headers=headers, allow_redirects=False) as upstream:
            if upstream.status >= 400:
                self.record_failure(status=upstream.status)
            if 300 <= upstream.status < 400:
                raise RouterError("upstream_redirect_refused")
            output = web.StreamResponse(status=upstream.status, headers=clean_headers(upstream.headers))
            await output.prepare(request)
            request[RESPONSE_STARTED] = True
            PHASE.set("http_stream")
            async for chunk in upstream.content.iter_any():
                await output.write(chunk)
            await output.write_eof()
            return output

    async def local_response(self, payload):
        return self.beeper.create(payload)[0]

    async def events(self, payload):
        PHASE.set("response_events")
        if payload["model"] == "beeper":
            for event in self.beeper.create(payload)[1]:
                yield event
            return
        route = self.registry.routes[payload["model"]]
        reject_opaque_context(payload)
        headers = {"Content-Type": "application/json", "Accept-Encoding": "identity"}
        key = route.key()
        if key:
            headers["Authorization"] = "Bearer " + key
        async with self.session.post(route.api_base.rstrip("/") + "/responses",
                data=encode({**payload, "model": route.model, "stream": True}),
                headers=headers, allow_redirects=False) as response:
            if response.status != 200 or "text/event-stream" not in response.headers.get("Content-Type", ""):
                self.record_failure(status=response.status)
                raise RouterError("external_stream_unavailable")
            data_lines, size, terminal = [], 0, False
            async for line in response.content:
                size += len(line)
                if size > MAX_BODY:
                    raise RouterError("external_event_too_large")
                line = line.rstrip(b"\r\n")
                if line.startswith(b"data:"):
                    data_lines.append(line[5:].removeprefix(b" "))
                elif not line:
                    size = 0
                    if not data_lines:
                        continue
                    data = b"\n".join(data_lines)
                    data_lines = []
                    if data == b"[DONE]":
                        if not terminal:
                            raise RouterError("truncated_external_response")
                        return
                    event = json.loads(data)
                    terminal = terminal or event.get("type") in {
                        "response.completed", "response.failed", "response.incomplete", "error"}
                    yield event
            if data_lines or not terminal:
                raise RouterError("truncated_external_response")

    async def websocket(self, request, query):
        PHASE.set("websocket_receive")
        # Bind each connection to a provider; never forward a later external turn to native.
        ws = web.WebSocketResponse(max_msg_size=MAX_BODY, compress=False)
        if not ws.can_prepare(request).ok:
            raise RouterError("websocket_upgrade_required")
        await ws.prepare(request)
        metadata_token = WS_METADATA.set({})
        try:
            first = await asyncio.wait_for(ws.receive(), 30)
            if first.type != aiohttp.WSMsgType.TEXT:
                raise RouterError("text_response_create_required")
            payload = json.loads(first.data)
            if payload.get("type") != "response.create":
                raise RouterError("response_create_required")
            if not await self.is_native(payload.get("model"), request.headers):
                model = payload["model"]
                while True:
                    if payload.get("type") != "response.create" or payload.get("model", model) != model:
                        raise RouterError("provider_switch_requires_new_connection")
                    local = {k: v for k, v in payload.items() if k != "type"}
                    local.update(model=model, stream=True)
                    async for event in self.events(local):
                        await ws.send_str(encode(event).decode("utf-8"))
                    message = await ws.receive()
                    if message.type != aiohttp.WSMsgType.TEXT:
                        return ws
                    payload = json.loads(message.data)
            headers = clean_headers(request.headers)
            PHASE.set("websocket_connect")
            headers = {k: v for k, v in headers.items() if not k.lower().startswith("sec-websocket-")}
            async with self.session.ws_connect(URL(self.native_base + "/responses" + query, encoded=True),
                                               headers=headers, max_msg_size=MAX_BODY, compress=0) as upstream:
                # The local handshake already finished so model routing could read frame one.
                # Preserve known native handshake metadata via Codex's metadata event instead.
                metadata = WS_METADATA.get()
                if metadata:
                    for kind, names in (("response.metadata", {"x-codex-turn-state", "openai-model"}),
                                        ("codex.response.metadata", WS_METADATA_HEADERS - {"x-codex-turn-state", "openai-model"})):
                        values = {name: value for name, value in metadata.items() if name in names}
                        if values:
                            await ws.send_str(encode({"type": kind, "headers": values}).decode("utf-8"))
                    metadata.clear()
                await upstream.send_str(first.data)
                PHASE.set("websocket_stream")

                async def pump(source, target, validate=False):
                    async for message in source:
                        if message.type == aiohttp.WSMsgType.TEXT:
                            if validate:
                                item = json.loads(message.data)
                                if item.get("model") is not None and not await self.is_native(item["model"], request.headers):
                                    raise RouterError("provider_switch_requires_new_connection")
                            await target.send_str(message.data)
                        elif message.type == aiohttp.WSMsgType.BINARY:
                            if validate:
                                raise RouterError("binary_client_frames_not_supported")
                            await target.send_bytes(message.data)

                tasks = [asyncio.create_task(pump(ws, upstream, True)), asyncio.create_task(pump(upstream, ws))]
                try:
                    done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        task.result()
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as exc:
            self.record_failure(exc)
            await ws.close(code=1011, message=b"router_websocket_stopped_no_retry")
        finally:
            WS_METADATA.reset(metadata_token)
            await ws.close()
        return ws
