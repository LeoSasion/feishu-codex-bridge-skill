from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "app_server_contract.py"
SPEC = importlib.util.spec_from_file_location("app_server_contract", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("app_server_contract module could not be loaded")
APP_SERVER_CONTRACT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(APP_SERVER_CONTRACT)


class AppServerContractTests(unittest.TestCase):
    def _write_fixture(
        self,
        root: Path,
        *,
        omitted_method: str | None = None,
        pipe_declared: bool = True,
        thread_metadata_required: bool = True,
        send_approval: str = "prompt",
    ) -> tuple[Path, Path, Path]:
        schema_root = root / "protocol"
        (schema_root / "v2").mkdir(parents=True)
        (schema_root / "v1").mkdir(parents=True)
        methods = [
            method
            for method in APP_SERVER_CONTRACT.REQUIRED_CLIENT_METHODS
            if method != omitted_method
        ]
        client_request = {
            "oneOf": [
                {"properties": {"method": {"enum": [method]}}}
                for method in methods
            ]
        }
        (schema_root / "ClientRequest.json").write_text(
            json.dumps(client_request), encoding="utf-8"
        )
        (schema_root / "v2" / "McpServerToolCallParams.json").write_text(
            json.dumps(
                {
                    "properties": {"arguments": {}},
                    "required": ["server", "threadId", "tool"],
                }
            ),
            encoding="utf-8",
        )
        (schema_root / "v2" / "ThreadCompactStartParams.json").write_text(
            json.dumps({"required": ["threadId"]}), encoding="utf-8"
        )
        (schema_root / "ClientNotification.json").write_text(
            json.dumps(
                {
                    "oneOf": [
                        {
                            "properties": {"method": {"enum": ["initialized"]}},
                            "required": ["method"],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        (schema_root / "JSONRPCRequest.json").write_text(
            json.dumps(
                {
                    "properties": {
                        "id": {},
                        "method": {"type": "string"},
                        "params": {},
                    },
                    "required": ["id", "method"],
                }
            ),
            encoding="utf-8",
        )
        (schema_root / "JSONRPCResponse.json").write_text(
            json.dumps(
                {
                    "properties": {"id": {}, "result": {}},
                    "required": ["id", "result"],
                }
            ),
            encoding="utf-8",
        )
        (schema_root / "JSONRPCError.json").write_text(
            json.dumps(
                {
                    "definitions": {
                        "JSONRPCErrorError": {"required": ["code", "message"]}
                    },
                    "properties": {"error": {}, "id": {}},
                    "required": ["error", "id"],
                }
            ),
            encoding="utf-8",
        )
        (schema_root / "JSONRPCNotification.json").write_text(
            json.dumps(
                {
                    "properties": {"method": {"type": "string"}, "params": {}},
                    "required": ["method"],
                }
            ),
            encoding="utf-8",
        )
        (schema_root / "v1" / "InitializeParams.json").write_text(
            json.dumps(
                {
                    "definitions": {
                        "ClientInfo": {"required": ["name", "version"]}
                    },
                    "properties": {"clientInfo": {"type": "object"}},
                    "required": ["clientInfo"],
                }
            ),
            encoding="utf-8",
        )
        (schema_root / "v2" / "ThreadStartParams.json").write_text(
            json.dumps(
                {
                    "properties": {
                        "approvalPolicy": {},
                        "cwd": {"type": ["string", "null"]},
                        "ephemeral": {"type": ["boolean", "null"]},
                        "sandbox": {},
                    }
                }
            ),
            encoding="utf-8",
        )
        (schema_root / "v2" / "ThreadStartResponse.json").write_text(
            json.dumps(
                {
                    "definitions": {
                        "Thread": {
                            "properties": {
                                "ephemeral": {"type": "boolean"},
                                "path": {"type": ["string", "null"]},
                            },
                            "required": ["ephemeral", "id"],
                        }
                    },
                    "properties": {"thread": {"$ref": "#/definitions/Thread"}},
                    "required": ["thread"],
                }
            ),
            encoding="utf-8",
        )
        (schema_root / "v2" / "ListMcpServerStatusParams.json").write_text(
            json.dumps(
                {
                    "definitions": {
                        "McpServerStatusDetail": {
                            "enum": ["full", "toolsAndAuthOnly"]
                        }
                    },
                    "properties": {
                        "cursor": {"type": ["string", "null"]},
                        "detail": {},
                        "limit": {"type": ["integer", "null"]},
                        "threadId": {"type": ["string", "null"]},
                    }
                }
            ),
            encoding="utf-8",
        )
        (schema_root / "v2" / "ListMcpServerStatusResponse.json").write_text(
            json.dumps(
                {
                    "definitions": {
                        "McpServerStatus": {
                            "properties": {
                                "name": {"type": "string"},
                                "runtimeStatus": {},
                                "tools": {"type": "object"},
                            },
                            "required": ["name", "tools"],
                        }
                    },
                    "properties": {"data": {"type": "array"}},
                    "required": ["data"],
                }
            ),
            encoding="utf-8",
        )
        (schema_root / "v2" / "McpServerToolCallResponse.json").write_text(
            json.dumps(
                {
                    "properties": {
                        "content": {"type": "array"},
                        "isError": {"type": ["boolean", "null"]},
                    },
                    "required": ["content"],
                }
            ),
            encoding="utf-8",
        )

        manifest_path = root / "desktop-mcp.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "codex_app": {
                            "enabled": True,
                            "env_vars": ["CODEX_APP_TOOLS_PIPE_PATH"]
                            if pipe_declared
                            else [],
                            "tools": {
                                "send_message_to_thread": {
                                    "approval_mode": send_approval
                                }
                            },
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        metadata_marker = (
            "Codex app tools require thread metadata from the executor."
            if thread_metadata_required
            else "thread metadata check removed"
        )
        server_path = root / "server.mjs"
        server_path.write_text(
            '\n'.join(
                (
                    'var PIPE_PATH_ENV_VAR = "CODEX_APP_TOOLS_PIPE_PATH";',
                    "const pipePath = process.env[PIPE_PATH_ENV_VAR];",
                    "Codex did not provide ${PIPE_PATH_ENV_VAR} to the app tools MCP.",
                    'var INTERACTION_CLIENT_ID_ARGUMENT = "--interaction-client-id";',
                    metadata_marker,
                    "threadId,",
                    'getHostClient().request("tools/list", {});',
                    'getHostClient().request(\n      "tools/call", {});',
                )
            ),
            encoding="utf-8",
        )
        return schema_root, manifest_path, server_path

    def _audit(self, root: Path, **kwargs):
        schema_root, manifest, server = self._write_fixture(root, **kwargs)
        return APP_SERVER_CONTRACT.audit_contract(
            schema_root=schema_root,
            desktop_mcp_manifest=manifest,
            desktop_mcp_server=server,
        )

    def test_matching_static_contract_passes_but_never_activates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = self._audit(Path(temp_dir))
        self.assertEqual(result["status"], "pass")
        self.assertTrue(result["mcp_direct_tool_call_available"])
        self.assertTrue(result["native_compaction_available"])
        self.assertFalse(result["plain_compact_is_native_compaction"])
        self.assertFalse(result["model_turn_required_for_tool_call"])
        self.assertTrue(result["read_only_mvp_protocol_available"])
        self.assertTrue(result["ephemeral_thread_start_shape_available"])
        self.assertTrue(result["ephemeral_thread_path_nullable"])
        self.assertTrue(result["mcp_status_shape_available"])
        self.assertTrue(result["mcp_tool_response_shape_available"])
        self.assertTrue(result["jsonl_rpc_envelopes_available"])
        self.assertFalse(result["desktop_task_coordination_certified"])
        self.assertFalse(result["activation_allowed"])
        self.assertIn("runtime_attestation_missing", result["activation_blockers"])
        self.assertEqual(result["issues"], [])

    def test_static_contract_drift_cases_fail_closed(self) -> None:
        fixture_cases = (
            (
                "missing_protocol_method",
                {"omitted_method": "mcpServer/tool/call"},
                "protocol_method_missing:mcpServer/tool/call",
                ("mcp_direct_tool_call_available", "activation_allowed"),
            ),
            (
                "pipe_declaration",
                {"pipe_declared": False},
                "codex_app_pipe_env_not_declared",
                ("app_tools_pipe_required",),
            ),
            (
                "executor_thread_metadata",
                {"thread_metadata_required": False},
                "codex_app_thread_metadata_requirement_missing",
                ("app_tools_thread_metadata_required",),
            ),
            (
                "send_approval",
                {"send_approval": "auto"},
                "send_message_approval_contract_changed",
                (),
            ),
        )
        for case, fixture_kwargs, expected_issue, false_fields in fixture_cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temp_dir:
                result = self._audit(Path(temp_dir), **fixture_kwargs)
                self.assertEqual(result["status"], "fail")
                self.assertIn(expected_issue, result["issues"])
                for field in false_fields:
                    self.assertFalse(result[field])

        with self.subTest(case="missing_mvp_schema_shape"):
            with tempfile.TemporaryDirectory() as temp_dir:
                schema_root, manifest, server = self._write_fixture(Path(temp_dir))
                (
                    schema_root / "v2" / "ListMcpServerStatusResponse.json"
                ).write_text(
                    json.dumps({"properties": {"items": {"type": "array"}}}),
                    encoding="utf-8",
                )
                result = APP_SERVER_CONTRACT.audit_contract(
                    schema_root=schema_root,
                    desktop_mcp_manifest=manifest,
                    desktop_mcp_server=server,
                )
            self.assertEqual(result["status"], "fail")
            self.assertIn("mcp_status_contract_changed", result["issues"])
            self.assertFalse(result["read_only_mvp_protocol_available"])

        with self.subTest(case="ephemeral_thread_path_nullable"):
            with tempfile.TemporaryDirectory() as temp_dir:
                schema_root, manifest, server = self._write_fixture(Path(temp_dir))
                response_path = schema_root / "v2" / "ThreadStartResponse.json"
                response = json.loads(response_path.read_text(encoding="utf-8"))
                response["definitions"]["Thread"]["properties"]["path"] = {
                    "type": "string"
                }
                response_path.write_text(
                    json.dumps(response), encoding="utf-8"
                )
                result = APP_SERVER_CONTRACT.audit_contract(
                    schema_root=schema_root,
                    desktop_mcp_manifest=manifest,
                    desktop_mcp_server=server,
                )
            self.assertEqual(result["status"], "fail")
            self.assertFalse(result["ephemeral_thread_path_nullable"])
            self.assertIn(
                "ephemeral_thread_start_contract_changed", result["issues"]
            )

    def test_static_json_rejects_duplicate_members_and_non_finite_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            schema_root, manifest, server = self._write_fixture(Path(temp_dir))
            (schema_root / "ClientRequest.json").write_text(
                '{"oneOf":[],"oneOf":[]}',
                encoding="utf-8",
            )
            duplicate = APP_SERVER_CONTRACT.audit_contract(
                schema_root=schema_root,
                desktop_mcp_manifest=manifest,
                desktop_mcp_server=server,
            )
            self.assertIn("client_request_schema_unreadable", duplicate["issues"])
            self.assertFalse(duplicate["activation_allowed"])

        with tempfile.TemporaryDirectory() as temp_dir:
            schema_root, manifest, server = self._write_fixture(Path(temp_dir))
            manifest.write_text(
                '{"mcpServers":{},"mcpServers":{}}',
                encoding="utf-8",
            )
            duplicate_manifest = APP_SERVER_CONTRACT.audit_contract(
                schema_root=schema_root,
                desktop_mcp_manifest=manifest,
                desktop_mcp_server=server,
            )
            self.assertIn(
                "desktop_mcp_manifest_unreadable",
                duplicate_manifest["issues"],
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            schema_root, manifest, server = self._write_fixture(Path(temp_dir))
            (schema_root / "ClientRequest.json").write_text(
                '{"oneOf":NaN}',
                encoding="utf-8",
            )
            non_finite = APP_SERVER_CONTRACT.audit_contract(
                schema_root=schema_root,
                desktop_mcp_manifest=manifest,
                desktop_mcp_server=server,
            )
            self.assertIn("client_request_schema_unreadable", non_finite["issues"])
            self.assertFalse(non_finite["activation_allowed"])

    def test_auditor_cannot_launch_app_server_or_another_process(self) -> None:
        source = SCRIPT_PATH.read_text(encoding="utf-8")
        for forbidden in ("subprocess", "Popen", "Start-Process", "codex app-server"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
