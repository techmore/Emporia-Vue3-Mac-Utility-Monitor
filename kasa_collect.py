"""Read-only Kasa collector; run independently of the dashboard on the always-on host."""
import argparse
import asyncio
import logging
import os
import time

import kasa_history
import kasa_monitor
import log_setup

logger = logging.getLogger("kasa_collect")


async def poll_once(username: str | None = None, password: str | None = None) -> dict:
    devices = kasa_history.get_devices()
    semaphore = asyncio.Semaphore(4)

    async def query(device: dict) -> str:
        started = time.monotonic()
        async with semaphore:
            try:
                snapshot = await kasa_monitor.probe(device['host'], username, password)
                result = 'ok' if kasa_history.record_query(device['id'], snapshot) else 'unavailable'
                log_setup.event(logger, 'kasa_query', logging.DEBUG, device_id=device['id'], status=result,
                                duration_ms=snapshot.get('duration_ms'), is_on=snapshot['is_on'],
                                brightness=snapshot.get('brightness'),
                                state_changed=device.get('is_on') != int(snapshot['is_on']),
                                timestamp=snapshot.get('queried_at'))
                return result
            except Exception as exc:
                log_setup.event(logger, 'kasa_query', logging.WARNING, device_id=device['id'],
                                status='unavailable', error=type(exc).__name__,
                                duration_ms=round((time.monotonic() - started) * 1000, 1))
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
    log_setup.configure()
    log_setup.startup(logger, 'kasa-collector', interval_s=args.interval, once=args.once,
                      db_path=os.environ.get('DB_PATH', 'energy.db'),
                      kasa_login_configured=bool(os.environ.get('KASA_USERNAME')))
    while True:
        started = time.monotonic()
        try:
            result = asyncio.run(poll_once(os.environ.get('KASA_USERNAME'), os.environ.get('KASA_PASSWORD')))
            log_setup.event(logger, 'poll_cycle', duration_s=round(time.monotonic() - started, 3),
                            devices=result['queried'], **{k: v for k, v in result.items() if k != 'queried'})
        except Exception as exc:
            log_setup.event(logger, 'poll_cycle', logging.WARNING, ok=False, error=type(exc).__name__,
                            duration_s=round(time.monotonic() - started, 3))
            if args.once:
                raise SystemExit(1) from None
        if args.once:
            return
        time.sleep(max(1, args.interval - (time.monotonic() - started)))


if __name__ == '__main__':
    main()
