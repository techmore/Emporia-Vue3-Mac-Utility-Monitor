# Solar and battery guidance, with and without the water heater

*Prepared 2026-10-27. These are estimates built on the assumptions listed below.
They are not a verdict, a quote or a production guarantee.*

## Data used, and what is missing

- **Source.** Emporia's own 1-hour export for the house panel (`8C9E94-Barn-1H.csv`):
  **149 complete days, 2026-01-15 to 2026-06-13**, in local time (America/New_York).
  The two DST-change days have 23 or 25 hours and are excluded. The 1-minute export
  (2026-06-07 to 06-14) was used only for peak power.
- **Not used: the production database on SER8.** `ssh sdolbec-ser8` stopped at a
  Tailscale SSH browser check that needs the owner to approve it, so the October live
  history was not read. The `/guidance` page and `/api/guidance` described below
  recompute everything from the production database once this branch is deployed.
- **Missing seasons.** There is no July–December data, so summer cooling and the
  autumn shoulder season are not measured. Winter is 45 of the 149 days, and winter
  heating dominates the totals. Any "per year" figure below scales this
  January–June window to 365 days and is labelled as such.
- **No solar or battery data.** The panel has no solar, inverter or battery channel,
  so there is no measured production, export or state of charge. All solar output
  below is **modelled**.
- **Circuits.** The whole-house total is Mains_A plus Mains_B (Mains_C has no CT).
  The water heater is its own circuit, `Water Heater` (category "Water Heater").
  About 6 kWh/day is not on any metered circuit.

## Load profile (measured)

| Metric | Value |
| --- | ---: |
| House load | **55.4 kWh/day** (p10 21.2, p90 102.9) |
| Water heater | **10.2 kWh/day, 18.3 % of the house load** |
| Evening/night load, 17:00–07:00 | 34.2 kWh/day (61.8 % of the house load) |
| Water heater within evening/night | 7.0 kWh/day (**20.5 % of evening/night load**) |
| Peak 1-minute house power, June week | 8.6 kW (p99 7.0 kW); water heater element 4.8 kW |
| Peak hourly-average evening load | 11.3 kWh in one hour (p99 7.8) |

### By month

| Month | Days | House kWh/day | Water heater kWh/day | WH share |
| --- | ---: | ---: | ---: | ---: |
| 2026-01 (from 15th) | 17 | 76.7 | 13.6 | 17.7 % |
| 2026-02 | 28 | 103.8 | 13.1 | 12.6 % |
| 2026-03 | 30 | 60.4 | 11.5 | 19.1 % |
| 2026-04 | 30 | 31.8 | 9.7 | 30.4 % |
| 2026-05 | 31 | 29.8 | 7.2 | 24.1 % |
| 2026-06 (to 13th) | 13 | 27.3 | 4.5 | 16.5 % |

### By season

| Season | Days | House kWh/day | Water heater kWh/day | WH share |
| --- | ---: | ---: | ---: | ---: |
| Winter (Dec–Feb) | 45 | 93.6 | 13.3 | 14.2 % |
| Spring (Mar–May) | 91 | 40.5 | 9.4 | 23.3 % |
| Summer (Jun only, 13 days) | 13 | 27.3 | 4.5 | 16.5 % |

The water heater's use falls by about two thirds from winter to June. Colder inlet
water and possibly occupancy explain some of that, but the data cannot say how much.

### Biggest circuits (average over the window)

| Circuit | kWh/day | Share |
| --- | ---: | ---: |
| Heat Pump | 23.4 | 42 % |
| Water Heater | 10.1 | 18 % |
| Master Bedroom | 5.2 | 9 % (about 33 kWh/day on mid-January days, likely space heating) |
| Radon fan & | 2.3 | 4 % |
| Kitchen Outlets | 2.1 | 4 % |
| Unmetered | 6.0 | 11 % |

Heating, not the water heater, is the largest load and the main reason for the
winter peak.

### Hour of day (average kWh in each hour)

