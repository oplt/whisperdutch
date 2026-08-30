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


def test_sentence_assembler_accumulates_max_duration_fragments() -> None:
    assembler = SentenceAssembler(min_final_words=2)

    first_sentences, first_buffer = assembler.add_fragment("Dit is een", force=False)
    second_sentences, second_buffer = assembler.add_fragment("lange zin.", force=False)

    assert first_sentences == []
    assert first_buffer == "Dit is een"
    assert second_sentences == ["Dit is een lange zin."]
    assert second_buffer == ""


def test_context_prompt_includes_session_hint() -> None:
    assembler = SentenceAssembler()
    assembler.session_prompt = "Ajax Champions League"
    assert "Ajax Champions League" in (assembler.context_prompt() or "")


def test_language_change_clears_context_and_disables_dutch_connector_rule() -> None:
    assembler = SentenceAssembler(min_final_words=2)
    assembler.add_fragment("Ik kom morgen", force=False)

    assembler.configure("en", "English lecture")
    sentences, buffer = assembler.add_fragment("We learn together and", force=True)

    assert sentences == ["We learn together and."]
    assert buffer == ""
    assert "Ik kom morgen" not in (assembler.context_prompt() or "")
