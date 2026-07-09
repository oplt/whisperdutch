#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ -d ".venv" ]; then
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
fi

if [ -f ".env" ]; then
  set -a
  # shellcheck disable=SC1091
  source ".env"
  set +a
fi

PYTHON_BIN="${PYTHON_BIN:-}"
if [ -z "$PYTHON_BIN" ]; then
  if command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
  else
    echo "Neither python nor python3 was found in PATH." >&2
    exit 127
  fi
fi

mkdir -p logs

# Daily logging. App logs are written to backend/logs/backend-YYYY-MM-DD.log.
export BACKEND_LOG_DIR="${BACKEND_LOG_DIR:-logs}"
export BACKEND_LOG_PREFIX="${BACKEND_LOG_PREFIX:-backend}"
export LOG_LEVEL="${LOG_LEVEL:-INFO}"
# Keep subtitle text out of logs by default. Set LOG_TRANSCRIPT_TEXT=1 only while debugging.
export LOG_TRANSCRIPT_TEXT="${LOG_TRANSCRIPT_TEXT:-0}"

# Stable live-latency defaults for RTX 3060 12 GB.
# For higher precision, run: ASR_MODEL=medium ./run_gpu.sh
export ASR_DEVICE="${ASR_DEVICE:-cuda}"
export ASR_MODEL="${ASR_MODEL:-small}"
export ASR_COMPUTE_TYPE="${ASR_COMPUTE_TYPE:-float16}"
export ASR_LANGUAGE="${ASR_LANGUAGE:-nl}"
export ASR_BEAM_SIZE="${ASR_BEAM_SIZE:-2}"
export FAST_ASR_BEAM_SIZE="${FAST_ASR_BEAM_SIZE:-1}"
export BALANCED_ASR_BEAM_SIZE="${BALANCED_ASR_BEAM_SIZE:-2}"
export QUALITY_ASR_BEAM_SIZE="${QUALITY_ASR_BEAM_SIZE:-3}"
export ASR_CONDITION_ON_PREVIOUS_TEXT="${ASR_CONDITION_ON_PREVIOUS_TEXT:-0}"
export ASR_INITIAL_PROMPT="${ASR_INITIAL_PROMPT:-}"

# Stable sentence construction with optional partial Dutch preview.
export SENTENCE_MODE="${SENTENCE_MODE:-1}"
export PARTIAL_ASR_ENABLED="${PARTIAL_ASR_ENABLED:-1}"
export PARTIAL_ASR_INTERVAL_MS="${PARTIAL_ASR_INTERVAL_MS:-900}"
export PARTIAL_ASR_MAX_SECONDS="${PARTIAL_ASR_MAX_SECONDS:-1.8}"
export PIPELINE_QUEUE_MAX_SEGMENTS="${PIPELINE_QUEUE_MAX_SEGMENTS:-3}"
export DROP_FILLERS="${DROP_FILLERS:-1}"
export GLOSSARY_ENABLED="${GLOSSARY_ENABLED:-0}"
export GLOSSARY_PATH="${GLOSSARY_PATH:-config/glossary.tsv}"
export MIN_SPEECH_SECONDS="${MIN_SPEECH_SECONDS:-0.35}"
export MIN_FINAL_WORDS="${MIN_FINAL_WORDS:-4}"
export SENTENCE_MAX_BUFFER_WORDS="${SENTENCE_MAX_BUFFER_WORDS:-28}"
export SENTENCE_MAX_BUFFER_CHARS="${SENTENCE_MAX_BUFFER_CHARS:-220}"
export FAST_MAX_SEGMENT_SECONDS="${FAST_MAX_SEGMENT_SECONDS:-2.8}"
export BALANCED_MAX_SEGMENT_SECONDS="${BALANCED_MAX_SEGMENT_SECONDS:-5.5}"
export QUALITY_MAX_SEGMENT_SECONDS="${QUALITY_MAX_SEGMENT_SECONDS:-6.5}"
export FAST_END_SILENCE_SECONDS="${FAST_END_SILENCE_SECONDS:-0.35}"
export BALANCED_END_SILENCE_SECONDS="${BALANCED_END_SILENCE_SECONDS:-0.65}"
export QUALITY_END_SILENCE_SECONDS="${QUALITY_END_SILENCE_SECONDS:-0.80}"
export PRE_ROLL_SECONDS="${PRE_ROLL_SECONDS:-0.15}"

# Translation. Recommended: run ./scripts/prepare_translation_ct2.sh once.
# CPU int8 avoids GPU contention with Whisper and keeps translation reliable.
export TRANSLATION_ENGINE="${TRANSLATION_ENGINE:-auto}"
export TRANSLATION_MODEL="${TRANSLATION_MODEL:-models/opus-mt-nl-en-ct2}"
export TRANSLATION_TOKENIZER="${TRANSLATION_TOKENIZER:-Helsinki-NLP/opus-mt-nl-en}"
export TRANSFORMERS_TRANSLATION_MODEL="${TRANSFORMERS_TRANSLATION_MODEL:-Helsinki-NLP/opus-mt-nl-en}"
export TRANSLATION_DEVICE="${TRANSLATION_DEVICE:-cpu}"
export TRANSLATION_COMPUTE_TYPE="${TRANSLATION_COMPUTE_TYPE:-int8}"
export TRANSLATION_BEAM_SIZE="${TRANSLATION_BEAM_SIZE:-1}"
export TRANSLATION_CACHE_ITEMS="${TRANSLATION_CACHE_ITEMS:-4096}"
export BACKEND_HOST="${BACKEND_HOST:-127.0.0.1}"
export BACKEND_PORT="${BACKEND_PORT:-8000}"

"$PYTHON_BIN" - <<'PY'
import os

try:
    import ctranslate2

    print('ctranslate2:', ctranslate2.__version__)
    get_cuda_count = getattr(ctranslate2, 'get_cuda_device_count', None)
    cuda_count = get_cuda_count() if get_cuda_count else 0
    print('ctranslate2 cuda devices:', cuda_count)
    if os.getenv('ASR_DEVICE', 'cuda').lower() == 'cuda' and cuda_count < 1:
        raise SystemExit('CTranslate2 cannot see a CUDA GPU. Fix CUDA/CTranslate2 install or set ASR_DEVICE=cpu.')
except ImportError:
    raise SystemExit('CTranslate2 is not installed. Run: python3 -m pip install -r requirements.txt')
PY

exec "$PYTHON_BIN" -m uvicorn app.main:app --host "$BACKEND_HOST" --port "$BACKEND_PORT"
