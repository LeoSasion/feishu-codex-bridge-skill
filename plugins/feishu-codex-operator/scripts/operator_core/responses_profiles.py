"""Portable explicit registrations with bounded, content-free verification records."""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from .model_registry import ModelRegistry, RouterError
from .responses_tool_adapter import dumps, prepare_request

TERMINAL_CASES = {"cli_powershell": "powershell", "cli_bash": "bash"}
CHECKS = frozenset({"json", "sse", "required", "named", "structured", "unicode", "unicode-json", "unicode-json-lf",
                    "unicode-arguments", "unicode-arguments-lf", "long", "long-lines",
                    "cli_nested", "cli_multiround", "cli_tool_error", "cli_error_stop", "cli_exit_stop",
                    "cli_patchplan", "cli_workspace", "cli_cancel", *TERMINAL_CASES})
CORE_CHECKS = frozenset({"cli_nested", "cli_multiround", "cli_tool_error", "cli_error_stop", "cli_exit_stop",
                         "cli_patchplan", "cli_cancel"})
FINAL_TEXT_POLICIES = frozenset({"exact", "marker_line_v1"})


def validate_row(row):
    catalog = json.loads(Path(__file__).with_name("beeper_model_catalog.json").read_text(encoding="utf-8"))
    registry = ModelRegistry({"version": 2, "models": [row]}, catalog)
    route = registry.routes[row["slug"]]
    if route.responses is None:
        raise RouterError("profile_requires_explicit_capabilities")
    return route


def contract_digest(row):
    validate_row(row)
    # Labels and the name of the key variable are portable; all behavior is bound.
    contract = {k: v for k, v in row.items() if k not in {"slug", "display_name", "api_key_env"}}
    return hashlib.sha256(json.dumps(contract, sort_keys=True, ensure_ascii=True,
                                    separators=(",", ":")).encode()).hexdigest()


def adapter_digest():
    digest = hashlib.sha256()
    for name in ("responses_capabilities.py", "responses_tool_adapter.py", "responses_events.py",
                 "model_registry.py", "model_router.py"):
        digest.update(name.encode())
        digest.update(Path(__file__).with_name(name).read_bytes())
    return digest.hexdigest()


def evaluator_digest():
    digest = hashlib.sha256()
    for path in (Path(__file__), Path(__file__).parent.parent / "operator_responses_eval.py",
                 Path(__file__).parent.parent / "operator_responses_probe.py",
                 Path(__file__).parent.parent / "operator_terminal_fixture.py"):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def make_profile(profile_id, row, checks):
    value = {"version": 2, "profile_id": profile_id, "registration": deepcopy(row),
             "contract_sha256": contract_digest(row), "adapter_sha256": adapter_digest(),
             "evaluator_sha256": evaluator_digest(), "checks": checks}
    inspect_profile(value)
    return value


def profile_from_reports(profile_id, row, reports):
    checks = []
    for report in reports:
        if (not isinstance(report, dict) or report.get("synthetic_only") is not True
                or report.get("contract_sha256") != contract_digest(row)
                or report.get("adapter_sha256") != adapter_digest()):
            raise RouterError("evaluation_report_contract_or_adapter_mismatch")
        if report.get("evaluator_sha256") != evaluator_digest():
            raise RouterError("evaluation_report_evaluator_mismatch")
        check = {key: report.get(key) for key in ("case", "status", "checked_at", "cli_version")}
        if "final_text_policy" in report:
            check["final_text_policy"] = report["final_text_policy"]
        if check["case"] in TERMINAL_CASES:
            check["terminal"] = deepcopy(report.get("terminal"))
            if check["status"] == "passed" and (any(report.get(field) is not True for field in
                    ("terminal_call_validated", "terminal_result_verified", "terminal_fixture_unchanged"))
                    or type(report.get("terminal_exit_code")) is not int or report["terminal_exit_code"] != 0
                    or report.get("terminal_policy_rejected") is not False or report.get("terminal_rejection") is not None):
                raise RouterError("terminal_report_success_not_verified")
        checks.append(check)
    return make_profile(profile_id, row, checks)


