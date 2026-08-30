#!/usr/bin/env python3
from __future__ import annotations

import fcntl
import json
import os
import signal
import socket
import struct
import subprocess
import sys
import time
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path

HOST_NAME = "com.polatozgur111.dutch_subtitle_backend"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
from app.constants import SERVICE_IDENTIFIER  # noqa: E402, I001
RUN_SCRIPT = BACKEND_DIR / "run_gpu.sh"
LOG_DIR = BACKEND_DIR / "logs"


def native_log_file() -> Path:
    return LOG_DIR / f"native-host-{date.today().isoformat()}.log"


PID_FILE = BACKEND_DIR / "backend.pid"
LOCK_FILE = LOG_DIR / "native-host.lock"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
SUPPORTED_ASR_DEVICES = {"cpu", "cuda"}


def backend_urls(port: int, host: str = DEFAULT_HOST) -> dict[str, str | int]:
    return {
        "host": host,
        "port": port,
        "base_url": f"http://{host}:{port}",
        "ws_url": f"ws://{host}:{port}/ws/subtitles",
        "health_url": f"http://{host}:{port}/health/live",
        "ready_url": f"http://{host}:{port}/health/ready",
        "device_url": f"http://{host}:{port}/debug/device",
    }


def read_message() -> dict:
    raw_length = sys.stdin.buffer.read(4)
    if len(raw_length) == 0:
        return {}
    if len(raw_length) != 4:
        raise RuntimeError("Invalid native message length header")
    message_length = struct.unpack("@I", raw_length)[0]
    if message_length > 1024 * 1024:
        raise RuntimeError("Native message exceeds the 1 MiB limit")
    message = sys.stdin.buffer.read(message_length)
    if len(message) != message_length:
        raise RuntimeError("Incomplete native message")
    payload = json.loads(message.decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("Native message must be a JSON object")
    return payload


def send_message(payload: dict) -> None:
    encoded = json.dumps(payload).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("@I", len(encoded)))
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


