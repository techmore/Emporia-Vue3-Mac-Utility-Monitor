"""Opt-in, read-only cloud mains supplement for the isolated local preview."""
import json
import math
import re
import shlex
import subprocess
from datetime import datetime, timezone

from runtime_store import write_private_json

# Executed through the existing collector virtualenv; never stored on SER8.
# Tokens stay in remote process memory. No token-storage callback/file is set.
REMOTE_SCRIPT = r'''
import json,requests
from datetime import datetime,timedelta,timezone
from pathlib import Path
from pyemvue import PyEmVue
keys=json.loads(Path('/var/lib/energy-monitor/collector/keys.json').read_text())
vue=PyEmVue()
vue.login(id_token=keys['id_token'],access_token=keys['access_token'],
 refresh_token=keys['refresh_token'],token_storage_file=None)
headers={'Authorization':vue.auth.tokens['id_token']}
base='https://api.emporiaenergy.com'
def get(path,params=None):
 r=requests.get(base+path,headers=headers,params=params,timeout=(5,10))
 r.raise_for_status()
 return r.json()
device=next(d for d in get('/v1/customers/devices')['devices'] if str(d['device_gid'])==GID)
channels={c['channel_id']:c for c in device['channels']}
end=datetime.now(timezone.utc).replace(second=0,microsecond=0)-timedelta(minutes=1)
rows=[]
for channel in ('Mains','Mains_A','Mains_B','Mains_C'):
 if not channels.get(channel,{}).get('has_data'):continue
 data=get('/v1/migrated/app-api/chart-usage',{
  'deviceGid':GID,'channel':channel,'start':(end-timedelta(minutes=2)).isoformat(),
  'end':end.isoformat(),'scale':'1MIN','energyUnit':'KilowattHours'})
 values=data['usageList']
 if not isinstance(values,list) or len(values)>4:raise ValueError('Invalid chart window')
 if not values:continue
 stamp=datetime.fromisoformat(data['firstUsageInstant'].replace('Z','+00:00'))+timedelta(minutes=len(values)-1)
 rows.append({'channel_name':'Main' if channel=='Mains' else channel,
  'usage_kwh':values[-1],'timestamp':stamp.isoformat(),
  'age_seconds':(datetime.now(timezone.utc)-stamp).total_seconds()})
print(json.dumps({'device_gid':GID,'captured_at':datetime.now(timezone.utc).isoformat(),
 'rows':rows},allow_nan=False))
'''


def validate(snapshot: dict, gid: str) -> dict:
    if not isinstance(snapshot, dict) or set(snapshot) != {'device_gid', 'captured_at', 'rows'}:
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
        if previous.get('device_gid') == gid and 0 <= age < 60:
            return
    except (OSError, ValueError, TypeError, KeyError):
        previous = {}
    cache = {'device_gid': gid, 'attempted_at': now.isoformat(), 'ok': False}
    try:
        # Replace only the interpreter in the already validated incus command.
        remote = shlex.split(command[-1])
        remote[-2] = remote_python
        result = subprocess.run(command[:-1] + [shlex.join(remote)],
                                input='GID=' + repr(gid) + '\n' + REMOTE_SCRIPT,
                                capture_output=True, text=True, timeout=70, check=True)
        if len(result.stdout) > 65536:
            raise ValueError('Mains response too large')
        cache.update(validate(json.loads(result.stdout), gid), ok=True)
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
