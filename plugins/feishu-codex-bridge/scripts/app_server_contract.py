from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
REQUIRED_CLIENT_METHODS = (
    "initialize",
    "mcpServer/tool/call",
    "mcpServerStatus/list",
    "thread/compact/start",
    "thread/list",
    "thread/loaded/list",
    "thread/read",
    "thread/resume",
    "thread/start",
    "turn/start",
)
MCP_TOOL_CALL_REQUIRED_FIELDS = frozenset(("server", "threadId", "tool"))
COMPACT_REQUIRED_FIELDS = frozenset(("threadId",))
INITIALIZE_REQUIRED_FIELDS = frozenset(("clientInfo",))
MCP_STATUS_RESPONSE_REQUIRED_FIELDS = frozenset(("data",))
MCP_TOOL_RESPONSE_REQUIRED_FIELDS = frozenset(("content",))
THREAD_READ_REQUIRED_FIELDS = frozenset(("threadId",))
THREAD_READ_FIELDS = frozenset(("includeTurns", "threadId"))
THREAD_READ_RESPONSE_REQUIRED_FIELDS = frozenset(("thread",))
CLIENT_REQUEST_REQUIRED_FIELDS = frozenset(("id", "method", "params"))
THREAD_OBSERVER_REQUIRED_FIELDS = frozenset(("id", "status", "turns"))
TURN_OBSERVER_REQUIRED_FIELDS = frozenset(("id", "items", "status"))
MCP_OBSERVER_ITEM_REQUIRED_FIELDS = frozenset(
    ("arguments", "id", "server", "status", "tool", "type")
)
THREAD_STATUS_TYPES = frozenset(("active", "idle", "notLoaded", "systemError"))
TURN_STATUS_TYPES = frozenset(("completed", "failed", "inProgress", "interrupted"))
MCP_TOOL_CALL_STATUS_TYPES = frozenset(("completed", "failed", "inProgress"))
JSONRPC_REQUEST_REQUIRED_FIELDS = frozenset(("id", "method"))
JSONRPC_RESPONSE_REQUIRED_FIELDS = frozenset(("id", "result"))
JSONRPC_ERROR_REQUIRED_FIELDS = frozenset(("error", "id"))
JSONRPC_NOTIFICATION_REQUIRED_FIELDS = frozenset(("method",))
THREAD_START_FIELDS = frozenset(("approvalPolicy", "cwd", "ephemeral", "sandbox"))
MCP_STATUS_FIELDS = frozenset(("cursor", "detail", "limit", "threadId"))


