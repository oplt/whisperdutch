from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal

import numpy as np
from faster_whisper import WhisperModel
from faster_whisper.vad import VadOptions

from .asr_types import TranscriptionResult, empty_transcription, segments_from_whisper
from .languages import DEFAULT_SOURCE_LANGUAGE, validate_language
from .logger import get_logger

logger = get_logger("asr")

InferenceKind = Literal["partial", "final"]


@dataclass(frozen=True)
class ASRDecodeConfig:
    language: str
    beam_size: int
    no_speech_threshold: float
    compression_ratio_threshold: float
    condition_on_previous_text: bool
    word_timestamps: bool
    vad_filter: bool


@dataclass(frozen=True)
class ASRRuntimeSettings:
    model_name: str
    device: str
    compute_type: str
    partial_word_timestamps: bool
    final_word_timestamps: bool
    vad_filter: bool
    vad_min_silence_ms: int
    vad_min_speech_ms: int
    vad_speech_pad_ms: int


def _cuda_info() -> dict[str, Any]:
    try:
        import ctranslate2

        count = int(ctranslate2.get_cuda_device_count())
        return {
            "cuda_available": count > 0,
            "cuda_device_count": count,
            "ctranslate2_version": ctranslate2.__version__,
        }
    except Exception as exc:
        return {
            "cuda_available": False,
            "cuda_device_count": 0,
            "cuda_error": str(exc),
        }


def _auto_device() -> str:
    requested = os.getenv("ASR_DEVICE", "auto").strip().lower()
    if requested != "auto":
        return requested
    return "cuda" if _cuda_info().get("cuda_available") else "cpu"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _resolve_model_name() -> str:
    explicit = os.getenv("ASR_MODEL", "").strip()
    if explicit:
        return explicit
    return os.getenv("BALANCED_ASR_MODEL", "large-v3-turbo").strip() or "large-v3-turbo"


