from __future__ import annotations

from threading import RLock
from typing import Any, Protocol

from .languages import (
    NLLB_LANGUAGE_CODES,
    UnsupportedLanguagePairError,
    validate_language,
)
from .translation_metadata import TranslationLanguageMetadata


class TranslationBackend(Protocol):
    model_family: str

    def translate_many(
        self,
        texts: list[str],
        *,
        source_language: str,
        target_language: str,
    ) -> list[str]:
        ...


def _prepare_ct2_batches(
    *,
    tokenizer: Any,
    texts: list[str],
    source_language: str,
    target_language: str,
    model_family: str,
    tokenizer_lock: RLock,
    language_metadata: TranslationLanguageMetadata | None = None,
) -> tuple[list[list[str]], list[list[str]] | None]:
    source = validate_language(source_language)
    target = validate_language(target_language)
    pair_metadata = language_metadata.pair(source, target) if language_metadata is not None else None
    with tokenizer_lock:
        target_prefix: list[list[str]] | None = None
        if model_family == "m2m100":
            if pair_metadata is not None:
                tokenizer.src_lang = source
                target_token = pair_metadata.m2m_target_token
                assert target_token is not None
                target_prefix = [[target_token] for _ in texts]
            else:
                tokenizer.src_lang = source
                target_id = tokenizer.get_lang_id(target)
                target_token = tokenizer.convert_ids_to_tokens([target_id])[0]
                target_prefix = [[target_token] for _ in texts]
        elif model_family == "nllb":
            if pair_metadata is not None:
                tokenizer.src_lang = pair_metadata.nllb_source_code
                target_token = pair_metadata.nllb_target_token
                assert target_token is not None
                target_prefix = [[target_token] for _ in texts]
            else:
                source_code = NLLB_LANGUAGE_CODES[source]
                target_code = NLLB_LANGUAGE_CODES[target]
                tokenizer.src_lang = source_code
                target_id = tokenizer.convert_tokens_to_ids([target_code])[0]
                target_token = tokenizer.convert_ids_to_tokens([target_id])[0]
                target_prefix = [[target_token] for _ in texts]

        source_batches: list[list[str]] = []
        for text in texts:
            input_ids = tokenizer.encode(text, add_special_tokens=True, truncation=True, max_length=160)
            source_batches.append(tokenizer.convert_ids_to_tokens(input_ids))
    return source_batches, target_prefix


class M2M100CTranslate2Backend:
    model_family = "m2m100"

    def __init__(
        self,
        *,
        tokenizer: Any,
        translator: Any,
        beam_size: int,
        max_decoding_length: int,
        tokenizer_lock: RLock | None = None,
        inference_lock: RLock | None = None,
        language_metadata: TranslationLanguageMetadata | None = None,
    ) -> None:
        self.tokenizer = tokenizer
        self.translator = translator
        self.beam_size = beam_size
        self.max_decoding_length = max_decoding_length
        self._tokenizer_lock = tokenizer_lock or RLock()
        self._inference_lock = inference_lock or RLock()
        self.language_metadata = language_metadata

    def translate_many(
        self,
        texts: list[str],
        *,
        source_language: str,
        target_language: str,
    ) -> list[str]:
        if not texts:
            return []
        source_batches, target_prefix = _prepare_ct2_batches(
            tokenizer=self.tokenizer,
            texts=texts,
            source_language=source_language,
            target_language=target_language,
            model_family=self.model_family,
            tokenizer_lock=self._tokenizer_lock,
            language_metadata=self.language_metadata,
        )
        options: dict[str, object] = {
            "beam_size": self.beam_size,
            "max_decoding_length": self.max_decoding_length,
            "return_scores": False,
        }
        if target_prefix is not None:
            options["target_prefix"] = target_prefix
        with self._inference_lock:
            results = self.translator.translate_batch(source_batches, **options)
        translations: list[str] = []
        for index, result in enumerate(results):
            output_tokens = list(result.hypotheses[0])
            if target_prefix is not None:
                prefix_token = target_prefix[index][0]
                if output_tokens and output_tokens[0] == prefix_token:
                    output_tokens = output_tokens[1:]
            output_ids = self.tokenizer.convert_tokens_to_ids(output_tokens)
            translations.append(self.tokenizer.decode(output_ids, skip_special_tokens=True).strip())
        return translations


