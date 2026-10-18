import os
import tempfile
import time
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import energy


class ImportIntervalOverlapTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        database = patch.object(energy, 'DB_PATH', str(self.root / 'data.db'))
        database.start()
        self.addCleanup(database.stop)
        energy.ensure_table()
        self.base = (datetime.now(timezone.utc)-timedelta(days=2)).replace(hour=12, minute=0, second=0, microsecond=0)

    def upload(self, interval, rows, gid='QA', channel='Pump', zone='UTC'):
        path = self.root / f'{gid}-Panel-{interval}.csv'
        path.write_text(f'Time Bucket ({zone}),QA-{channel} (kWhs)\n'+''.join(
            stamp.strftime('%m/%d/%Y %H:%M:%S')+f',{value}\n' for stamp, value in rows))
        return energy.import_emporia_csv(str(path), device_gid=gid)

    def total(self, gid):
        conn = energy._connect()
        try:
            return conn.execute('SELECT SUM(usage_kwh) FROM readings WHERE device_gid=?', (gid,)).fetchone()[0]
        finally:
            conn.close()

    def test_hour_and_constituent_quarters_cannot_double_count_in_either_order(self):
        hours = [(self.base, 1)]
        quarters = [(self.base+timedelta(minutes=15*i), .25) for i in range(4)]
        for gid, first, second, conflicts in (
            ('hour-first', ('1H', hours), ('15MIN', quarters), 4),
            ('quarters-first', ('15MIN', quarters), ('1H', hours), 1)):
            with self.subTest(gid=gid):
                self.upload(*first, gid=gid)
                conn = energy._connect()
                before = {table: [tuple(row) for row in conn.execute(f'SELECT * FROM {table}')]
                          for table in ('readings', 'reading_changes', 'latest_channel_snapshot', 'device_capabilities')}
                conn.close()
                rejected = self.upload(*second, gid=gid)
                self.assertEqual((rejected['imported'], rejected['interval_conflicts'], rejected['errors']), (0, conflicts, conflicts))
                self.assertEqual(self.total(gid), 1)
                conn = energy._connect()
                after = {table: [tuple(row) for row in conn.execute(f'SELECT * FROM {table}')]
                         for table in before}
                conn.close()
                self.assertEqual(after, before)
                repeated = self.upload(*first, gid=gid)
                self.assertEqual((repeated['imported'], repeated['errors']), (0, 0))
                self.assertEqual(self.total(gid), 1)

    def test_touching_unsorted_intervals_are_accepted_and_intra_file_overlap_rejected(self):
        result = self.upload('1H', [(self.base+timedelta(hours=1), 2), (self.base, 1),
                                    (self.base+timedelta(minutes=30), 999)])
        self.assertEqual((result['imported'], result['interval_conflicts']), (2, 1))
        self.assertEqual(self.total('QA'), 3)

    def test_previous_hour_overlap_is_detected_and_devices_and_channels_are_isolated(self):
        self.upload('1H', [(self.base+timedelta(minutes=30), 1)])
        result = self.upload('15MIN', [(self.base+timedelta(hours=1), .5)])
        self.assertEqual(result['interval_conflicts'], 1)
        self.assertEqual(self.upload('15MIN', [(self.base+timedelta(hours=1), .5)], gid='other')['imported'], 1)
        self.assertEqual(self.upload('15MIN', [(self.base+timedelta(hours=1), .5)], channel='Other')['imported'], 1)

    def test_unknown_duration_does_not_mix_silently_with_known_intervals(self):
        self.upload('1H', [(self.base, 1)])
        result = self.upload('UNKNOWN', [(self.base+timedelta(minutes=30), .5)])
        self.assertEqual(result['interval_conflicts'], 1)
        self.assertEqual(self.total('QA'), 1)

    def test_publication_failure_rolls_back_coverage_and_accepted_rows(self):
        with patch.object(energy, '_save_device_capabilities_with_conn', side_effect=RuntimeError('fail')):
            with self.assertRaises(RuntimeError):
                self.upload('1H', [(self.base, 1)])
        self.assertIsNone(self.total('QA'))
        self.assertEqual(self.upload('1H', [(self.base, 1)])['imported'], 1)

    def test_dst_daily_interval_allows_next_midnight_without_overlap(self):
        previous = os.environ.get('TZ')
        try:
            os.environ['TZ'] = 'America/New_York'
            time.tzset()
            start = datetime(2026, 3, 8)
            self.upload('1DAY', [(start, 23)], zone='America/New_York')
            result = self.upload('1H', [(start+timedelta(days=1), 1)], zone='America/New_York')
            self.assertEqual((result['imported'], result['errors']), (1, 0))
            self.assertEqual(self.total('QA'), 24)
        finally:
            if previous is None:
                os.environ.pop('TZ', None)
            else:
                os.environ['TZ'] = previous
            time.tzset()
