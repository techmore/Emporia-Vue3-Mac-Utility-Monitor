# SER8 Incus deployment

For reusable native macOS, native Linux and container instructions, see
[Deployment options](DEPLOYMENT_OPTIONS.md).

## Instance and isolation

The `energy-monitor` Ubuntu 24.04 container was created on SER8 on 2026-10-08
after inspecting every existing instance and finding no energy deployment.
It has two CPU cores and a 2 GiB memory limit. Release 2.3.31 comes from merged
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

The host's existing collectors remain authoritative until a deliberate cutover.
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
4. Enable authenticated HTTPS only after the gates in PUBLIC_ACCESS.md pass.
   Keep the Flask listener private and verify all existing hosted sites.
5. Point the Mac at the verified endpoint, test offline catch-up, and ensure the
   old collectors stay disabled. Retain the original installation for rollback.

Until these gates pass, this container is staging, not the production collector.

## Migration preflight - 2026-10-08

An online SQLite backup passed integrity verification with 98,622 readings,
through 18:58:21 local time. Its bytes were verified identical after private
transfer to the guest, alongside the existing owner-only credentials/settings.
These files are staged separately, not activated; no second poller was started.

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

Before cutover, take a new final snapshot after pausing only the original writers,
preserve sync identity and journal state, remap the private environment's DB_PATH,
and explicitly enable the selected guest collectors. The laptop still targets
the original host dashboard; switching its tunnel must be deliberate and verified.
