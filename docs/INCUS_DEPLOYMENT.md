# SER8 Incus deployment

For reusable native macOS, native Linux and container instructions, see
[Deployment options](DEPLOYMENT_OPTIONS.md).

## Production receipt - 2026-10-08

The `energy-monitor` instance became the authoritative collector on
release **2.3.35** from merged commit
`c03c68fd6b16387c0a206ae1562160d14a24b682`. The earlier staging records below
are historical; they do not describe the production endpoint. The table records
cutover-time paths; check `/api/version` and `/etc/energy-monitor/deployment.json`
for the active release after subsequent upgrades.

| Component | Deployment at cutover |
| --- | --- |
| Reviewed source | `/opt/energy-monitor/releases/2.3.35` |
| Independent locked Python runtime | `/opt/energy-monitor/runtimes/2.3.35` |
| Private production state | `/var/lib/energy-monitor/collector`, owner `energy`, mode 0700 |
| Private environment | `/etc/energy-monitor/collector-production.env`, root, mode 0600 |
| Source/unit manifest | `/etc/energy-monitor/deployment.json` |
| Flask | Guest loopback `127.0.0.1:5051` |
| Host-to-guest proxy | Host loopback `127.0.0.1:15033` |
| Mac client | Existing loopback `127.0.0.1:15001`, SSH-forwarded to host 15033 |
| Matter controller | Original commissioned controller on SER8, bridged to guest loopback 5580 |
| Public HTTPS/OAuth | Not activated; see [public access gates](PUBLIC_ACCESS.md) |

The final stopped-writer online backup passed integrity checking with **99,705
readings**, through 19:55:25 local time. All **30 existing tables** matched their
row-content fingerprints after transfer, including sync identity/generations,
change journals, Aqara labels, panel metadata and Kasa associations. Private
settings, credentials and the existing reports directory were preserved.
The installed units match the generated source-template hashes.

All six guest units are enabled and active: dashboard, Emporia poller, Kasa,
Aqara event capture, EcoSense and the optional Mitsubishi collector. The latter
is idle until its module is configured; an active service is not a successful
Comfort account connection. Kasa retains its 10-second capture cadence, and
all six registered endpoints reported successfully. Aqara observation time
advanced for the six recorded sensors. EcoSense resumed successful checks;
an already-recorded measurement remained a duplicate, not a fabricated new
radon sample. Emporia recorded new main readings with healthy heartbeats.

The original six native app units are stopped and disabled, not deleted.
The native Matter controller remained active with the same PID/fabric.
There is only one authoritative writer deployment. Private rollback snapshots
and the previous guest dashboard unit remain outside Git.

The Mac's original app process remained running. Its existing Keychain token,
collector origin and separate cache were preserved. Readings and radon cache
cursors caught up to their source high-water marks, and tunnel recovery returned
healthy. The API reported 2.3.35 and fresh menu data through the Mac endpoint.
Both sync APIs returned 401 without credentials and 200 with the existing token.
The bill-derived settings remained 22.58 cents/kWh plus $11.99 monthly fixed cost.

An actual restart of **only this container** restored all six services and fresh
collection. Other instances retained their running states and the Matter process
was untouched. Full SER8 reboot, Mac sleep and physical-sensor accuracy are not
established by this check. The 243-test suite passed locally and in the guest
(seven platform/tool skips in the guest), with all release CI jobs green.

For repeat deployments, use the [migration and rollback checklist](COLLECTOR_MIGRATION.md).
Do not reactivate the old native poller against the preserved identity while
the guest is collecting.

## Original staging and isolation

The `energy-monitor` Ubuntu 24.04 container was created on SER8 on 2026-10-08
after inspecting every existing instance and finding no energy deployment.
It has two CPU cores and a 2 GiB memory limit. The original staging release 2.3.31 came from merged
Git commit 3865e36, with locked dependencies in its own Python virtualenv.

- Code: `/opt/energy-monitor/releases/2.3.31`
- Runtime user: `energy`
- Private data: `/var/lib/energy-monitor`, mode 0700
- Dashboard: container loopback `127.0.0.1:5051`
- Collector credentials and production database: not migrated yet
- Public routing and authentication: not enabled

Validation: all 213 tests passed in the container (six macOS-only skips).
The rendered, hardened dashboard unit passed systemd verification and serves
v2.3.31. Circuits, Aqara, EcoQube, Mitsubishi and Kasa returned HTTP 200 with
the mobile navigation included. No collector credentials were installed.
An actual container restart restored the dashboard automatically; its listener
remained restricted to 127.0.0.1:5051. Original host collectors remained active.

