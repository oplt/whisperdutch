from __future__ import annotations

from typing import Protocol

from .languages import (
    NLLB_LANGUAGE_CODES,
    UnsupportedLanguagePairError,
    validate_language,
)


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


class M2M100CTranslate2Backend:
    model_family = "m2m100"

    def __init__(self, *, tokenizer: object, translator: object, beam_size: int, max_decoding_length: int) -> None:
        self.tokenizer = tokenizer
        self.translator = translator
        self.beam_size = beam_size
        self.max_decoding_length = max_decoding_length

    def translate_many(
        self,
        texts: list[str],
        *,
        source_language: str,
        target_language: str,
    ) -> list[str]:
        if not texts:
            return []
        source = validate_language(source_language)
        target = validate_language(target_language)
        self.tokenizer.src_lang = source
        target_id = self.tokenizer.get_lang_id(target)
        target_token = self.tokenizer.convert_ids_to_tokens([target_id])[0]
        target_prefix = [[target_token] for _ in texts]
        source_batches = []
        for text in texts:
            input_ids = self.tokenizer.encode(text, add_special_tokens=True, truncation=True, max_length=160)
            source_batches.append(self.tokenizer.convert_ids_to_tokens(input_ids))
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


class NLLBCTranslate2Backend:
    model_family = "nllb"

    def __init__(self, *, tokenizer: object, translator: object, beam_size: int, max_decoding_length: int) -> None:
        self.tokenizer = tokenizer
        self.translator = translator
        self.beam_size = beam_size
        self.max_decoding_length = max_decoding_length

    def translate_many(
        self,
        texts: list[str],
        *,
        source_language: str,
        target_language: str,
    ) -> list[str]:
        if not texts:
            return []
        source = validate_language(source_language)
        target = validate_language(target_language)
        source_code = NLLB_LANGUAGE_CODES[source]
        target_code = NLLB_LANGUAGE_CODES[target]
        self.tokenizer.src_lang = source_code
        target_id = self.tokenizer.convert_tokens_to_ids([target_code])[0]
        target_token = self.tokenizer.convert_ids_to_tokens([target_id])[0]
        target_prefix = [[target_token] for _ in texts]
        source_batches = []
        for text in texts:
            input_ids = self.tokenizer.encode(text, add_special_tokens=True, truncation=True, max_length=160)
            source_batches.append(self.tokenizer.convert_ids_to_tokens(input_ids))
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


class TransformersTranslationBackend:
    def __init__(
        self,
        *,
        model_family: str,
        tokenizer: object,
        model: object,
        device: str,
        beam_size: int,
        max_decoding_length: int,
    ) -> None:
        self.model_family = model_family
        self.tokenizer = tokenizer
        self.model = model
        self.device = device
        self.beam_size = beam_size
        self.max_decoding_length = max_decoding_length

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
        generate_options: dict[str, object] = {
            "max_new_tokens": self.max_decoding_length,
            "num_beams": self.beam_size,
            "do_sample": False,
            "use_cache": True,
        }
        if self.model_family == "m2m100":
            self.tokenizer.src_lang = source
            generate_options["forced_bos_token_id"] = self.tokenizer.get_lang_id(target)
        elif self.model_family == "nllb":
            self.tokenizer.src_lang = NLLB_LANGUAGE_CODES[source]
            target_code = NLLB_LANGUAGE_CODES[target]
            generate_options["forced_bos_token_id"] = self.tokenizer.convert_tokens_to_ids([target_code])[0]

        with torch.inference_mode():
            inputs = self.tokenizer(texts, return_tensors="pt", padding=True, truncation=True, max_length=160).to(self.device)
            output_ids = self.model.generate(**inputs, **generate_options)
            return [self.tokenizer.decode(row, skip_special_tokens=True).strip() for row in output_ids]


def create_ctranslate2_backend(
    *,
    model_family: str,
    tokenizer: object,
    translator: object,
    beam_size: int,
    max_decoding_length: int,
) -> TranslationBackend:
    if model_family == "nllb":
        return NLLBCTranslate2Backend(
            tokenizer=tokenizer,
            translator=translator,
            beam_size=beam_size,
            max_decoding_length=max_decoding_length,
        )
    if model_family == "m2m100":
        return M2M100CTranslate2Backend(
            tokenizer=tokenizer,
            translator=translator,
            beam_size=beam_size,
            max_decoding_length=max_decoding_length,
        )
    raise UnsupportedLanguagePairError(
        f"CTranslate2 translation backend does not support model family '{model_family}'. "
        "Use TRANSLATION_MODEL_FAMILY=m2m100 or nllb."
    )


def missing_model_message(*, model_family: str, model_path: str) -> str:
    script = "backend/scripts/prepare_translation_ct2.sh"
    return (
        f"{model_family.upper()} CTranslate2 model not found at:\n"
        f"  {model_path}\n\n"
        f"Prepare it with:\n"
        f"  {script} {model_family}"
    )
