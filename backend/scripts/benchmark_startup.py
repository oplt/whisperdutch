from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import ModuleType
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
RUN_SCRIPT = BACKEND_DIR / "run_gpu.sh"
NATIVE_HOST_SCRIPT = PROJECT_ROOT / "native-host" / "start_backend_host.py"
STARTUP_STATUS_PATH = BACKEND_DIR / "logs" / "startup-status.json"


def find_free_port(start: int = 8000, stop: int = 8049) -> int:
    for port in range(start, stop + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
            try:
                candidate.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError(f"No free localhost port in {start}-{stop}")


def read_json(url: str, timeout: float = 0.2) -> tuple[int, dict[str, Any]] | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except Exception:
            return exc.code, {}
    except Exception:
        return None


def read_startup_status_file() -> dict[str, Any] | None:
    try:
        return json.loads(STARTUP_STATUS_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def process_sample(pid: int) -> tuple[int, int]:
    try:
        stat = (Path("/proc") / str(pid) / "stat").read_text().split()
        cpu_ticks = int(stat[13]) + int(stat[14])
        status = (Path("/proc") / str(pid) / "status").read_text().splitlines()
        rss_kib = next(int(line.split()[1]) for line in status if line.startswith("VmRSS:"))
        return cpu_ticks, rss_kib
    except (FileNotFoundError, PermissionError, StopIteration, ValueError):
        return 0, 0


def terminate_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=8)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    process.wait(timeout=3)


async def measure_first_ws_ready_ms(port: int, launch_started: float) -> float | None:
    import websockets

    url = f"ws://127.0.0.1:{port}/ws/subtitles"
    try:
        async with websockets.connect(url, open_timeout=5) as websocket:
            raw = await asyncio.wait_for(websocket.recv(), timeout=5)
            payload = json.loads(raw)
            if payload.get("type") == "ready":
                return round((time.perf_counter() - launch_started) * 1000.0, 3)
    except Exception:
        return None
    return None


def benchmark_direct_start(timeout_seconds: float) -> dict[str, Any]:
    port = find_free_port()
    env = os.environ.copy()
    env["BACKEND_HOST"] = "127.0.0.1"
    env["BACKEND_PORT"] = str(port)
    env["SESSION_HISTORY_ENABLED"] = "0"
    with tempfile.TemporaryFile() as output:
        launch_started = time.perf_counter()
        process = subprocess.Popen(
            ["bash", str(RUN_SCRIPT)],
            cwd=BACKEND_DIR,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        process_launch_ms = (time.perf_counter() - launch_started) * 1000.0
        live_ms: float | None = None
        ready_ms: float | None = None
        first_ws_ready_ms: float | None = None
        live_payload: dict[str, Any] | None = None
        ready_payload: dict[str, Any] | None = None
        peak_rss_kib = 0
        first_cpu_ticks, _rss = process_sample(process.pid)
        deadline = launch_started + timeout_seconds
        try:
            while time.perf_counter() < deadline:
                if process.poll() is not None:
                    break
                cpu_ticks, rss_kib = process_sample(process.pid)
                _ = cpu_ticks
                peak_rss_kib = max(peak_rss_kib, rss_kib)
                elapsed_ms = (time.perf_counter() - launch_started) * 1000.0
                if live_ms is None:
                    live = read_json(f"http://127.0.0.1:{port}/health/live")
                    if live and live[0] == 200 and live[1].get("live"):
                        live_ms = elapsed_ms
                        live_payload = live[1]
                ready = read_json(f"http://127.0.0.1:{port}/health/ready")
                if ready and ready[0] == 200 and ready[1].get("ready"):
                    ready_ms = elapsed_ms
                    ready_payload = ready[1]
                    first_ws_ready_ms = asyncio.run(measure_first_ws_ready_ms(port, launch_started))
                    break
                time.sleep(0.05)
            final_cpu_ticks, rss_kib = process_sample(process.pid)
            peak_rss_kib = max(peak_rss_kib, rss_kib)
            wall_seconds = max(time.perf_counter() - launch_started, 0.000001)
            clock_ticks = max(1, os.sysconf("SC_CLK_TCK"))
            cpu_seconds = max(0.0, (final_cpu_ticks - first_cpu_ticks) / clock_ticks)
            startup_status = read_startup_status_file()
            result = {
                "port": port,
                "process_launch_ms": round(process_launch_ms, 3),
                "health_live_ms": round(live_ms, 3) if live_ms is not None else None,
                "health_ready_ms": round(ready_ms, 3) if ready_ms is not None else None,
                "first_ws_ready_ms": first_ws_ready_ms,
                "live_payload": live_payload,
                "ready_payload": ready_payload,
                "startup_status": startup_status,
                "peak_rss_kib": peak_rss_kib,
                "startup_cpu_seconds": round(cpu_seconds, 3),
                "startup_cpu_percent_one_core": round(100.0 * cpu_seconds / wall_seconds, 3),
                "exit_code_before_cleanup": process.poll(),
            }
            if ready_ms is None:
                output.seek(0)
                result["output_tail"] = output.read().decode("utf-8", errors="replace")[-4000:]
            return result
        finally:
            terminate_process(process)


def load_native_host() -> ModuleType:
    spec = importlib.util.spec_from_file_location("phase1_native_host", NATIVE_HOST_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import {NATIVE_HOST_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def benchmark_native_host_start(timeout_seconds: float = 60.0) -> dict[str, Any]:
    native = load_native_host()
    port = find_free_port()
    with tempfile.TemporaryDirectory(prefix="subtitle-native-benchmark-") as directory:
        temp_dir = Path(directory)
        native.PID_FILE = temp_dir / "backend.pid"
        native.LOCK_FILE = temp_dir / "native-host.lock"
        native.LOG_DIR = temp_dir
        started_at = time.perf_counter()
        response = native.start_backend({"port": port, "asr_device": "cpu"})
        command_completion_ms = round((time.perf_counter() - started_at) * 1000.0, 3)
        health_live_ms: float | None = None
        health_ready_ms: float | None = None
        ready_payload: dict[str, Any] | None = None
        if response.get("status") == "live":
            health_live_ms = command_completion_ms
        deadline = started_at + timeout_seconds
        try:
            while time.perf_counter() < deadline:
                ready = read_json(f"http://127.0.0.1:{port}/health/ready", timeout=0.4)
                if ready and ready[0] == 200 and ready[1].get("ready"):
                    health_ready_ms = round((time.perf_counter() - started_at) * 1000.0, 3)
                    ready_payload = ready[1]
                    break
                time.sleep(0.05)
            return {
                "port": port,
                "command_completion_ms": command_completion_ms,
                "health_live_ms": health_live_ms,
                "health_ready_ms": health_ready_ms,
                "response": response,
                "ready_payload": ready_payload,
            }
        finally:
            native.stop_backend()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Measure process launch, live, ready, websocket-ready, and native-host startup latency.",
    )
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument("--skip-native-host", action="store_true")
    parser.add_argument("--compare-warmup", action="store_true", help="Also compare sequential vs parallel warmup.")
    parser.add_argument("--output", type=Path, help="Write JSON results to this file.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result: dict[str, Any] = {
        "benchmark": "phase11-startup",
        "direct": benchmark_direct_start(args.timeout_seconds),
        "native_host": None if args.skip_native_host else benchmark_native_host_start(args.timeout_seconds),
    }
    if args.compare_warmup:
        scripts_dir = Path(__file__).resolve().parent
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        from benchmark_startup_warmup import compare_strategies

        result["warmup_comparison"] = compare_strategies()
    payload = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
