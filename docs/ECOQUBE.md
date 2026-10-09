# EcoQube Radon Integration

## Display units and older history

The dashboard defaults to pCi/L for the latest value, chart axis/tooltips and
reading table. Conversion uses the preserved normalized Bq/m3 value divided by
37; source values remain unchanged. Displaying two decimal places does not
increase sensor accuracy. The targeted radon tests cover the converted display.
The chart now plots every measurement at its source timestamp instead of hiding
individual readings inside hourly/daily means. Its horizontal axis fits the
available history inside the selected window, and the page states the plotted
sample count. Regression coverage checks 20 measurements produce 20 dots in all
four history windows; 28 targeted tests passed before deployment.

Release 2.3.36 adds **Recorded trend: On/Off**, enabled by default. It connects
adjacent recorded samples within three hours; longer gaps break the line and a
single sample stays a dot. This is a visual connection of measurements, not a
regression, moving average, forecast, safety classification or evidence of
continuous coverage. Every original measurement remains plotted and stored
unchanged. Sensor and Day/Week/Month/Year navigation preserve the toggle.
No cloud calls are made by the chart or toggle.

Manual verification: open Radon with recorded history, switch **Recorded trend**
off and on, then change the sensor and Day/Week/Month/Year window. Dot count,
values and source timestamps must stay unchanged, the choice must persist, and
samples separated by more than three hours must not share a connecting segment.
With one sample there is a dot but no line; with no samples there is no chart.
The database and collector credentials are not changed by these GET controls.

## Current production collector

SER8 collection runs in the `energy-monitor` Incus instance, independent of the
Mac. Private EcoSense credentials/status are beside the authoritative database
under `/var/lib/energy-monitor/collector`. Inspect its actual service with
`incus exec energy-monitor -- journalctl -u ecosense-collector` on SER8.
Native Linux uses its selected system or user manager instead. Do not restart
the retained native collector alongside the guest.
See INCUS_DEPLOYMENT.md for the verified migration and `/api/version` for the
active release. The original native setup receipt below is historical.

Collection saves new timestamped observations continuously. The current cloud
adapter only retrieves latest device values; older history has not been imported.
EcoSense's official EcoQube product page confirms app history and data export:
https://ecosense.io/products/ecoqube . A real exported history file is required
to verify its timestamp, unit and sensor columns before implementing a backfill
parser. No historical samples or timestamps should be synthesized. Public API
documentation routes returned HTTP 403; this does not establish that a history
API is unavailable, only that an authenticated history contract is unverified.

## Persistent SER8 collector — 2026-10-08

`ecosense_collect.py` and `setup/ecosense-collector.service` implement unattended
collection. The user service is installed and enabled on SER8, independently of
the Mac, and `/radon` now displays its actual status. The deployed process was
verified running. A valid account was subsequently saved through the Radon page;
live ingestion and dashboard rendering were verified at 09:26 EDT. Two devices
were returned; one available reading of 23 Bq/m3 was imported with the actual
source time 2026-10-08T13:22:10.203726+00:00. The unavailable zero-valued device
was skipped. The first Mac diagnostic authenticated and discovered two
devices, but saved neither credentials nor readings; a later login was rejected.

The service reads owner-only `ecosense-private.json` beside the database, with
`email` and `password` fields. No credentials enter Git, command-line arguments,
or journal messages. Replacing this file is picked up automatically. The service
polls every minute, reauthenticates on expired authorization, backs off on network
failure and rejected credentials, and resumes after process or host restart.
No interactive prompts are issued by the service.

The Radon page now has a one-time connection form when credentials are missing or
rejected. `/api/ecosense/connect` verifies the supplied EcoSense account before
atomically saving its owner-only credential file. It imports the first available
measurements immediately; the persistent service continues polling thereafter.
Failed authentication preserves any previously saved credentials. Responses and
the page are marked `no-store`, passwords are never prefilled or returned, and
the existing loopback/same-origin mutation guards apply. The connection form
and endpoint were verified on SER8, and 35 targeted tests pass locally.

`ecosense-status.json` records health; `ecosense-device-snapshot.json` privately
retains only the device fields needed to diagnose timestamp mapping. Inspect logs
with `journalctl --user -u ecosense-collector`. The process logs counts and error
classes rather than exception strings that could contain secrets.

The mapper uses only `last_radon_update_time`, accepting explicit offset/UTC ISO
times and plausible Unix seconds/milliseconds. Actual EcoQube dates omit the UTC
suffix; these are accepted only when the same response's `last_update_time`
matches its explicit Unix `last_update_ts` within one second, corroborating UTC.
The device's `time_zone` is a display preference, not a source-time offset.
Missing, uncorroborated naive, stale or future times are skipped rather than
replaced with retrieval time. `radon_level` is
interpreted as Bq/m3 following the community adapter implementation, and zero
is skipped as unavailable. The actual timestamp encoding was verified against
the independent epoch field. Exact retries use the existing idempotent
radon ingestion function.

Deployment preserves the previous dashboard files in a timestamped radon-service
backup. `radon-runtime` points at the matching installed Python release; update
that link during a future release upgrade. Existing energy polling is unchanged.

The following design/probe notes describe the earlier unconnected implementation;
the persistent deployment status above supersedes their pending items.

Initial status: issue #83; validated storage and a read-only `/radon` history table and seven-day chart. No live EcoQube credentials, device
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

## Local Ingestion API

Verified adapters may POST a JSON object containing an `observations` array to
`/api/radon/readings` on the collector's loopback server. Each observation uses
the fields documented above. This endpoint uses the same local-host,
same-origin mutation protections as the rest of the app; do not expose it publicly.
Remote access requires a private tunnel, not a public listener.

The response reports `inserted` and `duplicates`. Invalid units, missing or
timezone-free measurement timestamps, unavailable values and conflicting retries
return HTTP 400 without partial inserts. An exact retry does not duplicate data.
The endpoint does not authenticate to EcoSense or infer timestamps. Its tests use
synthetic fixtures, including 0.7 pCi/L = 25.9 Bq/m3; those fixtures are not the
user's confirmed device observation and are never inserted into production.

After ingestion, refresh `/radon` and select the source/sensor and history window.
Readings are stored in `energy.db`'s `radon_readings` table, separate from energy
consumption. Collector-to-Mac radon synchronization remains to be implemented.

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

## Recorded dashboard

Open `/radon` using the Radon navigation tab. Day and Week use hourly sample means; Month and Year use daily sample means. Sensor and window selection are carried in the URL. The first recorded sensor is selected automatically only when no identity was requested. Missing buckets stay blank, old samples are not presented as live, and no safety classification is inferred. Refresh the page after importing new observations. Collection is not enabled by this page.

## Timestamp diagnostics

The probe reports `candidate_timestamps_utc` only for recognized top-level time
fields containing timezone-qualified ISO timestamps. It normalizes those values
to UTC without printing arbitrary raw strings. Naive dates and numeric epoch
values are omitted rather than guessed. A device update, creation or last-seen
time is **not** necessarily the radon measurement time: compare it with the
vendor app before mapping any field. `measurement_time_verified` and
`history_ingested` remain false; diagnostics never insert observations.
