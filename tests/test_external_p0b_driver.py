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
    def test_fault_contract_has_exactly_nineteen_unique_test_ids(self) -> None:
        test_ids = driver.REQUIRED_FAULT_TEST_IDS
        self.assertEqual(len(test_ids), 19)
        self.assertEqual(len(set(test_ids)), 19)
        self.assertTrue(all(test_id.startswith("test_") for test_id in test_ids))

    def test_structured_result_writer_is_create_new(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            destination = Path(temporary) / "result.json"
            payload = {"schema_version": 1, "runner_status": "pass"}
            driver._write_create_new_json(destination, payload)
            self.assertEqual(json.loads(destination.read_text(encoding="utf-8")), payload)
            with self.assertRaises(FileExistsError):
                driver._write_create_new_json(destination, payload)

    def test_supervisor_surfaces_structured_failure_test_ids(self) -> None:
        supervisor = (SKILL_ROOT / "scripts" / "run-external-p0b.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("Failing/error P0-B test IDs:", supervisor)
        self.assertIn("failure_test_ids", supervisor)
        self.assertIn("error_test_ids", supervisor)
        self.assertIn("missing_required_test_ids", supervisor)

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

    def test_release_inventory_excludes_only_independent_plugin_and_agent_skills(self) -> None:
        inventory = json.loads(
            (SKILL_ROOT / "assets" / "release-inventory.json").read_text(
                encoding="utf-8"
            )
        )
        matches = [
            rule
            for rule in inventory["exclusions"]
            if rule["id"] == "workspace_plugin_projects"
        ]
        self.assertEqual(len(matches), 1)
        self.assertEqual(
            matches[0]["directory_paths"],
            ["plugins/human-authorization-relay"],
        )
        self.assertEqual(matches[0]["directory_names"], [])
        self.assertEqual(matches[0]["exact_paths"], [])
        self.assertEqual(matches[0]["file_suffixes"], [])
        self.assertIs(matches[0]["must_be_absent"], False)

        agent_matches = [
            rule
            for rule in inventory["exclusions"]
            if rule["id"] == "agent_local_state"
        ]
        self.assertEqual(len(agent_matches), 1)
        self.assertEqual(agent_matches[0]["directory_paths"], [".agents/skills"])
        self.assertIn(
            ".agents/plugins/marketplace.json",
            inventory["components"][0]["paths"],
        )
        self.assertIn(
            "plugins/feishu-codex-final-return/hooks/hooks.json",
            inventory["components"][0]["paths"],
        )
        ignore = (SKILL_ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".agents/skills/", ignore)
        self.assertIn("/plugins/human-authorization-relay/", ignore)
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
        self.assertNotIn("2>&1", wrapper)
        self.assertIn("$p0EvidencePath = [string]$p0Envelope.evidence_path", wrapper)
        self.assertIn("'-P0EvidencePath', $p0EvidencePath", wrapper)
        self.assertIn("'-ExpectedP0EvidenceSha256', $p0EvidenceSha256", wrapper)
        self.assertIn("-StageName 'P0-B one-shot wrapper'", wrapper)
        self.assertIn("-StageName 'P3 one-shot wrapper'", wrapper)
        self.assertIn('"$StageName failed with exit code $exitCode."', wrapper)
        self.assertIn("return $objects.ToArray()", wrapper)
        self.assertNotIn("return ,$objects.ToArray()", wrapper)
        self.assertIn("if ($p0.Count -ne 2)", wrapper)
        self.assertIn("if ($p3.Count -ne 2)", wrapper)

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


if __name__ == "__main__":
    unittest.main()
