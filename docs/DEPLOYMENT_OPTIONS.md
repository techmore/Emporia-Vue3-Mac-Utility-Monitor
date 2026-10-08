# Deployment options

The application supports a native macOS app, a native Linux collector, or a
Linux collector inside Incus/LXD. Use one authoritative collector database.
The Mac can be a client of either Linux option, with local cached history.

## Native macOS

```bash
brew install techmore/tap/energy-monitor
brew services start techmore/tap/energy-monitor
```

This is a Homebrew formula, not a cask. For source development, create `venv`,
install `requirements.lock`, and run `./build.sh`. Existing build and launch
scripts retain the native app, icon, and menu dropdown. Do not start a native
poller when SER8 is already authoritative. Configure Collector Connection in
the menu to use a remote collector; see COLLECTOR_CLIENT.md for cached history.
Browser OAuth onboarding is planned in issue #123, not shipped yet.
For optional automatic repair of a stalled private SSH forward, see
[Connection recovery](CONNECTION_RECOVERY.md).

## Native Linux or a Linux container

Inside the chosen Linux system, use a versioned, reviewed source checkout under
`/opt/energy-monitor/releases/`. Create a dedicated non-root `energy` user,
an owner-only `/var/lib/energy-monitor`, and the source virtualenv:

```bash
# Run from the reviewed release directory.
python3 -m venv venv
venv/bin/pip install -r requirements.lock
python3 scripts/prepare_linux_deployment.py \
  --output /tmp/energy-service-plan \
  --user energy \
  --code "$PWD" \
  --data /var/lib/energy-monitor \
  --environment /etc/energy-monitor/collector.env
```

The output directory must not already exist. This generates six system service
units and a template-checksum manifest; it does not install or start anything.
It rejects root service users, unsafe paths and unresolved placeholders.
The existing optional user-manager templates remain available separately.
To preserve the verified SER8 Kasa cadence, add `--kasa-interval 10`; otherwise
the default is 60 seconds. The selected interval is recorded in deployment.json.
This option is supported identically on native Linux and inside Incus/LXD.

Create `/etc/energy-monitor/collector.env` as root, mode 0600, outside Git:

```text
DB_PATH=/var/lib/energy-monitor/energy.db
FLASK_PORT=5051
```

Use the same DB_PATH for every enabled service. Transfer provider credentials
privately; never paste tokens into this document or commit runtime settings.
Match the original collector's timezone before migrating existing naive/local
timestamps. A new guest's UTC default is not safe for this database. Longer-term
UTC storage and explicit reporting-timezone migration need a separate plan.
Validate the generated units, then install only the intended services:

```bash
sudo systemd-analyze verify /tmp/energy-service-plan/*.service
sudo install -o root -g root -m 0644 \
  /tmp/energy-service-plan/energy-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now energy-dashboard.service
curl --fail http://127.0.0.1:5051/api/version
```

Collector units require explicit installation and enablement after credentials,
data migration, and verification. Aqara Matter additionally requires the existing
commissioned Matter controller on guest-local port 5580; that controller and its
fabric are not provisioned by this script. Kasa requires LAN reachability, and
Mitsubishi remains disabled until configured. Follow INCUS_DEPLOYMENT.md before
moving the actual SER8 collector state.

## Create an isolated Incus instance

Run on an Incus host. For LXD, use its corresponding `lxc` CLI and verify image
remote availability first; the actual deployment was tested with Incus.

```bash
incus launch images:ubuntu/24.04 energy-monitor \
  -c limits.memory=2GiB -c limits.cpu=2
incus exec energy-monitor -- bash
```

The command refuses an existing instance rather than replacing it. Inside the
guest, install Python virtualenv support and CA certificates, then follow the
Linux instructions above. Transfer source using `git archive` from the intended
commit, not a dirty checkout containing credentials or live database files.
Record the commit, source hashes, image, rendered units and instance configuration
with each deployment. Instance metadata can contain sensitive custom settings;
review it before saving it to Git.

Do not expose Flask publicly or change other containers. Publish only through an
authenticated HTTPS proxy after PUBLIC_ACCESS.md passes. See INCUS_DEPLOYMENT.md
for the narrow, host-specific AppArmor signal exception verified on SER8.

## Verification and rollback

- Run the Python suite under the runtime user and confirm real HTTP tests pass.
- Check `/api/version`, all selected sections, timestamps and database integrity.
- Restart only the selected instance/service and verify automatic recovery.
- Keep a private online SQLite backup, previous source release and prior units.
- Stop the new collectors before restoring old collectors; never run both.
- Public TLS, user authentication, desktop sync and provider collection require
  separate acceptance checks. A running dashboard does not prove collection.

All deployment scripts, templates and documentation are included by release.sh.
Runtime databases, credentials and settings intentionally are not Git-managed.
Ignore rules also cover alternate SQLite filenames, collector.env and EcoSense
credentials/status/snapshots. Git ignore rules do not remove already tracked
files or sanitize diagnostics; review staged files before every commit.
