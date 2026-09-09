"""Profile binding, stale evidence and preflight contracts; no live inference."""
from copy import deepcopy
from datetime import datetime, timezone
import unittest

from test_responses_tools import ROUTE
from operator_core.responses_profiles import CORE_CHECKS, contract_digest, inspect_profile, make_profile, preflight, profile_from_reports
from operator_core.responses_capabilities import RouterError


class ProfileTests(unittest.TestCase):
    def profile(self):
        when = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return make_profile("synthetic-profile", deepcopy(ROUTE), [
            {"case": case, "status": "passed", "checked_at": when, "cli_version": "0.153.4"}
            for case in sorted(CORE_CHECKS)])

    def test_exact_contract_and_version_bound_evidence(self):
        profile = self.profile()
        self.assertTrue(inspect_profile(profile, cli_version="0.153.4")["isolated_cli_verified"])
        self.assertFalse(inspect_profile(profile, cli_version="0.153.5")["isolated_cli_verified"])
        self.assertFalse(inspect_profile(profile)["desktop_verified"])
        for key, value in (("model", "changed"), ("api_base", "http://127.0.0.1:2/v1"),
                           ("reasoning_efforts", ["low"]), ("context_window", 16384)):
            changed = deepcopy(profile)
            changed["registration"][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(RouterError, "changed_since"):
                inspect_profile(changed)

    def test_labels_and_key_variable_are_portable_without_secret_read(self):
        profile = self.profile()
        profile["registration"].update(slug="api/renamed", display_name="Another label", api_key_env="UNSET_TEST_KEY")
        self.assertTrue(inspect_profile(profile)["isolated_cli_verified"])
        self.assertEqual(preflight(profile["registration"])["upstream_requests"], 0)

    def test_stale_source_and_missing_checks_are_never_verified(self):
        profile = self.profile()
        profile["adapter_sha256"] = "0" * 64
        self.assertFalse(inspect_profile(profile)["isolated_cli_verified"])
        profile = self.profile()
        profile["checks"].pop()
        self.assertFalse(inspect_profile(profile)["isolated_cli_verified"])

    def test_older_profiles_without_stop_checks_remain_incomplete(self):
        profile = self.profile()
        profile["checks"] = [r for r in profile["checks"] if r["case"] not in {"cli_error_stop", "cli_exit_stop"}]
        report = inspect_profile(profile)
        self.assertFalse(report["isolated_cli_verified"])
        self.assertEqual(report["missing_cli_checks"], ["cli_error_stop", "cli_exit_stop"])

    def test_unbound_and_changed_evaluators_cannot_verify_profiles(self):
        for change in ("unbound", "changed"):
            with self.subTest(change=change):
                profile = self.profile()
                if change == "unbound":
                    profile["version"] = 1
                    profile.pop("evaluator_sha256")
                else:
                    profile["evaluator_sha256"] = "0" * 64
                report = inspect_profile(profile)
                self.assertFalse(report["isolated_cli_verified"])
                self.assertEqual(report["evaluator_bound"], change != "unbound")
                self.assertEqual(report["evaluator_changed"], change == "changed")

    def test_profile_build_requires_matching_evaluator_for_every_cli_report(self):
        profile = self.profile()
        reports = [{**check, "synthetic_only": True, **{key: profile[key] for key in
                    ("contract_sha256", "adapter_sha256", "evaluator_sha256")}} for check in profile["checks"]]
        built = profile_from_reports("synthetic-build", profile["registration"], reports)
        self.assertTrue(inspect_profile(built)["isolated_cli_verified"])
        for value in (None, "0" * 64):
            changed = deepcopy(reports)
            if value is None:
                changed[0].pop("evaluator_sha256")
            else:
                changed[0]["evaluator_sha256"] = value
            with self.subTest(value=value), self.assertRaisesRegex(RouterError, "evaluator_mismatch"):
                profile_from_reports("synthetic-stale", profile["registration"], changed)

    def test_invalid_evidence_fields_dates_and_duplicates_refused(self):
        for mutate in (lambda p: p["checks"][0].update(prompt="private"),
                       lambda p: p["checks"][0].update(checked_at="9999-01-01T00:00:00Z"),
                       lambda p: p["checks"].append(deepcopy(p["checks"][0]))):
            profile = self.profile()
            mutate(profile)
            with self.assertRaises(RouterError):
                inspect_profile(profile)

    def test_report_policy_survives_profile_import_and_does_not_rewrite_failures(self):
        profile = self.profile()
        reports = [{**check, "synthetic_only": True, "final_text_policy": "marker_line_v1",
                    **{key: profile[key] for key in ("contract_sha256", "adapter_sha256", "evaluator_sha256")}}
                   for check in profile["checks"]]
        built = profile_from_reports("synthetic-compatible", profile["registration"], reports)
        self.assertTrue(all(r["final_text_policy"] == "marker_line_v1" for r in built["checks"]))
        first = built["checks"][0]
        built["checks"].append({**first, "checked_at": "2020-01-01T00:00:00Z", "status": "failed", "final_text_policy": "exact"})
        result = inspect_profile(built)
        self.assertEqual(result["recorded_failures"], 1)
        self.assertEqual(result["gates"][first["case"]]["final_text_policy"], "marker_line_v1")
        built["checks"][0]["final_text_policy"] = "arbitrary_trim"
        with self.assertRaises(RouterError):
            inspect_profile(built)

    def test_preflight_refuses_client_conflicts_and_unusable_effort(self):
        row = deepcopy(ROUTE)
        with self.assertRaises(RouterError):
            preflight(row, request={"input": "synthetic", "tools": [{"type": "web_search"}]})
        row["responses"]["tool_choice_by_reasoning"] = {"low": ["auto", "none"], "max": ["auto"]}
        with self.assertRaisesRegex(RouterError, "tool_choice_not_supported_at"):
            preflight(row)


if __name__ == "__main__":
    unittest.main()
