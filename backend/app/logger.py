from __future__ import annotations

import json
import logging
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

_CONFIGURED = False
_CURRENT_LOG_FILE: Path | None = None


def _backend_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def _resolve_log_dir() -> Path:
    raw = os.getenv("BACKEND_LOG_DIR", "logs").strip() or "logs"
    path = Path(raw)
    if not path.is_absolute():
        path = _backend_dir() / path
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


class DailyFileHandler(logging.FileHandler):
    """File handler that writes to backend/logs/<prefix>-YYYY-MM-DD.log."""

    def __init__(self, log_dir: Path, prefix: str = "backend", encoding: str = "utf-8") -> None:
        self.log_dir = log_dir
        self.prefix = prefix
        self.current_date = date.today().isoformat()
        super().__init__(self._path_for_today(), mode="a", encoding=encoding, delay=False)

    def _path_for_today(self) -> str:
        return str(self.log_dir / f"{self.prefix}-{self.current_date}.log")

    def emit(self, record: logging.LogRecord) -> None:
        today = date.today().isoformat()
        if today != self.current_date:
            self.acquire()
            try:
                self.current_date = today
                if self.stream:
                    self.stream.flush()
                    self.stream.close()
                self.baseFilename = self._path_for_today()
                self.stream = self._open()
            finally:
                self.release()
        super().emit(record)


def setup_logging() -> Path:
    """Configure console + daily backend file logging."""
    global _CONFIGURED, _CURRENT_LOG_FILE
    if _CONFIGURED and _CURRENT_LOG_FILE is not None:
        return _CURRENT_LOG_FILE

    log_dir = _resolve_log_dir()
    log_prefix = os.getenv("BACKEND_LOG_PREFIX", "backend").strip() or "backend"
    level_name = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)

    formatter = logging.Formatter(
        fmt="%(asctime)s.%(msecs)03d %(levelname)-8s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(level)

    # Remove only handlers created by this module, so uvicorn/test imports do not duplicate lines.
    for handler in list(root.handlers):
        if getattr(handler, "_dutch_subtitles_handler", False):
            root.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    console_handler._dutch_subtitles_handler = True  # type: ignore[attr-defined]

    file_handler = DailyFileHandler(log_dir=log_dir, prefix=log_prefix)
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    file_handler._dutch_subtitles_handler = True  # type: ignore[attr-defined]

    root.addHandler(console_handler)
    root.addHandler(file_handler)

    # Quiet down noisy dependencies.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("huggingface_hub").setLevel(logging.WARNING)
    logging.getLogger("multipart").setLevel(logging.WARNING)

    _CURRENT_LOG_FILE = Path(file_handler.baseFilename)
    _CONFIGURED = True
    logging.getLogger("dutch_subtitles.logging").info("logging_initialized file=%s level=%s", _CURRENT_LOG_FILE, level_name)
    return _CURRENT_LOG_FILE


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    if name.startswith("dutch_subtitles"):
        return logging.getLogger(name)
    return logging.getLogger(f"dutch_subtitles.{name}")


def current_log_file() -> Path:
    setup_logging()
    log_dir = _resolve_log_dir()
    log_prefix = os.getenv("BACKEND_LOG_PREFIX", "backend").strip() or "backend"
    return log_dir / f"{log_prefix}-{date.today().isoformat()}.log"


def tail_log(lines: int = 200) -> list[str]:
    log_file = current_log_file()
    if not log_file.exists():
        return []

    lines = max(1, min(int(lines or 200), 5000))
    block_size = 8192
    data = b""

    with log_file.open("rb") as fh:
        fh.seek(0, os.SEEK_END)
        pos = fh.tell()
        while pos > 0 and data.count(b"\n") <= lines:
            read_size = min(block_size, pos)
            pos -= read_size
            fh.seek(pos)
            data = fh.read(read_size) + data

    return data.decode("utf-8", errors="replace").splitlines()[-lines:]


def kv(logger: logging.Logger, level: int, event: str, **kwargs: Any) -> None:
    """Compact structured logging: event | key=value | key={json}."""
    parts = [event]
    for key, value in kwargs.items():
        if isinstance(value, (dict, list, tuple)):
            safe_value = json.dumps(value, ensure_ascii=False, default=str)
        else:
            safe_value = str(value)
        safe_value = safe_value.replace("\n", " ").replace("\r", " ")
        if len(safe_value) > 600:
            safe_value = safe_value[:600] + "…"
        parts.append(f"{key}={safe_value}")
    logger.log(level, " | ".join(parts))


def should_log_text() -> bool:
    return os.getenv("LOG_TRANSCRIPT_TEXT", "0").strip().lower() in {"1", "true", "yes", "on"}


def preview_text(text: str, limit: int = 180) -> str:
    if not should_log_text():
        return "[hidden; set LOG_TRANSCRIPT_TEXT=1 to log text previews]"
    text = " ".join(str(text or "").split())
    return text[:limit] + ("…" if len(text) > limit else "")
