#!/usr/bin/env bash
set -euo pipefail
HOST_NAME="com.polatozgur111.dutch_subtitle_backend"
rm -f "$HOME/.config/google-chrome/NativeMessagingHosts/$HOST_NAME.json"
rm -f "$HOME/.config/chromium/NativeMessagingHosts/$HOST_NAME.json"
rm -f "$HOME/.config/google-chrome-beta/NativeMessagingHosts/$HOST_NAME.json"
rm -f "$HOME/.config/google-chrome-unstable/NativeMessagingHosts/$HOST_NAME.json"
rm -f "$HOME/.config/BraveSoftware/Brave-Browser/NativeMessagingHosts/$HOST_NAME.json"
rm -f "$HOME/.config/BraveSoftware/Brave-Browser-Beta/NativeMessagingHosts/$HOST_NAME.json"
rm -f "$HOME/.config/BraveSoftware/Brave-Browser-Nightly/NativeMessagingHosts/$HOST_NAME.json"
rm -f "$HOME/.mozilla/native-messaging-hosts/$HOST_NAME.json"
echo "Native Messaging host removed."
