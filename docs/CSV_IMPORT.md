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

Development 2.3.46 checks HTTP status and nonnegative integer counts before
showing success. Validation, upload-size and same-origin failures display their
safe error message; non-JSON failures retain the HTTP status. Partial imports
remain error-colored with their actual accepted/skipped/error counts. A transport
failure cannot establish whether the server committed: check recorded history
before retrying, especially while overlap reconciliation remains unfinished.
This UI correction does not deduplicate overlapping resolutions or repair history.

## Historical repair

### Canonical Monitor Identity (Development 2.3.49)

The export filename prefix is not necessarily its cloud device ID. The verified
`8C9E94` export prefix matches the suffix of manufacturer IDs returned for cloud
monitor `551741`. Parent and nested SDK objects can share that same cloud ID;
discovery merges their channels without mutating the SDK objects. Main plus 16
branch channels remain 17 distinct channels, not two different monitors.

The poller persists cloud IDs, full manufacturer IDs and observed six-hex export
suffix claims in the database. A unique candidate allows automatic matching;
collisions across different cloud IDs require explicit selection. Monitor names,
existing reading values and apparent usage patterns never establish identity.
Discovery claims remain immutable across later discovery calls. Import does not
log in to the cloud or perform network discovery from the web server.

Settings -> Import lists only discovered canonical monitors. Leave automatic
matching selected for a known unique prefix. For an unknown or ambiguous export,
explicitly select its correct monitor; an unregistered choice or a choice that
contradicts the discovery candidates returns HTTP 400 with no publication. With
no discovered monitors, allow the poller to discover devices first. Selecting a
monitor applies to every selected file, so do not batch files for different
monitors under one explicit choice.

Trusted standalone Python callers may explicitly assert `device_gid` without a
registry, for intentional offline imports. This is not a fallback used by HTTP
clients. Such assertions create only an immutable per-source binding, never a
global alias that future uploads silently inherit. HTTP imports always require
the selected monitor to be registered. Binding records include normalized export
identity, canonical ID, resolution method and a UTC receipt time; the untouched
original filename/bytes retain original spelling. Publication, binding, source
observations, journal, snapshots and capabilities commit or roll back together.

New v2 batch identity includes canonical monitor, export prefix, interval and
exact content hash. Different prefixes cannot overwrite one another's binding;
changing the rate or temporary path does not reprice identical evidence. Original
v1 source batches are left intact. Multiple valid sources resolving to the same
monitor share the existing non-overlap/conflict projection rules.

If noncanonical history exists under this monitor's discovered aliases (or this
export prefix), import is blocked pending reviewed reconciliation. This prevents
canonical publication from bypassing the coverage gate for old split history.
It does not rewrite IDs, merge old evidence, infer unknown bounds or silently
include aliases in reports. Unknown live bounds under the canonical ID still
produce review warnings and retained source evidence, not duplicate totals.
#140 remains open for reviewed legacy reconciliation and production acceptance;
#127/#135 retain their UTC/general source-ledger scope.

Disposable-instance verification:

1. Run poller discovery, then open Settings -> Import. Confirm the monitor's
   label and canonical ID appear. Import its known export prefix automatically.
   Verify the returned monitor ID and its device-scoped history, not a new suffix
   device. Confirm the original file hash and bytes remain unchanged.
2. Upload an unknown prefix without selection: expect HTTP 400 and no new
   readings, source batches, bindings or journal entries. Explicitly select the
   correct discovered monitor and verify an operator-selected source binding.
3. In isolated fixtures, discover two different monitors with the same suffix.
   Automatic import must reject; explicit candidate selection must be required.
4. On a private fixture with old suffix history, try canonical and alternate
   prefixes. Each must request reconciliation and preserve all prior rows/IDs.
   Never use a plausible total as permission to rewrite production history.

The old startup correction divided exact-second timestamps on non-primary
devices by 60 without knowing the source unit or interval. It is retired in
2.3.39. Its compatibility function performs no mutation; startup no longer calls
it. Previous migration markers and stored values are retained, not reinterpreted.

### Reviewed Identity Copies (Development 2.3.50)

`scripts/reconcile_csv_identity.py` audits a verified **standalone backup**, not
the running database. It can then publish a new private copy with explicit approval
of that exact plan. It never installs the copy, stops services, logs in to Emporia,
changes prices or activates UTC. Do not point an older app at the artifact or
deploy this draft: later live writes and historical conflicts still need #135.

