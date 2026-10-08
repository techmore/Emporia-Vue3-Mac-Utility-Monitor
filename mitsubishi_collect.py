"""Opt-in separate Comfort snapshot collector; disabled modules make no requests."""
import argparse
import logging
from time import monotonic, sleep

import mitsubishi


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    retry_at, failed_generation = 0, None
    while True:
        result = {}
        try:
            config_path = mitsubishi._path('private.json')
            generation = config_path.stat().st_mtime_ns if config_path.exists() else None
            if not args.once and generation == failed_generation and monotonic() < retry_at:
                sleep(60)
                continue
            result = mitsubishi.poll()
            logging.info('Comfort collection: %s', result['state'])
            if result['state'] == 'authentication_rejected':
                retry_at = monotonic() + 900
                failed_generation = config_path.stat().st_mtime_ns if config_path.exists() else None
            else:
                retry_at = 0
        except mitsubishi.ComfortError as exc:
            logging.warning('Comfort collection: %s', str(exc))
        except Exception:
            logging.warning('Comfort collection: collector_error')
        if args.once:
            return
        sleep(60)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    main()
