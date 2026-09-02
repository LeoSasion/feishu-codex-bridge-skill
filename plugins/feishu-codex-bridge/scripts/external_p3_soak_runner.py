from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback
import unittest
import uuid


SCHEMA_VERSION = 2
MIN_ITERATIONS = 25
MAX_ITERATIONS = 100
SCENARIO_CONTRACT = (
    {
        "scenario_id": "grant_claim_race",
        "test_id": (
            "test_beeper_queue.BeeperQueueTests."
            "test_unclaimed_failure_cas_and_claim_are_exclusive"
        ),
    },
    {
        "scenario_id": "callback_duplicate_convergence",
        "test_id": (
            "test_beeper_queue.BeeperQueueTests."
            "test_final_callback_finish_is_exactly_once"
        ),
    },
    {
        "scenario_id": "callback_conflict_convergence",
        "test_id": (
            "test_beeper_queue.BeeperQueueTests."
            "test_final_callback_conflict_fails_closed_and_scrubs_capability"
        ),
    },
    {
        "scenario_id": "terminal_release_race",
        "test_id": (
            "test_beeper_queue.BeeperQueueTests."
            "test_finish_rechecks_terminal_after_release_race"
        ),
    },
    {
        "scenario_id": "delayed_claim_window",
        "test_id": (
            "test_beeper_queue.BeeperQueueTests."
            "test_finish_waits_for_delayed_beeper_claim"
        ),
    },
    {
        "scenario_id": "unclaimed_restart_recovery",
        "test_id": (
            "test_beeper_queue.BeeperQueueTests."
            "test_unclaimed_crash_state_reconciles_on_restart"
        ),
    },
    {
        "scenario_id": "pre_start_restart_requeue",
        "test_id": (
            "test_state.DurableStateTests."
            "test_restart_requeues_work_that_never_started_model"
        ),
    },
    {
        "scenario_id": "post_start_restart_no_replay",
        "test_id": (
            "test_state.DurableStateTests."
            "test_restart_does_not_rerun_a_started_model_turn"
        ),
    },
    {
        "scenario_id": "retryable_delivery_disposition",
        "test_id": (
            "test_routing.RoutingTests."
            "test_rate_limit_and_network_failures_remain_retryable"
        ),
    },
    {
        "scenario_id": "terminal_delivery_disposition",
        "test_id": (
            "test_runtime.ReplyDeliveryTests."
            "test_terminal_reply_result_is_not_rescheduled"
        ),
    },
)


class RunnerContractError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class RecordingResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.successful_test_ids: list[str] = []

    def addSuccess(self, test):  # noqa: N802 - unittest API name
        super().addSuccess(test)
        self.successful_test_ids.append(test.id())


