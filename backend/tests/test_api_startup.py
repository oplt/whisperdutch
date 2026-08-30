from __future__ import annotations

import asyncio
import threading
import time

from app import asr, model_runtime, translator
from app.api import create_app
from fastapi import Response


def route_endpoint(app, path: str):
    return next(route.endpoint for route in app.routes if getattr(route, "path", None) == path)


def test_metrics_during_startup_does_not_initialize_translation_engine(monkeypatch) -> None:
    release_warmup = threading.Event()
    created: list[str] = []

    class SlowTranslationEngine:
        def __init__(self) -> None:
            created.append("translation")
            time.sleep(0.2)

        def cache_info(self) -> dict[str, object]:
            return {"hits": 0}

    class SlowAsrEngine:
        def __init__(self) -> None:
            created.append("asr")
            time.sleep(0.2)

        def warmup(self) -> None:
            return None

        def info(self) -> dict[str, object]:
            return {}

    def blocked_warmup(generation: int) -> None:
        release_warmup.wait(timeout=2)
        if generation == model_runtime.runtime_state.generation:
            model_runtime.runtime_state.model_ready = True
            model_runtime.runtime_state.ready = True
            model_runtime.runtime_state.phase = "ready"

    monkeypatch.setattr(model_runtime, "warmup_models", blocked_warmup)
    monkeypatch.setattr(model_runtime, "write_startup_status", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(translator, "TranslationEngine", SlowTranslationEngine)
    monkeypatch.setattr(asr, "TranscriptionEngine", SlowAsrEngine)
    asr.get_asr_engine.cache_clear()
    translator.get_translation_engine.cache_clear()

    async def run() -> None:
        app = create_app()
        metrics_endpoint = route_endpoint(app, "/metrics")
        async with model_runtime.lifespan(app):
            payload = await metrics_endpoint()
            assert payload["translation_cache"] == {"status": "warming"}
            assert created == []
            release_warmup.set()

    asyncio.run(run())


def test_concurrent_engine_access_creates_one_instance_each(monkeypatch) -> None:
    created: list[str] = []
    creation_lock = threading.Lock()
    start_barrier = threading.Barrier(4)

    class SlowAsrEngine:
        def __init__(self) -> None:
            with creation_lock:
                created.append("asr")
            time.sleep(0.15)

        def warmup(self) -> None:
            return None

        def info(self) -> dict[str, object]:
            return {}

    class SlowTranslationEngine:
        def __init__(self) -> None:
            with creation_lock:
                created.append("translation")
            time.sleep(0.15)

        def cache_info(self) -> dict[str, object]:
            return {"hits": 0}

        def warmup(self) -> None:
            return None

    monkeypatch.setattr(asr, "TranscriptionEngine", SlowAsrEngine)
    monkeypatch.setattr(translator, "TranslationEngine", SlowTranslationEngine)
    asr.get_asr_engine.cache_clear()
    translator.get_translation_engine.cache_clear()

    def worker(kind: str) -> None:
        start_barrier.wait(timeout=2)
        if kind == "asr":
            asr.get_asr_engine()
        else:
            translator.get_translation_engine()

    threads = [
        threading.Thread(target=worker, args=("asr",), daemon=True),
        threading.Thread(target=worker, args=("asr",), daemon=True),
        threading.Thread(target=worker, args=("translation",), daemon=True),
        threading.Thread(target=worker, args=("translation",), daemon=True),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert created.count("asr") == 1
    assert created.count("translation") == 1


def test_live_endpoint_serves_while_models_warm(monkeypatch) -> None:
    warmup_started = threading.Event()
    release_warmup = threading.Event()

    def controlled_warmup(generation: int) -> None:
        warmup_started.set()
        release_warmup.wait(timeout=2)
        if generation == model_runtime.runtime_state.generation:
            model_runtime.runtime_state.model_ready = True
            model_runtime.runtime_state.ready = True
            model_runtime.runtime_state.phase = "ready"

    monkeypatch.setattr(model_runtime, "warmup_models", controlled_warmup)
    monkeypatch.setattr(model_runtime, "write_startup_status", lambda *_args, **_kwargs: {})

    async def run() -> None:
        app = create_app()
        async with model_runtime.lifespan(app):
            live_endpoint = route_endpoint(app, "/health/live")
            ready_endpoint = route_endpoint(app, "/health/ready")
            debug_endpoint = route_endpoint(app, "/debug/device")
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline and not warmup_started.is_set():
                await asyncio.sleep(0.01)
            assert warmup_started.is_set()
            started = time.perf_counter()
            live = live_endpoint()
            elapsed_ms = (time.perf_counter() - started) * 1000
            loading_response = Response()
            ready_while_loading = ready_endpoint(loading_response)

            assert live["live"] is True
            assert elapsed_ms < 250
            assert loading_response.status_code == 503
            assert ready_while_loading["phase"] == "starting"
            assert debug_endpoint()["asr"] is None

            release_warmup.set()
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline and not model_runtime.runtime_state.is_ready():
                await asyncio.sleep(0.01)
            ready_response = Response()
            ready = ready_endpoint(ready_response)
            assert ready_response.status_code == 200
            assert ready["ready"] is True

    asyncio.run(run())


def test_websocket_is_rejected_until_models_are_ready(monkeypatch) -> None:
    release_warmup = threading.Event()

    def blocked_warmup(_generation: int) -> None:
        release_warmup.wait(timeout=2)

    monkeypatch.setattr(model_runtime, "warmup_models", blocked_warmup)
    monkeypatch.setattr(model_runtime, "write_startup_status", lambda *_args, **_kwargs: {})

    class FakeWebSocket:
        def __init__(self) -> None:
            self.closed: tuple[int, str] | None = None

        async def close(self, code: int, reason: str) -> None:
            self.closed = (code, reason)

    async def run() -> None:
        app = create_app()
        websocket_endpoint = route_endpoint(app, "/ws/subtitles")
        async with model_runtime.lifespan(app):
            websocket = FakeWebSocket()
            await websocket_endpoint(websocket)
            assert websocket.closed is not None
            assert websocket.closed[0] == 1013
            assert "not ready" in websocket.closed[1].lower()
            release_warmup.set()

    asyncio.run(run())


def test_stale_warmup_cannot_mark_new_generation_ready() -> None:
    state = model_runtime.RuntimeState()
    first = state.begin_startup()
    second = state.begin_startup()

    assert state.set_phase(first, "ready") is False
    assert state.set_phase(second, "loading_asr") is True
    assert state.phase == "loading_asr"


def test_languages_endpoint_exposes_multilingual_catalog(monkeypatch) -> None:
    monkeypatch.setattr(model_runtime.runtime_state, "is_ready", lambda: False)
    endpoint = route_endpoint(create_app(), "/api/languages")

    payload = endpoint()

    assert payload["default_source"] == "nl"
    assert payload["default_target"] == "en"
    assert {language["code"] for language in payload["languages"]} >= {"nl", "en", "de", "fr", "ar", "ja"}
    assert payload["translation"] is None
