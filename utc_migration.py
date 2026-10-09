"""Transactional UTC conversion rehearsal; never mutates the supplied backup.

Published copies are explicitly rejected by the current collector. The remaining
writer/query/import/client work must be integrated before any live cutover.
"""
import hashlib
import json
import os
import re
import stat
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from timestamp_model import audit_reading_timestamps, classify_timestamp

TIME_COLUMNS = {
    "reading_changes": (("sequence",), "timestamp"),
    "latest_channel_snapshot": (("device_gid", "channel_name"), "timestamp"),
    "poller_health_events": (("id",), "timestamp"),
    "device_capabilities": (("device_gid",), "updated_at"),
    "migrations": (("name",), "applied_at"),
    "sync_cache_state": (("singleton",), "synchronized_at"),
    "readings": (("id",), "timestamp"),
}


def _identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _digest_file(stream) -> str:
    digest = hashlib.sha256()
    while data := stream.read(1024 * 1024):
        digest.update(data)
    return digest.hexdigest()


def _fingerprints(connection, tables: list[str], journal_watermark: int) -> dict:
    result = {}
    for table in tables:
        omitted = {TIME_COLUMNS[table][1]} if table in TIME_COLUMNS else set()
        columns = [row[1] for row in connection.execute(
            f"PRAGMA table_info({_identifier(table)})"
        ) if row[1] not in omitted]
        names = ",".join(_identifier(column) for column in columns)
        where = " WHERE sequence<=?" if table == "reading_changes" else ""
        parameters = (journal_watermark,) if where else ()
        digest, count = hashlib.sha256(), 0
        for row in connection.execute(
            f"SELECT {names} FROM {_identifier(table)}{where} ORDER BY {names}", parameters,
        ):
            values = [{"sqlite_blob": value.hex()} if isinstance(value, bytes) else value for value in row]
            digest.update(json.dumps(values, separators=(",", ":"), ensure_ascii=True).encode())
            digest.update(b"\n")
            count += 1
        result[table] = {"columns": columns, "rows": count, "sha256": digest.hexdigest()}
    return result


def _plan(connection, legacy_timezone: str) -> list[dict]:
    audit = audit_reading_timestamps(
        (dict(row) for row in connection.execute(
            "SELECT id,timestamp,device_gid,channel_name FROM readings ORDER BY id"
        )), legacy_timezone=legacy_timezone,
    )
    if not audit["candidate_conversion_unblocked"]:
        raise ValueError("Reading timestamps have unresolved interpretations or collisions")
    result = []
    for table, (keys, column) in TIME_COLUMNS.items():
        fields = ",".join(_identifier(name) for name in (*keys, column))
        for row in connection.execute(f"SELECT {fields} FROM {_identifier(table)}"):
            original = row[column]
            if original is None:
                continue
            resolved = classify_timestamp(original, legacy_timezone)
            if resolved["status"] not in ("legacy_unique", "aware"):
                raise ValueError(f"Unresolved timestamp in {table}")
            result.append({
                "table": table, "keys": tuple(row[key] for key in keys),
                "column": column, "original": original,
                "utc": resolved["utc_candidates"][0],
                "interpretation": resolved["status"],
            })
    return result


def _convert(connection, legacy_timezone: str, reporting_timezone: str,
             source_sha256: str) -> dict:
    """Convert all energy timestamp replicas in a single already-owned copy."""
    if (connection.execute("SELECT 1 FROM utc_rehearsal").fetchone() or
            connection.execute("SELECT 1 FROM utc_timestamp_evidence LIMIT 1").fetchone()):
        raise ValueError("Source is already a rehearsal artifact")
    tables = [row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN "
        "('sqlite_sequence','utc_rehearsal','utc_timestamp_evidence') ORDER BY name"
    )]
    watermark = connection.execute(
        "SELECT COALESCE(MAX(sequence),0) FROM reading_changes"
    ).fetchone()[0]
    sequence_before = dict(connection.execute("SELECT name,seq FROM sqlite_sequence"))
    original = _fingerprints(connection, tables, watermark)
    plan = _plan(connection, legacy_timezone)
    updated = Counter()
    for item in plan:
        connection.execute(
            "INSERT INTO utc_timestamp_evidence VALUES (?,?,?,?,?,?)",
            (item["table"], json.dumps(item["keys"], separators=(",", ":")),
             item["column"], item["original"], item["utc"], item["interpretation"]),
        )
        if item["original"] == item["utc"]:
            continue
        keys = TIME_COLUMNS[item["table"]][0]
        where = " AND ".join(f"{_identifier(key)} IS ?" for key in keys)
        changed = connection.execute(
            f"UPDATE {_identifier(item['table'])} SET {_identifier(item['column'])}=? "
            f"WHERE {where} AND {_identifier(item['column'])}=?",
            (item["utc"], *item["keys"], item["original"]),
        ).rowcount
        if changed != 1:
            raise RuntimeError("Timestamp identity changed during conversion")
        updated[item["table"]] += 1

    if _fingerprints(connection, tables, watermark) != original:
        raise RuntimeError("Conversion changed non-timestamp data or original journal identities")
    # Reading UPDATE triggers publish one new canonical upsert per changed ID.
    expected = {item["keys"][0]: item["utc"] for item in plan
                if item["table"] == "readings" and item["original"] != item["utc"]}
    generated = connection.execute(
        "SELECT reading_id,timestamp,operation FROM reading_changes WHERE sequence>?",
        (watermark,),
    ).fetchall()
    if (len(generated) != len(expected) or
            {row["reading_id"]: row["timestamp"] for row in generated} != expected or
            any(row["operation"] != "upsert" for row in generated)):
        raise RuntimeError("Unexpected reading journal updates during conversion")
    sequence_after = dict(connection.execute("SELECT name,seq FROM sqlite_sequence"))
    expected_sequence = {**sequence_before}
    if expected:
        expected_sequence["reading_changes"] = max(
            sequence_before.get("reading_changes", 0), watermark,
        ) + len(expected)
    if sequence_after != expected_sequence:
        raise RuntimeError("Conversion changed unexpected autoincrement identities")
    connection.execute("INSERT INTO utc_rehearsal VALUES (1,?,?,?,?)", (
        source_sha256, legacy_timezone, reporting_timezone,
        datetime.now(timezone.utc).isoformat(timespec="microseconds"),
    ))
    return {
        "purpose": "utc_rehearsal_only", "live_ready": False,
        "source_sha256": source_sha256,
        "legacy_timezone_assumption": legacy_timezone,
        "reporting_timezone": reporting_timezone,
        "timestamp_evidence_rows": len(plan), "updated_rows": dict(updated),
        "preserved_non_timestamp_fingerprints": original,
        "original_journal_watermark": watermark,
        "canonical_upserts_appended": len(expected),
        "sqlite_sequence_verified": True,
    }


