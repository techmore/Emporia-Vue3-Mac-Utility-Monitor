"""Solar-array and battery sizing guidance from measured hourly load.

Every number here is a scenario estimate built on stated assumptions (see
``DEFAULTS``), not a verdict or a quote. The measured part is the household's
hourly load and its water heater circuit; solar production is modelled from a
typical-year yield table because the panel has no solar or battery channels.

Input rows are ``{'start': datetime (local wall clock), 'total_kwh': float,
'wh_kwh': float | None}``, one per hour. Only complete days (24 hours) are used.
"""
import math
import re
from collections import defaultdict
from datetime import date, datetime, timedelta

# Typical-year PV yield, kWh per kW(DC) per day, for a south-facing ~25 degree array
# near 40-42 N in the US north-east with ~14 % system losses (PVWatts-style values,
# rounded). This is an assumption: replace it with a PVWatts run for the real roof.
PV_YIELD = {1: 2.6, 2: 3.4, 3: 4.2, 4: 4.7, 5: 5.0, 6: 5.2,
            7: 5.2, 8: 4.9, 9: 4.4, 10: 3.6, 11: 2.6, 12: 2.2}
# Approximate day length (hours) at ~41 N, mid-month.
DAY_LENGTH = {1: 9.4, 2: 10.5, 3: 11.9, 4: 13.3, 5: 14.5, 6: 15.1,
              7: 14.8, 8: 13.7, 9: 12.3, 10: 10.9, 11: 9.7, 12: 9.1}

DEFAULTS = {
    'rate_cents': 22.58,             # retail energy rate (bill-derived setting)
    'export_cents_low': 5.0,         # avoided-cost style export credit
    'pv_cost_per_w': 2.80,           # installed $/W(DC), before incentives
    'battery_cost_per_kwh': 1000.0,  # installed $/kWh usable, incl. inverter share
    'federal_credit_pct': 0.0,       # 25D residential credit ended for 2026 installs
    'battery_efficiency': 0.90,      # round trip
    'battery_reserve_pct': 10.0,     # usable = nameplate x (1 - reserve)
    'hpwh_factor': 0.33,             # heat-pump water heater kWh / resistance kWh
    'timer_window': (10, 15),        # timer-shifted heating hours [start, end)
    'timer_loss_pct': 5.0,           # extra standby loss when heating hours early
    'evening_hours': (17, 7),        # evening + night window [17:00, 07:00)
}
SEASONS = {12: 'Winter', 1: 'Winter', 2: 'Winter', 3: 'Spring', 4: 'Spring', 5: 'Spring',
           6: 'Summer', 7: 'Summer', 8: 'Summer', 9: 'Autumn', 10: 'Autumn', 11: 'Autumn'}
WATER_HEATER = re.compile(r'water\s*heater|\bhwh\b|hot\s*water', re.IGNORECASE)
PV_CANDIDATES = (0, 2, 4, 6, 8, 10, 12, 14, 16)
BATTERY_CANDIDATES = (0, 5, 10, 13.5, 20, 27, 40)


def find_water_heater(channel_names) -> str | None:
    """The first channel whose name looks like a water heater, else None."""
    for name in sorted(n for n in channel_names if n):
        if WATER_HEATER.search(name):
            return name
    return None


def _solar_noon(month: int) -> float:
    # Local clock time; daylight time (roughly Apr-Oct) moves solar noon to ~13:00.
    return 13.0 if 4 <= month <= 10 else 12.0


def pv_shape(month: int) -> list[float]:
    """Fraction of a day's PV energy produced in each local clock hour (sums to 1)."""
    length, noon = DAY_LENGTH[month], _solar_noon(month)
    rise = noon - length / 2

    def cumulative(t: float) -> float:
        t = min(max(t, rise), rise + length)
        return (1 - math.cos(math.pi * (t - rise) / length)) / 2

    return [cumulative(h + 1) - cumulative(h) for h in range(24)]


def complete_days(rows: list[dict]) -> dict[date, list[dict]]:
    """Group hourly rows into days, keeping only days with 24 distinct hours."""
    days: dict[date, dict[int, dict]] = defaultdict(dict)
    for row in rows:
        start = row['start']
        if row.get('total_kwh') is None or not math.isfinite(row['total_kwh']):
            continue
        days[start.date()][start.hour] = row
    return {day: [hours[h] for h in range(24)] for day, hours in sorted(days.items())
            if len(hours) == 24}


