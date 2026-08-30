from __future__ import annotations

from collections import OrderedDict, deque
from threading import Event, RLock, Thread
from time import monotonic, sleep
from types import SimpleNamespace

from app.languages import UnsupportedLanguagePairError
from app.translator import TRANSLATION_CACHE_SCHEMA_VERSION, TranslationEngine, _glossary_cache_version


def make_engine(max_cache_items: int = 4096) -> TranslationEngine:
    engine = object.__new__(TranslationEngine)
    engine.engine = "fake"
    engine.model_name = "fake-model"
    engine.tokenizer_name = "fake-tokenizer"
    engine.beam_size = 1
    engine.max_decoding_length = 160
    engine.cache_schema_version = TRANSLATION_CACHE_SCHEMA_VERSION
    engine.glossary_version = "disabled"
    engine.cache = OrderedDict()
    engine.cache_backend = "memory"
    engine.cache_ttl_seconds = 0.0
    engine._cache_lock = RLock()
    engine._model_lock = RLock()
    engine._inflight = {}
    engine._cache_generation = 0
    engine.max_cache_items = max_cache_items
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
    engine.durable_cache = None
    engine._durable_executor = None
    return engine


def wait_for_single_flight_wait(engine: TranslationEngine) -> None:
    deadline = monotonic() + 1
    while engine.cache_info()["single_flight_waits"] < 1 and monotonic() < deadline:
        sleep(0.001)
    assert engine.cache_info()["single_flight_waits"] >= 1


def test_translation_cache_key_normalizes_text_and_languages() -> None:
    engine = make_engine()

    key = engine.cache_key("  Hallo\n   wereld!  ", source_language=" NL ", target_language=" EN ")

    assert key.source_text == "Hallo wereld!"
    assert key.source_language == "nl"
    assert key.target_language == "en"
    assert key.schema_version == TRANSLATION_CACHE_SCHEMA_VERSION
    assert key.glossary_version == "disabled"


def test_translation_cache_key_includes_translation_configuration() -> None:
    engine = make_engine()
    baseline = engine.cache_key("Hallo")

    target_key = engine.cache_key("Hallo", target_language="de")
    engine.beam_size = 3
    beam_key = engine.cache_key("Hallo")
    engine.model_name = "other-model"
    model_key = engine.cache_key("Hallo")
    engine.glossary_version = "sha256:other"
    glossary_key = engine.cache_key("Hallo")

    assert baseline != target_key
    assert baseline != beam_key
    assert baseline != model_key
    assert baseline != glossary_key


def test_translation_cache_glossary_version_uses_content_hash(tmp_path, monkeypatch) -> None:
    glossary = tmp_path / "glossary.tsv"
    glossary.write_text("Ajax\tAjax\n", encoding="utf-8")
    monkeypatch.setenv("GLOSSARY_ENABLED", "1")
    monkeypatch.setenv("GLOSSARY_PATH", str(glossary))

    first_version = _glossary_cache_version()
    glossary.write_text("Feyenoord\tFeyenoord\n", encoding="utf-8")
    second_version = _glossary_cache_version()

    assert first_version.startswith("sha256:")
    assert second_version.startswith("sha256:")
    assert first_version != second_version


def test_translation_cache_refresh_glossary_version_clears_entries(tmp_path, monkeypatch) -> None:
    glossary = tmp_path / "glossary.tsv"
    glossary.write_text("Ajax\tAjax\n", encoding="utf-8")
    monkeypatch.setenv("GLOSSARY_ENABLED", "1")
    monkeypatch.setenv("GLOSSARY_PATH", str(glossary))

    engine = make_engine()
    engine.glossary_version = "sha256:old"
    engine._cache_set(engine.cache_key("Hallo"), "Hello")

    result = engine.refresh_glossary_version()

    assert result["cleared"] == 1
    assert result["previous_glossary_version"] == "sha256:old"
    assert result["glossary_version"].startswith("sha256:")
    assert engine.cache_info()["size"] == 0


