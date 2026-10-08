import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import energy
import web
from solar_model import hourly_generation_offset


class PowerHeatmapTests(unittest.TestCase):
    def test_repetition_coverage_and_complete_week(self):
        from datetime import timedelta
        rows = []
        for day in range(14):
            for hour in range(24):
                moment = datetime(2026, 9, 7) + timedelta(days=day, hours=hour)
                rows.append({'channel_name': 'Main', 'hour': moment.isoformat(),
                             'kwh': 1 if day < 7 else 3, 'minutes': 60})
        result = energy._weekly_pattern(rows)['main']
        self.assertEqual(result['supported'], 168)
        self.assertEqual(result['weekly_kwh'], 336)
        self.assertEqual(result['low_kwh'], 168)
        self.assertEqual(result['high_kwh'], 504)
        rows[0]['minutes'] = 56
        result = energy._weekly_pattern(rows)['main']
        self.assertEqual(result['supported'], 167)
        self.assertIsNone(result['weekly_kwh'])

    def test_generation_matches_hours_not_weekly_totals(self):
        night_load = [1 if hour % 24 < 6 else 0 for hour in range(168)]
        result = hourly_generation_offset(night_load, 10, 22.58)
        self.assertEqual(result['direct_kwh'], 0)
        self.assertAlmostEqual(result['generation_kwh'], 70)
        self.assertEqual(result['avoided_cost'], 0)
        self.assertIsNone(result['export_credit'])
        result = hourly_generation_offset([10] * 168, 10, 22.58, 0)
        self.assertAlmostEqual(result['direct_kwh'], 70)
        self.assertAlmostEqual(result['avoided_cost'], 70 * 0.2258)

    def test_invalid_generation_profiles_rejected(self):
        for loads in ([1] * 167, [None] * 168, [-1] * 168):
            with self.assertRaises(ValueError):
                hourly_generation_offset(loads, 10, 22.58)

    def test_forecast_route_renders_complete_profile_and_rejects_bad_input(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            energy, 'DB_PATH', str(Path(directory) / 'heatmap.db'),
        ):
            energy.ensure_table()
            main = {'name': 'Main', 'cells': [{'kwh': 1}] * 168, 'supported': 168,
                    'weekly_kwh': 168, 'low_kwh': 150, 'high_kwh': 180}
            with patch.object(energy, 'get_weekly_power_pattern', return_value={
                'main': main, 'circuits': [],
            }):
                response = web.app.test_client().get('/trends?heatmap_generation=10&heatmap_export=0')
                self.assertEqual(response.status_code, 200)
                self.assertIn('Weekly generation 70.00 kWh', response.get_data(as_text=True))
            for query in ('heatmap_generation=nan', 'heatmap_generation=1001', 'heatmap_export=-1'):
                self.assertEqual(web.app.test_client().get('/trends?' + query).status_code, 400)
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
