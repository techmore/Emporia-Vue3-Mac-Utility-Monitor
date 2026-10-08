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
