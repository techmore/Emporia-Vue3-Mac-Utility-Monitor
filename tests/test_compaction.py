import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import energy


class CompactionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        patcher = patch.object(energy, 'DB_PATH', str(Path(self.directory.name) / 'h.db'))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.directory.cleanup)
        energy.ensure_table()

    def test_old_minutes_fold_into_hours_and_recent_rows_stay(self):
        old = (datetime.now() - timedelta(days=40)).replace(minute=0, second=0, microsecond=0)
        recent = datetime.now() - timedelta(minutes=5)
        conn = energy._connect()
        for i in range(3):
            conn.execute("INSERT INTO readings (timestamp,device_gid,channel_num,channel_name,usage_kwh,cost_cents)"
                         " VALUES (?,?,?,?,?,?)", ((old + timedelta(minutes=i)).isoformat(), 'A', 1, 'Main', .01, 1))
        conn.execute("INSERT INTO readings (timestamp,device_gid,channel_num,channel_name,usage_kwh,cost_cents)"
                     " VALUES (?,?,?,?,?,?)", (recent.isoformat(), 'A', 1, 'Main', .02, 2))
        self.assertEqual(energy.compact_minute_readings(conn, 30), 3)
        conn.commit()
        rows = conn.execute("SELECT timestamp, usage_kwh FROM readings ORDER BY timestamp").fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][0], old.isoformat(timespec='seconds'))
        self.assertAlmostEqual(rows[0][1], 0.03)
        self.assertEqual(rows[1][0], recent.isoformat())
        # Idempotent: a second pass finds only one row per hour and changes nothing.
        energy.compact_minute_readings(conn, 30)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0], 2)
        self.assertEqual(energy.compact_minute_readings(conn, 0), 0)
        conn.close()


if __name__ == '__main__':
    unittest.main()


class MonthlyCostTests(unittest.TestCase):
    def test_monthly_costs_group_circuits_and_write_reports(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(energy, 'DB_PATH', str(Path(directory) / 'm.db')):
            energy.ensure_table()
            conn = energy._connect()
            for stamp, name, kwh in [('2026-08-03T10:00:00', 'Main', 10), ('2026-08-03T10:00:00', 'Heat Pump', 6),
                                     ('2026-08-04T10:00:00', 'Main', 5), ('2026-08-04T10:00:00', 'Balance', 1)]:
                conn.execute("INSERT INTO readings (timestamp,device_gid,channel_num,channel_name,usage_kwh,cost_cents)"
                             " VALUES (?,?,?,?,?,?)", (stamp, 'A', 1, name, kwh, kwh * 10))
            conn.commit()
            conn.close()
            months = energy.get_monthly_costs(24, 'A')
            aug = next(m for m in months if m['month'] == '2026-08')
            self.assertEqual((aug['total_kwh'], aug['total_cents'], aug['days_recorded']), (15, 150, 2))
            self.assertEqual([c['channel_name'] for c in aug['circuits']], ['Heat Pump'])
            with patch.object(energy, 'get_monthly_costs', return_value=[aug]):
                written = energy.write_monthly_reports(str(Path(directory) / 'reports'))
                self.assertEqual(len(written), 1)
                self.assertIn('Heat Pump', Path(written[0]).read_text())
                self.assertEqual(energy.write_monthly_reports(str(Path(directory) / 'reports')), [])
