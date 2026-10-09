"""Explicit timestamp interpretation for the UTC migration preflight.

This module neither imports the data layer nor reads or writes the database.
Legacy wall-clock timestamps require an explicit source-zone assumption.
"""
import re
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

ISO_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})?"
)


def classify_timestamp(value: str, source_timezone: str | None = None) -> dict:
    """Return exact UTC candidates, never guessing a legacy DST fold or gap."""
    if not isinstance(value, str) or not ISO_TIMESTAMP.fullmatch(value):
        return {"status": "invalid", "utc_candidates": []}
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return {"status": "invalid", "utc_candidates": []}
    if moment.tzinfo is not None:
        try:
            utc = moment.astimezone(timezone.utc).isoformat(timespec="microseconds")
        except OverflowError:
            return {"status": "invalid", "utc_candidates": []}
        return {"status": "aware", "utc_candidates": [utc]}
    if source_timezone is None:
        return {"status": "missing_source_timezone", "utc_candidates": []}
    zone = ZoneInfo(source_timezone)
    candidates = set()
    for fold in (0, 1):
        try:
            candidate = moment.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc)
            recovered = candidate.astimezone(zone).replace(tzinfo=None)
        except OverflowError:
            return {"status": "invalid", "utc_candidates": []}
        # Attaching ZoneInfo alone also accepts nonexistent wall-clock times.
        # Round trips reject gaps; distinct UTC candidates expose repeated hours.
        if recovered == moment:
            candidates.add(candidate.isoformat(timespec="microseconds"))
    values = sorted(candidates)
    status = {0: "nonexistent", 1: "legacy_unique", 2: "ambiguous"}[len(values)]
    return {"status": status, "utc_candidates": values}


def reporting_day_bounds(day: date, reporting_timezone: str) -> tuple[datetime, datetime]:
    """UTC bounds of an actual calendar day, including 23/25-hour DST days."""
    if type(day) is not date:
        raise ValueError("day must be a date")
    ZoneInfo(reporting_timezone)
    result = []
    for boundary in (day, day + timedelta(days=1)):
        stamp = datetime.combine(boundary, time()).isoformat()
        resolved = classify_timestamp(stamp, reporting_timezone)
        if resolved["status"] != "legacy_unique":
            raise ValueError("Calendar boundary is ambiguous or nonexistent")
        result.append(datetime.fromisoformat(resolved["utc_candidates"][0]))
    return tuple(result)


def audit_reading_timestamps(rows, *, legacy_timezone: str | None = None,
                            example_limit: int = 20) -> dict:
    """Analyze exported readings without approving or performing a migration.

    Rows contain id, timestamp, device_gid and channel_name. Optional
    source_timezone declares per-row provenance; otherwise legacy_timezone is
    only a dataset-wide assumption, not proof of where a row originated.
    """
    if type(example_limit) is not int or not 0 <= example_limit <= 100:
        raise ValueError("example_limit must be between zero and 100")
    if legacy_timezone is not None:
        ZoneInfo(legacy_timezone)
    counts = Counter()
    issues = []
    identifiers = set()
    projected_keys = {}
    collisions = duplicate_ids = total = 0
    first = last = None

    def example(problem: str, identifier, **details) -> None:
        if len(issues) < example_limit:
            issues.append({"problem": problem, "reading_id": identifier, **details})

    for row in rows:
        total += 1
        if not isinstance(row, dict):
            counts["invalid_row"] += 1
            example("invalid_row", None)
            continue
        identifier = row.get("id")
        device = row.get("device_gid")
        channel = row.get("channel_name")
        if (type(identifier) is not int or identifier <= 0 or
                not isinstance(device, str) or not device or
                (channel is not None and not isinstance(channel, str))):
            counts["invalid_row"] += 1
            example("invalid_row", identifier if type(identifier) is int else None)
            continue
        if identifier in identifiers:
            duplicate_ids += 1
            example("duplicate_reading_id", identifier)
        identifiers.add(identifier)
        source_timezone = row.get("source_timezone", legacy_timezone)
        if source_timezone is not None and not isinstance(source_timezone, str):
            raise ValueError("source_timezone must be an IANA timezone name or null")
        # Validate explicit per-row zones even for already-aware timestamps.
        if source_timezone is not None:
            ZoneInfo(source_timezone)
        resolved = classify_timestamp(row.get("timestamp"), source_timezone)
        status = resolved["status"]
        counts[status] += 1
        if status not in ("aware", "legacy_unique"):
            example(status, identifier, utc_candidates=resolved["utc_candidates"])
            continue
        utc = resolved["utc_candidates"][0]
        first = utc if first is None else min(first, utc)
        last = utc if last is None else max(last, utc)
        # SQLite's unique (device,timestamp,channel) index permits repeated NULL
        # channels; those are not collisions under the current schema.
        if channel is not None:
            key = (device, utc, channel)
            if key in projected_keys:
                collisions += 1
                example("utc_key_collision", identifier,
                        conflicting_reading_id=projected_keys[key], utc=utc)
            else:
                projected_keys[key] = identifier

    unresolved = sum(value for key, value in counts.items()
                     if key not in ("aware", "legacy_unique"))
    return {
        "report_version": 1,
        "dry_run": True,
        "migration_ready": False,
        "legacy_timezone_assumption": legacy_timezone,
        "rows": total,
        "classifications": dict(sorted(counts.items())),
        "unresolved_rows": unresolved,
        "utc_key_collisions": collisions,
        "duplicate_reading_ids": duplicate_ids,
        "projected_utc_range": {"first": first, "last": last},
        "candidate_conversion_unblocked": (
            total > 0 and unresolved == 0 and collisions == 0 and duplicate_ids == 0
        ),
        "examples": issues,
        "remaining_gates": [
            "Verify source timezone provenance; a default zone is only an assumption.",
            "Preserve IDs, costs, collector identity and replication journals/generations.",
            "Update writers, calendar queries, CSV imports, retention and client sync together.",
            "Verify DST, source totals, costs and replication on a private backup before cutover.",
        ],
    }
