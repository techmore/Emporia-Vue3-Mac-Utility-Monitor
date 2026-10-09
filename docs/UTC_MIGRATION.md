# UTC migration - issue #127

Status: preflight tooling in production; transactional rehearsal and readiness
compatibility and initial calendar integration in development. Production still uses legacy naive/local energy
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

Development work on `codex/utc-migration-integration` adds a transactional
conversion rehearsal. It does **not** enable UTC production storage. Run it only
from that reviewed development checkout, with the locked Python requirements:

```bash
umask 077
mkdir -m 700 /private/utc-rehearsal
venv/bin/python3 scripts/rehearse_utc_migration.py \
  --snapshot /private/verified-archive.db \
  --destination /private/utc-rehearsal/converted.utc-rehearsal.db \
  --expected-sha256 VERIFIED_ARCHIVE_SHA256 \
  --legacy-timezone America/New_York \
  --reporting-timezone America/New_York > /private/utc-rehearsal/receipt.json
```

The archive and output must be private regular files; output directory must be
0700. Existing targets, symlinks, source WAL/rollback sidecars and hash mismatches
are rejected. The tool copies the archive before opening SQLite, initializes only
that working copy, and refuses schema initialization that changes archived data.
The app's normal import-time schema bootstrap is isolated in a temporary directory.

One transaction converts energy readings, their latest snapshots, heartbeat
events, capability/migration metadata, cache receipt times and original reading
journal timestamps. Ambiguous or nonexistent times in **any** of those replicas
abort the whole copy. Original timestamps and row keys remain in evidence tables;
all non-timestamp column fingerprints must match, including stored kWh/cents and
collector/generation IDs. Existing journal sequences remain; reading UPDATE
triggers append exactly one canonical upsert per changed reading. Those appended
events and SQLite autoincrement state are checked, not silently rebuilt.

Only an integrity-checked, standalone copy is atomically published, without
overwriting a target. The original archive hash must still match. Failures clean
only this run's temporary copy. The output is **not deployable** and has
`live_ready: false`; current development app connections reject it before
switching journal mode. Inspection requires
`energy._connect(path, allow_utc_rehearsal=True, read_only=True)`, which cannot write
or change its file hash. Older app versions do not recognize this marker; never
point them at the artifact. No collectors, credentials, settings or services are
changed by the rehearsal. New artifacts persist `energy_time_policy` with
`timestamp_format=utc_v1`, an explicit reporting timezone and the declared legacy
timezone assumption. Unknown formats fail closed. Neither the rehearsal marker
nor this policy permits ordinary app connections or writes, even if one marker
is missing. An old artifact without the policy remains available for raw
read-only inspection but cannot run the new calendar queries; make a new copy.

The October 8 private rehearsal converted all 100,237 readings and their energy
timestamp replicas, retaining 201,918 original timestamp evidence entries. All 30
non-timestamp table fingerprints and 1,168 local-calendar day/month energy/cost
groups matched. Existing journal sequences were preserved and exactly 100,237
canonical reading upserts appended. Both archive and artifact hashes remained
unchanged during subsequent read-only inspection. These results use the declared
New York source assumption, not independently established row provenance.
Production remained on 2.3.37; development 2.3.38 is not released or deployed.

This engine is an integration prerequisite, not a replacement for the following
still-required live implementation:

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

The development dashboard and native-menu API now preserve complete timestamps
when determining reading and heartbeat age. Offset-aware timestamps are compared
as UTC instants, including microseconds and both explicit repeated-hour offsets.
Invalid values and future skew beyond the existing one-minute reading tolerance
cannot make the poller or menu appear live. The status label clamps permitted
small future skew to zero instead of displaying negative minutes. This changes
no stored timestamps: naive values retain current host-local interpretation,
which must still be replaced by persisted source/reporting timezone policy during
cutover.

The initial UTC calendar adapters now use this database policy in circuit
day/week/month-to-date totals, monthly recorded costs/report closure, completed-week comparisons,
the seven-day heatmap and repeated-week baselines. Range bounds are canonical UTC;
calendar grouping uses the persisted zone, not SQLite's UTC `strftime` or the
host clock. Stored cents and device boundaries remain intact; future readings
are excluded from month-to-date totals. Expected weekly capture minutes reflect
actual elapsed DST time rather than a constant 10,080 minutes.

Heatmap bins step along actual UTC instants within each reporting day. New York
transition days render 23/25 columns; both fall folds retain their local offsets.
Half-hour transitions retain a clipped final interval with its actual duration;
missing bins remain missing and recorded zero stays zero. Forecast baselines
require distinct calendar-day repetitions; ambiguous fold hours and partial
transition-hour bins cannot invent independent weeks. The original 168-slot
typical-week model remains a normal-week template, not a claim that every actual
week lasts 168 hours.

These adapters are exercised only through explicit read-only maintenance
connections to private rehearsal copies. This is not permission to launch Flask
against a converted artifact. The rest of the reporting/duration queries,
writers, imports, retention/compaction and native cache/sync paths are not yet
UTC-ready; the ordinary connection guard remains until they are coordinated.

## Reproduction tests

```bash
python3 -m unittest discover -s tests -p test_timestamp_model.py -v
venv/bin/python3 -m unittest discover -s tests -p test_utc_migration.py -v
venv/bin/python3 -m unittest discover -s tests -p test_dashboard_freshness.py -v
venv/bin/python3 -m unittest discover -s tests -p test_utc_calendar_queries.py -v
```

Tests cover New York gaps/folds, explicit offsets, a half-hour DST transition,
an entirely skipped calendar day, microseconds, collisions, invalid identities,
empty exports, bounded examples and redacted errors. Preflight CLI runs under
different host timezones create no database or input changes. Rehearsal tests
cover private atomic publication, source drift, nonregular input, schema-reseed
rejection, data fingerprints, journal updates, read-only inspection and rollback.
Calendar tests execute the real adapters on converted fixtures, assert rendered
heatmap column/cell/offset semantics, verify exact now/month boundaries and costs,
test full spring/fall capture weeks, forbid duplicate/fold forecast repetitions,
exercise half-/quarter-hour zones and repeat reads under three real host timezones.

The next private rehearsal of the same 100,237-reading archive exercised the
actual calendar adapters, not just timestamp grouping in an inspection script.
Independent pointwise aggregation matched twelve device-scoped day/week/month,
monthly-cost, heatmap and week-capture comparisons across two device IDs, plus
all seventeen repeated-week profiles. Source and artifact hashes stayed unchanged.
The maximum measured query times on that guest were 0.034 s for circuit calendar
totals, 0.452 s for twelve-month costs, 0.253 s for heatmaps and 0.185 s for week
comparisons. These are this snapshot's observations, not a performance guarantee
or proof that the remaining app queries support UTC. Browser fixture checks
verified 23/25 columns, explicit fold labels, contained 390px scrolling, and
readable sticky circuit labels. No production source, data or services changed.
