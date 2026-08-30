from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]


WARMUP_WORKER = """
import json
import time

from app import asr, model_runtime, translator

asr.get_asr_engine.cache_clear()
translator.get_translation_engine.cache_clear()

generation = model_runtime.runtime_state.begin_startup()
started = time.perf_counter()
model_runtime.warmup_models(generation)
elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
payload = {
    "ready": model_runtime.runtime_state.is_ready(),
    "elapsed_ms": elapsed_ms,
    "startup_timing": model_runtime.runtime_state.startup_timing_snapshot(),
}
print(json.dumps(payload))
"""


def run_strategy(strategy: str) -> dict[str, Any]:
    env = os.environ.copy()
    env["STARTUP_WARMUP_STRATEGY"] = strategy
    env["SESSION_HISTORY_ENABLED"] = "0"
    env.setdefault("LOG_LEVEL", "ERROR")
    started = time.perf_counter()
    completed = subprocess.run(
        [sys.executable, "-c", WARMUP_WORKER],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    wall_ms = round((time.perf_counter() - started) * 1000, 3)
    if completed.returncode != 0:
        return {
            "strategy": strategy,
            "ok": False,
            "wall_ms": wall_ms,
            "stderr": completed.stderr[-4000:],
            "stdout": completed.stdout[-4000:],
        }
    try:
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        return {
            "strategy": strategy,
            "ok": False,
            "wall_ms": wall_ms,
            "error": f"{type(exc).__name__}: {exc}",
            "stdout": completed.stdout[-4000:],
        }
    payload["strategy"] = strategy
    payload["ok"] = bool(payload.get("ready"))
    payload["wall_ms"] = wall_ms
    return payload


def compare_strategies() -> dict[str, Any]:
    sequential = run_strategy("sequential")
    parallel = run_strategy("parallel")
    recommendation = "sequential"
    if sequential.get("ok") and parallel.get("ok"):
        seq_ms = float(sequential.get("elapsed_ms") or sequential["wall_ms"])
        par_ms = float(parallel.get("elapsed_ms") or parallel["wall_ms"])
        if par_ms + 250 < seq_ms:
            recommendation = "parallel"
    return {
        "benchmark": "phase11-startup-warmup",
        "sequential": sequential,
        "parallel": parallel,
        "recommendation": recommendation,
        "default_strategy": "sequential",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare sequential vs parallel model warmup strategies in isolated subprocesses.",
    )
    parser.add_argument("--output", type=Path, help="Write JSON results to this file.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = compare_strategies()
    payload = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
