"""Phase 12 regression and stress coverage for the live subtitle pipeline."""

from __future__ import annotations

import asyncio
import threading

import pytest
from app.inference_runtime import AsrPriority, get_inference_runtime


@pytest.mark.usefixtures("reset_inference_runtime")
@pytest.mark.parametrize(
    ("session_ids", "jobs_per_session"),
    [
        (("s1", "s2"), 6),
        (("s1", "s2", "s3", "s4"), 4),
    ],
)
def test_fake_session_stress_completes_with_round_robin_fairness(
    session_ids: tuple[str, ...],
    jobs_per_session: int,
) -> None:
    async def run() -> None:
        runtime = get_inference_runtime()
        runtime.asr_max_concurrent = 1
        runtime.asr_max_pending = 64
        runtime.translation_max_concurrent = 1
        runtime.translation_max_pending = 64
        runtime.set_inline(False)
        await runtime.start()

        order: list[str] = []
        gate = threading.Event()

        def record(session_id: str, job_id: int) -> str:
            gate.wait(timeout=2)
            order.append(f"{session_id}:{job_id}")
            return f"{session_id}:{job_id}"

        tasks = [
            runtime.run_asr(
                AsrPriority.FINAL,
                record,
                session_id,
                job_id,
                session_id=session_id,
            )
            for session_id in session_ids
            for job_id in range(jobs_per_session)
        ]
        await asyncio.sleep(0.05)
        gate.set()
        results = await asyncio.gather(*tasks)

        expected_count = len(session_ids) * jobs_per_session
        assert len(results) == expected_count
        assert len(order) == expected_count

        first_batch = order[: len(session_ids)]
        assert len({entry.split(":")[0] for entry in first_batch}) == len(session_ids)

        per_session_counts = {session_id: 0 for session_id in session_ids}
        for entry in order:
            per_session_counts[entry.split(":")[0]] += 1
        assert all(count == jobs_per_session for count in per_session_counts.values())

        await runtime.stop()

    asyncio.run(run())
