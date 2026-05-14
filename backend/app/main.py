from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .asr import get_asr_engine
from .audio import SpeechSegmenter, pcm16le_to_float32
from .sentences import SentenceAssembler
from .translator import get_translation_engine


@dataclass
class ClientConfig:
    sample_rate: int = 16000
    source_lang: str = "nl"
    target_lang: str = "en"
    mode: str = "balanced"  # fast | balanced | quality


def warmup_models() -> None:
    """Load models at backend startup so the first real subtitle is not delayed."""
    start = time.perf_counter()
    asr = get_asr_engine()
    translator = get_translation_engine()
    asr.warmup()
    translator.warmup()
    elapsed = int((time.perf_counter() - start) * 1000)
    print(f"[STARTUP] Models warmed in {elapsed} ms", flush=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await asyncio.to_thread(warmup_models)
    yield


app = FastAPI(
    title="Dutch Live Subtitle Translator",
    version="0.7.0-repaired-stable",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "dutch-live-subtitle-translator",
        "version": "0.7.0-repaired-stable",
        "websocket": "/ws/subtitles",
    }


@app.get("/debug/device")
def debug_device() -> dict[str, Any]:
    asr = get_asr_engine()
    translator = get_translation_engine()
    return {
        "asr": asr.info(),
        "translation": translator.info(),
        "pipeline": {
            "partial_asr": False,
            "final_translation_is_blocking": True,
            "reason": "UI receives complete Dutch sentence and translation together; no disappearing partials.",
            "sentence_mode": True,
            "glossary_enabled": True,
        },
        "recommended_rtx_3060_env": {
            "ASR_DEVICE": "cuda",
            "ASR_MODEL": "small or medium",
            "ASR_COMPUTE_TYPE": "float16",
            "TRANSLATION_ENGINE": "ctranslate2",
            "TRANSLATION_DEVICE": "cpu",
            "TRANSLATION_COMPUTE_TYPE": "int8",
            "END_SILENCE_SECONDS": "0.65",
            "MAX_SEGMENT_SECONDS": "5.5",
        },
    }


@app.websocket("/ws/subtitles")
async def subtitles_ws(websocket: WebSocket) -> None:
    """
    Reliable stable subtitle pipeline.

    Fixes the "nothing appears" failure:
    - audio segments are forced into a final sentence on a real pause / max segment;
    - Dutch text is sent immediately as final_pending;
    - translation is sent as a second update for the same subtitle id;
    - translation errors no longer kill the WebSocket or hide the Dutch subtitle.
    """
    await websocket.accept()

    config = ClientConfig()
    segmenter = SpeechSegmenter(sample_rate=config.sample_rate)
    segmenter.set_mode(config.mode)
    sentence_assembler = SentenceAssembler()
    processing_lock = asyncio.Lock()

    await websocket.send_json({"type": "ready", "message": "Backend WebSocket connected"})

    try:
        while True:
            message = await websocket.receive()

            if "text" in message and message["text"] is not None:
                text_payload = message["text"]
                if _is_flush(text_payload):
                    async with processing_lock:
                        finalized = segmenter.flush()
                        if finalized is not None:
                            sentences, meta = await asyncio.to_thread(transcribe_and_collect_sentences, finalized, config, sentence_assembler, True)
                            await send_sentences_with_translation(websocket, sentences, config, meta)
                        sentences = sentence_assembler.flush()
                        await send_sentences_with_translation(websocket, sentences, config, {"asr_latency_ms": 0, "audio_seconds": 0.0, "fragment": ""})
                    continue

                config = _parse_config(text_payload, config)
                segmenter.sample_rate = config.sample_rate
                segmenter.set_mode(config.mode)
                await websocket.send_json({"type": "config_ack", "config": config.__dict__})
                continue

            if "bytes" not in message or message["bytes"] is None:
                continue

            audio = pcm16le_to_float32(message["bytes"])
            finalized = segmenter.add(audio)
            if finalized is None:
                continue

            async with processing_lock:
                # force=True is intentional. The segmenter only returns here after
                # a pause or max segment duration. Without force=True, ASR output
                # often has no punctuation, so the UI can remain empty forever.
                sentences, meta = await asyncio.to_thread(transcribe_and_collect_sentences, finalized, config, sentence_assembler, True)
                await send_sentences_with_translation(websocket, sentences, config, meta)

    except WebSocketDisconnect:
        return
    except Exception as exc:
        await _safe_send_json(websocket, {"type": "error", "message": str(exc)})


def transcribe_and_collect_sentences(audio, config: ClientConfig, sentence_assembler: SentenceAssembler, force: bool) -> tuple[list[str], dict[str, Any]]:
    start = time.perf_counter()
    asr = get_asr_engine()

    prompt = sentence_assembler.context_prompt()
    dutch_fragment = asr.transcribe_dutch(audio, prompt=prompt, mode=config.mode)
    sentences, _buffer = sentence_assembler.add_fragment(dutch_fragment, force=force)
    asr_latency_ms = int((time.perf_counter() - start) * 1000)

    meta = {
        "asr_latency_ms": asr_latency_ms,
        "audio_seconds": round(float(len(audio)) / float(config.sample_rate), 2),
        "fragment": dutch_fragment,
    }
    return sentences, meta


async def send_sentences_with_translation(
    websocket: WebSocket,
    sentences: list[str],
    config: ClientConfig,
    meta: dict[str, Any],
) -> None:
    if not sentences:
        fragment = str(meta.get("fragment") or "").strip()
        # Useful during debugging: shows backend is receiving/transcribing audio,
        # but the sentence assembler decided to keep buffering.
        if fragment and os.getenv("SEND_DEBUG_FRAGMENTS", "0") == "1":
            await websocket.send_json({
                "type": "debug_fragment",
                "fragment": fragment,
                "asr_latency_ms": int(meta.get("asr_latency_ms") or 0),
            })
        return

    for sentence in sentences:
        if not sentence:
            continue
        subtitle_id = f"final-{time.time_ns()}"
        asr_latency_ms = int(meta.get("asr_latency_ms") or 0)
        audio_seconds = float(meta.get("audio_seconds") or 0.0)
        fragment = str(meta.get("fragment") or "")

        # 1) Show Dutch immediately. Do not wait for translation.
        await websocket.send_json({
            "type": "final_pending",
            "id": subtitle_id,
            "source_lang": config.source_lang,
            "target_lang": config.target_lang,
            "mode": config.mode,
            "dutch": sentence,
            "translation": "Translating…",
            "asr_latency_ms": asr_latency_ms,
            "latency_ms": asr_latency_ms,
            "audio_seconds": audio_seconds,
            "asr_fragment": fragment,
            "sentence_mode": True,
        })

        # 2) Translate and update the same row.
        translation_start = time.perf_counter()
        try:
            translation = await asyncio.to_thread(translate_one_sentence, sentence)
        except Exception as exc:
            translation = f"[translation error: {exc}]"
        translation_latency_ms = int((time.perf_counter() - translation_start) * 1000)

        await websocket.send_json({
            "type": "final",
            "id": subtitle_id,
            "source_lang": config.source_lang,
            "target_lang": config.target_lang,
            "mode": config.mode,
            "dutch": sentence,
            "translation": translation,
            "asr_latency_ms": asr_latency_ms,
            "translation_latency_ms": translation_latency_ms,
            "latency_ms": asr_latency_ms + translation_latency_ms,
            "audio_seconds": audio_seconds,
            "asr_fragment": fragment,
            "sentence_mode": True,
        })


def translate_one_sentence(sentence: str) -> str:
    translator = get_translation_engine()
    return translator.translate(sentence)


def process_audio_segment(audio, config: ClientConfig, sentence_assembler: SentenceAssembler) -> list[dict[str, Any]]:
    """Backward-compatible helper retained for older callers/tests."""
    sentences, meta = transcribe_and_collect_sentences(audio, config, sentence_assembler, True)
    return translate_sentences(
        sentences=sentences,
        config=config,
        asr_latency_ms=int(meta.get("asr_latency_ms") or 0),
        audio_seconds=float(meta.get("audio_seconds") or 0.0),
        fragment=str(meta.get("fragment") or ""),
    )


def flush_sentences(config: ClientConfig, sentence_assembler: SentenceAssembler) -> list[dict[str, Any]]:
    sentences = sentence_assembler.flush()
    return translate_sentences(sentences, config, asr_latency_ms=0, audio_seconds=0.0, fragment="")


def translate_sentences(
    sentences: list[str],
    config: ClientConfig,
    asr_latency_ms: int,
    audio_seconds: float,
    fragment: str,
) -> list[dict[str, Any]]:
    if not sentences:
        return []

    results: list[dict[str, Any]] = []
    for sentence in sentences:
        if not sentence:
            continue
        translation_start = time.perf_counter()
        try:
            translation = translate_one_sentence(sentence)
        except Exception as exc:
            translation = f"[translation error: {exc}]"
        translation_latency_ms = int((time.perf_counter() - translation_start) * 1000)
        subtitle_id = f"final-{time.time_ns()}"
        results.append(
            {
                "type": "final",
                "id": subtitle_id,
                "source_lang": config.source_lang,
                "target_lang": config.target_lang,
                "mode": config.mode,
                "dutch": sentence,
                "translation": translation,
                "asr_latency_ms": asr_latency_ms,
                "translation_latency_ms": translation_latency_ms,
                "latency_ms": asr_latency_ms + translation_latency_ms,
                "audio_seconds": audio_seconds,
                "asr_fragment": fragment,
                "sentence_mode": True,
            }
        )
    return results


def _is_flush(raw: str) -> bool:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return False
    return payload.get("type") == "flush"


def _parse_config(raw: str, current: ClientConfig) -> ClientConfig:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return current

    if payload.get("type") != "config":
        return current

    sample_rate = int(payload.get("sample_rate", current.sample_rate))
    if sample_rate != 16000:
        sample_rate = 16000

    mode = str(payload.get("mode", current.mode)).strip().lower()
    if mode not in {"fast", "balanced", "quality"}:
        mode = "balanced"

    return ClientConfig(
        sample_rate=sample_rate,
        source_lang=str(payload.get("source_lang", current.source_lang)),
        target_lang=str(payload.get("target_lang", current.target_lang)),
        mode=mode,
    )


async def _safe_send_json(websocket: WebSocket, payload: dict[str, Any]) -> None:
    try:
        await websocket.send_json(payload)
    except Exception:
        pass
