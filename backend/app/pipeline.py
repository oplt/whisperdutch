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

    prompt = sentence_assembler.context_prompt()
    transcription = asr.transcribe_dutch_result(audio, prompt=prompt, mode=config.mode)
    dutch_fragment = transcription.text
    sentences, _buffer = sentence_assembler.add_fragment(dutch_fragment, force=force)
    asr_latency_ms = int((time.perf_counter() - start) * 1000)

    audio_seconds = round(float(len(audio)) / float(config.sample_rate), 2)
    realtime_factor = round((asr_latency_ms / 1000.0) / audio_seconds, 3) if audio_seconds > 0 else 0.0
    meta = {
        "asr_latency_ms": asr_latency_ms,
        "audio_seconds": audio_seconds,
        "fragment": dutch_fragment,
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
        preview_text(dutch_fragment),
    )
    return sentences, meta


def transcribe_partial(audio: np.ndarray, config: ClientConfig, prompt: str | None) -> tuple[str, dict[str, Any]]:
    start = time.perf_counter()
    result = get_asr_engine().transcribe_dutch_result(audio, prompt=prompt, mode="fast")
    latency_ms = int((time.perf_counter() - start) * 1000)
    audio_seconds = round(float(len(audio)) / float(config.sample_rate), 2)
    return result.text, {"latency_ms": latency_ms, "audio_seconds": audio_seconds, "quality": result.quality}


def translate_one_sentence(sentence: str) -> str:
    return get_translation_engine().translate(sentence)


def translate_many_sentences(sentences: list[str]) -> list[str]:
    return get_translation_engine().translate_many(sentences)


def translate_sentences(
    sentences: list[str],
    config: ClientConfig,
    asr_latency_ms: int,
    audio_seconds: float,
    fragment: str,
) -> list[dict[str, Any]]:
    if not sentences:
        return []

    translation_start = time.perf_counter()
    translations = translate_many_sentences(sentences)
    translation_latency_ms = int((time.perf_counter() - translation_start) * 1000)

    results: list[dict[str, Any]] = []
    for sentence, translation in zip(sentences, translations, strict=False):
        subtitle_id = f"final-{time.time_ns()}"
        results.append(
            {
                "type": "final",
                "id": subtitle_id,
                "source_lang": config.source_lang,
                "target_lang": config.target_lang,
                "mode": config.mode,
                "dutch": sentence,
                "translation": translation,
                "asr_latency_ms": asr_latency_ms,
                "translation_latency_ms": translation_latency_ms,
                "latency_ms": asr_latency_ms + translation_latency_ms,
                "audio_seconds": audio_seconds,
                "asr_fragment": fragment,
                "sentence_mode": True,
            }
        )
    return results


def process_audio_segment(audio: np.ndarray, config: ClientConfig, sentence_assembler: SentenceAssembler) -> list[dict[str, Any]]:
    sentences, meta = transcribe_and_collect_sentences(audio, config, sentence_assembler, True)
    return translate_sentences(
        sentences=sentences,
        config=config,
        asr_latency_ms=int(meta.get("asr_latency_ms") or 0),
        audio_seconds=float(meta.get("audio_seconds") or 0.0),
        fragment=str(meta.get("fragment") or ""),
    )


def flush_sentences(config: ClientConfig, sentence_assembler: SentenceAssembler) -> list[dict[str, Any]]:
    sentences = sentence_assembler.flush()
    return translate_sentences(sentences, config, asr_latency_ms=0, audio_seconds=0.0, fragment="")


def adapt_segmenter(segmenter: SpeechSegmenter, config: ClientConfig, realtime_factor: float) -> None:
    if config.mode == "quality" or realtime_factor <= 0:
        return

    min_segment = 1.8 if config.mode == "fast" else 2.5
    min_silence = 0.25 if config.mode == "fast" else 0.35

    if realtime_factor > 1.0:
        segmenter.max_segment_seconds = max(min_segment, segmenter.max_segment_seconds * 0.85)
        segmenter.end_silence_seconds = max(min_silence, segmenter.end_silence_seconds * 0.90)
        logger.info(
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
