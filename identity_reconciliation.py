"""Reviewed offline CSV identity binding and append-only reversal.

Only verified snapshot copies are changed. Raw evidence, reading values/IDs and
original journal entries never change. This does not repair source disagreements
or install a production database. Reconcile legacy identity before UTC conversion.
"""

import csv
import hashlib
import io
import json
import math
import re
import tempfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from csv_projection import _bounds, _unmanaged_bounds, identity
from device_identity import canonical_id
from timestamp_model import classify_timestamp
from verified_snapshot import file_sha256, verified_copy

REVIEW_TABLES = ('csv_identity_reviews', 'csv_identity_review_batches', 'csv_identity_review_rows')
MUTABLE_TABLES = ('readings', 'reading_changes', 'latest_channel_snapshot', 'device_capabilities')


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=True).encode()).hexdigest()


def _name(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _table_digest(conn, table, *, omit=(), where='', parameters=()) -> str:
    fields = conn.execute(f'PRAGMA table_info({_name(table)})').fetchall()
    columns = [field['name'] for field in fields if field['name'] not in omit]
    keys = [field['name'] for field in sorted(fields, key=lambda field: field['pk']) if field['pk']]
    selected = ','.join(map(_name, columns))
    order = ','.join(map(_name, keys or columns))
    digest = hashlib.sha256(json.dumps(columns).encode())
    for row in conn.execute(f'SELECT {selected} FROM {_name(table)} {where} ORDER BY {order}', parameters):
        values = [{'blob_sha256': hashlib.sha256(value).hexdigest(), 'length': len(value)}
                  if isinstance(value, bytes) else value for value in row]
        digest.update(json.dumps(values, separators=(',', ':'), ensure_ascii=True).encode())
        digest.update(b'\n')
    return digest.hexdigest()


def _tables(conn) -> list[str]:
    return [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name!='sqlite_sequence' ORDER BY name")]


def _scope_rows(conn, table, gids) -> list[dict]:
    order = 'device_gid,channel_name' if table == 'latest_channel_snapshot' else 'device_gid'
    return [dict(row) for row in conn.execute(
        f'SELECT * FROM {table} WHERE device_gid IN (?,?) ORDER BY {order}', gids)]


def _validate_batch(conn, batch: dict) -> int:
    # Reuse the actual importer helpers, not a separate guess about units or DST.
    import energy

    content = batch['content']
    if not isinstance(content, bytes) or hashlib.sha256(content).hexdigest() != batch['sha256']:
        raise ValueError('CSV source hash mismatch')
    stem = Path(batch['original_filename']).stem
    prefix, interval = stem.split('-')[0].upper(), stem.split('-')[-1].upper()
    if prefix != batch['device_gid'].upper() or interval != batch['interval']:
        raise ValueError('Original CSV identity/interval requires separate review')
    batch_ids = {identity('csv_source_v1', batch['device_gid'], interval, batch['sha256']),
                 identity('csv_source_v2', batch['device_gid'], prefix, interval, batch['sha256'])}
    if batch['id'] not in batch_ids:
        raise ValueError('Invalid immutable batch identity')
    rate = batch['rate_cents']
    if type(rate) not in (int, float) or not math.isfinite(rate) or rate < 0:
        raise ValueError('Invalid source pricing basis')
    with io.StringIO(content.decode('utf-8-sig'), newline='') as handle:
        reader = csv.DictReader(handle)
        headers = reader.fieldnames or []
        if len(headers) < 2 or len(set(headers)) != len(headers) or headers != json.loads(batch['headers_json']):
            raise ValueError('Invalid original CSV headers')
        zone = energy._csv_source_timezone(headers[0])
        if zone is None or zone != batch['source_timezone']:
            raise ValueError('CSV source timezone is unresolved')
        columns = {}
        for header in headers[1:]:
            match = re.search(r' \((kWatts|kW|kWhs|kWh)\)$', header.strip())
            if not match:
                raise ValueError('Invalid original CSV unit')
            columns[header] = (energy._clean_csv_channel_name(header), match.group(1))
        if len({name for name, _unit in columns.values()}) != len(columns) or any(not name for name, _unit in columns.values()):
            raise ValueError('Ambiguous original CSV channels')
        cursor = iter(conn.execute('SELECT * FROM csv_source_observations WHERE batch_id=? ORDER BY row_number,source_header', (batch['id'],)))
        observation = next(cursor, None)
        count = 0
        for number, raw_row in enumerate(reader, start=2):
            if observation is None:
                break
            if observation['row_number'] < number:
                raise ValueError('Source observation is not in the original file')
            if observation['row_number'] != number:
                continue
            if None in raw_row:
                raise ValueError('Invalid source row')
            stamp = raw_row.get(headers[0]) or ''
            moment = datetime.strptime(stamp.strip(), '%m/%d/%Y %H:%M:%S')
            resolved = classify_timestamp(moment.isoformat(), zone)
            duration = energy._csv_interval_seconds(interval, moment, zone)
            if resolved['status'] != 'legacy_unique' or duration is None:
                raise ValueError('Unresolved original source bounds')
            start = resolved['utc_candidates'][0]
            end = (datetime.fromisoformat(start) + timedelta(seconds=duration)).isoformat(timespec='microseconds')
            while observation is not None and observation['row_number'] == number:
                header = observation['source_header']
                if header not in columns:
                    raise ValueError('Source observation header is not in the file')
                name, unit = columns[header]
                value = raw_row.get(header) or ''
                raw = float(value.strip())
                power = unit in ('kW', 'kWatts')
                # Match the importer's arithmetic order exactly, including original cents.
                kwh = raw * (duration / 3600) if power else raw
                cents = kwh * rate
                if not all(math.isfinite(value) for value in (raw, kwh, cents)):
                    raise ValueError('Invalid original source value')
                expected = {'id': identity(batch['id'], number, header), 'channel_name': name,
                            'source_unit': unit, 'raw_timestamp': stamp, 'raw_value': value,
                            'source_local_timestamp': moment.isoformat(), 'source_utc_timestamp': start,
                            'start_utc': start, 'end_utc': end, 'usage_kwh': kwh, 'cost_cents': cents,
                            'measurement_seconds': duration, 'measurement_source': 'csv_power' if power else 'csv_energy'}
                if any(observation[key] != wanted for key, wanted in expected.items()):
                    raise ValueError('Source evidence disagrees with original CSV')
                count += 1
                observation = next(cursor, None)
        if observation is not None:
            raise ValueError('Source observation is beyond the original file')
        return count


def _reading_digest(row: dict) -> str:
    return _hash({key: value for key, value in row.items() if key != 'device_gid'})


def _plan(conn, snapshot_sha: str, source_gid, target_gid, rollback_review_id):
    blockers = Counter()
    original_review = None
    if rollback_review_id is not None:
        found = conn.execute('SELECT * FROM csv_identity_reviews WHERE id=? AND action=\'bind\'', (rollback_review_id,)).fetchone()
        if not found:
            raise ValueError('Unknown reversible identity review')
        original_review = dict(found)
        source_gid, target_gid = original_review['canonical_gid'], original_review['source_gid']
    source_gid, target_gid = canonical_id(source_gid), canonical_id(target_gid)
    if source_gid == target_gid or '81134' in (source_gid, target_gid):
        raise ValueError('Distinct non-ghost monitor identities are required')
    if original_review:
        decisions = [dict(row) for row in conn.execute('SELECT * FROM csv_identity_review_batches WHERE review_id=? ORDER BY batch_id', (rollback_review_id,))]
        batch_ids = [row['batch_id'] for row in decisions]
        expected_rows = [dict(row) for row in conn.execute('SELECT * FROM csv_identity_review_rows WHERE review_id=? ORDER BY reading_id', (rollback_review_id,))]
        rows = []
        for expected in expected_rows:
            reading = conn.execute('SELECT * FROM readings WHERE id=?', (expected['reading_id'],)).fetchone()
            member = conn.execute('SELECT observation_id FROM csv_reading_projection WHERE reading_id=?', (expected['reading_id'],)).fetchone()
            if (not reading or reading['device_gid'] != source_gid or _reading_digest(dict(reading)) != expected['reading_sha256']
                    or not member or member[0] != expected['observation_id']):
                blockers['projection_changed_since_review'] += 1
            if reading:
                rows.append(dict(reading))
        for batch_id in batch_ids:
            latest = conn.execute('SELECT review_id FROM csv_identity_review_batches WHERE batch_id=? ORDER BY sequence DESC LIMIT 1', (batch_id,)).fetchone()
            if not latest or latest[0] != rollback_review_id:
                blockers['review_no_longer_current'] += 1
        state = json.loads(original_review['state_json'])
        for table, key in (('latest_channel_snapshot', 'post_snapshot_sha256'), ('device_capabilities', 'post_capabilities_sha256')):
            if _hash(_scope_rows(conn, table, (source_gid, target_gid))) != state[key]:
                blockers['derived_state_changed_since_review'] += 1
    else:
        candidates = {row[0] for row in conn.execute('SELECT canonical_gid FROM device_identity_aliases WHERE alias=?', (source_gid.upper(),))}
        registered = conn.execute('SELECT 1 FROM device_identities WHERE canonical_gid=?', (target_gid,)).fetchone()
        if candidates != {target_gid} or not registered:
            blockers['identity_not_uniquely_discovered'] += 1
        batch_ids = [row[0] for row in conn.execute('SELECT id FROM csv_source_batches WHERE device_gid=? ORDER BY id', (source_gid,))]
        rows = [dict(row) for row in conn.execute('SELECT * FROM readings WHERE device_gid=? ORDER BY id', (source_gid,))]
        for batch_id in batch_ids:
            effective = conn.execute('SELECT device_gid FROM csv_effective_devices WHERE batch_id=?', (batch_id,)).fetchone()
            if effective[0] != source_gid:
                blockers['batch_already_reconciled'] += 1
    if not batch_ids:
        blockers['no_original_csv_evidence'] += 1
    validated = 0
    for batch_id in batch_ids:
        batch = dict(conn.execute('SELECT * FROM csv_source_batches WHERE id=?', (batch_id,)).fetchone())
        try:
            validated += _validate_batch(conn, batch)
        except (ValueError, UnicodeError, OverflowError, csv.Error):
            blockers['original_source_validation_failed'] += 1
    observations = {}
    for row in rows:
        observation = conn.execute('''SELECT o.* FROM csv_reading_projection p
            JOIN csv_source_observations o ON o.id=p.observation_id WHERE p.reading_id=?''', (row['id'],)).fetchone()
        if not observation or observation['batch_id'] not in batch_ids:
            blockers['unowned_source_history'] += 1
            continue
        observation = dict(observation)
        observations[row['id']] = observation
        expected = {key: observation[key] for key in ('channel_name', 'usage_kwh', 'cost_cents', 'measurement_seconds', 'measurement_source')}
        batch = conn.execute('SELECT source_timezone FROM csv_source_batches WHERE id=?', (observation['batch_id'],)).fetchone()
        expected.update(timestamp=observation['source_local_timestamp'], source_timezone=batch[0], channel_num=None, provider_timestamp=None)
        if any(row[key] != wanted for key, wanted in expected.items()) or _bounds(observation) is None:
            blockers['projection_disagrees_with_evidence'] += 1

    if not original_review:
        by_key = {(row['channel_name'], row['timestamp']): row for row in rows}
        for snapshot in conn.execute('SELECT * FROM latest_channel_snapshot WHERE device_gid=?', (source_gid,)):
            reading = by_key.get((snapshot['channel_name'], snapshot['timestamp']))
            if not reading or any(snapshot[key] != reading[key] for key in snapshot.keys() if key != 'device_gid'):
                blockers['source_snapshot_without_owned_evidence'] += 1

    intervals = {}
    for row in rows:
        if row['id'] in observations and _bounds(observations[row['id']]):
            intervals.setdefault(row['channel_name'], []).append(_bounds(observations[row['id']]))
    for channel, source_intervals in intervals.items():
        source_intervals.sort()
        if any(left[1] > right[0] for left, right in zip(source_intervals, source_intervals[1:], strict=False)):
            blockers['source_projection_overlaps'] += 1
        target_intervals = []
        for row in conn.execute('SELECT * FROM readings WHERE device_gid=? AND channel_name=?', (target_gid, channel)):
            bounds = _unmanaged_bounds(dict(row), None)
            if bounds is None:
                blockers['target_history_has_unknown_bounds'] += 1
            else:
                target_intervals.append(bounds)
        combined = sorted([(left, right, 'source') for left, right in source_intervals]
                          + [(left, right, 'target') for left, right in target_intervals])
        ends = {'source': '', 'target': ''}
        for left, right, owner in combined:
            if left < ends['target' if owner == 'source' else 'source']:
                blockers['cross_device_coverage_overlap'] += 1
            ends[owner] = max(ends[owner], right)
    for row in rows:
        if conn.execute('SELECT 1 FROM readings WHERE device_gid=? AND timestamp=? AND channel_name IS ?',
                        (target_gid, row['timestamp'], row['channel_name'])).fetchone():
            blockers['canonical_key_collision'] += 1
    report = {'schema': 'csv_identity_review_v1', 'action': 'revert' if original_review else 'bind',
              'reversed_review': rollback_review_id, 'source_gid': source_gid, 'canonical_gid': target_gid,
              'snapshot_sha256': snapshot_sha, 'source_batch_count': len(batch_ids),
              'source_observations_validated': validated, 'candidate_readings': len(rows),
              'candidate_channels': len({row['channel_name'] for row in rows}),
              'scope_sha256': _hash([(row['id'], _reading_digest(row)) for row in rows]),
              'candidate_unblocked': not blockers, 'blocking_counts': dict(sorted(blockers.items())),
              'historical_source_conflicts_repaired': False, 'continuous_capture_verified': False}
    report['plan_sha256'] = _hash(report)
    return report, rows, batch_ids, observations, original_review


def _apply(conn, report, rows, batch_ids, observations, original_review) -> dict:
    import energy

    watermark = conn.execute('SELECT COALESCE(MAX(sequence),0) FROM reading_changes').fetchone()[0]
    sequences = dict(conn.execute('SELECT name,seq FROM sqlite_sequence'))
    immutable = {table: _table_digest(conn, table) for table in _tables(conn) if table not in (*MUTABLE_TABLES, *REVIEW_TABLES)}
    reviews = {table: _table_digest(conn, table) for table in REVIEW_TABLES}
    reading_values = _table_digest(conn, 'readings', omit=('device_gid',))
    journal = _table_digest(conn, 'reading_changes', where='WHERE sequence<=?', parameters=(watermark,))
    before_gids = {row[0]: row[1] for row in conn.execute('SELECT id,device_gid FROM readings')}
    source, target = report['source_gid'], report['canonical_gid']
    gids = (source, target)
    unrelated = {table: _table_digest(conn, table, where='WHERE device_gid NOT IN (?,?)', parameters=gids)
                 for table in ('latest_channel_snapshot', 'device_capabilities')}
    state = {'before_snapshots': _scope_rows(conn, 'latest_channel_snapshot', gids),
             'before_capabilities': _scope_rows(conn, 'device_capabilities', gids)}
    for row in rows:
        changed = conn.execute('UPDATE readings SET device_gid=? WHERE id=? AND device_gid=?',
                               (target, row['id'], source)).rowcount
        if changed != 1:
            raise RuntimeError('Reading identity changed during reconciliation')
    if original_review:
        restored = json.loads(original_review['state_json'])
        for table, key in (('latest_channel_snapshot', 'before_snapshots'), ('device_capabilities', 'before_capabilities')):
            conn.execute(f'DELETE FROM {table} WHERE device_gid IN (?,?)', gids)
            for row in restored[key]:
                columns = ','.join(map(_name, row))
                conn.execute(f'INSERT INTO {table} ({columns}) VALUES ({",".join("?" for _ in row)})', tuple(row.values()))
    else:
        # Move only source-channel candidates; preserve newer or unrelated target
        # snapshots, including retained snapshots whose historical row was pruned.
        latest = {}
        for row in rows:
            key = row['channel_name']
            if key not in latest or (row['timestamp'], row['id']) > (latest[key]['timestamp'], latest[key]['id']):
                latest[key] = row
        for row in latest.values():
            fields = {key: value for key, value in row.items() if key not in ('id', 'device_gid')}
            energy._upsert_latest_snapshot_with_conn(conn, device_gid=target, **fields)
        conn.execute('DELETE FROM latest_channel_snapshot WHERE device_gid=?', (source,))
        capability = conn.execute('SELECT * FROM device_capabilities WHERE device_gid=?', (source,)).fetchone()
        if capability:
            flags = {key: bool(capability[key]) for key in ('has_main', 'has_mains_a', 'has_mains_b', 'has_mains_c', 'mains_c_no_ct')}
            energy._save_device_capabilities_with_conn(conn, device_gid=target,
                service_mode=capability['service_mode'], source=capability['source'], **flags)
            conn.execute('DELETE FROM device_capabilities WHERE device_gid=?', (source,))
    state['post_snapshot_sha256'] = _hash(_scope_rows(conn, 'latest_channel_snapshot', gids))
    state['post_capabilities_sha256'] = _hash(_scope_rows(conn, 'device_capabilities', gids))
    review_id = identity('csv_identity_review_v1', report['plan_sha256'])
    conn.execute('INSERT INTO csv_identity_reviews VALUES (?,?,?,?,?,?,?,?,?)',
        (review_id, report['action'], report['reversed_review'], source, target,
         report['snapshot_sha256'], report['plan_sha256'], datetime.now(timezone.utc).isoformat(timespec='microseconds'),
         json.dumps(state, sort_keys=True, separators=(',', ':'))))
    conn.executemany('INSERT INTO csv_identity_review_batches(review_id,batch_id,canonical_gid) VALUES (?,?,?)',
                     [(review_id, batch_id, target) for batch_id in batch_ids])
    conn.executemany('INSERT INTO csv_identity_review_rows VALUES (?,?,?,?,?,?)',
        [(review_id, row['id'], observations[row['id']]['id'], source, target, _reading_digest(row)) for row in rows])
    if any(_table_digest(conn, table) != digest for table, digest in immutable.items()):
        raise RuntimeError('Reconciliation changed immutable or unrelated data')
    for table, digest in reviews.items():
        key = 'id' if table == 'csv_identity_reviews' else 'review_id'
        if _table_digest(conn, table, where=f'WHERE {key}!=?', parameters=(review_id,)) != digest:
            raise RuntimeError('Reconciliation changed original reviews')
    if _table_digest(conn, 'readings', omit=('device_gid',)) != reading_values:
        raise RuntimeError('Reconciliation changed reading IDs, values, price or evidence')
    if _table_digest(conn, 'reading_changes', where='WHERE sequence<=?', parameters=(watermark,)) != journal:
        raise RuntimeError('Reconciliation changed original journal entries')
    expected_gids = {**before_gids, **{row['id']: target for row in rows}}
    if dict(conn.execute('SELECT id,device_gid FROM readings')) != expected_gids:
        raise RuntimeError('Reconciliation changed unrelated monitor identities')
    for table, digest in unrelated.items():
        if _table_digest(conn, table, where='WHERE device_gid NOT IN (?,?)', parameters=gids) != digest:
            raise RuntimeError('Reconciliation changed unrelated derived state')
    changes = [dict(row) for row in conn.execute('SELECT * FROM reading_changes WHERE sequence>? ORDER BY sequence', (watermark,))]
    if len(changes) != len(rows):
        raise RuntimeError('Unexpected reconciliation journal length')
    for event, reading in zip(changes, rows, strict=True):
        expected = {key: value for key, value in reading.items() if key != 'id'}
        expected.update(operation='upsert', reading_id=reading['id'], device_gid=target)
        if any(event[key] != value for key, value in expected.items()):
            raise RuntimeError('Unexpected reconciliation journal content')
    expected_sequences = {**sequences, 'csv_identity_review_batches': sequences.get('csv_identity_review_batches', 0) + len(batch_ids)}
    if rows:
        expected_sequences['reading_changes'] = max(sequences.get('reading_changes', 0), watermark) + len(rows)
    if dict(conn.execute('SELECT name,seq FROM sqlite_sequence')) != expected_sequences:
        raise RuntimeError('Reconciliation changed unexpected autoincrement identities')
    return {'review_id': review_id, 'original_journal_watermark': watermark,
            'canonical_upserts_appended': len(changes), 'stored_energy_costs_ids_preserved': True,
            'original_source_and_journal_preserved': True}


def review_identity_copy(snapshot: Path, *, expected_sha256: str, source_gid=None,
                         canonical_gid=None, destination: Path | None = None,
                         reviewed_plan_sha256: str | None = None, rollback_review_id: str | None = None) -> dict:
    """Audit by default; explicit hash approval publishes only a new private copy."""
    import energy

    if destination is not None and (not isinstance(reviewed_plan_sha256, str)
                                    or not re.fullmatch(r'[0-9a-f]{64}', reviewed_plan_sha256)):
        raise ValueError('Application requires the reviewed plan SHA-256')
    with tempfile.TemporaryDirectory(prefix='energy-identity-review-') as directory:
        published = destination is not None
        output = Path(destination) if published else Path(directory) / 'audit.db'
        with verified_copy(snapshot, output, expected_sha256) as working:
            conn = energy._connect(working)
            try:
                tables = _tables(conn)
                before = {table: _table_digest(conn, table) for table in tables}
                conn.close()
                energy.ensure_table(working)
                conn = energy._connect(working)
                conn.execute('BEGIN IMMEDIATE')
                if any(_table_digest(conn, table) != digest for table, digest in before.items()):
                    raise ValueError('Schema initialization changed archived data; upgrade a copy first')
                result, rows, batches, observations, old_review = _plan(conn, expected_sha256, source_gid, canonical_gid, rollback_review_id)
                if published:
                    if reviewed_plan_sha256 != result['plan_sha256'] or not result['candidate_unblocked']:
                        raise ValueError('Plan is blocked or differs from the approved snapshot review')
                    result.update(_apply(conn, result, rows, batches, observations, old_review))
                if [row[0] for row in conn.execute('PRAGMA integrity_check')] != ['ok'] or conn.execute('PRAGMA foreign_key_check').fetchall():
                    raise RuntimeError('Working copy failed SQLite integrity or foreign-key checks')
                conn.commit()
                conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
                conn.execute('PRAGMA journal_mode=DELETE')
            finally:
                conn.close()
        result.update(artifact_published=published, source_unchanged=True, production_activation_performed=False)
        if published:
            result.update(artifact_path=str(output.absolute()), artifact_sha256=file_sha256(output))
        return result
