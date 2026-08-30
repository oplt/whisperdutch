from __future__ import annotations

import statistics
import time
from collections import OrderedDict, deque
from dataclasses import asdict, dataclass, field
from typing import Any

METRIC_SAMPLE_LIMIT = 1000

SAMPLE_FIELD_NAMES = (
    "audio_seconds",
    "asr_latency_ms",
    "mt_latency_ms",
    "total_latency_ms",
    "realtime_factors",
    "queue_delay_ms",
)


@dataclass
class SeriesSummaryCache:
    fingerprint: tuple[Any, ...] = ()
    summary: dict[str, Any] = field(
        default_factory=lambda: {"count": 0, "p50": None, "p95": None, "max": None}
    )


def deque_fingerprint(values: deque[Any]) -> tuple[Any, ...]:
    if not values:
        return (0,)
    return (len(values), values[0], values[-1])


def summary(
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


def cached_summary(
    values: list[int] | list[float] | deque[int] | deque[float],
    cache: SeriesSummaryCache,
) -> dict[str, float | int | None]:
    fingerprint = deque_fingerprint(values) if isinstance(values, deque) else (len(values), values[0], values[-1])
    if fingerprint == cache.fingerprint:
        return cache.summary
    cache.fingerprint = fingerprint
    cache.summary = summary(values)
    return cache.summary


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
    audio_gap_resets: int = 0
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
    _summary_cache_key: tuple[Any, ...] | None = field(default=None, init=False, repr=False, compare=False)
    _summary_cache: dict[str, Any] | None = field(default=None, init=False, repr=False, compare=False)

    def touch(self) -> None:
        self.updated_at = time.time()

    def _samples_fingerprint(self) -> tuple[Any, ...]:
        return tuple(deque_fingerprint(getattr(self, name)) for name in SAMPLE_FIELD_NAMES)

    def _build_summary(self) -> dict[str, Any]:
        return {
            "audio_seconds_total": round(self.audio_seconds_total, 3),
            "asr_latency_ms": summary(self.asr_latency_ms),
            "mt_latency_ms": summary(self.mt_latency_ms),
            "total_latency_ms": summary(self.total_latency_ms),
            "realtime_factor": summary(self.realtime_factors),
            "queue_delay_ms": summary(self.queue_delay_ms),
        }

    def summary(self) -> dict[str, Any]:
        fingerprint = self._samples_fingerprint()
        if fingerprint == self._summary_cache_key and self._summary_cache is not None:
            return self._summary_cache
        self._summary_cache_key = fingerprint
        self._summary_cache = self._build_summary()
        return self._summary_cache

    def _scalar_fields(self) -> dict[str, Any]:
        return {
            "client_id": self.client_id,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "closed_at": self.closed_at,
            "mode": self.mode,
            "audio_chunks": self.audio_chunks,
            "finalized_segments": self.finalized_segments,
            "partial_segments": self.partial_segments,
            "partial_inferences": self.partial_inferences,
            "partial_suppressed": self.partial_suppressed,
            "dropped_segments": self.dropped_segments,
            "audio_gap_resets": self.audio_gap_resets,
            "merged_segments": self.merged_segments,
            "translations_started": self.translations_started,
            "translations_cancelled": self.translations_cancelled,
            "max_queue_depth": self.max_queue_depth,
            "max_translation_queue_depth": self.max_translation_queue_depth,
            "reconnects": self.reconnects,
            "audio_seconds_total": self.audio_seconds_total,
        }

    def snapshot(self, *, include_samples: bool = True) -> dict[str, Any]:
        summary_block = self.summary()
        if not include_samples:
            return {
                **self._scalar_fields(),
                "summary": summary_block,
                "sample_counts": {name: len(getattr(self, name)) for name in SAMPLE_FIELD_NAMES},
            }
        data = asdict(self)
        for name in SAMPLE_FIELD_NAMES:
            data[name] = list(data[name])
        for cache_name in ("_summary_cache_key", "_summary_cache"):
            data.pop(cache_name, None)
        data["summary"] = summary_block
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

    def get(self, client_id: str, *, include_samples: bool = True) -> dict[str, Any] | None:
        metrics = self._sessions.get(client_id)
        if not metrics:
            return None
        self._sessions.move_to_end(client_id)
        return metrics.snapshot(include_samples=include_samples)

    def recent(self, *, include_samples: bool = False) -> list[dict[str, Any]]:
        return [
            metrics.snapshot(include_samples=include_samples)
            for metrics in reversed(self._sessions.values())
        ]


def _percentile(values: list[int] | list[float], percentile: float) -> float:
    if len(values) == 1:
        return float(values[0])
    index = (len(values) - 1) * percentile
    lower = int(index)
    upper = min(lower + 1, len(values) - 1)
    weight = index - lower
    return float(values[lower]) * (1 - weight) + float(values[upper]) * weight


session_metrics_store = SessionMetricsStore()
