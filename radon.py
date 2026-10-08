"""Read-only radon observation storage; no device protocol or safety claims.

Adapters must supply actual measurement timestamps, not their polling time.
"""
import math
from datetime import datetime, timedelta, timezone

import energy


def ingest_observations(observations: list[dict], now: datetime | None = None) -> dict:
    """Validate a complete batch, preserve original units, and insert atomically.

    Conflicting measurements at the same source/sensor/time are rejected rather
    than silently replacing history. Exact duplicates are harmless retries.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('now requires a timezone')
    now = now.astimezone(timezone.utc)
    if not isinstance(observations, list) or not 1 <= len(observations) <= 1000:
        raise ValueError('observations must contain 1-1000 readings')
    validated = []
    for row in observations:
        if not isinstance(row, dict):
            raise ValueError('Each observation must be an object')
        for key in ('source', 'sensor_id', 'name', 'timestamp'):
            if not isinstance(row.get(key), str) or not 1 <= len(row[key]) <= 200:
                raise ValueError(f'{key} must be a nonempty string of at most 200 characters')
        if row['source'] not in {'ecosense', 'home_assistant', 'manual'}:
            raise ValueError('Unsupported radon source')
        stamp = datetime.fromisoformat(row['timestamp'].replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            raise ValueError('timestamp requires a timezone')
        try:
            stamp = stamp.astimezone(timezone.utc)
        except OverflowError as exc:
            raise ValueError('timestamp outside supported UTC range') from exc
        if not now - timedelta(days=energy.DB_RETENTION_DAYS) <= stamp <= now + timedelta(minutes=5):
            raise ValueError('timestamp outside retention or in the future')
        value = row.get('value')
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError('value must be a measured number; skip unavailable samples')
        try:
            value = float(value)
        except OverflowError as exc:
            raise ValueError('value must be finite and nonnegative') from exc
        unit = row.get('unit')
        if unit not in ('pCi/L', 'Bq/m3'):
            raise ValueError('unit must be pCi/L or Bq/m3')
        normalized = value * 37 if unit == 'pCi/L' else value
        if not math.isfinite(value) or value < 0 or not math.isfinite(normalized):
            raise ValueError('value must be finite and nonnegative')
        validated.append((row['source'], row['sensor_id'], stamp.isoformat(), row['name'],
                          value, unit, normalized, now.isoformat()))
    conn = energy._connect()
    try:
        with conn:
            inserted = 0
            for record in validated:
                old = conn.execute('''SELECT measured_value,measured_unit FROM radon_readings
                    WHERE source=? AND sensor_id=? AND timestamp=?''', record[:3]).fetchone()
                if old is not None:
                    if old['measured_value'] != record[4] or old['measured_unit'] != record[5]:
                        raise ValueError('Conflicting measurement at an existing timestamp')
                    continue
                conn.execute('INSERT INTO radon_readings VALUES (?,?,?,?,?,?,?,?)', record)
                inserted += 1
            conn.execute('DELETE FROM radon_readings WHERE timestamp<?',
                         ((now - timedelta(days=energy.DB_RETENTION_DAYS)).isoformat(),))
        return {'inserted': inserted, 'duplicates': len(validated) - inserted}
    finally:
        conn.close()


def get_history(source: str, sensor_id: str, days: int = 7,
                now: datetime | None = None) -> list[dict]:
    """Return recorded samples only, scoped to one sensor. Gaps are not filled."""
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 365:
        raise ValueError('days must be between 1 and 365')
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('now requires a timezone')
    now = now.astimezone(timezone.utc)
    conn = energy._connect()
    try:
        return [dict(row) for row in conn.execute('''SELECT * FROM radon_readings
            WHERE source=? AND sensor_id=? AND timestamp>=? AND timestamp<=?
            ORDER BY timestamp''', (source, sensor_id, (now-timedelta(days=days)).isoformat(),
                                   now.isoformat()))]
    finally:
        conn.close()


def get_sensors() -> list[dict]:
    """List sensor identities that have recorded observations, never discovered guesses."""
    conn = energy._connect()
    try:
        return [dict(row) for row in conn.execute('''SELECT source,sensor_id,
            MAX(timestamp) AS last_measurement,COUNT(*) AS sample_count
            FROM radon_readings GROUP BY source,sensor_id ORDER BY source,sensor_id''')]
    finally:
        conn.close()


def hourly_chart(rows: list[dict], now: datetime | None = None, days: int = 7) -> dict:
    """Plot bounded sample means; no interpolation or duration weighting.

    Input is already validated, sensor-scoped storage output. Blank hours produce
    no point. An hour containing one sample is not a full hour of coverage.
    """
    if isinstance(days, bool) or days not in (1, 7, 30, 365):
        raise ValueError('Invalid history window')
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('now requires a timezone')
    end = now.astimezone(timezone.utc)
    start = end - timedelta(days=days)
    buckets = {}
    for row in rows:
        stamp = datetime.fromisoformat(row['timestamp'])
        if not start <= stamp <= end:
            continue
        hour = stamp.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
        if days > 7:
            hour = hour.replace(hour=0)
        buckets.setdefault(hour, []).append(row['radon_bq_m3'])
    points = []
    for hour, values in sorted(buckets.items()):
        mean = math.fsum(value / len(values) for value in values)
        center = min(end, max(start, hour + timedelta(hours=12 if days > 7 else 0.5)))
        points.append({'timestamp': hour.isoformat(), 'mean': mean, 'samples': len(values),
                       'x': 50 + 900 * (center-start).total_seconds() / (days*86400)})
    peak = max((point['mean'] for point in points), default=0)
    scale = max(peak, 1)
    for point in points:
        point['y'] = 180 - 150 * point['mean'] / scale
    return {'points': points, 'scale': scale, 'start': start.isoformat(), 'end': end.isoformat()}