class TranscriptionEngine:
    def __init__(self) -> None:
        self.model_name = _resolve_model_name()
        self.device = _auto_device()
        self.compute_type = os.getenv(
            "ASR_COMPUTE_TYPE",
            "float16" if self.device == "cuda" else "int8",
        )
        self.cpu_threads = int(os.getenv("ASR_CPU_THREADS", "4"))
        self.runtime_settings = self._load_runtime_settings()
        self.decode_configs = self._build_decode_configs()

        logger.info(
            "asr_model_loading model=%s device=%s compute_type=%s cpu_threads=%s vad=%s partial_word_ts=%s final_word_ts=%s",
            self.model_name,
            self.device,
            self.compute_type,
            self.cpu_threads,
            self.runtime_settings.vad_filter,
            self.runtime_settings.partial_word_timestamps,
            self.runtime_settings.final_word_timestamps,
        )
        self.model = WhisperModel(
            self.model_name,
            device=self.device,
            compute_type=self.compute_type,
            cpu_threads=self.cpu_threads,
        )
        logger.info("asr_model_ready info=%s", self.info())

    def info(self) -> dict[str, Any]:
        return {
            "asr_model": self.model_name,
            "asr_device": self.device,
            "asr_compute_type": self.compute_type,
            "asr_cpu_threads": self.cpu_threads,
            "asr_vad_filter": self.runtime_settings.vad_filter,
            "asr_partial_word_timestamps": self.runtime_settings.partial_word_timestamps,
            "asr_final_word_timestamps": self.runtime_settings.final_word_timestamps,
            "asr_vad_min_silence_ms": self.runtime_settings.vad_min_silence_ms,
            "asr_vad_min_speech_ms": self.runtime_settings.vad_min_speech_ms,
            "asr_vad_speech_pad_ms": self.runtime_settings.vad_speech_pad_ms,
            "decode_modes": {
                mode: {
                    "beam_size": config.beam_size,
                    "word_timestamps": config.word_timestamps,
                    "vad_filter": config.vad_filter,
                }
                for mode, config in self.decode_configs.items()
            },
            **_cuda_info(),
        }

    def warmup(self) -> None:
        logger.info("asr_warmup_started")
        audio = np.zeros(16000, dtype=np.float32)
        _ = self.transcribe(audio, language=self.default_language, mode="fast", inference_kind="final", warmup=True)
        logger.info("asr_warmup_completed")

    def transcribe_dutch(self, audio_16k: np.ndarray, prompt: str | None = None, mode: str = "balanced") -> str:
        return self.transcribe(audio_16k, language="nl", prompt=prompt, mode=mode)

    def transcribe_dutch_result(self, audio_16k: np.ndarray, prompt: str | None = None, mode: str = "balanced") -> TranscriptionResult:
        return self.transcribe_result(audio_16k, language="nl", prompt=prompt, mode=mode)

    def transcribe(
        self,
        audio_16k: np.ndarray,
        *,
        language: str = DEFAULT_SOURCE_LANGUAGE,
        prompt: str | None = None,
        mode: str = "balanced",
        inference_kind: InferenceKind = "final",
        warmup: bool = False,
    ) -> str:
        return self.transcribe_result(
            audio_16k,
            language=language,
            prompt=prompt,
            mode=mode,
            inference_kind=inference_kind,
            warmup=warmup,
        ).text

    def transcribe_result(
        self,
        audio_16k: np.ndarray,
        *,
        language: str = DEFAULT_SOURCE_LANGUAGE,
        prompt: str | None = None,
        mode: str = "balanced",
        inference_kind: InferenceKind = "final",
        warmup: bool = False,
    ) -> TranscriptionResult:
        language = validate_language(language)
        if audio_16k.size < 1600:
            return empty_transcription(language=language, quality={"level": "empty", "reason": "too_short", "warmup": warmup})

        mode = (mode or "balanced").lower()
        decode = self.decode_configs.get(mode, self.decode_configs["balanced"])

        logger.debug(
            "asr_transcribe_started samples=%s mode=%s inference=%s beam_size=%s word_timestamps=%s vad=%s prompt_present=%s",
            audio_16k.size,
            mode,
            inference_kind,
            decode.beam_size,
            decode.word_timestamps,
            decode.vad_filter,
            bool(prompt),
        )
        started = __import__("time").perf_counter()
        segments, info = self.model.transcribe(
            audio_16k,
            language=language,
            task="transcribe",
            beam_size=decode.beam_size,
            best_of=1,
            temperature=0.0,
            vad_filter=decode.vad_filter,
            vad_parameters=self._vad_parameters() if decode.vad_filter else None,
            condition_on_previous_text=decode.condition_on_previous_text,
            initial_prompt=prompt,
            no_speech_threshold=decode.no_speech_threshold,
            compression_ratio_threshold=decode.compression_ratio_threshold,
            without_timestamps=not decode.word_timestamps,
            word_timestamps=decode.word_timestamps,
        )
        segment_list = list(segments)
        duration = round(float(audio_16k.size) / 16000.0, 3)
        result = segments_from_whisper(segment_list, language=language, duration=duration)
        asr_seconds = __import__("time").perf_counter() - started
        quality = dict(result.quality)
        quality.update(
            {
                "warmup": warmup,
                "inference_kind": inference_kind,
                "mode": mode,
                "asr_seconds": round(asr_seconds, 3),
                "realtime_factor": round(asr_seconds / duration, 3) if duration > 0 else 0.0,
                "detected_language": getattr(info, "language", language),
                "vad_filter": decode.vad_filter,
                "word_timestamps": decode.word_timestamps,
            }
        )
        logger.debug(
            "asr_transcribe_completed chars=%s segments=%s words=%s rtf=%.3f",
            len(result.text),
            len(result.segments),
            len(result.words),
            quality.get("realtime_factor") or 0.0,
        )
        return TranscriptionResult(
            text=result.text,
            segments=result.segments,
            words=result.words,
            language=result.language,
            duration=result.duration,
            quality=quality,
        )

    def _load_runtime_settings(self) -> ASRRuntimeSettings:
        default_word_ts = _env_bool("ASR_WORD_TIMESTAMPS", True)
        return ASRRuntimeSettings(
            model_name=self.model_name,
            device=self.device,
            compute_type=self.compute_type,
            partial_word_timestamps=_env_bool("PARTIAL_ASR_WORD_TIMESTAMPS", False),
            final_word_timestamps=_env_bool("FINAL_ASR_WORD_TIMESTAMPS", default_word_ts),
            vad_filter=_env_bool("ASR_VAD_FILTER", True),
            vad_min_silence_ms=_positive_env_int("ASR_VAD_MIN_SILENCE_MS", 300),
            vad_min_speech_ms=_positive_env_int("ASR_VAD_MIN_SPEECH_MS", 150),
            vad_speech_pad_ms=_positive_env_int("ASR_VAD_SPEECH_PAD_MS", 200),
        )

    def _vad_parameters(self) -> VadOptions:
        return VadOptions(
            min_silence_duration_ms=self.runtime_settings.vad_min_silence_ms,
            min_speech_duration_ms=self.runtime_settings.vad_min_speech_ms,
            speech_pad_ms=self.runtime_settings.vad_speech_pad_ms,
        )

    def _build_decode_configs(self) -> dict[str, ASRDecodeConfig]:
        language = os.getenv("ASR_LANGUAGE", "nl").strip().lower()
        if not language:
            raise ValueError("ASR_LANGUAGE must not be empty")
        self.default_language = validate_language(language)
        no_speech = _bounded_env_float("ASR_NO_SPEECH_THRESHOLD", 0.6, minimum=0.0, maximum=1.0)
        compression = _bounded_env_float("ASR_COMPRESSION_RATIO_THRESHOLD", 2.4, minimum=0.01)
        previous_text = _env_bool("ASR_CONDITION_ON_PREVIOUS_TEXT", False)
        beams = {
            "fast": _positive_env_int("FAST_ASR_BEAM_SIZE", 1),
            "balanced": _positive_env_int("BALANCED_ASR_BEAM_SIZE", _positive_env_int("ASR_BEAM_SIZE", 2)),
            "quality": _positive_env_int("QUALITY_ASR_BEAM_SIZE", 3),
        }
        configs: dict[str, ASRDecodeConfig] = {}
        for mode, beam in beams.items():
            word_timestamps = (
                self.runtime_settings.partial_word_timestamps
                if mode == "fast"
                else self.runtime_settings.final_word_timestamps
            )
            configs[mode] = ASRDecodeConfig(
                language=self.default_language,
                beam_size=beam,
                no_speech_threshold=no_speech,
                compression_ratio_threshold=compression,
                condition_on_previous_text=previous_text,
                word_timestamps=word_timestamps,
                vad_filter=self.runtime_settings.vad_filter,
            )
        return configs


def _positive_env_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _bounded_env_float(name: str, default: float, *, minimum: float, maximum: float | None = None) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if value < minimum or (maximum is not None and value > maximum):
        suffix = f" and at most {maximum}" if maximum is not None else ""
        raise ValueError(f"{name} must be at least {minimum}{suffix}")
    return value


@lru_cache(maxsize=1)
def get_asr_engine() -> TranscriptionEngine:
    return TranscriptionEngine()
