"""Loopback Responses router. No task control, history store, retries or fallback."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import zlib
from time import perf_counter
from contextvars import ContextVar

import aiohttp
from aiohttp import web
from yarl import URL

from .beeper_provider import BeeperResponsesEngine
from .model_registry import ModelRegistry, RouterError
from .responses_capabilities import UpstreamProtocolError, protocol_reason, protocol_response_state
from .responses_tool_adapter import prepare_request, restore_response, loads
from .responses_events import restore_events
from .responses_metrics import CURRENT_METRICS, ResponsesMetrics

NATIVE_BASE = "https://chatgpt.com/backend-api/codex"
MAX_BODY = 16 * 1024 * 1024
MAX_NATIVE_HTTP_BODY = 64 * 1024 * 1024
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


class RequestSizeError(RouterError):
    def __init__(self, limit, scope):
        super().__init__("router_request_too_large")
        self.limit = limit
        self.scope = scope


def decode_body(body: bytes, encoding: str, *, limit=None):
    """Inspect a bounded decoded copy for routing; keep native wire bytes intact."""
    limit = MAX_BODY if limit is None else limit
    encoding = encoding.strip().lower()
    if encoding == "zstd":
        try:
            from compression import zstd
        except ImportError:
            from backports import zstd
        decoder = zstd.ZstdDecompressor()
        try:
            data = decoder.decompress(body, max_length=limit + 1)
        except zstd.ZstdError as exc:
            raise RouterError("invalid_compressed_request") from exc
    elif encoding in {"gzip", "deflate"}:
        decoder = zlib.decompressobj(31 if encoding == "gzip" else 15)
        try:
            data = decoder.decompress(body, limit + 1)
        except zlib.error as exc:
            raise RouterError("invalid_compressed_request") from exc
    elif encoding == "identity":
        if len(body) > limit:
            raise RequestSizeError(limit, "decoded_request")
        return body
    else:
        raise RouterError("unsupported_content_encoding")
    if len(data) > limit:
        raise RequestSizeError(limit, "decoded_request")
    if not decoder.eof or decoder.unused_data:
        raise RouterError("incomplete_compressed_request")
    return data


def decode_request(body: bytes, encoding: str, *, strict=False):
    data = decode_body(body, encoding)
    return loads(data) if strict else json.loads(data)


def request_size_response(limit, scope):
    return web.json_response({"error": {"type": "invalid_request_error",
        "code": "router_request_too_large", "limit_bytes": limit, "scope": scope,
        "message": "Local router request size limit exceeded; request was not forwarded or retried."}}, status=413)


class ModelRouter:
    def __init__(self, registry: ModelRegistry, token: str, *, native_base: str = NATIVE_BASE,
                 registry_sha256: str | None = None) -> None:
        if not re.fullmatch(r"[a-f0-9]{64}", token):
            raise RouterError("router_token_requires_64_hex_characters")
        self._registry = registry
        self.registry_sha256 = registry_sha256
        self._registry_snapshot = ContextVar("router_registry_snapshot", default=None)
        self._reload_lock = asyncio.Lock()
        self._reload_not_before = 0.0
        self.prefix = "/" + token + "/v1"
        self.native_base = native_base.rstrip("/")
        self.beeper = BeeperResponsesEngine()
        self.native_models: dict[str, set[str]] = {}
        self.session: aiohttp.ClientSession | None = None
        self.slots = asyncio.Semaphore(16)
        self.failure_count = 0
        self.last_failure = None
        self.metrics = ResponsesMetrics()

    @property
    def registry(self):
        # A long-lived WebSocket retains its original provider contract, including
        # later turns on that connection. Only newly admitted requests see updates.
        snapshot = self._registry_snapshot.get()
        return self._registry if snapshot is None else snapshot

    async def reload_registry(self, path, expected_sha256):
        """Explicit memory-only publication; no polling, network, writes or retry."""
        if not isinstance(expected_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", expected_sha256):
            raise RouterError("registry_reload_expected_digest_required")
        if self._reload_lock.locked():
            raise RouterError("registry_reload_busy_no_retry")
        async with self._reload_lock:
            changed = expected_sha256 != self.registry_sha256
            if changed:
                if perf_counter() < self._reload_not_before:
                    raise RouterError("registry_reload_throttled_no_retry")
                # Failed reads also consume the interval. Reading/validation is
                # off the event loop; cancellation never publishes its result.
                self._reload_not_before = perf_counter() + 30.0
                candidate, digest = await asyncio.to_thread(ModelRegistry.load_snapshot, path, expected_sha256)
                self._registry, self.registry_sha256 = candidate, digest
            return {"changed": changed, "registry_sha256": self.registry_sha256,
                    "registered_models": len(self._registry.routes),
                    "adapted_models": sum(r.responses is not None for r in self._registry.routes.values()),
                    "inference_requests": 0, "desktop_refreshed": False}

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
        if isinstance(exc, UpstreamProtocolError):
            self.last_failure["protocol_reason"] = protocol_reason(exc)
            state = protocol_response_state(exc)
            if state is not None:
                self.last_failure["response_state"] = state
        return category

    def app(self) -> web.Application:
        app = web.Application(client_max_size=MAX_NATIVE_HTTP_BODY + 1,
                              handler_args={"auto_decompress": False, "handler_cancellation": True})
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
        # Opaque downstream clients may not decode compressed search/Responses
        # streams. Never advertise encodings they did not request. Explicit
        # client headers and the adapter/catalog's identity choice are retained.
        async with aiohttp.ClientSession(auto_decompress=False, skip_auto_headers={"Accept-Encoding"},
                trust_env=False, trace_configs=[trace],
                max_line_size=MAX_UPSTREAM_HEADER, max_field_size=MAX_UPSTREAM_HEADER,
                timeout=aiohttp.ClientTimeout(total=None, connect=15, sock_read=300)) as session:
            self.session = session
            yield

    async def admit(self, request):
        if self.slots.locked():
            return web.json_response({"error": "router_busy_no_retry"}, status=503)
        async with self.slots:
            begin = perf_counter()
            token = CURRENT_METRICS.set(self.metrics)
            registry_token = self._registry_snapshot.set(self._registry)
            self.metrics.active += 1
            outcome = "failed"
            try:
                result = await self.handle(request)
                outcome = "completed" if result.status < 400 and not request.get("router_failed") else "failed"
                return result
            except asyncio.CancelledError:
                outcome = "cancelled"
                raise
            finally:
                self.metrics.active -= 1
                self.metrics.outcomes[outcome] += 1
                self.metrics.observe("request_total", perf_counter() - begin)
                CURRENT_METRICS.reset(token)
                self._registry_snapshot.reset(registry_token)

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
                body = await request.read()
                if len(body) > MAX_BODY:
                    raise RequestSizeError(MAX_BODY, "native_auxiliary_request")
                return await self.proxy(request, URL(self.native_base + "/" + endpoint + query, encoded=True),
                                        body, clean_headers(request.headers))
            if endpoint not in {"responses", "responses/compact"}:
                return web.json_response({"error": "endpoint_not_supported"}, status=404)
            if request.method == "GET" and endpoint == "responses":
                return await self.websocket(request, query)
            if request.method != "POST":
                return web.json_response({"error": "method_not_allowed"}, status=405)
            body = await request.read()
            PHASE.set("decode")
            decoded = decode_body(body, request.headers.get("Content-Encoding", "identity"),
                                  limit=MAX_NATIVE_HTTP_BODY)
            payload = json.loads(decoded)
            if not isinstance(payload, dict):
                raise RouterError("invalid_request")
            if await self.is_native(payload.get("model"), request.headers):
                del decoded, payload
                return await self.proxy(request, URL(self.native_base + "/" + endpoint + query, encoded=True),
                                        body, clean_headers(request.headers))
            if len(decoded) > MAX_BODY:
                raise RequestSizeError(MAX_BODY, "external_request")
            if endpoint != "responses":
                raise RouterError("external_compaction_not_supported")
            route = self.registry.routes.get(payload["model"])
            if route:
                reject_opaque_context(payload)
            if route:
                context = None
                if route.responses is not None:
                    # Reject duplicate keys even though the routing inspection used a
                    # decoded JSON copy. Native/v1 wire behavior remains unchanged.
                    payload = loads(decoded)
                    with self.metrics.measure("request_adaptation"):
                        payload, context = prepare_request(payload, route.responses, route.reasoning_efforts)
                key = route.key()
                headers = {"Content-Type": "application/json", "Accept-Encoding": "identity"}
                if key:
                    headers["Authorization"] = "Bearer " + key
                external_body = encode({**payload, "model": route.model})
                if len(external_body) > MAX_BODY:
                    raise RouterError("adapted_request_too_large")
                return await self.proxy(request, route.api_base.rstrip("/") + "/responses",
                                        external_body, headers,
                                        context=context, stream=bool(payload.get("stream")))
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
        except RequestSizeError as exc:
            request["router_failed"] = True
            self.record_failure(exc)
            return request_size_response(exc.limit, exc.scope)
        except web.HTTPRequestEntityTooLarge:
            request["router_failed"] = True
            self.record_failure(RouterError("router_request_too_large"))
            return request_size_response(MAX_NATIVE_HTTP_BODY, "http_request")
        except UpstreamProtocolError as exc:
            request["router_failed"] = True
            self.record_failure(exc)
            if request.get(RESPONSE_STARTED):
                if request.transport:
                    request.transport.close()
                return web.StreamResponse()
            return web.json_response({"error": {"type": "server_error",
                "code": "router_protocol_no_retry",
                "message": "Upstream Responses protocol failed; request was not retried."}}, status=502)
        except (RouterError, ValueError, KeyError, TypeError) as exc:
            request["router_failed"] = True
            if request.get(RESPONSE_STARTED):
                if request.transport:
                    request.transport.close()
                return web.StreamResponse()
            message = str(exc) if isinstance(exc, RouterError) else "invalid_request"
            error = {"type": "invalid_request_error", "message": message}
            field = getattr(exc, "protocol_field", None)
            if field in {"protocol.string", "protocol.identifier", "tools.description",
                         "custom_tool.input", "content.text", "function_call.arguments", "input.tool_call"}:
                error["param"] = field  # Fixed schema label, never payload text or tool names.
            code = getattr(exc, "protocol_code", None)
            if code in {"history_tool_name_invalid", "history_tool_definitions_empty",
                        "history_tool_namespace_mismatch", "history_tool_kind_mismatch",
                        "history_registered_custom_not_advertised", "history_unregistered_tool_call"}:
                error["code"] = code
            return web.json_response({"error": error}, status=400)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            request["router_failed"] = True
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
            request["router_failed"] = True
            self.record_failure(exc)
            # Never expose request fragments, endpoint keys or provider details.
            if request.get(RESPONSE_STARTED):
                if request.transport:
                    request.transport.close()
                return web.StreamResponse()
            return web.json_response({"error": {"type": "server_error",
                "code": "router_internal_no_retry",
                "message": "Router internal failure; request was not retried."}}, status=502)

    async def proxy(self, request, url, body, headers, *, context=None, stream=False):
        PHASE.set("http_connect")
        begin = perf_counter()
        async with self.session.post(url, data=body, headers=headers, allow_redirects=False) as upstream:
            self.metrics.observe("upstream_headers", perf_counter() - begin)
            if upstream.status >= 400:
                self.record_failure(status=upstream.status)
            if 300 <= upstream.status < 400:
                raise RouterError("upstream_redirect_refused")
            if context is not None and upstream.status < 400:
                return await self.adapted_response(request, upstream, context, stream, begin)
            output = web.StreamResponse(status=upstream.status, headers=clean_headers(upstream.headers))
            await output.prepare(request)
            request[RESPONSE_STARTED] = True
            PHASE.set("http_stream")
            async for chunk in self.metrics.chunks(upstream.content.iter_any(), begin):
                await output.write(chunk)
            await output.write_eof()
            return output

    async def adapted_response(self, request, upstream, context, stream, begin):
        PHASE.set("http_tool_adaptation")
        if (upstream.status != 200
                or upstream.headers.get("Content-Encoding", "identity").lower() != "identity"):
            raise UpstreamProtocolError("unsupported_adapted_response_encoding_or_status")
        headers = {k: v for k, v in clean_headers(upstream.headers).items()
                   if k.lower() not in {"content-type", "content-encoding", "etag",
                                        "content-md5", "digest", "content-range"}}
        content_type = upstream.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if not stream:
            if content_type != "application/json":
                raise UpstreamProtocolError("external_json_response_required")
            data = bytearray()
            async for chunk in self.metrics.chunks(upstream.content.iter_chunked(65536), begin):
                data.extend(chunk)
                if len(data) > MAX_BODY:
                    raise UpstreamProtocolError("external_json_response_too_large")
            try:
                with self.metrics.measure("response_adaptation"):
                    restored = restore_response(loads(data), context)
            except RouterError as exc:
                raise UpstreamProtocolError("invalid_external_json_response") from exc
            return web.Response(body=encode(restored), headers=headers, content_type="application/json")
        if content_type != "text/event-stream":
            raise UpstreamProtocolError("external_sse_response_required")
        iterator = restore_events(self.metrics.chunks(upstream.content.iter_chunked(65536), begin), context)
        try:
            first = await anext(iterator)
            output = web.StreamResponse(headers={**headers, "Content-Type": "text/event-stream",
                                                  "Cache-Control": "no-cache"})
            await output.prepare(request)
            request[RESPONSE_STARTED] = True
            await output.write(b"data: " + encode(first) + b"\n\n")
            async for event in iterator:
                await output.write(b"data: " + encode(event) + b"\n\n")
            await output.write(b"data: [DONE]\n\n")
            await output.write_eof()
            return output
        finally:
            await iterator.aclose()

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
        context = None
        if route.responses is not None:
            with self.metrics.measure("request_adaptation"):
                payload, context = prepare_request(payload, route.responses, route.reasoning_efforts)
        headers = {"Content-Type": "application/json", "Accept-Encoding": "identity"}
        key = route.key()
        if key:
            headers["Authorization"] = "Bearer " + key
        external_body = encode({**payload, "model": route.model, "stream": True})
        if len(external_body) > MAX_BODY:
            raise RouterError("adapted_request_too_large")
        begin = perf_counter()
        async with self.session.post(route.api_base.rstrip("/") + "/responses",
                data=external_body,
                headers=headers, allow_redirects=False) as response:
            self.metrics.observe("upstream_headers", perf_counter() - begin)
            if response.status != 200 or "text/event-stream" not in response.headers.get("Content-Type", ""):
                self.record_failure(status=response.status)
                raise RouterError("external_stream_unavailable")
            if context is not None:
                if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                    raise UpstreamProtocolError("unsupported_adapted_stream_encoding")
                iterator = restore_events(self.metrics.chunks(response.content.iter_chunked(65536), begin), context)
                try:
                    async for event in iterator:
                        yield event
                finally:
                    await iterator.aclose()
                return
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

    async def external_websocket_turn(self, ws, payload):
        """Notice disconnects while the upstream is silent; never retry a turn."""
        async def send():
            iterator = self.events(payload)
            try:
                async for event in iterator:
                    await ws.send_str(encode(event).decode("utf-8"))
            finally:
                await iterator.aclose()

        sender = asyncio.create_task(send())
        receiver = asyncio.create_task(ws.receive())
        try:
            done, _ = await asyncio.wait({sender, receiver}, return_when=asyncio.FIRST_COMPLETED)
            if sender in done:
                sender.result()
                return await receiver
            message = receiver.result()
            if message.type == aiohttp.WSMsgType.TEXT:
                raise RouterError("external_mid_stream_steering_not_supported")
            return message
        finally:
            for task in (sender, receiver):
                task.cancel()
            await asyncio.gather(sender, receiver, return_exceptions=True)

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
                route = self.registry.routes.get(model)
                if route is not None and route.responses is not None:
                    payload = loads(first.data)
                while True:
                    if payload.get("type") != "response.create" or payload.get("model", model) != model:
                        raise RouterError("provider_switch_requires_new_connection")
                    local = {k: v for k, v in payload.items() if k != "type"}
                    local.update(model=model, stream=True)
                    if route is not None and route.responses is not None:
                        message = await self.external_websocket_turn(ws, local)
                    else:
                        # Keep legacy external and deterministic Beeper sequencing.
                        async for event in self.events(local):
                            await ws.send_str(encode(event).decode("utf-8"))
                        message = await ws.receive()
                    if message.type != aiohttp.WSMsgType.TEXT:
                        return ws
                    payload = (loads(message.data) if route is not None and route.responses is not None
                               else json.loads(message.data))
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
            request["router_failed"] = True
            self.record_failure(exc)
            await ws.close(code=1011, message=b"router_websocket_stopped_no_retry")
        finally:
            WS_METADATA.reset(metadata_token)
            await ws.close()
        return ws
