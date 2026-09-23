from __future__ import annotations

import asyncio
from collections import deque

import pytest
from app.inference_runtime import AsrPriority, InferenceRejectedError, get_inference_runtime
from app.metrics import SeriesSummaryCache, cached_summary
from app.security import allowed_origins, origin_allowed
from app.ws_session import ConfigIgnored, _decode_json_object, _is_flush, _parse_audio_gap, _parse_config


@pytest.mark.parametrize("raw", ["null", "42", '"text"', "[]", "true"])
def test_control_parsers_ignore_non_object_json(raw: str) -> None:
    assert _decode_json_object(raw) is None
    assert _is_flush(raw) is False
    assert _parse_audio_gap(raw) is None
    with pytest.raises(ConfigIgnored):
        _parse_config(raw)


def test_control_parsers_accept_object_messages() -> None:
    assert _is_flush('{"type":"flush"}') is True
    assert _parse_audio_gap('{"type":"audio_gap","reason":"seek"}')["reason"] == "seek"
    with pytest.raises(ConfigIgnored):
        _parse_config('{"type":"flush"}')


def test_cached_summary_invalidates_when_interior_samples_change() -> None:
    values = deque([1.0, 2.0, 3.0, 1.0], maxlen=4)
    cache = SeriesSummaryCache()
    first = cached_summary(values, cache)
    assert first["p50"] == 1.5

    values.clear()
    values.extend([1.0, 100.0, 100.0, 1.0])
    second = cached_summary(values, cache)
    assert second["p50"] == 50.5
    assert second is not first


@pytest.mark.usefixtures("reset_inference_runtime")
def test_final_jobs_are_bounded_by_asr_max_pending() -> None:
    async def run() -> None:
        runtime = get_inference_runtime()
        runtime.asr_max_pending = 1
        runtime.asr_max_concurrent = 1
        runtime.set_inline(False)
        await runtime.start()
        import threading

        started = threading.Event()
        release = threading.Event()

        def slow(label: str) -> str:
            if label == "blocker":
                started.set()
                assert release.wait(timeout=2)
            return label

        blocker = asyncio.create_task(runtime.run_asr(AsrPriority.FINAL, slow, "blocker", session_id="a"))
        await asyncio.sleep(0.02)
        assert started.is_set()
        queued = asyncio.create_task(runtime.run_asr(AsrPriority.FINAL, slow, "queued", session_id="b"))
        await asyncio.sleep(0.02)
        with pytest.raises(InferenceRejectedError, match="global ASR queue full"):
            await runtime.run_asr(AsrPriority.FINAL, slow, "overflow", session_id="c")
        release.set()
        await blocker
        await queued
        await runtime.stop()

    asyncio.run(run())


@pytest.mark.usefixtures("reset_inference_runtime")
def test_stop_settles_queued_futures_and_rejects_new_work() -> None:
    async def run() -> None:
        runtime = get_inference_runtime()
        runtime.asr_max_pending = 8
        runtime.asr_max_concurrent = 1
        runtime.set_inline(False)
        await runtime.start()
        import threading

        started = threading.Event()
        release = threading.Event()

        def slow(label: str) -> str:
            if label == "blocker":
                started.set()
                assert release.wait(timeout=2)
            return label

        blocker = asyncio.create_task(runtime.run_asr(AsrPriority.FINAL, slow, "blocker", session_id="a"))
        await asyncio.sleep(0.02)
        assert started.is_set()
        pending = [
            asyncio.create_task(runtime.run_asr(AsrPriority.FINAL, slow, f"p{i}", session_id="a"))
            for i in range(4)
        ]
        await asyncio.sleep(0.02)
        stop_task = asyncio.create_task(runtime.stop())
        results = await asyncio.gather(*pending, return_exceptions=True)
        await stop_task
        release.set()
        await asyncio.gather(blocker, return_exceptions=True)
        assert all(isinstance(result, InferenceRejectedError) for result in results)
        with pytest.raises(InferenceRejectedError, match="stopped"):
            await runtime.run_asr(AsrPriority.FINAL, lambda: "x", session_id="z")

    asyncio.run(run())


@pytest.mark.usefixtures("reset_inference_runtime")
def test_idle_sessions_are_removed_from_round_robin() -> None:
    async def run() -> None:
        runtime = get_inference_runtime()
        runtime.set_inline(False)
        await runtime.start()
        await runtime.run_asr(AsrPriority.FINAL, lambda: "ok", session_id="temp")
        await asyncio.sleep(0.05)
        assert "temp" not in runtime._asr_session_rr
        await runtime.stop()

    asyncio.run(run())


def test_firefox_origin_requires_explicit_registration(monkeypatch) -> None:
    monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
    monkeypatch.delenv("DUTCH_SUBTITLE_EXTENSION_ID", raising=False)
    monkeypatch.delenv("EXTENSION_ID", raising=False)
    monkeypatch.delenv("DUTCH_SUBTITLE_FIREFOX_ORIGINS", raising=False)
    assert origin_allowed("moz-extension://abcd-1234") is False

    monkeypatch.setenv("DUTCH_SUBTITLE_FIREFOX_ORIGINS", "moz-extension://abcd-1234")
    assert origin_allowed("moz-extension://abcd-1234") is True
    assert "moz-extension://abcd-1234" in allowed_origins()
    assert origin_allowed("moz-extension://other") is False


def test_asr_passes_local_files_only(monkeypatch) -> None:
    from app import asr

    captured: dict[str, object] = {}

    class FakeWhisperModel:
        def __init__(self, *_args, **kwargs) -> None:
            captured.update(kwargs)

        def transcribe(self, *_args, **_kwargs):
            return [], type("Info", (), {"language": "nl"})()

    monkeypatch.setattr(asr, "WhisperModel", FakeWhisperModel)
    monkeypatch.setenv("ASR_DEVICE", "cpu")
    monkeypatch.setenv("LOCAL_MODELS_ONLY", "1")
    engine = asr.TranscriptionEngine()
    assert captured.get("local_files_only") is True
    assert engine.info()["asr_local_files_only"] is True