def _iter_tests(suite: unittest.TestSuite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _iter_tests(item)
        else:
            yield item


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
                raise OSError("structured P3 soak result write made no progress")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--tests-dir", required=True)
    parser.add_argument("--result-path", required=True)
    parser.add_argument("--test-temp", required=True)
    parser.add_argument("--nonce", required=True)
    parser.add_argument("--iterations", required=True, type=int)
    parser.add_argument("--hard-timeout-seconds", required=True, type=int)
    return parser.parse_args()


def _install_child_process_guard(counter: dict[str, int]) -> None:
    def forbidden(*_args, **_kwargs):
        counter["attempts"] += 1
        raise RuntimeError("P3 soak scenario attempted to start a child process")

    original_popen = subprocess.Popen

    class ForbiddenPopen(original_popen):
        def __init__(self, *_args, **_kwargs):
            forbidden()

    subprocess.Popen = ForbiddenPopen  # type: ignore[assignment]
    os.system = forbidden  # type: ignore[assignment]
    if hasattr(os, "startfile"):
        os.startfile = forbidden  # type: ignore[attr-defined,assignment]
    for name in (
        "spawnl",
        "spawnle",
        "spawnlp",
        "spawnlpe",
        "spawnv",
        "spawnve",
        "spawnvp",
        "spawnvpe",
    ):
        if hasattr(os, name):
            setattr(os, name, forbidden)


def main() -> int:
    args = _parse_args()
    started = time.monotonic()
    tests_dir = Path(args.tests_dir).resolve(strict=True)
    result_path = Path(args.result_path).resolve(strict=False)
    test_temp = Path(args.test_temp).resolve(strict=True)
    snapshot_root = tests_dir.parent
    if not tests_dir.is_dir() or not test_temp.is_dir():
        raise RunnerContractError("invalid_directory")
    if result_path.exists() or result_path.is_symlink() or not result_path.parent.is_dir():
        raise RunnerContractError("result_path_not_create_new")
    if result_path.is_relative_to(snapshot_root) or test_temp.is_relative_to(snapshot_root):
        raise RunnerContractError("runtime_artifact_inside_snapshot")
    try:
        nonce = str(uuid.UUID(args.nonce))
    except ValueError as exc:
        raise RunnerContractError("invalid_nonce") from exc
    if nonce != args.nonce.lower():
        raise RunnerContractError("invalid_nonce")
    if args.iterations < MIN_ITERATIONS or args.iterations > MAX_ITERATIONS:
        raise RunnerContractError("iterations_out_of_range")
    if args.hard_timeout_seconds < 30 or args.hard_timeout_seconds > 900:
        raise RunnerContractError("timeout_out_of_range")

    os.environ["FEISHU_BRIDGE_TEST_TMP"] = str(test_temp)
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.path.insert(0, str(tests_dir))
    child_process_counter = {"attempts": 0}
    _install_child_process_guard(child_process_counter)

    scenario_ids = [entry["scenario_id"] for entry in SCENARIO_CONTRACT]
    scenario_test_ids = [entry["test_id"] for entry in SCENARIO_CONTRACT]
    reverse_scenarios = dict(zip(scenario_test_ids, scenario_ids, strict=True))
    pass_counts = {scenario_id: 0 for scenario_id in scenario_ids}
    failure_test_ids: set[str] = set()
    error_test_ids: set[str] = set()
    skipped_test_ids: set[str] = set()
    iteration_results: list[dict[str, object]] = []
    iterations_completed = 0
    total_tests_run = 0
    runner_error_code: str | None = None

    try:
        for iteration in range(1, args.iterations + 1):
            iteration_started = time.monotonic()
            loader = unittest.TestLoader()
            suite = loader.loadTestsFromNames(scenario_test_ids)
            loaded_ids = [test.id() for test in _iter_tests(suite)]
            if loader.errors or loaded_ids != scenario_test_ids:
                raise RunnerContractError("scenario_contract_not_loadable")
            result = unittest.TextTestRunner(
                stream=sys.stderr,
                verbosity=0,
                resultclass=RecordingResult,
            ).run(suite)
            duration = time.monotonic() - iteration_started
            total_tests_run += result.testsRun
            for test_id in result.successful_test_ids:
                pass_counts[reverse_scenarios[test_id]] += 1
            iteration_failures = sorted(test.id() for test, _ in result.failures)
            iteration_errors = sorted(test.id() for test, _ in result.errors)
            iteration_skips = sorted(test.id() for test, _ in result.skipped)
            failure_test_ids.update(iteration_failures)
            error_test_ids.update(iteration_errors)
            skipped_test_ids.update(iteration_skips)
            iteration_passed = (
                result.wasSuccessful()
                and result.testsRun == len(SCENARIO_CONTRACT)
                and len(result.successful_test_ids) == len(SCENARIO_CONTRACT)
            )
            iteration_results.append(
                {
                    "iteration": iteration,
                    "status": "pass" if iteration_passed else "fail",
                    "tests_run": result.testsRun,
                    "duration_seconds": round(duration, 6),
                }
            )
            if not iteration_passed:
                runner_error_code = "scenario_failure"
                break
            iterations_completed = iteration
            print(
                f"P3 soak iteration {iteration}/{args.iterations} pass "
                f"({duration:.3f}s)",
                file=sys.stderr,
                flush=True,
            )
    except RunnerContractError as exc:
        runner_error_code = exc.code
        traceback.print_exc(file=sys.stderr)
    except Exception:
        runner_error_code = "runner_exception"
        traceback.print_exc(file=sys.stderr)

    duration_seconds = time.monotonic() - started
    expected_total = args.iterations * len(SCENARIO_CONTRACT)
    passed = (
        runner_error_code is None
        and iterations_completed == args.iterations
        and total_tests_run == expected_total
        and child_process_counter["attempts"] == 0
        and not failure_test_ids
        and not error_test_ids
        and not skipped_test_ids
        and all(count == args.iterations for count in pass_counts.values())
        and duration_seconds < args.hard_timeout_seconds
    )
    if not passed and runner_error_code is None:
        runner_error_code = "semantic_mismatch"
    payload: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "nonce": nonce,
        "runner_status": "pass" if passed else "fail",
        "runner_error_code": runner_error_code,
        "iterations_requested": args.iterations,
        "iterations_completed": iterations_completed,
        "scenario_count": len(SCENARIO_CONTRACT),
        "total_tests_run": total_tests_run,
        "expected_total_tests": expected_total,
        "scenario_contract": list(SCENARIO_CONTRACT),
        "scenario_pass_counts": pass_counts,
        "failure_test_ids": sorted(failure_test_ids),
        "error_test_ids": sorted(error_test_ids),
        "skipped_test_ids": sorted(skipped_test_ids),
        "iteration_results": iteration_results,
        "duration_seconds": round(duration_seconds, 6),
        "max_iteration_duration_seconds": round(
            max((float(item["duration_seconds"]) for item in iteration_results), default=0.0),
            6,
        ),
        "hard_timeout_seconds": args.hard_timeout_seconds,
        "min_iterations": MIN_ITERATIONS,
        "max_iterations": MAX_ITERATIONS,
        "child_process_policy": "forbidden",
        "child_process_attempts": child_process_counter["attempts"],
        "live_desktop_contacted": False,
        "live_feishu_contacted": False,
    }
    _write_create_new_json(result_path, payload)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
