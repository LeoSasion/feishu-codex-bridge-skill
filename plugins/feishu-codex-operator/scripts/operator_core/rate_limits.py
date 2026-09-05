"""Adaptive, account-wide ChatGPT rate-limit checks for ordinary dispatch."""

from __future__ import annotations

from dataclasses import dataclass
from concurrent.futures import Future
from datetime import datetime, timezone
from pathlib import Path
import threading
import time
from typing import Any, Callable

from .app_server import AppServerError, AppServerSession
from .beeper_relay import (
    BEEPER_FALLBACK_MODEL,
    BEEPER_PRIMARY_REASONING_EFFORT,
    BEEPER_PRIMARY_MODEL,
    beeper_reasoning_effort,
    discover_codex_executable,
)
from .config import OperatorConfig


_UNAVAILABLE_RETRY_MESSAGES = 3
_UNAVAILABLE_RETRY_SECONDS = 5 * 60


class RateLimitReadError(RuntimeError):
    """A read-only account rate-limit snapshot could not be obtained."""


@dataclass(frozen=True)
class RateLimitWindow:
    used_percent: int
    window_duration_minutes: int | None
    resets_at: int | None

    @property
    def remaining_percent(self) -> int:
        return max(0, min(100, 100 - self.used_percent))


@dataclass(frozen=True)
class RateLimitBucket:
    limit_id: str
    limit_name: str
    primary: RateLimitWindow | None
    secondary: RateLimitWindow | None
    reached_type: str
    spend_control_reached: bool

    @property
    def windows(self) -> tuple[RateLimitWindow, ...]:
        return tuple(item for item in (self.primary, self.secondary) if item is not None)

    @property
    def controlling_window(self) -> RateLimitWindow | None:
        windows = self.windows
        if not windows:
            return None
        return min(
            windows,
            key=lambda item: (
                item.remaining_percent,
                item.resets_at if item.resets_at is not None else 2**63 - 1,
            ),
        )

    @property
    def remaining_percent(self) -> int | None:
        window = self.controlling_window
        return window.remaining_percent if window is not None else None

    @property
    def explicitly_reached(self) -> bool:
        return bool(self.reached_type) or self.spend_control_reached

    @property
    def reset_at(self) -> int | None:
        window = self.controlling_window
        return window.resets_at if window is not None else None


@dataclass(frozen=True)
class AccountRateLimitSnapshot:
    buckets: dict[str, RateLimitBucket]
    primary_limit_id: str
    account_id: str

    @property
    def primary_bucket(self) -> RateLimitBucket:
        return self.buckets[self.primary_limit_id]

    def bucket_for_model(self, model: str) -> RateLimitBucket | None:
        expected = model.strip().casefold()
        for bucket in self.buckets.values():
            if bucket.limit_name.casefold() == expected:
                return bucket
        return None

    @property
    def beeper_bucket(self) -> RateLimitBucket | None:
        return self.bucket_for_model(BEEPER_PRIMARY_MODEL)

    @property
    def beeper_model(self) -> str:
        bucket = self.beeper_bucket
        if bucket is not None and (
            bucket.explicitly_reached or bucket.remaining_percent == 0
        ):
            return BEEPER_FALLBACK_MODEL
        return BEEPER_PRIMARY_MODEL

    @property
    def monitoring_buckets(self) -> tuple[RateLimitBucket, ...]:
        beeper_bucket = self.beeper_bucket
        if beeper_bucket is None or beeper_bucket.limit_id == self.primary_limit_id:
            return (self.primary_bucket,)
        return (self.primary_bucket, beeper_bucket)


@dataclass(frozen=True)
class RateLimitDecision:
    blocked: bool
    refreshed: bool
    status: str
    limit_id: str = ""
    remaining_percent: int | None = None
    window_duration_minutes: int | None = None
    reset_at: int | None = None
    reached_type: str = ""
    beeper_model: str = BEEPER_PRIMARY_MODEL
    beeper_reasoning_effort: str = BEEPER_PRIMARY_REASONING_EFFORT
    beeper_limit_id: str = ""
    beeper_remaining_percent: int | None = None
    beeper_window_duration_minutes: int | None = None
    beeper_reset_at: int | None = None


