from __future__ import annotations

from collections.abc import Sequence

from .subtitle_cues import SubtitleCue


def cues_to_srt(cues: Sequence[SubtitleCue], *, bilingual: bool = False) -> str:
    lines: list[str] = []
    index = 1
    for cue in _valid_cues(cues):
        body = _format_body(cue, bilingual=bilingual)
        if not body:
            continue
        lines.extend(
            [
                str(index),
                f"{_format_timestamp(cue.start, separator=',')} --> {_format_timestamp(cue.end, separator=',')}",
                body,
                "",
            ]
        )
        index += 1
    return "\n".join(lines).strip() + ("\n" if lines else "")


def cues_to_vtt(cues: Sequence[SubtitleCue], *, bilingual: bool = False) -> str:
    lines = ["WEBVTT", ""]
    for cue in _valid_cues(cues):
        body = _format_body(cue, bilingual=bilingual)
        if not body:
            continue
        lines.extend(
            [
                f"{_format_timestamp(cue.start, separator='.')} --> {_format_timestamp(cue.end, separator='.')}",
                body,
                "",
            ]
        )
    return "\n".join(lines).strip() + ("\n" if len(lines) > 2 else "")


def _valid_cues(cues: Sequence[SubtitleCue]) -> list[SubtitleCue]:
    valid: list[SubtitleCue] = []
    for cue in cues:
        text = (cue.source_text or "").strip()
        if not text:
            continue
        start = max(0.0, float(cue.start))
        end = max(start + 0.001, float(cue.end))
        valid.append(
            SubtitleCue(
                start=start,
                end=end,
                source_text=text,
                translated_text=cue.translated_text,
                words=cue.words,
                final=cue.final,
            )
        )
    return valid


def _format_body(cue: SubtitleCue, *, bilingual: bool) -> str:
    source = cue.source_text.strip()
    if not source:
        return ""
    if bilingual and cue.translated_text:
        return f"{source}\n{cue.translated_text.strip()}"
    return source


def _format_timestamp(seconds: float, *, separator: str) -> str:
    total_ms = int(round(max(0.0, seconds) * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{separator}{millis:03d}"
