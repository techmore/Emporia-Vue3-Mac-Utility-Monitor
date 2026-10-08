"""Read-only Kasa collector; run independently of the dashboard on the always-on host."""
import argparse
import asyncio
import json
import os
import time

import kasa_history
import kasa_monitor


async def poll_once(username: str | None = None, password: str | None = None) -> dict:
    devices = kasa_history.get_devices()
    semaphore = asyncio.Semaphore(4)

    async def query(device: dict) -> str:
        async with semaphore:
            try:
                snapshot = await kasa_monitor.probe(device['host'], username, password)
                return 'ok' if kasa_history.record_query(device['id'], snapshot) else 'unavailable'
            except Exception as exc:
                try:
                    kasa_history.record_query(device['id'], None, type(exc).__name__)
                except ValueError:
                    # The user may remove a device while its query is in flight.
                    return 'skipped'
                return 'unavailable'

    results = await asyncio.gather(*(query(device) for device in devices))
    return {'queried': len(results), 'ok': results.count('ok'),
            'unavailable': results.count('unavailable'), 'skipped': results.count('skipped')}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--interval', type=int, default=60, help='Seconds between cycle starts')
    args = parser.parse_args()
    if not 10 <= args.interval <= 86400:
        parser.error('interval must be between 10 and 86400 seconds')
    while True:
        started = time.monotonic()
        try:
            result = asyncio.run(poll_once(os.environ.get('KASA_USERNAME'), os.environ.get('KASA_PASSWORD')))
            print(json.dumps(result), flush=True)
        except Exception as exc:
            print(json.dumps({'collector_error': type(exc).__name__}), flush=True)
            if args.once:
                raise SystemExit(1) from None
        if args.once:
            return
        time.sleep(max(1, args.interval - (time.monotonic() - started)))


if __name__ == '__main__':
    main()
