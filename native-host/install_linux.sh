#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HOST_NAME="com.polatozgur111.dutch_subtitle_backend"
HOST_PATH="$SCRIPT_DIR/start_backend_host.py"
ENV_FILE="$PROJECT_ROOT/backend/.env"

env_value() {
  local name="$1"
  if [ -f "$ENV_FILE" ]; then
    awk -F= -v key="$name" '$1 == key { sub(/^[^=]*=/, ""); print; exit }' "$ENV_FILE" | tr -d '\r\n'
  fi
}

EXTENSION_ID="${DUTCH_SUBTITLE_EXTENSION_ID:-}"
EXTENSION_PUBLIC_KEY="${DUTCH_SUBTITLE_EXTENSION_PUBLIC_KEY:-}"
FIREFOX_EXTENSION_ID="${DUTCH_SUBTITLE_FIREFOX_EXTENSION_ID:-}"

if [ -z "$EXTENSION_ID" ]; then
  EXTENSION_ID="$(env_value DUTCH_SUBTITLE_EXTENSION_ID)"
fi
if [ -z "$EXTENSION_PUBLIC_KEY" ]; then
  EXTENSION_PUBLIC_KEY="$(env_value DUTCH_SUBTITLE_EXTENSION_PUBLIC_KEY)"
fi
if [ -z "$FIREFOX_EXTENSION_ID" ]; then
  FIREFOX_EXTENSION_ID="$(env_value DUTCH_SUBTITLE_FIREFOX_EXTENSION_ID)"
fi

if [ -z "$EXTENSION_ID" ] && [ -f "$PROJECT_ROOT/EXTENSION_ID.txt" ]; then
  EXTENSION_ID="$(tr -d '\r\n' < "$PROJECT_ROOT/EXTENSION_ID.txt")"
fi
if [ -z "$EXTENSION_PUBLIC_KEY" ] && [ -f "$PROJECT_ROOT/EXTENSION_PUBLIC_KEY.txt" ]; then
  EXTENSION_PUBLIC_KEY="$(tr -d '\r\n' < "$PROJECT_ROOT/EXTENSION_PUBLIC_KEY.txt")"
fi

FIREFOX_EXTENSION_ID="${FIREFOX_EXTENSION_ID:-dutch-subtitle-translator@polatozgur111.local}"

chmod +x "$HOST_PATH"

if [ -n "$EXTENSION_PUBLIC_KEY" ]; then
  PROJECT_ROOT="$PROJECT_ROOT" EXTENSION_PUBLIC_KEY="$EXTENSION_PUBLIC_KEY" python3 - <<'PY'
import json
import os
from pathlib import Path

manifest_path = Path(os.environ["PROJECT_ROOT"]) / "frontend-extension" / "manifest.json"
data = json.loads(manifest_path.read_text())
data["key"] = os.environ["EXTENSION_PUBLIC_KEY"]
manifest_path.write_text(json.dumps(data, indent=2) + "\n")
PY
fi

write_chromium_manifest() {
  local target_dir="$1"
  mkdir -p "$target_dir"
  cat > "$target_dir/$HOST_NAME.json" <<JSON
{
  "name": "$HOST_NAME",
  "description": "Start the local GPU backend for Dutch Live Subtitle Translator",
  "path": "$HOST_PATH",
  "type": "stdio",
  "allowed_origins": [
    "chrome-extension://$EXTENSION_ID/"
  ]
}
JSON
}

write_firefox_manifest() {
  local target_dir="$1"
  mkdir -p "$target_dir"
  cat > "$target_dir/$HOST_NAME.json" <<JSON
{
  "name": "$HOST_NAME",
  "description": "Start the local GPU backend for Dutch Live Subtitle Translator",
  "path": "$HOST_PATH",
  "type": "stdio",
  "allowed_extensions": [
    "$FIREFOX_EXTENSION_ID"
  ]
}
JSON
}

if [ -n "$EXTENSION_ID" ]; then
  write_chromium_manifest "$HOME/.config/google-chrome/NativeMessagingHosts"
  write_chromium_manifest "$HOME/.config/chromium/NativeMessagingHosts"
  write_chromium_manifest "$HOME/.config/google-chrome-beta/NativeMessagingHosts"
  write_chromium_manifest "$HOME/.config/google-chrome-unstable/NativeMessagingHosts"
  write_chromium_manifest "$HOME/.config/BraveSoftware/Brave-Browser/NativeMessagingHosts"
  write_chromium_manifest "$HOME/.config/BraveSoftware/Brave-Browser-Beta/NativeMessagingHosts"
  write_chromium_manifest "$HOME/.config/BraveSoftware/Brave-Browser-Nightly/NativeMessagingHosts"
fi
write_firefox_manifest "$HOME/.mozilla/native-messaging-hosts"

cat <<EOF
Native Messaging host installed.

Chromium extension ID expected by native host:
  ${EXTENSION_ID:-Not configured; Chromium manifests were skipped}

Firefox extension ID expected by native host:
  $FIREFOX_EXTENSION_ID

Now do this:
  Chromium / Brave:
    1. Open chrome://extensions or brave://extensions
    2. Enable developer mode and click Load unpacked
    3. Select: $PROJECT_ROOT/frontend-extension

  Firefox:
    1. Run: npm run build:firefox
    2. Open about:debugging#/runtime/this-firefox
    3. Click Load Temporary Add-on and select: $PROJECT_ROOT/dist/firefox/manifest.json

  Restart the browser after installing the native host, then click the extension icon.

Backend log file:
  $PROJECT_ROOT/backend/logs/
EOF
