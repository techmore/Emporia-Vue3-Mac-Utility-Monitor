# AGENTS.md — Emporia Energy Monitor

Guidance for agentic coding agents working in this repository.

---

## Repository Layout

```
energy.py          # Data layer: polling daemon + all SQLite query functions
web.py             # Flask web server: routes, templates, REST API
setup_launch.sh    # Interactive installer for macOS LaunchAgents
setup/
  launchagent.plist          # UI app LaunchAgent template (__PROJECT_ROOT__ placeholder)
  launchagent-poller.plist   # Poller LaunchAgent template (__PROJECT_ROOT__ placeholder)
EnergyMonitorApp/
  Sources/main.swift         # SwiftUI + WKWebView macOS wrapper app
  project.yml                # XcodeGen project definition
  Resources/                 # Info.plist, entitlements
.gitignore
requirements.txt
```

**Two-process architecture:** `energy.py` polls the Emporia cloud API every 60 s and writes
to `energy.db`. `web.py` reads from the same SQLite file. They share state only through the
database — never import `web` from `energy` or vice versa (except `web.py` imports `energy`
for its public query functions).

---

## Build & Run Commands

### Python

```bash
# Install dependencies into the existing virtualenv
venv/bin/pip install -r requirements.txt

# Syntax-check both Python files (no test suite yet)
venv/bin/python3 -m py_compile energy.py web.py

# Run the poller (continuous, unbuffered output)
PYTHONUNBUFFERED=1 venv/bin/python3 -u energy.py

# Run the Flask dashboard
venv/bin/python3 web.py
# → http://localhost:5001

# One-shot poll (useful for debugging without running continuous loop)
venv/bin/python3 energy.py poll

# CLI data inspection
venv/bin/python3 energy.py summary [hours]
venv/bin/python3 energy.py daily   [days]
venv/bin/python3 energy.py hourly  [days]
venv/bin/python3 energy.py latest

# Smoke-test all Flask routes without starting a real server
venv/bin/python3 -c "
import web
c = web.app.test_client()
for path in ['/', '/circuits', '/trends', '/log']:
    r = c.get(path)
    print(r.status_code, path)
"
```

### Swift (macOS app)

```bash
cd EnergyMonitorApp

# Compile (single-file, no Xcode required)
swiftc -o EnergyMonitorApp Sources/main.swift -sdk $(xcrun --show-sdk-path)

# Copy into the app bundle after every recompile
cp EnergyMonitorApp EnergyMonitorApp.app/Contents/MacOS/EnergyMonitorApp

# Run directly
open EnergyMonitorApp.app
```

### LaunchAgents (auto-start at login)

```bash
# Install both agents (UI app + poller) — run from repo root
bash setup_launch.sh   # choose option 1 or 3

# Manual load/unload
launchctl load   ~/Library/LaunchAgents/com.dolbec.energymonitor.plist
launchctl load   ~/Library/LaunchAgents/com.dolbec.energymonitor.poller.plist
launchctl unload ~/Library/LaunchAgents/com.dolbec.energymonitor.plist
launchctl unload ~/Library/LaunchAgents/com.dolbec.energymonitor.poller.plist

# Tail logs
tail -f /tmp/energymonitor-poller.log
tail -f /tmp/energymonitor.log
```

### Linting

Ruff is used (cache present at `.ruff_cache/`). Install and run:

```bash
venv/bin/pip install ruff
venv/bin/ruff check energy.py web.py
venv/bin/ruff check --fix energy.py web.py    # auto-fix safe issues
```

No `pyproject.toml` or `ruff.toml` config file exists; Ruff runs with defaults.

---

## Environment Variables

All have sensible defaults; none are required to run locally with an existing `keys.json`.

| Variable           | Default     | Description                                  |
|--------------------|-------------|----------------------------------------------|
| `DB_PATH`          | `energy.db` | Path to the SQLite database                  |
| `RATE_CENTS`       | `11.04`     | Electricity rate in cents/kWh                |
| `POLL_INTERVAL`    | `60`        | Seconds between Emporia API polls            |
| `DB_RETENTION_DAYS`| `365`       | Rows older than this are pruned each poll    |
| `MONTHLY_BUDGET`   | `150`       | Monthly cost budget in dollars (web.py only) |
| `EMPORIA_EMAIL`    | —           | Only needed on first login (no `keys.json`)  |
| `EMPORIA_PASSWORD` | —           | Only needed on first login (no `keys.json`)  |

