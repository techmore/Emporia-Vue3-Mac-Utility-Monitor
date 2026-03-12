#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# build.sh — full dev-cycle script for Emporia Energy Monitor
#
# What it does (in order):
#   1. Kill any running Flask / poller processes from this project
#   2. Pull latest from git (so the build always reflects HEAD)
#   3. Compile the Swift app and copy binary into the .app bundle
#   4. Restart the Flask web server (web.py) in the background
#   5. Restart the Emporia poller (energy.py) in the background
#   6. Open the .app bundle (or launch the raw binary if bundle missing)
#
# Usage:
#   ./build.sh              — full build + restart + open app
#   ./build.sh --no-open    — build + restart, skip opening the app
#   ./build.sh --no-swift   — skip Swift compile (Python-only restart)
#   ./build.sh --no-pull    — skip git pull (use local changes)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$SCRIPT_DIR/EnergyMonitorApp"
SRC="$APP_DIR/Sources/main.swift"
BIN="$APP_DIR/EnergyMonitorApp"
BUNDLE="$APP_DIR/EnergyMonitorApp.app"
BUNDLE_BIN="$BUNDLE/Contents/MacOS/EnergyMonitorApp"
VENV_PYTHON="$SCRIPT_DIR/venv/bin/python3"
FLASK_LOG="$SCRIPT_DIR/flask.log"
POLLER_LOG="/tmp/energymonitor-poller.log"

# ── Parse flags ───────────────────────────────────────────────────────────────
DO_OPEN=true
DO_SWIFT=true
DO_PULL=true
for arg in "$@"; do
  case "$arg" in
    --no-open)  DO_OPEN=false  ;;
    --no-swift) DO_SWIFT=false ;;
    --no-pull)  DO_PULL=false  ;;
  esac
done

# ── Helpers ───────────────────────────────────────────────────────────────────
ok()   { echo "  ✓  $*"; }
info() { echo "  →  $*"; }
warn() { echo "  ⚠  $*"; }

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║       Emporia Energy Monitor — build.sh      ║"
echo "╚══════════════════════════════════════════════╝"
echo ""

# ── 1. Kill existing project processes ────────────────────────────────────────
echo "[ 1 / 6 ]  Stopping existing processes…"

# Kill anything using port 5001 that belongs to our venv
for PID in $(lsof -ti :5001 2>/dev/null); do
  CMD=$(ps -p "$PID" -o command= 2>/dev/null || true)
  if echo "$CMD" | grep -q "web\.py"; then
    kill -9 "$PID" 2>/dev/null && ok "Killed Flask (PID $PID)" || true
  fi
done

# Kill any running poller from this project
for PID in $(pgrep -f "$SCRIPT_DIR/venv/bin/python3" 2>/dev/null || true); do
  CMD=$(ps -p "$PID" -o command= 2>/dev/null || true)
  if echo "$CMD" | grep -q "energy\.py"; then
    kill -9 "$PID" 2>/dev/null && ok "Killed poller (PID $PID)" || true
  fi
done

sleep 1

# ── 2. Git pull ───────────────────────────────────────────────────────────────
if [ "$DO_PULL" = true ]; then
  echo ""
  echo "[ 2 / 6 ]  Pulling latest from git…"
  cd "$SCRIPT_DIR"
  git pull --ff-only && ok "Up to date" || warn "git pull failed — continuing with local files"
else
  echo ""
  echo "[ 2 / 6 ]  Skipping git pull (--no-pull)"
fi

# ── 3. Compile Swift app ──────────────────────────────────────────────────────
echo ""
if [ "$DO_SWIFT" = true ]; then
  echo "[ 3 / 6 ]  Compiling Swift app…"
  SDK=$(xcrun --show-sdk-path)
  swiftc \
    -sdk "$SDK" \
    -target arm64-apple-macosx13.0 \
    -framework SwiftUI \
    -framework AppKit \
    -framework WebKit \
    "$SRC" \
    -o "$BIN"
  cp "$BIN" "$BUNDLE_BIN"
  ok "Binary → $BIN"
  ok "Bundle → $BUNDLE_BIN"
else
  echo "[ 3 / 6 ]  Skipping Swift compile (--no-swift)"
fi

# ── 4. Start Flask (web.py) ───────────────────────────────────────────────────
echo ""
echo "[ 4 / 6 ]  Starting Flask server…"
cd "$SCRIPT_DIR"
nohup "$VENV_PYTHON" web.py >> "$FLASK_LOG" 2>&1 &
FLASK_PID=$!
ok "Flask started (PID $FLASK_PID) — log: $FLASK_LOG"

# Wait for Flask to be ready (up to 10s)
info "Waiting for Flask on :5001…"
for i in $(seq 1 10); do
  if curl -s http://localhost:5001/api/version > /dev/null 2>&1; then
    VERSION=$(curl -s http://localhost:5001/api/version | python3 -c "import sys,json; print(json.load(sys.stdin)['version'])" 2>/dev/null || echo "?")
    ok "Flask is up — v$VERSION"
    break
  fi
  sleep 1
done

# ── 5. Start poller (energy.py) ───────────────────────────────────────────────
echo ""
echo "[ 5 / 6 ]  Starting Emporia poller…"
nohup "$VENV_PYTHON" energy.py >> "$POLLER_LOG" 2>&1 &
POLLER_PID=$!
ok "Poller started (PID $POLLER_PID) — log: $POLLER_LOG"

# Give poller a moment to write its first heartbeat
sleep 2
POLLER_STATUS=$(python3 -c "
import json
try:
    d = json.load(open('$SCRIPT_DIR/poller_status.json'))
    print('ok' if d.get('ok') else 'error: ' + (d.get('error') or 'unknown'))
except:
    print('starting…')
" 2>/dev/null || echo "starting…")
ok "Poller status: $POLLER_STATUS"

# ── 6. Open the app ───────────────────────────────────────────────────────────
echo ""
if [ "$DO_OPEN" = true ]; then
  echo "[ 6 / 6 ]  Opening app…"
  if [ -d "$BUNDLE" ]; then
    open "$BUNDLE"
    ok "Opened $BUNDLE"
  elif [ -f "$BIN" ]; then
    "$BIN" &
    ok "Launched $BIN"
  else
    warn "No binary found — compile first with ./build.sh"
  fi
else
  echo "[ 6 / 6 ]  Skipping open (--no-open)"
fi

echo ""
echo "────────────────────────────────────────────────"
echo "  Dashboard → http://localhost:5001"
echo "  Flask log → $FLASK_LOG"
echo "  Poller log → $POLLER_LOG"
echo "────────────────────────────────────────────────"
echo ""
