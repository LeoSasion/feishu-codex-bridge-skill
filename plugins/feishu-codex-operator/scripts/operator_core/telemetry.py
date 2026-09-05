"""Content-free, monotonic phase timings for one admitted event."""

import json
import logging
import time


class EventTiming:
    def __init__(self, started: float | None = None) -> None:
        self.started = time.monotonic() if started is None else started
        self.previous = self.started
        self.phases: dict[str, float] = {}

    def mark(self, phase: str) -> None:
        now = time.monotonic()
        self.phases[phase] = round((now - self.previous) * 1000, 3)
        self.previous = now

    def finish(self, outcome: str) -> None:
        logging.getLogger("feishu-codex-operator").info(
            "event_timing %s",
            json.dumps({"outcome": outcome, "phases_ms": self.phases,
                        "total_ms": round((time.monotonic() - self.started) * 1000, 3)},
                       separators=(",", ":")),
        )
