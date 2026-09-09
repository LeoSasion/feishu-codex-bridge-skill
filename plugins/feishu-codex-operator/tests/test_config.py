from pathlib import Path
import os
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from operator_core.config import load_config  # noqa: E402


class ConfigTests(unittest.TestCase):
    def test_minimal_relay_defaults(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = load_config()
        self.assertEqual(300, config.unknown_status_timeout_seconds)
        self.assertEqual(20, config.callback_grace_seconds)
        self.assertEqual(168, config.callback_retention_hours)
        self.assertEqual(20, config.app_server_timeout_seconds)
        self.assertEqual("", config.codex_executable)
        self.assertEqual("", config.beeper_thread_id)
        self.assertEqual("gpt-5.6-luna", config.beeper_model_override)
        self.assertEqual("", config.beeper_reasoning_effort_override)
        self.assertEqual("", config.beeper_prompt_language_override)
        self.assertEqual("callbacks.sqlite3", config.callback_db.name)

    def test_beeper_model_selection_is_bounded(self) -> None:
        for model in ("", "beeper", "gpt-5.3-codex-spark", "gpt-5.6-luna"):
            with self.subTest(model=model), patch.dict(
                os.environ,
                {"CODEX_OPERATOR_BEEPER_MODEL": model},
                clear=True,
            ):
                config = load_config()
                self.assertEqual(model or "gpt-5.6-luna", config.beeper_model_override)

        with patch.dict(
            os.environ,
            {"CODEX_OPERATOR_BEEPER_MODEL": "unsupported"},
            clear=True,
        ):
            with self.assertRaises(ValueError):
                load_config()

    def test_spark_high_diagnostic_requires_explicit_spark(self) -> None:
        with patch.dict(
            os.environ,
            {
                "CODEX_OPERATOR_BEEPER_MODEL": "gpt-5.3-codex-spark",
                "CODEX_OPERATOR_BEEPER_REASONING_EFFORT": "high",
            },
            clear=True,
        ):
            config = load_config()
        self.assertEqual("high", config.beeper_reasoning_effort_override)

        for model in ("", "beeper", "gpt-5.6-luna"):
            with self.subTest(model=model), patch.dict(
                os.environ,
                {
                    "CODEX_OPERATOR_BEEPER_MODEL": model,
                    "CODEX_OPERATOR_BEEPER_REASONING_EFFORT": "high",
                },
                clear=True,
            ):
                with self.assertRaises(ValueError):
                    load_config()

        with patch.dict(
            os.environ,
            {
                "CODEX_OPERATOR_BEEPER_MODEL": "gpt-5.3-codex-spark",
                "CODEX_OPERATOR_BEEPER_REASONING_EFFORT": "low",
            },
            clear=True,
        ):
            config = load_config()
        self.assertEqual("low", config.beeper_reasoning_effort_override)

    def test_beeper_prompt_language_preserves_chinese_fallback(self) -> None:
        for language in ("en", "zh-cn"):
            with self.subTest(language=language), patch.dict(
                os.environ,
                {"CODEX_OPERATOR_BEEPER_PROMPT_LANGUAGE": language},
                clear=True,
            ):
                config = load_config()
                self.assertEqual(language, config.beeper_prompt_language_override)

        with patch.dict(
            os.environ,
            {"CODEX_OPERATOR_BEEPER_PROMPT_LANGUAGE": "unsupported"},
            clear=True,
        ):
            with self.assertRaises(ValueError):
                load_config()

    def test_invalid_current_integer_is_rejected(self) -> None:
        with patch.dict(
            os.environ,
            {"CODEX_OPERATOR_APP_SERVER_TIMEOUT": "not-an-integer"},
            clear=True,
        ):
            with self.assertRaises(ValueError):
                load_config()

    def test_invalid_beeper_task_id_is_rejected(self) -> None:
        with patch.dict(
            os.environ,
            {"CODEX_OPERATOR_BEEPER_THREAD_ID": "not-a-task"},
            clear=True,
        ):
            with self.assertRaises(ValueError):
                load_config()

if __name__ == "__main__":
    unittest.main()
