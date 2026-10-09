#!/usr/bin/env python3
import fcntl
import json
import logging
import math
import os
import re
import sqlite3
import stat
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pyemvue
import requests
from pyemvue.enums import Scale, Unit

from energy_clock import EnergyClock
from runtime_store import write_private_json
from timestamp_model import ISO_TIMESTAMP, classify_timestamp, reporting_day_bounds

DB_PATH = os.environ.get("DB_PATH", "energy.db")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "60"))
DB_RETENTION_DAYS = int(os.environ.get("DB_RETENTION_DAYS", "365"))
# Minute rows older than this are folded into one row per channel per hour (0 = keep all).
MINUTE_RETENTION_DAYS = int(os.environ.get("MINUTE_RETENTION_DAYS", "30"))
POLLER_STATUS_FILE = os.environ.get("POLLER_STATUS_FILE", "poller_status.json")
RECONNECT_FLAG_FILE = "reconnect.flag"

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

MEASUREMENT_FIELDS = (
    'measurement_seconds', 'measurement_source', 'source_timezone', 'provider_timestamp',
)


def _finite_measurement_number(value) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _validate_measurement_evidence(row: dict) -> None:
    seconds, source = row.get('measurement_seconds'), row.get('measurement_source')
    if source not in (None, 'emporia_minute', 'csv_energy', 'csv_power', 'compacted'):
        raise ValueError('Invalid measurement source')
    if seconds is not None:
        if (not _finite_measurement_number(seconds)
                or seconds <= 0 or source in (None, 'compacted')):
            raise ValueError('Invalid measurement duration evidence')
        if source == 'emporia_minute' and seconds != 60:
            raise ValueError('Emporia minute observations require a 60-second duration')
    zone = row.get('source_timezone')
    if zone is not None:
        if not isinstance(zone, str):
            raise ValueError('Invalid measurement source timezone')
        try:
            ZoneInfo(zone)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError('Invalid measurement source timezone') from exc
    stamp = row.get('provider_timestamp')
    if stamp is not None:
        if not isinstance(stamp, str):
            raise ValueError('Invalid provider timestamp')
        try:
            moment = datetime.fromisoformat(stamp)
        except ValueError as exc:
            raise ValueError('Invalid provider timestamp') from exc
        if moment.tzinfo is None:
            raise ValueError('Provider timestamp must carry an explicit offset')


def reading_average_watts(row: dict) -> float | None:
    """Average over the evidenced measurement interval, never an instantaneous load."""
    try:
        _validate_measurement_evidence(row)
    except ValueError:
        return None
    seconds, kwh = row.get('measurement_seconds'), row.get('usage_kwh')
    if seconds is None or not _finite_measurement_number(kwh):
        return None
    watts = kwh * (3_600_000 / seconds)
    return watts if math.isfinite(watts) else None


def reading_live_watts(row: dict | None, *, now: datetime | None = None,
                       max_age_seconds: int = 180) -> float | None:
    """Recent provider minute average; an import is historical, never a live sample.

    Until coordinated UTC cutover, naive receipts retain host-local interpretation.
    A supplied provider instant must also be fresh; receipt time cannot hide stale API data.
    """
    if not isinstance(row, dict) or row.get('measurement_source') != 'emporia_minute':
        return None
    if row.get('measurement_seconds') != 60:
        return None
    watts = reading_average_watts(row)
    if watts is None:
        return None
    stamps = [row.get('timestamp')]
    if row.get('provider_timestamp') is not None:
        stamps.append(row['provider_timestamp'])
    for stamp in stamps:
        if not isinstance(stamp, str) or not ISO_TIMESTAMP.fullmatch(stamp):
            return None
        try:
            moment = datetime.fromisoformat(stamp)
            reference = now or datetime.now(moment.tzinfo)
            if moment.tzinfo is None:
                reference = reference.astimezone().replace(tzinfo=None) if reference.tzinfo else reference
            else:
                reference = reference.astimezone(timezone.utc)
            age = (reference - moment).total_seconds()
        except (ValueError, OverflowError):
            return None
        if not -60 <= age < max_age_seconds:
            return None
    return watts


def write_poller_status(ok: bool, error: str | None = None, consecutive_errors: int = 0):
    """Write heartbeat file so Flask can monitor poller health."""
    try:
        data = {
            "ok": ok,
            "timestamp": datetime.now().isoformat(),
            "error": error,
            "consecutive_errors": consecutive_errors,
        }
        _write_json_file(POLLER_STATUS_FILE, data)
    except Exception:
        logger.exception("Could not write poller heartbeat")
        return
    conn = None
    try:
        conn = _connect()
        with conn:
            conn.execute('INSERT INTO poller_health_events(timestamp,ok) VALUES (?,?)',
                         (data['timestamp'], int(ok)))
            conn.execute('DELETE FROM poller_health_events WHERE timestamp<?',
                         ((datetime.now() - timedelta(days=DB_RETENTION_DAYS)).isoformat(),))
    except Exception:
        logger.exception("Could not record poller health history")
    finally:
        if conn is not None:
            conn.close()


def read_poller_status() -> dict:
    """Read the heartbeat file from Flask (safe — returns defaults if missing)."""
    try:
        with open(POLLER_STATUS_FILE) as f:
            return json.load(f)
    except Exception:
        return {"ok": None, "timestamp": None, "error": "Status file not found — poller may not be running", "consecutive_errors": 0}

# Rate can come from env, settings.json, or default
def _read_rate_cents() -> float:
    if os.environ.get("RATE_CENTS"):
        return float(os.environ["RATE_CENTS"])
    try:
        with open("settings.json") as f:
            v = json.load(f).get("rate_cents")
            if v is not None:
                return float(v)
    except Exception:
        pass
    return 11.04

RATE_CENTS = _read_rate_cents()

# Channels that represent whole-panel totals — exclude from circuit summaries
# to avoid double-counting individual circuit readings.
META_CHANNELS = frozenset({"Main", "Mains_A", "Mains_B", "Mains_C", "Balance"})

# Device GID that always reports 0 W (secondary/phantom device) — exclude from queries
_GHOST_DEVICE = "81134"

# Daily buckets require actual source-zone calendar bounds, not a fixed 24 hours.
_CSV_FIXED_INTERVAL_SECONDS: dict[str, int] = {
    "1SEC": 1,
    "1MIN": 60,
    "15MIN": 900,
    "1H": 3600,
}


def _chmod_owner_only(path: str | Path) -> None:
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def _write_json_file(path: str | Path, data: dict) -> None:
    write_private_json(path, data)


def _connect(path: str | Path | None = None, *,
             allow_utc_rehearsal: bool = False, read_only: bool = False) -> sqlite3.Connection:
    """Open WAL storage, or explicit read-only maintenance, with row_factory set."""
    if type(allow_utc_rehearsal) is not bool or type(read_only) is not bool:
        raise ValueError("Connection maintenance flags must be booleans")
    target = str(path) if path is not None else DB_PATH
    if read_only:
        target = Path(target).expanduser().absolute().as_uri() + "?mode=ro"
    conn = sqlite3.connect(target, timeout=30, uri=read_only)
    # Until every writer, query and client understands UTC, a converted rehearsal
    # copy must not become a live collector or be interpreted as legacy local data.
    try:
        marked = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='utc_rehearsal'"
        ).fetchone()
        if marked and conn.execute("SELECT 1 FROM utc_rehearsal LIMIT 1").fetchone():
            if not allow_utc_rehearsal or not read_only:
                raise RuntimeError("UTC rehearsal database is not a supported live collector")
        policy_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='energy_time_policy'"
        ).fetchone()
        policy = conn.execute("SELECT timestamp_format,reporting_timezone FROM energy_time_policy").fetchone() if policy_table else None
        if policy:
            if policy[0] != "utc_v1":
                raise RuntimeError("Unsupported energy timestamp policy")
            EnergyClock(policy[1])
            if not allow_utc_rehearsal or not read_only:
                raise RuntimeError("UTC energy policy is not ready for live collection")
        if read_only:
            conn.execute("PRAGMA query_only=ON")
        else:
            conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.row_factory = sqlite3.Row
    except Exception:
        conn.close()
        raise
    return conn


def _utc_clock(conn) -> EnergyClock | None:
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name IN ('energy_time_policy','utc_rehearsal')"
    )}
    policy = conn.execute("SELECT * FROM energy_time_policy").fetchone() if "energy_time_policy" in tables else None
    marker = conn.execute("SELECT * FROM utc_rehearsal").fetchone() if "utc_rehearsal" in tables else None
    if not policy:
        if marker:
            raise RuntimeError("UTC artifact lacks reporting policy; create a new rehearsal")
        return None
    if (policy["timestamp_format"] != "utc_v1" or
            (marker and (policy["reporting_timezone"] != marker["reporting_timezone"] or
                         policy["legacy_timezone"] != marker["legacy_timezone"]))):
        raise RuntimeError("Inconsistent energy timestamp policy")
    clock = EnergyClock(policy["reporting_timezone"])
    for name, function in (("energy_day", clock.day_key), ("energy_month", clock.month_key),
                           ("energy_year", clock.year_key),
                           ("energy_clock_hour", clock.clock_hour),
                           ("energy_weekday", clock.weekday),
                           ("energy_minute", clock.minute_key), ("energy_hour", clock.hour_key)):
        conn.create_function(name, 1, function, deterministic=True)
    return clock


def _query_window(conn, duration: timedelta, now: datetime | None) -> tuple:
    """Use elapsed-time windows; reporting-calendar grouping is separate."""
    clock = _utc_clock(conn)
    moment = now or (datetime.now(timezone.utc) if clock else datetime.now())
    if clock:
        moment = clock.instant(moment)
    serialize = clock.stamp if clock else datetime.isoformat
    return clock, moment, serialize(moment-duration), serialize(moment)


def _chart_rows(rows, key: str, clock: EnergyClock | None) -> list[dict]:
    result = [dict(row) for row in rows]
    if clock and key in ("hour", "period"):
        for row in result:
            row["bucket_utc"] = row[key]
            row["reporting_timezone"] = clock.reporting_timezone
            row[key] = clock.local(clock.parse(row[key])).isoformat(timespec="minutes")
    return result


def backup_database(destination: str | Path) -> dict:
    """Publish a verified, standalone SQLite snapshot without overwriting a file."""
    target = Path(destination).expanduser().absolute()
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"Backup destination already exists: {target}")
    fd, name = tempfile.mkstemp(prefix=".energy-backup-", dir=target.parent)
    os.close(fd)
    temporary = Path(name)
    source = None
    snapshot = None
    try:
        source = _connect()
        snapshot = _connect(temporary)
        source.backup(snapshot, pages=256)
        check = snapshot.execute("PRAGMA integrity_check").fetchall()
        if [row[0] for row in check] != ["ok"]:
            raise RuntimeError("Backup failed SQLite integrity check")
        row = snapshot.execute(
            """SELECT COUNT(*) AS readings, MIN(timestamp) AS first_timestamp,
                      MAX(timestamp) AS last_timestamp FROM readings"""
        ).fetchone()
        manifest = dict(row)
        snapshot.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        snapshot.execute("PRAGMA journal_mode=DELETE")
        snapshot.close()
        snapshot = None
        # Hard-link publication is atomic and fails if another writer creates the target.
        os.link(temporary, target)
        return {"path": str(target), "integrity": "ok", **manifest}
    finally:
        if snapshot is not None:
            snapshot.close()
        if source is not None:
            source.close()
        temporary.unlink(missing_ok=True)
        Path(str(temporary) + "-wal").unlink(missing_ok=True)
        Path(str(temporary) + "-shm").unlink(missing_ok=True)


