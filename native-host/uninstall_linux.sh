#!/usr/bin/env bash
set -euo pipefail
HOST_NAME="com.polatozgur111.dutch_subtitle_backend"
rm -f "$HOME/.config/google-chrome/NativeMessagingHosts/$HOST_NAME.json"
rm -f "$HOME/.config/chromium/NativeMessagingHosts/$HOST_NAME.json"
rm -f "$HOME/.config/google-chrome-beta/NativeMessagingHosts/$HOST_NAME.json"
rm -f "$HOME/.config/google-chrome-unstable/NativeMessagingHosts/$HOST_NAME.json"
echo "Native Messaging host removed."
