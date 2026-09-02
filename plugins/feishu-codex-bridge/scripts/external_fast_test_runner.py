from __future__ import annotations

import argparse
import contextlib
import io
import json
from pathlib import Path
import sys
import unittest


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from external_p0b_test_runner import (  # noqa: E402
    REQUIRED_FAULT_TEST_IDS,
    RecordingResult,
    _iter_tests,
)


SCHEMA_VERSION = 1
LANE_NAME = "development-fast"

SMOKE_TEST_IDS = (
    "test_runtime.ConfigDefaultsTests.test_plain_text_is_the_exact_return_default",
    "test_routing.RoutingTests.test_native_envelope_normalization_and_group_mention",
    "test_routing.RoutingTests.test_plain_text_single_part_preserves_all_whitespace_and_unicode",
    "test_state.DurableStateTests.test_dedup_completion_and_payload_erasure",
    "test_runtime.InitWizardTests.test_only_init_dispatches_and_unknown_slash_input_is_rejected",
    "test_runtime.ProducerFailClosedRuntimeTests.test_unclaimed_beeper_reports_safe_terminal_without_replay",
    "test_beeper_client.BeeperClientContractTests.test_client_uses_only_beeper_queue_queue",
    "test_beeper_client.BeeperClientContractTests.test_message_uses_send_message_to_thread_and_preserves_transport_manifest",
    "test_beeper_queue.BeeperQueueTests.test_final_callback_submission_reader_preserves_exact_unicode",
    "test_runtime.DesktopBeeperPromptContractTests.test_final_callback_mcp_exposes_only_controller_tools",
    "test_runtime.DesktopBeeperPromptContractTests.test_bridge_plugin_bundles_responder_owned_mcp_final_callback",
    "test_agents_rules.MachineReadableDiagnosticsTests.test_json_diagnostics_emit_one_versioned_object",
)

CONTRACT_TEST_IDS = (
    "test_source_route_contract.SourceRouteContractTests.test_canonical_marketplace_source_is_development_eligible",
    "test_source_route_contract.SourceRouteContractTests.test_installed_snapshot_is_diagnostic_only",
    "test_source_route_contract.SourceRouteContractTests.test_arbitrary_copy_and_legacy_root_are_rejected",
    "test_source_route_contract.SourceRouteContractTests.test_ambiguous_marketplace_entries_fail_closed",
    "test_source_route_contract.SourceRouteContractTests.test_duplicate_json_members_fail_closed",
    "test_source_route_contract.SourceRouteContractTests.test_identity_and_inventory_role_mismatches_fail_closed",
    "test_agents_rules.AgentsRulesMergeTests.test_fragment_uses_canonical_source_and_latest_first_capabilities",
    "test_agents_rules.AgentsRulesMergeTests.test_producer_is_isolated_and_historical_beeper_is_terminal",
    "test_agents_rules.MachineReadableDiagnosticsTests.test_readiness_source_is_read_only_and_never_scans_payload_receipts",
    "test_agents_rules.MachineReadableDiagnosticsTests.test_doctor_and_readiness_fail_closed_on_installed_historical_allow_rule",
    "test_agents_rules.MachineReadableDiagnosticsTests.test_run_once_readiness_evidence_is_strictly_bound_but_cannot_self_attest",
    "test_agents_rules.BridgePidIdentityTests.test_foreign_reused_pid_is_never_reported_or_stopped_as_bridge",
    "test_beeper_client.BeeperClientContractTests.test_catalog_uses_strict_readonly_lane_without_paths",
    "test_external_p0b_driver.ExternalP0BTestDriverTests.test_stopped_status_predicate_accepts_only_signed_or_hook_refresh_shape",
    "test_runtime.InitWizardTests.test_snapshot_number_requires_confirmation_before_binding",
    "test_agents_rules.BridgeLifecycleDetachmentTests.test_bridge_survives_transient_kill_on_close_hook_job",
    "test_beeper_run_once_contract.BeeperRunOnceContractTests.test_single_beeper_topology_is_mandatory",
    "test_beeper_run_once_contract.BeeperRunOnceContractTests.test_beeper_role_isolation_and_responder_ownership_are_mandatory",
    "test_terminology_contract.CanonicalTerminologyTests.test_current_protocol_has_no_role_aliases",
    "test_runtime.InitWizardTests.test_init_task_title_marker_is_never_an_attachment",
    "test_runtime.InitWizardTests.test_init_project_label_marker_is_never_an_attachment",
    "test_state.AccessAndSessionTests.test_catalog_binding_is_cas_persisted_without_display_or_path_metadata",
    "test_runtime.LifecycleLeaseTests.test_viability_is_source_and_host_bound_fail_closed",
    "test_runtime.DesktopBeeperPromptContractTests.test_historical_routes_are_absent_and_zero_allowlist_is_enforced",
    "test_runtime.DesktopBeeperPromptContractTests.test_current_send_is_final_callback_sealed_and_unsupported_steer_is_rejected",
)


