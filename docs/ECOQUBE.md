# EcoQube Radon Integration

Status: issue #83; storage groundwork only. No live EcoQube credentials, device
model, API payload, or measurement timestamp semantics have been verified.
Do not configure DNS interception or change pairing as part of installation.

`radon.ingest_observations` accepts a batch of objects with `source` (`ecosense`,
`home_assistant`, or `manual`), stable `sensor_id`, `name`, timezone-qualified
measurement `timestamp`, numeric `value`, and explicit `unit` (`pCi/L` or
`Bq/m3`). Conversion uses 1 pCi/L = 37 Bq/m3. Original values and units remain
stored. Retrieval time is separate from measurement time. Skip unavailable
samples; never fabricate a new measurement timestamp on each poll.

Schema lives exclusively in `energy.ensure_table()`. Radon is not a temperature
and does not enter the energy readings table. Batch validation precedes writes;
conflicting retries roll back the entire transaction. History queries require a
source and sensor identity and return only recorded samples, without gap filling.

Next steps:
1. Verify the user's actual EcoQube model and access route (vendor API, existing
   Home Assistant entity, or explicitly approved community cloud adapter).
2. Obtain a real redacted response and verify units and measurement timestamps
   against the vendor app before implementing its adapter.
3. Add secure configuration, bounded requests, authentication refresh, and
   collector health reporting on SER8.
4. Add radon display/history and extend the Mac download protocol to include
   radon. The 2.3.4 energy synchronization protocol does not replicate radon.
5. Verify live readings and disconnect/reconnect behavior before closing #83.

No mitigation advice or safety classification is implemented here.