def test_translation_cache_coalesces_concurrent_duplicate_misses() -> None:
    engine = make_engine()
    started = Event()
    release = Event()
    calls: list[list[str]] = []

    def translate(texts: list[str]) -> list[str]:
        calls.append(texts)
        started.set()
        assert release.wait(timeout=1)
        return [f"translated:{text}" for text in texts]

    engine._translate_transformers_many = translate
    results: list[list[str] | None] = [None, None]

    def run(index: int) -> None:
        results[index] = engine.translate_many(["Hallo"])

    first = Thread(target=run, args=(0,))
    second = Thread(target=run, args=(1,))
    first.start()
    assert started.wait(timeout=1)
    second.start()
    wait_for_single_flight_wait(engine)
    release.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert results == [["translated:Hallo"], ["translated:Hallo"]]
    assert calls == [["Hallo"]]
    assert engine.cache_info()["single_flight_waits"] == 1


def test_translation_cache_keeps_owned_batch_and_coalesces_duplicate_key() -> None:
    engine = make_engine()
    started = Event()
    release = Event()
    calls: list[list[str]] = []

    def translate(texts: list[str]) -> list[str]:
        calls.append(texts)
        started.set()
        assert release.wait(timeout=1)
        return [f"translated:{text}" for text in texts]

    engine._translate_transformers_many = translate
    results: list[list[str] | None] = [None, None]

    def run(index: int, texts: list[str]) -> None:
        results[index] = engine.translate_many(texts)

    first = Thread(target=run, args=(0, ["Hallo", "Dag"]))
    second = Thread(target=run, args=(1, ["Hallo"]))
    first.start()
    assert started.wait(timeout=1)
    second.start()
    wait_for_single_flight_wait(engine)
    release.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert results[0] == ["translated:Hallo", "translated:Dag"]
    assert results[1] == ["translated:Hallo"]
    assert calls == [["Hallo", "Dag"]]


def test_translation_cache_releases_waiters_when_owner_fails() -> None:
    engine = make_engine()
    started = Event()
    release = Event()
    calls = 0

    def translate(_texts: list[str]) -> list[str]:
        nonlocal calls
        calls += 1
        started.set()
        assert release.wait(timeout=1)
        raise RuntimeError("translation failed")

    engine._translate_transformers_many = translate
    errors: list[BaseException] = []

    def run() -> None:
        try:
            engine.translate("Hallo")
        except BaseException as exc:
            errors.append(exc)

    first = Thread(target=run)
    second = Thread(target=run)
    first.start()
    assert started.wait(timeout=1)
    second.start()
    wait_for_single_flight_wait(engine)
    release.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert calls == 1
    assert len(errors) == 2
    assert all(isinstance(error, RuntimeError) for error in errors)
    assert engine.cache_info()["inflight"] == 0

    engine._translate_transformers_many = lambda texts: [f"recovered:{text}" for text in texts]
    assert engine.translate("Hallo") == "recovered:Hallo"


def test_translation_cache_clear_prevents_inflight_result_from_being_cached() -> None:
    engine = make_engine()
    started = Event()
    release = Event()

    def translate(texts: list[str]) -> list[str]:
        started.set()
        assert release.wait(timeout=1)
        return [f"translated:{text}" for text in texts]

    engine._translate_transformers_many = translate
    result: list[str] | None = None

    def run() -> None:
        nonlocal result
        result = engine.translate_many(["Hallo"])

    thread = Thread(target=run)
    thread.start()
    started.wait(timeout=1)
    engine.clear_cache("test invalidation")
    release.set()
    thread.join(timeout=2)

    assert result == ["translated:Hallo"]
    assert engine.cache_info()["size"] == 0

    engine._translate_transformers_many = lambda texts: [f"fresh:{text}" for text in texts]
    assert engine.translate("Hallo") == "fresh:Hallo"


def test_translation_cache_is_lru_and_counts_evictions() -> None:
    engine = make_engine(max_cache_items=2)
    key_a = engine.cache_key("a")
    key_b = engine.cache_key("b")
    key_c = engine.cache_key("c")

    engine._cache_set(key_a, "A")
    engine._cache_set(key_b, "B")
    assert engine._cache_get(key_a) == "A"
    engine._cache_set(key_c, "C")

    assert list(engine.cache.keys()) == [key_a, key_c]
    assert engine.cache_info()["sets"] == 3
    assert engine.cache_info()["evictions"] == 1


