"""Batch discovery uses metadata only and commits all-or-nothing in stopped state."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_responses_tools import ROUTE
from operator_core import model_router_config as settings
from operator_core.lmstudio_discovery import discovery_plan, scan, stable_slug, synchronize, validate_policy
from operator_core.model_registry import RouterError


def policy():
    result = {key: deepcopy(ROUTE[key]) for key in ["context_window", "reasoning_efforts", "responses"]}
    result.update(version=1, api_base="http://127.0.0.1:1234/v1", api_key_env="")
    return result


def metadata():
    return {"models": [
        {"type": "llm", "key": "publisher/model A", "display_name": "中文 Model A", "max_context_length": 32000,
         "loaded_instances": [{"id": "publisher/model A", "config": {"context_length": 4096}}]},
        {"type": "llm", "key": "publisher/model_B", "display_name": "Model B", "max_context_length": 32000,
         "loaded_instances": []},
        {"type": "embedding", "key": "embedding/model"}]}


class DiscoveryTests(unittest.TestCase):
    def test_dedicated_mtp_architecture_is_excluded_without_guessing_from_names_or_size(self):
        value = metadata()
        value['models'][0].update(architecture='gemma4-assistant', display_name='Uninformative label')
        value['models'][1].update(architecture='qwen3', display_name='Small Assistant MTP-compatible', params_string='0.6B')
        result = discovery_plan({'version': 2, 'models': []}, policy(), value)
        self.assertEqual([r['model'] for r in result['models_to_add']], ['publisher/model_B'])
        self.assertIn({'model': 'publisher/model A', 'reason': 'auxiliary_draft_not_chat'}, result['excluded'])
        self.assertEqual(result['discovered_chat_models'], 1)

    def test_existing_auxiliary_row_is_reported_for_separate_cleanup_and_never_deleted(self):
        value = metadata(); value['models'][0]['architecture'] = 'gemma4-assistant'
        old = {**deepcopy(ROUTE), 'model': 'publisher/model A', 'api_base': policy()['api_base'], 'slug': 'local/old-mtp'}
        registry = {'version': 2, 'models': [old]}; before = deepcopy(registry)
        result = discovery_plan(registry, policy(), value)
        self.assertEqual(registry, before)
        self.assertEqual(result['excluded_existing_slugs'], ['local/old-mtp'])
        self.assertNotIn('local/old-mtp', result['preserved_slugs'])
        self.assertEqual(result['discovered_chat_models'], 1)

    def test_all_chat_models_are_planned_with_stable_unique_aliases_and_no_attestation(self):
        original = metadata()
        result = discovery_plan({"version": 1, "models": []}, policy(), original)
        self.assertEqual(original, metadata())
        self.assertEqual(result["discovered_chat_models"], 2)
        self.assertEqual(len(result["excluded"]), 1)
        self.assertEqual(result["models_to_add"][0]["context_window"], min(4096, policy()["context_window"]))
        self.assertIn("[unverified]", result["models_to_add"][0]["display_name"])
        self.assertFalse(result["capabilities_verified"])
        self.assertEqual(result["models_loaded"], 0)
        self.assertEqual(result["inference_requests"], 0)
        self.assertTrue(result["observations"][0]["current_loaded_context_verified"])
        self.assertFalse(result["observations"][1]["current_loaded_context_verified"])
        self.assertNotEqual(stable_slug(policy()["api_base"], "a/b"), stable_slug(policy()["api_base"], "a-b"))
        self.assertEqual(stable_slug(policy()["api_base"] + "/", "a/b"), stable_slug(policy()["api_base"], "a/b"))

    def test_existing_rows_and_other_endpoints_are_preserved_without_replacing_their_contracts(self):
        old = {**deepcopy(ROUTE), "model": "publisher/model A", "api_base": policy()["api_base"],
               "display_name": "Owner's name", "slug": "local/existing"}
        other = {**deepcopy(old), "api_base": "http://127.0.0.1:1235/v1", "slug": "local/other"}
        registry = {"version": 2, "models": [old, other]}
        before = deepcopy(registry)
        result = discovery_plan(registry, policy(), metadata())
        self.assertEqual(registry, before)
        self.assertEqual(result["preserved_slugs"], ["local/existing"])
        self.assertEqual(len(result["models_to_add"]), 1)

    def test_invalid_and_ambiguous_metadata_refuses_the_entire_batch(self):
        changes = [lambda m: m["models"].append(deepcopy(m["models"][0])),
                   lambda m: m["models"][1].update(max_context_length=True),
                   lambda m: m["models"][1].update(type="unknown"),
                   lambda m: m["models"][1].update(key="bad\nkey"),
                   lambda m: m["models"][1].update(architecture=[]),
                   lambda m: m["models"][0]["loaded_instances"].append(deepcopy(m["models"][0]["loaded_instances"][0])),
                   lambda m: m["models"][0]["loaded_instances"][0]["config"].update(context_length=999999)]
        for change in changes:
            value = metadata(); change(value)
            with self.subTest(change=change), self.assertRaises(RouterError):
                discovery_plan({"version": 2, "models": []}, policy(), value)

    def test_policy_is_explicit_and_loopback_only(self):
        for update in ({"api_base": "https://remote.example/v1"}, {"api_base": "http://localhost:1234/v1"},
                       {"responses": None}, {"context_window": True}, {"extra": True}, {"version": True}):
            with self.subTest(update=update), self.assertRaises(RouterError):
                validate_policy({**policy(), **update})

    def test_preview_does_not_modify_active_state_and_sync_is_atomic_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory); settings.initialize(state)
            original = (state / "registry.json").read_bytes()
            (state / "codex-entry.json").write_text("{}")
            with patch.object(settings, "lmstudio_json", return_value=metadata()) as get:
                preview = scan(state, policy())
                self.assertEqual((state / "registry.json").read_bytes(), original)
                self.assertEqual(preview["registry_sha256"], hashlib.sha256(original).hexdigest())
                get.assert_called_once_with(policy()["api_base"], "", native=True)
                with self.assertRaisesRegex(RouterError, "deactivate"):
                    synchronize(state, policy())
                self.assertEqual(get.call_count, 1)
            (state / "codex-entry.json").unlink()
            with patch.object(settings, "lmstudio_json", return_value=metadata()):
                self.assertEqual(synchronize(state, policy())["added"], 2)
                current = (state / "registry.json").read_bytes()
                self.assertEqual(synchronize(state, policy())["added"], 0)
                self.assertEqual((state / "registry.json").read_bytes(), current)
            self.assertFalse((state / "registry-edit.lock").exists())

    def test_batch_conflict_or_changed_registry_never_partially_commits(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory); settings.initialize(state)
            existing = {**deepcopy(ROUTE), "slug": "local/existing"}
            settings.register_route(state, existing)
            original = (state / "registry.json").read_bytes()
            rows = [{**deepcopy(ROUTE), "slug": "local/new"}, {**existing, "model": "changed"}]
            with self.assertRaisesRegex(RouterError, "conflict"):
                settings.register_routes(state, rows)
            self.assertEqual((state / "registry.json").read_bytes(), original)
            with self.assertRaisesRegex(RouterError, "changed_since"):
                settings.register_routes(state, rows[:1], expected_sha256="0" * 64)
            self.assertEqual((state / "registry.json").read_bytes(), original)
            self.assertFalse((state / "registry-edit.lock").exists())

    def test_native_metadata_get_uses_fixed_endpoint_and_no_redirect_or_proxy(self):
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(metadata()).encode()
        opener = unittest.mock.MagicMock(); opener.open.return_value = response
        with patch.object(settings, "build_opener", return_value=opener) as build:
            self.assertEqual(settings.lmstudio_json(policy()["api_base"], native=True), metadata())
        self.assertEqual(build.call_args.args[0].proxies, {})
        with self.assertRaisesRegex(RouterError, "redirect_refused"):
            build.call_args.args[1].redirect_request(None, None, 302, None, None, "https://example.invalid/")
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:1234/api/v1/models")
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(request.get_header("Authorization"))


if __name__ == "__main__":
    unittest.main()
