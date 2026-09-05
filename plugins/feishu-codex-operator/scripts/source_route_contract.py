"""Classify a Feishu Operator tree without treating documentation as authority.

The contract is intentionally read-only and answer-free.  A development source
is authoritative only when the repository-local Marketplace has exactly one
matching local plugin entry and that entry resolves to the supplied plugin
root.  A versioned Codex cache tree can be recognized for diagnostics, but it
is never eligible to stand in for development source.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from typing import Any, NoReturn, Sequence


PLUGIN_NAME = "feishu-codex-operator"
RELEASE_NAME = "feishu-codex-operator-plugin"
MAX_METADATA_BYTES = 2 * 1024 * 1024
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
PLUGIN_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")


class SourceRouteError(RuntimeError):
    """A fixed-token source route failure safe for machine output."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:  # pragma: no cover - argparse glue
        del message
        raise SourceRouteError("invalid_arguments")


def _is_reparse(stat_result: os.stat_result) -> bool:
    attributes = int(getattr(stat_result, "st_file_attributes", 0) or 0)
    return stat.S_ISLNK(stat_result.st_mode) or bool(
        attributes & FILE_ATTRIBUTE_REPARSE_POINT
    )


def _absolute_path(value: str, *, code: str) -> Path:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise SourceRouteError(code)
    candidate = Path(value)
    if not candidate.is_absolute() or str(candidate).startswith(("\\\\", "//")):
        raise SourceRouteError(code)
    return candidate


def _assert_existing_no_reparse(path: Path, *, file: bool, code: str) -> Path:
    """Return a strict physical path after rejecting every reparse component."""

    absolute = _absolute_path(str(path), code=code)
    parts = absolute.parts
    if not parts:
        raise SourceRouteError(code)
    current = Path(parts[0])
    try:
        root_stat = os.lstat(current)
    except OSError as exc:
        raise SourceRouteError(code) from exc
    if _is_reparse(root_stat):
        raise SourceRouteError("reparse_path_rejected")
    for part in parts[1:]:
        current = current / part
        try:
            current_stat = os.lstat(current)
        except OSError as exc:
            raise SourceRouteError(code) from exc
        if _is_reparse(current_stat):
            raise SourceRouteError("reparse_path_rejected")
    try:
        resolved = absolute.resolve(strict=True)
        resolved_stat = resolved.stat()
    except OSError as exc:
        raise SourceRouteError(code) from exc
    if file and not stat.S_ISREG(resolved_stat.st_mode):
        raise SourceRouteError(code)
    if not file and not stat.S_ISDIR(resolved_stat.st_mode):
        raise SourceRouteError(code)
    return resolved


def _read_json(path: Path, *, code: str) -> dict[str, Any]:
    physical = _assert_existing_no_reparse(path, file=True, code=code)
    try:
        size = physical.stat().st_size
        if size < 2 or size > MAX_METADATA_BYTES:
            raise SourceRouteError(code)
        payload = json.loads(
            physical.read_bytes().decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_members,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise SourceRouteError(code) from exc
    if not isinstance(payload, dict):
        raise SourceRouteError(code)
    return payload


def _reject_duplicate_json_members(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    normalized_names: set[str] = set()
    for name, item in pairs:
        normalized = name.casefold()
        if normalized in normalized_names:
            raise ValueError("duplicate JSON member")
        normalized_names.add(normalized)
        value[name] = item
    return value


def _validate_plugin_tree(plugin_root: Path) -> tuple[str, str]:
    manifest = _read_json(
        plugin_root / ".codex-plugin" / "plugin.json",
        code="invalid_plugin_manifest",
    )
    if manifest.get("name") != PLUGIN_NAME:
        raise SourceRouteError("plugin_identity_mismatch")
    plugin_version = manifest.get("version")
    if (
        not isinstance(plugin_version, str)
        or PLUGIN_VERSION_PATTERN.fullmatch(plugin_version) is None
    ):
        raise SourceRouteError("invalid_plugin_version")

    inventory = _read_json(
        plugin_root / "assets" / "release-inventory.json",
        code="invalid_release_inventory",
    )
    if inventory.get("schema_version") != 1:
        raise SourceRouteError("invalid_release_inventory")
    if inventory.get("release_name") != RELEASE_NAME:
        raise SourceRouteError("release_identity_mismatch")
    source_version = inventory.get("source_version")
    if not isinstance(source_version, str) or not source_version.strip():
        raise SourceRouteError("invalid_source_version")
    components = inventory.get("components")
    if not isinstance(components, list):
        raise SourceRouteError("invalid_release_inventory")
    desktop = [
        component
        for component in components
        if isinstance(component, dict)
        and isinstance(component.get("name"), str)
        and component["name"].casefold() == "desktop_operator"
    ]
    if (
        len(desktop) != 1
        or desktop[0].get("name") != "desktop_operator"
        or desktop[0].get("root_role") != "plugin_root"
    ):
        raise SourceRouteError("inventory_root_role_mismatch")
    return plugin_version, source_version


def _relative_marketplace_source(value: Any) -> tuple[str, ...]:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or ":" in value
        or "\x00" in value
    ):
        raise SourceRouteError("invalid_marketplace_source")
    pure = PurePosixPath(value)
    if pure.is_absolute():
        raise SourceRouteError("invalid_marketplace_source")
    parts = tuple(part for part in pure.parts if part != ".")
    if not parts or any(part in ("", "..") for part in parts):
        raise SourceRouteError("invalid_marketplace_source")
    return parts


def _marketplace_route(marketplace_path: Path) -> tuple[str, Path]:
    marketplace = _read_json(
        marketplace_path,
        code="invalid_marketplace_manifest",
    )
    physical_marketplace = _assert_existing_no_reparse(
        marketplace_path,
        file=True,
        code="invalid_marketplace_manifest",
    )
    if (
        physical_marketplace.name != "marketplace.json"
        or physical_marketplace.parent.name != "plugins"
        or physical_marketplace.parent.parent.name != ".agents"
        or marketplace.get("name") != PLUGIN_NAME
    ):
        raise SourceRouteError("marketplace_identity_mismatch")
    plugins = marketplace.get("plugins")
    if not isinstance(plugins, list):
        raise SourceRouteError("invalid_marketplace_manifest")
    matches = [
        entry
        for entry in plugins
        if isinstance(entry, dict)
        and isinstance(entry.get("name"), str)
        and entry["name"].casefold() == PLUGIN_NAME.casefold()
    ]
    if len(matches) != 1:
        raise SourceRouteError("ambiguous_marketplace_plugin")
    if matches[0].get("name") != PLUGIN_NAME:
        raise SourceRouteError("marketplace_identity_mismatch")
    source = matches[0].get("source")
    if not isinstance(source, dict) or source.get("source") != "local":
        raise SourceRouteError("invalid_marketplace_source")
    if source.get("path") != f"./plugins/{PLUGIN_NAME}":
        raise SourceRouteError("invalid_marketplace_source")
    relative_parts = _relative_marketplace_source(source.get("path"))
    repository_root = physical_marketplace.parents[2]
    routed = repository_root.joinpath(*relative_parts)
    routed = _assert_existing_no_reparse(
        routed,
        file=False,
        code="marketplace_source_missing",
    )
    _validate_plugin_tree(routed)
    return str(marketplace["name"]), routed


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left)) == os.path.normcase(str(right))