Prerequisites: upgrade only a disposable working copy to the reviewed schema,
record genuine poller discovery there, then take a new verified online backup.
Do not fabricate discovery claims or run schema initialization against the sole
archived backup. Backups must be private regular files (0600), single-link and
without WAL/SHM/rollback sidecars; output directories must be 0700. Existing
targets, symlinks, FIFOs, drift and incorrect hashes fail closed. If an old schema
upgrade changes archived row data, upgrade and inspect another copy first.

Audit example (substitute independently verified IDs and hashes):

```bash
umask 077
venv/bin/python3 scripts/reconcile_csv_identity.py \
  --snapshot /private/identity-review/archive.db \
  --expected-sha256 VERIFIED_SNAPSHOT_SHA256 \
  --source-gid VERIFIED_EXPORT_ID --canonical-gid VERIFIED_CLOUD_ID \
  > /private/identity-review/plan.json
```

The CLI isolates import-time bootstrap even when `DB_PATH` points to an existing
installation. Exit 0 means this **candidate** is unblocked, not deployed or proven
bill-correct; exit 2 returns blocker counts; exit 1 indicates invalid inputs or a
failed operation. Reports and backups must stay private and outside Git.
An operation error does not prove that a destination was never published: receipt
verification can fail after atomic publication. Inspect any destination privately;
do not retry blindly or activate it without a complete independently verified receipt.

The plan requires one discovery-backed canonical candidate, not a name match or
an ambiguous suffix guess. It verifies every retained observation against exact
original CSV bytes, headers, cells, IDs, units, declared source timezone, interval
bounds and original price basis. Every selected alias reading must be ledger-owned
and match that evidence. Unowned alias readings/snapshots, invalid source evidence,
overlapping source projections, canonical key collisions, intersecting target
coverage or target history with unknown bounds block publication. Adjacent verified
half-open intervals can coexist. An empty retained batch can be bound without
inventing readings. This cannot adopt or repair pre-ledger, previously mispriced,
divided-by-60 or otherwise unverifiable history.

After reviewing all counts and prerequisites, repeat the same audit command with:

```bash
  --destination /private/identity-review/canonical.db \
  --reviewed-plan-sha256 EXACT_PLAN_SHA256_FROM_REVIEW
```

Only `readings.device_gid` changes for approved owned IDs. kWh, stored cents,
timestamps, interval/provider evidence, original journal rows, stream identity,
raw batches/cells and projection membership remain unchanged. Derived snapshots
move only verified source candidates; newer/unrelated retained target snapshots
survive. Service-capability evidence merges without dropping stronger target data.
The transaction appends one ordinary upsert per changed reading ID, not new IDs
or a cache reset. Single-event cache pages retain the same total energy; device
scopes are complete only once the cache reaches the journal watermark.

Immutable review records retain the approved backup/plan hashes, row fingerprints
and before/after derived-state receipts. Batch-scoped decisions are append-only;
`csv_effective_devices` selects their latest decision without editing the original
batch/device binding. Subsequent imports use that effective scope and the same
existing conflict/coverage rules. This is not a global alias on every read query.

To review reversal, use the applied copy's verified `artifact_sha256` and its
`review_id`; omit monitor IDs:

```bash
venv/bin/python3 scripts/reconcile_csv_identity.py \
  --snapshot /private/identity-review/canonical.db \
  --expected-sha256 VERIFIED_APPLIED_ARTIFACT_SHA256 \
  --rollback-review-id ORIGINAL_BIND_REVIEW_ID
```

Approve that reversal plan separately and publish to another new private path.
Reversal appends new decisions/upserts; it preserves the first review and original
source evidence, and restores original reading IDs/values and derived-state rows.
Changed/pruned/reprojected readings, superseded decisions or newer derived state
invalidate automatic reversal. Obtain a new reviewed reconciliation instead of
discarding newer data. Reconcile identity **before** UTC conversion; a subsequent
UTC conversion remains guarded and cannot be silently undone by this tool.

Private fixture verification covers apply/revert, failure injection and cleanup,
real authenticated loopback downloads/cache resume, native offline history and
finer imports after binding. All five original files were also checked through a
private legacy-ledger apply/reversal: 460,350 source observations validated and
66,114 selected readings moved and reversed with original kWh/cents/IDs intact.
This evidence is not current SER8 acceptance. General live/source reconciliation,
completed capture, operational cutover and whole-bill correctness remain open.

