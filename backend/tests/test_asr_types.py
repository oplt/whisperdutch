from __future__ import annotations

from types import SimpleNamespace

import pytest
from app import asr_types


def test_empty_transcription() -> None:
    result = asr_types.empty_transcription(language="nl", quality={"level": "empty"})
    assert result.text == ""
    assert result.segments == ()
    assert result.words == ()
    assert result.language == "nl"
    assert not result.has_timestamps


def test_one_segment_with_words() -> None:
    segments = [
        SimpleNamespace(
            text=" Hallo wereld ",
            start=0.2,
            end=1.4,
            avg_logprob=-0.3,
            no_speech_prob=0.1,
            compression_ratio=1.1,
            words=[
                SimpleNamespace(word=" Hallo", start=0.2, end=0.6, probability=0.95),
                SimpleNamespace(word=" wereld", start=0.7, end=1.4, probability=0.91),
            ],
        )
    ]
    result = asr_types.segments_from_whisper(segments, language="nl", duration=1.5)
    assert result.text == "Hallo wereld"
    assert len(result.segments) == 1
    assert result.segments[0].start == 0.2
    assert result.segments[0].end == 1.4
    assert len(result.words) == 2
    assert result.words[0].text == "Hallo"
    assert result.words[1].probability == pytest.approx(0.91)


def test_several_segments_text_reconstruction() -> None:
    segments = [
        SimpleNamespace(text="Een", start=0.0, end=0.5, words=[], avg_logprob=-0.2, no_speech_prob=0.0, compression_ratio=1.0),
        SimpleNamespace(text="twee", start=0.6, end=1.1, words=[], avg_logprob=-0.2, no_speech_prob=0.0, compression_ratio=1.0),
    ]
    result = asr_types.segments_from_whisper(segments, language="nl", duration=1.2)
    assert result.text == "Een twee"
    assert [segment.start for segment in result.segments] == [0.0, 0.6]


def test_missing_word_probability() -> None:
    segments = [
        SimpleNamespace(
            text="test",
            start=0.0,
            end=0.4,
            words=[SimpleNamespace(word="test", start=0.0, end=0.4, probability=None)],
            avg_logprob=-0.2,
            no_speech_prob=0.0,
            compression_ratio=1.0,
        )
    ]
    result = asr_types.segments_from_whisper(segments, language="en", duration=0.5)
    assert result.words[0].probability is None


def test_timestamp_ordering_normalized() -> None:
    segments = [
        SimpleNamespace(
            text="x",
            start=1.0,
            end=0.5,
            words=[SimpleNamespace(word="x", start=1.0, end=0.2, probability=0.5)],
            avg_logprob=-0.2,
            no_speech_prob=0.0,
            compression_ratio=1.0,
        )
    ]
    result = asr_types.segments_from_whisper(segments, language="en", duration=1.0)
    assert result.segments[0].end >= result.segments[0].start
    assert result.words[0].end >= result.words[0].start