def lane_test_ids() -> tuple[str, ...]:
    return (*SMOKE_TEST_IDS, *CONTRACT_TEST_IDS, *REQUIRED_FAULT_TEST_IDS)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--tests-dir", required=True)
    return parser.parse_args()


def _emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True))


def main() -> int:
    try:
        args = _parse_args()
        tests_dir = Path(args.tests_dir).resolve(strict=True)
        if not tests_dir.is_dir():
            raise RuntimeError("invalid_tests_directory")

        smoke = tuple(SMOKE_TEST_IDS)
        contract = tuple(CONTRACT_TEST_IDS)
        fault = tuple(REQUIRED_FAULT_TEST_IDS)
        selected = lane_test_ids()
        if (
            len(smoke) != 12
            or len(contract) != 25
            or len(fault) != 19
            or len(selected) != 56
            or len(set(selected)) != 56
        ):
            raise RuntimeError("fast_lane_registry_invalid")

        test_stdout = io.StringIO()
        test_stderr = io.StringIO()
        with (
            contextlib.redirect_stdout(test_stdout),
            contextlib.redirect_stderr(test_stderr),
        ):
            sys.path.insert(0, str(tests_dir))
            loader = unittest.TestLoader()
            suite = loader.loadTestsFromNames(list(selected))
            loaded_ids = tuple(test.id() for test in _iter_tests(suite))
            if loader.errors or loaded_ids != selected:
                raise RuntimeError("fast_lane_registry_not_loadable")

            stream = io.StringIO()
            result = unittest.TextTestRunner(
                stream=stream,
                verbosity=0,
                resultclass=RecordingResult,
            ).run(suite)

        unexpected_test_output = bool(
            test_stdout.getvalue().strip() or test_stderr.getvalue().strip()
        )

        failures = [test.id() for test, _trace in result.failures]
        errors = [test.id() for test, _trace in result.errors]
        skipped = [test.id() for test, _reason in result.skipped]
        passed = (
            result.wasSuccessful()
            and result.testsRun == 56
            and len(result.successful_test_ids) == 56
            and not skipped
            and not unexpected_test_output
        )
        _emit(
            {
                "schema_version": SCHEMA_VERSION,
                "lane": LANE_NAME,
                "status": "pass" if passed else "fail",
                "tests_run": result.testsRun,
                "smoke_count": len(smoke),
                "contract_count": len(contract),
                "fault_count": len(fault),
                "failure_test_ids": failures,
                "error_test_ids": errors,
                "skipped_test_ids": skipped,
                "unexpected_test_output": unexpected_test_output,
                "release_evidence": False,
            }
        )
        return 0 if passed else 1
    except Exception:
        _emit(
            {
                "schema_version": SCHEMA_VERSION,
                "lane": LANE_NAME,
                "status": "error",
                "error_code": "fast_lane_contract_error",
                "release_evidence": False,
            }
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
