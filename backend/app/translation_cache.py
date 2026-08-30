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
from typing import Any

from .logger import get_logger

logger = get_logger("translation_cache")


@dataclass(frozen=True)
class _PendingWrite:
    cache_key: str
    source_text: str
    translation: str
    metadata_json: str
    created_at: float
    write_epoch: int


class DurableTranslationCache:
    """Best-effort SQLite second tier for translated subtitle text."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        max_items: int,
        ttl_seconds: float = 0.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.db_path = Path(db_path)
        self.max_items = max_items
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._lock = threading.RLock()
        self._db: sqlite3.Connection | None = None
        self._pending_access: dict[str, tuple[float, int]] = {}
        self._access_batch_size = max(1, int(os.getenv("TRANSLATION_CACHE_ACCESS_BATCH", "64")))
        self._write_queue: deque[_PendingWrite] = deque()
        self._write_queue_max = max(1, int(os.getenv("TRANSLATION_CACHE_WRITE_QUEUE_MAX", "256")))
        self._write_batch_size = max(1, int(os.getenv("TRANSLATION_CACHE_WRITE_BATCH", "16")))
        self._writes_since_prune = 0
        self._prune_every_writes = min(64, max(1, max_items // 10))
        self._epoch = 0
        self.enabled = max_items > 0
        self.error: str | None = None
        self._reads = 0
        self._hits = 0
        self._misses = 0
        self._writes = 0
        self._evictions = 0
        self._prunes = 0
        self._dropped_writes = 0

        if self.enabled:
            try:
                self._init()
            except Exception as exc:
                self.enabled = False
                self.error = f"{type(exc).__name__}: {exc}"
                logger.exception("durable_translation_cache_init_failed path=%s", self.db_path)

    @property
    def epoch(self) -> int:
        return self._epoch

    def _init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = self._connect()
        self._db.executescript(
            """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS translation_cache_entries (
                    cache_key TEXT PRIMARY KEY,
                    source_text TEXT NOT NULL,
                    translation TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    last_accessed_at REAL NOT NULL,
                    hit_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_translation_cache_last_accessed
                    ON translation_cache_entries(last_accessed_at);
            """
        )
        self._db.commit()

    def get(self, cache_key: str) -> str | None:
        hits = self.get_many([cache_key])
        return hits.get(cache_key)

    def get_many(self, cache_keys: list[str]) -> dict[str, str]:
        if not self.enabled or not cache_keys:
            return {}

        unique_keys = list(dict.fromkeys(cache_keys))
        now = self._clock()
        with self._lock:
            db = self._connection()
            self._reads += len(unique_keys)
            placeholders = ",".join("?" * len(unique_keys))
            rows = db.execute(
                f"""
                SELECT cache_key, translation, last_accessed_at
                FROM translation_cache_entries
                WHERE cache_key IN ({placeholders})
                """,
                unique_keys,
            ).fetchall()

            hits: dict[str, str] = {}
            expired_keys: list[str] = []
            for row in rows:
                key = str(row["cache_key"])
                if self.ttl_seconds > 0 and now - float(row["last_accessed_at"]) >= self.ttl_seconds:
                    expired_keys.append(key)
                    continue
                hits[key] = str(row["translation"])
                previous = self._pending_access.get(key)
                self._pending_access[key] = (now, (previous[1] if previous else 0) + 1)

            if expired_keys:
                db.executemany(
                    "DELETE FROM translation_cache_entries WHERE cache_key = ?",
                    [(key,) for key in expired_keys],
                )
                db.commit()
                self._prunes += len(expired_keys)

            self._hits += len(hits)
            self._misses += len(unique_keys) - len(hits)
            if len(self._pending_access) >= self._access_batch_size:
                self._flush_access_locked(db)
            return hits

    def set(
        self,
        cache_key: str,
        source_text: str,
        translation: str,
        metadata: dict[str, Any],
        *,
        write_epoch: int | None = None,
    ) -> None:
        if not self.enabled:
            return

        now = self._clock()
        with self._lock:
            epoch = self._epoch if write_epoch is None else write_epoch
            if epoch != self._epoch:
                self._dropped_writes += 1
                return
            self._enqueue_write_locked(
                _PendingWrite(
                    cache_key=cache_key,
                    source_text=source_text,
                    translation=translation,
                    metadata_json=json.dumps(metadata, separators=(",", ":"), sort_keys=True),
                    created_at=now,
                    write_epoch=epoch,
                )
            )

    def flush_writes(self) -> None:
        if not self.enabled:
            return
        with self._lock:
            db = self._connection()
            self._flush_writes_locked(db)

    def flush_access(self) -> None:
        if not self.enabled:
            return
        with self._lock:
            db = self._connection()
            self._flush_access_locked(db)

    def clear(self) -> int:
        if not self.enabled:
            return 0
        with self._lock:
            self._epoch += 1
            self._write_queue.clear()
            db = self._connection()
            self._pending_access.clear()
            cursor = db.execute("DELETE FROM translation_cache_entries")
            db.commit()
            return max(0, cursor.rowcount)

    def stats(self) -> dict[str, Any]:
        base = {
            "backend": "sqlite",
            "enabled": self.enabled,
            "path": str(self.db_path),
            "max_items": self.max_items,
            "ttl_seconds": self.ttl_seconds,
            "epoch": self._epoch,
            "reads": self._reads,
            "hits": self._hits,
            "misses": self._misses,
            "writes": self._writes,
            "evictions": self._evictions,
            "prunes": self._prunes,
            "dropped_writes": self._dropped_writes,
            "pending_writes": len(self._write_queue),
            "size": 0,
        }
        if self.error:
            base["error"] = self.error
        if not self.enabled:
            return base

        try:
            with self._lock:
                row = self._connection().execute("SELECT COUNT(*) AS size FROM translation_cache_entries").fetchone()
                base["size"] = int(row["size"] if row is not None else 0)
        except Exception as exc:
            base["error"] = f"{type(exc).__name__}: {exc}"
        return base

    def info(self) -> dict[str, Any]:
        return self.stats()

    def _prune_locked(self, db: sqlite3.Connection, now: float) -> None:
        self._flush_access_locked(db)
        if self.ttl_seconds > 0:
            cursor = db.execute(
                "DELETE FROM translation_cache_entries WHERE last_accessed_at < ?",
                (now - self.ttl_seconds,),
            )
            expired = max(0, cursor.rowcount)
            self._prunes += expired

        before = db.total_changes
        db.execute(
            """
            DELETE FROM translation_cache_entries
            WHERE cache_key IN (
                SELECT cache_key
                FROM translation_cache_entries
                ORDER BY last_accessed_at DESC, cache_key DESC
                LIMIT -1 OFFSET ?
            )
            """,
            (self.max_items,),
        )
        db.commit()
        removed = max(0, db.total_changes - before)
        self._evictions += removed
        self._prunes += removed

    def close(self) -> None:
        with self._lock:
            if self._db is None:
                return
            self._flush_writes_locked(self._db)
            self._flush_access_locked(self._db)
            self._db.close()
            self._db = None

    def _enqueue_write_locked(self, pending: _PendingWrite) -> None:
        if pending.write_epoch != self._epoch:
            self._dropped_writes += 1
            return
        if len(self._write_queue) >= self._write_queue_max:
            self._flush_writes_locked(self._connection())
        self._write_queue.append(pending)
        if len(self._write_queue) >= self._write_batch_size:
            self._flush_writes_locked(self._connection())

    def _flush_writes_locked(self, db: sqlite3.Connection) -> None:
        if not self._write_queue:
            return
        current_epoch = self._epoch
        batch: list[_PendingWrite] = []
        while self._write_queue and len(batch) < self._write_batch_size:
            pending = self._write_queue.popleft()
            if pending.write_epoch != current_epoch:
                self._dropped_writes += 1
                continue
            batch.append(pending)
        if not batch:
            return

        db.executemany(
            """
            INSERT INTO translation_cache_entries (
                cache_key, source_text, translation, metadata_json,
                created_at, last_accessed_at, hit_count
            ) VALUES (?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(cache_key) DO UPDATE SET
                source_text = excluded.source_text,
                translation = excluded.translation,
                metadata_json = excluded.metadata_json,
                last_accessed_at = excluded.last_accessed_at
            """,
            [
                (
                    item.cache_key,
                    item.source_text,
                    item.translation,
                    item.metadata_json,
                    item.created_at,
                    item.created_at,
                )
                for item in batch
            ],
        )
        db.commit()
        self._writes += len(batch)
        self._writes_since_prune += len(batch)
        if self._writes_since_prune >= self._prune_every_writes:
            self._prune_locked(db, self._clock())
            self._writes_since_prune = 0

        if self._write_queue:
            self._flush_writes_locked(db)

    def _flush_access_locked(self, db: sqlite3.Connection) -> None:
        if not self._pending_access:
            return
        db.executemany(
            """
            UPDATE translation_cache_entries
            SET last_accessed_at = ?, hit_count = hit_count + ?
            WHERE cache_key = ?
            """,
            [(last_accessed, hit_count, cache_key) for cache_key, (last_accessed, hit_count) in self._pending_access.items()],
        )
        db.commit()
        self._pending_access.clear()

    def _connection(self) -> sqlite3.Connection:
        if self._db is None:
            raise RuntimeError("durable translation cache is closed")
        return self._db

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=5.0, check_same_thread=False)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        return db
