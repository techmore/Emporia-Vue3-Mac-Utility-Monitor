# Elementary OS client

This client connects to an existing collector; it does not start another Emporia poller.
Run `bash linux/install.sh` with GTK 4, WebKitGTK 6, Python GI/Cairo and the Wingpanel 9
build dependencies installed. The installer adds a desktop launcher, login autostart,
and a native Wingpanel indicator. Choose **Collector Connection** to configure an
HTTPS origin. The default is `https://energy.stoverparc.org` over ordinary
internet HTTPS. The native app uses a separately enrolled device credential;
it does not need SSH, Tailscale, or Google sign-in in WebKit. Never disable TLS
verification. The public browser sign-in remains available for regular browsers.

One-time administrator enrollment for a new desktop (SSH/Incus is used only
for provisioning, never during app startup or normal operation):

```sh
python3 linux/enroll_device.py --ssh-target ADMIN@COLLECTOR --device-name energy-my-desktop
```

This saves `~/.config/energy-monitor/device.json` with mode 0600 and registers
only its bcrypt password hash in `/etc/energy-device.htpasswd`. Each desktop
must have a distinct name. The gateway supports this alongside Google OAuth;
its password login form stays hidden. Protect and back up the private device
file, and never commit it. Remove the device's server hash to prevent new logins;
already-issued gateway sessions expire within 24 hours.

The app exchanges the credential for a secure HttpOnly gateway cookie at startup,
renews after 12 hours, and retries failed authentication after network recovery.
Credentials are sent only in an HTTPS POST body to their exact enrolled origin;
redirects are never followed with credentials. Switching origins does not send
the device password to the other server.

The installer enables `energy-monitor-client.service` under the graphical user
session, replacing the old desktop autostart entry. After reboot and desktop
login, systemd starts the app and restarts it after a crash. The network may come
up later: authentication and loading retry automatically. The independent server
collector continues without the laptop. Check with:

```sh
systemctl --user status energy-monitor-client.service
systemctl --user is-enabled energy-monitor-client.service
```

Choose `https://energy.stoverparc.org` in **Collector Connection** for installations
that still point to a loopback port or private Tailscale endpoint. The legacy SSH
forward can remain disabled. For an app rollback, disable the user service before
restoring the saved `.desktop.pre-service` autostart file.

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

The native dropdown includes **Power / Temperatures**, **Temperature history…**,
**Temperature floor plan**, and Barn floor shortcuts including **Attic**. Temperature
cards show current recorded readings and their room, with stale readings labeled.
Click a sensor to open its native historical plot. Scroll over the plot or move
the time slider to inspect a recorded bucket; use Older/Newer for earlier windows
and Now to return. History windows are 4 hours, 24 hours, 7 days and 30 days.
Missing/offline buckets remain gaps, and retained offline history is labeled cached.
The client installs `temperatures.py` alongside `energy_monitor.py`; it uses the
same authenticated HTTPS session and private response cache as the existing app.

The Wingpanel **Temperatures** view includes the saved house preview above the sensor
cards. **3D home** now embeds the same Three.js scene builder as the web Home 3D
page, bundled locally in WebKit. It shows full walls with automatic cutaways, textured
floors, saved furniture, windows, doors, stair treads, and stacked levels. Choose
**Whole home** or an individual floor inside the scene, drag to rotate, scroll to zoom,
and use **Reset view** to frame the home again. The dropdown starts 12 degrees farther
overhead than the saved camera, with upper floors at 45% opacity. Use the **Floors**
slider to adjust transparency; the ground floor stays solid. Click a room to open its first mapped
temperature sensor's history. Temperature badges show the room and a large sensor
reading, with collision-free placement and lines back to the sensor location. Select
a badge for that exact sensor's history. Recent readings use the temperature bands;
last-recorded readings use a grey dashed border and never color room averages. Recent
readings below 68°F have a light-blue badge with a frosted white base; readings above
78°F have a light-red badge. The
recent sensor range excludes stale and missing readings. **All sensors** lists every
visible-floor sensor, including unplaced sensors in Whole home, with direct history
buttons and explicit recent/last-recorded/missing status. Native sensor cards are
collapsed under **Sensor history** to keep the house visible.
The renderer uses only the existing summary cache; it loads no remote page, account
session, or external assets. WebKit uses an ephemeral session and the document blocks
network loads with a Content Security Policy. Older collector payloads use architectural
defaults until the collector is upgraded. The updated summary projection includes the
saved wall heights, camera, furniture, and window dimensions.

**Floor plan** retains the native schematic with wrapped 12-pixel callouts and a full
room list. The Temperature menu expands to 660 pixels wide and the scene scales to the
screen height. Secondary actions are under **More…**. The floor and camera choices
survive new readings when the layout is unchanged. Room hues use
only recent mapped observations (blue below 70°F, yellow 70–75°F, amber above 75°F and
below 78°F, red from 78°F); grey rooms have no recent mapped temperature. Sensor dots
open recorded history. Stairs and service hatches use the same saved floor coordinates
as Home 3D. The preview receives its layout through the existing authenticated HTTPS
summary cache and needs no separate connection.
