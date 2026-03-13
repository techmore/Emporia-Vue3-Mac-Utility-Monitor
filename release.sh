#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DIST_DIR="$SCRIPT_DIR/dist"
STAGE_ROOT="$DIST_DIR/stage"
VERSION="$($SCRIPT_DIR/venv/bin/python3 - <<'PY'
import re
from pathlib import Path
text = Path('/Users/seandolbec/Projects/Emporia_energy_monitoring/web.py').read_text()
m = re.search(r'^VERSION\s*=\s*"([^"]+)"', text, re.M)
if not m:
    raise SystemExit('Could not determine version from web.py')
print(m.group(1))
PY
)"
RELEASE_NAME="Emporia-Energy-Monitor-$VERSION"
STAGE_DIR="$STAGE_ROOT/$RELEASE_NAME"
ARCHIVE_PATH="$DIST_DIR/$RELEASE_NAME-macos.zip"
APP_DIR="$SCRIPT_DIR/EnergyMonitorApp"
APP_BUNDLE="$APP_DIR/EnergyMonitorApp.app"
APP_BIN="$APP_DIR/EnergyMonitorApp"
NO_SWIFT=false

for arg in "$@"; do
  case "$arg" in
    --no-swift) NO_SWIFT=true ;;
    *) echo "Unknown argument: $arg" >&2; exit 1 ;;
  esac
done

ok() { echo "  ✓  $*"; }
info() { echo "  →  $*"; }
warn() { echo "  ⚠  $*"; }

if [ ! -x "$SCRIPT_DIR/venv/bin/python3" ]; then
  echo "Missing virtualenv Python at $SCRIPT_DIR/venv/bin/python3" >&2
  exit 1
fi

mkdir -p "$DIST_DIR"
rm -rf "$STAGE_DIR"
mkdir -p "$STAGE_DIR"

if [ "$NO_SWIFT" = false ]; then
  info "Compiling Swift app bundle"
  SDK="$(xcrun --show-sdk-path)"
  swiftc \
    -sdk "$SDK" \
    -target arm64-apple-macosx13.0 \
    -framework SwiftUI \
    -framework AppKit \
    -framework WebKit \
    "$APP_DIR/Sources/main.swift" \
    -o "$APP_BIN"
  cp "$APP_BIN" "$APP_BUNDLE/Contents/MacOS/EnergyMonitorApp"
  ok "Swift app compiled"
else
  warn "Skipping Swift compile (--no-swift)"
fi

info "Staging release files"
mkdir -p "$STAGE_DIR/EnergyMonitorApp"
mkdir -p "$STAGE_DIR/setup"
mkdir -p "$STAGE_DIR/tests"

cp "$SCRIPT_DIR/README.md" "$STAGE_DIR/README.md"
cp "$SCRIPT_DIR/CHANGELOG.md" "$STAGE_DIR/CHANGELOG.md"
cp "$SCRIPT_DIR/LICENSE" "$STAGE_DIR/LICENSE"
cp "$SCRIPT_DIR/build.sh" "$STAGE_DIR/build.sh"
cp "$SCRIPT_DIR/setup_launch.sh" "$STAGE_DIR/setup_launch.sh"
cp "$SCRIPT_DIR/requirements.txt" "$STAGE_DIR/requirements.txt"
cp "$SCRIPT_DIR/requirements.lock" "$STAGE_DIR/requirements.lock"
cp "$SCRIPT_DIR/energy.py" "$STAGE_DIR/energy.py"
cp "$SCRIPT_DIR/web.py" "$STAGE_DIR/web.py"
cp "$SCRIPT_DIR/aqara.py" "$STAGE_DIR/aqara.py"
cp "$SCRIPT_DIR/AGENTS.md" "$STAGE_DIR/AGENTS.md"
cp "$SCRIPT_DIR/setup/launchagent.plist" "$STAGE_DIR/setup/launchagent.plist"
cp "$SCRIPT_DIR/setup/launchagent-poller.plist" "$STAGE_DIR/setup/launchagent-poller.plist"
cp "$SCRIPT_DIR/tests/test_energy.py" "$STAGE_DIR/tests/test_energy.py"
cp -R "$APP_BUNDLE" "$STAGE_DIR/EnergyMonitorApp/EnergyMonitorApp.app"
cp "$APP_DIR/Resources/Info.plist" "$STAGE_DIR/EnergyMonitorApp/Info.plist"
cp "$APP_DIR/project.yml" "$STAGE_DIR/EnergyMonitorApp/project.yml"
cp "$APP_DIR/Resources/EnergyMonitorApp.entitlements" "$STAGE_DIR/EnergyMonitorApp/EnergyMonitorApp.entitlements"
cp "$APP_DIR/Sources/main.swift" "$STAGE_DIR/EnergyMonitorApp/main.swift"

cat > "$STAGE_DIR/RELEASE_NOTES.txt" <<NOTES
Emporia Energy Monitor $VERSION

This archive is a source-first macOS release package.

Included:
- Prebuilt macOS app bundle in EnergyMonitorApp/EnergyMonitorApp.app
- Python sources and setup scripts
- LaunchAgent templates
- Locked Python dependency set

Not included:
- Your local database, tokens, or runtime settings
- Python virtualenv
- Signed/notarized installer package

Recommended first-run steps:
1. Create a virtualenv and install requirements.lock
2. Run ./build.sh or open EnergyMonitorApp/EnergyMonitorApp.app
3. Configure credentials in Settings
NOTES

info "Creating zip archive"
rm -f "$ARCHIVE_PATH"
cd "$STAGE_ROOT"
/usr/bin/zip -qry "$ARCHIVE_PATH" "$RELEASE_NAME"
ok "Archive created at $ARCHIVE_PATH"

cat <<SUMMARY

Release package ready:
  Version: $VERSION
  Archive: $ARCHIVE_PATH
  Staged:  $STAGE_DIR
SUMMARY
