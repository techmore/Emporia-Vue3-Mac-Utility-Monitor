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

Development 2.3.41 persists `measurement_seconds`, `measurement_source`,
`source_timezone` and `provider_timestamp` alongside each reading, its latest
snapshot, change journal and downloaded cache. CSV duration comes from the declared
filename and actual source-zone day bounds. Accepted duplicates preserve original
evidence. A missing interval remains null; legacy rows are not backfilled by guess.
Emporia polling records the requested minute duration and retains an offset-aware
SDK observation timestamp separately from local receipt time. This provider
instant is not a proven interval-start/end or a non-overlap guarantee.
Its UTC offset also does not prove the zone of the legacy local receipt timestamp;
poll `source_timezone` stays null until an explicit storage policy is implemented.

Compacted sums are explicitly marked `compacted` with unknown duration: counting
samples or putting a sum at an hour boundary does not prove continuous coverage.
`reading_average_watts` derives interval-average power only from valid duration
and source evidence; unknown stays null, zero stays zero, signed energy is retained.
The existing dashboard/native live power consumers have not yet been converted
and can still display incorrect estimates. #135 remains open for those consumers,
mixed-resolution overlap and verified historical reconciliation. The additive
fields still use sync protocol 2; old clients can ignore them and are not yet
safe for the future UTC/power cutover. No production data is changed by this draft.

Schema upgrades live only in `ensure_table`. An older archived UTC rehearsal
requires schema upgrade on a separate private copy and a new verified receipt;
adding columns changes its schema fingerprints. Never modify the sole archive.

## Verification

```bash
venv/bin/python3 -m unittest discover -s tests -p test_import_integrity.py -v
venv/bin/python3 -m unittest discover -s tests -p test_measurement_evidence.py -v
venv/bin/python3 -m unittest discover -s tests -v
```

Tests use private databases and actual importer/startup/Flask code. They cover
duplicate consistency, publication rollback and connection cleanup, source-zone
folds/gaps, DST daily durations, unsupported/nonfinite input, zero/export values
and unchanged raw energy/cost after startup. No live database or device is used.
