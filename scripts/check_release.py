#!/usr/bin/env python3
"""Check release metadata and the portable source layout before packaging."""
import plistlib
import re
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
version = (root / 'VERSION').read_text().strip()
assert re.fullmatch(r'\d+\.\d+\.\d+', version), 'Invalid VERSION'
info = plistlib.loads((root / 'EnergyMonitorApp/Resources/Info.plist').read_bytes())
assert info['CFBundleShortVersionString'] == version, 'Bundle version drift'
project = (root / 'EnergyMonitorApp/project.yml').read_text()
assert f'MARKETING_VERSION: "{version}"' in project, 'Xcode version drift'
for name in ('web.py', 'energy.py', 'climate.py', 'climate_collect.py', 'extensions.py', 'templates/house.html', 'static/house.js', 'static/cooling-model.js', 'runtime_store.py', 'panel_model.py',
             'EnergyMonitorApp/Sources/main.swift', 'EnergyMonitorApp/Sources/Lifecycle.swift', 'EnergyMonitorApp/Sources/Theme.swift', 'requirements.lock',
             'scripts/check_release.py', 'templates/circuit_overlay.html',
             'static/circuit-overlay.js', 'EnergyMonitorApp/Sources/MenuPopover.swift',
             'EnergyMonitorApp/Sources/CollectorSync.swift', 'sync_history.py', 'radon.py',
             'templates/radon.html', 'ecosense.py', 'panel_photos.py', 'templates/panel_photos.html', 'solar_model.py', 'templates/solar_scenario.html', 'kasa_monitor.py', 'templates/kasa_settings.html'):
    assert (root / name).is_file(), f'Missing release file: {name}'
print(version)
