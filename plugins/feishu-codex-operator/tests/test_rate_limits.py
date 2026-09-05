from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import operator_core.rate_limits as rate_module  # noqa: E402


def result_for(
    *,
    used: int,
    reached: str | None = None,
    secondary_used: int | None = None,
    account_id: str = "account-1",
    spark_used: int = 1,
    spark_reached: str | None = None,
    spark_name: str = "GPT-5.3-Codex-Spark",
) -> dict[str, object]:
    bucket: dict[str, object] = {
        "limitId": "codex",
        "primary": {
            "usedPercent": used,
            "windowDurationMins": 10080,
            "resetsAt": 1_800_000_000,
        },
        "secondary": None,
        "rateLimitReachedType": reached,
    }
    if secondary_used is not None:
        bucket["secondary"] = {
            "usedPercent": secondary_used,
            "windowDurationMins": 300,
            "resetsAt": 1_799_000_000,
        }
    return {
        "accountId": account_id,
        "rateLimits": bucket,
        "rateLimitsByLimitId": {
            "codex": bucket,
            "codex_bengalfox": {
                "limitId": "codex_bengalfox",
                "limitName": spark_name,
                "primary": {
                    "usedPercent": spark_used,
                    "windowDurationMins": 300,
                    "resetsAt": 1_799_500_000,
                },
                "rateLimitReachedType": spark_reached,
            },
        },
    }


class FakeClock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value


