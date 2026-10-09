"""Durable completed-history scheduling using caller-owned transactions/connections."""

import json
import secrets
from datetime import timedelta
from zoneinfo import ZoneInfo

from completed_history import resolve_chart_scope
from energy_clock import EnergyClock


def _locked(conn):
    if not conn.in_transaction:
        raise RuntimeError('History scheduling requires an existing transaction')


def _integer(value, low, high, name):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'Invalid {name}')


def _policy(clock, zone):
    if clock:
        if zone is not None:
            raise ValueError('UTC collection must not declare a legacy storage timezone')
        return 'utc_v1', 'UTC'
    if not isinstance(zone, str):
        raise ValueError('Legacy collection requires a reviewed storage timezone')
    ZoneInfo(zone)
    return 'legacy_local_v1', zone


def configure(conn, gid, number, start, clock, *, legacy_storage_timezone,
              window_minutes, settling_seconds, retry_seconds, requests_hour, requests_day, now):
    _locked(conn)
    gid, number, name = resolve_chart_scope(conn, {'device_gid': gid, 'channel_num': number})
    start = EnergyClock.instant(start)
    if start.second or start.microsecond or start > EnergyClock.instant(now):
        raise ValueError('Collection start must be an explicit completed minute boundary')
    for value, low, high, label in ((window_minutes, 1, 360, 'window minutes'),
            (settling_seconds, 60, 86400, 'settling delay'), (retry_seconds, 60, 86400, 'retry delay'),
            (requests_hour, 1, 10000, 'hourly request budget'), (requests_day, 1, 100000, 'daily request budget')):
        _integer(value, low, high, label)
    if requests_hour > requests_day:
        raise ValueError('Hourly request budget exceeds daily budget')
    storage, zone = _policy(clock, legacy_storage_timezone)
    config = (gid, number, name, EnergyClock.stamp(start), storage, zone,
              window_minutes, settling_seconds, retry_seconds)
    previous = conn.execute('SELECT * FROM energy_history_channels WHERE device_gid=? AND channel_num=?',
                            (gid, number)).fetchone()
    fields = ('device_gid', 'channel_num', 'channel_name', 'start_utc', 'storage_format',
              'storage_timezone', 'window_minutes', 'settling_seconds', 'retry_seconds')
    if previous and tuple(previous[field] for field in fields) != config:
        raise ValueError('Collection scope/configuration changed; explicit cutover review is required')
    if not previous and conn.execute('SELECT COUNT(*) FROM energy_history_channels').fetchone()[0] >= 256:
        raise ValueError('Review collection capacity before adding more channels')
    # Unknown receipt-time history cannot be silently adopted or discarded.
    if not previous and conn.execute('''SELECT 1 FROM readings r
        LEFT JOIN csv_reading_projection c ON c.reading_id=r.id
        LEFT JOIN energy_reading_projection p ON p.reading_id=r.id
        WHERE r.device_gid=? AND r.channel_name=? AND c.reading_id IS NULL AND p.reading_id IS NULL LIMIT 1''',
        (gid, name)).fetchone():
        raise ValueError('Review unowned channel history before enabling completed collection')
    limits = conn.execute('SELECT requests_hour,requests_day FROM energy_history_limits').fetchone()
    if limits and tuple(limits) != (requests_hour, requests_day):
        raise ValueError('Persistent request budget changed; explicit review is required')
    conn.execute('INSERT OR IGNORE INTO energy_history_limits VALUES (1,?,?)', (requests_hour, requests_day))
    conn.execute('''INSERT OR IGNORE INTO energy_history_channels
        (device_gid,channel_num,channel_name,start_utc,storage_format,storage_timezone,window_minutes,
         settling_seconds,retry_seconds,scan_cursor_utc,enabled,configured_at_utc)
        VALUES (?,?,?,?,?,?,?,?,?,?,1,?)''', (*config, config[3], EnergyClock.stamp(now)))


def owns_live_channel(conn, gid, number, name):
    """Promotion is sticky, even when acquisition is paused: never mix live/history again."""
    row = conn.execute('SELECT channel_name FROM energy_history_channels WHERE device_gid=? AND channel_num=?',
                       (str(gid), str(number))).fetchone()
    if row and row[0] != name:
        raise ValueError('Configured channel was renamed; explicit identity review is required')
    return row is not None


def set_enabled(conn, gid, number, enabled):
    _locked(conn)
    if type(enabled) is not bool:
        raise ValueError('Enabled must be boolean')
    changed = conn.execute('UPDATE energy_history_channels SET enabled=? WHERE device_gid=? AND channel_num=?',
                          (int(enabled), gid, number)).rowcount
    if not changed:
        raise ValueError('Unknown collection channel')
    if not enabled:
        conn.execute('UPDATE energy_history_jobs SET lease_id=NULL,lease_until_utc=NULL WHERE device_gid=? AND channel_num=?',
                     (gid, number))


