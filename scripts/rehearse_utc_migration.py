#!/usr/bin/env python3
"""Create a private, NON-DEPLOYABLE UTC conversion rehearsal from a verified backup.

Requires an explicit archive hash and source/reporting-zone assumptions. Never
updates the archive or enables a UTC collector. Keep the report outside Git.
"""
import argparse
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfoNotFoundError


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--legacy-timezone", required=True)
    parser.add_argument("--reporting-timezone", required=True)
    args = parser.parse_args(argv)
    os.umask(0o077)
    # energy's normal import initializes schema. Isolate that bootstrap from the
    # source backup and all existing installations before importing the data layer.
    original = os.environ.get("DB_PATH")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    try:
        with tempfile.TemporaryDirectory(prefix="energy-utc-bootstrap-") as directory:
            os.environ["DB_PATH"] = str(Path(directory) / "bootstrap.db")
            from utc_migration import rehearse_utc_copy

            report = rehearse_utc_copy(
                args.snapshot, args.destination, expected_sha256=args.expected_sha256,
                legacy_timezone=args.legacy_timezone, reporting_timezone=args.reporting_timezone,
            )
    except (OSError, ValueError, RuntimeError, ImportError, ZoneInfoNotFoundError, sqlite3.DatabaseError):
        print("UTC rehearsal failed; check the complete source, locked dependencies, "
              "private snapshot, receipt and destination. "
              "No artifact was published by this run.", file=sys.stderr)
        return 1
    finally:
        if original is None:
            os.environ.pop("DB_PATH", None)
        else:
            os.environ["DB_PATH"] = original
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
