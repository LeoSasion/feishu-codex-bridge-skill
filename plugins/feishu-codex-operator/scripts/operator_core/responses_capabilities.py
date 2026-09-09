"""Explicit, versioned Responses capabilities. No provider guessing or I/O."""

from __future__ import annotations

from dataclasses import dataclass
import re


class RouterError(ValueError):
    """Fixed, content-free error suitable for the local API."""


class UpstreamProtocolError(RouterError):
    """A single upstream attempt returned an invalid Responses protocol."""


PROTOCOL_REASONS = frozenset({
    "invalid_protocol_json", "invalid_protocol_string", "invalid_protocol_unicode",
    "protocol_string_too_large", "opaque_upstream_context_not_supported",
    "invalid_output_message", "unsupported_structured_content", "invalid_content_part",
    "unsupported_input_modality", "image_url_required", "invalid_response_item",
    "unsupported_upstream_output_item", "unknown_or_mismatched_upstream_tool",
    "unfinished_tool_call", "function_arguments_must_be_object", "custom_wrapper_requires_exact_input",
    "unsuccessful_or_invalid_response", "duplicate_output_item_id", "duplicate_output_call_id",
    "upstream_violated_tool_choice", "invalid_initial_response", "response_id_changed",
    "response_created_missing", "duplicate_response_created", "invalid_or_duplicate_output_index",
    "unsupported_output_item", "duplicate_item_id", "unknown_output_index", "mismatched_event_item_id",
    "unexpected_arguments_event", "conflicting_arguments_events", "arguments_done_mismatch",
    "invalid_done_item", "conflicting_output_done", "tool_identity_changed", "final_tool_arguments_mismatch",
    "text_event_for_tool_or_finished_item", "final_output_indices_mismatch", "final_response_item_mismatch",
    "unsupported_responses_event", "upstream_unsuccessful_terminal", "adapted_stream_too_large",
    "tool_arguments_too_large", "event_after_terminal_or_invalid_event",
    "truncated_upstream_tool_stream_no_retry", "external_event_too_large_no_retry",
    "invalid_external_sse_event_no_retry", "truncated_external_sse_no_retry",
    "unsupported_adapted_response_encoding_or_status", "external_json_response_required",
    "external_sse_response_required", "unsupported_adapted_stream_encoding",
    "text_part_too_large", "text_snapshot_mismatch", "invalid_text_part_index",
    "invalid_initial_text_parts", "text_part_type_changed", "text_event_item_type_mismatch",
    "invalid_text_part", "duplicate_or_late_text_part", "text_delta_after_done",
    "duplicate_or_late_text_done", "final_text_part_missing", "final_text_part_type_mismatch",
    "invalid_initial_text_part", "conflicting_sse_event_type_no_retry",
})


def protocol_reason(exc):
    reason = None
    for _ in range(8):
        if isinstance(exc, RouterError) and str(exc) in PROTOCOL_REASONS:
            reason = str(exc)
        exc = getattr(exc, "__cause__", None)
        if exc is None:
            break
    return reason


def protocol_response_state(exc):
    """Only schema-level state from our adapter, never provider error messages."""
    for _ in range(8):
        if isinstance(exc, UpstreamProtocolError) and hasattr(exc, "response_state"):
            return exc.response_state
        exc = getattr(exc, "__cause__", None)
        if exc is None:
            return None
    return None


def string_list(value, allowed=None, *, nonempty=False):
    if (not isinstance(value, list) or (nonempty and not value)
            or any(not isinstance(v, str) or not v for v in value)
            or len(value) != len(set(value))
            or (allowed is not None and any(v not in allowed for v in value))):
        raise RouterError("invalid_capability_list")
    return tuple(value)