def reserve(conn, now, clock, *, request_weight):
    _locked(conn)
    _integer(request_weight, 1, 20, 'request retry allowance')
    stamp = EnergyClock.stamp(now)
    latest = conn.execute('SELECT MAX(started_at_utc) FROM energy_history_attempts').fetchone()[0]
    if latest and stamp < latest:
        raise ValueError('Collector clock regressed; no history request reserved')
    conn.execute('''UPDATE energy_history_jobs SET lease_id=NULL,lease_until_utc=NULL
        WHERE lease_until_utc<=?''', (stamp,))
    limits = conn.execute('SELECT * FROM energy_history_limits').fetchone()
    if not limits:
        return None
    if request_weight > min(limits['requests_hour'], limits['requests_day']):
        raise ValueError('SDK retry allowance exceeds the configured request budget')
    for hours, field in ((1, 'requests_hour'), (24, 'requests_day')):
        used = conn.execute('SELECT COALESCE(SUM(request_weight),0) FROM energy_history_attempts WHERE started_at_utc>?',
                            (EnergyClock.stamp(now - timedelta(hours=hours)),)).fetchone()[0]
        if used + request_weight > limits[field]:
            return None
    channels = conn.execute('''SELECT * FROM energy_history_channels WHERE enabled=1
        ORDER BY COALESCE(last_attempt_utc,''),device_gid,channel_num''').fetchall()
    for channel in channels:
        gid, number = channel['device_gid'], channel['channel_num']
        if conn.execute('SELECT 1 FROM energy_history_jobs WHERE device_gid=? AND channel_num=? AND lease_id IS NOT NULL',
                        (gid, number)).fetchone():
            continue
        _, _, name = resolve_chart_scope(conn, {'device_gid': gid, 'channel_num': number})
        storage, zone = _policy(clock, None if clock else channel['storage_timezone'])
        if (name != channel['channel_name'] or storage != channel['storage_format']
                or zone != channel['storage_timezone']):
            raise ValueError('Collection identity/storage policy changed; review before resuming')
        head = conn.execute('''SELECT * FROM energy_history_jobs WHERE device_gid=? AND channel_num=?
            AND start_utc=?''', (gid, number, channel['scan_cursor_utc'])).fetchone()
        repair = conn.execute('''SELECT * FROM energy_history_jobs WHERE device_gid=? AND channel_num=?
            AND status IN ('error','gap') AND start_utc<? AND next_attempt_utc<=?
            ORDER BY start_utc LIMIT 1''', (gid, number, channel['scan_cursor_utc'], stamp)).fetchone()
        if not repair:
            # A settling delay is not cloud finality: recheck recent completed
            # windows with the same durable budgets and immutable source selector.
            repair = conn.execute('''SELECT * FROM energy_history_jobs WHERE device_gid=? AND channel_num=?
                AND status='complete' AND end_utc>? AND next_attempt_utc<=?
                ORDER BY end_utc DESC LIMIT 1''',
                (gid, number, EnergyClock.stamp(now - timedelta(hours=2)), stamp)).fetchone()
        start = EnergyClock.parse(channel['scan_cursor_utc'])
        limit = (EnergyClock.instant(now) - timedelta(seconds=channel['settling_seconds'])).replace(second=0, microsecond=0)
        end = min(start + timedelta(minutes=channel['window_minutes']), limit)
        head_due = head and head['status'] in ('pending', 'error', 'gap') and head['next_attempt_utc'] <= stamp
        new_head = not head and end > start
        # Old null gaps cannot consume every slot and starve current collection.
        repair_turn = channel['reservation_count'] % 4 == 3
        job = repair if repair and (repair_turn or not (head_due or new_head)) else head if head_due else None
        if not job and new_head:
            job_id = secrets.token_hex(16)
            conn.execute('''INSERT INTO energy_history_jobs
                (id,device_gid,channel_num,start_utc,end_utc,status,attempts,next_attempt_utc)
                VALUES (?,?,?,?,?,'pending',0,?)''',
                (job_id, gid, number, EnergyClock.stamp(start), EnergyClock.stamp(end), stamp))
            job = conn.execute('SELECT * FROM energy_history_jobs WHERE id=?', (job_id,)).fetchone()
        if not job:
            continue
        token = secrets.token_hex(16)
        conn.execute('''UPDATE energy_history_jobs SET attempts=attempts+1,lease_id=?,lease_until_utc=? WHERE id=?''',
                     (token, EnergyClock.stamp(now + timedelta(minutes=15)), job['id']))
        conn.execute('INSERT INTO energy_history_attempts VALUES (?,?,?,?)', (token, job['id'], stamp, request_weight))
        conn.execute('UPDATE energy_history_channels SET last_attempt_utc=?,reservation_count=reservation_count+1 WHERE device_gid=? AND channel_num=?',
                     (stamp, gid, number))
        return {**dict(channel), **dict(job), 'lease_id': token, 'attempts': job['attempts'] + 1}
    return None


