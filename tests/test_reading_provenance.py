import json
import os
from pathlib import Path
import tempfile
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import energy
import web


class ReadingProvenanceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        database = patch.object(energy, 'DB_PATH', str(self.root / 'energy.db'))
        database.start()
        self.addCleanup(database.stop)
        energy.ensure_table()
        self.stamp = datetime.now(timezone.utc).strftime('%m/%d/%Y %H:%M:%S')
        self.addCleanup(setattr, energy, 'RATE_CENTS', energy.RATE_CENTS)
        self.addCleanup(setattr, energy, '_last_compaction', energy._last_compaction)

    def poll(self, values):
        vue = SimpleNamespace(get_device_list_usage=lambda **kwargs: {
            'QA': SimpleNamespace(channels={i: SimpleNamespace(name=name, usage=value)
                                          for i, (name, value) in enumerate(values)})})
        with patch.dict(os.environ, {'EMPORIA_MAINS_HISTORY': '0'}), \
                patch.object(energy, '_last_compaction', time.monotonic()):
            energy.poll_and_store(vue, ['QA'])

    def import_bucket(self, interval, value=1):
        path = self.root / f'QA-Panel-{interval}.csv'
        stamp = self.stamp
        path.write_text(f'Time Bucket (UTC),QA-Pump (kWhs)\n{stamp},{value}\n')
        return energy.import_emporia_csv(str(path), device_gid='QA')

    def rows(self, table):
        conn = energy._connect()
        try:
            return [dict(row) for row in conn.execute(f'SELECT * FROM {table}')]
        finally:
            conn.close()

    def test_fresh_hourly_import_has_correct_average_and_never_live_power(self):
        self.import_bucket('1H')
        result = energy.get_circuit_history('Pump', 'QA')
        self.assertEqual(result['average_watts'], 1000)
        self.assertIsNone(result['live_watts'])
        self.assertEqual(result['observation_source'], 'csv_import')
        self.assertEqual(result['interval_seconds'], 3600)
        row = energy.get_latest('QA')[0]
        self.assertFalse(web._reading_fresh(row))
        self.assertEqual(web._watts_estimate(row), 0)
        metadata = self.rows('reading_provenance')[0]
        self.assertEqual((metadata['source_timezone'], metadata['source_unit']), ('UTC', 'kWh'))

    def test_unknown_import_duration_and_legacy_rows_do_not_guess_power(self):
        self.import_bucket('UNKNOWN')
        result = energy.get_circuit_history('Pump', 'QA')
        self.assertIsNone(result['average_watts'])
        self.assertIsNone(result['live_watts'])
        conn = energy._connect()
        with conn:
            conn.execute("INSERT INTO readings(timestamp,device_gid,channel_name,usage_kwh) VALUES(?,?,?,?)",
                         (datetime.now().isoformat(), 'QA', 'Legacy', 1))
        conn.close()
        result = energy.get_circuit_history('Legacy', 'QA')
        self.assertIsNone(result['average_watts'])
        self.assertIsNone(result['live_watts'])

    def test_duplicate_does_not_change_accepted_interval(self):
        self.import_bucket('1H')
        metadata = self.rows('reading_provenance')
        duplicate = self.import_bucket('1MIN', 999)
        self.assertEqual((duplicate['imported'], duplicate['skipped']), (0, 1))
        self.assertEqual(self.rows('reading_provenance'), metadata)
        self.assertEqual(energy.get_circuit_history('Pump', 'QA')['average_watts'], 1000)

    def test_real_poll_preserves_live_zero_and_verified_menu_power(self):
        with patch.dict(os.environ, {'RATE_CENTS': '12'}):
            self.poll([('Main', .02), ('Pump', .01), ('Idle', 0)])
        for name, watts in (('Pump', 600), ('Idle', 0)):
            result = energy.get_circuit_history(name, 'QA')
            self.assertEqual(result['live_watts'], watts)
        with patch.object(web, '_poller_status_snapshot', return_value={'ok': True, 'poller_running': True}):
            result = web._build_menu_summary_payload()
        self.assertTrue(result['online'])
        self.assertEqual(result['current_watts'], 1200)
        self.assertTrue(all(row['source'] == 'live_poll' for row in self.rows('reading_provenance')))

    def test_metadata_rolls_back_and_is_removed_with_readings(self):
        with patch.object(energy, '_save_device_capabilities_with_conn', side_effect=RuntimeError('fail')):
            with self.assertRaises(RuntimeError):
                self.import_bucket('1H')
        self.assertEqual(self.rows('readings'), [])
        self.assertEqual(self.rows('reading_provenance'), [])
        self.import_bucket('1H')
        conn = energy._connect()
        with conn:
            conn.execute('DELETE FROM readings')
        conn.close()
        self.assertEqual(self.rows('reading_provenance'), [])

    def test_invalid_environment_rates_and_cost_overflow_leave_whole_batch_untouched(self):
        for rate, values in [('nan', [('Pump', 1)]), ('inf', [('Pump', 1)]),
                             ('-1', [('Pump', 1)]), ('0', [('Pump', 1)]),
                             ('1e308', [('Idle', 0), ('Pump', 2)])]:
            with self.subTest(rate=rate), patch.dict(os.environ, {'RATE_CENTS': rate}):
                with self.assertRaises(ValueError):
                    self.poll(values)
                for table in ('readings', 'reading_provenance', 'latest_channel_snapshot', 'reading_changes'):
                    self.assertEqual(self.rows(table), [], table)

    def test_file_rates_are_validated_without_silently_falling_back(self):
        for rate in ('nan', 'inf', -1, 0, True, {}, 'bad'):
            with self.subTest(rate=rate), patch.dict(os.environ, {'RATE_CENTS': ''}), \
                    patch('builtins.open', unittest.mock.mock_open(read_data=json.dumps({'rate_cents': rate}))):
                with self.assertRaises(ValueError):
                    energy._read_rate_cents()
