#!/usr/bin/env python3
"""Read-only UTC preflight for a private JSON-lines export of reading identities.

No database is opened, no migration is performed and no app module is imported.
Input needs id, timestamp, device_gid, channel_name; source_timezone is optional.
Keep the export and report private. Diagnostics never echo malformed input.
"""
import argparse
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfoNotFoundError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from timestamp_model import audit_reading_timestamps  # noqa: E402


def reading_rows(stream):
    """Stream a complete JSON-lines export; refuse blanks or oversized records."""
    number = 0
    while line := stream.readline(65537):
        number += 1
        if len(line) > 65536:
            raise ValueError(f"Record {number} exceeds the size limit")
        try:
            yield json.loads(line)
        except (ValueError, TypeError):
            raise ValueError(f"Record {number} is not valid JSON") from None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--legacy-timezone", help="Explicit IANA source-zone assumption")
    parser.add_argument("--example-limit", type=int, default=20)
    args = parser.parse_args(argv)
    try:
        with args.input.open(encoding="utf-8") as stream:
            report = audit_reading_timestamps(
                reading_rows(stream), legacy_timezone=args.legacy_timezone,
                example_limit=args.example_limit,
            )
    except (OSError, ValueError, ZoneInfoNotFoundError):
        # Paths, invalid rows and zone strings may contain private input.
        print("Preflight failed: check the private input, record format and timezone.",
              file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 0 if report["candidate_conversion_unblocked"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
