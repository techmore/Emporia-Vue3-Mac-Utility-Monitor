# Collector migration and rollback

Use this checklist when moving an existing native collector into Incus/LXD or
another native Linux installation. It is a deliberate state transfer, not a
second collector installation. Incus is the tested container manager; validate
the corresponding `lxc` commands before using LXD.

## Prepare without interrupting collection

1. Inventory the actual source, service commands, working directories, private
   state, timezone and enabled/running units. Preserve unexpected source changes
   before selecting a release. Keep unrelated processes and containers untouched.
2. Archive a reviewed Git commit, excluding runtime files. Install its exact
   `requirements.lock` into an independent versioned runtime. Record source hashes,
   dependency versions and the source commit; staging source is not deployment.
3. Create the target non-root runtime user and private data directory. Match the
   original reporting timezone before opening a database with naive timestamps.
   Do not silently convert timestamps or reset the collector identity.
4. Render units with `scripts/prepare_linux_deployment.py`, choosing verified
   paths and cadence. Validate them with `systemd-analyze verify`. Save the
   generated manifest somewhere persistent, not only `/tmp`.
5. Establish private reachability for the dashboard, known Kasa devices and the
   existing Matter controller. Preserve the commissioned Matter fabric; do not
   pair another controller or send physical control commands as a migration test.
6. Run the suite as the target runtime user, with an isolated test database.
   Record platform skips. Back up existing target units and the Mac tunnel and
   watchdog configuration privately. Define rollback before stopping writers.

For the verified SER8 transfer the ownership boundary is explicit:

| Original host user unit | Target guest system unit |
| --- | --- |
| `energy-dashboard` | `energy-dashboard` |
| `energy-poller` | `energy-poller` |
| `energy-kasa` | `kasa-collector` |
| `aqara-local-collector` | `aqara-local-collector` |
| `ecosense-collector` | `ecosense-collector` |
| `mitsubishi-collector` | `mitsubishi-collector` |

Host `aqara-matter` stays running and is **not** in the stop/disable list.
Do not assume these names or paths for a different installation.

## Final state transfer

1. Stop and disable only the verified original app services. Disabling prevents
   a host restart from creating a second writer after target activation. Verify
   stopped processes, including any separately launched poller or dashboard.
2. Acquire the source's existing `energy._poller_lock()` and retain its stable
   lock file. Take a fresh `energy.backup_database()` snapshot and require a
   successful integrity check. Never copy an active SQLite main file without
   its WAL state or unlink a live lock file.
3. Preserve every application table, including IDs, labels, panel slots, device
   links, stream identities/generations and change journals. Record counts and
   hashes of sorted row contents for every existing table while writers are
   stopped. Verify snapshot bytes before opening the transferred database, then
   compare all table fingerprints before starting target services.
4. Transfer credentials, settings and retained reports/photos privately. Inspect
   optional files rather than assuming they exist. Exclude old heartbeat files,
   locks, logs and previous backup directories from active target state.
5. Remap only installation-specific environment paths. Preserve the existing
   sync token privately; never print its value. Use root-owned mode 0600 for the
   system-service environment and owner-only credentials/data. Refuse symlinks,
   unknown runtime files or an existing target data directory until reviewed.
6. Install the verified units and activate the target only after transfer checks
   pass. Ensure old writers remain disabled. Keep private source snapshots and
   original installation files available for rollback.

## Accept the new collector

- Require a new Emporia main reading and healthy heartbeat, not just HTTP 200.
- Verify subsequent Aqara observations and successful Kasa queries for registered
  devices. A cached Matter observation does not prove a new sensor measurement.
- Verify EcoSense checks. Duplicate measurements are valid and must not acquire
  a new measurement timestamp just because a collector restarted.
- Verify settings, billing rate, module state, room labels and panel associations.
- Test every enabled page and menu API. Missing optional credentials must show
  disconnected/disabled state, not pretend successful device integration.
- Check both sync APIs with and without authorization. Preserve source identity,
  generation and monotonic cursor/high-water marks.
- Switch only the exact owned Mac tunnel and its watchdog after the target is
  healthy. Keep the origin and Keychain account unchanged when only forwarding
  changes. Reload launchd arguments with bootout/bootstrap; kickstart alone does
  not reload a changed plist. Do not launch a duplicate native app.
- Confirm cache catch-up, healthy watchdog and fresh native menu data. Restart
  only the selected container/services and confirm automatic restoration.
  Schedule full-machine reboot and laptop sleep acceptance separately.
- Keep public routing disabled until PUBLIC_ACCESS.md passes. Private SSH
  acceptance does not establish OAuth/HTTPS access.

## Rollback without discarding new data

Stop and disable **all target writers first** and verify they exited. If the
target has collected anything, take another verified online backup of its
current database and preserve updated credentials/settings before returning to
the original installation. Retain the pre-transfer snapshot separately; blindly
restoring it would lose post-cutover samples and journal changes.

With both deployments stopped, verify schema compatibility with the previous
release, checkpoint/close the original database and restore the verified current
snapshot and private runtime files. Never replace only a main SQLite file beside
live connections or stale WAL files. Restore the original unit enablement and
start only the original writers after releasing the retained source lock.
Restore the owned Mac tunnel/watchdog together only after the original endpoint
is healthy. Recheck identities, cursor continuity and fresh readings.

If backup, compatibility or writer shutdown cannot be verified, stop and request
operator assistance rather than creating concurrent collectors or deleting data.
Record the rollback result privately. Commit reusable source, templates and
redacted deployment evidence; never commit databases, tokens or commissioned
Matter storage.
