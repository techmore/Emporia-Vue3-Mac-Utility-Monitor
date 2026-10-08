# Changelog

## 2.3.9 - Unreleased

- Preserve labeled, noted, rated, or double-pole unmonitored breaker slots during automatic channel placement.

## 2.3.8 - 2026-10-07

- Restore the compact native menu to side-by-side physical slot rows: 1/2, 3/4, and onward.

- Show panel save errors instead of falsely reporting success on rejected requests.
- Reject non-object panel layout requests with HTTP 400 instead of server errors. Add isolated pole/amp persistence, estimated-load, and invalid-batch regression tests.

## 2.3.7 - 2026-10-07

- Lead the Guide with a linked setup checklist, collection verification, separate billing configuration, and truthful integration status; make reference cards responsive.

## 2.3.6 - 2026-10-07

- Add a recorded EcoQube/radon dashboard with sensor selection and day, week, month, and year history. Missing data stays blank; collection remains unconnected pending verified source timestamps.
- Add validated, sensor-scoped radon storage and a private read-only EcoSense diagnostic probe.

## 2.3.5 - 2026-10-07

- Restore the compact olive/stone menu: single-line service banner, two-line cost cards, horizontal circuit rows, smaller padding, and ascending slot order.
- Fix decoding of the native 24-hour cost field.

- Preserve Flask context for live event streams so cold-cache rendering and subsequent dashboard rebuilds do not fail.
- Add a stream-first regression test.

## 2.3.4 - 2026-10-07

- Add token-protected transactional history synchronization with collector identity, paginated changes, and generation checkpoints.
- Preserve offline caches while rebuilding after journal retention; synchronize repricing, compaction, and deletions.
- Add private native offline summaries and downloaded device-scoped history with explicit stale-data labels.
- Add opt-in automatic client downloads, Keychain token storage, overlap prevention, and bounded subprocess lifetime.
- Keep remote-client mode free of local collection; SER8 deployment still requires verified server access.

## 2.3.3 - 2026-10-07

- Add persistent collector-client connection settings; remote mode starts neither local Flask nor a local poller.
- Add verified online SQLite backups for history migration.
- Separate configurable monthly fixed charges from circuit usage costs in Reports.
- Rank measured usage reviews and show explicitly hypothetical savings scenarios.
- Gate circuit week comparisons on minute-level capture coverage.
- Implement Aqara v3 regional endpoints, request signing, token refresh, and sensor discovery with Settings authorization controls. Live authorization still requires approved credentials.
- Preserve the 2.3.2 menu popover, lifecycle controls, climate extensions, and compaction behavior.

## 2.3.2 - 2026-10-06

- Fix Homebrew installs: the menu app launches `web.py` and `energy.py` by absolute path, since it runs them from the data directory.

## 2.3.1 - 2026-10-06

- Fix uninstall of Homebrew installs launched through the `opt` link, which were treated as plain apps.

## 2.3.0 - 2026-10-06

- Start at login is on by default and togglable from the menu or `energy-monitor autostart on|off|status`.
- Uninstall from the menu or `energy-monitor uninstall [--purge]`; Homebrew installs are removed through Homebrew and data is kept unless purged.
- Menu popover shows 24h and month-to-date cost, usage highlights and top-usage stars.
- Warn when the dashboard port is held by another app; Emporia API timeouts are retried.

- Menu popover themed to match the dashboard; breaker cards show relative-usage highlight and a star on the top three.
- Default port moved to 5051. `build.sh` stops processes gracefully before force-killing.
- Minute readings older than `MINUTE_RETENTION_DAYS` (30) are folded into hourly rows; redundant indexes dropped.
- Poller logs per-poll duration and warns on gaps; charts show unrecorded periods as gaps.
- Monthly cost-by-circuit report on Reports, `/api/monthly-costs`, and automatic `reports/energy-YYYY-MM.md` files.
- Ruff configured; lint clean.

## 2.2.1 - 2026-10-05

- Hide breaker load indicators when live readings are unavailable instead of implying zero load.
- Scope the app's single-instance lock to its data directory so source and Homebrew installs can coexist.

## 2.2.0 - 2026-10-05

- Native dropdown follows the saved panel breaker slots with a two-column service-panel layout.
- Homebrew Formula for Apple Silicon; it installs a wheelhouse offline and builds the menu app locally.

