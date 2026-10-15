"""Read-only SER8 telemetry for an explicitly marked local Home 3D preview.

No remote files, services, credentials or floor plans are changed. The destination
must be a preview database; observations retain their original measurement times.
"""
import argparse
import json
import logging
import math
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
from datetime import datetime,timedelta,timezone
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
root=Path('/var/lib/energy-monitor/collector')
settings=json.loads((root/'settings.json').read_text()) if (root/'settings.json').exists() else {}
gid=settings.get('primary_device_gid')
if not gid or gid=='81134' or not c.execute('SELECT 1 FROM readings WHERE device_gid=? LIMIT 1',(gid,)).fetchone():
 row=c.execute("SELECT device_gid FROM readings WHERE device_gid!='81134' GROUP BY device_gid ORDER BY MAX(timestamp) DESC LIMIT 1").fetchone()
 gid=row['device_gid'] if row else None
latest=[dict(row) for row in c.execute('SELECT * FROM latest_channel_snapshot WHERE device_gid=?',(gid,))]
now=datetime.now()
for row in latest:
 try:
  stamp=datetime.fromisoformat(row['timestamp'])
  reference=datetime.now(timezone.utc) if stamp.tzinfo else now
  row['age_seconds']=(reference-stamp).total_seconds()
 except (ValueError,TypeError):row['age_seconds']=None
summary=[dict(row) for row in c.execute("""SELECT channel_name,SUM(usage_kwh) AS total_kwh,
 SUM(cost_cents) AS total_cents,COUNT(*) AS readings FROM readings
 WHERE device_gid=? AND timestamp>=? GROUP BY channel_name""",
 (gid,(now-timedelta(hours=24)).isoformat()))]
try:
 heartbeat=json.loads((root/'poller_status.json').read_text())
 stamp=datetime.fromisoformat(heartbeat['timestamp'])
 age=((datetime.now(timezone.utc) if stamp.tzinfo else now)-stamp).total_seconds()
 poller_ok=heartbeat.get('ok') is True and -60<=age<180
except (OSError,ValueError,TypeError,KeyError):poller_ok=False
cap=c.execute('SELECT * FROM device_capabilities WHERE device_gid=?',(gid,)).fetchone()
result['power']={'active_device_gid':gid,'captured_at':datetime.now(timezone.utc).isoformat(),
 'poller_ok':poller_ok,'latest':latest,'summary':summary,
 'mains_c_no_ct':bool(cap['mains_c_no_ct']) if cap else False}
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
    """Replace observation caches and publish validated power; never edit placements."""
    preview_root()
    import energy
    if Path(energy.DB_PATH).resolve() != Path(os.environ['DB_PATH']).resolve():
        raise ValueError('Data layer is not using the preview database')
    if not isinstance(snapshot, dict) or set(snapshot) != set(TABLES) | {'power'}:
        raise ValueError('Incomplete sensor snapshot; retaining previous observations')
    power = validate_power(snapshot['power'])
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
    write_private_json(preview_root() / 'power-cache.json', power)
    return {table: len(rows) for table, rows in clean.items()}


def validate_power(power: dict) -> dict:
    """Bound and validate the read-only panel snapshot before any cache writes."""
    if not isinstance(power, dict) or set(power) != {
            'active_device_gid', 'captured_at', 'poller_ok', 'latest', 'summary', 'mains_c_no_ct'}:
        raise ValueError('Incomplete power snapshot')
    if power['active_device_gid'] is not None and (
            not isinstance(power['active_device_gid'], str) or not power['active_device_gid']
            or power['active_device_gid'] == '81134'):
        raise ValueError('Invalid active panel')
    if type(power['poller_ok']) is not bool or type(power['mains_c_no_ct']) is not bool:
        raise ValueError('Invalid source status')
    if datetime.fromisoformat(power['captured_at']).tzinfo is None:
        raise ValueError('Power capture time requires a timezone')
    for name in ('latest', 'summary'):
        rows = power[name]
        if not isinstance(rows, list) or len(rows) > 512:
            raise ValueError('Power snapshot exceeds preview limit')
        seen = set()
        fields = ({'device_gid', 'channel_name', 'channel_num', 'usage_kwh', 'cost_cents',
                   'timestamp', 'age_seconds'} if name == 'latest' else
                  {'channel_name', 'total_kwh', 'total_cents', 'readings'})
        for row in rows:
            if not isinstance(row, dict) or set(row) != fields:
                raise ValueError('Invalid power observation schema')
            channel = row['channel_name']
            if not isinstance(channel, str) or not 1 <= len(channel) <= 256 or channel in seen:
                raise ValueError('Invalid or duplicate power channel')
            seen.add(channel)
            if name == 'latest':
                if row['device_gid'] != power['active_device_gid']:
                    raise ValueError('Cross-panel power data')
                datetime.fromisoformat(row['timestamp'])
            for key in ('usage_kwh', 'cost_cents', 'age_seconds') if name == 'latest' else ('total_kwh', 'total_cents', 'readings'):
                if row[key] is not None and (type(row[key]) not in (int, float)
                                             or not math.isfinite(row[key]) or abs(row[key]) > 1e12):
                    raise ValueError('Invalid power value')
    return power


