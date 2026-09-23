from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_eval_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "evaluate_models.py"
    spec = importlib.util.spec_from_file_location("evaluate_models", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_wer_cer_and_chrf_basics() -> None:
    m = _load_eval_module()
    assert m.wer("een korte test", "een korte test") == 0.0
    assert m.wer("een korte test", "een lange test") == 1 / 3
    assert m.cer("abc", "abc") == 0.0
    assert m.chrf("hello world", "hello world") == 1.0
    assert m.chrf("hello world", "") == 0.0
