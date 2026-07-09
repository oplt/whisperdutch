from __future__ import annotations

from app.metrics import SessionMetrics, SessionMetricsStore


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
