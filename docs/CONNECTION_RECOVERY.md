# Collector connection recovery

The native menu already retries reads and preserves offline history. SSH
keepalives and launchd KeepAlive, however, cannot reliably recover an SSH forward
that still has a live process but no longer forwards HTTP. Recovery must test
the application path, not just a PID or listening socket.

## Opt-in macOS tunnel watchdog

For the existing `com.dolbec.energymonitor.tunnel` LaunchAgent, run:

```bash
python3 scripts/install_collector_recovery.py \
  --ssh-target YOUR_USER@YOUR_COLLECTOR \
  --local-port 15001 --remote-port 5051 \
  --python /ABSOLUTE/PERSISTENT/PYTHON3 \
  --activate
```

Choose a persistent installed Python, not a temporary build virtualenv. The
installer refuses a mismatched tunnel; it preserves the SSH arguments and only
hardens the verified agent's permissions. It copies the standalone watchdog into
the user's private Application Support directory, independent of the checkout.
No token or password is needed. SSH keys and known-host verification remain in
effect. This does not publish any public listener or restart the collector.

For the completed SER8 Incus deployment, the remote port is **15033**, not the
former native dashboard's 5051. The existing tunnel and installed watchdog were
both switched and verified. For another collector, use its verified private
port; the installer deliberately refuses mismatched tunnel arguments.

The watchdog runs every 30 seconds and on login:

1. Probe the tunnel's `/api/version` with a five-second timeout, without HTTP
   proxies or redirects. Require a valid collector version response.
2. After three consecutive failures, check the remote collector over a separate,
   bounded SSH connection that does not reuse an SSH control socket.
3. If the remote collector is healthy, restart only the app's loaded tunnel via
   launchctl. The next scheduled HTTP probe verifies whether it recovered.
4. If the server or network is unavailable, leave services alone and back off
   from two minutes up to 32 minutes. A healthy local probe resets the backoff.
5. If the tunnel was intentionally unloaded, do not bootstrap it. Overlapping
   watchdog runs are locked out. Never create another Flask server or poller.

Typical stalled-path detection takes about 90 seconds plus probe time; an
unavailable server cannot be repaired by reconnecting the laptop. Ordinary
HTTP polling still detects a restored connection every 30 seconds, even during
remote-probe backoff. The watchdog is for SSH tunnel mode, not an OAuth/HTTPS
client's credential refresh.

## Status and manual checks

Private files under `~/Library/Application Support/EnergyMonitor/tunnel-recovery`:

- `watchdog-status.json`: last check/healthy time, failure count, restart count,
  cooldown and whether the tunnel, collector or network needs attention.
- `watchdog.log`: status transitions only; no raw SSH errors, tokens or passwords.
- `watchdog-config.json`: SSH target and forwarding ports, mode 0600.

`healthy` describes the transport. It does not mean a poller is collecting fresh
data; the dashboard's timestamps and stale-data indicators remain authoritative.

Verify healthy HTTP and zero unnecessary restarts first. For fault testing,
temporarily stall only the identified owned SSH tunnel process; confirm the
watchdog restarts that label, the dashboard becomes reachable again, and SER8
readings continue advancing. Never use broad process-kill commands. Test remote
outage logic using unit-test mocks rather than disrupting the production server.

Disable automatic recovery without deleting cached history:

```bash
launchctl bootout gui/$(id -u)/com.dolbec.energymonitor.tunnel-watchdog
rm ~/Library/LaunchAgents/com.dolbec.energymonitor.tunnel-watchdog.plist
```

Keep the original tunnel loaded for normal SSH connectivity. Reinstalling
recovery updates only its own helper/agent, not the native application or server.

## Verified Mac/SER8 installation - 2026-10-08

The watchdog was installed with the persistent Homebrew Python runtime and the
existing Tailscale SSH forward (local 15001 to SER8 loopback 5051). A healthy
baseline produced zero restarts. In a controlled fault test, only the verified
owned SSH process was suspended. Failed HTTP probes caused the watchdog to
replace that process through its exact LaunchAgent label; HTTP recovered and
private status returned `healthy`, zero failures and one recovery. The test
included a resume-on-failure guard.

