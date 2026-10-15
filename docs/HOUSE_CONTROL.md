# House-control integration preview

Open **Home 3D** (`/home-3d`) in the existing dashboard. This optional page reuses
house-control's floor, wall, opening and furniture renderer, with a new adapter
for Emporia's recorded circuit data. SER8 remains the authoritative collector.
The page works in browser clients on macOS and Linux; no AppKit bridge is used.

## Use

1. The initial four-room plan is explicitly an example, with no circuits mapped.
2. Import a house-control JSON plan under Floor plan. Import replaces geometry
   and clears all mappings and sensor placements after confirmation. Only rooms, openings, furniture,
   camera and wall dimensions survive validation. Device links/scenes/secrets
   are omitted. Download floor plan exports this geometry for editing elsewhere.
3. Under Map circuits to rooms, assign circuits from the active panel and Save.
   Each circuit can belong to one room. A circuit serving several rooms must
   remain unassigned or be associated with one explicitly understood grouping;
   the app cannot measure separate room consumption from one circuit.
4. Choose power, recorded 24-hour kWh, or recorded 24-hour usage cost. Click a
   room on the plan or its accessible card to see assigned circuits. Rotate by
   dragging; scroll/pinch to zoom. Room cards and mapping controls also work
   when WebGL is unavailable.

Unknown/stale circuit power remains unknown. A room total is unknown when any
assigned circuit lacks that metric; reported zero is preserved. Historical cost
comes from stored consumption charges and excludes monthly fixed charges.
The power overlay uses the same interval-derived watt estimates and freshness
rules as the current menu API; this integration does not solve interval modeling
tracked in #135. Totals describe **assigned circuits**, not measured room load.
Other-panel mappings are retained and cannot attach by channel name alone.

The page refreshes every 30 seconds while visible. Lost connections clear live
watts and label retained historical values. Conflicting edits return HTTP 409
and require reloading; edits are not silently overwritten. Private geometry and
mappings are stored in `house-control.json` beside `DB_PATH`, mode 0600, using
an atomic replacement and a process-shared lock. Include the file in private
installation backups, never in Git or release artifacts.

## Integration boundary

### Visual editing

Expand **Edit floor plan visually**. Draw a room by dragging on the top-down
plan, or Add room and edit its dimensions. Drag a room to move it, drag its
corner to resize, and use the selection list/fields for keyboard access.
Arrow keys on the focused plan move the selected item in 0.25-meter steps.
Add furniture/openings, choose their type and position, and use Undo/Redo for
draft changes. Furniture dimensions appear only when supported by that template,
using the renderer's actual defaults. Openings must lie on walls to appear in 3D; they and furniture
move independently of rooms. Save floor plan updates the 3D model and saves
current circuit selections for retained rooms. Room IDs survive renaming and
moving. Deleting a room requires confirmation and removes its mappings on save.
Reload discards drafts only after confirmation. Browser navigation warns about
unsaved edits. The geometry editor works without WebGL.

### Environmental sensor placement

The recorded sensor list reads existing Aqara local/Matter observations, climate
observations (including Home Assistant), and EcoQube/radon history. It makes no
vendor requests and starts no new collectors. Source plus sensor ID is the identity;
identical IDs from different sources remain distinct. Names come from existing
sensor labels. Climate/Aqara samples older than 3 minutes, radon samples older than
3 hours, and samples over 5 minutes in the future are marked stale. Lost connections
also mark all retained sensor observations stale. Temperature defaults to Fahrenheit;
missing measurements are never replaced by zero. A recent sample is not a guarantee
that its device is online.

Select a recorded sensor in the editor, choose **Place sensor on plan**, then click
inside a room. Or select a room in **Selected item** and use **Place in selected room
center** for keyboard access. Drag a placed marker within its room, or use its room
and X/Z fields. Save floor plan persists placements with geometry and circuit mappings.
Undo/Redo includes placements. Moving a room moves its sensors; resizing clamps them
inside the new boundary. Deleting a room explicitly removes its placements on save.
Delete a selected sensor marker to unplace it; this never deletes readings or changes
the physical sensor. Disappeared devices remain listed as stale/unavailable while placed.

3D markers and accessible sensor cards show the saved layout and observation timestamps.
Placements are stored in the private `house-control.json`, not in the older climate-lab
schematic or sensor registry. Geometry downloads exclude identities and placements;
include `house-control.json` in private backups to restore them. Config updates from
older clients that omit `sensors` preserve placements and reject incompatible room edits.

This first branch implements the read-only data/visualization milestone of #148.
It does not import house-control's native app, vendor clients,
device commands, scenes, AI links/MCP server or Mac/iPhone sync. Existing Kasa,
Mitsubishi, Aqara and EcoQube pages continue to operate independently. No public
endpoint or extra authentication exemption is introduced; the existing Flask
host/origin policy and deployment gateway apply to all new routes.

## Upstream and rebuild

### Local preview with live SER8 telemetry

