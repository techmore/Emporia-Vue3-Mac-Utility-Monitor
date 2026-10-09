import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from sqlite3 import ProgrammingError
from types import SimpleNamespace
from unittest.mock import Mock, patch

import energy
import sync_history
import web


class MeasurementEvidenceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        database = patch.object(energy, 'DB_PATH', str(self.root / 'energy.db'))
        database.start()
        self.addCleanup(database.stop)
        energy.ensure_table()

    def rows(self, table):
        conn = energy._connect()
        try:
            return [dict(row) for row in conn.execute(f'SELECT * FROM {table}')]
        finally:
            conn.close()

    def upload(self, interval='1H', stamp='10/08/2026 12:00:00', unit='kWhs', value=3):
        path = self.root / f'QA-Panel-{interval}.csv'
        path.write_text(f'Time Bucket (America/New_York),QA-Main ({unit})\n{stamp},{value}\n')
        return energy.import_emporia_csv(str(path), device_gid='QA')

    def evidence(self, row):
        return tuple(row[field] for field in energy.MEASUREMENT_FIELDS)

    def test_hourly_import_snapshot_journal_and_latest_retain_evidence(self):
        self.upload()
        reading = self.rows('readings')[0]
        self.assertEqual(self.evidence(reading), (3600, 'csv_energy', 'America/New_York', None))
        self.assertEqual(energy.reading_average_watts(reading), 3000)
        for table in ('latest_channel_snapshot', 'reading_changes'):
            self.assertEqual(self.evidence(self.rows(table)[0]), self.evidence(reading))
        self.assertEqual(self.evidence(energy.get_latest('QA')[0]), self.evidence(reading))
        latest = energy.get_now_vs_context(device_gid='QA', now=datetime(2026, 10, 8, 13))['latest'][0]
        self.assertEqual(self.evidence(latest), self.evidence(reading))

    def test_daily_energy_durations_follow_actual_dst_days(self):
        for day, seconds in (('03/08/2026', 23 * 3600), ('11/01/2026', 25 * 3600)):
            self.upload('1DAY', day + ' 00:00:00', value=seconds / 3600)
        rows = self.rows('readings')
        self.assertEqual([row['measurement_seconds'] for row in rows], [23 * 3600, 25 * 3600])
        self.assertEqual([energy.reading_average_watts(row) for row in rows], [1000, 1000])

    def test_unknown_energy_interval_remains_unknown_not_one_minute(self):
        self.upload('UNKNOWN')
        row = self.rows('readings')[0]
        self.assertIsNone(row['measurement_seconds'])
        self.assertEqual(row['measurement_source'], 'csv_energy')
        self.assertIsNone(energy.reading_average_watts(row))

    def test_power_conversion_retains_its_source_unit_evidence(self):
        self.upload('15MIN', unit='kWatts')
        row = self.rows('readings')[0]
        self.assertEqual(row['usage_kwh'], .75)
        self.assertEqual(row['measurement_source'], 'csv_power')
        self.assertEqual(energy.reading_average_watts(row), 3000)

    def test_duplicate_upload_does_not_replace_accepted_evidence(self):
        self.upload()
        before = self.rows('readings'), self.rows('latest_channel_snapshot'), self.rows('reading_changes')
        self.upload('1MIN', value=999)
        self.assertEqual((self.rows('readings'), self.rows('latest_channel_snapshot'), self.rows('reading_changes')), before)

    def test_schema_upgrade_preserves_legacy_values_and_never_infers_evidence(self):
        conn = energy._connect()
        conn.execute("INSERT INTO readings(timestamp,device_gid,channel_name,usage_kwh,cost_cents) VALUES ('2026-10-08T12:00:00.123456','A','Main',3,67.74)")
        conn.commit()
        conn.close()
        before = self.rows('readings'), self.rows('reading_changes')
        for _ in range(2):
            energy.ensure_table()
        self.assertEqual((self.rows('readings'), self.rows('reading_changes')), before)
        self.assertEqual(self.evidence(before[0][0]), (None,) * 4)
        self.assertIsNone(energy.reading_average_watts(before[0][0]))

    def test_actual_legacy_schema_upgrade_adds_unknown_columns_without_reseeding(self):
        conn = energy._connect()
        conn.execute("INSERT INTO readings(timestamp,device_gid,channel_name,usage_kwh,cost_cents) VALUES ('2026-10-08T12:00:00','A','Main',3,67.74)")
        # Model the existing pre-evidence schema, including its recorded journal/cursor identity.
        for operation in ('insert', 'update'):
            conn.execute(f'DROP TRIGGER readings_sync_{operation}')
        tables = ('readings', 'latest_channel_snapshot', 'reading_changes', 'sync_cached_readings')
        for table in tables:
            for field in energy.MEASUREMENT_FIELDS:
                conn.execute(f'ALTER TABLE {table} DROP COLUMN {field}')
        conn.commit()
        conn.close()
        before = {table: self.rows(table) for table in tables}
        identity = self.rows('collector_identity'), self.rows('reading_stream_generation')
        energy.ensure_table()
        for table in tables:
            upgraded = self.rows(table)
            self.assertEqual([{key: value for key, value in row.items() if key not in energy.MEASUREMENT_FIELDS}
                              for row in upgraded], before[table])
            self.assertTrue(all(self.evidence(row) == (None,) * 4 for row in upgraded))
        self.assertEqual((self.rows('collector_identity'), self.rows('reading_stream_generation')), identity)
        # The upgraded (not merely newly created) triggers publish evidence on future writes.
        self.upload()
        row = self.rows('reading_changes')[-1]
        self.assertEqual(self.evidence(row), (3600, 'csv_energy', 'America/New_York', None))

    def test_python_sync_cache_round_trip_preserves_evidence(self):
        self.upload()
        token = 'a' * 32
        with patch.dict(web.os.environ, {'ENERGY_SYNC_TOKEN': token}):
            response = web.app.test_client().get('/api/sync/readings',
                                                 headers={'Authorization': 'Bearer ' + token})
        self.assertEqual(response.status_code, 200)
        page = response.get_json()
        with patch.object(energy, 'DB_PATH', str(self.root / 'cache.db')):
            energy.ensure_table()
            # Execute the same Python download/cache path spawned by the native app.
            with patch.object(sync_history, 'fetch_page', return_value=page):
                state = sync_history.sync_once('http://127.0.0.1:15001', token)
            self.assertEqual(state['cursor'], page['next_cursor'])
            row = self.rows('sync_cached_readings')[0]
            self.assertEqual(self.evidence(row), (3600, 'csv_energy', 'America/New_York', None))
            self.assertEqual(energy.reading_average_watts(row), 3000)

    def test_legacy_sync_pages_remain_unknown_and_invalid_evidence_is_atomic(self):
        self.upload()
        page = energy.get_reading_changes()
        for field in energy.MEASUREMENT_FIELDS:
            page['changes'][0].pop(field)
        with patch.object(energy, 'DB_PATH', str(self.root / 'cache.db')):
            energy.ensure_table()
            invalid = deepcopy(page)
            invalid['changes'][0]['measurement_seconds'] = 60
            with self.assertRaises(ValueError):
                energy.apply_reading_changes(invalid)
            self.assertEqual(self.rows('sync_cached_readings'), [])
            self.assertEqual(self.rows('sync_cache_state'), [])
            energy.apply_reading_changes(page)
            self.assertEqual(self.evidence(self.rows('sync_cached_readings')[0]), (None,) * 4)

    def test_snapshot_rebuild_and_journal_checkpoint_preserve_evidence(self):
        self.upload()
        conn = energy._connect()
        for value in range(5):
            conn.execute('UPDATE readings SET cost_cents=?', (value,))
        conn.commit()
        conn.close()
        self.assertTrue(energy.compact_reading_journal(1))
        self.assertEqual(energy.rebuild_latest_channel_snapshot(), 1)
        expected = self.evidence(self.rows('readings')[0])
        self.assertEqual(self.evidence(self.rows('reading_changes')[0]), expected)
        self.assertEqual(self.evidence(self.rows('latest_channel_snapshot')[0]), expected)

    def poll(self, channels):
        vue = Mock()
        vue.get_device_list_usage.return_value = {'A': SimpleNamespace(channels=channels)}
        with patch.object(energy, '_last_compaction', float('inf')), patch.object(energy, '_read_rate_cents', return_value=22.58):
            energy.poll_and_store(vue, ['A'])
        self.assertEqual(vue.get_device_list_usage.call_args.kwargs['scale'], energy.Scale.MINUTE.value)

    def test_minute_poll_preserves_provider_instant_separately_from_receipt(self):
        provider = datetime.now(timezone.utc) - timedelta(minutes=1)
        self.poll({1: SimpleNamespace(name='Main', usage=.05, timestamp=provider)})
        row = self.rows('readings')[0]
        # The provider instant is UTC; the legacy receipt timestamp's zone is not proven by it.
        expected = (60, 'emporia_minute', None, provider.isoformat())
        self.assertEqual(self.evidence(row), expected)
        self.assertNotEqual(row['timestamp'], row['provider_timestamp'])
        self.assertEqual(energy.reading_average_watts(row), 3000)
        for table in ('latest_channel_snapshot', 'reading_changes'):
            self.assertEqual(self.evidence(self.rows(table)[0]), expected)

    def test_poll_failure_rolls_back_all_channels_and_closes_connection(self):
        conn = energy._connect()
        with patch.object(energy, '_connect', return_value=conn):
            with self.assertRaises(ValueError):
                self.poll({1: SimpleNamespace(name='Main', usage=.05),
                           2: SimpleNamespace(name='Pump', usage=float('nan'))})
        with self.assertRaises(ProgrammingError) as raised:
            conn.execute('SELECT 1')
        self.assertIn('closed', str(raised.exception))
        for table in ('readings', 'reading_changes', 'latest_channel_snapshot'):
            self.assertEqual(self.rows(table), [])

    def test_compaction_does_not_invent_continuous_hourly_duration(self):
        conn = energy._connect()
        old = (datetime.now() - timedelta(days=60)).replace(minute=0, second=0, microsecond=0)
        for minute in (1, 45):
            conn.execute("INSERT INTO readings(timestamp,device_gid,channel_name,usage_kwh,cost_cents,measurement_seconds,measurement_source) VALUES (?,?,?,?,?,60,'emporia_minute')",
                         ((old + timedelta(minutes=minute)).isoformat(), 'A', 'Main', .05, 1.129))
        self.assertEqual(energy.compact_minute_readings(conn, 30), 2)
        conn.commit()
        conn.close()
        row = self.rows('readings')[0]
        self.assertAlmostEqual(row['usage_kwh'], .1)
        self.assertAlmostEqual(row['cost_cents'], 2.258)
        self.assertEqual(row['measurement_source'], 'compacted')
        self.assertIsNone(row['measurement_seconds'])
        self.assertIsNone(energy.reading_average_watts(row))

    def test_unknown_invalid_zero_and_signed_power_are_distinct(self):
        base = dict(usage_kwh=0, measurement_seconds=60, measurement_source='emporia_minute')
        self.assertEqual(energy.reading_average_watts(base), 0)
        self.assertEqual(energy.reading_average_watts({**base, 'usage_kwh': -.05}), -3000)
        for evidence in ({'measurement_seconds': None}, {'measurement_seconds': 0},
                         {'measurement_seconds': True}, {'measurement_seconds': float('inf')},
                         {'measurement_seconds': 10**1000}, {'usage_kwh': 10**1000},
                         {'measurement_source': None}, {'measurement_source': 'compacted'},
                         {'provider_timestamp': '2026-10-08T12:00:00'},
                         {'source_timezone': 'Not/A_Zone'}, {'usage_kwh': float('nan')}):
            with self.subTest(evidence=evidence):
                self.assertIsNone(energy.reading_average_watts({**base, **evidence}))
