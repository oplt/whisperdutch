from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .logger import get_logger
from .metrics import SessionMetrics

logger = get_logger("history")


class SessionHistoryStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        default_path = Path(__file__).resolve().parents[1] / "logs" / "session-history.sqlite3"
        self.db_path = Path(db_path or os.getenv("SESSION_HISTORY_DB", str(default_path)))
        self.enabled = _env_bool("SESSION_HISTORY_ENABLED", True)
        self._lock = threading.Lock()
        if self.enabled:
            self.init()

    def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
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
        snapshot = metrics.snapshot()
        try:
            with self._lock, self._connect() as db:
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
                        metrics.client_id,
                        metrics.started_at,
                        metrics.updated_at,
                        metrics.closed_at,
                        metrics.mode,
                        json.dumps(snapshot["summary"], separators=(",", ":")),
                        json.dumps(snapshot, separators=(",", ":")),
                    ),
                )
        except Exception:
            logger.exception("session_history_save_failed client_id=%s", metrics.client_id)

    def save_subtitle(self, client_id: str, payload: dict[str, Any]) -> None:
        if not self.enabled:
            return
        subtitle_id = str(payload.get("id") or f"subtitle-{time.time_ns()}")
        try:
            with self._lock, self._connect() as db:
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
                        json.dumps(payload.get("quality") or {}, separators=(",", ":")),
                    ),
                )
        except Exception:
            logger.exception("subtitle_history_save_failed client_id=%s subtitle_id=%s", client_id, subtitle_id)

    def recent_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        limit = max(1, min(int(limit), 200))
        with self._lock, self._connect() as db:
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
        with self._lock, self._connect() as db:
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
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM subtitles WHERE client_id = ?", (client_id,))
            cursor = db.execute("DELETE FROM sessions WHERE client_id = ?", (client_id,))
        return cursor.rowcount > 0

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db


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
