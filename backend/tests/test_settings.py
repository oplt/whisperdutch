from __future__ import annotations

import os

import pytest
from app.settings import AppSettings, get_settings, reset_settings_cache
from pydantic import ValidationError


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    reset_settings_cache()
    yield
    reset_settings_cache()


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.upper().startswith(("ASR_", "TRANSLATION_", "INFERENCE_", "STARTUP_", "BACKEND_", "LOG_", "LOCAL_", "DUTCH_")):
            monkeypatch.delenv(key, raising=False)
    settings = AppSettings()
    assert settings.asr_model == "large-v3-turbo"
    assert settings.asr_device == "auto"
    assert settings.startup_warmup_strategy == "sequential"
    assert settings.local_models_only is True


def test_settings_empty_env_coerces(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASR_DEVICE", "")
    monkeypatch.setenv("ASR_CPU_THREADS", "")
    monkeypatch.setenv("STARTUP_WARMUP_STRATEGY", "parallel")
    settings = AppSettings()
    assert settings.asr_device == "auto"
    assert settings.asr_cpu_threads is None
    assert settings.startup_warmup_strategy == "parallel"


def test_settings_rejects_invalid_device(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASR_DEVICE", "tpu")
    with pytest.raises(ValidationError):
        AppSettings()


def test_get_settings_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASR_MODEL", "small")
    first = get_settings()
    monkeypatch.setenv("ASR_MODEL", "medium")
    second = get_settings()
    assert first is second
    assert second.asr_model == "small"
    reset_settings_cache()
    third = get_settings()
    assert third.asr_model == "medium"
