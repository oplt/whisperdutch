from __future__ import annotations

import asyncio
import threading

from app import asr, translator
from app.schemas import ClientConfig
from app.ws_session import SubtitleWebSocketSession


def make_isolated_session(client_id: str) -> SubtitleWebSocketSession:
    session = object.__new__(SubtitleWebSocketSession)
    session.client_id = client_id
    session.config = ClientConfig()
    session.segmenter = type("Segmenter", (), {"context_prompt": lambda self: None})()
    session.queue = asyncio.Queue(maxsize=4)
    session.translation_queue = asyncio.Queue(maxsize=4)
    session.stats = type("Stats", (), {"dropped_segments": 0, "merged_segments": 0})()
    session.metrics = type("Metrics", (), {"record_queue_depth": lambda *args, **kwargs: None})()
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


def test_model_singletons_are_shared_across_sessions(monkeypatch) -> None:
    created: list[str] = []

    class FakeAsrEngine:
        def __init__(self) -> None:
            created.append("asr")

    class FakeTranslationEngine:
        def __init__(self) -> None:
            created.append("translation")

    asr.get_asr_engine.cache_clear()
    translator.get_translation_engine.cache_clear()
    monkeypatch.setattr(asr, "TranscriptionEngine", FakeAsrEngine)
    monkeypatch.setattr(translator, "TranslationEngine", FakeTranslationEngine)

    first_asr = asr.get_asr_engine()
    second_asr = asr.get_asr_engine()
    first_translation = translator.get_translation_engine()
    second_translation = translator.get_translation_engine()

    assert first_asr is second_asr
    assert first_translation is second_translation
    assert created == ["asr", "translation"]


def test_websocket_sessions_keep_isolated_queue_state() -> None:
    left = make_isolated_session("left")
    right = make_isolated_session("right")
    left._final_generation = 3
    right._final_generation = 7

    assert left.client_id != right.client_id
    assert left.queue is not right.queue
    assert left.translation_queue is not right.translation_queue
    assert left._final_generation != right._final_generation


def test_concurrent_translation_cache_lookups_remain_thread_safe() -> None:
    engine = translator.TranslationEngine.__new__(translator.TranslationEngine)
    engine.engine = "fake"
    engine.model_name = "fake-model"
    engine.tokenizer_name = "fake-tokenizer"
    engine.model_family = "m2m100"
    engine.beam_size = 1
    engine.max_decoding_length = 160
    engine.cache_schema_version = translator.TRANSLATION_CACHE_SCHEMA_VERSION
    engine.glossary_version = "disabled"
    engine.cache = {}
    engine.cache_backend = "memory"
    engine.cache_ttl_seconds = 0.0
    engine._cache_lock = threading.RLock()
    engine._inflight = {}
    engine._cache_generation = 0
    engine.max_cache_items = 4096
    engine._cache_hits = 0
    engine._cache_misses = 0
    engine._cache_evictions = 0
    engine._cache_sets = 0
    engine._single_flight_waits = 0
    engine.durable_cache = None
    engine._durable_executor = None

    barrier = threading.Barrier(8)
    errors: list[Exception] = []

    def worker(index: int) -> None:
        try:
            barrier.wait(timeout=2)
            key = engine.cache_key(f"line-{index % 4}")
            with engine._cache_lock:
                engine.cache.setdefault(key, f"translation-{index % 4}")
                engine._cache_hits += 1
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert not errors
    assert len(engine.cache) == 4
    assert engine._cache_hits == 16


def test_slow_session_does_not_mutate_peer_generation() -> None:
    slow = make_isolated_session("slow")
    fast = make_isolated_session("fast")
    slow._final_generation = 2
    fast._next_final_generation()
    assert slow._final_generation == 2
    assert fast._final_generation == 1
