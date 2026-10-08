"""Registered Kasa devices and observed query outcomes, separate from energy usage."""
import uuid
from datetime import datetime, timedelta, timezone

import energy
import kasa_monitor


def register_device(host: str, label: str) -> str:
    host = kasa_monitor.validate_host(host)
    if not isinstance(label, str) or not 1 <= len(label.strip()) <= 100:
        raise ValueError('Device label must contain 1-100 characters')
    identifier = uuid.uuid4().hex
    conn = energy._connect()
    try:
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            if conn.execute('SELECT COUNT(*) FROM kasa_devices').fetchone()[0] >= 16:
                raise ValueError('At most 16 Kasa devices may be registered')
            if conn.execute('SELECT 1 FROM kasa_devices WHERE host=?', (host,)).fetchone():
                raise ValueError('Address is already registered')
            conn.execute('INSERT INTO kasa_devices VALUES (?,?,?,NULL,?)',
                         (identifier, host, label.strip(), datetime.now(timezone.utc).isoformat()))
    finally:
        conn.close()
    return identifier


def get_devices() -> list[dict]:
    conn = energy._connect()
    try:
        return [dict(row) for row in conn.execute('''SELECT d.*, o.timestamp AS queried_at,
            o.status,o.is_on,o.model,o.alias,o.error_type FROM kasa_devices d
            LEFT JOIN kasa_observations o ON o.device_id=d.id AND o.timestamp=(
                SELECT MAX(timestamp) FROM kasa_observations WHERE device_id=d.id)
            ORDER BY d.created_at,d.id''')]
    finally:
        conn.close()


def remove_device(identifier: str) -> bool:
    conn = energy._connect()
    try:
        with conn:
            conn.execute('DELETE FROM kasa_observations WHERE device_id=?', (identifier,))
            return conn.execute('DELETE FROM kasa_devices WHERE id=?', (identifier,)).rowcount == 1
    finally:
        conn.close()


def record_query(identifier: str, snapshot: dict | None, error_type: str | None = None) -> bool:
    now = datetime.now(timezone.utc)
    model = alias = reported_id = None
    state = None
    if snapshot is not None:
        for key in ('model', 'device_id'):
            if not isinstance(snapshot.get(key), str) or not 1 <= len(snapshot[key]) <= 200:
                raise ValueError('Invalid Kasa device metadata')
        if not isinstance(snapshot.get('alias'), str) or len(snapshot['alias']) > 200:
            raise ValueError('Invalid Kasa alias')
        if type(snapshot.get('is_on')) is not bool:
            raise ValueError('Device state is unknown')
        state = int(snapshot['is_on'])
        model, alias, reported_id = (snapshot[key] for key in ('model', 'alias', 'device_id'))
    if error_type is not None and (not isinstance(error_type, str) or
                                   not error_type.isidentifier() or len(error_type) > 100):
        raise ValueError('Use an exception class name, not a vendor payload')
    conn = energy._connect()
    try:
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            device = conn.execute('SELECT * FROM kasa_devices WHERE id=?', (identifier,)).fetchone()
            if not device:
                raise ValueError('Device is no longer registered')
            if snapshot is not None and snapshot.get('host') != device['host']:
                raise ValueError('Query address does not match registered device')
            if reported_id and device['reported_device_id'] and reported_id != device['reported_device_id']:
                state = model = alias = None
                error_type = 'DeviceIdentityChanged'
            elif reported_id:
                conn.execute('UPDATE kasa_devices SET reported_device_id=? WHERE id=?',
                             (reported_id, identifier))
            conn.execute('INSERT INTO kasa_observations VALUES (?,?,?,?,?,?,?)',
                         (identifier, now.isoformat(), 'ok' if state is not None else 'unavailable',
                          state, model, alias, error_type))
            conn.execute('DELETE FROM kasa_observations WHERE timestamp<?',
                         ((now - timedelta(days=energy.DB_RETENTION_DAYS)).isoformat(),))
        return state is not None
    finally:
        conn.close()
