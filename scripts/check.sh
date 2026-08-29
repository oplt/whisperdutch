#!/usr/bin/env bash
set -euo pipefail

python -m compileall backend/app backend/scripts native-host
python -m pytest backend/tests
node --check frontend-extension/subtitle.js
node --check frontend-extension/background.js
node --check frontend-extension/worklet.js
npm test

if python -m ruff --version >/dev/null 2>&1; then
  python -m ruff check backend/app backend/tests native-host/start_backend_host.py
fi

if python -m mypy --version >/dev/null 2>&1; then
  python -m mypy backend/app native-host/start_backend_host.py
fi
