from __future__ import annotations

from pathlib import Path
import os
import sys
import tempfile
import unittest


SKILL_ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP_ROOT = Path(os.environ.get("FEISHU_BRIDGE_TEST_TMP", tempfile.gettempdir()))
if "FEISHU_BRIDGE_TEST_TMP" not in os.environ:
    TEST_TEMP_ROOT = TEST_TEMP_ROOT / "feishu-codex-bridge-tests"
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from bridge_core.project_routing import (  # noqa: E402
    ProjectRoutingError,
    project_route_id,
    resolve_new_project_root,
    validate_staged_project_root,
    validate_project_name,
)


class ProjectRoutingTests(unittest.TestCase):
    def test_name_is_a_single_folder_name_not_a_path(self) -> None:
        self.assertEqual("秋季企划", validate_project_name('"秋季企划"'))
        for value in ("../escape", "child/name", r"child\name", "CON", ".hidden", "bad."):
            with self.subTest(value=value), self.assertRaises(ProjectRoutingError):
                validate_project_name(value)

    def test_new_project_is_one_direct_child_outside_bridge_project(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            container = Path(temporary)
            bridge = container / "bridge"
            bridge.mkdir()
            target = resolve_new_project_root(container, bridge, "isolated")
            self.assertEqual(container / "isolated", target)

            target.mkdir()
            with self.assertRaises(ProjectRoutingError):
                resolve_new_project_root(container, bridge, "isolated")

            nested_container = bridge / "projects"
            nested_container.mkdir()
            with self.assertRaises(ProjectRoutingError):
                resolve_new_project_root(nested_container, bridge, "mixed")

    def test_staged_project_must_remain_an_exact_direct_child(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            root = Path(temporary)
            container = root / "projects"
            bridge = root / "bridge"
            container.mkdir()
            bridge.mkdir()
            staged = container / "isolated"
            staged.mkdir()
            self.assertEqual(
                staged.resolve(),
                validate_staged_project_root(container, bridge, "isolated", staged),
            )
            nested = staged / "nested"
            nested.mkdir()
            with self.assertRaises(ProjectRoutingError):
                validate_staged_project_root(container, bridge, "isolated", nested)

    def test_project_id_is_stable_and_scope_specific(self) -> None:
        root = Path.cwd()
        first = project_route_id("group:one", root)
        self.assertEqual(first, project_route_id("group:one", root))
        self.assertNotEqual(first, project_route_id("group:two", root))
        self.assertEqual(10, len(first))


if __name__ == "__main__":
    unittest.main()