def _integer(value: object, *, minimum: int = 0) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        return None
    return value


def _window(raw: object) -> RateLimitWindow | None:
    if not isinstance(raw, dict):
        return None
    used = _integer(raw.get("usedPercent"))
    if used is None:
        return None
    return RateLimitWindow(
        used_percent=used,
        window_duration_minutes=_integer(raw.get("windowDurationMins")),
        resets_at=_integer(raw.get("resetsAt"), minimum=1),
    )


def _bucket(raw: object, fallback_id: str) -> RateLimitBucket | None:
    if not isinstance(raw, dict):
        return None
    limit_id = str(raw.get("limitId") or fallback_id).strip()
    if not limit_id:
        return None
    primary = _window(raw.get("primary"))
    secondary = _window(raw.get("secondary"))
    if primary is None and secondary is None:
        return None
    return RateLimitBucket(
        limit_id=limit_id,
        limit_name=str(raw.get("limitName") or "").strip()[:120],
        primary=primary,
        secondary=secondary,
        reached_type=str(raw.get("rateLimitReachedType") or "").strip()[:80],
        spend_control_reached=raw.get("spendControlReached") is True,
    )


def parse_account_rate_limits(result: object) -> AccountRateLimitSnapshot:
    if not isinstance(result, dict):
        raise RateLimitReadError("account rate-limit response is invalid")
    buckets: dict[str, RateLimitBucket] = {}
    raw_by_id = result.get("rateLimitsByLimitId")
    if isinstance(raw_by_id, dict):
        for key, raw in raw_by_id.items():
            parsed = _bucket(raw, str(key))
            if parsed is not None:
                buckets[parsed.limit_id] = parsed

    legacy = _bucket(result.get("rateLimits"), "codex")
    if legacy is not None:
        buckets.setdefault(legacy.limit_id, legacy)
    if not buckets:
        raise RateLimitReadError("account rate-limit response has no usable bucket")

    if legacy is not None:
        primary_limit_id = legacy.limit_id
    elif "codex" in buckets:
        primary_limit_id = "codex"
    else:
        primary_limit_id = next(iter(buckets))
    return AccountRateLimitSnapshot(
        buckets=buckets,
        primary_limit_id=primary_limit_id,
        account_id=str(result.get("accountId") or "").strip(),
    )


class AppServerRateLimitReader:
    """Fetch one account snapshot without starting, reading, or resuming a task."""

    def __init__(self, config: OperatorConfig, *, executable: Path | None = None) -> None:
        self.config = config
        self._executable = executable

    @property
    def executable(self) -> Path:
        if self._executable is None:
            self._executable = discover_codex_executable(self.config.codex_executable)
        return self._executable

    def read(self) -> AccountRateLimitSnapshot:
        try:
            with AppServerSession(
                self.executable, self.config.app_server_timeout_seconds
            ) as api:
                result = api.request("account/rateLimits/read")
        except (AppServerError, OSError) as exc:
            raise RateLimitReadError("account rate-limit read failed") from exc
        return parse_account_rate_limits(result)


