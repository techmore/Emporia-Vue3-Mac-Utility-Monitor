# Completed Emporia History Contract

The live `getDeviceListUsages` response and chart `getChartUsage` response are
different evidence sources. Do not assign live values to a chart bucket using
the live response's echoed `instant`, receipt time or a guessed one-minute shift.
The SDK propagates `instant` to channel timestamps; this does not prove a bucket
start/end. Its chart helper also falls back to the requested start when the
server omits `firstUsageInstant`, concealing missing provenance.

Read-only cloud probes on October 9, 2026 found:

- A request from 06:17 to 06:22 UTC returned six minute chart values. Its end
  bucket was included. A half-open five-minute history window must admit five,
  not six, complete intervals.
- One live-list value at an echoed 06:17 UTC instant matched the chart's 06:16
  bucket, not its 06:17 bucket. Another queried instant matched its same-time
  chart bucket. This is not a universal preceding-minute rule.
- Queries inside the same minute returned identical energy despite different
  echoed seconds. Expired minute history returned null, not zero.
- Repeating one completed chart window returned identical values in that probe.
  This does not prove all future cloud responses are immutable or final.

Original token files, running services and production databases were untouched;
temporary token copies were deleted. Keep captured data and request context
private. Do not commit real account identifiers or usage responses as fixtures.

## Strict Offline Validation

`emporia_history.validate_completed_minutes` requires explicit aware request and
receipt timestamps, minute scale, kWh units, and the **returned** aware,
minute-aligned `firstUsageInstant`. It never invokes the SDK's missing-anchor
fallback. Bucket positions follow the returned anchor and original array index;
nulls remain gaps rather than shifting following values or becoming zero.
Count unreturned leading/trailing intervals as missing too; an empty response
cannot claim zero missing history. Separate reported nulls from absent buckets.

Only complete half-open intervals inside the requested window are eligible.
An optional settling delay (default five minutes) also excludes recent buckets.
This delay is a conservative policy, **not** proof of cloud finality or continuous
capture. Signed finite energy and explicit zero survive. Malformed values reject
the whole report even if their array positions are outside the selected window.
Requests are bounded to seven days and responses to 10,081 minute values.

Capture the raw response bytes and their SHA256 separately. The audit envelope
must record the actual request, actual receipt time, and parsed response:

```json
{
  "schema": "emporia_chart_v1",
  "request": {
    "start": "2026-10-09T06:17:00Z",
    "end": "2026-10-09T06:22:00Z",
    "scale": "1MIN",
    "unit": "KilowattHours"
  },
  "received_at": "2026-10-09T06:30:00Z",
  "response": {
    "firstUsageInstant": "2026-10-09T06:17:00Z",
    "usageList": [0.01, 0.01, null, 0.01, 0.01, 0.01]
  }
}
```

Run `venv/bin/python3 scripts/audit_emporia_history.py --input PRIVATE_CAPTURE.json`.
It reads only that evidence and prints counts/hash, not measurement values by
default. `--include-buckets` explicitly includes private values and UTC bounds.
No login, database initialization, repricing, migration or publication occurs.
The example produces four eligible observations, one missing completed bucket,
and one excluded end bucket. `publication_performed` and
`continuous_capture_verified` remain false.

Verify with `venv/bin/python3 -m unittest discover -s tests -p test_emporia_history.py -v`.
Tests include subprocess execution with a DB path that must never be created.

## Shared Historical Publication (Development 2.3.51)

`energy.collect_completed_history` fetches one bounded window with the existing
authenticated SDK client. It issues the raw authenticated `getChartUsage` GET,
checks HTTP status/size and closes the response on success or failure. This is
not the SDK helper that replaces a missing server anchor with requested start.
`energy.publish_completed_history` accepts an already captured scoped envelope
and its **exact raw response bytes**; parsed content must agree exactly.