def _reject_duplicate_json_members(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON member")
        value[key] = item
    return value


def _reject_non_finite_json_constant(value: str) -> Any:
    raise ValueError(f"non-finite JSON constant: {value}")


def load_strict_json_value(path: Path) -> Any:
    """Load one static JSON document without duplicate or non-finite values."""

    try:
        return json.loads(
            path.resolve(strict=True).read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_members,
            parse_constant=_reject_non_finite_json_constant,
        )
    except RecursionError as exc:
        raise ValueError("JSON nesting exceeds the supported limit") from exc


def _load_json(path: Path, issue: str, issues: list[str]) -> dict[str, Any]:
    try:
        value = load_strict_json_value(path)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        issues.append(issue)
        return {}
    if not isinstance(value, dict):
        issues.append(issue)
        return {}
    return value


def _load_text(path: Path, issue: str, issues: list[str]) -> str:
    try:
        return path.resolve(strict=True).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        issues.append(issue)
        return ""


def _collect_methods(value: Any, methods: set[str]) -> None:
    if isinstance(value, dict):
        properties = value.get("properties")
        method_schema = properties.get("method") if isinstance(properties, dict) else None
        if isinstance(method_schema, dict):
            enum = method_schema.get("enum")
            if isinstance(enum, list):
                methods.update(item for item in enum if isinstance(item, str))
            const = method_schema.get("const")
            if isinstance(const, str):
                methods.add(const)
        for child in value.values():
            _collect_methods(child, methods)
    elif isinstance(value, list):
        for child in value:
            _collect_methods(child, methods)


def _required_fields(value: dict[str, Any]) -> frozenset[str]:
    required = value.get("required")
    if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
        return frozenset()
    return frozenset(required)


def _property_names(value: dict[str, Any]) -> frozenset[str]:
    properties = value.get("properties")
    if not isinstance(properties, dict):
        return frozenset()
    return frozenset(key for key in properties if isinstance(key, str))


def _property_has_type(value: dict[str, Any], name: str, expected: str) -> bool:
    properties = value.get("properties")
    prop = properties.get(name) if isinstance(properties, dict) else None
    if not isinstance(prop, dict):
        return False
    declared = prop.get("type")
    if isinstance(declared, str):
        return declared == expected
    if isinstance(declared, list):
        return expected in declared
    return False


def _schema_has_exact_type(value: Any, expected: str) -> bool:
    return isinstance(value, dict) and value.get("type") == expected


def _property_has_exact_type(
    value: dict[str, Any], name: str, expected: str
) -> bool:
    properties = value.get("properties")
    prop = properties.get(name) if isinstance(properties, dict) else None
    return _schema_has_exact_type(prop, expected)


def _definition(value: dict[str, Any], name: str) -> dict[str, Any] | None:
    definitions = value.get("definitions")
    definition = definitions.get(name) if isinstance(definitions, dict) else None
    return definition if isinstance(definition, dict) else None


def _property_schema(value: dict[str, Any], name: str) -> dict[str, Any] | None:
    properties = value.get("properties")
    prop = properties.get(name) if isinstance(properties, dict) else None
    return prop if isinstance(prop, dict) else None


def _schema_refers_to(value: Any, name: str) -> bool:
    if not isinstance(value, dict):
        return False
    expected = f"#/definitions/{name}"
    if value.get("$ref") == expected:
        return True
    all_of = value.get("allOf")
    return (
        isinstance(all_of, list)
        and len(all_of) == 1
        and isinstance(all_of[0], dict)
        and all_of[0].get("$ref") == expected
    )


def _property_refers_to(value: dict[str, Any], field: str, name: str) -> bool:
    return _schema_refers_to(_property_schema(value, field), name)


def _array_property_refers_to(
    value: dict[str, Any], field: str, name: str
) -> bool:
    prop = _property_schema(value, field)
    return (
        _schema_has_exact_type(prop, "array")
        and isinstance(prop.get("items"), dict)
        and prop["items"].get("$ref") == f"#/definitions/{name}"
    )


def _thread_item_variant(
    thread_item: dict[str, Any] | None, item_type: str
) -> dict[str, Any] | None:
    one_of = thread_item.get("oneOf") if isinstance(thread_item, dict) else None
    if not isinstance(one_of, list):
        return None
    matches = []
    for candidate in one_of:
        if not isinstance(candidate, dict):
            continue
        type_schema = _property_schema(candidate, "type")
        if (
            _schema_has_exact_type(type_schema, "string")
            and type_schema.get("enum") == [item_type]
        ):
            matches.append(candidate)
    return matches[0] if len(matches) == 1 else None


def _client_request_variant(
    client_request: dict[str, Any], method: str
) -> dict[str, Any] | None:
    one_of = client_request.get("oneOf")
    if not isinstance(one_of, list):
        return None
    matches = []
    for candidate in one_of:
        if not isinstance(candidate, dict):
            continue
        method_schema = _property_schema(candidate, "method")
        if (
            _schema_has_exact_type(method_schema, "string")
            and method_schema.get("enum") == [method]
        ):
            matches.append(candidate)
    return matches[0] if len(matches) == 1 else None


def _definition_string_enum_is_exact(
    value: dict[str, Any], name: str, expected: frozenset[str]
) -> bool:
    definition = _definition(value, name)
    if not _schema_has_exact_type(definition, "string"):
        return False
    enum = definition.get("enum")
    return (
        isinstance(enum, list)
        and len(enum) == len(expected)
        and all(isinstance(item, str) for item in enum)
        and frozenset(enum) == expected
    )


def _definition_object_union_types_are_exact(
    value: dict[str, Any], name: str, expected: frozenset[str]
) -> bool:
    definition = _definition(value, name)
    one_of = definition.get("oneOf") if isinstance(definition, dict) else None
    if not isinstance(one_of, list) or len(one_of) != len(expected):
        return False
    observed: list[str] = []
    for candidate in one_of:
        if (
            not _schema_has_exact_type(candidate, "object")
            or "type" not in _required_fields(candidate)
        ):
            return False
        type_schema = _property_schema(candidate, "type")
        enum = type_schema.get("enum") if isinstance(type_schema, dict) else None
        if (
            not _schema_has_exact_type(type_schema, "string")
            or not isinstance(enum, list)
            or len(enum) != 1
            or not isinstance(enum[0], str)
        ):
            return False
        observed.append(enum[0])
    return len(set(observed)) == len(observed) and frozenset(observed) == expected


def _definition_requires(value: dict[str, Any], name: str, field: str) -> bool:
    definitions = value.get("definitions")
    definition = definitions.get(name) if isinstance(definitions, dict) else None
    return isinstance(definition, dict) and field in _required_fields(definition)


def _definition_property_has_type(
    value: dict[str, Any], name: str, field: str, expected: str
) -> bool:
    definitions = value.get("definitions")
    definition = definitions.get(name) if isinstance(definitions, dict) else None
    return (
        isinstance(definition, dict)
        and _property_has_type(definition, field, expected)
    )


def _definition_has_properties(
    value: dict[str, Any], name: str, fields: frozenset[str]
) -> bool:
    definitions = value.get("definitions")
    definition = definitions.get(name) if isinstance(definitions, dict) else None
    return isinstance(definition, dict) and fields.issubset(_property_names(definition))


def _definition_enum_contains(
    value: dict[str, Any], name: str, expected: str
) -> bool:
    definitions = value.get("definitions")
    definition = definitions.get(name) if isinstance(definitions, dict) else None
    enum = definition.get("enum") if isinstance(definition, dict) else None
    return isinstance(enum, list) and expected in enum


def audit_contract(
    *,
    schema_root: Path,
    desktop_mcp_manifest: Path,
    desktop_mcp_server: Path,
) -> dict[str, Any]:
    issues: list[str] = []
    client_request = _load_json(
        schema_root / "ClientRequest.json",
        "client_request_schema_unreadable",
        issues,
    )
    mcp_tool_call = _load_json(
        schema_root / "v2" / "McpServerToolCallParams.json",
        "mcp_tool_call_schema_unreadable",
        issues,
    )
    compact_start = _load_json(
        schema_root / "v2" / "ThreadCompactStartParams.json",
        "thread_compact_schema_unreadable",
        issues,
    )
    client_notification = _load_json(
        schema_root / "ClientNotification.json",
        "client_notification_schema_unreadable",
        issues,
    )
    jsonrpc_request = _load_json(
        schema_root / "JSONRPCRequest.json",
        "jsonrpc_request_schema_unreadable",
        issues,
    )
    jsonrpc_response = _load_json(
        schema_root / "JSONRPCResponse.json",
        "jsonrpc_response_schema_unreadable",
        issues,
    )
    jsonrpc_error = _load_json(
        schema_root / "JSONRPCError.json",
        "jsonrpc_error_schema_unreadable",
        issues,
    )
    jsonrpc_notification = _load_json(
        schema_root / "JSONRPCNotification.json",
        "jsonrpc_notification_schema_unreadable",
        issues,
    )
    initialize_params = _load_json(
        schema_root / "v1" / "InitializeParams.json",
        "initialize_params_schema_unreadable",
        issues,
    )
    thread_start = _load_json(
        schema_root / "v2" / "ThreadStartParams.json",
        "thread_start_schema_unreadable",
        issues,
    )
    thread_start_response = _load_json(
        schema_root / "v2" / "ThreadStartResponse.json",
        "thread_start_response_schema_unreadable",
        issues,
    )
    mcp_status = _load_json(
        schema_root / "v2" / "ListMcpServerStatusParams.json",
        "mcp_status_schema_unreadable",
        issues,
    )
    mcp_status_response = _load_json(
        schema_root / "v2" / "ListMcpServerStatusResponse.json",
        "mcp_status_response_schema_unreadable",
        issues,
    )
    mcp_tool_response = _load_json(
        schema_root / "v2" / "McpServerToolCallResponse.json",
        "mcp_tool_response_schema_unreadable",
        issues,
    )
    thread_read = _load_json(
        schema_root / "v2" / "ThreadReadParams.json",
        "thread_read_schema_unreadable",
        issues,
    )
    thread_read_response = _load_json(
        schema_root / "v2" / "ThreadReadResponse.json",
        "thread_read_response_schema_unreadable",
        issues,
    )
    desktop_manifest = _load_json(
        desktop_mcp_manifest,
        "desktop_mcp_manifest_unreadable",
        issues,
    )
    desktop_server_text = _load_text(
        desktop_mcp_server,
        "desktop_mcp_server_unreadable",
        issues,
    )

    methods: set[str] = set()
    _collect_methods(client_request, methods)
    method_support = {method: method in methods for method in REQUIRED_CLIENT_METHODS}
    for method, available in method_support.items():
        if not available:
            issues.append(f"protocol_method_missing:{method}")

    mcp_tool_call_shape_exact = (
        _required_fields(mcp_tool_call) == MCP_TOOL_CALL_REQUIRED_FIELDS
    )
    if not mcp_tool_call_shape_exact:
        issues.append("mcp_tool_call_required_fields_changed")

    native_compaction_available = (
        method_support["thread/compact/start"]
        and _required_fields(compact_start) == COMPACT_REQUIRED_FIELDS
    )
    if not native_compaction_available:
        issues.append("thread_compact_contract_changed")

    notification_methods: set[str] = set()
    _collect_methods(client_notification, notification_methods)
    initialized_notification_available = "initialized" in notification_methods
    if not initialized_notification_available:
        issues.append("initialized_notification_missing")

    jsonl_rpc_envelopes_available = (
        _required_fields(jsonrpc_request) == JSONRPC_REQUEST_REQUIRED_FIELDS
        and _required_fields(jsonrpc_response) == JSONRPC_RESPONSE_REQUIRED_FIELDS
        and _required_fields(jsonrpc_error) == JSONRPC_ERROR_REQUIRED_FIELDS
        and _required_fields(jsonrpc_notification)
        == JSONRPC_NOTIFICATION_REQUIRED_FIELDS
        and _property_has_type(jsonrpc_request, "method", "string")
        and "params" in _property_names(jsonrpc_request)
        and _definition_requires(jsonrpc_error, "JSONRPCErrorError", "code")
        and _definition_requires(jsonrpc_error, "JSONRPCErrorError", "message")
    )
    if not jsonl_rpc_envelopes_available:
        issues.append("jsonl_rpc_envelope_contract_changed")

    initialize_shape_available = (
        _required_fields(initialize_params) == INITIALIZE_REQUIRED_FIELDS
        and _definition_requires(initialize_params, "ClientInfo", "name")
        and _definition_requires(initialize_params, "ClientInfo", "version")
    )
    if not initialize_shape_available:
        issues.append("initialize_contract_changed")

    ephemeral_thread_path_nullable = (
        _definition_property_has_type(
            thread_start_response, "Thread", "path", "null"
        )
    )
    thread_start_shape_available = (
        THREAD_START_FIELDS.issubset(_property_names(thread_start))
        and _property_has_type(thread_start, "cwd", "string")
        and _property_has_type(thread_start, "ephemeral", "boolean")
        and "thread" in _required_fields(thread_start_response)
        and _definition_requires(thread_start_response, "Thread", "id")
        and _definition_requires(thread_start_response, "Thread", "ephemeral")
        and _definition_property_has_type(
            thread_start_response, "Thread", "ephemeral", "boolean"
        )
        and ephemeral_thread_path_nullable
    )
    if not thread_start_shape_available:
        issues.append("ephemeral_thread_start_contract_changed")

    mcp_status_shape_available = (
        MCP_STATUS_FIELDS.issubset(_property_names(mcp_status))
        and _definition_enum_contains(
            mcp_status, "McpServerStatusDetail", "toolsAndAuthOnly"
        )
        and _property_has_type(mcp_status, "limit", "integer")
        and _property_has_type(mcp_status, "threadId", "string")
        and _required_fields(mcp_status_response)
        == MCP_STATUS_RESPONSE_REQUIRED_FIELDS
        and _property_has_type(mcp_status_response, "data", "array")
        and _definition_requires(mcp_status_response, "McpServerStatus", "name")
        and _definition_requires(mcp_status_response, "McpServerStatus", "tools")
        and _definition_has_properties(
            mcp_status_response,
            "McpServerStatus",
            frozenset(("name", "runtimeStatus", "tools")),
        )
    )
    if not mcp_status_shape_available:
        issues.append("mcp_status_contract_changed")

    mcp_tool_response_shape_available = (
        "arguments" in _property_names(mcp_tool_call)
        and
        _required_fields(mcp_tool_response) == MCP_TOOL_RESPONSE_REQUIRED_FIELDS
        and _property_has_type(mcp_tool_response, "content", "array")
        and _property_has_type(mcp_tool_response, "isError", "boolean")
    )
    if not mcp_tool_response_shape_available:
        issues.append("mcp_tool_response_contract_changed")

    thread_schema = _definition(thread_read_response, "Thread")
    turn_schema = _definition(thread_read_response, "Turn")
    thread_item_schema = _definition(thread_read_response, "ThreadItem")
    mcp_observer_item = _thread_item_variant(thread_item_schema, "mcpToolCall")
    agent_message_item = _thread_item_variant(thread_item_schema, "agentMessage")
    reasoning_item = _thread_item_variant(thread_item_schema, "reasoning")
    thread_read_request = _client_request_variant(client_request, "thread/read")
    embedded_thread_read = _definition(client_request, "ThreadReadParams")
    thread_read_shape_available = (
        method_support["thread/read"]
        and isinstance(thread_read_request, dict)
        and _schema_has_exact_type(thread_read_request, "object")
        and _required_fields(thread_read_request)
        == CLIENT_REQUEST_REQUIRED_FIELDS
        and _property_refers_to(
            thread_read_request, "params", "ThreadReadParams"
        )
        and isinstance(embedded_thread_read, dict)
        and _schema_has_exact_type(embedded_thread_read, "object")
        and _required_fields(embedded_thread_read)
        == THREAD_READ_REQUIRED_FIELDS
        and _property_names(embedded_thread_read) == THREAD_READ_FIELDS
        and _property_has_exact_type(
            embedded_thread_read, "threadId", "string"
        )
        and _property_has_exact_type(
            embedded_thread_read, "includeTurns", "boolean"
        )
        and _schema_has_exact_type(thread_read, "object")
        and _required_fields(thread_read) == THREAD_READ_REQUIRED_FIELDS
        and _property_names(thread_read) == THREAD_READ_FIELDS
        and _property_has_exact_type(thread_read, "threadId", "string")
        and _property_has_exact_type(thread_read, "includeTurns", "boolean")
        and _schema_has_exact_type(thread_read_response, "object")
        and _required_fields(thread_read_response)
        == THREAD_READ_RESPONSE_REQUIRED_FIELDS
        and _property_refers_to(thread_read_response, "thread", "Thread")
        and isinstance(thread_schema, dict)
        and _schema_has_exact_type(thread_schema, "object")
        and THREAD_OBSERVER_REQUIRED_FIELDS.issubset(
            _required_fields(thread_schema)
        )
        and _property_has_exact_type(thread_schema, "id", "string")
        and _property_refers_to(thread_schema, "status", "ThreadStatus")
        and _definition_object_union_types_are_exact(
            thread_read_response, "ThreadStatus", THREAD_STATUS_TYPES
        )
        and _array_property_refers_to(thread_schema, "turns", "Turn")
        and isinstance(turn_schema, dict)
        and _schema_has_exact_type(turn_schema, "object")
        and TURN_OBSERVER_REQUIRED_FIELDS.issubset(_required_fields(turn_schema))
        and _property_has_exact_type(turn_schema, "id", "string")
        and _property_refers_to(turn_schema, "status", "TurnStatus")
        and _definition_string_enum_is_exact(
            thread_read_response, "TurnStatus", TURN_STATUS_TYPES
        )
        and _array_property_refers_to(turn_schema, "items", "ThreadItem")
        and isinstance(mcp_observer_item, dict)
        and MCP_OBSERVER_ITEM_REQUIRED_FIELDS.issubset(
            _required_fields(mcp_observer_item)
        )
        and _schema_has_exact_type(mcp_observer_item, "object")
        and _property_has_exact_type(mcp_observer_item, "id", "string")
        and _property_has_exact_type(mcp_observer_item, "server", "string")
        and _property_has_exact_type(mcp_observer_item, "tool", "string")
        and _property_refers_to(
            mcp_observer_item, "status", "McpToolCallStatus"
        )
        and _definition_string_enum_is_exact(
            thread_read_response,
            "McpToolCallStatus",
            MCP_TOOL_CALL_STATUS_TYPES,
        )
    )
    if not thread_read_shape_available:
        issues.append("thread_read_observer_contract_changed")

    # The current thread/read response is useful for an isolated correlation
    # experiment, but it is not a metadata-only API. Its schema admits answer,
    # reasoning, MCP arguments, and MCP results. Filtering those fields after
    # receipt can keep the public receipt answer-free; it cannot prove that the
    # observer process never received content.
    thread_read_content_bearing = any(
        (
            isinstance(agent_message_item, dict)
            and "text" in _property_names(agent_message_item),
            isinstance(reasoning_item, dict)
            and bool(
                frozenset(("content", "summary"))
                & _property_names(reasoning_item)
            ),
            isinstance(mcp_observer_item, dict)
            and bool(
                frozenset(("arguments", "result"))
                & _property_names(mcp_observer_item)
            ),
        )
    )
    metadata_only_projection_available = False
    passive_observer_protocol_available = all(
        (
            initialized_notification_available,
            jsonl_rpc_envelopes_available,
            initialize_shape_available,
            thread_read_shape_available,
        )
    )

    read_only_mvp_protocol_available = all(
        (
            initialized_notification_available,
            jsonl_rpc_envelopes_available,
            initialize_shape_available,
            thread_start_shape_available,
            mcp_status_shape_available,
            mcp_tool_response_shape_available,
            method_support["mcpServer/tool/call"],
            method_support["mcpServerStatus/list"],
            method_support["thread/start"],
        )
    )

    servers = desktop_manifest.get("mcpServers")
    codex_app = servers.get("codex_app") if isinstance(servers, dict) else None
    codex_app_enabled = isinstance(codex_app, dict) and codex_app.get("enabled") is True
    if not codex_app_enabled:
        issues.append("codex_app_server_not_enabled")

    env_vars = codex_app.get("env_vars") if isinstance(codex_app, dict) else None
    manifest_declares_pipe = (
        isinstance(env_vars, list) and "CODEX_APP_TOOLS_PIPE_PATH" in env_vars
    )
    if not manifest_declares_pipe:
        issues.append("codex_app_pipe_env_not_declared")

    tools = codex_app.get("tools") if isinstance(codex_app, dict) else None
    send_tool = tools.get("send_message_to_thread") if isinstance(tools, dict) else None
    send_message_requires_prompt = (
        isinstance(send_tool, dict) and send_tool.get("approval_mode") == "prompt"
    )
    if not send_message_requires_prompt:
        issues.append("send_message_approval_contract_changed")

    pipe_markers = (
        'PIPE_PATH_ENV_VAR = "CODEX_APP_TOOLS_PIPE_PATH"',
        "process.env[PIPE_PATH_ENV_VAR]",
        "Codex did not provide ${PIPE_PATH_ENV_VAR} to the app tools MCP.",
    )
    app_tools_pipe_required = manifest_declares_pipe and all(
        marker in desktop_server_text for marker in pipe_markers
    )
    if not app_tools_pipe_required:
        issues.append("codex_app_pipe_requirement_missing")

    thread_metadata_markers = (
        'var INTERACTION_CLIENT_ID_ARGUMENT = "--interaction-client-id"',
        "Codex app tools require thread metadata from the executor.",
        "threadId,",
    )
    app_tools_thread_metadata_required = all(
        marker in desktop_server_text for marker in thread_metadata_markers
    )
    if not app_tools_thread_metadata_required:
        issues.append("codex_app_thread_metadata_requirement_missing")

    native_forwarding_available = all(
        marker in desktop_server_text
        for marker in ('getHostClient().request("tools/list"', 'getHostClient().request(\n      "tools/call"')
    )
    if not native_forwarding_available:
        issues.append("codex_app_native_forwarding_contract_missing")

    direct_tool_call_available = method_support["mcpServer/tool/call"] and mcp_tool_call_shape_exact
    static_pass = not issues
    activation_blockers = [
        "desktop_task_coordination_uncertified",
        "runtime_attestation_missing",
    ]
    if send_message_requires_prompt:
        activation_blockers.append("mutating_tool_requires_prompt")
    passive_observer_activation_blockers = [
        "metadata_only_observer_projection_missing",
        "cross_process_visibility_unverified",
        "exact_turn_correlation_unverified",
        "no_ui_impact_unverified",
        "runtime_attestation_missing",
    ]

    return {
        "schema_version": SCHEMA_VERSION,
        "command": "app-server-contract",
        "status": "pass" if static_pass else "fail",
        "protocol_methods": method_support,
        "mcp_tool_call_required_fields_exact": mcp_tool_call_shape_exact,
        "native_compaction_available": native_compaction_available,
        "plain_compact_is_native_compaction": False,
        "mcp_direct_tool_call_available": direct_tool_call_available,
        "app_tools_pipe_required": app_tools_pipe_required,
        "app_tools_thread_metadata_required": app_tools_thread_metadata_required,
        "app_tools_native_forwarding_available": native_forwarding_available,
        "send_message_requires_prompt": send_message_requires_prompt,
        "model_turn_required_for_tool_call": False if direct_tool_call_available else None,
        "initialized_notification_available": initialized_notification_available,
        "jsonl_rpc_envelopes_available": jsonl_rpc_envelopes_available,
        "initialize_shape_available": initialize_shape_available,
        "ephemeral_thread_start_shape_available": thread_start_shape_available,
        "ephemeral_thread_path_nullable": ephemeral_thread_path_nullable,
        "mcp_status_shape_available": mcp_status_shape_available,
        "mcp_tool_response_shape_available": mcp_tool_response_shape_available,
        "read_only_mvp_protocol_available": read_only_mvp_protocol_available,
        "thread_read_shape_available": thread_read_shape_available,
        "thread_read_content_bearing": thread_read_content_bearing,
        "passive_observer_protocol_available": passive_observer_protocol_available,
        "metadata_only_projection_available": metadata_only_projection_available,
        "observer_response_objects_closed": False,
        "observer_requires_allowlist_projection": True,
        "observed_runtime_correlation": False,
        "product_caller_turn_attested": False,
        "passive_observer_activation_allowed": False,
        "passive_observer_activation_blockers": passive_observer_activation_blockers,
        "desktop_task_coordination_certified": False,
        "runtime_attestation_required": True,
        "activation_allowed": False,
        "activation_blockers": activation_blockers,
        "issues": sorted(set(issues)),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--schema-root", required=True, type=Path)
    parser.add_argument("--desktop-mcp-manifest", required=True, type=Path)
    parser.add_argument("--desktop-mcp-server", required=True, type=Path)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    result = audit_contract(
        schema_root=args.schema_root,
        desktop_mcp_manifest=args.desktop_mcp_manifest,
        desktop_mcp_server=args.desktop_mcp_server,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=True, separators=(",", ":"), sort_keys=True))
    else:
        print(f"App Server static contract: {result['status']}")
        print("Activation allowed: no")
        print("Passive observer activation allowed: no")
        if result["thread_read_content_bearing"]:
            print("thread/read is content-bearing; no metadata-only projection is approved.")
        if result["issues"]:
            print("Issues: " + ", ".join(result["issues"]))
        print("A separate runtime attestation is required before any live probe.")
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
