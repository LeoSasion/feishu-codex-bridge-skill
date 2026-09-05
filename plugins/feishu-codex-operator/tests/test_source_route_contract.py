from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

import source_route_contract as contract  # noqa: E402


PLUGIN_VERSION = "0.2.0"
SOURCE_VERSION = "4.2.0-alpha.86"


class SourceRouteContractTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell audit requires pwsh")
    def test_release_audit_stdout_is_single_json(self) -> None:
        result = subprocess.run(
            ["pwsh", "-NoProfile", "-File", str(
                PLUGIN_ROOT / "scripts" / "audit-feishu-codex-release.ps1"
            )],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
            check=True,
        )
        self.assertEqual("passed", json.loads(result.stdout)["status"])

    def _write_json(self, path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _make_plugin(self, root: Path) -> Path:
        self._write_json(
            root / ".codex-plugin" / "plugin.json",
            {"name": contract.PLUGIN_NAME, "version": PLUGIN_VERSION},
        )
        self._write_json(
            root / "assets" / "release-inventory.json",
            {
                "schema_version": 1,
                "release_name": contract.RELEASE_NAME,
                "source_version": SOURCE_VERSION,
                "components": [
                    {"name": "desktop_operator", "root_role": "plugin_root"},
                    {"name": "harness_sibling", "root_role": "sibling_skill_root"},
                ],
            },
        )
        return root

    def _make_marketplace(self, repository: Path) -> tuple[Path, Path]:
        source = self._make_plugin(
            repository / "plugins" / contract.PLUGIN_NAME
        )
        marketplace = repository / ".agents" / "plugins" / "marketplace.json"
        self._write_json(
            marketplace,
            {
                "name": contract.PLUGIN_NAME,
                "plugins": [
                    {
                        "name": contract.PLUGIN_NAME,
                        "source": {
                            "source": "local",
                            "path": f"./plugins/{contract.PLUGIN_NAME}",
                        },
                    }
                ],
            },
        )
        return marketplace, source

    def test_canonical_marketplace_source_is_development_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            marketplace, source = self._make_marketplace(Path(temporary) / "repo")
            result = contract.evaluate(str(source), str(marketplace))

        self.assertEqual("pass", result["status"])
        self.assertEqual("canonical-development", result["role"])
        self.assertTrue(result["route_verified"])
        self.assertTrue(result["development_source_eligible"])
        self.assertFalse(result["installed_snapshot_diagnostic_only"])
        self.assertFalse(result["knowledge_content_authoritative"])

    def test_installed_snapshot_is_diagnostic_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            marketplace, _source = self._make_marketplace(base / "repo")
            snapshot = self._make_plugin(
                base
                / ".codex"
                / "plugins"
                / "cache"
                / contract.PLUGIN_NAME
                / contract.PLUGIN_NAME
                / PLUGIN_VERSION
            )
            result = contract.evaluate(
                str(snapshot), str(marketplace), str(base / ".codex")
            )

        self.assertEqual("pass", result["status"])
        self.assertEqual("installed-snapshot", result["role"])
        self.assertTrue(result["route_verified"])
        self.assertFalse(result["development_source_eligible"])
        self.assertTrue(result["installed_snapshot_diagnostic_only"])

    def test_fake_cache_layout_outside_current_codex_home_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            marketplace, _source = self._make_marketplace(base / "repo")
            current_home = base / "current-codex-home"
            current_home.mkdir()
            fake_snapshot = self._make_plugin(
                base
                / "fake-home"
                / "plugins"
                / "cache"
                / contract.PLUGIN_NAME
                / contract.PLUGIN_NAME
                / PLUGIN_VERSION
            )
            result = contract.evaluate(
                str(fake_snapshot), str(marketplace), str(current_home)
            )

        self.assertEqual("fail", result["status"])
        self.assertEqual("legacy-or-copy", result["role"])
        self.assertFalse(result["route_verified"])
        self.assertFalse(result["development_source_eligible"])

    def test_arbitrary_copy_and_legacy_root_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            marketplace, _source = self._make_marketplace(base / "repo")
            copied = self._make_plugin(base / "legacy-root")
            result = contract.evaluate(str(copied), str(marketplace))

        self.assertEqual("fail", result["status"])
        self.assertEqual("legacy-or-copy", result["role"])
        self.assertFalse(result["route_verified"])
        self.assertFalse(result["development_source_eligible"])
        self.assertEqual("marketplace_route_mismatch", result["reason"])

    def test_markdown_content_is_not_an_authority_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            marketplace, source = self._make_marketplace(Path(temporary) / "repo")
            knowledge = source / "upgrade-operator.md"
            knowledge.write_text("legacy knowledge candidate\n", encoding="utf-8")
            before = contract.evaluate(str(source), str(marketplace))
            knowledge.write_text("new semantic candidate\n", encoding="utf-8")
            after = contract.evaluate(str(source), str(marketplace))

        self.assertEqual(before, after)
        self.assertFalse(after["knowledge_content_authoritative"])

    def test_ambiguous_marketplace_entries_fail_closed(self) -> None:
        for duplicate_name in (contract.PLUGIN_NAME, "Feishu-Codex-Operator"):
            with self.subTest(name=duplicate_name), tempfile.TemporaryDirectory() as temporary:
                repository = Path(temporary) / "repo"
                marketplace, source = self._make_marketplace(repository)
                payload = json.loads(marketplace.read_text(encoding="utf-8"))
                duplicate = payload["plugins"][0].copy()
                duplicate["name"] = duplicate_name
                payload["plugins"].append(duplicate)
                self._write_json(marketplace, payload)

                with self.assertRaises(contract.SourceRouteError) as raised:
                    contract.evaluate(str(source), str(marketplace))

            self.assertEqual("ambiguous_marketplace_plugin", raised.exception.code)

    def test_duplicate_json_members_fail_closed(self) -> None:
        duplicate_payloads = (
            '{"name":"feishu-codex-operator",'
            '"name":"feishu-codex-operator","plugins":[]}',
            '{"Name":"feishu-codex-operator",'
            '"name":"feishu-codex-operator","plugins":[]}',
            '{"name":"feishu-codex-operator","plugins":['
            '{"name":"feishu-codex-operator","source":'
            '{"path":"a","PATH":"b"}}]}',
        )
        for payload in duplicate_payloads:
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as temporary:
                marketplace, source = self._make_marketplace(Path(temporary) / "repo")
                marketplace.write_text(payload, encoding="utf-8")
                with self.assertRaises(contract.SourceRouteError) as raised:
                    contract.evaluate(str(source), str(marketplace))

            self.assertEqual("invalid_marketplace_manifest", raised.exception.code)

    def test_identity_and_inventory_role_mismatches_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            marketplace, source = self._make_marketplace(Path(temporary) / "repo")
            manifest_path = source / ".codex-plugin" / "plugin.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["name"] = "wrong-plugin"
            self._write_json(manifest_path, manifest)
            with self.assertRaises(contract.SourceRouteError) as identity:
                contract.evaluate(str(source), str(marketplace))
            self.assertEqual("plugin_identity_mismatch", identity.exception.code)

            manifest["name"] = contract.PLUGIN_NAME
            self._write_json(manifest_path, manifest)
            inventory_path = source / "assets" / "release-inventory.json"
            inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
            inventory["components"][0]["root_role"] = "skill_root"
            self._write_json(inventory_path, inventory)
            with self.assertRaises(contract.SourceRouteError) as inventory_error:
                contract.evaluate(str(source), str(marketplace))

        self.assertEqual(
            "inventory_root_role_mismatch",
            inventory_error.exception.code,
        )

    def test_invalid_plugin_version_tokens_fail_closed(self) -> None:
        invalid_versions = ("../escape", "bad/version", "bad\\version", " bad", "v1\n")
        for invalid_version in invalid_versions:
            with self.subTest(version=repr(invalid_version)), tempfile.TemporaryDirectory() as temporary:
                marketplace, source = self._make_marketplace(Path(temporary) / "repo")
                manifest_path = source / ".codex-plugin" / "plugin.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest["version"] = invalid_version
                self._write_json(manifest_path, manifest)

                with self.assertRaises(contract.SourceRouteError) as raised:
                    contract.evaluate(str(source), str(marketplace))

            self.assertEqual("invalid_plugin_version", raised.exception.code)

    def test_reparse_plugin_root_and_marketplace_are_rejected(self) -> None:
        for surface in ("plugin_root", "marketplace"):
            with self.subTest(surface=surface), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                marketplace, source = self._make_marketplace(base / "repo")
                if surface == "plugin_root":
                    alias = base / "source-alias"
                    link_destination = source
                    target_is_directory = True
                    plugin_root = alias
                    marketplace_path = marketplace
                else:
                    alias_root = base / "alias-repo" / ".agents" / "plugins"
                    alias_root.mkdir(parents=True)
                    alias = alias_root / "marketplace.json"
                    link_destination = marketplace
                    target_is_directory = False
                    plugin_root = source
                    marketplace_path = alias
                try:
                    alias.symlink_to(
                        link_destination, target_is_directory=target_is_directory
                    )
                except OSError as exc:
                    self.skipTest(f"{surface} symlink unavailable: {exc}")
                with self.assertRaises(contract.SourceRouteError) as raised:
                    contract.evaluate(str(plugin_root), str(marketplace_path))

                self.assertEqual(
                    "reparse_path_rejected", raised.exception.code
                )

    def test_traversal_and_snapshot_version_mismatch_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            repository = base / "repo"
            marketplace, _source = self._make_marketplace(repository)
            payload = json.loads(marketplace.read_text(encoding="utf-8"))
            payload["plugins"][0]["source"]["path"] = "../outside"
            self._write_json(marketplace, payload)
            with self.assertRaises(contract.SourceRouteError) as traversal:
                contract.evaluate(
                    str(repository / "plugins" / contract.PLUGIN_NAME),
                    str(marketplace),
                )
            self.assertEqual("invalid_marketplace_source", traversal.exception.code)

            marketplace, _source = self._make_marketplace(repository)
            mismatched = self._make_plugin(
                base
                / ".codex"
                / "plugins"
                / "cache"
                / contract.PLUGIN_NAME
                / contract.PLUGIN_NAME
                / "different-version"
            )
            result = contract.evaluate(str(mismatched), str(marketplace))

        self.assertEqual("fail", result["status"])
        self.assertEqual("legacy-or-copy", result["role"])

    def test_noncanonical_equivalent_marketplace_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary) / "repo"
            marketplace, source = self._make_marketplace(repository)
            payload = json.loads(marketplace.read_text(encoding="utf-8"))
            payload["plugins"][0]["source"]["path"] = (
                f"plugins/{contract.PLUGIN_NAME}"
            )
            self._write_json(marketplace, payload)

            with self.assertRaises(contract.SourceRouteError) as raised:
                contract.evaluate(str(source), str(marketplace))

        self.assertEqual("invalid_marketplace_source", raised.exception.code)

    def test_cli_output_is_ascii_answer_free_and_contains_no_input_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            marketplace, source = self._make_marketplace(Path(temporary) / "repo")
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = contract.main(
                    [
                        "--plugin-root",
                        str(source),
                        "--marketplace",
                        str(marketplace),
                    ]
                )
            wire = output.getvalue().strip()

        self.assertEqual(0, exit_code)
        wire.encode("ascii")
        self.assertNotIn(str(source), wire)
        self.assertNotIn(str(marketplace), wire)
        parsed = json.loads(wire)
        self.assertEqual("canonical-development", parsed["role"])

    def test_cli_failure_is_one_sanitized_json_object(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = contract.main(
                ["--plugin-root", "relative", "--marketplace", "relative"]
            )
        wire = output.getvalue().strip()
        parsed = json.loads(wire)

        self.assertEqual(2, exit_code)
        self.assertEqual("fail", parsed["status"])
        self.assertEqual("legacy-or-copy", parsed["role"])
        self.assertEqual("invalid_plugin_root", parsed["reason"])
        self.assertEqual(1, len(output.getvalue().splitlines()))
        wire.encode("ascii")


if __name__ == "__main__":
    unittest.main()
