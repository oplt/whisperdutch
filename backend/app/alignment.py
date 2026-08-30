from __future__ import annotations

import os

import numpy as np

from .asr_types import ASRWord
from .logger import get_logger
from .subtitle_cues import SubtitleCue
from .subtitle_format import cues_to_srt, cues_to_vtt

logger = get_logger("alignment")


def export_alignment_engine() -> str:
    return os.getenv("EXPORT_ALIGNMENT_ENGINE", "live").strip().lower() or "live"


def whisperx_available() -> bool:
    try:
        import whisperx  # noqa: F401

        return True
    except ImportError:
        return False


def align_with_whisperx(
    audio: np.ndarray,
    *,
    sample_rate: int = 16000,
    language: str = "nl",
    model_name: str | None = None,
) -> list[SubtitleCue]:
    if not whisperx_available():
        raise RuntimeError(
            "WhisperX is not installed. Install optional dependencies with:\n"
            "  pip install -r backend/requirements-whisperx.txt"
        )
    import whisperx

    device = "cuda" if _cuda_available() else "cpu"
    compute_type = "float16" if device == "cuda" else "int8"
    asr_model = model_name or os.getenv("WHISPERX_ASR_MODEL", os.getenv("ASR_MODEL", "large-v3-turbo"))
    batch_size = int(os.getenv("WHISPERX_BATCH_SIZE", "8"))

    model = whisperx.load_model(asr_model, device=device, compute_type=compute_type)
    result = model.transcribe(audio, batch_size=batch_size, language=language)
    align_model, metadata = whisperx.load_align_model(language_code=language, device=device)
    aligned = whisperx.align(result["segments"], align_model, metadata, audio, device, return_char_alignments=False)

    cues: list[SubtitleCue] = []
    for segment in aligned.get("segments", []):
        words: list[ASRWord] = []
        for word in segment.get("words") or []:
            text = str(word.get("word") or "").strip()
            if not text:
                continue
            start = float(word.get("start") or segment.get("start") or 0.0)
            end = float(word.get("end") or start)
            score = word.get("score")
            probability = float(score) if score is not None else None
            words.append(ASRWord(text, start, end, probability))
        text = str(segment.get("text") or "").strip()
        if not text and words:
            text = " ".join(word.text for word in words)
        if not text:
            continue
        start = float(segment.get("start") or (words[0].start if words else 0.0))
        end = float(segment.get("end") or (words[-1].end if words else start + 0.8))
        cues.append(SubtitleCue(start=start, end=end, source_text=text, words=tuple(words)))
    return cues


def export_aligned_subtitles(
    audio: np.ndarray,
    *,
    sample_rate: int = 16000,
    language: str = "nl",
    format: str = "srt",
    bilingual: bool = False,
    translations: list[str] | None = None,
) -> str:
    engine = export_alignment_engine()
    if engine == "live":
        raise RuntimeError("High-quality export requires EXPORT_ALIGNMENT_ENGINE=whisperx")
    if engine != "whisperx":
        raise ValueError(f"Unsupported EXPORT_ALIGNMENT_ENGINE '{engine}'")

    cues = align_with_whisperx(audio, sample_rate=sample_rate, language=language)
    if translations:
        merged: list[SubtitleCue] = []
        for index, cue in enumerate(cues):
            translated = translations[index] if index < len(translations) else None
            merged.append(
                SubtitleCue(
                    start=cue.start,
                    end=cue.end,
                    source_text=cue.source_text,
                    translated_text=translated,
                    words=cue.words,
                )
            )
        cues = merged

    if format == "vtt":
        return cues_to_vtt(cues, bilingual=bilingual)
    return cues_to_srt(cues, bilingual=bilingual)


def _cuda_available() -> bool:
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False
