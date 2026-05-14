from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any


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
        self.cache: dict[str, str] = {}
        self.max_cache_items = int(os.getenv("TRANSLATION_CACHE_ITEMS", "4096"))

        requested_device = os.getenv("TRANSLATION_DEVICE", "auto").strip().lower()
        self.device = "cuda" if requested_device != "cpu" and _torch_cuda_available() else "cpu"

        if self.engine == "auto":
            self.engine = "ctranslate2" if Path(self.model_name).exists() else "transformers"

        if self.engine == "ctranslate2":
            self._load_ctranslate2()
        elif self.engine == "transformers":
            self._load_transformers()
        else:
            raise ValueError("TRANSLATION_ENGINE must be auto, ctranslate2, or transformers")

        print(f"[TRANSLATION] Ready: {self.info()}", flush=True)

    def _load_ctranslate2(self) -> None:
        import ctranslate2
        from transformers import AutoTokenizer

        model_path = Path(self.model_name)
        if not model_path.exists():
            raise FileNotFoundError(
                f"CTranslate2 model not found: {model_path}. Run scripts/prepare_translation_ct2.sh first."
            )

        print(
            f"[TRANSLATION] Loading CTranslate2 model={model_path} tokenizer={self.tokenizer_name} "
            f"device={self.device} compute_type={self.compute_type}",
            flush=True,
        )
        self.tokenizer = AutoTokenizer.from_pretrained(self.tokenizer_name)
        self.translator = ctranslate2.Translator(str(model_path), device=self.device, compute_type=self.compute_type)
        self.model = None

    def _load_transformers(self) -> None:
        import torch
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

        model_name = os.getenv("TRANSFORMERS_TRANSLATION_MODEL", "Helsinki-NLP/opus-mt-nl-en")
        print(f"[TRANSLATION] Loading Transformers model={model_name} device={self.device}", flush=True)
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
        _ = self.translate("Hallo, dit is een test.")

    def translate(self, text: str) -> str:
        text = " ".join(text.strip().split())
        if not text:
            return ""
        cached = self.cache.get(text)
        if cached is not None:
            return cached

        if self.engine == "ctranslate2":
            translated = self._translate_ctranslate2(text)
        else:
            translated = self._translate_transformers(text)

        if len(self.cache) >= self.max_cache_items:
            self.cache.clear()
        self.cache[text] = translated
        return translated

    def _translate_ctranslate2(self, text: str) -> str:
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
        import torch

        with torch.inference_mode():
            inputs = self.tokenizer(text, return_tensors="pt", truncation=True, max_length=160).to(self.device)
            output_ids = self.model.generate(
                **inputs,
                max_new_tokens=self.max_decoding_length,
                num_beams=self.beam_size,
                do_sample=False,
                use_cache=True,
            )
            return self.tokenizer.decode(output_ids[0], skip_special_tokens=True).strip()


@lru_cache(maxsize=1)
def get_translation_engine() -> TranslationEngine:
    return TranslationEngine()
