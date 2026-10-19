# Changelog

## Unreleased

- A **Compact layout** switch in the dropdown's More… section (no need to find Preferences); the app watches `ui.json` and re-applies density and dashboard style live when the dropdown or an editor changes it.
- elementary OS integration pass: the client now follows the system light/dark setting (a plain GTK app does not copy it the way Granite apps do, so it stayed light on a dark desktop; confirmed headlessly before and after), the launcher gains Keywords, StartupNotify, notification permission and dock actions (Temperatures, Collector Connection), and an AppStream metainfo file is installed. Both files validate (`desktop-file-validate`, `appstreamcli`).
- The Wingpanel house preview no longer demands 350 px, which was forcing the compact 340 px dropdown wider; a test now rejects any fixed minimum over 300 px in the panel code.
- Dashboard re-skin: bare data tables (such as the Typical Week per-circuit list) get a quiet uppercase header, 12 px cell padding, hairline row separators and tabular figures instead of cramped default styling. Found by rendering the real Trends page with seeded data.
- Production hardening for the Linux client: the installer reports (instead of aborting on) a refused panel install, a test fails the build if the app imports a module the installer does not copy, and the README documents Preferences, the dropdown views, notifications and the install behaviour.
- Compact density makes the dropdown's circuits single-line: in the panel view each breaker is 26 px with name and watts on one line (no rating line), and list rows drop the rating and load bar.
- Narrower Wingpanel dropdown: 440 px (was 520), and 340 px in Compact density; still one width for both views.
- Circuit history window shows a dense second line: peak (with its time), lowest, average and how many buckets were actually recorded; gaps are excluded, never averaged as zero.
- Preferences (Ctrl+, or the menu) for the elementary OS client: **Density** (Comfortable or Compact, a CodexBar-style dense layout for the dashboard, native windows, sidebar and the panel dropdown) and **Dashboard style** (elementary OS native, or the collector's original olive design). Applied as you choose and remembered in `ui.json`. The dropdown also loads the installed `theme.css`, so style changes no longer need a panel reinstall.
- Dashboard type and rhythm in the elementary OS client: 14 px body, 24 px page padding, tighter page and section titles, quieter uppercase card labels, tabular figures in card values and tables, 12 px grid gaps. Client-side style sheet only.
- Keyboard Shortcuts window for the elementary OS client (Ctrl+? or the menu) listing every page and general shortcut.
- Sidebar footer in the elementary OS client: live power (and cost per hour) at the foot of the source list with a live/offline dot; stale or unknown power shows "Offline" with the last recorded time, never the cached watts.
- Wingpanel dropdown: circuits can be shown as the physical breaker panel (default) or as the busiest-first list, remembered per user. The panel puts odd slots on the left and even slots on the right with a centre bus labelled 1–2, 3–4 and so on top to bottom; each breaker shows its name, watts and pole/amp rating with a toggle handle on the bus side (accent when carrying load, amber/red when loaded, grey when idle or offline); a double-pole breaker spans two rows when the slot below it is empty; empty and unmonitored slots are dashed and inert.
- Wingpanel dropdown keeps a single 520 px width in the Power and Temperatures views, so switching no longer resizes or shifts the popover.
- One shared chart for the history and temperature windows: round gridline values with a gutter sized to the labels, first/last time labels, a hover read-out with value and time that stays inside the plot, click selection, per-run fill and line so missing buckets stay gaps (never zero), an accessible summary, and a clear empty state.
- Window shell for the elementary OS client: the window remembers its size, sidebar and last page; Ctrl+1..9 open the sidebar pages in order and Ctrl+K opens a type-to-filter page switcher; F1 and the menu show an About dialog; the sidebar collapses on its own below 760 px and returns when there is room; the embedded view paints the theme's page colour before first paint (no white flash in dark mode); and a desktop notification says when the collector has been offline for 90 s (never during startup, at most once per 30 minutes) and when it is back. Ctrl+2 now opens Reports rather than Temperatures (Ctrl+5).
- Wingpanel dropdown, native layout: a frameless power hero with cost beside it, three stat tiles, and the monitored circuits as one boxed list (busiest first, slot number kept, name, load bar, watts, rating, chevron) in place of the two-column slot grid with placeholder rows; unassigned slots are summarised in one line. Rows are keyboard-activatable with spoken names.
- elementary OS client dashboard re-skin: colours are computed so text, links, status colours and button labels reach 4.5:1 for every accent in light and dark; the collector's dark brand panels (Now panel, breaker panel) become light cards in the light theme and neutral charcoal in the dark theme; unstyled form controls, links, hover/active rows and heatmap labels follow the system theme; reduced-motion and high-contrast preferences are honoured. Browsers and the Mac app are unchanged. Verified by unit tests and, on 2026-10-19, by rendering the collector's real Dashboard, Reports, Trends, Settings and Kasa pages (empty database, Flask 3.1.3, headless GTK) inside the client in the Blueberry light and dark themes: no unreadable text, clipped panels or leftover olive/serif styling was found. Not yet checked: populated data, other accents on real pages, Home 3D, and the live desktop.
- Mac menu-bar popover: system colours, SF type, a vibrant popover material, hairline grouped cards and the user's accent colour replace the olive palette and serif readouts, so it follows light/dark and Increase Contrast like a stock macOS popover. Web dashboard unchanged. Not compiled or run on a Mac yet.
- elementary OS client dashboard re-skin: colours are computed so text, links, status colours and button labels reach 4.5:1 for every accent in light and dark; the collector's dark brand panels (Now panel, breaker panel) become light cards in the light theme and neutral charcoal in the dark theme; unstyled form controls, links, hover/active rows and heatmap labels follow the system theme; reduced-motion and high-contrast preferences are honoured. Browsers and the Mac app are unchanged. Verified by unit tests only; the on-screen render check was not completed.
- Wingpanel dropdown: title over a status line (the live dot is labelled for screen readers and never carries state alone), a visible notice when readings are stale, a dashed muted hero for last-recorded power, 6/12 px spacing, roomier circuit rows with accessible names (name, watts, action), and the stale state reflected in the panel tooltip.
- Temperature window: current sensors are a boxed list with band swatches, room and a large reading; hovering or clicking the chart moves the readout to the nearest recorded bucket.
- Polish the elementary OS client: grouped sidebar (Monitor, Home, Devices, App) with accent selection, connection status as the window subtitle instead of a bottom bar, a clear offline page with Try Again and Collector Connection actions, and quieter circuit rows in the menu, a live/offline dot in the dropdown header (panel icon dims when readings are stale) and a proper elementary-style app icon.
- Native shell for the elementary OS client: a source-list sidebar replaces the web top bar, the embedded dashboard follows the system light/dark palette, accent colour and font through a client-side style sheet, and the connection status moves to a bottom bar. Browsers and the Mac app are unchanged.
- Make the elementary OS client native in look and feel: one stylesheet on the system's named colours (follows light/dark and accent), system type, a Quick Settings-style Power/Temperatures switch, header-bar dialogs with keyboard shortcuts, sectioned app menu, redrawn history and temperature charts, elementary-palette temperature bands. The embedded web dashboard is unchanged.

## 2.3.65 - 2026-10-18

- Show a single floor at full strength in the 3D views: the Floors slider still thins upper floors in Whole home, but a floor chosen on its own keeps opaque floors and full room temperature hues.

## 2.3.64 - 2026-10-18

- Hue the rooms themselves in Home 3D and the Wingpanel 3D view: each room with a recent reading fills with a translucent volume in its temperature band colour (blue <70, yellow 70–75, amber 75–78, red ≥78 °F). Rooms without a recent reading stay clear; upper floors fade with the Floors slider but keep their colour.
- When no sensor has a recent reading, the dropdown shows one stale-collector line instead of one warning per sensor.

## 2.3.63 - 2026-10-18

- Rebuild the Wingpanel Temperatures dropdown natively: an indoor-average overview with warmest/coolest rooms, humidity and a clickable comfort-scale strip; a "needs attention" row for offline sensors, low batteries, fast warming/cooling and humid or dry rooms; and a sortable sensor list (by floor, warmest, coolest, changing fastest) with °F/h trends, humidity, last-update age and hoverable 24-hour sparklines over the comfortable band.
- Remember the Power/Temperatures choice, sort order and expanded sections across refreshes and Wingpanel restarts; the sensor list no longer collapses on each update.
- Show the band legend once; the embedded Home 3D view no longer repeats the summary, legend and sensor list.

## 2.3.62 - 2026-10-18

- Keep live power fresh without double-counting energy already covered by imported intervals; serialize polling and imports.
- Isolate monthly report failures from collection health and mains history, and retry failed reports on the next poll.

## 2.3.61 - 2026-10-18

- Reject overlapping recorded intervals across CSV resolutions and report conflicts in either import order, preserving accepted history.
- Publish complete, private monthly reports atomically; interrupted writes leave no partial final report and retries can succeed.

## 2.3.60 - 2026-10-18

- Preserve imported intervals during compaction and report imports overlapping summarized or unverified history.
- Keep unknown/stale power distinct from verified zero in web panels, cost displays and live updates.
- Restore the selected cached temperature history range while offline; clear unavailable ranges.

## 2.3.59 - 2026-10-18

- Record source and interval metadata for new polls/imports; prevent imports and unknown legacy rows from becoming live power estimates.
- Reject invalid configured rates and cost overflow before poll writes.
- Scope detailed temperature history caches and pending responses to collector identity; clear the window when that identity changes.

## 2.3.58 - 2026-10-18

- Normalize declared CSV source zones to the collector clock and reject ambiguous local keys.
- Serialize settings updates across web requests and Aqara token refreshes.
- Isolate dropdown temperature trends from history-window caches and recover scoped offline history.

## 2.3.57 - 2026-10-18

- Embed the saved house preview directly in the native Linux Temperature dropdown.
- Switch floors without losing selection on refresh; show room temperature hues, sensor
  markers, stairs and attic access. Click sensor markers to open recorded history.
- Keep geometry available offline while withholding stale room temperature hues.

## 2.3.56 - 2026-10-18

- Add room temperature hues and recorded-history timeline with missing-data gaps.
- Add native temperature dropdown cards and a scrollable historical temperature window.
- Support attic service access hatches and sensors mounted at the base of a pole.
- Keep cached/stale temperatures distinct from current observations and preserve mappings.

## 2.3.55 - 2026-10-18

- Stack floor levels at their saved elevations and switch between First floor,
  Upstairs and All floors using the Home 3D dropdown. Keep rooms, sensors and the
  visual editor on the selected floor, with stair treads and an upstairs opening.
- Add Barn floor shortcuts to the Linux app menu and Wingpanel dropdown.
- Preserve sensor identities, circuit mappings and older single-floor plans.

## 2.3.54 - 2026-10-15

- Add a preview-only 24-hour measured mains power chart: separate olive Total,
  blue Leg A and rust Leg B lines, with distinct line patterns and null gaps.
- Read exactly 1440 UTC minute intervals for the active panel, preserving recorded
  zero/negative values and never interpolating or summing Main with its own legs.
- Add accessible local-time minute inspection, responsive in-card scrolling,
  empty/error states and retained historical display during disconnection.
- Test chart segmentation, missing/zero values, bounds, panel isolation and DST;
  no new dependencies or changes to the deployed production instance.

## 2.3.53 - 2026-10-15

- Share bounded actual mains chart collection between the branch poller and local
  preview; backfill up to 24 hours and overlap incremental/recovery requests.
- Store explicit UTC minute intervals idempotently with original usage rates,
  independently of legacy CSV/history compaction and without double-counting Main.
- Show real rolling phase energy/cost totals with observed-minute coverage and
  explicit partial-history labels. Never invent data for missing/null intervals.
- Keep preview writes local and production read-only; handle phase failures without
  rolling back branch readings. Accept current and legacy PyEmVue token-file keys.

## 2.3.52 - 2026-10-15

- Make the isolated preview navigation badge follow the live SER8 feed instead of
  the copied local history's age. Browser fetch failures also clear feed connectivity.
  Normal production navigation and collection are unchanged.
- Include the preview mains reader in source release archives; release validation
  rejects bundles missing the new module.

## 2.3.51 - 2026-10-15

- Diagnose stale mains legs: usage-list APIs omit individual phase channels, leaving
  CSV-imported readings untouched while aggregate Main continues updating.
- Add an opt-in, read-only phase-chart supplement to the isolated preview using
  the existing remote virtualenv; credentials stay on SER8 with no token-file writes.
- Display actual timestamped Main/Leg A/B one-minute averages, respect no-data C,
  throttle requests, and reject stale/malformed/cross-panel responses. No fabricated
  splits or rolling leg totals; production and local floor plans stay unchanged.

## 2.3.50 - 2026-10-15

- Refresh live circuits, service mains and rolling 24-hour energy/cost aggregates
  from the same read-only SER8 database transaction as environmental sensors.
- Project source-panel readings onto local rooms without importing remote floor
  plans, settings or placements. Display measured mains legs separately from Total.
- Require fresh source heartbeat, measurement age and transport for live watt
  estimates; stale/missing readings stay unknown and recorded zero stays zero.
- Support alternate SSH addresses with the existing verified host-key alias;
  no production deployment, new production service or authentication change.

## 2.3.49 - 2026-10-15

- Add an opt-in local Home 3D preview runner with read-only SER8 sensor refresh
  every 30 seconds, isolated from production deployment and device controls.
- Preserve local floor plans and placements; atomically replace only observation
  caches, retain measurement times, and mark transport failures/stopped feeds stale.
- Require an explicit preview marker and DB path; use noninteractive verified SSH
  and read-only SQLite on SER8. Reconnect automatically after transient failures.

## 2.3.48 - 2026-10-15

- Match furniture dimension controls to the pinned renderer's supported fields
  and defaults; remove misleading controls that could not affect the 3D model.

## 2.3.47 - 2026-10-15

- Allow Home 3D selectors to shrink within their labels, fixing horizontal
  overflow when the visual editor is expanded on narrow mobile screens.

## 2.3.46 - 2026-10-15

- Place recorded Aqara, climate and EcoQube sensors in the visual floor plan;
  drag markers, change room/coordinates, undo/redo, and save privately. Room moves
  carry sensors with them; resizing keeps placements inside the room.
- Show sensor markers in 3D and accessible observation cards with Fahrenheit,
  humidity, radon and battery values. Preserve zeros and mark stale/missing data.
- Validate namespaced sensor identities and in-room coordinates. Older mapping
  clients preserve placements; disappeared sensors remain removable. No device
  discovery, new collectors or device-control commands are introduced.

## 2.3.45 - 2026-10-15

- Add a browser floor-plan editor with room drawing, snapped dragging, resizing,
  furniture/opening placement, keyboard fields, and undo/redo. Saved geometry
  updates the 3D view; drafts warn before navigation and remain local until saved.
- Preserve circuit mappings for retained room IDs; confirm room deletion and
  remove only mappings to deleted rooms on save. Revision checks still protect
  concurrent edits.

## 2.3.44 - 2026-10-15

- Add a Home 3D preview using pinned MIT-licensed house-control floor-plan and
  furniture renderers, bundled locally with Three.js for Mac/Linux browsers.
- Import/export geometry and persist explicit panel/circuit-to-room mappings.
  Show existing power estimates and recorded 24-hour usage/cost without splitting
  shared circuits, double-counting two-pole slots, or showing missing data as zero.
- Keep mappings private, bound imports, and reject stale concurrent saves.
  No additional collectors, vendor logins, device commands, or AI gateways start.

## 2.3.43 - 2026-10-12

- Add opt-in trusted-proxy support for one explicitly configured HTTPS public
  origin. Flask remains loopback-only; local-only behavior remains the default.
- Preserve strict same-origin mutation checks behind the proxy and reject
  untrusted hosts, insecure forwarded schemes, and malformed public origins.
- Public deployment still requires an authenticated reverse proxy and a
  separate allowlisted OAuth session; the application does not expose itself.

## 2.3.42 - 2026-10-09

- Choose strongly separated initial history colors rather than hashing two
  HVAC units into similar blue hues. Add distinct dashed trace patterns so
  exact overlaps reveal underlying series; legend selection still isolates units.

## 2.3.41 - 2026-10-09

- Add Kasa and Mitsubishi comparison histories for 4 hours, 24 hours, 7, 14 and
  30 days, with colored device overlays, selectable metrics, prior windows,
  accessible tables and CSV exports. Aggregate in SQLite into bounded buckets.
- Show Comfort temperatures in Fahrenheit by default, heating/cooling setpoints,
  mode history and vendor schedule/adapter diagnostics from verified API fields.
- Distinguish missing data from zero; estimate enabled time only across adjacent
  successful observations within three minutes. Do not infer HVAC power or
  compressor runtime, or invent historical observations before collection began.

## 2.3.40 - 2026-10-09

- Add a private systemd update-check timer for Linux collectors. Compare the
  deployed HTTP version against both stable GitHub releases and immutable merged
  main source, so an older published release does not hide newer source updates.
- Persist bounded, read-only check results and explicit failed/partial states;
  run twice daily with missed-check recovery after restart. Updates are reported
  without changing application code or collector data automatically.
- Install and verify the checker in SER8's existing energy-monitor container.

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
