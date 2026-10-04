#!/usr/bin/env python3
"""Check release metadata and the portable source layout before packaging."""
from pathlib import Path
import plistlib
import re
import sys

root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
version = (root / 'VERSION').read_text().strip()
assert re.fullmatch(r'\d+\.\d+\.\d+', version), 'Invalid VERSION'
info = plistlib.loads((root / 'EnergyMonitorApp/Resources/Info.plist').read_bytes())
assert info['CFBundleShortVersionString'] == version, 'Bundle version drift'
project = (root / 'EnergyMonitorApp/project.yml').read_text()
assert f'MARKETING_VERSION: "{version}"' in project, 'Xcode version drift'
for name in ('web.py', 'energy.py', 'runtime_store.py', 'panel_model.py',
             'EnergyMonitorApp/Sources/main.swift', 'requirements.lock',
             'scripts/check_release.py'):
    assert (root / name).is_file(), f'Missing release file: {name}'
print(version)
