from __future__ import annotations

from collections import OrderedDict, deque
from threading import RLock

from app.translation_cache import DurableTranslationCache
from app.translator import TRANSLATION_CACHE_SCHEMA_VERSION, TranslationEngine, _cache_key_id


def make_engine_with_store(store: DurableTranslationCache) -> TranslationEngine:
    engine = object.__new__(TranslationEngine)
    engine.engine = "fake"
    engine.model_name = "fake-model"
    engine.tokenizer_name = "fake-tokenizer"
    engine.beam_size = 1
    engine.max_decoding_length = 160
    engine.cache_schema_version = TRANSLATION_CACHE_SCHEMA_VERSION
    engine.glossary_version = "disabled"
    engine.cache = OrderedDict()
    engine.cache_backend = "sqlite"
    engine.cache_ttl_seconds = store.ttl_seconds
    engine._cache_lock = RLock()
    engine._inflight = {}
    engine._cache_generation = 0
    engine.max_cache_items = store.max_items
    engine._cache_hits = 0
    engine._cache_misses = 0
    engine._cache_evictions = 0
    engine._cache_sets = 0
    engine._single_flight_waits = 0
    engine._durable_hits = 0
    engine._durable_misses = 0
    engine._durable_read_failures = 0
    engine._durable_write_failures = 0
    engine._cache_hit_latencies_ms = deque(maxlen=1000)
    engine._cache_miss_lookup_latencies_ms = deque(maxlen=1000)
    engine._cache_miss_translation_latencies_ms = deque(maxlen=1000)
    engine.durable_cache = store
    return engine


def test_durable_cache_persists_across_store_instances(tmp_path) -> None:
    db_path = tmp_path / "translation-cache.sqlite3"
    first = DurableTranslationCache(db_path, max_items=4)
    first.set("key-1", "Hallo", "Hello", {"schema_version": 1})

    second = DurableTranslationCache(db_path, max_items=4)

    assert second.get("key-1") == "Hello"
    assert second.info()["hits"] == 1
    assert second.info()["size"] == 1


def test_durable_cache_expires_entries_by_ttl(tmp_path) -> None:
    now = [100.0]
    store = DurableTranslationCache(tmp_path / "translation-cache.sqlite3", max_items=4, ttl_seconds=10, clock=lambda: now[0])
    store.set("key-1", "Hallo", "Hello", {})

    now[0] = 110.0

    assert store.get("key-1") is None
    assert store.info()["prunes"] == 1


def test_durable_cache_prunes_oldest_entries_by_item_limit(tmp_path) -> None:
    now = [100.0]
    store = DurableTranslationCache(tmp_path / "translation-cache.sqlite3", max_items=2, clock=lambda: now[0])
    store.set("key-a", "A", "A", {})
    now[0] += 1
    store.set("key-b", "B", "B", {})
    now[0] += 1
    store.set("key-c", "C", "C", {})

    assert store.get("key-a") is None
    assert store.get("key-b") == "B"
    assert store.get("key-c") == "C"
    assert store.info()["evictions"] == 1


def test_translation_engine_promotes_durable_hit_to_memory(tmp_path) -> None:
    store = DurableTranslationCache(tmp_path / "translation-cache.sqlite3", max_items=4)
    engine = make_engine_with_store(store)
    key = engine.cache_key("Hallo")
    store.set(_cache_key_id(key), key.source_text, "from disk", key.__dict__)
    engine._translate_transformers_many = lambda _texts: (_ for _ in ()).throw(AssertionError("MT called"))

    assert engine.translate("Hallo") == "from disk"
    assert engine.translate("Hallo") == "from disk"
    assert engine.cache_info()["durable"]["engine_hits"] == 1
    assert engine.cache_info()["hits"] == 2


def test_translation_engine_writes_and_reuses_durable_result(tmp_path) -> None:
    db_path = tmp_path / "translation-cache.sqlite3"
    first = make_engine_with_store(DurableTranslationCache(db_path, max_items=4))
    first._translate_transformers_many = lambda texts: [f"translated:{text}" for text in texts]

    assert first.translate("Hallo") == "translated:Hallo"

    second_store = DurableTranslationCache(db_path, max_items=4)
    second = make_engine_with_store(second_store)
    second._translate_transformers_many = lambda _texts: (_ for _ in ()).throw(AssertionError("MT called"))

    assert second.translate("Hallo") == "translated:Hallo"
    assert second.cache_info()["durable"]["engine_hits"] == 1


def test_translation_engine_clear_cache_clears_both_tiers(tmp_path) -> None:
    store = DurableTranslationCache(tmp_path / "translation-cache.sqlite3", max_items=4)
    engine = make_engine_with_store(store)
    engine._translate_transformers_many = lambda texts: [f"translated:{text}" for text in texts]
    engine.translate("Hallo")

    result = engine.clear_cache("test")

    assert result["cleared"] == 1
    assert result["durable_cleared"] == 1
    assert store.info()["size"] == 0
