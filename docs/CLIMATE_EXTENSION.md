# House climate extension — experimental branch

The House · Lab page is a working schematic and replay prototype, with an explicit built-in
extension registry in `extensions.py`. It does not discover or execute downloaded plugins.
The menu bar monitor remains the entry point; House is available in its browser dashboard.

## What works

- SQLite observation history in `climate_readings`, with sensor metadata and room assignment
  in `climate_sensors`. Schema changes are additive and owned by `energy.ensure_table()`.
- Normalized Celsius and UTC timestamps, atomic batch validation, timestamp deduplication,
  365-day default retention (the existing `DB_RETENTION_DAYS`), and persistent room placement.
- Hourly observed averages and 1/7/30-day replay, with gaps retained. No hold-last-value or
  interpolation. A sensor that only sends changes will leave empty hours; this is deliberate
  until an adapter has documented observation freshness semantics.
- Recorded device-scoped Main energy next to sensor history. One-day power is hourly;
  longer windows are daily. Values are recorded totals, not gap-adjusted forecasts. Current
  configured rate supplies the displayed cost, not an historical utility tariff.
- A simulated example house and energy series generated in memory, never written to SQLite.
- A cooling calculator using a single-zone resistance/capacitance model with thermostat duty
  and constant outdoor temperature. Manual parameters, uncalibrated results, no equipment control.

The floor plan is a fixed example layout, not your actual house. Sensors can be assigned to
rooms using each room selector; placement represents an illustrative room location. There is
no floorplan editor, free positioning, 3D building mesh or fluid-flow solver yet.

## Connection choices

The existing `aqara.py` is a legacy, unverified cloud skeleton. House does **not** use it for
logging. Its assumed signing/OAuth flow should not be taken as current documentation. We need the actual hub model, region, sensor models and current
pairing before choosing and validating a connection.

Home Assistant can expose supported HomeKit accessories via its [HomeKit Device integration](https://www.home-assistant.io/integrations/homekit_controller/).
Compatibility and pairing requirements must be checked for your actual hub before changing
an existing setup. Do not unpair a working Apple Home configuration merely to try this branch.
Aqara also documents [cloud integration and account authorization](https://opendoc.aqara.com/en/docs/developmanual/cloudDevelopment/docs/developmanual/processOverview.html).
No account creation, authorization or hardware pairing has been performed by this branch.

If your sensors already appear in Home Assistant, the optional read-only collector uses its
[REST API](https://developers.home-assistant.io/docs/api/rest/), `GET /api/states` and a bearer
token. Select specific temperature entities, including the outdoor sensor you actually use.
The adapter supports °C, °F and K, skips unavailable states and uses `last_updated` as the source
observation timestamp. It does not fabricate fresh readings from repeated polls. It currently
collects temperature only; humidity/battery can be supplied through the normalized import API.

Configure `HA_URL`, `HA_TOKEN` and comma-separated `HA_TEMPERATURE_ENTITIES` privately in your
shell environment. Do not paste tokens into Git or terminal commands that will be shared.
Then run from the project root:

```sh
venv/bin/python3 climate_collect.py --home-assistant
# Once verified, keep collecting until Ctrl-C:
venv/bin/python3 -u climate_collect.py --home-assistant --watch
```

No collector is started automatically. Continuous collection needs a configured source and a
running collector; installing the menu bar app alone does not start temperature logging.
The optional collector rejects redirects rather than forwarding its token elsewhere.

## Normalized import contract

`POST /api/climate/readings` accepts a same-origin JSON object, or import the same JSON file
locally with `venv/bin/python3 climate_collect.py --import-json /path/to/readings.json`:

```json
{
  "observations": [{
    "source": "aqara",
    "sensor_id": "your-stable-device-id",
    "name": "Office",
    "timestamp": "2026-10-04T12:00:00Z",
    "temperature_c": 21.5,
    "humidity_pct": 48,
    "battery_pct": 87
  }]
}
```

Use the real observation timestamp. Samples outside retention or more than five minutes in
the future are rejected. `source` is `aqara`, `home_assistant` or `manual`. The entire batch
is validated before writing; duplicate source/sensor/timestamp samples are ignored, not updated.
Unknown/missing readings should be skipped, not sent as zero. Data lives in the same private
local database as energy history and is not included in release archives.

API: `/api/extensions`, `/api/climate/replay?days=1&demo=0`,
`POST /api/climate/placement` with `source`, `sensor_id`, and `room_id` (or null).

## Next steps after connecting real sensors

1. Match the schematic to the actual rooms and assign indoor/outdoor sensors.
2. Identify which Emporia circuits feed HVAC equipment; include equipment state if available.
3. Collect synchronized weather, room temperatures and HVAC power with coverage reporting.
4. Fit heat-loss/capacity parameters on training intervals and check predictions on separate days.
5. Add room adjacency, sunlight and airflow assumptions, then compare scenarios with uncertainty.
6. Build budget estimates from validated HVAC/weather relationships, tariffs and appliance baselines.

Room-to-room flow, annual/monthly budget forecasting and fitted thermal models remain future
work. A visual temperature difference alone does not establish a heat-transfer rate or cause.
