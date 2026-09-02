from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import unittest


SCHEMA_VERSION = 1
REQUIRED_FAULT_TEST_IDS = (
    "test_beeper_queue.BeeperQueueTests."
    "test_namespace_and_registration_are_closed_and_immutable",
    "test_beeper_queue.BeeperQueueTests."
    "test_beeper_and_tombstones_cannot_be_business_responders",
    "test_beeper_client.BeeperClientContractTests."
    "test_argv_contains_only_fixed_control_and_opaque_page",
    "test_beeper_client.BeeperClientContractTests."
    "test_same_request_never_spawns_twice",
    "test_beeper_client.BeeperClientContractTests."
    "test_reserved_beeper_loads_exact_uri_once_without_requeue",
    "test_beeper_client.BeeperClientContractTests."
    "test_load_assist_failure_is_safe_and_terminal",
    "test_beeper_client.BeeperClientContractTests."
    "test_readonly_unknown_is_safe_terminal_and_not_retried",
    "test_beeper_queue.BeeperQueueTests."
    "test_readonly_claim_expiry_is_terminal_and_not_replayed",
    "test_beeper_queue.BeeperQueueTests."
    "test_unclaimed_failure_cas_and_claim_are_exclusive",
    "test_beeper_queue.BeeperQueueTests."
    "test_finish_waits_for_delayed_beeper_claim",
    "test_beeper_queue.BeeperQueueTests."
    "test_final_callback_finish_is_exactly_once",
    "test_beeper_client.BeeperClientContractTests."
    "test_completed_send_requires_top_level_final_callback_source",
    "test_beeper_queue.BeeperQueueTests."
    "test_final_callback_conflict_fails_closed_and_scrubs_capability",
    "test_beeper_queue.BeeperQueueTests."
    "test_catalog_tamper_is_rejected_and_scrubbed",
    "test_beeper_queue.BeeperQueueTests."
    "test_catalog_interrupted_consume_is_not_replayed_and_ages_out",
    "test_beeper_client.BeeperClientContractTests."
    "test_final_callback_timeout_is_terminal_and_not_retried",
    "test_runtime.StableConversationScopeTests."
    "test_binding_commit_control_crash_is_terminal_after_reopen",
    "test_beeper_queue.BeeperQueueTests."
    "test_catalog_is_staged_answer_free_then_consumed_once",
    "test_agents_rules.BridgeEnvEntrypointTests."
    "test_malformed_bridge_env_fails_closed_at_dispatcher_start_and_queue_entrypoints",
)


def _iter_tests(suite: unittest.TestSuite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _iter_tests(item)
        else:
            yield item


class RecordingResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.successful_test_ids: list[str] = []

    def addSuccess(self, test):  # noqa: N802 - unittest API name
        super().addSuccess(test)
        self.successful_test_ids.append(test.id())


def _write_create_new_json(path: Path, payload: dict[str, object]) -> None:
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    descriptor = os.open(path, flags, 0o600)
    try:
        offset = 0
        while offset < len(encoded):
            written = os.write(descriptor, encoded[offset:])
            if written <= 0:
                raise OSError("structured P0-B result write made no progress")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--tests-dir", required=True)
    parser.add_argument("--result-path", required=True)
    parser.add_argument("--nonce", required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    tests_dir = Path(args.tests_dir).resolve(strict=True)
    result_path = Path(args.result_path).resolve(strict=False)
    if not tests_dir.is_dir():
        raise RuntimeError("P0-B tests directory is not a directory")
    if result_path.exists() or result_path.is_symlink():
        raise RuntimeError("P0-B structured result path already exists")
    if not result_path.parent.is_dir():
        raise RuntimeError("P0-B structured result parent does not exist")
    if len(args.nonce) != 36:
        raise RuntimeError("P0-B structured result nonce has the wrong shape")

    loader = unittest.TestLoader()
    suite = loader.discover(str(tests_dir), pattern="test_*.py")
    discovered_ids = [test.id() for test in _iter_tests(suite)]
    if loader.errors:
        raise RuntimeError("P0-B unittest discovery reported loader errors")
    if len(discovered_ids) != len(set(discovered_ids)):
        raise RuntimeError("P0-B unittest discovery produced duplicate test IDs")

    result = unittest.TextTestRunner(
        stream=sys.stderr,
        verbosity=2,
        resultclass=RecordingResult,
    ).run(suite)
    successful_ids = sorted(result.successful_test_ids)
    successful_set = set(successful_ids)
    required_ids = list(REQUIRED_FAULT_TEST_IDS)
    missing_required_ids = sorted(set(required_ids) - successful_set)
    failed_ids = sorted(test.id() for test, _ in result.failures)
    error_ids = sorted(test.id() for test, _ in result.errors)
    skipped_ids = sorted(test.id() for test, _ in result.skipped)
    passed = (
        result.wasSuccessful()
        and result.testsRun == len(discovered_ids)
        and not missing_required_ids
        and not skipped_ids
        and len(required_ids) == 19
    )
    payload: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "nonce": args.nonce,
        "runner_status": "pass" if passed else "fail",
        "tests_discovered": len(discovered_ids),
        "tests_run": result.testsRun,
        "successful_test_ids": successful_ids,
        "required_fault_test_ids": required_ids,
        "missing_required_test_ids": missing_required_ids,
        "failure_test_ids": failed_ids,
        "error_test_ids": error_ids,
        "skipped_test_ids": skipped_ids,
    }
    _write_create_new_json(result_path, payload)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
