from __future__ import annotations

import os
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path
from typing import Any

from .logger import get_logger

logger = get_logger("translator")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _torch_cuda_available() -> bool:
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _torch_available() -> bool:
    try:
        import torch  # noqa: F401
        return True
    except Exception:
        return False


class TranslationEngine:
    """
    Dutch -> English translation engine.

    Preferred production path:
      TRANSLATION_ENGINE=ctranslate2
      TRANSLATION_MODEL=models/opus-mt-nl-en-ct2
      TRANSLATION_TOKENIZER=Helsinki-NLP/opus-mt-nl-en

    Fallback path:
      TRANSLATION_ENGINE=transformers
      TRANSLATION_MODEL=Helsinki-NLP/opus-mt-nl-en
    """

    def __init__(self) -> None:
        self.engine = os.getenv("TRANSLATION_ENGINE", "auto").strip().lower()
        self.model_name = os.getenv("TRANSLATION_MODEL", "models/opus-mt-nl-en-ct2")
        self.tokenizer_name = os.getenv("TRANSLATION_TOKENIZER", "Helsinki-NLP/opus-mt-nl-en")
        self.compute_type = os.getenv("TRANSLATION_COMPUTE_TYPE", "float16")
        self.beam_size = int(os.getenv("TRANSLATION_BEAM_SIZE", "1"))
        self.max_decoding_length = int(os.getenv("TRANSLATION_MAX_TOKENS", "160"))
        self.cache: OrderedDict[str, str] = OrderedDict()
        self.max_cache_items = int(os.getenv("TRANSLATION_CACHE_ITEMS", "4096"))

        requested_device = os.getenv("TRANSLATION_DEVICE", "auto").strip().lower()
        self.device = "cuda" if requested_device != "cpu" and _torch_cuda_available() else "cpu"

        if self.engine == "auto":
            if Path(self.model_name).exists():
                self.engine = "ctranslate2"
            elif _torch_available():
                self.engine = "transformers"
            else:
                raise RuntimeError(
                    f"CTranslate2 model not found: {self.model_name}. "
                    "Run backend/scripts/prepare_translation_ct2.sh, or install PyTorch for transformers fallback."
                )

        if self.engine == "ctranslate2":
            self._load_ctranslate2()
        elif self.engine == "transformers":
            self._load_transformers()
        else:
            raise ValueError("TRANSLATION_ENGINE must be auto, ctranslate2, or transformers")

        logger.info("translation_model_ready info=%s", self.info())

    def _load_ctranslate2(self) -> None:
        import ctranslate2
        from transformers import AutoTokenizer

        model_path = Path(self.model_name)
        if not model_path.exists():
            raise FileNotFoundError(
                f"CTranslate2 model not found: {model_path}. Run scripts/prepare_translation_ct2.sh first."
            )

        logger.info(
            "translation_ctranslate2_loading model=%s tokenizer=%s device=%s compute_type=%s",
            model_path,
            self.tokenizer_name,
            self.device,
            self.compute_type,
        )
        self.tokenizer = AutoTokenizer.from_pretrained(self.tokenizer_name)
        self.translator = ctranslate2.Translator(str(model_path), device=self.device, compute_type=self.compute_type)
        self.model = None

    def _load_transformers(self) -> None:
        import torch
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

        model_name = os.getenv("TRANSFORMERS_TRANSLATION_MODEL", "Helsinki-NLP/opus-mt-nl-en")
        logger.info("translation_transformers_loading model=%s device=%s", model_name, self.device)
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = AutoModelForSeq2SeqLM.from_pretrained(model_name)
        self.model.to(self.device)
        if self.device == "cuda" and _env_bool("TRANSLATION_FP16", True):
            self.model.half()
        self.model.eval()
        if self.device == "cuda":
            torch.backends.cuda.matmul.allow_tf32 = True
        self.translator = None

    def info(self) -> dict[str, Any]:
        return {
            "translation_engine": self.engine,
            "translation_model": self.model_name,
            "translation_tokenizer": self.tokenizer_name,
            "translation_device": self.device,
            "translation_compute_type": self.compute_type,
            "translation_beam_size": self.beam_size,
            "translation_cache_items": len(self.cache),
        }

    def warmup(self) -> None:
        logger.info("translation_warmup_started")
        _ = self.translate("Hallo, dit is een test.")
        logger.info("translation_warmup_completed")

    def translate(self, text: str) -> str:
        translations = self.translate_many([text])
        return translations[0] if translations else ""

    def translate_many(self, texts: list[str]) -> list[str]:
        normalized = [" ".join(text.strip().split()) for text in texts]
        results: list[str | None] = []
        uncached: list[str] = []
        uncached_indexes: list[int] = []

        for index, text in enumerate(normalized):
            if not text:
                results.append("")
                continue
            cached = self.cache.get(text)
            if cached is not None:
                self.cache.move_to_end(text)
                logger.debug("translation_cache_hit chars=%s", len(text))
                results.append(cached)
                continue
            results.append(None)
            uncached.append(text)
            uncached_indexes.append(index)

        if uncached:
            logger.debug("translation_batch_started engine=%s count=%s chars=%s", self.engine, len(uncached), sum(len(text) for text in uncached))
            if self.engine == "ctranslate2":
                translated = self._translate_ctranslate2_many(uncached)
            else:
                translated = self._translate_transformers_many(uncached)

            for index, source_text, translated_text in zip(uncached_indexes, uncached, translated, strict=False):
                self._cache_set(source_text, translated_text)
                results[index] = translated_text
            logger.debug("translation_batch_completed count=%s", len(uncached))

        return [str(item or "") for item in results]

    def _cache_set(self, text: str, translated: str) -> None:
        if self.max_cache_items <= 0:
            return
        self.cache[text] = translated
        self.cache.move_to_end(text)
        while len(self.cache) > self.max_cache_items:
            evicted, _ = self.cache.popitem(last=False)
            logger.debug("translation_cache_evicted chars=%s", len(evicted))

    def _translate_legacy(self, text: str) -> str:
        text = " ".join(text.strip().split())
        if not text:
            return ""
        cached = self.cache.get(text)
        if cached is not None:
            self.cache.move_to_end(text)
            logger.debug("translation_cache_hit chars=%s", len(text))
            return cached

        logger.debug("translation_started engine=%s chars=%s", self.engine, len(text))
        if self.engine == "ctranslate2":
            translated = self._translate_ctranslate2(text)
        else:
            translated = self._translate_transformers(text)

        self._cache_set(text, translated)
        logger.debug("translation_completed output_chars=%s", len(translated))
        return translated

    def _translate_ctranslate2(self, text: str) -> str:
        translations = self._translate_ctranslate2_many([text])
        return translations[0] if translations else ""

    def _translate_ctranslate2_many(self, texts: list[str]) -> list[str]:
        if not texts:
            return []
        source_batches = []
        for text in texts:
            input_ids = self.tokenizer.encode(text, add_special_tokens=True, truncation=True, max_length=160)
            source_batches.append(self.tokenizer.convert_ids_to_tokens(input_ids))
        results = self.translator.translate_batch(
            source_batches,
            beam_size=self.beam_size,
            max_decoding_length=self.max_decoding_length,
            return_scores=False,
        )
        translations: list[str] = []
        for result in results:
            output_tokens = result.hypotheses[0]
            output_ids = self.tokenizer.convert_tokens_to_ids(output_tokens)
            translations.append(self.tokenizer.decode(output_ids, skip_special_tokens=True).strip())
        return translations

    def _translate_ctranslate2_old(self, text: str) -> str:
        input_ids = self.tokenizer.encode(text, add_special_tokens=True, truncation=True, max_length=160)
        source_tokens = self.tokenizer.convert_ids_to_tokens(input_ids)
        results = self.translator.translate_batch(
            [source_tokens],
            beam_size=self.beam_size,
            max_decoding_length=self.max_decoding_length,
            return_scores=False,
        )
        output_tokens = results[0].hypotheses[0]
        output_ids = self.tokenizer.convert_tokens_to_ids(output_tokens)
        return self.tokenizer.decode(output_ids, skip_special_tokens=True).strip()

    def _translate_transformers(self, text: str) -> str:
        translations = self._translate_transformers_many([text])
        return translations[0] if translations else ""

    def _translate_transformers_many(self, texts: list[str]) -> list[str]:
        if not texts:
            return []
        import torch

        with torch.inference_mode():
            inputs = self.tokenizer(texts, return_tensors="pt", padding=True, truncation=True, max_length=160).to(self.device)
            output_ids = self.model.generate(
                **inputs,
                max_new_tokens=self.max_decoding_length,
                num_beams=self.beam_size,
                do_sample=False,
                use_cache=True,
            )
            return [self.tokenizer.decode(row, skip_special_tokens=True).strip() for row in output_ids]


@lru_cache(maxsize=1)
def get_translation_engine() -> TranslationEngine:
    return TranslationEngine()
