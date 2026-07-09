from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from .audio import SpeechSegmenter, pcm16le_to_float32
from .errors import map_exception
from .history import session_history_store
from .logger import get_logger, preview_text
from .metrics import SessionMetrics, session_metrics_store
from .pipeline import adapt_segmenter, transcribe_and_collect_sentences, transcribe_partial, translate_many_sentences
from .security import origin_allowed
from .schemas import ClientConfig, ClientConfigMessage
from .sentences import SentenceAssembler

logger = get_logger("ws")


@dataclass
class SegmentJob:
    kind: Literal["final", "flush", "partial"]
    audio: np.ndarray | None
    config: ClientConfig
    created_at: float
    force: bool = True


@dataclass
class SessionStats:
    audio_chunks: int = 0
    finalized_segments: int = 0
    partial_segments: int = 0
    dropped_segments: int = 0
    merged_segments: int = 0
    translations_started: int = 0
    translations_cancelled: int = 0
    max_queue_depth: int = 0


class SubtitleWebSocketSession:
    def __init__(self, websocket: WebSocket) -> None:
        self.websocket = websocket
        self.client_id = f"ws-{time.time_ns()}"
        self.config = ClientConfig()
        self.segmenter = SpeechSegmenter(sample_rate=self.config.sample_rate)
        self.segmenter.set_mode(self.config.mode)
        self.sentence_assembler = SentenceAssembler()
        self.queue: asyncio.Queue[SegmentJob] = asyncio.Queue(maxsize=int(os.getenv("PIPELINE_QUEUE_MAX_SEGMENTS", "3")))
        self.send_lock = asyncio.Lock()
        self.translation_tasks: set[asyncio.Task[None]] = set()
        self.processor_task: asyncio.Task[None] | None = None
        self.stats = SessionStats()
        self.metrics: SessionMetrics = session_metrics_store.create(self.client_id)
        session_history_store.save_session(self.metrics)
        self.closed = False
        self.partial_enabled = _env_bool("PARTIAL_ASR_ENABLED", True)
        self.partial_interval_ms = int(os.getenv("PARTIAL_ASR_INTERVAL_MS", "900"))
        self.partial_max_seconds = float(os.getenv("PARTIAL_ASR_MAX_SECONDS", "1.8"))
        self._last_partial_at = 0.0

    async def run(self) -> None:
        origin = self.websocket.headers.get("origin")
        if not origin_allowed(origin):
            logger.warning("websocket_origin_rejected client_id=%s origin=%s", self.client_id, origin)
            await self.websocket.close(code=1008)
            return
        await self.websocket.accept()
        logger.info("websocket_connected client_id=%s", self.client_id)
        self.processor_task = asyncio.create_task(self._process_jobs(), name=f"{self.client_id}-processor")
        await self._send_json({"type": "ready", "message": "Backend WebSocket connected", "client_id": self.client_id})

        try:
            await self._read_messages()
        except WebSocketDisconnect:
            logger.info("websocket_disconnected client_id=%s stats=%s", self.client_id, self.stats)
        except Exception as exc:
            logger.exception("websocket_failure client_id=%s", self.client_id)
            await self._safe_send_json(map_exception(exc).payload(debug_enabled=_env_bool("DEBUG_ERRORS", False)))
        finally:
            await self._cleanup()
            logger.info("websocket_cleanup_completed client_id=%s stats=%s", self.client_id, self.stats)

    async def _read_messages(self) -> None:
        while True:
            message = await self.websocket.receive()

            if "text" in message and message["text"] is not None:
                await self._handle_text(message["text"])
                continue

            if "bytes" not in message or message["bytes"] is None:
                continue

            audio = pcm16le_to_float32(message["bytes"])
            self.stats.audio_chunks += 1
            self.metrics.audio_chunks = self.stats.audio_chunks
            self.metrics.touch()
            finalized = self.segmenter.add(audio)
            await self._maybe_enqueue_partial()
            if finalized is None:
                continue

            self.stats.finalized_segments += 1
            self.metrics.finalized_segments = self.stats.finalized_segments
            logger.debug(
                "audio_segment_finalized client_id=%s samples=%s seconds=%.2f queue_depth=%s",
                self.client_id,
                len(finalized),
                float(len(finalized)) / float(self.config.sample_rate),
                self.queue.qsize(),
            )
            await self._enqueue(SegmentJob("final", finalized, self.config, time.perf_counter(), force=True))

    async def _handle_text(self, raw: str) -> None:
        if _is_flush(raw):
            finalized = self.segmenter.flush()
            if finalized is not None:
                await self._enqueue(SegmentJob("final", finalized, self.config, time.perf_counter(), force=True))
            await self._enqueue(SegmentJob("flush", None, self.config, time.perf_counter(), force=True))
            return

        try:
            parsed = _parse_config(raw)
        except ConfigIgnored:
            return
        except ValidationError as exc:
            errors = exc.errors(include_url=False, include_context=False, include_input=False)
            logger.warning("websocket_config_invalid client_id=%s errors=%s", self.client_id, errors)
            await self._send_json(
                {
                    "type": "config_error",
                    "code": "invalid_config",
                    "message": "Invalid subtitle config.",
                    "errors": errors,
                }
            )
            return

        self.config = parsed
        self.sentence_assembler.session_prompt = self.config.context_prompt
        self.segmenter.sample_rate = self.config.sample_rate
        self.segmenter.set_mode(self.config.mode)
        self.metrics.mode = self.config.mode
        self.metrics.reconnects = self.config.reconnect_count
        self.metrics.touch()
        logger.info("websocket_config client_id=%s config=%s", self.client_id, self.config.__dict__)
        await self._send_json({"type": "config_ack", "config": self.config.__dict__})

    async def _maybe_enqueue_partial(self) -> None:
        if not self.partial_enabled or self.queue.qsize() > 0:
            return
        now = time.perf_counter()
        if (now - self._last_partial_at) * 1000 < self.partial_interval_ms:
            return
        snapshot = self.segmenter.current_snapshot(max_seconds=self.partial_max_seconds)
        if snapshot is None:
            return
        self._last_partial_at = now
        self.stats.partial_segments += 1
        self.metrics.partial_segments = self.stats.partial_segments
        await self._enqueue(SegmentJob("partial", snapshot, self.config, now, force=False), merge_when_full=False)

    async def _enqueue(self, job: SegmentJob, merge_when_full: bool = True) -> None:
        if self.queue.full():
            try:
                old = self.queue.get_nowait()
                self.queue.task_done()
            except asyncio.QueueEmpty:
                old = None
            if merge_when_full and old and old.kind == "final" and job.kind == "final" and old.audio is not None and job.audio is not None:
                job.audio = np.concatenate([old.audio, job.audio]).astype(np.float32, copy=False)
                self.stats.merged_segments += 1
                self.metrics.merged_segments = self.stats.merged_segments
            else:
                self.stats.dropped_segments += 1
                self.metrics.dropped_segments = self.stats.dropped_segments
                logger.warning("pipeline_queue_drop client_id=%s dropped_kind=%s new_kind=%s", self.client_id, getattr(old, "kind", None), job.kind)
        await self.queue.put(job)
        self.stats.max_queue_depth = max(self.stats.max_queue_depth, self.queue.qsize())
        self.metrics.max_queue_depth = self.stats.max_queue_depth
        self.metrics.touch()

    async def _process_jobs(self) -> None:
        while True:
            job = await self.queue.get()
            try:
                if job.kind == "partial":
                    await self._process_partial(job)
                elif job.kind == "flush":
                    await self._send_sentences(self.sentence_assembler.flush(), job.config, {"asr_latency_ms": 0, "audio_seconds": 0.0, "fragment": ""})
                elif job.audio is not None:
                    await self._process_final(job)
            finally:
                self.queue.task_done()

    async def _process_partial(self, job: SegmentJob) -> None:
        if job.audio is None:
            return
        prompt = self.sentence_assembler.context_prompt()
        try:
            text, meta = await asyncio.to_thread(transcribe_partial, job.audio, job.config, prompt)
        except Exception as exc:
            logger.exception("partial_asr_failure client_id=%s", self.client_id)
            await self._safe_send_json(map_exception(exc).payload(debug_enabled=_env_bool("DEBUG_ERRORS", False)))
            return
        if not text:
            return
        await self._send_json(
            {
                "type": "partial",
                "dutch": text,
                "latency_ms": meta["latency_ms"],
                "audio_seconds": meta["audio_seconds"],
                "mode": job.config.mode,
                "quality": meta.get("quality"),
            }
        )

    async def _process_final(self, job: SegmentJob) -> None:
        if job.audio is None:
            return
        try:
            sentences, meta = await asyncio.to_thread(
                transcribe_and_collect_sentences,
                job.audio,
                job.config,
                self.sentence_assembler,
                job.force,
            )
        except Exception as exc:
            logger.exception("final_asr_failure client_id=%s", self.client_id)
            await self._safe_send_json(map_exception(exc).payload(debug_enabled=_env_bool("DEBUG_ERRORS", False)))
            return
        adapt_segmenter(self.segmenter, job.config, float(meta.get("realtime_factor") or 0.0))
        await self._maybe_degrade_mode(job.config, float(meta.get("realtime_factor") or 0.0))
        self.metrics.asr_latency_ms.append(int(meta.get("asr_latency_ms") or 0))
        self.metrics.audio_seconds.append(float(meta.get("audio_seconds") or 0.0))
        self.metrics.realtime_factors.append(float(meta.get("realtime_factor") or 0.0))
        self.metrics.touch()
        await self._send_sentences(sentences, job.config, meta)

    async def _maybe_degrade_mode(self, config: ClientConfig, realtime_factor: float) -> None:
        if config.mode != "balanced" or realtime_factor <= 1.2:
            return
        self.config = ClientConfig(
            sample_rate=config.sample_rate,
            source_lang=config.source_lang,
            target_lang=config.target_lang,
            mode="fast",
            context_prompt=config.context_prompt,
            reconnect_count=config.reconnect_count,
        )
        self.segmenter.set_mode("fast")
        logger.info(
            "adaptive_mode_degraded client_id=%s from_mode=%s to_mode=fast realtime_factor=%.3f",
            self.client_id,
            config.mode,
            realtime_factor,
        )
        await self._send_json({"type": "config_ack", "config": self.config.__dict__, "adaptive": True})

    async def _send_sentences(self, sentences: list[str], config: ClientConfig, meta: dict[str, Any]) -> None:
        if not sentences:
            fragment = str(meta.get("fragment") or "").strip()
            if fragment:
                logger.debug("sentence_buffering fragment=%s asr_latency_ms=%s", preview_text(fragment), int(meta.get("asr_latency_ms") or 0))
            if fragment and os.getenv("SEND_DEBUG_FRAGMENTS", "0") == "1":
                await self._send_json({"type": "debug_fragment", "fragment": fragment, "asr_latency_ms": int(meta.get("asr_latency_ms") or 0)})
            return

        subtitle_items: list[dict[str, Any]] = []
        asr_latency_ms = int(meta.get("asr_latency_ms") or 0)
        audio_seconds = float(meta.get("audio_seconds") or 0.0)
        fragment = str(meta.get("fragment") or "")
        quality = meta.get("quality") or {}
        for sentence in sentences:
            if not sentence:
                continue
            subtitle_id = f"final-{time.time_ns()}"
            subtitle_items.append({"id": subtitle_id, "sentence": sentence, "quality": quality})
            logger.info("subtitle_pending id=%s asr_latency_ms=%s audio_seconds=%.2f dutch=%s", subtitle_id, asr_latency_ms, audio_seconds, preview_text(sentence))
            await self._send_json(
                {
                    "type": "final_pending",
                    "id": subtitle_id,
                    "source_lang": config.source_lang,
                    "target_lang": config.target_lang,
                    "mode": config.mode,
                    "dutch": sentence,
                    "translation": "Translating...",
                    "asr_latency_ms": asr_latency_ms,
                    "latency_ms": asr_latency_ms,
                    "audio_seconds": audio_seconds,
                    "asr_fragment": fragment,
                    "sentence_mode": True,
                    "quality": quality,
                }
            )

        if subtitle_items:
            task = asyncio.create_task(self._translate_and_send(subtitle_items, config, asr_latency_ms, audio_seconds, fragment), name=f"{self.client_id}-translate")
            self.translation_tasks.add(task)
            task.add_done_callback(self.translation_tasks.discard)
            self.stats.translations_started += 1
            self.metrics.translations_started = self.stats.translations_started
            self.metrics.touch()

    async def _translate_and_send(
        self,
        subtitle_items: list[dict[str, Any]],
        config: ClientConfig,
        asr_latency_ms: int,
        audio_seconds: float,
        fragment: str,
    ) -> None:
        translation_start = time.perf_counter()
        sentences = [item["sentence"] for item in subtitle_items]
        try:
            translations = await asyncio.to_thread(translate_many_sentences, sentences)
        except Exception as exc:
            logger.exception("translation_failure client_id=%s count=%s", self.client_id, len(sentences))
            safe_error = map_exception(exc)
            await self._safe_send_json(safe_error.payload(debug_enabled=_env_bool("DEBUG_ERRORS", False)))
            translations = [safe_error.message for _ in sentences]
        translation_latency_ms = int((time.perf_counter() - translation_start) * 1000)
        self.metrics.mt_latency_ms.append(translation_latency_ms)

        for item, translation in zip(subtitle_items, translations, strict=False):
            total_latency_ms = asr_latency_ms + translation_latency_ms
            self.metrics.total_latency_ms.append(total_latency_ms)
            self.metrics.touch()
            logger.info(
                "subtitle_final id=%s translation_latency_ms=%s total_latency_ms=%s translation=%s",
                item["id"],
                translation_latency_ms,
                total_latency_ms,
                preview_text(translation),
            )
            payload = {
                "type": "final",
                "id": item["id"],
                "source_lang": config.source_lang,
                "target_lang": config.target_lang,
                "mode": config.mode,
                "dutch": item["sentence"],
                "translation": translation,
                "asr_latency_ms": asr_latency_ms,
                "translation_latency_ms": translation_latency_ms,
                "latency_ms": total_latency_ms,
                "audio_seconds": audio_seconds,
                "asr_fragment": fragment,
                "sentence_mode": True,
                "quality": item.get("quality") or ({"level": "good"} if translation else {"level": "watch", "reasons": ["translation_unavailable"]}),
            }
            await asyncio.to_thread(session_history_store.save_subtitle, self.client_id, payload)
            await self._send_json(payload)

    async def _send_json(self, payload: dict[str, Any]) -> None:
        if self.closed:
            return
        async with self.send_lock:
            await self.websocket.send_json(payload)

    async def _safe_send_json(self, payload: dict[str, Any]) -> None:
        try:
            await self._send_json(payload)
        except Exception:
            pass

    async def _cancel_translations(self) -> None:
        for task in list(self.translation_tasks):
            if not task.done():
                task.cancel()
                self.stats.translations_cancelled += 1
                self.metrics.translations_cancelled = self.stats.translations_cancelled
        await asyncio.gather(*self.translation_tasks, return_exceptions=True)
        self.translation_tasks.clear()

    def _drain_queue(self) -> None:
        while True:
            try:
                self.queue.get_nowait()
                self.queue.task_done()
            except asyncio.QueueEmpty:
                return

    async def _cleanup(self) -> None:
        self.closed = True
        await self._cancel_translations()
        if self.processor_task and not self.processor_task.done():
            self.processor_task.cancel()
            await _cancel_task(self.processor_task)
        self._drain_queue()
        self.sentence_assembler.flush()
        self.metrics.closed_at = time.time()
        self.metrics.touch()
        await asyncio.to_thread(session_history_store.save_session, self.metrics)
        logger.info("session_metrics client_id=%s metrics=%s", self.client_id, self.metrics.snapshot())


async def run_subtitle_session(websocket: WebSocket) -> None:
    await SubtitleWebSocketSession(websocket).run()


def _is_flush(raw: str) -> bool:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return False
    return payload.get("type") == "flush"


class ConfigIgnored(Exception):
    pass


def _parse_config(raw: str) -> ClientConfig:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise ConfigIgnored

    if payload.get("type") != "config":
        raise ConfigIgnored

    return ClientConfigMessage.model_validate(payload).to_client_config()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


async def _cancel_task(task: asyncio.Task[Any]) -> None:
    try:
        await task
    except (asyncio.CancelledError, Exception):
        return