This prevents further guessed corrections; it does not prove old history is
correct or repair a prior divide-by-60. Preserve a verified online backup and
original exports before any reconciliation. Match original device, timestamp,
source zone, interval and unit; audit collisions and mixed resolutions before
proposing replacements. Normal re-import intentionally skips conflicting rows.
Never infer a historical unit from timestamp precision, device ID or apparent
plausibility of the resulting number.

## Source Ledger And Projection (Development 2.3.47)

The collector retains exact uploaded bytes/hash/headers plus each validated cell's
original value, source timestamp/unit, canonical source instant, verified interval
bounds and import-time kWh/cents. Batch identity includes device, declared interval
and file hash, not the current rate or temporary upload path. Re-uploading identical
evidence does not reprice it. Source tables reject updates/deletes. UTC conversion
must preserve these already-canonical bounds and original source-local evidence;
only the projected reading timestamps follow the storage policy.

`csv_projection.py` borrows the data layer's locked connection. Schema DDL remains
in `ensure_table`. Its weighted interval selection maximizes covered elapsed time,
then finer resolution. Full minute coverage can replace an equivalent hour; a
partial subset cannot. Adjacent half-open intervals do not overlap. Never create
an estimated leftover from a partially overlapping coarse bucket. Crossing
intervals without a complete non-overlapping replacement require review.
Coverage here means reported bucket extent, not independently verified continuous
sensor capture. First/last export buckets may be partial measurements; the files
alone cannot prove their capture completeness or the correctness of a full bill.

Same-interval disagreements retain the first validated source value and flag
review. An immutable sequence preserves that precedence through `VACUUM`; it
does not depend on incidental rowids. Complete coarse/fine disagreements leave
the prior projection unchanged;
the arithmetic tolerance covers floating-point noise, not assumed export rounding.
This intentionally does not claim conflicting imports are order-independent or
that an accepted prior value has been independently confirmed correct.

Only ledger-owned reading IDs can be replaced. Unmanaged CSV history with verified
bounds blocks overlapping new projections; unrelated intervals can publish.
Unmanaged live/compacted/legacy history without verified bounds blocks competing
CSV projections for that channel. It is not automatically adopted, deleted or
assigned guessed boundaries. A first legacy source with unknown zone/duration can
retain its original readings but carries an unresolved-coverage warning; competing
sources remain blocked. An explicit historical reconciliation tool/receipt is still
required, as is coordination of later live writes. This is CSV-only projection
correctness, not proof that all existing readings are non-overlapping.

Source evidence, projection membership, reading changes, latest snapshots and
capabilities commit or roll back together. Surviving reading keys reuse IDs, so
clients receive ordinary upsert/delete events without reseeding identity.
Replacement events delete obsolete keys, update reused keys, then insert new keys,
so even single-event cache pages do not double-count superseded CSV coverage.
Partially synchronized caches are not complete-history/billing acceptance.
Pruning readings removes membership, not source evidence. A new disjoint import does not
resurrect older sources. An explicit re-import of an archived period may restore
its complete preferred evidence; projection counts make that change visible.

`imported` counts newly activated winning intervals represented by this upload;
`skipped` includes duplicates/suppressed input cells and unconnected CTs. The
`observations_recorded` count describes new retained valid source cells, not totals.
`inserted`/`updated`/`deleted` describe actual projection changes, including restored
earlier evidence. `warnings` and grouped `quality_issues` identify review gates.
An HTTP 200 with warnings is not a clean import. No automatic repricing occurs;
newly selected intervals retain their own original stored price basis.

The ledger includes the whole original file, even omitted/rejected cells, and is
kept privately in the database. Reading retention does **not** bound ledger disk
usage. Keep verified online backups and monitor storage before bulk imports; any
future archival/garbage collection needs an explicit recoverable evidence policy.
Raw source tables are collector-only; clients synchronize the selected readings.
No production history, service or ordinary writable-UTC guard is changed by this
draft. #127/#135 remain open.

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
Development 2.3.42 converts dashboard/native menu consumers: live estimates require
fresh `emporia_minute` observations with a 60-second interval and fresh provider
time when supplied. Imports never become live merely because their timestamp is
recent. Circuit previews can show a labeled historical interval average. Unknown
power is not zero or a safe breaker rating; signed export uses magnitude for
estimated loading. Peaks are aligned monitored averages, not instantaneous or
whole-home peaks. #135 remains open for mixed-resolution overlap, historical
reconciliation and production verification. The additive
fields still use sync protocol 2; old clients can ignore them and are not yet
safe for the future UTC/power cutover. No production data is changed by this draft.

