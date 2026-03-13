#!/usr/bin/env python3
import os
import json
import time
import sqlite3
import logging
from datetime import datetime, timedelta
from pathlib import Path
import pyemvue
from pyemvue.enums import Scale, Unit

DB_PATH = os.environ.get("DB_PATH", "energy.db")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "60"))
DB_RETENTION_DAYS = int(os.environ.get("DB_RETENTION_DAYS", "365"))
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
        with open(POLLER_STATUS_FILE, "w") as f:
            json.dump(data, f)
    except Exception:
        pass


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
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    _chmod_owner_only(path)


def _connect() -> sqlite3.Connection:
    """Open a WAL-mode SQLite connection with row_factory set."""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.row_factory = sqlite3.Row
    return conn


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
        CREATE INDEX IF NOT EXISTS idx_device_channel
            ON readings(device_gid, channel_num);
        CREATE INDEX IF NOT EXISTS idx_channel_name
            ON readings(channel_name);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_readings_device_ts_channel
            ON readings(device_gid, timestamp, channel_name);
        CREATE TABLE IF NOT EXISTS migrations (
            name TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        );

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
    # Migrate: add poles column if it doesn't exist yet (existing DBs)
    try:
        conn.execute("ALTER TABLE circuit_labels ADD COLUMN poles INTEGER DEFAULT 1")
        conn.commit()
    except Exception:
        pass  # column already exists
    conn.commit()
    conn.close()


def get_panel_layout() -> list[dict]:
    """Return all circuit_labels rows ordered by slot."""
    conn = _connect()
    rows = conn.execute(
        "SELECT slot, channel_name, label, note, amps, poles FROM circuit_labels ORDER BY slot"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


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
    conn = _connect()
    c = conn.cursor()

    usage_dict = vue.get_device_list_usage(
        deviceGids=device_gids,
        instant=None,
        scale=Scale.MINUTE.value,
        unit=Unit.KWH.value,
    )

    now = datetime.now().isoformat()

    for gid, device in usage_dict.items():
        for channelnum, channel in device.channels.items():
            if channel.usage is None:
                continue
            channel_name = _normalize_channel_name(channel.name)

            cost = channel.usage * RATE_CENTS

            c.execute(
                """INSERT INTO readings
                   (timestamp, device_gid, channel_num, channel_name, usage_kwh, cost_cents)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (now, gid, channelnum, channel_name, channel.usage, cost),
            )

    # Prune old rows to keep the database from growing unboundedly.
    cutoff = (datetime.now() - timedelta(days=DB_RETENTION_DAYS)).isoformat()
    c.execute("DELETE FROM readings WHERE timestamp < ?", (cutoff,))

    conn.commit()
    conn.close()
    logger.info("[%s] Recorded readings", now)


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
            poll_and_store(vue, device_gids)
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

    c.execute("""SELECT channel_name, usage_kwh, cost_cents, timestamp
        FROM (
            SELECT channel_name, usage_kwh, cost_cents, timestamp,
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
        fmt = "%Y-%m-%d %H:00"
    elif period == "day":
        since = (now - timedelta(days=7)).isoformat()
        group_by = "strftime('%Y-%m-%d', timestamp)"
        fmt = "%Y-%m-%d"
    elif period == "week":
        since = (now - timedelta(days=30)).isoformat()
        group_by = "strftime('%Y-%m-%d', timestamp)"
        fmt = "%Y-%m-%d"
    elif period == "month":
        since = (now - timedelta(days=365)).isoformat()
        group_by = "strftime('%Y-%m', timestamp)"
        fmt = "%Y-%m"
    elif period == "year":
        since = (now - timedelta(days=365 * 3)).isoformat()
        group_by = "strftime('%Y', timestamp)"
        fmt = "%Y"
    else:
        since = (now - timedelta(days=7)).isoformat()
        group_by = "strftime('%Y-%m-%d', timestamp)"
        fmt = "%Y-%m-%d"

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

    def window_kwh(offset_days: float = 0, offset_minutes: int = 0):
        end = now - timedelta(days=offset_days, minutes=offset_minutes)
        start = end - timedelta(minutes=window_minutes)
        start = start.isoformat()
        end = end.isoformat()
        c.execute(
            """SELECT SUM(usage_kwh) FROM readings
               WHERE channel_name = 'Main' AND timestamp BETWEEN ? AND ? AND device_gid = ?""",
            (start, end, resolved_gid),
        )
        row = c.fetchone()
        return row[0] if row and row[0] is not None else None

    current = window_kwh()
    previous_window = window_kwh(offset_minutes=window_minutes)
    yesterday = window_kwh(offset_days=1)
    last_week = window_kwh(offset_days=7)
    last_month = window_kwh(offset_days=30)

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

    # Most recent single reading per channel (for "right now" watts estimate)
    c.execute(
        """SELECT channel_name, usage_kwh, timestamp
           FROM (
               SELECT channel_name, usage_kwh, timestamp,
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


def get_trend(days_back: int = 14) -> dict:
    """
    Return daily totals for the last `days_back` days plus a simple
    linear trend slope (positive = usage rising, negative = falling).
    Also returns the 7-day rolling average and the best/worst days.
    """
    conn = _connect()
    c = conn.cursor()

    since = (datetime.now() - timedelta(days=days_back)).isoformat()
    c.execute(
        """SELECT strftime('%Y-%m-%d', timestamp) as day,
                  SUM(usage_kwh) as total_kwh,
                  SUM(cost_cents) as total_cents
           FROM readings
           WHERE channel_name = 'Main' AND timestamp >= ?
           GROUP BY day
           ORDER BY day""",
        (since,),
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
            channel_cols.append((col, _clean_csv_channel_name(col), is_kwatts))

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
        before_changes = conn.total_changes
        c.executemany(
            """INSERT OR IGNORE INTO readings
               (timestamp, device_gid, channel_num, channel_name, usage_kwh, cost_cents)
               VALUES (?, ?, ?, ?, ?, ?)""",
            rows_to_insert,
        )
        imported = conn.total_changes - before_changes
        skipped += len(rows_to_insert) - imported

    conn.commit()
    conn.close()
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
        if sys.argv[1] == "poll":
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
