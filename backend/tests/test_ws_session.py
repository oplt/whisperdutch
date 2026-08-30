from __future__ import annotations

import asyncio
import json
import time

import numpy as np
import pytest
from app.audio import SpeechSegmenter
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


def make_session() -> SubtitleWebSocketSession:
    session = object.__new__(SubtitleWebSocketSession)
    session.client_id = "priority-test"
    session.config = ClientConfig()
    session.segmenter = SpeechSegmenter(
        sample_rate=10,
        min_speech_seconds=0.1,
        end_silence_seconds=0.1,
        max_segment_seconds=1.0,
        pre_roll_seconds=0.0,
    )
    session.sentence_assembler = type("Assembler", (), {"context_prompt": lambda self: None})()
    session.queue = asyncio.Queue(maxsize=3)
    session.translation_queue = asyncio.Queue(maxsize=4)
    session.stats = SessionStats()
    session.metrics = SessionMetrics(client_id=session.client_id)
    session.partial_enabled = True
    session.partial_interval_ms = 0
    session.partial_interval_max_ms = 2400
    session.partial_max_seconds = 1.8
    session._last_partial_at = 0.0
    session._processing_kind = None
    session._final_generation = 0
    session._last_realtime_factor = 0.0
    session._backpressure_until = 0.0
    session._partial_suppression_reasons = {}
    session.flush_requested = False
    return session


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


@pytest.mark.parametrize(
    ("second_chunk", "expected_reason"),
    [
        (np.zeros(2, dtype=np.float32), "silence"),
        (np.ones(9, dtype=np.float32), "max"),
    ],
)
def test_final_segment_takes_priority_over_partial(second_chunk: np.ndarray, expected_reason: str) -> None:
    async def run() -> None:
        session = make_session()
        first_chunk = np.ones(2, dtype=np.float32)

        await session._handle_audio(first_chunk)
        assert [job.kind for job in session.queue._queue] == ["partial"]

        await session._handle_audio(second_chunk)
        assert session.segmenter.last_finalize_reason == expected_reason

        queued = list(session.queue._queue)
        assert [job.kind for job in queued] == ["final"]
        assert queued[0].force is (expected_reason == "silence")
        assert session.stats.dropped_segments == 1

    asyncio.run(run())


def test_queued_partial_is_removed_before_final() -> None:
    async def run() -> None:
        session = make_session()
        partial = SegmentJob("partial", np.ones(2, dtype=np.float32), session.config, time.perf_counter(), generation=0)
        await session.queue.put(partial)

        await session._enqueue_final(np.ones(4, dtype=np.float32), force=True)

        assert [job.kind for job in session.queue._queue] == ["final"]
        assert session._final_generation == 1

    asyncio.run(run())


def test_stale_partial_finishing_after_final_is_not_sent(monkeypatch) -> None:
    async def run() -> None:
        session = make_session()
        sent: list[dict] = []
        session._send_json = sent.append
        stale_partial = SegmentJob(
            "partial",
            np.ones(4, dtype=np.float32),
            session.config,
            time.perf_counter(),
            generation=session._final_generation,
        )

        monkeypatch.setattr("app.ws_session.transcribe_partial", lambda *_args: ("oude partial", {"latency_ms": 1, "audio_seconds": 0.4}))
        await session._enqueue_final(np.ones(4, dtype=np.float32), force=True)
        await session._process_partial(stale_partial)

        assert sent == []
        assert session.stats.partial_inferences == 1

    asyncio.run(run())


@pytest.mark.parametrize(
    ("setup", "reason"),
    [
        (lambda session: setattr(session, "_processing_kind", "partial"), "asr_busy"),
        (lambda session: setattr(session, "_last_realtime_factor", 0.8), "realtime_factor"),
        (lambda session: setattr(session, "_backpressure_until", time.perf_counter() + 10), "backpressure"),
    ],
)
def test_adaptive_partial_suppresses_inference_under_load(setup, reason: str) -> None:
    async def run() -> None:
        session = make_session()
        session.segmenter.add(np.ones(2, dtype=np.float32))
        setup(session)

        await session._maybe_enqueue_partial()

        assert session.queue.empty()
        assert session._partial_suppression_reasons == {reason: 1}
        assert session.stats.partial_suppressed == 1

    asyncio.run(run())


def test_adaptive_partial_suppresses_when_final_is_queued() -> None:
    async def run() -> None:
        session = make_session()
        session.segmenter.add(np.ones(2, dtype=np.float32))
        await session.queue.put(final_job([1, 2]))

        await session._maybe_enqueue_partial()

        assert [job.kind for job in session.queue._queue] == ["final"]
        assert session._partial_suppression_reasons == {"final_queued": 1}

    asyncio.run(run())


def test_adaptive_partial_suppresses_near_finalization() -> None:
    async def run() -> None:
        session = make_session()
        session.segmenter.add(np.ones(8, dtype=np.float32))

        await session._maybe_enqueue_partial()

        assert session.queue.empty()
        assert session._partial_suppression_reasons == {"close_to_final": 1}

    asyncio.run(run())


