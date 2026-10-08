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
