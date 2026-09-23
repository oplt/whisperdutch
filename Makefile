.PHONY: local-dev install-backend prepare-models build-firefox check inventory baseline-representative evaluate-models

PROCFILE ?= Procfile

local-dev:
	@if command -v honcho >/dev/null 2>&1; then \
		honcho start -f $(PROCFILE); \
	elif command -v foreman >/dev/null 2>&1; then \
		foreman start -f $(PROCFILE); \
	elif command -v overmind >/dev/null 2>&1; then \
		overmind start -f $(PROCFILE); \
	else \
		echo "No Procfile runner found; running backend process directly from $(PROCFILE)."; \
		sh -c "$$(sed -n 's/^backend: //p' $(PROCFILE))"; \
	fi

install-backend:
	cd backend && python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt

prepare-models:
	cd backend && . .venv/bin/activate && bash scripts/prepare_translation_ct2.sh

build-firefox:
	bash scripts/build_firefox.sh

check:
	bash scripts/check.sh

# Model-free host/config inventory (safe for CI / make check companion).
inventory:
	cd backend && . .venv/bin/activate && python scripts/inventory_runtime.py

# Explicit real-model speech baseline (downloads/weights required; not in check).
baseline-representative:
	cd backend && . .venv/bin/activate && set -a && [ -f .env ] && . ./.env; set +a && python scripts/benchmark_representative.py

# Explicit model quality comparison (loads weights; not in check).
evaluate-models:
	cd backend && . .venv/bin/activate && set -a && [ -f .env ] && . ./.env; set +a && python scripts/evaluate_models.py
