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
from .schemas import ClientConfig, ClientConfigMessage
from .security import origin_allowed
from .sentences import SentenceAssembler

logger = get_logger("ws")


@dataclass
class SegmentJob:
    kind: Literal["final", "flush", "partial"]
    audio: np.ndarray | None
    config: ClientConfig
    created_at: float
    force: bool = True
    generation: int = 0


@dataclass(frozen=True)
class TranslationJob:
    subtitle_items: list[dict[str, Any]]
    config: ClientConfig
    asr_latency_ms: int
    queue_delay_ms: int
    audio_seconds: float
    fragment: str


@dataclass
class SessionStats:
    audio_chunks: int = 0
    finalized_segments: int = 0
    partial_segments: int = 0
    partial_inferences: int = 0
    partial_suppressed: int = 0
    dropped_segments: int = 0
    merged_segments: int = 0
    translations_started: int = 0
    translations_cancelled: int = 0
    max_queue_depth: int = 0
    max_translation_queue_depth: int = 0


class SubtitleWebSocketSession:
    def __init__(self, websocket: WebSocket) -> None:
        self.websocket = websocket
        self.client_id = f"ws-{time.time_ns()}"
        self.config = ClientConfig()
        self.segmenter = SpeechSegmenter(sample_rate=self.config.sample_rate)
        self.segmenter.set_mode(self.config.mode)
        self.sentence_assembler = SentenceAssembler()
        self.queue: asyncio.Queue[SegmentJob] = asyncio.Queue(maxsize=max(1, int(os.getenv("PIPELINE_QUEUE_MAX_SEGMENTS", "3"))))
        self.translation_queue: asyncio.Queue[TranslationJob] = asyncio.Queue(
            maxsize=max(1, int(os.getenv("TRANSLATION_QUEUE_MAX_ITEMS", "4")))
        )
        self.history_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=64)
        self.send_lock = asyncio.Lock()
        self.processor_task: asyncio.Task[None] | None = None
        self.translation_task: asyncio.Task[None] | None = None
        self.history_task: asyncio.Task[None] | None = None
        self.stats = SessionStats()
        self.metrics: SessionMetrics = session_metrics_store.create(self.client_id)
        self.closed = False
        self.flush_requested = False
        self.partial_enabled = _env_bool("PARTIAL_ASR_ENABLED", True)
        self.partial_interval_ms = int(os.getenv("PARTIAL_ASR_INTERVAL_MS", "900"))
        self.partial_interval_max_ms = max(
            self.partial_interval_ms,
            int(os.getenv("PARTIAL_ASR_MAX_INTERVAL_MS", "2400")),
        )
        self.partial_max_seconds = float(os.getenv("PARTIAL_ASR_MAX_SECONDS", "1.8"))
        self._last_partial_at = 0.0
        self._processing_kind: str | None = None
        self._translation_in_progress = False
        self._final_generation = 0
        self._last_realtime_factor = 0.0
        self._backpressure_until = 0.0
        self._partial_suppression_reasons: dict[str, int] = {}

    async def run(self) -> None:
        origin = self.websocket.headers.get("origin")
        if not origin_allowed(origin):
            logger.warning("websocket_origin_rejected client_id=%s origin=%s", self.client_id, origin)
            await self.websocket.close(code=1008)
            return
        await self.websocket.accept()
        logger.info("websocket_connected client_id=%s", self.client_id)
        if session_history_store.enabled:
            await asyncio.to_thread(session_history_store.save_session, self.metrics)
        self.processor_task = asyncio.create_task(self._process_jobs(), name=f"{self.client_id}-processor")
        self.translation_task = asyncio.create_task(self._process_translations(), name=f"{self.client_id}-translator")
        if session_history_store.enabled:
            self.history_task = asyncio.create_task(self._process_history(), name=f"{self.client_id}-history")
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
            if self.flush_requested:
                continue

            audio = pcm16le_to_float32(message["bytes"])
            await self._handle_audio(audio)

    async def _handle_audio(self, audio: np.ndarray) -> None:
        self.stats.audio_chunks += 1
        self.metrics.audio_chunks = self.stats.audio_chunks
        self.metrics.touch()
        finalized = self.segmenter.add(audio)
        if finalized is None:
            await self._maybe_enqueue_partial()
            return

        await self._enqueue_final(
            finalized,
            force=self.segmenter.last_finalize_reason == "silence",
        )

    async def _handle_text(self, raw: str) -> None:
        if _is_flush(raw):
            self.flush_requested = True
            finalized = self.segmenter.flush()
            if finalized is not None:
                await self._enqueue_final(finalized, force=True)
            else:
                self._next_final_generation()
                self._discard_pending_partials("flush")
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
        if not self.partial_enabled:
            return
        now = time.perf_counter()
        reason = self._partial_suppression_reason(now)
        if reason:
            self._record_partial_suppression(reason)
            return
        interval_ms = self._adaptive_partial_interval_ms()
        if (now - self._last_partial_at) * 1000 < interval_ms:
            return
        snapshot = self.segmenter.current_snapshot(max_seconds=self.partial_max_seconds)
        if snapshot is None:
            return
        self._last_partial_at = now
        self.stats.partial_segments += 1
        self.metrics.partial_segments = self.stats.partial_segments
        await self._enqueue(
            SegmentJob("partial", snapshot, self.config, now, force=False, generation=self._final_generation),
            merge_when_full=False,
        )

    def _partial_suppression_reason(self, now: float) -> str | None:
        if self.flush_requested:
            return "flush_requested"
        if any(job.kind == "final" for job in self.queue._queue):
            return "final_queued"
        if self._processing_kind is not None:
            return "asr_busy"
        if self.queue.qsize() > 0:
            return "asr_queue_nonzero"
        if self.queue.full() or self.translation_queue.full() or now < self._backpressure_until:
            return "backpressure"
        if self._last_realtime_factor >= 0.80:
            return "realtime_factor"
        if self.segmenter.likely_close_to_final():
            return "close_to_final"
        return None

    def _adaptive_partial_interval_ms(self) -> int:
        realtime_factor = max(0.0, self._last_realtime_factor)
        load_multiplier = 1.0 + min(1.5, realtime_factor * 1.5)
        return min(self.partial_interval_max_ms, max(self.partial_interval_ms, round(self.partial_interval_ms * load_multiplier)))

    def _record_partial_suppression(self, reason: str) -> None:
        self.stats.partial_suppressed += 1
        self.metrics.partial_suppressed = self.stats.partial_suppressed
        self._partial_suppression_reasons[reason] = self._partial_suppression_reasons.get(reason, 0) + 1

    async def _enqueue_final(self, audio: np.ndarray, *, force: bool) -> None:
        generation = self._next_final_generation()
        self._discard_pending_partials("final")
        self.stats.finalized_segments += 1
        self.metrics.finalized_segments = self.stats.finalized_segments
        logger.debug(
            "audio_segment_finalized client_id=%s samples=%s seconds=%.2f queue_depth=%s",
            self.client_id,
            len(audio),
            float(len(audio)) / float(self.config.sample_rate),
            self.queue.qsize(),
        )
        await self._enqueue(
            SegmentJob(
                "final",
                audio,
                self.config,
                time.perf_counter(),
                force=force,
                generation=generation,
            )
        )

    def _discard_pending_partials(self, new_kind: str) -> None:
        pending = self._take_pending_jobs()
        for pending_job in pending:
            if pending_job.kind == "partial":
                self._record_dropped_segment(pending_job.kind, new_kind)
            else:
                self.queue.put_nowait(pending_job)

    async def _enqueue(self, job: SegmentJob, merge_when_full: bool = True) -> None:
        if self.queue.full():
            if job.kind == "partial":
                self._record_dropped_segment("partial", job.kind)
                return
            if job.kind == "flush":
                await self.queue.put(job)
                return
            pending = self._take_pending_jobs()
            partial_index = next((index for index, item in enumerate(pending) if item.kind == "partial"), None)
            if partial_index is not None:
                dropped = pending.pop(partial_index)
                self._record_dropped_segment(dropped.kind, job.kind)
            elif (
                merge_when_full
                and pending
                and pending[-1].kind == "final"
                and job.kind == "final"
                and pending[-1].audio is not None
                and job.audio is not None
                and len(pending[-1].audio) + len(job.audio)
                <= int(job.config.sample_rate * float(os.getenv("PIPELINE_MERGE_MAX_SECONDS", "12")))
            ):
                previous = pending.pop()
                job.audio = np.concatenate([previous.audio, job.audio]).astype(np.float32, copy=False)
                job.created_at = min(previous.created_at, job.created_at)
                self.stats.merged_segments += 1
                self.metrics.merged_segments = self.stats.merged_segments
                self._backpressure_until = time.perf_counter() + 2.0
            else:
                await self._restore_pending_jobs(pending)
                await self.queue.put(job)
                return
            for pending_job in pending:
                self.queue.put_nowait(pending_job)
        await self.queue.put(job)
        self.stats.max_queue_depth = max(self.stats.max_queue_depth, self.queue.qsize())
        self.metrics.max_queue_depth = self.stats.max_queue_depth
        self.metrics.touch()

    def _take_pending_jobs(self) -> list[SegmentJob]:
        pending: list[SegmentJob] = []
        while True:
            try:
                pending.append(self.queue.get_nowait())
                self.queue.task_done()
            except asyncio.QueueEmpty:
                return pending

    async def _restore_pending_jobs(self, pending: list[SegmentJob]) -> None:
        for pending_job in pending:
            await self.queue.put(pending_job)

    async def _process_jobs(self) -> None:
        while True:
            job = await self.queue.get()
            self._processing_kind = job.kind
            try:
                if job.kind == "partial":
                    await self._process_partial(job)
                elif job.kind == "flush":
                    await self._send_sentences(
                        self.sentence_assembler.flush(), job.config, {"asr_latency_ms": 0, "audio_seconds": 0.0, "fragment": ""}
                    )
                    await self.translation_queue.join()
                    if session_history_store.enabled:
                        await self.history_queue.join()
                    await self._safe_send_json({"type": "flushed"})
                elif job.audio is not None:
                    await self._process_final(job)
            finally:
                self._processing_kind = None
                self.queue.task_done()

    async def _process_partial(self, job: SegmentJob) -> None:
        if job.audio is None:
            return
        self.stats.partial_inferences += 1
        self.metrics.partial_inferences = self.stats.partial_inferences
        prompt = self.sentence_assembler.context_prompt()
        try:
            text, meta = await asyncio.to_thread(transcribe_partial, job.audio, job.config, prompt)
        except Exception as exc:
            logger.exception("partial_asr_failure client_id=%s", self.client_id)
            await self._safe_send_json(map_exception(exc).payload(debug_enabled=_env_bool("DEBUG_ERRORS", False)))
            return
        if not text or job.generation != self._final_generation:
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
        queue_delay_ms = max(0, int((time.perf_counter() - job.created_at) * 1000))
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
        self._last_realtime_factor = float(meta.get("realtime_factor") or 0.0)
        await self._maybe_degrade_mode(job.config, float(meta.get("realtime_factor") or 0.0))
        self.metrics.asr_latency_ms.append(int(meta.get("asr_latency_ms") or 0))
        audio_seconds = float(meta.get("audio_seconds") or 0.0)
        self.metrics.audio_seconds.append(audio_seconds)
        self.metrics.audio_seconds_total += audio_seconds
        self.metrics.realtime_factors.append(float(meta.get("realtime_factor") or 0.0))
        self.metrics.queue_delay_ms.append(queue_delay_ms)
        self.metrics.touch()
        meta["queue_delay_ms"] = queue_delay_ms
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
                logger.debug(
                    "sentence_buffering fragment=%s asr_latency_ms=%s", preview_text(fragment), int(meta.get("asr_latency_ms") or 0)
                )
            if fragment and os.getenv("SEND_DEBUG_FRAGMENTS", "0") == "1":
                await self._send_json(
                    {"type": "debug_fragment", "fragment": fragment, "asr_latency_ms": int(meta.get("asr_latency_ms") or 0)}
                )
            return

        subtitle_items: list[dict[str, Any]] = []
        asr_latency_ms = int(meta.get("asr_latency_ms") or 0)
        queue_delay_ms = int(meta.get("queue_delay_ms") or 0)
        audio_seconds = float(meta.get("audio_seconds") or 0.0)
        fragment = str(meta.get("fragment") or "")
        quality = meta.get("quality") or {}
        for sentence in sentences:
            if not sentence:
                continue
            subtitle_id = f"final-{time.time_ns()}"
            subtitle_items.append({"id": subtitle_id, "sentence": sentence, "quality": quality})
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
                    "queue_delay_ms": queue_delay_ms,
                    "latency_ms": queue_delay_ms + asr_latency_ms,
                    "audio_seconds": audio_seconds,
                    "asr_fragment": fragment,
                    "sentence_mode": True,
                    "quality": quality,
                }
            )
            logger.debug(
                "subtitle_pending id=%s asr_latency_ms=%s audio_seconds=%.2f dutch=%s",
                subtitle_id,
                asr_latency_ms,
                audio_seconds,
                preview_text(sentence),
            )

        if subtitle_items:
            await self.translation_queue.put(
                TranslationJob(subtitle_items, config, asr_latency_ms, queue_delay_ms, audio_seconds, fragment)
            )
            self.stats.translations_started += 1
            self.metrics.translations_started = self.stats.translations_started
            self.stats.max_translation_queue_depth = max(self.stats.max_translation_queue_depth, self.translation_queue.qsize())
            self.metrics.max_translation_queue_depth = self.stats.max_translation_queue_depth
            self.metrics.touch()

    async def _process_translations(self) -> None:
        while True:
            job = await self.translation_queue.get()
            self._translation_in_progress = True
            try:
                await self._translate_and_send(
                    job.subtitle_items,
                    job.config,
                    job.asr_latency_ms,
                    job.queue_delay_ms,
                    job.audio_seconds,
                    job.fragment,
                )
            finally:
                self._translation_in_progress = False
                self.translation_queue.task_done()

    async def _translate_and_send(
        self,
        subtitle_items: list[dict[str, Any]],
        config: ClientConfig,
        asr_latency_ms: int,
        queue_delay_ms: int,
        audio_seconds: float,
        fragment: str,
    ) -> None:
        translation_start = time.perf_counter()
        sentences = [item["sentence"] for item in subtitle_items]
        try:
            translations = await asyncio.to_thread(translate_many_sentences, sentences, config)
        except Exception as exc:
            logger.exception("translation_failure client_id=%s count=%s", self.client_id, len(sentences))
            safe_error = map_exception(exc)
            await self._safe_send_json(safe_error.payload(debug_enabled=_env_bool("DEBUG_ERRORS", False)))
            translations = [safe_error.message for _ in sentences]
        translation_latency_ms = int((time.perf_counter() - translation_start) * 1000)
        self.metrics.mt_latency_ms.append(translation_latency_ms)

        for item, translation in zip(subtitle_items, translations, strict=False):
            total_latency_ms = queue_delay_ms + asr_latency_ms + translation_latency_ms
            self.metrics.total_latency_ms.append(total_latency_ms)
            self.metrics.touch()
            payload = {
                "type": "final",
                "id": item["id"],
                "source_lang": config.source_lang,
                "target_lang": config.target_lang,
                "mode": config.mode,
                "dutch": item["sentence"],
                "translation": translation,
                "asr_latency_ms": asr_latency_ms,
                "queue_delay_ms": queue_delay_ms,
                "translation_latency_ms": translation_latency_ms,
                "latency_ms": total_latency_ms,
                "audio_seconds": audio_seconds,
                "asr_fragment": fragment,
                "sentence_mode": True,
                "quality": item.get("quality")
                or ({"level": "good"} if translation else {"level": "watch", "reasons": ["translation_unavailable"]}),
            }
            await self._send_json(payload)
            logger.debug(
                "subtitle_final id=%s translation_latency_ms=%s total_latency_ms=%s translation=%s",
                item["id"],
                translation_latency_ms,
                total_latency_ms,
                preview_text(translation),
            )
            await self._persist_subtitle(payload)

    async def _send_json(self, payload: dict[str, Any]) -> None:
        if self.closed:
            return
        async with self.send_lock:
            await self.websocket.send_json(payload)

    async def _safe_send_json(self, payload: dict[str, Any]) -> None:
        try:
            await self._send_json(payload)
        except Exception as exc:
            logger.debug("websocket_send_failed client_id=%s error=%s", self.client_id, exc)

    async def _persist_subtitle(self, payload: dict[str, Any]) -> None:
        if session_history_store.enabled:
            await self.history_queue.put(payload)

    async def _process_history(self) -> None:
        while True:
            payload = await self.history_queue.get()
            try:
                await asyncio.to_thread(session_history_store.save_subtitle, self.client_id, payload)
            finally:
                self.history_queue.task_done()

    def _next_final_generation(self) -> int:
        self._final_generation += 1
        return self._final_generation

    def _record_dropped_segment(self, dropped_kind: str, new_kind: str) -> None:
        self.stats.dropped_segments += 1
        self.metrics.dropped_segments = self.stats.dropped_segments
        self._backpressure_until = time.perf_counter() + 2.0
        logger.warning(
            "pipeline_queue_drop client_id=%s dropped_kind=%s new_kind=%s",
            self.client_id,
            dropped_kind,
            new_kind,
        )

    def _drain_queue(self) -> None:
        while True:
            try:
                self.queue.get_nowait()
                self.queue.task_done()
            except asyncio.QueueEmpty:
                return

    async def _cleanup(self) -> None:
        self.closed = True
        if self.processor_task and not self.processor_task.done():
            self.processor_task.cancel()
            await _cancel_task(self.processor_task)
        if self.translation_task and not self.translation_task.done():
            self.translation_task.cancel()
            self.stats.translations_cancelled += self.translation_queue.qsize() + int(self._translation_in_progress)
            self.metrics.translations_cancelled = self.stats.translations_cancelled
            await _cancel_task(self.translation_task)
        if self.history_task and not self.history_task.done():
            try:
                await asyncio.wait_for(self.history_queue.join(), timeout=2.0)
            except TimeoutError:
                logger.warning("session_history_drain_timeout client_id=%s pending=%s", self.client_id, self.history_queue.qsize())
            self.history_task.cancel()
            await _cancel_task(self.history_task)
        self._drain_queue()
        self._drain_translation_queue()
        self._drain_history_queue()
        self.sentence_assembler.flush()
        self.metrics.closed_at = time.time()
        self.metrics.touch()
        if session_history_store.enabled:
            await asyncio.to_thread(session_history_store.save_session, self.metrics)
        logger.info("session_metrics client_id=%s metrics=%s", self.client_id, self.metrics.snapshot())

    def _drain_translation_queue(self) -> None:
        while True:
            try:
                self.translation_queue.get_nowait()
                self.translation_queue.task_done()
            except asyncio.QueueEmpty:
                return

    def _drain_history_queue(self) -> None:
        while True:
            try:
                self.history_queue.get_nowait()
                self.history_queue.task_done()
            except asyncio.QueueEmpty:
                return


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
    except json.JSONDecodeError as exc:
        raise ConfigIgnored from exc

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