class AdaptiveRateLimitGuard:
    """Share one adaptive cache across every Feishu scope in this process."""

    def __init__(
        self,
        config: OperatorConfig,
        *,
        reader: Callable[[], AccountRateLimitSnapshot] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._reader = reader or AppServerRateLimitReader(config).read
        self._monotonic = monotonic
        self._lock = threading.RLock()
        self._beeper_model_override = str(
            getattr(config, "beeper_model_override", "") or ""
        ).strip()
        self._beeper_reasoning_effort_override = str(
            getattr(config, "beeper_reasoning_effort_override", "") or ""
        ).strip()
        self._snapshot: AccountRateLimitSnapshot | None = None
        self._last_refresh_at = 0.0
        self._messages_since_refresh = 0
        self._last_refresh_failed = False
        self._refresh_future: Future | None = None
        self._refresh_thread: threading.Thread | None = None

    @staticmethod
    def _cadence(snapshot: AccountRateLimitSnapshot) -> tuple[int, int]:
        buckets = snapshot.monitoring_buckets
        remaining_values = [
            bucket.remaining_percent
            for bucket in buckets
            if bucket.remaining_percent is not None
        ]
        remaining = min(remaining_values) if remaining_values else None
        if any(bucket.explicitly_reached for bucket in buckets) or remaining is None:
            return 1, 0
        if remaining > 50:
            return 20, 30 * 60
        if remaining >= 20:
            return 10, 15 * 60
        if remaining > 5:
            return 3, 5 * 60
        return 1, 0

    def _decision(self, *, refreshed: bool, allow_stale_block: bool) -> RateLimitDecision:
        snapshot = self._snapshot
        if snapshot is None:
            return RateLimitDecision(
                False,
                refreshed,
                "unavailable",
                beeper_model=self._beeper_model_override or BEEPER_PRIMARY_MODEL,
                beeper_reasoning_effort=beeper_reasoning_effort(
                    self._beeper_model_override or BEEPER_PRIMARY_MODEL,
                    primary_override=self._beeper_reasoning_effort_override,
                ),
            )
        bucket = snapshot.primary_bucket
        beeper_model = self._beeper_model_override or snapshot.beeper_model
        # Always retain the Spark-specific bucket as quota telemetry even when
        # a bounded diagnostic explicitly selects Luna.
        beeper_bucket = snapshot.beeper_bucket
        blocked = bucket.explicitly_reached and allow_stale_block and not self._last_refresh_failed
        return RateLimitDecision(
            blocked=blocked,
            refreshed=refreshed,
            status="fresh" if refreshed else ("stale" if self._last_refresh_failed else "cached"),
            limit_id=bucket.limit_id,
            remaining_percent=bucket.remaining_percent,
            window_duration_minutes=(
                bucket.controlling_window.window_duration_minutes
                if bucket.controlling_window is not None
                else None
            ),
            reset_at=bucket.reset_at,
            reached_type=bucket.reached_type,
            beeper_model=beeper_model,
            beeper_reasoning_effort=beeper_reasoning_effort(
                beeper_model,
                primary_override=self._beeper_reasoning_effort_override,
            ),
            beeper_limit_id=(beeper_bucket.limit_id if beeper_bucket is not None else ""),
            beeper_remaining_percent=(
                beeper_bucket.remaining_percent if beeper_bucket is not None else None
            ),
            beeper_window_duration_minutes=(
                beeper_bucket.controlling_window.window_duration_minutes
                if beeper_bucket is not None
                and beeper_bucket.controlling_window is not None
                else None
            ),
            beeper_reset_at=(beeper_bucket.reset_at if beeper_bucket is not None else None),
        )

    def _read_and_publish(self, future: Future) -> None:
        try:
            snapshot = self._reader()
        except Exception:
            snapshot = None
        with self._lock:
            valid = isinstance(snapshot, AccountRateLimitSnapshot)
            if valid:
                self._snapshot = snapshot
            self._last_refresh_failed = not valid
            self._last_refresh_at = self._monotonic()
            decision = self._decision(refreshed=valid, allow_stale_block=valid)
            future.set_result(decision)

    def _start_refresh_locked(self) -> Future:
        if self._refresh_future is not None and not self._refresh_future.done():
            return self._refresh_future
        future: Future = Future()
        self._refresh_future = future
        # Count messages arriving DURING the read toward the next refresh.
        self._messages_since_refresh = 0
        self._refresh_thread = threading.Thread(
            target=self._read_and_publish, args=(future,), name="quota-refresh", daemon=True,
        )
        self._refresh_thread.start()
        return future

    def prime(self) -> RateLimitDecision:
        """Fetch once at Operator startup; failure never blocks startup."""

        with self._lock:
            future = self._start_refresh_locked()
        return future.result()

    def before_dispatch(self, *, background: bool = True) -> RateLimitDecision:
        """Refresh only when the account-wide request/time cadence is due."""

        with self._lock:
            self._messages_since_refresh += 1
            if self._snapshot is None:
                age = max(0.0, self._monotonic() - self._last_refresh_at)
                due = (
                    self._last_refresh_at <= 0
                    or self._messages_since_refresh >= _UNAVAILABLE_RETRY_MESSAGES
                    or age >= _UNAVAILABLE_RETRY_SECONDS
                )
                urgent = False
            else:
                messages, max_age = self._cadence(self._snapshot)
                age = max(0.0, self._monotonic() - self._last_refresh_at)
                due = self._messages_since_refresh >= messages or (max_age > 0 and age >= max_age)
                urgent = messages == 1
            future = self._start_refresh_locked() if due else None
            decision = self._decision(refreshed=False, allow_stale_block=not due)
        if future is not None and (urgent or not background):
            return future.result()
        return decision

    def refresh_after_failure(self, *, background: bool = False) -> RateLimitDecision:
        """Refresh after a queue signal or uncertain lifecycle expiry."""

        with self._lock:
            future = self._start_refresh_locked()
            decision = self._decision(refreshed=False, allow_stale_block=False)
        return decision if background else future.result()

    def close(self) -> None:
        with self._lock:
            thread = self._refresh_thread
        if thread is not None:
            thread.join()

    def health_summary(self) -> dict[str, object]:
        with self._lock:
            decision = self._decision(refreshed=False, allow_stale_block=True)
            return {
                "status": decision.status,
                "limit_id": decision.limit_id or None,
                "remaining_percent": decision.remaining_percent,
                "window_duration_minutes": decision.window_duration_minutes,
                "reset_at": decision.reset_at,
                "beeper_model": decision.beeper_model,
                "beeper_reasoning_effort": decision.beeper_reasoning_effort,
                "beeper_limit_id": decision.beeper_limit_id or None,
                "beeper_remaining_percent": decision.beeper_remaining_percent,
                "beeper_window_duration_minutes": decision.beeper_window_duration_minutes,
                "beeper_reset_at": decision.beeper_reset_at,
            }


def format_reset_time(reset_at: int | None) -> str:
    if reset_at is None:
        return ""
    try:
        rendered = (
            datetime.fromtimestamp(reset_at, timezone.utc)
            .astimezone()
            .strftime("%Y-%m-%d %H:%M:%S %z")
        )
    except (OSError, OverflowError, ValueError):
        return ""
    if len(rendered) >= 5:
        rendered = rendered[:-2] + ":" + rendered[-2:]
    return rendered


def blocked_before_dispatch_reply(decision: RateLimitDecision) -> str:
    reset = format_reset_time(decision.reset_at)
    when = f"，预计于 {reset}（本机时区）重置" if reset else ""
    return (
        f"Codex 当前账户额度已达到限制{when}。本条消息没有发送给 Beeper，"
        "也不会自动重跑。额度恢复后请重新发送。"
    )


def uncertain_timeout_reply(decision: RateLimitDecision) -> str:
    if not decision.blocked:
        return (
            "Responder 可能已经开始，但没有在时限内返回 Final Callback。"
            "为避免重复操作，本条消息不会自动重跑。"
        )
    reset = format_reset_time(decision.reset_at)
    when = f"，预计于 {reset}（本机时区）重置" if reset else ""
    return (
        "Responder 可能已经开始，但没有在时限内返回 Final Callback；随后检测到 "
        f"Codex 当前账户额度已达到限制{when}。为避免重复操作，本条消息不会自动重跑。"
    )


def queue_limit_reply(decision: RateLimitDecision) -> str:
    if decision.blocked:
        return blocked_before_dispatch_reply(decision)
    return (
        "Codex 在接受 Beeper 前报告了额度或速率限制。本条消息未开始，"
        "也不会自动重跑；请在 Codex 中检查用量状态后重新发送。"
    )
