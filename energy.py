#!/usr/bin/env python3
import json
import logging
import os
import sqlite3
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

import pyemvue
import requests
from pyemvue.enums import Scale, Unit

from runtime_store import write_private_json

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

# kWatts CSV: interval label → minutes per bucket
_KWATTS_INTERVAL_MINUTES: dict[str, float] = {
    "1SEC": 1 / 60,
    "1MIN": 1.0,
    "15MIN": 15.0,
    "1H": 60.0,
    "1DAY": 1440.0,
}


def _chmod_owner_only(path: str | Path) -> None:
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def _write_json_file(path: str | Path, data: dict) -> None:
    write_private_json(path, data)


def _connect(path: str | Path | None = None) -> sqlite3.Connection:
    """Open a WAL-mode SQLite connection with row_factory set."""
    conn = sqlite3.connect(str(path) if path is not None else DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.row_factory = sqlite3.Row
    return conn


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

def get_circuit_week_comparison(
    device_gid: str | None = None, *, end: datetime | None = None,
) -> list[dict]:
    """Compare complete seven-day windows, withholding changes for sparse capture."""
    boundary = (end or datetime.now()).replace(hour=0, minute=0, second=0, microsecond=0)
    middle = boundary - timedelta(days=7)
    start = boundary - timedelta(days=14)
    conn = _connect()
    try:
        gid = _resolve_device_gid(conn.cursor(), device_gid)
        if not gid:
            return []
        rows = conn.execute(
            """SELECT channel_name,
                      CASE WHEN timestamp >= ? THEN 'current' ELSE 'previous' END AS period,
                      SUM(usage_kwh) AS kwh,
                      COUNT(DISTINCT strftime('%Y-%m-%dT%H:%M', timestamp)) AS minutes
               FROM readings
               WHERE device_gid = ? AND timestamp >= ? AND timestamp < ?
                 AND usage_kwh IS NOT NULL AND usage_kwh >= 0
               GROUP BY channel_name, period""",
            (middle.isoformat(), gid, start.isoformat(), boundary.isoformat()),
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
    expected_minutes = 7 * 24 * 60
    for name, periods in channels.items():
        current = periods.get("current", {})
        previous = periods.get("previous", {})
        current_coverage = current.get("minutes", 0) / expected_minutes
        previous_coverage = previous.get("minutes", 0) / expected_minutes
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
            "start": start.isoformat(),
            "middle": middle.isoformat(),
            "end": boundary.isoformat(),
        })
    return sorted(result, key=lambda row: row["current_kwh"], reverse=True)


def ensure_table():
    """Create the readings table and indexes if they don't exist yet."""
    conn = _connect()
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

        CREATE TABLE IF NOT EXISTS collector_identity (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            source_id TEXT NOT NULL
        );
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
    # Seed pre-existing history once, atomically with its migration marker.
    conn.execute("BEGIN IMMEDIATE")
    if not conn.execute(
        "SELECT 1 FROM migrations WHERE name = 'reading_sync_seed_v1'"
    ).fetchone():
        conn.execute("""INSERT INTO reading_changes(operation, reading_id, timestamp,
            device_gid, channel_num, channel_name, usage_kwh, cost_cents)
            SELECT 'upsert', id, timestamp, device_gid, channel_num, channel_name,
                   usage_kwh, cost_cents FROM readings ORDER BY id""")
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
            "protocol_version": 1, "source_id": source_id, "changes": rows,
            "next_cursor": next_cursor, "high_watermark": watermark,
            "has_more": next_cursor < watermark,
        }
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
) -> None:
    conn.execute(
        """INSERT INTO latest_channel_snapshot(
               device_gid, channel_name, channel_num, usage_kwh, cost_cents, timestamp
           ) VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(device_gid, channel_name) DO UPDATE SET
               channel_num=excluded.channel_num,
               usage_kwh=excluded.usage_kwh,
               cost_cents=excluded.cost_cents,
               timestamp=excluded.timestamp
           WHERE excluded.timestamp >= latest_channel_snapshot.timestamp""",
        (str(device_gid), channel_name, None if channel_num is None else str(channel_num), usage_kwh, cost_cents, timestamp),
    )


