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

## Still Required For Collection

1. Persist immutable raw chart responses, request identity and normalization
   contract in a general energy source ledger, not a falsely labeled CSV batch.
2. Integrate completed chart intervals with the shared non-overlapping projection
   transaction. Keep latest live display snapshots separate from canonical
   historical bucket publication. Do not drop live observations or mutate legacy
   values merely because their bounds are unknown.
3. Persist a stable device/channel identity mapping and collection cursor;
   tolerate null gaps, retries, delayed updates and collector restarts. Bound API
   calls, backfill windows and storage. Require explicit review on source conflicts.
4. Preserve stored costs, journals and client-generation semantics. Verify actual
   cloud-to-ledger-to-projection-to-cache totals before a backed-up cutover.

This development validator is a prerequisite, not completed history collection,
automatic CSV/live reconciliation or UTC activation. #127 and #135 remain open.

References: [SDK source](https://github.com/magico13/PyEmVue/blob/master/pyemvue/pyemvue.py),
[SDK endpoint examples](https://github.com/magico13/PyEmVue/blob/master/api_docs.md),
[Emporia measurement periods](https://help.emporiaenergy.com/en/articles/13274302-understanding-your-energy-data).
