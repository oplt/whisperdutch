#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SOURCE_DIR="$PROJECT_ROOT/frontend-extension"
BUILD_DIR="$PROJECT_ROOT/dist/firefox"

rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

cp -R "$SOURCE_DIR/app" "$BUILD_DIR/app"
cp -R "$SOURCE_DIR/audio" "$BUILD_DIR/audio"
cp "$SOURCE_DIR/backend-client.js" "$BUILD_DIR/backend-client.js"
cp "$SOURCE_DIR/background.js" "$BUILD_DIR/background.js"
cp "$SOURCE_DIR/styles.css" "$BUILD_DIR/styles.css"
cp "$SOURCE_DIR/subtitle-renderer.js" "$BUILD_DIR/subtitle-renderer.js"
cp "$SOURCE_DIR/subtitle.html" "$BUILD_DIR/subtitle.html"
cp "$SOURCE_DIR/manifest.firefox.json" "$BUILD_DIR/manifest.json"

echo "Firefox extension built at: $BUILD_DIR"