def _in_window(hour: int, window: tuple[int, int]) -> bool:
    start, end = window
    return start <= hour < end if start < end else hour >= start or hour < end


def load_profile(days: dict[date, list[dict]], evening=DEFAULTS['evening_hours']) -> dict:
    """Measured daily, monthly, seasonal and hour-of-day load, with water heater share."""
    if not days:
        return {'days': 0}
    has_wh = any(r.get('wh_kwh') is not None for hours in days.values() for r in hours)
    totals = [sum(r['total_kwh'] for r in hours) for hours in days.values()]
    wh = [sum(r.get('wh_kwh') or 0 for r in hours) for hours in days.values()]
    months: dict[str, list] = defaultdict(list)
    seasons: dict[str, list] = defaultdict(list)
    hourly_total, hourly_wh = [0.0] * 24, [0.0] * 24
    evening_total = evening_wh = 0.0
    for (day, hours), total, heater in zip(days.items(), totals, wh, strict=True):
        months[day.strftime('%Y-%m')].append((total, heater))
        seasons[SEASONS[day.month]].append((total, heater))
        for row in hours:
            hourly_total[row['start'].hour] += row['total_kwh']
            hourly_wh[row['start'].hour] += row.get('wh_kwh') or 0
            if _in_window(row['start'].hour, evening):
                evening_total += row['total_kwh']
                evening_wh += row.get('wh_kwh') or 0
    n = len(days)

    def summary(items):
        t = sum(i[0] for i in items) / len(items)
        h = sum(i[1] for i in items) / len(items)
        return {'days': len(items), 'kwh_per_day': t, 'wh_kwh_per_day': h if has_wh else None,
                'wh_share_pct': (h / t * 100) if has_wh and t else None}

    ordered = sorted(totals)
    return {
        'days': n, 'first_day': min(days).isoformat(), 'last_day': max(days).isoformat(),
        'has_water_heater': has_wh,
        'kwh_per_day': sum(totals) / n,
        'kwh_per_day_p10': ordered[int(0.1 * (n - 1))], 'kwh_per_day_p90': ordered[int(0.9 * (n - 1))],
        'wh_kwh_per_day': sum(wh) / n if has_wh else None,
        'wh_share_pct': sum(wh) / sum(totals) * 100 if has_wh and sum(totals) else None,
        'evening_window': list(evening),
        'evening_kwh_per_day': evening_total / n,
        'evening_share_of_load_pct': evening_total / sum(totals) * 100 if sum(totals) else None,
        'wh_evening_kwh_per_day': evening_wh / n if has_wh else None,
        'wh_share_of_evening_pct': evening_wh / evening_total * 100 if has_wh and evening_total else None,
        'months': {k: summary(v) for k, v in sorted(months.items())},
        'seasons': {k: summary(v) for k, v in seasons.items()},
        'hour_of_day_kwh': [v / n for v in hourly_total],
        'hour_of_day_wh_kwh': [v / n for v in hourly_wh] if has_wh else None,
    }


def scenario_days(days: dict[date, list[dict]], scenario: str, options=None) -> dict[date, list[float]]:
    """Hourly loads per day for: current, no_wh (removed), hpwh, timer."""
    o = dict(DEFAULTS, **(options or {}))
    out = {}
    for day, hours in days.items():
        total = [r['total_kwh'] for r in hours]
        heater = [r.get('wh_kwh') or 0.0 for r in hours]
        if scenario == 'current':
            out[day] = total
        elif scenario == 'no_wh':
            out[day] = [max(0.0, t - w) for t, w in zip(total, heater, strict=True)]
        elif scenario == 'hpwh':
            out[day] = [max(0.0, t - w) + w * o['hpwh_factor'] for t, w in zip(total, heater, strict=True)]
        elif scenario == 'timer':
            start, end = o['timer_window']
            moved = sum(heater) * (1 + o['timer_loss_pct'] / 100) / (end - start)
            out[day] = [max(0.0, t - w) + (moved if start <= h < end else 0.0)
                        for h, (t, w) in enumerate(zip(total, heater, strict=True))]
        else:
            raise ValueError(f'Unknown scenario {scenario}')
    return out