| Hour | House | Water heater | | Hour | House | Water heater |
| ---: | ---: | ---: | --- | ---: | ---: | ---: |
| 00 | 2.51 | 0.70 | | 12 | 2.07 | 0.40 |
| 01 | 2.20 | 0.42 | | 13 | 2.08 | 0.41 |
| 02 | 1.98 | 0.20 | | 14 | 1.87 | 0.29 |
| 03 | 1.84 | 0.05 | | 15 | 1.96 | 0.27 |
| 04 | 1.96 | 0.07 | | 16 | 2.36 | 0.46 |
| 05 | 2.57 | 0.50 | | 17 | 2.45 | 0.52 |
| 06 | 3.68 | **1.39** | | 18 | 2.51 | 0.57 |
| 07 | 2.42 | 0.34 | | 19 | 2.50 | 0.46 |
| 08 | 2.37 | 0.35 | | 20 | 2.60 | 0.50 |
| 09 | 2.09 | 0.24 | | 21 | 2.74 | 0.66 |
| 10 | 1.97 | 0.17 | | 22 | 2.40 | 0.52 |
| 11 | 1.96 | 0.19 | | 23 | 2.29 | 0.47 |

The water heater recovers mostly after the morning showers (06:00) and through the
evening (21:00–00:00). Both are outside solar hours.

### Existing solar production

None was measured: no channel on the panel carries solar or battery energy. If an
array is added, a CT on its breaker (a channel named with "Solar", "PV" or
"Inverter") is stored as `kind='solar'` in the new `energy_hourly` table. A
"Battery" or "Powerwall" channel is stored as `kind='battery'`. State of charge is
not available from Emporia and would need the battery's own API.

## Assumptions (change them in `guidance.DEFAULTS` and `PV_YIELD`)

| Assumption | Value | Note |
| --- | --- | --- |
| PV yield, kWh per kW(DC) per day | Jan 2.6, Feb 3.4, Mar 4.2, Apr 4.7, May 5.0, Jun 5.2, Jul 5.2, Aug 4.9, Sep 4.4, Oct 3.6, Nov 2.6, Dec 2.2 (about 1,450 kWh/kW/yr) | Typical-year yield for a south-facing array tilted about 25° at 40–42° N in the US north-east, with about 14 % losses. Run PVWatts for the real roof. |
| PV hourly shape | Half-sine between sunrise and sunset, solar noon 12:00 standard time / 13:00 daylight time | Smooth clear-sky shape. Real cloudy days are lumpier, so self-consumption is optimistic. |
| Electricity rate | 22.58 ¢/kWh | The installation's bill-derived setting. The fixed charge is excluded. |
| Export credit | Two cases: full retail net metering (22.58 ¢) and a low avoided-cost credit (5 ¢) | Check the utility's actual tariff. |
| Installed PV cost | $2.80/W(DC) | Planning figure. Get quotes. |
| Installed battery cost | $1,000/kWh nameplate | Includes a share of the inverter and gateway. |
| Federal credit | 0 % | The residential clean-energy credit (25D) ended for systems placed in service after 2025. Add any state or utility incentives. |
| Battery | 90 % round trip, 10 % reserve, charged from solar surplus only | No grid charging and no time-of-use arbitrage. |
| Heat pump water heater (HPWH) | Uses 0.33 × the resistance heater's kWh | Roughly UEF 3+ versus 0.93. In a cold basement in winter it could be 0.4–0.5. It also takes some heat from the space it sits in. |
| Timer shift | All heating moved into 10:00–15:00, plus 5 % extra standby loss | Assumes the tank is big enough to carry the evening and the next morning's draws. |
| Battery size rule | Usable capacity covers the **median** sunset-to-sunrise load | Covering p90 winter nights (45–51 kWh) is not practical; see below. |
| Battery power rule | max(1.25 × p99 hourly evening kW, capacity ÷ 2.7) | Hourly averages hide surges. Size the inverter's surge rating from the 1-minute data (8.6 kW in June; winter is higher). |
| Payback | Simple: installed cost ÷ annualized first-year savings | Ignores degradation, rate escalation, financing and replacement. Savings are the January–June window scaled to 365 days. |