class NLLBCTranslate2Backend:
    model_family = "nllb"

    def __init__(
        self,
        *,
        tokenizer: Any,
        translator: Any,
        beam_size: int,
        max_decoding_length: int,
        tokenizer_lock: RLock | None = None,
        inference_lock: RLock | None = None,
        language_metadata: TranslationLanguageMetadata | None = None,
    ) -> None:
        self.tokenizer = tokenizer
        self.translator = translator
        self.beam_size = beam_size
        self.max_decoding_length = max_decoding_length
        self._tokenizer_lock = tokenizer_lock or RLock()
        self._inference_lock = inference_lock or RLock()
        self.language_metadata = language_metadata

    def translate_many(
        self,
        texts: list[str],
        *,
        source_language: str,
        target_language: str,
    ) -> list[str]:
        if not texts:
            return []
        source_batches, target_prefix = _prepare_ct2_batches(
            tokenizer=self.tokenizer,
            texts=texts,
            source_language=source_language,
            target_language=target_language,
            model_family=self.model_family,
            tokenizer_lock=self._tokenizer_lock,
            language_metadata=self.language_metadata,
        )
        assert target_prefix is not None
        with self._inference_lock:
            results = self.translator.translate_batch(
                source_batches,
                beam_size=self.beam_size,
                max_decoding_length=self.max_decoding_length,
                return_scores=False,
                target_prefix=target_prefix,
            )
        translations: list[str] = []
        for index, result in enumerate(results):
            output_tokens = list(result.hypotheses[0])
            prefix_token = target_prefix[index][0]
            if output_tokens and output_tokens[0] == prefix_token:
                output_tokens = output_tokens[1:]
            output_ids = self.tokenizer.convert_tokens_to_ids(output_tokens)
            translations.append(self.tokenizer.decode(output_ids, skip_special_tokens=True).strip())
        return translations


class MarianCTranslate2Backend:
    model_family = "marian"

    def __init__(
        self,
        *,
        tokenizer: Any,
        translator: Any,
        beam_size: int,
        max_decoding_length: int,
        tokenizer_lock: RLock | None = None,
        inference_lock: RLock | None = None,
        language_metadata: TranslationLanguageMetadata | None = None,
    ) -> None:
        self.tokenizer = tokenizer
        self.translator = translator
        self.beam_size = beam_size
        self.max_decoding_length = max_decoding_length
        self._tokenizer_lock = tokenizer_lock or RLock()
        self._inference_lock = inference_lock or RLock()
        self.language_metadata = language_metadata

    def translate_many(
        self,
        texts: list[str],
        *,
        source_language: str,
        target_language: str,
    ) -> list[str]:
        if not texts:
            return []
        source_batches, _target_prefix = _prepare_ct2_batches(
            tokenizer=self.tokenizer,
            texts=texts,
            source_language=source_language,
            target_language=target_language,
            model_family=self.model_family,
            tokenizer_lock=self._tokenizer_lock,
            language_metadata=self.language_metadata,
        )
        with self._inference_lock:
            results = self.translator.translate_batch(
                source_batches,
                beam_size=self.beam_size,
                max_decoding_length=self.max_decoding_length,
                return_scores=False,
            )
        translations: list[str] = []
        for result in results:
            output_tokens = list(result.hypotheses[0])
            output_ids = self.tokenizer.convert_tokens_to_ids(output_tokens)
            translations.append(self.tokenizer.decode(output_ids, skip_special_tokens=True).strip())
        return translations


