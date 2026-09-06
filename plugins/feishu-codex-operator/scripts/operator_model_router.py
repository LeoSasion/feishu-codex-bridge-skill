"""Opt-in Python Responses gateway; configuration changes require activate/deactivate."""

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import Request, ProxyHandler, build_opener
from urllib.error import URLError

from operator_core import model_router_config as settings
from operator_core.model_registry import RouterError


def service_identity(state):
    return hashlib.sha256((str(Path(__file__).resolve()) + "\n" + str(state.resolve())).encode()).hexdigest()


def control(state, port, *, stop=False):
    request = Request(settings.url(state, port) + "/lifecycle",
                      data=b"" if stop else None, method="POST" if stop else "GET")
    with build_opener(ProxyHandler({})).open(request, timeout=2) as response:
        value = json.loads(response.read(4096))
    if value.get("service") != service_identity(state):
        raise RouterError("router_service_identity_mismatch")
    return value


def start(state, port):
    # Fail before spawning if state or optional dependencies are unavailable.
    import aiohttp  # noqa: F401
    settings.url(state, port)
    from operator_core.model_registry import ModelRegistry
    ModelRegistry.load(state / "registry.json")
    try:
        existing = control(state, port)
        if existing.get("status") != "ready":
            raise RouterError("router_not_ready")
        return existing
    except OSError:
        pass
    flags = (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == "nt" else 0
    child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "serve",
        "--state-dir", str(state.resolve()), "--port", str(port)], stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags,
        start_new_session=os.name != "nt", cwd=str(Path(__file__).resolve().parent))
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and child.poll() is None:
        try:
            ready = control(state, port)
            if ready.get("status") != "ready":
                raise RouterError("router_not_ready")
            return ready
        except OSError:
            time.sleep(0.1)  # Health polling only; never replays a model request.
    if child.poll() is None:
        child.terminate()  # Exact child handle, never a PID read from disk.
        child.wait(timeout=5)
    raise RouterError("router_start_failed")


async def serve(state, port):
    from aiohttp import web
    from operator_core.model_registry import ModelRegistry
    from operator_core.model_router import ModelRouter
    token = (state / "token").read_text(encoding="ascii").strip()
    router = ModelRouter(ModelRegistry.load(state / "registry.json"), token)
    shutdown = asyncio.Event()
    active = 0

    @web.middleware
    async def lifecycle(request, handler):
        nonlocal active
        if request.path == router.prefix + "/lifecycle":
            if request.headers.get("Origin") or request.headers.get("Sec-Fetch-Site"):
                return web.json_response({"error": "browser_requests_not_supported"}, status=403)
            if request.method not in {"GET", "POST"}:
                return web.json_response({"error": "method_not_allowed"}, status=405)
            if request.method == "POST":
                if (state / "codex-entry.json").exists() or active:
                    return web.json_response({"error": "deactivate_and_finish_requests_before_stop"}, status=409)
                shutdown.set()
            return web.json_response({"service": service_identity(state), "pid": os.getpid(),
                "status": "stopping" if shutdown.is_set() else "ready",
                "diagnostics": {"failure_count": router.failure_count,
                                "last_failure": router.last_failure}})
        if shutdown.is_set():
            return web.json_response({"error": "router_stopping_no_retry"}, status=503)
        active += 1
        try:
            return await handler(request)
        finally:
            active -= 1

    app = router.app()
    app.middlewares.insert(0, lifecycle)
    runner = web.AppRunner(app, access_log=None, shutdown_timeout=5)
    await runner.setup()
    try:
        await web.TCPSite(runner, "127.0.0.1", port).start()
        await shutdown.wait()
    finally:
        await runner.cleanup()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["init", "serve", "start", "stop", "status", "activate", "deactivate",
                                          "lmstudio-models", "lmstudio-register"])
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--codex-config", type=Path)
    parser.add_argument("--port", type=int, default=4317)
    parser.add_argument("--api-base", default="http://127.0.0.1:1234/v1")
    parser.add_argument("--api-key-env", default="")
    parser.add_argument("--model")
    parser.add_argument("--slug")
    parser.add_argument("--context-window", type=int)
    parser.add_argument("--reasoning-effort", action="append")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be in 1024..65535")
    if args.action == "lmstudio-models":
        print(json.dumps({"models": settings.lmstudio_models(args.api_base, args.api_key_env),
                          "inference_verified": False}, ensure_ascii=True))
        return
    if args.action == "lmstudio-register":
        if not args.model or not args.slug or args.context_window is None or not args.reasoning_effort:
            parser.error("model, slug, context-window and reasoning-effort must be explicit")
        try:
            control(args.state_dir, args.port)
        except URLError as error:
            if not isinstance(error.reason, ConnectionRefusedError):
                raise
        else:
            raise RouterError("stop_router_before_registration")
        settings.register_lmstudio(args.state_dir, model=args.model, slug=args.slug,
            api_base=args.api_base, key_env=args.api_key_env,
            context_window=args.context_window, efforts=args.reasoning_effort)
        print("LM Studio model registered. Start router separately; inference remains unverified.")
        return
    if args.action == "init":
        settings.initialize(args.state_dir)
        print("Router state initialized; existing model registrations retained.")
        return
    if args.action == "status":
        print(json.dumps(control(args.state_dir, args.port)))
        return
    if args.action == "start":
        print(json.dumps(start(args.state_dir, args.port)))
        return
    if args.action == "stop":
        print(json.dumps(control(args.state_dir, args.port, stop=True)))
        return
    if args.action in {"activate", "deactivate"}:
        if args.codex_config is None:
            parser.error("--codex-config must identify the exact user-level config.toml")
        if args.action == "activate":
            settings.activate(args.state_dir, args.port, args.codex_config)
        else:
            settings.deactivate(args.state_dir, args.codex_config)
        print("Codex entry updated. Restart Codex to load the change.")
        return
    asyncio.run(serve(args.state_dir, args.port))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Do not print credentials, capability paths, registry contents or provider errors.
        raise SystemExit("Router action failed; check configuration and service availability.")
