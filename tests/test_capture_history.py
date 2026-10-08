import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import energy
import web


class CaptureHistoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db = patch.object(energy, 'DB_PATH', str(Path(self.directory.name) / 'capture.db'))
        self.db.start()
        energy.ensure_table()

    def tearDown(self):
        self.db.stop()
        self.directory.cleanup()

    def test_device_scope_minute_deduplication_and_completed_hour_boundaries(self):
        end = datetime(2026, 10, 7, 12)
        hour = end - timedelta(hours=1)
        rows = [(hour + timedelta(minutes=i), 'A', 'Main') for i in range(57)]
        rows += [(hour + timedelta(seconds=20), 'A', 'Main'),
                 (end, 'A', 'Main'), (hour, 'B', 'Main'),
                 (hour - timedelta(hours=1), 'A', 'Circuit')]
        conn = energy._connect()
        conn.executemany('INSERT INTO readings(timestamp,device_gid,channel_name,usage_kwh) VALUES (?,?,?,?)',
                         [(stamp.isoformat(), gid, name, 0) for stamp, gid, name in rows])
        conn.commit()
        conn.close()
        history = energy.get_capture_history(48, 'A', end)
        self.assertEqual(len(history), 48)
        self.assertEqual(history[-1]['minutes'], 57)
        self.assertEqual(history[-1]['state'], 'dense')
        self.assertEqual(history[-2]['state'], 'missing')
        self.assertEqual(energy.get_capture_history(48, 'B', end)[-1]['minutes'], 1)
        self.assertEqual(len(energy.get_capture_history(168, 'A', end)), 168)
        for hours in (True, 0, 1000):
            with self.assertRaises(ValueError):
                energy.get_capture_history(hours)

    def test_empty_history_is_missing_not_zero_usage_or_proven_offline(self):
        history = energy.get_capture_history(48, 'missing')
        self.assertTrue(all(bucket['state'] == 'missing' for bucket in history))
        response = web.app.test_client().get('/log')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('Recorded Capture Quality', html)
        self.assertIn('not process uptime', html)
        self.assertIn('7 days', html)