def power_snapshot(now: datetime | None = None) -> dict:
    try:
        power = validate_power(json.loads((preview_root() / 'power-cache.json').read_text()))
    except (OSError, ValueError, TypeError, KeyError):
        power = {'active_device_gid': None, 'captured_at': None, 'poller_ok': False,
                 'latest': [], 'summary': [], 'mains_c_no_ct': False}
    now = now or datetime.now(timezone.utc)
    age = (now - datetime.fromisoformat(power['captured_at'])).total_seconds() if power['captured_at'] else float('inf')
    connected = sync_status()['ok'] and 0 <= age <= 90
    latest = {row['channel_name']: row for row in power['latest']}
    from preview_mains import readings
    cloud = readings(preview_root(), power['active_device_gid'], now)
    cloud_names = {row['channel_name'] for row in cloud}
    for row in cloud:
        latest[row['channel_name']] = dict(row, age_seconds=row['age_seconds'] - age)

    def fresh(row):
        return bool(connected and row and row['age_seconds'] is not None
                    and -60 <= row['age_seconds'] + age < 180)

    online = bool(power['poller_ok'] and fresh(latest.get('Main')))

    def watts(row):
        return row['usage_kwh'] * 60000 if online and fresh(row) and row['usage_kwh'] is not None else None

    meta = {'Main', 'Mains_A', 'Mains_B', 'Mains_C', 'Balance'}
    menu = {'active_device_gid': power['active_device_gid'], 'online': online,
            'current_watts': watts(latest['Main']) if 'Main' in latest else None,
            'last_reading': latest.get('Main', {}).get('timestamp'),
            'breaker_slots': [{'channel_name': name, 'display_name': name, 'watts': watts(row)}
                              for name, row in latest.items() if name not in meta]}
    totals = {row['channel_name']: row for row in power['summary']}
    mains = []
    for name, label in (('Main', 'Total'), ('Mains_A', 'Leg A'), ('Mains_B', 'Leg B'), ('Mains_C', 'Leg C')):
        if name not in latest or (name == 'Mains_C' and power['mains_c_no_ct'] and name not in cloud_names):
            continue
        total = totals.get(name, {})
        mains.append({'channel_name': name, 'name': label, 'watts': watts(latest[name]),
                      'timestamp': latest[name]['timestamp'], 'kwh_24h': total.get('total_kwh'),
                      'cost_24h': total['total_cents'] / 100 if total.get('total_cents') is not None else None,
                      'source': 'Emporia phase chart (1-minute average)' if name in cloud_names else 'SER8 recorded data'})
        if name in cloud_names and name != 'Main':
            # The collector has no rolling phase history; do not attach old CSV totals.
            mains[-1].update(kwh_24h=None, cost_24h=None)
    return {'menu': menu, 'summary': [row for row in power['summary'] if row['channel_name'] not in meta],
            'mains': mains, 'captured_at': power['captured_at']}


def power_catalog() -> dict[str, set[str]]:
    panel = power_snapshot()['menu']
    return {panel['active_device_gid']: {row['channel_name'] for row in panel['breaker_slots']}}


def refresh(host: str, container: str, host_key_alias: str | None = None,
            remote_python: str | None = None) -> dict:
    root = preview_root()
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.@-]{0,250}', host):
        raise ValueError('Invalid SSH destination')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,62}', container):
        raise ValueError('Invalid container name')
    if host_key_alias and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,250}', host_key_alias):
        raise ValueError('Invalid host-key alias')
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
                   '-o', 'ServerAliveCountMax=1']
        if host_key_alias:
            command += ['-o', f'HostKeyAlias={host_key_alias}']
        command += [host,
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
        status['error'] = 'SER8 refresh failed; last received observations retained'
    write_private_json(status_path, status)
    if status['ok'] and remote_python:
        from preview_mains import refresh as refresh_mains
        power = json.loads((root / 'power-cache.json').read_text())
        refresh_mains(root, command, remote_python, power['active_device_gid'])
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
    parser.add_argument('--host-key-alias', help='Reuse a verified SSH host key for an alternate address')
    parser.add_argument('--remote-python', help='Opt in to cloud phase reads using an existing remote virtualenv')
    parser.add_argument('--interval', type=int, default=30)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    if args.interval < 15:
        parser.error('Refresh interval must be at least 15 seconds')
    preview_root()
    while True:
        status = refresh(args.host, args.container, args.host_key_alias, args.remote_python)
        logging.info('Preview sensor refresh: %s', 'ok' if status['ok'] else 'unavailable')
        if args.once:
            if not status['ok']:
                raise SystemExit(1)
            return
        time.sleep(args.interval)


if __name__ == '__main__':
    main()
