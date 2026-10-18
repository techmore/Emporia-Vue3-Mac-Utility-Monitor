import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import energy


class CompactionImportIntegrityTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        database = patch.object(energy, 'DB_PATH', str(self.root / 'data.db'))
        database.start()
        self.addCleanup(database.stop)
        energy.ensure_table()
        self.old = (datetime.now(timezone.utc) - timedelta(days=40)).replace(minute=0, second=0, microsecond=0)
        self.path = self.root / 'QA-Panel-1MIN.csv'
        self.path.write_text('Time Bucket (UTC),QA-Pump (kWhs)\n' + ''.join(
            (self.old+timedelta(minutes=i)).strftime('%m/%d/%Y %H:%M:%S')+',1\n' for i in range(2)))

    def compact(self):
        conn = energy._connect()
        try:
            with conn:
                removed = energy.compact_minute_readings(conn, 30)
            return removed
        finally:
            conn.close()

    def total(self):
        conn = energy._connect()
        try:
            return conn.execute('SELECT SUM(usage_kwh) FROM readings').fetchone()[0]
        finally:
            conn.close()

    def test_import_compact_reimport_keeps_original_values_and_provenance(self):
        energy.import_emporia_csv(str(self.path), device_gid='QA')
        self.assertEqual(self.compact(), 0)
        result = energy.import_emporia_csv(str(self.path), device_gid='QA')
        self.assertEqual((result['imported'], result['skipped'], result['errors']), (0, 2, 0))
        self.assertEqual(self.total(), 2)
        conn = energy._connect()
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM reading_provenance').fetchone()[0], 2)
        conn.close()

    def test_legacy_compacted_hour_rejects_reimport_of_deleted_interval(self):
        energy.import_emporia_csv(str(self.path), device_gid='QA')
        conn = energy._connect()
        with conn:
            conn.execute('DELETE FROM reading_provenance')  # Existing pre-provenance history.
        conn.close()
        self.assertEqual(self.compact(), 2)
        result = energy.import_emporia_csv(str(self.path), device_gid='QA')
        self.assertEqual(result['imported'], 0)
        self.assertEqual((result['compacted_conflicts'], result['errors']), (1, 1))
        self.assertEqual(self.total(), 2)
        # Previously compacted databases may have no marker; unproven overlap still fails closed.
        conn = energy._connect()
        with conn:
            conn.execute('DELETE FROM reading_compactions')
        conn.close()
        result = energy.import_emporia_csv(str(self.path), device_gid='QA')
        self.assertEqual(result['imported'], 0)
        self.assertEqual(self.total(), 2)

    def test_markers_survive_restart_and_roll_back_with_failed_compaction(self):
        energy.import_emporia_csv(str(self.path), device_gid='QA')
        conn = energy._connect()
        with conn:
            conn.execute('DELETE FROM reading_provenance')
        try:
            with self.assertRaises(RuntimeError), conn:
                energy.compact_minute_readings(conn, 30)
                raise RuntimeError('cancel transaction')
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM reading_compactions').fetchone()[0], 0)
        finally:
            conn.close()
        self.assertEqual(self.total(), 2)
        self.compact()
        energy.ensure_table()
        result = energy.import_emporia_csv(str(self.path), device_gid='QA')
        self.assertEqual(result['imported'], 0)
        self.assertEqual(self.total(), 2)

    def test_mixed_hour_preserves_csv_and_other_raw_observations(self):
        energy.import_emporia_csv(str(self.path), device_gid='QA')
        local = (self.old+timedelta(minutes=2)).astimezone().replace(tzinfo=None).isoformat()
        conn = energy._connect()
        with conn:
            conn.execute('INSERT INTO readings(timestamp,device_gid,channel_name,usage_kwh) VALUES(?,?,?,?)',
                         (local, 'QA', 'Pump', 1))
        conn.close()
        self.assertEqual(self.compact(), 0)
        self.assertEqual(self.total(), 3)

    def test_daily_import_detects_compacted_hours_after_its_start(self):
        start = self.old.replace(hour=0)
        local = (start+timedelta(hours=6)).astimezone().replace(tzinfo=None)
        conn = energy._connect()
        with conn:
            for minute in (0, 1):
                conn.execute('INSERT INTO readings(timestamp,device_gid,channel_name,usage_kwh) VALUES(?,?,?,?)',
                             ((local+timedelta(minutes=minute)).isoformat(), 'QA', 'Pump', 1))
        conn.close()
        self.compact()
        path = self.root / 'QA-Panel-1DAY.csv'
        path.write_text('Time Bucket (UTC),QA-Pump (kWhs)\n'+start.strftime('%m/%d/%Y %H:%M:%S')+',20\n')
        result = energy.import_emporia_csv(str(path), device_gid='QA')
        self.assertEqual(result['imported'], 0)
        self.assertEqual(result['compacted_conflicts'], 1)
        self.assertEqual(self.total(), 2)
