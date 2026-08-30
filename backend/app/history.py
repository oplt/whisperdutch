from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .logger import get_logger
from .metrics import SessionMetrics

logger = get_logger("history")


@dataclass(frozen=True)
class _HistoryJob:
    kind: Literal["session", "subtitle", "delete"]
    client_id: str
    payload: dict[str, Any]


class SessionHistoryWriter:
    """Background SQLite writer with one connection, WAL, and batched transactions."""

    def __init__(
        self,
        db_path: Path,
        *,
        max_queue: int,
        batch_size: int,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.db_path = db_path
        self.max_queue = max(1, max_queue)
        self.batch_size = max(1, batch_size)
        self._clock = clock
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._queue: deque[_HistoryJob] = deque()
        self._db: sqlite3.Connection | None = None
        self._thread: threading.Thread | None = None
        self._stopped = False
        self._processing = False
        self._enqueued = 0
        self._written = 0
        self._dropped = 0
        self._write_failures = 0
        self._max_backlog = 0

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self) -> None:
        with self._cond:
            if self.running:
                return
            self._stopped = False
            self._thread = threading.Thread(target=self._run, name="session-history-writer", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        with self._cond:
            self._stopped = True
            self._cond.notify_all()
            thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
        self._close_db()

    def enqueue(self, job: _HistoryJob) -> bool:
        with self._cond:
            if len(self._queue) >= self.max_queue:
                self._dropped += 1
                logger.warning(
                    "session_history_queue_full client_id=%s kind=%s dropped=%s",
                    job.client_id,
                    job.kind,
                    self._dropped,
                )
                return False
            self._queue.append(job)
            self._enqueued += 1
            self._max_backlog = max(self._max_backlog, len(self._queue))
            self._cond.notify()
        return True

    def flush(self, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._cond:
                if not self._queue and not self._processing:
                    return True
            time.sleep(0.005)
        with self._cond:
            return not self._queue and not self._processing

    def stats(self) -> dict[str, Any]:
        with self._cond:
            return {
                "running": self.running,
                "queue_depth": len(self._queue),
                "max_queue": self.max_queue,
                "batch_size": self.batch_size,
                "enqueued": self._enqueued,
                "written": self._written,
                "dropped": self._dropped,
                "write_failures": self._write_failures,
                "max_backlog": self._max_backlog,
            }

    def _run(self) -> None:
        while True:
            batch = self._collect_batch()
            if batch:
                self._write_batch(batch)
                continue
            with self._cond:
                if self._stopped:
                    break

    def _collect_batch(self) -> list[_HistoryJob]:
        with self._cond:
            while not self._queue and not self._stopped:
                self._cond.wait(timeout=0.25)
            if not self._queue:
                return []
            batch: list[_HistoryJob] = []
            while self._queue and len(batch) < self.batch_size:
                batch.append(self._queue.popleft())
            return batch

    def _write_batch(self, batch: list[_HistoryJob]) -> None:
        with self._cond:
            self._processing = True
        db = self._connection()
        try:
            db.execute("BEGIN IMMEDIATE")
            for job in batch:
                if job.kind == "session":
                    self._write_session(db, job.payload)
                elif job.kind == "subtitle":
                    self._write_subtitle(db, job.client_id, job.payload)
                else:
                    self._delete_session(db, job.client_id)
            db.commit()
            with self._cond:
                self._written += len(batch)
        except Exception:
            with self._cond:
                self._write_failures += 1
            logger.exception("session_history_batch_write_failed count=%s", len(batch))
            try:
                db.rollback()
            except Exception:
                logger.exception("session_history_batch_rollback_failed")
        finally:
            with self._cond:
                self._processing = False
                self._cond.notify_all()

    def _write_session(self, db: sqlite3.Connection, payload: dict[str, Any]) -> None:
        db.execute(
            """
            INSERT INTO sessions (
                client_id, started_at, updated_at, closed_at, mode, summary_json, metrics_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(client_id) DO UPDATE SET
                updated_at=excluded.updated_at,
                closed_at=excluded.closed_at,
                mode=excluded.mode,
                summary_json=excluded.summary_json,
                metrics_json=excluded.metrics_json
            """,
            (
                payload["client_id"],
                payload["started_at"],
                payload["updated_at"],
                payload["closed_at"],
                payload["mode"],
                payload["summary_json"],
                payload["metrics_json"],
            ),
        )

    def _write_subtitle(self, db: sqlite3.Connection, client_id: str, payload: dict[str, Any]) -> None:
        subtitle_id = str(payload.get("id") or f"subtitle-{time.time_ns()}")
        db.execute(
            """
            INSERT OR REPLACE INTO subtitles (
                id, client_id, created_at, source_lang, target_lang, mode,
                dutch, translation, asr_latency_ms, translation_latency_ms,
                total_latency_ms, audio_seconds, quality_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                subtitle_id,
                client_id,
                float(payload.get("created_at") or self._clock()),
                str(payload.get("source_lang") or "nl"),
                str(payload.get("target_lang") or "en"),
                str(payload.get("mode") or "fast"),
                str(payload.get("dutch") or ""),
                str(payload.get("translation") or ""),
                int(payload.get("asr_latency_ms") or 0),
                int(payload.get("translation_latency_ms") or 0),
                int(payload.get("latency_ms") or 0),
                float(payload.get("audio_seconds") or 0.0),
                json.dumps(payload.get("quality") or {}, separators=(",", ":"), sort_keys=True),
            ),
        )

    def _delete_session(self, db: sqlite3.Connection, client_id: str) -> None:
        db.execute("DELETE FROM subtitles WHERE client_id = ?", (client_id,))
        db.execute("DELETE FROM sessions WHERE client_id = ?", (client_id,))

    def _connection(self) -> sqlite3.Connection:
        if self._db is None:
            self._db = sqlite3.connect(self.db_path, timeout=5.0, check_same_thread=False)
            self._db.row_factory = sqlite3.Row
            self._db.execute("PRAGMA foreign_keys=ON")
            self._db.execute("PRAGMA busy_timeout=5000")
            self._db.execute("PRAGMA journal_mode=WAL")
        return self._db

    def _close_db(self) -> None:
        if self._db is None:
            return
        try:
            self._db.close()
        except Exception:
            logger.exception("session_history_close_failed")
        self._db = None


class SessionHistoryStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        default_path = Path(__file__).resolve().parents[1] / "logs" / "session-history.sqlite3"
        configured_path = db_path if db_path is not None else os.getenv("SESSION_HISTORY_DB", str(default_path))
        self.db_path = Path(configured_path)
        self.enabled = _env_bool("SESSION_HISTORY_ENABLED", True)
        self._read_lock = threading.Lock()
        self._writer: SessionHistoryWriter | None = None
        if self.enabled:
            self.init()
            self._writer = SessionHistoryWriter(
                self.db_path,
                max_queue=max(1, int(os.getenv("SESSION_HISTORY_QUEUE_MAX", "1024"))),
                batch_size=max(1, int(os.getenv("SESSION_HISTORY_WRITE_BATCH", "32"))),
            )
            self._writer.start()

    def start(self) -> None:
        if self._writer is not None:
            self._writer.start()

    def stop(self, timeout: float | None = None) -> None:
        if self._writer is None:
            return
        flush_timeout = timeout
        if flush_timeout is None:
            flush_timeout = float(os.getenv("SESSION_HISTORY_FLUSH_TIMEOUT", "5"))
        self._writer.flush(timeout=flush_timeout)
        self._writer.stop(timeout=flush_timeout)

    def flush(self, timeout: float | None = None) -> bool:
        if self._writer is None:
            return True
        if timeout is None:
            timeout = float(os.getenv("SESSION_HISTORY_FLUSH_TIMEOUT", "5"))
        return self._writer.flush(timeout=timeout)

    def writer_stats(self) -> dict[str, Any]:
        if self._writer is None:
            return {"enabled": False, "running": False, "queue_depth": 0}
        stats = self._writer.stats()
        stats["enabled"] = self.enabled
        return stats

    def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._read_connect() as db:
            db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sessions (
                    client_id TEXT PRIMARY KEY,
                    started_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    closed_at REAL,
                    mode TEXT NOT NULL,
                    summary_json TEXT NOT NULL,
                    metrics_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS subtitles (
                    id TEXT PRIMARY KEY,
                    client_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    source_lang TEXT NOT NULL,
                    target_lang TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    dutch TEXT NOT NULL,
                    translation TEXT NOT NULL,
                    asr_latency_ms INTEGER NOT NULL,
                    translation_latency_ms INTEGER NOT NULL,
                    total_latency_ms INTEGER NOT NULL,
                    audio_seconds REAL NOT NULL,
                    quality_json TEXT NOT NULL,
                    FOREIGN KEY(client_id) REFERENCES sessions(client_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_subtitles_client_created
                    ON subtitles(client_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_sessions_updated
                    ON sessions(updated_at DESC);
                """
            )

    def save_session(self, metrics: SessionMetrics) -> None:
        if not self.enabled:
            return
        payload = _session_payload(metrics)
        if self._writer is not None and self._writer.running:
            self._writer.enqueue(_HistoryJob(kind="session", client_id=metrics.client_id, payload=payload))
            return
        self._save_session_direct(payload)

    def save_subtitle(self, client_id: str, payload: dict[str, Any]) -> None:
        if not self.enabled:
            return
        if self._writer is not None and self._writer.running:
            self._writer.enqueue(_HistoryJob(kind="subtitle", client_id=client_id, payload=dict(payload)))
            return
        self._save_subtitle_direct(client_id, payload)

    def enqueue_session(self, metrics: SessionMetrics) -> bool:
        if not self.enabled or self._writer is None:
            return False
        return self._writer.enqueue(
            _HistoryJob(kind="session", client_id=metrics.client_id, payload=_session_payload(metrics))
        )

    def enqueue_subtitle(self, client_id: str, payload: dict[str, Any]) -> bool:
        if not self.enabled or self._writer is None:
            return False
        return self._writer.enqueue(_HistoryJob(kind="subtitle", client_id=client_id, payload=dict(payload)))

    def recent_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        limit = max(1, min(int(limit), 200))
        with self._read_lock, self._read_connect() as db:
            rows = db.execute(
                """
                SELECT s.client_id, s.started_at, s.updated_at, s.closed_at, s.mode,
                       s.summary_json, COUNT(t.id) AS subtitle_count
                FROM sessions s
                LEFT JOIN subtitles t ON t.client_id = s.client_id
                GROUP BY s.client_id
                ORDER BY s.updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_session_row(row) for row in rows]

    def get_session(self, client_id: str) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        with self._read_lock, self._read_connect() as db:
            session = db.execute(
                """
                SELECT client_id, started_at, updated_at, closed_at, mode, summary_json, metrics_json
                FROM sessions
                WHERE client_id = ?
                """,
                (client_id,),
            ).fetchone()
            if session is None:
                return None
            subtitles = db.execute(
                """
                SELECT id, created_at, source_lang, target_lang, mode, dutch, translation,
                       asr_latency_ms, translation_latency_ms, total_latency_ms,
                       audio_seconds, quality_json
                FROM subtitles
                WHERE client_id = ?
                ORDER BY created_at ASC
                """,
                (client_id,),
            ).fetchall()
        return {
            "client_id": session["client_id"],
            "started_at": session["started_at"],
            "updated_at": session["updated_at"],
            "closed_at": session["closed_at"],
            "mode": session["mode"],
            "summary": _json(session["summary_json"], {}),
            "metrics": _json(session["metrics_json"], {}),
            "subtitles": [_subtitle_row(row) for row in subtitles],
        }

    def delete_session(self, client_id: str) -> bool:
        if not self.enabled:
            return False
        if self._writer is not None and self._writer.running:
            self._writer.enqueue(_HistoryJob(kind="delete", client_id=client_id, payload={}))
            self.flush()
            return self.get_session(client_id) is None
        with self._read_lock, self._read_connect() as db:
            db.execute("DELETE FROM subtitles WHERE client_id = ?", (client_id,))
            cursor = db.execute("DELETE FROM sessions WHERE client_id = ?", (client_id,))
            db.commit()
        return cursor.rowcount > 0

    def _save_session_direct(self, payload: dict[str, Any]) -> None:
        try:
            with self._read_lock, self._read_connect() as db:
                db.execute(
                    """
                    INSERT INTO sessions (
                        client_id, started_at, updated_at, closed_at, mode, summary_json, metrics_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(client_id) DO UPDATE SET
                        updated_at=excluded.updated_at,
                        closed_at=excluded.closed_at,
                        mode=excluded.mode,
                        summary_json=excluded.summary_json,
                        metrics_json=excluded.metrics_json
                    """,
                    (
                        payload["client_id"],
                        payload["started_at"],
                        payload["updated_at"],
                        payload["closed_at"],
                        payload["mode"],
                        payload["summary_json"],
                        payload["metrics_json"],
                    ),
                )
                db.commit()
        except Exception:
            logger.exception("session_history_save_failed client_id=%s", payload["client_id"])

    def _save_subtitle_direct(self, client_id: str, payload: dict[str, Any]) -> None:
        subtitle_id = str(payload.get("id") or f"subtitle-{time.time_ns()}")
        try:
            with self._read_lock, self._read_connect() as db:
                db.execute(
                    """
                    INSERT OR REPLACE INTO subtitles (
                        id, client_id, created_at, source_lang, target_lang, mode,
                        dutch, translation, asr_latency_ms, translation_latency_ms,
                        total_latency_ms, audio_seconds, quality_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        subtitle_id,
                        client_id,
                        time.time(),
                        str(payload.get("source_lang") or "nl"),
                        str(payload.get("target_lang") or "en"),
                        str(payload.get("mode") or "fast"),
                        str(payload.get("dutch") or ""),
                        str(payload.get("translation") or ""),
                        int(payload.get("asr_latency_ms") or 0),
                        int(payload.get("translation_latency_ms") or 0),
                        int(payload.get("latency_ms") or 0),
                        float(payload.get("audio_seconds") or 0.0),
                        json.dumps(payload.get("quality") or {}, separators=(",", ":"), sort_keys=True),
                    ),
                )
                db.commit()
        except Exception:
            logger.exception("subtitle_history_save_failed client_id=%s subtitle_id=%s", client_id, subtitle_id)

    def _read_connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=5.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=5000")
        return db


def _session_payload(metrics: SessionMetrics) -> dict[str, Any]:
    snapshot = metrics.snapshot()
    return {
        "client_id": metrics.client_id,
        "started_at": metrics.started_at,
        "updated_at": metrics.updated_at,
        "closed_at": metrics.closed_at,
        "mode": metrics.mode,
        "summary_json": json.dumps(snapshot["summary"], separators=(",", ":"), sort_keys=True),
        "metrics_json": json.dumps(snapshot, separators=(",", ":"), sort_keys=True),
    }


def _session_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "client_id": row["client_id"],
        "started_at": row["started_at"],
        "updated_at": row["updated_at"],
        "closed_at": row["closed_at"],
        "mode": row["mode"],
        "summary": _json(row["summary_json"], {}),
        "subtitle_count": int(row["subtitle_count"]),
    }


def _subtitle_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "source_lang": row["source_lang"],
        "target_lang": row["target_lang"],
        "mode": row["mode"],
        "dutch": row["dutch"],
        "translation": row["translation"],
        "asr_latency_ms": row["asr_latency_ms"],
        "translation_latency_ms": row["translation_latency_ms"],
        "latency_ms": row["total_latency_ms"],
        "audio_seconds": row["audio_seconds"],
        "quality": _json(row["quality_json"], {}),
    }


def _json(raw: str, default: Any) -> Any:
    try:
        return json.loads(raw)
    except Exception:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


session_history_store = SessionHistoryStore()
