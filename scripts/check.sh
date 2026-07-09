#!/usr/bin/env bash
set -euo pipefail

python -m compileall backend/app
python -m pytest backend/tests
node --check frontend-extension/subtitle.js
node --check frontend-extension/popup.js
npm test

if python -m ruff --version >/dev/null 2>&1; then
  python -m ruff check backend/app backend/tests
fi

if python -m mypy --version >/dev/null 2>&1; then
  python -m mypy backend/app
fi
