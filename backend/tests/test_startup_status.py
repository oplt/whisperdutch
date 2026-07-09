from __future__ import annotations

from pathlib import Path

from app import startup_status


def test_startup_status_roundtrip(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "startup.json"
    monkeypatch.setattr(startup_status, "STATUS_PATH", path)
    startup_status.write_startup_status("ready", True, extra={"elapsed_ms": 12})
    payload = startup_status.read_startup_status()
    assert payload is not None
    assert payload["phase"] == "ready"
    assert payload["elapsed_ms"] == 12
