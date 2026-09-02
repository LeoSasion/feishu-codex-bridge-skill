from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SKILL_ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP_ROOT = Path(os.environ.get("FEISHU_BRIDGE_TEST_TMP", tempfile.gettempdir()))
if "FEISHU_BRIDGE_TEST_TMP" not in os.environ:
    TEST_TEMP_ROOT = TEST_TEMP_ROOT / "feishu-codex-bridge-tests"
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(SKILL_ROOT / "scripts"))
sys.path.insert(0, str(SKILL_ROOT / "tests"))

import external_p3_soak_runner as driver  # noqa: E402


class ExternalP3SoakDriverTests(unittest.TestCase):
    def test_scenario_contract_has_ten_unique_loadable_tests(self) -> None:
        self.assertEqual(2, driver.SCHEMA_VERSION)
        scenarios = list(driver.SCENARIO_CONTRACT)
        scenario_ids = [entry["scenario_id"] for entry in scenarios]
        test_ids = [entry["test_id"] for entry in scenarios]
        self.assertEqual(10, len(scenarios))
        self.assertEqual(10, len(set(scenario_ids)))
        self.assertEqual(10, len(set(test_ids)))
        self.assertEqual(
            [
                "grant_claim_race",
                "callback_duplicate_convergence",
                "callback_conflict_convergence",
                "terminal_release_race",
                "delayed_claim_window",
                "unclaimed_restart_recovery",
                "pre_start_restart_requeue",
                "post_start_restart_no_replay",
                "retryable_delivery_disposition",
                "terminal_delivery_disposition",
            ],
            scenario_ids,
        )
        self.assertEqual(25, driver.MIN_ITERATIONS)
        self.assertEqual(100, driver.MAX_ITERATIONS)
        self.assertEqual(
            [
                "test_beeper_queue.BeeperQueueTests."
                "test_unclaimed_failure_cas_and_claim_are_exclusive",
                "test_beeper_queue.BeeperQueueTests."
                "test_final_callback_finish_is_exactly_once",
                "test_beeper_queue.BeeperQueueTests."
                "test_final_callback_conflict_fails_closed_and_scrubs_capability",
                "test_beeper_queue.BeeperQueueTests."
                "test_finish_rechecks_terminal_after_release_race",
                "test_beeper_queue.BeeperQueueTests."
                "test_finish_waits_for_delayed_beeper_claim",
                "test_beeper_queue.BeeperQueueTests."
                "test_unclaimed_crash_state_reconciles_on_restart",
            ],
            test_ids[:6],
        )

        loader = unittest.TestLoader()
        suite = loader.loadTestsFromNames(test_ids)
        loaded_ids: list[str] = []

        def collect(item: unittest.TestSuite | unittest.TestCase) -> None:
            if isinstance(item, unittest.TestSuite):
                for child in item:
                    collect(child)
            else:
                loaded_ids.append(item.id())

        collect(suite)
        self.assertEqual([], loader.errors)
        self.assertEqual(test_ids, loaded_ids)

    def test_structured_result_writer_is_create_new(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            destination = Path(temporary) / "result.json"
            payload = {"schema_version": driver.SCHEMA_VERSION, "runner_status": "pass"}
            driver._write_create_new_json(destination, payload)
            self.assertEqual(payload, json.loads(destination.read_text(encoding="utf-8")))
            with self.assertRaises(FileExistsError):
                driver._write_create_new_json(destination, payload)

    def test_supervisor_and_validator_pin_external_bounded_contract(self) -> None:
        supervisor = (SKILL_ROOT / "scripts" / "run-external-p3-soak.ps1").read_text(
            encoding="utf-8"
        )
        validator = (
            SKILL_ROOT / "scripts" / "validate-external-p3-soak-evidence.ps1"
        ).read_text(encoding="utf-8")
        schema = json.loads(
            (
                SKILL_ROOT / "assets" / "external-p3-soak-evidence.schema.json"
            ).read_text(encoding="utf-8")
        )
        wrapper = (
            SKILL_ROOT / "scripts" / "invoke-external-p3-soak-once.ps1"
        ).read_text(encoding="utf-8")

        self.assertEqual(2, schema["properties"]["schema_version"]["const"])
        self.assertEqual(
            r"^p0b-v2-[a-f0-9-]{36}\.json$",
            schema["properties"]["p0_evidence"]["properties"]["file"]["pattern"],
        )
        self.assertIn("$envelope.schema_version -ne 2", wrapper)
        self.assertNotIn("$envelope.schema_version -ne 1", wrapper)
        self.assertIn("$p0Receipt.schema_version -ne 2", supervisor)
        self.assertNotIn("$p0Receipt.schema_version -ne 1", supervisor)
        self.assertIn("$p0Receipt.schema_version -ne 2", validator)
        self.assertIn("$p0Validation.validation_schema_version -ne 2", validator)
        self.assertIn(
            "P0 validator child timed out and its process tree did not exit within 30 seconds.",
            validator,
        )
        self.assertIn(
            "P0 validator child timed out and its output pipes did not close within 30 seconds.",
            validator,
        )
        self.assertIn(
            "P0 validator cleanup could not confirm process-tree exit within 30 seconds.",
            validator,
        )
        self.assertIn(
            "P0 validator cleanup could not drain output pipes within 30 seconds.",
            validator,
        )
        self.assertIn(
            "Current P0 validator wrote unexpected stderr despite a zero exit code.",
            validator,
        )

        for marker in (
            "FEISHU_BRIDGE_EXTERNAL_P3_SOAK",
            "ExternalP3SoakJob",
            "Get-ExternalRunnerProcessGuard",
            "codex_ancestor_match_count",
            "runner_surface",
            "snapshot_files_pinned",
            "source-snapshot",
            "p0-validation-pre",
            "Get-LifecycleMutexName",
            "Get-BridgeObservation",
            "answer_free_idle_status_v1",
            "health_snapshot_present",
            "beeper-status-{0}",
            "beeper_status_stdout_sha256",
            "beeper",
            "-DeferCapture",
            "bridge_stopped_receipt",
            "held_for_complete_window",
            "p0_evidence_rehashed_after_run",
            "live_desktop_contacted",
            "live_feishu_contacted",
        ):
            self.assertIn(marker, supervisor)
        for script in (supervisor, validator):
            self.assertIn("'control_sending'", script)
        for entry in driver.SCENARIO_CONTRACT:
            self.assertIn(entry["scenario_id"], validator)
            self.assertIn(entry["test_id"], validator)
        self.assertIn(
            "$workDirectoryLeaf = 's-' + (Get-StringSha256 -Value $receiptId).Substring(0, 8)",
            supervisor,
        )
        self.assertIn(
            "Get-StringSha256 -Value ([string]$receipt.receipt_id)",
            validator,
        )
        self.assertIn("$expectedWorkDirectoryLeaf", validator)
        self.assertNotIn("p3-soak-work-", supervisor)
        self.assertNotIn("p3-soak-work-", validator)
        self.assertIn(
            "Test-IsWithinRoot -Candidate $runtimePath -Root $externalWork",
            supervisor,
        )
        self.assertIn("$testTemp = Join-Path $workDirectory 'test-temp'", supervisor)
        self.assertEqual(
            "external_p3_bounded_soak",
            schema["properties"]["evidence_kind"]["const"],
        )
        execution_schema = schema["properties"]["execution"]["properties"]
        self.assertEqual(10, execution_schema["scenario_count"]["const"])
        self.assertEqual(25, execution_schema["iterations"]["minimum"])
        self.assertEqual(100, execution_schema["iterations"]["maximum"])
        self.assertEqual(250, execution_schema["total_tests_run"]["minimum"])
        self.assertEqual(1000, execution_schema["total_tests_run"]["maximum"])
        self.assertIn("bridge_stopped_receipt", schema["required"])
        bridge_schema = schema["$defs"]["bridgeObservation"]
        bridge_properties = bridge_schema["properties"]
        for required_field in (
            "status_argv",
            "health_snapshot_present",
            "health_snapshot_valid",
            "health_status",
            "health_event_consumer",
            "health_active_turns",
            "health_dial_inflight",
            "health_dial_lease_remaining_seconds",
            "health_queue_counts",
            "beeper_status_argv",
            "beeper_status_stdout_sha256",
            "beeper_status_stderr_sha256",
            "beeper_queue_cli_namespace",
            "beeper_pending",
            "beeper_claimed",
            "beeper_dial_inflight",
            "beeper_dial_lease_remaining_seconds",
        ):
            self.assertIn(required_field, bridge_schema["required"])
        self.assertTrue(bridge_properties["health_snapshot_present"]["const"])
        self.assertTrue(bridge_properties["health_snapshot_valid"]["const"])
        self.assertEqual("stopped", bridge_properties["health_status"]["const"])
        self.assertFalse(bridge_properties["health_event_consumer"]["const"])
        self.assertEqual(0, bridge_properties["health_active_turns"]["const"])
        self.assertFalse(bridge_properties["health_dial_inflight"]["const"])
        self.assertIsNone(
            bridge_properties["health_dial_lease_remaining_seconds"]["const"]
        )
        self.assertEqual(
            {"queued", "running", "control_sending", "reply_pending"},
            set(bridge_properties["health_queue_counts"]["required"]),
        )
        self.assertEqual(
            0,
            bridge_properties["health_queue_counts"]["properties"][
                "control_sending"
            ]["const"],
        )
        self.assertEqual(
            "beeper",
            bridge_properties["beeper_queue_cli_namespace"]["const"],
        )
        self.assertEqual(9, bridge_properties["beeper_status_argv"]["minItems"])
        self.assertEqual(9, bridge_properties["beeper_status_argv"]["maxItems"])
        self.assertNotIn("'-I', '-S', '-B', $BeeperHelper", supervisor)
        self.assertNotIn("'-I', '-S', '-B', $ExpectedBeeperHelper", validator)
        self.assertEqual(0, bridge_properties["beeper_pending"]["const"])
        self.assertEqual(0, bridge_properties["beeper_claimed"]["const"])
        self.assertFalse(bridge_properties["beeper_dial_inflight"]["const"])
        self.assertIsNone(
            bridge_properties["beeper_dial_lease_remaining_seconds"]["const"]
        )
        for marker in (
            "beeper-status-{0}.stdout.txt",
            "beeper-status-{0}.stderr.txt",
            "ExpectedBeeperHelper",
            "ExpectedRuntime",
            "answer_free_idle_status_v1",
        ):
            self.assertIn(marker, validator)
        for script in (supervisor, validator):
            self.assertIn("function Test-AdmissibleStoppedStatus", script)
            self.assertIn("function Test-MvpObservation", script)
            self.assertIn("$healthVersionRelationIsAdmissible", script)
            self.assertIn("[string]$health.bridge_version -ceq", script)
            self.assertIn(
                "[string]$status.installed_manifest.bridge_version",
                script,
            )
            self.assertIn("[string]$status.status -ceq 'warning'", script)
            self.assertIn(
                "Test-JsonBooleanValue -Value $health.process_identity_current -Expected $false",
                script,
            )
            self.assertIn("integrity_check_failed", script)
            self.assertIn("$issueCountIsInteger", script)
            self.assertIn(
                "$manifest.issue_codes -is [System.Array]", script
            )
            self.assertIn(
                "@('invalid', 'stale_process_absent', 'stale_foreign_process')",
                script,
            )
            self.assertIn("-not $statusPidStateMatches", script)
            self.assertIn(
                "Test-JsonBooleanValue -Value $status.runtime.pid_file_present",
                script,
            )
            for marker in (
                "schema_current",
                "process_identity_current",
                "runtime_manifest_current",
                "snapshot_fresh",
                "beeper_pending",
                "beeper_claimed",
                "actionable_retryable_failed",
                "mvp_observation",
                "single_inbox_claim_observed",
            ):
                self.assertIn(marker, script)
        self.assertNotIn(
            "[string]$status.runtime.pid_file_state -cne $pidFileState",
            supervisor,
        )
        self.assertNotIn(
            "[string]$status.runtime.pid_file_state -cne [string]$Expected.pid_file_state",
            validator,
        )
        self.assertIn("$expectedPidFileState -cnotin @('absent', 'stale')", validator)
        self.assertTrue(
            schema["properties"]["guards"]["properties"]
            ["p0_evidence_rehashed_after_run"]["const"]
        )
        lifecycle_schema = schema["properties"]["bridge_stopped_receipt"][
            "properties"
        ]["lifecycle_mutex"]["properties"]
        self.assertTrue(lifecycle_schema["held_for_complete_window"]["const"])
        self.assertEqual(
            "registered_start_and_stop_hooks",
            lifecycle_schema["lifecycle_exclusion_scope"]["const"],
        )
        self.assertFalse(
            execution_schema["live_desktop_contacted"]["const"]
        )
        self.assertFalse(
            execution_schema["live_feishu_contacted"]["const"]
        )
        self.assertIn("[ValidateRange(25, 100)][int]$Iterations", supervisor)
        self.assertIn("[ValidateRange(25, 100)][int]$Iterations", wrapper)
        self.assertIn("[int]$StageTimeoutSeconds", wrapper)
        self.assertIn("$process.Kill($true)", wrapper)
        self.assertIn("$process.WaitForExit(30000)", wrapper)
        self.assertIn("[System.Threading.Tasks.Task]::WaitAll(", wrapper)
        self.assertIn("$process.Dispose()", wrapper)
        self.assertNotIn("& $pwsh -NoLogo", wrapper)
        self.assertIn(
            "$supervisorStageTimeout = [Math]::Min(1800, $TimeoutSeconds + 360)",
            wrapper,
        )
        self.assertIn("$validatorStageTimeout = 420", wrapper)
        self.assertIn("-StageTimeoutSeconds $supervisorStageTimeout", wrapper)
        self.assertIn("-StageTimeoutSeconds $validatorStageTimeout", wrapper)
        self.assertNotIn("$nonempty | Write-Output", wrapper)
        self.assertIn("-NotePropertyName evidence_path", wrapper)
        self.assertIn("[string]$ArtifactRoot", wrapper)
        self.assertIn(
            "Resolve-ArtifactRoot -Path $ArtifactRoot -ProtectedRoots",
            wrapper,
        )
        self.assertIn('$runRoot = Join-Path $artifact "p3-soak-$runTag"', wrapper)
        self.assertIn("$workRoot = Join-Path $runRoot 'work'", wrapper)
        self.assertIn("$evidenceRoot = Join-Path $runRoot 'evidence'", wrapper)
        self.assertIn("Unique P3 artifact run destination already exists", wrapper)
        self.assertNotIn("GetPathRoot($desktop)", wrapper)

    def test_one_shot_p3_wrappers_pin_physical_artifact_boundary_before_creation(self) -> None:
        for script_name in (
            "invoke-external-p3-soak-once.ps1",
            "invoke-external-p0b-p3-once.ps1",
        ):
            with self.subTest(script=script_name):
                wrapper = (SKILL_ROOT / "scripts" / script_name).read_text(
                    encoding="utf-8"
                )
                for marker in (
                    "GetFinalPathNameByHandleW",
                    "QueryDosDeviceW",
                    "ResolveExistingDevicePath",
                    "Get-PhysicalComparisonPath",
                    "-RejectLexicalAlias",
                    "Assert-PhysicalIsolation -ArtifactRecord $artifactPre",
                    "ArtifactRoot changed physical identity while it was being prepared.",
                ):
                    self.assertIn(marker, wrapper)
                self.assertLess(
                    wrapper.index("$artifactPre = Get-PhysicalComparisonPath"),
                    wrapper.index("New-Item -ItemType Directory -Path $fullPath"),
                )
                harness_parameter = wrapper.index("[string]$HarnessRoot")
                self.assertIn(
                    "[Parameter(Mandatory = $true)]",
                    wrapper[max(0, harness_parameter - 180) : harness_parameter],
                )
                self.assertIn(
                    "HarnessRoot must be an explicit ordinary fully qualified filesystem path.",
                    wrapper,
                )
                self.assertNotIn("$HarnessRoot = ''", wrapper)
                self.assertNotIn("feishu-codex-harness-bridge", wrapper)

    def test_supervisor_and_validator_accept_file_and_directory_path_chains(self) -> None:
        pwsh = shutil.which("pwsh.exe") or shutil.which("pwsh")
        self.assertIsNotNone(pwsh, "external P3 requires PowerShell 7.4+")
        probe = r"""
$ErrorActionPreference = 'Stop'
$sourceRoot = [System.Environment]::GetEnvironmentVariable('P3_PATH_PROBE_ROOT')
$scriptPath = [System.Environment]::GetEnvironmentVariable('P3_PATH_PROBE_SCRIPT')
if ([string]::IsNullOrWhiteSpace($sourceRoot) -or
    [string]::IsNullOrWhiteSpace($scriptPath)) {
    throw 'P3 path probe environment is incomplete.'
}
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $scriptPath,
    [ref]$tokens,
    [ref]$errors
)
if ($errors.Count) { throw ($errors -join '; ') }
$definitions = @(
    $ast.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -eq 'Assert-NoReparsePathChain'
    }, $true)
)
if ($definitions.Count -ne 1) { throw 'P3 path helper extraction was incomplete.' }
. ([scriptblock]::Create($definitions[0].Extent.Text))
Assert-NoReparsePathChain -Path $sourceRoot
Assert-NoReparsePathChain -Path (Join-Path $sourceRoot 'skills\feishu-codex-bridge\SKILL.md')
"""
        for script_name in (
            "run-external-p3-soak.ps1",
            "validate-external-p3-soak-evidence.ps1",
        ):
            with self.subTest(script=script_name):
                probe_environment = os.environ.copy()
                probe_environment["P3_PATH_PROBE_ROOT"] = str(SKILL_ROOT)
                probe_environment["P3_PATH_PROBE_SCRIPT"] = str(
                    SKILL_ROOT / "scripts" / script_name
                )
                completed = subprocess.run(
                    [
                        str(pwsh),
                        "-NoLogo",
                        "-NoProfile",
                        "-NonInteractive",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-Command",
                        probe,
                    ],
                    capture_output=True,
                    check=False,
                    encoding="utf-8",
                    env=probe_environment,
                    errors="replace",
                    timeout=30,
                )
                self.assertEqual(
                    completed.returncode,
                    0,
                    completed.stdout + "\n" + completed.stderr,
                )

    def test_child_process_guard_preserves_popen_type_and_blocks_construction(self) -> None:
        probe = r"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.environ["P3_GUARD_PROBE_SCRIPTS"])
import external_p3_soak_runner as runner

counter = {"attempts": 0}
runner._install_child_process_guard(counter)
if not isinstance(subprocess.Popen, type):
    raise RuntimeError("guarded subprocess.Popen is not a class")
import asyncio
try:
    subprocess.Popen([sys.executable, "-c", "pass"])
except RuntimeError as exc:
    if str(exc) != "P3 soak scenario attempted to start a child process":
        raise
else:
    raise RuntimeError("guarded subprocess.Popen allowed construction")
if counter != {"attempts": 1}:
    raise RuntimeError(f"unexpected child-process attempt count: {counter!r}")
print(json.dumps({"asyncio_imported": True, "popen_is_type": True, **counter}))
"""
        probe_environment = os.environ.copy()
        probe_environment["P3_GUARD_PROBE_SCRIPTS"] = str(SKILL_ROOT / "scripts")
        completed = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True,
            check=False,
            encoding="utf-8",
            env=probe_environment,
            errors="replace",
            timeout=30,
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + "\n" + completed.stderr,
        )
        self.assertEqual(
            {"asyncio_imported": True, "popen_is_type": True, "attempts": 1},
            json.loads(completed.stdout),
        )

    def test_validator_accepts_empty_pin_collection_for_its_first_handle(self) -> None:
        pwsh = shutil.which("pwsh.exe") or shutil.which("pwsh")
        self.assertIsNotNone(pwsh, "external P3 validation requires PowerShell 7.4+")
        probe = r"""
$ErrorActionPreference = 'Stop'
$scriptPath = [System.Environment]::GetEnvironmentVariable('P3_PIN_PROBE_SCRIPT')
$emptyFile = [System.Environment]::GetEnvironmentVariable('P3_PIN_PROBE_FILE')
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $scriptPath,
    [ref]$tokens,
    [ref]$errors
)
if ($errors.Count) { throw ($errors -join '; ') }
$definitions = @(
    $ast.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -eq 'Add-PinnedReadHandle'
    }, $true)
)
if ($definitions.Count -ne 1) { throw 'P3 pin helper extraction was incomplete.' }
. ([scriptblock]::Create($definitions[0].Extent.Text))
$pins = New-Object 'System.Collections.Generic.List[System.IO.FileStream]'
try {
    Add-PinnedReadHandle -Path $emptyFile -Pins $pins
    if ($pins.Count -ne 1 -or $pins[0].Length -ne 0) {
        throw 'P3 first pinned handle did not preserve the empty file.'
    }
} finally {
    foreach ($pin in $pins) { $pin.Dispose() }
}
"""
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            empty_file = Path(temporary) / "empty.txt"
            empty_file.write_bytes(b"")
            probe_environment = os.environ.copy()
            probe_environment["P3_PIN_PROBE_SCRIPT"] = str(
                SKILL_ROOT / "scripts" / "validate-external-p3-soak-evidence.ps1"
            )
            probe_environment["P3_PIN_PROBE_FILE"] = str(empty_file)
            completed = subprocess.run(
                [
                    str(pwsh),
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    probe,
                ],
                capture_output=True,
                check=False,
                encoding="utf-8",
                env=probe_environment,
                errors="replace",
                timeout=30,
            )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + "\n" + completed.stderr,
        )

    def test_validator_preserves_json_timestamp_fractional_precision(self) -> None:
        pwsh = shutil.which("pwsh.exe") or shutil.which("pwsh")
        self.assertIsNotNone(pwsh, "external P3 validation requires PowerShell 7.4+")
        probe = r"""
