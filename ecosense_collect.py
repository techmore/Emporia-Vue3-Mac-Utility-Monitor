"""Persistent EcoSense collection with private configuration and bounded retries.

API radon_level is Bq/m3 according to the community adapter's sensor.py.
Only last_radon_update_time is accepted as a measurement time; unavailable
values and unrecognized timestamps are never replaced with poll time.
"""

import json
import logging
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import sleep

code_root = os.environ.get('ENERGY_CODE_ROOT')
if code_root:
    sys.path.insert(0, code_root)

import energy  # noqa: E402 - standalone collector resolves its installation first
import radon  # noqa: E402
from ecosense import EcoSenseClient  # noqa: E402
from runtime_store import write_private_json  # noqa: E402

STATE_ROOT = Path(os.environ.get('ECOSENSE_STATE_ROOT', Path(energy.DB_PATH).parent))
CONFIG_PATH = STATE_ROOT / 'ecosense-private.json'
STATUS_PATH = STATE_ROOT / 'ecosense-status.json'
SNAPSHOT_PATH = STATE_ROOT / 'ecosense-device-snapshot.json'


def measurement_time(value, now: datetime, reference_time=None, reference_epoch=None) -> str:
    """Normalize explicit UTC/offset ISO dates or plausible Unix timestamps."""
    if isinstance(value, bool) or value is None:
        raise ValueError('Missing radon measurement time')
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            number = float(value)
        else:
            if stamp.tzinfo is None:
                # Real EcoQube payloads omit the UTC suffix. Accept that encoding
                # only when its same-payload update date is corroborated by an
                # explicit Unix timestamp, to subsecond precision. time_zone
                # is the app display preference and must not shift this value.
                if not isinstance(reference_time, str) or isinstance(reference_epoch, bool):
                    raise ValueError('Radon measurement time has no verified timezone')
                reference = datetime.fromisoformat(reference_time.replace('Z', '+00:00'))
                epoch = float(reference_epoch)
                if reference.tzinfo is not None or not math.isfinite(epoch):
                    raise ValueError('Unrecognized UTC corroboration')
                if abs(reference.replace(tzinfo=timezone.utc).timestamp() - epoch) >= 1:
                    raise ValueError('Source date does not match explicit UTC timestamp')
                stamp = stamp.replace(tzinfo=timezone.utc)
            stamp = stamp.astimezone(timezone.utc)
            number = None
    else:
        raise ValueError('Unrecognized radon measurement time')
    if number is not None:
        if not math.isfinite(number):
            raise ValueError('Invalid radon measurement time')
        if number > 100_000_000_000:
            number /= 1000
        stamp = datetime.fromtimestamp(number, timezone.utc)
    if not now - timedelta(days=energy.DB_RETENTION_DAYS) <= stamp <= now + timedelta(minutes=5):
        raise ValueError('Radon measurement is stale or in the future')
    return stamp.isoformat()


def observations(devices: list[dict], now: datetime) -> tuple[list[dict], int]:
    result, skipped = [], 0
    for device in devices:
        try:
            serial = device.get('serial_number')
            value = device.get('radon_level')
            if not isinstance(serial, str) or not serial or isinstance(value, bool):
                raise ValueError('Missing identity or value')
            value = float(value)
            # The community adapter treats zero as unavailable/initializing.
            if not math.isfinite(value) or value <= 0:
                raise ValueError('Unavailable reading')
            stamp = measurement_time(device.get('last_radon_update_time'), now,
                                     device.get('last_update_time'), device.get('last_update_ts'))
            result.append({
                'source': 'ecosense', 'sensor_id': serial,
                'name': str(device.get('device_name') or 'EcoQube')[:200],
                'timestamp': stamp, 'value': value, 'unit': 'Bq/m3',
            })
        except (ValueError, TypeError, OverflowError, OSError):
            skipped += 1
    return result, skipped


def status(state: str, **details) -> None:
    write_private_json(STATUS_PATH, {
        'state': state, 'checked_at': datetime.now(timezone.utc).isoformat(), **details,
    })


def connect_account(email: str, password: str) -> dict:
    """Verify once, save privately, and import the first available measurements."""
    if not isinstance(email, str) or not isinstance(password, str):
        raise ValueError('Supply the EcoSense email and password')
    email = email.strip()
    if not email or not password or len(email) > 320 or len(password) > 1024:
        raise ValueError('Supply the EcoSense email and password')
    client = EcoSenseClient(email, password)
    devices = client.get_devices()
    write_private_json(CONFIG_PATH, {'email': email, 'password': password})
    fields = ('serial_number', 'device_name', 'd_type', 'activated', 'deactivated',
              'd_status', 'radon_level', 'unit', 'last_radon_update_time',
              'last_update_time', 'last_update_ts', 'time_zone')
    write_private_json(SNAPSHOT_PATH, {
        'retrieved_at': datetime.now(timezone.utc).isoformat(),
        'devices': [{key: item.get(key) for key in fields} for item in devices],
    })
    rows, skipped = observations(devices, datetime.now(timezone.utc))
    counts = radon.ingest_observations(rows) if rows else {'inserted': 0, 'duplicates': 0}
    state = 'collecting' if rows else 'no_valid_measurements'
    status(state, devices=len(devices), skipped=skipped, **counts)
    return {'ok': True, 'state': state, 'devices': len(devices), **counts}


def run() -> None:
    client, identity = None, None
    failures = 0
    while True:
        delay = 60
        try:
            if not CONFIG_PATH.exists():
                status('credentials_missing', message='EcoSense credentials have not been saved on SER8.')
                sleep(delay)
                continue
            config = json.loads(CONFIG_PATH.read_text())
            credentials = (config.get('email'), config.get('password'))
            if not all(isinstance(item, str) and item for item in credentials):
                raise ValueError('Incomplete EcoSense credentials')
            if credentials != identity:
                client = EcoSenseClient(*credentials)
                identity = credentials
            devices = client.get_devices()
            fields = ('serial_number', 'device_name', 'd_type', 'activated', 'deactivated',
                      'd_status', 'radon_level', 'unit', 'last_radon_update_time',
                      'last_update_time', 'last_update_ts', 'time_zone')
            write_private_json(SNAPSHOT_PATH, {
                'retrieved_at': datetime.now(timezone.utc).isoformat(),
                'devices': [{key: item.get(key) for key in fields} for item in devices],
            })
            rows, skipped = observations(devices, datetime.now(timezone.utc))
            counts = radon.ingest_observations(rows) if rows else {'inserted': 0, 'duplicates': 0}
            status('collecting' if rows else 'no_valid_measurements',
                   devices=len(devices), skipped=skipped, **counts)
            logging.info('EcoSense: devices=%d inserted=%d duplicates=%d skipped=%d',
                         len(devices), counts['inserted'], counts['duplicates'], skipped)
            failures = 0
        except Exception as exc:
            failures += 1
            error_code = getattr(exc, 'response', {}).get('Error', {}).get('Code', '')
            rejected = error_code in ('NotAuthorizedException', 'UserNotFoundException')
            state = 'authentication_rejected' if rejected else 'connection_error'
            status(state, error_class=type(exc).__name__)
            # Never log secret-bearing exception strings, tokens or account URLs.
            logging.warning('EcoSense: %s (%s)', state, type(exc).__name__)
            if rejected:
                client, identity = None, None
                delay = 900
            else:
                delay = min(60 * 2 ** min(failures, 4), 900)
        sleep(delay)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    run()
