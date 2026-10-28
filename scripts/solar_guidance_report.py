#!/usr/bin/env python3
"""Print solar + battery guidance from an Emporia 1H CSV export or the energy database.

    venv/bin/python3 scripts/solar_guidance_report.py --csv data-export-x/PANEL-1H.csv
    DB_PATH=energy.db venv/bin/python3 scripts/solar_guidance_report.py --db

Read-only. Output is JSON (``--json``) or the Markdown tables used in
docs/solar-battery-guidance.md. Keep exports out of Git (data-export-*/ is ignored).
"""
import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import guidance  # noqa: E402


def clean_name(column: str) -> str:
    # Same normalization as energy._clean_csv_channel_name, without importing the poller.
    name = column.strip()
    for suffix in (" (kWatts)", " (kWhs)", " (kW)", " (kWh)"):
        if name.endswith(suffix):
            name = name[: -len(suffix)].strip()
            break
    if "-" in name and " " not in name.split("-", 1)[0]:
        name = name.split("-", 1)[1].strip()
    if "-" in name:
        name = name.split("-", 1)[1].strip()
    return name


def rows_from_csv(path: Path) -> tuple[list[dict], str | None]:
    with path.open(newline='') as handle:
        reader = csv.reader(handle)
        header = next(reader)
        names = [clean_name(c) for c in header[1:]]
        heater = guidance.find_water_heater(names)
        rows = []
        for line in reader:
            values = {}
            for name, raw in zip(names, line[1:], strict=False):
                try:
                    values[name] = float(raw)
                except ValueError:
                    values[name] = None
            legs = [values.get(n) for n in ('Mains_A', 'Mains_B', 'Mains_C') if values.get(n) is not None]
            total = values.get('Main')
            if total is None and legs:
                total = sum(legs)
            if total is None:
                continue
            rows.append({'start': datetime.strptime(line[0], '%m/%d/%Y %H:%M:%S'),
                         'total_kwh': total, 'wh_kwh': values.get(heater) if heater else None})
    return rows, heater


def rows_from_db(device_gid: str | None) -> tuple[list[dict], str | None]:
    import energy
    records = energy.get_energy_hourly(device_gid)
    heater = guidance.find_water_heater({r['channel_name'] for r in records})
    return guidance.rows_from_hourly(records, heater), heater


def _f(value, digits=1):
    return '—' if value is None else f'{value:,.{digits}f}'


