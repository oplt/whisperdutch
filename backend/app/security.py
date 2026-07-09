from __future__ import annotations

import os


def allowed_origins() -> list[str]:
    raw = os.getenv("CORS_ALLOWED_ORIGINS", "").strip()
    if raw:
        return [origin.strip() for origin in raw.split(",") if origin.strip()]

    origins = [
        "http://127.0.0.1:8000",
        "http://localhost:8000",
    ]
    extension_id = os.getenv("EXTENSION_ID", os.getenv("DUTCH_SUBTITLE_EXTENSION_ID", "")).strip()
    if extension_id:
        origins.append(f"chrome-extension://{extension_id}")
    return origins


def origin_allowed(origin: str | None) -> bool:
    if not origin:
        return True
    return origin in set(allowed_origins())
