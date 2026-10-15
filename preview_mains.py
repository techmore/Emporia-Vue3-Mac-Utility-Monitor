"""Opt-in, read-only cloud mains supplement for the isolated local preview."""
import json
import math
import re
import shlex
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from runtime_store import write_private_json

# Executed through the existing collector virtualenv; never stored on SER8.
# Tokens stay in remote process memory. No token-storage callback/file is set.
REMOTE_SCRIPT = Path(__file__).with_name('emporia_mains.py').read_text() + r'''
import json
from pathlib import Path
from pyemvue import PyEmVue
keys=json.loads(Path('/var/lib/energy-monitor/collector/keys.json').read_text())
vue=PyEmVue()
vue.login(id_token=keys['id_token'],access_token=keys['access_token'],
 refresh_token=keys['refresh_token'],token_storage_file=None)
end=utc(END) if 'END' in globals() else datetime.now(timezone.utc).replace(second=0,microsecond=0)-timedelta(minutes=1)
start=utc(START) if 'START' in globals() else end-timedelta(minutes=2)
snapshot=collect(vue,GID,start,end)
settings=json.loads(Path('/var/lib/energy-monitor/collector/settings.json').read_text())
snapshot['rate_cents']=float(settings.get('rate_cents',11.04))
print(json.dumps(snapshot,allow_nan=False))
'''


def validate(snapshot: dict, gid: str) -> dict:
    if not isinstance(snapshot, dict) or set(snapshot) not in ({'device_gid', 'captured_at', 'rows'},
            {'device_gid', 'captured_at', 'rows', 'intervals', 'rate_cents'}):
        raise ValueError('Invalid mains response')
    if snapshot['device_gid'] != gid or datetime.fromisoformat(snapshot['captured_at']).tzinfo is None:
        raise ValueError('Invalid mains panel/time')
    if not isinstance(snapshot['rows'], list) or len(snapshot['rows']) > 4:
        raise ValueError('Invalid mains rows')
    seen = set()
    for row in snapshot['rows']:
        if not isinstance(row, dict) or set(row) != {'channel_name', 'usage_kwh', 'timestamp', 'age_seconds'}:
            raise ValueError('Invalid mains observation')
        name = row['channel_name']
        if name not in ('Main', 'Mains_A', 'Mains_B', 'Mains_C') or name in seen:
            raise ValueError('Invalid mains channel')
        seen.add(name)
        if datetime.fromisoformat(row['timestamp']).tzinfo is None:
            raise ValueError('Mains measurement requires a timezone')
        for key in ('usage_kwh', 'age_seconds'):
            value = row[key]
            if value is None and key == 'usage_kwh':
                continue
            if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 1e12:
                raise ValueError('Invalid mains value')
    return snapshot


def refresh(root, command: list[str], remote_python: str, gid: str) -> None:
    """At most one bounded phase fetch per minute; failures cannot stop DB refresh."""
    if not re.fullmatch(r'/[A-Za-z0-9_./-]+', remote_python) or not gid.isdigit():
        raise ValueError('Invalid remote interpreter/panel')
    path = root / 'mains-cache.json'
    now = datetime.now(timezone.utc)
    try:
        previous = json.loads(path.read_text())
        age = (now - datetime.fromisoformat(previous['attempted_at'])).total_seconds()
        if (previous.get('device_gid') == gid and 0 <= age < 60
                and (previous.get('history_logged') or not previous.get('ok'))):
            return
    except (OSError, ValueError, TypeError, KeyError):
        previous = {}
    cache = {'device_gid': gid, 'attempted_at': now.isoformat(), 'ok': False}
    try:
        # Replace only the interpreter in the already validated incus command.
        remote = shlex.split(command[-1])
        remote[-2] = remote_python
        import energy
        end = now.replace(second=0, microsecond=0) - timedelta(minutes=1)
        start = energy.mains_history_start(gid, end)
        result = subprocess.run(command[:-1] + [shlex.join(remote)],
                                input='GID=' + repr(gid) + '\nSTART=' + repr(start.isoformat()) +
                                '\nEND=' + repr(end.isoformat()) + '\n' + REMOTE_SCRIPT,
                                capture_output=True, text=True, timeout=70, check=True)
        if len(result.stdout) > 2 * 1024 * 1024:
            raise ValueError('Mains response too large')
        snapshot = validate(json.loads(result.stdout), gid)
        if 'intervals' not in snapshot:
            raise ValueError('Mains response is missing history')
        energy.store_mains_intervals(gid, snapshot['intervals'], snapshot['rate_cents'])
        cache.update({key: snapshot[key] for key in ('device_gid', 'captured_at', 'rows')},
                     ok=True, history_logged=True)
    except Exception:
        # Never log stderr/token-bearing exceptions from the authentication client.
        cache['error'] = 'Cloud mains unavailable; circuit and sensor feed remains independent'
    write_private_json(path, cache)


def readings(root, gid: str, now: datetime) -> list[dict]:
    try:
        cache = json.loads((root / 'mains-cache.json').read_text())
        if not cache.get('ok'):
            return []
        snapshot = validate({key: cache[key] for key in ('device_gid', 'captured_at', 'rows')}, gid)
        age = (now - datetime.fromisoformat(snapshot['captured_at'])).total_seconds()
        if not 0 <= age < 180:
            return []
        return [dict(row, age_seconds=row['age_seconds'] + age) for row in snapshot['rows']]
    except (OSError, ValueError, TypeError, KeyError):
        return []