## Scenarios

"Net-zero PV" is the array that produces as much energy over the measured window as
the house uses. "Self-consumption" is the share of PV energy used in the house, and
"self-sufficiency" is the share of house load supplied by PV or the battery. Both
come from an hour-by-hour simulation over the 149 days.

| Scenario | Load kWh/day | Net-zero PV | Suggested PV | Battery | Overnight need p50 / p90 | PV only: self-consumption / self-sufficiency | PV + battery: self-consumption / self-sufficiency |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| **(a) Current house, with water heater** | 55.4 | 13.1 kW | **13.5 kW** | **25 kWh, 10 kW** | 19.9 / 51.4 kWh | 37 % / 38 % | 62 % / 64 % |
| **(b) Water heater removed** | 45.2 | 10.7 kW | **11 kW** | **17.5 kWh, 9 kW** | 14.2 / 45.2 kWh | 38 % / 39 % | 60 % / 61 % |
| (b1) Heat pump water heater | 48.6 | 11.5 kW | **12 kW** | **20 kWh, 9 kW** | 15.6 / 47.7 kWh | 37 % / 39 % | 60 % / 63 % |
| (b2) Existing heater on a 10:00–15:00 timer | 55.9 | 13.2 kW | **13.5 kW** | **17.5 kWh, 9 kW** | 14.2 / 45.2 kWh | **49 % / 50 %** | 64 % / 65 % |

How to read this:

- **The water heater adds about 2.5 kW of array and 7.5 kWh of battery** to a
  net-zero design (13.5 kW and 25 kWh versus 11 kW and 17.5 kWh).
- **A heat pump water heater** would use about **3.4 kWh/day instead of 10.2**, saving
  about 6.8 kWh/day. Scaled to a year at 22.58 ¢ that is roughly **2,500 kWh and $560
  a year**. Because the window is winter-heavy, the summer saving will be smaller. It
  brings the system back to about 12 kW and 20 kWh.
- **A timer on the existing heater** does not reduce energy (+5 % standby). It moves
  about 7 kWh/day out of the evening and night. **Self-consumption without a battery
  rises from 37 % to 49 %**, and the median overnight need drops by the same ~6 kWh.
  The battery for scenario (a) can then be the same 17.5 kWh as with the heater
  removed. This is the cheapest change, and it matters most when exports are poorly
  credited.
- **Winter cannot be covered by a battery.** p90 nights need 45–51 kWh, and the
  worst night needed 95 kWh (heat pump plus the master-bedroom heater). In
  January–February PV yields only about 2.6–3.4 kWh per kW per day, and daytime load
  absorbs most of it, so little surplus is left to charge a battery.
  Batteries mainly help from March to June.

### Self-sufficiency grid, current house (PV kW down, battery kWh across)

| PV kW \ battery | 0 | 5 | 10 | 13.5 | 20 | 27 | 40 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 4 | 21 % | 25 % | 28 % | 29 % | 30 % | 30 % | 30 % |
| 6 | 27 % | 32 % | 36 % | 39 % | 41 % | 42 % | 43 % |
| 8 | 31 % | 38 % | 42 % | 45 % | 49 % | 51 % | 52 % |
| 10 | 34 % | 41 % | 47 % | 50 % | 54 % | 57 % | 58 % |
| 12 | 36 % | 44 % | 51 % | 54 % | 59 % | 62 % | 63 % |
| 14 | 38 % | 46 % | 53 % | 57 % | 63 % | 66 % | 68 % |
| 16 | 39 % | 47 % | 54 % | 59 % | 65 % | 69 % | 72 % |

### Self-sufficiency grid, water heater removed

