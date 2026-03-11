#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$SCRIPT_DIR/EnergyMonitorApp"
SRC="$APP_DIR/Sources/main.swift"
BIN="$APP_DIR/EnergyMonitorApp"
BUNDLE_BIN="$APP_DIR/EnergyMonitorApp.app/Contents/MacOS/EnergyMonitorApp"
SDK=$(xcrun --show-sdk-path)

echo "Building Energy Monitor..."
swiftc \
  -sdk "$SDK" \
  -target arm64-apple-macosx13.0 \
  -framework SwiftUI \
  -framework AppKit \
  -framework WebKit \
  "$SRC" \
  -o "$BIN"

cp "$BIN" "$BUNDLE_BIN"
echo "Done — binary: $BIN"
echo "       bundle: $BUNDLE_BIN"
