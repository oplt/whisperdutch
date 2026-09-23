from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from benchmark_representative import DUTCH_REFERENCE_LINES, ensure_speech_fixture, write_pcm16_wav  # noqa: E402
from inventory_runtime import CONFIG_KEYS, parse_bash_exports, parse_env_file  # noqa: E402


def test_parse_env_file_ignores_comments_and_exports(tmp_path: Path) -> None:
    path = tmp_path / "sample.env"
    path.write_text(
        "# comment\nexport ASR_MODEL=small\nASR_DEVICE='cpu'\n\nINVALID\nTRANSLATION_ENGINE=auto\n",
        encoding="utf-8",
    )
    parsed = parse_env_file(path)
    assert parsed["ASR_MODEL"] == "small"
    assert parsed["ASR_DEVICE"] == "cpu"
    assert parsed["TRANSLATION_ENGINE"] == "auto"
    assert "INVALID" not in parsed


def test_parse_bash_exports_reads_run_gpu_style_defaults(tmp_path: Path) -> None:
    path = tmp_path / "run.sh"
    path.write_text(
        'export ASR_MODEL="${ASR_MODEL:-small}"\n'
        'export TRANSLATION_MODEL="${TRANSLATION_MODEL:-models/m2m100-418m-ct2}"\n',
        encoding="utf-8",
    )
    parsed = parse_bash_exports(path, ("ASR_MODEL", "TRANSLATION_MODEL", "ASR_DEVICE"))
    assert parsed["ASR_MODEL"] == "small"
    assert parsed["TRANSLATION_MODEL"] == "models/m2m100-418m-ct2"
    assert "ASR_DEVICE" not in parsed


def test_env_example_documents_core_config_keys() -> None:
    example = Path(__file__).resolve().parents[1] / ".env.example"
    parsed = parse_env_file(example)
    required = {
        "ASR_MODEL",
        "ASR_DEVICE",
        "TRANSLATION_MODEL_FAMILY",
        "TRANSLATION_MODEL",
        "TRANSLATION_TOKENIZER",
        "LOCAL_MODELS_ONLY",
        "INFERENCE_ASR_MAX_CONCURRENT",
    }
    assert required.issubset(parsed.keys())
    assert set(CONFIG_KEYS).issuperset(required)


def test_check_script_requires_ruff_and_mypy() -> None:
    check_sh = Path(__file__).resolve().parents[2] / "scripts" / "check.sh"
    text = check_sh.read_text(encoding="utf-8")
    assert "require_module ruff" in text
    assert "require_module mypy" in text
    assert 'if "${PYTHON}" -m ruff --version >/dev/null 2>&1; then' not in text


def test_representative_fixture_generation_metadata_roundtrip(tmp_path: Path) -> None:
    wav = tmp_path / "supplied.wav"
    samples = np.zeros(16000, dtype=np.float32)
    write_pcm16_wav(wav, samples, 16000)
    path, meta = ensure_speech_fixture(wav)
    assert path == wav
    assert meta["generator"] == "user-supplied"
    assert meta["duration_seconds"] == 1.0
    assert len(DUTCH_REFERENCE_LINES) >= 2
    assert json.loads(json.dumps(meta))["sample_rate"] == 16000
