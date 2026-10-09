import copy
import hashlib
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

import energy
from completed_history import capture_chart
from energy_clock import EnergyClock
from utc_migration import _convert


class CompletedHistoryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        database = patch.object(energy, 'DB_PATH', str(self.root / 'collector.db'))
        database.start()
        self.addCleanup(database.stop)
        rate = patch.object(energy, 'RATE_CENTS', 22.58)
        rate.start()
        self.addCleanup(rate.stop)
        energy.ensure_table()
        self.discover()

    def discover(self, name='Pump', channel='1', gid='551741'):
        device = SimpleNamespace(device_gid=gid, device_name='Barn', time_zone='America/New_York',
            manufacturer_id='F2518A001AECE3348C9E94', channels=[SimpleNamespace(channel_num=channel, name=name)])
        energy.get_devices_with_channels(Mock(get_devices=Mock(return_value=[device])))

    def capture(self, count=60, value=.05, offset=0):
        start = datetime(2026, 10, 8, 16, offset, tzinfo=timezone.utc)
        return {'schema': 'emporia_chart_v1', 'request': {'device_gid': '551741', 'channel_num': '1',
            'start': start.isoformat(), 'end': '2026-10-08T17:00:00Z',
            'scale': '1MIN', 'unit': 'KilowattHours'}, 'received_at': '2026-10-08T17:10:00Z',
            'response': {'firstUsageInstant': start.isoformat(), 'usageList': [value] * count}}

    def publish(self, capture=None, **kwargs):
        capture = capture or self.capture()
        content = json.dumps(capture['response']).encode()
        return energy.publish_completed_history(capture, content,
            legacy_storage_timezone='America/New_York', **kwargs)

    def csv(self, value=3):
        path = self.root / '8C9E94-Barn-1H.csv'
        path.write_text('Time Bucket (America/New_York),Barn-Pump (kWhs)\n10/08/2026 12:00:00,' + str(value) + '\n')
        return energy.import_emporia_csv(str(path))

    def rows(self, table):
        conn = energy._connect()
        try:
            return [dict(row) for row in conn.execute(f'SELECT * FROM {table} ORDER BY 1')]
        finally:
            conn.close()

    def total(self):
        return sum(row['usage_kwh'] for row in self.rows('readings'))

    def test_complete_chart_and_csv_share_projection_in_both_orders(self):
        for order in ('csv_first', 'chart_first'):
            with self.subTest(order=order):
                if order == 'chart_first':
                    energy.DB_PATH = str(self.root / 'second.db')
                    energy.ensure_table()
                    self.discover()
                    self.publish()
                    self.csv()
                else:
                    self.csv()
                    self.publish()
                self.assertAlmostEqual(self.total(), 3)
                self.assertEqual(len(self.rows('readings')), 60)
                self.assertEqual(len(self.rows('energy_source_observations')), 60)
                self.assertEqual(len(self.rows('csv_source_observations')), 1)
                self.assertTrue(all(row['measurement_source'] == 'emporia_chart' for row in self.rows('readings')))

    def test_partial_or_null_chart_preserves_complete_csv_hour(self):
        for index, item in enumerate((self.capture(count=20), self.capture(offset=10), self.capture(value=None))):
            energy.DB_PATH = str(self.root / f'partial-{index}.db')
            energy.ensure_table()
            self.discover()
            self.csv()
            self.publish(item)
            self.assertEqual(self.total(), 3)
            self.assertEqual(len(self.rows('readings')), 1)

    def test_separate_partial_captures_can_complete_verified_fine_coverage(self):
        self.csv()
        self.publish(self.capture(count=20))
        self.publish(self.capture(offset=10))
        self.assertAlmostEqual(self.total(), 3)
        self.assertEqual(len(self.rows('readings')), 60)

    def test_conflict_keeps_existing_values_and_retains_both_sources(self):
        self.csv()
        result = self.publish(self.capture(value=.1))
        self.assertGreater(result['warnings'], 0)
        self.assertEqual(self.total(), 3)
        self.assertEqual(len(self.rows('energy_source_observations')), 60)

    def test_raw_bytes_scope_bounds_price_and_retry_idempotence(self):
        item = self.capture(count=3)
        raw = b'{ "usageList": [0.05, 0.05, 0.05], "firstUsageInstant": "2026-10-08T16:00:00+00:00" }'
        before = copy.deepcopy(item)
        result = energy.publish_completed_history(item, raw, legacy_storage_timezone='America/New_York')
        batch = self.rows('energy_source_batches')[0]
        self.assertEqual(batch['content'], raw)
        self.assertEqual(batch['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(item, before)
        state = {table: self.rows(table) for table in ('readings', 'reading_changes', 'energy_source_batches', 'energy_source_observations', 'sqlite_sequence')}
        with patch.object(energy, 'RATE_CENTS', 99):
            energy.publish_completed_history(item, raw, legacy_storage_timezone='America/New_York')
        self.assertEqual(state, {table: self.rows(table) for table in state})
        self.assertEqual(result['missing_completed_buckets'], 57)
        self.assertAlmostEqual(self.rows('readings')[0]['cost_cents'], .05 * 22.58)
        self.assertIsNone(energy.reading_live_watts(self.rows('readings')[0]))
        self.assertEqual(energy.reading_average_watts(self.rows('readings')[0]), 3000)

    def test_signed_zero_null_and_inclusive_end_remain_distinct(self):
        item = self.capture(count=61)
        item['response']['usageList'] = [0, -.05, None] + [.05] * 57 + [999]
        result = self.publish(item)
        self.assertEqual(len(self.rows('readings')), 59)
        self.assertEqual(result['missing_completed_buckets'], 1)
        self.assertEqual(result['excluded_window'], 1)
        self.assertAlmostEqual(self.total(), 2.8)

    def test_unknown_live_coverage_blocks_chart_without_dropping_evidence(self):
        conn = energy._connect()
        with conn:
            conn.execute("INSERT INTO readings(timestamp,device_gid,channel_name,usage_kwh,cost_cents) VALUES ('2026-10-08T12:00:00','551741','Pump',9,99)")
        conn.close()
        result = self.publish()
        self.assertGreater(result['warnings'], 0)
        self.assertEqual(self.total(), 9)
        self.assertEqual(len(self.rows('energy_source_observations')), 60)

    def test_wrong_raw_payload_unregistered_or_renamed_channel_fail_before_writes(self):
        item = self.capture()
        for key, value in (('device_gid', 'unknown'), ('channel_num', 'other')):
            other = copy.deepcopy(item)
            other['request'][key] = value
            with self.assertRaises(ValueError):
                self.publish(other)
        with self.assertRaises(ValueError):
            energy.publish_completed_history(item, b'{}', legacy_storage_timezone='America/New_York')
        self.discover(name='Renamed')
        with self.assertRaises(ValueError):
            self.publish(item)
        self.assertEqual(self.rows('energy_source_batches'), [])

    def test_failure_rolls_back_sources_projection_journal_and_snapshots(self):
        self.csv()
        before = {table: self.rows(table) for table in ('readings', 'reading_changes', 'latest_channel_snapshot', 'csv_reading_projection')}
        with patch.object(energy, '_refresh_history_snapshots_with_conn', side_effect=RuntimeError('failure')):
            with self.assertRaises(RuntimeError):
                self.publish()
        self.assertEqual(before, {table: self.rows(table) for table in before})
        self.assertEqual(self.rows('energy_source_batches'), [])
        self.assertEqual(self.rows('energy_reading_projection'), [])

    def test_no_legacy_timezone_guess_and_missing_anchor_fails_closed(self):
        item = self.capture()
        with self.assertRaises(ValueError):
            energy.publish_completed_history(item, json.dumps(item['response']).encode())
        item['response'].pop('firstUsageInstant')
        with self.assertRaises(ValueError):
            self.publish(item)
        self.assertEqual(self.rows('readings'), [])

    def test_actual_acquisition_uses_raw_get_without_sdk_anchor_fallback(self):
        item = self.capture(count=3)
        raw = json.dumps(item['response']).encode()
        response = Mock(content=raw)
        vue = Mock(auth=Mock(request=Mock(return_value=response)))
        result = energy.collect_completed_history(vue, '551741', '1',
            datetime(2026, 10, 8, 16, tzinfo=timezone.utc),
            datetime(2026, 10, 8, 17, tzinfo=timezone.utc),
            legacy_storage_timezone='America/New_York')
        self.assertEqual(result['imported'], 3)
        method, path = vue.auth.request.call_args.args
        self.assertEqual(method, 'get')
        self.assertIn('apiMethod=getChartUsage', path)
        self.assertIn('deviceGid=551741', path)
        self.assertIn('energyUnit=KilowattHours', path)
        vue.get_chart_usage.assert_not_called()
        response.close.assert_called_once()
        self.assertEqual(self.rows('energy_source_batches')[0]['content'], raw)

    def test_acquisition_closes_http_response_on_all_failure_paths(self):
        for content, error in ((b'not-json', None), (b'x' * 2_000_001, None), (b'{}', requests.HTTPError('safe-test'))):
            response = Mock(content=content)
            response.raise_for_status.side_effect = error
            vue = Mock(auth=Mock(request=Mock(return_value=response)))
            with self.assertRaises((ValueError, requests.HTTPError)):
                capture_chart(vue, '551741', '1', datetime(2026, 10, 8, 16, tzinfo=timezone.utc),
                              datetime(2026, 10, 8, 17, tzinfo=timezone.utc))
            response.close.assert_called_once()
        self.assertEqual(self.rows('energy_source_batches'), [])

    def test_invalid_scope_delay_or_zone_does_not_make_cloud_calls(self):
        vue = Mock()
        start, end = datetime(2026, 10, 8, 16, tzinfo=timezone.utc), datetime(2026, 10, 8, 17, tzinfo=timezone.utc)
        for gid, zone, delay in (('unknown', 'America/New_York', 300), ('551741', None, 300),
                                 ('551741', 'America/New_York', True)):
            with self.assertRaises(ValueError):
                energy.collect_completed_history(vue, gid, '1', start, end,
                    legacy_storage_timezone=zone, settling_seconds=delay)
        vue.auth.request.assert_not_called()

    def test_real_sync_cache_pages_never_mix_csv_and_chart_coverage(self):
        self.csv()
        first = energy.get_reading_changes(limit=1)
        cache = self.root / 'cache.db'
        with patch.object(energy, 'DB_PATH', str(cache)):
            energy.ensure_table()
            state = energy.apply_reading_changes(first)
        self.publish()
        while True:
            page = energy.get_reading_changes(state['cursor'], limit=1, protocol_version=3,
                                              measurement_model='interval_v2')
            with patch.object(energy, 'DB_PATH', str(cache)):
                state = energy.apply_reading_changes(page)
                rows = self.rows('sync_cached_readings')
            intervals = sorted((datetime.fromisoformat(row['timestamp']), row['measurement_seconds']) for row in rows)
            for left, right in zip(intervals, intervals[1:], strict=False):
                self.assertLessEqual(left[0] + timedelta(seconds=left[1]), right[0])
            if not page['has_more']:
                break
        self.assertAlmostEqual(sum(row['usage_kwh'] for row in rows), 3)
        self.assertEqual(len(rows), 60)
        self.assertTrue(all(row['measurement_source'] == 'emporia_chart' for row in rows))

    def test_legacy_fold_evidence_is_retained_without_ambiguous_readings(self):
        item = self.capture(count=5)
        item['request'].update(start='2026-11-01T05:58:00Z', end='2026-11-01T06:03:00Z')
        item['response']['firstUsageInstant'] = '2026-11-01T05:58:00Z'
        item['received_at'] = '2026-11-01T06:10:00Z'
        result = self.publish(item)
        self.assertGreater(result['warnings'], 0)
        self.assertEqual(self.rows('readings'), [])
        self.assertEqual(len(self.rows('energy_source_observations')), 5)
        with patch.object(energy, '_utc_clock', return_value=EnergyClock('America/New_York')):
            result = energy.publish_completed_history(item, json.dumps(item['response']).encode())
        self.assertEqual(result['imported'], 5)
        self.assertEqual([row['timestamp'] for row in self.rows('readings')],
            ['2026-11-01T05:58:00.000000+00:00', '2026-11-01T05:59:00.000000+00:00',
             '2026-11-01T06:00:00.000000+00:00', '2026-11-01T06:01:00.000000+00:00',
             '2026-11-01T06:02:00.000000+00:00'])

    def test_source_order_and_raw_evidence_immutable_and_survive_vacuum(self):
        self.publish(self.capture(count=1, value=.05))
        later = self.capture(count=1, value=.1)
        result = self.publish(later)
        self.assertGreater(result['warnings'], 0)
        before = {table: self.rows(table) for table in ('readings', 'energy_source_order', 'energy_source_batches', 'energy_source_observations')}
        conn = energy._connect()
        try:
            for table in ('energy_channel_claims', 'energy_source_order', 'energy_source_batches', 'energy_source_observations'):
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(f'DELETE FROM {table}')
            conn.rollback()
            conn.execute('VACUUM')
        finally:
            conn.close()
        self.publish(later)
        self.assertEqual(before, {table: self.rows(table) for table in before})

    def test_schema_reinitialization_preserves_source_order_and_all_counters(self):
        self.csv()
        self.publish()
        before = {table: self.rows(table) for table in ('readings', 'energy_source_order', 'sqlite_sequence')}
        for _ in range(2):
            energy.ensure_table()
        self.assertEqual(before, {table: self.rows(table) for table in before})

    def test_provider_live_labels_can_fill_empty_discovery_without_guessing_names(self):
        self.discover(name='', channel='1,2,3')
        conn = energy._connect()
        try:
            with conn:
                energy.record_channel_names(conn, '551741', [('1,2,3', SimpleNamespace(name='Main'))],
                    energy._normalize_channel_name, '2026-10-08T17:10:00+00:00')
        finally:
            conn.close()
        item = self.capture(count=2)
        item['request']['channel_num'] = '1,2,3'
        result = self.publish(item)
        self.assertEqual(result['channel_name'], 'Main')
        self.assertEqual({row['channel_name'] for row in self.rows('readings')}, {'Main'})

    def test_actual_poll_records_provider_labels_even_without_a_measurement(self):
        self.discover(name='', channel='1,2,3')
        vue = Mock(get_device_list_usage=Mock(return_value={'551741': SimpleNamespace(channels={
            '1,2,3': SimpleNamespace(name='Main', usage=None)})}))
        with patch.object(energy, '_last_compaction', float('inf')):
            energy.poll_and_store(vue, ['551741'])
        claims = self.rows('energy_channel_claims')
        self.assertIn(('1,2,3', 'Main'), {(row['channel_num'], row['channel_name']) for row in claims})
        self.assertEqual(self.rows('readings'), [])

    def test_real_utc_conversion_preserves_raw_sources_and_blocks_ordinary_writes(self):
        self.publish(self.capture(count=2))
        tables = ('energy_channel_claims', 'energy_source_order', 'energy_source_batches', 'energy_source_observations')
        before = {table: self.rows(table) for table in tables}
        archive = self.root / 'archive.db'
        energy.backup_database(archive)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        conversion, writer = energy._connect(), energy._connect()
        self.addCleanup(conversion.close)
        self.addCleanup(writer.close)
        with conversion:
            conversion.execute('BEGIN IMMEDIATE')
            _convert(conversion, 'America/New_York', 'America/New_York', digest)
        item = self.capture(count=3)
        with patch.object(energy, '_connect', return_value=writer):
            result = energy.publish_completed_history(item, json.dumps(item['response']).encode())
        self.assertEqual(result['timestamp_format'], 'utc_v1')
        with self.assertRaises(RuntimeError):
            energy._connect()
        inspection = energy._connect(read_only=True, allow_utc_rehearsal=True)
        try:
            for table in tables:
                actual = [dict(row) for row in inspection.execute(f'SELECT * FROM {table} ORDER BY 1')]
                self.assertEqual([row for row in actual if row in before[table]], before[table])
            rows = [dict(row) for row in inspection.execute('SELECT * FROM readings')]
            self.assertEqual(len(rows), 3)
            self.assertAlmostEqual(sum(row['usage_kwh'] for row in rows), .15)
        finally:
            inspection.close()
        self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), digest)

    def test_newer_retained_live_snapshot_survives_history_publication(self):
        conn = energy._connect()
        try:
            energy._upsert_latest_snapshot_with_conn(conn, device_gid='551741', channel_name='Pump', channel_num=1,
                usage_kwh=.01, cost_cents=.2258, timestamp='2026-10-08T14:00:00',
                measurement_seconds=60, measurement_source='emporia_minute')
            conn.commit()
        finally:
            conn.close()
        before = self.rows('latest_channel_snapshot')
        self.publish()
        self.assertEqual(self.rows('latest_channel_snapshot'), before)


if __name__ == '__main__':
    unittest.main()
