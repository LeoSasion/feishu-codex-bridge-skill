from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from typing import Any


RESULT_SCHEMA_VERSION = 2
CANDIDATE_SCHEMA_VERSION = 4
PRODUCT_CONTRACT_SCHEMA_VERSION = 4
RUNTIME_ATTESTATION_SCHEMA_VERSION = 3
CANDIDATE_KIND = "single_beeper_run_once"
CANDIDATE_SCHEMA_TITLE = (
    "Feishu Desktop single Beeper run-once candidate"
)
RUNTIME_ATTESTATION_SCHEMA_TITLE = (
    "Feishu Desktop single Beeper runtime attestation receipt"
)
CAPTURE_SURFACE = "redacted_product_beeper_run_once_contract"
REDACTION_PROFILE = "answer_free_beeper_contract_v1"
SURFACE_FINGERPRINT_NAMESPACE = "feishu-codex-bridge.beeper-surface.v1"
SURFACE_FINGERPRINT_RECIPE_ID = "beeper-surface-sha256-v1"
SURFACE_FINGERPRINT_ALGORITHM = "sha256"
SURFACE_FINGERPRINT_CANONICALIZATION = "json-sort-keys-ascii-v1"
CANDIDATE_MARKER_NAMESPACE = "feishu-codex-bridge.beeper-run-once.v1"
RESERVED_HISTORICAL_MARKER_PREFIX = "feishu-codex-bridge.legacy."
DIGEST_PATTERN = r"^[0-9a-f]{64}$"
PRODUCT_BUILD_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
MAX_INPUT_BYTES = 65_536
BEEPER_EXPECTED: dict[str, Any] = {
    "scope": "bridge_installation",
    "active_cardinality": 1,
    "exact_identity_bound": True,
    "identity_immutable": True,
    "historical_beeper_reused": False,
    "feishu_scope_binding_allowed": False,
    "business_responder_allowed": False,
    "self_contact_allowed": False,
    "responder_contact_only": True,
    "desktop_responder_ownership_preserved": True,
    "alternate_responder_client_allowed": False,
}
TASK_COORDINATION_POLICY_EXPECTED: dict[str, Any] = {
    "profile": "desktop_task_coordination_only_v1",
    "allowed_methods": [
        "list_projects",
        "list_threads",
        "read_thread",
        "create_thread",
        "send_message_to_thread",
        "wait_threads",
        "set_thread_archived",
    ],
    "operation_scoped_minimum_subset_required": True,
    "unapproved_method_allowed": False,
    "non_desktop_responder_transport_allowed": False,
    "beeper_business_execution_allowed": False,
}
TASK_COORDINATION_POLICY_CANONICAL_SHA256 = hashlib.sha256(
    json.dumps(
        TASK_COORDINATION_POLICY_EXPECTED,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
).hexdigest()
CANDIDATE_EXPECTED: dict[str, Any] = {
    "schema_version": CANDIDATE_SCHEMA_VERSION,
    "candidate_kind": CANDIDATE_KIND,
    "responder_mode": "existing_thread",
    "recurrence_allowed": False,
    "active_status_required": False,
    "product_enforced_max_model_turns": 1,
    "max_executions_per_candidate": 1,
    "single_use_dispatch_grant_required": True,
    "budget_consumed_atomically_before_dispatch_required": True,
    "distinct_key_second_dispatch_rejected_required": True,
    "budget_non_resettable_required": True,
    "budget_survives_restart_and_failover_required": True,
    "rearm_or_update_allowed": False,
    "responder_thread_id_required": True,
    "new_thread_fallback_forbidden": True,
    "idempotency_key_required": True,
    "immutable_execution_id_required": True,
    "immutable_surface_fingerprint_required": True,
    "duplicate_key_returns_same_execution": True,
    "run_to_turn_mapping_required": True,
    "receipt_turn_cardinality_required": 1,
    "terminal_completed_state_required": True,
    "all_terminal_states_consume_budget_required": True,
    "all_terminal_states_next_run_must_be_null": True,
    "post_run_next_run_must_be_null": True,
    "helper_admission_is_hard_cap": False,
    "old_surface_reactivation_allowed": False,
    "product_contract_provenance_required": True,
    "surface_fingerprint_recipe_id": SURFACE_FINGERPRINT_RECIPE_ID,
    "surface_fingerprint_bindings_required": True,
    "candidate_terminal_marker_namespace": CANDIDATE_MARKER_NAMESPACE,
    "historical_terminal_marker_namespace_reuse_forbidden": True,
    "runtime_attestation_receipt_schema_required": True,
    "bounded_post_run_quiet_window_required": True,
    "beeper_scope": "bridge_installation",
    "beeper_cardinality_required": 1,
    "exact_beeper_identity_required": True,
    "beeper_identity_immutable_required": True,
    "historical_beeper_reuse_forbidden": True,
    "beeper_responder_contact_only_required": True,
    "beeper_scope_binding_forbidden": True,
    "beeper_as_responder_forbidden": True,
    "beeper_self_contact_forbidden": True,
    "desktop_responder_ownership_preserved_required": True,
    "alternate_responder_client_forbidden": True,
    "operation_scoped_task_coordination_policy_required": True,
}
CANDIDATE_SCHEMA_EXPECTED: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": CANDIDATE_SCHEMA_TITLE,
    "type": "object",
    "additionalProperties": False,
    "required": list(CANDIDATE_EXPECTED),
    "properties": {
        name: {"const": expected}
        for name, expected in CANDIDATE_EXPECTED.items()
    },
}
RUNTIME_ATTESTATION_RECEIPT_PROPERTIES: dict[str, Any] = {
    "schema_version": {"const": RUNTIME_ATTESTATION_SCHEMA_VERSION},
    "attestation_kind": {
        "const": "single_beeper_run_once_runtime_attestation"
    },
    "status": {"enum": ["pass", "fail"]},
    "candidate_kind": {"const": CANDIDATE_KIND},
    "marker_namespace": {"const": CANDIDATE_MARKER_NAMESPACE},
    "product_contract_canonical_sha256": {"type": "string", "pattern": DIGEST_PATTERN},
    "candidate_schema_canonical_sha256": {"type": "string", "pattern": DIGEST_PATTERN},
    "runtime_attestation_schema_canonical_sha256": {
        "type": "string",
        "pattern": DIGEST_PATTERN,
    },
    "surface_fingerprint_sha256": {"type": "string", "pattern": DIGEST_PATTERN},
    "runtime_build_fingerprint_sha256": {
        "type": "string",
        "pattern": DIGEST_PATTERN,
    },
    "execution_receipt_sha256": {"type": "string", "pattern": DIGEST_PATTERN},
    "receipt_immutable": {"type": "boolean"},
    "single_use_grant_consumed_before_dispatch": {"type": "boolean"},
    "execution_count": {"type": "integer", "minimum": 0, "maximum": 8},
    "beeper_turn_count": {"type": "integer", "minimum": 0, "maximum": 8},
    "run_to_turn_receipt_cardinality": {
        "type": "integer",
        "minimum": 0,
        "maximum": 8,
    },
    "same_key_same_execution": {"type": "boolean"},
    "distinct_key_rejected_before_dispatch": {"type": "boolean"},
    "queued_second_dispatch_count": {
        "type": "integer",
        "minimum": 0,
        "maximum": 8,
    },
    "overlap_second_dispatch_count": {
        "type": "integer",
        "minimum": 0,
        "maximum": 8,
    },
    "retry_second_dispatch_count": {
        "type": "integer",
        "minimum": 0,
        "maximum": 8,
    },
    "terminal_budget_consumed": {"type": "boolean"},
    "next_run_at_is_null": {"type": "boolean"},
    "rearm_allowed": {"type": "boolean"},
    "quiet_window_seconds": {"type": "integer", "minimum": 1, "maximum": 300},
    "quiet_window_new_execution_count": {
        "type": "integer",
        "minimum": 0,
        "maximum": 8,
    },
    "quiet_window_new_turn_count": {
        "type": "integer",
        "minimum": 0,
        "maximum": 8,
    },
    "active_beeper_count": {
        "type": "integer",
        "minimum": 0,
        "maximum": 8,
    },
    "beeper_identity_bound": {"type": "boolean"},
    "beeper_identity_stable": {"type": "boolean"},
    "historical_beeper_reuse_detected": {"type": "boolean"},
    "beeper_scope_binding_count": {
        "type": "integer",
        "minimum": 0,
        "maximum": 8,
    },
    "beeper_responder_collision_count": {
        "type": "integer",
        "minimum": 0,
        "maximum": 8,
    },
    "beeper_self_contact_count": {
        "type": "integer",
        "minimum": 0,
        "maximum": 8,
    },
    "non_task_coordination_call_count": {
        "type": "integer",
        "minimum": 0,
        "maximum": 8,
    },
    "beeper_business_execution_count": {
        "type": "integer",
        "minimum": 0,
        "maximum": 8,
    },
    "alternate_responder_client_count": {
        "type": "integer",
        "minimum": 0,
        "maximum": 8,
    },
    "desktop_responder_ownership_preserved": {"type": "boolean"},
    "task_coordination_policy_canonical_sha256": {
        "const": TASK_COORDINATION_POLICY_CANONICAL_SHA256,
    },
    "activation_allowed": {"const": False},
}
RUNTIME_ATTESTATION_PASS_ASSERTIONS: dict[str, Any] = {
    "receipt_immutable": {"const": True},
    "single_use_grant_consumed_before_dispatch": {"const": True},
    "execution_count": {"const": 1},
    "beeper_turn_count": {"const": 1},
    "run_to_turn_receipt_cardinality": {"const": 1},
    "same_key_same_execution": {"const": True},
    "distinct_key_rejected_before_dispatch": {"const": True},
    "queued_second_dispatch_count": {"const": 0},
    "overlap_second_dispatch_count": {"const": 0},
    "retry_second_dispatch_count": {"const": 0},
    "terminal_budget_consumed": {"const": True},
    "next_run_at_is_null": {"const": True},
    "rearm_allowed": {"const": False},
    "quiet_window_new_execution_count": {"const": 0},
    "quiet_window_new_turn_count": {"const": 0},
    "active_beeper_count": {"const": 1},
    "beeper_identity_bound": {"const": True},
    "beeper_identity_stable": {"const": True},
    "historical_beeper_reuse_detected": {"const": False},
    "beeper_scope_binding_count": {"const": 0},
    "beeper_responder_collision_count": {"const": 0},
    "beeper_self_contact_count": {"const": 0},
    "non_task_coordination_call_count": {"const": 0},
    "beeper_business_execution_count": {"const": 0},
    "alternate_responder_client_count": {"const": 0},
    "desktop_responder_ownership_preserved": {"const": True},
    "task_coordination_policy_canonical_sha256": {
        "const": TASK_COORDINATION_POLICY_CANONICAL_SHA256,
    },
}
RUNTIME_ATTESTATION_PASS_CONDITION: dict[str, Any] = {
    "if": {
        "properties": {"status": {"const": "pass"}},
        "required": ["status"],
    },
    "then": {"properties": RUNTIME_ATTESTATION_PASS_ASSERTIONS},
}
RUNTIME_ATTESTATION_SCHEMA_EXPECTED: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": RUNTIME_ATTESTATION_SCHEMA_TITLE,
    "type": "object",
    "additionalProperties": False,
    "required": list(RUNTIME_ATTESTATION_RECEIPT_PROPERTIES),
    "properties": RUNTIME_ATTESTATION_RECEIPT_PROPERTIES,
    "allOf": [RUNTIME_ATTESTATION_PASS_CONDITION],
}
TOOL_CONTRACT_KEYS = frozenset(
    (
        "schema_version",
        "surface_kind",
        "provenance",
        "surface_fingerprint",
        "beeper",
        "task_coordination_policy",
        "run_once",
        "legacy_recurring",
    )
)
PROVENANCE_KEYS = frozenset(
    (
        "capture_surface",
        "product_build",
        "redaction_profile",
        "capability_contract_canonical_sha256",
        "candidate_schema_canonical_sha256",
        "runtime_attestation_schema_canonical_sha256",
    )
)
SURFACE_FINGERPRINT_KEYS = frozenset(
    ("namespace", "recipe_id", "algorithm", "canonicalization", "sha256")
)
LEGACY_RECURRING_KEYS = frozenset(
    ("candidate_uses_recurrence", "rrule_count_used_as_cap")
)
RUN_ONCE_KEYS = frozenset(
    (
        "available",
        "exact_existing_thread_responder",
        "responder_thread_id_required",
        "new_thread_fallback_forbidden",
        "scheduler_enforced_max_model_turns",
        "max_executions_per_candidate",
        "cap_enforced_before_dispatch",
        "single_use_dispatch_grant",
        "budget_consumed_atomically_before_dispatch",
        "second_distinct_key_rejected_before_dispatch",
        "budget_non_resettable",
        "budget_survives_restart_and_failover",
        "rearm_or_update_allowed",
        "idempotency_key_required",
        "duplicate_key_returns_same_execution",
        "immutable_execution_id",
        "immutable_surface_fingerprint",
        "immutable_run_receipt",
        "run_to_turn_mapping",
        "receipt_turn_cardinality",
        "terminal_completed_state",
        "all_terminal_states_consume_budget",
        "all_terminal_states_next_run_null",
        "post_run_next_run_null",
        "recurrence_required",
        "active_status_required",
        "queued_runs_suppressed",
        "overlapping_runs_suppressed",
        "retry_runs_suppressed",
    )
)
RUN_ONCE_INTEGER_KEYS = frozenset(
    (
        "scheduler_enforced_max_model_turns",
        "max_executions_per_candidate",
        "receipt_turn_cardinality",
    )
)
RUN_ONCE_BOOLEAN_KEYS = RUN_ONCE_KEYS - RUN_ONCE_INTEGER_KEYS


