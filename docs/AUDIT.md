# Code and UI audit — 2026-10-04

## Verification Update — 2026-10-07

### Current Installed Release: 2.3.26

The installed Homebrew release is 2.3.26, not the earlier milestones listed
below. PR #116 and all six checks passed before publication and deployment.
The installed suite passed 170 tests; offline wheelhouse installation and
dependency compatibility checks passed. A strict scan of the final locked
dependencies reported zero known advisory matches at verification time. This
is not a penetration test or a guarantee against future vulnerabilities.

Actual Emporia readings advanced across two subsequent polling intervals after
startup. Exactly one native app and app-owned poller were observed; settings
checksum remained unchanged and the pre-upgrade database backup passed integrity
checks. PR #114's duplicate-poller protection was verified in installed 2.3.25:
a second poll command failed while the active poller held database ownership.
Older poller versions and separate-host collectors do not share that safeguard.

Radon storage, history display and replication code exist, but the production
local/cached radon tables are empty. Kasa read-only registration/collection code
exists, but no devices are registered. Aqara is not configured in runtime settings.
No actual EcoQube, Kasa or Aqara connection is claimed by fixture tests.
Linux collector unit templates passed real systemd syntax validation in CI;
they have not been installed or reboot-tested on the SER8. The current keychain
has an Apple Development identity, not Developer ID Application distribution
signing. Signing/notarization and clean-machine distribution remain unverified.

The earlier milestone notes below are historical verification records, not
current pending-deployment claims.

The findings below describe the historical 2.0.1 baseline, not the current release.
Subsequent verified changes include:

- Installed 2.3.10: physical odd/even panel columns remain side by side at narrow
  widths. The production right-column reversal setting was disabled at the user's
  request. Dashboard and Circuits showed 1/2, 3/4, 5/6; the native API returned
  ascending slot order. Unmonitored breakers are distinct from empty slots.
- Installed 2.3.10: Logs renders 48-hour and seven-day capture-quality strips.
  Collector health reports are persisted separately. Missing samples are not
  represented as proven downtime; imported history does not fabricate health events.
- Released 2.3.11: startup login/discovery retry regression tests cover timeouts,
  empty discovery, repeated failures, and token preservation. Installation and
  production recovery verification remain pending; release publication alone is
  not evidence that the running collector uses this fix.
