"""Read-only SER8 sensor snapshots for an explicitly marked local Home 3D preview.

No remote files, services, credentials or floor plans are changed. The destination
must be a preview database; observations retain their original measurement times.
"""
import argparse
import json
import logging
import os
import re
import shlex
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from runtime_store import write_private_json

TABLES = {
    'aqara_local_observations': ('device_id', 'timestamp', 'name', 'model', 'temperature',
                                 'humidity', 'battery', 'online', 'source'),
    'aqara_local_labels': ('device_id', 'name'),
    'climate_sensors': ('source', 'sensor_id', 'name', 'room_id'),
    'climate_readings': ('source', 'sensor_id', 'timestamp', 'temperature_c',
                         'humidity_pct', 'battery_pct'),
    'radon_readings': ('source', 'sensor_id', 'timestamp', 'name', 'measured_value',
                       'measured_unit', 'radon_bq_m3', 'received_at'),
}
REMOTE_SCRIPT = r'''
import json, sqlite3
from pathlib import Path
c=sqlite3.connect(Path('/var/lib/energy-monitor/collector/energy.db').as_uri()+'?mode=ro',uri=True)
c.row_factory=sqlite3.Row
c.execute('PRAGMA query_only=ON');c.execute('BEGIN')
queries={
 'aqara_local_observations': 'SELECT o.* FROM aqara_local_observations o WHERE timestamp=(SELECT MAX(n.timestamp) FROM aqara_local_observations n WHERE n.device_id=o.device_id)',
 'aqara_local_labels': 'SELECT * FROM aqara_local_labels',
 'climate_sensors': 'SELECT * FROM climate_sensors',
 'climate_readings': 'SELECT r.* FROM climate_readings r WHERE timestamp=(SELECT MAX(n.timestamp) FROM climate_readings n WHERE n.source=r.source AND n.sensor_id=r.sensor_id)',
}
table='radon_readings'
if c.execute("SELECT 1 FROM sqlite_master WHERE name='radon_sync_cache_state'").fetchone():
 if c.execute('SELECT 1 FROM radon_sync_cache_state WHERE singleton=1').fetchone():table='radon_cached_readings'
queries['radon_readings']=f'SELECT r.* FROM {table} r WHERE timestamp=(SELECT MAX(n.timestamp) FROM {table} n WHERE n.source=r.source AND n.sensor_id=r.sensor_id)'
result={}
for name,query in queries.items():
 if not c.execute('SELECT 1 FROM sqlite_master WHERE name=?',(name,)).fetchone():
  raise RuntimeError('Missing sensor table: '+name)
 rows=[dict(row) for row in c.execute(query+' LIMIT 513')]
 if len(rows)>512:raise RuntimeError('Sensor snapshot exceeds preview limit')
 result[name]=rows
c.close()
print(json.dumps(result,allow_nan=False))
'''


def preview_root() -> Path:
    if not os.environ.get('DB_PATH'):
        raise ValueError('Set an explicit preview DB_PATH')
    root = Path(os.environ['DB_PATH']).resolve().parent
    marker = root / 'preview-only.json'
    try:
        allowed = json.loads(marker.read_text()) == {'purpose': 'house-control-local-preview'}
    except (OSError, ValueError):
        allowed = False
    if not allowed:
        raise ValueError('Refusing to sync: destination is not an explicitly marked preview')
    return root


def import_snapshot(snapshot: dict) -> dict:
    """Atomically replace only the latest-sensor cache; never edit local placements."""
    preview_root()
    import energy
    if Path(energy.DB_PATH).resolve() != Path(os.environ['DB_PATH']).resolve():
        raise ValueError('Data layer is not using the preview database')
    if not isinstance(snapshot, dict) or set(snapshot) != set(TABLES):
        raise ValueError('Incomplete sensor snapshot; retaining previous observations')
    clean = {}
    for table, columns in TABLES.items():
        rows = snapshot[table]
        if not isinstance(rows, list) or len(rows) > 512:
            raise ValueError('Invalid sensor snapshot size')
        clean[table] = []
        for row in rows:
            if not isinstance(row, dict) or set(row) != set(columns):
                raise ValueError('Unexpected sensor snapshot schema')
            if any(value is not None and type(value) not in (str, int, float)
                   for value in row.values()):
                raise ValueError('Invalid sensor observation')
            if 'timestamp' in row:
                stamp = datetime.fromisoformat(row['timestamp'])
                if stamp.tzinfo is None:
                    raise ValueError('Sensor observation needs a timezone')
            clean[table].append(tuple(row[column] for column in columns))
    # Reject NaN/infinity before the transaction, including values supplied outside SSH.
    json.dumps(snapshot, allow_nan=False)
    conn = energy._connect()
    try:
        with conn:
            # These are preview caches, not the collector's historical tables.
            for table in ('climate_readings', 'climate_sensors', 'aqara_local_observations',
                          'aqara_local_labels', 'radon_readings', 'radon_cached_readings'):
                conn.execute(f'DELETE FROM {table}')
            for table, columns in TABLES.items():
                sql = f'INSERT INTO {table} ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})'
                conn.executemany(sql, clean[table])
                if table == 'radon_readings':
                    conn.executemany(sql.replace('radon_readings', 'radon_cached_readings'), clean[table])
    finally:
        conn.close()
    return {table: len(rows) for table, rows in clean.items()}


def refresh(host: str, container: str) -> dict:
    root = preview_root()
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.@-]{0,250}', host):
        raise ValueError('Invalid SSH destination')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,62}', container):
        raise ValueError('Invalid container name')
    status_path = root / 'sensor-sync-status.json'
    try:
        previous = json.loads(status_path.read_text())
    except (OSError, ValueError):
        previous = {}
    status = {'ok': False, 'last_success': previous.get('last_success'),
              'attempted_at': datetime.now(timezone.utc).isoformat()}
    try:
        command = ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                   '-o', 'ConnectTimeout=8', '-o', 'ServerAliveInterval=5',
                   '-o', 'ServerAliveCountMax=1', host,
                   shlex.join(['incus', 'exec', container, '--', 'python3', '-'])]
        result = subprocess.run(command, input=REMOTE_SCRIPT, capture_output=True, text=True,
                                timeout=20, check=True)
        if len(result.stdout) > 2 * 1024 * 1024:
            raise ValueError('Sensor response too large')
        status['counts'] = import_snapshot(json.loads(result.stdout))
        status['ok'] = True
        status['last_success'] = datetime.now(timezone.utc).isoformat()
    except Exception as exc:
        # Do not persist SSH stderr: it may contain installation-specific details.
        logging.warning('Preview sensor refresh failed: %s', type(exc).__name__)
        status['error'] = 'SER8 sensor refresh failed; last received observations retained'
    write_private_json(status_path, status)
    return status


def sync_status() -> dict:
    try:
        status = json.loads((preview_root() / 'sensor-sync-status.json').read_text())
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(status['last_success'])).total_seconds()
        return {**status, 'ok': bool(status.get('ok') and 0 <= age <= 90)}
    except (OSError, ValueError, TypeError, KeyError):
        return {'ok': False, 'last_success': None}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True)
    parser.add_argument('--container', default='energy-monitor')
    parser.add_argument('--interval', type=int, default=30)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    if args.interval < 15:
        parser.error('Refresh interval must be at least 15 seconds')
    preview_root()
    while True:
        status = refresh(args.host, args.container)
        logging.info('Preview sensor refresh: %s', 'ok' if status['ok'] else 'unavailable')
        if args.once:
            if not status['ok']:
                raise SystemExit(1)
            return
        time.sleep(args.interval)


if __name__ == '__main__':
    main()
