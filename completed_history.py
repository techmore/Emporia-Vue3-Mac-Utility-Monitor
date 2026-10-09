"""Raw completed chart acquisition and shared historical publication.

Connections/schema belong to energy; live-list samples are never guessed into
completed chart buckets. Raw response bytes and request identities stay private.
"""

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from csv_projection import identity, publish_intervals, record_source_order
from device_identity import canonical_id, resolve_export
from emporia_history import validate_completed_minutes
from energy_clock import EnergyClock
from timestamp_model import classify_timestamp


def record_channels(conn, devices, normalize, received: str) -> None:
    for device in devices:
        record_channel_names(conn, device.device_gid,
            ((channel.channel_num, channel) for channel in device.channels), normalize, received)


def record_channel_names(conn, device_gid, channels, normalize, received: str) -> None:
    """Accept only provider-supplied labels, never guess Main from a channel number."""
    gid = canonical_id(device_gid)
    if not conn.execute('SELECT 1 FROM device_identities WHERE canonical_gid=?', (gid,)).fetchone():
        return
    for number, channel in channels:
        name = normalize(getattr(channel, 'name', None))
        if name:
            conn.execute('INSERT OR IGNORE INTO energy_channel_claims VALUES (?,?,?,?)',
                         (gid, canonical_id(number), name, received))


