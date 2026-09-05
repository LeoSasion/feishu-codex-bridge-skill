from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from operator_core.final_callback import FinalCallbackStore, FinalCallbackStoreError  # noqa: E402
from operator_core.beeper_relay import BeeperRelayClient  # noqa: E402
import final_callback_mcp_server as mcp  # noqa: E402
import relay_mcp_server as relay_mcp  # noqa: E402
import routing_cli  # noqa: E402


class FinalCallbackStoreTests(unittest.TestCase):
    def test_relay_preserves_unicode_and_only_one_consumer_wins(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "callbacks.sqlite3"
            stores = [FinalCallbackStore(path) for _ in range(4)]
            original = '原句 20260905 "quotes" \\path\nemoji 🚀\r\n'
            stores[0].open("a" * 32, "event", "exact-task",
                           relay_prompt=original, responder_host_id="local")
            with ThreadPoolExecutor(max_workers=4) as executor:
                results = list(executor.map(lambda s: s.take_relay("a" * 32), stores))
            self.assertEqual([{"threadId": "exact-task", "hostId": "local", "prompt": original}],
                             [r for r in results if r is not None])
            self.assertIsNone(FinalCallbackStore(path).take_relay("a" * 32))
            with self.assertRaises(FinalCallbackStoreError):
                stores[0].open("a" * 32, "event", "exact-task", relay_prompt=original)
            self.assertTrue(stores[0].submit("a" * 32, "answer")["accepted"])

    def test_closed_captured_and_unknown_routes_never_release_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = FinalCallbackStore(Path(temporary) / "callbacks.sqlite3")
            self.assertIsNone(store.take_relay("a" * 32))
            for ending in ("close", "settle", "submit"):
                request_id = {"close": "a", "settle": "b", "submit": "c"}[ending] * 32
                store.open(request_id, ending, "task", relay_prompt="original")
                if ending == "submit":
                    store.submit(request_id, "answer")
                else:
                    getattr(store, ending)(request_id)
                self.assertIsNone(store.take_relay(request_id))
                store.close(request_id)
            with closing(sqlite3.connect(store.path)) as c:
                self.assertEqual(0, c.execute("SELECT count(*) FROM final_callback_requests "
                                             "WHERE relay_payload IS NOT NULL").fetchone()[0])

    @unittest.skipUnless(shutil.which("node"), "JavaScript execution requires Node")
    def test_generated_code_passes_exact_object_and_rejects_slow_or_uncertain_preparation(self) -> None:
        envelope = {"threadId": "exact-task", "hostId": "local",
                    "prompt": '原句 "2026"\n🚀\u2028\u2029\x00";calls.push("INJECTED");// `${tools}`'}
        prompt = BeeperRelayClient._relay_prompt(request_id="a" * 32)
        code = prompt[prompt.index("const started="):]
        self.assertEqual(4, len(code.splitlines()))
        self.assertNotIn("if(", code)
        self.assertNotIn("dispatch", code)
        self.assertNotIn("send_message", code)
        prepared = {"structuredContent": {"code": relay_mcp.relay_program(envelope)}}
        for elapsed, result, expected in (
            (150, prepared, [envelope]),
            (2000, prepared, [envelope]),
            (2001, prepared, []),
            (-1, prepared, []),
            (150, {"isError": True}, []),
            (150, {"isError": True, "structuredContent": {"ok": False, "error": "unavailable"}}, []),
            (150, {"structuredContent": {"code": relay_mcp.relay_program(None)}}, []),
            (150, {"structuredContent": {"ok": True, "dispatch": envelope}}, []),
            (150, {"content": [{"text": "truncated"}]}, []),
        ):
            with self.subTest(elapsed=elapsed, result=result):
                harness = (
                    "const calls=[];let ticks=0;Date.now=()=>ticks++*" + str(elapsed) + ";"
                    "const text=()=>{};const tools={mcp__feishu_operator_relay__take_relay:async()=>(" + json.dumps(result) + "),"
                    "mcp__codex_app__send_message_to_thread:async(p)=>{calls.push(p);return {isError:false};}};"
                    "const ALL_TOOLS=Object.keys(tools).map(name=>({name}));"
                    "try{" + code + "}catch(e){} console.log(JSON.stringify(calls));"
                )
                output = subprocess.run([shutil.which("node"), "--input-type=module", "-e", harness],
                                        capture_output=True, encoding="utf-8", check=True, timeout=5)
                self.assertEqual(expected, json.loads(output.stdout))

    @unittest.skipUnless(shutil.which("node"), "JavaScript execution requires Node")
    def test_server_program_resolves_send_and_never_retries_missing_or_uncertain_send(self) -> None:
        prompt = BeeperRelayClient._relay_prompt(request_id="a" * 32)
        code = prompt[prompt.index("const started="):]
        envelope = {"threadId": "exact-task", "hostId": "local", "prompt": "原句"}
        result = json.dumps({"structuredContent": {"code": relay_mcp.relay_program(envelope)}})
        for mode in ("unique", "missing", "send_ambiguous", "send_missing", "send_error", "send_throws"):
            with self.subTest(mode=mode):
                harness = (
                    "const calls=[];const text=()=>{};const tools={"
                    "live_namespace__take_relay:async()=>{calls.push('take');return " + result + ";},"
                    "desktop_namespace__send_message_to_thread:async(p)=>{calls.push(p);return {};}};"
                    "let ALL_TOOLS=Object.keys(tools).map(name=>({name}));"
                    + {"unique": "", "missing": "ALL_TOOLS=[];",
                       "send_ambiguous": "ALL_TOOLS.push({name:'other__send_message_to_thread'});",
                       "send_missing": "ALL_TOOLS=ALL_TOOLS.slice(0,1);",
                       "send_error": "tools.desktop_namespace__send_message_to_thread=async(p)=>{calls.push(p);return {isError:true};};",
                       "send_throws": "tools.desktop_namespace__send_message_to_thread=async(p)=>{calls.push(p);throw Error('uncertain');};"}[mode]
                    + "try{" + code + "}catch(e){} console.log(JSON.stringify(calls));"
                )
                output = subprocess.run([shutil.which("node"), "--input-type=module", "-e", harness],
                                        capture_output=True, encoding="utf-8", check=True, timeout=5)
                expected = [] if mode == "missing" else ["take"]
                if mode in {"unique", "send_error", "send_throws"}:
                    expected.append(envelope)
                self.assertEqual(expected, json.loads(output.stdout))

    def test_server_rejects_malformed_dispatch_before_building_program(self) -> None:
        for dispatch in ({}, {"prompt": "only"}, {"threadId": "task", "hostId": "local", "prompt": 7},
                         {"threadId": "task", "hostId": "local", "prompt": "text", "model": "override"}):
            with self.subTest(dispatch=dispatch), self.assertRaises(mcp.FinalCallbackError):
                relay_mcp.relay_program(dispatch)

    def test_first_exact_answer_wins_without_authentication(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = FinalCallbackStore(Path(temporary) / "callbacks.sqlite3")
            request_id = "a" * 32
            store.open(request_id, "event-1", "task-1")

            self.assertEqual(
                {"accepted": True, "state": "captured"},
                store.submit(request_id, "  完整回复\n"),
            )
            self.assertEqual("  完整回复\n", store.result(request_id).final_answer)
            self.assertEqual(
                {"accepted": True, "state": "duplicate"},
                store.submit(request_id, "  完整回复\n"),
            )
            self.assertEqual(
                {"accepted": False, "state": "conflict"},
                store.submit(request_id, "另一个回复"),
            )

    def test_unknown_public_request_id_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = FinalCallbackStore(Path(temporary) / "callbacks.sqlite3")
            self.assertEqual(
                {"accepted": False, "state": "unknown"},
                store.submit("b" * 32, "reply"),
            )


class FinalCallbackMcpTests(unittest.TestCase):
    def test_relay_server_is_separate_and_consumption_is_not_read_only(self) -> None:
        self.assertEqual(["take_relay"], [tool["name"] for tool in relay_mcp.TOOLS])
        self.assertFalse(relay_mcp.TOOLS[0]["annotations"]["readOnlyHint"])
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            store = FinalCallbackStore(runtime / "callbacks.sqlite3")
            store.open("a" * 32, "event", "task", relay_prompt="原句")
            with patch.object(relay_mcp, "_verified_runtime", return_value=(runtime, None)):
                self.assertEqual({"code": relay_mcp.relay_program({"threadId": "task", "hostId": "local", "prompt": "原句"})},
                                 relay_mcp.call_tool("take_relay", {"request_id": "a" * 32}))
                self.assertEqual({"code": relay_mcp.relay_program(None)},
                                 relay_mcp.call_tool("take_relay", {"request_id": "a" * 32}))
                with self.assertRaises(mcp.FinalCallbackError):
                    relay_mcp.call_tool("submit_final_callback", {"request_id": "a" * 32})

    def test_server_exposes_only_routing_tool(self) -> None:
        self.assertEqual(["submit_final_callback"], [tool["name"] for tool in mcp.TOOLS])
        schema = mcp.TOOLS[0]["inputSchema"]
        self.assertEqual(["request_id", "final_answer"], schema["required"])
        self.assertNotIn("final_callback_capability", json.dumps(schema))

    def test_tool_returns_answer_free_routing_result(self) -> None:
        with patch.object(mcp, "_invoke", return_value={"accepted": True, "state": "captured"}):
            result = mcp._call_tool(
                "submit_final_callback",
                {"request_id": "c" * 32, "final_answer": "用户可见回复"},
            )
        self.assertEqual({"ok": True, "accepted": True, "state": "captured"}, result)
        self.assertNotIn("用户可见回复", json.dumps(result, ensure_ascii=False))

    def test_registered_runtime_routes_callback_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            runtime = base / "runtime"
            core = runtime / "operator_core"
            core.mkdir(parents=True)
            sources = {
                "routing_cli.py": ROOT / "scripts" / "routing_cli.py",
                "operator_core/__init__.py": ROOT / "scripts" / "operator_core" / "__init__.py",
                "operator_core/final_callback.py": ROOT / "scripts" / "operator_core" / "final_callback.py",
            }
            hashes = {}
            for relative, source in sources.items():
                target = runtime / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                hashes[relative] = hashlib.sha256(target.read_bytes()).hexdigest()
            (runtime / "runtime-manifest.json").write_text(
                json.dumps({"schema_version": 1, "code_files": hashes}),
                encoding="utf-8",
            )
            request_id = "d" * 32
            FinalCallbackStore(runtime / "callbacks.sqlite3").open(
                request_id, "event", "task"
            )
            with patch.dict(os.environ, {"LOCALAPPDATA": str(base / "local")}), patch.object(
                mcp, "_registry_path", routing_cli._registry_path
            ):
                legacy_registry = routing_cli._registry_path()
                legacy_registry.parent.mkdir(parents=True)
                legacy_registry.write_text(
                    json.dumps({"schema_version": 1, "runtime_dir": str(runtime.resolve())}),
                    encoding="utf-8",
                )
                self.assertFalse(routing_cli._status(runtime.resolve())["runtime_valid"])
                routing_cli._register(runtime.resolve(), force=False)
                result = mcp._call_tool(
                    "submit_final_callback",
                    {"request_id": request_id, "final_answer": "完整回复"},
                )
                store = FinalCallbackStore(runtime / "callbacks.sqlite3")
                store.open("e" * 32, "relay-stdio", "exact-task", relay_prompt="原句 🚀")
                wire = "\n".join(json.dumps(item) for item in [
                    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                    {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
                        "name": "take_relay", "arguments": {"request_id": "e" * 32}}},
                ]) + "\n"
                process = subprocess.run([sys.executable, "-I", "-S", "-B", "-u",
                    str(ROOT / "scripts" / "relay_mcp_server.py")], input=wire,
                    capture_output=True, encoding="utf-8", timeout=5, check=True)
                responses = [json.loads(line)["result"] for line in process.stdout.splitlines()]
                self.assertEqual(["take_relay"], [t["name"] for t in responses[1]["tools"]])
                self.assertEqual({"code": relay_mcp.relay_program({"threadId": "exact-task", "hostId": "local", "prompt": "原句 🚀"})},
                                 responses[2]["structuredContent"])
                self.assertIsNone(store.take_relay("e" * 32))
            self.assertEqual(
                {"ok": True, "accepted": True, "state": "captured"}, result
            )


if __name__ == "__main__":
    unittest.main()
