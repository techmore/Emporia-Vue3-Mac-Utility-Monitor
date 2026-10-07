# Always-On Collector and Mac Client

## Implemented

The Mac wrapper accepts `ENERGY_COLLECTOR_URL`. When set, it skips local
Python-environment validation and local Flask startup. Its dashboard, version
request, and Copy Dashboard URL action use the configured origin. It rejects
credentials, paths, query strings, fragments, and non-loopback plain HTTP URLs.
Without the variable, local mode is unchanged.

This is connected-client groundwork, not an offline database replica. The
existing separately managed Mac poller is not stopped automatically.

## Secure First Deployment

Keep Flask on `127.0.0.1:5001` on the collector. Do not expose the current
unauthenticated backend on a LAN or the public internet. After verifying SSH
access to the actual collector, forward its loopback port:

```bash
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
  -L 15001:127.0.0.1:5001 USER@COLLECTOR
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

## Remaining Synchronization Work

Development branch `codex/collector-history-sync` implements a version-1 readings
change journal with a persistent source identity. Inserts, updates, and deletes
are recorded in the same SQLite transaction as the original mutation; pre-existing
history is seeded once. `/api/sync/readings` returns at most 1,000 changes per page
and requires an `ENERGY_SYNC_TOKEN` of at least 32 characters, passed as a Bearer
header. It is disabled when the token is absent. Keep access behind the loopback
SSH tunnel. Tokens must not be committed or put in URLs.

This is not deployed yet. `sync_history.py` downloads pages to isolated cache
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
high-water mark. The native offline UI and journal compaction/retention policy
remain to be implemented. A CLI download alone does not make the Mac app usable offline.
The initial journal stores full history, and changes currently accumulate; storage
growth must be resolved before continuous-production rollout.

Define an authenticated, versioned incremental export protocol with stable
source identities, bounded pagination, and a transactional sync cursor. Account
for updates, deletions, retention, and imported readings rather than assuming
an insertion ID alone captures all changes. A Mac cache must display its last
successful synchronization time and distinguish stale/offline data from live
readings. Never run a shared SQLite database over SMB or NFS.
