# UTC migration - issue #127

Status: preflight tooling only. Production still uses legacy naive/local energy
timestamps. Do not convert live data or change the collector timezone yet.
This is not a completed UTC migration, and a clean preflight does not authorize one.

On October 8, the preflight examined all 100,237 readings from the verified
pre-2.3.36 backup. Without declared provenance, every naive timestamp was correctly
unresolved. With an explicit America/New_York assumption, every timestamp had
one valid candidate, with zero projected key collisions or duplicate IDs. The
archived backup and exported input retained their SHA-256 hashes. This evidence
applies to that snapshot, not later readings or unverified original source zones.

## Evidence and interpretation

Emporia poll writes use `datetime.now().isoformat()`. The CSV importer parses
wall-clock buckets but does not persist their source-zone header. The supplied
exports declare `Time Bucket (America/New_York)`; that proves the timezone of
those files, not every row ever imported. `channel_num IS NULL` is not sufficient
provenance: compaction can also remove a channel number from live readings.
The current SER8 host and guest share America/New_York as a temporary mitigation.

`timestamp_model.py` provides explicit interpretation without importing the app
or touching its database. A naive value requires a declared source zone. Each
candidate fold is round-tripped through UTC: zero valid candidates means a gap;
two means an ambiguous repeated time. Neither is silently resolved. An explicit
offset is authoritative and is not reinterpreted in the legacy source zone.
UTC serialization preserves microseconds and uses a single fixed-width format.

Calendar bounds are calculated from successive local dates, then converted to
UTC. A New York spring-transition day has 23 actual hours, and the fall-transition
day has 25. Missing/ambiguous midnight boundaries are rejected, not normalized.
Source: [Python ZoneInfo documentation](https://docs.python.org/3/library/zoneinfo.html).

## Private preflight

Take a verified online backup using `energy.backup_database`. Leave the original
backup and its manifest untouched. Use a separate private working copy for data
inspection through `energy._connect()`; it enables WAL mode. Do not run an app
import or schema initialization against the sole archived backup.

Export every reading from the working copy, within one read transaction, into
owner-only JSON-lines storage outside Git. Include `id`, `timestamp`, `device_gid`
and `channel_name` (including null). Export in ID order and record the snapshot's
row count/hash and last timestamp separately. Do not export credentials, settings,
full device payloads or other unrelated private data. A per-row `source_timezone`
can be supplied only when independently established; explicit null means unknown.

Run the tool from the reviewed source directory:

```bash
umask 077
python3 scripts/audit_timestamps.py \
  --input /private/reading-identities.jsonl \
  --legacy-timezone America/New_York > /private/utc-preflight.json
```

The explicit default is a proposed interpretation, not source provenance. The
tool streams records, retaining reading IDs and projected unique keys to detect
collisions; memory therefore scales with the number of exported readings. It
never opens SQLite, initializes schema, writes input, converts a database or starts
a collector. It counts invalid rows, missing zones, gaps, folds, repeated reading
IDs and projected `(device_gid, UTC timestamp, channel_name)` collisions. Null
channels retain SQLite's existing unique-index semantics. Examples are capped;
counts are not. Malformed input diagnostics do not echo input or private paths.

Exit 0 means no candidate-conversion blockers were found, not that migration is
approved. Exit 2 means unresolved/colliding/empty data. Exit 1 means input or
timezone validation failed. Every report has `migration_ready: false` and lists
the remaining gates. Keep exports and reports private.

## Required implementation before cutover

1. Establish and persist collector reporting timezone and legacy source provenance.
   Preserve original timestamps, IDs, kWh and stored cents in migration evidence.
   Quarantine unresolved rows rather than guessing DST folds or source zones.
2. Change energy poll/heartbeat writes, latest snapshots, CSV imports, retention and
   compaction coherently. Do not mix UTC timestamps with legacy text comparisons.
3. Replace host-local range cutoffs and SQL `strftime` calendar assumptions across
   reports, heatmaps, coverage, patterns and bills. Bin actual UTC instants using
   the explicit reporting zone; distinguish both repeated-hour offsets.
4. Preserve collector identity and sync row IDs. Define journal/generation changes
   and client reset semantics before migration; test cache round-trips and old-client
   behavior. Review radon/Aqara/climate independently; they already use aware times.
5. Rehearse on a private snapshot. Verify row fingerprints and totals, 23/25-hour
   days, both folds, gaps, monthly boundaries and cross-host-timezone behavior.
   Stop only owned writers for the final cutover, verify backup/rollback, then
   confirm real collection, HTTP queries and Mac synchronization before closing #127.

## Reproduction tests

```bash
python3 -m unittest discover -s tests -p test_timestamp_model.py -v
```

Tests cover New York gaps/folds, explicit offsets, a half-hour DST transition,
an entirely skipped calendar day, microseconds, collisions, invalid identities,
empty exports, bounded examples, redacted errors and CLI runs under different
host timezones with no database creation or input-file changes.
