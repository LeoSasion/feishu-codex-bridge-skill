"""Opt-in LM Studio batch discovery. Metadata and configured contracts are not acceptance."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from .model_registry import ModelRegistry, RouterError
from . import model_router_config as settings
from .responses_profiles import preflight

# Dedicated auxiliary architecture observed in LM Studio's native model metadata.
# Ordinary small chat models may also serve as drafters; size/name is not proof.
AUXILIARY_ARCHITECTURES = frozenset({"gemma4-assistant"})


def validate_policy(policy):
    fields = {"version", "api_base", "api_key_env", "context_window", "reasoning_efforts", "responses"}
    if (not isinstance(policy, dict) or set(policy) != fields or type(policy["version"]) is not int
            or policy["version"] != 1 or not isinstance(policy["api_base"], str)
            or not isinstance(policy["responses"], dict)):
        raise RouterError("invalid_lmstudio_discovery_policy")
    base = urlsplit(policy["api_base"])
    if (base.scheme not in {"http", "https"} or base.hostname not in {"127.0.0.1", "::1"}
            or base.username or base.password or base.query or base.fragment
            or base.path.rstrip("/") != "/v1" or "%" in base.netloc):
        raise RouterError("lmstudio_requires_literal_loopback_v1")
    if base.port is not None and not 1 <= base.port <= 65535:
        raise RouterError("invalid_lmstudio_port")
    row = {k: deepcopy(policy[k]) for k in fields - {"version"}}
    row.update(slug="local/lmstudio/policy-check", model="policy-check", display_name="Policy check")
    preflight(row)
    return row


def text_field(value, maximum=512):
    if (not isinstance(value, str) or not value.strip() or len(value) > maximum
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise RouterError("invalid_lmstudio_discovery_text")
    return value


def stable_slug(api_base, model):
    label = re.sub(r"[^a-zA-Z0-9_-]", "-", model).strip("-")[:55] or "model"
    suffix = hashlib.sha256((api_base.rstrip("/") + "\n" + model).encode("utf-8")).hexdigest()[:12]
    return "local/lmstudio/" + label + "-" + suffix


def discovery_plan(registry, policy, metadata):
    """Pure preview; preserve existing rows and exclude known non-chat architectures."""
    template = validate_policy(policy)
    catalog = json.loads(Path(__file__).with_name("beeper_model_catalog.json").read_text(encoding="utf-8"))
    ModelRegistry(registry, catalog)
    if not isinstance(metadata, dict) or not isinstance(metadata.get("models"), list) or len(metadata["models"]) > 1000:
        raise RouterError("invalid_lmstudio_catalog")
    api_base = policy["api_base"].rstrip("/")
    existing = {(row["api_base"].rstrip("/"), row["model"]): row for row in registry["models"]}
    rows, preserved, excluded, observations, seen = [], [], [], [], set()
    excluded_existing = []
    for item in metadata["models"]:
        if not isinstance(item, dict) or item.get("type") not in {"llm", "embedding"}:
            raise RouterError("invalid_lmstudio_discovery_model")
        model = text_field(item.get("key"))
        if model in seen:
            raise RouterError("duplicate_lmstudio_model_key")
        seen.add(model)
        if item["type"] == "embedding":
            excluded.append({"model": model, "reason": "embedding_not_chat"})
            continue
        architecture = item.get("architecture")
        if architecture is not None:
            architecture = text_field(architecture, 128)
        if architecture in AUXILIARY_ARCHITECTURES:
            excluded.append({"model": model, "reason": "auxiliary_draft_not_chat"})
            if (api_base, model) in existing:
                excluded_existing.append(existing[(api_base, model)]["slug"])
            continue
        if (api_base, model) in existing:
            preserved.append(existing[(api_base, model)]["slug"])
            continue
        title = text_field(item.get("display_name"), 256)
        maximum = item.get("max_context_length")
        if type(maximum) is not int or not 1024 <= maximum <= 2000000:
            raise RouterError("invalid_lmstudio_max_context")
        instances = item.get("loaded_instances")
        if not isinstance(instances, list) or len(instances) > 100:
            raise RouterError("invalid_lmstudio_loaded_instances")
        matching, instance_ids = [], set()
        for instance in instances:
            if not isinstance(instance, dict):
                raise RouterError("invalid_lmstudio_loaded_instance")
            identity = text_field(instance.get("id"))
            if identity in instance_ids:
                raise RouterError("duplicate_lmstudio_instance")
            instance_ids.add(identity)
            if identity == model:
                config = instance.get("config")
                length = config.get("context_length") if isinstance(config, dict) else None
                if type(length) is not int or not 1024 <= length <= maximum:
                    raise RouterError("invalid_lmstudio_loaded_context")
                matching.append(length)
        context = min(policy["context_window"], maximum, *matching)
        row = {**deepcopy(template), "api_base": api_base,
               "model": model, "slug": stable_slug(api_base, model),
               "display_name": "LM Studio: " + title + " [unverified]", "context_window": context}
        preflight(row)
        rows.append(row)
        observations.append({"slug": row["slug"], "loaded_exact_model": bool(matching),
                             "context_source": "loaded_and_policy_limit" if matching else "model_maximum_and_policy_limit",
                             "current_loaded_context_verified": bool(matching)})
    # Validate all new aliases/capacity against the old registry before any write.
    merged = {"version": 2, "models": [{**deepcopy(r), "responses": r.get("responses")} for r in registry["models"]] + rows}
    ModelRegistry(merged, catalog)
    return {"models_to_add": rows, "preserved_slugs": preserved, "excluded": excluded,
            "excluded_existing_slugs": excluded_existing,
            "observations": observations, "discovered_chat_models": len(rows) + len(preserved),
            "inference_requests": 0, "models_loaded": 0, "models_downloaded": 0,
            "capabilities_verified": False, "desktop_verified": False}


def scan(state, policy):
    validate_policy(policy)
    target = state / "registry.json"
    if target.is_symlink():
        raise RouterError("registry_requires_regular_file")
    registry = settings.read_registration(target)
    original = target.read_bytes()
    if settings.loads(original) != registry:
        raise RouterError("registry_changed_during_discovery")
    metadata = settings.lmstudio_json(policy["api_base"], policy["api_key_env"], native=True)
    result = discovery_plan(registry, policy, metadata)
    result.update(registry_sha256=hashlib.sha256(original).hexdigest(), metadata_requests=1,
                  policy_sha256=hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest())
    return result


def synchronize(state, policy):
    if (state / "codex-entry.json").exists():
        raise RouterError("deactivate_before_registration")
    result = scan(state, policy)
    result["added"] = settings.register_routes(state, result["models_to_add"], expected_sha256=result["registry_sha256"])
    return result
