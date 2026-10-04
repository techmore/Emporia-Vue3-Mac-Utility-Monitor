#!/usr/bin/env python3
"""Optional read-only collector. Credentials stay in environment, never in replay JSON.

Run only after configuring and explicitly choosing temperature entities in your setup.
"""
import argparse
from datetime import datetime, timezone
import json
import logging
import math
import os
from pathlib import Path
import time
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

import climate


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward a bearer token to a redirected host.
        return None


def home_assistant_observations(states: list[dict], entity_ids: list[str]) -> list[dict]:
    """Normalize chosen temperature states. Missing/unavailable/unsupported units are skipped."""
    selected = set(entity_ids)
    observations = []
    for state in states:
        if not isinstance(state, dict):
            continue
        if state.get('entity_id') not in selected:
            continue
        attributes = state.get('attributes', {})
        if not isinstance(attributes, dict):
            continue
        if attributes.get('device_class') != 'temperature':
            continue
        unit = attributes.get('unit_of_measurement')
        if unit not in ('°C', '°F', 'K'):
            continue
        try:
            value = float(state['state'])
            if unit == '°F':
                value = (value - 32) * 5 / 9
            elif unit == 'K':
                value -= 273.15
            if not math.isfinite(value) or not -80 <= value <= 80:
                continue
            timestamp = state['last_updated']
            stamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            now = datetime.now(timezone.utc)
            if stamp.tzinfo is None or not 0 <= (now-stamp).total_seconds() <= climate.energy.DB_RETENTION_DAYS*86400:
                continue
        except (ValueError, TypeError, KeyError):
            continue
        observations.append({'source': 'home_assistant', 'sensor_id': state['entity_id'],
                             'name': attributes.get('friendly_name') or state['entity_id'],
                             'timestamp': timestamp, 'temperature_c': value})
    return observations


def collect_home_assistant() -> dict:
    base = os.environ.get('HA_URL', '').rstrip('/')
    token = os.environ.get('HA_TOKEN', '')
    entity_ids = [item.strip() for item in os.environ.get('HA_TEMPERATURE_ENTITIES', '').split(',') if item.strip()]
    parsed = urlsplit(base)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Set HA_URL to your Home Assistant http(s) base URL')
    if not token or not entity_ids:
        raise ValueError('Set HA_TOKEN and HA_TEMPERATURE_ENTITIES before collecting')
    req = Request(f'{base}/api/states', headers={'Authorization': f'Bearer {token}', 'Accept': 'application/json'})
    with build_opener(NoRedirect).open(req, timeout=15) as response:
        states = json.load(response)
    if not isinstance(states, list):
        raise ValueError('Unexpected Home Assistant response')
    observations = home_assistant_observations(states, entity_ids)
    if not observations:
        return {'inserted': 0, 'duplicates': 0, 'status': 'No available selected temperature readings'}
    return climate.ingest_observations(observations)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--import-json', type=Path, help='Import {"observations": [...]} from a local file')
    parser.add_argument('--home-assistant', action='store_true', help='Read configured temperature entities')
    parser.add_argument('--watch', action='store_true', help='Collect every 60 seconds until interrupted')
    args = parser.parse_args()
    if bool(args.import_json) == args.home_assistant or (args.watch and not args.home_assistant):
        parser.error('Choose --import-json or --home-assistant; --watch is only for Home Assistant')
    if args.import_json:
        print(json.dumps(climate.ingest_observations(json.loads(args.import_json.read_text()).get('observations'))))
        return
    while True:
        try:
            result = collect_home_assistant()
            print(json.dumps({'collected_at': datetime.now(timezone.utc).isoformat(), **result}), flush=True)
        except Exception as exc:
            if not args.watch:
                raise
            # Error messages can contain remote content; do not emit credential-bearing responses.
            logging.warning('Temperature collection failed (%s); retrying in 60 seconds', type(exc).__name__)
        if not args.watch:
            break
        time.sleep(60)


if __name__ == '__main__':
    main()