def test_partial_interval_increases_with_realtime_factor() -> None:
    session = make_session()
    session.partial_interval_ms = 900
    session.partial_interval_max_ms = 2400

    session._last_realtime_factor = 0.0
    idle_interval = session._adaptive_partial_interval_ms()
    session._last_realtime_factor = 0.7
    loaded_interval = session._adaptive_partial_interval_ms()

    assert idle_interval == 900
    assert loaded_interval == 1845


def test_adaptive_policy_reduces_overlapping_partial_inference_count(monkeypatch) -> None:
    async def run() -> None:
        session = make_session()
        session.segmenter.max_segment_seconds = 3.0
        session.partial_interval_ms = 900
        session.partial_interval_max_ms = 2400
        session._last_realtime_factor = 0.7
        now = [0.0]
        monkeypatch.setattr("app.ws_session.time.perf_counter", lambda: now[0])
        adaptive_candidates = 0

        for _ in range(30):
            now[0] += 0.1
            finalized = session.segmenter.add(np.ones(1, dtype=np.float32))
            if finalized is not None:
                break
            await session._maybe_enqueue_partial()
            if not session.queue.empty():
                session.queue.get_nowait()
                session.queue.task_done()
                adaptive_candidates += 1

        fixed_interval_candidates = 3
        assert adaptive_candidates == 1
        assert adaptive_candidates < fixed_interval_candidates

    asyncio.run(run())


def test_flush_invalidates_queued_partial_and_preserves_final_order() -> None:
    async def run() -> None:
        session = make_session()
        session.segmenter.add(np.ones(4, dtype=np.float32))
        await session.queue.put(
            SegmentJob("partial", np.ones(2, dtype=np.float32), session.config, time.perf_counter(), generation=0)
        )

        await session._handle_text(json.dumps({"type": "flush"}))

        assert session.flush_requested is True
        assert [job.kind for job in session.queue._queue] == ["final", "flush"]
        assert session._final_generation == 1
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

        def persist_sync(_payload: dict) -> None:
            events.append("persist")

        session._persist_subtitle = persist_sync
        monkeypatch.setattr("app.ws_session.translate_many_sentences", lambda _sentences, _config: ["Hello"])

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


def test_final_payload_includes_optional_timestamps(monkeypatch) -> None:
    async def run() -> None:
        session = object.__new__(SubtitleWebSocketSession)
        session.client_id = "timestamp-test"
        session.metrics = SessionMetrics(client_id=session.client_id)
        payloads: list[dict] = []

        async def send(payload: dict) -> None:
            payloads.append(payload)

        session._send_json = send
        session._persist_subtitle = lambda _payload: None
        monkeypatch.setattr("app.ws_session.translate_many_sentences", lambda _sentences, _config: ["Hello"])

        await session._translate_and_send(
            [
                {
                    "id": "final-1",
                    "sentence": "Hallo",
                    "quality": {"level": "good"},
                    "start": 12.34,
                    "end": 15.92,
                    "words": [{"text": "Hallo", "start": 12.34, "end": 12.78, "probability": 0.97}],
                }
            ],
            ClientConfig(),
            asr_latency_ms=100,
            queue_delay_ms=10,
            audio_seconds=1.0,
            fragment="Hallo",
        )

        final = payloads[0]
        assert final["type"] == "final"
        assert final["dutch"] == "Hallo"
        assert final["translation"] == "Hello"
        assert final["start"] == 12.34
        assert final["end"] == 15.92
        assert final["words"][0]["text"] == "Hallo"

    asyncio.run(run())


def test_audio_gap_resets_segmentation_state() -> None:
    async def run() -> None:
        session = make_session()
        from app.sentences import SentenceAssembler

        session.sentence_assembler = SentenceAssembler(source_language="nl", enabled=False)
        session.segmenter.add(np.ones(1600, dtype=np.float32) * 0.2)
        assert session.segmenter.in_speech is True
        session.sentence_assembler._buffer = "Unfinished fragment"

        acks: list[dict] = []

        async def send(payload: dict) -> None:
            acks.append(payload)

        session._send_json = send
        await session._handle_audio_gap(
            {
                "type": "audio_gap",
                "reason": "backpressure",
                "dropped_chunks": 3,
                "buffered_audio_ms": 300,
            }
        )

        assert session.segmenter.in_speech is False
        assert session.sentence_assembler._buffer == ""
        assert session._final_generation == 1
        assert session.stats.audio_gap_resets == 1
        assert acks == [{"type": "audio_gap_ack", "generation": 1}]

    asyncio.run(run())


def test_audio_gap_does_not_merge_sentences_across_gap() -> None:
    from app.sentences import SentenceAssembler

    assembler = SentenceAssembler(source_language="nl", enabled=True, min_final_words=1)
    assembler.add_fragment("Eerste")
    assembler._buffer = "Onafgemaakte"
    assembler.reset()
    completed, remainder = assembler.add_fragment("Tweede.")
    assert completed == ["Tweede."]
    assert "Onafgemaakte" not in remainder
