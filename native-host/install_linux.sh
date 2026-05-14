#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HOST_NAME="com.polatozgur111.dutch_subtitle_backend"
HOST_PATH="$SCRIPT_DIR/start_backend_host.py"
EXTENSION_ID="$(tr -d '\n' < "$PROJECT_ROOT/EXTENSION_ID.txt")"

chmod +x "$HOST_PATH"

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

Now do this:
  1. Open chrome://extensions
  2. Remove the old Dutch Live Subtitle Translator extension if it exists
  3. Click Load unpacked
  4. Select: $PROJECT_ROOT/frontend-extension
  5. Click the extension icon
  6. Click Start backend

Backend log file:
  $PROJECT_ROOT/backend/backend.log
EOF
