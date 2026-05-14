from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

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
    return (audio_i16.astype(np.float32) / 32768.0).clip(-1.0, 1.0)


def rms(audio: np.ndarray) -> float:
    if audio.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(audio), dtype=np.float64)))


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

    _speech: list[np.ndarray] = field(default_factory=list)
    _pre_roll: list[np.ndarray] = field(default_factory=list)
    _speech_samples: int = 0
    _silence_samples: int = 0
    _in_speech: bool = False

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
        self._speech.clear()
        self._pre_roll.clear()
        self._speech_samples = 0
        self._silence_samples = 0
        self._in_speech = False

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    @property
    def speech_seconds(self) -> float:
        return float(self._speech_samples) / float(self.sample_rate)

    def current_snapshot(self, max_seconds: float | None = None) -> Optional[np.ndarray]:
        if not self._speech:
            return None
        audio = np.concatenate(self._speech) if len(self._speech) > 1 else self._speech[0].copy()
        if max_seconds:
            max_samples = int(max_seconds * self.sample_rate)
            if audio.size > max_samples:
                audio = audio[-max_samples:]
        return audio.astype(np.float32, copy=False)

    def add(self, audio: np.ndarray) -> Optional[np.ndarray]:
        """Add a chunk and return a finalized speech segment, if available."""
        if audio.size == 0:
            return None

        chunk_rms = rms(audio)
        is_speech = chunk_rms >= self.silence_rms_threshold

        if not self._in_speech:
            self._remember_pre_roll(audio)
            if is_speech:
                self._in_speech = True
                self._speech = list(self._pre_roll)
                self._speech.append(audio)
                self._speech_samples = sum(len(x) for x in self._speech)
                self._silence_samples = 0
            return None

        self._speech.append(audio)
        self._speech_samples += len(audio)

        if is_speech:
            self._silence_samples = 0
        else:
            self._silence_samples += len(audio)

        reached_max = self._speech_samples >= int(self.max_segment_seconds * self.sample_rate)
        enough_speech = self._speech_samples >= int(self.min_speech_seconds * self.sample_rate)
        enough_silence = self._silence_samples >= int(self.end_silence_seconds * self.sample_rate)

        if reached_max or (enough_speech and enough_silence):
            finalized = np.concatenate(self._speech) if self._speech else np.empty(0, dtype=np.float32)
            self.reset()
            return finalized

        return None

    def flush(self) -> Optional[np.ndarray]:
        if not self._speech:
            return None
        finalized = np.concatenate(self._speech)
        self.reset()
        return finalized

    def _remember_pre_roll(self, audio: np.ndarray) -> None:
        self._pre_roll.append(audio)
        max_samples = int(self.pre_roll_seconds * self.sample_rate)
        total = sum(len(x) for x in self._pre_roll)
        while self._pre_roll and total > max_samples:
            removed = self._pre_roll.pop(0)
            total -= len(removed)