Use `scripts/run_home3d_preview.py` only with a copied database in a separate
private directory. Never point it at the collector's working data directory.
The destination must contain `energy.db` and an owner-only `preview-only.json`
with exactly `{"purpose":"house-control-local-preview"}`. Floor plans and
placements remain in this directory's `house-control.json`; no collector config
or placement file is downloaded or uploaded.

```bash
venv/bin/python3 scripts/run_home3d_preview.py \
  --data "$HOME/Library/Application Support/EnergyMonitor/previews/house-control" \
  --sensors-host sdolbec@10.20.0.117
```

The runner binds only `127.0.0.1:15045`, exposes Home 3D/configuration/static assets
only, and starts a supervised local sensor-cache worker. The worker uses existing
SSH keys with `BatchMode=yes` and strict host-key checking; no password prompts,
vendor authentication, new SER8 software, public endpoints or production changes
are required. The existing `energy-monitor` Incus container must permit a read-only
Python query of `/var/lib/energy-monitor/collector/energy.db`. Other installations
need an explicitly adapted read-only exporter; this is not a universal discovery tool.

Every 30 seconds it copies latest Aqara/Matter, climate and radon observations and
labels, plus source-panel circuit/mains readings and rolling 24-hour kWh/cost
aggregates from one read-only source database transaction. Sensor cache writes are
transactional; the validated power cache is atomically published separately.
It preserves measurement timestamps and zero values. Bulk historical readings are
not mirrored; the preview's room energy and cost overlays use refreshed source
aggregates, never the old local history snapshot. Failed/incomplete snapshots preserve the last
cache; the UI marks all sensors stale during a transport failure or if the worker
has not succeeded for 90 seconds. It retries automatically and never fabricates a
fresh measurement time. Removed devices with local placements remain removable.
Only the preview sensor caches are replaced, not historical production readings.
The runner stops its worker when terminated; logs/status remain in the preview directory.

Power additionally requires a fresh SER8 poller heartbeat and source measurement
(under 180 seconds), with at most 90 seconds since a successful transfer. A successful
transfer alone never makes an old measurement live. Stale power is shown as unknown;
retained recorded 24-hour totals remain labeled historical with the last refresh time.
Total, Leg A/B and an actually monitored Leg C are displayed independently. Main
is never summed with its own legs, absent legs are never fabricated, and no-CT Leg C
is omitted. Watt values retain the existing app's one-minute interval-derived estimate,
not a claim of instantaneous measurement (see #135). Source measurement ages are
calculated on SER8, avoiding interpretation of its legacy naive timestamps as Mac time.
Known panel/circuit identities for mapping come from this source cache, so newly
reported circuits do not require a bulk history download. Floor-plan revisions are
independent of incoming readings, protecting unsaved layout edits during refreshes.

If a verified host has an alternate address, `--host-key-alias EXISTING_KNOWN_HOST`
reuses its existing trusted SSH key. It never disables host-key checking or approves
unknown keys. This can be used with a configured Tailscale peer while a LAN address
is unavailable; it changes only the local preview's connection target.

Upstream: https://github.com/0xProbabillity/house-control
Pinned commit: `1ad70a26197c0503b5af7932f0b5c8a8d3c7a6f9` (MIT).
Unmodified source modules and SHA-256 provenance are in
`frontend/house-control/vendor/`. Their original colors/materials are retained;
the new page chrome follows the existing olive/stone theme.

The committed bundle and license notices under `static/house-control/` are
included in Python/macOS source releases. Normal app startup does not require
Node, npm, a CDN, or a separate frontend server. To rebuild from the Git checkout
with Node 22.12+:

```bash
cd frontend/house-control
npm ci --ignore-scripts
npm run build
```

Three.js 0.186.1 and Vite 8.3.4 are pinned in the package manifest and lockfile.
The bundle contains the Three.js runtime; Vite is only a build dependency.
Redistributions must keep `static/house-control/LICENSES.txt` with the bundle.

## Verify

- Visit `/home-3d` with an empty test database: the example plan must render,
  report no readings and show no invented mappings/power.
- With known panel data, assign a circuit, save, reload, and compare its 24-hour
  usage/cost with the existing circuit summary. Verify zero differs from missing.
- Load the page in two tabs; save in one, then save the other's old revision:
  the latter must return 409 without overwriting the first tab's mappings.
- Import a plan with duplicate room IDs or invalid/nonfinite geometry: reject
  it without changing the saved plan. Importing upstream plans omits device links.
- Check desktop and mobile layouts, WebGL failure fallback, and network-loss
  handling. Existing dashboard, menu summary and climate lab must still work.
- Place a known sensor, save/reload, move and resize its room, and verify its
  coordinates and room association. Undo room deletion must restore its placement.
- Compare Fahrenheit, humidity and radon values against existing sensor pages.
  Check stale observations, zero readings, source-ID collisions and vanished sensors.

```bash
venv/bin/python3 -m unittest discover -s tests -p 'test_house_control.py' -v
node scripts/test_home3d_editor.mjs
```
