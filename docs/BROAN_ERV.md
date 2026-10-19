# Broan ERV with VTouch wall control

Context recorded 2026-10-18 from the owner: the house has a **Broan AI Series ERV, model
B210E75RT** (the "AI 206" listing: top ports, rated 210 CFM nominal / 206 CFM in some listings,
120 V, built-in LCD for airflow, power and diagnostics) controlled by a **VTouch** wall control
(Broan VTTOUCHW-class touchscreen control). Retailer compatibility lists include the B210E75RT with
the VTTOUCHW, which is the combination the community RS-485 component below targets. The
installer/user manual (Broan 1107081 Rev. C, ~52 pages, listed as covering the B210E75RT) has the
wall-control terminal labels; it has not been read for this page yet.
Nothing in this repo reads or controls it yet. This page is the starting point; every protocol
statement below is **unverified on this unit** until someone has probed the bus.

## What public prior art says

- [nspitko/broan_erv_uart](https://github.com/nspitko/broan_erv_uart) is an ESPHome component
  for Broan, Nutone, Venmar and VanEE ERVs over their **RS-485** wall-control interface. Its README
  says compatibility is easiest to check by asking whether the unit supports the **VTTOUCHW**
  (the interface is shared across those brands); HRVs were reported to work with fewer features.
- Hardware suggested there: an ESPHome-capable board with RS-485 (for example a Waveshare
  ESP32-S3 relay board with automatic flow control, powered from the ERV's 12 V output).
- **Bus limitation (important):** the ERV answers only one controller. The ESP32 therefore
  **replaces** the wall control on the bus; it cannot sit beside it as a passive man-in-the-middle
  for control. A passive RS-485 *listener* (receive only, wall control stays) may be enough to
  prove the protocol and to monitor, but control needs a single owner.
- We found no published Modbus register map for the VTouch. Treat "it is Modbus" as a guess.

## Options, easiest first

1. **Read-only sniff (no risk to the ERV).** ESP32 + RS-485 transceiver wired in parallel to the
   A/B pair, RX only (TX pin unconnected or transceiver held in receive). Log frames while using
   the wall control, compare against the component's documented frames. Result: confirms the
   protocol and gives monitoring with the wall control untouched.
2. **ESPHome takeover.** Flash the community component, disconnect the VTouch, and let the ESP32
   be the controller: fan speed, mode, bypass, status and fault readouts, plus Home Assistant or
   MQTT. Cost: the wall touchscreen stops working unless we mirror its functions.
3. **ESP32 as a bridge (advanced).** Keep the VTouch on a second RS-485 port and have the ESP32
   act as controller toward the ERV and as a faithful ERV toward the VTouch, relaying frames and
   injecting commands. Real man-in-the-middle; only worth it if the screen must stay. Needs
   full protocol knowledge, so do options 1 and 2 first.

## Fitting it into this project

Follow the existing adapter pattern (`mitsubishi.py`, `kasa_*`, `climate.py`):

- **Phase 1, monitoring.** The ESP32 publishes state (supply/exhaust speed, mode, filter/fault,
  temperatures if exposed) over MQTT or the ESPHome native API on the LAN. A read-only collector
  module on SER8 subscribes, stores snapshots with real timestamps, and shows them as a Ventilation
  card, in the Wingpanel dropdown and in the Temperatures views (ERV supply/exhaust air if reported).
- **Phase 2, control.** Add writes behind explicit actions only: fan speed, away/boost modes.
  Reuse the collector's authenticated gateway (device credentials over HTTPS, Google sign-in, or
  Tailscale), never an open port on the ESP32. Validate ranges server-side, rate-limit, log every
  command with who/when, and show last-commanded versus last-reported state.
- **Phase 3, correlation.** Overlay ERV runtime with the Emporia circuit power, CO2/radon and
  indoor temperature already recorded, so ventilation cost and effect are visible together.

## Remote monitoring and control, simplest safe route

The collector already has public HTTPS access and an enrolled native client
(`docs/PUBLIC_ACCESS.md`, `linux/README.md`). The ESP32 stays on the home LAN and talks only to the
SER8 collector; remote users reach the collector, not the ESP32. Do not expose ESPHome's web
server or API to the internet, and give the board a unique API encryption key and OTA password.

## Safety

An ERV is life-safety adjacent (fresh air, combustion-appliance pressure). Keep the ERV's own
safe defaults, never disable freeze/defrost protection, never write unknown frames, and keep the
original wall control and wiring so the change is reversible. Mains power is not involved: the
board uses the control bus and 12 V only. Power the ERV off before touching its terminals.

## Next steps

1. Read the wiring section of the B210E75RT manual (terminal labels, 12 V, A/B); owner photographs the actual terminals to match.
2. Get an RS-485 ESP32 board; run option 1 and capture a frame log.
3. Compare with the community component; record the verified model, firmware and frames here.
4. Build the Phase 1 collector module with tests using the captured frames as fixtures.
