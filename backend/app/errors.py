from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class UserSafeError:
    code: str
    message: str
    debug: str | None = None

    def payload(self, debug_enabled: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": "error",
            "code": self.code,
            "message": self.message,
        }
        if debug_enabled and self.debug:
            payload["debug"] = self.debug
        return payload


def map_exception(exc: BaseException) -> UserSafeError:
    text = str(exc)
    lower = text.lower()
    name = exc.__class__.__name__

    if isinstance(exc, FileNotFoundError) or "model not found" in lower or "no such file" in lower:
        return UserSafeError(
            "model_missing",
            "Required local model is missing. Prepare the ASR and translation models, then restart backend.",
            _sanitize_debug(text),
        )

    if "cuda" in lower or "cudnn" in lower or "cublas" in lower:
        return UserSafeError(
            "cuda_unavailable",
            "GPU runtime failed. Switch to CPU mode or fix CUDA, then restart backend.",
            _sanitize_debug(text),
        )

    if "ctranslate2" in lower or "translation" in lower:
        return UserSafeError(
            "translation_runtime_error",
            "Translation failed. Check model setup and backend logs.",
            _sanitize_debug(text),
        )

    if "whisper" in lower or "asr" in lower or "transcrib" in lower:
        return UserSafeError(
            "asr_runtime_error",
            "Speech recognition failed. Check ASR model setup and backend logs.",
            _sanitize_debug(text),
        )

    return UserSafeError(
        "runtime_error",
        "Backend processing failed. Check backend logs for details.",
        _sanitize_debug(f"{name}: {text}"),
    )


def _sanitize_debug(text: str) -> str:
    parts = []
    for token in text.split():
        if "/" in token or "\\" in token:
            parts.append(Path(token).name or "<path>")
        else:
            parts.append(token)
    return " ".join(parts)
