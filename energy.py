#!/usr/bin/env python3
import os
import json
import time
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
import pyemvue
from pyemvue.enums import Scale, Unit

DB_PATH = os.environ.get("DB_PATH", "energy.db")
RATE_CENTS = float(os.environ.get("RATE_CENTS", "11.04"))
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "60"))
# Rows older than this many days are pruned during each poll cycle
DB_RETENTION_DAYS = int(os.environ.get("DB_RETENTION_DAYS", "365"))


def _connect() -> sqlite3.Connection:
    """Open a WAL-mode SQLite connection with row_factory set."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
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
    """)
    conn.commit()
    conn.close()


# Ensure the table exists as soon as this module is imported.
ensure_table()


def login_vue():
    vue = pyemvue.PyEmVue()
    token_file = "keys.json"
    username = os.environ.get("EMPORIA_EMAIL")
    password = os.environ.get("EMPORIA_PASSWORD")

    print(f"Attempting login with user: {username}")

    if os.path.exists(token_file):
        with open(token_file) as f:
            data = json.load(f)
            print("Using existing tokens from keys.json")
            vue.login(
                id_token=data.get("idToken"),
                access_token=data.get("accessToken"),
                refresh_token=data.get("refreshToken"),
                token_storage_file=token_file,
            )
    else:
        if not username or not password:
            print("Please set EMPORIA_EMAIL and EMPORIA_PASSWORD environment variables")
            exit(1)
        print("Logging in with username/password...")
        try:
            result = vue.login(
                username=username, password=password, token_storage_file=token_file
            )
        except Exception as e:
            print(f"LOGIN EXCEPTION: {type(e).__name__}: {e}")
            import traceback

            traceback.print_exc()
            exit(1)
        print(f"Login result: {result}")
        if not result:
            print("LOGIN FAILED! Check credentials.")
            print(
                "Note: Emporia may have restricted API access. Try ESPHome for local access."
            )
            exit(1)
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

            cost = channel.usage * RATE_CENTS

            c.execute(
                """INSERT INTO readings
                (timestamp, device_gid, channel_num, channel_name, usage_kwh, cost_cents)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (now, gid, channelnum, channel.name, channel.usage, cost),
            )

    # Prune old rows to keep the database from growing unboundedly.
    cutoff = (datetime.now() - timedelta(days=DB_RETENTION_DAYS)).isoformat()
    c.execute("DELETE FROM readings WHERE timestamp < ?", (cutoff,))

    conn.commit()
    conn.close()
    print(f"[{now}] Recorded readings")


def run_continuous():
    import sys

    # Force line-buffered output so logs appear immediately even via nohup/launchd
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[union-attr]
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(line_buffering=True)  # type: ignore[union-attr]

    print(f"Starting continuous polling every {POLL_INTERVAL} seconds")
    print(f"Rate: ${RATE_CENTS / 100:.4f}/kWh")
    print(f"Database: {DB_PATH}")

    vue = login_vue()
    device_gids, device_info = get_devices_with_channels(vue)
    print(f"Found {len(device_gids)} device(s)")

    while True:
        try:
            poll_and_store(vue, device_gids)
        except Exception as e:
            print(f"Error during poll: {type(e).__name__}: {e}")
        time.sleep(POLL_INTERVAL)


def get_summary(hours=24):
    conn = _connect()
    c = conn.cursor()

    since = (datetime.now() - timedelta(hours=hours)).isoformat()

    c.execute(
        """SELECT channel_name, 
        SUM(usage_kwh) as total_kwh, 
        SUM(cost_cents) as total_cents,
        COUNT(*) as readings
        FROM readings 
        WHERE timestamp >= ? 
        GROUP BY channel_name
        ORDER BY total_kwh DESC""",
        (since,),
    )

    results = c.fetchall()
    conn.close()
    return [dict(row) for row in results]


def get_hourly_data(days=7):
    conn = _connect()
    c = conn.cursor()

    since = (datetime.now() - timedelta(days=days)).isoformat()

    c.execute(
        """SELECT 
        strftime('%Y-%m-%d %H:00', timestamp) as hour,
        SUM(usage_kwh) as total_kwh,
        SUM(cost_cents) as total_cents
        FROM readings
        WHERE timestamp >= ?
        GROUP BY hour
        ORDER BY hour""",
        (since,),
    )

    results = c.fetchall()
    conn.close()
    return [dict(row) for row in results]


def get_daily_data(days=30):
    conn = _connect()
    c = conn.cursor()

    since = (datetime.now() - timedelta(days=days)).isoformat()

    c.execute(
        """SELECT 
        strftime('%Y-%m-%d', timestamp) as day,
        SUM(usage_kwh) as total_kwh,
        SUM(cost_cents) as total_cents
        FROM readings
        WHERE timestamp >= ?
        GROUP BY day
        ORDER BY day""",
        (since,),
    )

    results = c.fetchall()
    conn.close()
    return [dict(row) for row in results]


def get_latest():
    conn = _connect()
    c = conn.cursor()

    c.execute("""SELECT channel_name, usage_kwh, cost_cents, timestamp
        FROM readings
        WHERE id IN (SELECT MAX(id) FROM readings GROUP BY channel_name)
        ORDER BY usage_kwh DESC""")

    results = c.fetchall()
    conn.close()
    return [dict(row) for row in results]


def get_month_comparison():
    """Compare current month to previous month."""
    conn = _connect()
    c = conn.cursor()

    now = datetime.now()
    # First day of this month
    this_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # First day of last month
    last_month_start = (this_month_start - timedelta(days=1)).replace(day=1)

    c.execute(
        """SELECT
        strftime('%Y-%m', timestamp) as month,
        SUM(usage_kwh) as total_kwh,
        SUM(cost_cents) as total_cents
        FROM readings
        WHERE timestamp >= ?
        GROUP BY month""",
        (last_month_start.isoformat(),),
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


def get_peak_usage():
    """Find peak usage times."""
    conn = _connect()
    c = conn.cursor()

    c.execute("""SELECT 
        strftime('%H', timestamp) as hour,
        AVG(usage_kwh) as avg_kwh
        FROM readings
        WHERE timestamp >= datetime('now', '-30 days')
        GROUP BY hour
        ORDER BY avg_kwh DESC
        LIMIT 5""")

    peak_hours = c.fetchall()

    c.execute("""SELECT 
        strftime('%w', timestamp) as day_of_week,
        AVG(usage_kwh) as avg_kwh
        FROM readings
        WHERE timestamp >= datetime('now', '-30 days')
        GROUP BY day_of_week
        ORDER BY avg_kwh DESC
        LIMIT 5""")

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


def get_circuit_data(channel_name, period="day"):
    conn = _connect()
    c = conn.cursor()

    now = datetime.now()

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
        WHERE channel_name = ? AND timestamp >= ?
        GROUP BY period
        ORDER BY period""",
        (channel_name, since),
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
        WHERE channel_name = ? AND timestamp >= ?""",
        (channel_name, since),
    )

    total = dict(c.fetchone())
    conn.close()

    return {"data": [dict(row) for row in results], "total": total}


def get_monthly_projection():
    conn = _connect()
    c = conn.cursor()

    c.execute("""SELECT
        strftime('%Y-%m', timestamp) as month,
        SUM(usage_kwh) as total_kwh,
        SUM(cost_cents) as total_cents
        FROM readings
        GROUP BY month
        ORDER BY month DESC
        LIMIT 1""")

    result = c.fetchone()
    conn.close()
    return dict(result) if result else None


def _delta_pct(a, b):
    """Percent change from b to a. Returns None if b is None/zero."""
    if b is None or b == 0 or a is None:
        return None
    return round((a - b) / b * 100, 1)


def get_now_vs_context(window_minutes: int = 60) -> dict:
    """
    Return the total kWh for the most recent `window_minutes` of readings,
    compared to the same window yesterday, one week ago, and one month ago.
    Includes per-circuit breakdown for the current window.
    """
    conn = _connect()
    c = conn.cursor()
    now = datetime.now()

    def window_kwh(offset_days: float):
        start = (now - timedelta(days=offset_days, minutes=window_minutes)).isoformat()
        end = (now - timedelta(days=offset_days)).isoformat()
        c.execute(
            """SELECT SUM(usage_kwh) FROM readings
               WHERE channel_name = 'Main' AND timestamp BETWEEN ? AND ?""",
            (start, end),
        )
        row = c.fetchone()
        return row[0] if row and row[0] is not None else None

    current = window_kwh(0)
    yesterday = window_kwh(1)
    last_week = window_kwh(7)
    last_month = window_kwh(30)

    # Per-circuit for the current window
    since = (now - timedelta(minutes=window_minutes)).isoformat()
    c.execute(
        """SELECT channel_name, SUM(usage_kwh) as kwh, SUM(cost_cents) as cents
           FROM readings
           WHERE timestamp >= ?
           GROUP BY channel_name
           ORDER BY kwh DESC""",
        (since,),
    )
    circuits = [dict(r) for r in c.fetchall()]

    # Most recent single reading per channel (for "right now" watts estimate)
    c.execute(
        """SELECT channel_name, usage_kwh, timestamp
           FROM readings
           WHERE id IN (SELECT MAX(id) FROM readings GROUP BY channel_name)
           ORDER BY usage_kwh DESC"""
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


def import_emporia_csv(filepath: str, device_gid: str | None = None) -> dict:
    """
    Import an Emporia energy export CSV into the readings table.

    Expected format:
      Column 0: "Time Bucket (America/New_York)"  → "MM/DD/YYYY HH:MM:SS"
      Columns 1+: "{Device}-{ChannelDesc} (kWhs)" → float or "No CT"

    Returns {"imported": N, "skipped": N, "errors": N}.
    """
    import csv as csv_mod
    from pathlib import Path

    filepath = Path(filepath)
    if device_gid is None:
        # "8C9E94-Barn-1H.csv" → "8C9E94"
        stem = filepath.stem
        device_gid = stem.split("-")[0] if stem else "IMPORT"

    conn = _connect()
    c = conn.cursor()
    imported = skipped = errors = 0

    with open(filepath, newline="", encoding="utf-8-sig") as f:
        reader = csv_mod.DictReader(f)
        headers = reader.fieldnames or []
        if not headers:
            conn.close()
            return {"imported": 0, "skipped": 0, "errors": 1,
                    "message": "Empty or invalid CSV"}

        ts_col = headers[0]

        # Build [(original_col_header, channel_name), …]
        channel_cols = []
        for col in headers[1:]:
            name = col.strip()
            if name.endswith("(kWhs)"):
                name = name[:-6].strip()
            # Strip leading "DeviceName-" when the first segment has no spaces
            if "-" in name:
                parts = name.split("-", 1)
                if " " not in parts[0]:
                    name = parts[1].strip()
            channel_cols.append((col, name))

        # Pre-fetch existing (timestamp, device_gid, channel_name) combos
        # scoped to this device so dedup is fast without per-row queries.
        c.execute(
            "SELECT timestamp, channel_name FROM readings WHERE device_gid = ?",
            (device_gid,),
        )
        existing = {(r[0], r[1]) for r in c.fetchall()}

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

            for col, channel_name in channel_cols:
                val = row.get(col, "").strip()
                if not val or val.lower() == "no ct":
                    skipped += 1
                    continue
                try:
                    usage_kwh = float(val)
                except ValueError:
                    errors += 1
                    continue

                if (ts_iso, channel_name) in existing:
                    skipped += 1
                    continue

                cost_cents = usage_kwh * RATE_CENTS
                rows_to_insert.append(
                    (ts_iso, device_gid, None, channel_name, usage_kwh, cost_cents)
                )
                existing.add((ts_iso, channel_name))  # prevent intra-file dupes

    if rows_to_insert:
        c.executemany(
            """INSERT INTO readings
               (timestamp, device_gid, channel_num, channel_name, usage_kwh, cost_cents)
               VALUES (?, ?, ?, ?, ?, ?)""",
            rows_to_insert,
        )
        imported = len(rows_to_insert)

    conn.commit()
    conn.close()
    return {"imported": imported, "skipped": skipped, "errors": errors}


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