def markdown(doc: dict, heater: str | None) -> str:
    p = doc['profile']
    out = [f"Data: {p['days']} complete days, {p.get('first_day')} to {p.get('last_day')}; "
           f"water heater channel: {heater or 'not found'}.", '',
           '| Metric | Value |', '| --- | ---: |',
           f"| House load | {_f(p['kwh_per_day'])} kWh/day (p10 {_f(p['kwh_per_day_p10'])}, p90 {_f(p['kwh_per_day_p90'])}) |",
           f"| Water heater | {_f(p['wh_kwh_per_day'])} kWh/day ({_f(p['wh_share_pct'])} % of load) |",
           f"| Evening/night {p['evening_window'][0]:02d}:00–{p['evening_window'][1]:02d}:00 | "
           f"{_f(p['evening_kwh_per_day'])} kWh/day ({_f(p['evening_share_of_load_pct'])} % of load) |",
           f"| Water heater in evening/night | {_f(p['wh_evening_kwh_per_day'])} kWh/day "
           f"({_f(p['wh_share_of_evening_pct'])} % of evening/night load) |", '',
           '| Month | Days | House kWh/day | Water heater kWh/day | WH share |',
           '| --- | ---: | ---: | ---: | ---: |']
    out += [f"| {m} | {v['days']} | {_f(v['kwh_per_day'])} | {_f(v['wh_kwh_per_day'])} | {_f(v['wh_share_pct'])} % |"
            for m, v in p['months'].items()]
    out += ['', '| Season | Days | House kWh/day | Water heater kWh/day | WH share |',
            '| --- | ---: | ---: | ---: | ---: |']
    out += [f"| {s} | {v['days']} | {_f(v['kwh_per_day'])} | {_f(v['wh_kwh_per_day'])} | {_f(v['wh_share_pct'])} % |"
            for s, v in p['seasons'].items()]
    out += ['', '| Hour | House kWh | Water heater kWh |', '| ---: | ---: | ---: |']
    wh = p['hour_of_day_wh_kwh'] or [None] * 24
    out += [f"| {h:02d} | {_f(v, 2)} | {_f(w, 2)} |" for h, (v, w) in enumerate(zip(p['hour_of_day_kwh'], wh, strict=True))]
    out += ['', '| Scenario | Load kWh/day | Net-zero PV kW | PV kW | Battery kWh | Battery kW | '
            'Overnight p50/p90 kWh | Self-consumption PV only | Self-sufficiency PV only | '
            'Self-consumption PV+battery | Self-sufficiency PV+battery |',
            '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for name, s in doc['scenarios'].items():
        o, r, b = s['overnight'], s['pv_only'], s['recommended']
        out.append(f"| {name} | {_f(s['load_kwh_per_day'])} | {_f(s['net_zero_pv_kw'])} | {_f(s['pv_kw'])} | "
                   f"{_f(s['battery_kwh'])} | {_f(s['battery_kw'])} | {_f(o.get('p50_kwh'))} / {_f(o.get('p90_kwh'))} | "
                   f"{_f(r['self_consumption_pct'], 0)} % | {_f(r['self_sufficiency_pct'], 0)} % | "
                   f"{_f(b['self_consumption_pct'], 0)} % | {_f(b['self_sufficiency_pct'], 0)} % |")
    out += ['', '| Scenario | System | Installed cost | Savings/yr (net metering) | Payback | '
            'Savings/yr (low export) | Payback |', '| --- | --- | ---: | ---: | ---: | ---: | ---: |']
    for name, s in doc['scenarios'].items():
        e = s['economics']
        for label, nm, low in (('PV only', 'pv_only_net_metering', 'pv_only_low_export'),
                               ('PV + battery', 'pv_battery_net_metering', 'pv_battery_low_export')):
            out.append(f"| {name} | {label} | ${e[nm]['installed_cost']:,} | ${e[nm]['annualized_savings']:,} | "
                       f"{_f(e[nm]['simple_payback_years'])} y | ${e[low]['annualized_savings']:,} | "
                       f"{_f(e[low]['simple_payback_years'])} y |")
    return '\n'.join(out)


def grid_table(doc: dict, scenario: str) -> str:
    rows = doc['scenarios'][scenario].get('grid') or []
    kws = sorted({r['pv_kw'] for r in rows})
    batteries = sorted({r['battery_kwh'] for r in rows})
    out = ['| PV kW \\ battery kWh | ' + ' | '.join(_f(b, 1) for b in batteries) + ' |',
           '| ---: |' + ' ---: |' * len(batteries)]
    for kw in kws:
        cells = [next(r for r in rows if r['pv_kw'] == kw and r['battery_kwh'] == b) for b in batteries]
        out.append(f'| {kw:g} | ' + ' | '.join(_f(c['self_sufficiency_pct'], 0) + ' %' for c in cells) + ' |')
    return '\n'.join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--csv', type=Path, help='Emporia 1H export (local time buckets)')
    source.add_argument('--db', action='store_true', help='Read energy_hourly from DB_PATH')
    parser.add_argument('--device-gid')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--grid', action='store_true', help='Also print self-sufficiency grids')
    args = parser.parse_args()
    rows, heater = rows_from_csv(args.csv) if args.csv else rows_from_db(args.device_gid)
    doc = guidance.build_guidance(rows, include_grid=args.grid)
    if args.json:
        print(json.dumps(doc, indent=2, default=str))
        return
    print(markdown(doc, heater))
    if args.grid:
        for name in doc['scenarios']:
            print(f'\n### Self-sufficiency grid: {name}\n')
            print(grid_table(doc, name))


if __name__ == '__main__':
    main()
