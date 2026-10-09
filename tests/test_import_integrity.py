import io
import runpy
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

import energy
import web


class ImportIntegrityTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        database = patch.object(energy, 'DB_PATH', str(self.root / 'energy.db'))
        database.start()
        self.addCleanup(database.stop)
        energy.ensure_table()

    def upload(self, value, *, interval='1H', unit='kWhs', stamp='10/08/2026 12:00:00', zone='America/New_York'):
        path = self.root / f'QA-Panel-{interval}.csv'
        header = f'Time Bucket ({zone})' if zone else 'Time Bucket'
        path.write_text(f'{header},QA-Pump ({unit})\n{stamp},{value}\n')
        return path

    def state(self):
        conn = energy._connect()
        try:
            return {table: [tuple(row) for row in conn.execute(f'SELECT * FROM {table} ORDER BY 1')]
                    for table in ('readings', 'latest_channel_snapshot', 'reading_changes', 'device_capabilities', 'migrations')}
        finally:
            conn.close()

    def test_retired_heuristic_never_guesses_or_mutates_hourly_energy(self):
        path = self.upload(3)
        energy.import_emporia_csv(str(path))
        before = self.state()
        result = energy.fix_csv_kwatts_import()
        self.assertEqual(result['fixed'], 0)
        self.assertTrue(result['disabled'])
        self.assertEqual(self.state(), before)

    def test_actual_startup_preserves_imported_energy_and_cost(self):
        energy.import_emporia_csv(str(self.upload(3)))
        before = self.state()['readings']
        with patch.object(Flask, 'run'), patch.object(energy.requests, 'get', side_effect=RuntimeError('No external network in fixture')):
            runpy.run_path(web.__file__, run_name='__main__')
        self.assertEqual(self.state()['readings'], before)

    def test_unknown_power_interval_is_rejected_before_any_database_access(self):
        path = self.upload(3, interval='UNKNOWN', unit='kWatts')
        before = self.state()
        with patch.object(energy, '_connect', wraps=energy._connect) as connect:
            with self.assertRaisesRegex(ValueError, 'interval'):
                energy.import_emporia_csv(str(path))
            connect.assert_not_called()
        self.assertEqual(self.state(), before)

    def test_unknown_energy_interval_preserves_raw_kwh_without_power_conversion(self):
        result = energy.import_emporia_csv(str(self.upload(3, interval='UNKNOWN')))
        self.assertEqual(result['imported'], 1)
        self.assertEqual(self.state()['readings'][0][5], 3)
        self.assertIsNone(result['interval_seconds'])

    def test_unsupported_units_and_declared_zones_do_not_publish(self):
        for unit, zone in (('volts', 'America/New_York'), ('kWhs', 'Not/A_Zone')):
            with self.subTest(unit=unit, zone=zone):
                before = self.state()
                with self.assertRaises(ValueError):
                    energy.import_emporia_csv(str(self.upload(3, unit=unit, zone=zone)))
                self.assertEqual(self.state(), before)

    def test_nonfinite_and_overflow_values_never_reach_readings_or_snapshots(self):
        for value, unit in (('NaN', 'kWhs'), ('inf', 'kWatts'), ('-inf', 'kWhs'), ('1e308', 'kWatts')):
            with self.subTest(value=value, unit=unit):
                before = self.state()
                result = energy.import_emporia_csv(str(self.upload(value, unit=unit)))
                self.assertEqual(result['imported'], 0)
                self.assertEqual(result['errors'], 1)
                self.assertEqual(self.state()['readings'], before['readings'])
                self.assertEqual(self.state()['latest_channel_snapshot'], before['latest_channel_snapshot'])

    def test_fixed_intervals_convert_identical_average_power_to_correct_energy(self):
        for interval, seconds in (('1SEC', 1), ('1MIN', 60), ('15MIN', 900), ('1H', 3600)):
            result = energy.import_emporia_csv(str(self.upload(3, interval=interval, unit='kWatts')), device_gid=interval)
            self.assertEqual(result['interval_seconds'], seconds)
            conn = energy._connect()
            try:
                row = conn.execute('SELECT usage_kwh FROM readings WHERE device_gid=?', (interval,)).fetchone()
            finally:
                conn.close()
            self.assertAlmostEqual(row[0], 3 * seconds / 3600)

    def test_daily_power_conversion_uses_declared_zone_and_actual_dst_day(self):
        for stamp, hours in (('03/08/2026 00:00:00', 23), ('11/01/2026 00:00:00', 25)):
            result = energy.import_emporia_csv(str(self.upload(1, interval='1DAY', unit='kW', stamp=stamp)))
            self.assertEqual(result['interval_seconds'], hours * 3600)
            self.assertEqual(self.state()['readings'][-1][5], hours)
        before = self.state()
        with self.assertRaisesRegex(ValueError, 'timezone'):
            energy.import_emporia_csv(str(self.upload(1, interval='1DAY', unit='kW', zone=None)))
        self.assertEqual(self.state(), before)

    def test_declared_zone_fold_and_gap_rows_are_reported_not_guessed(self):
        for stamp, key in (('03/08/2026 02:30:00', 'nonexistent_timestamps'), ('11/01/2026 01:30:00', 'ambiguous_timestamps')):
            result = energy.import_emporia_csv(str(self.upload(3, stamp=stamp)))
            self.assertEqual(result['imported'], 0)
            self.assertEqual(result['errors'], 1)
            self.assertEqual(result[key], 1)
            self.assertFalse(self.state()['readings'])

    def test_zero_and_signed_finite_energy_are_preserved(self):
        for value, gid in ((0, 'zero'), (-1, 'export')):
            result = energy.import_emporia_csv(str(self.upload(value)), device_gid=gid)
            self.assertEqual(result['imported'], 1)
        self.assertEqual([row[5] for row in self.state()['readings']], [0, -1])

    def test_no_ct_skips_are_distinct_from_duplicate_skips(self):
        path = self.root / 'QA-Panel-1H.csv'
        path.write_text('Time Bucket (America/New_York),QA-Pump (kWhs),QA-Mains_C (kWhs)\n10/08/2026 12:00:00,3,No CT\n')
        first = energy.import_emporia_csv(str(path))
        second = energy.import_emporia_csv(str(path))
        self.assertEqual((first['imported'], first['skipped']), (1, 1))
        self.assertEqual((second['imported'], second['skipped']), (0, 2))
        self.assertEqual(self.state()['device_capabilities'][0][6], 1)

    def test_partial_bad_rows_report_errors_and_preserve_valid_rows(self):
        path = self.root / 'QA-Panel-1H.csv'
        path.write_text('Time Bucket (America/New_York),QA-Pump (kWhs)\n10/08/2026 12:00:00,3\nbad-date,4\n10/08/2026 13:00:00,NaN\n')
        result = energy.import_emporia_csv(str(path))
        self.assertEqual((result['imported'], result['errors']), (1, 2))
        self.assertEqual(self.state()['readings'][0][5], 3)

    def test_duplicate_headers_or_normalized_channels_are_rejected(self):
        for columns in ('QA-Pump (kWhs),QA-Pump (kWhs)', 'QA-Pump (kWhs),QA-Other-Pump (kWhs)'):
            path = self.root / 'QA-Panel-1H.csv'
            path.write_text(f'Time Bucket (America/New_York),{columns}\n10/08/2026 12:00:00,3,4\n')
            before = self.state()
            with self.assertRaises(ValueError):
                energy.import_emporia_csv(str(path))
            self.assertEqual(self.state(), before)

    def test_daily_energy_is_not_converted_and_mixed_daily_duration_is_explicit(self):
        path = self.root / 'QA-Panel-1DAY.csv'
        path.write_text('Time Bucket (America/New_York),QA-Pump (kWhs)\n03/08/2026 00:00:00,3\n11/01/2026 00:00:00,4\n')
        result = energy.import_emporia_csv(str(path))
        self.assertEqual([row[5] for row in self.state()['readings']], [3, 4])
        self.assertIsNone(result['interval_seconds'])
        self.assertEqual(result['conversion_factor'], 1)

    def test_ignored_duplicate_cannot_replace_accepted_snapshot(self):
        energy.import_emporia_csv(str(self.upload(3)))
        before = self.state()
        result = energy.import_emporia_csv(str(self.upload(999)))
        self.assertEqual(result['imported'], 0)
        self.assertEqual(result['skipped'], 1)
        self.assertEqual(self.state()['readings'], before['readings'])
        self.assertEqual(self.state()['latest_channel_snapshot'], before['latest_channel_snapshot'])
        self.assertEqual(self.state()['reading_changes'], before['reading_changes'])

    def test_older_history_upload_preserves_newer_snapshot_and_other_devices(self):
        energy.import_emporia_csv(str(self.upload(3, stamp='10/08/2026 12:00:00')))
        energy.import_emporia_csv(str(self.upload(7)), device_gid='Other')
        energy.import_emporia_csv(str(self.upload(999, stamp='10/07/2026 12:00:00')))
        self.assertEqual([row[3] for row in self.state()['latest_channel_snapshot']], [7, 3])

    def test_capability_failure_rolls_back_all_published_import_state_and_closes_connection(self):
        before = self.state()
        connection = energy._connect()
        with patch.object(energy, '_connect', return_value=connection), patch.object(
            energy, '_save_device_capabilities_with_conn', side_effect=RuntimeError('Simulated publication failure'),
        ):
            with self.assertRaises(RuntimeError):
                energy.import_emporia_csv(str(self.upload(3)))
        self.assertEqual(self.state(), before)
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute('SELECT 1')

    def test_import_api_reports_invalid_input_without_private_error_details(self):
        client = web.app.test_client()
        path = self.upload(3, interval='UNKNOWN', unit='kWatts')
        result = client.post('/api/import-csv', data={'file': (io.BytesIO(path.read_bytes()), path.name)})
        self.assertEqual(result.status_code, 400)
        self.assertIn('interval', result.get_json()['message'])
        path = self.upload(3)
        with patch.object(energy, 'import_emporia_csv', side_effect=RuntimeError('private-token-and-path')):
            result = client.post('/api/import-csv', data={'file': (io.BytesIO(path.read_bytes()), path.name)})
        self.assertEqual(result.status_code, 500)
        self.assertNotIn(b'private-token-and-path', result.data)


if __name__ == '__main__':
    unittest.main()
