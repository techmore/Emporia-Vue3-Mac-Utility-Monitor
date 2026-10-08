# Optional Local Aqara Matter Collection

This preserves the existing SER8 M3 integration. `aqara_matter_collect.py` reads
the local Matter server's WebSocket at `127.0.0.1:5580/ws`, receives node/attribute
events and requests cached node snapshots every minute. It does not pair, reset,
update firmware or control devices. `aqara_local.py` decodes the bridged
temperature, humidity, battery and reachability attributes into SQLite.

These timestamps are **collector observation times**, not sensor measurement
times. Periodic snapshots may contain unchanged cached values. Neither a recent
snapshot nor the bridge's reported reachability proves a recent physical
measurement. The Aqara page labels collector time, prefers existing local rows
over cloud requests and ages out its online indicator after three minutes.
Missing/invalid measurements stay unknown. Pressure is not exposed by this bridge.

## Deployment and Removal

The Matter server is an external, separately installed runtime, not a bundled
dependency of the macOS app or Python wheelhouse. Existing commissioned fabric
storage must be preserved; do not reinstall/re-pair the hub during an app upgrade.
Protect the storage directory and keep it out of Git and published diagnostics.

`setup/aqara-matter.service` is an opt-in template. Set `__NODE_BIN__` to the actual
Node executable, `__MATTER_ROOT__` to the installed Matter runtime,
`__MATTER_STORAGE__` to its private storage and `__MATTER_INTERFACE__` to the
verified interface reaching the hub. This template is not an installer and does
not pin or download the external runtime. Verify that installation/version
separately before using it on another host.

`setup/aqara-local-collector.service` is a separate Python user-service template.
Set `__PROJECT_ROOT__` to the installed app source and `__DATA_ROOT__` to its
private data directory. Use the same absolute `DB_PATH` as the dashboard. Validate
the rendered services, then enable explicitly. Credentials and commissioning
data are not placed in either unit's command arguments.

Stop/disable the Python collector to stop recording. Stop/disable the Matter
service only if no other integration uses that local server. Retain the SQLite
history and commissioned fabric unless the owner explicitly requests deletion.

## Sensor Display and Recorded Trends

Aqara has its own desktop navigation tab and retains the mobile device tab.
Temperature defaults to Fahrenheit; the display selector can show Celsius while
stored observations and CSV temperature values remain Celsius. Each card shows
a four-hour trend by default. Explore history focuses one sensor; Plot all uses
a shared comparison axis. Windows are 4 hours, 24 hours, 7 days, 30 days and all
retained observations. Select temperature or humidity independently.

Charts use UTC buckets (5 minutes for the default view), with mean, min/max and
online collector-observation count in the tooltip. All-history output is bounded
to at most 121 buckets per sensor. Empty or entirely offline buckets break lines;
zeros remain real values. Future observations are excluded from charts. These
are collector-observation trends, not proof of fresh physical measurements;
periodic Matter snapshots can repeat cached values. No cloud request is made
when recorded local sensor data exists.

Expand Room label to assign a verified name or leave it blank to restore the
sensor's default name. Labels live only in aqara_local_labels in the runtime
SQLite database and survive later collector writes; observation names and
measurements are not rewritten. Identity, length, request size and explicit
same-origin checks protect this endpoint. No room names are seeded in source.

## History Export

The Aqara page links to `/api/aqara/local/history.csv`. The export streams batches
instead of loading the entire retained history into memory and escapes string
cells that could be interpreted as spreadsheet formulas. Source values remain
in the database. The normal database retention window applies.

## Verification

Compare each displayed sensor identity/value against the Aqara app or physical
sensor; generic names are not proof of room assignment. Verify event observations
and periodic snapshots separately. Disconnecting collection must eventually
clear the online indicator; do not interpret cached snapshots as missing-device
proof. Tests validate null sentinels, finite/ranged values, unknown reachability,
local page routing and spreadsheet-safe streaming export. They do not prove
physical sensor accuracy or commissioning/reboot recovery.
