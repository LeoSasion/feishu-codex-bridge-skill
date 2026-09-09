"""Wire fragmentation and terminal integrity tests; no inference or execution."""

from copy import deepcopy
import unittest
from unittest.mock import patch

from test_responses_tools import CODE, FUNCTION, EXEC, prepare, call, response
from operator_core.responses_capabilities import RouterError, UpstreamProtocolError, protocol_reason
from operator_core.responses_events import DONE, SSEDecoder, ResponsesEventAdapter, restore_events
from operator_core.responses_tool_adapter import dumps


def events_for(*items):
    events = [{"type": "response.created", "response": response(status="in_progress")}]
    for index, item in enumerate(items):
        argument_key = "input" if item["type"] == "custom_tool_call" else "arguments"
        added = {**item, "status": "in_progress", argument_key: ""}
        events.append({"type": "response.output_item.added", "output_index": index, "item": added})
    # Interleave chunks from every call to exercise independent output indices.
    for part in range(3):
        for index, item in enumerate(items):
            key = "input" if item["type"] == "custom_tool_call" else "arguments"
            value = item[key]
            start, end = len(value) * part // 3, len(value) * (part + 1) // 3
            prefix = ("response.custom_tool_call_input." if key == "input"
                      else "response.function_call_arguments.")
            events.append({"type": prefix + "delta", "item_id": item["id"],
                           "output_index": index, "delta": value[start:end]})
    for index, item in enumerate(items):
        key = "input" if item["type"] == "custom_tool_call" else "arguments"
        prefix = ("response.custom_tool_call_input." if key == "input"
                  else "response.function_call_arguments.")
        events.append({"type": prefix + "done", "item_id": item["id"],
                       "output_index": index, key: item[key]})
        events.append({"type": "response.output_item.done", "output_index": index, "item": item})
    events.append({"type": "response.completed", "response": response(*items)})
    return events


def wire(events, newline=b"\n"):
    return b"".join(b"event: " + e["type"].encode() + newline +
                    b"data: " + dumps(e).encode() + newline + newline for e in events)


def text_events(text, *, part_type="output_text"):
    reasoning = part_type in {"summary_text", "reasoning_text"}
    collection = "summary" if part_type == "summary_text" else "content"
    index_key = "summary_index" if collection == "summary" else "content_index"
    value_key = "refusal" if part_type == "refusal" else "text"
    part = {"type": part_type, value_key: text}
    item = {"id": "text_1", "type": "reasoning" if reasoning else "message",
            "status": "completed", collection: [part]}
    if not reasoning:
        item["role"] = "assistant"
    prefix = {"output_text": "response.output_text", "refusal": "response.refusal",
              "summary_text": "response.reasoning_summary_text",
              "reasoning_text": "response.reasoning_text"}[part_type]
    common = {"item_id": item["id"], "output_index": 0, index_key: 0}
    events = [{"type": "response.created", "response": response(status="in_progress")},
              {"type": "response.output_item.added", "output_index": 0,
               "item": {**item, "status": "in_progress", collection: []}}]
    part_prefix = ("response.reasoning_summary_part" if collection == "summary"
                   else "response.content_part")
    if part_prefix:
        events.append({"type": part_prefix + ".added", **common, "part": {**part, value_key: ""}})
    for char in text:
        events.append({"type": prefix + ".delta", **common, "delta": char})
    events.append({"type": prefix + ".done", **common, value_key: text})
    if part_prefix:
        events.append({"type": part_prefix + ".done", **common, "part": part})
    events.extend([{"type": "response.output_item.done", "output_index": 0, "item": item},
                   {"type": "response.completed", "response": response(item)}])
    return events