def _is_installed_snapshot(
    plugin_root: Path,
    *,
    codex_home_value: str | None,
    marketplace_name: str,
    plugin_version: str,
) -> bool:
    # Expected cache layout:
    #   <codex-home>/plugins/cache/<marketplace>/<plugin>/<version>
    configured_home = codex_home_value or os.environ.get("CODEX_HOME")
    if not configured_home:
        default_home = Path.home() / ".codex"
        if not default_home.exists():
            return False
        configured_home = str(default_home)
    codex_home = _assert_existing_no_reparse(
        _absolute_path(configured_home, code="invalid_codex_home"),
        file=False,
        code="invalid_codex_home",
    )
    expected = codex_home.joinpath(
        "plugins",
        "cache",
        marketplace_name,
        PLUGIN_NAME,
        plugin_version,
    )
    return _same_path(plugin_root, expected)


def _result(
    *,
    status: str,
    role: str,
    verified: bool,
    development_eligible: bool,
    diagnostic_only: bool,
    reason: str,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": status,
        "role": role,
        "route_verified": verified,
        "development_source_eligible": development_eligible,
        "installed_snapshot_diagnostic_only": diagnostic_only,
        "knowledge_content_authoritative": False,
        "reason": reason,
    }


def evaluate(
    plugin_root_value: str,
    marketplace_value: str,
    codex_home_value: str | None = None,
) -> dict[str, Any]:
    plugin_root = _assert_existing_no_reparse(
        _absolute_path(plugin_root_value, code="invalid_plugin_root"),
        file=False,
        code="invalid_plugin_root",
    )
    plugin_version, _source_version = _validate_plugin_tree(plugin_root)
    marketplace_name, routed_source = _marketplace_route(
        _absolute_path(marketplace_value, code="invalid_marketplace_manifest")
    )

    if _same_path(plugin_root, routed_source):
        return _result(
            status="pass",
            role="canonical-development",
            verified=True,
            development_eligible=True,
            diagnostic_only=False,
            reason="canonical_marketplace_route",
        )
    if _is_installed_snapshot(
        plugin_root,
        codex_home_value=codex_home_value,
        marketplace_name=marketplace_name,
        plugin_version=plugin_version,
    ):
        return _result(
            status="pass",
            role="installed-snapshot",
            verified=True,
            development_eligible=False,
            diagnostic_only=True,
            reason="installed_cache_layout",
        )
    return _result(
        status="fail",
        role="legacy-or-copy",
        verified=False,
        development_eligible=False,
        diagnostic_only=False,
        reason="marketplace_route_mismatch",
    )


def _parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--plugin-root", required=True)
    parser.add_argument("--marketplace", required=True)
    parser.add_argument("--codex-home")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        result = evaluate(
            arguments.plugin_root,
            arguments.marketplace,
            arguments.codex_home,
        )
    except SourceRouteError as exc:
        result = _result(
            status="fail",
            role="legacy-or-copy",
            verified=False,
            development_eligible=False,
            diagnostic_only=False,
            reason=exc.code,
        )
    except Exception:
        result = _result(
            status="fail",
            role="legacy-or-copy",
            verified=False,
            development_eligible=False,
            diagnostic_only=False,
            reason="internal_contract_error",
        )
    print(json.dumps(result, ensure_ascii=True, separators=(",", ":"), sort_keys=True))
    return 0 if result["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
