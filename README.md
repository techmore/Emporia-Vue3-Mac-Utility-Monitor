# Emporia Energy Monitor

A macOS menu-bar app for local-first energy monitoring with [Emporia Vue 3](https://www.emporiaenergy.com/) smart panels. It combines a native Swift/AppKit wrapper, a Flask dashboard, and a SQLite-backed polling engine.

![Dashboard](https://img.shields.io/badge/version-2.0.0-olive) ![Python](https://img.shields.io/badge/python-3.12-blue) ![Swift](https://img.shields.io/badge/swift-5.9-orange) ![License](https://img.shields.io/badge/license-MIT-green)

---

## 2.0 Highlights

- **Realtime dashboard focus** — compact live banner, 7-day forecast strip, budget ring, and a service-panel-first layout
- **Service panel model** — 16-slot layouts, split-phase detection, native CSV-derived service capabilities, and estimated live leg fallback when the Emporia poll API omits `Mains_A/B`
- **Cleaner information architecture** — Dashboard for realtime, Trends for patterns/load review, Reports for recommendations/budget/monthly comparison, Settings for operational tools
- **Faster page loads** — latest-channel snapshot table plus dashboard caching keyed to fresh poll timestamps
- **Safer and more reliable** — localhost-only Flask binding, credential file hardening, idempotent CSV correction migration, SQLite busy timeout, CSV validation, autoescaping, and CI smoke coverage
- **Historical import correctness** — native CSV dedupe in SQLite, channel-name normalization, and capability metadata persisted from export headers (including `No CT`)

---

## Features

- **Live banner** — current 60-minute usage window, projected monthly cost, 7-day forecast strip, and intraday today-vs-yesterday comparison rows
- **Service panel view** — total service feed, live leg balance, breaker grid, NEC 80% safety indicators, and 1P/2P breaker modeling
- **Top active circuits** — live watts, 24-hour load, and percent-of-total context directly beside the panel
- **Trends** — daily and hourly charts, month comparison, operational review, biggest 24-hour load, and standby-load review
- **Reports** — recommendations, budget review, monthly comparison, pattern highlights, and workflow shortcuts
- **Circuit drilldown** — hourly/daily/weekly/monthly trends per circuit
- **Poller health** — heartbeat monitoring, reconnect flows, and live status updates through SSE
- **CSV import** — historical Emporia export import with service capability detection and duplicate protection
- **Panel editor** — breaker slot assignments, amps, pole type, labels, notes, and explicit panel slot count
- **Menu bar app** — native macOS wrapper that launches the local dashboard and shows synced app versioning

---

## Quick Start

### Prerequisites

- macOS 13+
- Xcode command-line tools
- Python 3.12+
- Emporia Vue 3 panel + account

### 1. Clone and install dependencies

```bash
git clone https://github.com/techmore/Emporia-Vue3-Mac-Utility-Monitor.git
cd Emporia-Vue3-Mac-Utility-Monitor
python3 -m venv venv
venv/bin/python3 -m pip install -r requirements.lock
```

### 2. Build and launch everything

```bash
./build.sh
```

On first run, open **Settings → Emporia Account**, enter credentials, and save. The poller reconnects immediately and the nav status turns green when live.

---

## Release Packaging

Use `release.sh` to create a source-first macOS release archive with the prebuilt app bundle and required project files.

```bash
./release.sh            # compile Swift app and create dist/Emporia-Energy-Monitor-<version>-macos.zip
./release.sh --no-swift # package the existing app bundle without recompiling Swift
```

The release archive intentionally excludes local runtime state such as `energy.db`, `settings.json`, `keys.json`, and the virtual environment.

---

## Build Script

`build.sh` is the canonical local run path.

```bash
./build.sh              # full build + restart + open app
./build.sh --no-swift   # skip Swift compile
./build.sh --no-pull    # use local changes
./build.sh --no-open    # restart without opening the app window
```

What it does:

| Step | Action |
|------|--------|
| 1 | Kills this repo's Flask, poller, and menu-app processes |
| 2 | `git pull --ff-only` unless `--no-pull` |
| 3 | Compiles `EnergyMonitorApp/Sources/main.swift` and refreshes the `.app` bundle |
| 4 | Starts `web.py`, waits for `127.0.0.1:${FLASK_PORT:-5001}`, and prints the confirmed version |
| 5 | Starts `energy.py` unbuffered and waits for the first heartbeat |
| 6 | Opens `EnergyMonitorApp.app` unless `--no-open` |

Logs:
- Flask → `/Users/seandolbec/Projects/Emporia_energy_monitoring/flask.log`
- Poller → `/tmp/energymonitor-poller.log`

---

## Manual Usage

```bash
# Flask dashboard only
venv/bin/python3 web.py

# Poller only
PYTHONUNBUFFERED=1 venv/bin/python3 -u energy.py

# One-shot poll
venv/bin/python3 energy.py poll

# CLI inspection
venv/bin/python3 energy.py summary
venv/bin/python3 energy.py hourly 7
venv/bin/python3 energy.py daily 30
venv/bin/python3 energy.py latest
```

Defaults:
- Dashboard → `http://127.0.0.1:5001`
- Override port with `FLASK_PORT`

---

## Auto-start At Login

```bash
./setup_launch.sh
```

Choose LaunchAgents to install:
- UI app agent
- poller agent

---

## Configuration

Runtime settings are stored locally in `settings.json` and managed through `/settings`.

| Setting | Description |
|---------|-------------|
| Emporia email / password | Used for reconnect when tokens expire |
| Electricity rate (¢/kWh) | Cost calculations across all views |
| Monthly budget ($) | Budget ring and report projections |
| Device labels | Friendly display names per Emporia device |
| Panel layout | Slot assignment, breaker amps, poles, notes |
| Panel display | Left/right column inversion |
| Panel slots | Explicit slot count for non-20/40 layouts |

Sensitive/runtime files are local-only and gitignored:
- `settings.json`
- `keys.json`
- `poller_status.json`
- `energy.db`

---

## Poller, Auth, And Data Model

- Polls Emporia every `POLL_INTERVAL` seconds
- Stores readings in SQLite (`energy.db`)
- Uses `latest_channel_snapshot` for fast live reads
- Handles token expiry and reconnect automatically
- Persists service capability metadata from CSV exports so the UI can distinguish:
  - aggregate-only service
  - split-phase native service
  - three-phase service
- Falls back to inferred live leg values when the Emporia poll API omits native `Mains_A/B`

---

## API Endpoints

```bash
GET  /api/version           # current app version
GET  /api/summary           # usage by circuit
GET  /api/daily             # daily totals
GET  /api/hourly            # hourly totals
GET  /api/latest            # latest reading per channel
GET  /api/context           # now vs historical windows
GET  /api/trend             # 7-day trend direction
GET  /api/weather           # 7-day forecast (Open-Meteo, cached)
GET  /api/poller-status     # poller heartbeat + error state
GET  /api/events            # SSE updates for live status/dashboard
GET  /api/live-dashboard    # lightweight live dashboard payload
POST /api/poller-reconnect  # trigger poller re-authentication
POST /api/panel-layout      # save breaker slot assignments
POST /api/settings/credentials
POST /api/settings/config
POST /api/settings/device-labels
POST /api/settings/panel-display
POST /api/import-csv
```

---

## Project Structure

```text
.
├── web.py
├── energy.py
├── aqara.py
├── build.sh
├── setup_launch.sh
├── requirements.txt
├── requirements.lock
├── tests/test_energy.py
├── EnergyMonitorApp/
│   ├── Sources/main.swift
│   ├── Resources/Info.plist
│   └── project.yml
└── setup/
    ├── launchagent.plist
    └── launchagent-poller.plist
```

---

## Verification

```bash
venv/bin/python3 -m unittest discover -s tests -v
./build.sh --no-pull --no-open
```

Current test coverage includes:
- latest-reading correctness
- CSV dedupe and migration behavior
- split-phase capability persistence
- dashboard/trends/reports route smoke coverage
- event stream/live payload behavior
- panel slot handling and unmapped circuit backfill

---

## Release Notes

See `/Users/seandolbec/Projects/Emporia_energy_monitoring/CHANGELOG.md` for the 2.0.0 release summary.