def rehearse_utc_copy(snapshot: Path, destination: Path, *, expected_sha256: str,
                      legacy_timezone: str, reporting_timezone: str) -> dict:
    """Publish a private UTC copy, refusing symlinks, drift and overwrites.

    The source must be a verified standalone backup, not a database with live WAL
    or rollback files. Call only as an offline maintenance operation. Source
    provenance and live migration approval remain separate requirements.
    """
    import energy

    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ValueError("Provide the verified snapshot SHA-256")
    ZoneInfo(legacy_timezone)
    ZoneInfo(reporting_timezone)
    snapshot = Path(snapshot).expanduser().absolute()
    destination = Path(destination).expanduser().absolute()
    if snapshot.is_symlink() or destination.is_symlink() or destination.exists():
        raise ValueError("Source must not be a symlink; destination must not exist")
    if destination.parent.stat().st_mode & 0o077:
        raise ValueError("Destination directory must be private (0700)")
    if any(Path(str(snapshot)+suffix).exists() or Path(str(snapshot)+suffix).is_symlink()
           for suffix in ("-wal", "-shm", "-journal")):
        raise ValueError("Use a standalone snapshot, not a live database")
    descriptor = os.open(snapshot, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    temporary = connection = None
    try:
        with os.fdopen(descriptor, "rb") as source:
            metadata = os.fstat(source.fileno())
            if (not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077
                    or metadata.st_nlink != 1):
                raise ValueError("Snapshot must be a private regular file")
            if _digest_file(source) != expected_sha256:
                raise ValueError("Snapshot hash does not match its verified receipt")
            source.seek(0)
            fd, name = tempfile.mkstemp(prefix=".utc-rehearsal-", dir=destination.parent)
            temporary = Path(name)
            with os.fdopen(fd, "wb") as target:
                while chunk := source.read(1024 * 1024):
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            with temporary.open("rb") as copied:
                if _digest_file(copied) != expected_sha256:
                    raise RuntimeError("Snapshot changed during copying")
            connection = energy._connect(temporary)
            if [row[0] for row in connection.execute("PRAGMA integrity_check")] != ["ok"]:
                raise ValueError("Snapshot integrity check failed")
            required = set(TIME_COLUMNS) | {"collector_identity", "reading_stream_generation"}
            existing = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            if not required <= existing:
                raise ValueError("Snapshot requires schema upgrade before UTC rehearsal")
            originals = sorted(existing - {
                "sqlite_sequence", "utc_rehearsal", "utc_timestamp_evidence",
            })
            watermark = connection.execute(
                "SELECT COALESCE(MAX(sequence),0) FROM reading_changes"
            ).fetchone()[0]
            baseline = _fingerprints(connection, originals, watermark)
            journal_count = connection.execute("SELECT COUNT(*) FROM reading_changes").fetchone()[0]
            sequences = dict(connection.execute("SELECT name,seq FROM sqlite_sequence"))
            connection.close()
            connection = None
            energy.ensure_table(temporary)
            connection = energy._connect(temporary)
            connection.execute("BEGIN IMMEDIATE")
            if (_fingerprints(connection, originals, watermark) != baseline or
                    connection.execute("SELECT COUNT(*) FROM reading_changes").fetchone()[0] != journal_count or
                    dict(connection.execute("SELECT name,seq FROM sqlite_sequence")) != sequences):
                raise ValueError("Schema initialization changed archived data; upgrade a copy first")
            result = _convert(connection, legacy_timezone, reporting_timezone, expected_sha256)
            if [row[0] for row in connection.execute("PRAGMA integrity_check")] != ["ok"]:
                raise RuntimeError("Converted snapshot failed integrity checking")
            connection.commit()
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.close()
            connection = None
            source.seek(0)
            current = snapshot.stat(follow_symlinks=False)
            if ((current.st_dev, current.st_ino) != (metadata.st_dev, metadata.st_ino)
                    or _digest_file(source) != expected_sha256):
                raise RuntimeError("Archived snapshot changed during rehearsal")
            with temporary.open("rb") as converted:
                result["artifact_sha256"] = _digest_file(converted)
            os.link(temporary, destination)
            result["source_unchanged"] = True
            return result
    finally:
        if connection is not None:
            connection.rollback()
            connection.close()
        if temporary is not None:
            temporary.unlink(missing_ok=True)
            Path(str(temporary)+"-wal").unlink(missing_ok=True)
            Path(str(temporary)+"-shm").unlink(missing_ok=True)
