#!/usr/bin/env python3
"""Audit a private captured chart request/response. No login or DB writes."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from emporia_history import validate_completed_minutes  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--settling-seconds", type=int, default=300)
    parser.add_argument(
        "--include-buckets",
        action="store_true",
        help="Include private measurement values in the JSON output",
    )
    args = parser.parse_args(argv)
    try:
        with args.input.open("rb") as stream:
            content = stream.read(2_000_001)
        if len(content) > 2_000_000:
            raise ValueError("Evidence exceeds size limit")
        report = validate_completed_minutes(
            json.loads(content), settling_seconds=args.settling_seconds
        )
    except (OSError, ValueError, TypeError, RecursionError, OverflowError):
        print(
            "History audit failed: check the private evidence and explicit request contract.",
            file=sys.stderr,
        )
        return 1
    if not args.include_buckets:
        report.pop("buckets")
    report["evidence_sha256"] = hashlib.sha256(content).hexdigest()
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
