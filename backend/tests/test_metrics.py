from __future__ import annotations

from app.metrics import METRIC_SAMPLE_LIMIT, SessionMetrics, SessionMetricsStore


def test_session_metrics_summary() -> None:
    metrics = SessionMetrics(client_id="ws-test")
    metrics.asr_latency_ms.extend([100, 200, 300])
    metrics.realtime_factors.extend([0.5, 1.0])
    snapshot = metrics.snapshot()
    assert snapshot["summary"]["asr_latency_ms"]["p50"] == 200
    assert snapshot["summary"]["realtime_factor"]["max"] == 1.0


def test_session_metrics_store_eviction() -> None:
    store = SessionMetricsStore(max_sessions=1)
    store.create("a")
    store.create("b")
    assert store.get("a") is None
    assert store.get("b") is not None


def test_session_metrics_samples_are_bounded() -> None:
    metrics = SessionMetrics(client_id="long-session")
    metrics.asr_latency_ms.extend(range(METRIC_SAMPLE_LIMIT + 25))

    snapshot = metrics.snapshot()

    assert len(snapshot["asr_latency_ms"]) == METRIC_SAMPLE_LIMIT
    assert snapshot["asr_latency_ms"][0] == 25


def test_session_metrics_preserve_lifetime_audio_total() -> None:
    metrics = SessionMetrics(client_id="long-session")
    metrics.audio_seconds_total = 1234.5
    metrics.audio_seconds.extend([1.0, 2.0])

    assert metrics.snapshot()["summary"]["audio_seconds_total"] == 1234.5
