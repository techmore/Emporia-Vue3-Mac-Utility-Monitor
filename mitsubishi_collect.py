"""Opt-in separate Comfort snapshot collector; disabled modules make no requests."""
import argparse
import logging
from time import sleep

import mitsubishi


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    while True:
        result = {}
        try:
            result = mitsubishi.poll()
            logging.info('Comfort collection: %s', result['state'])
        except mitsubishi.ComfortError as exc:
            logging.warning('Comfort collection: %s', str(exc))
        except Exception:
            logging.warning('Comfort collection: collector_error')
        if args.once:
            return
        sleep(900 if result.get('state') == 'authentication_rejected' else 60)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    main()
