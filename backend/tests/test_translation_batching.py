from __future__ import annotations

import asyncio

import pytest
from app.inference_runtime import get_inference_runtime
from app.schemas import ClientConfig


@pytest.mark.usefixtures("reset_inference_runtime")
def test_cross_session_translation_jobs_are_batched_together(monkeypatch) -> None:
    async def run() -> None:
        runtime = get_inference_runtime()
        runtime.translation_max_concurrent = 1
        runtime.set_inline(False)
        monkeypatch.setenv("TRANSLATION_BATCH_COLLECT_MS", "0")
        await runtime.start()
        calls: list[list[str]] = []

        def fake_translate(sentences: list[str], config: ClientConfig) -> list[str]:
            calls.append(list(sentences))
            return [f"{config.target_lang}:{text}" for text in sentences]

        class FakeEngine:
            def batch_key(self, source_language: str, target_language: str) -> tuple[str, str, str]:
                return ("fp", source_language, target_language)

        monkeypatch.setattr("app.translator.get_translation_engine", lambda: FakeEngine())

        config = ClientConfig(source_lang="nl", target_lang="en")
        task_a = asyncio.create_task(
            runtime.run_translation(fake_translate, ["Hallo"], config, session_id="a"),
        )
        task_b = asyncio.create_task(
            runtime.run_translation(fake_translate, ["Dag"], config, session_id="b"),
        )
        result_a, result_b = await asyncio.gather(task_a, task_b)

        assert result_a == ["en:Hallo"]
        assert result_b == ["en:Dag"]
        assert len(calls) == 1
        assert calls[0] == ["Hallo", "Dag"]
        assert runtime.metrics.translation_batches == 1
        assert runtime.metrics.translation_batch_requests == 2
        await runtime.stop()

    asyncio.run(run())


@pytest.mark.usefixtures("reset_inference_runtime")
def test_single_waiting_translation_is_not_delayed_for_collection(monkeypatch) -> None:
    async def run() -> None:
        runtime = get_inference_runtime()
        runtime.set_inline(False)
        monkeypatch.setenv("TRANSLATION_BATCH_COLLECT_MS", "50")
        await runtime.start()
        started = asyncio.get_running_loop().time()

        def fake_translate(sentences: list[str], _config: ClientConfig) -> list[str]:
            return [text.upper() for text in sentences]

        class FakeEngine:
            def batch_key(self, source_language: str, target_language: str) -> tuple[str, str, str]:
                return ("fp", source_language, target_language)

        monkeypatch.setattr("app.translator.get_translation_engine", lambda: FakeEngine())
        config = ClientConfig(source_lang="nl", target_lang="en")
        result = await runtime.run_translation(fake_translate, ["Hallo"], config, session_id="solo")
        elapsed_ms = (asyncio.get_running_loop().time() - started) * 1000

        assert result == ["HALLO"]
        assert elapsed_ms < 40
        assert runtime.metrics.translation_batches == 0
        await runtime.stop()

    asyncio.run(run())
