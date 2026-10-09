# Changelog

## 2.3.49 - Unreleased

- Resolve new CSV imports to canonical cloud monitor IDs using poller discovery,
  not filename prefixes or display names. Preserve parent/nested channels without
  mutating SDK objects; retain immutable discovery claims and per-source bindings.
- Add explicit discovered-monitor selection to Import. Reject unknown/ambiguous
  auto-matches, unregistered HTTP selections and contradictions before publication.
  Keep canonical live/CSV overlap guards active; existing split history requires
  reviewed reconciliation rather than a silent alias or rewrite.
- Verify actual Flask imports, scoped history, rendered submit-handler selection,
  source immutability, collisions, idempotence, upgrade preservation and rollback.
  Package the identity module for native and Linux/Incus/LXD source deployments.
  Legacy reconciliation and production acceptance remain open in #140/#127/#135;
  this draft does not change the running application or deploy a new collector.

## 2.3.48 - Unreleased

- Add strict read-only completed-chart evidence validation and a private offline
  audit CLI. Require explicit request/receipt timestamps and the returned server
  anchor; exclude inclusive API end buckets and unsettled/partial intervals.
  Preserve null gaps, signed energy and zero; reject malformed values and units.
- Real read-only probes exposed different live/chart timestamp semantics and an
  inclusive chart end bucket. Do not guess a universal previous-minute shift or
  substitute the SDK's requested-start fallback for missing source provenance.
- Keep production collection unchanged. General raw-source ledger integration,
  completed history publication, CSV/live reconciliation and UTC cutover remain
  open in #127/#135. No app installation, production mutation or deployment.

## 2.3.47 - Unreleased

- Retain immutable original CSV bytes, hashes, headers, cells, source-local/UTC
  instants, interval bounds and import-time pricing in a collector-side ledger.
  Project only non-overlapping verified CSV intervals into readings; maximize
  covered elapsed time before preferring finer resolution. Keep coarse energy
  when finer coverage is incomplete; never invent a prorated remainder.
- Preserve accepted values on conflicting sources. Quarantine crossing coverage,
  unmanaged overlaps and unknown bounds with explicit review warnings. Publish
  source evidence, owned projection replacements, journal and snapshots atomically;
  preserve surviving reading IDs and sync identity. Order journal replacements so
  even single-event cache pages never mix superseded and replacement CSV coverage.
  An immutable source sequence preserves precedence through database maintenance.
  Retention cleans membership without resurrecting unrelated archives; explicit
  re-import may restore evidence.
- Display review and projection counts in Import; distinguish retained source
  observations from rows contributing to totals. Verify exhaustive interval
  selection, actual import/history/cache paths, failure rollback, exact evidence
  and private persisted-policy UTC conversion. Source archives remain private and
  are not automatically pruned or sent to clients.
- Historical reconciliation, subsequently collected live overlap and operational
  UTC cutover remain unresolved. #127/#135 stay open; this draft is not deployed.

## 2.3.46 - Unreleased

- Reject HTTP failures and malformed success counts in the CSV upload UI before
  displaying a green result. Preserve safe validation/size/origin error messages,
  HTTP status for non-JSON proxy failures, and partial-import counts. A lost
  request reports an unknown import outcome rather than claiming rollback.
- Execute the actual rendered submit handler against real Flask error and partial
  import responses. Verify escaping, per-file continuation and restored controls.
- Preserve `.gitignore` in portable source packages and require it during release
  validation, so packaged runtime-exclusion tests and future Git checkouts retain
  the same private-file safeguards. Run the full suite from the extracted release
  ZIP in CI. No production deployment, historical overlap repair or UTC activation;
  #127 and #135 remain open.

## 2.3.45 - Unreleased

- Make poll receipts, latest snapshots, journal replicas, capability timestamps,
  migration markers and retention bounds follow persisted energy clock policy.
  Capture one poll instant and hold the publication transaction while reading
  policy. Heartbeat files use aware UTC; legacy health rows retain local semantics.
- Convert UTC CSV imports using their declared source zone, never the laptop or
  reporting zone. Reject missing source zones before publication, keep gaps/folds
  unresolved, preserve actual daily duration, and retain legacy import timestamps.
  Validate UTC snapshot stamps and close/rollback failed capability publications.
