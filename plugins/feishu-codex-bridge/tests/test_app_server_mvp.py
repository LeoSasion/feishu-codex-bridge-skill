from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "app_server_mvp.py"
SPEC = importlib.util.spec_from_file_location("app_server_mvp", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("app_server_mvp module could not be loaded")
APP_SERVER_MVP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(APP_SERVER_MVP)


class ScriptedTransport:
    def __init__(self, responses: list[dict | str | None]) -> None:
        self._responses = list(responses)
        self.writes: list[str] = []
        self.write_timeouts: list[float] = []
        self.read_timeouts: list[float] = []

    def write_line(self, line: str, timeout_seconds: float) -> None:
        self.writes.append(line)
        self.write_timeouts.append(timeout_seconds)

    def read_line(self, max_bytes: int, timeout_seconds: float) -> str | None:
        self.read_timeouts.append(timeout_seconds)
        if not self._responses:
            return None
        value = self._responses.pop(0)
        if value is None:
            return None
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=True, separators=(",", ":")) + "\n"


def _response(sequence: int, result) -> dict:
    return {"id": f"bridge:test:{sequence}", "result": result}


def _successful_responses(*, control_visible: bool = False) -> list[dict]:
    control_id = "control-thread"
    listed_id = control_id if control_visible else "desktop-thread"
    return [
        _response(
            1,
            {
                "codexHome": "opaque-home",
                "platformFamily": "windows",
                "platformOs": "windows",
                "userAgent": "test",
            },
        ),
        {"method": "thread/started", "params": {"thread": {"id": control_id}}},
        _response(
            2,
            {"thread": {"ephemeral": True, "id": control_id, "path": None}},
        ),
        _response(
            3,
            {
                "data": [
                    {
                        "name": "unrelated",
                        "runtimeStatus": "connected",
                        "tools": {},
                    }
                ],
                "nextCursor": "page-2",
            },
        ),
        _response(
            4,
            {
                "data": [
                    {
                        "name": "codex_app",
                        "runtimeStatus": "connected",
                        "tools": {
                            "list_threads": {},
                            "list_projects": {},
                        },
                    }
                ],
                "nextCursor": None,
            },
        ),
        _response(
            5,
            {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "pinnedThreads": [],
                                "threads": [{"id": listed_id}],
                            }
                        ),
                    }
                ]
            },
        ),
        _response(
            6,
            {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "projects": [
                                    {"id": "project-id", "root": "not-exported"}
                                ]
                            }
                        ),
                    }
                ]
            },
        ),
    ]


