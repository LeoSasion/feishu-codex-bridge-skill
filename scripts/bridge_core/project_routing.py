"""Safe project-folder routing for the conversational setup wizard."""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
import unicodedata


MAX_PROJECT_NAME_CHARS = 80
INVALID_WINDOWS_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


class ProjectRoutingError(ValueError):
    """A project command would escape its configured filesystem boundary."""


def validate_project_name(value: str) -> str:
    """Return one portable folder name or reject path-like and ambiguous input."""

    candidate = value.strip()
    if len(candidate) >= 2 and candidate[0] == candidate[-1] and candidate[0] in {'"', "'"}:
        candidate = candidate[1:-1].strip()
    candidate = unicodedata.normalize("NFKC", candidate)
    if not candidate:
        raise ProjectRoutingError("项目名称不能为空。")
    if len(candidate) > MAX_PROJECT_NAME_CHARS:
        raise ProjectRoutingError(f"项目名称不能超过 {MAX_PROJECT_NAME_CHARS} 个字符。")
    if candidate in {".", ".."} or candidate.startswith("."):
        raise ProjectRoutingError("项目名称不能是相对路径或隐藏目录名。")
    if candidate.endswith((" ", ".")):
        raise ProjectRoutingError("项目名称不能以空格或句点结尾。")
    if INVALID_WINDOWS_NAME.search(candidate):
        raise ProjectRoutingError("项目名称不能包含路径分隔符或 Windows 保留字符。")
    stem = candidate.split(".", 1)[0].upper()
    if stem in WINDOWS_RESERVED_NAMES:
        raise ProjectRoutingError("该名称是 Windows 保留设备名，请换一个项目名称。")
    return candidate


def project_route_id(scope: str, project_root: Path) -> str:
    """Build a stable opaque id without exposing a local path to Feishu."""

    normalized_root = str(project_root.resolve()).casefold()
    material = f"{scope}\0{normalized_root}".encode("utf-8", errors="surrogatepass")
    return hashlib.sha256(material).hexdigest()[:10]


def resolve_new_project_root(
    projects_root: Path,
    bridge_project_root: Path,
    project_name: str,
) -> Path:
    """Resolve one new sibling-style project and keep it outside the bridge project."""

    root = projects_root.resolve()
    if not root.is_dir():
        raise ProjectRoutingError("项目容器目录不存在，请先在 Codex Desktop 中配置。")
    candidate = (root / project_name).resolve()
    if candidate.parent != root:
        raise ProjectRoutingError("项目目录必须是所配置容器目录的直接子目录。")
    bridge_root = bridge_project_root.resolve()
    try:
        candidate.relative_to(bridge_root)
    except ValueError:
        pass
    else:
        raise ProjectRoutingError("新项目必须位于 Bridge 所在项目之外，避免文件继续混杂。")
    if candidate.exists():
        raise ProjectRoutingError("同名目录已经存在；Bridge 不会覆盖或接管现有目录。")
    return candidate


def validate_staged_project_root(
    projects_root: Path,
    bridge_project_root: Path,
    project_name: str,
    staged_root: Path,
) -> Path:
    """Revalidate one already-created direct child before Desktop receives it.

    Comparing the resolved target with the unresolved direct-child path rejects
    a staged folder replaced by a symlink or junction between request turns.
    """

    root = projects_root.resolve()
    expected = root / project_name
    try:
        target = staged_root.resolve(strict=True)
    except OSError as exc:
        raise ProjectRoutingError("暂存的项目目录已移动或缺失。") from exc
    if target != expected or target.parent != root or not target.is_dir():
        raise ProjectRoutingError("暂存的项目目录不再是所配置容器的直接子目录。")
    bridge_root = bridge_project_root.resolve()
    try:
        target.relative_to(bridge_root)
    except ValueError:
        pass
    else:
        raise ProjectRoutingError("暂存的项目目录不能位于 Bridge 项目内部。")
    return target
