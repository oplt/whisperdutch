from __future__ import annotations

import asyncio
import os
import threading
import time
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI

from .asr import get_asr_engine
from .errors import map_exception
from .history import session_history_store
from .inference_runtime import get_inference_runtime
from .logger import get_logger, setup_logging
from .startup_status import write_startup_status
from .translator import get_translation_engine

logger = get_logger("startup")


@dataclass
class StartupTiming:
    live_at: float | None = None
    model_ready_at: float | None = None
    first_ws_ready_at: float | None = None
    phases_ms: dict[str, int] = field(default_factory=dict)
    strategy: str = "sequential"
    total_warmup_ms: int | None = None


@dataclass
class RuntimeState:
    startup_started_at: float = field(default_factory=time.time)
    ready: bool = False
    model_ready: bool = False
    phase: str = "created"
    last_error: dict[str, Any] | None = None
    warmed_up_at: float | None = None
    generation: int = 0
    startup_timing: StartupTiming = field(default_factory=StartupTiming)

    def begin_startup(self) -> int:
        self.generation += 1
        self.startup_started_at = time.time()
        self.ready = False
        self.model_ready = False
        self.phase = "starting"
        self.last_error = None
        self.warmed_up_at = None
        self.startup_timing = StartupTiming()
        return self.generation

    def is_ready(self) -> bool:
        return self.ready and self.model_ready and self.last_error is None

    def set_phase(self, generation: int, phase: str) -> bool:
        if generation != self.generation:
            return False
        self.phase = phase
        return True

    def stop(self, generation: int) -> None:
        if generation != self.generation:
            return
        self.generation += 1
        self.ready = False
        self.model_ready = False
        self.phase = "stopped"

    def mark_live(self) -> None:
        self.startup_timing.live_at = time.time()

    def record_ws_ready(self) -> None:
        if self.startup_timing.first_ws_ready_at is None:
            self.startup_timing.first_ws_ready_at = time.time()

    def startup_timing_snapshot(self) -> dict[str, Any]:
        timing = self.startup_timing

        def elapsed_ms(timestamp: float | None) -> float | None:
            if timestamp is None:
                return None
            return round((timestamp - self.startup_started_at) * 1000, 3)

        return {
            "strategy": timing.strategy,
            "process_started_at": self.startup_started_at,
            "live_ms": elapsed_ms(timing.live_at),
            "model_ready_ms": elapsed_ms(timing.model_ready_at),
            "first_ws_ready_ms": elapsed_ms(timing.first_ws_ready_at),
            "phases_ms": dict(timing.phases_ms),
            "total_warmup_ms": timing.total_warmup_ms,
        }


runtime_state = RuntimeState()


def _warmup_strategy() -> str:
    raw = os.getenv("STARTUP_WARMUP_STRATEGY", "sequential").strip().lower()
    if raw in {"parallel", "1", "true", "yes", "on"}:
        return "parallel"
    return "sequential"


def _write_phase(generation: int, phase: str, ok: bool = False, **extra: Any) -> bool:
    if not runtime_state.set_phase(generation, phase):
        return False
    write_startup_status(phase, ok, extra=extra or None)
    return True


def _record_phase_ms(generation: int, phase: str, started: float) -> float:
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    if generation == runtime_state.generation:
        runtime_state.startup_timing.phases_ms[phase] = elapsed_ms
    return time.perf_counter()


def _warmup_models_sequential(generation: int) -> None:
    phase_started = time.perf_counter()
    _write_phase(generation, "loading_asr")
    asr = get_asr_engine()
    phase_started = _record_phase_ms(generation, "loading_asr", phase_started)

    _write_phase(generation, "loading_translation")
    translator = get_translation_engine()
    phase_started = _record_phase_ms(generation, "loading_translation", phase_started)

    _write_phase(generation, "warming_asr")
    asr.warmup()
    phase_started = _record_phase_ms(generation, "warming_asr", phase_started)

    _write_phase(generation, "warming_translation")
    translator.warmup()
    _record_phase_ms(generation, "warming_translation", phase_started)


