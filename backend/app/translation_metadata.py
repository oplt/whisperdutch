from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from .languages import NLLB_LANGUAGE_CODES, validate_language


def build_config_fingerprint(
    *,
    translation_engine: str,
    model_name: str,
    tokenizer_name: str,
    model_family: str,
    beam_size: int,
    max_decoding_length: int,
) -> str:
    payload = {
        "translation_engine": translation_engine,
        "model_name": model_name,
        "tokenizer_name": tokenizer_name,
        "model_family": model_family,
        "beam_size": beam_size,
        "max_decoding_length": max_decoding_length,
    }
    serialized = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LanguagePairMetadata:
    source_language: str
    target_language: str
    nllb_source_code: str | None = None
    nllb_target_token: str | None = None
    m2m_target_lang_id: int | None = None
    m2m_target_token: str | None = None


class TranslationLanguageMetadata:
    """Immutable per-language-pair metadata prepared once at model load."""

    def __init__(self, *, tokenizer: Any, model_family: str) -> None:
        self.model_family = model_family
        self._tokenizer = tokenizer
        self._pairs: dict[tuple[str, str], LanguagePairMetadata] = {}

    def pair(self, source_language: str, target_language: str) -> LanguagePairMetadata:
        source = validate_language(source_language)
        target = validate_language(target_language)
        if source == target:
            raise ValueError("source and target language must differ")
        key = (source, target)
        cached = self._pairs.get(key)
        if cached is not None:
            return cached
        built = self._build_pair(self._tokenizer, source, target)
        self._pairs[key] = built
        return built

    def _build_pair(self, tokenizer: Any, source: str, target: str) -> LanguagePairMetadata:
        if self.model_family == "nllb":
            source_code = NLLB_LANGUAGE_CODES[source]
            target_code = NLLB_LANGUAGE_CODES[target]
            target_id = tokenizer.convert_tokens_to_ids([target_code])[0]
            target_token = tokenizer.convert_ids_to_tokens([target_id])[0]
            return LanguagePairMetadata(
                source_language=source,
                target_language=target,
                nllb_source_code=source_code,
                nllb_target_token=target_token,
            )
        if self.model_family == "m2m100":
            target_id = tokenizer.get_lang_id(target)
            target_token = tokenizer.convert_ids_to_tokens([target_id])[0]
            return LanguagePairMetadata(
                source_language=source,
                target_language=target,
                m2m_target_lang_id=target_id,
                m2m_target_token=target_token,
            )
        return LanguagePairMetadata(source_language=source, target_language=target)
