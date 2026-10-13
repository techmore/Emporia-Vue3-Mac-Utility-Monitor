# Optional Mitsubishi Comfort Module

Issue #64. Supports read-only Comfort v3 cloud discovery and snapshots. The
PAC-USWHS002-WF-2 uses Comfort. Read-only access has been verified on SER8
with the owner's account and its Upstairs and Downstairs zones. Protocol is community-documented, not a
vendor-supported public API. No new dependency is introduced.

## Add and Remove

Open Settings > Configure Comfort connection, or **HVAC** in the main navigation.
Enter account credentials privately in the page; never put them in chat or shell
arguments. Login retrieves tokens, then queries sites/zones. Only after successful
discovery is the saved connection replaced. The password and raw cloud responses
are not saved. Access/refresh tokens are stored atomically with mode 0600 next to
`DB_PATH` in `mitsubishi-private.json`; protect this directory and exclude tokens
from backups distributed to others. Local file access can still expose tokens.

Remove module connection deletes tokens and the current status file. Cross-process
locking prevents an in-flight collector from recreating tokens after removal.
Collection makes no further network requests while disabled. Historical snapshots
remain in SQLite under the usual retention policy. This does not unlink devices
from the vendor account. Code is a built-in optional adapter, not dynamic plugin
execution; removing the connection does not uninstall Python source files.

## Collection

The web page reads recorded snapshots; page loads do not poll the vendor.
Connect makes one login followed by read-only discovery requests. A separate
collector polls every 60 seconds and refreshes expired tokens without retaining
the password. Failed refresh requires private reconnection. No device control,
configuration, schedule or reboot API is implemented.

```bash
venv/bin/python3 -u mitsubishi_collect.py --once
venv/bin/python3 -u mitsubishi_collect.py
```

Both processes must use the same absolute `DB_PATH`. Optional Linux user-service
template: `setup/mitsubishi-collector.service`. Replace `__PROJECT_ROOT__` and
`__DATA_ROOT__` with the installed source and private data directories. Validate
with `systemd-analyze --user verify`, then explicitly enable the rendered unit.
No service is installed automatically by sign-in. Stop/disable the service to
remove the idle collector process as well as removing the connection in the UI.

## Data Semantics and Limits

Temperature and setpoints are Celsius, also displayed as Fahrenheit. Unknown,
invalid or disconnected values remain unknown. Vendor `connected` is not proof of
sensor freshness. Query timestamps record cloud retrieval, not sensor measurement;
`updatedAt` and `lastStatusChangeAt` are not mislabeled as measurement timestamps.
No compressor power or circuit attribution is inferred. After three minutes
without collection the current cards become stale/empty; history stays visible.
At most four sites and 32 zones per site are accepted, with response-size bounds,
request timeouts, redirects disabled and no response-body/credential logging.

This first version uses cloud data, not the known LAN addresses. Local access
requires separate device credentials and identity checks and remains future work.
Sources: [Comfort v3 protocol](https://github.com/dlarrick/pykumo/blob/master/Cloud_api_v3.md),
[Home Assistant's integration](https://www.home-assistant.io/integrations/mitsubishi_comfort).

## Acceptance

1. With no saved connection, open HVAC: disabled and no invented readings.
2. Connect privately; verify discovered zone names against Comfort. Compare room
   temperature, mode and both setpoints. No HVAC settings should change.
3. Start the collector; verify new retrieval times after at least two intervals.
4. Stop it for over three minutes: current readings must become stale, not live.
5. Remove the connection: tokens/status disappear, history remains, and the idle
   collector makes no requests. Reconnect with bad credentials must not replace
   a working saved connection.

Fixture tests cover these boundaries but do not establish physical telemetry.

## Comparison History

The HVAC page overlays units in distinct colors for 4 hours, 24 hours, 7, 14
or 30 days. Select room temperature, heating/cooling setpoint, humidity or
enabled state; temperatures default to Fahrenheit. Move to prior windows or
choose an ending time. Toggle units in the legend and export selected data to CSV.

Buckets are averages of recorded cloud queries, not physical measurement times.
Missing buckets break lines. Enabled-time estimates cover only successive valid
queries at most three minutes apart and do not establish compressor runtime.
Diagnostics show available vendor schedule, hold, sensor and update metadata;
unsupported fan/vane/outdoor readings are not fabricated. Earlier snapshots may
lack new metadata. Historical periods grow from the retained observations.
