from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

STATUS_PATH = Path("logs/startup-status.json")


def write_startup_status(phase: str, ok: bool, error: dict[str, Any] | None = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "phase": phase,
        "ok": ok,
        "updated_at": time.time(),
        "error": error,
    }
    if extra:
        payload.update(extra)
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return payload


def read_startup_status() -> dict[str, Any] | None:
    if not STATUS_PATH.exists():
        return None
    try:
        return json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"phase": "invalid_status_file", "ok": False, "updated_at": time.time(), "error": None}
