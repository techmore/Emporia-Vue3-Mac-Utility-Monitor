#!/bin/bash
# Portable source-first release. Does not stop or install the running app.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$SCRIPT_DIR/venv/bin/python3"
[ -x "$PYTHON" ] || { echo "Create venv and install requirements.lock first" >&2; exit 1; }
VERSION="$("$PYTHON" "$SCRIPT_DIR/scripts/check_release.py")"
NO_SWIFT=false
for arg in "$@"; do
  case "$arg" in
    --no-swift) NO_SWIFT=true ;;
    *) echo "Unknown argument: $arg" >&2; exit 1 ;;
  esac
done
APP_DIR="$SCRIPT_DIR/EnergyMonitorApp"
BUNDLE="$APP_DIR/EnergyMonitorApp.app"
mkdir -p "$BUNDLE/Contents/MacOS" "$BUNDLE/Contents/Resources"
cp "$APP_DIR/Resources/Info.plist" "$BUNDLE/Contents/Info.plist"
if [ "$NO_SWIFT" = false ]; then
  swiftc -sdk "$(xcrun --show-sdk-path)" -target arm64-apple-macosx13.0 \
    -framework AppKit "$APP_DIR"/Sources/*.swift \
    -o "$BUNDLE/Contents/MacOS/EnergyMonitorApp"
fi
[ -x "$BUNDLE/Contents/MacOS/EnergyMonitorApp" ] || { echo "Missing app binary" >&2; exit 1; }
DIST="$SCRIPT_DIR/dist"
NAME="Emporia-Energy-Monitor-$VERSION"
STAGE="$DIST/stage/$NAME"
ARCHIVE="$DIST/$NAME-macos.zip"
rm -rf "$STAGE"
mkdir -p "$STAGE/EnergyMonitorApp"
for name in README.md CHANGELOG.md COLLECTOR_CLIENT.md LICENSE AGENTS.md VERSION build.sh release.sh \
  setup_launch.sh requirements.txt requirements.lock energy.py web.py aqara.py \
  runtime_store.py panel_model.py climate.py extensions.py climate_collect.py sync_history.py radon.py; do
  cp "$SCRIPT_DIR/$name" "$STAGE/"
done
for name in setup tests scripts docs templates static; do
  cp -R "$SCRIPT_DIR/$name" "$STAGE/"
done
cp -R "$APP_DIR/Sources" "$APP_DIR/Resources" "$APP_DIR/project.yml" "$BUNDLE" "$STAGE/EnergyMonitorApp/"
# Strip machine-specific launch pointers from the distributed app.
rm -f "$STAGE/EnergyMonitorApp/EnergyMonitorApp.app/Contents/Resources/project_root.txt" \
      "$STAGE/EnergyMonitorApp/EnergyMonitorApp.app/Contents/Resources/data_root.txt" \
      "$STAGE/EnergyMonitorApp/EnergyMonitorApp.app/Contents/Resources/flask_port.txt"
find "$STAGE" -type d -name __pycache__ -prune -exec rm -rf {} +
"$PYTHON" "$SCRIPT_DIR/scripts/check_release.py" "$STAGE"
cat > "$STAGE/RELEASE_NOTES.txt" <<NOTES
Emporia Energy Monitor $VERSION
Homebrew Formula source archive for Apple Silicon, macOS 13+.
Install: brew install techmore/tap/energy-monitor
Homebrew compiles the menu app locally and installs locked Python dependencies from the arm64 wheelhouse.
Start with: brew services start techmore/tap/energy-monitor
Settings, credentials and SQLite history live in \$(brew --prefix)/var/energy-monitor.
For source development, create venv with Python 3.12 and install requirements.lock, then run ./build.sh.
See docs/AUDIT.md, docs/ROADMAP.md and docs/CLIMATE_EXTENSION.md.
NOTES
"$PYTHON" - "$STAGE" <<'PY'
import hashlib, sys
from pathlib import Path
root = Path(sys.argv[1])
lines = [f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(root)}'
         for p in sorted(root.rglob('*')) if p.is_file()]
(root / 'MANIFEST.sha256').write_text('\n'.join(lines) + '\n')
PY
rm -f "$ARCHIVE"
(cd "$DIST/stage" && /usr/bin/zip -qry "$ARCHIVE" "$NAME")
shasum -a 256 "$ARCHIVE" > "$ARCHIVE.sha256"
WHEELHOUSE="$DIST/${NAME}-arm64-wheels"
mkdir -p "$WHEELHOUSE"
"$PYTHON" -m pip download --disable-pip-version-check --only-binary=:all: \
  --dest "$WHEELHOUSE" --platform macosx_13_0_arm64 --platform macosx_11_0_arm64 \
  --implementation cp --python-version 312 --abi cp312 --abi abi3 --abi none \
  -r "$SCRIPT_DIR/requirements.lock"
WHEELS="$DIST/${NAME}-arm64-wheels.tar.gz"
tar -czf "$WHEELS" -C "$WHEELHOUSE" .
shasum -a 256 "$WHEELS" > "$WHEELS.sha256"
echo "Release ready: $ARCHIVE and $WHEELS"
