"""Isolated protocol contracts; synthetic tool input is never executed."""

from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from operator_core.model_registry import ModelRegistry
from operator_core.responses_capabilities import ResponsesCapabilities, RouterError, UpstreamProtocolError
from operator_core.responses_tool_adapter import (
    EXEC_GRAMMAR, NAMED_OUTPUT_PREFIX, dumps, prepare_request, restore_response,
)

CAPABILITIES = {
    "protocol": "responses-tools-v1", "function_tools": True,
    "custom_tools": {"exec": "wrap"}, "tool_choice": ["auto", "none", "required"],
    "named_tool_choice": "native", "parallel_tool_calls": True, "tool_search": True,
    "input_modalities": ["text"], "structured_tool_outputs": False,
    "developer_role": "native", "reasoning_input": True, "reasoning_summary": False,
    "previous_response_id": False, "text_verbosity": False, "codex_tool_mode": "code_mode_only",
}
EXEC = {"type": "custom", "name": "exec", "description": "Run JavaScript using tools.",
        "format": {"type": "grammar", "syntax": "lark", "definition": EXEC_GRAMMAR}}
FUNCTION = {"type": "function", "name": "add", "description": "Add numbers.",
            "parameters": {"type": "object", "properties": {"a": {"type": "number"}}}}
CODE = '// @exec: {"max_output_tokens": 23}\r\ntext("中文😀\\n\\"");\ntext("\\\\");\u2028\u2029'
ROUTE = {"slug": "local/protocol", "display_name": "Protocol fixture", "model": "fixture",
         "api_base": "http://127.0.0.1:1/v1", "api_key_env": "", "context_window": 32768,
         "reasoning_efforts": ["low", "max"], "responses": CAPABILITIES}
BEEPER = json.loads((Path(__file__).resolve().parents[1] /
                    "scripts/operator_core/beeper_model_catalog.json").read_text(encoding="utf-8"))


def prepare(payload=None, **changes):
    caps = ResponsesCapabilities.parse({**deepcopy(CAPABILITIES), **changes})
    return prepare_request(payload or {"tools": [EXEC], "input": "Synthetic input."}, caps, ("low", "max"))


def call(context, code=CODE, *, name="exec", namespace=None, call_id="call_1", item_id="fc_1"):
    spec = context.specs[("custom", namespace, name)]
    return {"type": "function_call", "id": item_id, "call_id": call_id,
            "name": spec.upstream_name, "arguments": dumps({"input": code}), "status": "completed"}


def response(*items, status="completed"):
    return {"id": "resp_1", "object": "response", "status": status, "output": list(items),
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}, "model": "fixture"}


class CapabilitiesTests(unittest.TestCase):
    def test_named_function_outputs_require_exact_explicit_codec(self):
        self.assertEqual(ResponsesCapabilities.parse(CAPABILITIES).named_function_outputs, ())
        valid = {"codex_app.send_message_to_thread": "user_message_json_v1"}
        self.assertEqual(ResponsesCapabilities.parse({**CAPABILITIES,
            "named_function_outputs": valid}).named_function_outputs, tuple(valid.items()))
        for value in (None, [], True, {"unknown.tool": "user_message_json_v1"},
                      {"send_message_to_thread": "user_message_json_v1"},
                      {"codex_app.send_message_to_thread": "native"},
                      {"codex_app.send_message_to_thread": {}}):
            with self.subTest(value=value), self.assertRaises(RouterError):
                ResponsesCapabilities.parse({**CAPABILITIES, "named_function_outputs": value})

    def test_history_custom_tools_require_explicit_registered_identity_and_format(self):
        self.assertEqual(ResponsesCapabilities.parse(CAPABILITIES).history_custom_tools, ())
        valid = {**CAPABILITIES, "history_custom_tools": {"exec": "codex_exec_v1"}}
        self.assertEqual(ResponsesCapabilities.parse(valid).history_custom_tools,
                         (("exec", "codex_exec_v1"),))
        for value in (None, [], {"unknown": "codex_exec_v1"}, {"functions.exec": "codex_exec_v1"},
                      {"exec": "text"}, {"exec": {}}, {"a.b.exec": "codex_exec_v1"}):
            with self.subTest(value=value), self.assertRaises(RouterError):
                ResponsesCapabilities.parse({**CAPABILITIES, "history_custom_tools": value})

    def test_v1_is_unchanged_v2_is_explicit_and_max_is_supported(self):
        old = {k: v for k, v in ROUTE.items() if k != "responses"}
        v1 = ModelRegistry({"version": 1, "models": [old]}, BEEPER)
        v2 = ModelRegistry({"version": 2, "models": [{**old, "responses": None}]}, BEEPER)
        self.assertEqual(v1.routes, v2.routes)
        self.assertIn("max", v2.routes[old["slug"]].reasoning_efforts)
        for version, row in ((1, ROUTE), (2, old)):
            with self.assertRaises(RouterError):
                ModelRegistry({"version": version, "models": [row]}, BEEPER)

    def test_invalid_capabilities_and_types_are_refused(self):
        changes = [
            {"surprise": True}, {"protocol": "chat"}, {"function_tools": 1},
            {"custom_tools": {"exec": "guess"}}, {"tool_choice": [["auto"]]},
            {"tool_choice": ["auto", "auto"]}, {"input_modalities": ["video"]},
            {"function_tools": False}, {"named_tool_choice": "required", "tool_choice": ["auto"]},
            {"codex_tool_mode": "code_mode_only", "custom_tools": {}},
            {"previous_response_id": True}, {"developer_role": "guess"},
        ]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(RouterError):
                ResponsesCapabilities.parse({**deepcopy(CAPABILITIES), **change})

    def test_v2_catalog_does_not_inherit_native_feature_flags(self):
        registry = ModelRegistry({"version": 2, "models": [ROUTE]}, BEEPER)
        native = {"models": [{"slug": "native", "visibility": "list", "future_native_only": True,
                              "input_modalities": ["image"], "supports_search_tool": True}]}
        before = deepcopy(native)
        self.assertTrue(registry.merge(native)["models"][-1]["node_repl_auto_review_required"])
        merged = registry.merge(native)
        self.assertEqual(before, native)
        self.assertEqual(merged["models"][0], native["models"][0])
        external = merged["models"][-1]
        self.assertNotIn("future_native_only", external)
        self.assertEqual(external["input_modalities"], ["text"])
        self.assertFalse(external["supports_search_tool"])
        self.assertEqual(external["tool_mode"], "code_mode_only")
        self.assertEqual(external["multi_agent_version"], "disabled")
        self.assertNotIn("comp_hash", external)

        for mode, custom, expected_patch in (
                ("code_mode_only", {"exec": "wrap"}, "freeform"),
                ("standard", {}, None), ("standard", {"exec": "wrap"}, None),
                ("standard", {"apply_patch": "native"}, "freeform")):
            row = deepcopy(ROUTE)
            row["responses"].update(codex_tool_mode=mode, custom_tools=custom)
            selected = ModelRegistry({"version": 2, "models": [row]}, BEEPER).merge(native)
            with self.subTest(mode=mode, custom=custom):
                self.assertEqual(selected["models"][-1]["apply_patch_tool_type"], expected_patch)
                self.assertEqual(selected["models"][0], native["models"][0])
                self.assertEqual(selected["models"][1], BEEPER["models"][0])


