from __future__ import annotations

from collections import OrderedDict

from app.translator import TranslationEngine


def test_translation_cache_is_lru() -> None:
    engine = object.__new__(TranslationEngine)
    engine.cache = OrderedDict()
    engine.max_cache_items = 2

    engine._cache_set("a", "A")
    engine._cache_set("b", "B")
    engine.cache.move_to_end("a")
    engine._cache_set("c", "C")

    assert list(engine.cache.keys()) == ["a", "c"]
