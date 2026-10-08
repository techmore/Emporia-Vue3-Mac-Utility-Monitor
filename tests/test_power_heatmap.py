import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import energy


class PowerHeatmapTests(unittest.TestCase):
    def test_device_scope_zero_and_missing_hours(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            energy, 'DB_PATH', str(Path(directory) / 'heatmap.db'),
        ):
            energy.ensure_table()
            conn = energy._connect()
            with conn:
                conn.executemany(
                    'INSERT INTO readings(timestamp,device_gid,channel_name,usage_kwh) '
                    'VALUES (?,?,?,?)',
                    [('2026-10-01T00:00:00', 'A', 'Heat Pump', 0),
                     ('2026-10-01T01:00:00', 'A', 'Heat Pump', 0.1),
                     ('2026-10-01T01:01:00', 'A', 'Heat Pump', 0.2),
                     ('2026-10-01T01:00:00', 'B', 'Other', 99),
                     ('2026-10-01T01:00:00', 'A', 'Main', 99),
                     ('2026-10-08T00:00:00', 'A', 'Future', 99)],
                )
            conn.close()
            result = energy.get_power_heatmap('A', end=datetime(2026, 10, 8))
            self.assertEqual(len(result['hours']), 168)
            self.assertEqual(len(result['circuits']), 1)
            cells = result['circuits'][0]['cells']
            self.assertEqual(cells[0]['kwh'], 0)
            self.assertAlmostEqual(cells[1]['kwh'], 0.3)
            self.assertEqual(cells[1]['samples'], 2)
            self.assertIsNone(cells[2])

    def test_empty_history(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            energy, 'DB_PATH', str(Path(directory) / 'heatmap.db'),
        ):
            energy.ensure_table()
            self.assertEqual(energy.get_power_heatmap('missing')['circuits'], [])