def get_today_circuit_totals(device_gid: str | None = None, period: str = "day", *,
                             now: datetime | None = None) -> list[dict]:
    """Recorded totals for reporting day, Monday-based week, or month to date."""
    if period not in {"day", "week", "month"}:
        raise ValueError("Invalid cost period")
    conn = _connect()
    try:
        clock = _utc_clock(conn)
        now = now or (datetime.now(timezone.utc) if clock else datetime.now())
        if clock:
            start = clock.period_start(now, period)
            since, until = clock.stamp(start), clock.stamp(now)
        else:
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            if period == "week":
                start -= timedelta(days=start.weekday())
            elif period == "month":
                start = start.replace(day=1)
            since, until = start.isoformat(), now.isoformat()
        gid = _resolve_device_gid(conn.cursor(), device_gid)
        if not gid:
            return []
        placeholders = ",".join("?" for _ in META_CHANNELS)
        rows = conn.execute(
            f"""SELECT channel_name, SUM(usage_kwh) AS total_kwh,
                       SUM(cost_cents) AS total_cents
                FROM readings WHERE device_gid = ? AND timestamp >= ? AND timestamp <= ?
                  AND channel_name NOT IN ({placeholders})
                GROUP BY channel_name ORDER BY total_kwh DESC""",
            (gid, since, until, *META_CHANNELS),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_circuit_week_comparison(
    device_gid: str | None = None, *, end: datetime | None = None,
) -> list[dict]:
    """Compare complete seven-day windows, withholding changes for sparse capture."""
    conn = _connect()
    try:
        clock = _utc_clock(conn)
        if clock:
            start, boundary = clock.complete_days(end or datetime.now(timezone.utc), 14)
            middle = clock.day_bounds(clock.local(boundary).date()-timedelta(days=7))[0]
            minute_expression = "energy_minute(timestamp)"
            bounds = tuple(clock.stamp(moment) for moment in (start, middle, boundary))
        else:
            boundary = (end or datetime.now()).replace(hour=0, minute=0, second=0, microsecond=0)
            middle = boundary-timedelta(days=7)
            start = boundary-timedelta(days=14)
            minute_expression = "strftime('%Y-%m-%dT%H:%M', timestamp)"
            bounds = tuple(moment.isoformat() for moment in (start, middle, boundary))
        gid = _resolve_device_gid(conn.cursor(), device_gid)
        if not gid:
            return []
        rows = conn.execute(
            f"""SELECT channel_name,
                      CASE WHEN timestamp >= ? THEN 'current' ELSE 'previous' END AS period,
                      SUM(usage_kwh) AS kwh,
                      COUNT(DISTINCT {minute_expression}) AS minutes
               FROM readings
               WHERE device_gid = ? AND timestamp >= ? AND timestamp < ?
                 AND usage_kwh IS NOT NULL AND usage_kwh >= 0
               GROUP BY channel_name, period""",
            (bounds[1], gid, bounds[0], bounds[2]),
        ).fetchall()
    finally:
        conn.close()
    channels = {}
    for row in rows:
        name = row["channel_name"]
        if not name or name in META_CHANNELS:
            continue
        channels.setdefault(name, {})[row["period"]] = dict(row)
    result = []
    current_expected = (boundary-middle).total_seconds()/60
    previous_expected = (middle-start).total_seconds()/60
    for name, periods in channels.items():
        current = periods.get("current", {})
        previous = periods.get("previous", {})
        current_coverage = current.get("minutes", 0) / current_expected
        previous_coverage = previous.get("minutes", 0) / previous_expected
        comparable = min(current_coverage, previous_coverage) >= 0.95
        # Even similar totals can be misleading if one week has materially more gaps.
        comparable = comparable and abs(current_coverage - previous_coverage) <= 0.01
        old = previous.get("kwh", 0)
        new = current.get("kwh", 0)
        result.append({
            "channel_name": name,
            "current_kwh": new,
            "previous_kwh": old,
            "current_coverage_pct": current_coverage * 100,
            "previous_coverage_pct": previous_coverage * 100,
            "change_pct": ((new / old - 1) * 100) if comparable and old > 0 else None,
            "comparable": comparable,
            "start": bounds[0], "middle": bounds[1], "end": bounds[2],
            "current_expected_minutes": current_expected,
            "previous_expected_minutes": previous_expected,
        })
    return sorted(result, key=lambda row: row["current_kwh"], reverse=True)


def get_power_heatmap(device_gid: str | None = None, *, end: datetime | None = None) -> dict:
    """Recorded circuit energy in hourly buckets; absent hours are never zero-filled."""
    conn = _connect()
    try:
        clock = _utc_clock(conn)
        if clock:
            grid = clock.hour_grid(end or datetime.now(timezone.utc))
            keys = [row["key"] for row in grid["bins"]]
            hours = [row["hour"] for row in grid["bins"]]
            ticks = [row["tick"] for row in grid["bins"]]
            intervals = [row["minutes"] for row in grid["bins"]]
            groups = grid["day_columns"]
            since, until = grid["start"], grid["end"]
            hour_expression = "energy_hour(timestamp)"
        else:
            boundary = (end or datetime.now()).replace(hour=0, minute=0, second=0, microsecond=0)
            start = boundary-timedelta(days=7)
            hours = [(start+timedelta(hours=i)).strftime('%Y-%m-%dT%H') for i in range(168)]
            keys = hours
            intervals = [60]*len(hours)
            ticks = [hour[-2:] if hour[-2:] in {"00", "06", "12", "18"} else "" for hour in hours]
            groups = [{"label": (start+timedelta(days=i)).strftime('%a %m/%d'), "columns": 24}
                      for i in range(7)]
            since, until = start.isoformat(), boundary.isoformat()
            hour_expression = "strftime('%Y-%m-%dT%H', timestamp)"
        gid = _resolve_device_gid(conn.cursor(), device_gid)
        rows = conn.execute(
            f"""SELECT channel_name, {hour_expression} hour,
                      SUM(usage_kwh) kwh, COUNT(*) samples
               FROM readings WHERE device_gid = ? AND timestamp >= ? AND timestamp < ?
                 AND usage_kwh >= 0
               GROUP BY channel_name, hour""",
            (gid, since, until),
        ).fetchall() if gid else []
    finally:
        conn.close()
    channels = {}
    for row in rows:
        name = row['channel_name']
        if name and name not in META_CHANNELS:
            channels.setdefault(name, {})[row['hour']] = dict(row)
    maximum = max((row['kwh'] for cells in channels.values() for row in cells.values()),
                  default=0) or 1
    return {
        'hours': hours,
        'days': [group['label'] for group in groups], 'day_columns': groups,
        'hour_labels': ticks, 'hour_minutes': intervals,
        'reporting_timezone': clock.reporting_timezone if clock else None,
        'start': since, 'end': until,
        'circuits': [{'name': name, 'cells': [
            ({**cells[hour], 'level': min(5, int(cells[hour]['kwh'] / maximum * 5) + 1)}
             if hour in cells else None) for hour in keys
        ]} for name, cells in sorted(channels.items())],
    }


def _weekly_pattern(rows: list[dict], reporting_timezone: str | None = None) -> dict:
    """Compare repeated well-sampled hours, without filling gaps or summing circuits as mains."""
    patterns = {}
    clock = EnergyClock(reporting_timezone) if reporting_timezone else None
    for row in rows:
        name = row['channel_name']
        if not name or name in META_CHANNELS - {'Main'}:
            continue
        cells = patterns.setdefault(name, [{} for _ in range(168)])
        if 57 <= row['minutes'] <= 60 and row.get('samples', row['minutes']) == row['minutes']:
            moment = datetime.fromisoformat(row['hour'])
            if clock:
                moment = clock.local(moment)
                # Repeated-hour folds are not two independent weekly repetitions.
                # Skip ambiguous/partial wall hours rather than forecasting them.
                if moment.minute or classify_timestamp(
                    moment.replace(tzinfo=None).isoformat(), reporting_timezone,
                )["status"] != "legacy_unique":
                    continue
            day = moment.date()
            observations = cells[moment.weekday()*24+moment.hour]
            observations[day] = row['kwh'] if day not in observations else None
    results = []
    for name, values in sorted(patterns.items()):
        cells = []
        for observations in values:
            samples = [value for value in observations.values() if value is not None]
            cells.append({'kwh': sum(samples) / len(samples), 'low': min(samples),
                          'high': max(samples), 'weeks': len(samples)}
                         if len(samples) >= 2 else None)
        supported = sum(cell is not None for cell in cells)
        results.append({'name': name, 'cells': cells, 'supported': supported,
                        'weekly_kwh': sum(cell['kwh'] for cell in cells) if supported == 168 else None,
                        'low_kwh': sum(cell['low'] for cell in cells) if supported == 168 else None,
                        'high_kwh': sum(cell['high'] for cell in cells) if supported == 168 else None})
    return {'circuits': [row for row in results if row['name'] != 'Main'],
            'main': next((row for row in results if row['name'] == 'Main'), None)}


def get_weekly_power_pattern(device_gid: str | None = None, *, end: datetime | None = None) -> dict:
    """Four-week same-weekday/hour baseline, requiring two >=95%-sampled repetitions."""
    conn = _connect()
    try:
        clock = _utc_clock(conn)
        if clock:
            start, boundary = clock.complete_days(end or datetime.now(timezone.utc), 28)
            since, until = clock.stamp(start), clock.stamp(boundary)
            hour_expression, minute_expression = "energy_hour(timestamp)", "energy_minute(timestamp)"
        else:
            boundary = (end or datetime.now()).replace(hour=0, minute=0, second=0, microsecond=0)
            since, until = (boundary-timedelta(days=28)).isoformat(), boundary.isoformat()
            hour_expression = "strftime('%Y-%m-%dT%H:00:00',timestamp)"
            minute_expression = "strftime('%Y-%m-%dT%H:%M',timestamp)"
        gid = _resolve_device_gid(conn.cursor(), device_gid)
        rows = conn.execute(
            f"""SELECT channel_name, {hour_expression} hour,
                      SUM(usage_kwh) kwh,
                      COUNT(*) samples,
                      COUNT(DISTINCT {minute_expression}) minutes
               FROM readings WHERE device_gid=? AND timestamp>=? AND timestamp<?
                 AND usage_kwh>=0 GROUP BY channel_name,hour""",
            (gid, since, until),
        ).fetchall() if gid else []
    finally:
        conn.close()
    return {**_weekly_pattern([dict(row) for row in rows], clock.reporting_timezone if clock else None),
            'start': since, 'end': until}


def ensure_table(path: str | Path | None = None):
    """Create the readings table and indexes if they don't exist yet."""
    conn = _connect(path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            device_gid TEXT NOT NULL,
            channel_num INTEGER,
            channel_name TEXT,
            usage_kwh REAL,
            cost_cents REAL
        );
        CREATE INDEX IF NOT EXISTS idx_timestamp
            ON readings(timestamp);
        CREATE INDEX IF NOT EXISTS idx_channel_name
            ON readings(channel_name);
        -- Superseded: idx_device_timestamp is a prefix of the unique index below and
        -- idx_device_channel (device, channel_num) is never filtered on.
        DROP INDEX IF EXISTS idx_device_timestamp;
        DROP INDEX IF EXISTS idx_device_channel;
        CREATE INDEX IF NOT EXISTS idx_device_channel_timestamp
            ON readings(device_gid, channel_name, timestamp);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_readings_device_ts_channel
            ON readings(device_gid, timestamp, channel_name);
        CREATE TABLE IF NOT EXISTS migrations (
            name TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS utc_rehearsal (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            source_sha256 TEXT NOT NULL,
            legacy_timezone TEXT NOT NULL,
            reporting_timezone TEXT NOT NULL,
            converted_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS utc_timestamp_evidence (
            table_name TEXT NOT NULL,
            row_key TEXT NOT NULL,
            column_name TEXT NOT NULL,
            original_timestamp TEXT NOT NULL,
            utc_timestamp TEXT NOT NULL,
            interpretation TEXT NOT NULL,
            PRIMARY KEY (table_name, row_key, column_name)
        );
        CREATE TABLE IF NOT EXISTS energy_time_policy (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            timestamp_format TEXT NOT NULL,
            reporting_timezone TEXT NOT NULL,
            legacy_timezone TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS poller_health_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            ok INTEGER NOT NULL CHECK (ok IN (0,1))
        );
        CREATE INDEX IF NOT EXISTS idx_poller_health_timestamp
            ON poller_health_events(timestamp);
        CREATE TABLE IF NOT EXISTS device_capabilities (
            device_gid TEXT PRIMARY KEY,
            service_mode TEXT NOT NULL,
            has_main INTEGER NOT NULL DEFAULT 0,
            has_mains_a INTEGER NOT NULL DEFAULT 0,
            has_mains_b INTEGER NOT NULL DEFAULT 0,
            has_mains_c INTEGER NOT NULL DEFAULT 0,
            mains_c_no_ct INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS latest_channel_snapshot (
            device_gid TEXT NOT NULL,
            channel_name TEXT NOT NULL,
            channel_num TEXT,
            usage_kwh REAL,
            cost_cents REAL,
            timestamp TEXT NOT NULL,
            PRIMARY KEY (device_gid, channel_name)
        );

        CREATE TABLE IF NOT EXISTS climate_sensors (
            source TEXT NOT NULL,
            sensor_id TEXT NOT NULL,
            name TEXT NOT NULL,
            room_id TEXT,
            PRIMARY KEY (source, sensor_id)
        );
        CREATE TABLE IF NOT EXISTS climate_readings (
            source TEXT NOT NULL,
            sensor_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            temperature_c REAL NOT NULL,
            humidity_pct REAL,
            battery_pct REAL,
            PRIMARY KEY (source, sensor_id, timestamp),
            FOREIGN KEY (source, sensor_id) REFERENCES climate_sensors(source, sensor_id)
        );
        CREATE INDEX IF NOT EXISTS idx_climate_timestamp ON climate_readings(timestamp);

        CREATE TABLE IF NOT EXISTS radon_readings (
            source TEXT NOT NULL,
            sensor_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            name TEXT NOT NULL,
            measured_value REAL NOT NULL CHECK (measured_value >= 0),
            measured_unit TEXT NOT NULL CHECK (measured_unit IN ('pCi/L', 'Bq/m3')),
            radon_bq_m3 REAL NOT NULL CHECK (radon_bq_m3 >= 0),
            received_at TEXT NOT NULL,
            PRIMARY KEY (source, sensor_id, timestamp)
        );
        CREATE INDEX IF NOT EXISTS idx_radon_timestamp ON radon_readings(timestamp);
        CREATE TABLE IF NOT EXISTS aqara_local_labels (
            device_id TEXT PRIMARY KEY, name TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS aqara_local_observations (
            device_id TEXT NOT NULL, timestamp TEXT NOT NULL, name TEXT NOT NULL,
            model TEXT, temperature REAL, humidity REAL, battery REAL,
            online INTEGER NOT NULL, source TEXT NOT NULL,
            PRIMARY KEY (device_id,timestamp)
        );
        CREATE INDEX IF NOT EXISTS idx_aqara_local_timestamp ON aqara_local_observations(timestamp);
        CREATE TABLE IF NOT EXISTS kasa_devices (
            id TEXT PRIMARY KEY,
            host TEXT NOT NULL UNIQUE,
            label TEXT NOT NULL,
            reported_device_id TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS kasa_query_metrics (
            device_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            duration_ms REAL,
            brightness INTEGER,
            source TEXT NOT NULL,
            PRIMARY KEY (device_id, timestamp)
        );
        CREATE INDEX IF NOT EXISTS idx_kasa_query_timestamp ON kasa_query_metrics(timestamp);
        CREATE TABLE IF NOT EXISTS kasa_device_tags (
            device_id TEXT PRIMARY KEY,
            tag TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS kasa_observations (
            device_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('ok', 'unavailable')),
            is_on INTEGER CHECK (is_on IN (0, 1)),
            model TEXT,
            alias TEXT,
            error_type TEXT,
            PRIMARY KEY (device_id, timestamp),
            CHECK ((status='ok' AND is_on IS NOT NULL) OR
                   (status='unavailable' AND is_on IS NULL))
        );
        CREATE TABLE IF NOT EXISTS mitsubishi_observations (
            serial TEXT NOT NULL,
            queried_at TEXT NOT NULL,
            snapshot TEXT NOT NULL,
            PRIMARY KEY (serial, queried_at)
        );
        CREATE INDEX IF NOT EXISTS idx_mitsubishi_queried_at
            ON mitsubishi_observations(queried_at);
        CREATE TABLE IF NOT EXISTS kasa_circuit_links (
            device_id TEXT PRIMARY KEY,
            energy_device_gid TEXT NOT NULL,
            channel_name TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS radon_sync_cache_state (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            source_id TEXT NOT NULL,
            generation_id TEXT NOT NULL,
            cursor INTEGER NOT NULL,
            high_watermark INTEGER NOT NULL,
            synchronized_at TEXT
        );
        CREATE TABLE IF NOT EXISTS radon_cached_readings (
            source TEXT NOT NULL,
            sensor_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            name TEXT NOT NULL,
            measured_value REAL NOT NULL,
            measured_unit TEXT NOT NULL,
            radon_bq_m3 REAL NOT NULL,
            received_at TEXT NOT NULL,
            PRIMARY KEY (source, sensor_id, timestamp)
        );

        CREATE TABLE IF NOT EXISTS radon_changes (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            operation TEXT NOT NULL CHECK (operation IN ('upsert', 'delete')),
            source TEXT NOT NULL,
            sensor_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            name TEXT,
            measured_value REAL,
            measured_unit TEXT,
            radon_bq_m3 REAL,
            received_at TEXT
        );
        CREATE TABLE IF NOT EXISTS radon_stream_generation (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            generation_id TEXT NOT NULL
        );
        INSERT OR IGNORE INTO radon_stream_generation VALUES (1, lower(hex(randomblob(16))));
        CREATE TRIGGER IF NOT EXISTS radon_sync_insert AFTER INSERT ON radon_readings
        BEGIN
            INSERT INTO radon_changes(operation, source, sensor_id, timestamp, name,
                measured_value, measured_unit, radon_bq_m3, received_at)
            VALUES ('upsert', NEW.source, NEW.sensor_id, NEW.timestamp, NEW.name,
                NEW.measured_value, NEW.measured_unit, NEW.radon_bq_m3, NEW.received_at);
        END;
        CREATE TRIGGER IF NOT EXISTS radon_sync_update AFTER UPDATE ON radon_readings
        BEGIN
            INSERT INTO radon_changes(operation, source, sensor_id, timestamp)
            SELECT 'delete', OLD.source, OLD.sensor_id, OLD.timestamp
            WHERE OLD.source != NEW.source OR OLD.sensor_id != NEW.sensor_id
                OR OLD.timestamp != NEW.timestamp;
            INSERT INTO radon_changes(operation, source, sensor_id, timestamp, name,
                measured_value, measured_unit, radon_bq_m3, received_at)
            VALUES ('upsert', NEW.source, NEW.sensor_id, NEW.timestamp, NEW.name,
                NEW.measured_value, NEW.measured_unit, NEW.radon_bq_m3, NEW.received_at);
        END;
        CREATE TRIGGER IF NOT EXISTS radon_sync_delete AFTER DELETE ON radon_readings
        BEGIN
            INSERT INTO radon_changes(operation, source, sensor_id, timestamp)
            VALUES ('delete', OLD.source, OLD.sensor_id, OLD.timestamp);
        END;

        CREATE TABLE IF NOT EXISTS collector_identity (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            source_id TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sync_cache_state (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            source_id TEXT NOT NULL,
            cursor INTEGER NOT NULL,
            high_watermark INTEGER NOT NULL,
            synchronized_at TEXT
        );
        CREATE TABLE IF NOT EXISTS sync_cache_generation (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            generation_id TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS reading_stream_generation (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            generation_id TEXT NOT NULL
        );
        INSERT OR IGNORE INTO reading_stream_generation VALUES (1, lower(hex(randomblob(16))));
        CREATE TABLE IF NOT EXISTS sync_cached_readings (
            reading_id INTEGER PRIMARY KEY,
            timestamp TEXT NOT NULL,
            device_gid TEXT NOT NULL,
            channel_num INTEGER,
            channel_name TEXT,
            usage_kwh REAL,
            cost_cents REAL
        );
        CREATE INDEX IF NOT EXISTS idx_sync_cached_device_channel_timestamp
            ON sync_cached_readings(device_gid, channel_name, timestamp);
        INSERT OR IGNORE INTO collector_identity(singleton, source_id)
            VALUES (1, lower(hex(randomblob(16))));
        CREATE TABLE IF NOT EXISTS reading_changes (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            operation TEXT NOT NULL CHECK (operation IN ('upsert', 'delete')),
            reading_id INTEGER NOT NULL,
            timestamp TEXT,
            device_gid TEXT,
            channel_num INTEGER,
            channel_name TEXT,
            usage_kwh REAL,
            cost_cents REAL
        );
        CREATE TRIGGER IF NOT EXISTS readings_sync_insert AFTER INSERT ON readings
        BEGIN
            INSERT INTO reading_changes(operation, reading_id, timestamp, device_gid,
                channel_num, channel_name, usage_kwh, cost_cents)
            VALUES ('upsert', NEW.id, NEW.timestamp, NEW.device_gid, NEW.channel_num,
                NEW.channel_name, NEW.usage_kwh, NEW.cost_cents);
        END;
        CREATE TRIGGER IF NOT EXISTS readings_sync_update AFTER UPDATE ON readings
        BEGIN
            INSERT INTO reading_changes(operation, reading_id, timestamp, device_gid,
                channel_num, channel_name, usage_kwh, cost_cents)
            VALUES ('upsert', NEW.id, NEW.timestamp, NEW.device_gid, NEW.channel_num,
                NEW.channel_name, NEW.usage_kwh, NEW.cost_cents);
        END;
        CREATE TRIGGER IF NOT EXISTS readings_sync_delete AFTER DELETE ON readings
        BEGIN
            INSERT INTO reading_changes(operation, reading_id) VALUES ('delete', OLD.id);
        END;

        -- Panel layout: one row per physical breaker slot
        CREATE TABLE IF NOT EXISTS circuit_labels (
            slot        INTEGER PRIMARY KEY,  -- 1-based physical slot
            channel_name TEXT,               -- matches readings.channel_name (nullable = empty slot)
            label       TEXT,               -- display label override (defaults to channel_name)
            note        TEXT,               -- freeform note, e.g. "Bedroom outlets, 20A"
            amps        INTEGER,            -- breaker size in amps
            poles       INTEGER DEFAULT 1   -- 1 = single-pole (120V), 2 = double-pole (240V)
        );
    """)
    # Add evidence without inferring duration/source for pre-existing rows.
    conn.execute("BEGIN IMMEDIATE")
    for table in ('readings', 'latest_channel_snapshot', 'reading_changes', 'sync_cached_readings'):
        columns = {row[1] for row in conn.execute(f'PRAGMA table_info({table})')}
        for field in MEASUREMENT_FIELDS:
            if field not in columns:
                kind = 'REAL' if field == 'measurement_seconds' else 'TEXT'
                conn.execute(f'ALTER TABLE {table} ADD COLUMN {field} {kind}')
    # Replace old triggers in the same transaction so new journal events retain evidence.
    fields = 'timestamp,device_gid,channel_num,channel_name,usage_kwh,cost_cents,' + ','.join(MEASUREMENT_FIELDS)
    values = ','.join('NEW.' + field for field in fields.split(','))
    for operation in ('insert', 'update'):
        conn.execute(f'DROP TRIGGER IF EXISTS readings_sync_{operation}')
        conn.execute(f"""CREATE TRIGGER readings_sync_{operation} AFTER {operation.upper()} ON readings
            BEGIN INSERT INTO reading_changes(operation,reading_id,{fields})
            VALUES ('upsert',NEW.id,{values}); END""")
    # Seed pre-existing history once, atomically with its migration marker.
    if not conn.execute(
        "SELECT 1 FROM migrations WHERE name = 'radon_sync_seed_v1'"
    ).fetchone():
        conn.execute("""INSERT INTO radon_changes(operation, source, sensor_id, timestamp,
            name, measured_value, measured_unit, radon_bq_m3, received_at)
            SELECT 'upsert', source, sensor_id, timestamp, name, measured_value,
                   measured_unit, radon_bq_m3, received_at FROM radon_readings
            ORDER BY timestamp, source, sensor_id""")
        conn.execute(
            "INSERT INTO migrations(name, applied_at) VALUES (?, ?)",
            ("radon_sync_seed_v1", datetime.now().isoformat()),
        )
    if not conn.execute(
        "SELECT 1 FROM migrations WHERE name = 'reading_sync_seed_v1'"
    ).fetchone():
        conn.execute("""INSERT INTO reading_changes(operation, reading_id, timestamp,
            device_gid, channel_num, channel_name, usage_kwh, cost_cents,
            measurement_seconds,measurement_source,source_timezone,provider_timestamp)
            SELECT 'upsert', id, timestamp, device_gid, channel_num, channel_name,
                   usage_kwh, cost_cents,measurement_seconds,measurement_source,
                   source_timezone,provider_timestamp FROM readings ORDER BY id""")
        conn.execute(
            "INSERT INTO migrations(name, applied_at) VALUES (?, ?)",
            ("reading_sync_seed_v1", datetime.now().isoformat()),
        )
    conn.commit()
    # Migrate: add poles column if it doesn't exist yet (existing DBs)
    try:
        conn.execute("ALTER TABLE circuit_labels ADD COLUMN poles INTEGER DEFAULT 1")
        conn.commit()
    except Exception:
        pass  # column already exists
    conn.commit()
    conn.close()


def get_reading_changes(after: int = 0, limit: int = 500) -> dict:
    """Return a bounded page and collector identity from one consistent read transaction."""
    if isinstance(after, bool) or not isinstance(after, int) or after < 0:
        raise ValueError("after must be a nonnegative integer")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    conn = _connect()
    try:
        conn.execute("BEGIN")
        source_id = conn.execute("SELECT source_id FROM collector_identity").fetchone()[0]
        generation_id = conn.execute("SELECT generation_id FROM reading_stream_generation").fetchone()[0]
        watermark = conn.execute(
            "SELECT COALESCE(MAX(sequence), 0) FROM reading_changes"
        ).fetchone()[0]
        if after > watermark:
            raise ValueError("Cursor is ahead of this collector; a fresh sync is required")
        rows = [dict(row) for row in conn.execute(
            "SELECT * FROM reading_changes WHERE sequence > ? AND sequence <= ? "
            "ORDER BY sequence LIMIT ?", (after, watermark, limit),
        ).fetchall()]
        next_cursor = rows[-1]["sequence"] if rows else after
        return {
            "protocol_version": 2, "source_id": source_id, "generation_id": generation_id, "changes": rows,
            "after_cursor": after,
            "next_cursor": next_cursor, "high_watermark": watermark,
            "has_more": next_cursor < watermark,
        }
    finally:
        conn.close()


def get_sync_cache_status() -> dict:
    conn = _connect()
    try:
        row = conn.execute("""SELECT s.*, g.generation_id FROM sync_cache_state s
            LEFT JOIN sync_cache_generation g ON g.singleton=s.singleton
            WHERE s.singleton=1""").fetchone()
        return dict(row) if row else {"source_id": None, "cursor": 0,
                                     "high_watermark": 0, "synchronized_at": None, "generation_id": None}
    finally:
        conn.close()


def apply_reading_changes(page: dict) -> dict:
    """Apply a validated collector page and its cursor atomically to the isolated cache."""
    if not isinstance(page, dict) or page.get("protocol_version") != 2:
        raise ValueError("Unsupported sync protocol")
    source_id = page.get("source_id")
    if not isinstance(source_id, str) or len(source_id) != 32:
        raise ValueError("Invalid collector identity")
    generation_id = page.get("generation_id")
    if not isinstance(generation_id, str) or len(generation_id) != 32:
        raise ValueError("Invalid stream generation")
    after, next_cursor, watermark = (page.get(key) for key in
                                     ("after_cursor", "next_cursor", "high_watermark"))
    if any(type(value) is not int or value < 0 for value in (after, next_cursor, watermark)):
        raise ValueError("Invalid sync cursors")
    if not after <= next_cursor <= watermark or page.get("has_more") != (next_cursor < watermark):
        raise ValueError("Inconsistent sync cursors")
    changes = page.get("changes")
    if not isinstance(changes, list) or len(changes) > 1000:
        raise ValueError("Invalid sync page size")
    previous = after
    for row in changes:
        if not isinstance(row, dict):
            raise ValueError("Invalid change record")
        sequence, reading_id = row.get("sequence"), row.get("reading_id")
        if type(sequence) is not int or not previous < sequence <= next_cursor:
            raise ValueError("Unordered change records")
        if type(reading_id) is not int or reading_id <= 0:
            raise ValueError("Invalid reading identity")
        if row.get("operation") not in {"upsert", "delete"}:
            raise ValueError("Invalid change operation")
        if row["operation"] == "upsert":
            if not isinstance(row.get("timestamp"), str) or not isinstance(row.get("device_gid"), str):
                raise ValueError("Invalid reading timestamp or device")
            datetime.fromisoformat(row["timestamp"])
            for key in ("usage_kwh", "cost_cents"):
                value = row.get(key)
                if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
                    raise ValueError("Invalid reading measurement")
            _validate_measurement_evidence(row)
            if row.get("channel_name") is not None and not isinstance(row["channel_name"], str):
                raise ValueError("Invalid channel name")
            if row.get("channel_num") is not None and type(row["channel_num"]) not in (int, str):
                raise ValueError("Invalid channel number")
        previous = sequence
    if previous != next_cursor:
        raise ValueError("Page does not reach its advertised cursor")
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        state = conn.execute("SELECT * FROM sync_cache_state WHERE singleton=1").fetchone()
        if state and state["source_id"] != source_id:
            raise ValueError("Collector identity changed; use a fresh cache database")
        generation = conn.execute("SELECT generation_id FROM sync_cache_generation").fetchone()
        if state and (not generation or generation[0] != generation_id):
            raise ValueError("Stream generation changed; a fresh cache snapshot is required")
        if after != (state["cursor"] if state else 0):
            raise ValueError("Sync cursor changed; reload cache state and retry")
        for row in changes:
            if row["operation"] == "delete":
                conn.execute("DELETE FROM sync_cached_readings WHERE reading_id=?", (row["reading_id"],))
            else:
                conn.execute("""INSERT INTO sync_cached_readings(reading_id, timestamp,
                    device_gid, channel_num, channel_name, usage_kwh, cost_cents,
                    measurement_seconds,measurement_source,source_timezone,provider_timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(reading_id) DO UPDATE SET timestamp=excluded.timestamp,
                    device_gid=excluded.device_gid, channel_num=excluded.channel_num,
                    channel_name=excluded.channel_name, usage_kwh=excluded.usage_kwh,
                    cost_cents=excluded.cost_cents,
                    measurement_seconds=excluded.measurement_seconds,
                    measurement_source=excluded.measurement_source,
                    source_timezone=excluded.source_timezone,
                    provider_timestamp=excluded.provider_timestamp""",
                    tuple(row.get(key) for key in ("reading_id", "timestamp", "device_gid",
                                                  "channel_num", "channel_name", "usage_kwh", "cost_cents",
                                                  *MEASUREMENT_FIELDS)))
        synchronized_at = datetime.now().isoformat() if not page["has_more"] else (
            state["synchronized_at"] if state else None
        )
        conn.execute("""INSERT INTO sync_cache_state VALUES (1, ?, ?, ?, ?)
            ON CONFLICT(singleton) DO UPDATE SET source_id=excluded.source_id,
            cursor=excluded.cursor, high_watermark=excluded.high_watermark,
            synchronized_at=excluded.synchronized_at""",
            (source_id, next_cursor, watermark, synchronized_at))
        conn.execute("""INSERT INTO sync_cache_generation VALUES (1, ?)
            ON CONFLICT(singleton) DO UPDATE SET generation_id=excluded.generation_id""", (generation_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return get_sync_cache_status()


def compact_reading_journal(max_entries: int = 1_000_000) -> bool:
    """Bound repeated changes to a current-history checkpoint with a new stream generation."""
    if type(max_entries) is not int or max_entries < 1:
        raise ValueError("max_entries must be positive")
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        changes = conn.execute("SELECT COUNT(*) FROM reading_changes").fetchone()[0]
        current = conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
        if changes <= max(max_entries, current * 2):
            return False
        conn.execute("DELETE FROM reading_changes")
        conn.execute("DELETE FROM sqlite_sequence WHERE name='reading_changes'")
        conn.execute("""INSERT INTO reading_changes(operation, reading_id, timestamp,
            device_gid, channel_num, channel_name, usage_kwh, cost_cents,
            measurement_seconds,measurement_source,source_timezone,provider_timestamp)
            SELECT 'upsert', id, timestamp, device_gid, channel_num, channel_name,
                   usage_kwh, cost_cents,measurement_seconds,measurement_source,
                   source_timezone,provider_timestamp FROM readings ORDER BY id""")
        conn.execute("UPDATE reading_stream_generation SET generation_id=lower(hex(randomblob(16)))")
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_panel_layout() -> list[dict]:
    """Return all circuit_labels rows ordered by slot."""
    conn = _connect()
    rows = conn.execute(
        "SELECT slot, channel_name, label, note, amps, poles FROM circuit_labels ORDER BY slot"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_device_capabilities(
    device_gid: str,
    *,
    service_mode: str,
    has_main: bool,
    has_mains_a: bool,
    has_mains_b: bool,
    has_mains_c: bool,
    mains_c_no_ct: bool,
    source: str,
) -> None:
    conn = _connect()
    _save_device_capabilities_with_conn(
        conn,
        device_gid=device_gid,
        service_mode=service_mode,
        has_main=has_main,
        has_mains_a=has_mains_a,
        has_mains_b=has_mains_b,
        has_mains_c=has_mains_c,
        mains_c_no_ct=mains_c_no_ct,
        source=source,
    )
    conn.commit()
    conn.close()


def _save_device_capabilities_with_conn(
    conn: sqlite3.Connection,
    *,
    device_gid: str,
    service_mode: str,
    has_main: bool,
    has_mains_a: bool,
    has_mains_b: bool,
    has_mains_c: bool,
    mains_c_no_ct: bool,
    source: str,
) -> None:
    existing = conn.execute(
        """SELECT service_mode, has_main, has_mains_a, has_mains_b,
                  has_mains_c, mains_c_no_ct, source
           FROM device_capabilities
           WHERE device_gid = ?""",
        (str(device_gid),),
    ).fetchone()
    if existing:
        if _service_mode_rank(existing["service_mode"]) > _service_mode_rank(service_mode):
            service_mode = existing["service_mode"]
        if existing["source"] == "csv_import" and source == "live_poll":
            source = existing["source"]
        has_main = has_main or bool(existing["has_main"])
        has_mains_a = has_mains_a or bool(existing["has_mains_a"])
        has_mains_b = has_mains_b or bool(existing["has_mains_b"])
        has_mains_c = has_mains_c or bool(existing["has_mains_c"])
        mains_c_no_ct = mains_c_no_ct or bool(existing["mains_c_no_ct"])

    conn.execute(
        """INSERT INTO device_capabilities(
               device_gid, service_mode, has_main, has_mains_a, has_mains_b,
               has_mains_c, mains_c_no_ct, source, updated_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(device_gid) DO UPDATE SET
               service_mode=excluded.service_mode,
               has_main=excluded.has_main,
               has_mains_a=excluded.has_mains_a,
               has_mains_b=excluded.has_mains_b,
               has_mains_c=excluded.has_mains_c,
               mains_c_no_ct=excluded.mains_c_no_ct,
               source=excluded.source,
               updated_at=excluded.updated_at""",
        (
            str(device_gid),
            service_mode,
            int(has_main),
            int(has_mains_a),
            int(has_mains_b),
            int(has_mains_c),
            int(mains_c_no_ct),
            source,
            datetime.now().isoformat(),
        ),
    )


def _upsert_latest_snapshot_with_conn(
    conn: sqlite3.Connection,
    *,
    device_gid: str,
    channel_name: str,
    channel_num,
    usage_kwh: float,
    cost_cents: float,
    timestamp: str,
    measurement_seconds: float | None = None,
    measurement_source: str | None = None,
    source_timezone: str | None = None,
    provider_timestamp: str | None = None,
) -> None:
    _validate_measurement_evidence(dict(
        measurement_seconds=measurement_seconds, measurement_source=measurement_source,
        source_timezone=source_timezone, provider_timestamp=provider_timestamp,
    ))
    conn.execute(
        """INSERT INTO latest_channel_snapshot(
               device_gid, channel_name, channel_num, usage_kwh, cost_cents, timestamp,
               measurement_seconds,measurement_source,source_timezone,provider_timestamp
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(device_gid, channel_name) DO UPDATE SET
               channel_num=excluded.channel_num,
               usage_kwh=excluded.usage_kwh,
               cost_cents=excluded.cost_cents,
               timestamp=excluded.timestamp,
               measurement_seconds=excluded.measurement_seconds,
               measurement_source=excluded.measurement_source,
               source_timezone=excluded.source_timezone,
               provider_timestamp=excluded.provider_timestamp
           WHERE excluded.timestamp >= latest_channel_snapshot.timestamp""",
        (str(device_gid), channel_name, None if channel_num is None else str(channel_num), usage_kwh, cost_cents, timestamp,
         measurement_seconds,measurement_source,source_timezone,provider_timestamp),
    )


def rebuild_latest_channel_snapshot() -> int:
    conn = _connect()
    c = conn.cursor()
    rows = c.execute(
        """SELECT device_gid, channel_name, channel_num, usage_kwh, cost_cents, timestamp,
                  measurement_seconds,measurement_source,source_timezone,provider_timestamp
           FROM (
               SELECT device_gid, channel_name, channel_num, usage_kwh, cost_cents, timestamp,
                      measurement_seconds,measurement_source,source_timezone,provider_timestamp,
                      ROW_NUMBER() OVER (
                          PARTITION BY device_gid, channel_name
                          ORDER BY timestamp DESC, id DESC
                      ) AS rn
               FROM readings
           )
           WHERE rn = 1"""
    ).fetchall()
    c.execute("DELETE FROM latest_channel_snapshot")
    c.executemany(
        """INSERT INTO latest_channel_snapshot(
               device_gid, channel_name, channel_num, usage_kwh, cost_cents, timestamp,
               measurement_seconds,measurement_source,source_timezone,provider_timestamp
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                row["device_gid"],
                row["channel_name"],
                None if row["channel_num"] is None else str(row["channel_num"]),
                row["usage_kwh"],
                row["cost_cents"],
                row["timestamp"],
                *(row[field] for field in MEASUREMENT_FIELDS),
            )
            for row in rows
        ],
    )
    conn.commit()
    conn.close()
    return len(rows)


def get_device_capabilities(device_gid: str | None = None) -> dict | None:
    conn = _connect()
    c = conn.cursor()
    resolved_gid = _resolve_device_gid(c, device_gid)
    if not resolved_gid:
        conn.close()
        return None
    row = c.execute(
        """SELECT device_gid, service_mode, has_main, has_mains_a, has_mains_b,
                  has_mains_c, mains_c_no_ct, source, updated_at
           FROM device_capabilities
           WHERE device_gid = ?""",
        (resolved_gid,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def save_panel_slot(slot: int, channel_name: str | None, label: str | None,
                    note: str | None, amps: int | None, poles: int | None = 1):
    conn = _connect()
    conn.execute("""
        INSERT INTO circuit_labels(slot, channel_name, label, note, amps, poles)
        VALUES(?,?,?,?,?,?)
        ON CONFLICT(slot) DO UPDATE SET
            channel_name=excluded.channel_name,
            label=excluded.label,
            note=excluded.note,
            amps=excluded.amps,
            poles=excluded.poles
    """, (slot, channel_name or None, label or None, note or None, amps or None, poles or 1))
    conn.commit()
    conn.close()


def save_panel_layout(slots: list[dict]) -> None:
    """Save a validated layout in one transaction."""
    conn = _connect()
    try:
        with conn:
            conn.executemany(
                """INSERT INTO circuit_labels(slot, channel_name, label, note, amps, poles)
                   VALUES(?,?,?,?,?,?) ON CONFLICT(slot) DO UPDATE SET
                   channel_name=excluded.channel_name, label=excluded.label,
                   note=excluded.note, amps=excluded.amps, poles=excluded.poles""",
                [(s["slot"], s.get("channel_name") or None, s.get("label") or None,
                  s.get("note") or None, s.get("amps"), s.get("poles", 1)) for s in slots],
            )
    finally:
        conn.close()


# Ensure the table exists as soon as this module is imported.
ensure_table()


def get_known_devices() -> list[str]:
    """Return all distinct device_gids that have readings, sorted."""
    conn = _connect()
    rows = conn.execute(
        "SELECT DISTINCT device_gid FROM readings ORDER BY device_gid"
    ).fetchall()
    conn.close()
    return [r["device_gid"] for r in rows]


def get_active_device_gid(device_gid: str | None = None) -> str | None:
    """Resolve the effective device gid once so callers can reuse it across queries."""
    conn = _connect()
    gid = _resolve_device_gid(conn.cursor(), device_gid)
    conn.close()
    return gid


def get_latest_timestamp(device_gid: str | None = None) -> str | None:
    """Return the newest reading timestamp for the resolved device."""
    conn = _connect()
    c = conn.cursor()
    resolved_gid = _resolve_device_gid(c, device_gid)
    if not resolved_gid:
        conn.close()
        return None
    row = c.execute(
        "SELECT MAX(timestamp) AS latest_timestamp FROM readings WHERE device_gid = ?",
        (resolved_gid,),
    ).fetchone()
    conn.close()
    return row["latest_timestamp"] if row and row["latest_timestamp"] else None


def _resolve_device_gid(c: sqlite3.Cursor, device_gid: str | None = None) -> str | None:
    if device_gid and device_gid != _GHOST_DEVICE:
        return device_gid

    preferred_gid = _load_settings().get("primary_device_gid")
    if preferred_gid and preferred_gid != _GHOST_DEVICE:
        row = c.execute(
            "SELECT 1 FROM readings WHERE device_gid = ? LIMIT 1",
            (preferred_gid,),
        ).fetchone()
        if row:
            return preferred_gid

    row = c.execute(
        """SELECT device_gid
           FROM readings
           WHERE device_gid != ?
           GROUP BY device_gid
           ORDER BY MAX(timestamp) DESC
           LIMIT 1""",
        (_GHOST_DEVICE,),
    ).fetchone()
    return row["device_gid"] if row else None


def get_device_labels() -> dict[str, str]:
    """Return {device_gid: label} from settings.json (missing keys → empty string)."""
    try:
        with open("settings.json") as f:
            return json.load(f).get("device_labels", {})
    except Exception:
        return {}


def save_device_labels(labels: dict[str, str]):
    """Merge device_labels into settings.json."""
    cfg = _load_settings()
    cfg["device_labels"] = {k: v for k, v in labels.items() if isinstance(v, str)}
    _write_json_file("settings.json", cfg)


def migrate_channel_names():
    """
    One-time migration: re-clean any channel_names that still contain
    '(kWatts)' or look like 'Category-CircuitName' from the original
    broken import parser. Safe to call repeatedly — no-ops if already clean.
    """
    conn = _connect()
    rows = conn.execute(
        "SELECT DISTINCT channel_name FROM readings WHERE channel_name IS NOT NULL"
    ).fetchall()
    updates = []
    for row in rows:
        old = row["channel_name"]
        new = _clean_csv_channel_name(old)
        if new != old:
            updates.append((new, old))
    if updates:
        conn.executemany(
            "UPDATE readings SET channel_name = ? WHERE channel_name = ?", updates
        )
        conn.commit()
        logger.info("[migrate] renamed %s channel name(s)", len(updates))
    conn.close()
    return len(updates)


def _load_settings() -> dict:
    try:
        with open("settings.json") as f:
            return json.load(f)
    except Exception:
        return {}


def login_vue():
    vue = pyemvue.PyEmVue()
    token_file = "keys.json"
    cfg      = _load_settings()
    username = os.environ.get("EMPORIA_EMAIL")    or cfg.get("emporia_email")
    password = os.environ.get("EMPORIA_PASSWORD") or cfg.get("emporia_password")

    logger.info("Attempting login with user: %s", username)

    if os.path.exists(token_file):
        with open(token_file) as f:
            data = json.load(f)
            logger.info("Using existing tokens from keys.json")
            vue.login(
                id_token=data.get("idToken"),
                access_token=data.get("accessToken"),
                refresh_token=data.get("refreshToken"),
                token_storage_file=token_file,
            )
    else:
        if not username or not password:
            raise RuntimeError(
                "No keys.json and no credentials available. "
                "Enter your Emporia email & password via the Reconnect panel on /log."
            )
        logger.info("Logging in with username/password...")
        try:
            result = vue.login(
                username=username, password=password, token_storage_file=token_file
            )
        except Exception as e:
            raise RuntimeError(f"Login failed: {type(e).__name__}: {e}") from e
        logger.info("Login result: %s", result)
        if not result:
            raise RuntimeError(
                "Login returned False — check credentials or Emporia API availability."
            )
    if os.path.exists(token_file):
        _chmod_owner_only(token_file)
    return vue


def get_devices_with_channels(vue):
    devices = vue.get_devices()
    device_gids = []
    device_info = {}
    for device in devices:
        if device.device_gid not in device_gids:
            device_gids.append(device.device_gid)
            device_info[device.device_gid] = device
        else:
            device_info[device.device_gid].channels += device.channels
    return device_gids, device_info


def _normalize_channel_name(name: str | None) -> str | None:
    """Normalize live/API channel names before persisting or comparing them."""
    if name is None:
        return None
    return _clean_csv_channel_name(name)


def poll_and_store(vue, device_gids):
    global RATE_CENTS
    RATE_CENTS = _read_rate_cents()
    conn = _connect()
    try:
        c = conn.cursor()

        usage_dict = None
        for attempt in range(1, 4):
            try:
                usage_dict = vue.get_device_list_usage(
                    deviceGids=device_gids,
                    instant=None,
                    scale=Scale.MINUTE.value,
                    unit=Unit.KWH.value,
                )
                break
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                if attempt == 3:
                    raise
                logger.warning("Emporia API %s (attempt %s/3); retrying", type(exc).__name__, attempt)
                time.sleep(2 * attempt)

        now = datetime.now().isoformat()

        for gid, device in usage_dict.items():
            capability = {
                "has_main": False,
                "has_mains_a": False,
                "has_mains_b": False,
                "has_mains_c": False,
            }
            for channelnum, channel in device.channels.items():
                if channel.usage is None:
                    continue
                channel_name = _normalize_channel_name(channel.name)
                if channel_name == "Main":
                    capability["has_main"] = True
                elif channel_name == "Mains_A":
                    capability["has_mains_a"] = True
                elif channel_name == "Mains_B":
                    capability["has_mains_b"] = True
                elif channel_name == "Mains_C":
                    capability["has_mains_c"] = True

                cost = channel.usage * RATE_CENTS
                if (type(channel.usage) not in (int, float) or not math.isfinite(channel.usage)
                        or not math.isfinite(cost)):
                    raise ValueError('Emporia returned nonfinite energy or cost')
                # SDK timestamp is provider observation time, not a proven interval boundary.
                provider_moment = getattr(channel, 'timestamp', None)
                provider_stamp = None
                if isinstance(provider_moment, datetime) and provider_moment.tzinfo is not None:
                    provider_stamp = provider_moment.astimezone(timezone.utc).isoformat()

                c.execute(
                    """INSERT INTO readings
                       (timestamp, device_gid, channel_num, channel_name, usage_kwh, cost_cents,
                        measurement_seconds,measurement_source,source_timezone,provider_timestamp)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (now, gid, channelnum, channel_name, channel.usage, cost,
                     60, 'emporia_minute', None, provider_stamp),
                )
                _upsert_latest_snapshot_with_conn(
                    conn,
                    device_gid=str(gid),
                    channel_name=channel_name,
                    channel_num=channelnum,
                    usage_kwh=channel.usage,
                    cost_cents=cost,
                    timestamp=now,
                    measurement_seconds=60, measurement_source='emporia_minute',
                    source_timezone=None, provider_timestamp=provider_stamp,
                )
            _save_device_capabilities_with_conn(
                conn,
                device_gid=str(gid),
                service_mode=_classify_service_mode(**capability),
                has_main=capability["has_main"],
                has_mains_a=capability["has_mains_a"],
                has_mains_b=capability["has_mains_b"],
                has_mains_c=capability["has_mains_c"],
                mains_c_no_ct=False,
                source="live_poll",
            )

        # Prune old rows to keep the database from growing unboundedly.
        cutoff = (datetime.now() - timedelta(days=DB_RETENTION_DAYS)).isoformat()
        c.execute("DELETE FROM readings WHERE timestamp < ?", (cutoff,))
        global _last_compaction
        compacted = 0
        if time.monotonic() - _last_compaction > 3600:
            _last_compaction = time.monotonic()
            compacted = compact_minute_readings(conn, MINUTE_RETENTION_DAYS)
            conn.commit()
            for report in write_monthly_reports():
                logger.info("Wrote monthly report %s", report)
        if compacted:
            logger.info("Compacted %s minute rows into hourly rows", compacted)

        conn.commit()
        logger.info("[%s] Recorded readings", now)
    finally:
        conn.close()


_last_compaction = float("-inf")  # monotonic seconds; compaction runs at most hourly


def compact_minute_readings(conn, days: int) -> int:
    """Fold minute rows older than `days` into one row per channel per hour, in place.

    Hourly rows reuse the readings table (timestamp = start of hour, usage and cost summed),
    like imported 1H CSV buckets, so existing sum-based queries are unchanged. Only whole
    hours before the cutoff are folded. Returns the number of rows removed. Caller commits.
    """
    if days <= 0:
        return 0
    cutoff = (datetime.now() - timedelta(days=days)).replace(
        minute=0, second=0, microsecond=0).isoformat()
    conn.execute("DROP TABLE IF EXISTS _compact")
    conn.execute(
        """CREATE TEMP TABLE _compact AS
           SELECT device_gid, MIN(channel_num) channel_num, channel_name,
                  substr(timestamp, 1, 13) || ':00:00' ts,
                  SUM(usage_kwh) usage_kwh, SUM(cost_cents) cost_cents, COUNT(*) n
           FROM readings
           WHERE timestamp < ? AND channel_name IS NOT NULL AND usage_kwh IS NOT NULL
           GROUP BY device_gid, channel_name, substr(timestamp, 1, 13)
           HAVING COUNT(*) > 1 OR MIN(timestamp) != substr(MIN(timestamp), 1, 13) || ':00:00'""",
        (cutoff,),
    )
    removed = 0
    if conn.execute("SELECT COUNT(*) FROM _compact").fetchone()[0]:
        removed = conn.execute(
            """DELETE FROM readings WHERE id IN (
                   SELECT r.id FROM readings r JOIN _compact c
                     ON r.device_gid = c.device_gid AND r.channel_name = c.channel_name
                    AND substr(r.timestamp, 1, 13) = substr(c.ts, 1, 13)
                   WHERE r.timestamp < ?)""", (cutoff,)).rowcount
        conn.execute(
            """INSERT INTO readings (timestamp, device_gid, channel_num, channel_name, usage_kwh, cost_cents,
                                  measurement_source)
               SELECT ts, device_gid, channel_num, channel_name, usage_kwh, cost_cents, 'compacted' FROM _compact""")
    conn.execute("DROP TABLE _compact")
    return removed


def get_monthly_costs(months: int = 12, device_gid: str | None = None, *,
                      now: datetime | None = None) -> list[dict]:
    """Per-month whole-home and per-circuit recorded energy and cost, newest first.

    Totals come from Main (so circuits are not double counted). `days_recorded` counts
    distinct calendar days with data so partial months are visible rather than hidden.
    Costs use the rate stored with each reading, not today's rate.
    """
    conn = _connect()
    try:
        gid = _resolve_device_gid(conn.cursor(), device_gid)
        if not gid:
            return []
        clock = _utc_clock(conn)
        now = now or (datetime.now(timezone.utc) if clock else datetime.now())
        first = (clock.local(now) if clock else now).date().replace(day=1)
        for _ in range(max(1, months) - 1):
            first = (first - timedelta(days=1)).replace(day=1)
        since = clock.stamp(clock.day_bounds(first)[0]) if clock else first.isoformat()
        until = clock.stamp(now) if clock else now.isoformat()
        month_expression = "energy_month(timestamp)" if clock else "substr(timestamp, 1, 7)"
        day_expression = "energy_day(timestamp)" if clock else "substr(timestamp, 1, 10)"
        rows = conn.execute(
            f"""SELECT {month_expression} month, channel_name,
                      SUM(usage_kwh) kwh, SUM(cost_cents) cents,
                      COUNT(DISTINCT {day_expression}) days
               FROM readings
               WHERE device_gid = ? AND timestamp >= ? AND timestamp <= ? AND channel_name IS NOT NULL
                 AND channel_name NOT IN ('Mains_A', 'Mains_B', 'Mains_C')
               GROUP BY month, channel_name""",
            (gid, since, until),
        ).fetchall()
    finally:
        conn.close()
    by_month: dict[str, dict] = {}
    for row in rows:
        entry = by_month.setdefault(row["month"], {
            "month": row["month"], "total_kwh": None, "total_cents": None,
            "days_recorded": 0, "circuits": [],
        })
        if row["channel_name"] == "Main":
            entry["total_kwh"], entry["total_cents"] = row["kwh"], row["cents"]
            entry["days_recorded"] = row["days"]
        elif row["channel_name"] != "Balance":
            entry["circuits"].append({"channel_name": row["channel_name"],
                                      "kwh": row["kwh"], "cents": row["cents"]})
    result = sorted(by_month.values(), key=lambda m: m["month"], reverse=True)
    for entry in result:
        entry["circuits"].sort(key=lambda c: c["cents"] or 0, reverse=True)
    return result


def write_monthly_reports(directory: str | None = None, *, now: datetime | None = None) -> list[str]:
    """Write a Markdown cost report for each completed month that has none yet."""
    directory = directory or os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "reports")
    conn = _connect()
    try:
        clock = _utc_clock(conn)
        now = now or (datetime.now(timezone.utc) if clock else datetime.now())
        current = (clock.local(now) if clock else now).strftime("%Y-%m")
    finally:
        conn.close()
    written = []
    for month in get_monthly_costs(12, now=now):
        path = os.path.join(directory, f"energy-{month['month']}.md")
        if month["month"] >= current or os.path.exists(path) or month["total_cents"] is None:
            continue
        lines = [
            f"# Energy report {month['month']}", "",
            f"- Whole home: {month['total_kwh']:.1f} kWh, ${month['total_cents'] / 100:.2f}",
            f"- Days with readings: {month['days_recorded']}", "",
            "| Circuit | kWh | Cost |", "| --- | ---: | ---: |",
        ]
        lines += [f"| {c['channel_name']} | {c['kwh'] or 0:.1f} | ${(c['cents'] or 0) / 100:.2f} |"
                  for c in month["circuits"]]
        lines += ["", "Recorded data only; gaps are not estimated."]
        os.makedirs(directory, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
        written.append(path)
    return written


@contextmanager
def _poller_lock():
    """Keep a stable lock inode; unlinking it would permit a second owner."""
    path = str(Path(DB_PATH).resolve()) + '.poller.lock'
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        metadata = os.fstat(fd)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
                or metadata.st_nlink != 1):
            raise RuntimeError('Unsafe poller lock file')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another Emporia poller owns this database') from None
        os.fchmod(fd, 0o600)
        yield
    finally:
        os.close(fd)


def poll_once() -> None:
    with _poller_lock():
        vue = login_vue()
        device_gids, _ = get_devices_with_channels(vue)
        poll_and_store(vue, device_gids)


def run_continuous() -> None:
    with _poller_lock():
        _run_continuous()


def _run_continuous():
    import sys

    # Force line-buffered output so logs appear immediately even via nohup/launchd
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[union-attr]
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(line_buffering=True)  # type: ignore[union-attr]

    logger.info("Starting continuous polling every %s seconds", POLL_INTERVAL)
    logger.info("Rate: $%.4f/kWh", RATE_CENTS / 100)
    logger.info("Database: %s", DB_PATH)

    # ── Initial login — stay alive even if first login fails ─────────────
    vue = None
    device_gids = []
    consecutive_errors = 0
    last_ok: float | None = None
    last_journal_check = float("-inf")
    MAX_ERRORS_BEFORE_RELOGIN = 3
    RELOGIN_BACKOFF = [30, 60, 120, 300]

    try:
        vue = login_vue()
        device_gids, _ = get_devices_with_channels(vue)
        logger.info("Found %s device(s)", len(device_gids))
        write_poller_status(True, consecutive_errors=0)
    except Exception as e:
        err = f"Startup login failed: {e}"
        logger.exception(err)
        write_poller_status(False, error=err + " — use /log reconnect panel to re-authenticate.",
                            consecutive_errors=0)

    while True:
        # ── Check for a reconnect request from the Flask UI ───────────────
        if os.path.exists(RECONNECT_FLAG_FILE):
            logger.info("Reconnect flag detected — attempting re-login...")
            try:
                os.remove(RECONNECT_FLAG_FILE)
            except Exception:
                pass
            cfg = _load_settings()
            has_password = bool(cfg.get("emporia_password"))
            # Only clear tokens if we have a password to fall back on
            if has_password and os.path.exists("keys.json"):
                try:
                    os.remove("keys.json")
                except Exception:
                    pass
            try:
                vue = login_vue()
                device_gids, _ = get_devices_with_channels(vue)
                logger.info("Reconnected — %s device(s)", len(device_gids))
                consecutive_errors = 0
                write_poller_status(True, consecutive_errors=0)
            except Exception as e:
                err = f"Reconnect failed: {e}"
                logger.exception(err)
                write_poller_status(False, error=err, consecutive_errors=consecutive_errors)

        # Retry startup/discovery failures without discarding saved tokens.
        if vue is None or not device_gids:
            time.sleep(max(30, POLL_INTERVAL))
            try:
                candidate = login_vue()
                discovered, _ = get_devices_with_channels(candidate)
                if not discovered:
                    raise RuntimeError("No Emporia devices discovered")
                vue, device_gids = candidate, discovered
                consecutive_errors = 0
                logger.info("Startup recovery OK — %s device(s)", len(device_gids))
            except Exception as e:
                consecutive_errors += 1
                logger.warning("Startup recovery failed: %s: %s", type(e).__name__, e)
                write_poller_status(False, error=f"Connection retry failed: {type(e).__name__}: {e}",
                                    consecutive_errors=consecutive_errors)
                continue

        try:
            started = time.monotonic()
            poll_and_store(vue, device_gids)
            finished = time.monotonic()
            if finished - last_journal_check >= 3600:
                try:
                    if compact_reading_journal():
                        logger.info("History sync journal checkpointed; clients will refresh their cache")
                    last_journal_check = finished
                except Exception:
                    logger.exception("History sync journal maintenance failed")
            if last_ok is not None and started - last_ok > POLL_INTERVAL * 3:
                logger.warning("Poll gap: %.0fs since last successful poll", started - last_ok)
            logger.info("Poll took %.2fs", finished - started)
            last_ok = finished
            consecutive_errors = 0
            write_poller_status(True, consecutive_errors=0)
        except Exception as e:
            consecutive_errors += 1
            err_str = f"{type(e).__name__}: {e}"
            logger.warning("[Poll error #%s] %s", consecutive_errors, err_str)
            write_poller_status(False, error=err_str, consecutive_errors=consecutive_errors)

            # Auto re-login after enough consecutive failures (expired tokens etc.)
            if consecutive_errors >= MAX_ERRORS_BEFORE_RELOGIN:
                backoff = RELOGIN_BACKOFF[min(consecutive_errors - MAX_ERRORS_BEFORE_RELOGIN,
                                              len(RELOGIN_BACKOFF) - 1)]
                logger.warning("Auto re-login attempt (backoff %ss)…", backoff)
                time.sleep(backoff)
                try:
                    cfg = _load_settings()
                    # Only wipe tokens if we have credentials to re-login with
                    if cfg.get("emporia_password") and os.path.exists("keys.json"):
                        os.remove("keys.json")
                    vue = login_vue()
                    device_gids, _ = get_devices_with_channels(vue)
                    logger.info("Auto re-login OK — %s device(s)", len(device_gids))
                    consecutive_errors = 0
                    write_poller_status(True, consecutive_errors=0)
                except Exception as re_e:
                    reauth_err = f"Auto re-login failed: {re_e}"
                    logger.exception(reauth_err)
                    write_poller_status(False, error=reauth_err,
                                        consecutive_errors=consecutive_errors)

        time.sleep(POLL_INTERVAL)


def get_main_total(hours: int = 24, device_gid: str | None = None, *,
                   now: datetime | None = None) -> dict | None:
    """
    Return the Main channel total kWh and cost_cents for the last `hours` hours
    from the primary real device (551741).  Used for authoritative whole-house
    totals without double-counting individual circuits.
    """
    conn = _connect()
    try:
        c = conn.cursor()
        _, _, since, until = _query_window(conn, timedelta(hours=hours), now)
        resolved_gid = _resolve_device_gid(c, device_gid)
        if not resolved_gid:
            return None
        c.execute(
            """SELECT SUM(usage_kwh) as total_kwh, SUM(cost_cents) as total_cents,
                      COUNT(*) as readings
               FROM readings
               WHERE channel_name = 'Main'
                 AND device_gid = ?
                 AND timestamp >= ? AND timestamp <= ?""",
            (resolved_gid, since, until),
        )
        row = c.fetchone()
        if row and row["total_kwh"] is not None:
            return {"total_kwh": row["total_kwh"], "total_cents": row["total_cents"],
                    "readings": row["readings"], "channel_name": "Main"}
        return None
    finally:
        conn.close()


def get_summary(hours=24, device_gid: str | None = None, *,
                    now: datetime | None = None) -> list[dict]:
    conn = _connect()
    try:
        c = conn.cursor()

        _, _, since, until = _query_window(conn, timedelta(hours=hours), now)
        resolved_gid = _resolve_device_gid(c, device_gid)
        if not resolved_gid:
            return []

        # Exclude the ghost device (device 81134 always reports 0 W, pollutes sums)
        meta_placeholders = ",".join("?" for _ in META_CHANNELS)
        c.execute(
            f"""SELECT channel_name,
            SUM(usage_kwh) as total_kwh,
            SUM(cost_cents) as total_cents,
            COUNT(*) as readings
            FROM readings
            WHERE timestamp >= ? AND timestamp <= ?
              AND device_gid = ?
              AND channel_name NOT IN ({meta_placeholders})
            GROUP BY channel_name
            ORDER BY total_kwh DESC""",
            (since, until, resolved_gid, *META_CHANNELS),
        )

        results = c.fetchall()
        return [dict(row) for row in results]
    finally:
        conn.close()


def get_channel_totals(
    channel_names: list[str], hours: int = 24, device_gid: str | None = None, *,
    now: datetime | None = None,
) -> list[dict]:
    if not channel_names:
        return []

    conn = _connect()
    try:
        c = conn.cursor()
        _, _, since, until = _query_window(conn, timedelta(hours=hours), now)
        resolved_gid = _resolve_device_gid(c, device_gid)
        if not resolved_gid:
            return []

        placeholders = ",".join("?" for _ in channel_names)
        c.execute(
            f"""SELECT channel_name,
                       SUM(usage_kwh) as total_kwh,
                       SUM(cost_cents) as total_cents,
                       COUNT(*) as readings
                FROM readings
                WHERE timestamp >= ? AND timestamp <= ?
                  AND device_gid = ?
                  AND channel_name IN ({placeholders})
                GROUP BY channel_name""",
            (since, until, resolved_gid, *channel_names),
        )
        results = c.fetchall()
        return [dict(row) for row in results]
    finally:
        conn.close()


def get_hourly_data(days=7, device_gid: str | None = None, *,
                    now: datetime | None = None) -> list[dict]:
    conn = _connect()
    try:
        c = conn.cursor()

        clock, _, since, until = _query_window(conn, timedelta(days=days), now)
        resolved_gid = _resolve_device_gid(c, device_gid)
        if not resolved_gid:
            return []

        grouping = "energy_hour(timestamp)" if clock else "strftime('%Y-%m-%d %H:00', timestamp)"
        c.execute(
            f"""SELECT
            {grouping} as hour,
            SUM(usage_kwh) as total_kwh,
            SUM(cost_cents) as total_cents
            FROM readings
            WHERE timestamp >= ? AND timestamp <= ?
              AND channel_name = 'Main'
              AND device_gid = ?
            GROUP BY hour
            ORDER BY hour""",
            (since, until, resolved_gid),
        )

        results = c.fetchall()
        return _chart_rows(results, "hour", clock)
    finally:
        conn.close()


def get_daily_data(days=30, device_gid: str | None = None, *,
                    now: datetime | None = None) -> list[dict]:
    conn = _connect()
    try:
        c = conn.cursor()

        clock, _, since, until = _query_window(conn, timedelta(days=days), now)
        resolved_gid = _resolve_device_gid(c, device_gid)
        if not resolved_gid:
            return []

        grouping = "energy_day(timestamp)" if clock else "strftime('%Y-%m-%d', timestamp)"
        c.execute(
            f"""SELECT
            {grouping} as day,
            SUM(usage_kwh) as total_kwh,
            SUM(cost_cents) as total_cents
            FROM readings
            WHERE timestamp >= ? AND timestamp <= ?
              AND channel_name = 'Main'
              AND device_gid = ?
            GROUP BY day
            ORDER BY day""",
            (since, until, resolved_gid),
        )

        results = c.fetchall()
        return [dict(row) for row in results]
    finally:
        conn.close()


def get_latest(device_gid: str | None = None):
    conn = _connect()
    c = conn.cursor()
    resolved_gid = _resolve_device_gid(c, device_gid)
    if not resolved_gid:
        conn.close()
        return []

    c.execute(
        """SELECT channel_name, channel_num, usage_kwh, cost_cents, timestamp,
                  measurement_seconds,measurement_source,source_timezone,provider_timestamp
           FROM latest_channel_snapshot
           WHERE device_gid = ?
           ORDER BY usage_kwh DESC""",
        (resolved_gid,),
    )
    results = c.fetchall()
    if not results:
        c.execute("""SELECT channel_name, channel_num, usage_kwh, cost_cents, timestamp,
                  measurement_seconds,measurement_source,source_timezone,provider_timestamp
            FROM (
                SELECT channel_name, channel_num, usage_kwh, cost_cents, timestamp,
                  measurement_seconds,measurement_source,source_timezone,provider_timestamp,
                       ROW_NUMBER() OVER (
                           PARTITION BY channel_name
                           ORDER BY timestamp DESC, id DESC
                       ) AS rn
                FROM readings
                WHERE device_gid = ?
            )
            WHERE rn = 1
            ORDER BY usage_kwh DESC""", (resolved_gid,))
        results = c.fetchall()
    conn.close()
    return [dict(row) for row in results]


def get_month_comparison(device_gid: str | None = None, *,
                         now: datetime | None = None) -> dict:
    """Current reporting month vs the previous month, using Main only."""
    conn = _connect()
    try:
        clock, moment, _, until = _query_window(conn, timedelta(0), now)
        local = clock.local(moment) if clock else moment
        first_day = local.date().replace(day=1)
        previous_day = (first_day-timedelta(days=1)).replace(day=1)
        since = (clock.stamp(clock.day_bounds(previous_day)[0]) if clock else
                 datetime.combine(previous_day, datetime.min.time()).isoformat())
        gid = _resolve_device_gid(conn.cursor(), device_gid)
        if not gid:
            return {"this_month": None, "last_month": None}
        grouping = "energy_month(timestamp)" if clock else "strftime('%Y-%m', timestamp)"
        rows = conn.execute(
            f"""SELECT {grouping} month, SUM(usage_kwh) total_kwh,
                       SUM(cost_cents) total_cents FROM readings
                WHERE timestamp>=? AND timestamp<=? AND channel_name='Main' AND device_gid=?
                GROUP BY month""", (since, until, gid),
        ).fetchall()
        months = {row["month"]: dict(row) for row in rows}
        return {"this_month": months.get(first_day.strftime("%Y-%m")),
                "last_month": months.get(previous_day.strftime("%Y-%m"))}
    finally:
        conn.close()


def get_peak_usage(device_gid: str | None = None, *,
                   now: datetime | None = None) -> dict:
    """Hours/weekdays with largest mean stored reading energy (not measured power)."""
    conn = _connect()
    try:
        conn.execute("BEGIN")
        clock, _, since, until = _query_window(conn, timedelta(days=30), now)
        gid = _resolve_device_gid(conn.cursor(), device_gid)
        if not gid:
            return {"peak_hours": [], "peak_days": []}
        hour = "energy_clock_hour(timestamp)" if clock else "strftime('%H', timestamp)"
        weekday = "energy_weekday(timestamp)" if clock else "strftime('%w', timestamp)"
        parameters = (since, until, gid)
        hours = conn.execute(
            f"""SELECT {hour} hour, AVG(usage_kwh) avg_kwh FROM readings
                WHERE timestamp>=? AND timestamp<=? AND device_gid=? AND channel_name='Main'
                GROUP BY hour ORDER BY avg_kwh DESC,hour LIMIT 5""", parameters,
        ).fetchall()
        days = conn.execute(
            f"""SELECT {weekday} day_of_week, AVG(usage_kwh) avg_kwh FROM readings
                WHERE timestamp>=? AND timestamp<=? AND device_gid=? AND channel_name='Main'
                GROUP BY day_of_week ORDER BY avg_kwh DESC,day_of_week LIMIT 5""", parameters,
        ).fetchall()
        day_names = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
        return {"peak_hours": [dict(row) for row in hours],
                "peak_days": [{"day": day_names[int(row['day_of_week'])], "avg_kwh": row['avg_kwh']}
                              for row in days]}
    finally:
        conn.close()


def get_intraday_comparison(device_gid: str | None = None, *,
                            now: datetime | None = None) -> dict:
    """Actual hourly Main energy for today/yesterday; null means unrecorded."""
    conn = _connect()
    try:
        conn.execute("BEGIN")
        clock, moment, _, until = _query_window(conn, timedelta(0), now)
        gid = _resolve_device_gid(conn.cursor(), device_gid)
        if not gid:
            return {"labels": [], "today": [], "yesterday": []}
        if clock:
            day = clock.local(moment).date()
            first = clock.day_bounds(day-timedelta(days=1))[0]
            boundary = clock.day_bounds(day)[1]
            rows = conn.execute(
                """SELECT energy_hour(timestamp) hour, SUM(usage_kwh) total_kwh FROM readings
                   WHERE channel_name='Main' AND device_gid=? AND timestamp>=? AND timestamp<=?
                   GROUP BY hour""", (gid, clock.stamp(first), until),
            ).fetchall()
            totals = {row['hour']: row['total_kwh'] for row in rows}
            bins = clock.buckets(first, boundary, hourly=True)
            result = {"reporting_timezone": clock.reporting_timezone}
            for name, date in (("today", day), ("yesterday", day-timedelta(days=1))):
                selected = [row for row in bins if clock.local(row['start']).date() == date]
                clocks = [clock.local(row['start']).strftime('%H:%M') for row in selected]
                labels = []
                for row, wall in zip(selected, clocks, strict=True):
                    local = clock.local(row['start'])
                    label = str(local.hour % 12 or 12)
                    if local.minute:
                        label += f":{local.minute:02d}"
                    label += "a" if local.hour < 12 else "p"
                    if clocks.count(wall) > 1:
                        label += " " + local.strftime('%z')
                    labels.append(label)
                result[name] = [totals.get(row['key']) for row in selected]
                result[name+'_labels'] = labels
                result[name+'_hours'] = [row['label'] for row in selected]
            result['labels'] = result['today_labels']
            return result
        today = moment.replace(hour=0, minute=0, second=0, microsecond=0)
        yesterday = today-timedelta(days=1)
        rows = conn.execute(
            """SELECT date(timestamp) day_key, CAST(strftime('%H',timestamp) AS INTEGER) hour,
                      SUM(usage_kwh) total_kwh FROM readings
               WHERE channel_name='Main' AND device_gid=? AND timestamp>=? AND timestamp<=?
               GROUP BY day_key,hour""", (gid, yesterday.isoformat(), until),
        ).fetchall()
        result = {'labels': [str(hour % 12 or 12)+('a' if hour < 12 else 'p') for hour in range(24)],
                  'today': [None]*24, 'yesterday': [None]*24}
        for row in rows:
            name = 'today' if row['day_key'] == today.date().isoformat() else 'yesterday'
            result[name][row['hour']] = row['total_kwh']
        return result
    finally:
        conn.close()


def get_peak_24h(device_gid: str | None = None, *,
                 now: datetime | None = None) -> dict:
    """Highest evidenced aligned monitored-circuit interval average in 24h, not instantaneous power."""
    conn = _connect()
    try:
        c = conn.cursor()
        clock, _, since, until = _query_window(conn, timedelta(hours=24), now)
        resolved_gid = _resolve_device_gid(c, device_gid)
        if not resolved_gid:
            return {"peak_watts": None, "peak_time": None, "measurement_seconds": None}
        conn.create_function('energy_average_watts', 3,
            lambda kwh, seconds, source: reading_average_watts(dict(
                usage_kwh=kwh, measurement_seconds=seconds, measurement_source=source,
            )), deterministic=True)
        # Do not sum unknown durations, mixed scales, different source zones or
        # distinct provider instants into a fictitious simultaneous panel load.
        row = conn.execute("""
            SELECT timestamp, SUM(energy_average_watts(usage_kwh,measurement_seconds,
                       measurement_source)) watts, MIN(measurement_seconds) measurement_seconds
            FROM readings
            WHERE timestamp>=? AND timestamp<=? AND device_gid=?
              AND channel_name NOT IN ('Main','Mains_A','Mains_B','Mains_C','Balance')
            GROUP BY timestamp
            HAVING COUNT(*)=COUNT(energy_average_watts(usage_kwh,measurement_seconds,measurement_source))
              AND COUNT(DISTINCT measurement_seconds)=1
              AND COUNT(DISTINCT COALESCE(source_timezone,''))=1
              AND COUNT(DISTINCT COALESCE(provider_timestamp,''))=1
              AND COUNT(DISTINCT CASE WHEN measurement_source='emporia_minute' THEN 'live' ELSE 'csv' END)=1
              AND ABS(watts)<=1.7976931348623157e308
            ORDER BY watts DESC,timestamp DESC LIMIT 1
        """, (since, until, resolved_gid)).fetchone()
        if not row:
            return {"peak_watts": None, "peak_time": None, "measurement_seconds": None}
        watts = row['watts']
        ts = row["timestamp"]
        try:
            dt = clock.local(clock.parse(ts)) if clock else datetime.fromisoformat(ts)
            hour = dt.hour
            label = ("12 AM" if hour == 0 else f"{hour} AM" if hour < 12
                     else "12 PM" if hour == 12 else f"{hour-12} PM")
            time_label = f"{label} ({dt.strftime('%m/%d')})"
            if clock:
                time_label += " " + dt.strftime('%z')
        except ValueError:
            logger.warning("Invalid recorded peak timestamp")
            time_label = ts[:16]
        return {"peak_watts": watts, "peak_time": time_label,
                "measurement_seconds": row["measurement_seconds"]}
    finally:
        conn.close()


def get_circuit_data(channel_name, period="day", device_gid: str | None = None, *,
                     now: datetime | None = None) -> dict:
    conn = _connect()
    try:
        conn.execute("BEGIN")
        c = conn.cursor()

        resolved_gid = _resolve_device_gid(c, device_gid)
        if not resolved_gid:
            return {"data": [], "total": {
                "total_kwh": None,
                "total_cents": None,
                "readings": 0,
                "first_reading": None,
                "last_reading": None,
            }}

        duration, expression = {
            "hour": (timedelta(hours=24), "strftime('%Y-%m-%d %H:00', timestamp)"),
            "day": (timedelta(days=7), "strftime('%Y-%m-%d', timestamp)"),
            "week": (timedelta(days=30), "strftime('%Y-%m-%d', timestamp)"),
            "month": (timedelta(days=365), "strftime('%Y-%m', timestamp)"),
            "year": (timedelta(days=365*3), "strftime('%Y', timestamp)"),
        }.get(period, (timedelta(days=7), "strftime('%Y-%m-%d', timestamp)"))
        clock, _, since, until = _query_window(conn, duration, now)
        group_by = ({"hour": "energy_hour(timestamp)", "month": "energy_month(timestamp)",
                     "year": "energy_year(timestamp)"}.get(period, "energy_day(timestamp)")
                    if clock else expression)

        c.execute(
            f"""SELECT {group_by} as period,
            SUM(usage_kwh) as total_kwh,
            SUM(cost_cents) as total_cents
            FROM readings
            WHERE channel_name = ? AND timestamp >= ? AND timestamp <= ? AND device_gid = ?
            GROUP BY period
            ORDER BY period""",
            (channel_name, since, until, resolved_gid),
        )

        results = c.fetchall()

        # Also get total
        c.execute(
            """SELECT
            SUM(usage_kwh) as total_kwh,
            SUM(cost_cents) as total_cents,
            COUNT(*) as readings,
            MIN(timestamp) as first_reading,
            MAX(timestamp) as last_reading
            FROM readings
            WHERE channel_name = ? AND timestamp >= ? AND timestamp <= ? AND device_gid = ?""",
            (channel_name, since, until, resolved_gid),
        )

        total = dict(c.fetchone())

        return {"data": _chart_rows(results, "period", clock if period == "hour" else None),
                "total": total}
    finally:
        conn.close()


def get_circuit_history(
    channel_name: str, device_gid: str | None = None, now: datetime | None = None,
) -> dict | None:
    """Recorded rolling 1/7/30-day totals and series, scoped to one device.

    Null buckets mean missing data, not zero energy. Trend comparisons require
    dense minute sampling in both equal-length periods. This is a sampling guard,
    not a guarantee of coverage for imported history of unknown intervals.
    """
    conn = _connect()
    try:
        conn.execute("BEGIN")
        clock, now, _, until = _query_window(conn, timedelta(0), now)
        serialize = clock.stamp if clock else datetime.isoformat
        gid = _resolve_device_gid(conn.cursor(), device_gid)
        if not gid:
            return None
        latest = conn.execute(
            """SELECT timestamp,usage_kwh,measurement_seconds,measurement_source,source_timezone,provider_timestamp FROM readings
               WHERE device_gid=? AND channel_name=? AND timestamp<=?
               ORDER BY timestamp DESC LIMIT 1""",
            (gid, channel_name, until),
        ).fetchone()
        if latest is None:
            return None

        def totals(start: datetime, end: datetime) -> dict:
            minute = "energy_minute(timestamp)" if clock else "substr(timestamp,1,16)"
            return dict(conn.execute(
                f"""SELECT SUM(usage_kwh) total_kwh, SUM(cost_cents) total_cents,
                          COUNT(*) readings, COUNT(DISTINCT {minute}) sampled_minutes,
                          MIN(timestamp) first_reading, MAX(timestamp) last_reading
                   FROM readings WHERE device_gid=? AND channel_name=?
                   AND timestamp>=? AND timestamp<? AND usage_kwh IS NOT NULL""",
                (gid, channel_name, serialize(start), serialize(end)),
            ).fetchone())

        windows = []
        for days in (1, 7, 30):
            start = now - timedelta(days=days)
            previous_start = start - timedelta(days=days)
            current, previous = totals(start, now), totals(previous_start, start)
            dense = all(row['sampled_minutes'] >= days * 1440 * 0.8 for row in (current, previous))
            change = _delta_pct(current['total_kwh'], previous['total_kwh']) if dense else None
            grouping = "%Y-%m-%d %H:00" if days == 1 else "%Y-%m-%d"
            expression = ("energy_hour(timestamp)" if days == 1 else "energy_day(timestamp)") if clock else "strftime(?, timestamp)"
            parameters = (gid, channel_name, serialize(start), until)
            buckets = {
                row['period']: dict(row) for row in conn.execute(
                    f"""SELECT {expression} period, SUM(usage_kwh) total_kwh,
                              COUNT(*) readings FROM readings
                       WHERE device_gid=? AND channel_name=? AND timestamp>=?
                       AND timestamp<? AND usage_kwh IS NOT NULL
                       GROUP BY period ORDER BY period""",
                    parameters if clock else (grouping, *parameters),
                ).fetchall()
            }
            series = []
            if clock:
                for bucket in clock.buckets(start, now, hourly=days == 1):
                    row = dict(buckets.get(bucket['key'], {'total_kwh': None, 'readings': 0}))
                    row.update(period=bucket['label'],
                               partial_bucket=bucket['start'] < start or bucket['end'] > now,
                               bucket_utc=clock.stamp(bucket['start']),
                               interval_minutes=(bucket['end']-bucket['start']).total_seconds()/60)
                    series.append(row)
            else:
                cursor = start.replace(minute=0, second=0, microsecond=0) if days == 1 else start.replace(hour=0, minute=0, second=0, microsecond=0)
                step = timedelta(hours=1) if days == 1 else timedelta(days=1)
                while cursor < now:
                    label = cursor.strftime(grouping)
                    series.append({
                        **buckets.get(label, {'period': label, 'total_kwh': None, 'readings': 0}),
                        'partial_bucket': cursor < start or cursor + step > now,
                    })
                    cursor += step
            windows.append({
                **current, 'days': days, 'previous_kwh': previous['total_kwh'],
                'change_pct': change, 'comparison_sampled': dense,
                'series': series,
            })
        return {
            'channel_name': channel_name, 'device_gid': gid,
            'last_reading': latest['timestamp'],
            'live_watts': reading_live_watts(dict(latest), now=now),
            'latest_average_watts': reading_average_watts(dict(latest)),
            'measurement_seconds': latest['measurement_seconds'],
            'windows': windows,
        }
    finally:
        conn.close()


def get_monthly_projection(device_gid: str | None = None, *,
                           now: datetime | None = None) -> dict | None:
    """Most recent recorded reporting month, using Main only and excluding future rows."""
    conn = _connect()
    try:
        clock, _, _, until = _query_window(conn, timedelta(0), now)
        gid = _resolve_device_gid(conn.cursor(), device_gid)
        if not gid:
            return None
        grouping = "energy_month(timestamp)" if clock else "strftime('%Y-%m', timestamp)"
        row = conn.execute(
            f"""SELECT {grouping} month, SUM(usage_kwh) total_kwh,
                       SUM(cost_cents) total_cents FROM readings
                WHERE channel_name='Main' AND device_gid=? AND timestamp<=?
                GROUP BY month ORDER BY month DESC LIMIT 1""", (gid, until),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _delta_pct(a, b):
    """Percent change from b to a. Returns None if b is None/zero."""
    if b is None or b == 0 or a is None:
        return None
    return round((a - b) / b * 100, 1)


def _context_duration(window_minutes: int) -> timedelta:
    if type(window_minutes) is not int or not 1 <= window_minutes <= 525600:
        raise ValueError("Window minutes must be an integer between 1 and 525600")
    return timedelta(minutes=window_minutes)


def _context_with_conn(conn, gid: str | None, clock: EnergyClock | None,
                       moment: datetime, window_minutes: int, channel_name: str) -> dict:
    duration = _context_duration(window_minutes)
    if not isinstance(channel_name, str) or not channel_name:
        raise ValueError("Channel name must be nonempty")
    serialize = clock.stamp if clock else datetime.isoformat
    windows = {'current': (moment, 'available'), 'previous': (moment-duration, 'available')}
    for name, days in (('yesterday', 1), ('last_week', 7), ('last_month', 30)):
        windows[name] = (clock.previous_wall_time(moment, days) if clock else
                         (moment-timedelta(days=days), 'available'))
    values, counts, ranges, statuses = {}, {}, {}, {}
    minute = 'energy_minute(timestamp)' if clock else 'substr(timestamp,1,16)'
    for name, (end, status) in windows.items():
        statuses[name] = status
        if end is None:
            values[name], counts[name], ranges[name] = None, 0, None
            continue
        start = end-duration
        ranges[name] = {'start': serialize(start), 'end': serialize(end)}
        row = conn.execute(
            f"""SELECT SUM(usage_kwh) kwh, COUNT(DISTINCT {minute}) minutes FROM readings
                WHERE device_gid=? AND channel_name=? AND timestamp>? AND timestamp<=?
                  AND usage_kwh IS NOT NULL""",
            (gid, channel_name, serialize(start), serialize(end)),
        ).fetchone() if gid else None
        values[name], counts[name] = (row['kwh'], row['minutes']) if row else (None, 0)
    comparable = {name: counts['current'] >= window_minutes*0.8 and counts[name] >= window_minutes*0.8
                  for name in windows if name != 'current'}
    return {
        'window_minutes': window_minutes, 'current_kwh': values['current'],
        'previous_kwh': values['previous'],
        'yesterday_kwh': values['yesterday'], 'last_week_kwh': values['last_week'],
        'last_month_kwh': values['last_month'],
        'vs_yesterday_pct': _delta_pct(values['current'], values['yesterday']) if comparable['yesterday'] else None,
        'vs_last_week_pct': _delta_pct(values['current'], values['last_week']) if comparable['last_week'] else None,
        'vs_last_month_pct': _delta_pct(values['current'], values['last_month']) if comparable['last_month'] else None,
        'change_pct': _delta_pct(values['current'], values['previous']) if comparable['previous'] else None,
        'comparison_sampled': comparable, 'sampled_minutes': counts,
        'window_ranges': ranges, 'context_status': statuses,
    }


def get_channel_context(channel_name: str, window_minutes: int = 60,
                        device_gid: str | None = None, *, now: datetime | None = None) -> dict:
    """Device-scoped recorded context; last-month reference is thirty calendar days ago."""
    conn = _connect()
    try:
        conn.execute("BEGIN")
        clock, moment, _, _ = _query_window(conn, timedelta(0), now)
        gid = _resolve_device_gid(conn.cursor(), device_gid)
        return _context_with_conn(conn, gid, clock, moment, window_minutes, channel_name)
    finally:
        conn.close()


def get_now_vs_context(window_minutes: int = 60, device_gid: str | None = None, *,
                       now: datetime | None = None) -> dict:
    """Main recorded context plus bounded current circuit and latest-reading snapshots."""
    duration = _context_duration(window_minutes)
    conn = _connect()
    try:
        conn.execute("BEGIN")
        clock, moment, since, until = _query_window(conn, duration, now)
        gid = _resolve_device_gid(conn.cursor(), device_gid)
        result = _context_with_conn(conn, gid, clock, moment, window_minutes, 'Main')
        circuits = conn.execute(
            """SELECT channel_name,SUM(usage_kwh) kwh,SUM(cost_cents) cents FROM readings
               WHERE timestamp>? AND timestamp<=? AND device_gid=?
               GROUP BY channel_name ORDER BY kwh DESC""", (since, until, gid),
        ).fetchall() if gid else []
        # A future snapshot can mask an older valid reading for one channel;
        # fill only missing channels from bounded recorded history.
        latest = conn.execute(
            """SELECT channel_name,channel_num,usage_kwh,timestamp,
                          measurement_seconds,measurement_source,source_timezone,provider_timestamp FROM latest_channel_snapshot
               WHERE device_gid=? AND timestamp<=? ORDER BY usage_kwh DESC""", (gid, until),
        ).fetchall() if gid else []
        names = {row['channel_name'] for row in latest}
        future_snapshot = conn.execute(
            "SELECT 1 FROM latest_channel_snapshot WHERE device_gid=? AND timestamp>? LIMIT 1",
            (gid, until),
        ).fetchone() if gid else None
        history = conn.execute(
            """SELECT channel_name,channel_num,usage_kwh,timestamp,
                          measurement_seconds,measurement_source,source_timezone,provider_timestamp FROM (
                   SELECT channel_name,channel_num,usage_kwh,timestamp,
                          measurement_seconds,measurement_source,source_timezone,provider_timestamp,
                          ROW_NUMBER() OVER(PARTITION BY channel_name ORDER BY timestamp DESC,id DESC) rn
                   FROM readings WHERE device_gid=? AND timestamp<=?)
               WHERE rn=1 ORDER BY usage_kwh DESC""", (gid, until),
        ).fetchall() if gid and (not latest or future_snapshot) else []
        result['circuits'] = [dict(row) for row in circuits]
        result['latest'] = [dict(row) for row in latest]+[dict(row) for row in history if row['channel_name'] not in names]
        result['latest'].sort(key=lambda row: row['usage_kwh'] if row['usage_kwh'] is not None else -math.inf, reverse=True)
        return result
    finally:
        conn.close()


def get_trend(days_back: int = 14, device_gid: str | None = None, *,
              now: datetime | None = None) -> dict:
    """
    Return daily totals for the last `days_back` days plus a simple
    linear trend slope (positive = usage rising, negative = falling).
    Also returns the observed daily average and the best/worst recorded days.
    """
    conn = _connect()
    try:
        c = conn.cursor()
        resolved_gid = _resolve_device_gid(c, device_gid)
        if not resolved_gid:
            return {
                "daily": [],
                "slope": None,
                "avg_kwh": None,
                "best_day": None,
                "worst_day": None,
            }

        clock, _, since, until = _query_window(conn, timedelta(days=days_back), now)
        grouping = "energy_day(timestamp)" if clock else "strftime('%Y-%m-%d', timestamp)"
        c.execute(
            f"""SELECT {grouping} as day,
                      SUM(usage_kwh) as total_kwh,
                      SUM(cost_cents) as total_cents
               FROM readings
               WHERE channel_name = 'Main' AND timestamp >= ? AND timestamp <= ? AND device_gid = ?
               GROUP BY day
               ORDER BY day""",
            (since, until, resolved_gid),
        )
        daily = [dict(r) for r in c.fetchall()]

        if len(daily) < 2:
            return {
                "daily": daily,
                "slope": None,
                "avg_kwh": None,
                "best_day": None,
                "worst_day": None,
            }

        # Preserve elapsed calendar days when capture has gaps.
        n = len(daily)
        first_day = datetime.fromisoformat(daily[0]["day"]).date()
        xs = [(datetime.fromisoformat(row["day"]).date()-first_day).days for row in daily]
        ys = [d["total_kwh"] for d in daily]
        x_mean = sum(xs) / n
        y_mean = sum(ys) / n
        denom = sum((x - x_mean) ** 2 for x in xs)
        slope = (
            sum((xs[i] - x_mean) * (ys[i] - y_mean) for i in range(n)) / denom
            if denom
            else 0
        )

        avg_kwh = y_mean
        best_day = min(daily, key=lambda d: d["total_kwh"])
        worst_day = max(daily, key=lambda d: d["total_kwh"])

        return {
            "daily": daily,
            "slope": round(slope, 4),  # kWh/day change
            "avg_kwh": round(avg_kwh, 3),
            "best_day": best_day,
            "worst_day": worst_day,
        }
    finally:
        conn.close()


def _clean_csv_channel_name(col: str) -> str:
    """
    Normalize an Emporia CSV column header into a clean circuit name.

    Input examples:
      "Barn-Mains_A (kWatts)"              → "Mains_A"
      "Barn-Other-Kitchen Outlets (kWatts)" → "Kitchen Outlets"
      "Barn-Clothes Dryer-Dryer (kWatts)"  → "Dryer"
      "Barn-Water Heater-Water Heater (kWatts)" → "Water Heater"
    """
    name = col.strip()
    # Strip unit suffix: " (kWatts)", " (kWhs)", " (kW)", etc.
    for suffix in (" (kWatts)", " (kWhs)", " (kW)", " (kWh)"):
        if name.endswith(suffix):
            name = name[: -len(suffix)].strip()
            break
    # Strip device prefix — first hyphen-delimited segment that has no spaces
    # e.g. "Barn-..." → strips "Barn-"
    if "-" in name:
        parts = name.split("-", 1)
        if " " not in parts[0]:
            name = parts[1].strip()
    # Strip category prefix — Emporia groups like "Other-", "Pump-", etc.
    # These can contain spaces, so strip unconditionally if a hyphen remains.
    if "-" in name:
        name = name.split("-", 1)[1].strip()
    return name


def _classify_service_mode(
    *,
    has_main: bool,
    has_mains_a: bool,
    has_mains_b: bool,
    has_mains_c: bool,
    mains_c_no_ct: bool = False,
) -> str:
    if has_mains_a and has_mains_b and has_mains_c and not mains_c_no_ct:
        return "three_phase_native"
    if has_mains_a and has_mains_b:
        return "split_phase_native"
    if has_main:
        return "aggregate_only"
    return "unknown"


def _service_mode_rank(mode: str) -> int:
    return {
        "unknown": 0,
        "aggregate_only": 1,
        "split_phase_inferred": 2,
        "split_phase_native": 3,
        "three_phase_native": 4,
    }.get(mode, 0)


def _csv_source_timezone(header: str) -> str | None:
    match = re.fullmatch(r"Time Bucket(?: \(([^()]+)\))?", header.strip())
    if not match:
        raise ValueError("Expected a Time Bucket timestamp header")
    zone = match.group(1)
    if zone is not None:
        try:
            ZoneInfo(zone)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("Invalid declared source timezone") from exc
    return zone


def _csv_interval_seconds(interval: str, moment: datetime, zone: str | None) -> float | None:
    if interval != '1DAY':
        return _CSV_FIXED_INTERVAL_SECONDS.get(interval)
    if zone is None:
        return None
    if moment.time() != datetime.min.time():
        raise ValueError("Daily CSV buckets must start at source-zone midnight")
    start, end = reporting_day_bounds(moment.date(), zone)
    return (end - start).total_seconds()


def _refresh_import_snapshot_with_conn(conn, device_gid: str) -> None:
    """Publish only accepted database values, never an ignored conflicting upload."""
    rows = conn.execute(
        """SELECT device_gid,channel_name,channel_num,usage_kwh,cost_cents,timestamp,
                  measurement_seconds,measurement_source,source_timezone,provider_timestamp
           FROM (SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY channel_name ORDER BY timestamp DESC,id DESC) rn
                 FROM readings WHERE device_gid=? AND channel_name IS NOT NULL)
           WHERE rn=1""", (device_gid,),
    ).fetchall()
    conn.execute("DELETE FROM latest_channel_snapshot WHERE device_gid=?", (device_gid,))
    for row in rows:
        _upsert_latest_snapshot_with_conn(conn, **dict(row))


def import_emporia_csv(
    filepath: str,
    device_gid: str | None = None,
    original_filename: str | None = None,
) -> dict:
    """Validate an export before atomically publishing energy, snapshots and capabilities.

    Power columns require a known interval; energy columns retain their raw kWh.
    Declared zones are validated, and ambiguous/nonexistent wall times are reported
    rather than guessed. Storage remains legacy local until coordinated UTC cutover.
    Measurement duration and declared source-zone evidence are persisted per reading.
    This does not prove non-overlap with other imported resolutions (#135).
    """
    import csv

    path = Path(filepath)
    stem = Path(original_filename).stem if original_filename else path.stem
    gid = str(device_gid) if device_gid is not None else stem.split('-')[0]
    if not gid:
        raise ValueError("CSV device identity must be nonempty")
    interval = stem.split('-')[-1].upper()
    rate = RATE_CENTS
    if not math.isfinite(rate) or rate < 0:
        raise ValueError("Electricity rate must be finite and nonnegative")
    skipped = errors = ambiguous = nonexistent = 0
    capability = dict.fromkeys(('has_main', 'has_mains_a', 'has_mains_b', 'has_mains_c', 'mains_c_no_ct'), False)
    units, durations, rows_to_insert = set(), set(), []
    with path.open(newline='', encoding='utf-8-sig') as handle:
        reader = csv.DictReader(handle)
        headers = reader.fieldnames or []
        if len(headers) < 2 or any(not isinstance(header, str) for header in headers):
            raise ValueError("Empty or invalid CSV headers")
        if len(set(headers)) != len(headers):
            raise ValueError("Duplicate CSV headers are not supported")
        zone = _csv_source_timezone(headers[0])
        columns = []
        for header in headers[1:]:
            match = re.search(r" \((kWatts|kW|kWhs|kWh)\)$", header.strip())
            if not match:
                raise ValueError("Unsupported CSV measurement unit")
            power = match.group(1) in ('kWatts', 'kW')
            if power and interval not in {*_CSV_FIXED_INTERVAL_SECONDS, '1DAY'}:
                raise ValueError("Power-unit CSV requires a recognized filename interval")
            if power and interval == '1DAY' and zone is None:
                raise ValueError("Daily power conversion requires a declared source timezone")
            units.add('kWatts' if power else 'kWhs')
            name = _clean_csv_channel_name(header)
            if not name or any(name == existing[1] for existing in columns):
                raise ValueError("CSV channel names must be nonempty and unique after normalization")
            if name in ('Main', 'Mains_A', 'Mains_B', 'Mains_C'):
                capability[{'Main': 'has_main', 'Mains_A': 'has_mains_a', 'Mains_B': 'has_mains_b', 'Mains_C': 'has_mains_c'}[name]] = True
            columns.append((header, name, power))

        for row in reader:
            if None in row:
                errors += 1
                continue
            stamp = (row.get(headers[0]) or '').strip()
            if not stamp:
                skipped += 1
                continue
            try:
                moment = datetime.strptime(stamp, '%m/%d/%Y %H:%M:%S')
                if zone:
                    status = classify_timestamp(moment.isoformat(), zone)['status']
                    if status != 'legacy_unique':
                        ambiguous += status == 'ambiguous'
                        nonexistent += status == 'nonexistent'
                        errors += 1
                        continue
                duration = _csv_interval_seconds(interval, moment, zone)
            except ValueError:
                errors += 1
                continue
            if duration is not None:
                durations.add(duration)
            for header, name, power in columns:
                value = (row.get(header) or '').strip()
                if not value or value.lower() == 'no ct':
                    if name == 'Mains_C' and value.lower() == 'no ct':
                        capability['mains_c_no_ct'] = True
                    skipped += 1
                    continue
                try:
                    raw = float(value)
                    kwh = raw * (duration / 3600) if power else raw
                    cents = kwh * rate
                    if not all(math.isfinite(number) for number in (raw, kwh, cents)):
                        raise ValueError("Nonfinite energy or cost")
                except (ValueError, TypeError, OverflowError):
                    errors += 1
                    continue
                rows_to_insert.append((moment.isoformat(), gid, None, name, kwh, cents,
                                       duration, 'csv_power' if power else 'csv_energy', zone, None))

    conn = _connect()
    try:
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            inserted = conn.executemany(
                """INSERT OR IGNORE INTO readings
                   (timestamp,device_gid,channel_num,channel_name,usage_kwh,cost_cents,
                    measurement_seconds,measurement_source,source_timezone,provider_timestamp)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""", rows_to_insert,
            ).rowcount if rows_to_insert else 0
            skipped += len(rows_to_insert) - inserted
            if rows_to_insert:
                _refresh_import_snapshot_with_conn(conn, gid)
            _save_device_capabilities_with_conn(
                conn, device_gid=gid,
                service_mode=_classify_service_mode(**capability),
                source='csv_import', **capability,
            )
    finally:
        conn.close()
    duration = next(iter(durations)) if len(durations) == 1 else _CSV_FIXED_INTERVAL_SECONDS.get(interval)
    unit = next(iter(units)) if len(units) == 1 else 'mixed'
    return {
        'imported': inserted, 'skipped': skipped, 'errors': errors,
        'unit': unit, 'interval': interval, 'interval_seconds': duration,
        'source_timezone': zone, 'ambiguous_timestamps': ambiguous,
        'nonexistent_timestamps': nonexistent,
        'conversion_factor': duration / 3600 if unit == 'kWatts' and duration is not None else 1.0 if unit == 'kWhs' else None,
    }


def fix_csv_kwatts_import() -> dict:
    """Deprecated compatibility entrypoint: never guess historical units or mutate data.

    Timestamp precision/device identity cannot prove kW versus kWh or duration.
    Retain previous markers and values; repair requires verified original exports.
    """
    logger.warning("Heuristic CSV correction is disabled; verified source data is required")
    return {'fixed': 0, 'disabled': True}


def backfill_latest_channel_snapshot() -> dict:
    """One-time rebuild of latest_channel_snapshot from existing readings."""
    conn = _connect()
    c = conn.cursor()
    migration_name = "latest_channel_snapshot_backfill_v1"
    already_applied = c.execute(
        "SELECT 1 FROM migrations WHERE name = ?",
        (migration_name,),
    ).fetchone()
    if already_applied:
        conn.close()
        return {"rebuilt": 0}
    conn.close()
    rebuilt = rebuild_latest_channel_snapshot()
    conn = _connect()
    conn.execute(
        "INSERT INTO migrations(name, applied_at) VALUES(?, ?)",
        (migration_name, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
    return {"rebuilt": rebuilt}


def get_capture_history(hours: int, device_gid: str | None = None,
                        now: datetime | None = None) -> list[dict]:
    """Capture over completed elapsed hours, labeled in the persisted reporting zone."""
    if type(hours) is not int or hours not in (24, 48, 168):
        raise ValueError('hours must be 24, 48 or 168')
    conn = _connect()
    try:
        conn.execute("BEGIN")
        clock, moment, _, _ = _query_window(conn, timedelta(0), now)
        end = (clock.parse(clock.hour_key(clock.stamp(moment))) if clock else
               moment.replace(minute=0, second=0, microsecond=0))
        start = end-timedelta(hours=hours)
        serialize = clock.stamp if clock else datetime.isoformat
        if clock:
            bins = clock.buckets(start, end, hourly=True)
            hour, minute = 'energy_hour(timestamp)', 'energy_minute(timestamp)'
        else:
            bins = [{'key': (start+timedelta(hours=index)).isoformat()[:13],
                     'start': start+timedelta(hours=index), 'end': start+timedelta(hours=index+1)}
                    for index in range(hours)]
            hour, minute = 'substr(timestamp,1,13)', 'substr(timestamp,1,16)'
        gid = _resolve_device_gid(conn.cursor(), device_gid)
        rows = conn.execute(
            f"""SELECT {hour} hour, COUNT(DISTINCT {minute}) minutes FROM readings
                WHERE device_gid=? AND channel_name='Main' AND usage_kwh>=0
                  AND timestamp>=? AND timestamp<? GROUP BY hour""",
            (gid, serialize(start), serialize(end)),
        ).fetchall() if gid else []
        health = conn.execute(
            f"""SELECT {hour} hour, COUNT(*) reports,
                       SUM(CASE WHEN ok=0 THEN 1 ELSE 0 END) errors FROM poller_health_events
                WHERE timestamp>=? AND timestamp<? GROUP BY hour""",
            (serialize(start), serialize(end)),
        ).fetchall()
        counts = {row['hour']: row['minutes'] for row in rows}
        health_counts = {row['hour']: dict(row) for row in health}
        result = []
        for bucket in bins:
            lower, upper = max(start, bucket['start']), min(end, bucket['end'])
            expected = (upper-lower).total_seconds()/60
            minutes = min(expected, counts.get(bucket['key'], 0))
            coverage = minutes/expected*100
            reports = health_counts.get(bucket['key'], {})
            result.append({
                'hour': clock.local(lower).isoformat(timespec='minutes') if clock else lower.isoformat(),
                'minutes': minutes, 'expected_minutes': expected,
                'coverage_pct': round(coverage),
                'state': 'dense' if coverage >= 95 else 'partial' if minutes else 'missing',
                'health_reports': reports.get('reports', 0), 'reported_errors': reports.get('errors', 0),
                'partial_bucket': lower != bucket['start'] or upper != bucket['end'],
                **({'bucket_utc': bucket['key'], 'reporting_timezone': clock.reporting_timezone} if clock else {}),
            })
        return result
    finally:
        conn.close()


def get_log_entries(n: int = 200) -> list[dict]:
    """Return the most recent n poll timestamps and total kWh recorded."""
    conn = _connect()
    try:
        clock = _utc_clock(conn)
        rows = conn.execute(
            """SELECT timestamp,
                  SUM(usage_kwh)   as total_kwh,
                  SUM(cost_cents)  as total_cents,
                  COUNT(*)         as channel_count
           FROM readings
           GROUP BY timestamp
           ORDER BY timestamp DESC
           LIMIT ?""",
            (n,),
        ).fetchall()
        result = [dict(row) for row in rows]
        if clock:
            for row in result:
                row['local_timestamp'] = clock.local(clock.parse(row['timestamp'])).isoformat(timespec='seconds')
                row['reporting_timezone'] = clock.reporting_timezone
        return result
    finally:
        conn.close()


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        if sys.argv[1] == "backup":
            if len(sys.argv) != 3:
                raise SystemExit("Usage: python energy.py backup DESTINATION.db")
            print(json.dumps(backup_database(sys.argv[2]), indent=2))
        elif sys.argv[1] == "poll":
            poll_once()
        elif sys.argv[1] == "summary":
            hours = int(sys.argv[2]) if len(sys.argv) > 2 else 24
            for row in get_summary(hours):
                print(
                    f"{row['channel_name']}: {row['total_kwh']:.4f} kWh = ${row['total_cents'] / 100:.2f}"
                )
        elif sys.argv[1] == "hourly":
            days = int(sys.argv[2]) if len(sys.argv) > 2 else 7
            for row in get_hourly_data(days):
                print(
                    f"{row['hour']}: {row['total_kwh']:.4f} kWh = ${row['total_cents'] / 100:.2f}"
                )
        elif sys.argv[1] == "daily":
            days = int(sys.argv[2]) if len(sys.argv) > 2 else 30
            for row in get_daily_data(days):
                print(
                    f"{row['day']}: {row['total_kwh']:.4f} kWh = ${row['total_cents'] / 100:.2f}"
                )
        elif sys.argv[1] == "latest":
            for row in get_latest():
                print(
                    f"{row['channel_name']}: {row['usage_kwh']:.4f} kWh = ${row['cost_cents'] / 100:.2f}"
                )
        else:
            print("Usage: python energy.py {poll|summary|hourly|daily|latest}")
    else:
        run_continuous()