class TransformersTranslationBackend:
    def __init__(
        self,
        *,
        model_family: str,
        tokenizer: Any,
        model: Any,
        device: str,
        beam_size: int,
        max_decoding_length: int,
        tokenizer_lock: RLock | None = None,
        inference_lock: RLock | None = None,
        language_metadata: TranslationLanguageMetadata | None = None,
    ) -> None:
        self.model_family = model_family
        self.tokenizer = tokenizer
        self.model = model
        self.device = device
        self.beam_size = beam_size
        self.max_decoding_length = max_decoding_length
        self._tokenizer_lock = tokenizer_lock or RLock()
        self._inference_lock = inference_lock or RLock()
        self.language_metadata = language_metadata

    def translate_many(
        self,
        texts: list[str],
        *,
        source_language: str,
        target_language: str,
    ) -> list[str]:
        if not texts:
            return []
        import torch

        source = validate_language(source_language)
        target = validate_language(target_language)
        pair_metadata = self.language_metadata.pair(source, target) if self.language_metadata is not None else None
        generate_options: dict[str, object] = {
            "max_new_tokens": self.max_decoding_length,
            "num_beams": self.beam_size,
            "do_sample": False,
            "use_cache": True,
        }
        with self._tokenizer_lock:
            if self.model_family == "m2m100":
                self.tokenizer.src_lang = source
                if pair_metadata is not None:
                    generate_options["forced_bos_token_id"] = pair_metadata.m2m_target_lang_id
                else:
                    generate_options["forced_bos_token_id"] = self.tokenizer.get_lang_id(target)
            elif self.model_family == "nllb":
                if pair_metadata is not None:
                    self.tokenizer.src_lang = pair_metadata.nllb_source_code
                    generate_options["forced_bos_token_id"] = self.tokenizer.convert_tokens_to_ids(
                        [NLLB_LANGUAGE_CODES[target]]
                    )[0]
                else:
                    self.tokenizer.src_lang = NLLB_LANGUAGE_CODES[source]
                    target_code = NLLB_LANGUAGE_CODES[target]
                    generate_options["forced_bos_token_id"] = self.tokenizer.convert_tokens_to_ids([target_code])[0]
            inputs = self.tokenizer(texts, return_tensors="pt", padding=True, truncation=True, max_length=160).to(self.device)

        with self._inference_lock, torch.inference_mode():
            output_ids = self.model.generate(**inputs, **generate_options)
            return [self.tokenizer.decode(row, skip_special_tokens=True).strip() for row in output_ids]


def create_ctranslate2_backend(
    *,
    model_family: str,
    tokenizer: Any,
    translator: Any,
    beam_size: int,
    max_decoding_length: int,
    tokenizer_lock: RLock | None = None,
    inference_lock: RLock | None = None,
    language_metadata: TranslationLanguageMetadata | None = None,
) -> TranslationBackend:
    if model_family == "nllb":
        return NLLBCTranslate2Backend(
            tokenizer=tokenizer,
            translator=translator,
            beam_size=beam_size,
            max_decoding_length=max_decoding_length,
            tokenizer_lock=tokenizer_lock,
            inference_lock=inference_lock,
            language_metadata=language_metadata,
        )
    if model_family == "m2m100":
        return M2M100CTranslate2Backend(
            tokenizer=tokenizer,
            translator=translator,
            beam_size=beam_size,
            max_decoding_length=max_decoding_length,
            tokenizer_lock=tokenizer_lock,
            inference_lock=inference_lock,
            language_metadata=language_metadata,
        )
    if model_family == "marian":
        return MarianCTranslate2Backend(
            tokenizer=tokenizer,
            translator=translator,
            beam_size=beam_size,
            max_decoding_length=max_decoding_length,
            tokenizer_lock=tokenizer_lock,
            inference_lock=inference_lock,
            language_metadata=language_metadata,
        )
    raise UnsupportedLanguagePairError(
        f"CTranslate2 translation backend does not support model family '{model_family}'. "
        "Use TRANSLATION_MODEL_FAMILY=m2m100, nllb, or marian."
    )


def missing_model_message(*, model_family: str, model_path: str) -> str:
    script = "backend/scripts/prepare_translation_ct2.sh"
    return (
        f"{model_family.upper()} CTranslate2 model not found at:\n"
        f"  {model_path}\n\n"
        f"Prepare it with:\n"
        f"  {script} {model_family}"
    )
