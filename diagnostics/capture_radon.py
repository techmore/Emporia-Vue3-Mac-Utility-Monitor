"""Interactive EcoSense login and private diagnostic capture (no password saved)."""

import getpass
import json
import os
import shlex
import sys
import subprocess
import warnings
from datetime import datetime, timezone
from pathlib import Path
from time import sleep


def main() -> None:
    source = Path('/opt/homebrew/opt/energy-monitor/libexec')
    sys.path.insert(0, str(source))
    from ecosense import EcoSenseClient, describe_devices

    directory = Path.home() / 'Library/Logs/EnergyMonitor'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    log = directory / ('radon-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.jsonl')
    print('EcoSense / EcoQube diagnostic login')
    print('Use the account you use in the EcoSense phone app.')
    print('Your password is hidden. After verification, it is saved in an owner-only')
    print('file on SER8 so your dashboard can collect automatically.')
    email = input('EcoSense email [sdolbec1@gmail.com]: ').strip() or 'sdolbec1@gmail.com'
    while True:
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            password = getpass.getpass('EcoSense password: ')
        client = EcoSenseClient(email, password)
        try:
            devices = client.get_devices()
            break
        except Exception as exc:
            code = getattr(exc, 'response', {}).get('Error', {}).get('Code', '')
            if code == 'NotAuthorizedException':
                print('EcoSense rejected the email/password. Please try again.')
                replacement = input(f'Email [{email}]: ').strip()
                if replacement:
                    email = replacement
            else:
                print(f'Connection failed ({type(exc).__name__}). Press Enter to retry.')
                input()
    payload = {'email': email, 'password': password, 'devices': [
        {key: device.get(key) for key in (
            'serial_number', 'device_name', 'd_type', 'activated', 'deactivated',
            'd_status', 'radon_level', 'unit', 'last_radon_update_time',
            'last_update_time', 'last_update_ts', 'time_zone',
        )} for device in devices
    ]}
    remote = """import json, os, pathlib, sys
p = pathlib.Path.home()/'.local/share/energy-monitor/ecosense-private.json'
p.parent.mkdir(parents=True, exist_ok=True)
data = json.load(sys.stdin)
fd = os.open(p, os.O_WRONLY|os.O_CREAT|os.O_TRUNC, 0o600)
with os.fdopen(fd, 'w') as f: json.dump(data, f)
os.chmod(p, 0o600)
"""
    result = subprocess.run([
        'ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
        'sdolbec@100.127.99.74', 'python3 -c ' + shlex.quote(remote),
    ], input=json.dumps(payload), text=True, capture_output=True, timeout=30)
    if result.returncode:
        raise RuntimeError('Could not save the collector login on SER8')
    print('Login verified and saved privately on SER8 for automatic collection.', flush=True)
    del payload
    del password
    descriptor = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    print(f'Collecting 10 snapshots, once per minute. Log: {log}', flush=True)
    with os.fdopen(descriptor, 'w') as output:
        for index in range(10):
            record = {'retrieved_at': datetime.now(timezone.utc).isoformat()}
            try:
                devices = client.get_devices()
                record['devices'] = describe_devices(devices)
                record['status'] = 'ok'
                print(f'Snapshot {index + 1}: connected; {len(devices)} device(s).', flush=True)
            except Exception as exc:
                record['status'] = 'error'
                record['error_class'] = type(exc).__name__
                code = getattr(exc, 'code', None)
                if isinstance(code, int):
                    record['http_status'] = code
                print(f'Snapshot {index + 1}: {type(exc).__name__}. Details logged safely.', flush=True)
            output.write(json.dumps(record) + '\n')
            output.flush()
            if record['status'] == 'error' and index == 0:
                break
            if index < 9:
                sleep(60)
    print(f'Capture finished. Log: {log}', flush=True)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nCapture stopped; completed snapshots remain in the log.')
