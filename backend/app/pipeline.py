from __future__ import annotations

import time
from typing import Any

import numpy as np

from .asr import get_asr_engine
from .audio import SpeechSegmenter
from .logger import get_logger, preview_text
from .schemas import ClientConfig
from .sentences import SentenceAssembler
from .subtitle_cues import SubtitleCue, SubtitleSegmenter
from .translator import get_translation_engine

logger = get_logger("pipeline")

_subtitle_segmenter = SubtitleSegmenter()


def cue_to_dict(cue: SubtitleCue) -> dict[str, Any]:
    return {
        "start": round(float(cue.start), 3),
        "end": round(float(cue.end), 3),
        "source_text": cue.source_text,
        "translated_text": cue.translated_text,
        "final": cue.final,
        "words": [
            {
                "text": word.text,
                "start": round(float(word.start), 3),
                "end": round(float(word.end), 3),
                **({"probability": round(float(word.probability), 3)} if word.probability is not None else {}),
            }
            for word in cue.words
        ],
    }


def transcribe_and_collect_sentences(
    audio: np.ndarray,
    config: ClientConfig,
    sentence_assembler: SentenceAssembler,
    force: bool,
    *,
    time_offset_seconds: float = 0.0,
) -> tuple[list[str], dict[str, Any]]:
    start = time.perf_counter()
    asr = get_asr_engine()

    sentence_assembler.configure(config.source_lang, config.context_prompt)
    prompt = sentence_assembler.context_prompt()
    transcription = asr.transcribe_result(
        audio,
        language=config.source_lang,
        prompt=prompt,
        mode=config.mode,
        inference_kind="final",
    )
    cues = _subtitle_segmenter.build_cues(
        transcription,
        time_offset_seconds=time_offset_seconds,
        sentence_assembler=sentence_assembler,
        force=force,
    )
    if cues:
        sentences = [cue.source_text for cue in cues if cue.source_text.strip()]
        source_fragment = transcription.text
    else:
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
        "cues": [cue_to_dict(cue) for cue in cues],
        "time_offset_seconds": round(time_offset_seconds, 3),
        "word_count": len(transcription.words),
        "segment_count": len(transcription.segments),
    }
    logger.info(
        "asr_completed mode=%s audio_seconds=%.2f latency_ms=%s realtime_factor=%.3f sentences=%s cues=%s fragment=%s",
        config.mode,
        audio_seconds,
        asr_latency_ms,
        realtime_factor,
        len(sentences),
        len(cues),
        preview_text(source_fragment),
    )
    return sentences, meta


def transcribe_partial(audio: np.ndarray, config: ClientConfig, prompt: str | None) -> tuple[str, dict[str, Any]]:
    start = time.perf_counter()
    result = get_asr_engine().transcribe_result(
        audio,
        language=config.source_lang,
        prompt=prompt,
        mode="fast",
        inference_kind="partial",
    )
    latency_ms = int((time.perf_counter() - start) * 1000)
    audio_seconds = round(float(len(audio)) / float(config.sample_rate), 2)
    return result.text, {
        "latency_ms": latency_ms,
        "audio_seconds": audio_seconds,
        "quality": result.quality,
        "word_count": len(result.words),
    }


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
