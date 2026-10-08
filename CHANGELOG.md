# Changelog

## 2.3.35 - 2026-10-08

- Give Aqara a dedicated desktop tab, Fahrenheit-first display and four-hour
  recorded temperature/humidity trends per sensor, with longer windows,
  sensor-focused history and Plot all. Preserve Celsius storage and gaps, bound
  history chart output and distinguish cached collector observations from fresh
  measurements. Add verified room-label editing backed by SQLite with guarded
  same-origin writes. Include new modules/assets in release validation.

## 2.3.34 - 2026-10-08

- Make the verified Kasa collection cadence an explicit, validated deployment
  option shared by native Linux and Incus/LXD, recorded in the service plan.
- Exclude EcoSense credentials/snapshots, private collector environments and
  alternate SQLite filenames from Git. Test private/public file boundaries and
  document the actual deployed source versions without activating new collectors.

## 2.3.33 - 2026-10-08

- Preserve live native API additions for per-circuit today/week/month recorded
  usage/cost and a stored, unit-converted radon indicator. Add regression tests
  for device boundaries, future readings, empty data and stale radon values.
- Verify a private online backup for Incus migration, align the guest timezone
  with the current collector, and verify private Matter/Kasa connectivity without
  re-pairing or sending device-control commands. Production cutover remains gated.

## 2.3.32 - 2026-10-08

- Add opt-in macOS tunnel recovery based on actual HTTP health, independent
  SSH verification, three-failure confirmation and bounded restart backoff.
  Restart only the verified loaded app tunnel; preserve intentionally unloaded
  tunnels, native layout, cached history and server collectors. Include a private
  standalone installer and transport status diagnostics.

## 2.3.31 - 2026-10-08

- Add mobile device-section tabs and guarded swipe navigation for Circuits,
  Aqara, EcoQube, Mitsubishi and Kasa, with per-section scroll restoration.
- Document native and Incus/LXD deployment options and add tested system-service
  plan generation. Incus dashboard staging and restart are verified; production
  data/collector migration and authenticated public routing remain incomplete.

## 2.3.30 - 2026-10-08

- Preserve and integrate live EcoQube per-measurement pCi/L charts, Kasa rocker/dimmer controls and diagnostics, and local Aqara Matter history. Validate sensor values, stream spreadsheet-safe CSV exports, clear unknown Kasa states and remove orphaned query metrics. Generalize optional service templates.

- Add optional read-only Mitsubishi Comfort module: private token-only connection, cloud snapshots, separate opt-in collector, stale/offline handling and removal without deleting recorded history. No HVAC writes, adapter reboots or new dependencies. Physical telemetry validation remains required.

## 2.3.29 - 2026-10-08

- Add dedicated Kasa device controls and explicit circuit associations. Require same-origin confirmation, pinned hardware identity and fresh post-command verification. Failures remain unknown; commands are never retried automatically. Whole-circuit costs are not attributed to individual switches.

## 2.3.28 - 2026-10-08

- Add repeated weekday/hour baselines, sparse-history forecast gates, observed variability envelopes and explicit hourly solar self-use/export scenarios. Require two well-sampled repetitions for each predicted hour and a complete Main profile before modeling whole-panel generation offsets. Unknown export tariffs remain unknown.

## 2.3.27 - 2026-10-08

- Add circuit-by-hour energy heatmap to Trends for the last seven complete days.
  Preserve missing hours, recorded zero values, device boundaries and hover cost/sample details.
  Historical sample counts are not represented as proven coverage or predictive confidence.

## 2.3.26 - 2026-10-07

- Update six vulnerable locked dependencies while preserving Emporia/Cognito/Kasa library versions. Isolated resolution passes dependency checks, all application tests, and a zero-known-advisory scan. Add strict dependency auditing on lock changes, manual runs and weekly CI. Actual deployed polling verification remains a release gate.

## 2.3.25 - 2026-10-07

- Prevent competing continuous and one-shot Emporia pollers on the same canonical database path with nonblocking OS ownership before authentication. Preserve stable owner-only lock files and release ownership on exit. Test cross-process exclusion, aliases, independent databases and failure/crash cleanup. Older poller processes must be stopped explicitly during upgrade.
- Include opt-in core poller and loopback dashboard Linux service templates validated by Linux CI. Actual SER8 deployment remains unverified.

## 2.3.24 - 2026-10-07

