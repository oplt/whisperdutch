#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$BACKEND_DIR"

FAMILY="${1:-m2m100}"
QUANTIZATION="${QUANTIZATION:-int8}"

if [ -d ".venv" ]; then
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
fi

case "$FAMILY" in
  m2m100)
    MODEL_NAME="${MODEL_NAME:-facebook/m2m100_418M}"
    OUTPUT_DIR="${OUTPUT_DIR:-models/m2m100-418m-ct2}"
    ;;
  nllb)
    MODEL_NAME="${MODEL_NAME:-facebook/nllb-200-distilled-600M}"
    OUTPUT_DIR="${OUTPUT_DIR:-models/nllb-200-distilled-600m-ct2}"
    ;;
  *)
    echo "Usage: $0 [m2m100|nllb]" >&2
    exit 1
    ;;
esac

python -m pip install "ctranslate2>=4.6.0" "transformers>=4.44,<5" sentencepiece sacremoses huggingface-hub

ct2-transformers-converter \
  --model "$MODEL_NAME" \
  --output_dir "$OUTPUT_DIR" \
  --quantization "$QUANTIZATION" \
  --force

cat <<EOF

CTranslate2 translation model is ready:
  $BACKEND_DIR/$OUTPUT_DIR

Disk usage: typically 0.6–1.2 GB depending on quantization.
First run downloads the Hugging Face checkpoint (~1–2 GB).

Use:
  export TRANSLATION_ENGINE=ctranslate2
  export TRANSLATION_MODEL_FAMILY=$FAMILY
  export TRANSLATION_MODEL=$OUTPUT_DIR
  export TRANSLATION_TOKENIZER=$MODEL_NAME

NLLB licensing: Meta NLLB checkpoints may impose non-commercial or research
constraints. Review the model license on Hugging Face before commercial use.
EOF
