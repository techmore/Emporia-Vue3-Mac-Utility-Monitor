"""Bounded comparison views of recorded Kasa and Comfort observations."""
from datetime import datetime, timedelta, timezone
import hashlib

import energy

WINDOWS = {'4h': (4, 300), '24h': (24, 900), '7d': (168, 3600),
           '14d': (336, 7200), '30d': (720, 10800)}
MAX_STATE_GAP_SECONDS = 180


def bounds(window: str, end: str | None = None, now: datetime | None = None) -> tuple:
    if window not in WINDOWS:
        raise ValueError('Choose 4 hours, 24 hours, 7, 14 or 30 days')
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError('History times require a timezone')
    if end is not None and (not isinstance(end, str) or len(end) > 64):
        raise ValueError('Invalid window end')
    finish = datetime.fromisoformat(end) if end is not None else current
    if finish.tzinfo is None or finish > current:
        raise ValueError('The window end must have a timezone and cannot be in the future')
    finish = finish.astimezone(timezone.utc)
    hours, seconds = WINDOWS[window]
    return finish - timedelta(hours=hours), finish, seconds


def color_slots(identifiers: list[str]) -> dict:
    """Assign different palette slots across the full catalog, before filtering."""
    slots, used = {}, set()
    for identifier in sorted(set(identifiers)):
        slot = int(hashlib.sha256(identifier.encode()).hexdigest()[:8], 16) % 16
        for _ in range(16):
            if slot not in used:
                break
            slot = (slot + 1) % 16
        slots[identifier] = slot
        used.add(slot)
    return slots


