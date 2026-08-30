from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from app.api import create_app
from app.constants import SERVICE_IDENTIFIER

MODULE_PATH = Path(__file__).resolve().parents[2] / "native-host" / "start_backend_host.py"
SPEC = importlib.util.spec_from_file_location("start_backend_host", MODULE_PATH)
assert SPEC and SPEC.loader
native_host = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(native_host)


def test_native_host_recognizes_backend_health_payload() -> None:
    live_endpoint = next(
        route.endpoint for route in create_app().routes if getattr(route, "path", None) == "/health/live"
    )
    payload = live_endpoint()
    assert payload["service"] == SERVICE_IDENTIFIER

    def fake_urlopen(url: str, timeout: float = 0.6):
        assert url.endswith("/health/live")
        return type(
            "Response",
            (),
            {
                "status": 200,
                "read": lambda self: json.dumps(payload).encode("utf-8"),
                "__enter__": lambda self: self,
                "__exit__": lambda self, *_args: None,
            },
        )()

    original_urlopen = native_host.urllib.request.urlopen
    native_host.urllib.request.urlopen = fake_urlopen
    try:
        assert native_host.is_backend_healthy(8000) is True
    finally:
        native_host.urllib.request.urlopen = original_urlopen


def test_native_host_rejects_health_payload_with_wrong_service_identifier() -> None:
    def fake_urlopen(url: str, timeout: float = 0.6):
        assert url.endswith("/health/live")
        payload = {"ok": True, "live": True, "service": "some-other-backend"}
        return type(
            "Response",
            (),
            {
                "status": 200,
                "read": lambda self: json.dumps(payload).encode("utf-8"),
                "__enter__": lambda self: self,
                "__exit__": lambda self, *_args: None,
            },
        )()

    original_urlopen = native_host.urllib.request.urlopen
    native_host.urllib.request.urlopen = fake_urlopen
    try:
        assert native_host.is_backend_healthy(8000) is False
    finally:
        native_host.urllib.request.urlopen = original_urlopen


def test_already_running_backend_skips_spawn_and_poll(monkeypatch) -> None:
    calls = {"popen": 0, "sleep": 0}

    monkeypatch.setattr(native_host, "is_backend_healthy", lambda port, timeout=0.6: port == 8000)
    monkeypatch.setattr(
        native_host.subprocess,
        "Popen",
        lambda *_args, **_kwargs: calls.__setitem__("popen", calls["popen"] + 1) or (_ for _ in ()).throw(AssertionError("spawned")),
    )
    monkeypatch.setattr(native_host.time, "sleep", lambda _seconds: calls.__setitem__("sleep", calls["sleep"] + 1))

    response = native_host.start_backend({"port": 8000})

    assert response["status"] == "already_running"
    assert response["port"] == 8000
    assert calls == {"popen": 0, "sleep": 0}


def test_stale_pid_is_ignored_when_health_check_passes(monkeypatch) -> None:
    monkeypatch.setattr(native_host, "is_backend_healthy", lambda _port, timeout=0.6: True)
    monkeypatch.setattr(native_host, "existing_pid", lambda: 999999)
    monkeypatch.setattr(
        native_host.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("duplicate backend spawned")),
    )

    response = native_host.start_backend({"port": 8000})

    assert response["status"] == "already_running"


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