The request adds `device_gid` and `channel_num` to the validator envelope above.
Only a registered canonical device and an unambiguous provider-supplied channel
label may publish. Some discovery objects omit Main/branch labels; live responses
can supply those claims through the poller. Do not guess `Main` from `1,2,3` or
invent labels for unnamed channels. Multiple names for a channel, multiple channel
numbers for a label or unresolved old device aliases require explicit review.
Claim timestamps are UTC and claims are append-only; SDK objects remain unchanged.

Legacy storage requires a separately reviewed `legacy_storage_timezone` argument.
Neither host time nor a cloud request's UTC offset proves old storage provenance.
UTC-policy publication follows the persisted clock, but the ordinary writable-UTC
guard remains. Ambiguous legacy fold keys retain raw chart evidence without
publishing guessed wall-clock readings. Private conversion keeps new raw ledger
timestamps/bytes unchanged. These APIs do not enable UTC or install artifacts.

The private general ledger preserves raw bytes/SHA256, actual request/receipt,
device/channel identity, normalization delay and import-time rate. Each eligible
bucket retains original array index, canonical bounds and exact signed kWh/stored
cents. Exact capture retries preserve original rate and source counters; a later
changed cloud response is a new source, never a silent overwrite. Null gaps are
not readings. There is no claim that a settling delay proves finality.

Both CSV and chart observations feed the **same** interval selector. Global
append-only source order preserves existing CSV precedence and survives upgrades,
retries and VACUUM. Complete consistent finer coverage can replace owned coarse
coverage; partial captures retain the full coarse value without prorating. Later
captures can complete earlier partial coverage. Conflicting complete resolutions,
crossing intervals, unknown unmanaged history and legacy key collisions remain
review gates. Retaining raw evidence does not mean it contributed to totals.

The owning transaction publishes sources, projection ownership, readings,
journal and accepted snapshots together. Only owned rows may be replaced; shared
replacement ordering prevents a one-event client page from counting an old coarse
interval together with new fine intervals. Surviving keys keep reading IDs and
stream identity; no forced cache reset occurs. Newer/pruned live snapshots survive.
`emporia_chart` is historical interval-average evidence, never fresh live power.

Development verification:

```bash
venv/bin/python3 -m unittest discover -s tests -p test_completed_history.py -v
venv/bin/python3 -m unittest discover -s tests -v
```

On a disposable instance, discover devices and confirm provider channel claims,
then capture a completed window with an authenticated client and the independently
reviewed legacy storage zone. Inspect `imported`, `warnings`, `quality_issues`,
`missing_completed_buckets`, raw source SHA and actual device-scoped history.
Repeat the exact capture after changing only the fixture rate: stored costs and
IDs must remain unchanged. Mix a 3kWh CSV hour with 60 consistent 0.05kWh chart
buckets in both orders: totals must remain 3kWh, with 60 selected readings. With
only 20 buckets or all nulls, retain the complete hour. With conflicting values,
retain earlier accepted values and show review warnings. Never use these examples
as permission to modify or reprice production history.

## Still Required For Collection

1. Schedule completed-window collection with durable per-channel cursors, bounded
   API budgets/backfill, restart/retry recovery and explicit gap/conflict tracking.
   The new explicit acquisition/publication path is not a continuous scheduler.
2. Review and reconcile production unowned live/CSV history against verified
   original sources. Keep latest live display snapshots separate from canonical
   history; never drop unknown legacy observations to make the gate pass.
3. Add reviewed channel rename/alias reconciliation where immutable discovery
   claims are missing or ambiguous. Bound archive storage with a recoverable
   evidence-retention policy. Original sources are not automatically pruned.
4. Preserve stored costs, journals and client-generation semantics. Verify actual
   cloud-to-ledger-to-projection-to-cache totals before a backed-up cutover.

The shared source publisher is a collection prerequisite, not continuous capture,
automatic production CSV/live reconciliation or UTC activation. #127/#135/#140
remain open and the draft is not deployed.

References: [SDK source](https://github.com/magico13/PyEmVue/blob/master/pyemvue/pyemvue.py),
[SDK endpoint examples](https://github.com/magico13/PyEmVue/blob/master/api_docs.md),
[Emporia measurement periods](https://help.emporiaenergy.com/en/articles/13274302-understanding-your-energy-data).
