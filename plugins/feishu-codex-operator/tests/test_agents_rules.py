from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class AgentsRulesTests(unittest.TestCase):
    def test_root_rules_and_release_mirror_are_byte_identical(self) -> None:
        self.assertEqual(
            (ROOT.parents[1] / "AGENTS.md").read_bytes(),
            (ROOT / "assets" / "AGENTS.feishu-codex-operator.md").read_bytes(),
        )

    def test_rules_admit_only_minimal_beeper_relay_and_read_only_catalog(self) -> None:
        rules = (ROOT / "assets" / "AGENTS.feishu-codex-operator.md").read_text(encoding="utf-8")
        self.assertIn("`gpt-5.3-codex-spark` and `medium` reasoning by default", rules)
        self.assertIn("Spark with `low` reasoning is forbidden in normal selection", rules)
        self.assertIn("CODEX_OPERATOR_BEEPER_REASONING_EFFORT=low", rules)
        self.assertIn("Spark always receives concise, structured English", rules)
        self.assertIn("Chinese control template selectable for Luna only", rules)
        self.assertIn("CODEX_OPERATOR_BEEPER_PROMPT_LANGUAGE=zh-cn", rules)
        self.assertIn("CODEX_OPERATOR_BEEPER_REASONING_EFFORT=low` or `high", rules)
        self.assertIn("exactly one same-event Luna/low queue attempt", rules)
        self.assertIn("mcp__codex_app__send_message_to_thread` exactly once", rules)
        self.assertIn("codex://threads/<exact Beeper UUID>", rules)
        self.assertIn("30-minute wake lease", rules)
        self.assertIn("within 30 seconds", rules)
        self.assertIn("Concurrent requests coalesce wake\n  signals", rules)
        self.assertIn("not a\n  Desktop foreground, residency, wake-state", rules)
        self.assertIn("Never send a wake-up signal\n  to, resume, or otherwise take control of a Responder", rules)
        self.assertIn("thread/read` with\n  `includeTurns=false", rules)
        self.assertIn("request_id` is correlation data", rules)
        self.assertIn("old Page/capability/claim route is permanently non-executable", rules)
        self.assertIn("`account/rateLimits/read`", rules)
        self.assertIn("refresh after 20 messages or 30 minutes above\n  50%", rules)
        self.assertIn("Only a fresh server-classified reached limit", rules)
        self.assertIn("must\n  not block dispatch", rules)
        self.assertIn("`thread/turns/list` with `limit=20`", rules)
        self.assertIn("`itemsView=notLoaded`", rules)
        self.assertIn("has no execution deadline", rules)
        self.assertIn("callback-only grace of 20 seconds", rules)
        self.assertIn("`interrupted` without `completedAt` are unknown", rules)


if __name__ == "__main__":
    unittest.main()
