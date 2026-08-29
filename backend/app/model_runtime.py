from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI

from .asr import get_asr_engine
from .errors import map_exception
from .logger import get_logger, setup_logging
from .startup_status import write_startup_status
from .translator import get_translation_engine

logger = get_logger("startup")


@dataclass
class RuntimeState:
    startup_started_at: float = field(default_factory=time.time)
    ready: bool = False
    model_ready: bool = False
    phase: str = "created"
    last_error: dict[str, Any] | None = None
    warmed_up_at: float | None = None
    generation: int = 0

    def begin_startup(self) -> int:
        self.generation += 1
        self.startup_started_at = time.time()
        self.ready = False
        self.model_ready = False
        self.phase = "starting"
        self.last_error = None
        self.warmed_up_at = None
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


runtime_state = RuntimeState()


def _write_phase(generation: int, phase: str, ok: bool = False, **extra: Any) -> bool:
    if not runtime_state.set_phase(generation, phase):
        return False
    write_startup_status(phase, ok, extra=extra or None)
    return True


def warmup_models(generation: int) -> None:
    start = time.perf_counter()
    logger.info("startup_warmup_started generation=%s", generation)
    _write_phase(generation, "warming_models")
    try:
        _write_phase(generation, "loading_asr")
        asr = get_asr_engine()
        _write_phase(generation, "loading_translation")
        translator = get_translation_engine()
        _write_phase(generation, "warming_asr")
        asr.warmup()
        _write_phase(generation, "warming_translation")
        translator.warmup()
    except Exception as exc:
        if generation != runtime_state.generation:
            return
        safe_error = map_exception(exc).payload(debug_enabled=False)
        runtime_state.ready = False
        runtime_state.model_ready = False
        runtime_state.last_error = safe_error
        runtime_state.phase = "failed"
        write_startup_status("failed", False, error=safe_error)
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
    write_startup_status("ready", True, extra={"warmed_up_at": runtime_state.warmed_up_at, "elapsed_ms": elapsed})
    logger.info("startup_warmup_completed elapsed_ms=%s generation=%s", elapsed, generation)


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
    warmup_task = asyncio.create_task(supervise_warmup(app, generation), name=f"model-warmup-supervisor-{generation}")
    app.state.model_warmup_task = warmup_task
    try:
        yield
    finally:
        runtime_state.stop(generation)
        if not warmup_task.done():
            warmup_task.cancel()
        try:
            await warmup_task
        except asyncio.CancelledError:
            pass
        logger.info("application_shutdown generation=%s", generation)
