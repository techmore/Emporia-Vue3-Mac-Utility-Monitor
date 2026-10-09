"""Read-only radon observation storage; no device protocol or safety claims.

Adapters must supply actual measurement timestamps, not their polling time.
"""
import math
from datetime import datetime, timedelta, timezone

import energy


def get_cache_status() -> dict:
    conn = energy._connect()
    try:
        row = conn.execute('SELECT * FROM radon_sync_cache_state WHERE singleton=1').fetchone()
        return dict(row) if row else {'source_id': None, 'generation_id': None, 'cursor': 0, 'high_watermark': 0,
                                      'synchronized_at': None}
    finally:
        conn.close()


def apply_changes(page: dict) -> dict:
    """Validate a complete remote page before atomically publishing rows and cursor."""
    if not isinstance(page, dict) or page.get('protocol_version') != 1:
        raise ValueError('Unsupported radon sync protocol')
    identity = page.get('source_id')
    if not isinstance(identity, str) or len(identity) != 32:
        raise ValueError('Invalid collector identity')
    generation = page.get('generation_id')
    if not isinstance(generation, str) or len(generation) != 32:
        raise ValueError('Invalid radon stream generation')
    after, cursor, watermark = (page.get(key) for key in
                                ('after_cursor', 'next_cursor', 'high_watermark'))
    if any(type(value) is not int or not 0 <= value <= 2**63 - 1
           for value in (after, cursor, watermark)):
        raise ValueError('Invalid radon sync cursors')
    if not after <= cursor <= watermark or type(page.get('has_more')) is not bool:
        raise ValueError('Inconsistent radon sync cursors')
    if page['has_more'] != (cursor < watermark):
        raise ValueError('Inconsistent radon sync watermark')
    rows = page.get('changes')
    if not isinstance(rows, list) or len(rows) > 1000:
        raise ValueError('Invalid radon sync page size')
    previous = after
    for row in rows:
        if not isinstance(row, dict) or type(row.get('sequence')) is not int:
            raise ValueError('Invalid radon change')
        if not previous < row['sequence'] <= cursor:
            raise ValueError('Unordered radon changes')
        previous = row['sequence']
        for key in ('source', 'sensor_id', 'timestamp'):
            if not isinstance(row.get(key), str) or not 1 <= len(row[key]) <= 200:
                raise ValueError('Invalid radon identity or timestamp')
        try:
            stamp = datetime.fromisoformat(row['timestamp'])
            if stamp.tzinfo is None or stamp.isoformat() != stamp.astimezone(timezone.utc).isoformat():
                raise ValueError('Radon sync requires normalized UTC timestamps')
        except OverflowError as exc:
            raise ValueError('Invalid radon timestamp') from exc
        if row.get('operation') not in ('upsert', 'delete'):
            raise ValueError('Invalid radon operation')
        if row['operation'] == 'upsert':
            if row['source'] not in ('ecosense', 'home_assistant', 'manual'):
                raise ValueError('Unsupported radon source')
            if not isinstance(row.get('name'), str) or not 1 <= len(row['name']) <= 200:
                raise ValueError('Invalid radon name')
            value, normalized = row.get('measured_value'), row.get('radon_bq_m3')
            try:
                if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0
                       for v in (value, normalized)):
                    raise ValueError('Invalid radon measurement')
            except OverflowError as exc:
                raise ValueError('Invalid radon measurement') from exc
            if row.get('measured_unit') not in ('pCi/L', 'Bq/m3'):
                raise ValueError('Invalid radon unit')
            expected = float(value) * 37 if row['measured_unit'] == 'pCi/L' else float(value)
            if not math.isfinite(expected) or not math.isclose(normalized, expected, rel_tol=1e-12, abs_tol=1e-12):
                raise ValueError('Inconsistent radon unit conversion')
            received = row.get('received_at')
            if not isinstance(received, str) or len(received) > 100:
                raise ValueError('Invalid radon receipt time')
            if datetime.fromisoformat(received).tzinfo is None:
                raise ValueError('Radon receipt time requires a timezone')
    if previous != cursor:
        raise ValueError('Radon page does not reach its cursor')
    conn = energy._connect()
    try:
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            if conn.execute('SELECT COUNT(*) FROM radon_readings').fetchone()[0]:
                raise ValueError('Refusing to synchronize into locally collected radon history')
            state = conn.execute('SELECT * FROM radon_sync_cache_state WHERE singleton=1').fetchone()
            energy_state = conn.execute('SELECT source_id FROM sync_cache_state WHERE singleton=1').fetchone()
            if (state and state['source_id'] != identity) or (energy_state and energy_state[0] != identity):
                raise ValueError('Collector identity changed; use a fresh cache')
            if state and state['generation_id'] != generation:
                raise ValueError('Radon checkpoint changed; rebuild cache')
            if after != (state['cursor'] if state else 0):
                raise ValueError('Radon cursor changed; reload and retry')
            for row in rows:
                key = tuple(row[k] for k in ('source', 'sensor_id', 'timestamp'))
                if row['operation'] == 'delete':
                    conn.execute('DELETE FROM radon_cached_readings WHERE source=? AND sensor_id=? AND timestamp=?', key)
                else:
                    conn.execute('INSERT OR REPLACE INTO radon_cached_readings VALUES (?,?,?,?,?,?,?,?)',
                                 tuple(row[k] for k in ('source', 'sensor_id', 'timestamp', 'name',
                                       'measured_value', 'measured_unit', 'radon_bq_m3', 'received_at')))
            synchronized = datetime.now(timezone.utc).isoformat() if not page['has_more'] else (
                state['synchronized_at'] if state else None)
            conn.execute('INSERT OR REPLACE INTO radon_sync_cache_state VALUES (1,?,?,?,?,?)',
                         (identity, generation, cursor, watermark, synchronized))
    finally:
        conn.close()
    return get_cache_status()


