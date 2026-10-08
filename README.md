# Emporia Energy Monitor

A macOS menu-bar app for local-first energy monitoring with [Emporia Vue 3](https://www.emporiaenergy.com/) smart panels. It combines a native Swift/AppKit wrapper, a Flask dashboard, and a SQLite-backed polling engine.

See [Deployment options](docs/DEPLOYMENT_OPTIONS.md) for native macOS, native Linux,
and Incus/LXD installation, plus [SER8 deployment](docs/INCUS_DEPLOYMENT.md) for
the verified container state and remaining production cutover gates.

![Dashboard](https://img.shields.io/badge/version-2.3.34-olive) ![Python](https://img.shields.io/badge/python-3.12-blue) ![Swift](https://img.shields.io/badge/swift-5.9-orange) ![License](https://img.shields.io/badge/license-MIT-green)

---

## Optional HVAC Module

**HVAC** provides read-only Mitsubishi Comfort cloud snapshots, private token-only
sign-in and removal without deleting recorded history. It is disabled until connected;
continuous collection uses a separate opt-in process. No HVAC commands or adapter
reboots are sent. See [module setup and verification](docs/MITSUBISHI.md).

## Compact Menu Bar Monitor

Click the lightning icon in the macOS menu bar for a native compact dropdown with
minute-average power, hourly cost, recorded 24-hour usage, and the five most active
circuits. Select a circuit for 1-, 7- or 30-day recorded usage, cost, a small chart
and a trend when history is sufficiently sampled. Refresh, Dashboard and Settings
are available from the dropdown. Launching the app stays in the menu bar; opening
the app again reveals the dropdown. Right-click the icon for the utility menu.

The native dropdown shows compact horizontal circuit rows in ascending saved slot
order. Circuit labels, live watts and configured breaker ratings follow the Panel
Layout editor. Select a monitored breaker to open its recorded history. The web
dashboard retains its service-panel layout.

## Install with Homebrew

On Apple Silicon Macs running macOS 13 or later:

```sh
brew install techmore/tap/energy-monitor
energy-monitor            # launch the menu bar app
```

The Formula installs Python 3.12, prepares an offline wheelhouse, and compiles the
menu app locally with Swift, so no unsigned downloaded app bundle or Gatekeeper
bypass is involved. Xcode Command Line Tools are required.
Settings, credentials and the SQLite database live in `$(brew --prefix)/var/energy-monitor`.

**Start at login** is turned on the first time the app runs. Change it any time:

| Action | How |
| --- | --- |
| Turn off / on | Menu bar icon → ⋯ → **Start at Login**, or `energy-monitor autostart off` / `on` |
| Check | `energy-monitor autostart status` |
| Stop for now | Menu bar icon → ⋯ → **Quit Energy Monitor** |

It is a per-user LaunchAgent (`~/Library/LaunchAgents/com.dolbec.energymonitor.login.plist`).
If the app is ever removed, the agent deletes itself at the next login.
`brew services start energy-monitor` still works but is no longer needed.

**Uninstall** (your data is kept by default):

```sh
energy-monitor uninstall            # turn off start at login, then brew uninstall
energy-monitor uninstall --purge    # also delete energy history and settings
brew untap techmore/tap             # optional
```

The same option is in the menu: ⋯ → **Uninstall…**

## Getting Started

1. Install and launch using the Homebrew commands above. Click the lightning icon
   and choose **Dashboard**; Homebrew uses `http://127.0.0.1:5019`.
2. Open **Settings** and configure the Emporia account using the available setup
   flow. Check **Logs** for a successful poll and a recent measurement before
   treating any displayed usage as current.
3. Configure your electricity usage rate and monthly fixed charge separately.
   Circuit costs represent usage, not a complete utility bill or assistance credits.
4. Review **Settings → Circuits / Panel Editor** for names, slots, pole counts,
   and breaker ratings. Estimated load comparisons are not electrical safety certification.
5. Use **Trends** for usage patterns and **Reports** for costs and recommendations.
   Historical views fill as readings accumulate; missing samples are not zero usage.

For always-on SER8 collection and native offline Mac history, see
[Collector and Client Setup](COLLECTOR_CLIENT.md). Actual SER8 deployment and
cutover must be verified before stopping local collection.

## Radon Dashboard

Open **Radon** (`/radon`) for sensor-scoped Day, Week, Month, and Year history.
Day/Week plot hourly sample means; Month/Year plot daily sample means. Missing
buckets stay blank, and source measurement timestamps distinguish old history
from recent readings. EcoQube collection is not yet connected; an empty dashboard
is not a zero-radon reading. See [EcoQube Integration](docs/ECOQUBE.md).
Remote history downloads also replicate recorded radon observations into separate
cache tables. The dashboard labels cache download status without implying live
device connectivity; see [Collector and Client](COLLECTOR_CLIENT.md).

## Circuit Quick View

Click a circuit on Dashboard, Circuits, Trends or Reports to open an optional overlay.
Choose **1 day**, **7 days** or **30 days** for recorded kWh, recorded cost and an
hourly/daily chart. Trends compare with the preceding equal-length period only
when both windows have sufficiently dense minute sampling. Missing chart periods
remain gaps. Use **Circuit quick view** to disable previews, or **Open full circuit
page** inside the overlay for the existing detail view. The preference is local
to your browser; keyboard and modified clicks retain their normal behavior.

The existing SQLite database records each cloud poll (default 60 seconds), retains
365 days by default, and persists across app restarts. Poller health is visible in
Log. Longer history fills as the app runs; CSV imports can provide older data, but
unknown import intervals can affect recorded totals and are not a coverage guarantee.
These features are included in the released application; historical tags remain available.

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
- **Menu bar app** — native macOS wrapper with minute-average service watts, offline/stale status, automatic polling when launched directly, and synced app versioning

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
| 4 | Starts `web.py`, waits for `127.0.0.1:${FLASK_PORT:-5051}`, and prints the confirmed version |
| 5 | Starts `energy.py` unbuffered and waits for the first heartbeat |
| 6 | Opens `EnergyMonitorApp.app` unless `--no-open` |

If the default port is occupied, use `FLASK_PORT=5052 ./build.sh --no-pull`.
The chosen port is saved in the app bundle for subsequent launches. Startup rejects
unrelated services on that port and verifies the dashboard version before polling.

Logs:
- Flask → `flask.log in the project`
- Poller → `/tmp/energymonitor-poller.log` when started by `build.sh`, or `poller.log` in the project when started by the menu app

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
- Dashboard → `http://127.0.0.1:5051`
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
GET  /api/menu-summary      # compact native menu power, usage and active circuits
GET  /api/circuit-history/<name> # circuit 1/7/30-day recorded totals, series and guarded trends
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

See `CHANGELOG.md` for release notes, `docs/AUDIT.md` for audit findings, and `docs/ROADMAP.md` for the refinement plan.

### House climate extension (experimental)

**House · Lab** in the dashboard adds an example floor plan, sensor room assignment,
1/7/30-day temperature replay, whole-home energy history and an illustrative cooling scenario.
Choose **Try simulated house** to explore before connecting sensors; demo readings never enter
the database. Real Aqara logging still requires a verified hub connection. An optional read-only
Home Assistant collector and normalized JSON import are available. See
[connection, storage and modeling details](docs/CLIMATE_EXTENSION.md).

## Collector Client

See [COLLECTOR_CLIENT.md](COLLECTOR_CLIENT.md) for secure tunnel connections, persistent Mac client configuration, and history migration gates. Offline synchronization and SER8 deployment are not yet implemented.

### Panel reference photos

Open Settings → Panel Photos (also linked from Panel Editor). Upload up to six
JPEG/PNG images, 10 MiB and 24 megapixels each. Images are oriented, resized to
2400 pixels and re-encoded without metadata; original files are not retained.
Files are stored in `panel-photos/` beside this installation's database, not in
Git or release archives. Remove Photo asks for confirmation. HEIC is not
supported; export phone photos as JPEG first. Photos are local-only and are not
replicated by collector history sync. Treat them as private electrical-layout
reference material, not verified breaker assignments.

### Solar offset scenarios

Reports → Solar Scenario compares user-entered generation for the last 24 hours
with recorded Main demand. Enter an assumed self-consumption percentage and an
optional export-credit rate; blank means unknown, not zero or retail-rate credit.
Self-consumption is capped at recorded demand. Sparse capture can understate
demand, so review Logs before interpreting results. Fixed charges are unchanged;
this is not solar sizing, hourly matching, tariff eligibility or payback advice.
Inputs are carried in the URL and are not saved as device configuration.