SER8 remained online, its four checked collector services remained active, and
the recorded reading timestamp advanced from 18:43:16 to 18:44:17 local time.
The independent Incus staging dashboard continued serving 2.3.31. This validates
SSH transport repair, not a completed production container cutover or public
OAuth login. Sleep and full-machine reboot recovery still require testing.

The later production Incus cutover retained this watchdog and native app, updating
only the verified tunnel's remote port to 15033. HTTP returned 2.3.35, status
returned healthy, and cached history caught up. A container restart restored
services and the existing Mac connection without launching another app.
See INCUS_DEPLOYMENT.md for that separate deployment receipt.

## Linux desktop tunnel recovery — 2026-10-18

The elementary client was configured for loopback port 5052, but no SSH forward
survived the machine reboot. The independent SER8 collector remained reachable
on host loopback 15033 and returned recorded readings. The unrelated local
5051 deployment had no credentials and was not the client's data source.

`linux/install_tunnel.py` now installs an enabled systemd user service with
noninteractive SSH, strict known-host verification, loopback-only binding,
keepalive failure detection and unlimited delayed reconnect attempts. Installed
on this desktop with local 5052 and the verified SER8 destination; the existing
client origin was retained. HTTP summary returned 2.3.43, online=true and recorded
power. See `linux/README.md` for setup and rollback. Login/reboot behavior follows
the enabled service configuration; a full reboot has not been performed.

## Direct Linux HTTPS connection — 2026-10-18

The final desktop origin is `https://sdolbec-ser8.tailf80972.ts.net:8445`.
The optional SSH forward is stopped and disabled. A first attempt to use the
public Google gateway failed in the embedded WebKitGTK browser (passkeys are
unsupported), so that is not the native client's connection path.

SER8 now uses persistent Tailscale Serve HTTPS port 8445 to proxy host loopback
15033, preserving its existing 8443/8444 services. No Funnel is enabled. The
collector accepts the exact `ENERGY_TAILNET_ORIGIN` only with the configured
`ENERGY_TAILNET_USER` matching Serve's verified `Tailscale-User-Login` header.
Both settings are opt-in; absent settings preserve the previous host policy.
Deploy only behind Tailscale Serve, which supplies the verified identity header,
and keep the backend loopback-bound. A caller-supplied header is not authentication
on a directly exposed backend. Same-origin writes remain enforced.

The public Google-protected site remains unchanged. Valid-certificate private
HTTPS returns recorded power; requests to the tailnet origin without the identity
header return 403. Client Tailscale connectivity is required; no SSH process is
required. The existing collector/poller and database are retained.

Rollback: switch the client origin back, disable only this Serve handler with
`tailscale serve --https=8445 off`, remove the two ENERGY_TAILNET settings and
restart only energy-dashboard. Deployment backups of web.py and the private
environment file use the suffix `.pre-tailnet-20261018` inside the container.

## Public native HTTPS and reboot recovery — 2026-10-18

The final desktop path is now `https://energy.stoverparc.org`, not the earlier
private Tailscale endpoint. The gateway accepts a per-device random password via
its existing htpasswd support and returns its normal secure HttpOnly session.
The server stores only bcrypt; the desktop stores the origin-bound credential
privately. Google sign-in remains available for browsers. No public route was
exempted from authentication and no Google password was stored by this work.

The client performs the HTTPS session exchange on a background thread at startup,
renews after 12 hours and retries outages. No password-bearing redirect is
followed. Its enabled systemd user service starts at graphical login and restarts
a crashed client; the gateway, dashboard and poller are enabled on SER8. The
legacy SSH service stays disabled. See linux/README.md for enrollment and rollback.

Verification: removed only the app's public gateway session, restarted the
client, and it reauthenticated and returned Connected/live readings without a
browser login. A forced client termination exercises systemd restart. Public DNS
resolves to 71.162.207.117 and the route uses the physical network interface,
not tailscale0. Full host reboot is not performed during the owner's other work.
The gateway config backup is `/etc/energy-oauth2-proxy.cfg.pre-device-20261018`.
