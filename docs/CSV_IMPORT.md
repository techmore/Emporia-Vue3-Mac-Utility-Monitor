# CSV import integrity

Use Settings -> Import for Emporia exports. Keep the original filename: its last
segment declares `1SEC`, `1MIN`, `15MIN`, `1H` or `1DAY`. Power (`kWatts`/`kW`)
requires a recognized duration. Energy (`kWhs`/`kWh`) is stored without conversion;
an unknown filename interval does not authorize a watt estimate.

The first header must be `Time Bucket` or `Time Bucket (IANA/Timezone)`, such as
`Time Bucket (America/New_York)`. Declared zones are validated. Daily power needs
that zone and midnight-aligned buckets: transition days can last 23 or 25 hours,
not invariably 24. Ambiguous fold times and nonexistent gap times are reported
as errors, not resolved by a guessed offset. New imports with a declared zone are converted to the collector local clock before
storage and duplicate detection. Rows landing in a repeated collector-local hour
are rejected because legacy timestamp keys cannot distinguish its two occurrences.
Unzoned exports retain their local-clock interpretation. Existing stored history
is not reinterpreted. This does not complete the UTC migration in #127.

Unsupported units, duplicate/ambiguous channel headers and unknown power intervals
reject the file before database writes. Nonfinite/overflow values and invalid
row timestamps are counted as errors and omitted. Other valid rows can still be
imported; inspect all returned counts rather than treating HTTP 200 as a clean
import. Signed finite energy and recorded zeroes are preserved.

Readings, their change journal, accepted-row snapshots and service capabilities
publish in one transaction. A skipped conflicting duplicate does not overwrite
accepted energy/cost, create a new reading change, or replace its snapshot with
the rejected upload value. Older uploads cannot replace newer accepted snapshots.
Known validation errors return HTTP 400; unexpected publication failures return
HTTP 500 with a generic message and details in the private server log.

Since 2.3.61, imports also compare known UTC interval coverage for the same panel
and channel. Overlapping resolutions (such as an hour and its constituent
15-minute buckets) are counted in `interval_conflicts` and `errors`, regardless
of import order. Already accepted readings remain unchanged; adjacent intervals
are allowed. Same-key, same-duration duplicates remain skipped. Unknown durations
are handled conservatively rather than being assigned guessed coverage. Accepted
non-overlapping rows still publish together in one transaction. Existing overlaps
are not automatically rewritten or reconciled.

## Historical repair

The old startup correction divided exact-second timestamps on non-primary
devices by 60 without knowing the source unit or interval. It is retired in
2.3.39. Its compatibility function performs no mutation; startup no longer calls
it. Previous migration markers and stored values are retained, not reinterpreted.

This prevents further guessed corrections; it does not prove old history is
correct or repair a prior divide-by-60. Preserve a verified online backup and
original exports before any reconciliation. Match original device, timestamp,
source zone, interval and unit; audit collisions and mixed resolutions before
proposing replacements. Normal re-import intentionally skips conflicting rows.
Never infer a historical unit from timestamp precision, device ID or apparent
plausibility of the resulting number.

Since 2.3.59, new accepted imports persist source, interval, source zone and unit
in `reading_provenance`, atomically with readings and snapshots. New live polls
record their known 60-second interval. Circuit history exposes interval-average
watts separately from live power: imports never establish live power. Unknown
legacy provenance yields unknown power; existing energy and cost are unchanged.
Since 2.3.60, compaction preserves entire channel-hours containing known CSV
intervals, so reimporting accepted exports remains idempotent. Other rolled hours
have durable markers. New rows overlapping those markers or existing history with
unverified provenance are reported in `compacted_conflicts` and `errors`, with an
explanation in `message`; they are not silently merged. Exact duplicates still
skip without changing accepted values. Existing historical rollups are not
reinterpreted or repaired. Compacted raw poll metadata is removed and replacement
aggregates retain unknown provenance. Replication does not yet transfer this metadata, so the full
interval-aware aggregation and synchronization work in #135 remains open.

## Verification

```bash
venv/bin/python3 -m unittest discover -s tests -p test_import_integrity.py -v
venv/bin/python3 -m unittest discover -s tests -v
```

Tests use private databases and actual importer/startup/Flask code. They cover
duplicate consistency, publication rollback and connection cleanup, source-zone
folds/gaps, DST daily durations, unsupported/nonfinite input, zero/export values
and unchanged raw energy/cost after startup. No live database or device is used.
