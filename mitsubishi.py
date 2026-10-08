"""Optional read-only Comfort v3 adapter. No HVAC write or reboot operations.

Protocol reference: https://github.com/dlarrick/pykumo/blob/master/Cloud_api_v3.md
Cloud snapshots are retrieval observations, not sensor measurement timestamps.
"""
import fcntl
import json
import math
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import requests

import energy
from runtime_store import write_private_json

BASE = 'https://app-prod.kumocloud.com'
APP_VERSION = '3.2.4'


class ComfortError(Exception):
    """Safe error identifiers only; never retain response bodies or URLs."""


def _path(name: str) -> Path:
    return Path(energy.DB_PATH).resolve().parent / ('mitsubishi-' + name)


@contextmanager
def _lock():
    # Cross-process ownership also prevents removal racing a collector write.
    import os
    fd = os.open(_path('module.lock'), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ComfortError('busy') from None
        yield
    finally:
        os.close(fd)


def _request(method: str, path: str, token: str | None = None, body=None):
    headers = {'Accept': 'application/json', 'x-app-version': APP_VERSION}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    try:
        # Ignore proxy environment for these fixed endpoints; forbid redirects.
        with requests.Session() as session:
            session.trust_env = False
            with session.request(method, BASE + path, headers=headers, json=body,
                                 timeout=(3, 5), allow_redirects=False, stream=True) as response:
                if response.status_code in (401, 403):
                    raise ComfortError('authentication_rejected')
                if response.status_code != 200:
                    raise ComfortError('cloud_unavailable')
                chunks, size = [], 0
                for chunk in response.iter_content(8192):
                    size += len(chunk)
                    if size > 262144:
                        raise ComfortError('invalid_response')
                    chunks.append(chunk)
                return json.loads(b''.join(chunks))
    except ComfortError:
        raise
    except Exception:
        raise ComfortError('cloud_unavailable') from None


def _tokens(value) -> dict:
    if not isinstance(value, dict) or any(
        not isinstance(value.get(key), str) or not 1 <= len(value[key]) <= 16384
        for key in ('access', 'refresh')
    ):
        raise ComfortError('invalid_response')
    return {'access': value['access'], 'refresh': value['refresh']}


def _number(value, low=-80, high=80):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and low <= value <= high else None


def _read_units(config: dict) -> list[dict]:
    def get(path):
        try:
            return _request('GET', path, config['access'])
        except ComfortError as exc:
            if str(exc) != 'authentication_rejected':
                raise
            refreshed = _tokens(_request('POST', '/v3/refresh', config['refresh'],
                                         {'refresh': config['refresh']}))
            config.update(refreshed)
            return _request('GET', path, config['access'])

    sites = get('/v3/sites/')
    if not isinstance(sites, list) or len(sites) > 4:
        raise ComfortError('invalid_response')
    units = []
    for site in sites:
        if (not isinstance(site, dict) or not isinstance(site.get('id'), str)
                or not 1 <= len(site['id']) <= 200):
            raise ComfortError('invalid_response')
        zones = get('/v3/sites/' + quote(site['id'], safe='') + '/zones')
        if not isinstance(zones, list) or len(zones) > 32:
            raise ComfortError('invalid_response')
        for zone in zones:
            if not isinstance(zone, dict) or not isinstance(zone.get('adapter'), dict):
                raise ComfortError('invalid_response')
            adapter = zone['adapter']
            serial = adapter.get('deviceSerial')
            if not isinstance(serial, str) or not 1 <= len(serial) <= 200:
                raise ComfortError('invalid_response')
            connected = adapter.get('connected') is True
            mode = adapter.get('operationMode')
            power = adapter.get('power')
            units.append({
                'serial': serial, 'name': str(zone.get('name') or 'Mitsubishi unit')[:200],
                'connected': connected,
                'is_on': bool(power) if connected and type(power) is int and power in (0, 1) else None,
                'temperature_c': _number(adapter.get('roomTemp')) if connected else None,
                'heat_setpoint_c': _number(adapter.get('spHeat')) if connected else None,
                'cool_setpoint_c': _number(adapter.get('spCool')) if connected else None,
                'humidity_pct': _number(adapter.get('humidity'), 0, 100) if connected else None,
                'mode': mode[:50] if connected and isinstance(mode, str) else None,
            })
    if len({unit['serial'] for unit in units}) != len(units):
        raise ComfortError('invalid_response')
    return units


def _save_snapshot(units: list[dict]) -> dict:
    stamp = datetime.now(timezone.utc).isoformat()
    conn = energy._connect()
    try:
        with conn:
            for unit in units:
                conn.execute('INSERT INTO mitsubishi_observations '
                             '(serial,queried_at,snapshot) VALUES(?,?,?)',
                             (unit['serial'], stamp, json.dumps(unit)))
            cutoff = (datetime.now(timezone.utc) - timedelta(days=energy.DB_RETENTION_DAYS)).isoformat()
            conn.execute('DELETE FROM mitsubishi_observations WHERE queried_at<?', (cutoff,))
    finally:
        conn.close()
    snapshot = {'state': 'connected' if units else 'no_devices', 'queried_at': stamp,
                'units': units, 'transport': 'comfort_cloud', 'enabled': True}
    write_private_json(_path('status.json'), snapshot)
    return snapshot


def connect(email: str, password: str) -> dict:
    if (not isinstance(email, str) or not isinstance(password, str)
            or not 1 <= len(email.strip()) <= 320 or not 1 <= len(password) <= 1024):
        raise ComfortError('invalid_credentials')
    with _lock():
        response = _request('POST', '/v3/login', body={
            'username': email.strip(), 'password': password, 'appVersion': APP_VERSION})
        if not isinstance(response, dict):
            raise ComfortError('invalid_response')
        config = _tokens(response.get('token'))
        units = _read_units(config)
        # Replace the saved connection only after successful discovery.
        write_private_json(_path('private.json'), config)
        return _save_snapshot(units)


def poll() -> dict:
    with _lock():
        if not _path('private.json').exists():
            return {'state': 'disabled', 'enabled': False, 'units': []}
        try:
            config = _tokens(json.loads(_path('private.json').read_text()))
            try:
                units = _read_units(config)
            finally:
                # Persist rotated refresh tokens even if a later request fails.
                write_private_json(_path('private.json'), config)
            return _save_snapshot(units)
        except Exception as exc:
            error = str(exc) if isinstance(exc, ComfortError) else 'collector_error'
            snapshot = {'state': error, 'enabled': True, 'units': [],
                        'queried_at': datetime.now(timezone.utc).isoformat()}
            write_private_json(_path('status.json'), snapshot)
            return snapshot


def remove() -> dict:
    with _lock():
        _path('private.json').unlink(missing_ok=True)
        _path('status.json').unlink(missing_ok=True)
        return {'state': 'disabled', 'enabled': False, 'units': []}


def status() -> dict:
    if not _path('private.json').exists():
        return {'state': 'disabled', 'enabled': False, 'units': []}
    try:
        snapshot = json.loads(_path('status.json').read_text())
        stamp = datetime.fromisoformat(snapshot['queried_at'])
        age = (datetime.now(timezone.utc) - stamp).total_seconds()
        if not 0 <= age <= 180:
            return {'state': 'stale', 'enabled': True, 'units': [],
                    'queried_at': snapshot['queried_at']}
        return snapshot
    except (OSError, ValueError, KeyError, TypeError):
        return {'state': 'status_unavailable', 'enabled': True, 'units': []}


def history() -> list[dict]:
    """Last 100 retrieval observations, retained after module removal."""
    conn = energy._connect()
    try:
        return [{'queried_at': row['queried_at'], **json.loads(row['snapshot'])}
                for row in conn.execute('SELECT queried_at,snapshot FROM mitsubishi_observations '
                                        'ORDER BY queried_at DESC LIMIT 100').fetchall()]
    finally:
        conn.close()
