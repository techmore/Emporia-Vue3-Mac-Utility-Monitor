# Broan ERV receive-only listener

Step 1 of `docs/BROAN_ERV.md`: prove the protocol on **your** unit without touching how the ERV
runs. Unit: Broan AI Series B210E75RT. Only the **VTTOUCHW** wall control is wired to it (owner,
2026-10-18). Goal behind this: detect which cycle/mode the ERV is in, then be able to stop or switch it.

## What the manuals say (and what they do not)

Read: Broan's *Installation and User Guide for Advanced Touchscreen Control* (VTTOUCHW)
([PDF](https://www.broan-nutone.com/getmedia/3d41f698-bc28-4240-95de-4f12dc4954a8/Installation-Guide-VTTOUCHW.pdf?ext=.pdf)).

- The control connects with **four low-voltage wires** to a terminal block on the unit:
  **12V, D-, D+, Gnd**. Suggested colours: red 12V, yellow D-, green D+, black Gnd.
- The unit's board connector is **J15a**; "Check J15a connector connection" is the troubleshooting step.
  The community component also names the ERV's green terminal block near the control interface.
- **"Never install more than one main wall control per ventilation unit."** This control works with
  all AI series units. Keep control wiring 1 ft (305 mm) from motors, ballasts, dimmers and the panel.
- Error **E50** on the screen with power present means miswiring or a broken wire.

Not read: the B210E75RT *User and Installer Manual* (Broan 1107081 Rev. C), because the host that
carries it refused the download. Read it before wiring: confirm the terminal labels on the unit
match, and find the **auxiliary/dry-contact input** (see "Stop and switch" below).

## Detection: passive listener (safe)

A receive-only tap on D+/D- sees both sides of the conversation: the ERV's frames and the VTouch's
polls and writes. The mode register reply (`02 20`: `0A` MAX, `09` MIN, `01` standby, `0B` variable
speed, per the community write-up) and fan-speed registers should show the current cycle without
sending anything. This is the planned way to get "what cycle is it in" while the VTouch stays.

Parts: ESP32 board; a **3.3 V** RS-485 transceiver (SP3485/MAX3485 class; a 5 V MAX485's RO output
would overdrive the ESP32 pin). Power the ESP32 over USB. Do not draw from the unit's 12 V without a
proper regulator.

| Transceiver | Connect to |
|---|---|
| A (community: "A is D-") | ERV/VTouch **D-** (yellow) |
| B (community: "B is D+") | ERV/VTouch **D+** (green) |
| RO | ESP32 GPIO16 (RX) |
| DE and /RE | both to GND (permanently receive) |
| GND | **Gnd** (black) and ESP32 GND |
| VCC | ESP32 3.3 V |

Tap at the terminal block with short stubs (a few cm), in parallel with the existing wires; do not
disconnect the VTouch. Power the ERV down before touching terminals. Flash
`setup/erv/erv-listener.yaml`, watch the log while using the VTouch (set each mode, change speed),
save the log, then decode it:

```sh
python3 scripts/erv_frame_decode.py erv-capture.log
```

It validates the framing/checksum from the write-up and labels each frame (ping, flow, read, write).
If most frames are invalid: swap A/B, check the baud, try even/odd parity, add the transceiver's
termination jumper, and confirm the shared ground. Record the verified frames, model and firmware
back in `docs/BROAN_ERV.md`; those frames become the test fixtures for the collector module.

## Stop and switch: what a listener cannot do

The ERV answers one controller, and the protocol uses flow-control tokens, so a listener cannot send
commands. Ways to act, in order of preference:

1. **Aux/dry-contact override (keeps the VTouch).** The community notes that aux remotes use a
   dry-contact override that shows "ovr". If the B210E75RT manual confirms an input for this, an ESP32
   relay across it can force a state (for example boost or stop) while the VTouch stays wired for
   manual use and the listener reports the real mode. Needs the manual to confirm terminals, what
   states it can request, and whether it can stop the unit. Unverified.
2. **ESPHome takeover.** Replace the VTouch with the community `broan_erv_uart` component
   (selectable fan mode, manual speed, intermittent, filter reset, supply/exhaust CFM, temperatures,
   fault codes). Full control and detection from one device, but the touchscreen is gone and the
   VTouch must come off the bus (one controller only).
3. **Frame-relaying bridge.** Keep the screen by making the ESP32 sit between VTouch and ERV. Needs the
   complete protocol; only after steps above.

Any remote stop/switch must go through the collector's authenticated gateway, with server-side
limits and a log of every command, and the ERV's freeze/defrost protection left alone.
