"""Bounded, UTC-bucketed views of recorded Aqara collector observations."""
import math
from datetime import datetime, timedelta, timezone

import energy

WINDOWS = {'4h': (4, 300), '24h': (24, 1800), '7d': (168, 10800),
           '30d': (720, 43200), 'all': (None, None)}
COLORS = ('--olive-600', '--amber', '--green', '--red', '--stone-500', '--olive-400')


def get_trends(window: str = '4h', metric: str = 'temperature',
               device_id: str | None = None, now: datetime | None = None) -> dict:
    """Aggregate at most 121 UTC buckets per sensor; never read the cloud.

    Means/min/max describe online collector observations, including cached
    snapshots. They do not establish fresh measurements or physical coverage.
    Missing or wholly offline buckets stay unknown rather than becoming zero.
    """
    if window not in WINDOWS or metric not in {'temperature', 'humidity'}:
        raise ValueError('Invalid Aqara history window or metric')
    if device_id is not None and (not isinstance(device_id, str) or not 1 <= len(device_id) <= 200):
        raise ValueError('Invalid sensor identity')
    end = now or datetime.now(timezone.utc)
    if end.tzinfo is None:
        raise ValueError('History time requires a timezone')
    end = end.astimezone(timezone.utc)
    hours, seconds = WINDOWS[window]
    scope = ' AND device_id=?' if device_id is not None else ''
    scope_args = (device_id,) if device_id is not None else ()
    conn = energy._connect()
    try:
        if hours is None:
            first = conn.execute('SELECT MIN(timestamp) FROM aqara_local_observations '
                                 'WHERE timestamp<=?' + scope,
                                 (end.isoformat(), *scope_args)).fetchone()[0]
            start = datetime.fromisoformat(first) if first else end - timedelta(hours=4)
            seconds = max(60, math.ceil(max(1, (end - start).total_seconds()) / 120))
        else:
            start = end - timedelta(hours=hours)
        if start >= end:
            start = end - timedelta(minutes=1)
        # The metric is an explicit whitelist, not user-supplied SQL.
        measurement = f'CASE WHEN online=1 THEN {metric} END'
        rows = conn.execute(f'''SELECT device_id,
            CAST(strftime('%s', timestamp) AS INTEGER)/? AS bucket,
            AVG({measurement}) AS value, MIN({measurement}) AS minimum,
            MAX({measurement}) AS maximum, COUNT({measurement}) AS samples
            FROM aqara_local_observations WHERE timestamp>=? AND timestamp<=? {scope}
            GROUP BY device_id,bucket ORDER BY device_id,bucket''',
            (seconds, start.isoformat(), end.isoformat(), *scope_args)).fetchall()
    finally:
        conn.close()
    series = {}
    for row in rows:
        point = dict(row)
        series.setdefault(point.pop('device_id'), []).append(point)
    return {'start': start.isoformat(), 'end': end.isoformat(), 'seconds': seconds,
            'metric': metric, 'window': window, 'series': series}


def chart_model(trends: dict, sensors: list[dict], unit: str = 'F') -> dict:
    """SVG coordinates on a shared axis, with breaks across unknown buckets."""
    if unit not in {'F', 'C'}:
        raise ValueError('Invalid temperature unit')
    start = datetime.fromisoformat(trends['start'])
    end = datetime.fromisoformat(trends['end'])
    span = (end - start).total_seconds()
    convert = (lambda value: value * 9 / 5 + 32) if (
        trends['metric'] == 'temperature' and unit == 'F') else (lambda value: value)
    series, values = [], []
    for index, sensor in enumerate(sensors):
        points = []
        for row in trends['series'].get(sensor['did'], []):
            if row['value'] is None:
                continue
            middle = datetime.fromtimestamp((row['bucket'] + 0.5) * trends['seconds'], timezone.utc)
            stamp = min(end, max(start, middle))
            value = convert(row['value'])
            low, high = convert(row['minimum']), convert(row['maximum'])
            values.extend((low, high))
            points.append({**row, 'value': value, 'minimum': low, 'maximum': high,
                           'timestamp': datetime.fromtimestamp(
                               row['bucket'] * trends['seconds'], timezone.utc).isoformat(),
                           'x': 30 + 350 * (stamp - start).total_seconds() / span})
        series.append({'name': sensor['name'], 'color': f'var({COLORS[index % len(COLORS)]})',
                       'points': points, 'segments': []})
    lower, upper = min(values, default=0), max(values, default=1)
    padding = max(1, (upper - lower) * 0.1)
    lower, upper = lower - padding, upper + padding
    for item in series:
        previous, segment = None, []
        for point in item['points']:
            point['y'] = 140 - 110 * (point['value'] - lower) / (upper - lower)
            if previous is not None and point['bucket'] != previous + 1:
                item['segments'].append(segment)
                segment = []
            segment.append(f"{point['x']:.2f},{point['y']:.2f}")
            previous = point['bucket']
        if segment:
            item['segments'].append(segment)
    return {'series': series, 'lower': lower, 'upper': upper, 'start': trends['start'],
            'end': trends['end'], 'seconds': trends['seconds'], 'has_data': bool(values),
            'unit': ('F' if unit == 'F' else 'C') if trends['metric'] == 'temperature' else '% RH'}
