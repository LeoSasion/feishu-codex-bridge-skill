"""Reversible, narrowly owned Codex entry-point configuration."""

import json
import hashlib
import os
from pathlib import Path
import secrets
import tempfile
import tomllib
from urllib.request import ProxyHandler, build_opener, Request, HTTPRedirectHandler
from urllib.parse import urlsplit
import re

from .model_registry import ModelRegistry, RouterError
from .responses_tool_adapter import loads

BEGIN = "# BEGIN FEISHU OPERATOR MODEL ROUTER\n"
END = "# END FEISHU OPERATOR MODEL ROUTER\n"


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RouterError("lmstudio_redirect_refused")


def lmstudio_json(api_base: str, key_env: str = "", *, native=False):
    """One bounded metadata GET; no inference, loading, download or proxy."""
    base = urlsplit(api_base)
    if (base.scheme not in {"http", "https"} or base.hostname not in {"127.0.0.1", "::1"}
            or base.username or base.password or base.query or base.fragment
            or base.path.rstrip("/") != "/v1" or "%" in base.netloc):
        raise RouterError("lmstudio_requires_literal_loopback_v1")
    if base.port is not None and not 1 <= base.port <= 65535:
        raise RouterError("invalid_lmstudio_port")
    headers = {"Accept": "application/json"}
    if key_env:
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,100}", key_env):
            raise RouterError("invalid_key_environment_name")
        key = os.environ.get(key_env, "")
        if not key.strip() or any(ord(c) < 32 for c in key):
            raise RouterError("configured_api_key_unavailable")
        headers["Authorization"] = "Bearer " + key
    target = (base._replace(path="/api/v1/models").geturl() if native
              else api_base.rstrip("/") + "/models")
    request = Request(target, headers=headers)
    with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=3) as response:
        raw = response.read(1048577)
    if len(raw) > 1048576:
        raise RouterError("lmstudio_catalog_too_large")
    return loads(raw)


def lmstudio_models(api_base: str, key_env: str = "") -> list[str]:
    value = lmstudio_json(api_base, key_env)
    rows = value.get("data") if isinstance(value, dict) else None
    if not isinstance(rows, list) or len(rows) > 1000:
        raise RouterError("invalid_lmstudio_catalog")
    result = []
    for row in rows:
        model = row.get("id") if isinstance(row, dict) else None
        if (not isinstance(model, str) or not model.strip() or len(model) > 512
                or any(ord(c) < 32 for c in model) or model in result):
            raise RouterError("invalid_lmstudio_model_id")
        result.append(model)
    return result


def register_lmstudio(state: Path, *, model: str, slug: str, api_base: str,
                      key_env: str, context_window: int, efforts: list[str], responses=None):
    """Append an explicit model, preserving existing routes. Gateway must be stopped."""
    if not slug.startswith("local/"):
        raise RouterError("lmstudio_requires_local_slug")
    if (state / "codex-entry.json").exists():
        raise RouterError("deactivate_before_registration")
    row = dict(slug=slug, display_name="LM Studio: " + model, model=model,
               api_base=api_base.rstrip("/"), api_key_env=key_env,
               context_window=context_window, reasoning_efforts=efforts)
    if responses is not None:
        row["responses"] = responses
    catalog = json.loads(Path(__file__).with_name("beeper_model_catalog.json").read_text(encoding="utf-8"))
    ModelRegistry({"version": 2 if "responses" in row else 1, "models": [row]}, catalog)
    if model not in lmstudio_models(api_base, key_env):
        raise RouterError("lmstudio_model_not_listed")
    register_route(state, row)


def read_registration(path: Path):
    """Bounded explicit config read; never log the contents or inspect keys."""
    with path.open("rb") as handle:
        data = handle.read(1048577)
    if len(data) > 1048576:
        raise RouterError("registration_file_too_large")
    return loads(data)


def register_route(state: Path, row: dict):
    """Append only. v2 upgrades existing rows with explicit null passthrough."""
    register_routes(state, [row])


