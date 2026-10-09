#!/usr/bin/env python3
"""Explicit completed-history configuration, status and bounded acquisition."""

import argparse
import json
import os
import stat
import sys
import tempfile
from datetime import datetime
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    configure = sub.add_parser('configure')
    configure.add_argument('--device', required=True)
    configure.add_argument('--channel', required=True)
    configure.add_argument('--start', required=True)
    configure.add_argument('--legacy-storage-timezone')
    configure.add_argument('--window-minutes', type=int, default=60)
    configure.add_argument('--settling-seconds', type=int, default=300)
    configure.add_argument('--retry-seconds', type=int, default=300)
    configure.add_argument('--requests-hour', type=int, required=True)
    configure.add_argument('--requests-day', type=int, required=True)
    configure.add_argument('--confirm-completed-history-mode', action='store_true', required=True)
    for name in ('pause', 'resume'):
        command = sub.add_parser(name)
        command.add_argument('--device', required=True)
        command.add_argument('--channel', required=True)
    sub.add_parser('status')
    sub.add_parser('discover')
    run = sub.add_parser('run')
    run.add_argument('--max-requests', type=int, default=1)
    args = parser.parse_args(argv)
    os.umask(0o077)
    path = args.database.expanduser().absolute()
    original_cwd = Path.cwd()
    original_db = os.environ.get('DB_PATH')
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    try:
        metadata = path.lstat()
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
                or metadata.st_nlink != 1 or metadata.st_mode & 0o077
                or path.parent.stat().st_mode & 0o022):
            raise ValueError('Private regular database required')
        with tempfile.TemporaryDirectory(prefix='energy-history-bootstrap-') as directory:
            os.environ['DB_PATH'] = str(Path(directory) / 'bootstrap.db')
            import energy
            import history_collection

            energy.DB_PATH = str(path)
            os.environ['DB_PATH'] = str(path)
            os.chdir(path.parent)
            if args.command == 'status':
                conn = energy._connect(path, read_only=True)
                try:
                    conn.execute('BEGIN')
                    result = history_collection.status(conn)
                finally:
                    conn.close()
            else:
                energy.ensure_table()
                if args.command == 'configure':
                    result = energy.configure_completed_collection(args.device, args.channel,
                        datetime.fromisoformat(args.start), legacy_storage_timezone=args.legacy_storage_timezone,
                        window_minutes=args.window_minutes, settling_seconds=args.settling_seconds,
                        retry_seconds=args.retry_seconds, requests_hour=args.requests_hour, requests_day=args.requests_day)
                elif args.command in ('pause', 'resume'):
                    result = energy.set_completed_collection_enabled(args.device, args.channel, args.command == 'resume')
                elif args.command == 'discover':
                    with energy._poller_lock():
                        result = energy.discover_completed_collection_channels(energy.login_vue())
                else:
                    if not 1 <= args.max_requests <= 100:
                        raise ValueError('Invalid bounded request count')
                    if not any(row['enabled'] for row in energy.get_completed_collection_status()['channels']):
                        raise ValueError('No configured enabled channels; no login attempted')
                    with energy._poller_lock():
                        vue = energy.login_vue()
                        energy.RATE_CENTS = energy._read_rate_cents()
                        result = energy.run_completed_collection(vue, max_requests=args.max_requests)
    except Exception:
        print('Completed collection failed; inspect private configuration and durable job state. '
              'Publication outcome may be unknown; verify before retrying or activating.', file=sys.stderr)
        return 1
    finally:
        os.chdir(original_cwd)
        if original_db is None:
            os.environ.pop('DB_PATH', None)
        else:
            os.environ['DB_PATH'] = original_db
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
