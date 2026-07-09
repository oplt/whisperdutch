#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import signal
import socket
import struct
import subprocess
import sys
import time
from datetime import date
import urllib.request
from pathlib import Path

HOST_NAME = "com.polatozgur111.dutch_subtitle_backend"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"
RUN_SCRIPT = BACKEND_DIR / "run_gpu.sh"
LOG_DIR = BACKEND_DIR / "logs"


def native_log_file() -> Path:
    return LOG_DIR / f"native-host-{date.today().isoformat()}.log"
PID_FILE = BACKEND_DIR / "backend.pid"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


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
    message = sys.stdin.buffer.read(message_length).decode("utf-8")
    return json.loads(message)


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
            return bool(data.get("ok"))
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


def existing_pid() -> int | None:
    try:
        pid = int(PID_FILE.read_text().strip())
    except Exception:
        return None
    if pid_is_running(pid):
        return pid
    cleanup_stale_pid()
    return None


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
        return int(raw)
    except (TypeError, ValueError):
        return DEFAULT_PORT


def start_backend(message: dict | None = None) -> dict:
    preferred_port = requested_port(message)
    for port in range(DEFAULT_PORT, DEFAULT_PORT + 50):
        if is_backend_healthy(port):
            return {"ok": True, "status": "already_running", "message": "Backend is already running.", **backend_urls(port)}

    port = preferred_port
    if is_port_open(DEFAULT_HOST, port):
        port = find_available_port(preferred_port + 1)

    pid = existing_pid()
    if pid and is_port_open(DEFAULT_HOST, port):
        return {"ok": True, "status": "already_starting", "pid": pid, "message": "Backend process is already starting.", **backend_urls(port)}

    if not RUN_SCRIPT.exists():
        return {"ok": False, "error": f"Cannot find {RUN_SCRIPT}"}

    log_path = native_log_file()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = open(log_path, "ab", buffering=0)
    log.write(f"\n\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Starting backend via native host\n".encode())

    env = os.environ.copy()
    env.setdefault("ASR_DEVICE", "cuda")
    env.setdefault("ASR_MODEL", "small")
    env.setdefault("ASR_COMPUTE_TYPE", "float16")
    env.setdefault("TRANSLATION_DEVICE", "cpu")
    env.setdefault("BACKEND_LOG_DIR", "logs")
    env.setdefault("BACKEND_LOG_PREFIX", "backend")
    env.setdefault("LOG_LEVEL", "INFO")
    env.setdefault("LOG_TRANSCRIPT_TEXT", "0")
    env["BACKEND_PORT"] = str(port)
    env.setdefault("BACKEND_HOST", DEFAULT_HOST)

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
    PID_FILE.write_text(str(process.pid))
    for _attempt in range(30):
        time.sleep(0.5)
        if is_backend_healthy(port, timeout=0.4):
            return {
                "ok": True,
                "status": "ready",
                "pid": process.pid,
                "message": "Backend is ready.",
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
        if command == "start_backend":
            send_message(start_backend(message))
        elif command == "stop_backend":
            send_message(stop_backend())
        elif command == "restart_backend":
            send_message(restart_backend(message))
        else:
            send_message({"ok": False, "error": f"Unsupported command: {command}"})
    except Exception as exc:
        send_message({"ok": False, "error": str(exc)})


if __name__ == "__main__":
    main()
