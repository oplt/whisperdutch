from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ASRWord:
    text: str
    start: float
    end: float
    probability: float | None = None


@dataclass(frozen=True)
class ASRSegment:
    text: str
    start: float
    end: float
    words: tuple[ASRWord, ...]
    avg_logprob: float | None = None
    no_speech_prob: float | None = None
    compression_ratio: float | None = None


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    segments: tuple[ASRSegment, ...]
    words: tuple[ASRWord, ...]
    language: str
    duration: float | None
    quality: dict[str, Any]

    @property
    def has_timestamps(self) -> bool:
        return bool(self.segments or self.words)


def _normalize_word_text(text: str) -> str:
    return text.strip()


def _word_from_whisper(word: Any) -> ASRWord | None:
    raw = _normalize_word_text(str(getattr(word, "word", "") or ""))
    if not raw:
        return None
    start = float(getattr(word, "start", 0.0) or 0.0)
    end = float(getattr(word, "end", start) or start)
    if end < start:
        end = start
    probability = getattr(word, "probability", None)
    prob_value = float(probability) if probability is not None else None
    return ASRWord(raw, start, end, prob_value)


def segments_from_whisper(segment_iter: Any, *, language: str, duration: float | None) -> TranscriptionResult:
    """Convert faster-whisper segment output into project-owned domain objects."""
    segments: list[ASRSegment] = []
    all_words: list[ASRWord] = []

    for segment in segment_iter:
        words: list[ASRWord] = []
        for whisper_word in getattr(segment, "words", None) or ():
            converted = _word_from_whisper(whisper_word)
            if converted is not None:
                words.append(converted)
                all_words.append(converted)

        text = str(getattr(segment, "text", "") or "").strip()
        if not text and words:
            text = " ".join(word.text for word in words).strip()

        start = float(getattr(segment, "start", 0.0) or 0.0)
        end = float(getattr(segment, "end", start) or start)
        if end < start:
            end = start

        segments.append(
            ASRSegment(
                text=text,
                start=start,
                end=end,
                words=tuple(words),
                avg_logprob=_optional_float(getattr(segment, "avg_logprob", None)),
                no_speech_prob=_optional_float(getattr(segment, "no_speech_prob", None)),
                compression_ratio=_optional_float(getattr(segment, "compression_ratio", None)),
            )
        )

    flat_text = reconstruct_text(segments)
    quality = quality_from_segments(segments)
    return TranscriptionResult(
        text=flat_text,
        segments=tuple(segments),
        words=tuple(all_words),
        language=language,
        duration=duration,
        quality=quality,
    )


def empty_transcription(*, language: str, quality: dict[str, Any]) -> TranscriptionResult:
    return TranscriptionResult("", (), (), language, None, quality)


def reconstruct_text(segments: tuple[ASRSegment, ...] | list[ASRSegment]) -> str:
    parts = [_clean_text(segment.text) for segment in segments if segment.text.strip()]
    return _clean_text(" ".join(parts))


def _clean_text(text: str) -> str:
    return " ".join(text.replace("\n", " ").split()).strip()


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def quality_from_segments(segments: tuple[ASRSegment, ...] | list[ASRSegment]) -> dict[str, Any]:
    if not segments:
        return {"level": "empty", "reason": "no_segments"}

    no_speech_values = [float(segment.no_speech_prob or 0.0) for segment in segments]
    avg_logprob_values = [float(segment.avg_logprob or 0.0) for segment in segments if segment.avg_logprob is not None]
    compression_values = [float(segment.compression_ratio or 0.0) for segment in segments if segment.compression_ratio is not None]

    max_no_speech = max(no_speech_values) if no_speech_values else 0.0
    avg_logprob = sum(avg_logprob_values) / len(avg_logprob_values) if avg_logprob_values else 0.0
    max_compression = max(compression_values) if compression_values else 0.0

    level = "good"
    reasons: list[str] = []
    if max_no_speech >= 0.75:
        level = "low"
        reasons.append("high_no_speech_probability")
    if avg_logprob_values and avg_logprob <= -1.2:
        level = "low"
        reasons.append("low_average_logprob")
    if compression_values and max_compression >= 2.4:
        level = "low"
        reasons.append("high_compression_ratio")
    if not reasons and (max_no_speech >= 0.55 or (avg_logprob_values and avg_logprob <= -0.85)):
        level = "watch"
        reasons.append("borderline_asr_confidence")

    return {
        "level": level,
        "reasons": reasons,
        "no_speech_prob": round(max_no_speech, 3),
        "avg_logprob": round(avg_logprob, 3) if avg_logprob_values else None,
        "compression_ratio": round(max_compression, 3) if compression_values else None,
        "segment_count": len(segments),
        "word_count": sum(len(segment.words) for segment in segments),
    }
