# Always-On Collector and Mac Client

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
and publishes it through SQLite's backup API only after catching up. Failed
rebuilds preserve the old cache. Different collector identities are still refused.
This retention behavior and automatic checkpoint recovery are integration-tested.
Their operation across the actual SER8/Mac connection remains unverified.

Energy history synchronization does not yet replicate radon observations. The
Radon dashboard and local ingestion API are separate capabilities; do not assume
EcoQube data will appear in the Mac's offline cache until radon replication is
implemented and verified.

Remaining deployment work is to configure and verify the SER8 services, the
private connection, and automatic downloads on the actual Mac installation.
Verify disconnect/reconnect and Mac sleep before retiring local collection.
Never run a shared SQLite database over SMB or NFS.