def check_lease(conn, lease, now, clock):
    _locked(conn)
    job = conn.execute('''SELECT j.*,c.enabled,c.storage_format,c.storage_timezone,c.channel_name,
        c.settling_seconds,c.retry_seconds FROM energy_history_jobs j JOIN energy_history_channels c
        ON c.device_gid=j.device_gid AND c.channel_num=j.channel_num WHERE j.id=?''', (lease['id'],)).fetchone()
    stamp = EnergyClock.stamp(now)
    if (not job or not job['enabled'] or job['lease_id'] != lease['lease_id']
            or job['lease_until_utc'] <= stamp):
        raise RuntimeError('History lease expired or superseded; inspect durable job state')
    attempt = conn.execute('SELECT started_at_utc FROM energy_history_attempts WHERE token=?', (lease['lease_id'],)).fetchone()
    if not attempt or stamp < attempt[0]:
        raise RuntimeError('Collector clock regressed during acquisition')
    _, _, name = resolve_chart_scope(conn, {'device_gid': job['device_gid'], 'channel_num': job['channel_num']})
    storage, zone = _policy(clock, None if clock else job['storage_timezone'])
    if name != job['channel_name'] or (storage, zone) != (job['storage_format'], job['storage_timezone']):
        raise ValueError('Collection scope/storage changed during acquisition')
    return dict(job)


def finish(conn, lease, now, clock, result=None, *, error_type=None):
    job = check_lease(conn, lease, now, clock)
    status = 'error' if error_type else ('review' if result['warnings'] else
                                       'gap' if result['missing_completed_buckets'] else 'complete')
    delay = min(86400, job['retry_seconds'] * 2 ** min(job['attempts'] - 1, 6))
    if status == 'complete':
        delay = max(1800, job['retry_seconds'])
    outcome = {'status': status, 'error_type': error_type} if error_type else result
    conn.execute('INSERT INTO energy_history_attempt_results VALUES (?,?,?,?)',
                 (lease['lease_id'], EnergyClock.stamp(now), result['source_id'] if result else None,
                  json.dumps(outcome, sort_keys=True, allow_nan=False)))
    conn.execute('''UPDATE energy_history_jobs SET status=?,next_attempt_utc=?,lease_id=NULL,
        lease_until_utc=NULL,source_id=?,result_json=? WHERE id=?''',
        (status, EnergyClock.stamp(now + timedelta(seconds=delay)), result['source_id'] if result else None,
         json.dumps(outcome, sort_keys=True, allow_nan=False), job['id']))
    if result:
        conn.execute('''UPDATE energy_history_channels SET scan_cursor_utc=MAX(scan_cursor_utc,?)
            WHERE device_gid=? AND channel_num=?''', (job['end_utc'], job['device_gid'], job['channel_num']))
    return status


def status(conn):
    channels = []
    for row in conn.execute('SELECT * FROM energy_history_channels ORDER BY device_gid,channel_num'):
        channel = dict(row)
        jobs = [dict(job) for job in conn.execute('''SELECT id,start_utc,end_utc,status,attempts,
            next_attempt_utc,lease_until_utc,source_id FROM energy_history_jobs
            WHERE device_gid=? AND channel_num=? ORDER BY start_utc DESC LIMIT 100''', (row['device_gid'], row['channel_num']))]
        incomplete = conn.execute('''SELECT MIN(start_utc) FROM energy_history_jobs
            WHERE device_gid=? AND channel_num=? AND status!='complete' ''',
            (row['device_gid'], row['channel_num'])).fetchone()[0]
        counts = {item['status']: item['count'] for item in conn.execute('''SELECT status,COUNT(*) count
            FROM energy_history_jobs WHERE device_gid=? AND channel_num=? GROUP BY status''',
            (row['device_gid'], row['channel_num']))}
        channel.update(verified_until_utc=min(row['scan_cursor_utc'], incomplete or row['scan_cursor_utc']),
                       jobs=list(reversed(jobs)), job_counts=counts, jobs_truncated=sum(counts.values()) > 100)
        channels.append(channel)
    limits = conn.execute('SELECT requests_hour,requests_day FROM energy_history_limits').fetchone()
    return {'channels': channels, 'request_limits': dict(limits) if limits else None,
            'continuous_capture_verified': False}
