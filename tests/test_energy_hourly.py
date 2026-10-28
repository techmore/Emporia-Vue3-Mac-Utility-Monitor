import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import energy


class EnergyHourlyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for target, value in [('DB_PATH', str(self.root / 'test.db')),
                              ('_last_compaction', time.monotonic()),
                              ('_last_reports', time.monotonic())]:
            override = patch.object(energy, target, value)
            override.start()
            self.addCleanup(override.stop)
        environment = patch.dict(os.environ, {'EMPORIA_MAINS_HISTORY': '0', 'RATE_CENTS': '10'})
        environment.start()
        self.addCleanup(environment.stop)
        self.addCleanup(setattr, energy, 'RATE_CENTS', energy.RATE_CENTS)
        energy.ensure_table()

    def vue(self, channels):
        return SimpleNamespace(get_device_list_usage=lambda **kwargs: {
            '77': SimpleNamespace(channels={n: SimpleNamespace(name=name, usage=value)
                                           for n, (name, value) in enumerate(channels, 1)})})

    def rows(self, sql, params=()):
        conn = energy._connect()
        try:
            return [dict(row) for row in conn.execute(sql, params)]
        finally:
            conn.close()

    def test_poll_returns_counts_and_rolls_up_hourly_with_kinds(self):
        stats = energy.poll_and_store(self.vue([('Main', .02), ('Water Heater', .01),
                                                ('Solar', -.005), ('Dryer', None)]), ['77'])
        self.assertEqual(stats['readings_written'], 3)
        self.assertEqual(stats['channels_null'], 1)
        self.assertEqual(stats['devices_reported'], 1)
        self.assertEqual(stats['hourly_rows_upserted'], 3)
        hourly = {r['channel_name']: r for r in self.rows('SELECT * FROM energy_hourly')}
        self.assertEqual(hourly['Main']['kind'], 'mains')
        self.assertEqual(hourly['Water Heater']['kind'], 'circuit')
        self.assertEqual(hourly['Solar']['kind'], 'solar')
        self.assertEqual(hourly['Main']['observed_seconds'], 60)
        self.assertAlmostEqual(hourly['Main']['kwh'], .02)

    def test_empty_usage_response_is_an_error_not_a_success(self):
        vue = SimpleNamespace(get_device_list_usage=lambda **kwargs: {})
        with self.assertRaisesRegex(RuntimeError, 'no channel usage'):
            energy.poll_and_store(vue, ['77'])

    def test_retention_keeps_imported_history_and_hourly_rollup_survives_pruning(self):
        old = (datetime.now() - timedelta(days=500)).replace(minute=0, second=0, microsecond=0)
        conn = energy._connect()
        with conn:
            for source, stamp in (('csv_import', old), ('live_poll', old + timedelta(minutes=7))):
                cursor = conn.execute('INSERT INTO readings(timestamp,device_gid,channel_num,channel_name,'
                                      'usage_kwh,cost_cents) VALUES (?,?,?,?,?,?)',
                                      (stamp.isoformat(), '77', 1, 'Main', 1.0, 10.0))
                conn.execute('INSERT INTO reading_provenance VALUES (?,?,?,?,?)',
                             (cursor.lastrowid, source, 3600 if source == 'csv_import' else 60,
                              None, 'kWh'))
            energy.rollup_energy_hourly(conn)
        conn.close()
        stats = energy.poll_and_store(self.vue([('Main', .02)]), ['77'])
        self.assertEqual(stats['rows_pruned'], 1)
        sources = self.rows('SELECT p.source FROM readings r JOIN reading_provenance p '
                            'ON p.reading_id=r.id WHERE r.timestamp<?', ((old + timedelta(days=1)).isoformat(),))
        self.assertEqual([r['source'] for r in sources], ['csv_import'])
        hour = self.rows('SELECT kwh, observed_seconds FROM energy_hourly WHERE hour_start=?',
                         (old.isoformat(),))
        self.assertEqual(hour, [{'kwh': 2.0, 'observed_seconds': 3600}])

    def test_backfill_runs_once(self):
        energy.poll_and_store(self.vue([('Main', .02)]), ['77'])
        self.rows('DELETE FROM energy_hourly')
        conn = energy._connect()
        with conn:
            conn.execute('DELETE FROM energy_hourly')
        conn.close()
        self.assertEqual(energy.backfill_energy_hourly(), 1)
        self.assertEqual(energy.backfill_energy_hourly(), 0)
        self.assertEqual(len(energy.get_energy_hourly('77')), 1)

    def test_gap_is_logged_with_missed_polls(self):
        energy.poll_and_store(self.vue([('Main', .02)]), ['77'])
        later = datetime.now() + timedelta(minutes=10)
        with patch.object(energy, 'reporting_now', return_value=later), \
                self.assertLogs(energy.logger, 'WARNING') as logs:
            energy.poll_and_store(self.vue([('Main', .02)]), ['77'])
        gap = next(r for r in logs.records if getattr(r, 'event', None) == 'gap_detected')
        self.assertGreaterEqual(gap.fields['missed_polls'], 8)

    def test_log_entries_report_whole_home_not_sum_of_channels(self):
        energy.poll_and_store(self.vue([('Main', .02), ('Mains_A', .01), ('Mains_B', .01),
                                        ('Dryer', .005)]), ['77'])
        entry = energy.get_log_entries(1)[0]
        self.assertAlmostEqual(entry['total_kwh'], .02)
        self.assertEqual(entry['channel_count'], 4)

    def test_next_poll_delay_keeps_fixed_start_to_start_interval(self):
        with patch.object(energy, 'POLL_INTERVAL', 60):
            self.assertAlmostEqual(energy._next_poll_delay(100.0, 103.5), 56.5)
            self.assertEqual(energy._next_poll_delay(100.0, 175.0), 1.0)


if __name__ == '__main__':
    unittest.main()
