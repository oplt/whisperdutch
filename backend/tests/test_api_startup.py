from __future__ import annotations

import asyncio
import threading
import time

from app import model_runtime
from app.api import create_app
from fastapi import Response


def route_endpoint(app, path: str):
    return next(route.endpoint for route in app.routes if getattr(route, "path", None) == path)


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
