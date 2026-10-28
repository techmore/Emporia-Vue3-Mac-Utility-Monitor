#!/usr/bin/env python3
"""Check release metadata and the portable source layout before packaging.

Usage: check_release.py [ROOT] [--write-metainfo]

VERSION is the one version source. The Mac bundle, the Xcode project, the changelog's newest released entry,
the elementary client's AppStream <releases> element and the Wingpanel indicator's meson project must all agree
with it; the script fails when any of them drifts. --write-metainfo regenerates the <releases> element from
VERSION and the changelog instead of failing (the Homebrew stage has no linux/ directory, so those checks are
skipped there). Prints the version on success, which release.sh relies on.
"""
import hashlib
import json
import plistlib
import re
import sys
from pathlib import Path

CHANGELOG_RELEASE = re.compile(r'^## (\d+\.\d+\.\d+) - (\d{4}-\d{2}-\d{2})\s*$', re.MULTILINE)
RELEASES_ELEMENT = re.compile(r'<releases>.*?</releases>', re.DOTALL)
METAINFO = 'linux/org.techmore.EnergyMonitor.metainfo.xml'
MESON = 'linux/wingpanel/meson.build'


def changelog_release(text):
    """(version, date) of the newest released changelog entry; Unreleased is not a release."""
    match = CHANGELOG_RELEASE.search(text)
    if not match:
        raise AssertionError('CHANGELOG.md has no "## X.Y.Z - YYYY-MM-DD" entry')
    return match.group(1), match.group(2)


def metainfo_releases(version, date):
    """The AppStream <releases> element for the current version, generated rather than hand-edited."""
    return f'<releases><release version="{version}" date="{date}"/></releases>'


def check_metainfo(root, version, changelog, write=False):
    """The metainfo releases must match VERSION and the changelog's newest entry; --write-metainfo regenerates them."""
    entry_version, date = changelog_release(changelog)
    assert entry_version == version, f'CHANGELOG.md newest release is {entry_version}, VERSION is {version}'
    path = root / METAINFO
    text = path.read_text()
    assert RELEASES_ELEMENT.search(text), f'{METAINFO} has no <releases> element'
    expected = metainfo_releases(version, date)
    if RELEASES_ELEMENT.search(text).group(0) != expected:
        if not write:
            raise AssertionError(f'{METAINFO} <releases> does not match VERSION and CHANGELOG.md; '
                                 'run scripts/check_release.py --write-metainfo')
        path.write_text(RELEASES_ELEMENT.sub(expected, text, count=1))
    return date


def check_meson(root):
    """The indicator takes its project version from VERSION instead of carrying its own number."""
    text = (root / MESON).read_text()
    assert "'../../VERSION'" in text and 'version: run_command' in text, f'{MESON} must read the VERSION file'
    assert not re.search(r"version:\s*'\d", text), f'{MESON} hardcodes a project version'


def main(argv):
    write = '--write-metainfo' in argv
    positional = [arg for arg in argv if not arg.startswith('--')]
    root = Path(positional[0]).resolve() if positional else Path(__file__).resolve().parents[1]
    version = (root / 'VERSION').read_text().strip()
    assert re.fullmatch(r'\d+\.\d+\.\d+', version), 'Invalid VERSION'
    info = plistlib.loads((root / 'EnergyMonitorApp/Resources/Info.plist').read_bytes())
    assert info['CFBundleShortVersionString'] == version, 'Bundle version drift'
    project = (root / 'EnergyMonitorApp/project.yml').read_text()
    assert f'MARKETING_VERSION: "{version}"' in project, 'Xcode version drift'
    for name in ('web.py', 'energy.py', 'climate.py', 'climate_collect.py', 'extensions.py', 'templates/house.html', 'static/house.js', 'static/cooling-model.js', 'runtime_store.py', 'panel_model.py',
                 'EnergyMonitorApp/Sources/main.swift', 'EnergyMonitorApp/Sources/Lifecycle.swift', 'EnergyMonitorApp/Sources/Theme.swift', 'requirements.lock',
                 'scripts/check_release.py', 'templates/circuit_overlay.html',
                 'static/circuit-overlay.js', 'static/actions.js', 'EnergyMonitorApp/Sources/MenuPopover.swift',
                 'EnergyMonitorApp/Sources/CollectorSync.swift', 'sync_history.py', 'radon.py',
                 'templates/radon.html', 'ecosense.py', 'ecosense_collect.py', 'mitsubishi.py', 'mitsubishi_collect.py', 'aqara_local.py', 'aqara_matter_collect.py', 'templates/mitsubishi.html', 'panel_photos.py', 'templates/panel_photos.html', 'solar_model.py', 'templates/solar_scenario.html', 'kasa_monitor.py', 'kasa_history.py', 'kasa_collect.py', 'templates/kasa_settings.html', 'templates/kasa.html'):
        assert (root / name).is_file(), f'Missing release file: {name}'
    for name in ('device_identity.py', 'device_history.py', 'templates/device_history.html', 'static/device-history.js', 'aqara_trends.py', 'templates/aqara_trend_chart.html', 'static/aqara-labels.js'):
        assert (root / name).is_file(), f'Missing release file: {name}'
    for name in ('timestamp_model.py', 'scripts/audit_timestamps.py', 'docs/UTC_MIGRATION.md'):
        assert (root / name).is_file(), f'Missing UTC preflight file: {name}'
    assert (root / 'docs/CSV_IMPORT.md').is_file(), 'Missing CSV import/reconciliation guide'
    for name in ('log_setup.py', 'guidance.py', 'scripts/solar_guidance_report.py'):
        assert (root / name).is_file(), f'Missing logging/guidance release file: {name}'
    for name in ('house_control.py', 'preview_sensor_sync.py', 'preview_mains.py', 'emporia_mains.py', 'scripts/run_home3d_preview.py',
                 'templates/home_3d.html', 'static/house-control/home-3d.js',
                 'static/house-control/LICENSES.txt', 'docs/HOUSE_CONTROL.md'):
        assert (root / name).is_file(), f'Missing Home 3D release file: {name}'
    # Self-hosted Chart.js and fonts: every recorded file present and unmodified.
    vendor = root / 'static/vendor'
    recorded = json.loads((vendor / 'UPSTREAM.json').read_text())['files']
    for name in ('chart.umd.min.js', 'fonts.css'):
        assert name in recorded, f'Missing vendored asset: {name}'
    for name, digest in recorded.items():
        path = vendor / name
        assert path.is_file(), f'Missing vendored asset: {name}'
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, f'Vendored asset changed: {name}'
    if (root / 'linux').is_dir():
        check_metainfo(root, version, (root / 'CHANGELOG.md').read_text(), write)
        check_meson(root)
    print(version)


if __name__ == '__main__':
    main(sys.argv[1:])
