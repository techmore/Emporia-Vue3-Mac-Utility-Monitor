#!/usr/bin/env bash
set -euo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
/usr/bin/python3 - <<'PY'
import gi
gi.require_version('Gtk','4.0')
gi.require_version('WebKit','6.0')
PY
install -Dm644 "$repo/linux/native_auth.py" "$HOME/.local/lib/energy-monitor/native_auth.py"
install -Dm644 "$repo/linux/client_core.py" "$HOME/.local/lib/energy-monitor/client_core.py"
install -Dm644 "$repo/linux/web_theme.py" "$HOME/.local/lib/energy-monitor/web_theme.py"
install -Dm644 "$repo/linux/temperatures.py" "$HOME/.local/lib/energy-monitor/temperatures.py"
install -Dm644 "$repo/linux/chart.py" "$HOME/.local/lib/energy-monitor/chart.py"
install -Dm755 "$repo/linux/energy_monitor.py" "$HOME/.local/lib/energy-monitor/energy_monitor.py"
install -m644 "$repo/linux/theme.css" "$HOME/.local/lib/energy-monitor/theme.css"
rm -f "$HOME/.local/lib/energy-monitor/theme-light.css" "$HOME/.local/lib/energy-monitor/theme-dark.css"
install -Dm644 "$repo/linux/org.techmore.EnergyMonitor.svg" "$HOME/.local/share/icons/hicolor/scalable/apps/org.techmore.EnergyMonitor.svg"
gtk-update-icon-cache -q -f "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
mkdir -p "$HOME/.local/bin" "$HOME/.local/share/applications" "$HOME/.config/autostart"
cat > "$HOME/.local/bin/energy-monitor" <<'APP'
#!/usr/bin/env bash
exec /usr/bin/python3 "$HOME/.local/lib/energy-monitor/energy_monitor.py" "$@"
APP
chmod +x "$HOME/.local/bin/energy-monitor"
cat > "$HOME/.local/share/applications/org.techmore.EnergyMonitor.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Energy Monitor
GenericName=Home Energy Monitor
Comment=Energy, circuits, costs and home sensors from your existing collector
Keywords=energy;power;electricity;circuits;cost;temperature;emporia;
Exec="$HOME/.local/bin/energy-monitor"
Icon=org.techmore.EnergyMonitor
Categories=Utility;Monitor;
StartupNotify=true
StartupWMClass=org.techmore.EnergyMonitor
X-GNOME-UsesNotifications=true
Actions=Temperatures;Settings;

[Desktop Action Temperatures]
Name=Temperatures
Exec="$HOME/.local/bin/energy-monitor" --temperatures

[Desktop Action Settings]
Name=Collector Connection…
Exec="$HOME/.local/bin/energy-monitor" --connection
DESKTOP
install -Dm644 "$repo/linux/org.techmore.EnergyMonitor.metainfo.xml" "$HOME/.local/share/metainfo/org.techmore.EnergyMonitor.metainfo.xml"
mkdir -p "$HOME/.config/systemd/user"
install -m644 "$repo/linux/energy-monitor-client.service" "$HOME/.config/systemd/user/"
if [[ -f "$HOME/.config/autostart/energy-monitor.desktop" ]]; then
  mv "$HOME/.config/autostart/energy-monitor.desktop" "$HOME/.config/autostart/energy-monitor.desktop.pre-service"
fi
systemctl --user daemon-reload
systemctl --user enable energy-monitor-client.service
if pkg-config --exists wingpanel-9; then
  build=$(mktemp -d /tmp/energy-monitor-wingpanel.XXXXXX)
  trap 'rm -rf "$build"' EXIT
  meson setup "$build" "$repo/linux/wingpanel" --buildtype=release
  ninja -C "$build"
  if sudo -n true 2>/dev/null || [[ -t 0 ]]; then
    sudo install -m644 "$build/libenergy-monitor.so" "$(pkg-config --variable=indicatorsdir wingpanel-9)/libenergy-monitor.so" \
      || echo "Panel library NOT installed (administrator rights were refused). The app itself is up to date; re-run this script from a terminal to update the panel." >&2
  else
    echo "Panel library NOT installed (no administrator rights without a terminal). The app itself is up to date; re-run this script from a terminal to update the panel." >&2
  fi
fi
echo 'Energy Monitor installed. Launch energy-monitor, then choose Collector Connection if needed.'
