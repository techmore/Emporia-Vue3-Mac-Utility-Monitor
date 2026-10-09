"""Validate captured Emporia chart evidence without opening a database.

Live-list `instant` values are not substituted for chart boundaries. This module
does not publish readings, prove continuous capture, or reconcile old history.
"""

import math
from datetime import datetime, timedelta, timezone

from timestamp_model import classify_timestamp


def _instant(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("An explicit aware timestamp is required")
    try:
        resolved = classify_timestamp(value)
    except (ValueError, OverflowError):
        raise ValueError("Invalid aware timestamp") from None
    if resolved["status"] != "aware":
        raise ValueError("An explicit aware timestamp is required")
    return datetime.fromisoformat(resolved["utc_candidates"][0])


def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="microseconds")


def validate_completed_minutes(evidence: dict, *, settling_seconds: int = 300) -> dict:
    """Select complete half-open minute buckets from an explicit captured request.

    `settling_seconds` is a conservative observation delay, not proof that cloud
    values are final. Preserve original responses separately before publication.
    Unknown values remain gaps; malformed measurements fail the entire report.
    """
    if type(settling_seconds) is not int or not 0 <= settling_seconds <= 86400:
        raise ValueError("Invalid settling delay")
    if not isinstance(evidence, dict) or evidence.get("schema") != "emporia_chart_v1":
        raise ValueError("Unsupported chart evidence")
    request, response = evidence.get("request"), evidence.get("response")
    if not isinstance(request, dict) or not isinstance(response, dict):
        raise ValueError("Missing chart request or response")
    if request.get("scale") != "1MIN" or request.get("unit") != "KilowattHours":
        raise ValueError("Only explicit minute energy requests are supported")
    start, end = _instant(request.get("start")), _instant(request.get("end"))
    received = _instant(evidence.get("received_at"))
    if start >= end or end - start > timedelta(days=7):
        raise ValueError("Invalid chart window")
    # PyEmVue otherwise defaults missing firstUsageInstant to the requested start.
    first = _instant(response.get("firstUsageInstant"))
    if first.second or first.microsecond:
        raise ValueError("Chart anchor must be minute-aligned")
    values = response.get("usageList")
    if not isinstance(values, list) or len(values) > 10081:
        raise ValueError("Invalid chart value list")
    for value in values:
        if value is not None:
            try:
                valid = type(value) in (int, float) and math.isfinite(value)
            except OverflowError:
                valid = False
            if not valid:
                raise ValueError("Invalid chart measurement")
    buckets = []
    excluded_window = excluded_unsettled = missing = 0
    try:
        limit = received - timedelta(seconds=settling_seconds)
        expected_start = start.replace(second=0, microsecond=0)
        if expected_start < start:
            expected_start += timedelta(minutes=1)
        expected_end = min(end, limit).replace(second=0, microsecond=0)
        expected = max(0, int((expected_end - expected_start).total_seconds() // 60))
        for index, value in enumerate(values):
            left = first + timedelta(minutes=index)
            right = left + timedelta(minutes=1)
            if left < start or right > end:
                excluded_window += 1
            elif right > limit:
                excluded_unsettled += 1
            elif value is None:
                missing += 1
            else:
                buckets.append(
                    {
                        "start_utc": _stamp(left),
                        "end_utc": _stamp(right),
                        "usage_kwh": value,
                        "measurement_seconds": 60,
                        "source_index": index,
                    }
                )
    except OverflowError:
        raise ValueError("Chart bounds overflow") from None
    return {
        "request_start_utc": _stamp(start),
        "request_end_utc": _stamp(end),
        "received_at_utc": _stamp(received),
        "first_usage_utc": _stamp(first),
        "settling_seconds": settling_seconds,
        "reported_values": len(values),
        "excluded_window": excluded_window,
        "excluded_unsettled": excluded_unsettled,
        "expected_completed_buckets": expected,
        "reported_null_buckets": missing,
        "unreported_completed_buckets": expected - len(buckets) - missing,
        "missing_completed_buckets": expected - len(buckets),
        "eligible_completed_buckets": len(buckets),
        "buckets": buckets,
        "continuous_capture_verified": False,
        "publication_performed": False,
    }