- Proposed 2.3.12 (PR #95): shared Settings workspace navigation preserves direct
  URLs. Browser checks covered Settings, Circuits, Panel Editor, Import, Aqara and
  Logs at 390px. Panel Editor's wide fields scroll within a focusable region rather
  than overflowing the document. The full suite passed 110 tests before final
  release-note and assertion-only changes; deployment is pending.

SER8 cutover, actual EcoQube/Aqara/Mitsubishi/Kasa telemetry and control, reboot
recovery, comprehensive VoiceOver testing, signing/notarization, and a complete
dependency/security assessment remain unverified. These improvements do not close
the entire application audit.

## Historical Baseline

Scope: Python data/poller/server code, native menu app, build/install/release scripts,
CI, and the running Dashboard and Settings flows. This is a local source and UI
review, not a penetration test or electrical assessment. Baseline release: 2.0.1.

## Release blockers corrected

| Finding | Evidence before correction | Resolution |
| --- | --- | --- |
| Release not portable | `release.sh` read the older checkout, flattened Sources/Resources, and copied local launch pointers | Resolve current repo, preserve directories, strip machine pointers, check versions and manifest |
| Dynamic HTML injection | Circuit names and CSV filenames/messages entered JavaScript `innerHTML` without escaping | Escape dynamic text; server-rendered panel fragments retain Jinja autoescaping |
| Local mutation exposure | `force=True` accepted simple text bodies; no origin/host guard; host could be overridden | Require JSON objects, validate loopback host and same origin, force loopback binding |
| Runtime file corruption | Settings and heartbeat JSON were overwritten in place | Atomic replace with owner-only temporary files; preserve original on failure |
| Partial panel updates | Slots were committed individually before later inputs were validated | Validate complete request, save slots in one database transaction |
| Contradictory projections | Initial banner used 60-minute extrapolation while ring and SSE used recorded 24h totals | Use one projection calculation and update both displays together; disclose partial history |
| Panel not live | SSE updated banner and top list, leaving service and breaker values unchanged | Refresh escaped panel fragment with each new poll |
| Unknown ratings represented as known | Missing amps were displayed as 15A; capacity column called “Safe” | Shared model treats ratings as unknown; column named “Rating”; no invented capacity warning |
| Cache could retain configuration/status | Cache keyed only on latest poll/device | Invalidate successful writes; cap cache lifetime at 15 seconds |
| Menu lifecycle and URLs | URL copied LAN address despite loopback binding; app did not start polling; PID file lock raced | Correct loopback URL, direct-launch poller, OS instance lock, asynchronous power/health display |
| Build accepted unrelated service | Any HTTP response counted as readiness | Port ownership preflight and validated version response |
| Test expired | A 24h-window test used a fixed March timestamp | Relative fixture timestamp |
| Accessibility and copy | Icon view controls lacked explicit names/state; dark ring text; weather called Trend; rate showed float noise | Accessible names/pressed state, light ring text, Weather label, two-decimal rate, accurate save help |
| Missing page structure | Pages lacked document language, title and mobile viewport | Shared document wrapper and basic narrow-screen styles |

## Remaining findings and limits

1. **P1 — history and units:** readings lack source and interval metadata. Historical
   exports and live minute samples can overlap or be averaged as if they have the
   same duration. Monthly estimates use recorded data and remain provisional with
   sparse history. Add provenance, coverage and unit-aware aggregation before
   changing billing logic. Do not infer full coverage solely from row count.
2. **P1 — panel semantics:** API channel numbers and inferred leg assignments need
   explicit mapping to physical slots. The app cannot confirm installed amp ratings,
   pole counts or actual leg topology. Inferred leg balance is labelled estimated;
   unconfigured breaker ratings remain unknown. Do not auto-fill physical ratings.
3. **P2 — lifecycle:** build uses forceful termination; native Flask probes still
   accept generic HTTP responses. Separate UI/poller LaunchAgents can race the
   menu-owned poller at login. Adopt one explicit supervisor/ownership model and
   graceful shutdown. Direct-launch startup was verified; simultaneous login
   agents and reboot recovery were not exercised.
4. **P2 — stale secondary displays:** panel, banner, budget and top list update, but
   charts, 24h/MTD cost cards and alternate grid/bar views are snapshots until reload.
   Extend one typed live payload to all displayed values.
5. **P2 — storage and settings:** atomic replacement prevents torn JSON but does
   not serialize concurrent read-modify-write operations. Plaintext account secrets
   are owner-only files; migrate to macOS Keychain. Import-time DB creation,
   hardcoded ghost-device/migration IDs and broadly swallowed exceptions remain.
6. **P2 — UI depth:** desktop is the primary verified flow. Small captions, dense
   panel labels, hover-only notes, long Settings integration list and fixed weather
   location need refinement. Basic mobile viewport/reflow is included, but 390px emulation still reported
   a scaled wide layout (1182px). Responsive layout, mobile panel editor and
   complete keyboard/VoiceOver workflows need a dedicated pass.
7. **P2 — dependencies and security:** charts/fonts rely on remote CDNs. Vendor
   assets, add a CSP after inline scripts are extracted, and schedule dependency
   review. No dependency vulnerability scan or full auth/API fault matrix was run.
8. **P2 — native distribution:** release is unsigned, Apple Silicon only, and needs
   Python/venv/project sources. Signed/notarized standalone distribution is future
   work. Embedded paths are excluded from the archive.
9. **P3 — privacy and structure:** the repository already tracks historical CSV
   exports. New packages exclude them, but Git history still contains that data.
   `web.py` remains approximately 5,000 lines with inline CSS/JS/templates. Split
   by stable boundaries rather than a full rewrite.

## Validation

26 tests passed. Native Swift compilation and shell/Python syntax checks passed.
A fresh archive extraction passed 12 route smoke checks, version metadata checks,
all file-manifest hashes, runtime-file exclusions and the archive checksum. Browser evidence
confirmed equal banner/ring projections, readable ring text, unknown-rating labels,
weather naming, and a rate of `13.75`. UI screenshots and the pre-update database
snapshot stay local under ignored `dist/`. The after-change screenshot capture
initially timed out; semantic/browser-state checks were used for that round.

## Release boundary

2.0.1 freezes the audit fixes and menu-monitor enhancements. Future refinements
start on a separate branch. Release archives contain a SHA-256 file manifest and
an archive checksum. A Git tag pins source, not the mutable database or credentials.
Remote publication is a separate action from local packaging/tagging.
