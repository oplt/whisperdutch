from __future__ import annotations

import pytest
from app.inference_runtime import get_inference_runtime


@pytest.fixture(autouse=True)
def inline_inference_runtime() -> None:
    runtime = get_inference_runtime()
    runtime.set_inline(True)


@pytest.fixture
def reset_inference_runtime() -> None:
    get_inference_runtime.cache_clear()
    yield
    get_inference_runtime.cache_clear()
