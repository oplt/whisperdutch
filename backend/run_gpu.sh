#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ -d ".venv" ]; then
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
fi

mkdir -p logs

# Daily logging. App logs are written to backend/logs/backend-YYYY-MM-DD.log.
export BACKEND_LOG_DIR="${BACKEND_LOG_DIR:-logs}"
export BACKEND_LOG_PREFIX="${BACKEND_LOG_PREFIX:-backend}"
export LOG_LEVEL="${LOG_LEVEL:-INFO}"
# Keep subtitle text out of logs by default. Set LOG_TRANSCRIPT_TEXT=1 only while debugging.
export LOG_TRANSCRIPT_TEXT="${LOG_TRANSCRIPT_TEXT:-0}"

# Stable precision-first defaults for RTX 3060 12 GB.
# For lower latency, run: ASR_MODEL=small ./run_gpu.sh
export ASR_DEVICE="${ASR_DEVICE:-cuda}"
export ASR_MODEL="${ASR_MODEL:-medium}"
export ASR_COMPUTE_TYPE="${ASR_COMPUTE_TYPE:-float16}"
export ASR_LANGUAGE="${ASR_LANGUAGE:-nl}"
export ASR_BEAM_SIZE="${ASR_BEAM_SIZE:-2}"
export FAST_ASR_BEAM_SIZE="${FAST_ASR_BEAM_SIZE:-1}"
export BALANCED_ASR_BEAM_SIZE="${BALANCED_ASR_BEAM_SIZE:-2}"
export QUALITY_ASR_BEAM_SIZE="${QUALITY_ASR_BEAM_SIZE:-3}"
export ASR_CONDITION_ON_PREVIOUS_TEXT="${ASR_CONDITION_ON_PREVIOUS_TEXT:-0}"
export ASR_INITIAL_PROMPT="${ASR_INITIAL_PROMPT:-}"

# Stable sentence construction.
# No unstable live partials are shown; each UI row contains Dutch + translation together.
export SENTENCE_MODE="${SENTENCE_MODE:-1}"
export DROP_FILLERS="${DROP_FILLERS:-1}"
export GLOSSARY_ENABLED="${GLOSSARY_ENABLED:-0}"
export GLOSSARY_PATH="${GLOSSARY_PATH:-config/glossary.tsv}"
export MIN_SPEECH_SECONDS="${MIN_SPEECH_SECONDS:-0.35}"
export MIN_FINAL_WORDS="${MIN_FINAL_WORDS:-4}"
export SENTENCE_MAX_BUFFER_WORDS="${SENTENCE_MAX_BUFFER_WORDS:-28}"
export SENTENCE_MAX_BUFFER_CHARS="${SENTENCE_MAX_BUFFER_CHARS:-220}"
export FAST_MAX_SEGMENT_SECONDS="${FAST_MAX_SEGMENT_SECONDS:-4.0}"
export BALANCED_MAX_SEGMENT_SECONDS="${BALANCED_MAX_SEGMENT_SECONDS:-5.5}"
export QUALITY_MAX_SEGMENT_SECONDS="${QUALITY_MAX_SEGMENT_SECONDS:-6.5}"
export FAST_END_SILENCE_SECONDS="${FAST_END_SILENCE_SECONDS:-0.45}"
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

python - <<'PY'
try:
    import torch
    print('torch:', torch.__version__)
    print('cuda available:', torch.cuda.is_available())
    print('cuda version:', torch.version.cuda)
    if torch.cuda.is_available():
        print('gpu:', torch.cuda.get_device_name(0))
    else:
        raise SystemExit('CUDA is not available to PyTorch. Fix CUDA/PyTorch install before running the server.')
except ImportError:
    raise SystemExit('PyTorch is not installed. Install a CUDA PyTorch wheel before running ./run_gpu.sh')
PY

exec python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
