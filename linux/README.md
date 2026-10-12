# Elementary OS client

This client connects to an existing collector; it does not start another Emporia poller.
Run `bash linux/install.sh` with GTK 4, WebKitGTK 6, Python GI/Cairo and the Wingpanel 9
build dependencies installed. The installer adds a desktop launcher, login autostart,
and a native Wingpanel indicator. Choose **Collector Connection** to configure an
HTTPS origin or a loopback SSH tunnel. Never disable TLS verification.

The dashboard retains the collector's web functionality, authentication cookies and
downloads. The native menu shows power, recorded totals and circuit history with
1/7/30-day charts. Summary and requested circuit responses are cached privately;
when disconnected, live readings are cleared and recorded history remains labeled
as cached. This is not yet the Mac client's full SQLite history download/Keychain
sync implementation.

The native menu and history windows mirror `EnergyMonitorApp/Sources/Theme.swift`:
olive/stone light and dark palettes, serif numeric readings, dark service-feed card,
rounded metric cards and compact circuit buttons. Styles are scoped to this client;
they do not recolor other panel indicators. Desktop GTK light/dark preferences are
followed. The embedded dashboard already uses the same palette from the collector.

For a private collector reachable through Tailscale SSH, forward its loopback port:

```sh
tailscale ssh USER@HOST -N -o ExitOnForwardFailure=yes \
  -L 127.0.0.1:15001:127.0.0.1:15033
```

Then configure `http://127.0.0.1:15001`. Keep the collector bound to loopback.
Host keys are verified through Tailscale SSH. No account keys belong in this repo.

Validation:

```sh
python3 -m unittest discover -s tests -p test_linux_client.py
python3 linux/smoke_test.py  # requires a graphical desktop; isolated synthetic fixture
meson setup /tmp/energy-panel-build linux/wingpanel
ninja -C /tmp/energy-panel-build
```