def get_history(kind: str, window: str = '24h', end: str | None = None,
                now: datetime | None = None) -> dict:
    """Return <=241 buckets/device; no raw minute/second histories enter Python.

    Enabled time assumes the earlier state holds between successful observations
    at most three minutes apart. Failed queries and longer gaps remain uncovered.
    HVAC enabled state does not establish compressor operation or electricity use.
    """
    start, finish, seconds = bounds(window, end, now)
    source_start = start - timedelta(seconds=MAX_STATE_GAP_SECONDS)
    if kind == 'kasa':
        source = '''SELECT o.device_id AS identity, o.timestamp AS stamp,
            o.status='ok' AS valid, o.is_on AS enabled,
            NULL AS temperature, NULL AS heat_setpoint, NULL AS cool_setpoint,
            NULL AS humidity, NULL AS mode,
            CASE WHEN m.brightness BETWEEN 0 AND 100 THEN m.brightness END AS brightness,
            CASE WHEN m.duration_ms BETWEEN 0 AND 20000 THEN m.duration_ms END AS latency
            FROM kasa_observations o LEFT JOIN kasa_query_metrics m
            ON m.device_id=o.device_id AND m.timestamp=o.timestamp
            WHERE o.timestamp>=? AND o.timestamp<=?'''
        catalog_query = 'SELECT id AS identity,label AS name FROM kasa_devices ORDER BY created_at,id'
        catalog_args = ()
        metrics = [{'id': 'on_percent', 'label': 'Observed ON', 'unit': '%'},
                   {'id': 'brightness', 'label': 'Dimmer setting', 'unit': '%'},
                   {'id': 'latency', 'label': 'Query latency', 'unit': 'ms'}]
    elif kind == 'mitsubishi':
        source = '''SELECT serial AS identity, queried_at AS stamp,
            json_extract(snapshot,'$.connected')=1 AS valid,
            json_extract(snapshot,'$.is_on') AS enabled,
            json_extract(snapshot,'$.temperature_c') AS temperature,
            json_extract(snapshot,'$.heat_setpoint_c') AS heat_setpoint,
            json_extract(snapshot,'$.cool_setpoint_c') AS cool_setpoint,
            json_extract(snapshot,'$.humidity_pct') AS humidity,
            json_extract(snapshot,'$.mode') AS mode,
            NULL AS brightness, NULL AS latency
            FROM mitsubishi_observations WHERE queried_at>=? AND queried_at<=?
            AND json_valid(snapshot)'''
        catalog_query = '''SELECT serial AS identity, json_extract(snapshot,'$.name') AS name
            FROM mitsubishi_observations o WHERE queried_at=(
                SELECT MAX(queried_at) FROM mitsubishi_observations
                WHERE serial=o.serial AND queried_at<=?) AND json_valid(snapshot)
            ORDER BY serial'''
        catalog_args = ((now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(),)
        metrics = [{'id': 'temperature', 'label': 'Room temperature', 'unit': 'temperature'},
                   {'id': 'heat_setpoint', 'label': 'Heating setpoint', 'unit': 'temperature'},
                   {'id': 'cool_setpoint', 'label': 'Cooling setpoint', 'unit': 'temperature'},
                   {'id': 'humidity', 'label': 'Humidity', 'unit': '% RH'},
                   {'id': 'on_percent', 'label': 'HVAC enabled', 'unit': '%'}]
    else:
        raise ValueError('Unknown history module')

    cte = f'''WITH source AS ({source}), timed AS (
        SELECT *, CAST(strftime('%s',stamp) AS REAL) AS t FROM source
    ), sequenced AS (
        SELECT *, LEAD(t) OVER (PARTITION BY identity ORDER BY stamp) AS next_t,
        LEAD(valid) OVER (PARTITION BY identity ORDER BY stamp) AS next_valid,
        LEAD(enabled) OVER (PARTITION BY identity ORDER BY stamp) AS next_enabled
        FROM timed
    )'''
    args = (source_start.isoformat(), finish.isoformat())
    start_epoch, end_epoch = start.timestamp(), finish.timestamp()
    conn = energy._connect()
    try:
        catalog = [dict(row) for row in conn.execute(catalog_query, catalog_args)]
        rows = conn.execute(cte + '''SELECT identity, CAST(t/? AS INTEGER) AS bucket,
            COUNT(*) AS samples, SUM(valid) AS valid_samples,
            AVG(CASE WHEN valid THEN enabled*100.0 END) AS on_percent,
            AVG(CASE WHEN valid THEN temperature END) AS temperature,
            MIN(CASE WHEN valid THEN temperature END) AS minimum_temperature,
            MAX(CASE WHEN valid THEN temperature END) AS maximum_temperature,
            AVG(CASE WHEN valid THEN heat_setpoint END) AS heat_setpoint,
            AVG(CASE WHEN valid THEN cool_setpoint END) AS cool_setpoint,
            AVG(CASE WHEN valid THEN humidity END) AS humidity,
            AVG(CASE WHEN valid THEN brightness END) AS brightness,
            AVG(CASE WHEN valid THEN latency END) AS latency
            FROM sequenced WHERE t>=? AND t<=?
            GROUP BY identity,bucket ORDER BY identity,bucket''',
            (*args, seconds, start_epoch, end_epoch)).fetchall()
        totals = conn.execute(cte + '''SELECT identity,
            SUM(CASE WHEN t>=? THEN 1 ELSE 0 END) AS samples,
            SUM(CASE WHEN t>=? AND valid THEN 1 ELSE 0 END) AS valid_samples,
            MIN(CASE WHEN t>=? THEN stamp END) AS first_observation,
            MAX(CASE WHEN t>=? THEN stamp END) AS last_observation,
            SUM(CASE WHEN valid AND next_valid AND enabled IN (0,1) AND next_enabled IN (0,1)
                AND next_t-t BETWEEN 0 AND ?
                AND next_t>? THEN MAX(0, MIN(next_t,?)-MAX(t,?)) ELSE 0 END) AS covered_seconds,
            SUM(CASE WHEN valid AND next_valid AND enabled=1 AND next_enabled IN (0,1)
                AND next_t-t BETWEEN 0 AND ?
                AND next_t>? THEN MAX(0, MIN(next_t,?)-MAX(t,?)) ELSE 0 END) AS enabled_seconds
            FROM sequenced GROUP BY identity''',
            (*args, start_epoch, start_epoch, start_epoch, start_epoch,
             MAX_STATE_GAP_SECONDS, start_epoch, end_epoch, start_epoch,
             MAX_STATE_GAP_SECONDS, start_epoch, end_epoch, start_epoch)).fetchall()
        modes = conn.execute(cte + '''SELECT identity,mode,COUNT(*) AS samples
            FROM sequenced WHERE t>=? AND valid AND mode IS NOT NULL
            GROUP BY identity,mode''', (*args, start_epoch)).fetchall()
    finally:
        conn.close()
    colors = color_slots([device['identity'] for device in catalog])
    series = {device['identity']: {
        'id': device['identity'], 'name': device['name'] or device['identity'],
        'color_index': colors[device['identity']], 'points': [],
        'summary': {'samples': 0, 'valid_samples': 0, 'covered_seconds': 0,
                    'enabled_seconds': 0, 'modes': {}},
    } for device in catalog}
    for row in rows:
        if row['identity'] not in series:
            continue
        point = dict(row)
        point.pop('identity')
        point['timestamp'] = datetime.fromtimestamp(row['bucket'] * seconds, timezone.utc).isoformat()
        series[row['identity']]['points'].append(point)
    for row in totals:
        if row['identity'] in series:
            series[row['identity']]['summary'].update({
                key: value for key, value in dict(row).items() if key != 'identity'
            })
    for row in modes:
        if row['identity'] in series:
            series[row['identity']]['summary']['modes'][row['mode']] = row['samples']
    return {'kind': kind, 'window': window, 'start': start.isoformat(), 'end': finish.isoformat(),
            'bucket_seconds': seconds, 'max_state_gap_seconds': MAX_STATE_GAP_SECONDS,
            'metrics': metrics, 'series': list(series.values())}
