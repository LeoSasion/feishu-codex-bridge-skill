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
                browser = Request(config.url(state, port) + "/lifecycle", data=b"",
                    headers={"Origin": "http://untrusted.example"})
                with self.assertRaises(HTTPError) as error:
                    build_opener(ProxyHandler({})).open(browser, timeout=2)
                self.assertEqual(403, error.exception.code)
                error.exception.close()
                config.activate(state, port, state / "test-config.toml")
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