| PV kW \ battery | 0 | 5 | 10 | 13.5 | 20 | 27 | 40 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 4 | 24 % | 30 % | 34 % | 35 % | 35 % | 36 % | 36 % |
| 6 | 31 % | 38 % | 42 % | 44 % | 46 % | 47 % | 48 % |
| 8 | 35 % | 44 % | 49 % | 51 % | 53 % | 54 % | 55 % |
| 10 | 38 % | 47 % | 54 % | 57 % | 59 % | 61 % | 61 % |
| 12 | 40 % | 49 % | 57 % | 60 % | 64 % | 66 % | 67 % |
| 14 | 41 % | 51 % | 58 % | 63 % | 68 % | 71 % | 72 % |
| 16 | 42 % | 52 % | 59 % | 64 % | 70 % | 74 % | 77 % |

Gains flatten beyond about 10–13.5 kWh of battery for 8–10 kW of PV. The
"net-zero" sizes above sit near the top of these grids.

## Simple payback (assumptions above; first-year, no incentives)

| Scenario | System | Installed cost | Savings/yr, net metering | Payback | Savings/yr, 5 ¢ export | Payback |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| (a) current | 13.5 kW PV | $37,800 | $4,697 | 8.0 y | $2,376 | 15.9 y |
| (a) current | + 25 kWh battery | $62,800 | $4,557 | 13.8 y | $3,293 | 19.1 y |
| (b) heater removed | 11 kW PV | $30,800 | $4,664 | 6.6 y | $2,820 | 10.9 y |
| (b) heater removed | + 17.5 kWh battery | $48,300 | $4,570 | 10.6 y | $3,438 | 14.0 y |
| (b1) HPWH | 12 kW PV | $33,600 | $4,736 | 7.1 y | $2,699 | 12.5 y |
| (b1) HPWH | + 20 kWh battery | $53,600 | $4,626 | 11.6 y | $3,420 | 15.7 y |
| (b2) timer | 13.5 kW PV | $37,800 | $4,655 | 8.1 y | $2,801 | 13.5 y |
| (b2) timer | + 17.5 kWh battery | $55,300 | $4,575 | 12.1 y | $3,326 | 16.6 y |

Savings are measured against the current house in every row, so the heat pump
water heater's own saving is included. Its purchase cost, typically $2,500–4,500
installed and not modelled, is not. Under full net metering a battery *lowers*
savings, because round-trip losses waste energy that the grid would have credited
in full. A battery is worth it for backup or under a poor export tariff, not for
bill savings alone.

## What this suggests (estimates, for discussion)

1. **Deal with the water heater first.** A heat pump water heater, or at least a
   timer that heats between 10:00 and 15:00, cuts the evening load and the battery
   needed for it more cheaply than extra array or battery capacity does.
2. **PV in the 10–13.5 kW range** brings the measured window close to net-zero.
   11–12 kW is enough once the water heater is replaced. Check roof area first:
   13.5 kW is about 33 panels of 410 W, roughly 65 m².
3. **Battery: 13.5–20 kWh usable, at least 9–10 kW continuous**, if backup or a
   poor export tariff makes it worthwhile. Larger batteries add little because
   winter surplus is small.
4. **Re-run on a full year** of production data (`/guidance` once deployed, or
   `scripts/solar_guidance_report.py --db`). Summer cooling and autumn could move
   the net-zero size in either direction.

## How to reproduce

```bash
# From an Emporia 1H export (keep exports in a data-export-*/ directory; it is gitignored)
venv/bin/python3 scripts/solar_guidance_report.py --csv data-export-x/PANEL-1H.csv --grid
# From the collector database (durable energy_hourly table)
DB_PATH=/var/lib/energy-monitor/collector/energy.db venv/bin/python3 scripts/solar_guidance_report.py --db
```

The dashboard serves the same model at **`/guidance`** (linked from Reports →
Solar Scenario) and as JSON at **`/api/guidance`**. Both are recomputed from the
stored hourly history every 10 minutes, at the configured rate. Water-heater
detection matches channel names containing "water heater", "hot water" or "HWH".
