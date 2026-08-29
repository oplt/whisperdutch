# Dutch Live Subtitle Translator

Local Chrome extension + FastAPI backend for live Dutch ASR and English subtitles.

## Requirements

- Python 3.11+
- Chrome or Chromium
- Linux native messaging support
- Optional NVIDIA GPU with working CUDA for faster Whisper ASR

## Backend Setup

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

For reproducible installs on the tested stack, use the lock file:

```bash
pip install -r requirements.lock
```

Tested stack: Python 3.12, CTranslate2 4.7.1, faster-whisper 1.2.1, CUDA-capable runtime when `ASR_DEVICE=cuda`.

Prepare the CTranslate2 Dutch to English translation model:

```bash
cd backend
bash scripts/prepare_translation_ct2.sh
```

Run backend manually:

```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Health endpoints:

- `GET http://127.0.0.1:8000/health/live`: process is alive.
- `GET http://127.0.0.1:8000/health/ready`: ASR and translation models warmed.
- `GET http://127.0.0.1:8000/debug/device`: device/model readiness and last safe error.
- `GET http://127.0.0.1:8000/metrics`: readiness and recent session metrics.
- `GET http://127.0.0.1:8000/debug/sessions`: recent session latency and queue metrics.
- `GET http://127.0.0.1:8000/debug/session/<client_id>`: one session's metrics.
- `GET http://127.0.0.1:8000/api/history`: persisted local SQLite session history.
- `GET http://127.0.0.1:8000/api/history/<client_id>`: one persisted session with subtitles.

## GPU Profile

Recommended RTX 3060 live profile:

```bash
ASR_DEVICE=cuda
ASR_MODEL=small
ASR_COMPUTE_TYPE=float16
TRANSLATION_ENGINE=ctranslate2
TRANSLATION_DEVICE=cpu
TRANSLATION_COMPUTE_TYPE=int8
FAST_END_SILENCE_SECONDS=0.35
FAST_MAX_SEGMENT_SECONDS=2.8
```

CPU fallback:

```bash
ASR_DEVICE=cpu
ASR_MODEL=small
ASR_COMPUTE_TYPE=int8
TRANSLATION_DEVICE=cpu
TRANSLATION_COMPUTE_TYPE=int8
```

## Runtime Storage

Session history is stored locally in SQLite at `backend/logs/session-history.sqlite3` by default. It includes subtitle text for export/review. Set `SESSION_HISTORY_ENABLED=0` if transcript text must not be persisted.

Translation result caching is memory-only by default. To persist translated subtitle text across backend restarts, opt in explicitly:

```bash
TRANSLATION_CACHE_BACKEND=sqlite
TRANSLATION_CACHE_DB=backend/logs/translation-cache.sqlite3
TRANSLATION_CACHE_ITEMS=4096
TRANSLATION_CACHE_TTL_SECONDS=0
```

The durable cache is separate from session history, bounded by item count, and pruned by optional TTL. Set `TRANSLATION_CACHE_BACKEND=memory` to guarantee no translation-cache text is written to disk.

Runtime translation tokenizer/model loading is offline by default (`LOCAL_MODELS_ONLY=1`). Prepare the translation model once with `backend/scripts/prepare_translation_ct2.sh`; the live backend will then avoid network probes and retry delays. Set `LOCAL_MODELS_ONLY=0` only while intentionally downloading a missing translation model.

Audio and translation queues are bounded independently. Partial subtitles may be dropped under load, while finalized audio is preserved by merging adjacent pending segments. `PIPELINE_QUEUE_MAX_SEGMENTS` and `TRANSLATION_QUEUE_MAX_ITEMS` control those bounds.

## Native Host

Set the extension id, then install native host:

```bash
export DUTCH_SUBTITLE_EXTENSION_ID=<chrome-extension-id>
bash native-host/install_linux.sh
```

The live subtitle window uses native messaging to start `backend/run_gpu.sh`. Re-run install after the extension id changes.

Native host commands:

- `start_backend`: starts backend, choosing the requested/free port.
- `stop_backend`: stops the native-host-managed backend process.
- `restart_backend`: stops then starts backend and returns fresh URLs.

Backend port defaults to `8000`. Set `DUTCH_SUBTITLE_BACKEND_PORT` or send `port` in the native message to request another port. If the requested port is occupied, the native host picks the next free local port and returns `base_url` plus `ws_url`; the popup stores those for the subtitle window.

## Chrome Extension

1. Open `chrome://extensions`.
2. Enable Developer mode.
3. Load unpacked extension from `frontend-extension/`.
4. Open a tab with Dutch audio and click the extension icon.
5. The subtitle window opens, starts the backend if needed, and begins capture automatically.

## Product Features

- Low latency / Balanced / High accuracy presets.
- Per-video context hint for names, topics, jargon.
- Glossary editor backed by `backend/config/glossary.tsv`.
- English target language only unless more translation models are configured.
- Monitor volume/mute.
- Reconnect after backend restart.
- Export `.txt`, `.vtt`, `.srt`.
- Shortcuts: `space` start/stop, `f` font cycle, `m` mute, `e` export TXT, `h` history.

## Quality Checks

```bash
bash scripts/check.sh
```

Runs Python compile, pytest, frontend syntax checks, and Node tests. If `ruff` or `mypy` are installed, the script runs them too.

## Benchmark

Run ASR + translation without Chrome using a 16 kHz 16-bit PCM WAV:

```bash
cd backend
source .venv/bin/activate
python scripts/benchmark_pipeline.py sample.wav --mode fast --segment-seconds 3 --json
```

The output includes p50/p95/max ASR latency, translation latency, total latency, and realtime factor.

## Model Artifacts

Generated models are ignored by git under `backend/models/`. Recreate them with:

```bash
cd backend
bash scripts/prepare_translation_ct2.sh
```

Do not commit `model.bin` or other generated model files. Use Git LFS or an external artifact store only if model versioning becomes required.

## Troubleshooting

- Backend not ready: open `/health/ready` and `/debug/device`; model load errors appear as safe codes.
- `model_missing`: run `backend/scripts/prepare_translation_ct2.sh` and verify ASR model download access.
- `cuda_unavailable`: use CPU env vars or fix NVIDIA driver/CUDA runtime.
- Extension cannot start backend: run `native-host/install_linux.sh` with the current extension id.
- No subtitles: check backend logs in `backend/logs/`, verify tab audio permission, and try Low latency mode.
- Browser memory grows or audio lags: backend is slower than realtime; the subtitle window drops audio when WebSocket buffering exceeds threshold and reports it in Audio status.