class AppServerMvpTests(unittest.TestCase):
    def _run(self, transport: ScriptedTransport):
        with tempfile.TemporaryDirectory() as temp_dir:
            return APP_SERVER_MVP.run_read_only_probe(
                transport,
                cwd=Path(temp_dir),
                epoch="test",
            )

    def test_success_uses_one_ephemeral_thread_and_two_read_only_tools(self) -> None:
        transport = ScriptedTransport(_successful_responses())
        result = self._run(transport)

        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["schema_version"], 2)
        self.assertEqual(result["failure_phase"], "none")
        self.assertTrue(result["control_thread_started"])
        self.assertTrue(result["control_thread_ephemeral"])
        self.assertTrue(result["codex_app_connected"])
        self.assertTrue(result["list_threads_result_valid"])
        self.assertTrue(result["list_projects_result_valid"])
        self.assertTrue(result["control_thread_hidden_from_desktop_catalog"])
        self.assertFalse(result["model_turn_started"])
        self.assertFalse(result["responder_mutation_attempted"])
        self.assertFalse(result["queue_claimed"])
        self.assertFalse(result["activation_allowed"])
        self.assertFalse(result["desktop_task_coordination_certified"])
        self.assertFalse(result["runtime_attestation_passed"])
        self.assertEqual(result["issues"], [])

        frames = [json.loads(line) for line in transport.writes]
        self.assertEqual(
            [frame["method"] for frame in frames],
            [
                "initialize",
                "initialized",
                "thread/start",
                "mcpServerStatus/list",
                "mcpServerStatus/list",
                "mcpServer/tool/call",
                "mcpServer/tool/call",
            ],
        )
        self.assertEqual(frames[2]["params"]["ephemeral"], True)
        self.assertEqual(frames[2]["params"]["sandbox"], "read-only")
        self.assertEqual(frames[5]["params"]["arguments"], {"limit": 50})
        self.assertEqual(frames[5]["params"]["tool"], "list_threads")
        self.assertEqual(frames[6]["params"]["tool"], "list_projects")
        self.assertTrue(transport.read_timeouts)
        self.assertTrue(
            all(
                0 < timeout <= APP_SERVER_MVP.REQUEST_TIMEOUT_SECONDS
                for timeout in transport.read_timeouts
            )
        )
        self.assertTrue(transport.write_timeouts)
        self.assertTrue(
            all(
                0 < timeout <= APP_SERVER_MVP.REQUEST_TIMEOUT_SECONDS
                for timeout in transport.write_timeouts
            )
        )

    def test_safe_result_never_contains_catalog_or_owned_thread_values(self) -> None:
        transport = ScriptedTransport(_successful_responses())
        result = self._run(transport)
        wire = json.dumps(result, ensure_ascii=True, sort_keys=True)
        for secret in ("control-thread", "desktop-thread", "project-id", "not-exported"):
            self.assertNotIn(secret, wire)

    def test_server_request_fails_closed_before_thread_start(self) -> None:
        transport = ScriptedTransport(
            [{"id": "server-1", "method": "approval/request", "params": {}}]
        )
        result = self._run(transport)
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["issues"], ["server_request_unsupported"])
        self.assertEqual(result["failure_phase"], "initialize")
        self.assertFalse(result["control_thread_started"])
        self.assertFalse(result["control_thread_ephemeral"])
        self.assertEqual(len(transport.writes), 1)

    def test_remote_error_is_answer_free_and_not_retried(self) -> None:
        transport = ScriptedTransport(
            [
                {
                    "id": "bridge:test:1",
                    "error": {"code": -32000, "message": "sensitive remote text"},
                }
            ]
        )
        result = self._run(transport)
        self.assertEqual(result["issues"], ["server_request_failed"])
        self.assertNotIn("sensitive", json.dumps(result))
        self.assertEqual(len(transport.writes), 1)

    def test_mismatched_response_id_fails_closed(self) -> None:
        transport = ScriptedTransport([{"id": "old-epoch", "result": {}}])
        result = self._run(transport)
        self.assertEqual(result["issues"], ["server_response_id_mismatch"])

    def test_incomplete_or_oversized_frame_fails_closed(self) -> None:
        incomplete = ScriptedTransport(["{}"])
        result = self._run(incomplete)
        self.assertEqual(result["issues"], ["server_frame_incomplete"])

        oversized_line = (" " * APP_SERVER_MVP.MAX_FRAME_BYTES) + "\n"
        oversized = ScriptedTransport([oversized_line])
        result = self._run(oversized)
        self.assertEqual(result["issues"], ["server_frame_too_large"])

    def test_missing_tool_and_mcp_tool_error_fail_closed(self) -> None:
        missing = _successful_responses()
        missing[4]["result"]["data"][0]["tools"].pop("list_projects")
        result = self._run(ScriptedTransport(missing))
        self.assertEqual(result["issues"], ["list_projects_unavailable"])
        self.assertEqual(result["failure_phase"], "tool_catalog")
        self.assertFalse(result["list_projects_available"])

        tool_error = _successful_responses()
        tool_error[5]["result"] = {"content": [], "isError": True}
        result = self._run(ScriptedTransport(tool_error))
        self.assertEqual(result["issues"], ["mcp_tool_returned_error"])
        self.assertEqual(result["failure_phase"], "list_threads")
        self.assertFalse(result["list_threads_result_valid"])

        malformed_catalog = _successful_responses()
        malformed_catalog[4]["result"]["data"][0]["tools"] = []
        result = self._run(ScriptedTransport(malformed_catalog))
        self.assertEqual(result["issues"], ["codex_app_tools_invalid"])
        self.assertEqual(result["failure_phase"], "tool_catalog")
        self.assertTrue(result["codex_app_connected"])

    def test_dynamic_json_rejects_duplicate_members_and_non_finite_values(self) -> None:
        duplicate_frame = ScriptedTransport(
            ['{"id":"bridge:test:1","id":"bridge:test:1","result":{}}\n']
        )
        result = self._run(duplicate_frame)
        self.assertEqual(result["issues"], ["server_frame_invalid_json"])
        self.assertEqual(result["failure_phase"], "initialize")

        non_finite_frame = ScriptedTransport(
            ['{"id":"bridge:test:1","result":{"value":NaN}}\n']
        )
        result = self._run(non_finite_frame)
        self.assertEqual(result["issues"], ["server_frame_invalid_json"])
        self.assertEqual(result["failure_phase"], "initialize")

        duplicate_tool_payload = _successful_responses()
        duplicate_tool_payload[5]["result"]["content"][0]["text"] = (
            '{"pinnedThreads":[],"threads":[],"threads":[]}'
        )
        result = self._run(ScriptedTransport(duplicate_tool_payload))
        self.assertEqual(result["issues"], ["mcp_tool_text_invalid_json"])
        self.assertEqual(result["failure_phase"], "list_threads")

        deeply_nested_frame = ScriptedTransport(
            [
                '{"id":"bridge:test:1","result":'
                + ("[" * 1500)
                + "0"
                + ("]" * 1500)
                + "}\n"
            ]
        )
        result = self._run(deeply_nested_frame)
        self.assertEqual(result["issues"], ["server_frame_invalid_json"])
        self.assertEqual(result["failure_phase"], "initialize")

    def test_control_thread_must_not_enter_desktop_catalog(self) -> None:
        result = self._run(ScriptedTransport(_successful_responses(control_visible=True)))
        self.assertEqual(
            result["issues"],
            ["control_thread_visible_in_desktop_catalog"],
        )
        self.assertFalse(result["control_thread_hidden_from_desktop_catalog"])
        self.assertFalse(result["list_threads_result_valid"])

    def test_catalog_items_require_unique_nonempty_ids(self) -> None:
        missing_thread_id = _successful_responses()
        missing_thread_id[5]["result"]["content"][0]["text"] = json.dumps(
            {"pinnedThreads": [], "threads": [{}]}
        )
        result = self._run(ScriptedTransport(missing_thread_id))
        self.assertEqual(result["issues"], ["list_threads_result_invalid"])
        self.assertFalse(result["list_threads_result_valid"])

        duplicate_thread_id = _successful_responses()
        duplicate_thread_id[5]["result"]["content"][0]["text"] = json.dumps(
            {
                "pinnedThreads": [{"id": "desktop-thread"}],
                "threads": [{"id": "desktop-thread"}],
            }
        )
        result = self._run(ScriptedTransport(duplicate_thread_id))
        self.assertEqual(result["issues"], ["list_threads_result_invalid"])

        missing_project_id = _successful_responses()
        missing_project_id[6]["result"]["content"][0]["text"] = json.dumps(
            {"projects": [{}]}
        )
        result = self._run(ScriptedTransport(missing_project_id))
        self.assertEqual(result["issues"], ["list_projects_result_invalid"])
        self.assertFalse(result["list_projects_result_valid"])

    def test_thread_start_must_confirm_ephemeral_ownership(self) -> None:
        responses = _successful_responses()
        responses[2]["result"]["thread"]["ephemeral"] = False
        result = self._run(ScriptedTransport(responses))
        self.assertEqual(result["issues"], ["thread_start_result_invalid"])
        self.assertFalse(result["control_thread_started"])
        self.assertFalse(result["control_thread_ephemeral"])

        path_omitted = _successful_responses()
        path_omitted[2]["result"]["thread"].pop("path")
        result = self._run(ScriptedTransport(path_omitted))
        self.assertEqual(result["status"], "pass")
        self.assertTrue(result["control_thread_ephemeral"])

        persisted = _successful_responses()
        persisted[2]["result"]["thread"]["path"] = "persisted-rollout"
        result = self._run(ScriptedTransport(persisted))
        self.assertEqual(result["issues"], ["thread_start_result_invalid"])
        self.assertFalse(result["control_thread_started"])
        self.assertFalse(result["control_thread_ephemeral"])

    def test_parameter_allowlist_rejects_mutation_before_write(self) -> None:
        transport = ScriptedTransport(
            [
                _successful_responses()[0],
                _response(
                    2,
                    {
                        "thread": {
                            "ephemeral": True,
                            "id": "control-thread",
                            "path": None,
                        }
                    },
                ),
            ]
        )
        session = APP_SERVER_MVP.JsonLineRpcSession(transport, epoch="test")
        session.request(
            "initialize",
            {
                "capabilities": {"experimentalApi": False},
                "clientInfo": {
                    "name": "feishu-codex-bridge-app-server-probe",
                    "version": "1",
                },
            },
        )
        session.notify("initialized")
        writes_before_unsafe_start = len(transport.writes)
        with self.assertRaises(APP_SERVER_MVP.AppServerProtocolError) as start_error:
            session.request(
                "thread/start",
                {
                    "approvalPolicy": "never",
                    "cwd": "opaque",
                    "ephemeral": False,
                    "sandbox": "read-only",
                },
            )
        self.assertEqual(start_error.exception.reason, "thread_start_params_not_allowed")
        self.assertEqual(len(transport.writes), writes_before_unsafe_start)

        session.request(
            "thread/start",
            {
                "approvalPolicy": "never",
                "cwd": "opaque",
                "ephemeral": True,
                "sandbox": "read-only",
            },
        )
        writes_before_unsafe_tool = len(transport.writes)
        with self.assertRaises(APP_SERVER_MVP.AppServerProtocolError) as tool_error:
            session.request(
                "mcpServer/tool/call",
                {
                    "arguments": {"prompt": "not allowed"},
                    "server": "codex_app",
                    "threadId": "control-thread",
                    "tool": "send_message",
                },
            )
        self.assertEqual(tool_error.exception.reason, "mcp_tool_params_not_allowed")
        self.assertEqual(len(transport.writes), writes_before_unsafe_tool)

    def test_source_has_no_launcher_queue_or_responder_mutation_surface(self) -> None:
        source = SCRIPT_PATH.read_text(encoding="utf-8")
        for forbidden in (
            "subprocess",
            "Popen",
            "codex app-server",
            "CODEX_APP_TOOLS_PIPE_PATH",
            "thread/resume",
            "turn/start",
            "thread/compact/start",
            "send_message_to_thread",
            "beeper_queue_cli",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
