from __future__ import annotations

import asyncio
from unittest.mock import patch

from app import model_runtime
from app.api import create_app
from app.metrics import (
    METRIC_SAMPLE_LIMIT,
    SeriesSummaryCache,
    SessionMetrics,
    SessionMetricsStore,
    cached_summary,
)


def route_endpoint(app, path: str):
    return next(route.endpoint for route in app.routes if getattr(route, "path", None) == path)


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


def test_session_metrics_basic_snapshot_omits_sample_arrays() -> None:
    metrics = SessionMetrics(client_id="ws-basic")
    metrics.asr_latency_ms.extend([10, 20, 30])

    snapshot = metrics.snapshot(include_samples=False)

    assert "asr_latency_ms" not in snapshot
    assert snapshot["sample_counts"]["asr_latency_ms"] == 3
    assert snapshot["summary"]["asr_latency_ms"]["p50"] == 20


def test_session_metrics_summary_cache_reuses_unchanged_samples() -> None:
    metrics = SessionMetrics(client_id="cached")
    metrics.asr_latency_ms.extend([100, 200, 300])

    first = metrics.summary()
    with patch("app.metrics.summary") as mocked:
        second = metrics.summary()
        mocked.assert_not_called()
    assert first is second


def test_session_metrics_summary_cache_invalidates_on_append() -> None:
    metrics = SessionMetrics(client_id="cached")
    metrics.asr_latency_ms.extend([100, 200, 300])
    first = metrics.summary()

    metrics.asr_latency_ms.append(400)
    second = metrics.summary()

    assert first is not second
    assert second["asr_latency_ms"]["max"] == 400


def test_session_metrics_store_recent_defaults_to_basic() -> None:
    store = SessionMetricsStore(max_sessions=2)
    store.create("a").asr_latency_ms.extend([1, 2, 3])
    store.create("b").asr_latency_ms.extend([4, 5])

    sessions = store.recent()

    assert len(sessions) == 2
    assert all("asr_latency_ms" not in session for session in sessions)
    by_id = {session["client_id"]: session for session in sessions}
    assert by_id["a"]["sample_counts"]["asr_latency_ms"] == 3
    assert by_id["b"]["sample_counts"]["asr_latency_ms"] == 2


def test_cached_summary_reuses_fingerprint() -> None:
    from collections import deque

    values = deque([1.0, 2.0, 3.0])
    cache = SeriesSummaryCache()

    first = cached_summary(values, cache)
    with patch("app.metrics.summary") as mocked:
        second = cached_summary(values, cache)
        mocked.assert_not_called()
    assert first is second


def test_metrics_endpoint_includes_timing_and_basic_payload(monkeypatch) -> None:
    monkeypatch.setattr(model_runtime.runtime_state, "is_ready", lambda: True)

    class FakeTranslationEngine:
        def cache_info(self, *, basic: bool = False) -> dict[str, object]:
            assert basic is True
            return {
                "hits": 1,
                "latency_ms": {"cache_hit": {"count": 0}},
            }

    monkeypatch.setattr("app.api.get_translation_engine", lambda: FakeTranslationEngine())
    monkeypatch.setattr(
        "app.api.get_inference_runtime",
        lambda: type(
            "Runtime",
            (),
            {"metrics_snapshot": lambda self: {"asr": {"active": 0}}},
        )(),
    )
    monkeypatch.setattr(
        "app.api.session_history_store",
        type("History", (), {"writer_stats": lambda self: {"queued": 0}})(),
    )

    async def run() -> None:
        app = create_app()
        metrics_endpoint = route_endpoint(app, "/metrics")
        payload = await metrics_endpoint()

        assert payload["ok"] is True
        assert "timing_ms" in payload
        assert payload["timing_ms"]["total"] >= 0
        assert payload["translation_cache"]["hits"] == 1

    asyncio.run(run())


def test_debug_sessions_includes_sample_arrays(monkeypatch) -> None:
    from app.metrics import session_metrics_store

    session_metrics_store.create("debug-session-metrics").asr_latency_ms.extend([7, 8])

    async def run() -> None:
        app = create_app()
        endpoint = route_endpoint(app, "/debug/sessions")
        payload = await endpoint()
        matching = [item for item in payload["sessions"] if item["client_id"] == "debug-session-metrics"]
        assert matching[0]["asr_latency_ms"] == [7, 8]

    asyncio.run(run())
