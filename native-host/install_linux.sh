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

EXTENSION_ID="$(env_value DUTCH_SUBTITLE_EXTENSION_ID)"
EXTENSION_PUBLIC_KEY="$(env_value DUTCH_SUBTITLE_EXTENSION_PUBLIC_KEY)"

if [ -z "$EXTENSION_ID" ] && [ -f "$PROJECT_ROOT/EXTENSION_ID.txt" ]; then
  EXTENSION_ID="$(tr -d '\r\n' < "$PROJECT_ROOT/EXTENSION_ID.txt")"
fi
if [ -z "$EXTENSION_PUBLIC_KEY" ] && [ -f "$PROJECT_ROOT/EXTENSION_PUBLIC_KEY.txt" ]; then
  EXTENSION_PUBLIC_KEY="$(tr -d '\r\n' < "$PROJECT_ROOT/EXTENSION_PUBLIC_KEY.txt")"
fi

if [ -z "$EXTENSION_ID" ]; then
  echo "Missing DUTCH_SUBTITLE_EXTENSION_ID in $ENV_FILE or EXTENSION_ID.txt" >&2
  exit 1
fi

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

write_manifest() {
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

write_manifest "$HOME/.config/google-chrome/NativeMessagingHosts"
write_manifest "$HOME/.config/chromium/NativeMessagingHosts"
write_manifest "$HOME/.config/google-chrome-beta/NativeMessagingHosts"
write_manifest "$HOME/.config/google-chrome-unstable/NativeMessagingHosts"

cat <<EOF
Native Messaging host installed.

Extension ID expected by native host:
  $EXTENSION_ID

Extension public key source:
  $ENV_FILE

Now do this:
  1. Open chrome://extensions
  2. Remove the old Dutch Live Subtitle Translator extension if it exists
  3. Click Load unpacked
  4. Select: $PROJECT_ROOT/frontend-extension
  5. Click the extension icon
  6. Click Start backend

Backend log file:
  $PROJECT_ROOT/backend/logs/
EOF
