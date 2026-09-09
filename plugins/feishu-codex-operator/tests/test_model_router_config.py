import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from operator_core import model_router_config as config
from operator_core.model_registry import RouterError


class RouterConfigTests(unittest.TestCase):
    def test_v2_append_preserves_legacy_routes_and_refuses_replacement(self):
        from test_responses_tools import ROUTE
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            config.initialize(state)
            old = {k: v for k, v in ROUTE.items() if k != "responses"}
            config.register_route(state, old)
            adapted = {**ROUTE, "slug": "api/adapted"}
            config.register_route(state, adapted)
            value = json.loads((state / "registry.json").read_text())
            self.assertEqual(value["version"], 2)
            self.assertEqual(value["models"], [{**old, "responses": None}, adapted])
            before = (state / "registry.json").read_bytes()
            config.register_route(state, old)
            config.register_route(state, adapted)
            self.assertEqual(before, (state / "registry.json").read_bytes())
            with self.assertRaises(RouterError):
                config.register_route(state, {**adapted, "model": "different"})
            self.assertEqual(before, (state / "registry.json").read_bytes())

    def test_lmstudio_can_explicitly_append_v2_capabilities(self):
        from test_responses_tools import CAPABILITIES
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            config.initialize(state)
            with patch.object(config, "lmstudio_models", return_value=["fixture"]):
                config.register_lmstudio(state, model="fixture", slug="local/fixture",
                    api_base="http://127.0.0.1:1234/v1", key_env="", context_window=8192,
                    efforts=["low"], responses=CAPABILITIES)
            value = json.loads((state / "registry.json").read_text())
            self.assertEqual(value["version"], 2)
            self.assertEqual(value["models"][0]["responses"], CAPABILITIES)

    def test_registration_file_is_bounded_and_duplicate_keys_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "registration.json"
            for data in (b'{"model":"one","model":"two"}', b"x" * 1048577):
                path.write_bytes(data)
                with self.assertRaises(RouterError):
                    config.read_registration(path)

    def test_lmstudio_refuses_non_loopback_and_credential_urls(self):
        for base in ("https://example.com/v1", "http://localhost/v1", "http://user@127.0.0.1/v1",
                     "http://127.0.0.1/v1?key=secret", "http://127.0.0.1/chat/completions"):
            with self.subTest(base=base), self.assertRaises(RouterError):
                config.lmstudio_models(base)

    def test_lmstudio_registration_preserves_and_converges(self):
        with tempfile.TemporaryDirectory() as root:
            state = Path(root)
            config.initialize(state)
            kwargs = dict(model="local-model", slug="local/lmstudio", api_base="http://127.0.0.1:1234/v1",
                          key_env="", context_window=8192, efforts=["none"])
            with patch.object(config, "lmstudio_models", return_value=["local-model"]):
                config.register_lmstudio(state, **kwargs)
                before = (state / "registry.json").read_bytes()
                config.register_lmstudio(state, **kwargs)
                self.assertEqual(before, (state / "registry.json").read_bytes())
                with self.assertRaises(RouterError):
                    config.register_lmstudio(state, **{**kwargs, "context_window": 16384})
                self.assertEqual(before, (state / "registry.json").read_bytes())
                self.assertFalse((state / "registry-edit.lock").exists())
                (state / "codex-entry.json").write_text("{}")
                with self.assertRaises(RouterError):
                    config.register_lmstudio(state, **kwargs)

    def test_lmstudio_discovery_is_bounded_and_metadata_only(self):
        from unittest.mock import MagicMock
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'{"data":[{"id":"a"}]}'
        opener = MagicMock()
        opener.open.return_value = response
        with patch.object(config, "build_opener", return_value=opener):
            self.assertEqual(config.lmstudio_models("http://127.0.0.1:1234/v1"), ["a"])
            request = opener.open.call_args.args[0]
            self.assertEqual(request.get_method(), "GET")
            self.assertFalse(request.has_header("Authorization"))
            self.assertEqual(request.full_url, "http://127.0.0.1:1234/v1/models")
            response.read.assert_called_once_with(1048577)
            response.read.return_value = b'x' * 1048577
            with self.assertRaises(RouterError):
                config.lmstudio_models("http://127.0.0.1:1234/v1")

    def test_switches_invalidate_cache_with_recoverable_backups(self):
        with tempfile.TemporaryDirectory() as root:
            state, target = Path(root) / "state", Path(root) / "config.toml"
            cache = Path(root) / "models_cache.json"
            config.initialize(state)
            target.write_bytes(b'model="native"\n')
            cache.write_bytes(b"original catalog")
            with patch.object(config, "health", return_value={}):
                config.activate(state, 4317, target)
                self.assertFalse(cache.exists())
                cache.write_bytes(b"augmented catalog")
                config.activate(state, 4317, target)
                self.assertEqual(cache.read_bytes(), b"augmented catalog")
            config.deactivate(state, target)
            self.assertFalse(cache.exists())
            self.assertEqual({p.read_bytes() for p in Path(root).glob("models_cache.router-backup-*.json")},
                             {b"original catalog", b"augmented catalog"})

    def test_config_failure_restores_cache(self):
        with tempfile.TemporaryDirectory() as root:
            target, cache = Path(root) / "config.toml", Path(root) / "models_cache.json"
            target.write_bytes(b"original")
            cache.write_bytes(b"catalog")
            with patch.object(config, "atomic_write", side_effect=OSError("test")), self.assertRaises(OSError):
                config.write_entry(target, b"changed")
            self.assertEqual(target.read_bytes(), b"original")
            self.assertEqual(cache.read_bytes(), b"catalog")

    def test_concurrent_cache_is_not_overwritten_on_failure(self):
        with tempfile.TemporaryDirectory() as root:
            target, cache = Path(root) / "config.toml", Path(root) / "models_cache.json"
            cache.write_bytes(b"original")
            def fail(*_args):
                cache.write_bytes(b"concurrent")
                raise OSError("test")
            with patch.object(config, "atomic_write", side_effect=fail), self.assertRaises(OSError):
                config.write_entry(target, b"changed")
            self.assertEqual(cache.read_bytes(), b"concurrent")
            self.assertEqual([p.read_bytes() for p in Path(root).glob("models_cache.router-backup-*.json")], [b"original"])

    def test_non_file_cache_blocks_config_change(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "config.toml"
            (Path(root) / "models_cache.json").mkdir()
            with self.assertRaises(RouterError):
                config.write_entry(target, b"changed")
            self.assertFalse(target.exists())

    def test_activation_and_rollback_preserve_later_unrelated_edits(self):
        with tempfile.TemporaryDirectory() as root:
            state = Path(root) / "state"
            target = Path(root) / "config.toml"
            target.write_bytes(b'model = "native"\r\n[features]\r\nfoo = true\r\n')
            before = target.read_bytes()
            config.initialize(state)
            token = (state / "token").read_bytes()
            config.initialize(state)
            self.assertEqual(token, (state / "token").read_bytes())
            with patch.object(config, "health", return_value={"status": "ready"}):
                config.activate(state, 4317, target)
                config.activate(state, 4317, target)
            target.write_bytes(target.read_bytes() + b'bar = false\r\n')
            config.deactivate(state, target)
            self.assertEqual(before + b'bar = false\r\n', target.read_bytes())

    def test_existing_route_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as root:
            state = Path(root) / "state"
            target = Path(root) / "config.toml"
            config.initialize(state)
            for text in ('openai_base_url="https://example.org"\n',
                         'model_provider="other"\n', 'model_catalog_json="custom.json"\n'):
                target.write_text(text)
                with patch.object(config, "health", return_value={"status": "ready"}), self.assertRaises(RouterError):
                    config.activate(state, 4317, target)
                self.assertEqual(text, target.read_text())

    def test_changed_owned_prefix_blocks_rollback(self):
        with tempfile.TemporaryDirectory() as root:
            state = Path(root) / "state"
            target = Path(root) / "config.toml"
            config.initialize(state)
            with patch.object(config, "health", return_value={"status": "ready"}):
                config.activate(state, 4317, target)
            target.write_text("# user edited\n" + target.read_text())
            before = target.read_bytes()
            with self.assertRaises(RouterError):
                config.deactivate(state, target)
            self.assertEqual(before, target.read_bytes())
