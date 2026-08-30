from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Language:
    code: str
    name: str


# Keep this curated list aligned with frontend-extension/app/languages.js. Every
# entry is supported by both Whisper and the multilingual M2M100 model.
LANGUAGES: tuple[Language, ...] = (
    Language("nl", "Dutch"),
    Language("en", "English"),
    Language("de", "German"),
    Language("fr", "French"),
    Language("es", "Spanish"),
    Language("it", "Italian"),
    Language("pt", "Portuguese"),
    Language("pl", "Polish"),
    Language("tr", "Turkish"),
    Language("ru", "Russian"),
    Language("uk", "Ukrainian"),
    Language("ar", "Arabic"),
    Language("hi", "Hindi"),
    Language("zh", "Chinese"),
    Language("ja", "Japanese"),
    Language("ko", "Korean"),
    Language("sv", "Swedish"),
    Language("da", "Danish"),
    Language("no", "Norwegian"),
    Language("fi", "Finnish"),
)

DEFAULT_SOURCE_LANGUAGE = "nl"
DEFAULT_TARGET_LANGUAGE = "en"
SUPPORTED_LANGUAGE_CODES = frozenset(language.code for language in LANGUAGES)

# FLORES-200 codes used by facebook/nllb-200-distilled-600M.
NLLB_LANGUAGE_CODES: dict[str, str] = {
    "nl": "nld_Latn",
    "en": "eng_Latn",
    "de": "deu_Latn",
    "fr": "fra_Latn",
    "es": "spa_Latn",
    "it": "ita_Latn",
    "pt": "por_Latn",
    "pl": "pol_Latn",
    "tr": "tur_Latn",
    "ru": "rus_Cyrl",
    "uk": "ukr_Cyrl",
    "ar": "arb_Arab",
    "hi": "hin_Deva",
    "zh": "zho_Hans",
    "ja": "jpn_Jpan",
    "ko": "kor_Hang",
    "sv": "swe_Latn",
    "da": "dan_Latn",
    "no": "nob_Latn",
    "fi": "fin_Latn",
}

M2M100_LANGUAGE_CODES = dict.fromkeys(SUPPORTED_LANGUAGE_CODES, True)


class UnsupportedLanguagePairError(ValueError):
    pass


def normalize_language(language: str) -> str:
    return str(language or "").strip().lower()


def validate_language(language: str) -> str:
    normalized = normalize_language(language)
    if normalized not in SUPPORTED_LANGUAGE_CODES:
        supported = ", ".join(sorted(SUPPORTED_LANGUAGE_CODES))
        raise ValueError(f"Unsupported language '{normalized or language}'. Supported languages: {supported}")
    return normalized


def language_catalog() -> list[dict[str, str]]:
    return [{"code": language.code, "name": language.name} for language in LANGUAGES]