def register_routes(state: Path, rows: list[dict], *, expected_sha256=None):
    """Validate and append an entire batch atomically; never change an existing row."""
    if (state / "codex-entry.json").exists():
        raise RouterError("deactivate_before_registration")
    if not isinstance(rows, list) or len(rows) > 100:
        raise RouterError("invalid_registration_batch")
    catalog = json.loads(Path(__file__).with_name("beeper_model_catalog.json").read_text(encoding="utf-8"))
    version = 2 if any(isinstance(row, dict) and "responses" in row for row in rows) else 1
    if version == 2:
        rows = [{**row, "responses": row.get("responses")} if isinstance(row, dict) else row for row in rows]
    ModelRegistry({"version": version, "models": rows}, catalog)
    lock = state / "registry-edit.lock"
    handle = lock.open("xb")
    try:
        target = state / "registry.json"
        if target.is_symlink():
            raise RouterError("registry_requires_regular_file")
        original = target.read_bytes()
        if len(original) > 1048576:
            raise RouterError("registration_file_too_large")
        if expected_sha256 is not None and hashlib.sha256(original).hexdigest() != expected_sha256:
            raise RouterError("registry_changed_since_discovery")
        value = loads(original)
        ModelRegistry(value, catalog)
        if version == 2 and value["version"] == 1:
            value = {"version": 2, "models": [{**item, "responses": None} for item in value["models"]]}
        added = 0
        for row in rows:
            if value["version"] == 2 and "responses" not in row:
                row = {**row, "responses": None}
            existing = next((r for r in value["models"] if r["slug"] == row["slug"]), None)
            if existing == row:
                continue
            if existing is not None:
                raise RouterError("existing_model_registration_conflict")
            value["models"].append(row)
            added += 1
        ModelRegistry(value, catalog)
        if added:
            if target.read_bytes() != original:
                raise RouterError("registry_changed_during_registration")
            atomic_write(target, (json.dumps(value, ensure_ascii=True, indent=2) + "\n").encode())
        return added
    finally:
        handle.close()
        lock.unlink()


def atomic_write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".router-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def initialize(state: Path):
    state.mkdir(parents=True, exist_ok=True)
    for name, data in (("registry.json", b'{"version":1,"models":[]}\n'),
                       ("token", secrets.token_hex(32).encode("ascii"))):
        path = state / name
        # Exclusive creation: never rotate a live routing token or overwrite user models.
        try:
            with path.open("xb") as handle:
                handle.write(data)
        except FileExistsError:
            pass
    ModelRegistry.load(state / "registry.json")


def write_entry(config: Path, data: bytes):
    """Invalidate only the adjacent model cache, retaining a recoverable snapshot.

    Run with Desktop closed. Never overwrite a cache recreated by another process.
    Backups contain catalog metadata only and are not installed or published.
    """
    cache = config.resolve().parent / "models_cache.json"
    backup = None
    if cache.is_symlink() or (cache.exists() and not cache.is_file()):
        raise RouterError("model_cache_requires_regular_file")
    if cache.exists():
        fd, name = tempfile.mkstemp(prefix="models_cache.router-backup-", suffix=".json", dir=cache.parent)
        os.close(fd)
        backup = Path(name)
        try:
            os.replace(cache, backup)
        except Exception:
            backup.unlink()
            raise
    try:
        atomic_write(config, data)
    except Exception:
        # A concurrent writer's new cache takes precedence; keep the backup instead.
        if backup is not None and not cache.exists():
            os.link(backup, cache)
            backup.unlink()
        raise


def url(state: Path, port: int) -> str:
    import re
    token = (state / "token").read_text(encoding="ascii").strip()
    if not re.fullmatch(r"[a-f0-9]{64}", token) or not 1024 <= port <= 65535:
        raise RouterError("invalid_router_endpoint")
    return f"http://127.0.0.1:{port}/{token}/v1"


def health(state: Path, port: int) -> dict:
    with build_opener(ProxyHandler({})).open(url(state, port) + "/health", timeout=3) as response:
        value = json.loads(response.read(4096))
    if value.get("status") != "ready":
        raise RouterError("router_not_ready")
    return value


def activate(state: Path, port: int, config: Path):
    health(state, port)
    original = config.read_bytes() if config.exists() else b""
    if original.startswith(b"\xef\xbb\xbf"):
        raise RouterError("bom_config_requires_explicit_normalization")
    text = original.decode("utf-8-sig")
    parsed = tomllib.loads(text)
    block = (BEGIN + "openai_base_url = " + json.dumps(url(state, port)) + "\n" + END).encode()
    journal = state / "codex-entry.json"
    if journal.exists():
        owned = json.loads(journal.read_text())
        if owned != {"config": str(config.resolve()), "block": block.decode()}:
            raise RouterError("existing_router_journal_conflict")
        if original.startswith(block):
            return
        # A journal written before a failed config write can safely converge below.
    if BEGIN.strip() in text or END.strip() in text:
        raise RouterError("managed_config_changed")
    if parsed.get("model_provider", "openai") != "openai":
        raise RouterError("existing_provider_requires_explicit_migration")
    if "openai_base_url" in parsed or "model_catalog_json" in parsed or parsed.get("profile"):
        raise RouterError("existing_route_catalog_or_profile_requires_explicit_migration")
    # Journal first. Deactivation removes only this exact prefix, retaining later user edits.
    atomic_write(journal, json.dumps({"config": str(config.resolve()), "block": block.decode()}).encode())
    write_entry(config, block + original)


def deactivate(state: Path, config: Path):
    journal = state / "codex-entry.json"
    if not journal.exists():
        raise RouterError("router_journal_missing")
    owned = json.loads(journal.read_text())
    if owned["config"] != str(config.resolve()):
        raise RouterError("router_config_target_mismatch")
    current = config.read_bytes()
    block = owned["block"].encode()
    if not current.startswith(block):
        raise RouterError("managed_config_changed")
    write_entry(config, current[len(block):])
    journal.unlink()
