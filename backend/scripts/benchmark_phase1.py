from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import tempfile
import wave
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
PROJECT_ROOT = BACKEND_DIR.parent
ARTIFACTS_DIR = PROJECT_ROOT / "docs" / "benchmark-artifacts"


def git_metadata() -> dict[str, str | None]:
    def run(*args: str) -> str | None:
        try:
            completed = subprocess.run(
                ["git", "-C", str(PROJECT_ROOT), *args],
                check=True,
                capture_output=True,
                text=True,
            )
            return completed.stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            return None

    return {
        "commit": run("rev-parse", "HEAD"),
        "commit_short": run("rev-parse", "--short", "HEAD"),
        "branch": run("rev-parse", "--abbrev-ref", "HEAD"),
        "subject": run("log", "-1", "--format=%s"),
    }


def package_versions() -> dict[str, str]:
    versions = {"python": platform.python_version()}
    for package in ("faster-whisper", "ctranslate2", "transformers", "fastapi", "numpy"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "not-installed"
    return versions


def cuda_metadata() -> dict[str, Any]:
    try:
        import ctranslate2

        count = int(ctranslate2.get_cuda_device_count())
    except Exception as exc:
        return {"cuda_available": False, "cuda_device_count": 0, "error": str(exc)}
    return {"cuda_available": count > 0, "cuda_device_count": count}


def runtime_configuration() -> dict[str, str]:
    keys = (
        "ASR_MODEL",
        "ASR_DEVICE",
        "ASR_COMPUTE_TYPE",
        "TRANSLATION_MODEL_FAMILY",
        "TRANSLATION_MODEL",
        "TRANSLATION_TOKENIZER",
        "TRANSLATION_DEVICE",
        "TRANSLATION_COMPUTE_TYPE",
        "TRANSLATION_ENGINE",
    )
    return {key: os.getenv(key, "") for key in keys}


def write_synthetic_wav(path: Path, *, sample_rate: int = 16000, seconds: float = 30.0) -> Path:
    sample_count = int(sample_rate * seconds)
    time_axis = np.arange(sample_count, dtype=np.float32) / sample_rate
    syllable_rate = 4.0
    amplitude = 0.35 * (0.5 + 0.5 * np.sin(2 * np.pi * syllable_rate * time_axis))
    carrier = (
        0.55 * np.sin(2 * np.pi * 160 * time_axis)
        + 0.30 * np.sin(2 * np.pi * 320 * time_axis)
        + 0.15 * np.sin(2 * np.pi * 640 * time_axis)
    )
    audio = np.clip(carrier * amplitude, -0.95, 0.95)
    pcm16 = (audio * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm16.tobytes())
    return path


def run_python_script(script_name: str, output_path: Path, *args: str) -> dict[str, Any]:
    command = [sys.executable, str(SCRIPT_DIR / script_name), *args, "--output", str(output_path)]
    env = os.environ.copy()
    env.setdefault("LOG_LEVEL", "ERROR")
    subprocess.run(
        command,
        cwd=BACKEND_DIR,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return json.loads(output_path.read_text(encoding="utf-8"))


def build_report(*, include_real_models: bool, max_segments: int, skip_startup: bool) -> dict[str, Any]:
    generated_at = datetime.now(UTC).isoformat()
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="phase1-benchmark-", dir=ARTIFACTS_DIR) as temp_dir:
        temp_path = Path(temp_dir)
        wav_path = write_synthetic_wav(temp_path / "phase1-synthetic.wav", seconds=30.0)

        startup: dict[str, Any] | None = None
        if not skip_startup:
            startup = run_python_script(
                "benchmark_startup.py",
                temp_path / "startup.json",
                "--timeout-seconds",
                "120",
            )

        pipeline = run_python_script(
            "benchmark_pipeline.py",
            temp_path / "pipeline.json",
            str(wav_path),
            "--mode",
            "fast",
            "--segment-seconds",
            "3.0",
            "--max-segments",
            str(max_segments),
            "--partial-runs",
            "10",
            "--cache-hit-runs",
            "100",
            "--translation-probe-runs",
            "10",
            "--json",
        )

        concurrency_fake = run_python_script(
            "benchmark_concurrency.py",
            temp_path / "concurrency-fake.json",
            "--engine",
            "fake",
            "--sessions",
            "1",
            "2",
            "4",
            "--jobs",
            "8",
            "--partials-per-final",
            "1",
        )

        concurrency_real: dict[str, Any] | None = None
        if include_real_models:
            concurrency_real = run_python_script(
                "benchmark_concurrency.py",
                temp_path / "concurrency-real.json",
                "--engine",
                "real",
                "--wav",
                str(wav_path),
                "--sessions",
                "1",
                "2",
                "4",
                "--jobs",
                str(min(4, max_segments)),
                "--segment-seconds",
                "3.0",
                "--mode",
                "fast",
                "--partials-per-final",
                "1",
            )

    workload_cache = pipeline.get("translation_cache", {}).get("workload", {})
    total_lookups = int(workload_cache.get("hits", 0) or 0) + int(workload_cache.get("misses", 0) or 0)
    cache_hit_ratio = float(workload_cache.get("hit_ratio") or 0.0)
    if total_lookups == 0 and pipeline.get("translation_cache", {}).get("l1_hit_latency_ms", {}).get("count"):
        cache_hit_ratio = 1.0

    return {
        "benchmark": "phase1-next-baseline",
        "generated_at_utc": generated_at,
        "git": git_metadata(),
        "host": {
            "platform": platform.platform(),
            "processor": platform.processor() or platform.machine(),
            "logical_cpus": os.cpu_count(),
        },
        "versions": package_versions(),
        "cuda": cuda_metadata(),
        "configuration": runtime_configuration(),
        "startup": startup,
        "pipeline_fast": pipeline,
        "concurrency_fake": concurrency_fake,
        "concurrency_real": concurrency_real,
        "derived": {
            "health_live_ms": (startup or {}).get("direct", {}).get("health_live_ms"),
            "health_ready_ms": (startup or {}).get("direct", {}).get("health_ready_ms"),
            "native_host_start_ms": ((startup or {}).get("native_host") or {}).get("latency_ms"),
            "partial_asr_latency_ms": pipeline.get("summary", {}).get("partial_asr_latency_ms"),
            "final_asr_latency_ms": pipeline.get("summary", {}).get("asr_latency_ms"),
            "translation_latency_ms": pipeline.get("summary", {}).get("mt_latency_ms")
            or pipeline.get("summary", {}).get("translation_probe_latency_ms"),
            "final_end_to_end_latency_ms": pipeline.get("summary", {}).get("total_latency_ms"),
            "realtime_factor": pipeline.get("summary", {}).get("realtime_factor"),
            "translation_cache_hit_ratio": round(cache_hit_ratio, 4),
            "translation_cache_l1_hit_latency_ms": pipeline.get("translation_cache", {}).get("l1_hit_latency_ms"),
            "max_rss_kib_pipeline": pipeline.get("resources", {}).get("max_rss_kib"),
            "cpu_percent_pipeline": pipeline.get("resources", {}).get("cpu_percent_system_capacity"),
        },
        "not_measured_in_this_harness": {
            "capture_to_backend_audio_delay_ms": "PCM WebSocket frames carry no capture timestamp.",
            "dropped_audio_chunks": "Requires live browser capture telemetry.",
            "websocket_buffered_amount_high_water_mark": "Frontend does not persist this metric yet.",
            "history_write_backlog": "Excluded from offline harness; SESSION_HISTORY_ENABLED may differ in production.",
            "asr_queue_wait_ms_live": "Offline harness uses synthetic enqueue timestamps; see concurrency_fake results.",
            "translation_queue_wait_ms_live": "Offline harness uses synthetic enqueue timestamps; see concurrency_fake results.",
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Phase 1 baseline benchmarks and emit consolidated JSON.")
    parser.add_argument("--include-real-models", action="store_true", help="Also run real-model 1/2/4 session concurrency.")
    parser.add_argument("--max-segments", type=int, default=6, help="Pipeline segment cap for the synthetic 30s WAV.")
    parser.add_argument("--skip-startup", action="store_true", help="Skip process launch/live/ready measurement.")
    parser.add_argument("--output", type=Path, help="Optional JSON output path.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = build_report(
        include_real_models=args.include_real_models,
        max_segments=max(1, args.max_segments),
        skip_startup=args.skip_startup,
    )
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_path = args.output or (ARTIFACTS_DIR / f"phase1-baseline-{timestamp}.json")
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    latest_path = ARTIFACTS_DIR / "phase1-baseline-latest.json"
    latest_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"artifact": str(output_path), "latest": str(latest_path)}, indent=2))


if __name__ == "__main__":
    main()
