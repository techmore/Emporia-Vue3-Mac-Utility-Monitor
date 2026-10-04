# Refinement and maintenance roadmap

Preserve the 2.0.1 tag as the stable baseline. Use small branches with behavior
checks and rollback notes. Do not bundle a structural rewrite with new analytics.

## 1. Trustworthy data and panel setup

- Store reading source, interval, device time/zone and import batch. Define overlap
  precedence for live samples and CSV buckets, then migrate with a DB backup.
- Calculate coverage before projecting costs; show “collecting history” or missing
  periods instead of extrapolating a partial day as a complete day.
- Add active-device selection and guided channel-to-slot mapping. Require explicit
  breaker rating/poles for rating indicators. Keep inferred legs visibly estimated.
- Acceptance: mixed-duration/import/live tests; gaps cannot appear as zero usage;
  two-pole and unknown-rating fixtures; reversible schema migration.

## 2. Organize the code around existing boundaries

- Keep public `energy` functions as compatibility adapters while extracting
  `storage`, `queries`, `imports`, `polling` and `settings` in separate steps.
- Move web templates to `templates/` and CSS/JS to `static/`, preserving palette and
  DOM IDs. Introduce route blueprints for dashboard, settings, imports and devices.
- Expand `panel_model.py` as the single panel calculation boundary. Keep pure
  calculations independent of Flask/database concerns.
- Split Swift into project configuration, process supervisor and menu status.
- Acceptance: same route and API contracts, preserved panel invariants, portable
  release smoke test, measured dashboard response time before/after.

## 3. UI polish and complete live behavior

- One payload for panel, banner, cards and alternate views; update charts with new
  samples. Show last poll time, measurement interval and data quality consistently.
- Simplify Settings into Account, Panel, Costs and Operations; visually separate
  future integrations from usable features. Make notes available by click/keyboard.
- Refine small text, contrast, view-switch states and mobile editor layout; add
  configurable weather location and clear external-service failure states.
- Acceptance: desktop and 390px checks, keyboard flow and VoiceOver names, no
  horizontal document overflow, consistent values after SSE updates.

## 4. Operations and distribution

- Pick one poller supervisor; prevent duplicate polling with a process-level lock.
  Graceful stop, restart backoff, structured logs and bounded log retention.
- Keychain-backed credentials; serialized settings updates; explicit database
  initialization/migrations; configurable device exclusions.
- Local chart/font assets, CSP, dependency audit, packaging for Intel if needed,
  then signing/notarization and a self-contained install experience.
- Acceptance: launch/relaunch/quit, cloud outage/recovery, disk write failure,
  reboot/login-agent ownership, package install on a clean machine.

## Routine release checklist

1. Back up the runtime database outside the package. Run tests in a temporary DB.
2. Update `VERSION`, bundle plist and XcodeGen metadata; run `scripts/check_release.py`.
3. Run syntax checks, full tests, native compile and `release.sh`.
4. Extract into a clean directory; verify manifest, required paths, no secrets or
   local paths, route smoke tests, and archive checksum.
5. Record evidence in changelog; commit and annotate the release tag. Publish only
   the tested archive. Keep credentials/database out of Git and releases.
6. For each following release, review dependency updates and audit findings; require
   tests for data conversions, billing changes and process ownership changes.
