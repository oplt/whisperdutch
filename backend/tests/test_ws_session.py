from __future__ import annotations

import asyncio
import time

import numpy as np
from app.metrics import SessionMetrics
from app.schemas import ClientConfig
from app.ws_session import SegmentJob, SessionStats, SubtitleWebSocketSession


def make_queue_session(maxsize: int = 2) -> SubtitleWebSocketSession:
    session = object.__new__(SubtitleWebSocketSession)
    session.client_id = "queue-test"
    session.queue = asyncio.Queue(maxsize=maxsize)
    session.stats = SessionStats()
    session.metrics = SessionMetrics(client_id=session.client_id)
    return session


def final_job(values: list[float]) -> SegmentJob:
    return SegmentJob("final", np.asarray(values, dtype=np.float32), ClientConfig(), time.perf_counter())


def test_partial_backpressure_never_evicts_final_audio() -> None:
    async def run() -> None:
        session = make_queue_session()
        first = final_job([1])
        second = final_job([2])
        await session.queue.put(first)
        await session.queue.put(second)

        partial = SegmentJob("partial", np.asarray([9], dtype=np.float32), ClientConfig(), time.perf_counter())
        await session._enqueue(partial, merge_when_full=False)

        assert list(session.queue._queue) == [first, second]
        assert session.stats.dropped_segments == 1

    asyncio.run(run())


def test_final_backpressure_merges_in_chronological_order() -> None:
    async def run() -> None:
        session = make_queue_session()
        first = final_job([1])
        second = final_job([2])
        await session.queue.put(first)
        await session.queue.put(second)

        await session._enqueue(final_job([3]))

        queued = list(session.queue._queue)
        assert queued[0] is first
        assert queued[1].audio.tolist() == [2, 3]
        assert session.stats.merged_segments == 1
        assert session.stats.dropped_segments == 0

    asyncio.run(run())


def test_new_final_evicts_only_expendable_partial() -> None:
    async def run() -> None:
        session = make_queue_session()
        partial = SegmentJob("partial", np.asarray([9], dtype=np.float32), ClientConfig(), time.perf_counter())
        final = final_job([1])
        await session.queue.put(partial)
        await session.queue.put(final)

        newest = final_job([2])
        await session._enqueue(newest)

        assert list(session.queue._queue) == [final, newest]
        assert session.stats.dropped_segments == 1

    asyncio.run(run())


def test_final_subtitle_is_sent_before_history_persistence(monkeypatch) -> None:
    async def run() -> None:
        session = object.__new__(SubtitleWebSocketSession)
        session.client_id = "send-order-test"
        session.metrics = SessionMetrics(client_id=session.client_id)
        events: list[str] = []

        async def send(_payload: dict) -> None:
            events.append("send")

        session._send_json = send

        async def persist(_payload: dict) -> None:
            events.append("persist")

        session._persist_subtitle = persist
        monkeypatch.setattr("app.ws_session.translate_many_sentences", lambda _sentences, _config: ["Hello"])

        async def run_inline(function, *args):
            return function(*args)

        monkeypatch.setattr(asyncio, "to_thread", run_inline)

        await session._translate_and_send(
            [{"id": "final-1", "sentence": "Hallo", "quality": {"level": "good"}}],
            ClientConfig(),
            asr_latency_ms=100,
            queue_delay_ms=10,
            audio_seconds=1.0,
            fragment="Hallo",
        )

        assert events == ["send", "persist"]

    asyncio.run(run())
