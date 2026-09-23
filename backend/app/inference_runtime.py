from __future__ import annotations

import asyncio
import os
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import IntEnum
from threading import RLock
from typing import Any, TypeVar

from .logger import get_logger
from .metrics import SeriesSummaryCache, cached_summary

logger = get_logger("inference")

T = TypeVar("T")

METRIC_SAMPLE_LIMIT = 1000


class AsrPriority(IntEnum):
    FINAL = 0
    FLUSH = 1
    PARTIAL = 2


class InferenceRejectedError(Exception):
    """Raised when a low-priority inference job cannot be admitted."""


@dataclass
class _AsrJob:
    priority: AsrPriority
    session_id: str
    fn: Callable[..., Any]
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    future: asyncio.Future[Any]
    enqueued_at: float
    is_stale: Callable[[], bool] | None = None


@dataclass
class _TranslationJob:
    session_id: str
    fn: Callable[..., Any]
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    future: asyncio.Future[Any]
    enqueued_at: float


@dataclass
class InferenceMetrics:
    asr_active: int = 0
    asr_queue_depth: int = 0
    asr_partials_discarded: int = 0
    translation_active: int = 0
    translation_queue_depth: int = 0
    asr_queue_wait_ms: deque[float] = field(default_factory=lambda: deque(maxlen=METRIC_SAMPLE_LIMIT))
    asr_final_queue_wait_ms: deque[float] = field(default_factory=lambda: deque(maxlen=METRIC_SAMPLE_LIMIT))
    asr_partial_queue_wait_ms: deque[float] = field(default_factory=lambda: deque(maxlen=METRIC_SAMPLE_LIMIT))
    translation_queue_wait_ms: deque[float] = field(default_factory=lambda: deque(maxlen=METRIC_SAMPLE_LIMIT))
    translation_batches: int = 0
    translation_batch_requests: int = 0
    translation_batch_collect_wait_ms: deque[float] = field(default_factory=lambda: deque(maxlen=METRIC_SAMPLE_LIMIT))
    _asr_queue_wait_cache: SeriesSummaryCache = field(default_factory=SeriesSummaryCache, init=False, repr=False)
    _asr_final_queue_wait_cache: SeriesSummaryCache = field(default_factory=SeriesSummaryCache, init=False, repr=False)
    _asr_partial_queue_wait_cache: SeriesSummaryCache = field(default_factory=SeriesSummaryCache, init=False, repr=False)
    _translation_queue_wait_cache: SeriesSummaryCache = field(default_factory=SeriesSummaryCache, init=False, repr=False)
    _translation_batch_collect_wait_cache: SeriesSummaryCache = field(
        default_factory=SeriesSummaryCache,
        init=False,
        repr=False,
    )

    def snapshot(self, runtime: InferenceRuntime) -> dict[str, Any]:
        return {
            "asr": {
                "active": self.asr_active,
                "queue_depth": self.asr_queue_depth,
                "partials_discarded_before_inference": self.asr_partials_discarded,
                "queue_wait_ms": cached_summary(self.asr_queue_wait_ms, self._asr_queue_wait_cache),
                "final_queue_wait_ms": cached_summary(self.asr_final_queue_wait_ms, self._asr_final_queue_wait_cache),
                "partial_queue_wait_ms": cached_summary(
                    self.asr_partial_queue_wait_ms,
                    self._asr_partial_queue_wait_cache,
                ),
            },
            "translation": {
                "active": self.translation_active,
                "queue_depth": self.translation_queue_depth,
                "queue_wait_ms": cached_summary(self.translation_queue_wait_ms, self._translation_queue_wait_cache),
                "cross_session_batches": self.translation_batches,
                "cross_session_batch_requests": self.translation_batch_requests,
                "batch_collect_wait_ms": cached_summary(
                    self.translation_batch_collect_wait_ms,
                    self._translation_batch_collect_wait_cache,
                ),
            },
            "executor": {
                "asr_saturation": self.asr_active >= max(1, runtime.asr_max_concurrent),
                "translation_saturation": self.translation_active >= max(1, runtime.translation_max_concurrent),
            },
        }


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _translation_batch_collect_ms() -> float:
    # Default 0: do not artificially delay the first waiting translation job.
    # Operators can raise this (e.g. 2) to coalesce cross-session bursts.
    raw = os.getenv("TRANSLATION_BATCH_COLLECT_MS", "0").strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 0.0


def _translation_batch_max_requests() -> int:
    return _env_int("TRANSLATION_BATCH_MAX_REQUESTS", 8)


