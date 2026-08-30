from __future__ import annotations

from app.asr_types import ASRWord
from app.subtitle_cues import SubtitleCue, SubtitleLayoutConfig, SubtitleSegmenter
from app.subtitle_format import cues_to_srt, cues_to_vtt


def _words(texts: list[str], *, gap: float = 0.1) -> tuple[ASRWord, ...]:
    words: list[ASRWord] = []
    cursor = 0.0
    for text in texts:
        start = cursor
        end = start + 0.35
        words.append(ASRWord(text, start, end, 0.9))
        cursor = end + gap
    return tuple(words)


def test_punctuation_split() -> None:
    segmenter = SubtitleSegmenter(SubtitleLayoutConfig(max_characters=200, break_silence_ms=10_000))
    words = _words(["Ik", "denk", "dat", "het", "goed", "is."])
    cues = segmenter._build_from_words(words, time_offset_seconds=0.0)
    assert len(cues) == 1
    assert cues[0].source_text.endswith("is.")


def test_max_duration_split() -> None:
    segmenter = SubtitleSegmenter(SubtitleLayoutConfig(max_duration_seconds=1.0, break_silence_ms=10_000))
    words = _words(["a", "b", "c", "d", "e", "f"], gap=0.05)
    cues = segmenter._build_from_words(words, time_offset_seconds=0.0)
    assert len(cues) >= 2


def test_silence_split() -> None:
    words = (
        ASRWord("Hallo", 0.0, 0.4, 0.9),
        ASRWord("daar.", 0.5, 0.9, 0.9),
        ASRWord("Nog", 2.0, 2.3, 0.9),
        ASRWord("een.", 2.4, 2.8, 0.9),
    )
    segmenter = SubtitleSegmenter(SubtitleLayoutConfig(break_silence_ms=500))
    cues = segmenter._build_from_words(words, time_offset_seconds=0.0)
    assert len(cues) == 2


def test_no_overlap_and_no_empty() -> None:
    segmenter = SubtitleSegmenter()
    cues = segmenter._normalize_cues(
        [
            SubtitleCue(start=0.0, end=2.0, source_text="Eerste zin."),
            SubtitleCue(start=1.5, end=3.0, source_text="Tweede zin."),
            SubtitleCue(start=3.0, end=3.0, source_text="   "),
        ]
    )
    assert len(cues) == 2
    assert cues[1].start >= cues[0].end


def test_srt_and_vtt_formatting() -> None:
    cues = [SubtitleCue(start=1.23, end=4.567, source_text="Hallo")]
    srt = cues_to_srt(cues)
    vtt = cues_to_vtt(cues)
    assert "00:00:01,230 --> 00:00:04,567" in srt
    assert "00:00:01.230 --> 00:00:04.567" in vtt
    assert "WEBVTT" in vtt


def test_unicode_and_bilingual_export() -> None:
    cues = [SubtitleCue(start=0.0, end=1.0, source_text="café", translated_text="coffee")]
    srt = cues_to_srt(cues, bilingual=True)
    assert "café\ncoffee" in srt
