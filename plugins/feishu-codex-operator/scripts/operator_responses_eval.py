"""Explicit bounded live CLI evaluation in disposable state. Never an answer transport."""

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
import time

from operator_core.model_registry import ModelRegistry, RouterError
from operator_core.model_router_config import atomic_write, read_registration
from operator_core.responses_profiles import adapter_digest, contract_digest, evaluator_digest, preflight
from operator_core.responses_profiles import FINAL_TEXT_POLICIES
from operator_core.responses_tool_adapter import dumps
from operator_responses_probe import reserve_receipt

STOP_CASES = frozenset({"cli_error_stop", "cli_exit_stop"})
CASES = ("cli_nested", "cli_multiround", "cli_tool_error", "cli_error_stop", "cli_exit_stop",
         "cli_patchplan", "cli_workspace", "cli_cancel")
SOURCE = '# Synthetic currency helper — preserve this comment.\r\ndef cents(amount):\r\n    return round(amount * 10)\r\n'
EXPECTED = SOURCE.replace('amount * 10)', 'amount * 100)')


class Fixture:
    """Only this disposable fixture's exact file can be read or changed.

    Replacement text is data, never executed. Verification checks the complete
    expected bytes; model-authored Python is not executed by this MCP server.
    """
    def __init__(self, root, case):
        self.root, self.case, self.stage = Path(root), case, 0
        self.challenge = secrets.token_hex(8)
        self.marker = "OPERATOR_EVAL_" + secrets.token_hex(8)
        self.preview = None

    def expected_report(self):
        if self.case in STOP_CASES:
            return "STOPPED synthetic_expected_error " + self.marker
        return self.marker

    def call(self, args):
        begin = time.perf_counter()
        action, ok = args.get("action"), False
        result = {"isError": True, "content": [{"type": "text", "text": "fixture_sequence_rejected"}]}
        if self.case == "cli_nested" and self.stage == 0 and args == {"action": "add", "left": 17, "right": 25}:
            ok, text = True, dumps({"sum": 42, "marker": self.marker})
        elif self.case == "cli_multiround":
            if self.stage == 0 and args == {"action": "challenge"}:
                ok, text = True, dumps({"challenge": self.challenge})
            elif self.stage == 1 and args == {"action": "answer", "value": self.challenge}:
                ok, text = True, self.marker
        elif self.case == "cli_tool_error":
            if self.stage == 0 and args == {"action": "fail"}:
                # Expected tool failure; the next model round must interpret it.
                text = dumps({"error": "synthetic_expected_error", "recovery_value": self.challenge})
                ok = True
            elif self.stage == 1 and args == {"action": "recover", "value": self.challenge}:
                ok, text = True, self.marker
        elif self.case in STOP_CASES and self.stage == 0 and args == {"action": "fail"}:
            # The exit case models a successful wrapper carrying a failed command.
            # Neither case grants a recovery operation, even within the same exec.
            failure = {"error": "synthetic_expected_error", "marker": self.marker}
            if self.case == "cli_exit_stop":
                failure["exit_code"] = 1
            ok, text = True, dumps(failure)
        elif self.case in {"cli_workspace", "cli_patchplan"}:
            path = self.root / "work" / "currency.py"
            if self.stage == 0 and args == {"action": "read"}:
                ok, text = True, path.read_bytes().decode("utf-8")
                self.preview = text
            elif (self.stage == 1 and set(args) == {"action", "old", "new"}
                  and action == ("replace" if self.case == "cli_workspace" else "propose")):
                old, new = args["old"], args["new"]
                current = path.read_bytes().decode("utf-8")
                if (isinstance(old, str) and isinstance(new, str) and 0 < len(old) <= 128
                        and len(new) <= 128 and current.count(old) == 1):
                    self.preview = current.replace(old, new, 1)
                    if self.case == "cli_workspace":
                        path.write_bytes(self.preview.encode("utf-8"))
                    ok, text = True, "patch_processed; call verify"
            elif self.stage == 2 and args == {"action": "verify"}:
                ok = (path.read_bytes() == EXPECTED.encode("utf-8") if self.case == "cli_workspace"
                      else self.preview == EXPECTED)
                text = self.marker if ok else "fixture_verification_failed"
        if ok:
            self.stage += 1
            result = {"isError": self.case in {"cli_tool_error", "cli_error_stop"} and self.stage == 1,
                      "content": [{"type": "text", "text": text}]}
        audit = {"action": action if action in {"add", "challenge", "answer", "fail", "recover",
                                               "read", "replace", "propose", "verify"} else "unknown",
                 "accepted": ok, "is_error": result["isError"],
                 "elapsed_ms": round((time.perf_counter() - begin) * 1000, 3)}
        with (self.root / "fixture-audit.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(dumps(audit) + "\n")
        # Private random final marker is for this disposable test only.
        (self.root / "expected-marker").write_text(self.expected_report(), encoding="ascii")
        return result


def fixture_main(root, case):
    fixture = Fixture(root, case)
    def send(value):
        sys.stdout.buffer.write(dumps(value).encode("utf-8") + b"\n")
        sys.stdout.buffer.flush()
    for line in sys.stdin.buffer:
        request = json.loads(line)
        if "id" not in request:
            continue
        method, params = request.get("method"), request.get("params", {})
        if method == "initialize":
            result = {"protocolVersion": params["protocolVersion"], "capabilities": {"tools": {}},
                      "serverInfo": {"name": "operator-eval-fixture", "version": "1"}}
        elif method == "tools/list":
            result = {"tools": [{"name": "fixture_step", "description":
                "Operate on the explicitly disposable synthetic evaluation fixture only. "
                "In cli_patchplan, propose/verify operate on a memory preview and never write the source file. "
                "No arbitrary paths, commands, network, or code execution.",
                "inputSchema": {"type": "object", "properties": {
                    "action": {"type": "string"}, "left": {"type": "integer"},
                    "right": {"type": "integer"}, "value": {"type": "string"},
                    "old": {"type": "string"}, "new": {"type": "string"}},
                    "required": ["action"], "additionalProperties": False},
                "annotations": {"destructiveHint": False, "openWorldHint": False,
                                "readOnlyHint": case != "cli_workspace"}}]}
        elif method == "tools/call" and params.get("name") == "fixture_step":
            result = fixture.call(params.get("arguments", {}))
        elif method == "ping":
            result = {}
        else:
            send({"jsonrpc": "2.0", "id": request["id"], "error": {
                "code": -32601, "message": "fixture_method_not_found"}})
            continue
        send({"jsonrpc": "2.0", "id": request["id"], "result": result})


def prompt_for(case, final_text_policy="exact"):
    if final_text_policy not in FINAL_TEXT_POLICIES:
        raise RouterError("invalid_final_text_policy")
    common = ('Bounded synthetic evaluation. Use exec to call the MCP tool whose name ends with '
              '__fixture_step. Resolve it using ALL_TOOLS.find(t => t.name.endsWith("__fixture_step")), '
              'then call tools[t.name](arguments) and text the result. Use no other tools. '
              'Perform exactly ONE fixture operation per exec; wait for each tool result before deciding '
              'the next operation. ')
    instructions = {
        "cli_nested": 'Call {action:"add", left:17, right:25}.',
        "cli_multiround": 'First call {action:"challenge"}. Read its returned challenge value, '
                          'then call {action:"answer", value:<that exact challenge>}.',
        "cli_tool_error": 'First call {action:"fail"}; an error is intentional. Read its recovery_value, '
                          'then call {action:"recover", value:<that exact recovery_value>}.',
        "cli_error_stop": 'Call {action:"fail"} exactly once. The tool error is intentional. '
                          'After it returns, STOP: do not retry, recover, change tools or perform another operation. '
                          'Report the failure with exactly: STOPPED synthetic_expected_error <the returned marker>.',
        "cli_exit_stop": 'Call {action:"fail"} exactly once. The wrapper succeeds but its result contains '
                         'exit_code:1 and a synthetic_expected_error. Treat the nonzero exit code as failure. '
                         'STOP without retrying, recovering, changing tools or performing another operation. '
                         'Report the failure with exactly: STOPPED synthetic_expected_error <the returned marker>.',
        "cli_workspace": 'The synthetic currency.py helper converts dollars to cents but has a multiplier bug. '
                         'Call {action:"read"}, inspect the source, call {action:"replace", old:<minimal unique '
                         'substring>, new:<correct replacement>} to fix the multiplier, and finally '
                         'call {action:"verify"}. Preserve every other byte, comment and newline.',
        "cli_patchplan": 'The synthetic currency.py helper converts dollars to cents but has a multiplier bug. '
                         'Call {action:"read"} to read the existing file, inspect the source, then call '
                         '{action:"propose", old:<minimal unique substring>, new:<correct replacement>} '
                         'to propose a small patch in memory. Finally call {action:"verify"} to verify the '
                         'proposed result. This read-only case does not write the file. Preserve every other '
                         'byte, comment and newline.',
        "cli_cancel": 'Explain a simple arithmetic example in a long answer. This run will be cancelled by the harness.',
    }
    final = '' if case in STOP_CASES else ' When the marker arrives, reply with only the full OPERATOR_EVAL_ marker.'
    formatting = (" For this synthetic report, the required marker or STOPPED report must occupy one line. "
                  "Up to eight empty LF or CRLF lines before and after it are allowed. "
                  "No spaces, tabs, explanation, or other text may surround that line. "
                  "This allowance applies only to the final report, never to tool arguments or file bytes."
                  if final_text_policy == "marker_line_v1" else "")
    return common + instructions[case] + final + formatting


def verify_final_message(path, expected, final_text_policy="exact"):
    """Compare the isolated CLI's final-message file without trimming model text.

    Console output has its own formatting. This synthetic artifact is never a
    saved task transcript or business-answer transport.
    """
    if final_text_policy not in FINAL_TEXT_POLICIES:
        raise RouterError("invalid_final_text_policy")
    exists = path.is_file()
    if exists and path.stat().st_size > 16 * 1024 * 1024:
        raise RouterError("evaluation_final_message_too_large")
    raw = path.read_bytes() if exists else b""
    text = raw.decode("utf-8")
    exact = exists and expected is not None and text == expected
    # Match a bounded grammar, not arbitrary trim(). The original bytes remain intact.
    marker_line = (exists and isinstance(expected, str) and bool(expected)
                   and "\n" not in expected and "\r" not in expected
                   and re.fullmatch(r"(?:\r?\n){0,8}" + re.escape(expected)
                                    + r"(?:\r?\n){0,8}", text) is not None)
    return {"verification_source": "isolated_cli_output_last_message",
            "final_text_policy": final_text_policy,
            "final_message_present": exists,
            "verification_exact": exact,
            "verification_accepted": exact if final_text_policy == "exact" else marker_line,
            "verification_matched_after_trim": exists and expected is not None and text.strip() == expected,
            "final_message_sha256": hashlib.sha256(raw).hexdigest(),
            "final_message_bytes": len(raw),
            "final_message_leading_lf_count": len(text) - len(text.lstrip("\n"))}


def isolated_environment(home):
    # Provider credentials are in the router parent only, never the model/tool child.
    environment = {k: v for k, v in os.environ.items() if not k.upper().startswith(
        ("CODEX_", "OPENAI_", "CHATGPT_", "DEEPSEEK_", "GLM_")) and not any(
            word in k.upper() for word in ("TOKEN", "SECRET", "PASSWORD", "API_KEY"))}
    environment["CODEX_HOME"] = str(home)
    return environment


async def collect_child(child):
    async def bounded(stream):
        data = bytearray()
        while chunk := await stream.read(65536):
            data.extend(chunk)
            if len(data) > 16 * 1024 * 1024:
                raise RouterError("evaluation_cli_output_too_large")
        return bytes(data)
    tasks = [asyncio.create_task(bounded(child.stdout)), asyncio.create_task(bounded(child.stderr)),
             asyncio.create_task(child.wait())]
    try:
        stdout, stderr, _ = await asyncio.gather(*tasks)
        return stdout, stderr
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def evaluate(row, case, executable, *, timeout=90, final_text_policy="exact"):
    if final_text_policy not in FINAL_TEXT_POLICIES:
        raise RouterError("invalid_final_text_policy")
    from aiohttp import web
    from operator_core.model_router import ModelRouter
    preflight(row)
    version_result = subprocess.run([str(executable), "--version"], capture_output=True,
                                    timeout=10, creationflags=0x08000000 if os.name == "nt" else 0)
    match = re.fullmatch(rb"codex-cli (\d+\.\d+\.\d+)\s*", version_result.stdout)
    if version_result.returncode or not match:
        raise RouterError("explicit_cli_version_unavailable")
    version = match[1].decode()
    catalog = json.loads(Path(__file__).with_name("operator_core").joinpath(
        "beeper_model_catalog.json").read_text(encoding="utf-8"))
    router = ModelRouter(ModelRegistry({"version": 2, "models": [row]}, catalog), secrets.token_hex(32))
    admitted = asyncio.Event()
    request_times = []
    requests, active, limit = 0, 0, {"cli_nested": 2, "cli_multiround": 3,
                                    "cli_tool_error": 3, "cli_error_stop": 2, "cli_exit_stop": 2,
                                    "cli_workspace": 4, "cli_patchplan": 4, "cli_cancel": 1}[case]
    @web.middleware
    async def bound(request, handler):
        nonlocal requests, active
        if not request.path.endswith("/responses"):
            return web.json_response({"error": "fixture_endpoint_refused"}, status=404)
        requests += 1
        if requests > limit:
            return web.json_response({"error": "fixture_request_budget_exhausted_no_retry"}, status=409)
        admitted.set()
        sample = {"started_ms": round((time.perf_counter() - begin) * 1000, 3)}
        request_times.append(sample)
        active += 1
        try:
            return await handler(request)
        finally:
            sample["completed_ms"] = round((time.perf_counter() - begin) * 1000, 3)
            active -= 1
    app = router.app()
    app.middlewares.insert(0, bound)
    runner = web.AppRunner(app, access_log=None, shutdown_timeout=2)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    child, temporary, communication, begin = None, None, None, time.perf_counter()
    report = {"case": case, "status": "failed", "synthetic_only": True, "cli_version": version,
              "final_text_policy": final_text_policy,
              "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "contract_sha256": contract_digest(row), "adapter_sha256": adapter_digest(),
              "evaluator_sha256": evaluator_digest(),
              "request_limit": limit, "configured_retry_count": 0}
    try:
        temporary = tempfile.TemporaryDirectory(prefix="operator-responses-eval-")
        directory = temporary.name
        root = Path(directory)
        home, work = root / "home", root / "work"
        home.mkdir()
        work.mkdir()
        (work / "currency.py").write_bytes(SOURCE.encode("utf-8"))
        catalog_file = root / "catalog.json"
        native = {"models": [{**catalog["models"][0], "slug": "synthetic-native"}]}
        catalog_file.write_text(dumps(router.registry.merge(native)), encoding="utf-8")
        settings = {"model": row["slug"], "model_provider": "operator_fixture",
            "model_reasoning_effort": row["reasoning_efforts"][0], "model_catalog_json": str(catalog_file),
            "approval_policy": "never", "web_search": "disabled", "analytics.enabled": False,
            "model_providers.operator_fixture.name": "Operator explicit evaluation",
            "model_providers.operator_fixture.base_url": f"http://127.0.0.1:{runner.addresses[0][1]}" + router.prefix,
            "model_providers.operator_fixture.wire_api": "responses",
            "model_providers.operator_fixture.request_max_retries": 0,
            "model_providers.operator_fixture.stream_max_retries": 0,
            "model_providers.operator_fixture.supports_websockets": False,
            "mcp_servers.operator_fixture.command": sys.executable,
            "mcp_servers.operator_fixture.args": [str(Path(__file__).resolve()), "fixture", "--fixture-root", str(root), "--case", case]}
        final_file = root / "final-message.txt"
        command = [str(executable), "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules",
                   "--skip-git-repo-check", "--sandbox", "read-only", "--color", "never", "-C", str(work),
                   "--output-last-message", str(final_file)]
        for key, value in settings.items():
            command += ["-c", key + "=" + dumps(value)]
        command.append(prompt_for(case, final_text_policy))
        child = await asyncio.create_subprocess_exec(*command, cwd=str(work), env=isolated_environment(home),
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            creationflags=0x08000000 if os.name == "nt" else 0)
        communication = asyncio.create_task(collect_child(child))
        if case == "cli_cancel":
            await asyncio.wait_for(admitted.wait(), timeout=min(timeout, 30))
            deadline = time.monotonic() + 25
            while active and "upstream_headers" not in router.metrics.stages and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            report["upstream_headers_observed_before_cancel"] = "upstream_headers" in router.metrics.stages
            was_active = active > 0
            # Exact disposable child handle; no saved task is interrupted.
            child.terminate()
            await asyncio.wait_for(communication, timeout=10)
            deadline = time.monotonic() + 3
            while active and time.monotonic() < deadline:
                await asyncio.sleep(0.02)
            report.update(cancel_observed=active == 0, exit_code=child.returncode)
            if was_active and active == 0 and requests == 1 and report["upstream_headers_observed_before_cancel"]:
                report["status"] = "passed"
        else:
            stdout, stderr = await asyncio.wait_for(communication, timeout=timeout)
            report["tool_approval_required"] = b"MCP tool call requires approval" in stdout + stderr
            expected_file = root / "expected-marker"
            expected = expected_file.read_text(encoding="ascii") if expected_file.exists() else None
            report.update(verify_final_message(final_file, expected, final_text_policy))
            audit_file = root / "fixture-audit.jsonl"
            audit = [json.loads(line) for line in audit_file.read_text(encoding="utf-8").splitlines()] if audit_file.exists() else []
            report.update(exit_code=child.returncode, fixture_operations=audit,
                          fixture_tool_ms=round(sum(r["elapsed_ms"] for r in audit), 3))
            expected_actions = {"cli_nested": ["add"], "cli_multiround": ["challenge", "answer"],
                                "cli_tool_error": ["fail", "recover"], "cli_workspace": ["read", "replace", "verify"],
                                "cli_error_stop": ["fail"], "cli_exit_stop": ["fail"],
                                "cli_patchplan": ["read", "propose", "verify"]}[case]
            sequence = [r["action"] for r in audit] == expected_actions and all(r["accepted"] for r in audit)
            report["sequence_verified"] = sequence
            if case in STOP_CASES:
                report.update(stopped_after_fixture_error=sequence and child.returncode == 0
                              and report["final_message_present"] and requests == limit,
                              fixture_calls_after_error=max(0, len(audit) - 1),
                              failure_report_verified=report["verification_matched_after_trim"],
                              stop_evidence_scope="synthetic_fixture_operations_only")
            if case == "cli_workspace":
                report["file_exact"] = (work / "currency.py").read_bytes() == EXPECTED.encode("utf-8")
            if case == "cli_patchplan":
                report["original_file_unchanged"] = (work / "currency.py").read_bytes() == SOURCE.encode("utf-8")
                sequence = sequence and report["original_file_unchanged"]
            if child.returncode == 0 and report["verification_accepted"] and sequence and requests == limit:
                report["status"] = "passed"
    except Exception as exc:
        report["error_category"] = "timeout" if isinstance(exc, asyncio.TimeoutError) else "harness_error"
    finally:
        if child is not None and child.returncode is None:
            child.kill()
            await child.wait()
        if communication is not None:
            communication.cancel()
            await asyncio.gather(communication, return_exceptions=True)
        await runner.cleanup()
        if temporary is not None:
            # Preserve failure diagnostics even when a stopped child briefly
            # retains a Windows handle. Never turn cleanup trouble into a pass.
            try:
                temporary.cleanup()
            except OSError:
                report.update(status="failed", cleanup_failed=True)
        report.update(requests=requests, request_budget_exceeded=requests > limit,
                      router_failure=router.last_failure, timing=router.metrics.snapshot(),
                      elapsed_ms=round((time.perf_counter() - begin) * 1000))
        if request_times:
            report["client_startup_ms"] = request_times[0]["started_ms"]
            report["client_between_requests_ms"] = [round(b["started_ms"] - a["completed_ms"], 3)
                for a, b in zip(request_times, request_times[1:]) if "completed_ms" in a]
            if "completed_ms" in request_times[-1]:
                report["client_and_harness_shutdown_ms"] = round(report["elapsed_ms"] - request_times[-1]["completed_ms"], 3)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["run", "fixture"])
    parser.add_argument("--registration", type=Path)
    parser.add_argument("--cli", type=Path)
    parser.add_argument("--case", required=True, choices=CASES)
    parser.add_argument("--receipt-dir", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--fixture-root", type=Path)
    parser.add_argument("--final-text-policy", choices=sorted(FINAL_TEXT_POLICIES), default="exact",
                        help="Synthetic final-report grammar only; never changes model output or file checks")
    args = parser.parse_args()
    if args.action == "fixture":
        if not args.fixture_root:
            parser.error("fixture-root required")
        fixture_main(args.fixture_root, args.case)
        return 0
    if not (args.registration and args.cli and args.receipt_dir and args.run_id):
        parser.error("registration, cli, receipt-dir and run-id must be explicit")
    row = read_registration(args.registration)
    preflight(row)
    from operator_core.responses_profiles import validate_row
    validate_row(row).key()
    executable = args.cli.resolve(strict=True)
    receipt = reserve_receipt(args.receipt_dir, row, args.case, args.run_id)
    report = asyncio.run(evaluate(row, args.case, executable, final_text_policy=args.final_text_policy))
    atomic_write(receipt, (dumps(report) + "\n").encode())
    print(dumps(report), flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        raise SystemExit("Evaluation stopped; inspect the private receipt. Never retry the same run.")
