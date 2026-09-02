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

import external_p0b_test_runner as driver  # noqa: E402


class ExternalP0BTestDriverTests(unittest.TestCase):
    def test_fault_contract_has_nineteen_current_test_ids(self) -> None:
        test_ids = driver.REQUIRED_FAULT_TEST_IDS
        expected = (
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
        self.assertEqual(test_ids, expected)
        self.assertEqual(len(set(test_ids)), 19)
        self.assertTrue(all(test_id.startswith("test_") for test_id in test_ids))
        expected_groups = (
            expected[0:2],
            expected[2:4],
            expected[4:5],
            expected[5:6],
            expected[6:8],
            expected[8:9],
            expected[9:10],
            expected[10:12],
            expected[12:14],
            expected[14:16],
            expected[16:18],
            expected[18:19],
        )
        evidence_schema = json.loads(
            (SKILL_ROOT / "assets" / "external-test-evidence.schema.json").read_text(
                encoding="utf-8"
            )
        )
        schema_fault_ids = tuple(
            branch["then"]["properties"]["test_id"]["const"]
            for branch in evidence_schema["$defs"]["fault_result"]["allOf"]
        )
        self.assertEqual(tuple(";".join(group) for group in expected_groups), schema_fault_ids)
        self.assertEqual(2, evidence_schema["properties"]["schema_version"]["const"])

    def test_structured_result_writer_is_create_new(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            destination = Path(temporary) / "result.json"
            payload = {"schema_version": 1, "runner_status": "pass"}
            driver._write_create_new_json(destination, payload)
            self.assertEqual(json.loads(destination.read_text(encoding="utf-8")), payload)
            with self.assertRaises(FileExistsError):
                driver._write_create_new_json(destination, payload)

    def test_supervisor_surfaces_structured_failure_test_ids(self) -> None:
        runner = (
            SKILL_ROOT / "scripts" / "external_p0b_test_runner.py"
        ).read_text(encoding="utf-8")
        supervisor = (SKILL_ROOT / "scripts" / "run-external-p0b.ps1").read_text(
            encoding="utf-8"
        )
        validator = (
            SKILL_ROOT / "scripts" / "validate-external-p0b-evidence.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("Failing/error P0-B test IDs:", supervisor)
        self.assertIn("failure_test_ids", supervisor)
        self.assertIn("error_test_ids", supervisor)
        self.assertIn("missing_required_test_ids", supervisor)
        self.assertIn("$queueCounts.reply_pending", supervisor)
        for queue_status in (
            "queued",
            "running",
            "control_sending",
            "reply_pending",
            "retryable_failed",
            "completed",
            "terminal_failed",
        ):
            self.assertIn(f"'{queue_status}'", supervisor)
            self.assertIn(f"'{queue_status}'", validator)
        self.assertIn("'beeper'", supervisor)
        self.assertIn("beeper_status_stdout_sha256", supervisor)
        for script in (supervisor, validator):
            self.assertIn("$healthVersionRelationIsAdmissible", script)
            self.assertIn("[string]$health.bridge_version -ceq", script)
            self.assertIn(
                "[string]$status.installed_manifest.bridge_version",
                script,
            )
            self.assertIn("[string]$status.status -ceq 'warning'", script)
            self.assertIn("process_identity_current", script)
            self.assertIn("actionable_retryable_failed", script)
        self.assertIn("$processIdentityIsStopped", supervisor)
        self.assertIn(
            "Test-JsonBooleanValue -Value $health.process_identity_current -Expected $false",
            validator,
        )
        for script in (supervisor, validator):
            self.assertIn("function Test-AdmissibleStoppedStatus", script)
            self.assertIn("integrity_check_failed", script)
            self.assertIn("$issueCountIsInteger", script)
            self.assertIn(
                "$manifest.issue_codes -is [System.Array]", script
            )
        self.assertIn("and not skipped_ids", runner)
        self.assertIn(
            "@($structuredTestResult.skipped_test_ids).Count -ne 0",
            supervisor,
        )
        self.assertIn(
            "@($structured.skipped_test_ids).Count -ne 0",
            validator,
        )
        self.assertIn(
            "Assert-UniqueJsonObjectKeys -Json $structuredJson -Role 'P0-B structured unittest result'",
            validator,
        )
        self.assertIn("function Test-ExactJsonPropertySet", validator)
        self.assertIn("function Test-JsonBooleanValue", validator)
        self.assertIn("function Test-JsonIntegerZero", validator)
        self.assertIn("function Test-MvpObservation", validator)
        self.assertIn("'health_snapshot', 'health_issue'", validator)
        self.assertIn("$null -ne $status.health_issue", validator)
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
            self.assertIn(marker, validator)
        self.assertIn(
            "'claimed', 'beeper_host_id', 'ok', 'pending', 'registered', 'beeper_thread_id'",
            validator,
        )
        self.assertIn(
            "'dial_generation', 'dial_inflight', 'dial_lease_remaining_seconds'",
            validator,
        )
        self.assertIn("Test-JsonIntegerZero -Value $beeperStatus.pending", validator)
        self.assertIn(
            "$null -ne $beeperStatus.dial_lease_remaining_seconds",
            validator,
        )
        self.assertIn("Beeper queue capture is not an exact idle checkpoint", validator)
        schema = json.loads(
            (SKILL_ROOT / "assets" / "external-test-evidence.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            0,
            schema["$defs"]["execution"]["properties"]["skipped"]["const"],
        )
        self.assertIn(
            "$workDirectoryLeaf = 'w-' + (Get-StringSha256 -Text $receiptId).Substring(0, 8)",
            supervisor,
        )
        self.assertIn("FEISHU_BRIDGE_TEST_TMP = $workDirectory", supervisor)
        self.assertIn("$expectedWorkDirectoryLeaf = 'w-' +", validator)
        self.assertIn(
            "Receipt test temp does not equal its retained P0-B work directory.",
            validator,
        )

    def test_stopped_status_predicate_accepts_only_signed_or_hook_refresh_shape(
        self,
    ) -> None:
        pwsh = shutil.which("pwsh.exe") or shutil.which("pwsh")
        self.assertIsNotNone(pwsh, "release status predicate requires PowerShell 7.4+")
        script_paths = [
            SKILL_ROOT / "scripts" / "run-external-p0b.ps1",
            SKILL_ROOT / "scripts" / "validate-external-p0b-evidence.ps1",
            SKILL_ROOT / "scripts" / "run-external-p3-soak.ps1",
            SKILL_ROOT / "scripts" / "validate-external-p3-soak-evidence.ps1",
        ]
        probe = r"""
$ErrorActionPreference = 'Stop'
$paths = @(
    [Environment]::GetEnvironmentVariable('BRIDGE_STATUS_HELPER_SCRIPTS') |
        Microsoft.PowerShell.Utility\ConvertFrom-Json
)
$cases = @(
    [pscustomobject]@{ name = 'signed-pass'; expected = $true; json = '{"status":"pass","installed_manifest":{"present":true,"valid":true,"bridge_version":"4.2.0-alpha.64","issue_count":0,"issue_codes":[]},"health_issue":null}' },
    [pscustomobject]@{ name = 'hook-refresh-warning'; expected = $true; json = '{"status":"warning","installed_manifest":{"present":false,"valid":false,"bridge_version":null,"issue_count":1,"issue_codes":["integrity_check_failed"]},"health_issue":null}' },
    [pscustomobject]@{ name = 'present-invalid'; expected = $false; json = '{"status":"warning","installed_manifest":{"present":true,"valid":false,"bridge_version":null,"issue_count":1,"issue_codes":["integrity_check_failed"]},"health_issue":null}' },
    [pscustomobject]@{ name = 'stale-version'; expected = $false; json = '{"status":"warning","installed_manifest":{"present":false,"valid":false,"bridge_version":"old","issue_count":1,"issue_codes":["integrity_check_failed"]},"health_issue":null}' },
    [pscustomobject]@{ name = 'multiple-codes'; expected = $false; json = '{"status":"warning","installed_manifest":{"present":false,"valid":false,"bridge_version":null,"issue_count":1,"issue_codes":["integrity_check_failed","other"]},"health_issue":null}' },
    [pscustomobject]@{ name = 'scalar-codes'; expected = $false; json = '{"status":"warning","installed_manifest":{"present":false,"valid":false,"bridge_version":null,"issue_count":1,"issue_codes":"integrity_check_failed"},"health_issue":null}' },
    [pscustomobject]@{ name = 'string-count'; expected = $false; json = '{"status":"warning","installed_manifest":{"present":false,"valid":false,"bridge_version":null,"issue_count":"1","issue_codes":["integrity_check_failed"]},"health_issue":null}' },
    [pscustomobject]@{ name = 'boolean-count'; expected = $false; json = '{"status":"warning","installed_manifest":{"present":false,"valid":false,"bridge_version":null,"issue_count":true,"issue_codes":["integrity_check_failed"]},"health_issue":null}' },
    [pscustomobject]@{ name = 'health-error'; expected = $false; json = '{"status":"warning","installed_manifest":{"present":false,"valid":false,"bridge_version":null,"issue_count":1,"issue_codes":["integrity_check_failed"]},"health_issue":"invalid_health_snapshot"}' },
    [pscustomobject]@{ name = 'unsigned-pass'; expected = $false; json = '{"status":"pass","installed_manifest":{"present":false,"valid":false,"bridge_version":null,"issue_count":1,"issue_codes":["integrity_check_failed"]},"health_issue":null}' },
    [pscustomobject]@{ name = 'failed'; expected = $false; json = '{"status":"fail","installed_manifest":{"present":false,"valid":false,"bridge_version":null,"issue_count":1,"issue_codes":["integrity_check_failed"]},"health_issue":null}' }
)
foreach ($path in $paths) {
    Remove-Item Function:\Test-JsonBooleanValue -ErrorAction SilentlyContinue
    Remove-Item Function:\Test-AdmissibleStoppedStatus -ErrorAction SilentlyContinue
    $tokens = $null
    $errors = $null
    $ast = [Management.Automation.Language.Parser]::ParseFile(
        [string]$path,
        [ref]$tokens,
        [ref]$errors
    )
    if ($errors.Count) { throw ($errors -join '; ') }
    $definitions = @(
        $ast.FindAll({
            param($node)
            $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
                $node.Name -in @(
                    'Test-JsonBooleanValue',
                    'Test-AdmissibleStoppedStatus'
                )
        }, $true) | Sort-Object { $_.Extent.StartOffset }
    )
    if (@($definitions | Where-Object Name -eq 'Test-AdmissibleStoppedStatus').Count -ne 1) {
        throw "Missing exact stopped-status predicate: $path"
    }
    foreach ($definition in $definitions) {
        . ([scriptblock]::Create($definition.Extent.Text))
    }
    foreach ($case in $cases) {
        $status = [string]$case.json |
            Microsoft.PowerShell.Utility\ConvertFrom-Json
        $actual = [bool](Test-AdmissibleStoppedStatus -Status $status)
        if ($actual -ne [bool]$case.expected) {
            throw "Stopped-status predicate mismatch for $($case.name): $path"
        }
    }
}
"ok"
"""
        environment = os.environ.copy()
        environment["BRIDGE_STATUS_HELPER_SCRIPTS"] = json.dumps(
            [str(path) for path in script_paths], separators=(",", ":")
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
            env=environment,
            errors="replace",
            timeout=30,
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + "\n" + completed.stderr,
        )
        self.assertEqual("ok", completed.stdout.strip())

    def test_one_shot_wrapper_reports_the_exact_success_evidence_path(self) -> None:
        wrapper = (SKILL_ROOT / "scripts" / "invoke-external-p0b-once.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "$envelope | Add-Member -NotePropertyName evidence_path -NotePropertyValue $evidencePath",
            wrapper,
        )
        self.assertLess(
            wrapper.index("-NotePropertyName evidence_path"),
            wrapper.index("$validation = Invoke-CleanPowerShellJson"),
        )
        self.assertIn("$envelope.schema_version -ne 2", wrapper)
        self.assertIn("$validation.validation_schema_version -ne 2", wrapper)
        self.assertIn("^p0b-v2-", wrapper)
        self.assertIn("[int]$StageTimeoutSeconds", wrapper)
        self.assertIn("$process.Kill($true)", wrapper)
        self.assertIn("$process.WaitForExit(30000)", wrapper)
        self.assertIn("[System.Threading.Tasks.Task]::WaitAll(", wrapper)
        self.assertIn("$process.Dispose()", wrapper)
        self.assertIn("-StageTimeoutSeconds 2400", wrapper)
        self.assertIn("-StageTimeoutSeconds 420", wrapper)
        self.assertNotIn("& $pwsh -NoLogo", wrapper)
        self.assertNotIn("$nonempty | Write-Output", wrapper)

    def test_one_shot_wrappers_require_a_bounded_artifact_root(self) -> None:
        wrapper_names = (
            "invoke-external-p0b-once.ps1",
            "invoke-external-p3-soak-once.ps1",
            "invoke-external-p0b-p3-once.ps1",
        )
        wrappers = {
            name: (SKILL_ROOT / "scripts" / name).read_text(encoding="utf-8")
            for name in wrapper_names
        }
        for name, wrapper in wrappers.items():
            with self.subTest(wrapper=name):
                self.assertIn("[string]$ArtifactRoot", wrapper)
                self.assertIn(
                    "Resolve-ArtifactRoot -Path $ArtifactRoot -ProtectedRoots",
                    wrapper,
                )
                self.assertIn("ArtifactRoot cannot be a filesystem root.", wrapper)
                self.assertIn(
                    "$fullPath,\n            $driveRoot,",
                    wrapper,
                )
                self.assertNotIn(
                    "$driveRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar)",
                    wrapper,
                )
                self.assertIn("[System.IO.DriveType]::Fixed", wrapper)
                self.assertIn("ordinary ready fixed local drive", wrapper)
                self.assertIn(
                    "Assert-NoReparseExistingPathPrefix -Path $protectedRoot",
                    wrapper,
                )
                self.assertIn("$Role path chain contains a reparse point", wrapper)
                self.assertIn(
                    "ArtifactRoot must be separate from Desktop, Harness, project, and runtime roots.",
                    wrapper,
                )
                self.assertIn("GetFinalPathNameByHandleW", wrapper)
                self.assertIn("GetLongPathNameW", wrapper)
                self.assertIn("ExpandExistingLongPath", wrapper)
                self.assertIn("QueryDosDeviceW", wrapper)
                self.assertIn("ResolveExistingDevicePath", wrapper)
                self.assertIn("Get-PhysicalComparisonPath", wrapper)
                self.assertIn("-RejectLexicalAlias", wrapper)
                self.assertIn(
                    "must not use an 8.3 or other lexical filesystem alias",
                    wrapper,
                )
                self.assertIn(
                    "Assert-PhysicalIsolation -ArtifactRecord $artifactPre",
                    wrapper,
                )
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
                self.assertNotIn("GetPathRoot($desktop)", wrapper)

        p0_wrapper = wrappers["invoke-external-p0b-once.ps1"]
        self.assertIn('$runRoot = Join-Path $artifact "p0b-$runTag"', p0_wrapper)
        self.assertIn("$workRoot = Join-Path $runRoot 'work'", p0_wrapper)
        self.assertIn("$evidenceRoot = Join-Path $runRoot 'evidence'", p0_wrapper)
        self.assertIn("Unique P0-B artifact run destination already exists", p0_wrapper)

        combined = wrappers["invoke-external-p0b-p3-once.ps1"]
        self.assertEqual(2, combined.count("'-ArtifactRoot', $artifact"))

    def test_release_inventory_excludes_agent_skills_and_tracks_bridge_files(self) -> None:
        inventory = json.loads(
            (SKILL_ROOT / "assets" / "release-inventory.json").read_text(
                encoding="utf-8"
            )
        )
        agent_matches = [
            rule
            for rule in inventory["exclusions"]
            if rule["id"] == "agent_local_state"
        ]
        self.assertEqual(len(agent_matches), 1)
        self.assertEqual(agent_matches[0]["directory_paths"], [".agents/skills"])
        self.assertIn(
            ".codex-plugin/plugin.json",
            inventory["components"][0]["paths"],
        )
        self.assertIn(
            "README.md",
            inventory["components"][0]["paths"],
        )
        # The Responder-owned Final Callback replaced the plugin UserPromptSubmit/Stop
        # Hooks.  A release must not resurrect the removed Hook transport.
        self.assertNotIn(
            "hooks/hooks.json",
            inventory["components"][0]["paths"],
        )
        self.assertFalse((SKILL_ROOT / "hooks" / "hooks.json").exists())
        self.assertIn(
            "skills/feishu-codex-bridge/SKILL.md",
            inventory["components"][0]["paths"],
        )
        ignore = (SKILL_ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".agents/skills/", ignore)
        self.assertNotIn("\n.agents/\n", f"\n{ignore}")
        self.assertNotIn("\n/plugins/\n", f"\n{ignore}")

        temp_matches = [
            rule
            for rule in inventory["exclusions"]
            if rule["id"] == "workspace_local_temp"
        ]
        self.assertEqual(len(temp_matches), 1)
        self.assertEqual(temp_matches[0]["directory_paths"], [".tmp"])
        self.assertEqual(temp_matches[0]["directory_names"], [])
        self.assertEqual(temp_matches[0]["exact_paths"], [])
        self.assertEqual(temp_matches[0]["file_suffixes"], [])
        self.assertIs(temp_matches[0]["must_be_absent"], False)

    def test_combined_suite_keeps_child_stderr_separate_and_handoffs_p0(self) -> None:
        wrapper = (
            SKILL_ROOT / "scripts" / "invoke-external-p0b-p3-once.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("$startInfo.RedirectStandardOutput = $true", wrapper)
        self.assertIn("$startInfo.RedirectStandardError = $true", wrapper)
        self.assertIn("[int]$StageTimeoutSeconds", wrapper)
        self.assertIn("$process.Kill($true)", wrapper)
        self.assertIn("$process.WaitForExit(30000)", wrapper)
        self.assertIn("[System.Threading.Tasks.Task]::WaitAll(", wrapper)
        self.assertIn("$process.Dispose()", wrapper)
        self.assertNotIn("$process.WaitForExit()", wrapper)
        self.assertNotIn("2>&1", wrapper)
        self.assertIn("$p0EvidencePath = [string]$p0Envelope.evidence_path", wrapper)
        self.assertIn("'-P0EvidencePath', $p0EvidencePath", wrapper)
        self.assertIn("'-ExpectedP0EvidenceSha256', $p0EvidenceSha256", wrapper)
        self.assertEqual(2, wrapper.count("'-ArtifactRoot', $artifact"))
        self.assertIn("-StageName 'P0-B one-shot wrapper'", wrapper)
        self.assertIn("-StageName 'P3 one-shot wrapper'", wrapper)
        self.assertIn("-StageTimeoutSeconds 3000", wrapper)
        self.assertIn(
            "$p3StageTimeout = [Math]::Min(1800, $TimeoutSeconds + 900)",
            wrapper,
        )
        self.assertIn("-StageTimeoutSeconds $p3StageTimeout", wrapper)
        self.assertIn('"$StageName failed with exit code $exitCode."', wrapper)
        self.assertIn("Select-Object -Last 120", wrapper)
        self.assertIn("$stderr + [Environment]::NewLine + $stdout", wrapper)
        self.assertIn("return $objects.ToArray()", wrapper)
        self.assertNotIn("return ,$objects.ToArray()", wrapper)
        self.assertIn("if ($p0.Count -ne 2)", wrapper)
        self.assertIn("if ($p3.Count -ne 2)", wrapper)
        self.assertIn("$p0Envelope.schema_version -ne 2", wrapper)
        self.assertIn("$p0Validation.validation_schema_version -ne 2", wrapper)
        self.assertIn("$p3Envelope.schema_version -ne 2", wrapper)
        self.assertIn("$p3Validation.validation_schema_version -ne 2", wrapper)

    def test_supervisor_and_validator_collapse_or_reject_path_aliases(self) -> None:
        pwsh = shutil.which("pwsh.exe") or shutil.which("pwsh")
        self.assertIsNotNone(pwsh, "external P0-B requires PowerShell 7.4+")
        probe = r"""
$ErrorActionPreference = 'Stop'
$sourceRoot = [System.Environment]::GetEnvironmentVariable('P0B_PATH_PROBE_ROOT')
$scriptPath = [System.Environment]::GetEnvironmentVariable('P0B_PATH_PROBE_SCRIPT')
if ([string]::IsNullOrWhiteSpace($sourceRoot) -or
    [string]::IsNullOrWhiteSpace($scriptPath)) {
    throw 'Path probe environment is incomplete.'
}
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $scriptPath,
    [ref]$tokens,
    [ref]$errors
)
if ($errors.Count) { throw ($errors -join '; ') }
$native = $ast.Find({
    param($node)
    $node -is [System.Management.Automation.Language.StringConstantExpressionAst] -and
        $node.Value.Contains('public static class ExternalP0BPath')
}, $true)
if ($null -eq $native) { throw 'Native path canonicalizer is missing.' }
Microsoft.PowerShell.Utility\Add-Type -TypeDefinition $native.Value
$wanted = @(
    'Get-NormalizedFullPath',
    'Get-CanonicalComparisonPath',
    'Test-IsWithinRoot',
    'Test-NoReparsePathChain',
    'Assert-NoReparseExistingPathPrefix'
)
$definitions = @(
    $ast.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
            $wanted -contains $node.Name
    }, $true) | Sort-Object { $_.Extent.StartOffset } | ForEach-Object { $_.Extent.Text }
)
if ($definitions.Count -ne 4) { throw 'Path helper extraction was incomplete.' }
. ([scriptblock]::Create(($definitions -join "`n")))
$extended = '\\?\' + $sourceRoot
$extendedRejected = $false
try { [void](Get-NormalizedFullPath -Path $extended) } catch { $extendedRejected = $true }
if (-not $extendedRejected) { throw 'Extended device syntax was accepted.' }
$child = Join-Path $sourceRoot 'scripts'
if (-not (Test-IsWithinRoot -Root $sourceRoot -Candidate $child)) {
    throw 'Ordinary child containment failed.'
}
$drive = [System.IO.Path]::GetPathRoot($sourceRoot)
$shortProgramFiles = Join-Path $drive 'PROGRA~1'
$longProgramFiles = Join-Path $drive 'Program Files'
if ((Test-Path -LiteralPath $shortProgramFiles -PathType Container) -and
    -not (Test-IsWithinRoot -Root $longProgramFiles -Candidate $shortProgramFiles)) {
    throw '8.3 alias was not collapsed to its physical path.'
}
"""
        for script_name in (
            "run-external-p0b.ps1",
            "validate-external-p0b-evidence.ps1",
        ):
            with self.subTest(script=script_name):
                probe_environment = os.environ.copy()
                probe_environment["P0B_PATH_PROBE_ROOT"] = str(SKILL_ROOT)
                probe_environment["P0B_PATH_PROBE_SCRIPT"] = str(
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

    def test_one_shot_wrapper_helpers_physically_canonicalize_83_aliases(self) -> None:
        pwsh = shutil.which("pwsh.exe") or shutil.which("pwsh")
        self.assertIsNotNone(pwsh, "external P0-B requires PowerShell 7.4+")
        probe = r"""
$ErrorActionPreference = 'Stop'
$sourceRoot = [System.Environment]::GetEnvironmentVariable('ONESHOT_PATH_PROBE_ROOT')
$scriptPath = [System.Environment]::GetEnvironmentVariable('ONESHOT_PATH_PROBE_SCRIPT')
if ([string]::IsNullOrWhiteSpace($sourceRoot) -or
    [string]::IsNullOrWhiteSpace($scriptPath)) {
    throw 'One-shot path probe environment is incomplete.'
}
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $scriptPath,
    [ref]$tokens,
    [ref]$errors
)
if ($errors.Count) { throw ($errors -join '; ') }
$native = $ast.Find({
    param($node)
    $node -is [System.Management.Automation.Language.StringConstantExpressionAst] -and
        $node.Value.Contains('public static class ExternalOneShotPath')
}, $true)
if ($null -eq $native) { throw 'One-shot native path canonicalizer is missing.' }
Microsoft.PowerShell.Utility\Add-Type -TypeDefinition $native.Value
$wanted = @(
    'Get-NormalizedLocalDosPath',
    'Test-IsWithinPath',
    'Assert-NoReparseExistingPathPrefix',
    'Get-PhysicalComparisonPath',
    'Resolve-ArtifactRoot'
)
$definitions = @(
    $ast.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
            $wanted -contains $node.Name
    }, $true) | Sort-Object { $_.Extent.StartOffset } | ForEach-Object { $_.Extent.Text }
)
if ($definitions.Count -ne 5) { throw 'One-shot path helper extraction was incomplete.' }
. ([scriptblock]::Create(($definitions -join "`n")))
$driveRoot = [System.IO.Path]::GetPathRoot($sourceRoot)
$rootRejected = $false
try {
    [void](Resolve-ArtifactRoot -Path $driveRoot -ProtectedRoots @($sourceRoot))
} catch {
    $rootRejected = $_.Exception.Message.Contains('filesystem root')
}
if (-not $rootRejected) { throw 'One-shot ArtifactRoot accepted a filesystem root.' }
$sourceRecord = Get-PhysicalComparisonPath -Path $sourceRoot -Role 'probe source'
if ([string]::IsNullOrWhiteSpace([string]$sourceRecord.DevicePath)) {
    throw 'One-shot physical source path was empty.'
}
$drive = [System.IO.Path]::GetPathRoot($sourceRoot)
$shortProgramFiles = Join-Path $drive 'PROGRA~1'
$longProgramFiles = Join-Path $drive 'Program Files'
if ((Test-Path -LiteralPath $shortProgramFiles -PathType Container) -and
    (Test-Path -LiteralPath $longProgramFiles -PathType Container)) {
    $shortRecord = Get-PhysicalComparisonPath -Path $shortProgramFiles -Role 'short alias'
    $longRecord = Get-PhysicalComparisonPath -Path $longProgramFiles -Role 'long path'
    if (-not $shortRecord.DevicePath.Equals(
            $longRecord.DevicePath,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
        throw 'One-shot helper did not collapse the 8.3 alias for physical comparison.'
    }
    $aliasRejected = $false
    try {
        [void](Resolve-ArtifactRoot `
            -Path $shortProgramFiles `
            -ProtectedRoots @($sourceRoot))
    } catch {
        $aliasRejected = $_.Exception.Message.Contains('8.3')
    }
    if (-not $aliasRejected) { throw 'One-shot ArtifactRoot accepted an 8.3 alias.' }
}
"""
        for script_name in (
            "invoke-external-p0b-once.ps1",
            "invoke-external-p3-soak-once.ps1",
            "invoke-external-p0b-p3-once.ps1",
        ):
            with self.subTest(script=script_name):
                probe_environment = os.environ.copy()
                probe_environment["ONESHOT_PATH_PROBE_ROOT"] = str(SKILL_ROOT)
                probe_environment["ONESHOT_PATH_PROBE_SCRIPT"] = str(
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

    def test_release_audit_rejects_duplicate_and_case_colliding_json_members(self) -> None:
        pwsh = shutil.which("pwsh.exe") or shutil.which("pwsh")
        self.assertIsNotNone(pwsh, "external P0-B requires PowerShell 7.4+")
        audit_path = SKILL_ROOT / "scripts" / "audit-feishu-codex-release.ps1"
        audit_text = audit_path.read_text(encoding="utf-8")
        for marker in (
            "$manifest = ConvertFrom-UniqueJsonBytes",
            "$marketplace = ConvertFrom-UniqueJsonBytes",
            "$inventory = ConvertFrom-UniqueJsonBytes",
            "[System.StringComparer]::OrdinalIgnoreCase",
            "$manifest.name -isnot [string]",
            "$marketplace.plugins -isnot [System.Array]",
            "$entries[0].source.path -isnot [string]",
            "^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$",
        ):
            self.assertIn(marker, audit_text)

        probe = r"""
$ErrorActionPreference = 'Stop'
$scriptPath = [System.Environment]::GetEnvironmentVariable('P0B_JSON_PROBE_SCRIPT')
if ([string]::IsNullOrWhiteSpace($scriptPath)) {
    throw 'JSON probe path is missing.'
}
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $scriptPath,
    [ref]$tokens,
    [ref]$errors
)
if ($errors.Count) { throw ($errors -join '; ') }
$wanted = @('Assert-JsonMembersUnique', 'ConvertFrom-UniqueJsonBytes')
$definitions = @(
    $ast.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
            $wanted -contains $node.Name
    }, $true) | Sort-Object { $_.Extent.StartOffset } | ForEach-Object { $_.Extent.Text }
)
if ($definitions.Count -ne 2) { throw 'JSON helper extraction was incomplete.' }
. ([scriptblock]::Create(($definitions -join "`n")))
$script:StrictUtf8 = New-Object System.Text.UTF8Encoding($false, $true)
$valid = [System.Text.Encoding]::UTF8.GetBytes('{"name":"x","source":{"path":"a"}}')
$null = ConvertFrom-UniqueJsonBytes -Bytes $valid -Role 'probe'
$invalid = @(
    '{"name":"x","name":"y"}',
    '{"Name":"x","name":"y"}',
    '{"source":{"path":"a","PATH":"b"}}'
)
foreach ($json in $invalid) {
    $rejected = $false
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
        $null = ConvertFrom-UniqueJsonBytes -Bytes $bytes -Role 'probe'
    } catch {
        $rejected = $true
    }
    if (-not $rejected) { throw 'Duplicate JSON member was accepted.' }
}
"""
        environment = os.environ.copy()
        environment["P0B_JSON_PROBE_SCRIPT"] = str(audit_path)
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
            env=environment,
            errors="replace",
            timeout=30,
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + "\n" + completed.stderr,
        )

    def test_release_audit_uses_one_harness_provenance_reference(self) -> None:
        audit_text = (
            SKILL_ROOT / "scripts" / "audit-feishu-codex-release.ps1"
        ).read_text(encoding="utf-8")
        start = audit_text.index("    $harnessReference =")
        end = audit_text.index("\n$schemaText =", start)
        frozen_binding = audit_text[start:end]

        self.assertIn("$harnessReference", frozen_binding)
        self.assertIn("HARNESS_FROZEN_HASH_DRIFT", frozen_binding)
        self.assertIn("HARNESS_FROZEN_HASH_UNBOUND", frozen_binding)
        self.assertIn("references/external-lab.md", frozen_binding)
        self.assertNotIn("$upgradeGuide", frozen_binding)
        self.assertNotIn("upgrade-bridge.md", frozen_binding)
        self.assertEqual(1, frozen_binding.count("HARNESS_FROZEN_HASH_UNBOUND"))

    def test_real_release_source_gate_rejects_ambiguous_route_metadata(self) -> None:
        pwsh = shutil.which("pwsh.exe") or shutil.which("pwsh")
        self.assertIsNotNone(pwsh, "external P0-B requires PowerShell 7.4+")
        inventory = json.loads(
            (SKILL_ROOT / "assets" / "release-inventory.json").read_text(
                encoding="utf-8"
            )
        )
        desktop = next(
            component
            for component in inventory["components"]
            if component["name"] == "desktop_bridge"
        )

        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            repository = Path(temporary) / "repo"
            plugin = repository / "plugins" / "feishu-codex-bridge"
            for relative in desktop["paths"]:
                source = SKILL_ROOT / Path(relative)
                destination = plugin / Path(relative)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)

            marketplace = repository / ".agents" / "plugins" / "marketplace.json"
            marketplace.parent.mkdir(parents=True)
            marketplace_payload = {
                "name": "feishu-codex-bridge",
                "plugins": [
                    {
                        "name": "feishu-codex-bridge",
                        "source": {
                            "source": "local",
                            "path": "./plugins/feishu-codex-bridge",
                        },
                    }
                ],
            }
            marketplace.write_text(
                json.dumps(marketplace_payload, separators=(",", ":")),
                encoding="utf-8",
            )
            def run_audit(
                role: str = "canonical-development",
                *,
                desktop_root: Path = plugin,
                environment: dict[str, str] | None = None,
                cwd: Path | None = None,
            ) -> subprocess.CompletedProcess[str]:
                audit = desktop_root / "scripts" / "audit-feishu-codex-release.ps1"
                return subprocess.run(
                    [
                        str(pwsh),
                        "-NoLogo",
                        "-NoProfile",
                        "-NonInteractive",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        str(audit),
                        "-DesktopRoot",
                        str(desktop_root),
                        "-DesktopOnly",
                        "-SourceRole",
                        role,
                    ],
                    capture_output=True,
                    check=False,
                    encoding="utf-8",
                    env=environment,
                    errors="replace",
                    cwd=cwd,
                    timeout=120,
                )

            baseline = run_audit()
            self.assertEqual(0, baseline.returncode, baseline.stdout + baseline.stderr)
            self.assertEqual("pass", json.loads(baseline.stdout)["status"])

            manifest_path = plugin / ".codex-plugin" / "plugin.json"
            inventory_path = plugin / "assets" / "release-inventory.json"
            originals = {
                manifest_path: manifest_path.read_bytes(),
                inventory_path: inventory_path.read_bytes(),
                marketplace: marketplace.read_bytes(),
            }

            manifest_payload = json.loads(originals[manifest_path].decode("utf-8"))
            numeric_manifest = dict(manifest_payload)
            numeric_manifest["version"] = 7
            traversal_manifest = dict(manifest_payload)
            traversal_manifest["version"] = "../escape"
            numeric_marketplace = json.loads(originals[marketplace].decode("utf-8"))
            numeric_marketplace["plugins"][0]["source"]["path"] = 7
            mixed_marketplace = json.loads(originals[marketplace].decode("utf-8"))
            mixed_entry = json.loads(
                json.dumps(mixed_marketplace["plugins"][0])
            )
            mixed_entry["name"] = "Feishu-Codex-Bridge"
            mixed_marketplace["plugins"].append(mixed_entry)
            cases = (
                (
                    manifest_path,
                    b'{"name":"feishu-codex-bridge",' + originals[manifest_path].lstrip()[1:],
                ),
                (
                    manifest_path,
                    b'{"Name":"feishu-codex-bridge",' + originals[manifest_path].lstrip()[1:],
                ),
                (
                    manifest_path,
                    json.dumps(numeric_manifest).encode("utf-8"),
                ),
                (
                    manifest_path,
                    json.dumps(traversal_manifest).encode("utf-8"),
                ),
                (
                    marketplace,
                    b'{"name":"feishu-codex-bridge",' + originals[marketplace].lstrip()[1:],
                ),
                (
                    marketplace,
                    json.dumps(numeric_marketplace).encode("utf-8"),
                ),
                (
                    marketplace,
                    json.dumps(mixed_marketplace).encode("utf-8"),
                ),
                (
                    inventory_path,
                    b'{"schema_version":1,' + originals[inventory_path].lstrip()[1:],
                ),
            )
            for path, mutated in cases:
                with self.subTest(path=path.name, marker=mutated[:48]):
                    try:
                        path.write_bytes(mutated)
                        rejected = run_audit()
                        self.assertNotEqual(
                            0,
                            rejected.returncode,
                            rejected.stdout + rejected.stderr,
                        )
                    finally:
                        path.write_bytes(originals[path])

            wrong_case_role = run_audit("CANONICAL-DEVELOPMENT")
            self.assertNotEqual(
                0,
                wrong_case_role.returncode,
                wrong_case_role.stdout + wrong_case_role.stderr,
            )
            restored = run_audit()
            self.assertEqual(0, restored.returncode, restored.stdout + restored.stderr)

            plugin_version = manifest_payload["version"]
            # Keep the installed-snapshot fixture unique without copying every
            # release file through a legacy MAX_PATH-limited Windows API.  The
            # canonical checks are complete above, so a same-volume directory
            # rename can expose the exact bytes at the cache route and then put
            # them back before TemporaryDirectory performs its cleanup.
            fake_home = Path(temporary) / "h"
            installed = (
                fake_home
                / "plugins"
                / "cache"
                / "feishu-codex-bridge"
                / "feishu-codex-bridge"
                / plugin_version
            )
            installed.parent.mkdir(parents=True)
            plugin.rename(installed)
            try:
                exact_home_environment = os.environ.copy()
                exact_home_environment["CODEX_HOME"] = str(fake_home)
                exact_installed = run_audit(
                    "installed-snapshot",
                    desktop_root=installed,
                    environment=exact_home_environment,
                )
                self.assertEqual(
                    0,
                    exact_installed.returncode,
                    exact_installed.stdout + exact_installed.stderr,
                )
                for invalid_home in ("h", "   "):
                    with self.subTest(codex_home=repr(invalid_home)):
                        invalid_environment = os.environ.copy()
                        invalid_environment["CODEX_HOME"] = invalid_home
                        rejected_home = run_audit(
                            "installed-snapshot",
                            desktop_root=installed,
                            environment=invalid_environment,
                            cwd=TEST_TEMP_ROOT,
                        )
                        self.assertNotEqual(
                            0,
                            rejected_home.returncode,
                            rejected_home.stdout + rejected_home.stderr,
                        )
            finally:
                installed.rename(plugin)


if __name__ == "__main__":
    unittest.main()