class SSEDecoderTests(unittest.TestCase):
    def test_large_fragmented_line_and_many_short_lines_preserve_exact_text(self):
        event = {"type": "response.output_text.delta", "delta": "中文\\\"\r\n" * 24000}
        data = b": transport comment\n\n" * 4096 + wire([event], b"\r\n")
        decoder, result = SSEDecoder(), []
        for start in range(0, len(data), 127):
            result.extend(decoder.feed(data[start:start + 127]))
        result.extend(decoder.finish())
        self.assertEqual(result, [event])

    def test_every_utf8_byte_boundary_crlf_lf_cr_and_bom(self):
        event = {"type": "response.output_text.delta", "delta": CODE}
        for newline in (b"\n", b"\r\n", b"\r"):
            data = b"\xef\xbb\xbf" + wire([event], newline) + b"data: [DONE]" + newline + newline
            for width in (1, 2, 3, 7, 64, len(data)):
                with self.subTest(newline=newline, width=width):
                    parser, result = SSEDecoder(), []
                    for start in range(0, len(data), width):
                        result.extend(parser.feed(data[start:start + width]))
                    result.extend(parser.finish())
                    self.assertEqual(result, [event, DONE])

    def test_comments_multiline_json_transport_ids_and_truncation(self):
        decoder = SSEDecoder()
        result = decoder.feed(b': keepalive\n\nid: unused\nretry: 1\n'
                              b'data: {"type":\ndata: "response.created"}\n\n')
        self.assertEqual(result, [{"type": "response.created"}])
        decoder.finish()
        for data in (b'data: {"type":"response.created"}',
                     b'data: {"type":"response.created"}\n',
                     b'event: wrong\ndata: {"type":"response.created"}\n\n',
                     b'data: {"type":"response.created","type":"error"}\n\n',
                     b'data: \xff\n\n'):
            with self.subTest(data=data), self.assertRaises(RouterError):
                parser = SSEDecoder()
                parser.feed(data)
                parser.finish()

    def test_record_limit_applies_across_chunks_and_data_lines(self):
        for chunks in ([b"data: " + b"x" * 32], [b"data: x\n"] * 8):
            with self.assertRaises(UpstreamProtocolError):
                parser = SSEDecoder(maximum=24)
                for chunk in chunks:
                    parser.feed(chunk)


