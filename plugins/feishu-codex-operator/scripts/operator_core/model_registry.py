"""Explicit model registration; native catalog rows are never rewritten."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit


class RouterError(ValueError):
    """Safe, content-free error suitable for the local API."""


@dataclass(frozen=True)
class ModelRoute:
    slug: str
    display_name: str
    model: str
    api_base: str
    api_key_env: str
    context_window: int
    reasoning_efforts: tuple[str, ...]

    def key(self) -> str:
        if not self.api_key_env:
            return ""
        value = os.environ.get(self.api_key_env, "")
        if not value or "\r" in value or "\n" in value:
            raise RouterError("configured_api_key_unavailable")
        return value


class ModelRegistry:
    def __init__(self, value: dict, beeper_catalog: dict) -> None:
        if set(value) != {"version", "models"} or value["version"] != 1:
            raise RouterError("invalid_registry_version_or_fields")
        if not isinstance(value["models"], list) or len(value["models"]) > 100:
            raise RouterError("invalid_models")
        self.beeper = deepcopy(beeper_catalog["models"][0])
        self.routes: dict[str, ModelRoute] = {}
        fields = set(ModelRoute.__dataclass_fields__)
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
            if (not isinstance(efforts, list) or not efforts or len(efforts) != len(set(efforts))
                    or any(e not in {"none", "minimal", "low", "medium", "high", "xhigh"} for e in efforts)):
                raise RouterError("invalid_reasoning_efforts")
            self.routes[slug] = ModelRoute(**{**row, "reasoning_efforts": tuple(efforts)})

    @classmethod
    def load(cls, path: Path) -> "ModelRegistry":
        return cls(json.loads(path.read_text(encoding="utf-8")), json.loads(
            Path(__file__).with_name("beeper_model_catalog.json").read_text(encoding="utf-8")))

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
            row = deepcopy(template)
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
            added.append(row)
        return {**deepcopy(catalog), "models": deepcopy(native) + added}
