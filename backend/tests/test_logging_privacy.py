from __future__ import annotations

from app.logger import preview_text


def test_transcript_preview_is_hidden_by_default(monkeypatch) -> None:
    monkeypatch.delenv("LOG_TRANSCRIPT_TEXT", raising=False)

    preview = preview_text("gevoelige ondertitel")

    assert "gevoelige ondertitel" not in preview


def test_transcript_preview_requires_explicit_opt_in(monkeypatch) -> None:
    monkeypatch.setenv("LOG_TRANSCRIPT_TEXT", "1")

    assert preview_text("zichtbare ondertitel") == "zichtbare ondertitel"
