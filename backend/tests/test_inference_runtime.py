from __future__ import annotations

import asyncio
import threading

import pytest
from app.inference_runtime import AsrPriority, InferenceRejectedError, get_inference_runtime


@pytest.mark.usefixtures("reset_inference_runtime")
def test_final_jobs_run_before_partials_under_load() -> None:
    async def run() -> None:
        runtime = get_inference_runtime()
        runtime.asr_max_pending = 32
        runtime.asr_max_concurrent = 1
        runtime.set_inline(False)
        await runtime.start()
        order: list[str] = []
        gate = threading.Event()

        def partial_fn(label: str) -> str:
            gate.wait(timeout=2)
            order.append(label)
            return label

        def final_fn() -> str:
            order.append("final")
            return "final"

        first_partial = asyncio.create_task(
            runtime.run_asr(AsrPriority.PARTIAL, partial_fn, "partial-1", session_id="a"),
        )
        await asyncio.sleep(0.02)
        second_partial = asyncio.create_task(
            runtime.run_asr(AsrPriority.PARTIAL, partial_fn, "partial-2", session_id="a"),
        )
        final_task = asyncio.create_task(runtime.run_asr(AsrPriority.FINAL, final_fn, session_id="b"))
        await asyncio.sleep(0.02)
        gate.set()
        await asyncio.gather(first_partial, final_task, second_partial)
        assert order[0] == "partial-1"
        assert order.index("final") < order.index("partial-2")
        await runtime.stop()

    asyncio.run(run())


@pytest.mark.usefixtures("reset_inference_runtime")
def test_stale_partial_is_discarded_before_inference() -> None:
    async def run() -> None:
        runtime = get_inference_runtime()
        runtime.set_inline(False)
        await runtime.start()
        called = {"partial": False}

        def partial_fn() -> str:
            called["partial"] = True
            return "partial"

        with pytest.raises(InferenceRejectedError):
            await runtime.run_asr(
                AsrPriority.PARTIAL,
                partial_fn,
                session_id="a",
                is_stale=lambda: True,
            )
        await asyncio.sleep(0.05)
        assert called["partial"] is False
        assert runtime.metrics.asr_partials_discarded >= 1
        await runtime.stop()

    asyncio.run(run())


@pytest.mark.usefixtures("reset_inference_runtime")
def test_partial_jobs_are_rejected_when_global_asr_queue_is_full() -> None:
    async def run() -> None:
        runtime = get_inference_runtime()
        runtime.asr_max_pending = 1
        runtime.asr_max_concurrent = 1
        runtime.set_inline(False)
        await runtime.start()
        blocker_started = threading.Event()
        release_blocker = threading.Event()

        def slow_fn(label: str) -> str:
            if label == "blocker":
                blocker_started.set()
                assert release_blocker.wait(timeout=2)
            return label

        blocker = asyncio.create_task(
            runtime.run_asr(AsrPriority.FINAL, slow_fn, "blocker", session_id="a"),
        )
        await asyncio.sleep(0.02)
        assert blocker_started.is_set()
        queued = asyncio.create_task(
            runtime.run_asr(AsrPriority.PARTIAL, slow_fn, "queued", session_id="b"),
        )
        await asyncio.sleep(0.02)
        with pytest.raises(InferenceRejectedError):
            await runtime.run_asr(AsrPriority.PARTIAL, slow_fn, "overflow", session_id="c")
        release_blocker.set()
        await blocker
        await queued
        await runtime.stop()

    asyncio.run(run())


@pytest.mark.usefixtures("reset_inference_runtime")
def test_scheduler_fairness_rotates_between_sessions() -> None:
    async def run() -> None:
        runtime = get_inference_runtime()
        runtime.asr_max_concurrent = 1
        runtime.asr_max_pending = 16
        runtime.set_inline(False)
        await runtime.start()
        order: list[str] = []
        gate = threading.Event()

        def record(session_id: str) -> str:
            gate.wait(timeout=2)
            order.append(session_id)
            return session_id

        tasks = [
            runtime.run_asr(AsrPriority.FINAL, record, "a", session_id="a"),
            runtime.run_asr(AsrPriority.FINAL, record, "b", session_id="b"),
            runtime.run_asr(AsrPriority.FINAL, record, "a", session_id="a"),
            runtime.run_asr(AsrPriority.FINAL, record, "b", session_id="b"),
        ]
        await asyncio.sleep(0.05)
        gate.set()
        await asyncio.gather(*tasks)
        assert order[:2].count("a") == 1
        assert order[:2].count("b") == 1
        await runtime.stop()

    asyncio.run(run())


@pytest.mark.usefixtures("reset_inference_runtime")
def test_translation_queue_rejects_when_global_backlog_is_full() -> None:
    async def run() -> None:
        runtime = get_inference_runtime()
        runtime.translation_max_pending = 2
        runtime.translation_max_concurrent = 1
        runtime.set_inline(False)
        await runtime.start()
        started = threading.Event()
        release = threading.Event()

        def slow_fn(label: str) -> str:
            if label == "blocker":
                started.set()
                assert release.wait(timeout=2)
            return label

        blocker = asyncio.create_task(runtime.run_translation(slow_fn, "blocker", session_id="a"))
        await asyncio.sleep(0.02)
        assert started.is_set()
        queued_a = asyncio.create_task(runtime.run_translation(slow_fn, "queued-a", session_id="b"))
        queued_b = asyncio.create_task(runtime.run_translation(slow_fn, "queued-b", session_id="c"))
        await asyncio.sleep(0.02)
        with pytest.raises(InferenceRejectedError):
            await runtime.run_translation(slow_fn, "overflow", session_id="d")
        snapshot = runtime.metrics_snapshot()
        assert snapshot["executor"]["translation_saturation"] is True
        release.set()
        await asyncio.gather(blocker, queued_a, queued_b)
        await runtime.stop()

    asyncio.run(run())


@pytest.mark.usefixtures("reset_inference_runtime")
def test_metrics_snapshot_reports_queue_and_active_counts() -> None:
    async def run() -> None:
        runtime = get_inference_runtime()
        runtime.set_inline(False)
        await runtime.start()
        started = threading.Event()
        release = threading.Event()

        def slow_fn() -> str:
            started.set()
            assert release.wait(timeout=2)
            return "ok"

        task = asyncio.create_task(runtime.run_asr(AsrPriority.FINAL, slow_fn, session_id="metrics"))
        await asyncio.sleep(0.02)
        snapshot = runtime.metrics_snapshot()
        assert snapshot["asr"]["active"] == 1
        assert snapshot["asr"]["queue_depth"] == 0
        release.set()
        await task
        await runtime.stop()

    asyncio.run(run())
