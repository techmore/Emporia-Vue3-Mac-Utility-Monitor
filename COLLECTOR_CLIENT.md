# Always-On Collector and Mac Client

## Current SER8 Deployment - 2026-10-08

The authoritative collector has moved from the retained native services into
the `energy-monitor` Incus instance, initially on release 2.3.35. The Mac keeps its existing
origin `http://127.0.0.1:15001`, Keychain credentials and offline cache; its SSH
forward now targets SER8 loopback **15033**, which proxies to guest loopback 5051.
The old native writers are stopped and disabled. The Matter controller/fabric
remains on the host and is bridged privately into the guest.

All 30 transferred database tables matched, new collection and cache catch-up
were verified, and a restart of this container restored its six services.
Public HTTPS/OAuth and full host reboot/Mac sleep acceptance remain pending.
See [production receipt](docs/INCUS_DEPLOYMENT.md) and
[repeat migration/rollback procedure](docs/COLLECTOR_MIGRATION.md).
The native deployment below is retained as historical evidence, not the current
service endpoint. Never run its preserved poller alongside the guest.
Subsequent release upgrades are reflected by `/api/version` and the private
deployment manifest; the original cutover receipt is not a latest-version pointer.

## Original Native SER8 Deployment — 2026-10-08

Release 2.3.26 is installed on the verified Ubuntu SER8. SSH was verified over
Tailscale before deployment; Flask remains bound to 127.0.0.1:5051. No public
listener, port forwarding or Tailscale Funnel was enabled. The Mac connects
through an owner-only, reconnecting SSH LaunchAgent and loopback port 15001.

The released dependency lock installed successfully on Python 3.14.4 with no
dependency conflicts. The actual Linux host passed 170 tests (six Swift-only
tests skipped). A final online SQLite snapshot was taken after quitting the
identified Mac app and confirming its Flask and poller children had exited.
The transferred snapshot passed integrity verification with exactly 85,474
readings, through 2026-10-08T07:24:17.978654. Earlier staging and final source
backups were retained for rollback. Private settings and authentication files
were transferred via SSH, not committed to Git.

Passwordless administrator access is unavailable. Instead, the existing
lingering user manager runs enabled energy-poller and energy-dashboard services.
Their private deployment uses owner-only files, a restrictive umask, unbuffered
Python, restart backoff and NoNewPrivileges. These user units do not claim the
system templates' ProtectSystem/ProtectHome filesystem isolation. The release
lives under ~/.local/lib/energy-monitor/releases/2.3.26 and writable state under
~/.local/share/energy-monitor on SER8. Secrets remain outside the checkout.

The server successfully authenticated, discovered actual Emporia devices and
recorded its first new readings at 07:25:00 with the preserved 22.58-cent rate.
The Mac was reopened in persisted remote-client mode, with a private separate
history cache and automatic downloads using a Keychain token. Its downloader
started and completed; no local Flask or poller process remained.

Controlled disconnection was verified by unloading only the tunnel LaunchAgent.
The native view labeled cached data not live, withheld circuit watts and showed
cached Heat Pump history (1,164 readings, with missing periods kept as gaps).
SER8 continued recording while the Mac connection was unavailable. Restoring
the tunnel restored live readings; the automatic downloader subsequently caught
up with matching cursor/high-water mark and cleared its temporary failure label.
Reports, Trends, Settings and Radon rendered through the actual tunnel without
horizontal overflow at the inspected browser viewport. Radon correctly showed
no readings, not zero concentration.

This is deployment and reconnect evidence, not reboot/sleep verification.
Collector reboot and Mac sleep checks remain required.
The copied snapshot retains its collector identity: never restart the
old Mac poller while SER8 is collecting, and never merge divergent copies.
For rollback, stop SER8 polling first, then explicitly restore local Mac mode.
The older sections below describe mechanisms and original verification gates;
their pending-deployment wording predates this deployment record.

## Implemented

The Mac wrapper accepts `ENERGY_COLLECTOR_URL`. When set, it skips local
Python-environment validation and local Flask startup. Its dashboard, version
request, and Copy Dashboard URL action use the configured origin. It rejects
credentials, paths, query strings, fragments, and non-loopback plain HTTP URLs.
Without the variable, local mode is unchanged.

Version 2.3.4 adds an opt-in local history cache and native offline views, as
described below. The existing separately managed Mac poller is not stopped
automatically; stop it only after verifying collection on the SER8.

## Secure First Deployment

