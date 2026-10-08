# EcoQube Radon Integration

Status: issue #83; validated storage and a read-only `/radon` history table and seven-day chart. No live EcoQube credentials, device
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
4. Extend the Mac download protocol to include
   radon. The 2.3.4 energy synchronization protocol does not replicate radon.
5. Verify live readings and disconnect/reconnect behavior before closing #83.

No mitigation advice or safety classification is implemented here.

## Read-Only Cloud Probe

`ecosense.py` uses the account endpoint and Cognito identifiers inspected in the
[community adapter source](https://github.com/rwestergren/hass-ecosense-radon/tree/master/custom_components/ecosense_radon).
This is experimental, not a supported vendor API contract. Its sensor code
interprets `radon_level` as Bq/m3 but does not establish measurement timestamp
semantics. Our probe therefore writes no database records. Zero remains a
candidate value, not a confirmed valid measurement or a safety indication.

Supply `ECOSENSE_EMAIL` and `ECOSENSE_PASSWORD` privately in the process
environment, then run `venv/bin/python3 ecosense.py`. Never put credentials in
Git or command-line arguments. Output contains device field names and candidate
values, not serial values, account credentials, or authorization tokens.
The request is bounded to 1 MiB and 15 seconds, refuses redirects, and retries
an expired authorization once. Authentication requests have bounded connection
and read timeouts. Failures report only their class, not secret-bearing content.

Live authentication is not verified. A real response still needs to establish
the actual device model, units, unavailable-value semantics, and measurement
timestamp before enabling recorded history collection.

For a one-time interactive login, run `venv/bin/python3 ecosense.py --login`.
The password prompt is hidden and neither credential is saved by the probe.
Do not run this with shell tracing or paste passwords into chat.

The chart uses arithmetic hourly sample means in Bq/m3, not duration-weighted
exposure. Hours without samples have no dots; points are not connected. Samples
remain available in the original-unit table. The chart does not infer safety.
