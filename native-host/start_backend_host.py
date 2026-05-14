#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import socket
import struct
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HOST_NAME = "com.polatozgur111.dutch_subtitle_backend"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"
RUN_SCRIPT = BACKEND_DIR / "run_gpu.sh"
LOG_FILE = BACKEND_DIR / "backend.log"
PID_FILE = BACKEND_DIR / "backend.pid"
HEALTH_URL = "http://127.0.0.1:8000/health"


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


def is_port_open(host: str = "127.0.0.1", port: int = 8000, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def is_backend_healthy(timeout: float = 0.6) -> bool:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=timeout) as response:
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
    return pid if pid_is_running(pid) else None


def start_backend() -> dict:
    if is_backend_healthy():
        return {"ok": True, "status": "already_running", "message": "Backend is already running."}

    pid = existing_pid()
    if pid and is_port_open():
        return {"ok": True, "status": "already_starting", "pid": pid, "message": "Backend process is already starting."}

    if not RUN_SCRIPT.exists():
        return {"ok": False, "error": f"Cannot find {RUN_SCRIPT}"}

    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    log = open(LOG_FILE, "ab", buffering=0)
    log.write(f"\n\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Starting backend via native host\n".encode())

    env = os.environ.copy()
    env.setdefault("ASR_DEVICE", "cuda")
    env.setdefault("ASR_MODEL", "small")
    env.setdefault("ASR_COMPUTE_TYPE", "float16")
    env.setdefault("TRANSLATION_DEVICE", "cuda")

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

    return {
        "ok": True,
        "status": "started",
        "pid": process.pid,
        "message": "Backend process started. Model loading can take a moment.",
        "log_file": str(LOG_FILE),
    }


def main() -> None:
    try:
        message = read_message()
        command = message.get("command")
        if command != "start_backend":
            send_message({"ok": False, "error": f"Unsupported command: {command}"})
            return
        send_message(start_backend())
    except Exception as exc:
        send_message({"ok": False, "error": str(exc)})


if __name__ == "__main__":
    main()
