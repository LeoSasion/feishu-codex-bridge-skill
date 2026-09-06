"""Opt-in current-CLI catalog test; isolated home, no credentials or task calls."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from operator_core.app_server import AppServerSession
from operator_core.model_registry import ModelRegistry


@unittest.skipUnless(os.environ.get("CODEX_OPERATOR_TEST_CLI") and
                     os.environ.get("CODEX_OPERATOR_TEST_CATALOG"),
                     "explicit current Desktop CLI and read-only catalog paths required")
class CurrentCliCatalogTests(unittest.TestCase):
    def test_current_cli_accepts_augmented_catalog_without_native_row_changes(self):
        executable = Path(os.environ["CODEX_OPERATOR_TEST_CLI"]).resolve(strict=True)
        cached = json.loads(Path(os.environ["CODEX_OPERATOR_TEST_CATALOG"]).read_text(encoding="utf-8"))
        beeper = json.loads((Path(__file__).resolve().parents[1] /
            "scripts/operator_core/beeper_model_catalog.json").read_text(encoding="utf-8"))
        registry = ModelRegistry({"version": 1, "models": [{
            "slug": "api/catalog-check", "display_name": "Catalog check",
            "model": "catalog-check", "api_base": "http://127.0.0.1:1/v1",
            "api_key_env": "", "context_window": 32000, "reasoning_efforts": ["low"]}]}, beeper)
        native = {"models": cached["models"]}
        merged = registry.merge(native)
        self.assertEqual(native["models"], merged["models"][:len(native["models"])])
        with tempfile.TemporaryDirectory(prefix="operator-catalog-check-") as directory:
            root = Path(directory)
            catalog = root / "catalog.json"
            catalog.write_text(json.dumps(merged), encoding="utf-8")
            # No real home config, auth, caches, MCPs or plugins are loaded by this child.
            environment = {k: v for k, v in os.environ.items()
                if not k.upper().startswith(("CODEX_", "OPENAI_", "CHATGPT_"))}
            environment["CODEX_HOME"] = directory
            popen = subprocess.Popen

            def isolated_popen(args, **kwargs):
                return popen([*args, "-c", "model_catalog_json=" + json.dumps(str(catalog)),
                    "-c", "openai_base_url=\"http://127.0.0.1:1/v1\"",
                    "-c", "cli_auth_credentials_store=\"file\""],
                    **{**kwargs, "env": environment, "cwd": str(root.parent)})

            with patch("operator_core.app_server.subprocess.Popen", side_effect=isolated_popen):
                with AppServerSession(executable, 15) as session:
                    result = session.request("model/list", {"includeHidden": False, "limit": 100})
                session.process.stdout.close()
            models = {row["model"]: row for row in result["data"]}
            self.assertIn("beeper", models)
            self.assertIn("api/catalog-check", models)
            self.assertEqual("low", models["beeper"]["defaultReasoningEffort"])
            self.assertFalse(models["beeper"]["hidden"])
            for row in native["models"]:
                if row.get("visibility") == "list" and row.get("supported_in_api"):
                    self.assertIn(row["slug"], models)


if __name__ == "__main__":
    unittest.main()
