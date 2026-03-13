# Emporia Energy Monitor

A macOS menu-bar app for real-time energy monitoring with [Emporia Vue 3](https://www.emporiaenergy.com/) smart panels. Combines a native Swift/AppKit wrapper, a Flask web dashboard, and a SQLite-backed polling engine.

![Dashboard](https://img.shields.io/badge/version-1.7.2-olive) ![Python](https://img.shields.io/badge/python-3.12-blue) ![Swift](https://img.shields.io/badge/swift-5.9-orange) ![License](https://img.shields.io/badge/license-MIT-green)

---

## Features

- **Live panel view** — breaker-by-breaker wattage, NEC 80% safety zones, single/double pole support, top-load badges
- **14-day weather strip** — min/max forecast with HVAC pressure indicators (heating/cooling threshold alerts)
- **Circuit detail** — hourly/daily/weekly/monthly trends per circuit
- **Cost tracking** — $/hr live, daily kWh, monthly projection vs budget
- **Poller health** — heartbeat monitoring, auto-reconnect on token expiry, reconnect UI on `/log`
- **CSV import** — load historical Emporia exports at any resolution
- **Panel editor** — assign circuits to breaker slots, set amps + pole type, add labels and notes
- **Settings** — live Save & Authenticate restarts the poller and confirms connection in-place
- **Mac menu bar** — ⚡ bolt icon, version synced live from Flask, toggle window from status bar

---

## Quick Start

### Prerequisites

- macOS 13+, Xcode command-line tools
- Python 3.12+ with a virtual environment
- Emporia Vue 3 smart panel + account

### 1. Clone and install Python dependencies

```bash
git clone https://github.com/techmore/Emporia-Vue3-Mac-Utility-Monitor.git
cd Emporia-Vue3-Mac-Utility-Monitor
python3 -m venv venv
venv/bin/pip install -r requirements.txt
# For reproducible installs, use the pinned lock file instead:
venv/bin/python3 -m pip install -r requirements.lock
```

### 2. Build, start everything, and open the app

```bash
./build.sh
```

That's it. On first run, open **Settings → Emporia Account**, enter your email and password, and click **Save & Authenticate** — the poller connects live and the nav badge flips to green.

---

## build.sh

The build script is the single command for the full dev cycle. Run it any time you pull new changes or want a clean restart.

```bash
./build.sh              # full build + restart + open app (default)
./build.sh --no-swift   # skip Swift compile — Python-only restart (~3s vs ~30s)
./build.sh --no-pull    # skip git pull — use local uncommitted changes
./build.sh --no-open    # build + restart without opening the app window
```

**What it does in order:**

| Step | Action |
|------|--------|
| 1 | Kills any running Flask (port 5001) and poller from this project |
| 2 | `git pull --ff-only` — always builds from latest |
| 3 | Compiles `main.swift` → copies binary into `.app` bundle |
| 4 | Starts `web.py` (Flask), waits for `:5001`, prints confirmed version |
| 5 | Starts `energy.py` (poller), reads first heartbeat, prints `ok` or error |
| 6 | Opens `EnergyMonitorApp.app` |

Logs:
- Flask → `flask.log` (project root)
- Poller → `/tmp/energymonitor-poller.log`

---

## Manual Usage

If you prefer to run processes individually:

```bash
# Flask dashboard only
venv/bin/python3 web.py

# Poller only (runs continuously, auto-reconnects on token expiry)
venv/bin/python3 energy.py

# One-shot CLI commands
venv/bin/python3 energy.py poll          # single poll
venv/bin/python3 energy.py summary       # 24h summary
venv/bin/python3 energy.py hourly 7      # hourly data for last 7 days
venv/bin/python3 energy.py daily 30      # daily data for last 30 days
venv/bin/python3 energy.py latest        # latest readings per channel
```

Dashboard: **http://localhost:5001**

---

## Auto-start at Login (LaunchAgents)

To have the app and poller start automatically at login:

```bash
./setup_launch.sh
```

Choose option **1** (LaunchAgents) or **3** (LaunchAgents + copy to `/Applications`). This installs two `launchd` plists — one for the UI app and one for the poller — so everything restarts on reboot without any manual steps.

---

## Configuration

All settings are stored in `settings.json` (created automatically) and editable via the **Settings** page at `/settings`:

| Setting | Description |
|---------|-------------|
| Emporia email / password | Used when tokens expire; poller reconnects automatically |
| Electricity rate (¢/kWh) | Used for cost calculations across all views |
| Monthly budget ($) | Budget bar and projection on the dashboard |
| Panel labels | Per-device display names |
| Panel layout | Breaker slot assignments, amps, pole type (1P/2P), labels, notes |
| Panel display | Invert left or right column independently (for bottom-up wiring) |

---

## Poller & Authentication

Emporia uses AWS Cognito tokens that expire after **1 hour**. The poller handles this automatically:

1. On 3 consecutive poll failures it attempts re-login using the refresh token
2. If that fails it waits for a reconnect request from the web UI
3. The `/log` page shows a **Poller Health** card — status dot, last poll time, error details, and a **Reconnect** panel to re-enter credentials without restarting anything
4. **Settings → Save & Authenticate** also triggers an immediate reconnect and polls until the status dot goes green

---

## API Endpoints

```bash
GET  /api/version          # current app version
GET  /api/summary          # 24h usage per circuit
GET  /api/daily            # daily totals
GET  /api/hourly           # hourly totals
GET  /api/latest           # latest reading per channel
GET  /api/context          # now vs historical average
GET  /api/trend            # 7-day trend direction
GET  /api/weather          # 14-day forecast (Open-Meteo, cached 30min)
GET  /api/poller-status    # poller heartbeat + error state
POST /api/poller-reconnect # trigger poller re-authentication
POST /api/panel-layout     # save breaker slot assignments
POST /api/settings/credentials     # save Emporia credentials
POST /api/settings/config          # save rate + budget
POST /api/settings/device-labels   # save device display names
POST /api/settings/panel-display   # save column invert preferences
POST /api/import-csv       # import Emporia CSV export
```

---

## Project Structure

```
.
├── web.py                  # Flask server — all routes, templates, CSS
├── energy.py               # SQLite layer + Emporia API poller
├── aqara.py                # Aqara Hub M3 integration (coming soon)
├── build.sh                # Full dev-cycle build script
├── setup_launch.sh         # LaunchAgent installer for auto-start
├── requirements.txt
├── requirements.lock       # Pinned dependency set for reproducible installs
├── settings.json           # Runtime config (gitignored)
├── keys.json               # Emporia auth tokens (gitignored)
├── energy.db               # SQLite database (gitignored)
└── EnergyMonitorApp/
    ├── Sources/main.swift  # Swift/AppKit menu-bar wrapper
    ├── Resources/Info.plist
    └── EnergyMonitorApp.app/
```
