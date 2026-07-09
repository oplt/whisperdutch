from __future__ import annotations

from app.sentences import SentenceAssembler


def test_sentence_assembler_forces_final_sentence() -> None:
    assembler = SentenceAssembler(min_final_words=2)
    sentences, buffer = assembler.add_fragment("Dit is een test", force=True)
    assert buffer == ""
    assert sentences == ["Dit is een test."]


def test_sentence_assembler_waits_on_connector() -> None:
    assembler = SentenceAssembler(min_final_words=2)
    sentences, buffer = assembler.add_fragment("Ik kom morgen en", force=True)
    assert sentences == []
    assert buffer == "Ik kom morgen en"


def test_context_prompt_includes_session_hint() -> None:
    assembler = SentenceAssembler()
    assembler.session_prompt = "Ajax Champions League"
    assert "Ajax Champions League" in (assembler.context_prompt() or "")