`RATE` in `web.py` is always derived from `energy.RATE_CENTS / 100` — never hardcode it
separately in web.py.

---

## Code Style — Python

### Formatting
- **4-space indentation**, no tabs.
- Max line length: ~100 characters (Ruff default is 88; either is acceptable).
- Trailing commas in multi-line collections.
- One blank line between methods, two between top-level definitions.

### Imports
- Standard library first, then third-party, then local (`import energy`).
- `from X import Y` preferred over bare `import X` for commonly used names.
- Avoid inline imports inside functions unless the import is only needed in one
  rarely-called path (e.g., `from urllib.parse import unquote` inside a route is
  acceptable; `from datetime import datetime` at module level is required).
- Never use wildcard imports (`from x import *`).

### Naming
- `snake_case` for functions, variables, module constants that are configurable.
- `UPPER_SNAKE` for true module-level constants (`DB_PATH`, `RATE_CENTS`, `POLL_INTERVAL`).
- Private helpers prefixed with `_` (`_connect`, `_delta_pct`, `_format_hour`).
- SQLite connection helper always via `energy._connect()` — never call
  `sqlite3.connect()` directly anywhere else.

### Types
- Prefer type hints on all public functions in `energy.py`.
- Return type annotations are especially important for query functions that return
  `list[dict]` or `dict`.
- Use `float | None` (union syntax, Python 3.10+) not `Optional[float]`.
- Avoid `Any`; use `dict` with a comment describing the shape if the structure is
  complex.

### Error handling
- Catch `Exception` (never bare `except:`).
- Log unexpected exceptions with `logging.warning(...)` or `logging.exception(...)`;
  do not silently `pass`.
- In `run_continuous`, print the error class and message; do not re-raise (the loop
  must survive transient API failures).
- In Flask routes, let unhandled exceptions propagate — Flask's debug/500 handler
  is preferable to silent wrong data.

### Database
- All connections via `energy._connect()` which sets WAL mode and `row_factory`.
- Open a connection, do the work, close it. Do not hold connections open across
  function calls.
- All query results converted with `[dict(row) for row in results]` before returning
  so callers receive plain dicts, not `sqlite3.Row` objects.
- SQL DDL lives only in `energy.ensure_table()`. Do not add `CREATE TABLE` elsewhere.
- `ensure_table()` is called once at module import — do not call it inside query
  functions.

### Templates (web.py)
- CSS lives in the `BASE_CSS` string constant at the top of `web.py`.
- All pages share the `NAV_HTML` partial and `_render()` helper.
- Use CSS custom properties (`var(--olive-950)`) for all colours — no hardcoded hex
  or rgb in templates.
- Chart.js colours must be read from CSS custom properties at runtime via
  `getComputedStyle` so dark/light mode is respected.

---

## Code Style — Swift

- Target: **macOS 13.0+**, Swift 5.9, compiled with `swiftc` (no Xcode project required).
- No sandbox (`CODE_SIGNING_REQUIRED: NO`); entitlements allow subprocess spawning.
- Project root is derived at runtime via `resolveProjectRoot()` — never hardcode
  absolute paths like `/Users/username/...`.
- All subprocesses (`flask`) spawned via `Process`; always set `currentDirectoryURL`
  to `projectRoot` so relative paths resolve correctly.
- Notification names defined as `Notification.Name` extensions, not raw strings.
- Use `DispatchQueue.main` for all UI updates; background queues only for I/O.

---

## Key Invariants — Do Not Break

1. `energy.py` and `web.py` communicate **only through `energy.db`** (SQLite file).
2. `RATE` in `web.py` is always `energy.RATE_CENTS / 100` — never a separate literal.
3. `web.py` runs on **`127.0.0.1:5001`** only (not `0.0.0.0`).
4. The `instantaneous_watts` column was removed — do not re-add it.
5. `ensure_table()` is the single source of truth for the schema.
6. The poller must be launched with `-u` (unbuffered) or `PYTHONUNBUFFERED=1` so
   logs appear in `/tmp/energymonitor-poller.log` in real time.
7. LaunchAgent plists use `__PROJECT_ROOT__` as a placeholder;
   `setup_launch.sh` substitutes the real path via `sed` at install time.