def simulate(loads: dict[date, list[float]], pv_kw: float, battery_kwh: float = 0.0,
             battery_kw: float | None = None, options=None) -> dict:
    """Hour-by-hour PV + battery dispatch (charge from surplus only, discharge to load)."""
    o = dict(DEFAULTS, **(options or {}))
    usable = battery_kwh * (1 - o['battery_reserve_pct'] / 100)
    power = battery_kw if battery_kw is not None else max(battery_kwh / 2.7, 0.0)
    leg = math.sqrt(o['battery_efficiency'])
    soc = usable / 2
    load = generation = direct = imported = exported = discharged = 0.0
    shapes = {m: pv_shape(m) for m in range(1, 13)}
    for day, hours in loads.items():
        daily = pv_kw * PV_YIELD[day.month]
        for hour, demand in enumerate(hours):
            pv = daily * shapes[day.month][hour]
            load += demand
            generation += pv
            used = min(demand, pv)
            direct += used
            surplus, deficit = pv - used, demand - used
            if surplus > 0 and usable:
                charge = min(surplus, power, (usable - soc) / leg)
                soc += charge * leg
                surplus -= charge
            if deficit > 0 and usable:
                give = min(deficit, power, soc * leg)
                soc -= give / leg
                deficit -= give
                discharged += give
            exported += surplus
            imported += deficit
    self_used = direct + discharged
    return {
        'pv_kw': pv_kw, 'battery_kwh': battery_kwh, 'battery_kw': round(power, 2),
        'load_kwh': load, 'generation_kwh': generation, 'import_kwh': imported,
        'export_kwh': exported, 'battery_discharge_kwh': discharged,
        'self_consumption_pct': self_used / generation * 100 if generation else None,
        'self_sufficiency_pct': (load - imported) / load * 100 if load else None,
    }


def overnight_need(loads: dict[date, list[float]]) -> dict:
    """Energy from modelled sunset to the next sunrise (no PV), per night."""
    nights = []
    ordered = sorted(loads)
    for day, after in zip(ordered, ordered[1:], strict=False):
        if after - day != timedelta(days=1):
            continue
        length, noon = DAY_LENGTH[day.month], _solar_noon(day.month)
        sunset = math.ceil(noon + length / 2)
        sunrise = math.floor(_solar_noon(after.month) - DAY_LENGTH[after.month] / 2)
        nights.append(sum(loads[day][sunset:]) + sum(loads[after][:sunrise]))
    if not nights:
        return {'nights': 0}
    nights.sort()
    pick = lambda q: nights[min(len(nights) - 1, int(q * (len(nights) - 1)))]  # noqa: E731
    return {'nights': len(nights), 'p50_kwh': pick(0.5), 'p90_kwh': pick(0.9), 'max_kwh': nights[-1]}


def evening_power(loads: dict[date, list[float]], window=DEFAULTS['evening_hours']) -> dict:
    """Hourly-average kW in the evening window (instantaneous peaks are higher)."""
    values = sorted(v for hours in loads.values() for h, v in enumerate(hours) if _in_window(h, window))
    if not values:
        return {}
    return {'p95_kw': values[int(0.95 * (len(values) - 1))], 'p99_kw': values[int(0.99 * (len(values) - 1))],
            'max_kw': values[-1]}


def _round_up(value: float, step: float) -> float:
    return math.ceil(value / step - 1e-9) * step


def _economics(result: dict, baseline_kwh: float, days: int, o: dict, export_cents: float) -> dict:
    cost = (result['pv_kw'] * 1000 * o['pv_cost_per_w'] + result['battery_kwh'] * o['battery_cost_per_kwh'])
    cost *= 1 - o['federal_credit_pct'] / 100
    saved = (baseline_kwh - result['import_kwh']) * o['rate_cents'] / 100 + result['export_kwh'] * export_cents / 100
    annual = saved / days * 365 if days else 0
    return {'installed_cost': round(cost), 'annualized_savings': round(annual),
            'simple_payback_years': round(cost / annual, 1) if annual > 0 else None}


