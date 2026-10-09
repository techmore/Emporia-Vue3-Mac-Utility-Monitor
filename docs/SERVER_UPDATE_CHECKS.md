# Automatic server update checks

The collector can check its deployed HTTP version against GitHub's latest stable
release and the VERSION file at an immutable merged main commit. Checking only
releases is insufficient: SER8 currently runs a version newer than the latest
published release. Update availability does not automatically deploy code or
migrate the database.

Install inside the existing Linux system or energy-monitor Incus/LXD guest as
root, where the existing service user is `energy` and the dashboard uses guest
loopback port 5051. Other installations must adapt those two values explicitly.

```sh
install -d -m 755 /usr/local/libexec/energy-monitor
install -m 644 scripts/check_server_updates.py /usr/local/libexec/energy-monitor/
install -m 644 setup/energy-monitor-update-check.service /etc/systemd/system/
install -m 644 setup/energy-monitor-update-check.timer /etc/systemd/system/
systemd-analyze verify /etc/systemd/system/energy-monitor-update-check.service /etc/systemd/system/energy-monitor-update-check.timer
systemctl daemon-reload
systemctl enable --now energy-monitor-update-check.timer
systemctl start energy-monitor-update-check.service
systemctl list-timers --all energy-monitor-update-check.timer --no-pager
journalctl -u energy-monitor-update-check.service -n 5 --no-pager
```

Checks run at 08:00 and 20:00 in the server's timezone, with up to five minutes
of jitter. Persistent scheduling catches a missed check after restart. Atomic
private status lives at `/var/lib/energy-monitor-updates/status.json`; failures
replace status with error/partial instead of reporting an unavailable source as
up-to-date. Each check has bounded HTTP requests and a 90-second service limit.
The checker uses public read-only GitHub requests and needs no account password.

SER8 status commands from its host:

```sh
incus exec energy-monitor -- systemctl list-timers --all energy-monitor-update-check.timer --no-pager
incus exec energy-monitor -- cat /var/lib/energy-monitor-updates/status.json
incus exec energy-monitor -- journalctl -u energy-monitor-update-check.service -n 5 --no-pager
```

Disable scheduled checking with `systemctl disable --now
energy-monitor-update-check.timer` inside the same guest. Existing collector
units and state are independent of this timer.

## SER8 installation verified 2026-10-09

Installed in the existing `energy-monitor` guest through its authorized Incus
administrator. Systemd unit verification passed; the timer is enabled, active
and persistent. Its first actual run completed with exit status zero and wrote
private status identifying deployed 2.3.37, merged main 2.3.39 at commit
`7325bde4d3ec6db2ef9d03cbb8f3d6932741c97f`, and published release v2.3.26.
The newer merged source was correctly flagged without treating the older stable
release as an upgrade. All six existing collector/dashboard services remained
active, with a healthy fresh Emporia heartbeat and zero consecutive errors.
