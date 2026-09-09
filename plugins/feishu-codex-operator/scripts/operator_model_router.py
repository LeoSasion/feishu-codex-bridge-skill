"""Opt-in Python Responses gateway; configuration changes require activate/deactivate."""

import argparse
import asyncio
from contextlib import contextmanager
import hashlib
import json
import os
import re
import socket
from pathlib import Path
import subprocess
import sys
import time
import tomllib
from urllib.request import Request, ProxyHandler, build_opener
from urllib.error import URLError

from operator_core import model_router_config as settings
from operator_core.model_registry import RouterError


def service_identity(state):
    return hashlib.sha256((str(Path(__file__).resolve()) + "\n" + str(state.resolve())).encode()).hexdigest()


@contextmanager
def reserve_inactive_port(port):
    """Prove/reserve the configured listener's absence while editing registration.

    Windows can time out, rather than refuse, a connection to an unused port.
    Never interpret that timeout as absence or contact an unrelated listener.
    """
    with socket.socket() as listener:
        if os.name == "nt":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            listener.bind(("127.0.0.1", port))
        except OSError as exc:
            raise RouterError("router_port_in_use_stop_before_registration") from exc
        yield


def control(state, port, *, stop=False):
    request = Request(settings.url(state, port) + "/lifecycle",
                      data=b"" if stop else None, method="POST" if stop else "GET")
    with build_opener(ProxyHandler({})).open(request, timeout=2) as response:
        value = json.loads(response.read(4096))
    if value.get("service") != service_identity(state):
        raise RouterError("router_service_identity_mismatch")
    return value


