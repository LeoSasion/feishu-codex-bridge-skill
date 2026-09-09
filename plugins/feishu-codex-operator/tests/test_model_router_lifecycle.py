"""Detached router lifecycle against isolated state; no native requests."""

import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError, URLError
from urllib.request import Request, ProxyHandler, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import operator_model_router as service
from operator_core import model_router_config as config


@unittest.skipUnless(importlib.util.find_spec("aiohttp"), "optional router environment required")
class RouterLifecycleTests(unittest.TestCase):
    def test_registration_port_reservation_refuses_live_listener_and_releases_after_use(self):
        with socket.socket() as live:
            live.bind(("127.0.0.1", 0))
            live.listen()
            port = live.getsockname()[1]
            with self.assertRaisesRegex(ValueError, "port_in_use"):
                with service.reserve_inactive_port(port):
                    self.fail("reserved a live listener")
        with service.reserve_inactive_port(port):
            with self.assertRaisesRegex(ValueError, "port_in_use"):
                with service.reserve_inactive_port(port):
                    self.fail("reserved the same port twice")
        with service.reserve_inactive_port(port):
            pass

    def test_readiness_never_starts_or_sends_and_reports_configuration_ownership(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory(prefix="operator-router-readiness-") as directory:
            state = Path(directory)
            config.initialize(state)
            target = state / "synthetic-config.toml"
            target.write_text('model="native"\n', encoding="utf-8")
            before = {p.name: p.read_bytes() for p in state.iterdir() if p.is_file()}
            with patch.object(service, "control", side_effect=OSError), patch.object(service, "start") as start:
                result = service.readiness(state, 4317, target)
                start.assert_not_called()
            self.assertEqual(result["entry"], "native_deactivated")
            self.assertEqual(result["service"], "unavailable")
            self.assertFalse(result["ready_for_global_activation"])
            self.assertEqual(result["upstream_requests"], 0)
            self.assertEqual(before, {p.name: p.read_bytes() for p in state.iterdir() if p.is_file()})

    def test_detached_start_converges_and_stop_requires_deactivation(self):
        with tempfile.TemporaryDirectory(prefix="operator-router-service-") as directory:
            state = Path(directory)
            config.initialize(state)
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            command = [sys.executable, service.__file__, "start", "--state-dir", directory, "--port", str(port)]
            try:
                first = subprocess.run(command, capture_output=True, text=True, timeout=20, check=True)
                started = json.loads(first.stdout)
                second = subprocess.run(command, capture_output=True, text=True, timeout=20, check=True)
                self.assertEqual(started["pid"], json.loads(second.stdout)["pid"])
                self.assertEqual("ready", service.control(state, port)["status"])
                before = {name: (state / name).read_bytes() for name in ("token", "registry.json")}
                result = subprocess.run([*command[:2], "restart", *command[3:]],
                                        capture_output=True, text=True, timeout=20, check=True)
                restarted = json.loads(result.stdout)
                self.assertNotEqual(started["pid"], restarted["pid"])
                self.assertEqual(before, {name: (state / name).read_bytes() for name in before})
                browser = Request(config.url(state, port) + "/lifecycle", data=b"",
                    headers={"Origin": "http://untrusted.example"})
                with self.assertRaises(HTTPError) as error:
                    build_opener(ProxyHandler({})).open(browser, timeout=2)
                self.assertEqual(403, error.exception.code)
                error.exception.close()
                config.activate(state, port, state / "test-config.toml")
                # The live reload command changes memory only, even with the
                # owned entry active; it neither stops the process nor rewrites config.
                import hashlib
                target = state / "test-config.toml"
                config_before = target.read_bytes()
                journal_before = (state / "codex-entry.json").read_bytes()
                row = {"slug": "local/reload-test", "display_name": "Reload fixture",
                       "model": "fixture", "api_base": "http://127.0.0.1:1/v1", "api_key_env": "",
                       "context_window": 32000, "reasoning_efforts": ["low"]}
                raw = json.dumps({"version": 1, "models": [row]}).encode()
                config.atomic_write(state / "registry.json", raw)
                digest = hashlib.sha256(raw).hexdigest()
                reload_command = [*command[:2], "reload-registry", *command[3:], "--expected-registry-sha256", digest]
                outcome = subprocess.run(reload_command, capture_output=True, text=True, timeout=20, check=True)
                reloaded = json.loads(outcome.stdout)
                self.assertTrue(reloaded["changed"])
                self.assertEqual(reloaded["registered_models"], 1)
                self.assertEqual(reloaded["pid"], restarted["pid"])
                self.assertEqual(service.control(state, port)["diagnostics"]["registry_sha256"], digest)
                self.assertFalse(service.reload_registry(state, port, digest)["changed"])
                self.assertEqual(target.read_bytes(), config_before)
                self.assertEqual((state / "codex-entry.json").read_bytes(), journal_before)
                self.assertEqual((state / "registry.json").read_bytes(), raw)
                with self.assertRaisesRegex(ValueError, "deactivate_before_router_restart"):
                    service.restart(state, port)
                with self.assertRaises(HTTPError) as error:
                    service.control(state, port, stop=True)
                self.assertEqual(409, error.exception.code)
                error.exception.close()
                self.assertEqual("ready", service.control(state, port)["status"])
                config.deactivate(state, state / "test-config.toml")
            finally:
                if (state / "codex-entry.json").exists():
                    config.deactivate(state, state / "test-config.toml")
                try:
                    service.control(state, port, stop=True)
                except URLError:
                    pass
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    try:
                        service.control(state, port)
                    except URLError:
                        break
                    time.sleep(0.1)
                else:
                    self.fail("owned router did not stop")


if __name__ == "__main__":
    unittest.main()
