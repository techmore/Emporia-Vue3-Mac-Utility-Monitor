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
