from __future__ import annotations

import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor

from app.history import SessionHistoryStore, SessionHistoryWriter, _HistoryJob
from app.metrics import SessionMetrics


def _subtitle_payload(index: int) -> dict:
    return {
        "id": f"final-{index}",
        "source_lang": "nl",
        "target_lang": "en",
        "mode": "balanced",
        "dutch": f"Regel {index}.",
        "translation": f"Line {index}.",
        "asr_latency_ms": 120,
        "translation_latency_ms": 40,
        "latency_ms": 160,
        "audio_seconds": 2.0,
        "quality": {"level": "good"},
    }


def test_session_history_persists_session_and_subtitle(tmp_path) -> None:
    store = SessionHistoryStore(tmp_path / "history.sqlite3")
    metrics = SessionMetrics(client_id="ws-test")
    metrics.mode = "balanced"
    metrics.asr_latency_ms.append(120)
    store.save_session(metrics)
    store.save_subtitle("ws-test", _subtitle_payload(1))
    assert store.flush(timeout=2.0)

    recent = store.recent_sessions()
    assert recent[0]["client_id"] == "ws-test"
    assert recent[0]["subtitle_count"] == 1

    session = store.get_session("ws-test")
    assert session is not None
    assert session["mode"] == "balanced"
    assert session["subtitles"][0]["translation"] == "Line 1."


def test_session_history_delete(tmp_path) -> None:
    store = SessionHistoryStore(tmp_path / "history.sqlite3")
    store.save_session(SessionMetrics(client_id="ws-test"))
    assert store.flush(timeout=2.0)

    assert store.delete_session("ws-test") is True
    assert store.get_session("ws-test") is None


def test_history_writer_reuses_one_sqlite_connection(tmp_path, monkeypatch) -> None:
    import app.history as history_module

    store = SessionHistoryStore(tmp_path / "history.sqlite3")
    store.stop()

    real_connect = sqlite3.connect
    calls = 0

    def counting_connect(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(history_module.sqlite3, "connect", counting_connect)
    writer = SessionHistoryWriter(tmp_path / "history.sqlite3", max_queue=32, batch_size=4)
    writer.start()
    metrics = SessionMetrics(client_id="conn-test")
    writer.enqueue(
        history_module._HistoryJob(
            kind="session",
            client_id=metrics.client_id,
            payload=history_module._session_payload(metrics),
        )
    )
    writer.enqueue(
        history_module._HistoryJob(kind="subtitle", client_id=metrics.client_id, payload=_subtitle_payload(1))
    )
    assert writer.flush(timeout=2.0)
    writer.stop()

    assert calls == 1


def test_history_writer_batches_subtitles_in_one_transaction(tmp_path) -> None:
    db_path = tmp_path / "history.sqlite3"
    store = SessionHistoryStore(db_path)
    store.save_session(SessionMetrics(client_id="batch-test"))
    for index in range(6):
        store.enqueue_subtitle("batch-test", _subtitle_payload(index))
    assert store.flush(timeout=2.0)

    session = store.get_session("batch-test")
    assert session is not None
    assert len(session["subtitles"]) == 6


def test_history_writer_drops_when_queue_is_full(tmp_path) -> None:
    import app.history as history_module

    writer = SessionHistoryWriter(tmp_path / "history.sqlite3", max_queue=4, batch_size=64)
    metrics = SessionMetrics(client_id="drop-test")
    session_job = history_module._HistoryJob(
        kind="session",
        client_id=metrics.client_id,
        payload=history_module._session_payload(metrics),
    )
    subtitle_job = history_module._HistoryJob(
        kind="subtitle",
        client_id=metrics.client_id,
        payload=_subtitle_payload(1),
    )

    for _ in range(4):
        assert writer.enqueue(session_job)
    assert writer.enqueue(subtitle_job) is False

    stats = writer.stats()
    assert stats["dropped"] == 1
    assert stats["queue_depth"] == 4


def test_history_failure_does_not_block_enqueue_path(tmp_path, monkeypatch) -> None:
    store = SessionHistoryStore(tmp_path / "history.sqlite3")
    writer = store._writer
    assert writer is not None

    def failing_write_batch(_batch: list) -> None:
        with writer._cond:
            writer._processing = True
            writer._write_failures += 1
            writer._processing = False
            writer._cond.notify_all()

    monkeypatch.setattr(writer, "_write_batch", failing_write_batch)

    assert store.enqueue_subtitle("fail-test", _subtitle_payload(1)) is True
    assert store.flush(timeout=2.0)
    stats = store.writer_stats()
    assert stats["write_failures"] >= 1
    assert stats["dropped"] == 0


def test_concurrent_sessions_keep_history_backlog_bounded(tmp_path) -> None:
    store = SessionHistoryStore(tmp_path / "history.sqlite3")
    assert store._writer is not None
    store._writer.batch_size = 16

    def worker(client_id: str, count: int) -> None:
        store.enqueue_session(SessionMetrics(client_id=client_id))
        for index in range(count):
            store.enqueue_subtitle(client_id, _subtitle_payload(index))

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(worker, f"client-{index}", 120) for index in range(8)]
        for future in futures:
            future.result(timeout=10)

    assert store.flush(timeout=5.0)
    stats = store.writer_stats()
    assert stats["max_backlog"] <= stats["max_queue"]
    assert stats["dropped"] == 0
    assert stats["written"] >= 8 + 8 * 120


def test_history_backlog_during_long_session_simulation(tmp_path) -> None:
    store = SessionHistoryStore(tmp_path / "history.sqlite3")
    assert store._writer is not None
    store._writer.batch_size = 32
    client_id = "long-session"
    store.enqueue_session(SessionMetrics(client_id=client_id))

    # ~30 minutes at one subtitle every two seconds, time-compressed.
    subtitle_count = 900
    for index in range(subtitle_count):
        store.enqueue_subtitle(client_id, _subtitle_payload(index))
        time.sleep(0.002)

    assert store.flush(timeout=10.0)
    stats = store.writer_stats()
    assert stats["dropped"] == 0
    assert stats["max_backlog"] < 128

    session = store.get_session(client_id)
    assert session is not None
    assert len(session["subtitles"]) == subtitle_count


def test_history_writer_graceful_shutdown_drains_pending_jobs(tmp_path) -> None:
    db_path = tmp_path / "history.sqlite3"
    store = SessionHistoryStore(db_path)
    store.stop()

    writer = SessionHistoryWriter(db_path, max_queue=64, batch_size=4)
    writer.start()
    client_id = "shutdown-drain"
    writer.enqueue(
        _HistoryJob(
            kind="session",
            client_id=client_id,
            payload={
                "client_id": client_id,
                "started_at": 1.0,
                "updated_at": 1.0,
                "closed_at": None,
                "mode": "fast",
                "summary_json": "{}",
                "metrics_json": "{}",
            },
        )
    )
    for index in range(12):
        writer.enqueue(
            _HistoryJob(kind="subtitle", client_id=client_id, payload=_subtitle_payload(index))
        )

    writer.stop(timeout=5.0)

    stats = writer.stats()
    assert stats["written"] == 13
    assert stats["queue_depth"] == 0

    with sqlite3.connect(db_path) as db:
        subtitle_count = db.execute("SELECT COUNT(*) FROM subtitles WHERE client_id = ?", (client_id,)).fetchone()[0]
    assert subtitle_count == 12
