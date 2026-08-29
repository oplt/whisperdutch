from __future__ import annotations

import os
from collections import deque
from dataclasses import dataclass, field

import numpy as np


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def pcm16le_to_float32(data: bytes) -> np.ndarray:
    """Decode little-endian signed 16-bit PCM into float32 audio in [-1, 1]."""
    if not data:
        return np.empty(0, dtype=np.float32)
    audio_i16 = np.frombuffer(data, dtype="<i2")
    audio = audio_i16.astype(np.float32)
    np.multiply(audio, 1.0 / 32768.0, out=audio)
    return audio


def rms(audio: np.ndarray) -> float:
    if audio.size == 0:
        return 0.0
    return float(np.sqrt(float(np.dot(audio, audio)) / audio.size))


@dataclass
class SpeechSegmenter:
    """
    Low-latency speech segmenter.

    It returns finalized audio on silence/max-duration and can also expose a
    current in-progress snapshot for partial Dutch subtitles.
    """

    sample_rate: int = 16000
    silence_rms_threshold: float = field(default_factory=lambda: _env_float("SILENCE_RMS_THRESHOLD", 0.010))
    min_speech_seconds: float = field(default_factory=lambda: _env_float("MIN_SPEECH_SECONDS", 0.25))
    end_silence_seconds: float = field(default_factory=lambda: _env_float("END_SILENCE_SECONDS", 0.50))
    max_segment_seconds: float = field(default_factory=lambda: _env_float("MAX_SEGMENT_SECONDS", 4.5))
    pre_roll_seconds: float = field(default_factory=lambda: _env_float("PRE_ROLL_SECONDS", 0.12))

    _pre_roll: deque[np.ndarray] = field(default_factory=deque)
    _speech_buffer: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.float32))
    _speech_samples: int = 0
    _pre_roll_samples: int = 0
    _silence_samples: int = 0
    _in_speech: bool = False
    last_finalize_reason: str | None = None

    def set_mode(self, mode: str) -> None:
        mode = (mode or "balanced").lower()
        if mode == "fast":
            self.end_silence_seconds = float(os.getenv("FAST_END_SILENCE_SECONDS", "0.35"))
            self.max_segment_seconds = float(os.getenv("FAST_MAX_SEGMENT_SECONDS", "3.0"))
        elif mode == "quality":
            self.end_silence_seconds = float(os.getenv("QUALITY_END_SILENCE_SECONDS", "0.70"))
            self.max_segment_seconds = float(os.getenv("QUALITY_MAX_SEGMENT_SECONDS", "5.8"))
        else:
            self.end_silence_seconds = float(os.getenv("BALANCED_END_SILENCE_SECONDS", "0.50"))
            self.max_segment_seconds = float(os.getenv("BALANCED_MAX_SEGMENT_SECONDS", "4.5"))

    def reset(self) -> None:
        self._pre_roll.clear()
        self._speech_samples = 0
        self._pre_roll_samples = 0
        self._silence_samples = 0
        self._in_speech = False
        self.last_finalize_reason = None

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    @property
    def speech_seconds(self) -> float:
        return float(self._speech_samples) / float(self.sample_rate)

    @property
    def silence_seconds(self) -> float:
        return float(self._silence_samples) / float(self.sample_rate)

    def likely_close_to_final(self, margin_seconds: float = 0.35) -> bool:
        if not self._in_speech:
            return False
        margin_samples = max(1, int(margin_seconds * self.sample_rate))
        max_remaining = int(self.max_segment_seconds * self.sample_rate) - self._speech_samples
        silence_remaining = int(self.end_silence_seconds * self.sample_rate) - self._silence_samples
        return max_remaining <= margin_samples or (self._silence_samples > 0 and silence_remaining <= margin_samples)

    def current_snapshot(self, max_seconds: float | None = None) -> np.ndarray | None:
        if self._speech_samples <= 0:
            return None
        max_samples = int(max_seconds * self.sample_rate) if max_seconds else self._speech_samples
        start = max(0, self._speech_samples - max_samples)
        return self._speech_buffer[start : self._speech_samples].copy()

    def add(self, audio: np.ndarray) -> np.ndarray | None:
        """Add a chunk and return a finalized speech segment, if available."""
        if audio.size == 0:
            return None

        chunk_rms = rms(audio)
        is_speech = chunk_rms >= self.silence_rms_threshold

        if not self._in_speech:
            if is_speech:
                self._in_speech = True
                required = self._pre_roll_samples + len(audio)
                self._ensure_speech_capacity(required)
                for chunk in self._pre_roll:
                    self._append_speech(chunk)
                self._append_speech(audio)
                self._silence_samples = 0
            else:
                self._remember_pre_roll(audio)
            return None

        self._append_speech(audio)

        if is_speech:
            self._silence_samples = 0
        else:
            self._silence_samples += len(audio)

        reached_max = self._speech_samples >= int(self.max_segment_seconds * self.sample_rate)
        enough_speech = self._speech_samples >= int(self.min_speech_seconds * self.sample_rate)
        enough_silence = self._silence_samples >= int(self.end_silence_seconds * self.sample_rate)

        if reached_max or (enough_speech and enough_silence):
            finalized = self._speech_buffer[: self._speech_samples].copy()
            finalize_reason = "max" if reached_max else "silence"
            self.reset()
            self.last_finalize_reason = finalize_reason
            return finalized

        return None

    def flush(self) -> np.ndarray | None:
        if self._speech_samples <= 0:
            return None
        finalized = self._speech_buffer[: self._speech_samples].copy()
        self.reset()
        self.last_finalize_reason = "flush"
        return finalized

    def _remember_pre_roll(self, audio: np.ndarray) -> None:
        self._pre_roll.append(audio)
        self._pre_roll_samples += len(audio)
        max_samples = int(self.pre_roll_seconds * self.sample_rate)
        while self._pre_roll and self._pre_roll_samples > max_samples:
            removed = self._pre_roll.popleft()
            self._pre_roll_samples -= len(removed)

    def _append_speech(self, audio: np.ndarray) -> None:
        if audio.size == 0:
            return
        end = self._speech_samples + len(audio)
        self._ensure_speech_capacity(end)
        self._speech_buffer[self._speech_samples : end] = audio
        self._speech_samples = end

    def _ensure_speech_capacity(self, required: int) -> None:
        if required <= len(self._speech_buffer):
            return
        configured = int(max(self.max_segment_seconds, 1.0) * self.sample_rate)
        capacity = max(required, configured, max(1, len(self._speech_buffer) * 2))
        replacement = np.empty(capacity, dtype=np.float32)
        if self._speech_samples:
            replacement[: self._speech_samples] = self._speech_buffer[: self._speech_samples]
        self._speech_buffer = replacement
