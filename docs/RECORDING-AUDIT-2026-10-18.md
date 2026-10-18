# Recording and house-control audit — 2026-10-18

ENERGY AND TEMPERATURE RECORDING / HOUSE-CONTROL AUDIT
2026-10-18, America/New_York

Scope: deployed SER8 recording services and read-only SQL aggregates; energy,
climate, Aqara/Matter adapters; draft Home 3D PR #150 plus current main;
vendored house-control renderer and frontend assets. No reboot, device pairing,
or actuator commands were performed. No historical measurements were rewritten.

1. HIGH: Temperature recording silently stopped for about 69.5 hours.
   Last original observation: October 15 at 10:02:05 EDT. The user Matter
   service started before wg0 existed after boot. Node logged a fatal mDNS
   initialization error, remained alive and never opened port 5580. systemd's
   Restart=on-failure therefore did nothing. The collector repeatedly failed
   WebSocket connections, while its process also remained 'running'.
   Recovered the existing controller without deleting/recommissioning its
   Matter fabric. Six sensor observations resumed October 18 at 07:28:57 EDT;
   subsequent individual temperature/humidity reports and snapshots advanced.
   Added interface precondition, bounded listener readiness, startup retry,
   and a shorter stop deadline. Installed readiness guard on SER8 and verified
   the live listener. Future whole-machine reboot remains untested.
   The missing historical temperature interval cannot be reconstructed from
   cached values and was not filled in.

2. HIGH: One sensor's event refreshed all sensors' observation timestamps.
   record_node decoded and inserted every bridged child for each attribute
   update. A report from one endpoint therefore made other sensors look
   recently observed and overweighted their unchanged values in aggregates.
   Attribute reports now select only the sensor owning that endpoint; whole
   node messages are explicitly snapshots. Regression test proves an unrelated
   sensor retains its previous timestamp. Deployed this collector correction.
   Cached snapshots remain observations of controller state: the physical
   sensor's measurement time is unknown. Home 3D now states that explicitly.

3. HIGH: Freshness checks discarded timezone offsets.
   _reading_fresh sliced timestamps to 19 characters and compared them with
   local time. A correct UTC phase sample could appear hours in the future.
   Fixed aware comparisons in UTC and kept legacy naive timestamps local.
   Tests cover UTC, negative and positive offsets, stale and malformed values.
   This correction is in the reviewed integration code, not yet deployed as
   a full new dashboard release.

4. HIGH / ACCOUNTING LIMIT: Legacy power rows are poll observations, not a
   continuous, source-timestamped meter ledger.
   The live database initially contained 357,877 readings and no mains_intervals
   table. In the exact last-24-hour audit, the active Main channel had 1,433
   observations; adjacent intervals were 60.19-65.59 seconds, with no >120s gap.
   Those counts cannot be called measured-minute coverage or corrected by
   multiplying totals by a guessed factor. Poll timestamps are collector time;
   old import/compaction rows share the same legacy table. The mirror/ghost panel
   is not an additional household meter and must not be summed with the active one.
   Draft PR #150 already adds independent UTC mains_intervals with idempotent
   keys, late revisions, zero preservation, missing-minute gaps, retained rates,
   active-panel filtering, and bounded cloud backfill. Existing tests exercise
   exact 24-hour windows across DST and reject incomplete/future intervals.
   Audit implementation now exposes those measured mains series and coverage
   in the normal Home 3D endpoint, which previously existed only in its preview.
   Legacy circuit room totals are explicitly labeled poll-observation sums;
   no exact historical completeness, HVAC allocation or measured room energy
   is inferred. Continuous per-circuit source-interval collection remains future work.

5. MEDIUM: Invalid cloud numbers could silently corrupt a poll batch.
   Booleans, NaN, infinity or strings were not validated before SQL writes.
   Validate every channel before any mutation, fail atomically, and always close
   the SQLite connection on errors. Regression tests keep the database empty
   when a later channel in the batch is invalid.

6. MEDIUM: One malformed Home Assistant timestamp aborted the adapter batch.
   A non-string last_updated raised AttributeError outside the expected parsing
   failures. Skip that malformed state while retaining valid selected readings;
   the test includes a legitimate zero-degree observation.

7. MEDIUM: Mains cloud requests followed redirects by default.
   Disable redirects and reject 3xx responses explicitly. The bearer token is
   never forwarded by following a redirect. Tests assert this network policy.

HOUSE-CONTROL IMPLEMENTATION REVIEW
- Reuses a bounded MIT renderer, not the upstream native shell, provider account
  clients, device commands, scenes or AI/MCP control bridge.
- All five imported files match the recorded SHA-256 and exact upstream commit
  1ad70a26197c0503b5af7932f0b5c8a8d3c7a6f9. Upstream HEAD inspected:
  88c84cca77fc12f9297dc91ba28ce58bc9f316d7. Renderer subset has no diff.
- Source-qualified sensors, explicit panel/circuit bindings, private atomic saves,
  revision conflicts, bounded geometry and same-origin JSON writes retained.
- Added explicit rejection of a supplied cross-panel summary.
- No hidden device or vendor account connection is created by rendering a plan.
- Temperature history in climate_readings was empty; the actual local Matter
  observations were in aqara_local_observations (94,032 rows at audit start).
  An empty climate table does not mean Matter logging was never configured.
- Aqara averages are averages of online collector observations, not independent
  physical measurement coverage or a calibrated time-weighted thermal model.
- Radon continued recording during the temperature outage; streams must be
  evaluated independently. A healthy power heartbeat cannot prove sensor health.

VALIDATION
- 373 Python tests pass, six platform-dependent tests skipped.
- Regression cases: offsets, per-sensor event attribution, nonfinite/boolean
  power, malformed HA time, cross-panel rejection, cloud redirects, readiness,
  and regular-endpoint measured history with explicit null gaps.
- Frontend production build, editor/sensor tests and mains-chart tests pass.
- npm reports zero known advisories in this locked frontend dependency set.
- Imported upstream hashes and MIT provenance verified against Git objects.
- Browser QA in a separate synthetic preview confirms zero temperature is 32 F,
  1/1440 coverage is partial, missing phases remain missing, mains are not stacked,
  and legacy circuit accounting is visibly distinguished.
- Existing user preview and floor-plan drafts were not changed. Production
  remains 2.3.43 with the scoped Matter recovery fixes; full 3D deployment and
  a real reboot test are not claimed.
