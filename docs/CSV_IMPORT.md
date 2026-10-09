# CSV import integrity

Use Settings -> Import for Emporia exports. Keep the original filename: its last
segment declares `1SEC`, `1MIN`, `15MIN`, `1H` or `1DAY`. Power (`kWatts`/`kW`)
requires a recognized duration. Energy (`kWhs`/`kWh`) is stored without conversion;
an unknown filename interval does not authorize a watt estimate.

The first header must be `Time Bucket` or `Time Bucket (IANA/Timezone)`, such as
`Time Bucket (America/New_York)`. Declared zones are validated. Daily power needs
that zone and midnight-aligned buckets: transition days can last 23 or 25 hours,
not invariably 24. Ambiguous fold times and nonexistent gap times are reported
as errors, not resolved by a guessed offset. Production energy timestamps still
follow the legacy convention; this does not complete the UTC migration in #127.

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

Per-reading interval/provenance persistence, compaction, sync and power consumers
remain required in #135. Returning validated import metadata is not persisting
it with every observation or making existing power estimates correct.

## Verification

```bash
venv/bin/python3 -m unittest discover -s tests -p test_import_integrity.py -v
venv/bin/python3 -m unittest discover -s tests -v
```

Tests use private databases and actual importer/startup/Flask code. They cover
duplicate consistency, publication rollback and connection cleanup, source-zone
folds/gaps, DST daily durations, unsupported/nonfinite input, zero/export values
and unchanged raw energy/cost after startup. No live database or device is used.