def is_port_open(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def is_backend_healthy(port: int = DEFAULT_PORT, timeout: float = 0.6) -> bool:
    try:
        with urllib.request.urlopen(str(backend_urls(port)["health_url"]), timeout=timeout) as response:
            if response.status != 200:
                return False
            data = json.loads(response.read().decode("utf-8"))
            return bool(data.get("ok")) and data.get("service") == SERVICE_IDENTIFIER
    except Exception:
        return False


def pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def pid_belongs_to_backend(pid: int) -> bool:
    proc = Path("/proc") / str(pid)
    try:
        cwd = (proc / "cwd").resolve()
        command = (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", errors="replace")
    except OSError:
        return False
    return cwd == BACKEND_DIR.resolve() and ("uvicorn" in command or str(RUN_SCRIPT) in command)


def read_pid_record() -> tuple[int, int] | None:
    try:
        raw = PID_FILE.read_text().strip()
        if raw.startswith("{"):
            record = json.loads(raw)
            return int(record["pid"]), int(record.get("port") or DEFAULT_PORT)
        return int(raw), DEFAULT_PORT
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def existing_pid() -> int | None:
    record = read_pid_record()
    if record is None:
        return None
    pid, _port = record
    if pid_is_running(pid) and pid_belongs_to_backend(pid):
        return pid
    cleanup_stale_pid()
    return None


def existing_port() -> int:
    record = read_pid_record()
    return record[1] if record else DEFAULT_PORT


def write_pid_record(pid: int, port: int) -> None:
    PID_FILE.write_text(json.dumps({"pid": pid, "port": port}, separators=(",", ":")))


@contextmanager
def lifecycle_lock() -> Iterator[None]:
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_FILE.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def cleanup_stale_pid() -> None:
    try:
        PID_FILE.unlink()
    except FileNotFoundError:
        pass


def find_available_port(preferred: int = DEFAULT_PORT, host: str = DEFAULT_HOST) -> int:
    for port in range(preferred, preferred + 50):
        if not is_port_open(host, port):
            return port
    raise RuntimeError(f"No free backend port found from {preferred} to {preferred + 49}")


def requested_port(message: dict | None = None) -> int:
    raw = None
    if message:
        raw = message.get("port")
    raw = raw or os.getenv("DUTCH_SUBTITLE_BACKEND_PORT") or os.getenv("BACKEND_PORT") or str(DEFAULT_PORT)
    try:
        port = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_PORT
    return port if 1024 <= port <= 65535 else DEFAULT_PORT


def requested_asr_device(message: dict | None = None) -> str:
    raw = message.get("asr_device") if message else None
    device = str(raw or os.getenv("ASR_DEVICE") or "cpu").strip().lower()
    return device if device in SUPPORTED_ASR_DEVICES else "cpu"


def start_backend(message: dict | None = None) -> dict:
    preferred_port = requested_port(message)
    for port in range(DEFAULT_PORT, DEFAULT_PORT + 50):
        if is_backend_healthy(port):
            return {"ok": True, "status": "already_running", "message": "Backend is already running.", **backend_urls(port)}

    pid = existing_pid()
    if pid:
        port = existing_port()
        return {
            "ok": True,
            "status": "already_starting",
            "pid": pid,
            "message": "Backend process is already starting.",
            **backend_urls(port),
        }

    port = preferred_port
    if is_port_open(DEFAULT_HOST, port):
        port = find_available_port(preferred_port + 1)

    if not RUN_SCRIPT.exists():
        return {"ok": False, "error": f"Cannot find {RUN_SCRIPT}"}

    log_path = native_log_file()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = open(log_path, "ab", buffering=0)
    log.write(f"\n\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Starting backend via native host\n".encode())

    env = os.environ.copy()
    asr_device = requested_asr_device(message)
    env["ASR_DEVICE_OVERRIDE"] = asr_device
    env["ASR_COMPUTE_TYPE_OVERRIDE"] = "float16" if asr_device == "cuda" else "int8"
    env["ASR_DEVICE"] = asr_device
    env.setdefault("ASR_MODEL", "small")
    env["ASR_COMPUTE_TYPE"] = env["ASR_COMPUTE_TYPE_OVERRIDE"]
    env.setdefault("TRANSLATION_DEVICE", "cpu")
    env.setdefault("BACKEND_LOG_DIR", "logs")
    env.setdefault("BACKEND_LOG_PREFIX", "backend")
    env.setdefault("LOG_LEVEL", "INFO")
    env.setdefault("LOG_TRANSCRIPT_TEXT", "0")
    env["BACKEND_PORT"] = str(port)
    env.setdefault("BACKEND_HOST", DEFAULT_HOST)

    try:
        process = subprocess.Popen(
            ["bash", str(RUN_SCRIPT)],
            cwd=str(BACKEND_DIR),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
            close_fds=True,
        )
    finally:
        log.close()
    write_pid_record(process.pid, port)
    for _attempt in range(30):
        time.sleep(0.5)
        if is_backend_healthy(port, timeout=0.4):
            return {
                "ok": True,
                "status": "live",
                "pid": process.pid,
                "message": "Backend process is live; models may still be loading.",
                "log_file": str(log_path),
                **backend_urls(port),
            }
        return_code = process.poll()
        if return_code is not None:
            try:
                PID_FILE.unlink()
            except FileNotFoundError:
                pass
            return {
                "ok": False,
                "error": f"Backend exited with code {return_code}. Check {log_path}",
                "log_file": str(log_path),
            }

    return {
        "ok": True,
        "status": "started",
        "pid": process.pid,
        "message": "Backend process started. Model loading can take a moment.",
        "log_file": str(log_path),
        **backend_urls(port),
    }


def stop_backend() -> dict:
    pid = existing_pid()
    if not pid:
        cleanup_stale_pid()
        return {"ok": True, "status": "not_running", "message": "Backend is not running."}

    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        cleanup_stale_pid()
        return {"ok": True, "status": "not_running", "message": "Backend was not running."}
    except PermissionError:
        os.kill(pid, signal.SIGTERM)

    deadline = time.time() + 8
    while time.time() < deadline:
        if not pid_is_running(pid):
            cleanup_stale_pid()
            return {"ok": True, "status": "stopped", "message": "Backend stopped."}
        time.sleep(0.25)

    try:
        os.killpg(pid, signal.SIGKILL)
    except Exception:
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass
    cleanup_stale_pid()
    return {"ok": True, "status": "killed", "message": "Backend killed after timeout."}


def restart_backend(message: dict | None = None) -> dict:
    stopped = stop_backend()
    started = start_backend(message)
    started["stopped"] = stopped
    return started


def main() -> None:
    try:
        message = read_message()
        command = message.get("command")
        with lifecycle_lock():
            if command == "start_backend":
                response = start_backend(message)
            elif command == "stop_backend":
                response = stop_backend()
            elif command == "restart_backend":
                response = restart_backend(message)
            else:
                response = {"ok": False, "error": f"Unsupported command: {command}"}
        send_message(response)
    except Exception as exc:
        send_message({"ok": False, "error": str(exc)})


if __name__ == "__main__":
    main()
