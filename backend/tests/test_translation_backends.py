from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from app import translation_backends
from app.languages import NLLB_LANGUAGE_CODES, UnsupportedLanguagePairError


class FakeTokenizer:
    def __init__(self) -> None:
        self.src_lang = None

    def encode(self, text: str, **kwargs):
        return [1, 2, 3]

    def convert_ids_to_tokens(self, ids):
        if ids == [99]:
            return ["eng_Latn"]
        return ["__en__"]

    def convert_tokens_to_ids(self, tokens):
        if tokens == ["eng_Latn"]:
            return [99]
        return [42]

    def decode(self, ids, **kwargs):
        return "translated"


def test_nllb_language_mapping_covers_ui_languages() -> None:
    expected = {"nl", "en", "de", "fr", "es", "it", "pt", "pl", "tr", "ru", "uk", "ar", "hi", "zh", "ja", "ko", "sv", "da", "no", "fi"}
    assert expected == set(NLLB_LANGUAGE_CODES)


def test_m2m100_backend_forces_target_prefix() -> None:
    tokenizer = FakeTokenizer()
    tokenizer.get_lang_id = lambda code: {"nl": 10, "en": 20}[code]
    translator = MagicMock()
    translator.translate_batch.return_value = [SimpleNamespace(hypotheses=[["__en__", "hello"]])]
    backend = translation_backends.M2M100CTranslate2Backend(
        tokenizer=tokenizer,
        translator=translator,
        beam_size=1,
        max_decoding_length=32,
    )
    result = backend.translate_many(["Hallo"], source_language="nl", target_language="en")
    assert result == ["translated"]
    assert tokenizer.src_lang == "nl"
    kwargs = translator.translate_batch.call_args.kwargs
    assert kwargs["target_prefix"] == [["__en__"]]


def test_nllb_backend_uses_flores_codes() -> None:
    tokenizer = FakeTokenizer()
    translator = MagicMock()
    translator.translate_batch.return_value = [SimpleNamespace(hypotheses=[["eng_Latn", "hello"]])]
    backend = translation_backends.NLLBCTranslate2Backend(
        tokenizer=tokenizer,
        translator=translator,
        beam_size=1,
        max_decoding_length=32,
    )
    backend.translate_many(["Hallo"], source_language="nl", target_language="en")
    assert tokenizer.src_lang == NLLB_LANGUAGE_CODES["nl"]


def test_unsupported_backend_family() -> None:
    with pytest.raises(UnsupportedLanguagePairError):
        translation_backends.create_ctranslate2_backend(
            model_family="marian",
            tokenizer=FakeTokenizer(),
            translator=MagicMock(),
            beam_size=1,
            max_decoding_length=32,
        )
