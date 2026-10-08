"""Experimental read-only EcoSense cloud probe based on community API research.

This is not a documented vendor API. It does not ingest readings because source
measurement timestamp semantics have not been verified against a real device.
"""
import argparse
import getpass
import json
import math
import os
import warnings
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from botocore import UNSIGNED
from botocore.config import Config
from pycognito import Cognito

API_URL = 'https://api.cloud.ecosense.io/api/v1/device'
MAX_RESPONSE_BYTES = 1024 * 1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class EcoSenseClient:
    """Keep credentials in memory; retry expired authorization only once."""

    def __init__(self, username: str, password: str):
        if not username or not password:
            raise ValueError('Set ECOSENSE_EMAIL and ECOSENSE_PASSWORD privately')
        self.username = username
        self._password = password
        self._cognito = Cognito(
            'us-west-2_vB73oNa7f', '1dk9ul54cdo42lt6e9u1oa9g1d',
            user_pool_region='us-west-2', username=username,
            boto3_client_kwargs={'config': Config(
                signature_version=UNSIGNED, connect_timeout=10, read_timeout=15,
                retries={'max_attempts': 0})},
        )
        self._authenticated = False

    def _authenticate(self) -> None:
        self._cognito.authenticate(password=self._password)
        self._authenticated = True

    def _request_devices(self) -> list[dict]:
        token = self._cognito.id_token
        if not isinstance(token, str) or not token:
            raise ValueError('EcoSense authorization did not return an ID token')
        request = Request(API_URL + '?' + urlencode({'email': self.username}), headers={
            'Authorization': f'Bearer {token}', 'Accept': 'application/json'})
        with build_opener(NoRedirect).open(request, timeout=15) as response:
            if response.status != 200:
                raise ValueError('Unexpected EcoSense response status')
            body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise ValueError('EcoSense response exceeds size limit')
        devices = json.loads(body)
        if not isinstance(devices, list) or not all(isinstance(item, dict) for item in devices):
            raise ValueError('Unexpected EcoSense device response')
        return devices

    def get_devices(self) -> list[dict]:
        if not self._authenticated:
            self._authenticate()
        try:
            return self._request_devices()
        except HTTPError as exc:
            if exc.code != 401:
                raise
            exc.close()
            self._authenticate()
            return self._request_devices()


def describe_devices(devices: list[dict]) -> list[dict]:
    """Expose field names and candidate values, not an invented live measurement."""
    result = []
    for device in devices:
        timestamps = {}
        for field in ('timestamp', 'measured_at', 'measurement_time', 'last_updated',
                      'updated_at', 'last_seen', 'created_at'):
            raw = device.get(field)
            if not isinstance(raw, str) or len(raw) > 100:
                continue
            try:
                stamp = datetime.fromisoformat(raw.replace('Z', '+00:00'))
                if stamp.tzinfo is not None:
                    timestamps[field] = stamp.astimezone(timezone.utc).isoformat()
            except (ValueError, OverflowError):
                continue
        value = device.get('radon_level')
        if isinstance(value, bool):
            value = None
        try:
            value = float(value)
            if not math.isfinite(value) or value < 0:
                value = None
        except (ValueError, TypeError, OverflowError):
            value = None
        result.append({
            'device_fields': sorted(device),
            'has_serial_number': isinstance(device.get('serial_number'), str)
                                 and bool(device['serial_number']),
            'candidate_radon_value': value,
            'measurement_unit_verified': False,
            'candidate_timestamps_utc': timestamps,
            'measurement_time_verified': False,
            'history_ingested': False,
        })
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--login', action='store_true',
                        help='Prompt for account email and a hidden password; save neither')
    args = parser.parse_args()
    try:
        if args.login:
            username = input('EcoSense account email: ').strip()
            with warnings.catch_warnings():
                # Refuse getpass's visible-input fallback when no terminal is available.
                warnings.simplefilter('error', getpass.GetPassWarning)
                password = getpass.getpass('EcoSense password (not saved): ')
        else:
            username = os.environ.get('ECOSENSE_EMAIL', '')
            password = os.environ.get('ECOSENSE_PASSWORD', '')
        client = EcoSenseClient(username, password)
        print(json.dumps({'devices': describe_devices(client.get_devices())}, indent=2))
    except Exception as exc:
        # Authentication and HTTP errors may contain tokens/account URLs.
        raise SystemExit(f'EcoSense probe failed ({type(exc).__name__}); no history written.') from None


if __name__ == '__main__':
    main()
