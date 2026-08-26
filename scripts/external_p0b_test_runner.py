from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import unittest


SCHEMA_VERSION = 1
REQUIRED_FAULT_TEST_IDS = (
    "test_desktop_router.DesktopRouterQueueTests."
    "test_exclusive_claim_publication_keeps_canonical_pending",
    "test_desktop_router.DesktopRouterQueueTests."
    "test_identical_producer_overlap_cannot_republish_claimed_request",
    "test_desktop_router.DesktopRouterQueueTests."
    "test_legacy_unfenced_claim_is_terminalized_as_uncertain",
    "test_desktop_router.DesktopRouterQueueTests."
    "test_receipt_payload_without_marker_is_authoritative_and_not_replayed",
    "test_desktop_router.DesktopRouterQueueTests."
    "test_receipt_payload_survives_marker_descriptor_close_failure",
    "test_desktop_router.DesktopRouterQueueTests."
    "test_orphan_terminal_receipt_recovers_as_unknown_and_survives_cleanup",
    "test_desktop_router.DesktopRouterQueueTests."
    "test_concurrent_terminal_finalizers_preserve_first_receipt",
    "test_desktop_router.DesktopRouterQueueTests."
    "test_wake_database_lock_preserves_pending_and_reconciles_once",
    "test_desktop_router.DesktopRouterQueueTests."
    "test_concurrent_conflicting_producers_publish_one_fingerprint",
    "test_desktop_router.DesktopRouterQueueTests."
    "test_explicit_safe_failure_advances_one_retry_generation",
    "test_desktop_router.DesktopRouterQueueTests."
    "test_retry_generation_ancestry_survives_response_cleanup",
    "test_desktop_router.DesktopRouterQueueTests."
    "test_stale_read_only_claim_advances_retry_generation",
    "test_desktop_router.DesktopRouterQueueTests."
    "test_mutating_claim_keeps_long_ttl_when_read_claim_would_expire",
    "test_desktop_router.DesktopRouterQueueTests."
    "test_target_lifecycle_failure_never_advances_retry_generation",
    "test_desktop_router.DesktopRouterQueueTests."
    "test_retry_generation_requires_explicit_json_booleans",
    "test_runtime.PendingProjectMarkerTests."
    "test_fresh_project_marker_precedes_unknown_create_and_same_event_recovers",
    "test_runtime.PendingProjectMarkerTests."
    "test_same_event_resumes_exact_pending_project_marker",
    "test_runtime.PendingProjectMarkerTests."
    "test_different_event_cannot_overwrite_a_pending_project_marker",
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
