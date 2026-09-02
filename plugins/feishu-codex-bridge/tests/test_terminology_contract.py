from __future__ import annotations

import json
from pathlib import Path
import re
import unittest


SKILL_ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = frozenset(
    {".json", ".md", ".ps1", ".py", ".template", ".toml", ".txt", ".yaml", ".yml"}
)
LEGACY_BOUNDARIES = frozenset(
    {
        "scripts/bridge_core/legacy_identifiers.py",
        "scripts/install-feishu-codex-bridge.ps1",
        "tests/test_agents_rules.py",
        "tests/test_terminology_contract.py",
    }
)


class CanonicalTerminologyTests(unittest.TestCase):
    @staticmethod
    def _desktop_paths() -> tuple[str, ...]:
        inventory = json.loads(
            (SKILL_ROOT / "assets" / "release-inventory.json").read_text(
                encoding="utf-8"
            )
        )
        component = next(
            item for item in inventory["components"] if item["name"] == "desktop_bridge"
        )
        return tuple(str(item).replace("\\", "/") for item in component["paths"])

    def test_current_protocol_has_no_role_aliases(self) -> None:
        deprecated = (
            re.compile(r"(?i)\bexperimental\b|experimental[_-]"),
            re.compile(r"(?i)\b" + "lis" + r"tener\b"),
            re.compile(r"(?i)\b" + "gate" + r"way\b"),
            re.compile(r"(?i)\b" + "rou" + r"ter\b"),
            re.compile(r"(?i)\b" + "tic" + r"ket\b"),
            re.compile(r"(?i)(?:\b" + "wake" + r"\b|" + "wake" + r"_|_" + "wake" + r")"),
            re.compile(
                r"(?i)(?:"
                + "target_thread_id|submit_target_final|target_mcp|"
                + "final_return|responder_final|responder_mcp|return_token|responder_callback"
                + r")"
            ),
            re.compile(r"(?i)(?<!final_)callback_capability"),
            re.compile(
                r"(?i)(?:beeper_server|beeper-server|"
                r"\bbeeper\s+(?:server(?:\s+thread)?|coordinator)\b|"
                r"\bcontrol\s+(?:thread|task)\b|Bridge/controller)"
            ),
            re.compile(
                r"(?i)(?:\bresponder\s+MCP\b|\bMCP\s+final\b|"
                r"(?<!Final )\bcallback\s+(?:capability|transport)\b|respondered)"
            ),
            re.compile(
                r"(?i)(?:\broute_message\b|_validate_route_responder|"
                r"\bCodexAnswer\b|\bCodexSessionNotBound\b|"
                r"\bTurnHandle\b|\bThreadActivation\b)"
            ),
        )
        paths = self._desktop_paths()
        for relative in paths:
            self.assertFalse(
                any(
                    piece in relative.casefold()
                    for piece in ("experimental", "listener", "gateway", "router", "ticket")
                ),
                relative,
            )
            path = SKILL_ROOT / relative
            if relative in LEGACY_BOUNDARIES or path.suffix.casefold() not in TEXT_SUFFIXES:
                continue
            text = path.read_text(encoding="utf-8")
            for pattern in deprecated:
                self.assertIsNone(pattern.search(text), f"{relative}: {pattern.pattern}")
    def test_six_terms_are_bound_to_current_surfaces(self) -> None:
        terminology = (SKILL_ROOT / "references" / "terminology.md").read_text(
            encoding="utf-8"
        )
        for term in ("Bridge", "Dial", "Page", "Beeper", "Responder", "Final Callback"):
            self.assertIn(f"**{term}**", terminology)

        queue = (SKILL_ROOT / "scripts" / "bridge_core" / "beeper_queue.py").read_text(
            encoding="utf-8"
        )
        for marker in (
            "beeper_thread_id",
            "beeper_host_id",
            "responder_thread_id",
            "responder_host_id",
            "final_callback_capability",
            "final_callback_source",
            "final_answer",
            'QUEUE_ROOT_NAME = "beeper"',
        ):
            self.assertIn(marker, queue)

        client = (
            SKILL_ROOT / "scripts" / "bridge_core" / "beeper_client.py"
        ).read_text(encoding="utf-8")
        self.assertIn("def alert_responder(", client)
        for scoped_name in (
            "def beeper_status(",
            "def beeper_state(",
            "beeper_activator",
            "beeper_uri",
        ):
            self.assertNotIn(scoped_name, client)
        for direct_name in ("def status(", "def state(", "activator", "uri"):
            self.assertIn(direct_name, client)

        mcp = (SKILL_ROOT / "scripts" / "final_callback_mcp_server.py").read_text(
            encoding="utf-8"
        )
        for tool in (
            '"name": "claim_and_arm"',
            '"name": "submit_final_callback"',
            '"name": "finish_final_callback"',
            '"name": "fail_page"',
        ):
            self.assertIn(tool, mcp)


if __name__ == "__main__":
    unittest.main()
