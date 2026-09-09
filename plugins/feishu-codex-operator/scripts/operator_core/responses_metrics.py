"""Fixed-cardinality, process-local timing. No request or response material."""

from contextlib import contextmanager
from contextvars import ContextVar
from time import perf_counter

CURRENT_METRICS = ContextVar("responses_metrics", default=None)
STAGES = frozenset({"request_adaptation", "response_adaptation", "upstream_headers",
                    "upstream_first_byte", "upstream_body", "request_total"})
OUTCOMES = frozenset({"completed", "failed", "cancelled"})


class ResponsesMetrics:
    def __init__(self):
        self.stages = {}
        self.outcomes = {key: 0 for key in sorted(OUTCOMES)}
        self.active = 0

    def observe(self, stage, seconds):
        if stage not in STAGES:
            raise ValueError("unknown_timing_stage")
        elapsed = max(0, round(seconds * 1000, 3))
        row = self.stages.setdefault(stage, {"count": 0, "total_ms": 0, "min_ms": elapsed,
                                            "max_ms": elapsed, "last_ms": elapsed})
        row.update(count=row["count"] + 1, total_ms=round(row["total_ms"] + elapsed, 3),
                   min_ms=min(row["min_ms"], elapsed), max_ms=max(row["max_ms"], elapsed), last_ms=elapsed)

    @contextmanager
    def measure(self, stage):
        begin = perf_counter()
        try:
            yield
        finally:
            self.observe(stage, perf_counter() - begin)

    async def chunks(self, source, begin):
        first = None
        try:
            async for chunk in source:
                if first is None:
                    first = perf_counter()
                    self.observe("upstream_first_byte", first - begin)
                yield chunk
        finally:
            if first is not None:
                self.observe("upstream_body", perf_counter() - first)

    def snapshot(self):
        return {"active": self.active, "outcomes": dict(self.outcomes),
                "stages": {key: dict(value) for key, value in self.stages.items()},
                "first_byte_is_not_first_token": True, "tool_execution_measured_by_client": True}


@contextmanager
def measure_current(stage):
    metrics = CURRENT_METRICS.get()
    if metrics is None:
        yield
    else:
        with metrics.measure(stage):
            yield
