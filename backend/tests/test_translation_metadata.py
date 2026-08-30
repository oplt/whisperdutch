from __future__ import annotations

from app.translation_metadata import TranslationLanguageMetadata, build_config_fingerprint


class FakeTokenizer:
    def __init__(self) -> None:
        self.src_lang = None

    def get_lang_id(self, language: str) -> int:
        return {"nl": 10, "en": 20, "de": 30}[language]

    def convert_ids_to_tokens(self, ids: list[int]) -> list[str]:
        mapping = {10: "nl", 20: "en", 30: "de", 101: "nld_Latn", 102: "eng_Latn", 103: "deu_Latn"}
        return [mapping[ids[0]]]

    def convert_tokens_to_ids(self, tokens: list[str]) -> list[int]:
        mapping = {"nld_Latn": 101, "eng_Latn": 102, "deu_Latn": 103}
        return [mapping[tokens[0]]]


def test_config_fingerprint_changes_with_model_settings() -> None:
    baseline = build_config_fingerprint(
        translation_engine="ctranslate2",
        model_name="models/a",
        tokenizer_name="tokenizer/a",
        model_family="nllb",
        beam_size=1,
        max_decoding_length=160,
    )
    changed = build_config_fingerprint(
        translation_engine="ctranslate2",
        model_name="models/b",
        tokenizer_name="tokenizer/a",
        model_family="nllb",
        beam_size=1,
        max_decoding_length=160,
    )
    assert baseline != changed


def test_language_metadata_precomputes_nllb_target_prefix_token() -> None:
    metadata = TranslationLanguageMetadata(tokenizer=FakeTokenizer(), model_family="nllb")
    pair = metadata.pair("nl", "en")
    assert pair.nllb_source_code == "nld_Latn"
    assert pair.nllb_target_token == "eng_Latn"


def test_language_metadata_precomputes_m2m_target_prefix_token() -> None:
    metadata = TranslationLanguageMetadata(tokenizer=FakeTokenizer(), model_family="m2m100")
    pair = metadata.pair("de", "en")
    assert pair.m2m_target_lang_id == 20
    assert pair.m2m_target_token == "en"