class RateLimitTests(unittest.TestCase):
    def test_parser_preserves_limit_buckets_but_uses_primary_for_dispatch(self) -> None:
        snapshot = rate_module.parse_account_rate_limits(result_for(used=1))

        self.assertEqual({"codex", "codex_bengalfox"}, set(snapshot.buckets))
        self.assertEqual("codex", snapshot.primary_limit_id)
        self.assertEqual(99, snapshot.primary_bucket.remaining_percent)
        self.assertFalse(snapshot.primary_bucket.explicitly_reached)
        self.assertEqual("codex_bengalfox", snapshot.beeper_bucket.limit_id)
        self.assertEqual(rate_module.BEEPER_PRIMARY_MODEL, snapshot.beeper_model)
        guard = rate_module.AdaptiveRateLimitGuard(
            SimpleNamespace(), reader=lambda: snapshot, monotonic=FakeClock()
        )
        self.assertEqual(
            "medium",
            guard.prime().beeper_reasoning_effort,
        )

    def test_spark_exhaustion_preselects_luna_without_blocking_account(self) -> None:
        for values in (
            {"spark_used": 100},
            {"spark_used": 50, "spark_reached": "usageLimitExceeded"},
        ):
            with self.subTest(values=values):
                snapshot = rate_module.parse_account_rate_limits(
                    result_for(used=1, **values)
                )
                guard = rate_module.AdaptiveRateLimitGuard(
                    SimpleNamespace(), reader=lambda: snapshot, monotonic=FakeClock()
                )
                decision = guard.prime()
                self.assertFalse(decision.blocked)
                self.assertEqual(rate_module.BEEPER_FALLBACK_MODEL, decision.beeper_model)
                self.assertEqual("low", decision.beeper_reasoning_effort)
                self.assertEqual("codex_bengalfox", decision.beeper_limit_id)

    def test_explicit_luna_override_bypasses_spark_selection(self) -> None:
        snapshot = rate_module.parse_account_rate_limits(
            result_for(used=1, spark_used=1)
        )
        guard = rate_module.AdaptiveRateLimitGuard(
            SimpleNamespace(beeper_model_override=rate_module.BEEPER_FALLBACK_MODEL),
            reader=lambda: snapshot,
            monotonic=FakeClock(),
        )

        decision = guard.prime()

        self.assertEqual(rate_module.BEEPER_FALLBACK_MODEL, decision.beeper_model)
        self.assertEqual("codex_bengalfox", decision.beeper_limit_id)
        self.assertEqual(99, decision.beeper_remaining_percent)

    def test_explicit_spark_high_override_is_visible_in_health(self) -> None:
        snapshot = rate_module.parse_account_rate_limits(
            result_for(used=1, spark_used=1)
        )
        guard = rate_module.AdaptiveRateLimitGuard(
            SimpleNamespace(
                beeper_model_override=rate_module.BEEPER_PRIMARY_MODEL,
                beeper_reasoning_effort_override="high",
            ),
            reader=lambda: snapshot,
            monotonic=FakeClock(),
        )

        guard.prime()

        health = guard.health_summary()
        self.assertEqual(rate_module.BEEPER_PRIMARY_MODEL, health["beeper_model"])
        self.assertEqual("high", health["beeper_reasoning_effort"])

    def test_unknown_spark_bucket_defaults_to_spark(self) -> None:
        snapshot = rate_module.parse_account_rate_limits(
            result_for(used=1, spark_used=100, spark_name="Another quota")
        )
        self.assertIsNone(snapshot.beeper_bucket)
        self.assertEqual(rate_module.BEEPER_PRIMARY_MODEL, snapshot.beeper_model)

    def test_percentage_reset_and_duration_use_the_same_tightest_window(self) -> None:
        snapshot = rate_module.parse_account_rate_limits(
            result_for(used=10, secondary_used=70)
        )
        bucket = snapshot.primary_bucket

        self.assertEqual(30, bucket.remaining_percent)
        self.assertEqual(300, bucket.controlling_window.window_duration_minutes)
        self.assertEqual(1_799_000_000, bucket.reset_at)

        guard = rate_module.AdaptiveRateLimitGuard(
            SimpleNamespace(), reader=lambda: snapshot, monotonic=FakeClock()
        )
        decision = guard.prime()
        self.assertEqual(30, decision.remaining_percent)
        self.assertEqual(300, decision.window_duration_minutes)
        self.assertEqual(1_799_000_000, decision.reset_at)

    def test_high_remaining_refreshes_on_twentieth_message(self) -> None:
        clock = FakeClock()
        calls = 0

        def read():
            nonlocal calls
            calls += 1
            return rate_module.parse_account_rate_limits(result_for(used=1))

        guard = rate_module.AdaptiveRateLimitGuard(
            SimpleNamespace(), reader=read, monotonic=clock
        )
        self.assertTrue(guard.prime().refreshed)
        for _ in range(19):
            self.assertFalse(guard.before_dispatch(background=False).refreshed)
        self.assertTrue(guard.before_dispatch(background=False).refreshed)
        self.assertEqual(2, calls)

    def test_gradient_message_cadence(self) -> None:
        cases = ((99, 20), (51, 20), (50, 10), (20, 10), (19, 3), (6, 3), (5, 1))
        for remaining, expected_messages in cases:
            with self.subTest(remaining=remaining):
                snapshot = rate_module.parse_account_rate_limits(
                    result_for(used=100 - remaining)
                )
                self.assertEqual(
                    expected_messages,
                    rate_module.AdaptiveRateLimitGuard._cadence(snapshot)[0],
                )

    def test_spark_bucket_controls_refresh_cadence_when_more_constrained(self) -> None:
        snapshot = rate_module.parse_account_rate_limits(
            result_for(used=1, spark_used=95)
        )
        self.assertEqual((1, 0), rate_module.AdaptiveRateLimitGuard._cadence(snapshot))

    def test_elapsed_time_refreshes_even_without_enough_messages(self) -> None:
        clock = FakeClock()
        calls = 0

        def read():
            nonlocal calls
            calls += 1
            return rate_module.parse_account_rate_limits(result_for(used=1))

        guard = rate_module.AdaptiveRateLimitGuard(
            SimpleNamespace(), reader=read, monotonic=clock
        )
        guard.prime()
        clock.value += 30 * 60
        self.assertTrue(guard.before_dispatch(background=False).refreshed)
        self.assertEqual(2, calls)

    def test_explicit_reached_state_blocks_only_after_a_successful_read(self) -> None:
        snapshot = rate_module.parse_account_rate_limits(
            result_for(used=100, reached="usageLimitExceeded")
        )
        calls = 0

        def read():
            nonlocal calls
            calls += 1
            if calls == 1:
                return snapshot
            raise rate_module.RateLimitReadError("offline")

        guard = rate_module.AdaptiveRateLimitGuard(
            SimpleNamespace(), reader=read, monotonic=FakeClock()
        )

        primed = guard.prime()
        self.assertTrue(primed.blocked)
        self.assertIn("没有发送给 Beeper", rate_module.blocked_before_dispatch_reply(primed))
        stale = guard.before_dispatch(background=False)
        self.assertFalse(stale.blocked)
        self.assertEqual("stale", stale.status)

    def test_failed_refresh_keeps_the_percentage_cadence(self) -> None:
        clock = FakeClock()
        calls = 0

        def read():
            nonlocal calls
            calls += 1
            if calls == 1:
                return rate_module.parse_account_rate_limits(result_for(used=1))
            raise rate_module.RateLimitReadError("offline")

        guard = rate_module.AdaptiveRateLimitGuard(
            SimpleNamespace(), reader=read, monotonic=clock
        )
        guard.prime()
        for _ in range(20):
            guard.before_dispatch(background=False)
        self.assertEqual(2, calls)
        for _ in range(19):
            decision = guard.before_dispatch(background=False)
            self.assertFalse(decision.refreshed)
            self.assertEqual("stale", decision.status)
        self.assertEqual(2, calls)
        guard.before_dispatch(background=False)
        self.assertEqual(3, calls)

    def test_unexpected_initial_failure_is_fail_open_and_rate_limited(self) -> None:
        clock = FakeClock()
        calls = 0

        def read():
            nonlocal calls
            calls += 1
            raise RuntimeError("unexpected local failure")

        guard = rate_module.AdaptiveRateLimitGuard(
            SimpleNamespace(), reader=read, monotonic=clock
        )
        primed = guard.prime()
        self.assertFalse(primed.blocked)
        self.assertEqual("unavailable", primed.status)
        self.assertFalse(guard.before_dispatch(background=False).blocked)
        self.assertFalse(guard.before_dispatch(background=False).blocked)
        self.assertEqual(1, calls)
        self.assertFalse(guard.before_dispatch(background=False).blocked)
        self.assertEqual(2, calls)

    def test_app_server_reader_calls_only_account_rate_limits(self) -> None:
        requests: list[tuple[str, object]] = []

        class FakeSession:
            def __init__(self, *_args, **_kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def request(self, method, params=None):
                requests.append((method, params))
                return result_for(used=25)

        config = SimpleNamespace(
            app_server_timeout_seconds=20,
            codex_executable="",
        )
        with patch.object(rate_module, "AppServerSession", FakeSession):
            snapshot = rate_module.AppServerRateLimitReader(
                config, executable=Path("C:/fake/codex.exe")
            ).read()

        self.assertEqual([("account/rateLimits/read", None)], requests)
        self.assertEqual(75, snapshot.primary_bucket.remaining_percent)

    def test_health_exposes_sanitized_beeper_selection(self) -> None:
        snapshot = rate_module.parse_account_rate_limits(
            result_for(used=10, spark_used=100)
        )
        guard = rate_module.AdaptiveRateLimitGuard(
            SimpleNamespace(), reader=lambda: snapshot, monotonic=FakeClock()
        )
        guard.prime()

        health = guard.health_summary()
        self.assertEqual(rate_module.BEEPER_FALLBACK_MODEL, health["beeper_model"])
        self.assertEqual("low", health["beeper_reasoning_effort"])
        self.assertEqual("codex_bengalfox", health["beeper_limit_id"])
        self.assertEqual(0, health["beeper_remaining_percent"])
        self.assertNotIn("account_id", health)


if __name__ == "__main__":
    unittest.main()
