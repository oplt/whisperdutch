from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .asr import get_asr_engine
from .audio import SpeechSegmenter, pcm16le_to_float32
from .sentences import SentenceAssembler
from .translator import get_translation_engine
from .logger import current_log_file, get_logger, kv, preview_text, setup_logging, tail_log

logger = get_logger("main")


@dataclass
class ClientConfig:
    sample_rate: int = 16000
    source_lang: str = "nl"
    target_lang: str = "en"
    mode: str = "balanced"  # fast | balanced | quality


class ClientLog(BaseModel):
    level: str = Field(default="info", pattern="^(debug|info|warn|warning|error)$")
    source: str = "frontend"
    message: str
    context: dict[str, Any] = Field(default_factory=dict)


def warmup_models() -> None:
    """Load models at backend startup so the first real subtitle is not delayed."""
    start = time.perf_counter()
    logger.info("startup_warmup_started")
    asr = get_asr_engine()
    translator = get_translation_engine()
    asr.warmup()
    translator.warmup()
    elapsed = int((time.perf_counter() - start) * 1000)
    logger.info("startup_warmup_completed elapsed_ms=%s", elapsed)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    logger.info("application_startup")
    await asyncio.to_thread(warmup_models)
    yield
    logger.info("application_shutdown")


app = FastAPI(
    title="Dutch Live Subtitle Translator",
    version="0.7.1-neutral-context",
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
        "version": "0.7.1-neutral-context",
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
            "reason": "UI receives stable Dutch first, then updates the same row with translation; ASR prompt is neutral by default.",
            "sentence_mode": True,
            "glossary_enabled": os.getenv("GLOSSARY_ENABLED", "0") == "1",
            "asr_initial_prompt_default": "empty",
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


@app.get("/api/logs/recent")
def recent_logs(lines: int = 200) -> dict[str, Any]:
    """Return recent daily backend log lines for the extension UI."""
    return {
        "ok": True,
        "log_file": str(current_log_file()),
        "lines": tail_log(lines),
    }


@app.post("/api/logs/client")
def client_log(payload: ClientLog) -> dict[str, Any]:
    """Receive frontend/popup logs and write them into backend/logs/backend-YYYY-MM-DD.log."""
    level_map = {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warn": logging.WARNING,
        "warning": logging.WARNING,
        "error": logging.ERROR,
    }
    kv(
        get_logger("frontend"),
        level_map.get(payload.level.lower(), logging.INFO),
        "frontend_event",
        source=payload.source,
        message=payload.message,
        context=payload.context,
    )
    return {"ok": True}


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
    client_id = f"ws-{time.time_ns()}"
    logger.info("websocket_connected client_id=%s", client_id)

    config = ClientConfig()
    segmenter = SpeechSegmenter(sample_rate=config.sample_rate)
    segmenter.set_mode(config.mode)
    sentence_assembler = SentenceAssembler()
    processing_lock = asyncio.Lock()

    await websocket.send_json({"type": "ready", "message": "Backend WebSocket connected", "client_id": client_id})

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
                logger.info("websocket_config client_id=%s config=%s", client_id, config.__dict__)
                await websocket.send_json({"type": "config_ack", "config": config.__dict__})
                continue

            if "bytes" not in message or message["bytes"] is None:
                continue

            audio = pcm16le_to_float32(message["bytes"])
            finalized = segmenter.add(audio)
            if finalized is None:
                continue

            logger.debug("audio_segment_finalized client_id=%s samples=%s seconds=%.2f", client_id, len(finalized), float(len(finalized)) / float(config.sample_rate))

            async with processing_lock:
                # force=True is intentional. The segmenter only returns here after
                # a pause or max segment duration. Without force=True, ASR output
                # often has no punctuation, so the UI can remain empty forever.
                sentences, meta = await asyncio.to_thread(transcribe_and_collect_sentences, finalized, config, sentence_assembler, True)
                await send_sentences_with_translation(websocket, sentences, config, meta)

    except WebSocketDisconnect:
        logger.info("websocket_disconnected client_id=%s", client_id)
        return
    except Exception as exc:
        logger.exception("websocket_failure client_id=%s", client_id)
        await _safe_send_json(websocket, {"type": "error", "message": str(exc)})


def transcribe_and_collect_sentences(audio, config: ClientConfig, sentence_assembler: SentenceAssembler, force: bool) -> tuple[list[str], dict[str, Any]]:
    start = time.perf_counter()
    asr = get_asr_engine()

    prompt = sentence_assembler.context_prompt()
    dutch_fragment = asr.transcribe_dutch(audio, prompt=prompt, mode=config.mode)
    sentences, _buffer = sentence_assembler.add_fragment(dutch_fragment, force=force)
    asr_latency_ms = int((time.perf_counter() - start) * 1000)

    audio_seconds = round(float(len(audio)) / float(config.sample_rate), 2)
    meta = {
        "asr_latency_ms": asr_latency_ms,
        "audio_seconds": audio_seconds,
        "fragment": dutch_fragment,
    }
    logger.info(
        "asr_completed mode=%s audio_seconds=%.2f latency_ms=%s sentences=%s fragment=%s",
        config.mode,
        audio_seconds,
        asr_latency_ms,
        len(sentences),
        preview_text(dutch_fragment),
    )
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
        if fragment:
            logger.debug("sentence_buffering fragment=%s asr_latency_ms=%s", preview_text(fragment), int(meta.get("asr_latency_ms") or 0))
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

        logger.info("subtitle_pending id=%s asr_latency_ms=%s audio_seconds=%.2f dutch=%s", subtitle_id, asr_latency_ms, audio_seconds, preview_text(sentence))

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
            logger.exception("translation_failure id=%s dutch=%s", subtitle_id, preview_text(sentence))
            translation = f"[translation error: {exc}]"
        translation_latency_ms = int((time.perf_counter() - translation_start) * 1000)

        logger.info("subtitle_final id=%s translation_latency_ms=%s total_latency_ms=%s translation=%s", subtitle_id, translation_latency_ms, asr_latency_ms + translation_latency_ms, preview_text(translation))

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