- Remove the unverified Bq/m3 unit claim from EcoSense diagnostic candidates; report raw candidate values with explicit unit-verification status. No measurements are automatically ingested.
- Include the opt-in hardened Linux Kasa service template and setup instructions, validated by real systemd-analyze checks in Linux CI. Actual SER8 deployment and hardware remain unverified.

## 2.3.23 - 2026-10-07

- Add private Kasa device registration and a separate bounded read-only collector with recorded query history. Preserve unknown states on failure, pin hardware identity, and remove local device history on explicit removal. Include collector modules in release verification. Automatic service startup, remote replication, physical-device verification and controls remain incomplete.

## 2.3.22 - 2026-10-07

- Replace the disabled Kasa placeholder with a bounded, single-device read-only state probe. Use transient optional credentials, clear them after each request, distinguish OFF from unavailable state, and preserve unknown power telemetry. Pin dependencies and include the adapter/template in release verification. Physical-switch and remote-access tests remain unverified.

## 2.3.21 - 2026-10-07

- Add authenticated radon history replication with ordered updates/deletions, private separate cache tables, explicit cache status, resumable downloads, journal checkpoints and atomic staged recovery. Preserve local collected data and unrelated cache tables. Real EcoQube collection and SER8 deployment remain unverified.
- Publish the native downloader test fixture PID atomically to remove an observed stop/read race.

## 2.3.20 - 2026-10-07

- Reject oversized radon numbers and timezone conversions outside the supported UTC date range as validation errors, preserving atomic ingestion and avoiding HTTP 500 responses.

## 2.3.19 - 2026-10-07

- Scope responsive recommendation/billing grids and wrapping review rows to Reports, preventing mobile overflow exposed by real circuit history. Preserve table-local scrolling.

## 2.3.18 - 2026-10-07

- Add explicit same-window solar offset scenarios in Reports using recorded Main demand, separate avoided usage charges/export credits, unknown compensation handling and capture-quality caveats. Fixed charges and payback are excluded.
- Make Reports overview cards responsive to prevent narrow-window overflow.

## 2.3.17 - 2026-10-07

- Add private local panel-reference photos in Settings with a Panel Editor link, JPEG/PNG upload and deletion. Bound uploads to six images, 10 MiB and 24 megapixels; apply phone orientation, resize and strip metadata. Keep photo files outside Git/releases and protect concurrent upload limits.

## 2.3.16 - 2026-10-07

- Expose normalized, explicitly unverified timestamp candidates in the read-only EcoSense probe. Omit naive and numeric dates instead of guessing; retain secret redaction and no-write behavior. Live collection remains unverified.

## 2.3.15 - 2026-10-07

- Replace only energy cache tables during checkpoint recovery, preserving unrelated sensor history. Verify atomic rollback on replacement failure and radon preservation on successful reset. This does not enable radon replication or live collection.

## 2.3.14 - 2026-10-07

- Keep Trends charts and operational sections inside the page wrapper by removing premature closing tags. Add a rendered-template containment regression test.

## 2.3.13 - 2026-10-07

- Add a loopback-only validated radon observation API for future verified adapters, preserving original units and measurement timestamps with atomic batch rejection and idempotent retries. EcoSense authentication and timestamp semantics remain unverified; no automatic collection is claimed.

## 2.3.12 - 2026-10-07

- Keep Circuits, Panel Editor, Import, Aqara Sensors and Logs inside a responsive Settings workspace shell with selected-page navigation and preserved direct URLs. Separate configuration from analysis links.
- Contain wide breaker configuration fields in a keyboard-focusable scroll region rather than overflowing narrow pages.

## 2.3.11 - 2026-10-07

- Retry failed startup login and device discovery at a bounded interval without deleting saved tokens or falsely reporting missing credentials.

## 2.3.10 - 2026-10-07

- Preserve the physical odd/even side-by-side web panel layout at narrow window widths.
- Persist bounded collector health report history without storing error text; never backfill health events from imported readings.
- Add selected-device recorded capture-quality strips for 48 hours and 7 days to Logs, with accessible hourly details and explicit coverage-versus-uptime limitations.

## 2.3.9 - 2026-10-07

- Allow the panel editor to expand from 16 to 40 slots without a prior reload; save only selected-size rows.
- Preserve labeled, noted, rated, or double-pole unmonitored breaker slots during automatic channel placement. Distinguish unmonitored from empty breakers in web and native panels without fabricated power/load readings.

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
