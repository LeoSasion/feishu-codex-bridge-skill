"""Explicit model registration; native catalog rows are never rewritten."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import hashlib
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from .responses_capabilities import ResponsesCapabilities, RouterError


@dataclass(frozen=True)
class ModelRoute:
    slug: str
    display_name: str
    model: str
    api_base: str
    api_key_env: str
    context_window: int
    reasoning_efforts: tuple[str, ...]
    responses: ResponsesCapabilities | None = None

    def key(self) -> str:
        if not self.api_key_env:
            return ""
        value = os.environ.get(self.api_key_env, "")
        if not value or "\r" in value or "\n" in value:
            raise RouterError("configured_api_key_unavailable")
        return value


class ModelRegistry:
    def __init__(self, value: dict, beeper_catalog: dict) -> None:
        if (not isinstance(value, dict) or set(value) != {"version", "models"}
                or type(value["version"]) is not int or value["version"] not in {1, 2}):
            raise RouterError("invalid_registry_version_or_fields")
        if not isinstance(value["models"], list) or len(value["models"]) > 100:
            raise RouterError("invalid_models")
        self.beeper = deepcopy(beeper_catalog["models"][0])
        self.routes: dict[str, ModelRoute] = {}
        self.version = value["version"]
        fields = set(ModelRoute.__dataclass_fields__) - {"responses"}
        if self.version == 2:
            fields.add("responses")
        for row in value["models"]:
            if not isinstance(row, dict) or set(row) != fields:
                raise RouterError("invalid_route_fields")
            slug = row["slug"]
            if not isinstance(slug, str) or not re.fullmatch(r"(?:local|api)/[a-zA-Z0-9._/-]{1,100}", slug):
                raise RouterError("external_slug_requires_local_or_api_namespace")
            if slug in self.routes or ".." in slug:
                raise RouterError("duplicate_or_invalid_slug")
            for field in ("display_name", "model", "api_base", "api_key_env"):
                if not isinstance(row[field], str) or any(ord(c) < 32 for c in row[field]):
                    raise RouterError("invalid_route_string")
            if not row["model"] or not row["display_name"]:
                raise RouterError("empty_route_name")
            base = urlsplit(row["api_base"])
            if (base.scheme not in {"http", "https"} or not base.hostname or base.username
                    or base.password or base.query or base.fragment or "%" in base.netloc):
                raise RouterError("invalid_api_base")
            if base.scheme == "http" and base.hostname not in {"127.0.0.1", "::1", "localhost"}:
                raise RouterError("remote_api_requires_https")
            if row["api_key_env"] and not re.fullmatch(r"[A-Z][A-Z0-9_]{0,100}", row["api_key_env"]):
                raise RouterError("invalid_key_environment_name")
            if type(row["context_window"]) is not int or not 1024 <= row["context_window"] <= 2000000:
                raise RouterError("invalid_context_window")
            efforts = row["reasoning_efforts"]
            if (not isinstance(efforts, list) or not efforts
                    or any(not isinstance(e, str) or e not in {
                        "none", "minimal", "low", "medium", "high", "xhigh", "max"} for e in efforts)
                    or len(efforts) != len(set(efforts))):
                raise RouterError("invalid_reasoning_efforts")
            capabilities = row.get("responses")
            if capabilities is not None:
                capabilities = ResponsesCapabilities.parse(capabilities)
                if any(effort not in set(efforts) | {"unspecified"}
                       for effort, _ in capabilities.tool_choice_by_reasoning):
                    raise RouterError("reasoning_tool_choice_profile_not_registered")
            self.routes[slug] = ModelRoute(**{**row, "reasoning_efforts": tuple(efforts),
                                            "responses": capabilities})

    @classmethod
    def load(cls, path: Path) -> "ModelRegistry":
        return cls(json.loads(path.read_text(encoding="utf-8")), json.loads(
            Path(__file__).with_name("beeper_model_catalog.json").read_text(encoding="utf-8")))

    @classmethod
    def load_snapshot(cls, path: Path, expected_sha256: str | None = None):
        """One bounded, strict read for an explicit reload, never request-path I/O."""
        from .responses_tool_adapter import loads
        try:
            if path.is_symlink() or not path.is_file():
                raise ValueError("regular registry required")
            with path.open("rb") as handle:
                raw = handle.read(1048577)
            if len(raw) > 1048576:
                raise ValueError("registry too large")
            digest = hashlib.sha256(raw).hexdigest()
            if expected_sha256 is not None and digest != expected_sha256:
                raise RouterError("registry_reload_digest_mismatch")
            registry = cls(loads(raw), json.loads(
                Path(__file__).with_name("beeper_model_catalog.json").read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError, KeyError) as exc:
            if isinstance(exc, RouterError) and str(exc) == "registry_reload_digest_mismatch":
                raise
            raise RouterError("registry_reload_invalid_registry") from exc
        return registry, digest

    def merge(self, catalog: dict) -> dict:
        if not isinstance(catalog, dict) or not isinstance(catalog.get("models"), list):
            raise RouterError("invalid_native_catalog")
        native = catalog["models"]
        slugs = [row.get("slug") for row in native if isinstance(row, dict)]
        if len(slugs) != len(native) or any(not isinstance(s, str) or not s for s in slugs):
            raise RouterError("invalid_native_model")
        if len(slugs) != len(set(slugs)) or set(slugs) & (set(self.routes) | {"beeper"}):
            raise RouterError("model_catalog_collision")
        template = next((row for row in native if row.get("visibility") == "list"), None)
        if template is None:
            raise RouterError("native_template_unavailable")
        added = [deepcopy(self.beeper)]
        for route in self.routes.values():
            # v1/null routes keep their original presentation. Adapted entries start
            # from a project-owned catalog row, never unknown native feature flags.
            row = deepcopy(template if route.responses is None else self.beeper)
            for field in ("comp_hash", "availability_nux", "auto_compact_token_limit"):
                row.pop(field, None)
            row.update(slug=route.slug, display_name=route.display_name,
                       description="Configured OpenAI Responses endpoint",
                       visibility="list", supported_in_api=True, upgrade=None,
                       context_window=route.context_window, max_context_window=route.context_window,
                       effective_context_window_percent=95, multi_agent_version="disabled",
                       input_modalities=["text"], tool_mode=None, base_instructions="",
                       model_messages={}, use_responses_lite=False,
                       experimental_supported_tools=[], supports_search_tool=False,
                       additional_speed_tiers=[], service_tiers=[], default_service_tier=None,
                       default_reasoning_level=route.reasoning_efforts[0],
                       supported_reasoning_levels=[{"effort": e, "description": e}
                                                   for e in route.reasoning_efforts])
            if route.responses is not None:
                row.update(route.responses.catalog_fields())
                row.update(include_skills_usage_instructions=False,
                           include_plugin_usage_instructions=False,
                           include_apps_usage_instructions=False,
                           multi_agent_version="disabled", node_repl_auto_review_required=True)
            added.append(row)
        return {**deepcopy(catalog), "models": deepcopy(native) + added}
