"""Private Linux client state; independent of the collector's database and secrets."""
import hashlib
import json
import math
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

TEMPERATURE_TREND_CACHE_KEY = "native-temperature-trends-24h-v1"

DEFAULT_ORIGIN = "https://energy.stoverparc.org"


def validate_origin(value):
    value = value.strip()
    url = urlsplit(value)
    if (url.scheme not in {"https", "http"} or not url.hostname or url.username
            or url.password or url.query or url.fragment or url.path not in {"", "/"}):
        raise ValueError("Enter an HTTPS origin or a loopback SSH tunnel, without a path.")
    if url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Plain HTTP is allowed only through a loopback SSH tunnel.")
    # Validate malformed and out-of-range ports too.
    _ = url.port
    return value.rstrip("/")


def private_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, allow_nan=False)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path):
    path = Path(path)
    if not path.exists() or path.stat().st_size > 8 * 1024 * 1024:
        return None
    return json.loads(path.read_text())


def cache_key(origin, endpoint):
    return hashlib.sha256((validate_origin(origin) + endpoint).encode()).hexdigest() + ".json"


def temperature_history_cache_key(origin, source_id, endpoint):
    if not isinstance(source_id, str) or not source_id:
        raise ValueError('Temperature history requires a collector identity')
    return cache_key(origin, json.dumps(['temperature-window-v2', source_id, endpoint]))


def load_temperature_window_cache(directory, origin, source_id, endpoint):
    if not source_id:
        return None
    try:
        record = read_json(Path(directory) / temperature_history_cache_key(origin, source_id, endpoint))
        if (not isinstance(record, dict) or record.get('origin') != origin
                or record.get('source_id') != source_id):
            return None
        validate_temperature_history(record['data'])
        return record
    except (OSError, ValueError, KeyError, TypeError):
        return None


def load_temperature_trend_cache(directory, origin, source_id):
    """Recover only source-scoped trends, migrating the old shared cache safely."""
    if not source_id:
        return None
    target = Path(directory) / cache_key(origin, TEMPERATURE_TREND_CACHE_KEY)
    legacy = Path(directory) / cache_key(origin, '/api/house-control/temperatures?window=24h')
    for path in (target, legacy):
        try:
            record = read_json(path)
            if (not isinstance(record, dict) or record.get('origin') != origin
                    or record.get('source_id') != source_id):
                continue
            payload = validate_temperature_history(record['data'])
            fetched = record.get('fetched_at')
            if (payload['window'] != '24h' or type(fetched) not in (int, float)
                    or not math.isfinite(fetched) or fetched <= 0):
                continue
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if path == legacy:
            try:
                private_json(target, record)
            except OSError:
                pass  # The validated history can still be displayed from memory.
        return record
    return None


def display_state(record, origin, connected=False, now=None):
    """A cached response must never masquerade as live power or sensor readings."""
    now = time.time() if now is None else now
    if not record or record.get("origin") != origin:
        return {"online": False, "connection": "Collector unavailable", "origin": origin}
    state = json.loads(json.dumps(record["data"]))
    age = now - record.get("fetched_at", 0)
    live = connected and 0 <= age <= 180 and state.get("online") is True
    state.update(origin=origin, fetched_at=record.get("fetched_at"), online=live,
                 connection="Connected" if connected else "Offline — cached history")
    if not connected or not 0 <= age <= 180:
        for sensor in state.get('temperature_sensors', []):
            sensor['stale'] = True
    if not live:
        state["current_watts"] = state["cost_per_hour"] = None
        for field in ("top_circuits", "breaker_slots"):
            for circuit in state.get(field, []):
                circuit["watts"] = circuit["load_percent"] = None
        if state.get("radon_reading"):
            state["radon_reading"] = None
    return state


def validate_summary(value):
    if not isinstance(value, dict) or not isinstance(value.get("version"), str):
        raise ValueError("The server did not return an Energy Monitor summary.")
    if type(value.get("online")) is not bool:
        raise ValueError("Summary has no collection status.")
    for field in ("current_watts", "cost_per_hour", "recorded_kwh", "cost_24h"):
        number = value.get(field)
        if number is not None and (type(number) not in (float, int) or not math.isfinite(number)):
            raise ValueError("Invalid summary measurement.")
    return value


