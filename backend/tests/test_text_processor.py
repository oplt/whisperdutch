from __future__ import annotations

from app.text_processor import DutchTextProcessor


def test_text_processor_normalizes_spacing() -> None:
    processor = DutchTextProcessor(glossary_enabled=False)
    assert processor.normalize("Hallo   ,\nwereld  !") == "Hallo, wereld!"


def test_text_processor_applies_glossary() -> None:
    processor = DutchTextProcessor(glossary_enabled=True)
    processor._rules = []
    processor._rules.append((__import__("re").compile("Ajaaks", flags=__import__("re").IGNORECASE), "Ajax"))
    assert processor.correct("Ajaaks speelt") == "Ajax speelt"
