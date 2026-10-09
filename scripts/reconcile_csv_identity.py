#!/usr/bin/env python3
"""Audit a verified offline backup; publish/revert a copy only with its reviewed hash."""

import argparse
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--source-gid')
    parser.add_argument('--canonical-gid')
    parser.add_argument('--rollback-review-id')
    parser.add_argument('--destination', type=Path)
    parser.add_argument('--reviewed-plan-sha256')
    args = parser.parse_args(argv)
    if args.rollback_review_id:
        if args.source_gid or args.canonical_gid:
            parser.error('Rollback derives identities from the original review; omit monitor IDs')
    elif not args.source_gid or not args.canonical_gid:
        parser.error('Binding requires both source and canonical monitor IDs')
    os.umask(0o077)
    previous = os.environ.get('DB_PATH')
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    try:
        with tempfile.TemporaryDirectory(prefix='energy-identity-bootstrap-') as directory:
            os.environ['DB_PATH'] = str(Path(directory) / 'bootstrap.db')
            from identity_reconciliation import review_identity_copy

            report = review_identity_copy(args.snapshot, expected_sha256=args.expected_sha256,
                source_gid=args.source_gid, canonical_gid=args.canonical_gid,
                rollback_review_id=args.rollback_review_id, destination=args.destination,
                reviewed_plan_sha256=args.reviewed_plan_sha256)
    except (OSError, ValueError, RuntimeError, ImportError, sqlite3.DatabaseError):
        print('Identity review failed; check the private standalone backup, verified hashes, '
              'source evidence and review. Publication outcome is unknown; inspect any destination '
              'and obtain a complete verified receipt before retrying or activating it.', file=sys.stderr)
        return 1
    finally:
        if previous is None:
            os.environ.pop('DB_PATH', None)
        else:
            os.environ['DB_PATH'] = previous
    print(json.dumps(report, indent=2))
    return 0 if report['candidate_unblocked'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