def _json_exact(value: Any, expected: Any) -> bool:
    if type(value) is not type(expected):
        return False
    if isinstance(expected, dict):
        return (
            frozenset(value) == frozenset(expected)
            and all(_json_exact(value[name], item) for name, item in expected.items())
        )
    if isinstance(expected, list):
        return len(value) == len(expected) and all(
            _json_exact(left, right) for left, right in zip(value, expected)
        )
    return value == expected


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def _canonical_sha256(value: Any) -> str:
    try:
        payload = _canonical_json_bytes(value)
    except (TypeError, ValueError, OverflowError):
        return ""
    return hashlib.sha256(payload).hexdigest()


def _digest_string_valid(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(DIGEST_PATTERN, value) is not None


def _capability_contract_payload(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": value.get("schema_version"),
        "surface_kind": value.get("surface_kind"),
        "beeper": value.get("beeper"),
        "task_coordination_policy": value.get("task_coordination_policy"),
        "run_once": value.get("run_once"),
        "legacy_recurring": value.get("legacy_recurring"),
    }


def _surface_fingerprint_payload(
    *,
    provenance: dict[str, Any],
    capability_contract_canonical_sha256: str,
    candidate_schema_canonical_sha256: str,
    runtime_attestation_schema_canonical_sha256: str,
) -> dict[str, Any]:
    return {
        "namespace": SURFACE_FINGERPRINT_NAMESPACE,
        "recipe_id": SURFACE_FINGERPRINT_RECIPE_ID,
        "algorithm": SURFACE_FINGERPRINT_ALGORITHM,
        "canonicalization": SURFACE_FINGERPRINT_CANONICALIZATION,
        "capture_surface": provenance.get("capture_surface"),
        "product_build": provenance.get("product_build"),
        "redaction_profile": provenance.get("redaction_profile"),
        "capability_contract_canonical_sha256": (
            capability_contract_canonical_sha256
        ),
        "candidate_schema_canonical_sha256": (
            candidate_schema_canonical_sha256
        ),
        "runtime_attestation_schema_canonical_sha256": (
            runtime_attestation_schema_canonical_sha256
        ),
        "candidate_kind": CANDIDATE_KIND,
        "candidate_terminal_marker_namespace": CANDIDATE_MARKER_NAMESPACE,
    }


def _reject_duplicate_json_members(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for name, item in pairs:
        if name in value:
            raise ValueError("duplicate JSON member")
        value[name] = item
    return value


def _reject_json_constant(value: str) -> Any:
    raise ValueError(f"non-finite JSON number is forbidden: {value}")


def _load_json(path: Path, issue: str, issues: list[str]) -> dict[str, Any]:
    try:
        if path.is_symlink():
            raise OSError
        resolved = path.resolve(strict=True)
        if not resolved.is_file() or resolved.stat().st_size > MAX_INPUT_BYTES:
            raise OSError
        value = json.loads(
            resolved.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_members,
            parse_constant=_reject_json_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, RuntimeError, ValueError):
        issues.append(issue)
        return {}
    if not isinstance(value, dict):
        issues.append(issue)
        return {}
    return value


def _has_exact_keys(value: Any, expected: frozenset[str]) -> bool:
    return isinstance(value, dict) and frozenset(value) == expected


def _run_once_shape_valid(value: Any) -> bool:
    return (
        _has_exact_keys(value, RUN_ONCE_KEYS)
        and all(type(value[name]) is bool for name in RUN_ONCE_BOOLEAN_KEYS)
        and all(type(value[name]) is int for name in RUN_ONCE_INTEGER_KEYS)
    )


def _legacy_recurring_shape_valid(value: Any) -> bool:
    return _has_exact_keys(value, LEGACY_RECURRING_KEYS) and all(
        type(value[name]) is bool for name in LEGACY_RECURRING_KEYS
    )


def _true(value: dict[str, Any], name: str) -> bool:
    return value.get(name) is True


def _false(value: dict[str, Any], name: str) -> bool:
    return value.get(name) is False


def audit_beeper_run_once_contract(
    *,
    candidate: dict[str, Any],
    candidate_schema: dict[str, Any],
    runtime_attestation_schema: dict[str, Any],
    automation_tool_contract: dict[str, Any],
) -> dict[str, Any]:
    """Audit declarative capability assertions without certifying runtime facts."""

    issues: list[str] = []
    schema_valid = _json_exact(candidate_schema, CANDIDATE_SCHEMA_EXPECTED)
    if not schema_valid:
        issues.append("candidate_schema_changed")

    runtime_attestation_schema_valid = _json_exact(
        runtime_attestation_schema,
        RUNTIME_ATTESTATION_SCHEMA_EXPECTED,
    )
    if not runtime_attestation_schema_valid:
        issues.append("runtime_attestation_receipt_schema_changed")

    candidate_valid = _json_exact(candidate, CANDIDATE_EXPECTED)
    if not candidate_valid:
        issues.append("candidate_contract_changed")

    candidate_marker_namespace = candidate.get(
        "candidate_terminal_marker_namespace"
    )
    marker_namespace_collision = (
        isinstance(candidate_marker_namespace, str)
        and candidate_marker_namespace.startswith(RESERVED_HISTORICAL_MARKER_PREFIX)
    )
    if marker_namespace_collision:
        issues.append("candidate_terminal_marker_namespace_collides_with_history")
    elif candidate_marker_namespace != CANDIDATE_MARKER_NAMESPACE:
        issues.append("candidate_terminal_marker_namespace_changed")
    candidate_marker_namespace_isolated = all(
        (
            candidate_valid,
            candidate_marker_namespace == CANDIDATE_MARKER_NAMESPACE,
            not marker_namespace_collision,
            candidate.get(
                "historical_terminal_marker_namespace_reuse_forbidden"
            )
            is True,
        )
    )

    capability_contract_canonical_sha256 = _canonical_sha256(
        _capability_contract_payload(automation_tool_contract)
    )
    candidate_schema_canonical_sha256 = _canonical_sha256(candidate_schema)
    runtime_attestation_schema_canonical_sha256 = _canonical_sha256(
        runtime_attestation_schema
    )

    tool_shape_valid = _has_exact_keys(automation_tool_contract, TOOL_CONTRACT_KEYS)
    if not tool_shape_valid:
        issues.append("automation_tool_contract_contains_unapproved_fields")

    top_value_types_valid = (
        tool_shape_valid
        and type(automation_tool_contract.get("schema_version")) is int
        and type(automation_tool_contract.get("surface_kind")) is str
    )
    if not top_value_types_valid:
        issues.append("automation_tool_contract_value_type_invalid")

    beeper = automation_tool_contract.get("beeper")
    beeper_contract_valid = _json_exact(
        beeper,
        BEEPER_EXPECTED,
    )
    if not beeper_contract_valid:
        issues.append("beeper_contract_changed")

    task_coordination_policy = automation_tool_contract.get(
        "task_coordination_policy"
    )
    task_coordination_policy_valid = _json_exact(
        task_coordination_policy,
        TASK_COORDINATION_POLICY_EXPECTED,
    )
    if not task_coordination_policy_valid:
        issues.append("task_coordination_policy_changed")

    provenance_value = automation_tool_contract.get("provenance")
    provenance_shape_valid = (
        _has_exact_keys(provenance_value, PROVENANCE_KEYS)
        and all(isinstance(provenance_value[name], str) for name in PROVENANCE_KEYS)
        and PRODUCT_BUILD_PATTERN.fullmatch(provenance_value["product_build"])
        is not None
        and all(
            _digest_string_valid(provenance_value[name])
            for name in (
                "capability_contract_canonical_sha256",
                "candidate_schema_canonical_sha256",
                "runtime_attestation_schema_canonical_sha256",
            )
        )
    )
    if not provenance_shape_valid:
        issues.append("product_contract_provenance_shape_or_type_changed")
    provenance = provenance_value if isinstance(provenance_value, dict) else {}
    provenance_contract_valid = all(
        (
            provenance_shape_valid,
            provenance.get("capture_surface") == CAPTURE_SURFACE,
            provenance.get("redaction_profile") == REDACTION_PROFILE,
        )
    )
    if not provenance_contract_valid:
        issues.append("product_contract_provenance_contract_changed")

    provenance_digest_relations = {
        "capability_contract_canonical_sha256": (
            capability_contract_canonical_sha256
        ),
        "candidate_schema_canonical_sha256": (
            candidate_schema_canonical_sha256
        ),
        "runtime_attestation_schema_canonical_sha256": (
            runtime_attestation_schema_canonical_sha256
        ),
    }
    provenance_digests_match = provenance_shape_valid
    if provenance_shape_valid:
        for field, expected_digest in provenance_digest_relations.items():
            if provenance.get(field) != expected_digest:
                provenance_digests_match = False
                issues.append(f"{field}_mismatch")
    product_contract_integrity_bound = all(
        (
            provenance_contract_valid,
            provenance_digests_match,
            schema_valid,
            runtime_attestation_schema_valid,
        )
    )

    fingerprint_value = automation_tool_contract.get("surface_fingerprint")
    surface_fingerprint_shape_valid = (
        _has_exact_keys(fingerprint_value, SURFACE_FINGERPRINT_KEYS)
        and all(
            isinstance(fingerprint_value[name], str)
            for name in SURFACE_FINGERPRINT_KEYS
        )
        and _digest_string_valid(fingerprint_value["sha256"])
    )
    if not surface_fingerprint_shape_valid:
        issues.append("surface_fingerprint_shape_or_type_changed")
    surface_fingerprint = (
        fingerprint_value if isinstance(fingerprint_value, dict) else {}
    )
    surface_fingerprint_recipe_valid = all(
        (
            surface_fingerprint_shape_valid,
            surface_fingerprint.get("namespace")
            == SURFACE_FINGERPRINT_NAMESPACE,
            surface_fingerprint.get("recipe_id")
            == SURFACE_FINGERPRINT_RECIPE_ID,
            surface_fingerprint.get("algorithm")
            == SURFACE_FINGERPRINT_ALGORITHM,
            surface_fingerprint.get("canonicalization")
            == SURFACE_FINGERPRINT_CANONICALIZATION,
        )
    )
    if not surface_fingerprint_recipe_valid:
        issues.append("surface_fingerprint_recipe_changed")
    expected_surface_fingerprint_sha256 = _canonical_sha256(
        _surface_fingerprint_payload(
            provenance=provenance,
            capability_contract_canonical_sha256=(
                capability_contract_canonical_sha256
            ),
            candidate_schema_canonical_sha256=(
                candidate_schema_canonical_sha256
            ),
            runtime_attestation_schema_canonical_sha256=(
                runtime_attestation_schema_canonical_sha256
            ),
        )
    )
    surface_fingerprint_digest_matches = (
        surface_fingerprint_shape_valid
        and surface_fingerprint.get("sha256")
        == expected_surface_fingerprint_sha256
    )
    if not surface_fingerprint_digest_matches:
        issues.append("surface_fingerprint_digest_mismatch")
    surface_fingerprint_integrity_bound = all(
        (
            product_contract_integrity_bound,
            surface_fingerprint_recipe_valid,
            surface_fingerprint_digest_matches,
            candidate_marker_namespace_isolated,
        )
    )

    legacy_recurring = automation_tool_contract.get("legacy_recurring")
    legacy_shape_valid = _legacy_recurring_shape_valid(legacy_recurring)
    if not legacy_shape_valid:
        issues.append("legacy_recurring_contract_shape_or_type_changed")
        legacy_recurring = {}
    candidate_uses_recurrence = _true(
        legacy_recurring, "candidate_uses_recurrence"
    )
    rrule_count_used_as_cap = _true(
        legacy_recurring, "rrule_count_used_as_cap"
    )
    if candidate_uses_recurrence or rrule_count_used_as_cap:
        issues.append("recurrence_count_not_hard_cap")

    run_once = automation_tool_contract.get("run_once")
    run_once_shape_valid = _run_once_shape_valid(run_once)
    if not run_once_shape_valid:
        issues.append("run_once_contract_shape_or_type_changed")
        run_once = {}

    one_shot_declared = run_once_shape_valid and _true(run_once, "available")
    if not one_shot_declared:
        issues.append("one_shot_execution_method_missing")

    surface_kind_declared = (
        top_value_types_valid
        and automation_tool_contract.get("schema_version")
        == PRODUCT_CONTRACT_SCHEMA_VERSION
        and automation_tool_contract.get("surface_kind") == CANDIDATE_KIND
    )
    if not surface_kind_declared:
        issues.append("materially_different_surface_declaration_missing")

    required_true = {
        "exact_existing_thread_responder": "exact_existing_thread_responder_missing",
        "responder_thread_id_required": "responder_thread_id_not_required",
        "new_thread_fallback_forbidden": "new_thread_fallback_not_forbidden",
        "cap_enforced_before_dispatch": "cap_not_enforced_before_dispatch",
        "single_use_dispatch_grant": "single_use_dispatch_grant_missing",
        "budget_consumed_atomically_before_dispatch": "dispatch_budget_not_consumed_atomically",
        "second_distinct_key_rejected_before_dispatch": "distinct_key_second_dispatch_not_rejected",
        "budget_non_resettable": "dispatch_budget_resettable",
        "budget_survives_restart_and_failover": "dispatch_budget_restart_failover_gap",
        "idempotency_key_required": "idempotency_key_not_required",
        "duplicate_key_returns_same_execution": "duplicate_key_may_create_new_execution",
        "immutable_execution_id": "immutable_execution_id_missing",
        "immutable_surface_fingerprint": "immutable_surface_fingerprint_missing",
        "immutable_run_receipt": "immutable_run_receipt_missing",
        "run_to_turn_mapping": "run_to_turn_mapping_missing",
        "terminal_completed_state": "terminal_completed_state_missing",
        "all_terminal_states_consume_budget": "terminal_state_may_restore_dispatch_budget",
        "all_terminal_states_next_run_null": "terminal_state_may_leave_future_dispatch",
        "post_run_next_run_null": "post_run_next_run_not_null",
        "queued_runs_suppressed": "queued_run_suppression_missing",
        "overlapping_runs_suppressed": "overlapping_run_suppression_missing",
        "retry_runs_suppressed": "retry_run_suppression_missing",
    }
    declared_true: dict[str, bool] = {}
    for field, issue in required_true.items():
        declared_true[field] = run_once_shape_valid and _true(run_once, field)
        if not declared_true[field]:
            issues.append(issue)

    if not _false(run_once, "rearm_or_update_allowed"):
        issues.append("rearm_or_update_allowed")
    if not _false(run_once, "recurrence_required"):
        issues.append("run_once_still_requires_recurrence")
    if not _false(run_once, "active_status_required"):
        issues.append("run_once_still_requires_active_status")

    max_turns = run_once.get("scheduler_enforced_max_model_turns")
    max_executions = run_once.get("max_executions_per_candidate")
    turn_cardinality = run_once.get("receipt_turn_cardinality")
    if type(max_turns) is not int or max_turns != 1:
        issues.append("product_max_model_turns_not_one")
    if type(max_executions) is not int or max_executions != 1:
        issues.append("max_executions_per_candidate_not_one")
    if type(turn_cardinality) is not int or turn_cardinality != 1:
        issues.append("receipt_turn_cardinality_not_one")

    total_budget_declared = all(
        (
            type(max_executions) is int and max_executions == 1,
            declared_true["single_use_dispatch_grant"],
            declared_true["budget_consumed_atomically_before_dispatch"],
            declared_true["second_distinct_key_rejected_before_dispatch"],
            declared_true["budget_non_resettable"],
            declared_true["budget_survives_restart_and_failover"],
            declared_true["all_terminal_states_consume_budget"],
            declared_true["all_terminal_states_next_run_null"],
            declared_true["post_run_next_run_null"],
            _false(run_once, "rearm_or_update_allowed"),
        )
    )
    pre_dispatch_cap_declared = all(
        (
            type(max_turns) is int and max_turns == 1,
            declared_true["cap_enforced_before_dispatch"],
            declared_true["queued_runs_suppressed"],
            declared_true["overlapping_runs_suppressed"],
            declared_true["retry_runs_suppressed"],
            total_budget_declared,
        )
    )
    idempotent_execution_declared = all(
        (
            declared_true["idempotency_key_required"],
            declared_true["duplicate_key_returns_same_execution"],
            declared_true["immutable_execution_id"],
        )
    )
    all_terminal_states_quiescent_declared = all(
        (
            declared_true["terminal_completed_state"],
            declared_true["all_terminal_states_consume_budget"],
            declared_true["all_terminal_states_next_run_null"],
            declared_true["post_run_next_run_null"],
        )
    )
    candidate_declares_new_surface_kind = all(
        (
            candidate_valid,
            schema_valid,
            tool_shape_valid,
            top_value_types_valid,
            legacy_shape_valid,
            run_once_shape_valid,
            beeper_contract_valid,
            task_coordination_policy_valid,
            surface_kind_declared,
            provenance_contract_valid,
            product_contract_integrity_bound,
            surface_fingerprint_integrity_bound,
            runtime_attestation_schema_valid,
            candidate_marker_namespace_isolated,
            one_shot_declared,
            not candidate_uses_recurrence,
            not rrule_count_used_as_cap,
            _false(run_once, "recurrence_required"),
            _false(run_once, "active_status_required"),
        )
    )
    single_beeper_declared = all(
        (
            candidate_valid,
            beeper_contract_valid,
            candidate.get("beeper_scope") == "bridge_installation",
            candidate.get("beeper_cardinality_required") == 1,
            candidate.get("exact_beeper_identity_required")
            is True,
            candidate.get(
                "beeper_identity_immutable_required"
            )
            is True,
            candidate.get("historical_beeper_reuse_forbidden") is True,
        )
    )
    beeper_role_declared = all(
        (
            candidate_valid,
            beeper_contract_valid,
            task_coordination_policy_valid,
            candidate.get("beeper_responder_contact_only_required")
            is True,
            candidate.get("beeper_scope_binding_forbidden") is True,
            candidate.get("beeper_as_responder_forbidden")
            is True,
            candidate.get("beeper_self_contact_forbidden") is True,
            candidate.get("alternate_responder_client_forbidden") is True,
            candidate.get(
                "operation_scoped_task_coordination_policy_required"
            )
            is True,
        )
    )
    desktop_responder_ownership_preserved_declared = all(
        (
            candidate_valid,
            beeper_contract_valid,
            candidate.get("desktop_responder_ownership_preserved_required")
            is True,
            beeper.get("desktop_responder_ownership_preserved") is True
            if isinstance(beeper, dict)
            else False,
        )
    )
    static_pass = not issues
    fixed_blockers = [
        "product_contract_provenance_unverified",
        "runtime_attestation_missing",
        "single_beeper_runtime_topology_unverified",
        "desktop_responder_ownership_runtime_unverified",
        "task_tool_surface_uncertified",
        "live_activation_not_implemented",
    ]

    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "command": "beeper-run-once-contract",
        "status": "pass" if static_pass else "fail",
        "candidate_schema_valid": schema_valid,
        "candidate_contract_valid": candidate_valid,
        "automation_tool_contract_shape_valid": (
            tool_shape_valid
            and top_value_types_valid
            and provenance_shape_valid
            and provenance_contract_valid
            and surface_fingerprint_shape_valid
            and surface_fingerprint_recipe_valid
            and beeper_contract_valid
            and task_coordination_policy_valid
            and legacy_shape_valid
            and run_once_shape_valid
        ),
        "product_contract_provenance_shape_valid": (
            provenance_shape_valid and provenance_contract_valid
        ),
        "product_contract_integrity_bound": product_contract_integrity_bound,
        "capability_contract_canonical_sha256": (
            capability_contract_canonical_sha256
        ),
        "candidate_schema_canonical_sha256": (
            candidate_schema_canonical_sha256
        ),
        "runtime_attestation_schema_canonical_sha256": (
            runtime_attestation_schema_canonical_sha256
        ),
        "surface_fingerprint_recipe_valid": surface_fingerprint_recipe_valid,
        "surface_fingerprint_integrity_bound": (
            surface_fingerprint_integrity_bound
        ),
        "surface_fingerprint_sha256": expected_surface_fingerprint_sha256,
        "candidate_marker_namespace_isolated": (
            candidate_marker_namespace_isolated
        ),
        "runtime_attestation_receipt_schema_valid": (
            runtime_attestation_schema_valid
        ),
        "candidate_declares_new_surface_kind": candidate_declares_new_surface_kind,
        "single_beeper_declared": (
            single_beeper_declared
        ),
        "beeper_role_declared": (
            beeper_role_declared
        ),
        "desktop_responder_ownership_preserved_declared": (
            desktop_responder_ownership_preserved_declared
        ),
        "task_coordination_policy_canonical_sha256": (
            TASK_COORDINATION_POLICY_CANONICAL_SHA256
        ),
        "one_shot_execution_declared": one_shot_declared,
        "exact_existing_thread_responder_declared": declared_true[
            "exact_existing_thread_responder"
        ],
        "single_use_total_budget_declared": total_budget_declared,
        "pre_dispatch_cap_declared": pre_dispatch_cap_declared,
        "idempotent_execution_declared": idempotent_execution_declared,
        "immutable_run_receipt_declared": declared_true["immutable_run_receipt"],
        "run_to_turn_mapping_declared": all(
            (
                declared_true["run_to_turn_mapping"],
                type(turn_cardinality) is int and turn_cardinality == 1,
            )
        ),
        "all_terminal_states_quiescent_declared": (
            all_terminal_states_quiescent_declared
        ),
        "product_contract_provenance_verified": False,
        "surface_materially_different_certified": False,
        "scheduler_cap_enforced_certified": False,
        "task_tool_surface_certified": False,
        "runtime_attestation_observed": False,
        "runtime_attestation_passed": False,
        "policy_admissible_for_runtime_attestation": static_pass,
        "runtime_attestation_required": True,
        "activation_allowed": False,
        "activation_blockers": sorted(set(issues + fixed_blockers)),
        "issues": sorted(set(issues)),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--candidate-schema", required=True, type=Path)
    parser.add_argument("--runtime-attestation-schema", required=True, type=Path)
    parser.add_argument("--automation-tool-contract", required=True, type=Path)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    load_issues: list[str] = []
    candidate = _load_json(args.candidate, "candidate_unreadable", load_issues)
    candidate_schema = _load_json(
        args.candidate_schema,
        "candidate_schema_unreadable",
        load_issues,
    )
    runtime_attestation_schema = _load_json(
        args.runtime_attestation_schema,
        "runtime_attestation_schema_unreadable",
        load_issues,
    )
    automation_tool_contract = _load_json(
        args.automation_tool_contract,
        "automation_tool_contract_unreadable",
        load_issues,
    )
    result = audit_beeper_run_once_contract(
        candidate=candidate,
        candidate_schema=candidate_schema,
        runtime_attestation_schema=runtime_attestation_schema,
        automation_tool_contract=automation_tool_contract,
    )
    if load_issues:
        result["status"] = "fail"
        result["policy_admissible_for_runtime_attestation"] = False
        result["issues"] = sorted(set(result["issues"] + load_issues))
        result["activation_blockers"] = sorted(
            set(result["activation_blockers"] + load_issues)
        )
    if args.json:
        print(
            json.dumps(
                result,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
        )
    else:
        print(f"Beeper run-once candidate static contract: {result['status']}")
        print("Activation allowed: no")
        if result["issues"]:
            print("Issues: " + ", ".join(result["issues"]))
        print("A separate materially different runtime attestation is required.")
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
