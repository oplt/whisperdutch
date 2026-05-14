# Dutch Subtitle Translator

A local browser subtitle translator that captures Dutch audio from a browser tab, transcribes it with local ASR, translates it, and displays the current subtitle in a popup window.

The app is designed for low-latency local use with a GPU, while keeping the browser extension lightweight.

---

## Features

- Capture audio from a browser tab
- Transcribe Dutch speech locally
- Translate Dutch subtitles into English
- Show the current Dutch subtitle and its translation in a popup window
- Collapsible subtitle/history sections
- Adjustable Dutch and translation font sizes
- Local FastAPI WebSocket backend
- GPU acceleration with `faster-whisper`
- Optional CTranslate2 translation runtime
- Optional Chrome Native Messaging button to start the backend from the extension
- Neutral ASR by default: no hard-coded topic prompt or glossary bias

---

## Architecture

```text
Browser tab audio
  ↓
Chrome Extension / Subtitle Window
  ↓
AudioWorklet: 16 kHz mono PCM chunks
  ↓
WebSocket
  ↓
FastAPI backend
  ↓
VAD / speech segmentation
  ↓
faster-whisper ASR: Dutch audio → Dutch text
  ↓
Sentence buffering / finalization
  ↓
Translation engine: Dutch → English
  ↓
WebSocket response
  ↓
Popup UI: Dutch subtitle + translation
```

The backend runs locally. Audio is streamed from the extension to the backend through WebSocket.

---

## Project Structure

```text
.
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── asr.py
│   │   ├── translator.py
│   │   ├── sentences.py
│   │   └── ...
│   ├── config/
│   │   └── glossary.tsv
│   ├── scripts/
│   │   └── prepare_translation_ct2.sh
│   ├── requirements.txt
│   ├── run_gpu.sh
│   └── .env.example
│
├── frontend-extension/
│   ├── manifest.json
│   ├── popup.html
│   ├── popup.js
│   ├── subtitle.html
│   ├── subtitle.js
│   └── styles.css
│
└── native-host/
    ├── start_backend_host.py
    ├── install_linux.sh
    └── uninstall_linux.sh
```

---

## Requirements

### System

- Linux recommended
- Google Chrome or Chromium
- Python 3.10+
- NVIDIA GPU recommended for low latency
- Working NVIDIA driver

### Recommended GPU

The app works best with a CUDA-capable GPU. It has been tested conceptually around an RTX 3060 12 GB setup.

---

## Backend Setup

From the project root:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

Install PyTorch with a CUDA wheel that matches your NVIDIA driver.

Example for CUDA 11.8:

```bash
pip install torch==2.6.0 torchvision==0.21.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cu118
```

Then install the app dependencies:

```bash
pip install -r requirements.txt
```

Check GPU availability:

```bash
python - <<'PY'
import torch
print("torch:", torch.__version__)
print("cuda available:", torch.cuda.is_available())
print("cuda version:", torch.version.cuda)
if torch.cuda.is_available():
    print("gpu:", torch.cuda.get_device_name(0))
PY
```

Expected output:

```text
cuda available: True
gpu: NVIDIA GeForce RTX 3060
```

---

## Optional: Prepare Fast Translation Runtime

If the project includes CTranslate2 translation support, run:

```bash
cd backend
source .venv/bin/activate
./scripts/prepare_translation_ct2.sh
```

This converts or downloads the translation model for faster local inference.

If you skip this step, the app may fall back to the default translation runtime depending on your backend configuration.

---

## Start Backend

From the backend directory:

```bash
cd backend
source .venv/bin/activate
./run_gpu.sh
```

Health check:

```text
http://127.0.0.1:8000/health
```

Debug device check:

```text
http://127.0.0.1:8000/debug/device
```

You should see that ASR is running on CUDA.

---

## Load the Chrome Extension

1. Open Chrome
2. Go to:

```text
chrome://extensions
```

3. Enable **Developer mode**
4. Click **Load unpacked**
5. Select:

```text
frontend-extension/
```

6. Click the extension icon
7. Open the subtitle window
8. Start playing a Dutch video in the browser

---

## Optional: Start Backend From Extension Button

Chrome extensions cannot directly execute local shell commands for security reasons. This project uses Chrome Native Messaging for the optional **Start backend** button.

From the project root:

```bash
./native-host/install_linux.sh
```

Then reload the extension from:

```text
chrome://extensions
```

The extension popup should now be able to call the native host, which starts:

```bash
backend/run_gpu.sh
```

Backend logs are written to:

```text
backend/backend.log
```

To watch logs:

```bash
tail -f backend/backend.log
```

To stop the backend:

```bash
kill $(cat backend/backend.pid)
```

To uninstall the native host:

