"""Bounded Emporia phase-chart collection shared by the poller and local preview."""
import math
from datetime import datetime, timedelta, timezone

import requests

CHANNELS = ('Main', 'Mains_A', 'Mains_B', 'Mains_C')


def utc(value: str) -> datetime:
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('Mains measurements require a timezone')
    return stamp.astimezone(timezone.utc)


def intervals(channel: str, data: dict, start: datetime, end: datetime) -> list[dict]:
    """Decode interval starts, preserving zero and omitting unknown measurements."""
    if channel not in CHANNELS:
        raise ValueError('Invalid mains channel')
    values = data['usageList']
    if not isinstance(values, list) or len(values) > 1440:
        raise ValueError('Invalid mains chart size')
    first = utc(data['firstUsageInstant'])
    if first.second or first.microsecond:
        raise ValueError('Unaligned mains interval')
    result = []
    for index, value in enumerate(values):
        stamp = first + timedelta(minutes=index)
        if not start <= stamp <= end:
            raise ValueError('Mains measurement outside requested window')
        if value is None:
            continue
        if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 1e6:
            raise ValueError('Invalid mains energy')
        result.append({'channel_name': channel, 'timestamp': stamp.isoformat(),
                       'usage_kwh': value})
    return result


def collect(vue, gid: str, start: datetime, end: datetime) -> dict:
    """Read at most 24 hours. The caller owns credentials and persistence."""
    if not str(gid).isdigit() or start.tzinfo is None or end.tzinfo is None:
        raise ValueError('Invalid mains panel/window')
    start, end = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
    if start.second or start.microsecond or end.second or end.microsecond or not (
            timedelta(0) <= end - start <= timedelta(minutes=1439)):
        raise ValueError('Mains window must contain 1..1440 aligned minutes')
    headers = {'Authorization': vue.auth.tokens['id_token']}

    def get(path, params=None):
        response = requests.get('https://api.emporiaenergy.com' + path,
                                headers=headers, params=params, timeout=(5, 10), allow_redirects=False)
        if 300 <= response.status_code < 400:
            raise ValueError("Emporia API redirect rejected")
        response.raise_for_status()
        return response.json()

    device = next(d for d in get('/v1/customers/devices')['devices']
                  if str(d['device_gid']) == str(gid))
    channels = {c['channel_id']: c for c in device['channels']}
    history, rows = [], []
    for channel in ('Mains', 'Mains_A', 'Mains_B', 'Mains_C'):
        if not channels.get(channel, {}).get('has_data'):
            continue
        name = 'Main' if channel == 'Mains' else channel
        cursor = start
        while cursor <= end:
            # Verified 720-point requests work; a single full-day request returns 400.
            chunk_end = min(end, cursor + timedelta(minutes=719))
            data = get('/v1/migrated/app-api/chart-usage', {
                'deviceGid': str(gid), 'channel': channel, 'start': cursor.isoformat(),
                'end': chunk_end.isoformat(), 'scale': '1MIN', 'energyUnit': 'KilowattHours',
            })
            history.extend(intervals(name, data, cursor, chunk_end))
            cursor = chunk_end + timedelta(minutes=1)
        # A null final bucket must not be presented as a fresh zero or old value.
        if data['usageList']:
            last = {'channel_name': name, 'usage_kwh': data['usageList'][-1],
                    'timestamp': (utc(data['firstUsageInstant']) +
                                  timedelta(minutes=len(data['usageList']) - 1)).isoformat()}
            rows.append(dict(last, age_seconds=(datetime.now(timezone.utc) - utc(last['timestamp'])).total_seconds()))
    return {'device_gid': str(gid), 'captured_at': datetime.now(timezone.utc).isoformat(),
            'rows': rows, 'intervals': history}
