"""Opt-in separate Comfort snapshot collector; disabled modules make no requests."""
import argparse
import logging
from time import monotonic, sleep

import log_setup
import mitsubishi

logger = logging.getLogger("mitsubishi_collect")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    retry_at, failed_generation = 0, None
    while True:
        result = {}
        started = monotonic()
        try:
            config_path = mitsubishi._path('private.json')
            generation = config_path.stat().st_mtime_ns if config_path.exists() else None
            if not args.once and generation == failed_generation and monotonic() < retry_at:
                sleep(60)
                continue
            result = mitsubishi.poll()
            log_setup.event(logger, 'poll_cycle', duration_s=round(monotonic() - started, 3),
                            state=result['state'], devices=len(result.get('units') or []))
            if result['state'] == 'authentication_rejected':
                retry_at = monotonic() + 900
                failed_generation = config_path.stat().st_mtime_ns if config_path.exists() else None
            else:
                retry_at = 0
        except mitsubishi.ComfortError as exc:
            log_setup.event(logger, 'poll_cycle', logging.WARNING, ok=False, error=type(exc).__name__,
                            detail=str(exc)[:200], duration_s=round(monotonic() - started, 3))
        except Exception:
            log_setup.event(logger, 'poll_cycle', logging.WARNING, ok=False, error='collector_error',
                            duration_s=round(monotonic() - started, 3))
        if args.once:
            return
        sleep(60)


if __name__ == '__main__':
    log_setup.configure()
    log_setup.startup(logger, 'mitsubishi-collector', interval_s=60)
    main()