def rebuild_latest_channel_snapshot() -> int:
    conn = _connect()
    c = conn.cursor()
    rows = c.execute(
        """SELECT device_gid, channel_name, channel_num, usage_kwh, cost_cents, timestamp
           FROM (
               SELECT device_gid, channel_name, channel_num, usage_kwh, cost_cents, timestamp,
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
               device_gid, channel_name, channel_num, usage_kwh, cost_cents, timestamp
           ) VALUES (?, ?, ?, ?, ?, ?)""",
        [
            (
                row["device_gid"],
                row["channel_name"],
                None if row["channel_num"] is None else str(row["channel_num"]),
                row["usage_kwh"],
                row["cost_cents"],
                row["timestamp"],
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
                conn.close()
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

            c.execute(
                """INSERT INTO readings
                   (timestamp, device_gid, channel_num, channel_name, usage_kwh, cost_cents)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (now, gid, channelnum, channel_name, channel.usage, cost),
            )
            _upsert_latest_snapshot_with_conn(
                conn,
                device_gid=str(gid),
                channel_name=channel_name,
                channel_num=channelnum,
                usage_kwh=channel.usage,
                cost_cents=cost,
                timestamp=now,
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
    conn.close()
    logger.info("[%s] Recorded readings", now)


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
            """INSERT INTO readings (timestamp, device_gid, channel_num, channel_name, usage_kwh, cost_cents)
               SELECT ts, device_gid, channel_num, channel_name, usage_kwh, cost_cents FROM _compact""")
    conn.execute("DROP TABLE _compact")
    return removed


def get_monthly_costs(months: int = 12, device_gid: str | None = None) -> list[dict]:
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
        first = datetime.now().replace(day=1)
        for _ in range(max(1, months) - 1):
            first = (first - timedelta(days=1)).replace(day=1)
        since = first.strftime("%Y-%m-01")
        rows = conn.execute(
            """SELECT substr(timestamp, 1, 7) month, channel_name,
                      SUM(usage_kwh) kwh, SUM(cost_cents) cents,
                      COUNT(DISTINCT substr(timestamp, 1, 10)) days
               FROM readings
               WHERE device_gid = ? AND timestamp >= ? AND channel_name IS NOT NULL
                 AND channel_name NOT IN ('Mains_A', 'Mains_B', 'Mains_C')
               GROUP BY month, channel_name""",
            (gid, since),
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


def write_monthly_reports(directory: str | None = None) -> list[str]:
    """Write a Markdown cost report for each completed month that has none yet."""
    directory = directory or os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "reports")
    current = datetime.now().strftime("%Y-%m")
    written = []
    for month in get_monthly_costs(12):
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


def run_continuous():
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

        # ── Normal poll (skip if no client yet) ───────────────────────────
        if vue is None or not device_gids:
            write_poller_status(False,
                                error="Waiting for credentials — use /log reconnect panel.",
                                consecutive_errors=consecutive_errors)
            time.sleep(POLL_INTERVAL)
            continue

        try:
            started = time.monotonic()
            poll_and_store(vue, device_gids)
            finished = time.monotonic()
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


def get_main_total(hours: int = 24, device_gid: str | None = None) -> dict | None:
    """
    Return the Main channel total kWh and cost_cents for the last `hours` hours
    from the primary real device (551741).  Used for authoritative whole-house
    totals without double-counting individual circuits.
    """
    conn = _connect()
    c = conn.cursor()
    since = (datetime.now() - timedelta(hours=hours)).isoformat()
    resolved_gid = _resolve_device_gid(c, device_gid)
    if not resolved_gid:
        conn.close()
        return None
    c.execute(
        """SELECT SUM(usage_kwh) as total_kwh, SUM(cost_cents) as total_cents,
                  COUNT(*) as readings
           FROM readings
           WHERE channel_name = 'Main'
             AND device_gid = ?
             AND timestamp >= ?""",
        (resolved_gid, since),
    )
    row = c.fetchone()
    conn.close()
    if row and row["total_kwh"] is not None:
        return {"total_kwh": row["total_kwh"], "total_cents": row["total_cents"],
                "readings": row["readings"], "channel_name": "Main"}
    return None


def get_summary(hours=24, device_gid: str | None = None):
    conn = _connect()
    c = conn.cursor()

    since = (datetime.now() - timedelta(hours=hours)).isoformat()
    resolved_gid = _resolve_device_gid(c, device_gid)
    if not resolved_gid:
        conn.close()
        return []

    # Exclude the ghost device (device 81134 always reports 0 W, pollutes sums)
    meta_placeholders = ",".join("?" for _ in META_CHANNELS)
    c.execute(
        f"""SELECT channel_name,
        SUM(usage_kwh) as total_kwh,
        SUM(cost_cents) as total_cents,
        COUNT(*) as readings
        FROM readings
        WHERE timestamp >= ?
          AND device_gid = ?
          AND channel_name NOT IN ({meta_placeholders})
        GROUP BY channel_name
        ORDER BY total_kwh DESC""",
        (since, resolved_gid, *META_CHANNELS),
    )

    results = c.fetchall()
    conn.close()
    return [dict(row) for row in results]


def get_channel_totals(
    channel_names: list[str], hours: int = 24, device_gid: str | None = None
) -> list[dict]:
    if not channel_names:
        return []

    conn = _connect()
    c = conn.cursor()
    since = (datetime.now() - timedelta(hours=hours)).isoformat()
    resolved_gid = _resolve_device_gid(c, device_gid)
    if not resolved_gid:
        conn.close()
        return []

    placeholders = ",".join("?" for _ in channel_names)
    c.execute(
        f"""SELECT channel_name,
                   SUM(usage_kwh) as total_kwh,
                   SUM(cost_cents) as total_cents,
                   COUNT(*) as readings
            FROM readings
            WHERE timestamp >= ?
              AND device_gid = ?
              AND channel_name IN ({placeholders})
            GROUP BY channel_name""",
        (since, resolved_gid, *channel_names),
    )
    results = c.fetchall()
    conn.close()
    return [dict(row) for row in results]


def get_hourly_data(days=7, device_gid: str | None = None):
    conn = _connect()
    c = conn.cursor()

    since = (datetime.now() - timedelta(days=days)).isoformat()
    resolved_gid = _resolve_device_gid(c, device_gid)
    if not resolved_gid:
        conn.close()
        return []

    c.execute(
        """SELECT 
        strftime('%Y-%m-%d %H:00', timestamp) as hour,
        SUM(usage_kwh) as total_kwh,
        SUM(cost_cents) as total_cents
        FROM readings
        WHERE timestamp >= ?
          AND channel_name = 'Main'
          AND device_gid = ?
        GROUP BY hour
        ORDER BY hour""",
        (since, resolved_gid),
    )

    results = c.fetchall()
    conn.close()
    return [dict(row) for row in results]


def get_daily_data(days=30, device_gid: str | None = None):
    conn = _connect()
    c = conn.cursor()

    since = (datetime.now() - timedelta(days=days)).isoformat()
    resolved_gid = _resolve_device_gid(c, device_gid)
    if not resolved_gid:
        conn.close()
        return []

    c.execute(
        """SELECT 
        strftime('%Y-%m-%d', timestamp) as day,
        SUM(usage_kwh) as total_kwh,
        SUM(cost_cents) as total_cents
        FROM readings
        WHERE timestamp >= ?
          AND channel_name = 'Main'
          AND device_gid = ?
        GROUP BY day
        ORDER BY day""",
        (since, resolved_gid),
    )

    results = c.fetchall()
    conn.close()
    return [dict(row) for row in results]


def get_latest(device_gid: str | None = None):
    conn = _connect()
    c = conn.cursor()
    resolved_gid = _resolve_device_gid(c, device_gid)
    if not resolved_gid:
        conn.close()
        return []

    c.execute(
        """SELECT channel_name, channel_num, usage_kwh, cost_cents, timestamp
           FROM latest_channel_snapshot
           WHERE device_gid = ?
           ORDER BY usage_kwh DESC""",
        (resolved_gid,),
    )
    results = c.fetchall()
    if not results:
        c.execute("""SELECT channel_name, channel_num, usage_kwh, cost_cents, timestamp
            FROM (
                SELECT channel_name, channel_num, usage_kwh, cost_cents, timestamp,
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


def get_month_comparison(device_gid: str | None = None):
    """Compare current month to previous month using Main channel only (avoids double-counting)."""
    conn = _connect()
    c = conn.cursor()

    now = datetime.now()
    # First day of this month
    this_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # First day of last month
    last_month_start = (this_month_start - timedelta(days=1)).replace(day=1)

    resolved_gid = _resolve_device_gid(c, device_gid)
    if not resolved_gid:
        conn.close()
        return {"this_month": None, "last_month": None}

    c.execute(
        """SELECT
        strftime('%Y-%m', timestamp) as month,
        SUM(usage_kwh) as total_kwh,
        SUM(cost_cents) as total_cents
        FROM readings
        WHERE timestamp >= ?
          AND channel_name = 'Main'
          AND device_gid = ?
        GROUP BY month""",
        (last_month_start.isoformat(), resolved_gid),
    )

    results = c.fetchall()
    conn.close()

    months = {row["month"]: dict(row) for row in results}

    this_month_key = now.strftime("%Y-%m")
    last_month_key = last_month_start.strftime("%Y-%m")

    return {
        "this_month": months.get(this_month_key),
        "last_month": months.get(last_month_key),
    }


def get_peak_usage(device_gid: str | None = None):
    """Find peak usage times."""
    conn = _connect()
    c = conn.cursor()
    since = (datetime.now() - timedelta(days=30)).isoformat()
    resolved_gid = _resolve_device_gid(c, device_gid)
    if not resolved_gid:
        conn.close()
        return {"peak_hours": [], "peak_days": []}

    c.execute("""SELECT 
        strftime('%H', timestamp) as hour,
        AVG(usage_kwh) as avg_kwh
        FROM readings
        WHERE timestamp >= ?
          AND device_gid = ?
          AND channel_name = 'Main'
        GROUP BY hour
        ORDER BY avg_kwh DESC
        LIMIT 5""", (since, resolved_gid))

    peak_hours = c.fetchall()

    c.execute("""SELECT 
        strftime('%w', timestamp) as day_of_week,
        AVG(usage_kwh) as avg_kwh
        FROM readings
        WHERE timestamp >= ?
          AND device_gid = ?
          AND channel_name = 'Main'
        GROUP BY day_of_week
        ORDER BY avg_kwh DESC
        LIMIT 5""", (since, resolved_gid))

    peak_days = c.fetchall()
    conn.close()

    day_names = [
        "Sunday",
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
    ]

    return {
        "peak_hours": [dict(row) for row in peak_hours],
        "peak_days": [
            {"day": day_names[int(row["day_of_week"])], "avg_kwh": row["avg_kwh"]}
            for row in peak_days
        ],
    }


def get_intraday_comparison(device_gid: str | None = None) -> dict:
    """Return hourly Main-channel totals for today vs yesterday in local time."""
    conn = _connect()
    c = conn.cursor()
    resolved_gid = _resolve_device_gid(c, device_gid)
    if not resolved_gid:
        conn.close()
        return {"labels": [], "today": [], "yesterday": []}

    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today_start - timedelta(days=1)
    tomorrow_start = today_start + timedelta(days=1)

    c.execute(
        """SELECT date(timestamp) as day_key,
                  CAST(strftime('%H', timestamp) AS INTEGER) as hour_num,
                  SUM(usage_kwh) as total_kwh
           FROM readings
           WHERE channel_name = 'Main'
             AND device_gid = ?
             AND timestamp >= ?
             AND timestamp < ?
           GROUP BY day_key, hour_num""",
        (resolved_gid, yesterday_start.isoformat(), tomorrow_start.isoformat()),
    )
    rows = c.fetchall()
    conn.close()

    today_key = today_start.date().isoformat()
    yesterday_key = yesterday_start.date().isoformat()
    today = [0.0] * 24
    yesterday = [0.0] * 24
    for row in rows:
        hour = row["hour_num"]
        if row["day_key"] == today_key:
            today[hour] = row["total_kwh"] or 0.0
        elif row["day_key"] == yesterday_key:
            yesterday[hour] = row["total_kwh"] or 0.0

    labels = []
    for hour in range(24):
        if hour == 0:
            labels.append("12a")
        elif hour < 12:
            labels.append(f"{hour}a")
        elif hour == 12:
            labels.append("12p")
        else:
            labels.append(f"{hour-12}p")

    return {"labels": labels, "today": today, "yesterday": yesterday}


def get_peak_24h(device_gid: str | None = None) -> dict:
    """Return the highest-demand timestamp in the last 24h (Main channel) and its watt estimate."""
    conn = _connect()
    c = conn.cursor()
    since = (datetime.now() - timedelta(hours=24)).isoformat()
    resolved_gid = _resolve_device_gid(c, device_gid)
    if not resolved_gid:
        conn.close()
        return {"peak_watts": 0, "peak_time": None}
    # Sum all channels per timestamp to get total load, pick the max
    c.execute("""
        SELECT timestamp, SUM(usage_kwh) as total_kwh
        FROM readings
        WHERE timestamp >= ?
          AND device_gid = ?
          AND channel_name NOT IN ('Main','Mains_A','Mains_B','Mains_C','Balance')
        GROUP BY timestamp
        ORDER BY total_kwh DESC
        LIMIT 1
    """, (since, resolved_gid))
    row = c.fetchone()
    conn.close()
    if not row:
        return {"peak_watts": 0, "peak_time": None}
    # Poll data is requested at one-minute scale, so convert kWh/min to watts.
    watts = (row["total_kwh"] or 0) * 60 * 1000
    ts = row["timestamp"]
    try:
        dt = datetime.fromisoformat(ts[:19])
        hour = dt.hour
        label = ("12 AM" if hour == 0 else f"{hour} AM" if hour < 12
                 else "12 PM" if hour == 12 else f"{hour-12} PM")
        time_label = f"{label} ({dt.strftime('%m/%d')})"
    except Exception:
        time_label = ts[:16]
    return {"peak_watts": watts, "peak_time": time_label}


def get_circuit_data(channel_name, period="day", device_gid: str | None = None):
    conn = _connect()
    c = conn.cursor()

    now = datetime.now()
    resolved_gid = _resolve_device_gid(c, device_gid)
    if not resolved_gid:
        conn.close()
        return {"data": [], "total": {
            "total_kwh": None,
            "total_cents": None,
            "readings": 0,
            "first_reading": None,
            "last_reading": None,
        }}

    if period == "hour":
        since = (now - timedelta(hours=24)).isoformat()
        group_by = "strftime('%Y-%m-%d %H:00', timestamp)"
    elif period == "day":
        since = (now - timedelta(days=7)).isoformat()
        group_by = "strftime('%Y-%m-%d', timestamp)"
    elif period == "week":
        since = (now - timedelta(days=30)).isoformat()
        group_by = "strftime('%Y-%m-%d', timestamp)"
    elif period == "month":
        since = (now - timedelta(days=365)).isoformat()
        group_by = "strftime('%Y-%m', timestamp)"
    elif period == "year":
        since = (now - timedelta(days=365 * 3)).isoformat()
        group_by = "strftime('%Y', timestamp)"
    else:
        since = (now - timedelta(days=7)).isoformat()
        group_by = "strftime('%Y-%m-%d', timestamp)"

    c.execute(
        f"""SELECT {group_by} as period,
        SUM(usage_kwh) as total_kwh,
        SUM(cost_cents) as total_cents
        FROM readings
        WHERE channel_name = ? AND timestamp >= ? AND device_gid = ?
        GROUP BY period
        ORDER BY period""",
        (channel_name, since, resolved_gid),
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
        WHERE channel_name = ? AND timestamp >= ? AND device_gid = ?""",
        (channel_name, since, resolved_gid),
    )

    total = dict(c.fetchone())
    conn.close()

    return {"data": [dict(row) for row in results], "total": total}


def get_circuit_history(
    channel_name: str, device_gid: str | None = None, now: datetime | None = None,
) -> dict | None:
    """Recorded rolling 1/7/30-day totals and series, scoped to one device.

    Null buckets mean missing data, not zero energy. Trend comparisons require
    dense minute sampling in both equal-length periods. This is a sampling guard,
    not a guarantee of coverage for imported history of unknown intervals.
    """
    now = now or datetime.now()
    conn = _connect()
    try:
        gid = _resolve_device_gid(conn.cursor(), device_gid)
        if not gid:
            return None
        latest = conn.execute(
            """SELECT timestamp, usage_kwh FROM readings
               WHERE device_gid=? AND channel_name=? AND timestamp<=?
               ORDER BY timestamp DESC LIMIT 1""",
            (gid, channel_name, now.isoformat()),
        ).fetchone()
        if latest is None:
            return None

        def totals(start: datetime, end: datetime) -> dict:
            return dict(conn.execute(
                """SELECT SUM(usage_kwh) total_kwh, SUM(cost_cents) total_cents,
                          COUNT(*) readings, COUNT(DISTINCT substr(timestamp,1,16)) sampled_minutes,
                          MIN(timestamp) first_reading, MAX(timestamp) last_reading
                   FROM readings WHERE device_gid=? AND channel_name=?
                   AND timestamp>=? AND timestamp<? AND usage_kwh IS NOT NULL""",
                (gid, channel_name, start.isoformat(), end.isoformat()),
            ).fetchone())

        windows = []
        for days in (1, 7, 30):
            start = now - timedelta(days=days)
            previous_start = start - timedelta(days=days)
            current, previous = totals(start, now), totals(previous_start, start)
            dense = all(row['sampled_minutes'] >= days * 1440 * 0.8 for row in (current, previous))
            change = _delta_pct(current['total_kwh'], previous['total_kwh']) if dense else None
            grouping = "%Y-%m-%d %H:00" if days == 1 else "%Y-%m-%d"
            buckets = {
                row['period']: dict(row) for row in conn.execute(
                    """SELECT strftime(?, timestamp) period, SUM(usage_kwh) total_kwh,
                              COUNT(*) readings FROM readings
                       WHERE device_gid=? AND channel_name=? AND timestamp>=?
                       AND timestamp<? AND usage_kwh IS NOT NULL
                       GROUP BY period ORDER BY period""",
                    (grouping, gid, channel_name, start.isoformat(), now.isoformat()),
                ).fetchall()
            }
            cursor = start.replace(minute=0, second=0, microsecond=0) if days == 1 else start.replace(hour=0, minute=0, second=0, microsecond=0)
            step = timedelta(hours=1) if days == 1 else timedelta(days=1)
            series = []
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
        age = (now - datetime.fromisoformat(latest['timestamp'])).total_seconds()
        return {
            'channel_name': channel_name, 'device_gid': gid,
            'last_reading': latest['timestamp'],
            'live_watts': latest['usage_kwh'] * 60000 if 0 <= age < 180 and latest['usage_kwh'] is not None else None,
            'windows': windows,
        }
    finally:
        conn.close()


def get_monthly_projection(device_gid: str | None = None):
    """Most recent month's total using Main channel only (avoids double-counting)."""
    conn = _connect()
    c = conn.cursor()
    resolved_gid = _resolve_device_gid(c, device_gid)
    if not resolved_gid:
        conn.close()
        return None

    c.execute("""SELECT
        strftime('%Y-%m', timestamp) as month,
        SUM(usage_kwh) as total_kwh,
        SUM(cost_cents) as total_cents
        FROM readings
        WHERE channel_name = 'Main'
          AND device_gid = ?
        GROUP BY month
        ORDER BY month DESC
        LIMIT 1""", (resolved_gid,))

    result = c.fetchone()
    conn.close()
    return dict(result) if result else None


def _delta_pct(a, b):
    """Percent change from b to a. Returns None if b is None/zero."""
    if b is None or b == 0 or a is None:
        return None
    return round((a - b) / b * 100, 1)


def get_now_vs_context(window_minutes: int = 60, device_gid: str | None = None) -> dict:
    """
    Return the total kWh for the most recent `window_minutes` of readings,
    compared to the same window yesterday, one week ago, and one month ago.
    Includes per-circuit breakdown for the current window.
    """
    conn = _connect()
    c = conn.cursor()
    now = datetime.now()
    resolved_gid = _resolve_device_gid(c, device_gid)
    if not resolved_gid:
        conn.close()
        return {
            "window_minutes": window_minutes,
            "current_kwh": None,
            "yesterday_kwh": None,
            "last_week_kwh": None,
            "last_month_kwh": None,
            "vs_yesterday_pct": None,
            "vs_last_week_pct": None,
            "vs_last_month_pct": None,
            "change_pct": None,
            "circuits": [],
            "latest": [],
        }

    current_end = now
    current_start = current_end - timedelta(minutes=window_minutes)
    previous_end = current_start
    previous_start = previous_end - timedelta(minutes=window_minutes)
    yesterday_end = now - timedelta(days=1)
    yesterday_start = yesterday_end - timedelta(minutes=window_minutes)
    last_week_end = now - timedelta(days=7)
    last_week_start = last_week_end - timedelta(minutes=window_minutes)
    last_month_end = now - timedelta(days=30)
    last_month_start = last_month_end - timedelta(minutes=window_minutes)

    c.execute(
        """SELECT
               SUM(CASE WHEN timestamp BETWEEN ? AND ? THEN usage_kwh ELSE 0 END) AS current_kwh,
               SUM(CASE WHEN timestamp BETWEEN ? AND ? THEN usage_kwh ELSE 0 END) AS previous_kwh,
               SUM(CASE WHEN timestamp BETWEEN ? AND ? THEN usage_kwh ELSE 0 END) AS yesterday_kwh,
               SUM(CASE WHEN timestamp BETWEEN ? AND ? THEN usage_kwh ELSE 0 END) AS last_week_kwh,
               SUM(CASE WHEN timestamp BETWEEN ? AND ? THEN usage_kwh ELSE 0 END) AS last_month_kwh
           FROM readings
           WHERE channel_name = 'Main'
             AND device_gid = ?
             AND timestamp >= ?
             AND timestamp <= ?""",
        (
            current_start.isoformat(), current_end.isoformat(),
            previous_start.isoformat(), previous_end.isoformat(),
            yesterday_start.isoformat(), yesterday_end.isoformat(),
            last_week_start.isoformat(), last_week_end.isoformat(),
            last_month_start.isoformat(), last_month_end.isoformat(),
            resolved_gid,
            last_month_start.isoformat(),
            current_end.isoformat(),
        ),
    )
    row = c.fetchone()
    current = row["current_kwh"] if row and row["current_kwh"] is not None else None
    previous_window = row["previous_kwh"] if row and row["previous_kwh"] is not None else None
    yesterday = row["yesterday_kwh"] if row and row["yesterday_kwh"] is not None else None
    last_week = row["last_week_kwh"] if row and row["last_week_kwh"] is not None else None
    last_month = row["last_month_kwh"] if row and row["last_month_kwh"] is not None else None

    # Per-circuit for the current window
    since = (now - timedelta(minutes=window_minutes)).isoformat()
    c.execute(
        """SELECT channel_name, SUM(usage_kwh) as kwh, SUM(cost_cents) as cents
           FROM readings
           WHERE timestamp >= ? AND device_gid = ?
           GROUP BY channel_name
           ORDER BY kwh DESC""",
        (since, resolved_gid),
    )
    circuits = [dict(r) for r in c.fetchall()]

    c.execute(
        """SELECT channel_name, channel_num, usage_kwh, timestamp
           FROM latest_channel_snapshot
           WHERE device_gid = ?
           ORDER BY usage_kwh DESC""",
        (resolved_gid,),
    )
    latest_readings = [dict(r) for r in c.fetchall()]
    if not latest_readings:
        c.execute(
            """SELECT channel_name, channel_num, usage_kwh, timestamp
               FROM (
                   SELECT channel_name, channel_num, usage_kwh, timestamp,
                          ROW_NUMBER() OVER (
                              PARTITION BY channel_name
                              ORDER BY timestamp DESC, id DESC
                          ) AS rn
                FROM readings
                WHERE device_gid = ?
               )
               WHERE rn = 1
               ORDER BY usage_kwh DESC""",
            (resolved_gid,),
        )
        latest_readings = [dict(r) for r in c.fetchall()]

    conn.close()

    return {
        "window_minutes": window_minutes,
        "current_kwh": current,
        "yesterday_kwh": yesterday,
        "last_week_kwh": last_week,
        "last_month_kwh": last_month,
        "vs_yesterday_pct": _delta_pct(current, yesterday),
        "vs_last_week_pct": _delta_pct(current, last_week),
        "vs_last_month_pct": _delta_pct(current, last_month),
        "change_pct": _delta_pct(current, previous_window),
        "circuits": circuits,
        "latest": latest_readings,
    }


def get_trend(days_back: int = 14, device_gid: str | None = None) -> dict:
    """
    Return daily totals for the last `days_back` days plus a simple
    linear trend slope (positive = usage rising, negative = falling).
    Also returns the 7-day rolling average and the best/worst days.
    """
    conn = _connect()
    c = conn.cursor()
    resolved_gid = _resolve_device_gid(c, device_gid)
    if not resolved_gid:
        conn.close()
        return {
            "daily": [],
            "slope": None,
            "avg_kwh": None,
            "best_day": None,
            "worst_day": None,
        }

    since = (datetime.now() - timedelta(days=days_back)).isoformat()
    c.execute(
        """SELECT strftime('%Y-%m-%d', timestamp) as day,
                  SUM(usage_kwh) as total_kwh,
                  SUM(cost_cents) as total_cents
           FROM readings
           WHERE channel_name = 'Main' AND timestamp >= ? AND device_gid = ?
           GROUP BY day
           ORDER BY day""",
        (since, resolved_gid),
    )
    daily = [dict(r) for r in c.fetchall()]
    conn.close()

    if len(daily) < 2:
        return {
            "daily": daily,
            "slope": None,
            "avg_kwh": None,
            "best_day": None,
            "worst_day": None,
        }

    # Simple least-squares slope over the day index
    n = len(daily)
    xs = list(range(n))
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
    for suffix in (" (kWatts)", " (kWhs)", " (kW)"):
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


def import_emporia_csv(
    filepath: str,
    device_gid: str | None = None,
    original_filename: str | None = None,
) -> dict:
    """
    Import an Emporia energy export CSV into the readings table.

    Expected format:
      Column 0: "Time Bucket (America/New_York)"  → "MM/DD/YYYY HH:MM:SS"
      Columns 1+: "{Device}-{ChannelDesc} (kWatts)" or "(kWhs)" → float or "No CT"

    Unit handling:
      - Columns suffixed "(kWatts)" store average power in kW per bucket.
        These are converted to kWh using the interval duration (parsed from filename).
        e.g. 1MIN: kWh = kW × (1/60);  15MIN: kWh = kW × (15/60)
      - Columns suffixed "(kWhs)" are already in kWh — stored as-is.

    Returns {"imported": N, "skipped": N, "errors": N, "unit": "kWatts"|"kWhs"}.
    """
    import csv as csv_mod
    from pathlib import Path

    filepath = Path(filepath)
    # Use original filename (from upload) for device_gid + interval, not temp path
    name_stem = Path(original_filename).stem if original_filename else filepath.stem

    if device_gid is None:
        # "8C9E94-Barn-1MIN.csv" → "8C9E94"
        parts = name_stem.split("-")
        device_gid = parts[0] if parts else "IMPORT"

    # Detect time-bucket interval from filename (last segment after last "-")
    # e.g. "8C9E94-Barn-1MIN" → "1MIN"
    interval_str = name_stem.split("-")[-1].upper()
    interval_minutes = _KWATTS_INTERVAL_MINUTES.get(interval_str)  # None if unrecognised

    conn = _connect()
    c = conn.cursor()
    imported = skipped = errors = 0
    detected_unit: str | None = None
    capability = {
        "has_main": False,
        "has_mains_a": False,
        "has_mains_b": False,
        "has_mains_c": False,
        "mains_c_no_ct": False,
    }

    with open(filepath, newline="", encoding="utf-8-sig") as f:
        reader = csv_mod.DictReader(f)
        headers = reader.fieldnames or []
        if not headers:
            conn.close()
            return {"imported": 0, "skipped": 0, "errors": 1,
                    "message": "Empty or invalid CSV"}

        ts_col = headers[0]

        # Determine unit from first data column header suffix
        # Build [(col_header, channel_name, is_kwatts), …]
        channel_cols = []
        for col in headers[1:]:
            is_kwatts = "(kWatts)" in col or "(kW)" in col
            if detected_unit is None:
                detected_unit = "kWatts" if is_kwatts else "kWhs"
            channel_name = _clean_csv_channel_name(col)
            if channel_name == "Main":
                capability["has_main"] = True
            elif channel_name == "Mains_A":
                capability["has_mains_a"] = True
            elif channel_name == "Mains_B":
                capability["has_mains_b"] = True
            elif channel_name == "Mains_C":
                capability["has_mains_c"] = True
            channel_cols.append((col, channel_name, is_kwatts))

        # Conversion factor for kWatts columns: kWh = kW × (interval_minutes / 60)
        # Fall back to assuming 1MIN if interval not parseable from filename
        if interval_minutes is None:
            interval_minutes = 1.0  # safe default; warn via returned dict
        kwatts_to_kwh = interval_minutes / 60.0

        rows_to_insert = []
        for row in reader:
            ts_raw = row.get(ts_col, "").strip()
            if not ts_raw:
                skipped += 1
                continue
            try:
                dt = datetime.strptime(ts_raw, "%m/%d/%Y %H:%M:%S")
                ts_iso = dt.isoformat()
            except ValueError:
                errors += 1
                continue

            for col, channel_name, is_kwatts in channel_cols:
                val = row.get(col, "").strip()
                if not val or val.lower() == "no ct":
                    if channel_name == "Mains_C" and val.lower() == "no ct":
                        capability["mains_c_no_ct"] = True
                    skipped += 1
                    continue
                try:
                    raw_value = float(val)
                except ValueError:
                    errors += 1
                    continue

                # Apply unit conversion
                usage_kwh = raw_value * kwatts_to_kwh if is_kwatts else raw_value
                cost_cents = usage_kwh * RATE_CENTS
                rows_to_insert.append(
                    (ts_iso, device_gid, None, channel_name, usage_kwh, cost_cents)
                )

    if rows_to_insert:
        c.executemany(
            """INSERT OR IGNORE INTO readings
               (timestamp, device_gid, channel_num, channel_name, usage_kwh, cost_cents)
               VALUES (?, ?, ?, ?, ?, ?)""",
            rows_to_insert,
        )
        imported = c.rowcount
        skipped += len(rows_to_insert) - imported
        for ts_iso, row_device_gid, channel_num, channel_name, usage_kwh, cost_cents in rows_to_insert:
            _upsert_latest_snapshot_with_conn(
                conn,
                device_gid=str(row_device_gid),
                channel_name=channel_name,
                channel_num=channel_num,
                usage_kwh=usage_kwh,
                cost_cents=cost_cents,
                timestamp=ts_iso,
            )

    conn.commit()
    conn.close()
    save_device_capabilities(
        str(device_gid),
        service_mode=_classify_service_mode(
            has_main=capability["has_main"],
            has_mains_a=capability["has_mains_a"],
            has_mains_b=capability["has_mains_b"],
            has_mains_c=capability["has_mains_c"],
            mains_c_no_ct=capability["mains_c_no_ct"],
        ),
        has_main=capability["has_main"],
        has_mains_a=capability["has_mains_a"],
        has_mains_b=capability["has_mains_b"],
        has_mains_c=capability["has_mains_c"],
        mains_c_no_ct=capability["mains_c_no_ct"],
        source="csv_import",
    )
    return {
        "imported": imported,
        "skipped": skipped,
        "errors": errors,
        "unit": detected_unit or "unknown",
        "interval": interval_str,
        "conversion_factor": kwatts_to_kwh if detected_unit == "kWatts" else 1.0,
    }


def fix_csv_kwatts_import() -> dict:
    """
    One-time migration: CSV rows imported before unit-detection was added stored
    kWatts values directly as usage_kwh (kWh).  For 1-minute buckets this inflates
    every reading by 60×.

    Heuristic: rows whose timestamp has NO fractional-second component came from
    CSV imports (live poller always writes microseconds).  We divide those rows'
    usage_kwh and cost_cents by 60 (assumes 1MIN source files, which is what
    Emporia exports by default and what was historically imported here).

    Safe to re-run — already-corrected rows are not touched because after
    correction their values will be small and a second ÷60 would make them tiny,
    but we guard against double-application by only touching rows in the ghost
    import device bucket (device_gid != '551741' and not LIKE '%.%' timestamp).

    Returns {"fixed": N} count of rows updated.
    """
    conn = _connect()
    c = conn.cursor()
    migration_name = "fix_csv_kwatts_import_v1"
    already_applied = c.execute(
        "SELECT 1 FROM migrations WHERE name = ?",
        (migration_name,),
    ).fetchone()
    if already_applied:
        conn.close()
        return {"fixed": 0}
    # Rows from CSV import: exact-second timestamps (no '.' in timestamp string)
    # Exclude the real live device (551741) which should never have exact timestamps
    c.execute(
        """UPDATE readings
           SET usage_kwh  = usage_kwh  / 60.0,
               cost_cents = cost_cents / 60.0
           WHERE timestamp NOT LIKE '%.%'
             AND device_gid != '551741'""",
    )
    fixed = c.rowcount
    c.execute(
        "INSERT INTO migrations(name, applied_at) VALUES(?, ?)",
        (migration_name, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
    return {"fixed": fixed}


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


def get_log_entries(n: int = 200) -> list[dict]:
    """Return the most recent n poll timestamps and total kWh recorded."""
    conn = _connect()
    c = conn.cursor()
    c.execute(
        """SELECT timestamp,
                  SUM(usage_kwh)   as total_kwh,
                  SUM(cost_cents)  as total_cents,
                  COUNT(*)         as channel_count
           FROM readings
           GROUP BY timestamp
           ORDER BY timestamp DESC
           LIMIT ?""",
        (n,),
    )
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        if sys.argv[1] == "backup":
            if len(sys.argv) != 3:
                raise SystemExit("Usage: python energy.py backup DESTINATION.db")
            print(json.dumps(backup_database(sys.argv[2]), indent=2))
        elif sys.argv[1] == "poll":
            vue = login_vue()
            device_gids, _ = get_devices_with_channels(vue)
            poll_and_store(vue, device_gids)
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
