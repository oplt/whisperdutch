from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, Response, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from .asr import get_asr_engine
from .errors import map_exception
from .history import session_history_store
from .logger import current_log_file, get_logger, kv, setup_logging, should_log_text, tail_log
from .metrics import session_metrics_store
from .schemas import ClientLog, GlossaryUpdate, PrivacyUpdate
from .security import allowed_origins
from .startup_status import read_startup_status, write_startup_status
from .text_processor import list_glossary_rules, save_glossary_rules
from .translator import get_translation_engine
from .ws_session import run_subtitle_session

logger = get_logger("api")


@dataclass
class RuntimeState:
    startup_started_at: float = field(default_factory=time.time)
    ready: bool = False
    model_ready: bool = False
    last_error: dict[str, Any] | None = None
    warmed_up_at: float | None = None


runtime_state = RuntimeState()


def _translation_cache_metrics() -> dict[str, Any]:
    try:
        return get_translation_engine().cache_info()
    except Exception as exc:
        logger.exception("translation_cache_metrics_failed")
        return {"error": f"{type(exc).__name__}: {exc}"}


def warmup_models() -> None:
    start = time.perf_counter()
    logger.info("startup_warmup_started")
    write_startup_status("warming_models", False)
    try:
        write_startup_status("loading_asr", False)
        asr = get_asr_engine()
        write_startup_status("loading_translation", False)
        translator = get_translation_engine()
        write_startup_status("warming_asr", False)
        asr.warmup()
        write_startup_status("warming_translation", False)
        translator.warmup()
    except Exception as exc:
        safe_error = map_exception(exc).payload(debug_enabled=_env_bool("DEBUG_ERRORS", False))
        runtime_state.ready = False
        runtime_state.model_ready = False
        runtime_state.last_error = safe_error
        write_startup_status("failed", False, error=safe_error)
        logger.exception("startup_warmup_failed")
        return
    elapsed = int((time.perf_counter() - start) * 1000)
    runtime_state.ready = True
    runtime_state.model_ready = True
    runtime_state.last_error = None
    runtime_state.warmed_up_at = time.time()
    write_startup_status("ready", True, extra={"warmed_up_at": runtime_state.warmed_up_at, "elapsed_ms": elapsed})
    logger.info("startup_warmup_completed elapsed_ms=%s", elapsed)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    logger.info("application_startup")
    await asyncio.to_thread(warmup_models)
    yield
    logger.info("application_shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Dutch Live Subtitle Translator",
        version="0.8.0-low-latency",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_routes(app)
    return app


def register_routes(app: FastAPI) -> None:
    @app.get("/health")
    def health() -> dict[str, Any]:
        return health_live()

    @app.get("/health/live")
    def health_live() -> dict[str, Any]:
        return {
            "ok": True,
            "live": True,
            "service": "dutch-live-subtitle-translator",
            "version": "0.8.0-low-latency",
            "websocket": "/ws/subtitles",
        }

    @app.get("/health/ready")
    def health_ready(response: Response) -> dict[str, Any]:
        ready = runtime_state.ready and runtime_state.model_ready and runtime_state.last_error is None
        if not ready:
            response.status_code = 503
        return {
            "ok": ready,
            "ready": ready,
            "model_ready": runtime_state.model_ready,
            "last_error": runtime_state.last_error,
            "warmed_up_at": runtime_state.warmed_up_at,
        }

    @app.get("/debug/device")
    def debug_device() -> dict[str, Any]:
        asr_info: dict[str, Any] | None = None
        translation_info: dict[str, Any] | None = None
        try:
            asr_info = get_asr_engine().info()
        except Exception as exc:
            runtime_state.last_error = map_exception(exc).payload(debug_enabled=True)
            logger.exception("debug_device_asr_failed")
        try:
            translation_info = get_translation_engine().info()
        except Exception as exc:
            runtime_state.last_error = map_exception(exc).payload(debug_enabled=True)
            logger.exception("debug_device_translation_failed")
        return {
            "readiness": {
                "ready": runtime_state.ready,
                "model_ready": runtime_state.model_ready,
                "last_error": runtime_state.last_error,
                "warmed_up_at": runtime_state.warmed_up_at,
                "startup_status": read_startup_status(),
            },
            "asr": asr_info,
            "translation": translation_info,
            "pipeline": {
                "partial_asr": os.getenv("PARTIAL_ASR_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"},
                "partial_asr_interval_ms": int(os.getenv("PARTIAL_ASR_INTERVAL_MS", "900")),
                "final_translation_is_blocking": False,
                "bounded_queue": True,
                "queue_max_segments": int(os.getenv("PIPELINE_QUEUE_MAX_SEGMENTS", "3")),
                "merge_max_seconds": float(os.getenv("PIPELINE_MERGE_MAX_SECONDS", "12")),
                "translation_queue_max_items": int(os.getenv("TRANSLATION_QUEUE_MAX_ITEMS", "4")),
                "final_segments_preserved_under_backpressure": True,
                "adaptive_segmentation": True,
                "translation_batching": True,
                "translation_cache": "lru",
                "translation_cache_backend": (translation_info or {}).get("translation_cache", {}).get("backend"),
                "sentence_mode": True,
                "glossary_enabled": os.getenv("GLOSSARY_ENABLED", "0") == "1",
                "asr_initial_prompt_default": "empty",
                "log_transcript_text": should_log_text(),
                "session_history": {
                    "enabled": session_history_store.enabled,
                    "db_path": str(session_history_store.db_path),
                },
            },
        }

    @app.post("/api/cache/translation/clear")
    def translation_cache_clear(reason: str = "manual") -> dict[str, Any]:
        result = get_translation_engine().clear_cache(reason)
        return {
            "ok": True,
            "cleared": result["cleared"],
            "durable_cleared": result["durable_cleared"],
            "reason": result["reason"],
        }

    @app.get("/api/logs/recent")
    def recent_logs(lines: int = 200) -> dict[str, Any]:
        return {"ok": True, "log_file": str(current_log_file()), "lines": tail_log(lines)}

    @app.get("/debug/sessions")
    async def debug_sessions() -> dict[str, Any]:
        return {"ok": True, "sessions": session_metrics_store.recent()}

    @app.get("/metrics")
    async def metrics() -> dict[str, Any]:
        ready = runtime_state.ready and runtime_state.model_ready and runtime_state.last_error is None
        return {
            "ok": True,
            "ready": ready,
            "sessions": session_metrics_store.recent(),
            "translation_cache": _translation_cache_metrics(),
        }

    @app.get("/debug/session/{client_id}")
    async def debug_session(client_id: str, response: Response) -> dict[str, Any]:
        metrics = session_metrics_store.get(client_id)
        if metrics is None:
            response.status_code = 404
            return {"ok": False, "message": "Session not found"}
        return {"ok": True, "session": metrics}

    @app.get("/api/history")
    def history_recent(limit: int = 50) -> dict[str, Any]:
        return {"ok": True, "enabled": session_history_store.enabled, "sessions": session_history_store.recent_sessions(limit)}

    @app.get("/api/history/{client_id}")
    def history_get(client_id: str, response: Response) -> dict[str, Any]:
        session = session_history_store.get_session(client_id)
        if session is None:
            response.status_code = 404
            return {"ok": False, "message": "Session not found"}
        return {"ok": True, "session": session}

    @app.delete("/api/history/{client_id}")
    def history_delete(client_id: str, response: Response) -> dict[str, Any]:
        deleted = session_history_store.delete_session(client_id)
        if not deleted:
            response.status_code = 404
            return {"ok": False, "message": "Session not found"}
        return {"ok": True}

    @app.post("/api/logs/client")
    def client_log(payload: ClientLog) -> dict[str, Any]:
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

    @app.get("/api/glossary")
    def glossary_get() -> dict[str, Any]:
        return {"ok": True, "rules": list_glossary_rules()}

    @app.put("/api/glossary")
    def glossary_put(payload: GlossaryUpdate, response: Response) -> dict[str, Any]:
        try:
            save_glossary_rules([(rule.pattern, rule.replacement) for rule in payload.rules])
        except Exception as exc:
            response.status_code = 400
            safe = map_exception(exc)
            return {"ok": False, "code": "invalid_glossary", "message": "Glossary rule is invalid.", "debug": safe.debug}

        try:
            cache_result = get_translation_engine().refresh_glossary_version()
        except Exception as exc:
            response.status_code = 503
            logger.exception("glossary_cache_invalidation_failed")
            safe = map_exception(exc)
            return {
                "ok": False,
                "code": "cache_invalidation_failed",
                "message": "Glossary saved, but translation cache invalidation failed.",
                "debug": safe.debug,
            }
        return {"ok": True, "rules": list_glossary_rules(), "cache": {"cleared": cache_result["cleared"]}}

    @app.get("/api/privacy")
    def privacy_get() -> dict[str, Any]:
        return {"ok": True, "log_transcript_text": should_log_text()}

    @app.put("/api/privacy")
    def privacy_put(payload: PrivacyUpdate) -> dict[str, Any]:
        os.environ["LOG_TRANSCRIPT_TEXT"] = "1" if payload.log_transcript_text else "0"
        return {"ok": True, "log_transcript_text": should_log_text()}

    @app.websocket("/ws/subtitles")
    async def subtitles_ws(websocket: WebSocket) -> None:
        await run_subtitle_session(websocket)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


app = create_app()
