#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$BACKEND_DIR"

if [ -d ".venv" ]; then
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
fi

MODEL_NAME="${MODEL_NAME:-Helsinki-NLP/opus-mt-nl-en}"
OUTPUT_DIR="${OUTPUT_DIR:-models/opus-mt-nl-en-ct2}"
QUANTIZATION="${QUANTIZATION:-int8}"

python -m pip install "ctranslate2>=4.6.0" "transformers>=4.44,<5" sentencepiece sacremoses huggingface-hub

ct2-transformers-converter \
  --model "$MODEL_NAME" \
  --output_dir "$OUTPUT_DIR" \
  --quantization "$QUANTIZATION" \
  --force

cat <<EOF

CTranslate2 translation model is ready:
  $BACKEND_DIR/$OUTPUT_DIR

Use:
  export TRANSLATION_ENGINE=ctranslate2
  export TRANSLATION_MODEL=$OUTPUT_DIR
  export TRANSLATION_TOKENIZER=$MODEL_NAME
EOF
