from __future__ import annotations

import os
import re
from collections import deque
from dataclasses import dataclass, field

from .text_processor import get_text_processor


_TERMINAL_RE = re.compile(r'(?<=[.!?…])(?:["”’\)\]]+)?\s+')
_WORD_RE = re.compile(r"\b[\wÀ-ÿ'-]+\b", re.UNICODE)

_FILLERS = {
    "ehm", "euh", "uh", "um", "uhm", "hm", "hmm", "mmm", "ja ehm", "nou ehm",
}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def normalize_fragment(text: str) -> str:
    return get_text_processor().normalize(text)


def word_count(text: str) -> int:
    return len(_WORD_RE.findall(text))


def is_filler_only(text: str) -> bool:
    normalized = normalize_fragment(text).lower()
    normalized = normalized.replace("…", "...")
    normalized = re.sub(r"[.!,?:;\-]+", " ", normalized)
    normalized = " ".join(normalized.split())
    return normalized in _FILLERS


@dataclass
class SentenceAssembler:
    """
    Converts low-latency ASR fragments into translation-quality sentences.

    Rules:
    - keep live partial text available immediately;
    - emit final sentences only on punctuation, pause-forced flush, or a safety cap;
    - avoid finalizing when the Dutch text ends with a connector word.
    """

    enabled: bool = field(default_factory=lambda: _env_bool("SENTENCE_MODE", True))
    max_buffer_words: int = field(default_factory=lambda: _env_int("SENTENCE_MAX_BUFFER_WORDS", 32))
    max_buffer_chars: int = field(default_factory=lambda: _env_int("SENTENCE_MAX_BUFFER_CHARS", 240))
    drop_fillers: bool = field(default_factory=lambda: _env_bool("DROP_FILLERS", True))
    context_chars: int = field(default_factory=lambda: _env_int("ASR_CONTEXT_CHARS", 420))
    min_final_words: int = field(default_factory=lambda: _env_int("MIN_FINAL_WORDS", 3))

    _buffer: str = ""
    _recent: deque[str] = field(default_factory=lambda: deque(maxlen=8))

    def add_fragment(self, fragment: str, force: bool = False) -> tuple[list[str], str]:
        processor = get_text_processor()
        fragment = processor.correct(fragment)
        if not fragment:
            return [], self._buffer

        if self.drop_fillers and is_filler_only(fragment):
            return [], self._buffer

        if not self.enabled:
            self._remember(fragment)
            return [fragment], ""

        self._buffer = self._join(self._buffer, fragment)
        completed, self._buffer = self._split_completed(self._buffer, force=force)

        for sentence in completed:
            self._remember(sentence)

        return completed, self._buffer

    def flush(self) -> list[str]:
        if not self._buffer:
            return []
        completed, self._buffer = self._split_completed(self._buffer, force=True)
        for sentence in completed:
            self._remember(sentence)
        return completed

    def partial_text(self) -> str:
        return normalize_fragment(self._buffer)

    def context_prompt(self) -> str | None:
        """Return optional ASR context without forcing any topic/domain.

        ASR_INITIAL_PROMPT is intentionally empty by default. This app must
        transcribe/translate any Dutch video, not bias the model toward a
        specific news/geopolitics frame. If the user wants a per-video hint,
        they can set ASR_INITIAL_PROMPT manually before launching the backend.

        We still append a short window of recent finalized Dutch text to help
        Whisper keep continuity across audio chunks.
        """
        static_prompt = os.getenv("ASR_INITIAL_PROMPT", "").strip()
        dynamic = " ".join([*self._recent, self._buffer]).strip()
        prompt = " ".join(part for part in [static_prompt, dynamic[-self.context_chars:]] if part).strip()
        return prompt or None

    def _remember(self, sentence: str) -> None:
        sentence = normalize_fragment(sentence)
        if sentence:
            self._recent.append(sentence)

    def _join(self, left: str, right: str) -> str:
        left = normalize_fragment(left)
        right = normalize_fragment(right)
        if not left:
            return right
        if not right:
            return left
        if left.endswith(right):
            return left
        # Avoid obvious overlap duplication.
        left_words = left.split()
        right_words = right.split()
        max_overlap = min(8, len(left_words), len(right_words))
        for n in range(max_overlap, 0, -1):
            if [w.lower() for w in left_words[-n:]] == [w.lower() for w in right_words[:n]]:
                return normalize_fragment(" ".join(left_words + right_words[n:]))
        return f"{left} {right}"

    def _split_completed(self, text: str, force: bool) -> tuple[list[str], str]:
        processor = get_text_processor()
        text = processor.correct(text)
        if not text:
            return [], ""

        if force:
            if processor.ends_with_connector(text) and word_count(text) < self.max_buffer_words:
                return [], text
            sentence = self._finish_sentence(text)
            if self._should_drop(sentence):
                return [], ""
            return [sentence], ""

        boundaries: list[int] = []
        for match in _TERMINAL_RE.finditer(text + " "):
            boundaries.append(match.end() - 1)

        completed: list[str] = []
        start = 0
        for boundary in boundaries:
            candidate = normalize_fragment(text[start:boundary])
            start = boundary
            if self._should_drop(candidate):
                continue
            if processor.ends_with_connector(candidate):
                # Keep connector-ending text in the buffer and wait for the next fragment.
                start = 0
                completed.clear()
                break
            completed.append(candidate)

        remainder = normalize_fragment(text[start:]) if start else text

        if not completed and (
            word_count(remainder) >= self.max_buffer_words or len(remainder) >= self.max_buffer_chars
        ):
            if not processor.ends_with_connector(remainder):
                return [self._finish_sentence(remainder)], ""

        return completed, remainder

    def _should_drop(self, text: str) -> bool:
        text = normalize_fragment(text)
        if not text:
            return True
        if self.drop_fillers and is_filler_only(text):
            return True
        if word_count(text) < self.min_final_words and not text.endswith(("?", "!")):
            return True
        return False

    def _finish_sentence(self, text: str) -> str:
        text = normalize_fragment(text)
        if text and text[-1] not in ".!?…":
            text += "."
        return text