class ToolAdapterTests(unittest.TestCase):
    def test_history_argument_limit_applies_after_json_encoding(self):
        maximum = 2 * 1024 * 1024
        # Each literal backslash becomes two bytes on the function wire.
        exact = "\\" * ((maximum - len('{"input":""}')) // 2)
        for source, accepted in ((exact, True), (exact + "\\", False)):
            encoded = json.dumps({"input": source}, ensure_ascii=False, separators=(",", ":"))
            self.assertEqual(len(encoded.encode()), maximum + (0 if accepted else 2))
            for mode in ("wrapped", "history_only", "search", "native"):
                with self.subTest(accepted=accepted, mode=mode):
                    caps = {}
                    tools = [EXEC]
                    item = {"type": "custom_tool_call", "call_id": "bound", "name": "exec", "input": source}
                    result = {"type": "custom_tool_call_output", "call_id": "bound", "output": "synthetic"}
                    if mode == "history_only":
                        tools = []
                        caps = {"history_custom_tools": {"exec": "codex_exec_v1"}}
                    elif mode == "search":
                        tools = [{"type": "tool_search", "execution": "client", "parameters": {"type": "object"}}]
                        item = {"type": "tool_search_call", "call_id": "bound", "execution": "client",
                                "arguments": {"input": source}}
                        result = {"type": "tool_search_output", "call_id": "bound", "execution": "client",
                                  "status": "completed", "tools": []}
                    elif mode == "native":
                        caps = {"custom_tools": {"exec": "native"}}
                    payload = {"tools": tools, "input": [item, result]}
                    before = deepcopy(payload)
                    if accepted or mode == "native":
                        wire, _ = prepare(payload, **caps)
                        if mode == "native":
                            self.assertEqual(wire["input"][0]["input"], source)
                        else:
                            self.assertEqual(wire["input"][0]["arguments"], encoded)
                    else:
                        with self.assertRaisesRegex(RouterError, "^protocol_string_too_large$"):
                            prepare(payload, **caps)
                    self.assertEqual(payload, before)

    def test_reasoning_nullable_content_and_text_variants_are_preserved(self):
        for fields in ({}, {"content": None}, {"content": []},
                       {"content": [{"type": "reasoning_text", "text": "\r\n中文😀  ", "keep": 1}]},
                       {"content": [{"type": "text", "text": " legacy reasoning "}]}):
            item = {"id": "rs_1", "type": "reasoning", "summary": [], **fields}
            with self.subTest(fields=fields):
                wire, context = prepare({"tools": [EXEC], "input": [item]})
                self.assertEqual(wire["input"], [item])
                self.assertEqual(restore_response(response(item), context), response(item))

    def test_invalid_reasoning_history_and_terminal_are_rejected_without_content_diagnostics(self):
        from operator_core.responses_capabilities import protocol_reason
        _, context = prepare()
        for index, fields in enumerate(({"content": "private draft"}, {"content": {"private": "draft"}},
                       {"content": [{"type": "output_text", "text": "private draft"}]},
                       {"content": [{"type": "reasoning_text", "text": None}]},
                       {"summary": None}, {"summary": [{"type": "reasoning_text", "text": "private draft"}]},
                       {"summary": [{"type": "summary_text", "text": 3}]},
                       {"content": [{"type": "reasoning_text", "text": ""}] * 1025},
                       {"status": "in_progress"}, {"status": "incomplete"})):
            item = {"id": "rs_1", "type": "reasoning", "summary": [], **fields}
            with self.subTest(case=index, lane="history"):
                with self.assertRaises(RouterError):
                    prepare({"tools": [EXEC], "input": [item]})
            with self.subTest(case=index, lane="terminal"):
                raw = response(call(context), item)
                snapshot = deepcopy(raw)
                with self.assertRaises(UpstreamProtocolError) as caught:
                    restore_response(raw, context)
                self.assertIn(protocol_reason(caught.exception), {"invalid_reasoning_content", "unfinished_reasoning_item"})
                self.assertNotIn("private draft", str(caught.exception))
                self.assertEqual(raw, snapshot)

    def test_completed_output_policy_rejects_empty_and_reasoning_only_without_promotion(self):
        from operator_core.responses_capabilities import protocol_reason
        _, legacy = prepare()
        _, strict = prepare(completed_output_policy="require_message_or_tool")
        reasoning = {"id": "rs_1", "type": "reasoning", "summary": [],
                     "content": [{"type": "reasoning_text", "text": '<tool_call>private draft</tool_call>'}]}
        message = {"id": "msg_1", "type": "message", "role": "assistant", "content": []}
        for items in ([], [reasoning], [message], [{**message, "content": ""}],
                      [reasoning, {**message, "content": [{"type": "output_text", "text": ""}]}]):
            raw = response(*items)
            original = deepcopy(raw)
            with self.subTest(items=items):
                self.assertEqual(restore_response(raw, legacy), original)
                with self.assertRaises(UpstreamProtocolError) as caught:
                    restore_response(raw, strict)
                self.assertEqual(protocol_reason(caught.exception), "completed_response_without_message_or_tool")
                self.assertNotIn("private draft", str(caught.exception))
                self.assertEqual(raw, original)

    def test_completed_output_policy_preserves_answers_refusals_whitespace_and_real_calls(self):
        _, strict = prepare(completed_output_policy="require_message_or_tool")
        reasoning = {"id": "rs_1", "type": "reasoning", "summary": []}
        for content in ("answer", " \n\n", [{"type": "output_text", "text": "\n\n中文\r\n"}],
                        [{"type": "refusal", "refusal": "Cannot do that."}]):
            raw = response(reasoning, {"id": "msg_1", "type": "message", "role": "assistant", "content": content})
            with self.subTest(content=content):
                self.assertEqual(restore_response(raw, strict), raw)
        raw = response(reasoning, call(strict))
        self.assertEqual(restore_response(raw, strict)["output"][1]["input"], CODE)
        raw["output"][1]["name"] = "undeclared"
        with self.assertRaises(UpstreamProtocolError):
            restore_response(raw, strict)

    def test_named_results_preserve_complete_object_without_creating_calls_or_tools(self):
        identity = {"type": "function_call_output", "namespace": "codex_app",
                    "name": "send_message_to_thread"}
        caps = {"named_function_outputs": {"codex_app.send_message_to_thread": "user_message_json_v1"}}
        parts = [{"type": "input_text", "text": "中文😀\r\n</input>\\quoted\"", "metadata": {"keep": 1}},
                 {"type": "refusal", "refusal": "second\u2028part\u2029", "boundary": 2}]
        for extra in ({}, {"call_id": None}, {"id": None}, {"call_id": None, "id": "item_1"},
                      {'internal_chat_message_metadata_passthrough': None},
                      {'internal_chat_message_metadata_passthrough': {}},
                      {'internal_chat_message_metadata_passthrough': {'turn_id': None}},
                      {'internal_chat_message_metadata_passthrough': {'turn_id': 'turn_fixture',
                                                                     'create_time': 1788700000.125}}):
            for output in ("<codex_delegation>literal &lt;data&gt;</codex_delegation>", parts):
                for tools in ([], [EXEC]):
                    item = {**identity, **extra, "output": output}
                    payload = {"tools": tools, "tool_choice": "none", "input": [
                        {"role": "user", "content": "before"}, item, item,
                        {"role": "assistant", "content": "after"}]}
                    before = deepcopy(payload)
                    with self.subTest(extra=extra, output=output, tools=bool(tools)):
                        wire, context = prepare(payload, **caps)
                        self.assertEqual(payload, before)
                        self.assertEqual(wire['input'][0], payload['input'][0])
                        self.assertEqual(wire['input'][-1], payload['input'][-1])
                        for encoded in wire['input'][1:3]:
                            self.assertEqual((encoded['type'], encoded['role']), ('message', 'user'))
                            text = encoded['content'][0]['text']
                            self.assertTrue(text.startswith(NAMED_OUTPUT_PREFIX))
                            self.assertEqual(json.loads(text[len(NAMED_OUTPUT_PREFIX):]), item)
                        self.assertEqual(context.history_calls, frozenset())
                        self.assertEqual(len(context.specs), len(tools))
                        self.assertEqual(len(wire['tools']), len(tools))
                        with self.assertRaises(UpstreamProtocolError):
                            restore_response(response({'type': 'function_call', 'id': 'new_item',
                                'call_id': 'new_call', 'name': 'send_message_to_thread',
                                'arguments': '{}', 'status': 'completed'}), context)

    def test_named_result_codec_never_repairs_paired_or_unknown_history(self):
        item = {'type': 'function_call_output', 'namespace': 'codex_app',
                'name': 'send_message_to_thread', 'output': 'synthetic'}
        caps = {'named_function_outputs': {'codex_app.send_message_to_thread': 'user_message_json_v1'}}
        with self.assertRaisesRegex(RouterError, '^named_function_output_not_registered$'):
            prepare({'tools': [], 'input': [item]})
        for update in ({'namespace': 'other'}, {'name': 'other'}, {'name': None},
                       {'namespace': None}, {'call_id': ''}, {'call_id': False},
                       {'call_id': 'unmatched'}, {'id': ''}, {'id': False},
                       {'output': None}, {'status': 'in_progress'}, {'extra': 'opaque'},
                       {'internal_chat_message_metadata_passthrough': {'unknown': 'opaque'}},
                       {'internal_chat_message_metadata_passthrough': []},
                       {'internal_chat_message_metadata_passthrough': {'turn_id': {}}},
                       {'internal_chat_message_metadata_passthrough': {'create_time': True}},
                       {'internal_chat_message_metadata_passthrough': {'create_time': -1}},
                       {'internal_chat_message_metadata_passthrough': {'create_time': float('inf')}},
                       {'internal_chat_message_metadata_passthrough': {'create_time': None}},
                       {'internal_chat_message_metadata_passthrough': {'create_time': 10 ** 400}},
                       {'encrypted_content': 'opaque'},
                       {'output': [{'type': 'input_image', 'image_url': 'data:synthetic'}]},
                       {'output': [{'type': 'encrypted_content', 'encrypted_content': 'opaque'}]},
                       {'output': [{'type': 'input_text', 'text': None}]},
                       {'type': 'custom_tool_call_output'}, {'type': 'tool_search_output'}):
            with self.subTest(update=update), self.assertRaises(RouterError):
                prepare({'tools': [], 'input': [{**item, **update}]},
                        **caps, input_modalities=['text', 'image'], structured_tool_outputs=True)
        unfinished = {'type': 'custom_tool_call', 'name': 'exec', 'call_id': 'pending', 'input': CODE}
        with self.assertRaisesRegex(RouterError, '^tool_output_missing_from_explicit_history$'):
            prepare({'tools': [EXEC], 'input': [unfinished, item]}, **caps)
        paired_call = {'type': 'function_call', 'name': FUNCTION['name'], 'call_id': 'paired', 'arguments': '{}'}
        paired_output = {'type': 'function_call_output', 'call_id': 'paired', 'output': 'ordinary'}
        wire, context = prepare({'tools': [FUNCTION], 'input': [paired_call, paired_output, item]}, **caps)
        self.assertEqual(wire['input'][:2], [paired_call, paired_output])
        self.assertEqual(context.history_calls, {'paired'})

    def test_registered_history_with_no_current_tools_preserves_mapping_and_never_enables_calls(self):
        for namespace in (None, "functions"):
            for mode in ("wrap", "native"):
                with self.subTest(namespace=namespace, mode=mode):
                    key = (namespace + "." if namespace else "") + "exec"
                    tools = [EXEC] if namespace is None else [{"type": "namespace",
                        "name": namespace, "tools": [EXEC]}]
                    caps = {"custom_tools": {"exec": mode, key: mode},
                            "history_custom_tools": {key: "codex_exec_v1"},
                            "text_tool_outputs": "json_string"}
                    _, original_context = prepare({"tools": tools}, **caps)
                    upstream = call(original_context, namespace=namespace)
                    if mode == "native":
                        upstream.pop("arguments")
                        upstream.update(type="custom_tool_call", input=CODE)
                    historical = restore_response(response(upstream), original_context)["output"][0]
                    parts = [{"type": "input_text", "text": "中文😀\r\nfirst", "keep": 1},
                             {"type": "input_text", "text": "second\\part"}]
                    history = [historical, {"type": "custom_tool_call_output",
                        "call_id": "call_1", "output": parts}]
                    for declaration in ({"tools": []}, {}):
                        for choice in ("auto", "none"):
                            payload = {**declaration, "input": history, "tool_choice": choice}
                            before = deepcopy(payload)
                            wire, context = prepare(payload, **caps)
                            self.assertEqual(wire.get("tools"), payload.get("tools"))
                            self.assertEqual(wire["input"][0], upstream)
                            self.assertEqual(json.loads(wire["input"][1]["output"]), parts)
                            self.assertEqual(wire["input"][1]["call_id"], "call_1")
                            self.assertEqual(context.history_calls, {"call_1"})
                            self.assertEqual((context.specs, context.upstream, context.visible_names),
                                             ({}, {}, frozenset()))
                            self.assertEqual(payload, before)
                            with self.assertRaises(UpstreamProtocolError):
                                restore_response(response({**upstream, "call_id": "new_call"}), context)

    def test_history_codec_does_not_accept_unknown_incomplete_or_conflicting_history(self):
        item = {"type": "custom_tool_call", "name": "exec", "call_id": "one", "input": CODE}
        output = {"type": "custom_tool_call_output", "call_id": "one", "output": "ok"}
        caps = {"history_custom_tools": {"exec": "codex_exec_v1"}}
        with self.assertRaises(RouterError):
            prepare({"tools": [], "input": [item, output]})
        for tools, history in (
                ([FUNCTION], [item, output]),
                ([], [{**item, "namespace": "functions"}, output]),
                ([], [{**item, "namespace": ""}, output]),
                ([], [{**item, "name": "unknown"}, output]),
                ([], [{**item, "type": "function_call"}, output]),
                ([], [{**item, "status": "in_progress"}, output]),
                ([], [{**item, "call_id": None}, output]),
                ([], [{**item, "input": ""}, output]),
                ([], [{**item, "input": None}, output]),
                ([], [item]), ([], [output]), ([], [item, output, output]),
                ([], [item, {**output, "call_id": "other"}]),
                ([], [item, {**output, "type": "function_call_output"}]),
                ([], [{**item, "encrypted_content": "opaque"}, output])):
            with self.subTest(tools=tools, history=history), self.assertRaises(RouterError):
                prepare({"tools": tools, "input": history}, **caps)
        with self.assertRaises(RouterError):
            prepare({"tools": [], "input": [item, output], "tool_choice": "required"}, **caps)

    def test_nullable_descriptions_preserve_identity_and_explicit_metadata(self):
        function = {**FUNCTION, "description": None}
        custom = {**EXEC, "description": None}
        payload = {"tools": [function, {"type": "namespace", "name": "functions",
                    "description": None, "tools": [custom]}], "input": "Synthetic input."}
        before = deepcopy(payload)
        wire, context = prepare(payload, custom_tools={"exec": "wrap", "functions.exec": "wrap"})
        self.assertEqual(payload, before)
        self.assertIsNone(wire["tools"][0]["description"])
        self.assertIsNone(context.specs[("custom", "functions", "exec")].original["description"])
        self.assertIn("Tool functions.exec.", wire["tools"][1]["description"])
        restored = restore_response(response(call(context, namespace="functions")), context)["output"][0]
        self.assertEqual((restored["namespace"], restored["name"], restored["input"]),
                         ("functions", "exec", CODE))

    def test_nontext_descriptions_and_null_executable_values_remain_rejected(self):
        for value in (False, 0, [], {}):
            for tool in ({**EXEC, "description": value},
                         {"type": "namespace", "name": "functions", "description": value, "tools": []}):
                with self.subTest(value=value, type=tool["type"]), self.assertRaises(RouterError) as caught:
                    prepare({"tools": [tool]})
                self.assertEqual(caught.exception.protocol_field, "tools.description")
        _, context = prepare()
        with self.assertRaises(RouterError):
            context.specs[("custom", None, "exec")].check_input(None)
        with self.assertRaises(RouterError):
            prepare({"tools": [EXEC], "input": [{"role": "user", "content": [{"type": "input_text", "text": None}]}]})

    def test_text_part_results_can_be_serialized_losslessly_only_when_explicit(self):
        _, context = prepare()
        initial = restore_response(response(call(context)), context)["output"][0]
        parts = [{"type": "input_text", "text": "中文\r\n😀", "fixture_metadata": "preserved"},
                 {"type": "input_text", "text": "second\\quoted\"part"}]
        history = [initial, {"type": "custom_tool_call_output", "call_id": "call_1", "output": parts}]
        payload = {"tools": [EXEC], "input": history}
        before = deepcopy(payload)
        wire, _ = prepare(payload, text_tool_outputs="json_string")
        self.assertEqual(json.loads(wire["input"][-1]["output"]), parts)
        self.assertEqual(payload, before)
        with self.assertRaises(RouterError):
            prepare(payload)
        history[-1]["output"] = [{"type": "input_image", "image_url": "https://example.test/synthetic.png"}]
        with self.assertRaisesRegex(RouterError, "text_tool_output_conversion_requires_text_parts"):
            prepare(payload, text_tool_outputs="json_string", input_modalities=["text", "image"])
        history[-1]["output"] = "unchanged plain string"
        wire, _ = prepare(payload, text_tool_outputs="json_string")
        self.assertEqual(wire["input"][-1]["output"], "unchanged plain string")

    def test_reasoning_tool_choice_constraints_are_checked_before_conversion(self):
        constraints = {"none": ["auto", "none", "required", "named"], "low": ["auto", "none"]}
        base = {"tools": [EXEC], "tool_choice": "required", "reasoning": {"effort": "low"}}
        with self.assertRaisesRegex(RouterError, "tool_choice_not_supported_at_reasoning_effort"):
            prepare(base, tool_choice_by_reasoning=constraints)
        with self.assertRaisesRegex(RouterError, "tool_choice_reasoning_profile_missing"):
            prepare({"tools": [EXEC]}, tool_choice_by_reasoning=constraints)
        wire, _ = prepare_request({**base, "reasoning": {"effort": "none"}},
            ResponsesCapabilities.parse({**CAPABILITIES, "tool_choice_by_reasoning": constraints}),
            ("none", "low"))
        self.assertEqual(wire["tool_choice"], "required")
        with self.assertRaisesRegex(RouterError, "tool_choice_not_supported_at_reasoning_effort"):
            prepare({**base, "tool_choice": {"type": "custom", "name": "exec"}},
                    tool_choice_by_reasoning=constraints)
        with self.assertRaises(RouterError):
            ResponsesCapabilities.parse({**CAPABILITIES, "tool_choice_by_reasoning": {"low": ["invented"]}})

    def test_custom_input_is_exact_after_json_and_history_roundtrip(self):
        original = {"tools": [EXEC, FUNCTION], "input": "Synthetic", "metadata": {"keep": "中文"},
                    "text": {"format": {"type": "json_object"}}}
        before = deepcopy(original)
        prepared, context = prepare(original)
        self.assertEqual(original, before)
        self.assertEqual(prepared["metadata"], original["metadata"])
        self.assertEqual(prepared["text"], original["text"])
        self.assertEqual(prepared["tools"][1], FUNCTION)
        self.assertEqual(prepared["tools"][0]["parameters"]["required"], ["input"])
        raw = response(call(context))
        saved = deepcopy(raw)
        restored = restore_response(raw, context)
        self.assertEqual(raw, saved)
        item = restored["output"][0]
        self.assertEqual(item["type"], "custom_tool_call")
        self.assertEqual(item["name"], "exec")
        self.assertEqual(item["input"], CODE)
        self.assertNotIn("arguments", item)
        followup = {**original, "input": [
            {"role": "user", "content": "Synthetic"}, item,
            {"type": "custom_tool_call_output", "call_id": "call_1", "output": "结果😀\r\n42"},
        ]}
        wire, following = prepare(followup)
        self.assertEqual(wire["input"][1], raw["output"][0])
        self.assertEqual(wire["input"][2]["type"], "function_call_output")
        self.assertEqual(wire["input"][2]["output"], "结果😀\r\n42")
        self.assertEqual(following.history_calls, {"call_1"})

    def test_aliases_are_stable_and_conflicts_are_refused(self):
        prepared, context = prepare()
        alias = prepared["tools"][0]["name"]
        reordered, _ = prepare({"tools": [FUNCTION, EXEC], "input": "synthetic"})
        self.assertEqual(reordered["tools"][1]["name"], alias)
        collision = {**FUNCTION, "name": alias}
        for tools in ([EXEC, collision], [collision, EXEC], [EXEC, EXEC]):
            with self.assertRaises(RouterError):
                prepare({"tools": tools})

    def test_native_custom_keeps_protocol_and_mixed_calls_keep_order(self):
        prepared, context = prepare({"tools": [EXEC, FUNCTION]}, custom_tools={"exec": "native"})
        self.assertEqual(prepared["tools"], [EXEC, FUNCTION])
        custom = {"type": "custom_tool_call", "id": "ct_1", "call_id": "call_1",
                  "name": "exec", "input": CODE, "status": "completed"}
        function = {"type": "function_call", "id": "fc_2", "call_id": "call_2",
                    "name": "add", "arguments": '{"a":17}', "status": "completed"}
        self.assertEqual(restore_response(response(custom, function), context), response(custom, function))

    def test_namespaces_restore_type_name_and_history(self):
        tools = [{"type": "namespace", "name": "functions", "description": "Synthetic namespace",
                  "tools": [EXEC, FUNCTION]}, {**FUNCTION, "name": "add"}]
        prepared, context = prepare({"tools": tools},
                                    custom_tools={"exec": "wrap", "functions.exec": "wrap"})
        self.assertEqual(len(prepared["tools"]), 3)
        self.assertEqual(len({t["name"] for t in prepared["tools"]}), 3)
        raw = call(context, namespace="functions")
        restored = restore_response(response(raw), context)["output"][0]
        self.assertEqual((restored["namespace"], restored["name"]), ("functions", "exec"))
        followup, _ = prepare({"tools": tools, "input": [restored, {
            "type": "custom_tool_call_output", "call_id": "call_1", "output": "ok"}]},
            custom_tools={"exec": "wrap", "functions.exec": "wrap"})
        self.assertEqual(followup["input"][0], raw)

    def test_client_search_and_loaded_tools_use_same_mapping(self):
        search = {"type": "tool_search", "execution": "client", "description": "Find a tool",
                  "parameters": {"type": "object", "properties": {"goal": {"type": "string"}}}}
        wire, context = prepare({"tools": [search]})
        alias = wire["tools"][0]["name"]
        upstream = {"type": "function_call", "id": "search_1", "call_id": "search_call",
                    "name": alias, "arguments": '{"goal":"中文 tools"}', "status": "completed"}
        restored = restore_response(response(upstream), context)["output"][0]
        self.assertEqual(restored["type"], "tool_search_call")
        self.assertEqual(restored["arguments"], {"goal": "中文 tools"})
        self.assertNotIn("name", restored)
        history = [restored, {"type": "tool_search_output", "execution": "client",
                             "call_id": "search_call", "status": "completed", "tools": [EXEC]}]
        wire, context = prepare({"tools": [search], "input": history})
        self.assertEqual(len(wire["tools"]), 2)
        self.assertEqual(json.loads(wire["input"][1]["output"]), {"tools": [EXEC]})
        tool = restore_response(response(call(context)), context)["output"][0]
        self.assertEqual(tool["input"], CODE)
        self.assertEqual(tool["type"], "custom_tool_call")

    def test_deferred_tools_are_exposed_only_after_loading(self):
        deferred = {**EXEC, "defer_loading": True}
        wire, context = prepare({"tools": [deferred]})
        self.assertEqual(wire["tools"], [])
        with self.assertRaises(UpstreamProtocolError):
            restore_response(response(call(context)), context)
        with self.assertRaises(RouterError):
            prepare({"tools": [deferred]}, tool_search=False)

    def test_unknown_tools_formats_and_history_are_refused(self):
        bad_tools = [
            [{**EXEC, "format": {"type": "grammar", "syntax": "regex", "definition": ".*"}}],
            [{**EXEC, "format": {"type": "grammar", "syntax": "lark", "definition": "start: NEW"}}],
            [{"type": "web_search", "name": "search"}], [{**EXEC, "name": "unknown"}],
            [{"type": "tool_search", "execution": "server"}],
        ]
        for tools in bad_tools:
            with self.subTest(tools=tools), self.assertRaises(RouterError):
                prepare({"tools": tools})
        for item in ({"type": "compaction"}, {"type": "reasoning", "encrypted_content": "opaque"},
                     {"type": "item_reference", "id": "old"}):
            with self.assertRaises(RouterError):
                prepare({"tools": [EXEC], "input": [item]})

    def test_empty_exec_bad_json_unknown_tool_and_duplicate_ids_are_refused(self):
        _, context = prepare()
        with self.assertRaises(UpstreamProtocolError) as oversized:
            restore_response(response(*[{
                "id": "reasoning_" + str(index), "type": "reasoning", "summary": []
            } for index in range(1025)]), context)
        self.assertEqual(str(oversized.exception.__cause__), "unsuccessful_or_invalid_response")
        good = call(context)
        bad_arguments = ['{"input":null}', '{"input":42}', '{"other":"x"}',
                         '{"input":"x","extra":1}', '{"input":"a","input":"b"}',
                         '{"input":NaN}', '{"input":"x"', '{"input":""}', '{"input":"\\ud800"}']
        for arguments in bad_arguments:
            with self.subTest(arguments=arguments), self.assertRaises(UpstreamProtocolError):
                restore_response(response({**good, "arguments": arguments}), context)
        for args in ({"cmd": "PRIVATE_SOURCE"}, {"input": "PRIVATE_SOURCE", "PRIVATE_KEY": "PRIVATE_VALUE"}, {}):
            with self.subTest(fields=len(args)), self.assertRaises(UpstreamProtocolError) as rejected:
                restore_response(response({**good, "arguments": dumps(args)}), context)
            shape = rejected.exception.response_state["wrapper_shape"]
            self.assertEqual(shape, {"field_count": len(args), **{
                "has_" + key: key in args for key in
                ("input", "code", "source", "cmd", "command", "arguments", "action")}})
            self.assertNotIn("PRIVATE", dumps(rejected.exception.response_state))
        for items in ([good, good], [{**good, "name": "unknown"}],
                      [good, {**good, "id": "new"}], [{**good, "status": "in_progress"}]):
            with self.assertRaises(UpstreamProtocolError):
                restore_response(response(*items), context)
        for state in ("incomplete", "failed", "in_progress"):
            with self.assertRaises(UpstreamProtocolError):
                restore_response(response(good, status=state), context)

    def test_history_must_match_exact_call_type_and_have_one_output(self):
        _, context = prepare()
        item = restore_response(response(call(context)), context)["output"][0]
        output = {"type": "custom_tool_call_output", "call_id": "call_1", "output": "42"}
        cases = [[item], [output], [item, {**output, "type": "function_call_output"}],
                 [item, output, output], [item, item, output],
                 [item, {**output, "call_id": "unknown"}]]
        for history in cases:
            with self.subTest(history=history), self.assertRaises(RouterError):
                prepare({"tools": [EXEC], "input": history})

    def test_tool_choice_constraints_and_named_fallback_are_enforced(self):
        for choice in ("required", {"type": "custom", "name": "exec"}):
            _, context = prepare({"tools": [EXEC], "tool_choice": choice})
            with self.assertRaises(UpstreamProtocolError):
                restore_response(response(), context)
        _, context = prepare({"tools": [EXEC], "tool_choice": "none"})
        with self.assertRaises(UpstreamProtocolError):
            restore_response(response(call(context)), context)
        wire, context = prepare({"tools": [EXEC, FUNCTION],
                                 "tool_choice": {"type": "custom", "name": "exec"}},
                                named_tool_choice="required")
        self.assertEqual(wire["tool_choice"], "required")
        self.assertEqual(len(wire["tools"]), 1)
        restore_response(response(call(context)), context)
        wrong = {"type": "function_call", "name": "add", "id": "fc_bad",
                 "call_id": "call_bad", "arguments": "{}", "status": "completed"}
        with self.assertRaises(UpstreamProtocolError):
            restore_response(response(wrong), context)
        with self.assertRaises(RouterError):
            prepare({"tools": [EXEC], "tool_choice": {"type": "custom", "name": "exec"}},
                    named_tool_choice="reject")

    def test_parallel_calls_capability_is_enforced_in_request_and_response(self):
        for incoming in ({}, {"parallel_tool_calls": True}, {"parallel_tool_calls": False}):
            payload = {"tools": [EXEC], **incoming}
            before = deepcopy(payload)
            wire, context = prepare(payload, parallel_tool_calls=False)
            self.assertEqual(payload, before)
            self.assertFalse(wire["parallel_tool_calls"])
            self.assertFalse(context.parallel)
            with self.assertRaises(UpstreamProtocolError):
                restore_response(response(call(context), call(context, item_id="fc_2", call_id="call_2")), context)
        wire, context = prepare({"tools": [EXEC], "parallel_tool_calls": False})
        self.assertFalse(wire["parallel_tool_calls"])
        self.assertFalse(context.parallel)

    def test_roles_modalities_reasoning_and_state_are_explicit(self):
        original = {"tools": [], "input": [{"role": "developer", "content": "Keep this exact."}]}
        wire, _ = prepare(original, developer_role="system")
        self.assertEqual(wire["input"][0], {"role": "system", "content": "Keep this exact."})
        self.assertEqual(original["input"][0]["role"], "developer")
        with self.assertRaises(RouterError):
            prepare(original, developer_role="reject")
        for extra in ({"previous_response_id": "resp_old"}, {"conversation": "conv_old"},
                      {"reasoning": {"effort": "xhigh"}}, {"reasoning": {"summary": "auto"}},
                      {"text": {"verbosity": "high"}}):
            with self.assertRaises(RouterError):
                prepare({"tools": [EXEC], **extra})
        image = {"role": "user", "content": [{"type": "input_image", "image_url": "data:image/png;base64,eA=="}]}
        with self.assertRaises(RouterError):
            prepare({"input": [image]})
        wire, _ = prepare({"input": [image]}, input_modalities=["text", "image"])
        self.assertEqual(wire["input"], [image])
        prepare({"reasoning": {"effort": "max"}})
        include = ["reasoning.encrypted_content"]
        wire, _ = prepare({"include": include})
        self.assertEqual(wire["include"], include)

    def test_null_opaque_field_is_preserved_but_real_opaque_data_is_refused(self):
        for empty in (None, ""):
            reasoning = {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": empty}
            wire, context = prepare({"tools": [EXEC], "input": [reasoning]})
            self.assertEqual(wire["input"], [reasoning])
            self.assertEqual(restore_response(response(reasoning), context)["output"], [reasoning])
        with self.assertRaises(UpstreamProtocolError):
            restore_response(response({**reasoning, "encrypted_content": "opaque"}), context)

    def test_protocol_diagnostics_never_include_provider_content(self):
        from operator_core.responses_capabilities import protocol_reason, protocol_response_state
        _, context = prepare()
        raw = {"id": "resp_1", "status": "incomplete", "output": [], "incomplete_details":
               {"reason": "private-provider-content"}, "usage": {"output_tokens": 2048}}
        try:
            restore_response(raw, context)
        except UpstreamProtocolError as error:
            state = protocol_response_state(error)
            self.assertEqual(state["status"], "incomplete")
            self.assertEqual(state["output_tokens"], 2048)
            self.assertNotIn("private-provider-content", dumps(state))
            self.assertEqual(protocol_reason(error), "unsuccessful_or_invalid_response")
        self.assertIsNone(protocol_reason(RouterError("private-provider-content")))

    def test_structured_tool_output_requires_declared_support(self):
        _, context = prepare()
        item = restore_response(response(call(context)), context)["output"][0]
        output = {"type": "custom_tool_call_output", "call_id": "call_1",
                  "output": [{"type": "input_text", "text": "图片说明"}]}
        payload = {"tools": [EXEC], "input": [item, output]}
        with self.assertRaises(RouterError):
            prepare(payload)
        wire, _ = prepare(payload, structured_tool_outputs=True)
        self.assertEqual(wire["input"][1]["output"], output["output"])


class InputToolDefinitionTests(unittest.TestCase):
    """Codex 0.153.4 Responses Lite declarations use the ordinary tool codec."""

    def prepare_lite(self, payload, **changes):
        return prepare(payload, input_tool_definitions="additional_tools_v1", **changes)

    def block(self, tools, **fields):
        return {"type": "additional_tools", "role": "developer", "tools": tools, **fields}

    def test_input_declarations_require_explicit_capability(self):
        self.assertEqual(ResponsesCapabilities.parse(CAPABILITIES).input_tool_definitions, "reject")
        for value in (None, True, [], "auto", "native"):
            with self.subTest(value=value), self.assertRaisesRegex(RouterError, "input_tool_definitions"):
                ResponsesCapabilities.parse({**CAPABILITIES, "input_tool_definitions": value})
        payload = {"input": [self.block([EXEC])]}
        with self.assertRaisesRegex(RouterError, "input_tool_definitions_not_supported"):
            prepare(payload)
        wire, context = self.prepare_lite(payload)
        self.assertEqual((wire["tools"][0]["name"], wire["input"]), ("exec", []))
        self.assertEqual(context.input_tool_definitions, ((0, payload["input"][0]),))

    def test_unified_namespaced_calls_and_named_results_roundtrip(self):
        blocks = [self.block([{"type": "namespace", "name": "functions",
                              "tools": [EXEC, FUNCTION]}], id="at_fixture"),
                  self.block([{"type": "namespace", "name": "plugin_fixture", "tools": [FUNCTION]}], id=None)]
        message = {"role": "user", "content": "Keep 中文😀\r\n  exactly."}
        payload = {"tools": [FUNCTION], "input": [blocks[0], message, blocks[1]]}
        before = deepcopy(payload)
        caps = {"custom_tools": {"exec": "wrap", "functions.exec": "wrap"}}
        wire, context = self.prepare_lite(payload, **caps)
        self.assertEqual(payload, before)
        self.assertEqual(wire["input"], [message])
        self.assertEqual(len({tool["name"] for tool in wire["tools"]}), 4)
        self.assertEqual(context.input_tool_definitions, ((0, blocks[0]), (2, blocks[1])))
        raw_custom = call(context, namespace="functions")
        alias = context.specs[("function", "plugin_fixture", "add")].upstream_name
        raw_function = {"type": "function_call", "id": "fc_2", "call_id": "call_2",
                        "name": alias, "arguments": '{ "a": 17 }', "status": "completed"}
        restored = restore_response(response(raw_custom, raw_function), context)["output"]
        self.assertEqual((restored[0]["type"], restored[0]["namespace"], restored[0]["input"]),
                         ("custom_tool_call", "functions", CODE))
        self.assertEqual((restored[1]["name"], restored[1]["namespace"]), ("add", "plugin_fixture"))
        custom_result = {"type": "custom_tool_call_output", "call_id": "call_1", "name": "exec",
                         "output": "  first\r\n\n中文😀  "}
        function_result = {"type": "function_call_output", "call_id": "call_2", "name": "add",
                           "namespace": "plugin_fixture", "output": "17"}
        following = {**payload, "input": payload["input"] + [
            restored[0], custom_result, restored[1], function_result]}
        following_before = deepcopy(following)
        forwarded, next_context = self.prepare_lite(following, **caps)
        self.assertEqual(following, following_before)
        self.assertEqual(forwarded["input"][1], raw_custom)
        self.assertEqual(forwarded["input"][2], {**custom_result, "type": "function_call_output",
                                                "name": raw_custom["name"]})
        self.assertEqual(forwarded["input"][3], raw_function)
        self.assertEqual(forwarded["input"][4], {key: value for key, value in
                         {**function_result, "name": alias}.items() if key != "namespace"})
        self.assertEqual(next_context.history_calls, {"call_1", "call_2"})

    def test_input_definitions_do_not_loosen_choices_or_deferred_loading(self):
        payload = {"input": [self.block([EXEC, FUNCTION])],
                   "tool_choice": {"type": "custom", "name": "exec"}, "parallel_tool_calls": False}
        wire, context = self.prepare_lite(payload, named_tool_choice="required")
        self.assertEqual((wire["tool_choice"], len(wire["tools"])), ("required", 1))
        self.assertEqual(restore_response(response(call(context)), context)["output"][0]["input"], CODE)
        with self.assertRaises(UpstreamProtocolError):
            restore_response(response(), context)
        for choice in ("none", "auto"):
            deferred = self.block([{**EXEC, "defer_loading": True}])
            wire, context = self.prepare_lite({"input": [deferred], "tool_choice": choice})
            self.assertEqual(wire["tools"], [])
            with self.assertRaises(UpstreamProtocolError):
                restore_response(response(call(context)), context)
        with self.assertRaisesRegex(RouterError, "required_tool_choice_without_tools"):
            self.prepare_lite({"input": [self.block([])], "tool_choice": "required"})

    def test_malformed_opaque_or_conflicting_blocks_are_rejected(self):
        for change in ({"role": "user"}, {"role": "assistant"}, {"role": "system"},
                       {"role": None}, {"id": ""}, {"id": []}, {"tools": None},
                       {"encrypted_content": "opaque"}, {"content": "not a tool declaration"}):
            with self.subTest(change=change), self.assertRaises(RouterError):
                self.prepare_lite({"input": [{**self.block([EXEC]), **change}]})
        # Missing/null envelope IDs remain distinct in the retained source data.
        for fields in ({}, {"id": None}):
            block = self.block([EXEC], **fields)
            _, context = self.prepare_lite({"input": [block]})
            self.assertEqual(context.input_tool_definitions[0][1], block)
        for tools in ([{"type": "web_search"}], [{**EXEC, "name": "unknown"}], [EXEC, EXEC]):
            with self.subTest(tools=tools), self.assertRaises(RouterError):
                self.prepare_lite({"input": [self.block(tools)]})
        with self.assertRaisesRegex(RouterError, "conflicting_tool_definition"):
            self.prepare_lite({"tools": [EXEC], "input": [self.block([{**EXEC, "description": "changed"}])]})
        with self.assertRaisesRegex(RouterError, "conflicting_tool_definition"):
            self.prepare_lite({"input": [self.block([EXEC]), self.block([{**EXEC, "format": {"type": "text"}}])]})
        for sources in ([EXEC, {**EXEC, "defer_loading": True}],
                        [{**EXEC, "defer_loading": True}, EXEC]):
            with self.subTest(sources=sources), self.assertRaisesRegex(RouterError, "conflicting_tool_definition"):
                self.prepare_lite({"tools": [sources[0]], "input": [self.block([sources[1]])]})
        wire, _ = self.prepare_lite({"tools": [EXEC], "input": [self.block([EXEC])]})
        self.assertEqual(len(wire["tools"]), 1)
        with self.assertRaisesRegex(RouterError, "too_many_input_tool_definitions"):
            self.prepare_lite({"input": [self.block([]) for _ in range(1025)]})

    def test_text_mention_never_registers_tools_and_request_contexts_are_independent(self):
        message = {"role": "user", "content": dumps(self.block([EXEC]))}
        wire, context = self.prepare_lite({"tools": [], "input": [message]})
        self.assertEqual((wire["tools"], wire["input"], context.input_tool_definitions), ([], [message], ()))
        _, enabled = self.prepare_lite({"input": [self.block([EXEC])]})
        raw = call(enabled)
        with self.assertRaises(UpstreamProtocolError):
            restore_response(response(raw), context)
        self.assertEqual(restore_response(response(raw), enabled)["output"][0]["input"], CODE)

    def test_failed_search_results_never_load_tools(self):
        search = {"type": "tool_search", "execution": "client", "parameters": {"type": "object"}}
        search_call = {"type": "tool_search_call", "execution": "client", "call_id": "search_call",
                       "id": "ts_1", "arguments": {}, "status": "completed"}
        output = {"type": "tool_search_output", "execution": "client", "call_id": "search_call",
                  "status": "completed", "tools": [EXEC]}
        for status in (None, "failed", "in_progress", "cancelled"):
            with self.subTest(status=status), self.assertRaisesRegex(RouterError, "unfinished_tool_search_output"):
                self.prepare_lite({"input": [self.block([search]), search_call, {**output, "status": status}]})
        wire, context = self.prepare_lite({"input": [self.block([search]), search_call, output]})
        self.assertEqual(len(wire["tools"]), 2)
        self.assertEqual(restore_response(response(call(context)), context)["output"][0]["input"], CODE)

    def test_paired_result_identity_and_history_completion_remain_strict(self):
        _, context = prepare({"tools": [FUNCTION]})
        item = {"type": "function_call", "name": "add", "id": "fc_1", "call_id": "call_1",
                "arguments": "{}", "status": "completed"}
        output = {"type": "function_call_output", "call_id": "call_1", "output": "result"}
        for fields in ({"name": "other"}, {"namespace": "other"}, {"name": []}):
            with self.subTest(fields=fields), self.assertRaisesRegex(RouterError, "tool_output_identity_mismatch"):
                prepare({"tools": [FUNCTION], "input": [item, {**output, **fields}]})
        for fields in ({}, {"name": None, "namespace": None}):
            wire, _ = prepare({"tools": [FUNCTION], "input": [item, {**output, **fields}]})
            self.assertEqual(wire["input"][1], {**output, **fields})
        with self.assertRaisesRegex(RouterError, "unfinished_tool_call"):
            prepare({"tools": [FUNCTION], "input": [{**item, "status": "in_progress"}, output]})


if __name__ == "__main__":
    unittest.main()