def _warmup_engine(engine: Any) -> None:
    engine.warmup()


def _warmup_models_parallel(generation: int) -> None:
    _write_phase(generation, "loading_models")
    load_started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="startup-load") as pool:
        asr_future = pool.submit(get_asr_engine)
        translation_future = pool.submit(get_translation_engine)
        asr = asr_future.result()
        translator = translation_future.result()
    if generation != runtime_state.generation:
        return
    _record_phase_ms(generation, "loading_models_parallel", load_started)

    _write_phase(generation, "warming_models")
    warm_started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="startup-warm") as pool:
        pool.submit(_warmup_engine, asr).result()
        pool.submit(_warmup_engine, translator).result()
    if generation != runtime_state.generation:
        return
    _record_phase_ms(generation, "warming_models_parallel", warm_started)


def warmup_models(generation: int) -> None:
    start = time.perf_counter()
    strategy = _warmup_strategy()
    runtime_state.startup_timing.strategy = strategy
    logger.info("startup_warmup_started generation=%s strategy=%s", generation, strategy)
    _write_phase(generation, "warming_models", extra={"strategy": strategy})
    try:
        if strategy == "parallel":
            _warmup_models_parallel(generation)
        else:
            _warmup_models_sequential(generation)
    except Exception as exc:
        if generation != runtime_state.generation:
            return
        safe_error = map_exception(exc).payload(debug_enabled=False)
        runtime_state.ready = False
        runtime_state.model_ready = False
        runtime_state.last_error = safe_error
        runtime_state.phase = "failed"
        write_startup_status(
            "failed",
            False,
            error=safe_error,
            extra=runtime_state.startup_timing_snapshot(),
        )
        logger.exception("startup_warmup_failed generation=%s", generation)
        return

    if generation != runtime_state.generation:
        logger.info("startup_warmup_discarded stale_generation=%s", generation)
        return

    elapsed = int((time.perf_counter() - start) * 1000)
    runtime_state.ready = True
    runtime_state.model_ready = True
    runtime_state.phase = "ready"
    runtime_state.last_error = None
    runtime_state.warmed_up_at = time.time()
    runtime_state.startup_timing.model_ready_at = runtime_state.warmed_up_at
    runtime_state.startup_timing.total_warmup_ms = elapsed
    timing = runtime_state.startup_timing_snapshot()
    write_startup_status(
        "ready",
        True,
        extra={
            "warmed_up_at": runtime_state.warmed_up_at,
            "elapsed_ms": elapsed,
            **timing,
        },
    )
    logger.info(
        "startup_warmup_completed elapsed_ms=%s generation=%s strategy=%s phases=%s",
        elapsed,
        generation,
        strategy,
        timing["phases_ms"],
    )


async def supervise_warmup(app: FastAPI, generation: int) -> None:
    thread = threading.Thread(
        target=warmup_models,
        args=(generation,),
        name=f"model-warmup-{generation}",
        daemon=True,
    )
    app.state.model_warmup_thread = thread
    thread.start()
    while thread.is_alive():
        await asyncio.sleep(0.05)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    generation = runtime_state.begin_startup()
    write_startup_status("starting", False)
    logger.info("application_startup generation=%s", generation)
    inference = get_inference_runtime()
    await inference.start()
    app.state.inference_runtime = inference
    session_history_store.start()
    warmup_task = asyncio.create_task(supervise_warmup(app, generation), name=f"model-warmup-supervisor-{generation}")
    app.state.model_warmup_task = warmup_task
    runtime_state.mark_live()
    write_startup_status("live", False, extra=runtime_state.startup_timing_snapshot())
    try:
        yield
    finally:
        runtime_state.stop(generation)
        await inference.stop()
        session_history_store.stop()
        if not warmup_task.done():
            warmup_task.cancel()
        try:
            await warmup_task
        except asyncio.CancelledError:
            pass
        logger.info("application_shutdown generation=%s", generation)