def temperature_band(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        return None
    return 'cold' if value < 70 else 'comfortable' if value <= 75 else 'warm' if value < 78 else 'hot'


def validate_temperature_history(value):
    if not isinstance(value, dict) or value.get('window') not in ('4h', '24h', '7d', '30d'):
        raise ValueError('Invalid temperature history')
    times, series = value.get('times'), value.get('series')
    if not isinstance(times, list) or not 1 <= len(times) <= 200 or not isinstance(series, list) or len(series) > 256:
        raise ValueError('Oversized or missing temperature history')
    if not all(isinstance(stamp, str) for stamp in times):
        raise ValueError('History times must be ISO strings')
    stamps = [datetime.fromisoformat(stamp) for stamp in times]
    if any(stamp.tzinfo is None for stamp in stamps) or any(a >= b for a, b in zip(stamps, stamps[1:])):
        raise ValueError('History times must be ordered and timezone aware')
    for key in ('start', 'end'):
        if datetime.fromisoformat(value[key]).tzinfo is None:
            raise ValueError('History window requires UTC timestamps')
    if type(value.get('seconds')) is not int or not 60 <= value['seconds'] <= 86400:
        raise ValueError('Invalid history interval')
    for sensor in series:
        if not isinstance(sensor, dict) or not all(isinstance(sensor.get(key), str) for key in ('source', 'sensor_id', 'name')):
            raise ValueError('Invalid history sensor')
        values = sensor.get('values')
        if not isinstance(values, list) or len(values) != len(times):
            raise ValueError('Temperature history does not match its time axis')
        if any(v is not None and (type(v) not in (float, int) or not math.isfinite(v)) for v in values):
            raise ValueError('Invalid recorded temperature')
    return value


def load_ui_state(path):
    """Remembered window size, sidebar and last page. Anything malformed falls back to defaults."""
    state = {"width": 1240, "height": 800, "sidebar": True, "page": "/"}
    try:
        saved = read_json(path) or {}
    except (OSError, ValueError):
        return state
    for key, low, high in (("width", 480, 7680), ("height", 360, 4320)):
        value = saved.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and low <= value <= high:
            state[key] = value
    if isinstance(saved.get("sidebar"), bool):
        state["sidebar"] = saved["sidebar"]
    page = saved.get("page")
    # Only a collector path: never a scheme, host, credentials or protocol-relative URL.
    if isinstance(page, str) and page.startswith("/") and not page.startswith("//") and len(page) <= 200 \
            and "\\" not in page and "@" not in page.split("?")[0]:
        state["page"] = page
    return state


class OutageNotifier:
    """Decides when to tell the user the collector went offline or came back; quiet and rate limited.

    Offline is announced only after `grace` seconds of continuous failure (and never during startup),
    at most once per `cooldown`; recovery is announced only if an offline notice was sent.
    """

    def __init__(self, grace=90, cooldown=1800, startup=60):
        self.grace, self.cooldown, self.startup = grace, cooldown, startup
        self.started = None
        self.down_since = None
        self.last_offline = None
        self.announced = False

    def update(self, connected, now):
        if self.started is None:
            self.started = now
        if connected:
            self.down_since = None
            if self.announced:
                self.announced = False
                return "recovered"
            return None
        if self.down_since is None:
            self.down_since = now
        if (not self.announced and now - self.started >= self.startup and now - self.down_since >= self.grace
                and (self.last_offline is None or now - self.last_offline >= self.cooldown)):
            self.announced = True
            self.last_offline = now
            return "offline"
        return None


def nice_ticks(low, high, count=4):
    """Round axis ticks covering [low, high]; returns (ticks, axis_low, axis_high). Never divides by zero."""
    if not (math.isfinite(low) and math.isfinite(high)):
        raise ValueError("Axis bounds must be finite")
    if high < low:
        low, high = high, low
    span = high - low
    if span == 0:
        span = abs(high) * 0.1 or 1.0
        low, high = low - span / 2, high + span / 2
        span = high - low
    raw = span / max(1, count)
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw)
    start = math.floor(low / step) * step
    end = math.ceil(high / step) * step
    ticks = [round(start + i * step, 10) for i in range(int(round((end - start) / step)) + 1)]
    return ticks, ticks[0], ticks[-1]


def value_runs(values):
    """Split a series into runs of recorded values: [[(index, value), ...], ...]. None, NaN, negatives are gaps."""
    runs, run = [], []
    for index, value in enumerate(values):
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            run.append((index, value))
        elif run:
            runs.append(run)
            run = []
    if run:
        runs.append(run)
    return runs


def index_at(x, left, plot_width, count):
    """Nearest data index for a pointer x; None if there is nothing to point at."""
    if count < 1 or plot_width <= 0:
        return None
    if count == 1:
        return 0
    return min(count - 1, max(0, round((x - left) * (count - 1) / plot_width)))


def sidebar_footer(state):
    """(headline, caption, live) for the sidebar's glanceable power readout; never shows stale power as live."""
    watts = state.get("current_watts")
    if state.get("online") is True and isinstance(watts, (int, float)) and not isinstance(watts, bool) and math.isfinite(watts):
        cost = state.get("cost_per_hour")
        caption = "Live now" + (" · $%.2f/h" % cost if isinstance(cost, (int, float)) and math.isfinite(cost) else "")
        return "%.0f W" % watts, caption, True
    last = state.get("last_reading")
    return "Offline", ("Last recorded " + str(last)[-8:-3] if last else "No recorded reading"), False
