#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${ROOT}/backend/.venv/bin/python"
if [[ ! -x "${PYTHON}" ]]; then
  PYTHON="$(command -v python3)"
fi

require_module() {
  local module="$1"
  local install_hint="$2"
  if ! "${PYTHON}" -m "${module}" --version >/dev/null 2>&1; then
    echo "error: required checker '${module}' is not available via ${PYTHON}" >&2
    echo "install with: ${install_hint}" >&2
    exit 1
  fi
}

# Model-free quality gate. Real-model / GPU benchmarks stay explicit (see docs/).
echo "==> compileall"
"${PYTHON}" -m compileall "${ROOT}/backend/app" "${ROOT}/backend/tests" "${ROOT}/native-host"

echo "==> pytest (backend, model-free)"
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "${PYTHON}" -m pytest "${ROOT}/backend/tests"

echo "==> node --check (extension sources)"
for file in \
  "${ROOT}/frontend-extension/background.js" \
  "${ROOT}/frontend-extension/backend-client.js" \
  "${ROOT}/frontend-extension/subtitle-renderer.js" \
  "${ROOT}/frontend-extension/audio/worklet.js" \
  "${ROOT}/frontend-extension/app/"*.js; do
  node --check "${file}"
done

echo "==> npm test (extension)"
npm --prefix "${ROOT}" test

require_module ruff "cd backend && . .venv/bin/activate && pip install -r requirements-dev.txt"
require_module mypy "cd backend && . .venv/bin/activate && pip install -r requirements-dev.txt"

echo "==> ruff"
"${PYTHON}" -m ruff check "${ROOT}/backend/app" "${ROOT}/backend/tests" "${ROOT}/native-host/start_backend_host.py"

echo "==> mypy"
"${PYTHON}" -m mypy "${ROOT}/backend/app" "${ROOT}/native-host/start_backend_host.py"

echo "All checks passed!"