Keep Flask on its configured loopback port on the collector (the source default
is `127.0.0.1:5051`). Do not expose the current
unauthenticated backend on a LAN or the public internet. After verifying SSH
access to the actual collector, forward its loopback port:

```bash
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
  -L 15001:127.0.0.1:5051 USER@COLLECTOR
```

Launch the compiled Mac executable with:

```bash
ENERGY_COLLECTOR_URL=http://127.0.0.1:15001 \
  EnergyMonitorApp/EnergyMonitorApp
```

Quit the existing app first; the single-instance guard remains enabled.
For Finder and login launches, use **Collector Connection** in the app menu to
save the collector origin, then quit and reopen the app. Leave the field blank
to restore local mode. The environment variable takes precedence over the saved
preference. The SSH tunnel must already be running; the app does not create it.
Readiness requires HTTP 200 and a JSON version field from `/api/version`, rather
than accepting an arbitrary responding service as the collector.

## Required Verification Before Cutover

For a verified Linux/systemd collector, `setup/energy-poller.service` and
`setup/energy-dashboard.service` are opt-in templates for the two core processes.
They are not installed or enabled automatically. Render the placeholders with
the non-root service user, absolute project and private data directories, and a
root-owned 0600 environment file outside the repository. Use paths without spaces.
Both units must use the same absolute `DB_PATH`; keep the working/data directory
owner-only and writable by that user. Install locked dependencies into the
project's `venv` first. The poller uses unbuffered output and restart backoff;
the dashboard binds to `127.0.0.1` in code and defaults to port 5051.

Verify rendered units with `systemd-analyze verify` before installing them under
`/etc/systemd/system/`. After the cutover checks below, reload systemd and enable
the explicitly selected units. Confirm service journal output, fresh readings,
loopback-only listening and collection after a collector reboot and Mac sleep.
Do not run a second Emporia poller against the same deployment. Templates and
Linux CI syntax validation do not establish actual SER8 installation or cutover.

Create a consistent standalone snapshot while the existing poller is running:

```bash
venv/bin/python3 energy.py backup /PRIVATE_EXISTING_DIRECTORY/collector-history.db
```

The command uses SQLite's online backup API, checks integrity, reports row counts
and timestamp bounds, and publishes an owner-only file. It refuses to overwrite
an existing destination. Transfer this file securely; credentials and settings
require a separate private transfer. The snapshot does not include later polls.
Do not switch collectors until the final catch-up and duplicate-handling strategy
has been verified.

Continuous and one-shot Emporia polling acquire a nonblocking process lock next
to the canonical database path before authentication. A second cooperating
poller fails without changing health status. The owner-only `.poller.lock` file
is intentionally retained: never delete it while a poller is running. Process
exit releases the kernel lock, including abnormal termination. Different
databases remain independent. This guards one host/database, not duplicate
collectors on different hosts or hard-linked database copies. Older versions
do not honor the lock; identify and stop them explicitly during upgrade/cutover.

1. Inspect the SER8 OS, storage, Python environment, SSH access, and service manager.
2. Back up the source database with SQLite's backup API, not a live raw WAL-file copy.
3. Transfer the consistent backup and private settings securely. Preserve timestamps
   and use the same collector timezone until a UTC migration is implemented.
4. Verify imported row counts and totals before starting collection on the SER8.
5. Confirm fresh readings and poller health across multiple polling intervals.
6. Verify the Mac dashboard, Reports, Settings, and reconnect controls through the tunnel.
7. Stop only the identified Mac poller, then check that server collection continues
   while the Mac sleeps. Keep the backup for rollback.

## History Synchronization

Released collector/client code implements a version-2 readings
change journal with a persistent source identity. Inserts, updates, and deletes
are recorded in the same SQLite transaction as the original mutation; pre-existing
history is seeded once. `/api/sync/readings` returns at most 1,000 changes per page
and requires an `ENERGY_SYNC_TOKEN` of at least 32 characters, passed as a Bearer
header. It is disabled when the token is absent. Keep access behind the loopback
SSH tunnel. Tokens must not be committed or put in URLs.

