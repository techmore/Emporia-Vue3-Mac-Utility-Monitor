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
REPO_NAME="$(basename "$SCRIPT_DIR")"
FLASK_PORT="${FLASK_PORT:-5001}"
FLASK_BASE_URL="http://localhost:${FLASK_PORT}"
APP_DIR="$SCRIPT_DIR/EnergyMonitorApp"
SRC="$APP_DIR/Sources/main.swift"
BIN="$APP_DIR/EnergyMonitorApp"
BUNDLE="$APP_DIR/EnergyMonitorApp.app"
BUNDLE_BIN="$BUNDLE/Contents/MacOS/EnergyMonitorApp"
APP_INSTALL="/Applications/EnergyMonitorApp.app"
VENV_PYTHON="$SCRIPT_DIR/venv/bin/python3"
FLASK_LOG="$SCRIPT_DIR/flask.log"
POLLER_LOG="/tmp/energymonitor-poller.log"
POLLER_STATUS_FILE="$SCRIPT_DIR/poller_status.json"

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

kill_matching_repo_processes() {
  local pattern="$1"
  local label="$2"
  for PID in $(pgrep -f "$pattern" 2>/dev/null || true); do
    local CMD
    CMD=$(ps eww -p "$PID" -o command= 2>/dev/null || true)
    if echo "$CMD" | grep -q "$REPO_NAME"; then
      kill -9 "$PID" 2>/dev/null && ok "Killed $label (PID $PID)" || true
    fi
  done
}

kill_installed_app() {
  local installed_bin="$APP_INSTALL/Contents/MacOS/EnergyMonitorApp"
  for PID in $(pgrep -f "$installed_bin" 2>/dev/null || true); do
    kill -9 "$PID" 2>/dev/null && ok "Killed installed app (PID $PID)" || true
  done
}

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║       Emporia Energy Monitor — build.sh      ║"
echo "╚══════════════════════════════════════════════╝"
echo ""

# Refuse to mistake another service for our dashboard before changing processes.
for PID in $(lsof -nP -iTCP:"$FLASK_PORT" -sTCP:LISTEN -t 2>/dev/null || true); do
  CMD=$(ps -p "$PID" -o command= 2>/dev/null || true)
  PROCESS_CWD=$(lsof -a -p "$PID" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')
  if [[ "$PROCESS_CWD" != "$SCRIPT_DIR" || "$CMD" != *"web.py"* ]]; then
    warn "Port $FLASK_PORT is occupied by another service. Set FLASK_PORT to a free port."
    exit 1
  fi
done

# ── 1. Kill existing project processes ────────────────────────────────────────
echo "[ 1 / 6 ]  Stopping existing processes…"

kill_matching_repo_processes "web.py" "Flask"
kill_installed_app

# Kill any running poller or wrapper from this repo, including stale clones
kill_matching_repo_processes "energy.py" "poller"
kill_matching_repo_processes "EnergyMonitorApp" "menu app"

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
    "$APP_DIR"/Sources/*.swift \
    -o "$BIN"
  mkdir -p "$BUNDLE/Contents/MacOS" "$BUNDLE/Contents/Resources"
  cp "$APP_DIR/Resources/Info.plist" "$BUNDLE/Contents/Info.plist"
  cp "$BIN" "$BUNDLE_BIN"
  ok "Binary → $BIN"
  ok "Bundle → $BUNDLE_BIN"
else
  echo "[ 3 / 6 ]  Skipping Swift compile (--no-swift)"
fi

if [ -d "$BUNDLE" ]; then
  mkdir -p "$BUNDLE/Contents/Resources"
  printf "%s\n" "$SCRIPT_DIR" > "$BUNDLE/Contents/Resources/project_root.txt"
  printf "%s\n" "$FLASK_PORT" > "$BUNDLE/Contents/Resources/flask_port.txt"
  rm -rf "$APP_INSTALL"
  cp -R "$BUNDLE" "$APP_INSTALL"
  ok "Installed → $APP_INSTALL"
else
  warn "Bundle missing — skipping install to /Applications"
fi

# ── 4. Start Flask (web.py) ───────────────────────────────────────────────────
echo ""
echo "[ 4 / 6 ]  Starting Flask server…"
cd "$SCRIPT_DIR"
nohup "$VENV_PYTHON" web.py >> "$FLASK_LOG" 2>&1 &
FLASK_PID=$!
ok "Flask started (PID $FLASK_PID) — log: $FLASK_LOG"

# Wait for Flask to be ready (up to 10s)
info "Waiting for Flask on :${FLASK_PORT}…"
FLASK_READY=false
for i in $(seq 1 10); do
  if ! kill -0 "$FLASK_PID" 2>/dev/null; then
    warn "Flask exited during startup. See $FLASK_LOG"
    exit 1
  fi
  if VERSION=$(curl --fail --silent --max-time 2 "$FLASK_BASE_URL/api/version" | "$VENV_PYTHON" -c "import sys,json; print(json.load(sys.stdin)['version'])" 2>/dev/null); then
    ok "Flask is up — v$VERSION"
    FLASK_READY=true
    break
  fi
  sleep 1
done
if [ "$FLASK_READY" != true ]; then
  warn "Flask did not become ready. See $FLASK_LOG"
  kill "$FLASK_PID" 2>/dev/null || true
  exit 1
fi

# ── 5. Start poller (energy.py) ───────────────────────────────────────────────
echo ""
echo "[ 5 / 6 ]  Starting Emporia poller…"
rm -f "$POLLER_STATUS_FILE"
nohup "$VENV_PYTHON" -u energy.py >> "$POLLER_LOG" 2>&1 &
POLLER_PID=$!
ok "Poller started (PID $POLLER_PID) — log: $POLLER_LOG"

# Wait for the new poller heartbeat instead of reading a stale previous run
POLLER_STATUS="starting…"
for i in $(seq 1 15); do
  POLLER_STATUS=$(python3 -c "
import json
try:
    d = json.load(open('$POLLER_STATUS_FILE'))
    print('ok' if d.get('ok') else 'error: ' + (d.get('error') or 'unknown'))
except:
    print('starting…')
" 2>/dev/null || echo "starting…")
  if [ "$POLLER_STATUS" != "starting…" ]; then
    break
  fi
  sleep 1
done
ok "Poller status: $POLLER_STATUS"

# ── 6. Open the app ───────────────────────────────────────────────────────────
echo ""
if [ "$DO_OPEN" = true ]; then
  echo "[ 6 / 6 ]  Opening app…"
  if [ -d "$APP_INSTALL" ]; then
    open "$APP_INSTALL"
    ok "Opened $APP_INSTALL"
  elif [ -d "$BUNDLE" ]; then
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
echo "  Dashboard → $FLASK_BASE_URL"
echo "  Flask log → $FLASK_LOG"
echo "  Poller log → $POLLER_LOG"
echo "────────────────────────────────────────────────"
echo ""