def test_translation_cache_tracks_hits_misses_and_ratio() -> None:
    engine = make_engine()
    engine._translate_transformers_many = lambda texts: [f"translated:{text}" for text in texts]

    assert engine.translate_many([" Hallo "]) == ["translated:Hallo"]
    assert engine.translate_many(["Hallo"]) == ["translated:Hallo"]

    info = engine.cache_info()
    assert info["hits"] == 1
    assert info["misses"] == 1
    assert info["sets"] == 1
    assert info["hit_ratio"] == 0.5
    assert info["latency_ms"]["cache_hit"]["count"] == 1
    assert info["latency_ms"]["cache_miss_translation"]["count"] == 1
    assert info["acceptance"]["cache_hit_under_5ms"] is True


def test_translation_cache_configuration_isolated_by_language() -> None:
    engine = make_engine()
    calls: list[list[str]] = []

    def translate(texts: list[str]) -> list[str]:
        calls.append(texts)
        return [f"translated:{text}" for text in texts]

    engine._translate_transformers_many = translate

    assert engine.translate("Hallo", target_language="en") == "translated:Hallo"
    assert engine.translate("Hallo", target_language="de") == "translated:Hallo"
    assert engine.translate("Hallo", target_language="en") == "translated:Hallo"

    assert calls == [["Hallo"], ["Hallo"]]
    assert engine.cache_info()["hits"] == 1
    assert engine.cache_info()["misses"] == 2


def test_marian_model_rejects_a_pair_it_cannot_translate() -> None:
    engine = make_engine()
    engine.engine = "ctranslate2"
    engine.model_family = "marian"
    engine.fixed_source_language = "nl"
    engine.fixed_target_language = "en"

    try:
        engine.validate_pair("de", "fr")
    except UnsupportedLanguagePairError as exc:
        assert "nl→en only" in str(exc)
    else:
        raise AssertionError("unsupported Marian language pair was accepted")


def test_same_language_pair_bypasses_translation() -> None:
    engine = make_engine()
    engine._translate_transformers_many = lambda _texts: (_ for _ in ()).throw(AssertionError("model called"))

    assert engine.translate_many(["  Hallo   wereld  "], source_language="nl", target_language="nl") == [
        "Hallo wereld"
    ]


def test_m2m100_ctranslate2_sets_source_and_target_languages() -> None:
    engine = make_engine()
    engine.model_family = "m2m100"
    calls: list[dict] = []

    class Tokenizer:
        src_lang = ""

        def get_lang_id(self, language: str) -> int:
            assert language == "fr"
            return 7

        def encode(self, text: str, **_kwargs) -> list[int]:
            assert self.src_lang == "de"
            assert text == "Guten Tag"
            return [1]

        def convert_ids_to_tokens(self, ids: list[int]) -> list[str]:
            return ["__fr__"] if ids == [7] else ["source"]

        def convert_tokens_to_ids(self, tokens: list[str]) -> list[int]:
            assert tokens == ["bonjour"]
            return [2]

        def decode(self, _ids: list[int], **_kwargs) -> str:
            return "Bonjour"

    class Translator:
        def translate_batch(self, batches: list[list[str]], **kwargs):
            calls.append({"batches": batches, **kwargs})
            return [SimpleNamespace(hypotheses=[["__fr__", "bonjour"]])]

    engine.tokenizer = Tokenizer()
    engine.translator = Translator()

    assert engine._translate_ctranslate2_many(
        ["Guten Tag"], source_language="de", target_language="fr"
    ) == ["Bonjour"]
    assert calls[0]["target_prefix"] == [["__fr__"]]


def test_translation_cache_can_be_disabled() -> None:
    engine = make_engine(max_cache_items=0)
    calls: list[list[str]] = []
    engine._translate_transformers_many = lambda texts: calls.append(texts) or ["translated" for _ in texts]

    assert engine.translate_many(["Hallo"]) == ["translated"]
    assert engine.translate_many(["Hallo"]) == ["translated"]

    info = engine.cache_info()
    assert info["disabled"] is True
    assert info["hits"] == 0
    assert info["misses"] == 2
    assert info["sets"] == 0
    assert info["size"] == 0
    assert len(calls) == 2


def test_translation_cache_clear_removes_entries_and_preserves_stats() -> None:
    engine = make_engine()
    key = engine.cache_key("Hallo")
    engine._cache_set(key, "Hello")
    assert engine._cache_get(key) == "Hello"

    result = engine.clear_cache("  glossary updated  ")

    assert result["cleared"] == 1
    assert result["reason"] == "glossary updated"
    assert result["stats_before"]["hits"] == 1
    assert engine.cache_info()["size"] == 0
    assert engine.cache_info()["hits"] == 1