def size_scenario(loads: dict[date, list[float]], baseline: dict[date, list[float]], options=None) -> dict:
    """Net-zero PV size for the measured window, battery size from the overnight need."""
    o = dict(DEFAULTS, **(options or {}))
    days = len(loads)
    load = sum(sum(h) for h in loads.values())
    yield_sum = sum(PV_YIELD[d.month] for d in loads)
    net_zero_kw = load / yield_sum if yield_sum else 0
    need = overnight_need(loads)
    usable_fraction = 1 - o['battery_reserve_pct'] / 100
    battery = _round_up(need.get('p50_kwh', 0) / usable_fraction / math.sqrt(o['battery_efficiency']), 2.5)
    power = evening_power(loads)
    battery_kw = _round_up(max(power.get('p99_kw', 0) * 1.25, battery / 2.7), 0.5)
    pv_kw = _round_up(net_zero_kw, 0.5)
    base_kwh = sum(sum(h) for h in baseline.values())
    grid = []
    for kw in PV_CANDIDATES:
        for kwh in BATTERY_CANDIDATES:
            grid.append(simulate(loads, kw, kwh, None, o))
    recommended = simulate(loads, pv_kw, battery, battery_kw, o)
    pv_only = simulate(loads, pv_kw, 0, None, o)
    return {
        'days': days, 'load_kwh_per_day': load / days if days else None,
        'net_zero_pv_kw': net_zero_kw, 'pv_kw': pv_kw,
        'battery_kwh': battery, 'battery_kw': battery_kw,
        'overnight': need, 'evening_power': power,
        'recommended': recommended, 'pv_only': pv_only,
        'economics': {
            'pv_only_net_metering': _economics(pv_only, base_kwh, days, o, o['rate_cents']),
            'pv_only_low_export': _economics(pv_only, base_kwh, days, o, o['export_cents_low']),
            'pv_battery_net_metering': _economics(recommended, base_kwh, days, o, o['rate_cents']),
            'pv_battery_low_export': _economics(recommended, base_kwh, days, o, o['export_cents_low']),
        },
        'grid': grid,
    }


def build_guidance(rows: list[dict], options=None, include_grid: bool = False) -> dict:
    """Full guidance document: profile, scenarios, sizing, assumptions and caveats."""
    o = dict(DEFAULTS, **(options or {}))
    days = complete_days(rows)
    profile = load_profile(days, o['evening_hours'])
    result = {'assumptions': {k: (list(v) if isinstance(v, tuple) else v) for k, v in o.items()},
              'pv_yield_kwh_per_kw_day': PV_YIELD, 'profile': profile, 'scenarios': {},
              'caveats': [
                  'Solar production is modelled from a typical-year yield table, not measured: '
                  'the panel has no solar or battery channels.',
                  'Only months present in the data are modelled; missing seasons are not '
                  'extrapolated except in the annualized savings, which scale the measured '
                  'window to 365 days.',
                  'Hourly averages hide start-up surges; size inverter surge capability '
                  'from the 1-minute or 1-second data, not from these kW figures.',
                  'Costs, export credit and incentives are assumptions; get local quotes and '
                  'the utility net-metering tariff before deciding.',
              ]}
    if not days:
        result['caveats'].insert(0, 'No complete days of hourly data are available.')
        return result
    baseline = scenario_days(days, 'current', o)
    names = ['current'] + (['no_wh', 'hpwh', 'timer'] if profile['has_water_heater'] else [])
    for name in names:
        loads = scenario_days(days, name, o)
        sized = size_scenario(loads, baseline, o)
        if not include_grid:
            sized.pop('grid')
        result['scenarios'][name] = sized
    return result


def rows_from_hourly(records: list[dict], water_heater: str | None,
                     min_coverage: float = 0.9) -> list[dict]:
    """Turn energy_hourly rows into guidance rows.

    The whole-home total is Main when present, otherwise the sum of the mains legs.
    Hours observed for less than `min_coverage` are dropped; covered hours are scaled
    to a full hour so skipped poll minutes do not bias the load low.
    """
    by_hour: dict[str, dict] = defaultdict(dict)
    for record in records:
        by_hour[record['hour_start']][record['channel_name']] = record

    def scaled(record):
        if not record or record['observed_seconds'] < 3600 * min_coverage:
            return None
        return record['kwh'] * 3600 / min(3600, record['observed_seconds'])

    rows = []
    for stamp, channels in by_hour.items():
        total = scaled(channels.get('Main'))
        if total is None:
            legs = [scaled(channels.get(n)) for n in ('Mains_A', 'Mains_B') if n in channels]
            total = sum(legs) if legs and None not in legs else None
        if total is None:
            continue
        heater = scaled(channels.get(water_heater)) if water_heater else None
        if water_heater and heater is None:
            continue
        rows.append({'start': datetime.fromisoformat(stamp), 'total_kwh': total, 'wh_kwh': heater})
    return rows