During staging, the host's existing collectors remained authoritative until cutover.
Do not start another Emporia poller against a copied collector identity. Do not
replace the host release files while another agent is modifying them.

## Container signal policy

On this host, AppArmor denied SIGTERM from a parent Python process to its own
child, even in a minimal `subprocess.Popen(['sleep', '2'])` reproduction. Kernel
audit records showed the peer as the same container's stacked unconfined profile.
A narrow instance-specific `raw.apparmor` rule restored child termination:

```text
signal (send, receive) peer="incus-energy-monitor_</var/lib/incus>//&unconfined",
```

This is not an unconfined container or a host-wide AppArmor change. The exact
profile name is host-specific; do not blindly reuse it for other instances.
Retest parent/child termination and the real HTTP integration tests after Incus
or host security-policy upgrades. Keep the rule out of unrelated containers.

## Production cutover gates

1. Preserve and reconcile the current host source changes before selecting the
   production release. Back up SQLite with its online backup API, not by copying
   an active WAL database file alone.
2. Transfer settings and credentials privately, with owner-only permissions.
   Inspect paths and service users rather than copying host units unchanged.
3. Pause only the original collectors during the final transfer, then start
   container collectors and verify timestamps, device identities and row counts.
   Preserve the Aqara Matter fabric and its event transport explicitly.
4. If publishing beyond the private SSH path, enable authenticated HTTPS only
   after PUBLIC_ACCESS.md passes. Keep Flask private and verify other hosted sites.
5. Point the Mac at the verified endpoint, test offline catch-up, and ensure the
   old collectors stay disabled. Retain the original installation for rollback.

The private production transfer and desktop switch passed the gates above;
public access remains a separate, unfulfilled gate.

## Migration preflight - 2026-10-08

An online SQLite backup passed integrity verification with 98,622 readings,
through 18:58:21 local time. Its bytes were verified identical after private
transfer to the guest, alongside the existing owner-only credentials/settings.
At that stage the files were kept separately, not activated; no second poller
was started. The later final production snapshot superseded this preflight.

The guest timezone was corrected from Etc/UTC to America/New_York, matching the
existing collector's naive timestamp and calendar-query assumptions. Boot
autostart is explicitly enabled. Two instance-specific loopback proxy devices
were added without changing other services or exposing a public listener:

```bash
incus config device add energy-monitor dashboard-private proxy \
  listen=tcp:127.0.0.1:15033 connect=tcp:127.0.0.1:5051
incus config device add energy-monitor matter-controller proxy bind=instance \
  listen=tcp:127.0.0.1:5580 connect=tcp:127.0.0.1:5580
```

The commissioned Matter controller remains on SER8; the guest bridge returned
its cached M3 node without pairing or control commands. The known Kasa endpoint
was reachable over TCP. This follows the supported bidirectional
[Incus proxy device](https://linuxcontainers.org/incus/docs/main/reference/devices_proxy/)
mechanism; it does not migrate or duplicate the Matter fabric.

That preflight required pausing only the original writers for a new final
snapshot, preserving sync identity/journals, remapping the private environment's
DB_PATH, and explicitly enabling the selected guest collectors. The laptop then targeted
the original host dashboard; its later switch is recorded in the production receipt.

## Source preservation and deployment profiles

The live native dashboard's additional menu usage/cost fields and stored radon
indicator are preserved in release 2.3.33, merged as Git commit 036a105 (PR #126).
The staged 2.3.33 energy.py, web.py, radon.py and VERSION hashes were verified
against the pushed source. Its 231 tests passed in Incus (six macOS-only skips).
At that preflight stage the active guest dashboard still ran 2.3.31;
staged source alone was not a cutover.

Before cutover, the native host used user-manager units: dashboard and optional
collectors used 2.3.30, while Emporia polling used 2.3.26. Those services remain
preserved but are now stopped and disabled. A dedicated-user system deployment
can render the same Kasa
10-second cadence using prepare_linux_deployment.py with --kasa-interval 10.
Do not copy the native host's absolute paths into a new system or guest.

Both deployment options share the committed code, locked dependencies and six
service templates. Matter controller installation/version and commissioned
fabric remain separate private prerequisites. No credentials, database backups
or sensor room assignments belong in Git.
