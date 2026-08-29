from __future__ import annotations

import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[2] / "native-host" / "start_backend_host.py"
SPEC = importlib.util.spec_from_file_location("start_backend_host", MODULE_PATH)
assert SPEC and SPEC.loader
native_host = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(native_host)


def test_repeated_start_does_not_spawn_while_backend_is_starting(monkeypatch) -> None:
    monkeypatch.setattr(native_host, "is_backend_healthy", lambda _port: False)
    monkeypatch.setattr(native_host, "existing_pid", lambda: 321)
    monkeypatch.setattr(native_host, "existing_port", lambda: 8123)
    monkeypatch.setattr(
        native_host.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("duplicate backend spawned")),
    )

    response = native_host.start_backend({"port": 8123})

    assert response["status"] == "already_starting"
    assert response["pid"] == 321
    assert response["port"] == 8123


def test_pid_record_is_backward_compatible(tmp_path, monkeypatch) -> None:
    pid_file = tmp_path / "backend.pid"
    monkeypatch.setattr(native_host, "PID_FILE", pid_file)

    pid_file.write_text("123")
    assert native_host.read_pid_record() == (123, native_host.DEFAULT_PORT)

    native_host.write_pid_record(456, 8123)
    assert native_host.read_pid_record() == (456, 8123)


def test_requested_port_rejects_invalid_ranges() -> None:
    assert native_host.requested_port({"port": 8123}) == 8123
    assert native_host.requested_port({"port": 99999}) == native_host.DEFAULT_PORT