def reload_registry(state, port, expected_sha256):
    """One explicit reload request to the exact running router, without retries."""
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", expected_sha256):
        raise RouterError("registry_reload_expected_digest_required")
    before = control(state, port)
    if before.get("status") != "ready":
        raise RouterError("router_not_ready")
    if "registry_sha256" not in before.get("diagnostics", {}):
        raise RouterError("registry_reload_requires_upgraded_router")
    request = Request(settings.url(state, port) + "/lifecycle/registry",
        data=json.dumps({"expected_registry_sha256": expected_sha256}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with build_opener(ProxyHandler({}), settings._NoRedirect()).open(request, timeout=10) as response:
        raw = response.read(4097)
    if len(raw) > 4096:
        raise RouterError("registry_reload_response_invalid_no_retry")
    value = settings.loads(raw)
    if (not isinstance(value, dict) or value.get("service") != before["service"]
            or value.get("pid") != before["pid"] or value.get("registry_sha256") != expected_sha256):
        raise RouterError("registry_reload_identity_changed_no_retry")
    return value


async def registry_reload_response(router, state, request):
    """Bounded local control lane; existing requests and sockets are untouched."""
    from aiohttp import web
    if request.headers.get("Origin") or request.headers.get("Sec-Fetch-Site"):
        return web.json_response({"error": "browser_requests_not_supported"}, status=403)
    if request.method != "POST":
        return web.json_response({"error": "method_not_allowed"}, status=405)
    if (request.content_length is None or not 0 < request.content_length <= 1024
            or request.headers.get("Content-Encoding", "identity") != "identity"
            or request.content_type != "application/json"):
        return web.json_response({"error": "registry_reload_invalid_request"}, status=400)
    try:
        value = settings.loads(await request.read())
        if not isinstance(value, dict) or set(value) != {"expected_registry_sha256"}:
            raise RouterError("registry_reload_invalid_request")
        result = await router.reload_registry(state / "registry.json", value["expected_registry_sha256"])
    except (ValueError, TypeError) as exc:
        allowed = {"registry_reload_expected_digest_required", "registry_reload_digest_mismatch",
                   "registry_reload_invalid_registry", "registry_reload_busy_no_retry",
                   "registry_reload_throttled_no_retry"}
        reason = str(exc) if isinstance(exc, RouterError) and str(exc) in allowed else "registry_reload_invalid_request"
        status = 409 if reason in {"registry_reload_digest_mismatch", "registry_reload_busy_no_retry",
                                  "registry_reload_throttled_no_retry"} else 400
        return web.json_response({"error": reason}, status=status)
    return web.json_response({"service": service_identity(state), "pid": os.getpid(), **result})


def readiness(state, port, config=None):
    """Local-only inspection. Never start, activate, change keys or call a model."""
    from operator_core.model_registry import ModelRegistry
    from operator_core.responses_profiles import preflight
    registry = ModelRegistry.load(state / "registry.json")
    rows = settings.read_registration(state / "registry.json")["models"]
    for row in rows:
        if row.get("responses") is not None:
            preflight(row)
    result = {"registry_valid": True, "registered_models": len(registry.routes),
              "adapted_models": sum(r.responses is not None for r in registry.routes.values()),
              "configured_keys_available": all(not route.api_key_env or bool(os.environ.get(route.api_key_env))
                                               for route in registry.routes.values()),
              "service": "unavailable", "entry": "not_inspected", "upstream_requests": 0,
              "desktop_picker": "unknown", "desktop_default_persistence": "unknown",
              "native_auxiliary_live": "unknown", "windows_login_startup": "not_installed",
              "ready_for_global_activation": False}
    try:
        service = control(state, port)
        result["service"] = service.get("status", "unknown")
    except (OSError, RouterError, ValueError):
        pass
    if config is not None:
        raw = config.read_bytes() if config.exists() else b""
        parsed = tomllib.loads(raw.decode("utf-8-sig"))
        if (state / "codex-entry.json").exists():
            journal = settings.read_registration(state / "codex-entry.json")
            exact = (journal == {"config": str(config.resolve()), "block":
                     settings.BEGIN + "openai_base_url = " + json.dumps(settings.url(state, port)) + "\n" + settings.END})
            result["entry"] = "owned_active" if exact and raw.startswith(journal["block"].encode()) else "ownership_conflict"
        elif (parsed.get("model_provider", "openai") != "openai" or "openai_base_url" in parsed
              or "model_catalog_json" in parsed or parsed.get("profile")):
            result["entry"] = "custom_configuration_requires_review"
        else:
            result["entry"] = "native_deactivated"
    return result


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


def restart(state, port):
    """Explicit restart only while deactivated. Never replay accepted inference."""
    if (state / "codex-entry.json").exists():
        raise RouterError("deactivate_before_router_restart")
    try:
        control(state, port, stop=True)
    except URLError as exc:
        if not isinstance(exc.reason, ConnectionRefusedError):
            raise
    else:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            # The acknowledged stop owns no requests. Prove that its listener
            # released this exact loopback port; a connect timeout is not proof.
            with socket.socket() as listener:
                if os.name == "nt":
                    listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                else:
                    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    listener.bind(("127.0.0.1", port))
                except OSError:
                    pass
                else:
                    break
            time.sleep(0.05)
        else:
            raise RouterError("router_stop_not_confirmed")
    return start(state, port)


async def serve(state, port):
    from aiohttp import web
    from operator_core.model_registry import ModelRegistry
    from operator_core.model_router import ModelRouter
    token = (state / "token").read_text(encoding="ascii").strip()
    registry, digest = ModelRegistry.load_snapshot(state / "registry.json")
    router = ModelRouter(registry, token, registry_sha256=digest)
    shutdown = asyncio.Event()
    active = 0

    @web.middleware
    async def lifecycle(request, handler):
        nonlocal active
        if request.path == router.prefix + "/lifecycle/registry":
            if shutdown.is_set():
                return web.json_response({"error": "router_stopping_no_retry"}, status=503)
            return await registry_reload_response(router, state, request)
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
                                "last_failure": router.last_failure,
                                "timing": router.metrics.snapshot(),
                                "registry_sha256": router.registry_sha256,
                                "adapted_models": sum(route.responses is not None
                                                      for route in router.registry.routes.values())}})
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
    parser.add_argument("action", choices=["init", "serve", "start", "restart", "stop", "status", "activate", "deactivate",
                                          "lmstudio-models", "lmstudio-register", "register-model", "preflight",
                                          "profile-build", "readiness", "lmstudio-sync", "reload-registry",
                                          "verification-init", "verification-status", "verification-label"])
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--codex-config", type=Path)
    parser.add_argument("--port", type=int, default=4317)
    parser.add_argument("--api-base", default="http://127.0.0.1:1234/v1")
    parser.add_argument("--api-key-env", default="")
    parser.add_argument("--model")
    parser.add_argument("--slug")
    parser.add_argument("--context-window", type=int)
    parser.add_argument("--reasoning-effort", action="append")
    parser.add_argument("--responses-capabilities", type=Path)
    parser.add_argument("--registration", type=Path)
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--request", type=Path)
    parser.add_argument("--cli-version")
    parser.add_argument("--desktop-version")
    parser.add_argument("--model-sha256")
    parser.add_argument("--desktop-evidence", type=Path)
    parser.add_argument("--format", choices=("json", "text"), default="json")
    parser.add_argument("--expected-registry-sha256")
    parser.add_argument("--profile-id")
    parser.add_argument("--evidence", action="append", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--discovery-policy", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.format != "json" and args.action != "verification-status":
        parser.error("text format is only supported by verification-status")
    if args.action not in {"preflight", "profile-build", "lmstudio-models",
                           "verification-init", "verification-status"} and args.state_dir is None:
        parser.error("state-dir must be explicit")
    if not 1024 <= args.port <= 65535:
        parser.error("port must be in 1024..65535")
    if args.registration and args.profile and args.action != "verification-status":
        parser.error("choose registration or profile, not both")
    if args.apply and args.action not in {"lmstudio-sync", "verification-label"}:
        parser.error("apply is only supported by lmstudio-sync or verification-label")
    if args.action == "verification-label":
        if not (args.slug and args.desktop_evidence and args.cli_version and args.desktop_version and args.model_sha256):
            parser.error("slug, desktop-evidence, cli-version, desktop-version and model-sha256 must be explicit")
        if args.registration:
            parser.error("verification-label reads the exact current registration from state-dir")
        if args.apply and not args.expected_registry_sha256:
            parser.error("apply requires expected-registry-sha256 from a reviewed preview")
        from operator_core.responses_labels import label_update
        versions = {"cli_version": args.cli_version, "desktop_version": args.desktop_version,
                    "model_sha256": args.model_sha256}
        if args.apply:
            with reserve_inactive_port(args.port):
                result = label_update(args.state_dir, args.slug, args.desktop_evidence, args.profile,
                    versions, apply=True, expected_sha256=args.expected_registry_sha256)
        else:
            result = label_update(args.state_dir, args.slug, args.desktop_evidence, args.profile, versions)
        print(json.dumps(result, ensure_ascii=True))
        return
    if args.action in {"verification-init", "verification-status"}:
        if not ((args.registration or args.profile) and args.cli_version
                and args.desktop_version and args.model_sha256):
            parser.error("registration or profile, cli-version, desktop-version and model-sha256 must be explicit")
        if args.action == "verification-status" and not args.registration:
            parser.error("verification-status requires the current registration; a historical profile alone is insufficient")
        from operator_core.responses_verification import empty_ledger, inspect_ledger, format_verification_report
        profile = settings.read_registration(args.profile) if args.profile else None
        if profile is not None:
            from operator_core.responses_profiles import inspect_profile
            inspect_profile(profile, cli_version=args.cli_version)
        row = settings.read_registration(args.registration) if args.registration else profile["registration"]
        versions = {"cli_version": args.cli_version, "desktop_version": args.desktop_version,
                    "model_sha256": args.model_sha256}
        if args.action == "verification-init":
            if not args.output:
                parser.error("output must be explicit")
            value = empty_ledger(row, **versions)
            with args.output.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(value, ensure_ascii=True, indent=2) + "\n")
            print("Empty evidence ledger created; no test, catalog or permission change performed.")
        else:
            if not args.desktop_evidence:
                parser.error("desktop-evidence must be explicit")
            result = inspect_ledger(args.desktop_evidence, row, profile=profile, **versions)
            print(format_verification_report(result) if args.format == "text" else json.dumps(result))
        return
    if args.action == "lmstudio-sync":
        if not args.discovery_policy:
            parser.error("discovery-policy must be explicit")
        from operator_core.lmstudio_discovery import scan, synchronize
        policy = settings.read_registration(args.discovery_policy)
        if args.apply:
            with reserve_inactive_port(args.port):
                result = synchronize(args.state_dir, policy)
        else:
            result = scan(args.state_dir, policy)
            result["added"] = 0
        print(json.dumps({"applied": args.apply, **result}, ensure_ascii=True))
        return
    if args.action == "profile-build":
        if not (args.profile_id and args.registration and args.evidence and args.output):
            parser.error("profile-id, registration, evidence and output must be explicit")
        from operator_core.responses_profiles import profile_from_reports
        value = profile_from_reports(args.profile_id, settings.read_registration(args.registration),
                                     [settings.read_registration(path) for path in args.evidence])
        # Exclusive output: no overwrite of an older verification profile.
        with args.output.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(value, ensure_ascii=True, indent=2) + "\n")
        print("Profile created from matching evaluation records; Desktop acceptance is separate.")
        return
    if args.action == "readiness":
        print(json.dumps(readiness(args.state_dir, args.port, args.codex_config)))
        return
    registration = None
    if args.action in {"preflight", "register-model"}:
        from operator_core.responses_profiles import inspect_profile, preflight
        if not (args.registration or args.profile):
            parser.error("registration or profile must be explicit")
        verification = None
        if args.profile:
            profile = settings.read_registration(args.profile)
            verification = inspect_profile(profile, cli_version=args.cli_version)
            registration = profile["registration"]
        else:
            registration = settings.read_registration(args.registration)
        # Preserve generic legacy/null registration; explicit adaptation adds preflight.
        checks = (preflight(registration, request=settings.read_registration(args.request) if args.request else None)
                  if isinstance(registration, dict) and registration.get("responses") is not None else
                  {"adaptation_enabled": False, "upstream_requests": 0})
        if args.action == "preflight":
            if not registration.get("responses"):
                from operator_core.model_registry import ModelRegistry
                catalog = json.loads(Path(__file__).with_name("operator_core").joinpath(
                    "beeper_model_catalog.json").read_text(encoding="utf-8"))
                ModelRegistry({"version": 2 if "responses" in registration else 1, "models": [registration]}, catalog)
            print(json.dumps({"preflight": checks, "verification": verification}))
            return
    if args.action == "lmstudio-models":
        print(json.dumps({"models": settings.lmstudio_models(args.api_base, args.api_key_env),
                          "inference_verified": False}, ensure_ascii=True))
        return
    if args.action in {"lmstudio-register", "register-model"}:
        if args.action == "lmstudio-register" and (
                not args.model or not args.slug or args.context_window is None or not args.reasoning_effort):
            parser.error("model, slug, context-window and reasoning-effort must be explicit")
        with reserve_inactive_port(args.port):
            if args.action == "register-model":
                settings.register_route(args.state_dir, registration)
            else:
                responses = (settings.read_registration(args.responses_capabilities)
                             if args.responses_capabilities else None)
                settings.register_lmstudio(args.state_dir, model=args.model, slug=args.slug,
                    api_base=args.api_base, key_env=args.api_key_env,
                    context_window=args.context_window, efforts=args.reasoning_effort, responses=responses)
        print("Model registered. Start router separately; configured capabilities remain unverified.")
        return
    if args.action == "init":
        settings.initialize(args.state_dir)
        print("Router state initialized; existing model registrations retained.")
        return
    if args.action == "status":
        print(json.dumps(control(args.state_dir, args.port)))
        return
    if args.action == "reload-registry":
        if not args.expected_registry_sha256:
            parser.error("reload-registry requires expected-registry-sha256")
        print(json.dumps(reload_registry(args.state_dir, args.port, args.expected_registry_sha256)))
        return
    if args.action == "start":
        print(json.dumps(start(args.state_dir, args.port)))
        return
    if args.action == "restart":
        print(json.dumps(restart(args.state_dir, args.port)))
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
    except RouterError as exc:
        import re
        reason = str(exc)
        code = reason if re.fullmatch(r"[a-z][a-z0-9_]{1,100}", reason) else "invalid_configuration"
        raise SystemExit("Router action failed: " + code)
    except Exception:
        # Do not print credentials, capability paths, registry contents or provider errors.
        raise SystemExit("Router action failed; check configuration and service availability.")