Development 2.3.43 compacts only groups containing compatible evidenced Emporia
minute observations. Any imported/unknown interval, differing channel identity or
source zone, repeated provider instant, invalid value or overflow preserves the
whole group unchanged. Equivalent offset strings identify the same provider
instant. Isolated samples retain their original evidence. Valid aggregates retain
signed kWh/stored cents and an agreed source zone, but duration/provider time stay
unknown: even distinct minute observations cannot prove a continuous full hour.
This avoids destructive coalescing; it does not deduplicate existing overlapping
energy, repair old compaction or prove full coverage.

Deletes, replacement inserts and their journal events use a savepoint, leaving
the caller responsible for commit/rollback. A failed replacement cannot publish
deleted history; unrelated pending edits survive. Scratch tables are explicitly
temporary, never permanent application tables.

Schema upgrades live only in `ensure_table`. An older archived UTC rehearsal
requires schema upgrade on a separate private copy and a new verified receipt;
adding columns changes its schema fingerprints. Never modify the sole archive.

Development 2.3.45 adds policy-aware writer/import paths without enabling live UTC
connections. Under a UTC policy, CSV source-local times become canonical UTC using
the declared header zone; files without a source zone are rejected, not interpreted
in the reporting/laptop timezone. The response declares storage format and reporting
zone. Legacy storage still retains local CSV strings. Error counts, actual daily
durations, accepted duplicate values and source evidence remain intact. Import
publication locks policy with readings, journal, snapshots and capabilities.
Private UTC conversion also rotates energy stream generation so existing clients
replace their legacy caches; collector identity and reading IDs remain preserved.
This is verified on disposable fixtures and authenticated localhost sync, not a
production activation or historical overlap repair. #127/#135 remain open.

## Verification

```bash
venv/bin/python3 -m unittest discover -s tests -p test_import_integrity.py -v
venv/bin/python3 -m unittest discover -s tests -p test_import_ui.py -v
venv/bin/python3 -m unittest discover -s tests -p test_csv_projection.py -v
venv/bin/python3 -m unittest discover -s tests -p test_measurement_evidence.py -v
venv/bin/python3 -m unittest discover -s tests -p test_power_consumers.py -v
venv/bin/python3 -m unittest discover -s tests -p test_compaction.py -v
venv/bin/python3 -m unittest discover -s tests -p test_utc_writers.py -v
venv/bin/python3 -m unittest discover -s tests -v
```

Tests use private databases and actual importer/startup/Flask code. They cover
duplicate consistency, publication rollback and connection cleanup, source-zone
folds/gaps, DST daily durations, unsupported/nonfinite input, zero/export values
and unchanged raw energy/cost after startup. No live database or device is used.

The UI tests require Node and execute the submit handler extracted from the actual
Flask-rendered Import page. They check real 400/403/413/500 responses, partial
imports, malformed counts/JSON, escaped filenames/messages, batch continuation
and restored controls. The DOM/fetch harness is not a visual browser or production
proxy acceptance test.

Manual verification on a disposable local instance:

1. Open Settings -> Import and select a valid Emporia CSV. Verify the returned
   counts and that the Import button becomes usable again.
2. Upload a CSV with one invalid value and one valid row. Verify an error-colored
   result with one accepted row, not an all-or-nothing success claim.
3. Set a small `MAX_UPLOAD_BYTES` on that disposable instance and upload a larger
   CSV. Verify an error-colored size-limit message, never green undefined counts.
4. In browser network tools, test a lost request. Verify an unknown-outcome warning
   and inspect recorded history before any retry. Do not simulate this on production.
5. On a fresh private database, import a 3kWh hour and 60 minute rows of 0.05kWh
   in both orders. Verify 3kWh total and 60 selected rows, while all 61 source
   observations remain. Repeat with only 20 minutes: the complete hour must remain.
6. Upload a conflicting whole-hour value or disagreeing complete finer coverage.
   Verify review warnings, retained raw evidence and unchanged prior projection.
   Do not approve a production replacement solely because a value seems plausible.
