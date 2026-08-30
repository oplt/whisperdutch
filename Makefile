.PHONY: local-dev install-backend prepare-models build-firefox check

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
	cd backend && python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.lock && pip install -r requirements-dev.txt

prepare-models:
	cd backend && . .venv/bin/activate && bash scripts/prepare_translation_ct2.sh

build-firefox:
	bash scripts/build_firefox.sh

check:
	bash scripts/check.sh