```bash
./native-host/uninstall_linux.sh
```

---

## Configuration

The backend can be configured with environment variables or a `.env` file.

Recommended default configuration:

```env
ASR_DEVICE=cuda
ASR_MODEL=small
ASR_COMPUTE_TYPE=float16
ASR_BEAM_SIZE=1
ASR_LANGUAGE=nl

TRANSLATION_DEVICE=cpu
TRANSLATION_COMPUTE_TYPE=int8

SENTENCE_MODE=1
DROP_FILLERS=1
GLOSSARY_ENABLED=0
ASR_INITIAL_PROMPT=
```

For better ASR accuracy, try:

```env
ASR_MODEL=medium
ASR_BEAM_SIZE=2
```

For lower latency, try:

```env
ASR_MODEL=small
ASR_BEAM_SIZE=1
```

---

## About `ASR_INITIAL_PROMPT`

`ASR_INITIAL_PROMPT` is optional.

By default it should be empty:

```env
ASR_INITIAL_PROMPT=
```

Only set it when you know the video domain and want to give the ASR model context.

Example:

```env
ASR_INITIAL_PROMPT=Dit is Nederlandstalige audio over voetbal en sportnieuws.
```

Do not hard-code a specific topic prompt for a general translation app. It can bias transcription and reduce accuracy on unrelated videos.

---

## Glossary

Glossary correction is disabled by default:

```env
GLOSSARY_ENABLED=0
```

If enabled, the backend can apply controlled term replacements from:

```text
backend/config/glossary.tsv
```

Use this only for known recurring terms, brand names, project vocabulary, or domain-specific videos.

---

## UI Behavior

The subtitle window shows:

```text
Current Dutch subtitle
English translation
```

History is collapsible so the main screen stays focused on the current subtitle.

The UI also supports adjustable font sizes for Dutch and translated text.

---

## Performance Notes

For best performance:

- Use GPU for ASR
- Use `ASR_MODEL=small` for low latency
- Use `ASR_MODEL=medium` for better accuracy
- Avoid running the backend with `uvicorn --reload` during real usage
- Keep translation on CPU int8 if GPU contention causes ASR slowdown
- Use CTranslate2 translation when available

Expected behavior on a good local GPU setup:

```text
Dutch subtitle: fast after speech segment finalization
Translation: appears after the Dutch sentence is finalized
```

The app prioritizes stable subtitles over rapidly changing incomplete partial text.

---

## Troubleshooting

### CUDA is not available

Run:

```bash
nvidia-smi
python - <<'PY'
import torch
print(torch.__version__)
print(torch.cuda.is_available())
print(torch.version.cuda)
PY
```

If CUDA is false, install a PyTorch CUDA wheel compatible with your NVIDIA driver.

---

### Backend starts but no subtitles appear

Check backend logs:

```bash
tail -f backend/backend.log
```

Also verify that:

- The backend is running on `127.0.0.1:8000`
- The extension is loaded from the latest `frontend-extension/` folder
- The subtitle window is open
- The browser has permission to capture tab audio
- The video is not DRM-protected or blocking capture

---

### Translation is missing or slow

Check whether the translation model loaded successfully in backend logs.

Try CPU translation:

```bash
TRANSLATION_DEVICE=cpu ./run_gpu.sh
```

If using CTranslate2, run:

```bash
./scripts/prepare_translation_ct2.sh
```

---

### Subtitles are inaccurate

Try a better ASR model:

```bash
ASR_MODEL=medium ASR_BEAM_SIZE=2 ./run_gpu.sh
```

For videos with a known topic, use a short neutral context prompt:

```bash
ASR_INITIAL_PROMPT="Dit is Nederlandstalige audio over sportnieuws." ./run_gpu.sh
```

Avoid overly specific prompts unless the video really matches that domain.

---

## Known Limitations

- Browser tab audio capture can be restricted by browser security policies
- DRM-protected streams may not work
- Translation quality depends heavily on ASR quality
- Very noisy audio may produce poor subtitles
- Fully real-time word-by-word translation is intentionally avoided because it often produces unstable and incorrect translations

---

## Development

Start backend manually:

```bash
cd backend
source .venv/bin/activate
./run_gpu.sh
```

Reload extension after frontend changes:

```text
chrome://extensions → Reload
```

Watch logs:

```bash
tail -f backend/backend.log
```

---

## Roadmap

Potential improvements:

- Add language selection in the UI
- Add subtitle export as `.srt` or `.vtt`
- Add model/mode selector in the popup
- Add per-video glossary profiles
- Add translation caching
- Add better VAD tuning controls
- Add Docker setup for backend
- Add Windows/macOS native host installers

---

## License

Choose a license before publishing. For open-source release, MIT or Apache-2.0 are common choices.