Unreleased UTC development also supports energy protocol 3, requested with
`protocol_version=3`. It declares timestamp format, reporting zone and measurement
model; legacy protocol 2 remains the default for old clients. UTC inspection
adapters refuse old clients with HTTP 426 rather than send ambiguous timestamps.
The updated downloader requests 3 and accepts legacy collectors' protocol 2.
UTC rows live in a separate cache namespace, with atomically saved format metadata.
Format changes require a new stream generation and complete verified snapshot;
concurrent cache progress prevents stale replacement. Native UTC cache history
uses the declared reporting zone and preserves offsets/microseconds. This has
passed private converted-artifact HTTP/download/offline-native tests, not live
collector activation. Legacy local caches still require matching timezones;
UTC writers/imports and the production cutover remain unfinished (#127/#135).

Development 2.3.45 adds private-tested policy-aware energy writers/imports and
atomic migration generation rotation. A converted snapshot now causes an existing
legacy cache to download a full format-safe replacement automatically; unrelated
sensor/panel data survives. Ordinary writable UTC connections are still rejected.
Source provenance, live activation/rollback and overlap reconciliation remain
unfinished; do not deploy the UTC draft merely because client reset tests pass.

The code is included in the release, but the actual SER8 connection and automatic
downloads have not been configured or verified. `sync_history.py` downloads pages to isolated cache
tables and advances the cursor atomically with each page. It refuses collector
identity changes and HTTP redirects, bounds response memory, and preserves the
saved cursor on interrupted downloads. The cache file is owner-only. Use a
separate cache path, never the active collector database:

```bash
# Supply ENERGY_SYNC_TOKEN privately in the environment on both processes.
venv/bin/python3 sync_history.py --collector http://127.0.0.1:15001 \
  --cache /PRIVATE_EXISTING_DIRECTORY/collector-cache.db
```

`synchronized_at` is only updated when the client has caught up to the page's
high-water mark. The native menu now persists successfully fetched summaries and
viewed circuit histories per collector endpoint, uses private files, and labels
offline data with its cache timestamp. Live watts and breaker safety indicators
are withheld while offline. History never viewed online may not be cached.
Collector Connection also accepts the absolute path to the downloaded cache,
saved per collector URL. After restarting, the native circuit view falls back to
that database when the online history request fails. It requires the collector
and active-device identities from a previously fetched menu summary, rejects
partially synchronized caches, and keeps missing chart buckets blank. It does
not invent trend percentages from sparse offline data. The cache is opened
without CREATE and with SQLite query-only protection; WAL sidecar access is
allowed. Collector and client must currently use the same timezone because
existing readings have naive local timestamps. The web dashboard still requires
a running server. To enable periodic downloads, select **Download history
automatically** and enter the matching server token in Collector Connection.
Tokens are saved in macOS Keychain, not UserDefaults, and passed to the downloader
through its environment, never command-line arguments. Downloads run every 60
seconds only in remote-client mode; overlapping requests are skipped and an
individual subprocess is terminated after ten minutes. A Python environment and
the bundled sync script are required for downloads, though previously cached
native views remain usable without a working server. Restart after configuration.

The poller checks journal size hourly. Above the greater of one million changes
or twice the number of current readings, it transactionally replaces obsolete
changes with a checkpoint of current readings and rotates the stream generation.
The permanent collector identity does not change. This bounds logical journal
growth relative to retained history; SQLite may retain allocated pages for reuse.
The client detects a checkpoint change, downloads to a separate staging database,
and publishes only the energy cache tables in one SQLite transaction after catching up. Failed
rebuilds preserve the old cache. Different collector identities are still refused.
This retention behavior and automatic checkpoint recovery are integration-tested.
Their operation across the actual SER8/Mac connection remains unverified.

The bundled downloader also fetches `/api/sync/radon` with the same private
collector token. Radon observations retain their original units, measurement
timestamps, receipt times and device identities in separate cache tables.
The Radon dashboard reads this cache when configured and labels the last completed
download explicitly; download time does not imply a fresh sensor measurement.
Interrupted downloads retain the saved cursor and previously received data.
Checkpoint recovery stages a complete radon snapshot, then transactionally
replaces only radon cache tables. Local collected radon history is never overwritten
or mixed with remote data. Collector and client must both support the radon endpoint;
an older collector returns an error rather than silently claiming a complete sync.
The journal compacts above the greater of 100,000 changes or twice retained samples.
Automated tests exercise real loopback HTTP downloads, authentication failure,
deletions and checkpoint recovery, but do not prove deployment on the actual SER8.
This replicates recorded observations; it does not connect EcoQube collection.

Remaining deployment work is to configure and verify the SER8 services, the
private connection, and automatic downloads on the actual Mac installation.
Verify disconnect/reconnect and Mac sleep before retiring local collection.
Never run a shared SQLite database over SMB or NFS.
