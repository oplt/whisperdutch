from __future__ import annotations

import statistics
import time
from collections import OrderedDict, deque
from dataclasses import asdict, dataclass, field
from typing import Any

METRIC_SAMPLE_LIMIT = 1000


@dataclass
class SessionMetrics:
    client_id: str
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    closed_at: float | None = None
    mode: str = "fast"
    audio_chunks: int = 0
    finalized_segments: int = 0
    partial_segments: int = 0
    partial_inferences: int = 0
    partial_suppressed: int = 0
    dropped_segments: int = 0
    merged_segments: int = 0
    translations_started: int = 0
    translations_cancelled: int = 0
    max_queue_depth: int = 0
    max_translation_queue_depth: int = 0
    reconnects: int = 0
    audio_seconds_total: float = 0.0
    audio_seconds: deque[float] = field(default_factory=lambda: deque(maxlen=METRIC_SAMPLE_LIMIT))
    asr_latency_ms: deque[int] = field(default_factory=lambda: deque(maxlen=METRIC_SAMPLE_LIMIT))
    mt_latency_ms: deque[int] = field(default_factory=lambda: deque(maxlen=METRIC_SAMPLE_LIMIT))
    total_latency_ms: deque[int] = field(default_factory=lambda: deque(maxlen=METRIC_SAMPLE_LIMIT))
    realtime_factors: deque[float] = field(default_factory=lambda: deque(maxlen=METRIC_SAMPLE_LIMIT))
    queue_delay_ms: deque[int] = field(default_factory=lambda: deque(maxlen=METRIC_SAMPLE_LIMIT))

    def touch(self) -> None:
        self.updated_at = time.time()

    def snapshot(self) -> dict[str, Any]:
        data = asdict(self)
        for name in (
            "audio_seconds",
            "asr_latency_ms",
            "mt_latency_ms",
            "total_latency_ms",
            "realtime_factors",
            "queue_delay_ms",
        ):
            data[name] = list(data[name])
        data["summary"] = {
            "audio_seconds_total": round(self.audio_seconds_total, 3),
            "asr_latency_ms": _summary(self.asr_latency_ms),
            "mt_latency_ms": _summary(self.mt_latency_ms),
            "total_latency_ms": _summary(self.total_latency_ms),
            "realtime_factor": _summary(self.realtime_factors),
            "queue_delay_ms": _summary(self.queue_delay_ms),
        }
        return data


class SessionMetricsStore:
    def __init__(self, max_sessions: int = 50) -> None:
        self.max_sessions = max_sessions
        self._sessions: OrderedDict[str, SessionMetrics] = OrderedDict()

    def create(self, client_id: str) -> SessionMetrics:
        metrics = SessionMetrics(client_id=client_id)
        self._sessions[client_id] = metrics
        self._sessions.move_to_end(client_id)
        while len(self._sessions) > self.max_sessions:
            self._sessions.popitem(last=False)
        return metrics

    def get(self, client_id: str) -> dict[str, Any] | None:
        metrics = self._sessions.get(client_id)
        if not metrics:
            return None
        self._sessions.move_to_end(client_id)
        return metrics.snapshot()

    def recent(self) -> list[dict[str, Any]]:
        return [metrics.snapshot() for metrics in reversed(self._sessions.values())]


def _summary(
    values: list[int] | list[float] | deque[int] | deque[float],
) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "p50": None, "p95": None, "max": None}
    sorted_values = sorted(values)
    return {
        "count": len(values),
        "p50": round(float(statistics.median(sorted_values)), 3),
        "p95": round(float(_percentile(sorted_values, 0.95)), 3),
        "max": round(float(max(sorted_values)), 3),
    }


def _percentile(values: list[int] | list[float], percentile: float) -> float:
    if len(values) == 1:
        return float(values[0])
    index = (len(values) - 1) * percentile
    lower = int(index)
    upper = min(lower + 1, len(values) - 1)
    weight = index - lower
    return float(values[lower]) * (1 - weight) + float(values[upper]) * weight


session_metrics_store = SessionMetricsStore()