def resolve_chart_scope(conn, request) -> tuple[str, str, str]:
    gid, number = canonical_id(request.get('device_gid')), canonical_id(request.get('channel_num'))
    resolve_export(conn, gid.upper(), gid, True)
    names = {row[0] for row in conn.execute(
        'SELECT channel_name FROM energy_channel_claims WHERE device_gid=? AND channel_num=?', (gid, number))}
    if len(names) != 1:
        raise ValueError('Chart channel identity is unknown or renamed; explicit review is required')
    name = next(iter(names))
    numbers = {row[0] for row in conn.execute(
        'SELECT channel_num FROM energy_channel_claims WHERE device_gid=? AND channel_name=?', (gid, name))}
    if numbers != {number}:
        raise ValueError('Chart channel label is ambiguous; explicit review is required')
    return gid, number, name


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def publish_chart(conn, capture: dict, content: bytes, clock, rate: float, *,
                  legacy_storage_timezone=None, settling_seconds=300) -> dict:
    if not conn.in_transaction:
        raise RuntimeError('Chart publication requires an existing transaction')
    if not isinstance(content, bytes) or not 0 < len(content) <= 2_000_000:
        raise ValueError('Invalid raw chart response size/type')
    report = validate_completed_minutes(capture, settling_seconds=settling_seconds)
    response = json.loads(content)
    if _json(response) != _json(capture['response']):
        raise ValueError('Captured response disagrees with original bytes')
    request = capture['request']
    if set(request) != {'device_gid', 'channel_num', 'start', 'end', 'scale', 'unit'}:
        raise ValueError('Explicit chart request scope is required')
    gid, number, name = resolve_chart_scope(conn, request)
    if type(rate) not in (int, float) or not math.isfinite(rate) or rate < 0:
        raise ValueError('Invalid historical pricing basis')
    zone = 'UTC' if clock else legacy_storage_timezone
    if not isinstance(zone, str):
        raise ValueError('Legacy chart publication requires a reviewed storage timezone')
    local_zone = ZoneInfo(zone)
    digest = hashlib.sha256(content).hexdigest()
    request_json = _json(request)
    batch_id = identity('emporia_chart_v1', request_json, report['received_at_utc'],
                        digest, name, zone, settling_seconds)
    conn.execute('''INSERT OR IGNORE INTO energy_source_batches
        (id,kind,sha256,content,request_json,received_at_utc,device_gid,channel_num,channel_name,
         source_timezone,rate_cents,settling_seconds) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
        (batch_id, 'emporia_chart_v1', digest, content, request_json, report['received_at_utc'],
         gid, number, name, zone, rate, settling_seconds))
    # Exact retries keep the original import-time rate, not today's edited setting.
    stored_rate = conn.execute('SELECT rate_cents FROM energy_source_batches WHERE id=?', (batch_id,)).fetchone()[0]
    observations = []
    for bucket in report['buckets']:
        start = bucket['start_utc']
        wall = datetime.fromisoformat(start).astimezone(local_zone).replace(tzinfo=None).isoformat()
        # Legacy wall storage cannot represent a fold. Keep raw evidence but do
        # not publish either ambiguous hour until coordinated UTC activation.
        local = wall if classify_timestamp(wall, zone)['status'] == 'legacy_unique' else None
        cost = bucket['usage_kwh'] * stored_rate
        if not math.isfinite(cost):
            raise ValueError('Historical cost overflow')
        observations.append({'id': identity(batch_id, bucket['source_index']), 'batch_id': batch_id,
            'source_index': bucket['source_index'], 'channel_num': number, 'channel_name': name,
            'source_local_timestamp': local, 'source_utc_timestamp': start,
            'start_utc': start, 'end_utc': bucket['end_utc'], 'usage_kwh': bucket['usage_kwh'],
            'cost_cents': cost, 'measurement_seconds': 60, 'measurement_source': 'emporia_chart',
            'provider_timestamp': start, 'projection_table': 'energy_reading_projection'})
    recorded = conn.executemany('''INSERT INTO energy_source_observations
        (id,batch_id,source_index,channel_num,channel_name,source_local_timestamp,source_utc_timestamp,
         start_utc,end_utc,usage_kwh,cost_cents,measurement_seconds,measurement_source,provider_timestamp)
        SELECT :id,:batch_id,:source_index,:channel_num,:channel_name,:source_local_timestamp,:source_utc_timestamp,
               :start_utc,:end_utc,:usage_kwh,:cost_cents,:measurement_seconds,:measurement_source,:provider_timestamp
        WHERE NOT EXISTS (SELECT 1 FROM energy_source_observations WHERE id=:id)''',
        observations).rowcount if observations else 0
    record_source_order(conn, 'emporia_chart_v1', batch_id)
    projection = publish_intervals(conn, gid, observations, clock, recorded=recorded)
    return {**{key: value for key, value in report.items() if key != 'buckets'}, **projection,
            'source_id': batch_id, 'device_gid': gid, 'channel_num': number, 'channel_name': name,
            'raw_response_sha256': digest, 'publication_performed': True,
            'timestamp_format': 'utc_v1' if clock else 'legacy_local_v1',
            'legacy_storage_timezone': None if clock else zone}


def capture_chart(vue, device_gid, channel_num, start: datetime, end: datetime) -> tuple[dict, bytes]:
    """One SDK-authenticated GET; do not use its chart helper's anchor fallback."""
    start, end = EnergyClock.instant(start), EnergyClock.instant(end)
    if start >= end or end - start > timedelta(days=7):
        raise ValueError('Invalid chart acquisition window')
    gid, number = canonical_id(device_gid), canonical_id(channel_num)
    request = {'device_gid': gid, 'channel_num': number, 'start': EnergyClock.stamp(start),
               'end': EnergyClock.stamp(end), 'scale': '1MIN', 'unit': 'KilowattHours'}
    parameters = {'apiMethod': 'getChartUsage', 'deviceGid': gid, 'channel': number,
                  'start': start.isoformat().replace('+00:00', 'Z'),
                  'end': end.isoformat().replace('+00:00', 'Z'),
                  'scale': '1MIN', 'energyUnit': 'KilowattHours'}
    response = vue.auth.request('get', 'AppAPI?' + urlencode(parameters))
    try:
        response.raise_for_status()
        received = datetime.now(timezone.utc)
        content = response.content
        if not isinstance(content, bytes) or not 0 < len(content) <= 2_000_000:
            raise ValueError('Invalid raw chart response size/type')
        parsed = json.loads(content)
    finally:
        response.close()
    return {'schema': 'emporia_chart_v1', 'request': request,
            'received_at': EnergyClock.stamp(received), 'response': parsed}, content
