# Kasa Integration Verification Plan

Status: issue #66. The current Settings pane is a placeholder, not an active
integration. No successful physical switch or off-network test is recorded.

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

Remaining: identify actual devices, prove local communication, implement the
backend, and prove the off-network path. No current compatibility or remote-control
claim is justified by mock tests alone.