- Rotate the energy stream generation atomically with private UTC conversion,
  including already-aware inputs. Record old/new generations, preserve collector
  identity, reading IDs, original journal sequences, raw energy/cost and unrelated
  streams. Verify automatic HTTP replacement of a real legacy cache; failed
  generation changes roll back timestamps, evidence and policy.
- Add 20 writer/rollback regressions, including real persisted-policy writes on a
  disposable converted fixture and cross-host timezone subprocesses. Ordinary
  writable UTC connections remain blocked; activation/cutover and historical
  overlap reconciliation are unfinished. #127/#135 stay open. Not deployed.

## 2.3.44 - Unreleased

- Negotiate energy sync protocol 3 with explicit timestamp/reporting-zone and
  measurement contracts. Preserve protocol 2 for legacy collectors; reject old
  clients before sending UTC rows. Validate canonical UTC timestamps, reject
  silent format downgrades and isolate UTC rows from older native cache readers.
- Guard snapshot publication against concurrent cache progress. Publish cache
  rows, format and generation atomically without replacing unrelated sensor data.
- Read UTC offline history using the collector reporting zone, actual elapsed
  windows and offset-bearing fold labels. Preserve microsecond bounds, recorded
  zeroes and missing bins; omit nonexistent spring hours and clip half-hour
  transition bins. Fail closed on invalid policies and partial caches.
- Private converted-artifact HTTP/download/native tests pass; ordinary live UTC
  connections remain blocked. No production migration or deployment. #127/#135
  stay open pending writer/import coordination and historical reconciliation.

## 2.3.43 - Unreleased

- Restrict compaction to compatible evidenced Emporia minute-only groups. Preserve
  unknown/imported intervals, isolated samples, mixed channel/source identities and
  repeated provider instants (including equivalent offsets). Keep aggregate duration
  unknown, preserve signed energy/stored costs and an agreed source zone. Do not erase
  colliding CSV buckets or imply complete hourly capture.
- Protect compaction deletes, replacement inserts and journal events with a savepoint
  while retaining caller transaction ownership. Qualify scratch-table operations with
  `temp` so maintenance cannot drop a permanent same-named table. Use canonical hour
  keys and elapsed retention bounds in private UTC-clock tests; ordinary UTC activation
  remains blocked. Add rollback, collision, cache and fold-boundary regressions.
- Development only: does not repair previously corrupted/overlapping history or
  complete UTC writer/client coordination. #135/#127 remain open; not deployed.

## 2.3.42 - Unreleased

- Derive live power only from fresh evidenced Emporia minute observations, checking
  both receipt and provider age. Preserve unknown power across dashboard events,
  circuit previews, menu summaries and breaker ratings; keep recorded zero distinct.
  Imported hourly averages are labeled historical, not live. Peak monitored averages
  require aligned duration/source evidence rather than multiplying every kWh by 60,000.
  Missing native legs cannot manufacture a total; recorded Main zero is authoritative.
  Unknown/stale/imported readings cannot enter standby lists or power-based safety alerts.
- Give the thin Today/Yesterday charts independent sizing containers to prevent
  Chart.js from including row-label width and overflowing the banner on mobile.
- Development only: historical overlap, sync capability negotiation and coordinated
  UTC writers remain unfinished. No production reprice, migration or deployment;
  #135 and #127 stay open.

## 2.3.41 - Unreleased

- Persist per-reading duration, measurement source, declared source timezone and
  provider observation timestamp through CSV/minute polling, latest snapshots,
  reading journals and Python/native-runner sync caches. Preserve unknown legacy
  evidence and raw energy/cost; aggregate compaction cannot invent a full hour of
  capture. Add an evidence-only average-power function and transactional poll
  cleanup. CSV source zones take precedence over declared fallback assumptions in
  private UTC conversion, without reinterpreting local poll receipt timestamps.
  Dashboard power consumers, mixed-resolution overlap, cache capability
  negotiation and coordinated UTC writers remain unfinished; #135/#127 stay open.