class EventAdapterTests(unittest.TestCase):
    def test_content_part_types_follow_their_owning_output_item(self):
        _, context = prepare()
        for owner, wrong in (("reasoning_text", "output_text"), ("output_text", "reasoning_text")):
            events = text_events("synthetic", part_type=owner)
            next(e for e in events if e['type'] == 'response.content_part.added')['part']['type'] = wrong
            with self.subTest(owner=owner), self.assertRaises(UpstreamProtocolError):
                adapter = ResponsesEventAdapter(context)
                for event in events:
                    adapter.feed(event)

    def test_text_failures_have_fixed_content_free_diagnostic_reasons(self):
        _, context = prepare()
        events = text_events("synthetic secret text")
        next(e for e in events if e['type'] == 'response.output_text.done')['text'] = 'other private text'
        adapter = ResponsesEventAdapter(context)
        with self.assertRaises(UpstreamProtocolError) as caught:
            for event in events:
                adapter.feed(event)
        self.assertEqual(protocol_reason(caught.exception), 'text_snapshot_mismatch')
        self.assertIsNone(protocol_reason(RouterError('synthetic secret text')))

    def test_text_whitespace_and_unicode_are_preserved_at_every_snapshot(self):
        _, context = prepare()
        for part_type in ("output_text", "refusal", "summary_text", "reasoning_text"):
            for text in ("\n\n正文\r\n  尾行\t", "", "x"):
                with self.subTest(part_type=part_type, text=text):
                    events = text_events(text, part_type=part_type)
                    adapter, restored = ResponsesEventAdapter(context), []
                    for event in events:
                        restored.extend(adapter.feed(event))
                    adapter.finish()
                    self.assertEqual([{k: v for k, v in e.items() if k != "sequence_number"}
                                      for e in restored], events)

    def test_inconsistent_text_done_part_and_final_snapshot_are_rejected(self):
        _, context = prepare()
        for part_type in ("output_text", "refusal", "summary_text", "reasoning_text"):
            original = text_events("\n\nOK", part_type=part_type)
            value_key = "refusal" if part_type == "refusal" else "text"
            collection = "summary" if part_type == "summary_text" else "content"
            variants = []
            delta = deepcopy(original)
            next(e for e in delta if e["type"].endswith(".delta"))["delta"] = "extra"
            variants.append(delta)
            done = deepcopy(original)
            next(e for e in done if value_key in e)[value_key] = "OK"
            variants.append(done)
            item = deepcopy(original)
            item[-2]["item"][collection][0][value_key] = "changed"
            # Even two mutually consistent final snapshots must match the deltas.
            item[-1]["response"]["output"][0][collection][0][value_key] = "changed"
            variants.append(item)
            for events in variants:
                with self.subTest(part_type=part_type), self.assertRaises(UpstreamProtocolError):
                    adapter = ResponsesEventAdapter(context)
                    for event in events:
                        adapter.feed(event)

    def test_duplicate_done_late_delta_and_invalid_text_index_are_rejected(self):
        _, context = prepare()
        original = text_events("OK")
        at = next(i for i, e in enumerate(original) if e["type"] == "response.output_text.done")
        variants = [original[:at + 1] + [original[at]] + original[at + 1:],
                    original[:at + 1] + [original[3]] + original[at + 1:]]
        for index in (-1, True, "0", 9):
            events = deepcopy(original)
            events[3]["content_index"] = index
            variants.append(events)
        for events in variants:
            with self.subTest(events=events), self.assertRaises(UpstreamProtocolError):
                adapter = ResponsesEventAdapter(context)
                for event in events:
                    adapter.feed(event)

    def test_inconsistent_text_prevents_buffered_tool_release(self):
        _, context = prepare()
        tool_events = events_for(call(context))
        text = text_events("\n\nOK")
        for event in text[1:-1]:
            event["output_index"] = 1
        next(e for e in text if e["type"] == "response.output_text.done")["text"] = "OK"
        tool_events[-1]["response"]["output"].extend(text[-1]["response"]["output"])
        events = tool_events[:-1] + text[1:-1] + tool_events[-1:]
        adapter, delivered = ResponsesEventAdapter(context), []
        with self.assertRaises(UpstreamProtocolError):
            for event in events:
                delivered.extend(adapter.feed(event))
        self.assertFalse(any(e.get("item", {}).get("type") in {"function_call", "custom_tool_call"}
                             for e in delivered))
        self.assertFalse(any(e["type"] == "response.completed" for e in delivered))

    def test_prefilled_text_is_checked_without_rewriting_or_duplicate_prefix(self):
        _, context = prepare()
        original = text_events("OK")
        original[1]["item"]["content"] = [{"type": "output_text", "text": "O"}]
        original[2]["part"]["text"] = "O"
        del original[3]  # only the K delta follows the already observed O prefix
        adapter, delivered = ResponsesEventAdapter(context), []
        for event in original:
            delivered.extend(adapter.feed(event))
        self.assertEqual([{k: v for k, v in e.items() if k != "sequence_number"}
                          for e in delivered], original)
        snapshots = [original[0], original[1], original[-2], original[-1]]
        with self.assertRaises(UpstreamProtocolError):
            adapter = ResponsesEventAdapter(context)
            for event in snapshots:
                adapter.feed(event)

    def test_interleaved_content_parts_are_compared_independently(self):
        _, context = prepare()
        left, right = text_events("\n\n甲"), text_events("乙\r\n")
        for event in right[2:-2]:
            event["content_index"] = 1
        final_item = deepcopy(left[-2]["item"])
        final_item["content"].extend(right[-2]["item"]["content"])
        bodies = []
        for a, b in zip(left[2:-2], right[2:-2]):
            bodies.extend([a, b])
        events = left[:2] + bodies + [{"type": "response.output_item.done", "output_index": 0,
                                     "item": final_item},
                                    {"type": "response.completed", "response": response(final_item)}]
        adapter, restored = ResponsesEventAdapter(context), []
        for event in events:
            restored.extend(adapter.feed(event))
        adapter.finish()
        self.assertEqual(restored[-1]["response"]["output"][0], final_item)

    def test_no_executable_item_before_success_and_complete_ordered_restoration(self):
        _, context = prepare({"tools": [EXEC, FUNCTION]})
        items = [call(context), {"type": "function_call", "name": "add", "arguments": '{"a":17}',
                                 "status": "completed", "call_id": "call_2", "id": "fc_2"}]
        events = events_for(*items)
        adapter = ResponsesEventAdapter(context)
        result = []
        for event in events[:-1]:
            result.extend(adapter.feed(event))
        self.assertEqual([e["type"] for e in result], ["response.created"])
        result.extend(adapter.feed(events[-1]))
        adapter.finish()
        done = [e["item"] for e in result if e["type"] == "response.output_item.done"]
        self.assertEqual(done[0]["input"], CODE)
        self.assertEqual(done[1], items[1])
        self.assertEqual(result[-1]["response"]["output"], done)
        self.assertEqual([e["sequence_number"] for e in result], list(range(len(result))))
        delta = [e["delta"] for e in result if e["type"] == "response.custom_tool_call_input.delta"]
        self.assertEqual(delta, [CODE])
        self.assertEqual(adapter.items, {})

    def test_text_flows_before_tool_commit(self):
        _, context = prepare()
        tool = call(context)
        message = {"id": "msg_1", "type": "message", "role": "assistant", "status": "completed",
                   "content": [{"type": "output_text", "text": "Checking."}]}
        events = events_for(tool)
        events.insert(1, {"type": "response.output_item.added", "output_index": 1,
                          "item": {**message, "status": "in_progress", "content": []}})
        events.insert(2, {"type": "response.output_text.delta", "output_index": 1,
                          "item_id": "msg_1", "content_index": 0, "delta": "Checking."})
        events.insert(-1, {"type": "response.output_item.done", "output_index": 1, "item": message})
        events[-1]["response"]["output"].append(message)
        adapter = ResponsesEventAdapter(context)
        before = []
        for event in events[:-1]:
            before.extend(adapter.feed(event))
        self.assertTrue(any(e["type"] == "response.output_text.delta" for e in before))
        self.assertFalse(any(e.get("item", {}).get("type") == "custom_tool_call" for e in before))
        adapter.feed(events[-1])
        adapter.finish()

    def test_failure_incomplete_error_and_truncation_never_commit_tool(self):
        _, context = prepare()
        events = events_for(call(context))
        for terminal in ("response.failed", "response.incomplete", "error", None):
            adapter = ResponsesEventAdapter(context)
            delivered = []
            for event in events[:-1]:
                delivered.extend(adapter.feed(event))
            with self.subTest(terminal=terminal), self.assertRaises(UpstreamProtocolError):
                if terminal:
                    adapter.feed({"type": terminal, "response": response(call(context), status="failed")})
                else:
                    adapter.finish()
            self.assertEqual([e["type"] for e in delivered], ["response.created"])

    def test_conflicting_ids_arguments_final_items_and_duplicate_events_stop(self):
        _, context = prepare()
        original = events_for(call(context))
        corruptions = []
        for at, changes in ((2, {"item_id": "wrong"}), (2, {"output_index": 9}),
                            (2, {"delta": "wrong"}), (5, {"arguments": "{}"})):
            events = deepcopy(original)
            events[at].update(changes)
            corruptions.append(events)
        mismatch = deepcopy(original)
        mismatch[-1]["response"]["output"][0]["arguments"] = '{"input":"changed"}'
        corruptions.append(mismatch)
        mismatch = deepcopy(original)
        mismatch[-1]["response"]["id"] = "other"
        corruptions.append(mismatch)
        corruptions.append(original[:2] + [original[1]] + original[2:])
        corruptions.append(original[:-1] + [original[-2], original[-1]])
        corruptions.append(original + [original[-1]])
        for events in corruptions:
            with self.subTest(events=events), self.assertRaises(UpstreamProtocolError):
                adapter = ResponsesEventAdapter(context)
                for event in events:
                    adapter.feed(event)

    def test_malformed_wrapper_cannot_commit_even_with_success_terminal(self):
        _, context = prepare()
        item = {**call(context), "arguments": '{"input":42}'}
        adapter = ResponsesEventAdapter(context)
        events = events_for(item)
        for event in events[:-1]:
            adapter.feed(event)
        with self.assertRaises(UpstreamProtocolError):
            adapter.feed(events[-1])

    def test_argument_and_total_stream_limits(self):
        _, context = prepare()
        events = events_for(call(context))
        with patch("operator_core.responses_events.MAX_ARGUMENT_BYTES", 8):
            adapter = ResponsesEventAdapter(context)
            with self.assertRaises(UpstreamProtocolError):
                for event in events:
                    adapter.feed(event)
        with patch("operator_core.responses_events.MAX_STREAM_BYTES", 32):
            with self.assertRaises(UpstreamProtocolError):
                ResponsesEventAdapter(context).feed(events[0])


class StreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_text_snapshots_survive_single_byte_utf8_wire_fragmentation(self):
        _, context = prepare()
        original = text_events("\n\n中文\r\n末行\t")
        data = wire(original, b"\r\n") + b"data: [DONE]\r\n\r\n"
        async def chunks():
            for value in data:
                yield bytes([value])
        result = [e async for e in restore_events(chunks(), context)]
        self.assertEqual([{k: v for k, v in e.items() if k != "sequence_number"} for e in result], original)

    async def test_shared_async_adapter_handles_single_byte_chunks(self):
        _, context = prepare()
        data = wire(events_for(call(context))) + b"data: [DONE]\n\n"
        async def chunks():
            for value in data:
                yield bytes([value])
        result = [e async for e in restore_events(chunks(), context)]
        self.assertEqual(result[-1]["response"]["output"][0]["input"], CODE)

    async def test_done_without_success_and_trailing_events_are_rejected(self):
        _, context = prepare()
        streams = [b"data: [DONE]\n\n",
                   wire(events_for(call(context))[:-1]),
                   wire(events_for(call(context))) + b"data: [DONE]\n\n" +
                   b'data: {"type":"response.completed"}\n\n']
        for data in streams:
            async def chunks():
                yield data
            with self.assertRaises(UpstreamProtocolError):
                [e async for e in restore_events(chunks(), context)]


if __name__ == "__main__":
    unittest.main()
