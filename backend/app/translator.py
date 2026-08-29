from __future__ import annotations

import hashlib
import json
import os
import time
from collections import OrderedDict, deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from threading import RLock
from typing import Any

from .logger import get_logger
from .translation_cache import DurableTranslationCache

logger = get_logger("translator")

TRANSLATION_CACHE_SCHEMA_VERSION = 1
TRANSLATION_CACHE_SAMPLE_LIMIT = 1000


def _normalize_source_text(text: str) -> str:
    return " ".join(text.strip().split())


@dataclass(frozen=True)
class TranslationCacheKey:
    source_text: str
    source_language: str
    target_language: str
    translation_engine: str
    model_name: str
    tokenizer_name: str
    beam_size: int
    max_decoding_length: int
    glossary_version: str
    schema_version: int = TRANSLATION_CACHE_SCHEMA_VERSION


@dataclass
class _InFlightTranslation:
    generation: int
    future: Future[str]


def _normalize_language(language: str) -> str:
    return language.strip().lower()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _glossary_cache_version() -> str:
    if not _env_bool("GLOSSARY_ENABLED", False):
        return "disabled"

    path = Path(os.getenv("GLOSSARY_PATH", "config/glossary.tsv"))
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return "missing"
    except OSError:
        logger.warning("translation_cache_glossary_unavailable path=%s", path)
        return "unavailable"
    return f"sha256:{digest}"


def _translation_cache_db_path() -> Path:
    default_path = Path(__file__).resolve().parents[1] / "logs" / "translation-cache.sqlite3"
    return Path(os.getenv("TRANSLATION_CACHE_DB", str(default_path)))


def _translation_cache_ttl() -> float:
    raw = os.getenv("TRANSLATION_CACHE_TTL_SECONDS", "0").strip()
    try:
        ttl = float(raw)
    except ValueError as exc:
        raise ValueError("TRANSLATION_CACHE_TTL_SECONDS must be a non-negative number") from exc
    if ttl < 0:
        raise ValueError("TRANSLATION_CACHE_TTL_SECONDS must be a non-negative number")
    return ttl


