# Kasa Integration Verification Plan

Status: issue #66. Settings provides a single-device read-only state probe.
No successful physical switch or off-network test is recorded. Device registration
and a separate read-only collector are implemented; automatic service startup,
remote replication and control remain incomplete.

## Compatibility

Verify model, hardware revision, firmware, and exposed capabilities for each
actual device. Local communication does not always mean credential-free:
KS205 requires authentication, and newer HS200 versions may require it.
The supported-device list is not proof that the user's installed revision works.

Source: https://python-kasa.readthedocs.io/en/latest/

## Acceptance Tests

1. Select one known switch by its router-assigned IP and record its model/revision.
2. Use bounded, read-only discovery and state requests. Do not pair, reset,
   update firmware, or toggle a device during discovery.
3. If authentication is required, provide credentials privately; never put them
   in command arguments, logs, diagnostic output, or committed settings.
4. Check reported state against the physical switch and vendor app. Unreachable
   and unauthorized must be distinct from OFF. Missing power telemetry is not zero.
5. Only after explicit approval, change one noncritical light, verify the physical
   result and a fresh state read, then restore its original state.
6. Repeat after switch reboot and collector restart. Use a DHCP reservation or
   re-discovery instead of assuming an address never changes.
7. Test away from the home network through an authenticated private connection
   to SER8. SER8 communicates locally with switches; do not expose device ports
   or an unauthenticated control endpoint to the internet.
8. Test expired credentials, device offline, collector offline, and stale client
   state. A failed command must not display a successful state change.

## Implementation Gates

Begin with read-only monitoring. Controls require explicit device selection,
CSRF/authentication safeguards, bounded requests, capability checks, and fresh
post-command verification. Credential storage and collector-to-client protocol
must be implemented and verified before remote control is considered complete.

Remaining: identify actual devices, prove local communication, implement
automatic monitoring startup and safeguarded controls, and prove the off-network path. No current compatibility or remote-control
claim is justified by mock tests alone.

## Read-Only Probe

Open Settings, select Kasa, enter one router-assigned private IPv4 address, and
select **Check state — no control**. Supply an account and password only if the
actual device requires authentication. The server does not persist credentials;
the form clears both fields after success or failure. Requests are bounded to
ten seconds plus up to two seconds of disconnect cleanup. No subnet broadcast,
command, pairing or firmware operation is performed. Query time is shown, not
a claimed sensor measurement time. Failed requests show unknown, never OFF.

For collector-side diagnostics, use `venv/bin/python3 kasa_monitor.py --host DEVICE_IP`
or add `--login` for hidden-password entry. Do not put credentials in command
arguments or chat. The locked library is python-kasa 0.11.0.1. Test fixtures prove
request guards, cleanup and error handling; they do not prove hardware support.

## Registered Monitoring

Register a label and private IPv4 address under Settings > Kasa. Registration
stores no credentials and makes no device request. Start the separate collector
from the same installation and with the same `DB_PATH` as the dashboard:

```bash
venv/bin/python3 -u kasa_collect.py --once
venv/bin/python3 -u kasa_collect.py --interval 60
```

Devices needing authentication require `KASA_USERNAME` and `KASA_PASSWORD` in
the collector's private process environment. The one-shot Settings probe does
not supply credentials to this separate process. Do not put secrets in command
arguments or committed service files.

Reload Settings to see the latest recorded query. Failed queries become unknown,
not OFF; timestamps are collector query times, not device measurement times.
The first successful query pins the reported hardware identity. If another
device subsequently occupies that IP, it is reported as `DeviceIdentityChanged`
rather than silently accepted. Remove and re-register only after verifying the
physical replacement; removal deletes that device's local observation history.
Registered monitoring is local to this database and is not yet replicated to
remote clients. It does not provide power measurements or switch controls.
