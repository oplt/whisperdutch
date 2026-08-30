from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from benchmark_concurrency import measured_call, run_concurrency  # noqa: E402
from benchmark_pipeline import summary  # noqa: E402
from benchmark_startup import find_free_port  # noqa: E402


def test_summary_uses_interpolated_percentile() -> None:
    assert summary([1, 2, 3, 4])["p95"] == 3.85


def test_measured_call_reports_queue_and_service_time() -> None:
    result, queue_wait_ms, service_ms = measured_call(__import__("time").perf_counter(), lambda value: value * 2, 4)

    assert result == 8
    assert queue_wait_ms >= 0
    assert service_ms >= 0


def test_fake_concurrency_covers_one_two_and_four_sessions() -> None:
    args = argparse.Namespace(
        sessions=[1, 2, 4],
        jobs=2,
        engine="fake",
        source_language="nl",
        target_language="en",
        mode="fast",
        fake_asr_ms=0.1,
        fake_translation_ms=0.1,
        partials_per_final=1,
    )

    rows = asyncio.run(run_concurrency(args, None))

    assert [row["sessions"] for row in rows] == [1, 2, 4]
    assert [row["final_asr_latency_ms"]["count"] for row in rows] == [2, 4, 8]
    assert [row["partial_asr_latency_ms"]["count"] for row in rows] == [2, 4, 8]
    assert all(row["translation_queue_wait_ms"]["count"] > 0 for row in rows)


def test_startup_benchmark_finds_a_bindable_port() -> None:
    port = find_free_port(18000, 18020)

    assert 18000 <= port <= 18020