$ErrorActionPreference = 'Stop'
$scriptPath = [System.Environment]::GetEnvironmentVariable('P3_TIME_PROBE_SCRIPT')
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $scriptPath,
    [ref]$tokens,
    [ref]$errors
)
if ($errors.Count) { throw ($errors -join '; ') }
$definitions = @(
    $ast.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -eq 'ConvertTo-P3DateTimeOffset'
    }, $true)
)
if ($definitions.Count -ne 1) { throw 'P3 timestamp helper extraction was incomplete.' }
. ([scriptblock]::Create($definitions[0].Extent.Text))
$literal = '2026-08-25T09:34:54.0313677+00:00'
$payload = ('{"stamp":"' + $literal + '"}') | ConvertFrom-Json
$expected = [DateTimeOffset]::Parse(
    $literal,
    [System.Globalization.CultureInfo]::InvariantCulture,
    [System.Globalization.DateTimeStyles]::RoundtripKind
)
$converted = ConvertTo-P3DateTimeOffset -Value $payload.stamp -Role 'converted JSON timestamp'
$stringValue = ConvertTo-P3DateTimeOffset -Value $literal -Role 'string timestamp'
if ($converted.UtcTicks -ne $expected.UtcTicks -or
    $stringValue.UtcTicks -ne $expected.UtcTicks) {
    throw 'P3 timestamp conversion lost fractional precision.'
}
"""
        probe_environment = os.environ.copy()
        probe_environment["P3_TIME_PROBE_SCRIPT"] = str(
            SKILL_ROOT / "scripts" / "validate-external-p3-soak-evidence.ps1"
        )
        completed = subprocess.run(
            [
                str(pwsh),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                probe,
            ],
            capture_output=True,
            check=False,
            encoding="utf-8",
            env=probe_environment,
            errors="replace",
            timeout=30,
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + "\n" + completed.stderr,
        )


if __name__ == "__main__":
    unittest.main()