def get_changes(after: int = 0, limit: int = 500) -> dict:
    """Export ordered observations and tombstones from one consistent snapshot."""
    if isinstance(after, bool) or not isinstance(after, int) or not 0 <= after <= 2**63 - 1:
        raise ValueError('after must be a nonnegative SQLite integer')
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise ValueError('limit must be between 1 and 1000')
    conn = energy._connect()
    try:
        conn.execute('BEGIN')
        source_id = conn.execute('SELECT source_id FROM collector_identity').fetchone()[0]
        generation = conn.execute('SELECT generation_id FROM radon_stream_generation').fetchone()[0]
        watermark = conn.execute('SELECT COALESCE(MAX(sequence),0) FROM radon_changes').fetchone()[0]
        if after > watermark:
            raise ValueError('Cursor is ahead of this collector')
        rows = [dict(row) for row in conn.execute(
            'SELECT * FROM radon_changes WHERE sequence>? AND sequence<=? '
            'ORDER BY sequence LIMIT ?', (after, watermark, limit),
        )]
        cursor = rows[-1]['sequence'] if rows else after
        return {'protocol_version': 1, 'source_id': source_id, 'generation_id': generation, 'changes': rows,
                'after_cursor': after, 'next_cursor': cursor, 'high_watermark': watermark,
                'has_more': cursor < watermark}
    finally:
        conn.close()


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
            conn.execute('BEGIN IMMEDIATE')
            if conn.execute('SELECT 1 FROM radon_sync_cache_state WHERE singleton=1').fetchone():
                raise ValueError('Refusing local ingestion into a remote radon cache')
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
            _compact_journal(conn, 100_000)
        return {'inserted': inserted, 'duplicates': len(validated) - inserted}
    finally:
        conn.close()


def _compact_journal(conn, max_entries: int) -> bool:
    changes = conn.execute('SELECT COUNT(*) FROM radon_changes').fetchone()[0]
    current = conn.execute('SELECT COUNT(*) FROM radon_readings').fetchone()[0]
    if changes <= max(max_entries, current * 2):
        return False
    conn.execute('DELETE FROM radon_changes')
    conn.execute("DELETE FROM sqlite_sequence WHERE name='radon_changes'")
    conn.execute('''INSERT INTO radon_changes(operation,source,sensor_id,timestamp,name,
        measured_value,measured_unit,radon_bq_m3,received_at)
        SELECT 'upsert',source,sensor_id,timestamp,name,measured_value,measured_unit,
               radon_bq_m3,received_at FROM radon_readings ORDER BY timestamp,source,sensor_id''')
    conn.execute('UPDATE radon_stream_generation SET generation_id=lower(hex(randomblob(16)))')
    return True