def inspect_profile(value, *, cli_version=None):
    fields = {"version", "profile_id", "registration", "contract_sha256", "adapter_sha256", "checks"}
    if isinstance(value, dict) and value.get("version") == 2:
        fields.add("evaluator_sha256")
    if (not isinstance(value, dict) or set(value) != fields or type(value["version"]) is not int
            or value["version"] not in (1, 2) or not isinstance(value["profile_id"], str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,79}", value["profile_id"])):
        raise RouterError("invalid_responses_profile")
    if value["contract_sha256"] != contract_digest(value["registration"]):
        raise RouterError("profile_registration_changed_since_verification")
    if not isinstance(value["adapter_sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", value["adapter_sha256"]):
        raise RouterError("invalid_profile_adapter_digest")
    if value["version"] == 2 and (not isinstance(value["evaluator_sha256"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", value["evaluator_sha256"])):
        raise RouterError("invalid_profile_evaluator_digest")
    records = value["checks"]
    if not isinstance(records, list) or len(records) > 100:
        raise RouterError("invalid_profile_checks")
    outcomes, seen = {}, set()
    for record in records:
        if (not isinstance(record, dict)
                or set(record) - {"final_text_policy", "terminal"} != {"case", "status", "checked_at", "cli_version"}
                or not isinstance(record.get("final_text_policy", "exact"), str)
                or record.get("final_text_policy", "exact") not in FINAL_TEXT_POLICIES
                or ("final_text_policy" in record and not str(record.get("case", "")).startswith("cli_"))
                or record["case"] not in CHECKS or record["status"] not in {"passed", "failed", "unknown"}
                or not isinstance(record["checked_at"], str)
                or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", record["checked_at"])
                or not isinstance(record["cli_version"], str)
                or not re.fullmatch(r"(?:\d+\.\d+\.\d+|none)", record["cli_version"])):
            raise RouterError("invalid_profile_check_record")
        if record["case"] in TERMINAL_CASES:
            terminal = record.get("terminal")
            if (not isinstance(terminal, dict)
                    or set(terminal) - {"requested_windows_sandbox"} != {"family", "requested_executable_sha256"}
                    or terminal.get("requested_windows_sandbox") not in (None, "unelevated")
                    or terminal["family"] != TERMINAL_CASES[record["case"]] or record["cli_version"] == "none"
                    or not isinstance(terminal["requested_executable_sha256"], str)
                    or not re.fullmatch(r"[a-f0-9]{64}", terminal["requested_executable_sha256"])):
                raise RouterError("invalid_profile_terminal_identity")
        elif "terminal" in record:
            raise RouterError("terminal_identity_requires_terminal_case")
        try:
            when = datetime.strptime(record["checked_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError as exc:
            raise RouterError("invalid_profile_check_date") from exc
        if when > datetime.now(timezone.utc):
            raise RouterError("profile_check_date_in_future")
        identity = (record["case"], record["checked_at"], record["cli_version"])
        if identity in seen:
            raise RouterError("duplicate_profile_check")
        seen.add(identity)
        # Keep failures visible; a later result never erases the failed-run count.
        if cli_version is None or record["cli_version"] == cli_version:
            previous = outcomes.get(record["case"])
            if previous is None or record["checked_at"] > previous["checked_at"]:
                outcomes[record["case"]] = record
            elif record["checked_at"] == previous["checked_at"]:
                raise RouterError("ambiguous_profile_check_time")
    stale = value["adapter_sha256"] != adapter_digest()
    evaluator_bound = value["version"] == 2
    evaluator_changed = evaluator_bound and value["evaluator_sha256"] != evaluator_digest()
    current = not stale and evaluator_bound and not evaluator_changed
    missing = sorted(case for case in CORE_CHECKS if outcomes.get(case, {}).get("status") != "passed")
    gates = {}
    for case in sorted(CORE_CHECKS):
        record = outcomes.get(case)
        reasons = []
        if stale:
            reasons.append("adapter_changed")
        if not evaluator_bound:
            reasons.append("evaluator_unbound")
        elif evaluator_changed:
            reasons.append("evaluator_changed")
        if record is None:
            reasons.append("cli_version_changed" if any(r["case"] == case for r in records) else "missing")
        elif record["status"] != "passed":
            reasons.append(record["status"])
        gates[case] = {"passed": not reasons, "reasons": reasons,
                       "final_text_policy": record.get("final_text_policy", "exact") if record else None,
                       "recorded_status": record["status"] if record else None,
                       "checked_at": record["checked_at"] if record else None}
    return {"profile_id": value["profile_id"], "configuration_valid": True,
            "adapter_changed": stale, "evaluator_bound": evaluator_bound,
            "evaluator_changed": evaluator_changed, "missing_cli_checks": missing,
            "recorded_failures": sum(r["status"] == "failed" for r in records),
            "gates": gates,
            "terminal_checks": [deepcopy(record) for record in records if record["case"] in TERMINAL_CASES],
            "terminal_checks_establish_desktop_selection": False,
            "isolated_cli_verified": current and not missing, "desktop_verified": False,
            "write_tool_approval_verified": outcomes.get("cli_workspace", {}).get("status") == "passed" and current,
            "evidence_scope": "recorded_isolated_cli_cases", "current_upstream_rechecked": False,
            "evidence_is_self_reported_not_attestation": True}


def preflight(row, *, request=None):
    route = validate_row(row)
    for effort in route.reasoning_efforts:
        # Codex needs both tool invocation and final text to finish a turn.
        for choice in ("auto", "none"):
            prepare_request({"model": row["slug"], "input": "Configuration-only preflight",
                             "reasoning": {"effort": effort}, "tool_choice": choice},
                            route.responses, route.reasoning_efforts)
    if request is not None:
        prepare_request(request, route.responses, route.reasoning_efforts)
    return {"configuration_valid": True, "upstream_requests": 0,
            "required_client_settings": {"web_search": "disabled", "request_max_retries": 0,
                                         "stream_max_retries": 0},
            "requires_explicit_history": True, "desktop_verified": False}
