"""Canonical monitor identity: one physical Emporia monitor, one device scope.

Live polling keys readings by Emporia's numeric cloud ``deviceGid``. Emporia CSV
exports name the monitor by a manufacturer suffix (for example ``8C9E94``). This
module records which cloud gid each manufacturer identifier was *observed* under
during real device discovery, and resolves an export prefix to exactly one cloud
gid or fails closed. Nothing here guesses from display names, and nothing here
rewrites stored readings.

All functions take an open sqlite3 connection and never commit, so the caller
decides the transaction boundary (the CSV importer resolves inside its
publication transaction). DDL lives in ``energy.ensure_table``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

# Observed export prefixes are the trailing six hex digits of the manufacturer id.
# Shorter fragments would match too many devices to be evidence of anything.
MIN_SUFFIX_LENGTH = 6
_HEX = re.compile(r"[0-9A-F]+")


def normalize_alias(value) -> str:
    return str(value).strip().upper()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def register_cloud_gid(conn, cloud_gid) -> None:
    """Record a cloud gid seen from the live API (idempotent, never overwrites)."""
    gid = str(cloud_gid).strip()
    if not gid:
        raise ValueError("cloud gid must be nonempty")
    conn.execute(
        "INSERT OR IGNORE INTO device_registry(cloud_gid, first_seen) VALUES (?, ?)",
        (gid, _now()),
    )


def register_alias(conn, alias_kind: str, alias, cloud_gid, source: str) -> bool:
    """Bind ``alias`` to a registered cloud gid. Returns True if newly recorded.

    Existing bindings are never changed. A manufacturer id already bound to a
    different cloud gid is a collision and raises ``ValueError``: discovery data
    that contradicts earlier discovery must be reviewed, not silently replaced.
    """
    if alias_kind not in ("manufacturer_id", "export_prefix"):
        raise ValueError("unsupported alias kind")
    if source not in ("discovery", "operator"):
        raise ValueError("unsupported alias source")
    text = normalize_alias(alias)
    gid = str(cloud_gid).strip()
    if not text:
        raise ValueError("alias must be nonempty")
    if not conn.execute("SELECT 1 FROM device_registry WHERE cloud_gid=?", (gid,)).fetchone():
        raise ValueError("alias target is not a registered cloud gid")
    if alias_kind == "manufacturer_id":
        other = conn.execute(
            "SELECT cloud_gid FROM device_aliases WHERE alias_kind='manufacturer_id' "
            "AND alias=? AND cloud_gid!=?", (text, gid)).fetchone()
        if other:
            raise ValueError("manufacturer id is already bound to a different cloud gid")
    cursor = conn.execute(
        "INSERT OR IGNORE INTO device_aliases(alias_kind, alias, cloud_gid, source, recorded_at) "
        "VALUES (?,?,?,?,?)", (alias_kind, text, gid, source, _now()))
    return bool(cursor.rowcount)


def register_discovered_devices(conn, devices) -> dict:
    """Record cloud gids and manufacturer ids from live device discovery.

    ``devices`` are SDK device objects. The SDK can return several objects for one
    cloud gid (parent plus nested); they are merged by gid, and every manufacturer
    id seen on any of them is kept. Objects without a usable gid are ignored.
    A colliding manufacturer id is skipped and reported rather than rebound.
    """
    registered, aliases, collisions = set(), 0, []
    for device in devices:
        gid = getattr(device, "device_gid", None)
        if gid is None or not str(gid).strip():
            continue
        gid = str(gid).strip()
        register_cloud_gid(conn, gid)
        registered.add(gid)
        manufacturer = getattr(device, "manufacturer_id", None)
        if manufacturer is None or not str(manufacturer).strip():
            continue
        try:
            aliases += register_alias(conn, "manufacturer_id", manufacturer, gid, "discovery")
        except ValueError:
            collisions.append(normalize_alias(manufacturer))
    return {"devices": sorted(registered), "aliases_added": aliases, "collisions": collisions}


def known_cloud_gids(conn) -> list[str]:
    return [row[0] for row in conn.execute("SELECT cloud_gid FROM device_registry ORDER BY cloud_gid")]


@dataclass(frozen=True)
class Resolution:
    status: str                  # 'resolved' | 'unknown' | 'ambiguous'
    cloud_gid: str | None        # set only when resolved
    candidates: tuple[str, ...]  # every cloud gid the prefix could mean
    method: str | None           # how it resolved (for evidence)


def candidates_for_prefix(conn, prefix) -> dict[str, str]:
    """Return {cloud_gid: method} for every registered monitor the prefix can denote."""
    text = normalize_alias(prefix)
    found: dict[str, str] = {}
    if not text:
        return found
    for row in conn.execute("SELECT cloud_gid FROM device_registry WHERE cloud_gid=?", (text,)):
        found.setdefault(row[0], "cloud_gid")
    for row in conn.execute(
            "SELECT cloud_gid FROM device_aliases WHERE alias_kind='export_prefix' AND alias=?", (text,)):
        found.setdefault(row[0], "operator_binding")
    for row in conn.execute(
            "SELECT cloud_gid FROM device_aliases WHERE alias_kind='manufacturer_id' AND alias=?", (text,)):
        found.setdefault(row[0], "manufacturer_id")
    if len(text) >= MIN_SUFFIX_LENGTH and _HEX.fullmatch(text):
        escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        for row in conn.execute(
                "SELECT DISTINCT cloud_gid FROM device_aliases WHERE alias_kind='manufacturer_id' "
                "AND alias LIKE ? ESCAPE '\\'", ("%" + escaped,)):
            found.setdefault(row[0], "manufacturer_id_suffix")
    # An operator's recorded choice for this exact prefix settles an ambiguity. It was
    # validated against the candidates at the time it was made, so it only narrows.
    chosen = {gid: method for gid, method in found.items() if method == "operator_binding"}
    return chosen or found


def resolve_export_prefix(conn, prefix) -> Resolution:
    """Resolve a CSV filename prefix to one canonical cloud gid, or fail closed."""
    found = candidates_for_prefix(conn, prefix)
    if len(found) == 1:
        gid, method = next(iter(found.items()))
        return Resolution("resolved", gid, (gid,), method)
    if not found:
        return Resolution("unknown", None, (), None)
    return Resolution("ambiguous", None, tuple(sorted(found)), None)


def bind_export_prefix(conn, prefix, cloud_gid) -> bool:
    """Operator-confirmed, source-local binding of one export prefix to one monitor.

    Refuses a binding that contradicts discovery evidence (the prefix already
    denotes a different monitor). The prefix is an alias for this single gid only;
    it never widens to a suffix match for other devices.
    """
    gid = str(cloud_gid).strip()
    found = candidates_for_prefix(conn, prefix)
    if found and gid not in found:
        raise ValueError("export prefix contradicts a registered monitor identity")
    return register_alias(conn, "export_prefix", prefix, gid, "operator")


class DeviceIdentityError(ValueError):
    """An export cannot be attributed to exactly one registered monitor.

    ``status`` is 'unknown', 'ambiguous' or 'contradiction'; ``candidates`` lists the
    cloud gids an operator may explicitly select. Raised before any write.
    """

    def __init__(self, message: str, *, status: str, prefix: str, candidates=()):
        super().__init__(message)
        self.status = status
        self.prefix = prefix
        self.candidates = tuple(candidates)


def legacy_alias_gids(conn, cloud_gid) -> list[str]:
    """Reading scopes that are not registered monitors but denote ``cloud_gid``.

    These are histories imported before identity resolution existed (the filename
    prefix was stored as the device scope). Reported, never rewritten here.
    """
    gid = str(cloud_gid)
    found = []
    for row in conn.execute("SELECT DISTINCT device_gid FROM readings").fetchall():
        scope = row[0]
        if scope == gid:
            continue
        if conn.execute("SELECT 1 FROM device_registry WHERE cloud_gid=?", (scope,)).fetchone():
            continue
        if gid in candidates_for_prefix(conn, scope):
            found.append(scope)
    return sorted(found)


def resolve_import_identity(conn, prefix, explicit_gid=None) -> tuple[str, str]:
    """Decide which device scope an export belongs to: ``(cloud_gid, method)``.

    Without an explicit selection the prefix must resolve to exactly one monitor.
    An explicit selection may settle an unknown or ambiguous prefix, but never
    contradicts discovery evidence; selecting a registered monitor records a
    source-local ``export_prefix`` binding. Unregistered explicit scopes are
    accepted for library callers and recorded as such.
    """
    text = normalize_alias(prefix)
    if not text:
        raise DeviceIdentityError("CSV device identity must be nonempty",
                                  status="unknown", prefix=text)
    found = candidates_for_prefix(conn, text)
    if explicit_gid is None or not str(explicit_gid).strip():
        if len(found) == 1:
            gid, method = next(iter(found.items()))
            return gid, method
        if not found:
            raise DeviceIdentityError(
                "This export's device prefix does not match any monitor seen by the "
                "collector. Select the monitor it belongs to; nothing was imported.",
                status="unknown", prefix=text, candidates=known_cloud_gids(conn))
        raise DeviceIdentityError(
            "This export's device prefix matches more than one monitor. Select the "
            "monitor it belongs to; nothing was imported.",
            status="ambiguous", prefix=text, candidates=sorted(found))
    gid = str(explicit_gid).strip()
    if found and gid not in found:
        raise DeviceIdentityError(
            "The selected monitor contradicts the registered identity of this export's "
            "device prefix; nothing was imported.",
            status="contradiction", prefix=text, candidates=sorted(found))
    registered = conn.execute(
        "SELECT 1 FROM device_registry WHERE cloud_gid=?", (gid,)).fetchone()
    if not registered:
        return gid, "explicit_unregistered"
    if len(found) == 1:
        return gid, found[gid]
    # Unknown or ambiguous prefix settled by the operator: bind this prefix to this
    # one monitor only (never a suffix pattern, never other prefixes).
    bind_export_prefix(conn, text, gid)
    return gid, "operator_selection"