def _translation_batch_max_chars() -> int:
    return _env_int("TRANSLATION_BATCH_MAX_CHARS", 2400)


def _default_asr_concurrency() -> int:
    return 1


def _default_translation_concurrency() -> int:
    return 1


class InferenceRuntime:
    def __init__(self) -> None:
        self.asr_max_concurrent = _env_int("INFERENCE_ASR_MAX_CONCURRENT", _default_asr_concurrency())
        self.translation_max_concurrent = _env_int(
            "INFERENCE_TRANSLATION_MAX_CONCURRENT",
            _default_translation_concurrency(),
        )
        self.asr_max_pending = _env_int("INFERENCE_ASR_MAX_PENDING", 16)
        self.translation_max_pending = _env_int("INFERENCE_TRANSLATION_MAX_PENDING", 32)
        self._asr_executor = ThreadPoolExecutor(
            max_workers=self.asr_max_concurrent,
            thread_name_prefix="asr-inference",
        )
        self._translation_executor = ThreadPoolExecutor(
            max_workers=self.translation_max_concurrent,
            thread_name_prefix="translation-inference",
        )
        self._asr_pending: dict[AsrPriority, dict[str, deque[_AsrJob]]] = {
            priority: {} for priority in AsrPriority
        }
        self._asr_session_rr: deque[str] = deque()
        self._asr_pending_count = 0
        self._asr_active = 0
        self._translation_pending: dict[str, deque[_TranslationJob]] = {}
        self._translation_session_rr: deque[str] = deque()
        self._translation_pending_count = 0
        self._translation_active = 0
        self._asr_lock = asyncio.Lock()
        self._translation_lock = asyncio.Lock()
        self._asr_notify = asyncio.Condition(self._asr_lock)
        self._translation_notify = asyncio.Condition(self._translation_lock)
        self._running = False
        self._inline = False
        self._asr_scheduler_task: asyncio.Task[None] | None = None
        self._translation_scheduler_task: asyncio.Task[None] | None = None
        self._asr_job_tasks: set[asyncio.Task[Any]] = set()
        self._translation_job_tasks: set[asyncio.Task[Any]] = set()
        self.metrics = InferenceMetrics()
        self._init_lock = RLock()

    def metrics_snapshot(self) -> dict[str, Any]:
        return self.metrics.snapshot(self)

    @property
    def asr_queue_depth(self) -> int:
        return self._asr_pending_count

    @property
    def translation_queue_depth(self) -> int:
        return self._translation_pending_count

    def set_inline(self, enabled: bool) -> None:
        self._inline = enabled

    def _ensure_executors(self) -> None:
        if self._asr_executor._shutdown:  # type: ignore[attr-defined]
            self._asr_executor = ThreadPoolExecutor(
                max_workers=self.asr_max_concurrent,
                thread_name_prefix="asr-inference",
            )
        if self._translation_executor._shutdown:  # type: ignore[attr-defined]
            self._translation_executor = ThreadPoolExecutor(
                max_workers=self.translation_max_concurrent,
                thread_name_prefix="translation-inference",
            )

    async def start(self) -> None:
        with self._init_lock:
            if self._running:
                return
            self._ensure_executors()
            self._running = True
            self._asr_scheduler_task = asyncio.create_task(self._asr_scheduler(), name="inference-asr-scheduler")
            self._translation_scheduler_task = asyncio.create_task(
                self._translation_scheduler(),
                name="inference-translation-scheduler",
            )
            logger.info(
                "inference_runtime_started asr_workers=%s translation_workers=%s asr_pending=%s translation_pending=%s",
                self.asr_max_concurrent,
                self.translation_max_concurrent,
                self.asr_max_pending,
                self.translation_max_pending,
            )

    def _reject_pending_jobs(self, jobs: list[Any], reason: str) -> None:
        error = InferenceRejectedError(reason)
        for job in jobs:
            if not job.future.done():
                job.future.set_exception(error)

    def _drain_asr_pending_locked(self) -> list[_AsrJob]:
        drained: list[_AsrJob] = []
        for priority in AsrPriority:
            for session_id, queue in list(self._asr_pending[priority].items()):
                drained.extend(queue)
                queue.clear()
                del self._asr_pending[priority][session_id]
        self._asr_pending_count = 0
        self.metrics.asr_queue_depth = 0
        self._asr_session_rr.clear()
        return drained

    def _drain_translation_pending_locked(self) -> list[_TranslationJob]:
        drained: list[_TranslationJob] = []
        for session_id, queue in list(self._translation_pending.items()):
            drained.extend(queue)
            queue.clear()
            del self._translation_pending[session_id]
        self._translation_pending_count = 0
        self.metrics.translation_queue_depth = 0
        self._translation_session_rr.clear()
        return drained

    def _release_asr_session_if_idle_locked(self, session_id: str) -> None:
        for priority in AsrPriority:
            queue = self._asr_pending[priority].get(session_id)
            if queue:
                return
        try:
            self._asr_session_rr.remove(session_id)
        except ValueError:
            return

    def _release_translation_session_if_idle_locked(self, session_id: str) -> None:
        queue = self._translation_pending.get(session_id)
        if queue:
            return
        try:
            self._translation_session_rr.remove(session_id)
        except ValueError:
            return

    async def stop(self) -> None:
        with self._init_lock:
            if not self._running:
                return
            self._running = False

        async with self._asr_lock:
            self._asr_notify.notify_all()
            pending_asr = self._drain_asr_pending_locked()
        async with self._translation_lock:
            self._translation_notify.notify_all()
            pending_mt = self._drain_translation_pending_locked()

        self._reject_pending_jobs(pending_asr, "inference runtime stopped")
        self._reject_pending_jobs(pending_mt, "inference runtime stopped")

        for task in (self._asr_scheduler_task, self._translation_scheduler_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._asr_scheduler_task = None
        self._translation_scheduler_task = None

        active_tasks = list(self._asr_job_tasks | self._translation_job_tasks)
        for task in active_tasks:
            task.cancel()
        if active_tasks:
            await asyncio.gather(*active_tasks, return_exceptions=True)
        self._asr_job_tasks.clear()
        self._translation_job_tasks.clear()

        self._asr_executor.shutdown(wait=False, cancel_futures=True)
        self._translation_executor.shutdown(wait=False, cancel_futures=True)
        logger.info("inference_runtime_stopped")

    async def run_asr(
        self,
        priority: AsrPriority,
        fn: Callable[..., Any],
        /,
        *args: Any,
        session_id: str,
        is_stale: Callable[[], bool] | None = None,
        **kwargs: Any,
    ) -> T:
        if self._inline:
            if is_stale and is_stale():
                self.metrics.asr_partials_discarded += 1
                raise InferenceRejectedError("stale partial")
            return fn(*args, **kwargs)

        if not self._running:
            raise InferenceRejectedError("inference runtime stopped")

        loop = asyncio.get_running_loop()
        future: asyncio.Future[T] = loop.create_future()
        job = _AsrJob(
            priority=priority,
            session_id=session_id,
            fn=fn,
            args=args,
            kwargs=kwargs,
            future=future,
            enqueued_at=time.perf_counter(),
            is_stale=is_stale,
        )
        async with self._asr_lock:
            if not self._running:
                raise InferenceRejectedError("inference runtime stopped")
            # Bound all ASR work (partial + final). Overload is explicit rejection,
            # not silent loss of speech already past admission.
            if self._asr_pending_count >= self.asr_max_pending:
                if priority == AsrPriority.PARTIAL:
                    self.metrics.asr_partials_discarded += 1
                raise InferenceRejectedError("global ASR queue full")
            if session_id not in self._asr_session_rr:
                self._asr_session_rr.append(session_id)
            session_queue = self._asr_pending[priority].setdefault(session_id, deque())
            session_queue.append(job)
            self._asr_pending_count += 1
            self.metrics.asr_queue_depth = self._asr_pending_count
            self._asr_notify.notify_all()
        return await future

    async def run_translation(
        self,
        fn: Callable[..., Any],
        /,
        *args: Any,
        session_id: str,
        **kwargs: Any,
    ) -> T:
        if self._inline:
            return fn(*args, **kwargs)

        if not self._running:
            raise InferenceRejectedError("inference runtime stopped")

        loop = asyncio.get_running_loop()
        future: asyncio.Future[T] = loop.create_future()
        job = _TranslationJob(
            session_id=session_id,
            fn=fn,
            args=args,
            kwargs=kwargs,
            future=future,
            enqueued_at=time.perf_counter(),
        )
        async with self._translation_lock:
            if not self._running:
                raise InferenceRejectedError("inference runtime stopped")
            if self._translation_pending_count >= self.translation_max_pending:
                raise InferenceRejectedError("global translation queue full")
            if session_id not in self._translation_session_rr:
                self._translation_session_rr.append(session_id)
            session_queue = self._translation_pending.setdefault(session_id, deque())
            session_queue.append(job)
            self._translation_pending_count += 1
            self.metrics.translation_queue_depth = self._translation_pending_count
            self._translation_notify.notify_all()
        return await future

    async def _asr_scheduler(self) -> None:
        while self._running:
            job: _AsrJob | None = None
            async with self._asr_lock:
                while self._running and (self._asr_active >= self.asr_max_concurrent or self._asr_pending_count == 0):
                    await self._asr_notify.wait()
                if not self._running:
                    return
                job = self._dequeue_fair_asr_locked()
                if job is None:
                    continue
                self._asr_active += 1
                self._asr_pending_count -= 1
                self.metrics.asr_active = self._asr_active
                self.metrics.asr_queue_depth = self._asr_pending_count
            task = asyncio.create_task(self._run_asr_job(job), name=f"asr-job-{job.session_id}")
            self._asr_job_tasks.add(task)
            task.add_done_callback(self._asr_job_tasks.discard)

    async def _translation_scheduler(self) -> None:
        while self._running:
            batch = await self._collect_translation_batch()
            if not batch:
                continue
            async with self._translation_lock:
                self._translation_active += 1
                self.metrics.translation_active = self._translation_active
            task = asyncio.create_task(self._run_translation_batch(batch), name="translation-batch")
            self._translation_job_tasks.add(task)
            task.add_done_callback(self._translation_job_tasks.discard)

    async def _collect_translation_batch(self) -> list[_TranslationJob]:
        async with self._translation_lock:
            while self._running and (
                self._translation_active >= self.translation_max_concurrent or self._translation_pending_count == 0
            ):
                await self._translation_notify.wait()
            if not self._running:
                return []
            pending_snapshot = self._translation_pending_count
            first = self._dequeue_fair_translation_locked()
            if first is None:
                return []
            self._translation_pending_count -= 1
            self.metrics.translation_queue_depth = self._translation_pending_count
            batch = [first]
            batch_key = self._translation_batch_key(first)

        if batch_key is not None and pending_snapshot > 1:
            collect_ms = _translation_batch_collect_ms()
            if collect_ms > 0:
                await asyncio.sleep(collect_ms / 1000.0)
                self.metrics.translation_batch_collect_wait_ms.append(collect_ms)

        if batch_key is not None:
            async with self._translation_lock:
                self._drain_compatible_translation_jobs_locked(batch, batch_key)

        return batch

    def _translation_batch_key(self, job: _TranslationJob) -> tuple[Any, ...] | None:
        from .schemas import ClientConfig
        from .translator import get_translation_engine

        if len(job.args) < 2:
            return None
        sentences, config = job.args[0], job.args[1]
        if not isinstance(sentences, list) or not isinstance(config, ClientConfig):
            return None
        engine = get_translation_engine()
        return engine.batch_key(config.source_lang, config.target_lang)

    def _drain_compatible_translation_jobs_locked(
        self,
        batch: list[_TranslationJob],
        batch_key: tuple[Any, ...],
    ) -> None:
        max_requests = _translation_batch_max_requests()
        max_chars = _translation_batch_max_chars()
        batch_chars = 0
        for job in batch:
            sentences = job.args[0] if job.args else []
            if isinstance(sentences, list):
                batch_chars += sum(len(str(item)) for item in sentences)

        for session_id in list(self._translation_session_rr):
            if len(batch) >= max_requests or batch_chars >= max_chars:
                break
            queue = self._translation_pending.get(session_id)
            if not queue:
                continue
            while queue and self._translation_batch_key(queue[0]) == batch_key:
                if len(batch) >= max_requests:
                    break
                candidate = queue[0]
                sentences = candidate.args[0] if candidate.args else []
                next_chars = sum(len(str(item)) for item in sentences) if isinstance(sentences, list) else 0
                if batch and batch_chars + next_chars > max_chars:
                    break
                batch.append(queue.popleft())
                batch_chars += next_chars
                self._translation_pending_count -= 1
            if not queue:
                del self._translation_pending[session_id]
                self._release_translation_session_if_idle_locked(session_id)
        self.metrics.translation_queue_depth = self._translation_pending_count

    def _dequeue_fair_asr_locked(self) -> _AsrJob | None:
        for priority in AsrPriority:
            sessions = self._asr_pending[priority]
            if not sessions:
                continue
            rotations = len(self._asr_session_rr)
            for _ in range(rotations):
                session_id = self._asr_session_rr[0]
                self._asr_session_rr.rotate(-1)
                queue = sessions.get(session_id)
                if queue:
                    job = queue.popleft()
                    if not queue:
                        del sessions[session_id]
                        self._release_asr_session_if_idle_locked(session_id)
                    return job
        return None

    def _dequeue_fair_translation_locked(self) -> _TranslationJob | None:
        if not self._translation_session_rr:
            return None
        rotations = len(self._translation_session_rr)
        for _ in range(rotations):
            session_id = self._translation_session_rr[0]
            self._translation_session_rr.rotate(-1)
            queue = self._translation_pending.get(session_id)
            if queue:
                job = queue.popleft()
                if not queue:
                    del self._translation_pending[session_id]
                    self._release_translation_session_if_idle_locked(session_id)
                return job
        return None

    async def _run_asr_job(self, job: _AsrJob) -> None:
        try:
            if job.is_stale and job.is_stale():
                self.metrics.asr_partials_discarded += 1
                if not job.future.done():
                    job.future.set_exception(InferenceRejectedError("stale partial"))
                return
            wait_ms = (time.perf_counter() - job.enqueued_at) * 1000
            self._record_asr_wait(job.priority, wait_ms)
            result: Any = await self._execute(self._asr_executor, job.fn, *job.args, **job.kwargs)
            if not job.future.done():
                job.future.set_result(result)
        except asyncio.CancelledError:
            if not job.future.done():
                job.future.set_exception(InferenceRejectedError("inference runtime stopped"))
            raise
        except Exception as exc:
            if not job.future.done():
                job.future.set_exception(exc)
        finally:
            async with self._asr_lock:
                self._asr_active -= 1
                self.metrics.asr_active = self._asr_active
                self._asr_notify.notify_all()

    async def _run_translation_batch(self, jobs: list[_TranslationJob]) -> None:
        try:
            for job in jobs:
                wait_ms = (time.perf_counter() - job.enqueued_at) * 1000
                self.metrics.translation_queue_wait_ms.append(wait_ms)
            if len(jobs) == 1:
                job = jobs[0]
                result: Any = await self._execute(self._translation_executor, job.fn, *job.args, **job.kwargs)
                if not job.future.done():
                    job.future.set_result(result)
                return

            first = jobs[0]
            if len(first.args) < 2:
                for job in jobs:
                    job_result: Any = await self._execute(self._translation_executor, job.fn, *job.args, **job.kwargs)
                    if not job.future.done():
                        job.future.set_result(job_result)
                return

            combined: list[str] = []
            ranges: list[tuple[_TranslationJob, int, int]] = []
            config = first.args[1]
            for job in jobs:
                sentences = job.args[0]
                start = len(combined)
                combined.extend(sentences)
                ranges.append((job, start, len(combined)))

            self.metrics.translation_batches += 1
            self.metrics.translation_batch_requests += len(jobs)
            translated: Any = await self._execute(self._translation_executor, first.fn, combined, config)
            for job, start, end in ranges:
                if not job.future.done():
                    job.future.set_result(translated[start:end])
        except asyncio.CancelledError:
            self._reject_pending_jobs(jobs, "inference runtime stopped")
            raise
        except Exception as exc:
            for job in jobs:
                if not job.future.done():
                    job.future.set_exception(exc)
        finally:
            async with self._translation_lock:
                self._translation_active -= 1
                self.metrics.translation_active = self._translation_active
                self._translation_notify.notify_all()

    async def _execute(self, executor: ThreadPoolExecutor, fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(executor, lambda: fn(*args, **kwargs))

    def _record_asr_wait(self, priority: AsrPriority, wait_ms: float) -> None:
        self.metrics.asr_queue_wait_ms.append(wait_ms)
        if priority == AsrPriority.FINAL:
            self.metrics.asr_final_queue_wait_ms.append(wait_ms)
        elif priority == AsrPriority.PARTIAL:
            self.metrics.asr_partial_queue_wait_ms.append(wait_ms)


_runtime: InferenceRuntime | None = None
_runtime_lock = RLock()


def get_inference_runtime() -> InferenceRuntime:
    global _runtime
    if _runtime is not None:
        return _runtime
    with _runtime_lock:
        if _runtime is None:
            _runtime = InferenceRuntime()
        return _runtime


def _clear_inference_runtime_cache() -> None:
    global _runtime
    with _runtime_lock:
        _runtime = None


get_inference_runtime.cache_clear = _clear_inference_runtime_cache  # type: ignore[attr-defined]
