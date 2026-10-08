"""Read locally collected Aqara Matter observations from the shared database."""
import math
from datetime import datetime, timedelta, timezone

import energy


def decode_sensors(node: dict) -> list[dict]:
    if not isinstance(node, dict):
        return []
    attributes = node.get('attributes', {})
    if not isinstance(attributes, dict):
        return []
    sensors = []
    for path, identifier in attributes.items():
        if (not isinstance(path, str) or not path.endswith('/57/18')
                or not isinstance(identifier, str) or not 1 <= len(identifier) <= 200):
            continue
        endpoint = path.split('/')[0]
        children = attributes.get(f'{endpoint}/29/3', [])
        if not isinstance(children, list):
            continue
        temperature = humidity = None
        for child in children:
            if type(child) is not int or not 0 <= child <= 65535:
                continue
            temp = attributes.get(f'{child}/1026/0')
            hum = attributes.get(f'{child}/1029/0')
            if type(temp) in (int, float) and math.isfinite(temp) and -8000 <= temp <= 8000:
                temperature = temp / 100
            if type(hum) in (int, float) and math.isfinite(hum) and 0 <= hum <= 10000:
                humidity = hum / 100
        if temperature is None and humidity is None:
            continue
        raw_battery = attributes.get(f'{endpoint}/47/12')
        sensors.append({'did': identifier, 'name': 'Sensor ' + identifier.split('.')[-1][-6:],
                        'model': str(attributes.get(f'{endpoint}/57/3') or 'Aqara sensor')[:200],
                        'temperature': temperature, 'humidity': humidity,
                        'battery': raw_battery / 2 if type(raw_battery) in (int, float) and math.isfinite(raw_battery) and 0 <= raw_battery <= 200 else None,
                        'online': node.get('available') is True and attributes.get(f'{endpoint}/57/17') is True})
    return sensors


def record_node(node: dict, source: str) -> int:
    if source not in {'matter_snapshot', 'matter_event'}:
        raise ValueError('Unknown Matter observation source')
    sensors = decode_sensors(node)
    timestamp = datetime.now(timezone.utc).isoformat()
    conn = energy._connect()
    try:
        with conn:
            for sensor in sensors:
                conn.execute('''INSERT OR IGNORE INTO aqara_local_observations
                    (device_id,timestamp,name,model,temperature,humidity,battery,online,source)
                    VALUES (?,?,?,?,?,?,?,?,?)''',
                    (sensor['did'], timestamp, sensor['name'], sensor['model'],
                     sensor['temperature'], sensor['humidity'], sensor['battery'],
                     int(sensor['online']), source))
            conn.execute('DELETE FROM aqara_local_observations WHERE timestamp<?',
                         ((datetime.now(timezone.utc) - timedelta(days=energy.DB_RETENTION_DAYS)).isoformat(),))
    finally:
        conn.close()
    return len(sensors)


def get_sensors() -> list[dict]:
    conn = energy._connect()
    try:
        return [dict(row) for row in conn.execute('''SELECT o.device_id AS did,
            COALESCE(l.name,o.name) AS name,o.model,o.temperature,o.humidity,o.battery,
            CASE WHEN o.online=1 AND julianday('now')-julianday(o.timestamp) BETWEEN 0 AND 3.0/1440
                 THEN 1 ELSE 0 END AS online,o.timestamp AS observed_at,o.source
            FROM aqara_local_observations o
            LEFT JOIN aqara_local_labels l ON l.device_id=o.device_id
            WHERE o.timestamp=(SELECT MAX(n.timestamp) FROM aqara_local_observations n
                               WHERE n.device_id=o.device_id)
            ORDER BY name''')]
    finally:
        conn.close()


def get_history() -> list[dict]:
    conn = energy._connect()
    try:
        return [dict(row) for row in conn.execute(
            'SELECT * FROM aqara_local_observations ORDER BY timestamp,device_id')]
    finally:
        conn.close()


def save_label(device_id: str, name: str) -> None:
    """Save or clear a user-verified room name without rewriting observations."""
    if not isinstance(device_id, str) or not 1 <= len(device_id) <= 200:
        raise ValueError('Invalid sensor identity')
    if (not isinstance(name, str) or len(name.strip()) > 120
            or any(ord(c) < 32 or ord(c) == 127 for c in name)):
        raise ValueError('Use a room name of at most 120 characters without control characters')
    conn = energy._connect()
    try:
        with conn:
            if not conn.execute('SELECT 1 FROM aqara_local_observations WHERE device_id=? LIMIT 1',
                                (device_id,)).fetchone():
                raise ValueError('Sensor has no recorded local observations')
            if name.strip():
                conn.execute('INSERT INTO aqara_local_labels(device_id,name) VALUES (?,?) '
                             'ON CONFLICT(device_id) DO UPDATE SET name=excluded.name',
                             (device_id, name.strip()))
            else:
                conn.execute('DELETE FROM aqara_local_labels WHERE device_id=?', (device_id,))
    finally:
        conn.close()


def iter_history():
    """Stream the export without loading retained sensor history into memory."""
    conn = energy._connect()
    try:
        cursor = conn.execute('SELECT * FROM aqara_local_observations ORDER BY timestamp,device_id')
        while rows := cursor.fetchmany(500):
            for row in rows:
                yield dict(row)
    finally:
        conn.close()
