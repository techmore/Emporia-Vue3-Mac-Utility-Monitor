#!/bin/bash
# Setup script for Energy Monitor app
set -euo pipefail

# ── Resolve project directory from this script's location ────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_BINARY="$SCRIPT_DIR/EnergyMonitorApp/EnergyMonitorApp"
APP_BUNDLE="$SCRIPT_DIR/EnergyMonitorApp/EnergyMonitorApp.app"
LAUNCHAGENTS_DIR="$HOME/Library/LaunchAgents"
UI_PLIST_SRC="$SCRIPT_DIR/setup/launchagent.plist"
POLLER_PLIST_SRC="$SCRIPT_DIR/setup/launchagent-poller.plist"
UI_PLIST_DST="$LAUNCHAGENTS_DIR/com.dolbec.energymonitor.plist"
POLLER_PLIST_DST="$LAUNCHAGENTS_DIR/com.dolbec.energymonitor.poller.plist"

echo "Energy Monitor - Setup Script"
echo "=============================="
echo "Project directory: $SCRIPT_DIR"
echo ""

# Check app binary exists
if [ ! -f "$APP_BINARY" ]; then
    echo "Error: App binary not found at $APP_BINARY"
    echo "Compile with:"
    echo "  cd EnergyMonitorApp"
    echo "  swiftc -o EnergyMonitorApp Sources/main.swift -sdk \$(xcrun --show-sdk-path)"
    exit 1
fi

# Helper: install a LaunchAgent plist, substituting __PROJECT_ROOT__ with the
# real project directory path so the plists are portable across machines.
install_plist() {
    local src="$1"
    local dst="$2"

    mkdir -p "$LAUNCHAGENTS_DIR"

    # Unload first if already loaded (ignore errors if not loaded)
    launchctl unload "$dst" 2>/dev/null || true

    # Substitute placeholder, then write to destination
    sed "s|__PROJECT_ROOT__|$SCRIPT_DIR|g" "$src" > "$dst"

    launchctl load "$dst"
    echo "  Loaded: $dst"
}

echo "Choose an option:"
echo "  1) Install LaunchAgents (UI app + poller — auto-start at login)"
echo "  2) Copy app to /Applications for Dock access"
echo "  3) Both"
echo "  4) Uninstall LaunchAgents"
echo "  5) Exit"
echo ""
read -r -p "Enter choice [1-5]: " choice

case "$choice" in
    1)
        echo "Installing LaunchAgents..."
        install_plist "$UI_PLIST_SRC"     "$UI_PLIST_DST"
        install_plist "$POLLER_PLIST_SRC" "$POLLER_PLIST_DST"
        echo "Done — both services will start at login."
        ;;
    2)
        echo "Copying app bundle to /Applications..."
        if [ -d "$APP_BUNDLE" ]; then
            cp -R "$APP_BUNDLE" "/Applications/EnergyMonitorApp.app"
        else
            # Fall back to wrapping the raw binary in a minimal bundle
            mkdir -p "/Applications/EnergyMonitorApp.app/Contents/MacOS"
            cp "$APP_BINARY" "/Applications/EnergyMonitorApp.app/Contents/MacOS/EnergyMonitorApp"
            cp "$SCRIPT_DIR/EnergyMonitorApp/Resources/Info.plist" \
               "/Applications/EnergyMonitorApp.app/Contents/Info.plist"
        fi
        echo "App copied to /Applications. Drag it to the Dock."
        ;;
    3)
        echo "Installing LaunchAgents..."
        install_plist "$UI_PLIST_SRC"     "$UI_PLIST_DST"
        install_plist "$POLLER_PLIST_SRC" "$POLLER_PLIST_DST"

        echo "Copying app bundle to /Applications..."
        if [ -d "$APP_BUNDLE" ]; then
            cp -R "$APP_BUNDLE" "/Applications/EnergyMonitorApp.app"
        else
            mkdir -p "/Applications/EnergyMonitorApp.app/Contents/MacOS"
            cp "$APP_BINARY" "/Applications/EnergyMonitorApp.app/Contents/MacOS/EnergyMonitorApp"
            cp "$SCRIPT_DIR/EnergyMonitorApp/Resources/Info.plist" \
               "/Applications/EnergyMonitorApp.app/Contents/Info.plist"
        fi
        echo "Done — services installed and app is in /Applications."
        ;;
    4)
        echo "Unloading LaunchAgents..."
        launchctl unload "$UI_PLIST_DST"     2>/dev/null && echo "  Unloaded UI agent"     || echo "  UI agent not loaded"
        launchctl unload "$POLLER_PLIST_DST" 2>/dev/null && echo "  Unloaded poller agent" || echo "  Poller agent not loaded"
        rm -f "$UI_PLIST_DST" "$POLLER_PLIST_DST"
        echo "Done — both agents removed."
        ;;
    *)
        echo "Exiting."
        ;;
esac

echo ""
echo "To launch the app manually: open \"$APP_BINARY\""
echo "Poller log:  /tmp/energymonitor-poller.log"
echo "UI app log:  /tmp/energymonitor.log"