@dataclass(frozen=True)
class ResponsesCapabilities:
    protocol: str
    function_tools: bool
    custom_tools: tuple[tuple[str, str], ...]
    tool_choice: tuple[str, ...]
    named_tool_choice: str
    parallel_tool_calls: bool
    tool_search: bool
    input_modalities: tuple[str, ...]
    structured_tool_outputs: bool
    developer_role: str
    reasoning_input: bool
    reasoning_summary: bool
    previous_response_id: bool
    text_verbosity: bool
    codex_tool_mode: str
    tool_choice_by_reasoning: tuple[tuple[str, tuple[str, ...]], ...] = ()
    text_tool_outputs: str = "native"
    history_custom_tools: tuple[tuple[str, str], ...] = ()
    named_function_outputs: tuple[tuple[str, str], ...] = ()

    @classmethod
    def parse(cls, value):
        fields = set(cls.__dataclass_fields__)
        optional = {"tool_choice_by_reasoning", "text_tool_outputs", "history_custom_tools",
                    "named_function_outputs"}
        if (not isinstance(value, dict) or set(value) - fields
                or fields - optional - set(value)):
            raise RouterError("invalid_responses_capability_fields")
        if value["protocol"] != "responses-tools-v1":
            raise RouterError("unsupported_responses_tool_protocol")
        for field in ("function_tools", "parallel_tool_calls", "tool_search",
                      "structured_tool_outputs", "reasoning_input", "reasoning_summary",
                      "previous_response_id", "text_verbosity"):
            if type(value[field]) is not bool:
                raise RouterError("invalid_capability_boolean")
        for field, choices in (("named_tool_choice", {"native", "required", "reject"}),
                               ("developer_role", {"native", "system", "user", "reject"}),
                               ("codex_tool_mode", {"standard", "code_mode_only"})):
            if not isinstance(value[field], str) or value[field] not in choices:
                raise RouterError("invalid_capability_choice")
        tools = value["custom_tools"]
        if (not isinstance(tools, dict) or len(tools) > 256
                or any(not isinstance(k, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,129}", k)
                       or not isinstance(v, str) or v not in {"native", "wrap"}
                       for k, v in tools.items())):
            raise RouterError("invalid_custom_tool_capabilities")
        choices = string_list(value["tool_choice"], {"auto", "none", "required"})
        history_tools = value.get("history_custom_tools", {})
        if (not isinstance(history_tools, dict) or len(history_tools) > 256
                or any(not isinstance(key, str)
                       or not re.fullmatch(r"(?:[A-Za-z0-9_-]{1,64}\.)?exec", key)
                       or key not in tools or fmt != "codex_exec_v1"
                       for key, fmt in history_tools.items())):
            raise RouterError("invalid_history_custom_tool_capabilities")
        named_outputs = value.get("named_function_outputs", {})
        if (not isinstance(named_outputs, dict) or any(
                key != "codex_app.send_message_to_thread" or mode != "user_message_json_v1"
                for key, mode in named_outputs.items())):
            raise RouterError("invalid_named_function_output_capabilities")
        text_outputs = value.get("text_tool_outputs", "native")
        if not isinstance(text_outputs, str) or text_outputs not in {"native", "json_string"}:
            raise RouterError("invalid_text_tool_output_mode")
        if text_outputs == "json_string" and value["structured_tool_outputs"]:
            raise RouterError("conflicting_tool_output_capabilities")
        restrictions = value.get("tool_choice_by_reasoning", {})
        if (not isinstance(restrictions, dict) or any(not isinstance(key, str) or key not in {
                "unspecified", "none", "minimal", "low", "medium", "high", "xhigh", "max"}
                for key in restrictions)):
            raise RouterError("invalid_tool_choice_reasoning_constraints")
        allowed = set(choices) | ({"named"} if value["named_tool_choice"] != "reject" else set())
        restrictions = tuple(sorted((key, string_list(items, allowed))
                                    for key, items in restrictions.items()))
        if value["named_tool_choice"] == "required" and any(
                "named" in items and "required" not in items for _, items in restrictions):
            raise RouterError("named_choice_requires_verified_required_support")
        modalities = string_list(value["input_modalities"], {"text", "image"}, nonempty=True)
        if "text" not in modalities:
            raise RouterError("text_input_capability_required")
        if (not value["function_tools"] and ("wrap" in tools.values() or value["tool_search"])):
            raise RouterError("function_capability_required_for_wrapping")
        if value["named_tool_choice"] == "required" and "required" not in choices:
            raise RouterError("named_choice_requires_verified_required_support")
        if value["codex_tool_mode"] == "code_mode_only" and tools.get("exec") not in {"native", "wrap"}:
            raise RouterError("code_mode_requires_exec_capability")
        # A saved upstream response cannot recover our per-request alias/call mapping.
        # Native stateful Responses remains available through a v1/null passthrough route.
        if value["previous_response_id"]:
            raise RouterError("adapted_routes_require_explicit_input_history")
        return cls(**{**value, "custom_tools": tuple(sorted(tools.items())),
                      "tool_choice": choices, "input_modalities": modalities,
                      "tool_choice_by_reasoning": restrictions, "text_tool_outputs": text_outputs,
                      "history_custom_tools": tuple(sorted(history_tools.items())),
                      "named_function_outputs": tuple(sorted(named_outputs.items()))})

    def custom_mode(self, name, namespace=None):
        return dict(self.custom_tools).get((namespace + "." if namespace else "") + name)

    def check_tool_choice(self, choice, reasoning):
        if not self.tool_choice_by_reasoning:
            return
        effort = reasoning.get("effort", "unspecified") if isinstance(reasoning, dict) else "unspecified"
        permitted = dict(self.tool_choice_by_reasoning).get(effort)
        if permitted is None:
            raise RouterError("tool_choice_reasoning_profile_missing")
        if choice not in permitted:
            raise RouterError("tool_choice_not_supported_at_reasoning_effort")

    def catalog_fields(self):
        """Use an explicit presentation; do not inherit native model features."""
        return dict(input_modalities=list(self.input_modalities),
                    support_verbosity=self.text_verbosity,
                    default_reasoning_summary="auto" if self.reasoning_summary else "none",
                    supports_parallel_tool_calls=self.parallel_tool_calls,
                    tool_mode=None if self.codex_tool_mode == "standard" else "code_mode_only",
                    node_repl_disabled=self.codex_tool_mode != "code_mode_only",
                    prefer_websockets=False)
