from __future__ import annotations

import os
import re
from dataclasses import dataclass

from .asr_types import ASRWord, TranscriptionResult
from .sentences import SentenceAssembler, normalize_fragment

_STRONG_PUNCT = re.compile(r"[.!?…][\"”’)\]]*$")
_WEAK_PUNCT = re.compile(r"[,;:][\"”’)\]]*$")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class SubtitleCue:
    start: float
    end: float
    source_text: str
    translated_text: str | None = None
    words: tuple[ASRWord, ...] = ()
    final: bool = True


@dataclass(frozen=True)
class SubtitleLayoutConfig:
    max_duration_seconds: float = 6.0
    min_duration_seconds: float = 0.8
    max_characters: int = 84
    max_line_characters: int = 42
    max_lines: int = 2
    break_silence_ms: int = 500

    @classmethod
    def from_env(cls) -> SubtitleLayoutConfig:
        return cls(
            max_duration_seconds=_env_float("SUBTITLE_MAX_DURATION_SECONDS", 6.0),
            min_duration_seconds=_env_float("SUBTITLE_MIN_DURATION_SECONDS", 0.8),
            max_characters=_env_int("SUBTITLE_MAX_CHARACTERS", 84),
            max_line_characters=_env_int("SUBTITLE_MAX_LINE_CHARACTERS", 42),
            max_lines=_env_int("SUBTITLE_MAX_LINES", 2),
            break_silence_ms=_env_int("SUBTITLE_BREAK_SILENCE_MS", 500),
        )


class SubtitleSegmenter:
    def __init__(self, config: SubtitleLayoutConfig | None = None) -> None:
        self.config = config or SubtitleLayoutConfig.from_env()

    def build_cues(
        self,
        transcription: TranscriptionResult,
        *,
        time_offset_seconds: float = 0.0,
        sentence_assembler: SentenceAssembler | None = None,
        force: bool = False,
    ) -> list[SubtitleCue]:
        if transcription.words:
            return self._build_from_words(transcription.words, time_offset_seconds=time_offset_seconds)
        if sentence_assembler is not None and transcription.text:
            sentences, _buffer = sentence_assembler.add_fragment(transcription.text, force=force)
            duration = transcription.duration or 0.0
            if not sentences:
                return []
            if len(sentences) == 1:
                return [
                    SubtitleCue(
                        start=time_offset_seconds,
                        end=time_offset_seconds + max(duration, self.config.min_duration_seconds),
                        source_text=sentences[0],
                    )
                ]
            slice_duration = max(duration / len(sentences), self.config.min_duration_seconds)
            cues: list[SubtitleCue] = []
            cursor = time_offset_seconds
            for sentence in sentences:
                cues.append(
                    SubtitleCue(
                        start=cursor,
                        end=cursor + slice_duration,
                        source_text=sentence,
                    )
                )
                cursor += slice_duration
            return self._normalize_cues(cues)
        return []

    def _build_from_words(self, words: tuple[ASRWord, ...], *, time_offset_seconds: float) -> list[SubtitleCue]:
        if not words:
            return []

        silence_gap = self.config.break_silence_ms / 1000.0
        groups: list[list[ASRWord]] = []
        current: list[ASRWord] = [words[0]]

        for previous, word in zip(words, words[1:], strict=False):
            gap = max(0.0, word.start - previous.end)
            candidate_text = " ".join(item.text for item in current + [word])
            candidate_duration = word.end - current[0].start
            should_break = self._should_break(current, word, candidate_text, candidate_duration, gap, silence_gap)
            if should_break:
                groups.append(current)
                current = [word]
            else:
                current.append(word)
        if current:
            groups.append(current)

        cues = [self._cue_from_words(group, time_offset_seconds) for group in groups]
        return self._normalize_cues(cues)

    def _should_break(
        self,
        current: list[ASRWord],
        next_word: ASRWord,
        candidate_text: str,
        candidate_duration: float,
        gap: float,
        silence_gap: float,
    ) -> bool:
        current_text = " ".join(word.text for word in current)
        if len(candidate_text) > self.config.max_characters:
            return True
        if candidate_duration > self.config.max_duration_seconds:
            return True
        if gap >= silence_gap:
            return True
        if _STRONG_PUNCT.search(current_text.strip()):
            return True
        if _WEAK_PUNCT.search(current_text.strip()) and len(current_text) >= self.config.max_line_characters:
            return True
        return False

    def _cue_from_words(self, words: list[ASRWord], time_offset_seconds: float) -> SubtitleCue:
        start = time_offset_seconds + words[0].start
        end = time_offset_seconds + words[-1].end
        if end <= start:
            end = start + self.config.min_duration_seconds
        text = normalize_fragment(" ".join(word.text for word in words))
        return SubtitleCue(start=start, end=end, source_text=text, words=tuple(words))

    def _normalize_cues(self, cues: list[SubtitleCue]) -> list[SubtitleCue]:
        normalized: list[SubtitleCue] = []
        previous_start = -1.0
        for cue in cues:
            text = normalize_fragment(cue.source_text)
            if not text:
                continue
            start = max(0.0, float(cue.start))
            end = max(start + self.config.min_duration_seconds, float(cue.end))
            if normalized:
                previous = normalized[-1]
                if start < previous.end:
                    start = previous.end
                    end = max(end, start + self.config.min_duration_seconds)
            if start < previous_start:
                start = previous_start
            previous_start = start
            normalized.append(
                SubtitleCue(
                    start=start,
                    end=end,
                    source_text=text,
                    translated_text=cue.translated_text,
                    words=cue.words,
                    final=cue.final,
                )
            )
        return normalized
