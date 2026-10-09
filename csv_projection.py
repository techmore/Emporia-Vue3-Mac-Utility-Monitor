"""Append-only CSV evidence and transactional non-overlapping reading projection.

No connections or schema DDL live here. The data layer supplies its locked
connection; original source-local evidence and canonical bounds never change.
"""

import hashlib
import json
import math
from bisect import bisect_left, bisect_right
from datetime import datetime, timedelta
from zoneinfo import ZoneInfoNotFoundError

from timestamp_model import classify_timestamp


def identity(*values) -> str:
    return hashlib.sha256(
        json.dumps(values, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def _bounds(row: dict) -> tuple[str, str] | None:
    if not row["start_utc"] or not row["end_utc"]:
        return None
    start, end = row["start_utc"], row["end_utc"]
    if (
        any(classify_timestamp(value).get("utc_candidates") != [value] for value in (start, end))
        or not start < end
    ):
        raise ValueError("Invalid CSV interval bounds")
    return start, end


def select_intervals(rows: list[dict]) -> list[dict]:
    """Maximize covered elapsed time, then resolution; adjacent bounds can touch.

    Weighted interval scheduling uses integer microseconds and O(n log n) time.
    Stable end/start/source ordering breaks ties without relying on import order.
    Unknown bounds are never passed to this selector.
    """
    for row in rows:
        if _bounds(row) is None:
            raise ValueError("Unknown CSV interval bounds cannot be selected")
    ordered = sorted(rows, key=lambda row: (row["end_utc"], row["start_utc"], row["id"]))
    ends = [row["end_utc"] for row in ordered]
    scores, decisions = [(0, 0)], []
    for index, row in enumerate(ordered):
        start, end = row["start_utc"], row["end_utc"]
        delta = datetime.fromisoformat(end) - datetime.fromisoformat(start)
        duration = (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds
        previous = bisect_right(ends, start, 0, index)
        candidate = (scores[previous][0] + duration, scores[previous][1] + 1)
        take = candidate > scores[-1]
        scores.append(candidate if take else scores[-1])
        decisions.append((take, previous))
    selected, cursor = [], len(ordered)
    while cursor:
        take, previous = decisions[cursor - 1]
        if take:
            selected.append(ordered[cursor - 1])
            cursor = previous
        else:
            cursor -= 1
    return sorted(selected, key=lambda row: (row["start_utc"], row["end_utc"]))


def _components(rows: list[dict], incoming: set[str]):
    group, end, touched = [], "", False
    for row in sorted(rows, key=lambda item: (item["start_utc"], item["end_utc"], item["id"])):
        if group and row["start_utc"] >= end:
            if touched:
                yield group
            group, end, touched = [], "", False
        group.append(row)
        end = max(end, row["end_utc"])
        touched |= row["id"] in incoming
    if group and touched:
        yield group


def _same_energy(left: float, right: float) -> bool:
    # This tolerates arithmetic noise, not an unproven source-rounding correction.
    return math.isclose(left, right, rel_tol=1e-10, abs_tol=1e-10)


def _elapsed_microseconds(start: str, end: str) -> int:
    delta = datetime.fromisoformat(end) - datetime.fromisoformat(start)
    return (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds


def _resolution_conflict(rows: list[dict], selected: list[dict]) -> bool:
    starts = [row["start_utc"] for row in selected]
    selected_ids = {row["id"] for row in selected}
    gaps = [0]
    for index, row in enumerate(selected):
        gaps.append(
            gaps[-1] + int(index > 0 and selected[index - 1]["end_utc"] != row["start_utc"])
        )
    for row in rows:
        if row["id"] in selected_ids:
            continue
        left, right = bisect_left(starts, row["start_utc"]), bisect_left(starts, row["end_utc"])
        if (
            left < right
            and selected[left]["start_utc"] == row["start_utc"]
            and selected[right - 1]["end_utc"] == row["end_utc"]
            and gaps[right] == gaps[left + 1]
        ):
            total = math.fsum(item["usage_kwh"] for item in selected[left:right])
            if not _same_energy(total, row["usage_kwh"]):
                return True
    return False


def _unmanaged_bounds(row: dict, clock) -> tuple[str, str] | None:
    if (
        row["measurement_source"] not in ("csv_energy", "csv_power")
        or not row["source_timezone"]
        or not row["measurement_seconds"]
        or not math.isfinite(row["measurement_seconds"])
        or row["measurement_seconds"] <= 0
    ):
        return None
    stamp = row["timestamp"]
    try:
        resolved = classify_timestamp(stamp, None if clock else row["source_timezone"])
    except (ValueError, ZoneInfoNotFoundError):
        return None
    if resolved["status"] not in ("aware", "legacy_unique"):
        return None
    start = resolved["utc_candidates"][0]
    try:
        end = (
            datetime.fromisoformat(start) + timedelta(seconds=row["measurement_seconds"])
        ).isoformat(timespec="microseconds")
    except (OverflowError, ValueError):
        return None
    return start, end


def _apply_projection(conn, selected: list[dict], existing: list[dict], clock) -> dict:
    """Mutate only explicitly owned rows, preserving IDs when their key survives."""
    old = {row["observation_id"]: row for row in existing}
    wanted = {row["id"]: row for row in selected}
    retained = set(old) & set(wanted)
    removed = [row for key, row in old.items() if key not in retained]
    by_stamp = {row["timestamp"]: row for row in removed}
    stats = {
        "updated": 0,
        "inserted": 0,
        "deleted": 0,
        "superseded": len(removed),
        "activated": set(wanted) - set(old),
        "members": [old[key] for key in retained],
    }
    for row in removed:
        conn.execute(
            "DELETE FROM csv_reading_projection WHERE observation_id=?", (row["observation_id"],)
        )

    def stamp_for(observation_id):
        row = wanted[observation_id]
        return row["source_utc_timestamp"] if clock else row["source_local_timestamp"]

    replacement_stamps = {stamp_for(key) for key in stats["activated"]}
    # Incremental cache pages must remove old coverage before adding its replacement.
    for row in removed:
        if row["timestamp"] not in replacement_stamps:
            conn.execute("DELETE FROM readings WHERE id=?", (row["reading_id"],))
            stats["deleted"] += 1

    def replacement_order(observation_id):
        stamp = stamp_for(observation_id)
        return stamp not in by_stamp, stamp, observation_id

    for observation_id in sorted(stats["activated"], key=replacement_order):
        row = wanted[observation_id]
        stamp = row["source_utc_timestamp"] if clock else row["source_local_timestamp"]
        values = (
            stamp,
            row["device_gid"],
            None,
            row["channel_name"],
            row["usage_kwh"],
            row["cost_cents"],
            row["measurement_seconds"],
            row["measurement_source"],
            row["source_timezone"],
            None,
        )
        previous = by_stamp.get(stamp)
        if previous:
            reading_id = previous["reading_id"]
            conn.execute(
                """UPDATE readings SET timestamp=?,device_gid=?,channel_num=?,
                channel_name=?,usage_kwh=?,cost_cents=?,measurement_seconds=?,
                measurement_source=?,source_timezone=?,provider_timestamp=? WHERE id=?""",
                (*values, reading_id),
            )
            stats["updated"] += 1
        else:
            reading_id = conn.execute(
                """INSERT INTO readings
                (timestamp,device_gid,channel_num,channel_name,usage_kwh,cost_cents,
                 measurement_seconds,measurement_source,source_timezone,provider_timestamp)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                values,
            ).lastrowid
            stats["inserted"] += 1
        conn.execute(
            "INSERT INTO csv_reading_projection VALUES (?,?)", (observation_id, reading_id)
        )
        stats["members"].append(
            {"observation_id": observation_id, "reading_id": reading_id, "timestamp": stamp}
        )
    return stats


def publish_csv(conn, source: dict, observations: list[dict], clock) -> dict:
    """Publish evidence/projection inside the caller's transaction, or fail closed.

    Existing unmanaged rows are never automatically adopted or deleted. Unknown
    boundaries block competing sources; first-source legacy imports remain usable
    but are explicitly marked unresolved. Reconciliation needs verified archives.
    """
    if not conn.in_transaction:
        raise RuntimeError("CSV publication requires an existing transaction")
    batch_id = source["id"]
    conn.execute(
        """INSERT OR IGNORE INTO csv_source_batches
        (id,sha256,original_filename,device_gid,interval,source_timezone,headers_json,rate_cents,content)
        VALUES (:id,:sha256,:original_filename,:device_gid,:interval,:source_timezone,:headers_json,:rate_cents,:content)""",
        source,
    )
    recorded = (
        conn.executemany(
            """INSERT OR IGNORE INTO csv_source_observations
        (id,batch_id,row_number,channel_name,source_header,source_unit,raw_timestamp,raw_value,
         source_local_timestamp,source_utc_timestamp,start_utc,end_utc,usage_kwh,cost_cents,measurement_seconds,measurement_source)
        VALUES (:id,:batch_id,:row_number,:channel_name,:source_header,:source_unit,:raw_timestamp,:raw_value,
         :source_local_timestamp,:source_utc_timestamp,:start_utc,:end_utc,:usage_kwh,:cost_cents,:measurement_seconds,:measurement_source)""",
            observations,
        ).rowcount
        if observations
        else 0
    )
    result = {
        "imported": 0,
        "observations_recorded": recorded,
        "updated": 0,
        "inserted": 0,
        "deleted": 0,
        "superseded": 0,
        "overlap_suppressed": 0,
        "warnings": 0,
        "quality_issues": [],
    }

    issues = {}

    def warn(channel, reason):
        result["warnings"] += 1
        key = channel, reason
        if key not in issues:
            issues[key] = {"channel_name": channel, "reason": reason, "count": 0}
            result["quality_issues"].append(issues[key])
        issues[key]["count"] += 1

    for channel in sorted({row["channel_name"] for row in observations}):
        incoming = {row["id"] for row in observations if row["channel_name"] == channel}
        raw = [
            dict(row)
            for row in conn.execute(
            """SELECT o.*,o.sequence observation_order,b.device_gid,b.source_timezone
            FROM csv_source_observations o JOIN csv_source_batches b ON b.id=o.batch_id
            WHERE b.device_gid=? AND o.channel_name=? ORDER BY o.sequence""",
                (source["device_gid"], channel),
            )
        ]
        existing = [
            dict(row)
            for row in conn.execute(
                """SELECT p.observation_id,p.reading_id,r.timestamp
            FROM csv_reading_projection p JOIN readings r ON r.id=p.reading_id
            WHERE r.device_gid=? AND r.channel_name=?""",
                (source["device_gid"], channel),
            )
        ]
        unmanaged = [
            dict(row)
            for row in conn.execute(
                """SELECT r.* FROM readings r
            LEFT JOIN csv_reading_projection p ON p.reading_id=r.id
            WHERE r.device_gid=? AND r.channel_name=? AND p.reading_id IS NULL""",
                (source["device_gid"], channel),
            )
        ]
        unknown = [row for row in raw if not _bounds(row)]
        known = [row for row in raw if _bounds(row)]
        unmanaged_intervals = [_unmanaged_bounds(row, clock) for row in unmanaged]
        if any(bounds is None for bounds in unmanaged_intervals):
            warn(channel, "unmanaged_history_has_unknown_bounds")
            continue
        blocked = []
        for left, right in sorted(unmanaged_intervals):
            if blocked and left <= blocked[-1][1]:
                blocked[-1] = (blocked[-1][0], max(right, blocked[-1][1]))
            else:
                blocked.append((left, right))
        blocked_ends = [right for _, right in blocked]
        if unknown:
            warn(channel, "source_interval_or_timezone_unresolved")
            if not known and not unmanaged and all(row["batch_id"] == batch_id for row in unknown):
                # Preserve first-source legacy behavior without claiming verified coverage.
                first = {}
                for row in unknown:
                    first.setdefault(row["source_local_timestamp"], row)
                selected = list(first.values())
                stats = _apply_projection(conn, selected, existing, clock)
                result["imported"] += len(stats["activated"])
                for field in ("updated", "inserted", "deleted", "superseded"):
                    result[field] += stats[field]
            continue

        membership = {row["observation_id"]: row for row in existing}
        occupied = {row["timestamp"]: row["reading_id"] for row in existing}
        unmanaged_stamps = {row["timestamp"] for row in unmanaged}
        for component in _components(known, incoming):
            # A same-interval conflict preserves the first validated source value.
            variants, canonical = {}, []
            for row in component:
                key = row["start_utc"], row["end_utc"]
                variants.setdefault(key, []).append(row)
            conflict = False
            for group in variants.values():
                group.sort(key=lambda row: row["observation_order"])
                canonical.append(group[0])
                if any(
                    not _same_energy(row["usage_kwh"], group[0]["usage_kwh"]) for row in group[1:]
                ):
                    conflict = True
            if conflict:
                warn(channel, "same_interval_sources_disagree")
            selected = select_intervals(canonical)
            start = min(row["start_utc"] for row in component)
            end = max(row["end_utc"] for row in component)
            covered = sum(
                _elapsed_microseconds(row["start_utc"], row["end_utc"]) for row in selected
            )
            if covered < _elapsed_microseconds(start, end):
                warn(channel, "partial_overlap_requires_review")
                continue
            if _resolution_conflict(canonical, selected):
                warn(channel, "complete_resolutions_disagree")
                continue
            blocker = bisect_right(blocked_ends, start)
            if blocker < len(blocked) and blocked[blocker][0] < end:
                warn(channel, "overlaps_unmanaged_history")
                continue
            component_ids = {row["id"] for row in component}
            owned = [membership[key] for key in component_ids if key in membership]
            owned_ids = {row["reading_id"] for row in owned}
            stamps = [
                row["source_utc_timestamp"] if clock else row["source_local_timestamp"]
                for row in selected
            ]
            if (
                len(stamps) != len(set(stamps))
                or any(stamp in occupied and occupied[stamp] not in owned_ids for stamp in stamps)
                or unmanaged_stamps.intersection(stamps)
            ):
                warn(channel, "legacy_storage_key_collision")
                continue
            stats = _apply_projection(conn, selected, owned, clock)
            for row in owned:
                membership.pop(row["observation_id"], None)
                occupied.pop(row["timestamp"], None)
            for row in stats["members"]:
                membership[row["observation_id"]] = row
                occupied[row["timestamp"]] = row["reading_id"]
            incoming_keys = {
                (row["start_utc"], row["end_utc"], row["usage_kwh"])
                for row in component
                if row["id"] in incoming
            }
            result["imported"] += sum(
                row["id"] in stats["activated"]
                and (row["start_utc"], row["end_utc"], row["usage_kwh"]) in incoming_keys
                for row in selected
            )
            chosen_keys = {(row["start_utc"], row["end_utc"], row["usage_kwh"]) for row in selected}
            result["overlap_suppressed"] += sum(
                (row["start_utc"], row["end_utc"], row["usage_kwh"]) not in chosen_keys
                for row in component
                if row["id"] in incoming
            )
            for field in ("updated", "inserted", "deleted", "superseded"):
                result[field] += stats[field]
    if result["warnings"]:
        result["message"] = (
            "Source evidence retained; review conflicts or unknown coverage. Unverified history was not replaced."
        )
    elif result["overlap_suppressed"] or result["superseded"]:
        result["message"] = (
            "Source evidence retained; CSV projections use non-overlapping intervals. Existing live/history reconciliation remains separate."
        )
    return result
