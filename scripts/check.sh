#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${ROOT}/backend/.venv/bin/python"
if [[ ! -x "${PYTHON}" ]]; then
  PYTHON="$(command -v python3)"
fi

"${PYTHON}" -m compileall "${ROOT}/backend/app" "${ROOT}/backend/tests" "${ROOT}/native-host"
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "${PYTHON}" -m pytest "${ROOT}/backend/tests"

for file in \
  "${ROOT}/frontend-extension/background.js" \
  "${ROOT}/frontend-extension/backend-client.js" \
  "${ROOT}/frontend-extension/subtitle-renderer.js" \
  "${ROOT}/frontend-extension/audio/worklet.js" \
  "${ROOT}/frontend-extension/app/"*.js; do
  node --check "${file}"
done

npm --prefix "${ROOT}" test

if "${PYTHON}" -m ruff --version >/dev/null 2>&1; then
  "${PYTHON}" -m ruff check "${ROOT}/backend/app" "${ROOT}/backend/tests" "${ROOT}/native-host/start_backend_host.py"
fi

if "${PYTHON}" -m mypy --version >/dev/null 2>&1; then
  "${PYTHON}" -m mypy "${ROOT}/backend/app" "${ROOT}/native-host/start_backend_host.py"
fi

echo "All checks passed!"
