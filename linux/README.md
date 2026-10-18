# Elementary OS client

This client connects to an existing collector; it does not start another Emporia poller.
Run `bash linux/install.sh` with GTK 4, WebKitGTK 6, Python GI/Cairo and the Wingpanel 9
build dependencies installed. The installer adds a desktop launcher, login autostart,
and a native Wingpanel indicator. Choose **Collector Connection** to configure an
HTTPS origin. The default is the private Tailscale Serve endpoint
`https://sdolbec-ser8.tailf80972.ts.net:8445`. Keep Tailscale signed in on the
client; no SSH tunnel or embedded Google login is needed. The server accepts
only the configured Tailscale user identity. Never disable TLS verification.
If an older installation still points to a loopback port or the public website,
change **Collector Connection** to this private HTTPS origin. Disable the legacy
forward with `systemctl --user disable --now energy-collector-tunnel.service`.

The public `https://energy.stoverparc.org` site remains available in a regular
browser with Google sign-in. WebKitGTK does not support passkeys, and Google OAuth
should not be run in embedded browsers. Browser cookies do not sign the native
client in; use the private Tailscale endpoint for the native app.

The dashboard retains the collector's web functionality, authentication cookies and
downloads. The native menu shows power, recorded totals and circuit history with
1/7/30-day charts. Summary and requested circuit responses are cached privately;
when disconnected, live readings are cleared and recorded history remains labeled
as cached. This is not yet the Mac client's full SQLite history download/Keychain
sync implementation.

The native menu and history windows mirror `EnergyMonitorApp/Sources/Theme.swift`:
olive/stone light and dark palettes, serif numeric readings, dark service-feed card,
rounded metric cards and compact circuit buttons. Circuits follow physical breaker
positions in two columns (odd slots left, even slots right), with unassigned positions
preserved instead of shifting subsequent circuits. Styles are scoped to this client;
they do not recolor other panel indicators. Desktop GTK light/dark preferences are
followed. The embedded dashboard already uses the same palette from the collector.

For a private collector, install a persistent SSH forward after verifying that
`ssh USER@HOST` works with a trusted host key and noninteractive authentication:

```sh
python3 linux/install_tunnel.py --ssh-target USER@HOST \
  --local-port 15001 --remote-port 15033
```

Then configure `http://127.0.0.1:15001` in **Collector Connection**. Use the
same local port as the configured client origin (for example, `--local-port 5052`
for an existing `http://127.0.0.1:5052` connection). The installer leaves client
settings and collector data untouched. Keep the collector bound to loopback.
No account keys belong in this repo.

The user service starts at login, retries after network/SSH failures, and uses SSH
keepalives to detect stalled connections. It requires `/usr/bin/ssh` and systemd;
Tailscale must already be available if the host is reachable only through it.
Verify the connection and fresh readings:

```sh
systemctl --user status energy-collector-tunnel.service
curl --fail http://127.0.0.1:15001/api/menu-summary
```

A running SSH process alone does not prove the collector has fresh data. Check
`online` and `last_reading`. To remove this forward:

```sh
systemctl --user disable --now energy-collector-tunnel.service
rm ~/.config/systemd/user/energy-collector-tunnel.service
systemctl --user daemon-reload
```

Validation:

```sh
python3 -m unittest discover -s tests -p test_linux_client.py
python3 linux/smoke_test.py  # requires a graphical desktop; isolated synthetic fixture
meson setup /tmp/energy-panel-build linux/wingpanel
ninja -C /tmp/energy-panel-build
```
