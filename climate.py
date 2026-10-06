"""Climate extension storage and replay. Adapters submit observations, never UI state.

Temperatures are Celsius; observation timestamps are UTC. Demo data never enters SQLite.
"""
import math
from datetime import datetime, timedelta, timezone

import energy

ROOMS = [
    {'id': 'living', 'name': 'Living room', 'x': 30, 'y': 40, 'width': 320, 'height': 190},
    {'id': 'kitchen', 'name': 'Kitchen', 'x': 350, 'y': 40, 'width': 220, 'height': 190},
    {'id': 'bedroom', 'name': 'Bedroom', 'x': 30, 'y': 230, 'width': 250, 'height': 180},
    {'id': 'office', 'name': 'Office', 'x': 280, 'y': 230, 'width': 290, 'height': 180},
    {'id': 'outdoor', 'name': 'Outside', 'x': 600, 'y': 40, 'width': 140, 'height': 370},
]
ROOM_IDS = {room['id'] for room in ROOMS}


def _number(value, name: str, low: float, high: float) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError(f'{name} must be a number')
    value = float(value)
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{name} must be between {low} and {high}')
    return value


def ingest_observations(observations: list[dict], now: datetime | None = None) -> dict:
    """Validate the entire batch before an atomic write; deduplicate source timestamps.

    Each row: source, sensor_id, name, timestamp (ISO8601 with zone), temperature_c,
    optional humidity_pct/battery_pct. Adapters must submit source observation time,
    not their polling time. Room placement is managed separately and survives updates.
    """
    now = now or datetime.now(timezone.utc)
    if not isinstance(observations, list) or not 1 <= len(observations) <= 1000:
        raise ValueError('observations must contain 1–1000 readings')
    validated = []
    for row in observations:
        if not isinstance(row, dict):
            raise ValueError('Each observation must be an object')
        for key in ('source', 'sensor_id', 'name', 'timestamp'):
            if not isinstance(row.get(key), str) or not 1 <= len(row[key]) <= 200:
                raise ValueError(f'{key} must be a nonempty string of at most 200 characters')
        if row['source'] not in {'aqara', 'home_assistant', 'manual'}:
            raise ValueError('Unsupported source')
        stamp = datetime.fromisoformat(row['timestamp'].replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            raise ValueError('timestamp requires a timezone')
        stamp = stamp.astimezone(timezone.utc)
        if stamp > now + timedelta(minutes=5) or stamp < now - timedelta(days=energy.DB_RETENTION_DAYS):
            raise ValueError('timestamp outside the retention window or in the future')
        temp = _number(row.get('temperature_c'), 'temperature_c', -80, 80)
        if temp is None:
            raise ValueError('temperature_c is required; unavailable readings should be skipped')
        humidity = _number(row.get('humidity_pct'), 'humidity_pct', 0, 100)
        battery = _number(row.get('battery_pct'), 'battery_pct', 0, 100)
        validated.append((row['source'], row['sensor_id'], row['name'], stamp.isoformat(), temp, humidity, battery))
    conn = energy._connect()
    try:
        with conn:
            inserted = 0
            for source, sensor_id, name, stamp, temp, humidity, battery in validated:
                conn.execute('''INSERT INTO climate_sensors(source,sensor_id,name) VALUES(?,?,?)
                    ON CONFLICT(source,sensor_id) DO UPDATE SET name=excluded.name''',
                    (source, sensor_id, name))
                inserted += conn.execute('''INSERT OR IGNORE INTO climate_readings
                    (source,sensor_id,timestamp,temperature_c,humidity_pct,battery_pct)
                    VALUES(?,?,?,?,?,?)''', (source, sensor_id, stamp, temp, humidity, battery)).rowcount
            conn.execute('DELETE FROM climate_readings WHERE timestamp<?',
                         ((now - timedelta(days=energy.DB_RETENTION_DAYS)).isoformat(),))
        return {'inserted': inserted, 'duplicates': len(validated) - inserted}
    finally:
        conn.close()


def place_sensor(source: str, sensor_id: str, room_id: str | None) -> None:
    """Assign a sensor to a schematic room; null means unplaced."""
    if room_id is not None and room_id not in ROOM_IDS:
        raise ValueError('Unknown room')
    conn = energy._connect()
    try:
        with conn:
            result = conn.execute('UPDATE climate_sensors SET room_id=? WHERE source=? AND sensor_id=?',
                                  (room_id, source, sensor_id))
            if result.rowcount != 1:
                raise ValueError('Unknown sensor')
    finally:
        conn.close()


def get_replay(days: int = 1, demo: bool = False, now: datetime | None = None) -> dict:
    """Hourly averages only where observations exist; gaps stay null.

    Power uses the existing device-scoped Main history query. It is grouped by local
    hour for one day and local day for longer windows, never interpreted as HVAC power.
    """
    if days not in (1, 7, 30):
        raise ValueError('days must be 1, 7 or 30')
    now = now or datetime.now(timezone.utc)
    start = (now - timedelta(days=days)).replace(minute=0, second=0, microsecond=0)
    stamps = [start + timedelta(hours=i) for i in range(days * 24 + 1)]
    frames = [{'timestamp': stamp.isoformat(), 'temperatures': {}, 'humidity': {}} for stamp in stamps]
    sensors = []
    if demo:
        for index, room in enumerate(ROOMS):
            key = f'demo:{room["id"]}'
            sensors.append({'key': key, 'source': 'demo', 'sensor_id': room['id'],
                            'name': room['name'], 'room_id': room['id']})
            for i, frame in enumerate(frames):
                phase = (stamps[i].hour - 8) / 24 * 2 * math.pi
                temp = 21 + index * .45 + math.sin(phase - index * .35) * 1.6
                if room['id'] == 'outdoor':
                    temp = 18 + 7 * math.sin(phase)
                frame['temperatures'][key] = round(temp, 1)
                frame['humidity'][key] = round(48 + 8 * math.cos(phase), 1)
        power = [{'period': stamp.astimezone().strftime('%Y-%m-%d %H:00'),
                  'total_kwh': round(.35 + max(0, math.sin((stamp.hour - 8) / 24 * 2 * math.pi)) * 1.2, 3)}
                 for stamp in stamps]
    else:
        conn = energy._connect()
        try:
            sensors = [dict(row) for row in conn.execute(
                'SELECT source,sensor_id,name,room_id FROM climate_sensors ORDER BY name')]
            for sensor in sensors:
                sensor['key'] = f'{sensor["source"]}:{sensor["sensor_id"]}'
            rows = conn.execute('''SELECT source,sensor_id,substr(timestamp,1,13) hour,
                AVG(temperature_c) temperature_c,AVG(humidity_pct) humidity_pct,COUNT(*) samples
                FROM climate_readings WHERE timestamp>=? AND timestamp<=?
                GROUP BY source,sensor_id,hour ORDER BY hour''', (start.isoformat(), now.isoformat()))
            by_hour = {stamp.strftime('%Y-%m-%dT%H'): frame for stamp, frame in zip(stamps, frames, strict=True)}
            for row in rows:
                frame = by_hour.get(row['hour'])
                if frame is not None:
                    key = f'{row["source"]}:{row["sensor_id"]}'
                    frame['temperatures'][key] = round(row['temperature_c'], 2)
                    frame['humidity'][key] = row['humidity_pct']
        finally:
            conn.close()
        history = energy.get_circuit_history('Main', now=now.astimezone().replace(tzinfo=None))
        power = next((w['series'] for w in history['windows'] if w['days'] == days), []) if history else []
    return {'demo': demo, 'days': days, 'rooms': ROOMS, 'sensors': sensors,
            'frames': frames, 'power': power, 'power_resolution': 'hour' if demo or days == 1 else 'day',
            'rate_per_kwh': energy.RATE_CENTS / 100,
            'retention_days': energy.DB_RETENTION_DAYS,
            'warning': 'Simulated temperatures and power — never stored' if demo else
                       'Hourly observed averages. Blank rooms and gaps mean no readings. Power is whole-home recorded energy.'}