def _cache_key_id(key: TranslationCacheKey) -> str:
    serialized = json.dumps(asdict(key), separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _cuda_available() -> bool:
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
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
        self.max_cache_items = int(os.getenv("TRANSLATION_CACHE_ITEMS", "4096"))
        self.cache_backend = os.getenv("TRANSLATION_CACHE_BACKEND", "memory").strip().lower()
        if self.cache_backend not in {"memory", "sqlite"}:
            raise ValueError("TRANSLATION_CACHE_BACKEND must be memory or sqlite")
        self.cache_ttl_seconds = _translation_cache_ttl()
        self.tokenizer: Any = None
        self.translator: Any = None
        self.model: Any = None
        self.cache: OrderedDict[TranslationCacheKey, str] = OrderedDict()
        self._cache_lock = RLock()
        self._inflight: dict[TranslationCacheKey, _InFlightTranslation] = {}
        self._cache_generation = 0
        self.cache_schema_version = TRANSLATION_CACHE_SCHEMA_VERSION
        self.glossary_version = _glossary_cache_version()
        self._cache_hits = 0
        self._cache_misses = 0
        self._cache_evictions = 0
        self._cache_sets = 0
        self._single_flight_waits = 0
        self._durable_hits = 0
        self._durable_misses = 0
        self._durable_read_failures = 0
        self._durable_write_failures = 0
        self._cache_hit_latencies_ms: deque[float] = deque(maxlen=TRANSLATION_CACHE_SAMPLE_LIMIT)
        self._cache_miss_lookup_latencies_ms: deque[float] = deque(maxlen=TRANSLATION_CACHE_SAMPLE_LIMIT)
        self._cache_miss_translation_latencies_ms: deque[float] = deque(maxlen=TRANSLATION_CACHE_SAMPLE_LIMIT)
        self.durable_cache: DurableTranslationCache | None = None
        self._durable_executor: ThreadPoolExecutor | None = None
        if self.cache_backend == "sqlite" and self.max_cache_items > 0:
            self.durable_cache = DurableTranslationCache(
                _translation_cache_db_path(),
                max_items=self.max_cache_items,
                ttl_seconds=self.cache_ttl_seconds,
            )
            if self.durable_cache.enabled:
                self._durable_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="translation-cache")

        requested_device = os.getenv("TRANSLATION_DEVICE", "auto").strip().lower()
        self.device = "cuda" if requested_device != "cpu" and _cuda_available() else "cpu"

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
            raise FileNotFoundError(f"CTranslate2 model not found: {model_path}. Run scripts/prepare_translation_ct2.sh first.")

        logger.info(
            "translation_ctranslate2_loading model=%s tokenizer=%s device=%s compute_type=%s",
            model_path,
            self.tokenizer_name,
            self.device,
            self.compute_type,
        )
        self.tokenizer = AutoTokenizer.from_pretrained(
            self.tokenizer_name,
            local_files_only=_env_bool("LOCAL_MODELS_ONLY", True),
        )
        self.translator = ctranslate2.Translator(str(model_path), device=self.device, compute_type=self.compute_type)
        self.model = None

    def _load_transformers(self) -> None:
        import torch
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

        model_name = os.getenv("TRANSFORMERS_TRANSLATION_MODEL", "Helsinki-NLP/opus-mt-nl-en")
        self.model_name = model_name
        self.tokenizer_name = model_name
        logger.info("translation_transformers_loading model=%s device=%s", model_name, self.device)
        local_only = _env_bool("LOCAL_MODELS_ONLY", True)
        self.tokenizer = AutoTokenizer.from_pretrained(model_name, local_files_only=local_only)
        self.model = AutoModelForSeq2SeqLM.from_pretrained(model_name, local_files_only=local_only)
        self.model.to(self.device)
        if self.device == "cuda" and _env_bool("TRANSLATION_FP16", True):
            self.model.half()
        self.model.eval()
        if self.device == "cuda":
            torch.backends.cuda.matmul.allow_tf32 = True
        self.translator = None

    def info(self) -> dict[str, Any]:
        cache_info = self.cache_info()
        return {
            "translation_engine": self.engine,
            "translation_model": self.model_name,
            "translation_tokenizer": self.tokenizer_name,
            "translation_device": self.device,
            "translation_compute_type": self.compute_type,
            "translation_beam_size": self.beam_size,
            "translation_cache_items": cache_info["size"],
            "translation_cache": cache_info,
        }

    def cache_info(self) -> dict[str, Any]:
        with self._cache_lock:
            info = self._cache_info_locked()
        info["durable"] = self._durable_info()
        return info

    def _cache_info_locked(self) -> dict[str, Any]:
        total_lookups = self._cache_hits + self._cache_misses
        return {
            "disabled": self.max_cache_items <= 0,
            "schema_version": self.cache_schema_version,
            "hits": self._cache_hits,
            "misses": self._cache_misses,
            "evictions": self._cache_evictions,
            "sets": self._cache_sets,
            "single_flight_waits": self._single_flight_waits,
            "inflight": len(self._inflight),
            "backend": self.cache_backend,
            "ttl_seconds": self.cache_ttl_seconds,
            "max_items": self.max_cache_items,
            "size": len(self.cache),
            "hit_ratio": self._cache_hits / total_lookups if total_lookups else 0.0,
            "glossary_version": self.glossary_version,
            "latency_ms": {
                "cache_hit": _sample_summary(self._cache_hit_latencies_ms),
                "cache_miss_lookup": _sample_summary(self._cache_miss_lookup_latencies_ms),
                "cache_miss_translation": _sample_summary(self._cache_miss_translation_latencies_ms),
            },
            "acceptance": {
                "cache_hit_under_5ms": _under_threshold(self._cache_hit_latencies_ms, 5.0),
                "cache_hit_samples": len(self._cache_hit_latencies_ms),
            },
        }

    def _durable_info(self) -> dict[str, Any]:
        with self._cache_lock:
            durable_cache = self.durable_cache
            backend = self.cache_backend
            ttl_seconds = self.cache_ttl_seconds
            hits = self._durable_hits
            misses = self._durable_misses
            read_failures = self._durable_read_failures
            write_failures = self._durable_write_failures
        if durable_cache is None:
            return {
                "backend": backend,
                "enabled": False,
                "path": None,
                "ttl_seconds": ttl_seconds,
                "hits": hits,
                "misses": misses,
                "read_failures": read_failures,
                "write_failures": write_failures,
                "size": 0,
            }
        info = durable_cache.info()
        info.update(
            {
                "engine_hits": hits,
                "engine_misses": misses,
                "read_failures": read_failures,
                "write_failures": write_failures,
            }
        )
        return info

    def clear_cache(self, reason: str) -> dict[str, Any]:
        reason = reason.strip()[:120] or "unspecified"
        stats_before = self.cache_info()
        with self._cache_lock:
            cleared = len(self.cache)
            self.cache.clear()
            self._cache_generation += 1
        durable_cleared = self._clear_durable_cache()
        logger.info(
            "translation_cache_cleared reason=%s cleared=%s durable_cleared=%s stats_before=%s",
            reason,
            cleared,
            durable_cleared,
            stats_before,
        )
        return {
            "cleared": cleared,
            "durable_cleared": durable_cleared,
            "reason": reason,
            "stats_before": stats_before,
        }

    def _clear_durable_cache(self) -> int:
        durable_cache = self.durable_cache
        if durable_cache is None or not durable_cache.enabled:
            return 0
        try:
            if self._durable_executor is None:
                return durable_cache.clear()
            return self._durable_executor.submit(durable_cache.clear).result(timeout=5.0)
        except Exception:
            logger.exception("durable_translation_cache_clear_failed")
            return 0

    def refresh_glossary_version(self, reason: str = "glossary_updated") -> dict[str, Any]:
        new_version = _glossary_cache_version()
        with self._cache_lock:
            previous_version = self.glossary_version
            self.glossary_version = new_version
        result = self.clear_cache(reason)
        result.update(
            {
                "previous_glossary_version": previous_version,
                "glossary_version": self.glossary_version,
            }
        )
        logger.info(
            "translation_cache_glossary_version_refreshed changed=%s",
            previous_version != new_version,
        )
        return result

    def cache_key(
        self,
        text: str,
        *,
        source_language: str = "nl",
        target_language: str = "en",
    ) -> TranslationCacheKey:
        with self._cache_lock:
            return self._cache_key_locked(text, source_language, target_language)

    def _cache_key_locked(
        self,
        text: str,
        source_language: str,
        target_language: str,
    ) -> TranslationCacheKey:
        return TranslationCacheKey(
            source_text=_normalize_source_text(text),
            source_language=_normalize_language(source_language),
            target_language=_normalize_language(target_language),
            translation_engine=self.engine,
            model_name=self.model_name,
            tokenizer_name=self.tokenizer_name,
            beam_size=self.beam_size,
            max_decoding_length=self.max_decoding_length,
            glossary_version=self.glossary_version,
            schema_version=self.cache_schema_version,
        )

    def warmup(self) -> None:
        logger.info("translation_warmup_started")
        _ = self.translate("Hallo, dit is een test.")
        logger.info("translation_warmup_completed")

    def translate(
        self,
        text: str,
        *,
        source_language: str = "nl",
        target_language: str = "en",
    ) -> str:
        translations = self.translate_many(
            [text],
            source_language=source_language,
            target_language=target_language,
        )
        return translations[0] if translations else ""

    def translate_many(
        self,
        texts: list[str],
        *,
        source_language: str = "nl",
        target_language: str = "en",
    ) -> list[str]:
        results: list[str | None] = []
        owners: list[tuple[int, TranslationCacheKey, _InFlightTranslation]] = []
        waiters: list[tuple[int, Future[str]]] = []

        with self._cache_lock:
            keys = [self._cache_key_locked(text, source_language, target_language) for text in texts]
            generation = self._cache_generation
            for index, key in enumerate(keys):
                if not key.source_text:
                    results.append("")
                    continue
                lookup_started = time.perf_counter()
                cached = self._memory_cache_get_locked(key)
                lookup_latency_ms = (time.perf_counter() - lookup_started) * 1000
                if cached is not None:
                    self._record_cache_lookup_locked(True, lookup_latency_ms)
                    results.append(cached)
                    continue
                results.append(None)
                inflight = self._inflight.get(key)
                if inflight is None or inflight.generation != generation:
                    inflight = _InFlightTranslation(generation=generation, future=Future())
                    self._inflight[key] = inflight
                    owners.append((index, key, inflight))
                else:
                    self._cache_misses += 1
                    self._record_cache_lookup_locked(False, lookup_latency_ms)
                    self._single_flight_waits += 1
                    waiters.append((index, inflight.future))

        owners = self._resolve_durable_owners(owners, results)
        if owners:
            source_texts = [key.source_text for _, key, _ in owners]
            logger.debug(
                "translation_batch_started engine=%s count=%s chars=%s",
                self.engine,
                len(source_texts),
                sum(len(text) for text in source_texts),
            )
            translation_started = time.perf_counter()
            try:
                if self.engine == "ctranslate2":
                    translated = self._translate_ctranslate2_many(source_texts)
                else:
                    translated = self._translate_transformers_many(source_texts)
            except Exception as exc:
                with self._cache_lock:
                    for _, key, inflight in owners:
                        if not inflight.future.done():
                            inflight.future.set_exception(exc)
                        if self._inflight.get(key) is inflight:
                            del self._inflight[key]
                raise

            translation_latency_ms = (time.perf_counter() - translation_started) * 1000
            durable_writes: list[tuple[TranslationCacheKey, str, int]] = []
            with self._cache_lock:
                self._cache_miss_translation_latencies_ms.extend([translation_latency_ms] * len(owners))
                for offset, (index, key, inflight) in enumerate(owners):
                    translated_text = str(translated[offset] or "") if offset < len(translated) else ""
                    if self._cache_generation == inflight.generation and offset < len(translated):
                        self._cache_set_locked(key, translated_text, persist=False)
                        durable_writes.append((key, translated_text, inflight.generation))
                    results[index] = translated_text
                    if not inflight.future.done():
                        inflight.future.set_result(translated_text)
                    if self._inflight.get(key) is inflight:
                        del self._inflight[key]
            for key, translated_text, owner_generation in durable_writes:
                self._persist_durable(key, translated_text, owner_generation)
            logger.debug("translation_batch_completed count=%s", len(source_texts))

        for index, future in waiters:
            results[index] = future.result()

        return [str(item or "") for item in results]

    def _record_cache_lookup_locked(self, hit: bool, latency_ms: float) -> None:
        sample = round(max(0.0, latency_ms), 3)
        if hit:
            self._cache_hit_latencies_ms.append(sample)
        else:
            self._cache_miss_lookup_latencies_ms.append(sample)

    def _cache_get(self, key: TranslationCacheKey) -> str | None:
        with self._cache_lock:
            return self._memory_cache_get_locked(key)

    def _memory_cache_get_locked(self, key: TranslationCacheKey) -> str | None:
        if self.max_cache_items <= 0:
            return None
        cached = self.cache.get(key)
        if cached is not None:
            self.cache.move_to_end(key)
            self._cache_hits += 1
            logger.debug("translation_cache_hit chars=%s", len(key.source_text))
            return cached

        return None

    def _resolve_durable_owners(
        self,
        owners: list[tuple[int, TranslationCacheKey, _InFlightTranslation]],
        results: list[str | None],
    ) -> list[tuple[int, TranslationCacheKey, _InFlightTranslation]]:
        remaining: list[tuple[int, TranslationCacheKey, _InFlightTranslation]] = []
        durable_cache = self.durable_cache
        for index, key, inflight in owners:
            lookup_started = time.perf_counter()
            durable: str | None = None
            failed = False
            if durable_cache is not None and durable_cache.enabled:
                try:
                    durable = durable_cache.get(_cache_key_id(key))
                except Exception:
                    failed = True
                    logger.exception("durable_translation_cache_read_failed")
            lookup_latency_ms = (time.perf_counter() - lookup_started) * 1000
            with self._cache_lock:
                if failed:
                    self._durable_read_failures += 1
                if durable is None:
                    self._cache_misses += 1
                    if durable_cache is not None and durable_cache.enabled and not failed:
                        self._durable_misses += 1
                    self._record_cache_lookup_locked(False, lookup_latency_ms)
                    remaining.append((index, key, inflight))
                    continue
                self._cache_hits += 1
                self._durable_hits += 1
                self._record_cache_lookup_locked(True, lookup_latency_ms)
                if self._cache_generation == inflight.generation:
                    self._cache_set_locked(key, durable, persist=False)
                results[index] = durable
                if not inflight.future.done():
                    inflight.future.set_result(durable)
                if self._inflight.get(key) is inflight:
                    del self._inflight[key]
                logger.debug("translation_cache_durable_hit chars=%s", len(key.source_text))
        return remaining

    def _cache_set(self, key: TranslationCacheKey, translated: str) -> None:
        with self._cache_lock:
            self._cache_set_locked(key, translated)

    def _cache_set_locked(self, key: TranslationCacheKey, translated: str, *, persist: bool = True) -> None:
        if self.max_cache_items <= 0:
            return
        self.cache[key] = translated
        self.cache.move_to_end(key)
        self._cache_sets += 1
        while len(self.cache) > self.max_cache_items:
            evicted, _ = self.cache.popitem(last=False)
            self._cache_evictions += 1
            logger.debug("translation_cache_evicted chars=%s", len(evicted.source_text))
        if persist:
            self._persist_durable(key, translated, self._cache_generation)

    def _persist_durable(self, key: TranslationCacheKey, translated: str, generation: int) -> None:
        durable_cache = self.durable_cache
        if durable_cache is None or not durable_cache.enabled:
            return
        args = (_cache_key_id(key), key.source_text, translated, asdict(key))

        def write() -> None:
            try:
                durable_cache.set(*args)
            except Exception:
                with self._cache_lock:
                    self._durable_write_failures += 1
                logger.exception("durable_translation_cache_write_failed")

        with self._cache_lock:
            if generation != self._cache_generation:
                return
            executor = self._durable_executor
        if executor is None:
            write()
        else:
            executor.submit(write)

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


def _sample_summary(values: deque[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "p50": None, "p95": None, "max": None}
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "p50": round(_percentile(ordered, 0.50), 3),
        "p95": round(_percentile(ordered, 0.95), 3),
        "max": round(max(ordered), 3),
    }


def _percentile(values: list[float], percentile: float) -> float:
    if len(values) == 1:
        return values[0]
    index = (len(values) - 1) * percentile
    lower = int(index)
    upper = min(lower + 1, len(values) - 1)
    weight = index - lower
    return values[lower] * (1 - weight) + values[upper] * weight


def _under_threshold(values: deque[float], threshold: float) -> bool | None:
    if not values:
        return None
    return max(values) < threshold
