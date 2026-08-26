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
        scenarios = list(driver.SCENARIO_CONTRACT)
        scenario_ids = [entry["scenario_id"] for entry in scenarios]
        test_ids = [entry["test_id"] for entry in scenarios]
        self.assertEqual(10, len(scenarios))
        self.assertEqual(10, len(set(scenario_ids)))
        self.assertEqual(10, len(set(test_ids)))

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
            payload = {"schema_version": 1, "runner_status": "pass"}
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

        for marker in (
            "FEISHU_BRIDGE_EXTERNAL_P3_SOAK",
            "ExternalP3SoakJob",
            "Get-ExternalRunnerProcessGuard",
            "codex_ancestor_match_count",
            "runner_surface",
            "snapshot_files_pinned",
            "source-snapshot",
            "p0-validation-pre",
            "p0-validation-post",
            "live_desktop_contacted",
            "live_feishu_contacted",
        ):
            self.assertIn(marker, supervisor)
        for entry in driver.SCENARIO_CONTRACT:
            self.assertIn(entry["scenario_id"], validator)
            self.assertIn(entry["test_id"], validator)
        self.assertEqual(
            "external_p3_bounded_soak",
            schema["properties"]["evidence_kind"]["const"],
        )
        self.assertEqual(10, schema["properties"]["execution"]["properties"]["scenario_count"]["const"])
        self.assertFalse(
            schema["properties"]["execution"]["properties"]["live_desktop_contacted"]["const"]
        )
        self.assertFalse(
            schema["properties"]["execution"]["properties"]["live_feishu_contacted"]["const"]
        )
        self.assertIn("-NotePropertyName evidence_path", wrapper)

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
Assert-NoReparsePathChain -Path (Join-Path $sourceRoot 'SKILL.md')
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
