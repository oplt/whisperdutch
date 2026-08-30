from __future__ import annotations

import asyncio
import time

from app import asr, model_runtime, translator


def test_startup_timing_snapshot_reports_live_and_ready(monkeypatch) -> None:
    monkeypatch.setattr(model_runtime, "write_startup_status", lambda *_args, **_kwargs: {})

    class FakeAsrEngine:
        def warmup(self) -> None:
            time.sleep(0.01)

    class FakeTranslationEngine:
        def warmup(self) -> None:
            time.sleep(0.01)

    monkeypatch.setattr(asr, "TranscriptionEngine", lambda: FakeAsrEngine())
    monkeypatch.setattr(translator, "TranslationEngine", lambda: FakeTranslationEngine())
    asr.get_asr_engine.cache_clear()
    translator.get_translation_engine.cache_clear()

    async def run() -> None:
        from app.api import create_app

        app = create_app()
        async with model_runtime.lifespan(app):
            timing = model_runtime.runtime_state.startup_timing_snapshot()
            assert timing["live_ms"] is not None
            assert timing["live_ms"] >= 0
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and not model_runtime.runtime_state.is_ready():
                await asyncio.sleep(0.01)
            assert model_runtime.runtime_state.is_ready()
            ready_timing = model_runtime.runtime_state.startup_timing_snapshot()
            assert ready_timing["model_ready_ms"] is not None
            assert ready_timing["total_warmup_ms"] is not None
            assert ready_timing["phases_ms"]["loading_asr"] >= 0
            assert ready_timing["phases_ms"]["loading_translation"] >= 0
            assert ready_timing["strategy"] == "sequential"

    asyncio.run(run())


def test_parallel_warmup_strategy_records_parallel_phases(monkeypatch) -> None:
    monkeypatch.setenv("STARTUP_WARMUP_STRATEGY", "parallel")
    monkeypatch.setattr(model_runtime, "write_startup_status", lambda *_args, **_kwargs: {})

    class FakeAsrEngine:
        def warmup(self) -> None:
            return None

    class FakeTranslationEngine:
        def warmup(self) -> None:
            return None

    monkeypatch.setattr(asr, "TranscriptionEngine", FakeAsrEngine)
    monkeypatch.setattr(translator, "TranslationEngine", FakeTranslationEngine)
    asr.get_asr_engine.cache_clear()
    translator.get_translation_engine.cache_clear()

    generation = model_runtime.runtime_state.begin_startup()
    model_runtime.warmup_models(generation)

    timing = model_runtime.runtime_state.startup_timing_snapshot()
    assert timing["strategy"] == "parallel"
    assert "loading_models_parallel" in timing["phases_ms"]
    assert "warming_models_parallel" in timing["phases_ms"]
    assert model_runtime.runtime_state.is_ready()


def test_record_ws_ready_is_recorded_once() -> None:
    state = model_runtime.RuntimeState()
    state.begin_startup()
    state.mark_live()
    state.record_ws_ready()
    first = state.startup_timing.first_ws_ready_at
    time.sleep(0.01)
    state.record_ws_ready()
    assert state.startup_timing.first_ws_ready_at == first


def test_health_live_exposes_startup_timing(monkeypatch) -> None:
    monkeypatch.setattr(model_runtime.runtime_state, "startup_timing_snapshot", lambda: {"live_ms": 12.0})
    from app.api import create_app

    endpoint = next(
        route.endpoint for route in create_app().routes if getattr(route, "path", None) == "/health/live"
    )
    payload = endpoint()
    assert payload["startup_timing"]["live_ms"] == 12.0
