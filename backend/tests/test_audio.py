from __future__ import annotations

import numpy as np

from app.audio import SpeechSegmenter, pcm16le_to_float32


def test_pcm16le_to_float32_decodes_signed_samples() -> None:
    data = np.array([-32768, 0, 32767], dtype="<i2").tobytes()
    decoded = pcm16le_to_float32(data)
    assert decoded.dtype == np.float32
    assert decoded.tolist() == [-1.0, 0.0, 32767 / 32768]


def test_segmenter_flushes_after_silence() -> None:
    segmenter = SpeechSegmenter(sample_rate=16000, min_speech_seconds=0.01, end_silence_seconds=0.01, pre_roll_seconds=0.0)
    speech = np.ones(400, dtype=np.float32) * 0.05
    silence = np.zeros(400, dtype=np.float32)
    assert segmenter.add(speech) is None
    finalized = segmenter.add(silence)
    assert finalized is not None
    assert finalized.size == 800
