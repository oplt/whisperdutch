"""Typed process configuration loaded once at startup.

Existing modules may still read os.environ for hot-path overrides; this module
is the validated source of truth for launch-time knobs and /debug/config.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _empty_to_none(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return value


def _empty_to_default(value: Any, default: str) -> str:
    if value is None:
        return default
    if isinstance(value, str) and not value.strip():
        return default
    return str(value)


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    asr_model: str = "large-v3-turbo"
    asr_device: Literal["auto", "cpu", "cuda"] = "auto"
    asr_compute_type: str = ""
    asr_language: str = "nl"
    asr_cpu_threads: int | None = None
    asr_num_workers: int = Field(default=1, ge=1)
    local_models_only: bool = True

    translation_engine: Literal["auto", "ctranslate2", "transformers"] = "auto"
    translation_model_family: Literal["auto", "nllb", "m2m100", "marian"] = "nllb"
    translation_model: str = "models/nllb-200-distilled-600m-ct2"
    translation_tokenizer: str = "facebook/nllb-200-distilled-600M"
    translation_device: Literal["auto", "cpu", "cuda"] = "auto"
    translation_inter_threads: int = Field(default=1, ge=1)
    translation_intra_threads: int | None = None

    inference_asr_max_concurrent: int = Field(default=1, ge=1)
    inference_translation_max_concurrent: int = Field(default=1, ge=1)
    inference_asr_max_pending: int = Field(default=16, ge=1)
    inference_translation_max_pending: int = Field(default=32, ge=1)
    translation_batch_collect_ms: float = Field(default=0.0, ge=0.0)
    translation_batch_max_requests: int = Field(default=8, ge=1)
    translation_batch_max_chars: int = Field(default=2400, ge=1)

    startup_warmup_strategy: Literal["sequential", "parallel"] = "sequential"
    backend_host: str = "127.0.0.1"
    backend_port: int = Field(default=8000, ge=1024, le=65535)
    log_level: str = "INFO"
    log_transcript_text: bool = False

    dutch_subtitle_firefox_origins: str = ""

    @field_validator("asr_cpu_threads", "translation_intra_threads", mode="before")
    @classmethod
    def optional_int_empty(cls, value: Any) -> Any:
        return _empty_to_none(value)

    @field_validator("translation_inter_threads", mode="before")
    @classmethod
    def translation_inter_threads_default(cls, value: Any) -> Any:
        return 1 if isinstance(value, str) and not value.strip() else value

    @field_validator("asr_device", "translation_device", mode="before")
    @classmethod
    def device_default(cls, value: Any) -> str:
        return _empty_to_default(value, "auto").strip().lower()

    @field_validator("translation_engine", mode="before")
    @classmethod
    def translation_engine_default(cls, value: Any) -> str:
        return _empty_to_default(value, "auto").strip().lower()

    @field_validator("translation_model_family", mode="before")
    @classmethod
    def translation_family_default(cls, value: Any) -> str:
        return _empty_to_default(value, "nllb").strip().lower()

    @field_validator("startup_warmup_strategy", mode="before")
    @classmethod
    def warmup_strategy_default(cls, value: Any) -> str:
        raw = _empty_to_default(value, "sequential").strip().lower()
        if raw in {"1", "true", "yes", "on", "parallel"}:
            return "parallel"
        return "sequential"

    @field_validator("asr_language")
    @classmethod
    def normalize_language(cls, value: str) -> str:
        return str(value or "nl").strip().lower() or "nl"

    def public_dict(self) -> dict[str, object]:
        return self.model_dump()


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    return AppSettings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
