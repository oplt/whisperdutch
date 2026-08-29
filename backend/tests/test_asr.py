from __future__ import annotations

from types import SimpleNamespace

import numpy as np
from app import asr


class FakeWhisperModel:
    def __init__(self, *_args, **_kwargs) -> None:
        self.calls: list[dict] = []

    def transcribe(self, _audio, **kwargs):
        self.calls.append(kwargs)
        return [SimpleNamespace(text=" hallo ", no_speech_prob=0.1, avg_logprob=-0.2, compression_ratio=1.0)], None


def test_asr_precomputes_mode_specific_decode_configuration(monkeypatch) -> None:
    monkeypatch.setattr(asr, "WhisperModel", FakeWhisperModel)
    monkeypatch.setenv("ASR_DEVICE", "cpu")
    monkeypatch.setenv("FAST_ASR_BEAM_SIZE", "1")
    monkeypatch.setenv("BALANCED_ASR_BEAM_SIZE", "2")
    monkeypatch.setenv("QUALITY_ASR_BEAM_SIZE", "4")
    monkeypatch.setenv("ASR_LANGUAGE", "NL")
    engine = asr.TranscriptionEngine()

    assert {mode: config.beam_size for mode, config in engine.decode_configs.items()} == {
        "fast": 1,
        "balanced": 2,
        "quality": 4,
    }
    assert engine.decode_configs["fast"].language == "nl"


def test_asr_hot_path_does_not_reread_environment(monkeypatch) -> None:
    monkeypatch.setattr(asr, "WhisperModel", FakeWhisperModel)
    monkeypatch.setenv("ASR_DEVICE", "cpu")
    monkeypatch.setenv("ASR_LANGUAGE", "nl")
    monkeypatch.setenv("FAST_ASR_BEAM_SIZE", "1")
    engine = asr.TranscriptionEngine()
    monkeypatch.setenv("ASR_LANGUAGE", "de")
    monkeypatch.setenv("FAST_ASR_BEAM_SIZE", "9")

    result = engine.transcribe_dutch_result(np.ones(1600, dtype=np.float32), mode="fast")

    assert result.text == "hallo"
    assert engine.model.calls[0]["language"] == "nl"
    assert engine.model.calls[0]["beam_size"] == 1


def test_asr_rejects_invalid_invariant_configuration(monkeypatch) -> None:
    monkeypatch.setattr(asr, "WhisperModel", FakeWhisperModel)
    monkeypatch.setenv("ASR_DEVICE", "cpu")
    monkeypatch.setenv("ASR_NO_SPEECH_THRESHOLD", "1.5")

    try:
        asr.TranscriptionEngine()
    except ValueError as exc:
        assert "ASR_NO_SPEECH_THRESHOLD" in str(exc)
    else:
        raise AssertionError("invalid threshold was accepted")
