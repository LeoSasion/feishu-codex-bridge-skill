"""Explicit synthetic Responses probes; at most two requests, no tool execution."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import secrets
import time

from operator_core.model_registry import ModelRegistry, RouterError
from operator_core.model_router_config import atomic_write, read_registration
from operator_core.responses_tool_adapter import EXEC_GRAMMAR, dumps, loads


CASES = ("json", "sse", "required", "named", "structured", "unicode", "unicode-json", "long", "long-lines")


def probe_input(case):
    if case in {"unicode", "unicode-json"}:
        return 'text("中文😀\\\\path\\"quote");\r\ntext("line\\nnext");'
    if case == "long":
        return "// " + ("bounded synthetic source " * 192) + "\ntext(17 + 25);"
    if case == "long-lines":
        return "\n".join("// synthetic line " + str(index).zfill(3) + ": preserve order and characters"
                         for index in range(96)) + "\ntext(17 + 25);"
    return "text(17 + 25);"


def probe_usage(value):
    """Allow only known bounded counters, never provider-authored strings."""
    if not isinstance(value, dict):
        return None
    result = {}
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        count = value.get(key)
        if type(count) is int and 0 <= count <= 10000000:
            result[key] = count
    for field, key in (("input_tokens_details", "cached_tokens"),
                       ("output_tokens_details", "reasoning_tokens")):
        detail = value.get(field)
        count = detail.get(key) if isinstance(detail, dict) else None
        if type(count) is int and 0 <= count <= 10000000:
            result[field] = {key: count}
    return result


def reserve_receipt(directory, row, case, run_id):
    identity = dumps({"protocol": "responses-probe-v1", "registration": row,
                      "case": case, "run_id": run_id}).encode("utf-8")
    digest = hashlib.sha256(identity).hexdigest()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (digest + ".json")
    # May-have-started boundary precedes network I/O. Never reuse a pending receipt.
    with target.open("xb") as handle:
        handle.write(b'{"status":"may_have_started_no_retry"}\n')
        handle.flush()
        import os
        os.fsync(handle.fileno())
    return target


async def probe(row, case):
    import aiohttp
    from aiohttp import web
    from operator_core.model_router import MAX_BODY, ModelRouter
    from operator_core.responses_events import DONE, SSEDecoder

    if case not in CASES:
        raise RouterError("unknown_synthetic_probe_case")
    registry = ModelRegistry({"version": 2, "models": [row]}, json.loads(
        Path(__file__).with_name("operator_core").joinpath("beeper_model_catalog.json").read_text(encoding="utf-8")))
    route = registry.routes[row["slug"]]
    if route.responses is None:
        raise RouterError("probe_requires_explicit_responses_capabilities")
    router = ModelRouter(registry, secrets.token_hex(32))
    runner = web.AppRunner(router.app(), access_log=None, shutdown_timeout=2)
    report = {"case": case, "status": "failed", "requests": 0, "execution_performed": False,
              "stages": [], "configured_capabilities_are_not_verification": True}
    await runner.setup()
    started = time.monotonic()
    try:
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = runner.addresses[0][1]
        endpoint = f"http://127.0.0.1:{port}" + router.prefix + "/responses"
        tool = {"type": "custom", "name": "exec", "description":
                "Return JavaScript source as raw tool input. The test harness supplies a synthetic output.",
                "format": {"type": "grammar", "syntax": "lark", "definition": EXEC_GRAMMAR}}
        code = probe_input(case)
        prompt = ("Call exec exactly once with the following exact source, preserving whitespace "
                  "and characters. Do not compute the answer yourself. After the tool output arrives, "
                  "reply with only its verification value.\nSOURCE START\n" + code + "\nSOURCE END")
        if case == "unicode-json":
            prompt = ("Call exec exactly once using the decoded value of the following JSON string "
                      "as its raw source. Decode JSON escapes once, preserving CRLF, Unicode, "
                      "quotes and backslashes. This representation makes invisible characters explicit. "
                      "After the tool output arrives, reply with only its verification value.\n" + dumps(code))
        history = [{"role": "user", "content": prompt}]
        payload = {"model": row["slug"], "input": history, "tools": [tool],
                   "tool_choice": ({"type": "custom", "name": "exec"} if case == "named" else
                                   "required" if case == "required" else "auto"),
                   "max_output_tokens": 8192 if case in {"long", "long-lines"} else 2048,
                   "reasoning": {"effort": route.reasoning_efforts[0]}, "stream": case == "sse"}
        async with aiohttp.ClientSession(trust_env=False, auto_decompress=False,
                timeout=aiohttp.ClientTimeout(total=60)) as client:
            async def send(label, body):
                report["requests"] += 1
                stage = {"stage": label}
                report["stages"].append(stage)
                begin = time.monotonic()
                async with client.post(endpoint, data=dumps(body).encode(),
                        headers={"Content-Type": "application/json"}, allow_redirects=False) as result:
                    stage["http_status"] = result.status
                    raw = bytearray()
                    async for chunk in result.content.iter_chunked(65536):
                        raw.extend(chunk)
                        if len(raw) > MAX_BODY:
                            raise RouterError("probe_response_too_large")
                    stage["elapsed_ms"] = round((time.monotonic() - begin) * 1000)
                    if result.status != 200:
                        raise RouterError("probe_request_failed_no_retry")
                    if body.get("stream"):
                        parser = SSEDecoder()
                        events = parser.feed(raw) + parser.finish()
                        finals = [e["response"] for e in events if e is not DONE
                                  and e.get("type") == "response.completed"]
                        if len(finals) != 1:
                            raise RouterError("probe_success_terminal_missing")
                        value = finals[0]
                    else:
                        value = loads(raw)
                    stage["response_status"] = value.get("status")
                    stage["output_types"] = [item.get("type") for item in value.get("output", [])]
                    stage["usage"] = probe_usage(value.get("usage"))
                    return value

            initial = await send("custom_call", payload)
            calls = [item for item in initial.get("output", []) if item.get("type") == "custom_tool_call"]
            report["custom_call_count"] = len(calls)
            if len(calls) != 1 or calls[0].get("name") != "exec":
                raise RouterError("probe_expected_one_exec_call")
            report["input_exact"] = calls[0].get("input") == code
            report["input_bytes"] = len(code.encode("utf-8"))
            report["input_match_after_newline_normalization"] = (
                calls[0].get("input", "").replace("\r\n", "\n") == code.replace("\r\n", "\n"))
            actual = calls[0].get("input", "")
            report["actual_input_bytes"] = len(actual.encode("utf-8"))
            report["input_contains_requested_source"] = code in actual
            report["input_is_markdown_wrapped"] = actual.lstrip().startswith("```")
            report["input_equals_format_metadata"] = actual == dumps(tool["format"])
            report["input_is_nested_json_wrapper"] = False
            try:
                nested = loads(actual)
                report["input_is_nested_json_wrapper"] = isinstance(nested, dict) and nested.get("input") == code
            except RouterError:
                pass
            if not report["input_exact"]:
                raise RouterError("probe_model_changed_requested_source")
            verification = "OPERATOR_PROBE_" + secrets.token_hex(8)
            output = {"type": "custom_tool_call_output", "call_id": calls[0]["call_id"],
                      "output": dumps({"result": 42, "verification": verification})}
            if case == "structured":
                output["output"] = [{"type": "input_text", "text": output["output"]}]
            final = await send("synthetic_output_roundtrip", {
                **payload, "input": history + initial["output"] + [output], "tool_choice": "none"})
            text = "".join(part.get("text", "") for item in final["output"] if item.get("type") == "message"
                           for part in item.get("content", []) if part.get("type") == "output_text")
            report["verification_exact"] = text == verification
            report["verification_after_trim"] = text.strip() == verification
            if not report["verification_after_trim"]:
                raise RouterError("probe_synthetic_output_not_consumed")
            report["status"] = "passed"
    except Exception as exc:
        report["error"] = str(exc) if isinstance(exc, RouterError) else type(exc).__name__
        report["router_failure"] = router.last_failure
    finally:
        report["elapsed_ms"] = round((time.monotonic() - started) * 1000)
        await runner.cleanup()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", required=True, type=Path)
    parser.add_argument("--case", required=True, choices=CASES)
    parser.add_argument("--receipt-dir", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    row = read_registration(args.registration)
    catalog = json.loads(Path(__file__).with_name("operator_core").joinpath(
        "beeper_model_catalog.json").read_text(encoding="utf-8"))
    registry = ModelRegistry({"version": 2, "models": [row]}, catalog)
    # Check only the explicitly configured key. Never inspect another credential store.
    registry.routes[row["slug"]].key()
    receipt = reserve_receipt(args.receipt_dir, row, args.case, args.run_id)
    report = asyncio.run(probe(row, args.case))
    atomic_write(receipt, (dumps(report) + "\n").encode())
    print(dumps(report), flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        raise SystemExit("Probe refused or stopped; no automatic retry. Inspect the private receipt.")