- UTC integration in development: transactional conversion rehearsal on a new
  private copy of a verified backup. Preserve original timestamp evidence,
  non-time data, IDs and journal sequences; verify generated canonical upserts.
  Reject the resulting artifact from ordinary app connections until the UTC
  writer/query/import/client integration is complete. Add byte-preserving,
  read-only maintenance connections. Not deployed; #127 remains open.
- Preserve timezone offsets and microseconds in dashboard/native-menu freshness
  and poller readiness checks. Reject malformed and excessively future-dated
  timestamps; retain legacy local interpretation until storage cutover.
- Persist the rehearsal's explicit UTC/reporting-zone policy and integrate it into
  circuit calendar totals, monthly stored costs, week comparisons, heatmaps and
  repeated-week baselines. Render actual transition-day column counts and retain
  fold offsets; do not count duplicate/fold observations as independent weeks.
  Live activation remains blocked pending the remaining coordinated UTC paths.
- Restore readable light text on the heatmap's dark sticky circuit labels while
  preserving the olive/stone theme and contained horizontal scrolling.
- Continue UTC query integration: elapsed-time rolling totals, hourly/daily and
  circuit history, reporting-month comparison/projection and observed-day trends.
  Exclude future rows; retain stored cents, fold offsets and actual interval
  lengths. Fill chart gaps along real UTC bins, not nonexistent wall hours, and
  calculate trend slope across actual calendar-day gaps. These adapters remain
  read-only rehearsal work; remaining writers/client paths still block cutover.
- Integrate recorded context, intraday comparisons, capture history and peak-time
  labels with the reporting zone. Keep repeated hours distinct, missing data null,
  and partial capture bins duration-weighted. Use consistent read snapshots and
  device-scoped circuit comparisons; do not double-count adjacent boundaries.
- Remove duplicated dashboard sections that made circuit detail return HTTP 500;
  retain its own chart and comparison cards. Correct polls/hour and recorded-zero
  comparison display. The legacy-compatible fix is merged separately in 2.3.38 (PR #137).
- Track measurement-interval/provenance repair in #135: hourly imported/compacted
  energy must not be presented as minute-average watts. UTC time-label tests do
  not establish power accuracy; this remains a release/cutover gate.

## 2.3.39 - 2026-10-08

- Retire the unproven startup divide-by-60 correction; preserve historical values
  and migration evidence. Verified original exports are required for any repair.
- Validate CSV units, filename intervals and declared zones before writing. Reject
  unsupported power durations and nonfinite values; report ambiguous/gap timestamps
  without guessing. Daily power conversion uses actual source-zone day duration.
- Publish imported readings, journal changes, accepted-row snapshots and device
  capabilities atomically. Ignored duplicate uploads cannot replace live snapshots;
  failed publication rolls back the entire import and closes its connection.
- Return useful HTTP 400 validation errors and generic HTTP 500 publication errors.
  Document import/reconciliation gates. Interval-aware power modeling remains #135;
  UTC migration remains #127. No historical repair or schema change is performed.

## 2.3.38 - 2026-10-08

- Restore full circuit-detail pages and every period tab by removing copied
  dashboard sections that referenced missing template values and returned HTTP 500.
- Scope circuit charts and context comparisons to the same selected device.
  Read comparison windows from one snapshot, retain recorded zeroes, and correct
  the polls/hour label. Add actual route regression tests, including empty data
  and escaped circuit names. No timestamp migration or database schema change.

## 2.3.37 - 2026-10-08

- Add a read-only UTC migration preflight for private exported reading identities.
  Require explicit legacy source-zone assumptions, detect DST gaps/folds and
  projected key collisions, and preserve microseconds. Test actual 23/25-hour
  calendar bounds and fail closed on ambiguous boundaries. Include the tool and
  migration gates in native/container source releases. Production energy storage
  remains unchanged; this does not complete or automatically authorize #127.

## 2.3.36 - 2026-10-08

- Add an optional recorded trend line to EcoQube history, retaining every raw
  measurement dot and source timestamp. Break the line at gaps longer than three
  hours; isolated samples remain dots. Preserve sensor/window/toggle selection
  without writing readings or averaging, forecasting or classifying safety.

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
