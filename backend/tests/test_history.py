from __future__ import annotations

from app.history import SessionHistoryStore
from app.metrics import SessionMetrics


def test_session_history_persists_session_and_subtitle(tmp_path) -> None:
    store = SessionHistoryStore(tmp_path / "history.sqlite3")
    metrics = SessionMetrics(client_id="ws-test")
    metrics.mode = "balanced"
    metrics.asr_latency_ms.append(120)
    store.save_session(metrics)
    store.save_subtitle(
        "ws-test",
        {
            "id": "final-1",
            "source_lang": "nl",
            "target_lang": "en",
            "mode": "balanced",
            "dutch": "Hallo wereld.",
            "translation": "Hello world.",
            "asr_latency_ms": 120,
            "translation_latency_ms": 40,
            "latency_ms": 160,
            "audio_seconds": 2.0,
            "quality": {"level": "good"},
        },
    )

    recent = store.recent_sessions()
    assert recent[0]["client_id"] == "ws-test"
    assert recent[0]["subtitle_count"] == 1

    session = store.get_session("ws-test")
    assert session is not None
    assert session["mode"] == "balanced"
    assert session["subtitles"][0]["translation"] == "Hello world."


def test_session_history_delete(tmp_path) -> None:
    store = SessionHistoryStore(tmp_path / "history.sqlite3")
    store.save_session(SessionMetrics(client_id="ws-test"))

    assert store.delete_session("ws-test") is True
    assert store.get_session("ws-test") is None
