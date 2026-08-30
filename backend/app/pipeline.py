from __future__ import annotations

import time
from typing import Any

import numpy as np

from .asr import get_asr_engine
from .audio import SpeechSegmenter
from .logger import get_logger, preview_text
from .schemas import ClientConfig
from .sentences import SentenceAssembler
from .translator import get_translation_engine

logger = get_logger("pipeline")


def transcribe_and_collect_sentences(
    audio: np.ndarray,
    config: ClientConfig,
    sentence_assembler: SentenceAssembler,
    force: bool,
) -> tuple[list[str], dict[str, Any]]:
    start = time.perf_counter()
    asr = get_asr_engine()

    sentence_assembler.configure(config.source_lang, config.context_prompt)
    prompt = sentence_assembler.context_prompt()
    transcription = asr.transcribe_result(audio, language=config.source_lang, prompt=prompt, mode=config.mode)
    source_fragment = transcription.text
    sentences, _buffer = sentence_assembler.add_fragment(source_fragment, force=force)
    asr_latency_ms = int((time.perf_counter() - start) * 1000)

    audio_seconds = round(float(len(audio)) / float(config.sample_rate), 2)
    realtime_factor = round((asr_latency_ms / 1000.0) / audio_seconds, 3) if audio_seconds > 0 else 0.0
    meta = {
        "asr_latency_ms": asr_latency_ms,
        "audio_seconds": audio_seconds,
        "fragment": source_fragment,
        "realtime_factor": realtime_factor,
        "quality": transcription.quality,
    }
    logger.info(
        "asr_completed mode=%s audio_seconds=%.2f latency_ms=%s realtime_factor=%.3f sentences=%s fragment=%s",
        config.mode,
        audio_seconds,
        asr_latency_ms,
        realtime_factor,
        len(sentences),
        preview_text(source_fragment),
    )
    return sentences, meta


def transcribe_partial(audio: np.ndarray, config: ClientConfig, prompt: str | None) -> tuple[str, dict[str, Any]]:
    start = time.perf_counter()
    result = get_asr_engine().transcribe_result(audio, language=config.source_lang, prompt=prompt, mode="fast")
    latency_ms = int((time.perf_counter() - start) * 1000)
    audio_seconds = round(float(len(audio)) / float(config.sample_rate), 2)
    return result.text, {"latency_ms": latency_ms, "audio_seconds": audio_seconds, "quality": result.quality}


def translate_many_sentences(sentences: list[str], config: ClientConfig | None = None) -> list[str]:
    translator = get_translation_engine()
    if config is None:
        return translator.translate_many(sentences)
    return translator.translate_many(
        sentences,
        source_language=config.source_lang,
        target_language=config.target_lang,
    )


def adapt_segmenter(segmenter: SpeechSegmenter, config: ClientConfig, realtime_factor: float) -> None:
    if config.mode == "quality" or realtime_factor <= 0:
        return

    min_segment = 1.8 if config.mode == "fast" else 2.5
    min_silence = 0.25 if config.mode == "fast" else 0.35

    if realtime_factor > 1.0:
        segmenter.max_segment_seconds = max(min_segment, segmenter.max_segment_seconds * 0.85)
        segmenter.end_silence_seconds = max(min_silence, segmenter.end_silence_seconds * 0.90)
        logger.debug(
            "adaptive_segmentation_tightened mode=%s realtime_factor=%.3f max_segment_seconds=%.2f end_silence_seconds=%.2f",
            config.mode,
            realtime_factor,
            segmenter.max_segment_seconds,
            segmenter.end_silence_seconds,
        )
    elif realtime_factor < 0.45:
        before_segment = segmenter.max_segment_seconds
        segmenter.set_mode(config.mode)
        segmenter.max_segment_seconds = min(segmenter.max_segment_seconds, before_segment * 1.05)
