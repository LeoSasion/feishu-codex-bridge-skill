"""Pure Responses tool mapping. Never executes, persists or repairs tool input."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
import re

from .responses_capabilities import ResponsesCapabilities, RouterError, UpstreamProtocolError

MAX_ARGUMENT_BYTES = 2 * 1024 * 1024
MAX_TOOLS = 1024
MAX_OUTPUT_ITEMS = 1024
CALL_TYPES = {"function_call", "custom_tool_call", "tool_search_call"}
OUTPUT_TYPES = {"function_call_output", "custom_tool_call_output", "tool_search_output"}
NAMED_OUTPUT_PREFIX = (
    "Codex named function result (protocol data; source metadata is not authentication):\n")
EXEC_GRAMMAR = r"""start: pragma_source | plain_source
pragma_source: PRAGMA_LINE NEWLINE SOURCE
plain_source: SOURCE

PRAGMA_LINE: /[ \t]*\/\/ @exec:[^\r\n]*/
NEWLINE: /\r?\n/
SOURCE: /[\s\S]+/"""


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def loads(value):
    def unique(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise RouterError("duplicate_json_key")
            result[key] = item
        return result

    def invalid_constant(_value):
        raise RouterError("nonfinite_json_value")

    try:
        return json.loads(value, object_pairs_hook=unique, parse_constant=invalid_constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise RouterError("invalid_protocol_json") from exc


def bounded_string(value, *, nonempty=False, maximum=MAX_ARGUMENT_BYTES, field="protocol.string"):
    if not isinstance(value, str) or (nonempty and not value):
        error = RouterError("invalid_protocol_string")
        error.protocol_field = field
        raise error
    try:
        if len(value.encode("utf-8")) > maximum:
            raise RouterError("protocol_string_too_large")
    except UnicodeError as exc:
        raise RouterError("invalid_protocol_unicode") from exc
    return value


def identifier(value):
    return bounded_string(value, nonempty=True, maximum=512, field="protocol.identifier")


def optional_description(value):
    # Null is absence of optional documentation, never executable input.
    # Original tool metadata remains in ToolSpec; only decorated text uses "".
    return "" if value is None else bounded_string(value, field="tools.description")


def tool_name(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
        error = RouterError("invalid_tool_name")
        error.protocol_field = "tools.name"
        error.protocol_code = "tool_definition_name_invalid"
        raise error
    return value


def input_format(tool):
    value = tool.get("format", {"type": "text"})
    if value == {"type": "text"}:
        return "text"
    if (isinstance(value, dict) and set(value) == {"type", "syntax", "definition"}
            and value["type"] == "grammar" and value["syntax"] == "lark"
            and tool["name"] == "exec" and isinstance(value["definition"], str)
            and value["definition"].replace("\r\n", "\n").strip() == EXEC_GRAMMAR):
        return "codex_exec_v1"
    raise RouterError("unsupported_custom_tool_format")


@dataclass(frozen=True)
class ToolSpec:
    kind: str
    name: str
    namespace: str | None
    upstream_name: str
    upstream_type: str
    original: dict
    upstream: dict
    format: str = "text"

    @property
    def key(self):
        return self.kind, self.namespace, self.name

    def check_input(self, value):
        # This approved grammar accepts any nonempty source (optionally a pragma).
        # It is framing validation, not a JavaScript parser or constrained decoding.
        return bounded_string(value, nonempty=self.format == "codex_exec_v1", field="custom_tool.input")


@dataclass(frozen=True)
class HistoricalCustomTool:
    """Explicit history codec, never a declaration of an executable tool."""
    name: str
    namespace: str | None
    upstream_name: str
    upstream_type: str
    format: str
    kind: str = "custom"

    @property
    def key(self):
        return self.kind, self.namespace, self.name

    def check_input(self, value):
        return bounded_string(value, nonempty=True, field="custom_tool.input")


def tool_alias(kind, namespace, name):
    if namespace is not None:
        suffix = hashlib.sha256(dumps((kind, namespace, name)).encode()).hexdigest()[:12]
        return "operator_" + namespace[:16] + "_" + name[:16] + "_" + suffix
    return "operator_tool_search" if kind == "tool_search" else name


@dataclass(frozen=True)
class RequestContext:
    specs: dict
    upstream: dict
    history_calls: frozenset
    choice: str
    selected: tuple | None
    parallel: bool
    capabilities: ResponsesCapabilities
    visible_names: frozenset
    # Dated Responses Lite envelopes, retained verbatim with their input index.
    # These are protocol declarations, never messages, inferred calls or grants.
    input_tool_definitions: tuple = ()

    def find(self, item):
        kind = {"function_call": "function", "custom_tool_call": "custom",
                "tool_search_call": "tool_search"}.get(item.get("type"), item.get("type"))
        name = "tool_search" if kind == "tool_search" else item.get("name")
        spec = self.specs.get((kind, item.get("namespace"), name))
        if spec is None and not self.specs and kind == "custom" and isinstance(name, str):
            namespace = item.get("namespace")
            if namespace is None or isinstance(namespace, str) and namespace:
                key = (namespace + "." if namespace is not None else "") + name
                fmt = dict(self.capabilities.history_custom_tools).get(key)
                if fmt is not None:
                    # No definitions are added to specs/upstream/visible_names.
                    # A nonempty current catalog still requires an exact match.
                    if item.get("status") not in (None, "completed"):
                        raise RouterError("unfinished_tool_call")
                    mode = self.capabilities.custom_mode(name, namespace)
                    return HistoricalCustomTool(name, namespace, tool_alias(kind, namespace, name),
                                                "function" if mode == "wrap" else "custom", fmt)
        if spec is None:
            # Classify a rejected historical identity without returning names,
            # source, arguments or definitions. Never repair or infer a match.
            namespace = item.get("namespace")
            if not isinstance(name, str) or not name:
                reason = "history_tool_name_invalid"
            elif not self.specs:
                reason = "history_tool_definitions_empty"
            elif any(k == kind and n == name for k, ns, n in self.specs):
                reason = "history_tool_namespace_mismatch"
            elif any(ns == namespace and n == name for k, ns, n in self.specs):
                reason = "history_tool_kind_mismatch"
            elif kind == "custom" and isinstance(namespace, (str, type(None))) and self.capabilities.custom_mode(name, namespace):
                reason = "history_registered_custom_not_advertised"
            else:
                reason = "history_unregistered_tool_call"
            error = RouterError("undeclared_tool_call")
            error.protocol_field = "input.tool_call"
            error.protocol_code = reason
            raise error
        return spec


def _compile_tools(payload, caps):
    specs, upstream, visible = {}, {}, {}
    input_definitions = []
    declared_defer = {}

    def add_many(tools, *, loaded=False, namespace=None, description=""):
        if not isinstance(tools, list):
            raise RouterError("invalid_tool_definitions")
        local_names = set()
        for original in tools:
            if not isinstance(original, dict):
                raise RouterError("invalid_tool_definition")
            kind = original.get("type")
            # Hosted built-ins do not have a function name. Classify them before
            # validating names; never drop them or infer an execution backend.
            if not isinstance(kind, str) or kind not in {"namespace", "custom", "function", "tool_search"}:
                error = RouterError("unsupported_tool_type")
                error.protocol_field = "tools.type"
                error.protocol_code = "unsupported_tool_type"
                raise error
            name = "tool_search" if kind == "tool_search" else tool_name(original.get("name"))
            if (kind, name) in local_names:
                raise RouterError("duplicate_tool_definition")
            local_names.add((kind, name))
            if kind == "namespace":
                if namespace is not None or set(original) - {"type", "name", "description", "tools"}:
                    raise RouterError("unsupported_tool_namespace")
                add_many(original.get("tools"), loaded=loaded, namespace=name,
                         description=optional_description(original.get("description")))
                continue
            tool = deepcopy(original)
            defer = tool.pop("defer_loading", False)
            if type(defer) is not bool or (defer and not caps.tool_search):
                raise RouterError("unsupported_deferred_tool")
            fmt, mode = "text", None
            if kind == "custom":
                if set(tool) - {"type", "name", "description", "format"}:
                    raise RouterError("unsupported_custom_tool_fields")
                mode = caps.custom_mode(name, namespace)
                if mode is None:
                    raise RouterError("custom_tool_not_registered")
                fmt = input_format(tool)
            if kind == "function" and not caps.function_tools:
                raise RouterError("function_tools_not_supported")
            if kind == "tool_search":
                if (not caps.tool_search or namespace is not None
                        or tool.get("execution") != "client"
                        or set(tool) - {"type", "execution", "description", "parameters"}):
                    raise RouterError("client_tool_search_not_supported")
                if not isinstance(tool.get("parameters"), dict):
                    raise RouterError("tool_search_parameters_required")
            if kind == "function" and set(tool) - {"type", "name", "description", "parameters", "strict"}:
                raise RouterError("unsupported_function_tool_fields")
            if kind == "function" and not isinstance(tool.get("parameters"), dict):
                raise RouterError("function_parameters_required")
            key = kind, namespace, name
            if not loaded:
                # Successful search results may lift defer_loading, but two
                # declaration sources cannot silently disagree about visibility.
                if key in declared_defer and declared_defer[key] != defer:
                    raise RouterError("conflicting_tool_definition")
                declared_defer[key] = defer
            alias = tool_alias(kind, namespace, name)
            # Keep top-level exec recognizable to the model. Types, not opaque
            # names, carry its wrapper; any real namespace/name collision fails.
            converted = deepcopy(tool)
            converted["name"] = alias
            original_description = optional_description(tool.get("description"))
            if namespace or mode == "wrap" or kind == "tool_search":
                converted["description"] = (
                    "Tool " + ((namespace + ".") if namespace else "") + name + ".\n"
                    + (description + "\n" if description else "") + original_description)
            if mode == "wrap":
                converted.pop("format", None)
                converted.update(type="function", strict=True, parameters={
                    "type": "object", "properties": {"input": {"type": "string",
                        "description": "The actual raw input requested for tool " + name + "."}},
                    "required": ["input"], "additionalProperties": False})
                if "format" in tool:
                    converted["description"] += (
                        "\nInput format metadata (a description of the syntax, not the tool input): "
                        + dumps(tool["format"]))
                converted["description"] += (
                    "\nSet input to the actual caller-requested tool input verbatim. "
                    "Never copy the format metadata or grammar definition into input. "
                    "Do not add a JSON or Markdown wrapper inside that string. "
                    "Encode the outer function arguments as JSON once. Backslashes within the "
                    "raw input belong to the original source: preserve them without interpreting "
                    "source-language escapes. A backslash followed by n inside source code must "
                    "remain those two characters, distinct from an actual LF or CRLF line break.")
            if kind == "tool_search":
                converted.pop("execution")
                converted["type"] = "function"
            spec = ToolSpec(kind, name, namespace, alias, converted["type"], tool, converted, fmt)
            if key in specs:
                # The same deferred definition can recur after client-side loading.
                if specs[key] != spec:
                    raise RouterError("conflicting_tool_definition")
            elif alias in upstream:
                raise RouterError("tool_alias_collision")
            else:
                specs[key], upstream[alias] = spec, spec
            if len(specs) > MAX_TOOLS:
                raise RouterError("too_many_tools")
            if loaded or not defer:
                visible[alias] = converted

    add_many(payload.get("tools", []))
    items = payload.get("input")
    if isinstance(items, list):
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            if item.get("type") == "additional_tools":
                if caps.input_tool_definitions != "additional_tools_v1":
                    raise RouterError("input_tool_definitions_not_supported")
                # Codex 0.153.4 ResponseItem::AdditionalTools is deliberately
                # excluded from its App Server schema. Its wire shape is bound
                # to the matching official protocol/tool_spec sources instead.
                if (set(item) - {"type", "role", "tools", "id"}
                        or item.get("role") != "developer"):
                    raise RouterError("unsupported_input_tool_definition_fields")
                if item.get("id") is not None:
                    identifier(item["id"])
                if len(input_definitions) >= MAX_TOOLS:
                    raise RouterError("too_many_input_tool_definitions")
                add_many(item.get("tools"))
                input_definitions.append((index, deepcopy(item)))
            elif item.get("type") == "tool_search_output":
                if item.get("execution") != "client" or not caps.tool_search:
                    raise RouterError("server_tool_search_not_supported")
                if item.get("status") != "completed":
                    raise RouterError("unfinished_tool_search_output")
                add_many(item.get("tools"), loaded=True)
    return specs, upstream, visible, tuple(input_definitions)


def _content(value, caps, *, tool_output=False):
    if isinstance(value, str):
        bounded_string(value, maximum=16 * 1024 * 1024)
        return
    encode_text_output = tool_output and caps.text_tool_outputs == "json_string"
    if not isinstance(value, list) or (tool_output and not caps.structured_tool_outputs and not encode_text_output):
        raise RouterError("unsupported_structured_content")
    for part in value:
        if not isinstance(part, dict):
            raise RouterError("invalid_content_part")
        kind = part.get("type")
        if kind in {"input_text", "output_text", "text", "refusal"}:
            bounded_string(part.get("refusal") if kind == "refusal" else part.get("text"),
                           maximum=16 * 1024 * 1024, field="content.text")
        elif encode_text_output:
            raise RouterError("text_tool_output_conversion_requires_text_parts")
        elif kind == "input_image" and "image" in caps.input_modalities:
            if not isinstance(part.get("image_url"), str):
                raise RouterError("image_url_required")
        else:
            raise RouterError("unsupported_input_modality")


def _reasoning_item(item):
    """Validate completed textual reasoning without changing optional fields."""
    if item.get("status") not in (None, "completed"):
        raise RouterError("unfinished_reasoning_item")
    for collection, allowed in (("summary", {"summary_text"}),
                                ("content", {"reasoning_text", "text"})):
        if collection not in item:
            continue
        parts = item[collection]
        # Codex 0.153.4 permits absent/null reasoning content, not null summary.
        if collection == "content" and parts is None:
            continue
        if not isinstance(parts, list) or len(parts) > MAX_OUTPUT_ITEMS:
            raise RouterError("invalid_reasoning_content")
        for part in parts:
            if (not isinstance(part, dict) or not isinstance(part.get("type"), str)
                    or part["type"] not in allowed):
                raise RouterError("invalid_reasoning_content")
            try:
                bounded_string(part.get("text"), maximum=16 * 1024 * 1024)
            except RouterError:
                raise RouterError("invalid_reasoning_content") from None


def _named_function_output(item, caps):
    # Current Codex defines named results with an optional/nullable call_id.
    # This explicitly selected codec changes their representation to a user
    # message; it never reconstructs a call or infers authority from the name.
    identity = item.get("namespace"), item.get("name")
    if (identity != ("codex_app", "send_message_to_thread")
            or dict(caps.named_function_outputs).get("codex_app.send_message_to_thread")
            != "user_message_json_v1"):
        raise RouterError("named_function_output_not_registered")
    if set(item) - {"type", "namespace", "name", "call_id", "id", "output",
                    "internal_chat_message_metadata_passthrough"}:
        raise RouterError("unsupported_named_function_output_fields")
    metadata = item.get("internal_chat_message_metadata_passthrough")
    if metadata is not None:
        # Current schema's turn_id and the observed Desktop timestamp are
        # plain correlation data. Preserve them; reject unknown/opaque fields.
        if not isinstance(metadata, dict) or set(metadata) - {"turn_id", "create_time"}:
            raise RouterError("unsupported_named_function_output_metadata")
        if metadata.get("turn_id") is not None:
            identifier(metadata["turn_id"])
        if "create_time" in metadata:
            value = metadata["create_time"]
            if type(value) not in (int, float) or not 0 <= value <= 1e15 or not math.isfinite(value):
                raise RouterError("unsupported_named_function_output_metadata")
    if item.get("id") is not None:
        identifier(item["id"])
    output = item.get("output")
    if isinstance(output, list) and any(not isinstance(part, dict) or part.get("type") not in {
            "input_text", "output_text", "text", "refusal"} for part in output):
        raise RouterError("named_function_output_requires_text_parts")
    _content(output, caps)
    encoded = bounded_string(dumps(item), maximum=16 * 1024 * 1024,
                             field="input.named_function_output")
    # Preserve the entire object, including absent versus null identities,
    # every text part/metadata field and its order. No XML extraction,
    # system/developer message, generated identity, declaration, execution or retry.
    return {"type": "message", "role": "user", "content": [
        {"type": "input_text", "text": NAMED_OUTPUT_PREFIX + encoded}]}


def _history(payload, context):
    items = payload.get("input", [])
    if isinstance(items, str):
        bounded_string(items, maximum=16 * 1024 * 1024)
        return items, frozenset()
    if not isinstance(items, list):
        raise RouterError("invalid_responses_input")
    result, calls, outputs, aliases = [], {}, set(), {}
    caps = context.capabilities
    definition_indices = {index for index, _ in context.input_tool_definitions}
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise RouterError("invalid_history_item")
        kind = item.get("type", "message")
        if item.get("encrypted_content") not in (None, "") or kind == "compaction":
            raise RouterError("opaque_cross_provider_context_not_supported")
        if kind == "additional_tools":
            if index not in definition_indices:
                raise RouterError("input_tool_definitions_not_supported")
            # Already compiled into top-level tools using the same identities,
            # schemas and capability checks. Keep the source envelope in context;
            # do not inject its JSON into a system/developer/user message.
            continue
        entry = deepcopy(item)
        if kind in CALL_TYPES:
            if item.get("status") not in (None, "completed"):
                raise RouterError("unfinished_tool_call")
            spec = context.find(item)
            if spec.upstream_name in aliases and aliases[spec.upstream_name] != spec.key:
                raise RouterError("tool_alias_collision")
            aliases[spec.upstream_name] = spec.key
            call_id = identifier(item.get("call_id"))
            if call_id in calls:
                raise RouterError("duplicate_history_call_id")
            calls[call_id] = spec
            entry.pop("namespace", None)
            entry["name"] = spec.upstream_name
            if spec.kind == "custom":
                spec.check_input(item.get("input"))
                if spec.upstream_type == "function":
                    entry.pop("input")
                    # Encoding source-language backslashes may grow the wire
                    # arguments beyond the raw custom-input bound. Reject before
                    # dispatch; never truncate or reinterpret the original source.
                    entry.update(type="function_call", arguments=bounded_string(
                        dumps({"input": item["input"]}), field="function_call.arguments"))
            elif spec.kind == "tool_search":
                if item.get("execution") != "client" or not isinstance(item.get("arguments"), dict):
                    raise RouterError("invalid_tool_search_history")
                entry.pop("execution")
                entry.update(type="function_call", arguments=bounded_string(
                    dumps(item["arguments"]), field="function_call.arguments"))
            else:
                if not isinstance(loads(bounded_string(item.get("arguments"), field="function_call.arguments")), dict):
                    raise RouterError("function_arguments_must_be_object")
        elif kind in OUTPUT_TYPES:
            if kind == "function_call_output" and item.get("call_id") is None:
                result.append(_named_function_output(item, caps))
                continue
            call_id = identifier(item.get("call_id"))
            spec = calls.get(call_id)
            expected = {"function": "function_call_output", "custom": "custom_tool_call_output",
                        "tool_search": "tool_search_output"}
            if spec is None or call_id in outputs or kind != expected[spec.kind]:
                raise RouterError("unmatched_tool_output")
            if (item.get("name") is not None and item["name"] != spec.name
                    or item.get("namespace") is not None and item["namespace"] != spec.namespace):
                raise RouterError("tool_output_identity_mismatch")
            outputs.add(call_id)
            if item.get("name") is not None:
                entry["name"] = spec.upstream_name
            if item.get("namespace") is not None:
                entry.pop("namespace")
            if kind == "tool_search_output":
                if item.get("execution") != "client":
                    raise RouterError("server_tool_search_not_supported")
                # Definitions are also exposed as real upstream tools by _compile_tools.
                # Preserve their exact data in the function result, not a summary.
                entry = {k: v for k, v in entry.items() if k not in {"tools", "execution"}}
                entry.update(type="function_call_output", output=dumps({"tools": item["tools"]}))
            else:
                _content(item.get("output"), caps, tool_output=True)
                if caps.text_tool_outputs == "json_string" and isinstance(item.get("output"), list):
                    # Preserve every part, metadata field and boundary. Concatenation
                    # would discard structure; images cannot become text implicitly.
                    entry["output"] = dumps(item["output"])
                if spec.upstream_type == "function":
                    entry["type"] = "function_call_output"
        elif kind == "message":
            role = item.get("role")
            if role not in {"system", "developer", "user", "assistant"}:
                raise RouterError("unsupported_message_role")
            if role == "developer" and caps.developer_role != "native":
                if caps.developer_role == "reject":
                    raise RouterError("developer_role_not_supported")
                entry["role"] = caps.developer_role
            _content(item.get("content"), caps)
        elif kind == "reasoning":
            if not caps.reasoning_input:
                raise RouterError("reasoning_history_not_supported")
            _reasoning_item(item)
        else:
            raise RouterError("unsupported_history_item")
        result.append(entry)
    if set(calls) != outputs:
        raise RouterError("tool_output_missing_from_explicit_history")
    return result, frozenset(calls)


def prepare_request(payload, capabilities, reasoning_efforts):
    """Compile once before network I/O. The input object remains unchanged."""
    caps = capabilities
    if not isinstance(payload, dict):
        raise RouterError("invalid_responses_request")
    if "stream" in payload and type(payload["stream"]) is not bool:
        raise RouterError("invalid_stream_flag")
    include = payload.get("include", [])
    if not isinstance(include, list) or any(not isinstance(value, str) for value in include):
        raise RouterError("invalid_responses_include")
    if payload.get("previous_response_id") is not None or payload.get("conversation") is not None:
        raise RouterError("adapted_routes_require_explicit_input_history")
    if payload.get("context_management") or payload.get("background"):
        raise RouterError("unsupported_responses_execution_mode")
    # A request for optional response fields is not itself opaque history.
    # Codex includes this even in a fresh external context. Preserve the request;
    # refuse any actual nonempty opaque output/history rather than strip it.
    reasoning = payload.get("reasoning")
    if reasoning is not None:
        if not isinstance(reasoning, dict) or set(reasoning) - {"effort", "summary"}:
            raise RouterError("unsupported_reasoning_fields")
        if "effort" in reasoning and reasoning["effort"] not in reasoning_efforts:
            raise RouterError("reasoning_effort_not_registered")
        if reasoning.get("summary") is not None and not caps.reasoning_summary:
            raise RouterError("reasoning_summary_not_supported")
    text = payload.get("text")
    if text is not None and (not isinstance(text, dict)
                            or (text.get("verbosity") is not None and not caps.text_verbosity)):
        raise RouterError("text_verbosity_not_supported")
    if "parallel_tool_calls" in payload and type(payload["parallel_tool_calls"]) is not bool:
        raise RouterError("invalid_parallel_tool_calls")
    specs, upstream, visible, input_definitions = _compile_tools(payload, caps)
    choice = payload.get("tool_choice", "auto")
    selected = None
    if isinstance(choice, str):
        if choice not in caps.tool_choice:
            raise RouterError("tool_choice_not_supported")
        wire_choice = choice
    elif isinstance(choice, dict):
        if set(choice) - {"type", "name", "namespace"} or choice.get("type") not in {"function", "custom", "tool_search"}:
            raise RouterError("unsupported_named_tool_choice")
        name = "tool_search" if choice["type"] == "tool_search" else choice.get("name")
        selected = choice["type"], choice.get("namespace"), name
        spec = specs.get(selected)
        if spec is None or caps.named_tool_choice == "reject":
            raise RouterError("named_tool_choice_not_supported")
        visible[spec.upstream_name] = spec.upstream
        if caps.named_tool_choice == "required":
            visible = {spec.upstream_name: spec.upstream}
            wire_choice = "required"
        else:
            wire_choice = {"type": spec.upstream_type, "name": spec.upstream_name}
        choice = "named"
    else:
        raise RouterError("invalid_tool_choice")
    if choice == "required" and not visible:
        raise RouterError("required_tool_choice_without_tools")
    caps.check_tool_choice(choice, reasoning)
    # True permits parallel calls; it does not require more than one. Narrow
    # that permission to the registered endpoint's ability before the first send.
    parallel = caps.parallel_tool_calls and payload.get("parallel_tool_calls", True)
    context = RequestContext(specs, upstream, frozenset(), choice, selected, parallel, caps,
                             frozenset(visible), input_definitions)
    history, history_calls = _history(payload, context)
    prepared = deepcopy(payload)
    prepared["input"] = history
    if "tools" in payload or visible or input_definitions:
        prepared["tools"] = list(visible.values())
    if "tool_choice" in payload or choice == "named":
        prepared["tool_choice"] = wire_choice
    # The model's omitted default must not enable unsupported parallel calls.
    if visible and not caps.parallel_tool_calls:
        prepared["parallel_tool_calls"] = False
    context = RequestContext(specs, upstream, history_calls, choice, selected, parallel, caps,
                             frozenset(visible), input_definitions)
    return prepared, context


def restore_item(item, context):
    if not isinstance(item, dict):
        raise RouterError("invalid_response_item")
    entry = deepcopy(item)
    kind = item.get("type")
    if kind in {"message", "reasoning"}:
        if item.get("encrypted_content") not in (None, ""):
            raise RouterError("opaque_upstream_context_not_supported")
        if kind == "message":
            if item.get("role") != "assistant" or item.get("status", "completed") != "completed":
                raise RouterError("invalid_output_message")
            _content(item.get("content"), context.capabilities)
        else:
            _reasoning_item(item)
        return entry, None
    if kind not in {"function_call", "custom_tool_call"} or item.get("namespace") is not None:
        raise RouterError("unsupported_upstream_output_item")
    spec = context.upstream.get(item.get("name"))
    expected = {"function": "function_call", "custom": "custom_tool_call"}
    if (spec is None or kind != expected[spec.upstream_type]
            or spec.upstream_name not in context.visible_names):
        raise RouterError("unknown_or_mismatched_upstream_tool")
    identifier(item.get("id"))
    identifier(item.get("call_id"))
    if item.get("status", "completed") != "completed":
        raise RouterError("unfinished_tool_call")
    entry["name"] = spec.name
    if spec.namespace:
        entry["namespace"] = spec.namespace
    if kind == "function_call":
        arguments = loads(bounded_string(item.get("arguments"), field="function_call.arguments"))
        if not isinstance(arguments, dict):
            raise RouterError("function_arguments_must_be_object")
        if spec.kind == "custom":
            if set(arguments) != {"input"}:
                error = RouterError("custom_wrapper_requires_exact_input")
                # Fixed shape flags only. Never retain arbitrary argument keys,
                # source, values or an inferred replacement for the missing field.
                error.wrapper_shape = {"field_count": len(arguments), **{
                    "has_" + key: key in arguments for key in
                    ("input", "code", "source", "cmd", "command", "arguments", "action")}}
                raise error
            entry.pop("arguments")
            entry.update(type="custom_tool_call", input=spec.check_input(arguments["input"]))
        elif spec.kind == "tool_search":
            entry.pop("name")
            entry.update(type="tool_search_call", execution="client", arguments=arguments)
    else:
        spec.check_input(item.get("input"))
    return entry, spec


def restore_response(response, context):
    try:
        if (not isinstance(response, dict) or response.get("status") != "completed"
                or response.get("error") is not None or response.get("incomplete_details") is not None
                or not isinstance(response.get("output"), list)
                or len(response["output"]) > MAX_OUTPUT_ITEMS):
            raise RouterError("unsuccessful_or_invalid_response")
        identifier(response.get("id"))
        result, calls, item_ids, chosen = [], set(), set(), []
        for item in response["output"]:
            restored, spec = restore_item(item, context)
            item_id = identifier(item.get("id"))
            if item_id in item_ids:
                raise RouterError("duplicate_output_item_id")
            item_ids.add(item_id)
            if spec is not None:
                call_id = item["call_id"]
                if call_id in calls or call_id in context.history_calls:
                    raise RouterError("duplicate_output_call_id")
                calls.add(call_id)
                chosen.append(spec.key)
            result.append(restored)
        if ((context.choice == "none" and chosen)
                or (context.choice in {"required", "named"} and not chosen)
                or (context.selected is not None and any(k != context.selected for k in chosen))
                or (not context.parallel and len(chosen) > 1)):
            raise RouterError("upstream_violated_tool_choice")
        if context.capabilities.completed_output_policy == "require_message_or_tool" and not chosen:
            # This endpoint explicitly requires a deliverable at the terminal.
            # Reasoning (including textual tool markup) is never an answer or call.
            # Inspect only already-validated message parts; preserve all bytes.
            message_present = False
            for item in result:
                if item.get("type") != "message":
                    continue
                content = item["content"]
                if isinstance(content, str):
                    message_present |= bool(content)
                else:
                    message_present |= any(
                        bool(part.get("refusal") if part["type"] == "refusal" else part.get("text"))
                        for part in content if part["type"] in {"output_text", "text", "refusal"})
            if not message_present:
                raise RouterError("completed_response_without_message_or_tool")
        return {**deepcopy(response), "output": result}
    except (RouterError, TypeError, KeyError, UnicodeError, RecursionError) as exc:
        error = UpstreamProtocolError("invalid_upstream_tool_response_no_retry")
        if isinstance(response, dict):
            status = response.get("status")
            output = response.get("output")
            usage = response.get("usage")
            usage = usage if isinstance(usage, dict) else {}
            tokens = usage.get("output_tokens")
            error.response_state = {
                "status": status if isinstance(status, str) and status in {
                    "completed", "failed", "incomplete", "in_progress", "queued", "cancelled"} else "invalid_or_missing",
                "output_is_list": isinstance(output, list),
                "output_count": len(output) if isinstance(output, list) else None,
                "has_error": response.get("error") is not None,
                "has_incomplete_details": response.get("incomplete_details") is not None,
                "output_tokens": tokens if type(tokens) is int and 0 <= tokens <= 10000000 else None,
            }
            if isinstance(exc, RouterError) and hasattr(exc, "wrapper_shape"):
                error.response_state["wrapper_shape"] = exc.wrapper_shape
        raise error from exc