def compact_journal(max_entries: int = 100_000) -> bool:
    if type(max_entries) is not int or max_entries < 1:
        raise ValueError('max_entries must be positive')
    conn = energy._connect()
    try:
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            return _compact_journal(conn, max_entries)
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
        table = _history_table(conn)
        return [dict(row) for row in conn.execute(f'''SELECT * FROM {table}
            WHERE source=? AND sensor_id=? AND timestamp>=? AND timestamp<=?
            ORDER BY timestamp''', (source, sensor_id, (now-timedelta(days=days)).isoformat(),
                                   now.isoformat()))]
    finally:
        conn.close()


def get_sensors() -> list[dict]:
    """List sensor identities that have recorded observations, never discovered guesses."""
    conn = energy._connect()
    try:
        table = _history_table(conn)
        return [dict(row) for row in conn.execute(f'''SELECT source,sensor_id,
            MAX(timestamp) AS last_measurement,COUNT(*) AS sample_count
            FROM {table} GROUP BY source,sensor_id ORDER BY source,sensor_id''')]
    finally:
        conn.close()


def _history_table(conn) -> str:
    cached = conn.execute('SELECT 1 FROM radon_sync_cache_state WHERE singleton=1').fetchone()
    return 'radon_cached_readings' if cached else 'radon_readings'


def observation_chart(rows: list[dict], now: datetime | None = None, days: int = 7) -> dict:
    """Plot recorded samples, breaking connecting lines at gaps over three hours."""
    if isinstance(days, bool) or days not in (1, 7, 30, 365):
        raise ValueError('Invalid history window')
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('now requires a timezone')
    end = now.astimezone(timezone.utc)
    start = end - timedelta(days=days)
    samples = sorted(
        ((datetime.fromisoformat(row['timestamp']).astimezone(timezone.utc),
          row['radon_bq_m3']) for row in rows),
        key=lambda sample: sample[0],
    )
    samples = [(stamp, value) for stamp, value in samples if start <= stamp <= end]
    if samples:
        # Fit the observed time range so a few hours of new history remain
        # distinguishable even when the user selects Week, Month or Year.
        first, last = samples[0][0], samples[-1][0]
        start = max(start, first - timedelta(minutes=5))
        end = min(end, last + timedelta(minutes=5))
        if end <= start:
            start = end - timedelta(minutes=1)
    scale = max((value for _, value in samples), default=1) or 1
    span = (end - start).total_seconds()
    points = [{
        'timestamp': stamp.isoformat(), 'value': value,
        'x': 50 + 900 * (stamp - start).total_seconds() / span,
        'y': 180 - 150 * value / scale,
    } for stamp, value in samples]
    segments = []
    segment = []
    for index, point in enumerate(points):
        if index and samples[index][0] - samples[index - 1][0] > timedelta(hours=3):
            if len(segment) > 1:
                segments.append(segment)
            segment = []
        segment.append(point)
    if len(segment) > 1:
        segments.append(segment)
    return {'points': points, 'trend_segments': segments, 'scale': scale,
            'start': start.isoformat(), 'end': end.isoformat()}


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


def get_latest_indicator() -> dict | None:
    """Read one stored measurement for the circuit menu; never contact the sensor."""
    conn = energy._connect()
    try:
        table = _history_table(conn)
        row = conn.execute(
            f"SELECT name, timestamp, radon_bq_m3 FROM {table} ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        stamp = datetime.fromisoformat(row["timestamp"])
        age = (datetime.now(timezone.utc) - stamp).total_seconds()
        return {"pci_l": row["radon_bq_m3"] / 37, "timestamp": row["timestamp"],
                "name": row["name"], "stale": age > 10800 or age < -300}
    finally:
        conn.close()
