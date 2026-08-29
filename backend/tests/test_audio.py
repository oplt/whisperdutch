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
    assert segmenter.last_finalize_reason == "silence"


def test_segmenter_records_max_duration_boundary() -> None:
    segmenter = SpeechSegmenter(sample_rate=16000, min_speech_seconds=0.01, max_segment_seconds=0.01, pre_roll_seconds=0.0)

    assert segmenter.add(np.ones(160, dtype=np.float32)) is None
    finalized = segmenter.add(np.ones(1, dtype=np.float32))

    assert finalized is not None
    assert segmenter.last_finalize_reason == "max"


def test_first_speech_chunk_is_not_duplicated_as_pre_roll() -> None:
    segmenter = SpeechSegmenter(
        sample_rate=16000,
        min_speech_seconds=0.01,
        end_silence_seconds=0.01,
        pre_roll_seconds=0.15,
    )
    speech = np.ones(400, dtype=np.float32) * 0.05
    silence = np.zeros(400, dtype=np.float32)

    assert segmenter.add(speech) is None
    finalized = segmenter.add(silence)

    assert finalized is not None
    assert finalized.size == 800


def test_partial_snapshot_copies_only_recent_requested_audio() -> None:
    segmenter = SpeechSegmenter(sample_rate=10, min_speech_seconds=0.1, pre_roll_seconds=0)
    segmenter.add(np.ones(6, dtype=np.float32))
    segmenter.add(np.full(6, 2, dtype=np.float32))

    snapshot = segmenter.current_snapshot(max_seconds=0.5)

    assert snapshot is not None
    assert snapshot.tolist() == [2, 2, 2, 2, 2]


def test_segmenter_reuses_contiguous_buffer_between_utterances() -> None:
    segmenter = SpeechSegmenter(sample_rate=10, min_speech_seconds=0.1, end_silence_seconds=0.1, pre_roll_seconds=0)
    segmenter.add(np.ones(5, dtype=np.float32))
    first_buffer = segmenter._speech_buffer
    finalized = segmenter.add(np.zeros(2, dtype=np.float32))

    assert finalized is not None
    assert not np.shares_memory(finalized, first_buffer)

    segmenter.add(np.ones(3, dtype=np.float32))
    assert segmenter._speech_buffer is first_buffer


def test_pre_roll_uses_bounded_deque_and_preserves_recent_audio() -> None:
    segmenter = SpeechSegmenter(sample_rate=10, min_speech_seconds=0.1, pre_roll_seconds=0.3)
    segmenter.add(np.full(2, 0.001, dtype=np.float32))
    segmenter.add(np.full(2, 0.002, dtype=np.float32))
    segmenter.add(np.full(2, 0.003, dtype=np.float32))

    assert segmenter._pre_roll_samples <= 3
    segmenter.add(np.full(2, 0.1, dtype=np.float32))
    snapshot = segmenter.current_snapshot()

    assert snapshot is not None
    assert np.allclose(snapshot, [0.003, 0.003, 0.1, 0.1])


def test_partial_snapshot_is_detached_from_reusable_buffer() -> None:
    segmenter = SpeechSegmenter(sample_rate=10, min_speech_seconds=0.1, pre_roll_seconds=0)
    segmenter.add(np.ones(4, dtype=np.float32))
    snapshot = segmenter.current_snapshot()
    assert snapshot is not None

    snapshot[:] = 9

    assert segmenter.current_snapshot().tolist() == [1, 1, 1, 1]