## 2.1.0 - Unreleased

- Experimental House climate extension: SQLite temperature observations, room placement,
  1/7/30-day replay alongside energy, clearly labeled simulated preview, cooling scenario
  calculator, and optional read-only Home Assistant collector. Aqara connection remains pending.

- Icon-only native menu bar monitor with compact SwiftUI dropdown
- Live power, hourly cost, recorded 24-hour energy and top circuit summaries
- Native circuit 1/7/30-day chart and trend view; Dashboard/Settings shortcuts
- Quiet startup with no automatic browser window; app reopen reveals the dropdown
- Compact JSON menu endpoint with offline-safe power values

- Optional circuit overlay with rolling 1-day, 7-day and 30-day energy and cost
- Hourly/daily charts, accessible chart data, explicit missing-data buckets
- Equal-period trends guarded against sparse history; stale live power unavailable
- Toggle preference, Escape/backdrop close, full-page fallback and refresh/error states
- SQLite logging verified; no database replacement or schema migration required
- Feature isolated from the stable 2.0.1 tag

## 2.0.1 - 2026-10-04

### Added
- Minute-average service watts and offline status in the native menu bar
- Automatic poller launch when the menu app starts directly
- Shared breaker model and atomic private runtime storage modules
- Version metadata checks, portable release archive, manifests and checksums
- Code/UI audit and phased maintenance roadmap in `docs/`

### Fixed
- Unsafe dynamic circuit/import text rendering and cross-origin local writes
- In-place runtime JSON writes and partially applied panel layout requests
- Invented 15A ratings; unconfigured ratings now remain unknown
- Conflicting monthly projections and stale live service/breaker/budget display
- Dashboard cache invalidation, rate refresh in the poller and stale power labeling
- Unreachable copied dashboard URL, custom-port persistence and unrelated-port readiness
- Single-instance race using an OS lock
- Release staging paths, machine-specific pointers and version drift
- Expired test fixture, control names/states, weather label, ring text and rate formatting
- Document title/language/mobile viewport and basic narrow-screen reflow

### Validation
- 26 unit/regression tests, Python and shell syntax checks, native Swift compilation
- Browser checks of live Dashboard and Settings, projections, rating labels and rate
- Fresh archive extraction, metadata/manifest verification and route smoke checks
- Local 2.0.1 tag and package; remote publication remains separate

### Known limits
- Cost projections remain provisional with partial history; source/interval-aware
  aggregation and physical-panel mapping need further work
- Charts and secondary historical cards still require reload; see `docs/AUDIT.md`
- Unsigned Apple Silicon source-first package; Python virtualenv required

## 2.0.0 - 2026-03-13

### Added
- Server-Sent Events for live nav status and dashboard payload updates
- Latest-channel snapshot table for faster dashboard reads
- Service capability persistence from CSV headers, including `No CT` handling
- Split-phase native/inferred service-feed rendering model
- 7-day forecast strip integrated into the live dashboard banner
- Reports and Trends jump navigation and reorganized section hierarchy
- Dashboard/trends/reports regression tests in `/Users/seandolbec/Projects/Emporia_energy_monitoring/tests/test_energy.py`

### Changed
- Dashboard now focuses on realtime banner, service panel, and live circuit context
- Trends now owns historical charts, month comparison, operational review, and load review
- Reports now owns recommendations, billing review, and budget/monthly analysis
- Settings now owns operational tool links for Circuits, Import, Aqara, and Log
- Explicit panel slot counts supported for non-20/40 panel inventories
- Poll/build flow hardened against stale repo copies and invalid local environments

### Fixed
- Repeated CSV correction migration corruption
- Stored XSS risk from string-template rendering without autoescape
- CSV import scalability and duplicate handling
- Latest-reading selection after historical imports
- Multi-device data conflation
- Incorrect UTC/local analytics mixing
- Numeric circuit names being dropped from the UI
- Bus bar and panel layout drift across Dashboard and Circuits views
- Missing circuit rendering when saved panel layouts were partial
- Build failures caused by unresolved bundle metadata

### Security
- Flask binds to `127.0.0.1` by default
- Credentials and token files are hardened to owner-only permissions

### Verification
- `venv/bin/python3 -m unittest discover -s tests -v`
- `./build.sh --no-pull --no-open`
