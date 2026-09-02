from __future__ import annotations

from pathlib import Path
import sys
import unittest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import external_fast_test_runner as fast  # noqa: E402


class ExternalFastDriverTests(unittest.TestCase):
    def test_fast_lane_is_unique_loadable_and_cannot_publish_release_evidence(self) -> None:
        smoke = tuple(fast.SMOKE_TEST_IDS)
        contract = tuple(fast.CONTRACT_TEST_IDS)
        fault = tuple(fast.REQUIRED_FAULT_TEST_IDS)
        selected = fast.lane_test_ids()

        self.assertEqual((12, 25, 19, 56), (len(smoke), len(contract), len(fault), len(selected)))
        self.assertEqual(56, len(set(selected)))

        loader = unittest.TestLoader()
        suite = loader.loadTestsFromNames(list(selected))
        self.assertFalse(loader.errors)
        loaded: list[str] = []

        def collect(item: unittest.TestSuite | unittest.TestCase) -> None:
            if isinstance(item, unittest.TestSuite):
                for child in item:
                    collect(child)
            else:
                loaded.append(item.id())

        collect(suite)
        self.assertEqual(list(selected), loaded)
        self.assertNotIn(
            "test_source_route_contract.SourceRouteContractTests."
            "test_reparse_plugin_root_and_marketplace_are_rejected",
            selected,
        )

        wrapper = (SCRIPTS / "invoke-external-fast-tests.ps1").read_text(encoding="utf-8")
        for marker in (
            "FEISHU_BRIDGE_EXTERNAL_TEST_RUNNER",
            "CODEX_BRIDGE_CHILD",
            "canonical-development",
            "development_source_eligible",
            "Assert-BridgeIdle",
            "beeper",
            "release_evidence = $false",
            "$startInfo.Environment['Path']",
            "$PSModuleAutoLoadingPreference = 'None'",
            "WaitForExit(10000)",
            "ReadToEndAsync()",
            "[Threading.Tasks.Task]::WaitAll(",
            "$process.Kill($true)",
            "$process.WaitForExit(30000)",
            "$process.Dispose()",
            "bridge_status_precondition",
            "beeper_queue_precondition",
            "$bridge.health_snapshot.queue_counts.control_sending",
            "source_route_precondition",
            "process_ancestry_depth_exceeded",
            "[IO.FileAttributes]::ReparsePoint",
            "Remove-Item -LiteralPath $resolvedTemp",
        ):
            self.assertIn(marker, wrapper)
        self.assertNotIn("$output = @(& $Executable", wrapper)
        self.assertEqual(3, wrapper.count("Invoke-JsonCommand -Executable"))
        self.assertNotIn("$route.development_eligible", wrapper)
        self.assertNotIn("evidence_path", wrapper)
        self.assertNotIn("evidence_sha256", wrapper)

        runner = (SCRIPTS / "external_fast_test_runner.py").read_text(encoding="utf-8")
        self.assertIn("contextlib.redirect_stdout", runner)
        self.assertIn("contextlib.redirect_stderr", runner)
        self.assertIn('"unexpected_test_output": unexpected_test_output', runner)


if __name__ == "__main__":
    unittest.main()
