"""Bounded Responses SSE decoding and success-gated tool event restoration."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import re

from .responses_capabilities import RouterError, UpstreamProtocolError
from .responses_tool_adapter import (
    MAX_ARGUMENT_BYTES, MAX_OUTPUT_ITEMS, bounded_string, dumps, identifier, loads, restore_response,
)

MAX_EVENT_BYTES = 16 * 1024 * 1024
MAX_STREAM_BYTES = 64 * 1024 * 1024
MAX_ITEMS = MAX_OUTPUT_ITEMS
TOOL_CALLS = {"function_call", "custom_tool_call"}
ARGUMENT_EVENTS = {"response.function_call_arguments.delta", "response.function_call_arguments.done",
                   "response.custom_tool_call_input.delta", "response.custom_tool_call_input.done"}
TEXT_EVENTS = {
    "response.content_part.added", "response.content_part.done",
    "response.output_text.delta", "response.output_text.done",
    "response.refusal.delta", "response.refusal.done",
    "response.reasoning_summary_part.added", "response.reasoning_summary_part.done",
    "response.reasoning_summary_text.delta", "response.reasoning_summary_text.done",
    "response.reasoning_text.delta", "response.reasoning_text.done",
}
DONE = object()
LINE_END = re.compile(br"\r\n|\r|\n")


class SSEDecoder:
    """Accept arbitrary wire chunks, UTF-8, CRLF/LF/CR, comments and multi-line data."""

    def __init__(self, maximum=MAX_EVENT_BYTES):
        self.maximum = maximum
        self.pending = bytearray()
        self.data = []
        self.event_type = None
        self.size = 0
        self.first_line = True
        self.scan_from = 0

    def feed(self, chunk):
        self.pending.extend(chunk)
        result = []
        start = 0
        while True:
            ending = LINE_END.search(self.pending, self.scan_from)
            if ending is None:
                self.scan_from = len(self.pending)
                break
            end, stop = ending.span()
            if self.pending[end] == 13 and stop == end + 1 and stop == len(self.pending):
                # A final CR may become CRLF with the next wire chunk.
                self.scan_from = end
                break
            self.scan_from = stop
            line = bytes(self.pending[start:end])
            self.size += stop - start
            if self.size > self.maximum:
                raise UpstreamProtocolError("external_event_too_large_no_retry")
            start = stop
            if self.first_line:
                line = line.removeprefix(b"\xef\xbb\xbf")
                self.first_line = False
            if not line:
                if self.data:
                    data = b"\n".join(self.data)
                    if data == b"[DONE]":
                        result.append(DONE)
                    else:
                        event = loads(data)
                        if (not isinstance(event, dict) or not isinstance(event.get("type"), str)
                                or (self.event_type is not None and
                                    self.event_type != event["type"].encode("utf-8"))):
                            raise UpstreamProtocolError("invalid_external_sse_event_no_retry")
                        result.append(event)
                self.data, self.event_type, self.size = [], None, 0
            elif not line.startswith(b":"):
                name, _, value = line.partition(b":")
                value = value.removeprefix(b" ")
                if name == b"data":
                    self.data.append(value)
                elif name == b"event":
                    if self.event_type is not None and self.event_type != value:
                        raise UpstreamProtocolError("conflicting_sse_event_type_no_retry")
                    self.event_type = value
                # id/retry are SSE transport metadata, never a reason to reconnect.
        if start:
            del self.pending[:start]
            self.scan_from -= start
        if self.size + len(self.pending) > self.maximum:
            raise UpstreamProtocolError("external_event_too_large_no_retry")
        return result

    def finish(self):
        events = self.feed(b"\n") if self.pending.endswith(b"\r") else []
        if self.pending or self.data:
            raise UpstreamProtocolError("truncated_external_sse_no_retry")
        return events


@dataclass
class TextState:
    """Compare observed UTF-8 bytes without retaining another copy of the text."""
    kind: str
    digest: object = field(default_factory=hashlib.sha256)
    size: int = 0
    observed: bool = False
    saw_delta: bool = False
    added: bool = False
    text_done: bool = False
    part_done: bool = False
    snapshot: tuple | None = None

    def append(self, text, *, delta=False):
        raw = bounded_string(text, maximum=MAX_EVENT_BYTES).encode("utf-8")
        self.size += len(raw)
        if self.size > MAX_STREAM_BYTES:
            raise RouterError("text_part_too_large")
        self.digest.update(raw)
        self.observed = self.observed or bool(raw) or delta
        self.saw_delta = self.saw_delta or delta

    def check(self, text):
        raw = bounded_string(text, maximum=MAX_EVENT_BYTES).encode("utf-8")
        fingerprint = (len(raw), hashlib.sha256(raw).digest())
        if ((self.observed and fingerprint != (self.size, self.digest.digest()))
                or (self.snapshot is not None and fingerprint != self.snapshot)):
            raise RouterError("text_snapshot_mismatch")
        return fingerprint


@dataclass
class OutputState:
    added: dict
    done: dict | None = None
    pieces: list = field(default_factory=list)
    argument_bytes: int = 0
    arguments_done: str | None = None
    saw_delta: bool = False
    text_parts: dict = field(default_factory=dict)

    def arguments(self):
        return "".join(self.pieces)


class ResponsesEventAdapter:
    def __init__(self, context):
        self.context = context
        self.response_id = None
        self.items = {}
        self.ids = {}
        self.sequence = 0
        self.terminal = False
        self.total = 0

    def _emit(self, event):
        result = {**deepcopy(event), "sequence_number": self.sequence}
        self.sequence += 1
        return result

    def feed(self, event):
        try:
            return self._feed(event)
        except (RouterError, ValueError, TypeError, KeyError, UnicodeError, RecursionError) as exc:
            raise UpstreamProtocolError("invalid_upstream_tool_stream_no_retry") from exc

    def _state(self, event):
        index = event.get("output_index")
        if type(index) is not int or index not in self.items:
            raise RouterError("unknown_output_index")
        state = self.items[index]
        if event.get("item_id") != state.added.get("id"):
            raise RouterError("mismatched_event_item_id")
        return state

    def _text_state(self, state, collection, index, kind):
        if type(index) is not int or not 0 <= index < MAX_ITEMS:
            raise RouterError("invalid_text_part_index")
        key = (collection, index)
        if key not in state.text_parts:
            part = TextState(kind)
            initial = state.added.get(collection, [])
            if state.added["type"] == "reasoning" and collection == "content" and initial is None:
                initial = []
            if not isinstance(initial, list):
                raise RouterError("invalid_initial_text_parts")
            if index < len(initial):
                entry = initial[index]
                if not isinstance(entry, dict) or entry.get("type") != kind:
                    raise RouterError("text_part_type_changed")
                part.append(entry.get("refusal" if kind == "refusal" else "text"))
            state.text_parts[key] = part
        part = state.text_parts[key]
        if part.kind != kind:
            raise RouterError("text_part_type_changed")
        return part

    def _track_text(self, state, event):
        kind = event["type"]
        summary = kind.startswith("response.reasoning_summary_")
        is_part = kind.startswith(("response.content_part.", "response.reasoning_summary_part."))
        reasoning = (summary or kind.startswith("response.reasoning_text.")
                     or (is_part and state.added["type"] == "reasoning"))
        if state.added["type"] != ("reasoning" if reasoning else "message"):
            raise RouterError("text_event_item_type_mismatch")
        collection = "summary" if summary else "content"
        index = event.get("summary_index" if summary else "content_index")
        entry = event.get("part") if is_part else None
        if is_part:
            allowed = ({"summary_text"} if summary else {"reasoning_text"} if reasoning
                       else {"output_text", "refusal"})
            if not isinstance(entry, dict) or entry.get("type") not in allowed:
                raise RouterError("invalid_text_part")
            part_kind = entry["type"]
        else:
            part_kind = ("summary_text" if summary else "reasoning_text" if reasoning
                         else "refusal" if kind.startswith("response.refusal.") else "output_text")
        part = self._text_state(state, collection, index, part_kind)
        key = "refusal" if part_kind == "refusal" else "text"
        if kind.endswith(".added"):
            if part.added or part.text_done or part.part_done or part.saw_delta:
                raise RouterError("duplicate_or_late_text_part")
            if part.observed:
                # The initial item may already contain the identical prefix.
                part.check(entry.get(key))
            else:
                part.append(entry.get(key))
            part.added = True
        elif kind.endswith(".delta"):
            if part.text_done or part.part_done:
                raise RouterError("text_delta_after_done")
            part.append(event.get("delta"), delta=True)
        else:
            if part.part_done or (not is_part and part.text_done):
                raise RouterError("duplicate_or_late_text_done")
            part.snapshot = part.check(entry.get(key) if is_part else event.get(key))
            if is_part:
                part.part_done = True
            else:
                part.text_done = True

    def _check_text_item(self, state, item):
        for (collection, index), part in state.text_parts.items():
            entries = item.get(collection)
            if not isinstance(entries, list) or index >= len(entries):
                raise RouterError("final_text_part_missing")
            entry = entries[index]
            if not isinstance(entry, dict) or entry.get("type") != part.kind:
                raise RouterError("final_text_part_type_mismatch")
            part.check(entry.get("refusal" if part.kind == "refusal" else "text"))

    def _feed(self, event):
        if not isinstance(event, dict) or self.terminal:
            raise RouterError("event_after_terminal_or_invalid_event")
        self.total += len(dumps(event).encode("utf-8"))
        if self.total > MAX_STREAM_BYTES:
            raise RouterError("adapted_stream_too_large")
        kind = event.get("type")
        if kind in {"response.failed", "response.incomplete", "error"}:
            raise RouterError("upstream_unsuccessful_terminal")
        if kind in {"response.created", "response.in_progress", "response.queued"}:
            response = event.get("response")
            if not isinstance(response, dict) or response.get("output") != []:
                raise RouterError("invalid_initial_response")
            response_id = identifier(response.get("id"))
            if self.response_id is not None and self.response_id != response_id:
                raise RouterError("response_id_changed")
            if kind == "response.created" and self.response_id is not None:
                raise RouterError("duplicate_response_created")
            self.response_id = response_id
            return [self._emit(event)]
        if self.response_id is None:
            raise RouterError("response_created_missing")
        if kind == "response.output_item.added":
            index, item = event.get("output_index"), event.get("item")
            if type(index) is not int or not 0 <= index < MAX_ITEMS or index in self.items:
                raise RouterError("invalid_or_duplicate_output_index")
            if not isinstance(item, dict) or item.get("type") not in TOOL_CALLS | {"message", "reasoning"}:
                raise RouterError("unsupported_output_item")
            item_id = identifier(item.get("id"))
            if item_id in self.ids:
                raise RouterError("duplicate_item_id")
            state = OutputState(deepcopy(item))
            self.items[index], self.ids[item_id] = state, index
            if item["type"] in TOOL_CALLS:
                argument = item.get("arguments" if item["type"] == "function_call" else "input", "")
                bounded_string(argument)
                state.pieces.append(argument)
                state.argument_bytes = len(argument.encode("utf-8"))
                return []
            for collection in ("content", "summary"):
                initial = item.get(collection, [])
                if item["type"] == "reasoning" and collection == "content" and initial is None:
                    continue
                if not isinstance(initial, list) or len(initial) > MAX_ITEMS:
                    raise RouterError("invalid_initial_text_parts")
                allowed = ({"output_text", "refusal"} if item["type"] == "message"
                           else {"summary_text"} if collection == "summary" else {"reasoning_text"})
                for part_index, entry in enumerate(initial):
                    if not isinstance(entry, dict) or entry.get("type") not in allowed:
                        raise RouterError("invalid_initial_text_part")
                    self._text_state(state, collection, part_index, entry["type"])
            return [self._emit(event)]
        if kind in ARGUMENT_EVENTS:
            state = self._state(event)
            if state.added["type"] not in TOOL_CALLS or state.done is not None:
                raise RouterError("unexpected_arguments_event")
            expected = ("response.function_call_arguments." if state.added["type"] == "function_call"
                        else "response.custom_tool_call_input.")
            if not kind.startswith(expected) or state.arguments_done is not None:
                raise RouterError("conflicting_arguments_events")
            if kind.endswith(".delta"):
                value = bounded_string(event.get("delta"))
                state.argument_bytes += len(value.encode("utf-8"))
                if state.argument_bytes > MAX_ARGUMENT_BYTES:
                    raise RouterError("tool_arguments_too_large")
                state.pieces.append(value)
                state.saw_delta = True
            else:
                key = "arguments" if state.added["type"] == "function_call" else "input"
                value = bounded_string(event.get(key))
                if (state.saw_delta or state.arguments()) and state.arguments() != value:
                    raise RouterError("arguments_done_mismatch")
                state.arguments_done = value
            return []
        if kind == "response.output_item.done":
            item, index = event.get("item"), event.get("output_index")
            if not isinstance(item, dict):
                raise RouterError("invalid_done_item")
            state = self._state({"item_id": item.get("id"), "output_index": index})
            if state.done is not None or item.get("type") != state.added["type"]:
                raise RouterError("conflicting_output_done")
            for key in ("name", "call_id"):
                if state.added.get(key) and state.added[key] != item.get(key):
                    raise RouterError("tool_identity_changed")
            self._check_text_item(state, item)
            state.done = deepcopy(item)
            if item["type"] in TOOL_CALLS:
                key = "arguments" if item["type"] == "function_call" else "input"
                value = bounded_string(item.get(key))
                if ((state.saw_delta or state.arguments()) and state.arguments() != value
                        or state.arguments_done is not None and state.arguments_done != value):
                    raise RouterError("final_tool_arguments_mismatch")
                return []
            return [self._emit(event)]
        if kind in TEXT_EVENTS:
            state = self._state(event)
            if state.added["type"] in TOOL_CALLS or state.done is not None:
                raise RouterError("text_event_for_tool_or_finished_item")
            self._track_text(state, event)
            return [self._emit(event)]
        if kind == "response.completed":
            response = event.get("response")
            if not isinstance(response, dict) or response.get("id") != self.response_id:
                raise RouterError("response_id_changed")
            restored = restore_response(response, self.context)
            output = response["output"]
            if set(self.items) != set(range(len(output))):
                raise RouterError("final_output_indices_mismatch")
            result = []
            for index, item in enumerate(output):
                state = self.items[index]
                if state.done != item:
                    raise RouterError("final_response_item_mismatch")
                if item["type"] not in TOOL_CALLS:
                    continue
                restored_item = restored["output"][index]
                added = deepcopy(restored_item)
                added["status"] = "in_progress"
                key = "input" if restored_item["type"] == "custom_tool_call" else "arguments"
                value = restored_item[key]
                added[key] = {} if restored_item["type"] == "tool_search_call" else ""
                result.append(self._emit({"type": "response.output_item.added",
                                         "output_index": index, "item": added}))
                # Client search arguments are objects and arrive on output_item.done.
                if restored_item["type"] != "tool_search_call":
                    prefix = ("response.custom_tool_call_input." if key == "input"
                              else "response.function_call_arguments.")
                    common = {"item_id": item["id"], "output_index": index}
                    result.append(self._emit({"type": prefix + "delta", **common, "delta": value}))
                    result.append(self._emit({"type": prefix + "done", **common, key: value}))
                result.append(self._emit({"type": "response.output_item.done",
                                         "output_index": index, "item": restored_item}))
            result.append(self._emit({**event, "response": restored}))
            self.terminal = True
            # Release buffered argument strings as soon as the terminal batch is built.
            self.items.clear()
            self.ids.clear()
            return result
        raise RouterError("unsupported_responses_event")

    def finish(self):
        if not self.terminal:
            raise UpstreamProtocolError("truncated_upstream_tool_stream_no_retry")


def completed_response_events(response):
    """Serialize an already validated JSON response, never repair an observed stream.

    Callers must run restore_response on the entire successful response first.
    These events all become available after completion; they are not token timing.
    """
    # JSON passthrough can carry shapes that have no lossless text-event form.
    # Check every item before even the first event, including items after a tool.
    for item in response["output"]:
        if item["type"] not in {"message", "reasoning"}:
            continue
        for collection in ("summary", "content"):
            if collection not in item:
                continue
            parts = item[collection]
            if item["type"] == "reasoning" and collection == "content" and parts is None:
                continue
            allowed = ({"summary_text"} if collection == "summary" and item["type"] == "reasoning"
                       else {"reasoning_text"} if item["type"] == "reasoning"
                       else {"output_text", "refusal"} if collection == "content" else set())
            if not isinstance(parts, list) or len(parts) > MAX_ITEMS:
                raise UpstreamProtocolError("invalid_json_event_content")
            for part in parts:
                if not isinstance(part, dict) or part.get("type") not in allowed:
                    raise UpstreamProtocolError("invalid_json_event_content")
                try:
                    bounded_string(part.get("refusal" if part["type"] == "refusal" else "text"),
                                   maximum=MAX_EVENT_BYTES)
                except RouterError as exc:
                    raise UpstreamProtocolError("invalid_json_event_content") from exc
    # Item/part/done snapshots repeat text and metadata, so a bounded JSON
    # response can expand past the stream budget. Check every actual event
    # before releasing even the first one, including any earlier tool calls.
    # Two passes retain only one generated event, not an expanded event list.
    total = 0
    for event in _completed_response_events(response):
        size = len(dumps(event).encode("utf-8"))
        if size > MAX_EVENT_BYTES:
            raise UpstreamProtocolError("external_event_too_large_no_retry")
        total += size
        if total > MAX_STREAM_BYTES:
            raise UpstreamProtocolError("adapted_stream_too_large")
    yield from _completed_response_events(response)


def _completed_response_events(response):
    """Deterministic event projection; callers validate before exposing output."""
    sequence = 0

    def event(kind, **fields):
        nonlocal sequence
        result = {"type": kind, "sequence_number": sequence, **fields}
        sequence += 1
        return result

    initial = {**response, "status": "in_progress", "output": []}
    if "completed_at" in initial:
        initial["completed_at"] = None
    yield event("response.created", response=initial)
    yield event("response.in_progress", response=deepcopy(initial))
    for index, item in enumerate(response["output"]):
        added = deepcopy(item)
        if "status" in added:
            added["status"] = "in_progress"
        kind = item["type"]
        if kind in {"function_call", "custom_tool_call", "tool_search_call"}:
            key = "input" if kind == "custom_tool_call" else "arguments"
            added[key] = {} if kind == "tool_search_call" else ""
        else:
            for collection in ("content", "summary"):
                if collection in added and added[collection] is not None:
                    added[collection] = []
        yield event("response.output_item.added", output_index=index, item=added)
        common = {"output_index": index, "item_id": item["id"]}
        if kind in {"function_call", "custom_tool_call"}:
            prefix = "response.custom_tool_call_input" if key == "input" else "response.function_call_arguments"
            yield event(prefix + ".delta", **common, delta=item[key])
            yield event(prefix + ".done", **common, **{key: item[key]})
        elif kind in {"message", "reasoning"}:
            for collection in ("summary", "content"):
                parts = item.get(collection)
                if parts is None:
                    continue
                for part_index, part in enumerate(parts):
                    summary = collection == "summary"
                    part_fields = {**common, "summary_index" if summary else "content_index": part_index}
                    part_prefix = "response.reasoning_summary_part" if summary else "response.content_part"
                    text_key = "refusal" if part["type"] == "refusal" else "text"
                    text_prefix = {"output_text": "response.output_text", "refusal": "response.refusal",
                                   "reasoning_text": "response.reasoning_text",
                                   "summary_text": "response.reasoning_summary_text"}[part["type"]]
                    yield event(part_prefix + ".added", **part_fields, part={**part, text_key: ""})
                    yield event(text_prefix + ".delta", **part_fields, delta=part[text_key])
                    yield event(text_prefix + ".done", **part_fields, **{text_key: part[text_key]})
                    yield event(part_prefix + ".done", **part_fields, part=part)
        yield event("response.output_item.done", output_index=index, item=item)
    yield event("response.completed", response=response)


async def restore_events(chunks, context):
    """Shared by HTTP and WebSocket. Cancellation closes the caller's upstream."""
    from .responses_metrics import measure_current
    decoder, adapter, done = SSEDecoder(), ResponsesEventAdapter(context), False

    def consume(events):
        nonlocal done
        output = []
        for event in events:
            if done:
                raise UpstreamProtocolError("event_after_done_no_retry")
            if event is DONE:
                adapter.finish()
                done = True
            else:
                output.extend(adapter.feed(event))
        return output

    try:
        async for chunk in chunks:
            with measure_current("response_adaptation"):
                output = consume(decoder.feed(chunk))
            for event in output:
                yield event
        with measure_current("response_adaptation"):
            output = consume(decoder.finish())
        for event in output:
            yield event
        adapter.finish()
    except RouterError as exc:
        raise UpstreamProtocolError("invalid_upstream_tool_stream_no_retry") from exc
