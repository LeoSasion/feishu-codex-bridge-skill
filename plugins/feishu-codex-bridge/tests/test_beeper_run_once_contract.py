from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "beeper_run_once_contract.py"
SCHEMA_PATH = (
    PLUGIN_ROOT / "assets" / "desktop-beeper-run-once-candidate.schema.json"
)
RUNTIME_ATTESTATION_SCHEMA_PATH = (
    PLUGIN_ROOT
    / "assets"
    / "desktop-beeper-run-once-runtime-attestation.schema.json"
)
SPEC = importlib.util.spec_from_file_location("beeper_run_once_contract", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("beeper_run_once_contract module could not be loaded")
BEEPER_CONTRACT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BEEPER_CONTRACT)


class BeeperRunOnceContractTests(unittest.TestCase):
    def _candidate(self, **updates):
        return {**BEEPER_CONTRACT.CANDIDATE_EXPECTED, **updates}

    def _schema(self):
        return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    def _runtime_attestation_schema(self):
        return json.loads(
            RUNTIME_ATTESTATION_SCHEMA_PATH.read_text(encoding="utf-8")
        )

    def _ideal_tool_contract(self, *, schema=None, runtime_attestation_schema=None):
        candidate_schema = self._schema() if schema is None else schema
        receipt_schema = (
            self._runtime_attestation_schema()
            if runtime_attestation_schema is None
            else runtime_attestation_schema
        )
        contract = {
            "schema_version": 4,
            "surface_kind": "single_beeper_run_once",
            "beeper": dict(BEEPER_CONTRACT.BEEPER_EXPECTED),
            "task_coordination_policy": json.loads(
                json.dumps(
                    BEEPER_CONTRACT.TASK_COORDINATION_POLICY_EXPECTED,
                    ensure_ascii=True,
                )
            ),
            "run_once": {
                "available": True,
                "exact_existing_thread_responder": True,
                "responder_thread_id_required": True,
                "new_thread_fallback_forbidden": True,
                "scheduler_enforced_max_model_turns": 1,
                "max_executions_per_candidate": 1,
                "cap_enforced_before_dispatch": True,
                "single_use_dispatch_grant": True,
                "budget_consumed_atomically_before_dispatch": True,
                "second_distinct_key_rejected_before_dispatch": True,
                "budget_non_resettable": True,
                "budget_survives_restart_and_failover": True,
                "rearm_or_update_allowed": False,
                "idempotency_key_required": True,
                "duplicate_key_returns_same_execution": True,
                "immutable_execution_id": True,
                "immutable_surface_fingerprint": True,
                "immutable_run_receipt": True,
                "run_to_turn_mapping": True,
                "receipt_turn_cardinality": 1,
                "terminal_completed_state": True,
                "all_terminal_states_consume_budget": True,
                "all_terminal_states_next_run_null": True,
                "post_run_next_run_null": True,
                "recurrence_required": False,
                "active_status_required": False,
                "queued_runs_suppressed": True,
                "overlapping_runs_suppressed": True,
                "retry_runs_suppressed": True,
            },
            "legacy_recurring": {
                "candidate_uses_recurrence": False,
                "rrule_count_used_as_cap": False,
            },
        }
        capability_digest = BEEPER_CONTRACT._canonical_sha256(
            BEEPER_CONTRACT._capability_contract_payload(contract)
        )
        candidate_schema_digest = BEEPER_CONTRACT._canonical_sha256(
            candidate_schema
        )
        receipt_schema_digest = BEEPER_CONTRACT._canonical_sha256(
            receipt_schema
        )
        provenance = {
            "capture_surface": BEEPER_CONTRACT.CAPTURE_SURFACE,
            "product_build": "desktop-26.825.4187.0",
            "redaction_profile": BEEPER_CONTRACT.REDACTION_PROFILE,
            "capability_contract_canonical_sha256": capability_digest,
            "candidate_schema_canonical_sha256": candidate_schema_digest,
            "runtime_attestation_schema_canonical_sha256": (
                receipt_schema_digest
            ),
        }
        fingerprint_digest = BEEPER_CONTRACT._canonical_sha256(
            BEEPER_CONTRACT._surface_fingerprint_payload(
                provenance=provenance,
                capability_contract_canonical_sha256=capability_digest,
                candidate_schema_canonical_sha256=candidate_schema_digest,
                runtime_attestation_schema_canonical_sha256=(
                    receipt_schema_digest
                ),
            )
        )
        contract["provenance"] = provenance
        contract["surface_fingerprint"] = {
            "namespace": BEEPER_CONTRACT.SURFACE_FINGERPRINT_NAMESPACE,
            "recipe_id": BEEPER_CONTRACT.SURFACE_FINGERPRINT_RECIPE_ID,
            "algorithm": BEEPER_CONTRACT.SURFACE_FINGERPRINT_ALGORITHM,
            "canonicalization": (
                BEEPER_CONTRACT.SURFACE_FINGERPRINT_CANONICALIZATION
            ),
            "sha256": fingerprint_digest,
        }
        return contract

    def _audit(
        self,
        *,
        candidate=None,
        schema=None,
        runtime_attestation_schema=None,
        tool_contract=None,
    ):
        candidate_schema = self._schema() if schema is None else schema
        receipt_schema = (
            self._runtime_attestation_schema()
            if runtime_attestation_schema is None
            else runtime_attestation_schema
        )
        return BEEPER_CONTRACT.audit_beeper_run_once_contract(
            candidate=self._candidate() if candidate is None else candidate,
            candidate_schema=candidate_schema,
            runtime_attestation_schema=receipt_schema,
            automation_tool_contract=(
                self._ideal_tool_contract(
                    schema=candidate_schema,
                    runtime_attestation_schema=receipt_schema,
                )
                if tool_contract is None
                else tool_contract
            ),
        )

    def test_future_ideal_static_shape_passes_but_never_certifies_or_activates(self) -> None:
        result = self._audit()
        self.assertEqual(result["status"], "pass")
        self.assertTrue(result["candidate_declares_new_surface_kind"])
        self.assertTrue(result["single_beeper_declared"])
        self.assertTrue(result["beeper_role_declared"])
        self.assertTrue(result["desktop_responder_ownership_preserved_declared"])
        self.assertEqual(
            result["task_coordination_policy_canonical_sha256"],
            BEEPER_CONTRACT.TASK_COORDINATION_POLICY_CANONICAL_SHA256,
        )
        self.assertTrue(result["single_use_total_budget_declared"])
        self.assertTrue(result["pre_dispatch_cap_declared"])
        self.assertTrue(result["product_contract_provenance_shape_valid"])
        self.assertTrue(result["product_contract_integrity_bound"])
        self.assertTrue(result["surface_fingerprint_recipe_valid"])
        self.assertTrue(result["surface_fingerprint_integrity_bound"])
        self.assertTrue(result["candidate_marker_namespace_isolated"])
        self.assertTrue(result["runtime_attestation_receipt_schema_valid"])
        self.assertTrue(result["policy_admissible_for_runtime_attestation"])
        self.assertFalse(result["product_contract_provenance_verified"])
        self.assertFalse(result["surface_materially_different_certified"])
        self.assertFalse(result["scheduler_cap_enforced_certified"])
        self.assertFalse(result["task_tool_surface_certified"])
        self.assertFalse(result["runtime_attestation_observed"])
        self.assertFalse(result["runtime_attestation_passed"])
        self.assertTrue(result["runtime_attestation_required"])
        self.assertFalse(result["activation_allowed"])
        self.assertIn(
            "product_contract_provenance_unverified",
            result["activation_blockers"],
        )

    def test_single_beeper_topology_is_mandatory(self) -> None:
        candidate_mutations = {
            "beeper_scope": "global_desktop",
            "beeper_cardinality_required": 2,
            "exact_beeper_identity_required": False,
            "beeper_identity_immutable_required": False,
            "historical_beeper_reuse_forbidden": False,
        }
        for field, value in candidate_mutations.items():
            with self.subTest(candidate_field=field):
                result = self._audit(candidate=self._candidate(**{field: value}))
                self.assertIn("candidate_contract_changed", result["issues"])
                self.assertFalse(result["single_beeper_declared"])
                self.assertFalse(result["policy_admissible_for_runtime_attestation"])

        beeper_mutations = {
            "scope": "global_desktop",
            "active_cardinality": 2,
            "exact_identity_bound": False,
            "identity_immutable": False,
            "historical_beeper_reused": True,
        }
        for field, value in beeper_mutations.items():
            with self.subTest(beeper_field=field):
                contract = self._ideal_tool_contract()
                contract["beeper"][field] = value
                result = self._audit(tool_contract=contract)
                self.assertIn("beeper_contract_changed", result["issues"])
                self.assertFalse(result["single_beeper_declared"])
                self.assertFalse(result["policy_admissible_for_runtime_attestation"])

    def test_beeper_role_isolation_and_responder_ownership_are_mandatory(self) -> None:
        candidate_mutations = {
            "beeper_responder_contact_only_required": False,
            "beeper_scope_binding_forbidden": False,
            "beeper_as_responder_forbidden": False,
            "beeper_self_contact_forbidden": False,
            "alternate_responder_client_forbidden": False,
            "operation_scoped_task_coordination_policy_required": False,
        }
        for field, value in candidate_mutations.items():
            with self.subTest(candidate_role_field=field):
                result = self._audit(candidate=self._candidate(**{field: value}))
                self.assertIn("candidate_contract_changed", result["issues"])
                self.assertFalse(result["beeper_role_declared"])

        ownership_result = self._audit(
            candidate=self._candidate(
                desktop_responder_ownership_preserved_required=False
            )
        )
        self.assertIn("candidate_contract_changed", ownership_result["issues"])
        self.assertFalse(
            ownership_result["desktop_responder_ownership_preserved_declared"]
        )

        beeper_mutations = {
            "feishu_scope_binding_allowed": True,
            "business_responder_allowed": True,
            "self_contact_allowed": True,
            "responder_contact_only": False,
            "desktop_responder_ownership_preserved": False,
            "alternate_responder_client_allowed": True,
        }
        for field, value in beeper_mutations.items():
            with self.subTest(beeper_role_field=field):
                contract = self._ideal_tool_contract()
                contract["beeper"][field] = value
                result = self._audit(tool_contract=contract)
                self.assertIn("beeper_contract_changed", result["issues"])
                if field == "desktop_responder_ownership_preserved":
                    self.assertFalse(
                        result["desktop_responder_ownership_preserved_declared"]
                    )
                else:
                    self.assertFalse(
                        result["beeper_role_declared"]
                    )

    def test_runtime_attestation_cardinality_is_independent_from_beeper_turn_count(self) -> None:
        schema = self._runtime_attestation_schema()
        pass_properties = schema["allOf"][0]["then"]["properties"]
        self.assertEqual(pass_properties["beeper_turn_count"], {"const": 1})
        self.assertEqual(
            pass_properties["active_beeper_count"],
            {"const": 1},
        )

        pass_properties["active_beeper_count"]["const"] = 2
        result = self._audit(runtime_attestation_schema=schema)
        self.assertIn(
            "runtime_attestation_receipt_schema_changed", result["issues"]
        )
        self.assertFalse(result["runtime_attestation_receipt_schema_valid"])
        self.assertEqual(pass_properties["beeper_turn_count"], {"const": 1})

    def test_task_coordination_policy_is_exact_and_digest_bound(self) -> None:
        contract = self._ideal_tool_contract()
        expected_digest = BEEPER_CONTRACT.TASK_COORDINATION_POLICY_CANONICAL_SHA256
        self.assertEqual(
            expected_digest,
            "046ba6d2902a190c41ee2da8344bd052d22d7b159454aa476a25bab632a60bd5",
        )
        result = self._audit(tool_contract=contract)
        self.assertEqual(
            result["task_coordination_policy_canonical_sha256"],
            expected_digest,
        )

        policy_mutations = {
            "profile": "unbounded_desktop_tools_v1",
            "allowed_methods": [
                *BEEPER_CONTRACT.TASK_COORDINATION_POLICY_EXPECTED[
                    "allowed_methods"
                ],
                "navigate_to_codex_page",
            ],
            "operation_scoped_minimum_subset_required": False,
            "unapproved_method_allowed": True,
            "non_desktop_responder_transport_allowed": True,
            "beeper_business_execution_allowed": True,
        }
        for field, value in policy_mutations.items():
            with self.subTest(policy_field=field):
                changed_contract = self._ideal_tool_contract()
                changed_contract["task_coordination_policy"][field] = value
                result = self._audit(tool_contract=changed_contract)
                self.assertIn(
                    "task_coordination_policy_changed", result["issues"]
                )
                self.assertIn(
                    "capability_contract_canonical_sha256_mismatch",
                    result["issues"],
                )
                self.assertIn(
                    "surface_fingerprint_digest_mismatch", result["issues"]
                )
                self.assertFalse(
                    result["beeper_role_declared"]
                )

        schema = self._runtime_attestation_schema()
        self.assertEqual(
            schema["properties"]["task_coordination_policy_canonical_sha256"],
            {"const": expected_digest},
        )
        self.assertEqual(
            schema["allOf"][0]["then"]["properties"][
                "task_coordination_policy_canonical_sha256"
            ],
            {"const": expected_digest},
        )
        schema["properties"]["task_coordination_policy_canonical_sha256"] = {
            "type": "string",
            "pattern": "^[0-9a-f]{64}$",
        }
        result = self._audit(runtime_attestation_schema=schema)
        self.assertIn(
            "runtime_attestation_receipt_schema_changed", result["issues"]
        )
        self.assertFalse(result["runtime_attestation_receipt_schema_valid"])

    def test_current_recurring_only_surface_fails_closed(self) -> None:
        contract = self._ideal_tool_contract()
        contract["surface_kind"] = "generic_recurring_producer_surface"
        contract["run_once"] = None
        contract["legacy_recurring"] = {
            "candidate_uses_recurrence": True,
            "rrule_count_used_as_cap": True,
        }
        result = self._audit(tool_contract=contract)
        self.assertEqual(result["status"], "fail")
        self.assertIn("recurrence_count_not_hard_cap", result["issues"])
        self.assertIn("one_shot_execution_method_missing", result["issues"])
        self.assertFalse(result["activation_allowed"])

    def test_cap_must_be_one_and_enforced_before_dispatch(self) -> None:
        contract = self._ideal_tool_contract()
        contract["run_once"]["scheduler_enforced_max_model_turns"] = 2
        contract["run_once"]["cap_enforced_before_dispatch"] = False
        result = self._audit(tool_contract=contract)
        self.assertIn("product_max_model_turns_not_one", result["issues"])
        self.assertIn("cap_not_enforced_before_dispatch", result["issues"])
        self.assertFalse(result["pre_dispatch_cap_declared"])

    def test_single_use_budget_rejects_distinct_keys_and_cannot_reset(self) -> None:
        contract = self._ideal_tool_contract()
        updates = {
            "max_executions_per_candidate": 2,
            "single_use_dispatch_grant": False,
            "budget_consumed_atomically_before_dispatch": False,
            "second_distinct_key_rejected_before_dispatch": False,
            "budget_non_resettable": False,
            "budget_survives_restart_and_failover": False,
            "rearm_or_update_allowed": True,
        }
        contract["run_once"].update(updates)
        result = self._audit(tool_contract=contract)
        for issue in (
            "max_executions_per_candidate_not_one",
            "single_use_dispatch_grant_missing",
            "dispatch_budget_not_consumed_atomically",
            "distinct_key_second_dispatch_not_rejected",
            "dispatch_budget_resettable",
            "dispatch_budget_restart_failover_gap",
            "rearm_or_update_allowed",
        ):
            self.assertIn(issue, result["issues"])
        self.assertFalse(result["single_use_total_budget_declared"])

    def test_all_queued_overlap_and_retry_paths_must_be_suppressed(self) -> None:
        for field, issue in (
            ("queued_runs_suppressed", "queued_run_suppression_missing"),
            ("overlapping_runs_suppressed", "overlapping_run_suppression_missing"),
            ("retry_runs_suppressed", "retry_run_suppression_missing"),
        ):
            with self.subTest(field=field):
                contract = self._ideal_tool_contract()
                contract["run_once"][field] = False
                result = self._audit(tool_contract=contract)
                self.assertIn(issue, result["issues"])
                self.assertFalse(result["pre_dispatch_cap_declared"])

    def test_idempotency_and_immutable_execution_are_mandatory(self) -> None:
        contract = self._ideal_tool_contract()
        contract["run_once"]["idempotency_key_required"] = False
        contract["run_once"]["duplicate_key_returns_same_execution"] = False
        contract["run_once"]["immutable_execution_id"] = False
        result = self._audit(tool_contract=contract)
        self.assertIn("idempotency_key_not_required", result["issues"])
        self.assertIn("duplicate_key_may_create_new_execution", result["issues"])
        self.assertIn("immutable_execution_id_missing", result["issues"])
        self.assertFalse(result["idempotent_execution_declared"])

    def test_responder_receipt_mapping_and_all_terminal_states_are_mandatory(self) -> None:
        contract = self._ideal_tool_contract()
        updates = {
            "responder_thread_id_required": False,
            "new_thread_fallback_forbidden": False,
            "immutable_run_receipt": False,
            "run_to_turn_mapping": False,
            "receipt_turn_cardinality": 2,
            "terminal_completed_state": False,
            "all_terminal_states_consume_budget": False,
            "all_terminal_states_next_run_null": False,
            "post_run_next_run_null": False,
        }
        contract["run_once"].update(updates)
        result = self._audit(tool_contract=contract)
        for issue in (
            "responder_thread_id_not_required",
            "new_thread_fallback_not_forbidden",
            "immutable_run_receipt_missing",
            "run_to_turn_mapping_missing",
            "receipt_turn_cardinality_not_one",
            "terminal_completed_state_missing",
            "terminal_state_may_restore_dispatch_budget",
            "terminal_state_may_leave_future_dispatch",
            "post_run_next_run_not_null",
        ):
            self.assertIn(issue, result["issues"])
        self.assertFalse(result["all_terminal_states_quiescent_declared"])
        self.assertFalse(result["single_use_total_budget_declared"])
        self.assertFalse(result["pre_dispatch_cap_declared"])

    def test_candidate_marker_namespace_cannot_use_retired_prefix(self) -> None:
        for marker_namespace in (
            "feishu-codex-bridge.legacy.retired-producer.v9",
            "feishu-codex-bridge.legacy.unseen-future-suffix",
        ):
            with self.subTest(marker_namespace=marker_namespace):
                candidate = self._candidate(
                    candidate_terminal_marker_namespace=marker_namespace
                )
                result = self._audit(candidate=candidate)
                self.assertIn(
                    "candidate_terminal_marker_namespace_collides_with_history",
                    result["issues"],
                )
                self.assertFalse(result["candidate_marker_namespace_isolated"])
                self.assertFalse(result["activation_allowed"])

    def test_provenance_digests_bind_contract_and_both_source_schemas(self) -> None:
        for field, issue in (
            (
                "capability_contract_canonical_sha256",
                "capability_contract_canonical_sha256_mismatch",
            ),
            (
                "candidate_schema_canonical_sha256",
                "candidate_schema_canonical_sha256_mismatch",
            ),
            (
                "runtime_attestation_schema_canonical_sha256",
                "runtime_attestation_schema_canonical_sha256_mismatch",
            ),
        ):
            with self.subTest(field=field):
                contract = self._ideal_tool_contract()
                contract["provenance"][field] = "0" * 64
                result = self._audit(tool_contract=contract)
                self.assertIn(issue, result["issues"])
                self.assertFalse(result["product_contract_integrity_bound"])
                self.assertFalse(result["surface_fingerprint_integrity_bound"])
                self.assertFalse(result["product_contract_provenance_verified"])
                self.assertFalse(result["activation_allowed"])

    def test_capability_mutation_invalidates_provenance_and_fingerprint(self) -> None:
        contract = self._ideal_tool_contract()
        contract["run_once"]["queued_runs_suppressed"] = False
        result = self._audit(tool_contract=contract)
        self.assertIn(
            "capability_contract_canonical_sha256_mismatch",
            result["issues"],
        )
        self.assertIn("surface_fingerprint_digest_mismatch", result["issues"])
        self.assertFalse(result["product_contract_integrity_bound"])
        self.assertFalse(result["surface_fingerprint_integrity_bound"])

    def test_surface_fingerprint_recipe_and_digest_are_recomputed(self) -> None:
        contract = self._ideal_tool_contract()
        original = contract["surface_fingerprint"]["sha256"]
        result = self._audit(tool_contract=contract)
        self.assertEqual(result["surface_fingerprint_sha256"], original)

        contract["surface_fingerprint"]["sha256"] = "f" * 64
        result = self._audit(tool_contract=contract)
        self.assertIn("surface_fingerprint_digest_mismatch", result["issues"])
        self.assertFalse(result["surface_fingerprint_integrity_bound"])

        contract = self._ideal_tool_contract()
        contract["surface_fingerprint"]["canonicalization"] = "untrusted"
        result = self._audit(tool_contract=contract)
        self.assertIn("surface_fingerprint_recipe_changed", result["issues"])
        self.assertFalse(result["surface_fingerprint_recipe_valid"])

    def test_provenance_is_closed_bounded_and_never_self_certifies(self) -> None:
        contract = self._ideal_tool_contract()
        contract["provenance"]["prompt"] = "must never be accepted or echoed"
        result = self._audit(tool_contract=contract)
        wire = json.dumps(result, ensure_ascii=True, separators=(",", ":"))
        self.assertIn(
            "product_contract_provenance_shape_or_type_changed",
            result["issues"],
        )
        self.assertFalse(result["product_contract_provenance_verified"])
        self.assertNotIn("must never be accepted or echoed", wire)

        contract = self._ideal_tool_contract()
        contract["provenance"]["product_build"] = "../unbounded path"
        result = self._audit(tool_contract=contract)
        self.assertIn(
            "product_contract_provenance_shape_or_type_changed",
            result["issues"],
        )

    def test_runtime_attestation_receipt_schema_is_exact_and_answer_free(self) -> None:
        schema = self._runtime_attestation_schema()
        forbidden = {
            "task_id",
            "beeper_thread_id",
            "responder_thread_id",
            "turn_id",
            "run_id",
            "automation_id",
            "path",
            "prompt",
            "answer",
            "queue",
            "remote_error",
        }
        self.assertTrue(forbidden.isdisjoint(schema["properties"]))
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["activation_allowed"], {"const": False})
        self.assertEqual(
            schema["allOf"][0]["if"],
            {
                "properties": {"status": {"const": "pass"}},
                "required": ["status"],
            },
        )
        expected_pass_assertions = {
            "receipt_immutable": True,
            "single_use_grant_consumed_before_dispatch": True,
            "execution_count": 1,
            "beeper_turn_count": 1,
            "active_beeper_count": 1,
            "beeper_identity_bound": True,
            "beeper_identity_stable": True,
            "historical_beeper_reuse_detected": False,
            "beeper_scope_binding_count": 0,
            "beeper_responder_collision_count": 0,
            "beeper_self_contact_count": 0,
            "non_task_coordination_call_count": 0,
            "beeper_business_execution_count": 0,
            "alternate_responder_client_count": 0,
            "desktop_responder_ownership_preserved": True,
            "run_to_turn_receipt_cardinality": 1,
            "same_key_same_execution": True,
            "distinct_key_rejected_before_dispatch": True,
            "queued_second_dispatch_count": 0,
            "overlap_second_dispatch_count": 0,
            "retry_second_dispatch_count": 0,
            "terminal_budget_consumed": True,
            "next_run_at_is_null": True,
            "rearm_allowed": False,
            "quiet_window_new_execution_count": 0,
            "quiet_window_new_turn_count": 0,
            "task_coordination_policy_canonical_sha256": (
                BEEPER_CONTRACT.TASK_COORDINATION_POLICY_CANONICAL_SHA256
            ),
        }
        self.assertEqual(
            schema["allOf"][0]["then"]["properties"],
            {
                name: {"const": value}
                for name, value in expected_pass_assertions.items()
            },
        )

        for field, expected in expected_pass_assertions.items():
            with self.subTest(pass_assertion=field):
                changed_schema = self._runtime_attestation_schema()
                changed_schema["allOf"][0]["then"]["properties"][field][
                    "const"
                ] = not expected if type(expected) is bool else 8
                result = self._audit(
                    runtime_attestation_schema=changed_schema
                )
                self.assertIn(
                    "runtime_attestation_receipt_schema_changed",
                    result["issues"],
                )
                self.assertFalse(
                    result["runtime_attestation_receipt_schema_valid"]
                )

        schema["required"].append("prompt")
        schema["properties"]["prompt"] = {"type": "string"}
        result = self._audit(runtime_attestation_schema=schema)
        self.assertIn(
            "runtime_attestation_receipt_schema_changed", result["issues"]
        )
        self.assertFalse(result["runtime_attestation_receipt_schema_valid"])
        self.assertFalse(result["runtime_attestation_observed"])
        self.assertFalse(result["runtime_attestation_passed"])
        self.assertFalse(result["activation_allowed"])

    def test_json_contract_boundaries_are_exact_and_answer_free(self) -> None:
        with self.subTest(case="exact_value_types"):
            contract = self._ideal_tool_contract()
            contract["legacy_recurring"]["candidate_uses_recurrence"] = None
            contract["legacy_recurring"]["rrule_count_used_as_cap"] = "unknown"
            contract["run_once"]["scheduler_enforced_max_model_turns"] = True
            result = self._audit(tool_contract=contract)
            self.assertIn(
                "legacy_recurring_contract_shape_or_type_changed",
                result["issues"],
            )
            self.assertIn(
                "run_once_contract_shape_or_type_changed", result["issues"]
            )
            self.assertFalse(result["automation_tool_contract_shape_valid"])
            self.assertFalse(result["candidate_declares_new_surface_kind"])

            candidate_result = self._audit(
                candidate=self._candidate(schema_version=True)
            )
            self.assertIn(
                "candidate_contract_changed", candidate_result["issues"]
            )

        with self.subTest(case="unapproved_field_is_not_echoed"):
            contract = self._ideal_tool_contract()
            contract["prompt"] = "must never be echoed"
            result = self._audit(tool_contract=contract)
            wire = json.dumps(
                result, ensure_ascii=True, separators=(",", ":")
            )
            self.assertIn(
                "automation_tool_contract_contains_unapproved_fields",
                result["issues"],
            )
            self.assertNotIn("must never be echoed", wire)
            self.assertTrue(wire.isascii())

    def test_candidate_schema_is_exact_not_merely_compatible(self) -> None:
        for mutate in (
            lambda schema: schema.update({"not": {}}),
            lambda schema: schema["required"].append(schema["required"][0]),
            lambda schema: schema["required"].append({"not": "a string"}),
            lambda schema: schema["properties"]["schema_version"].update(
                {"not": {}}
            ),
            lambda schema: schema["properties"]["schema_version"].update(
                {"const": True}
            ),
        ):
            with self.subTest(mutate=mutate):
                schema = self._schema()
                mutate(schema)
                result = self._audit(schema=schema)
                self.assertIn("candidate_schema_changed", result["issues"])
                self.assertFalse(result["candidate_schema_valid"])

    def test_loader_rejects_bounded_strict_json_violations(self) -> None:
        cases = (
            (
                "oversized",
                (b"{" + b" " * BEEPER_CONTRACT.MAX_INPUT_BYTES + b"}",),
                "oversized_unreadable",
            ),
            (
                "duplicate_members",
                (
                    b'{"schema_version":1,"schema_version":1}',
                    b'{"properties":{"field":{"const":1,"const":1}}}',
                    b'{"run_once":{"available":true,"available":true}}',
                ),
                "duplicate_unreadable",
            ),
            (
                "non_finite_number",
                (b'{"schema_version":NaN}',),
                "non_finite_unreadable",
            ),
        )
        with tempfile.TemporaryDirectory() as temp_root:
            for case, documents, expected_issue in cases:
                for index, document in enumerate(documents):
                    with self.subTest(case=case, index=index):
                        invalid = Path(temp_root) / f"{case}-{index}.json"
                        invalid.write_bytes(document)
                        issues = []
                        loaded = BEEPER_CONTRACT._load_json(
                            invalid, expected_issue, issues
                        )
                        self.assertEqual(loaded, {})
                        self.assertEqual(issues, [expected_issue])

    def test_auditor_imports_and_calls_stay_source_only(self) -> None:
        source = SCRIPT_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(SCRIPT_PATH))
        imported_roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".", 1)[0])
        self.assertEqual(
            imported_roots,
            {
                "__future__",
                "argparse",
                "hashlib",
                "json",
                "pathlib",
                "re",
                "typing",
            },
        )
        for forbidden in (
            "subprocess",
            "Popen",
            "Start-Process",
            "automation_update",
            "mcp__codex_app",
            "codex queue",
            "app-server",
            "beeper_queue_cli",
            "lark-cli",
            "urllib",
            "requests.",
            "socket",
            "http.client",
            "shell=True",
            "os.system",
            "startfile",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
