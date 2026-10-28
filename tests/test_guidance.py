import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import energy
import guidance
import web


def synthetic_rows(days=30, start=datetime(2026, 4, 1), heater_hours=(6, 7, 19, 20)):
    rows = []
    for d in range(days):
        for h in range(24):
            wh = 1.5 if h in heater_hours else 0.0
            rows.append({'start': start + timedelta(days=d, hours=h), 'total_kwh': 1.0 + wh, 'wh_kwh': wh})
    return rows


class GuidanceModelTests(unittest.TestCase):
    def test_pv_shape_sums_to_one_and_is_zero_at_night(self):
        for month in range(1, 13):
            shape = guidance.pv_shape(month)
            self.assertAlmostEqual(sum(shape), 1.0, places=6)
            self.assertEqual(shape[0], 0)
            self.assertEqual(shape[23], 0)

    def test_profile_counts_only_complete_days_and_water_heater_share(self):
        rows = synthetic_rows(3)
        rows = [r for r in rows if not (r['start'].day == 3 and r['start'].hour == 5)]
        profile = guidance.load_profile(guidance.complete_days(rows))
        self.assertEqual(profile['days'], 2)
        self.assertAlmostEqual(profile['kwh_per_day'], 30.0)
        self.assertAlmostEqual(profile['wh_kwh_per_day'], 6.0)
        self.assertAlmostEqual(profile['wh_share_pct'], 20.0)
        # 06:00 and 19-20 are inside 17:00-07:00; 07:00 is not.
        self.assertAlmostEqual(profile['wh_evening_kwh_per_day'], 4.5)

    def test_scenarios_remove_shrink_and_shift_heater_energy(self):
        days = guidance.complete_days(synthetic_rows(2))
        totals = {name: sum(sum(h) for h in guidance.scenario_days(days, name).values())
                  for name in ('current', 'no_wh', 'hpwh', 'timer')}
        self.assertAlmostEqual(totals['current'], 60.0)
        self.assertAlmostEqual(totals['no_wh'], 48.0)
        self.assertAlmostEqual(totals['hpwh'], 48.0 + 12.0 * 0.33)
        self.assertAlmostEqual(totals['timer'], 48.0 + 12.0 * 1.05)
        timer = next(iter(guidance.scenario_days(days, 'timer').values()))
        self.assertEqual(timer[20], 1.0)
        self.assertGreater(timer[12], 1.0)

    def test_battery_never_creates_energy_and_raises_self_sufficiency(self):
        loads = guidance.scenario_days(guidance.complete_days(synthetic_rows()), 'current')
        pv = guidance.simulate(loads, 8)
        both = guidance.simulate(loads, 8, 13.5)
        self.assertGreater(both['self_sufficiency_pct'], pv['self_sufficiency_pct'])
        for result in (pv, both):
            balance = result['generation_kwh'] - result['export_kwh'] + result['import_kwh']
            self.assertLessEqual(result['load_kwh'], balance + 1e-6)

    def test_build_guidance_with_and_without_water_heater(self):
        doc = guidance.build_guidance(synthetic_rows())
        self.assertEqual(set(doc['scenarios']), {'current', 'no_wh', 'hpwh', 'timer'})
        current, removed = doc['scenarios']['current'], doc['scenarios']['no_wh']
        self.assertGreater(current['pv_kw'], removed['pv_kw'])
        self.assertGreaterEqual(current['battery_kwh'], removed['battery_kwh'])
        self.assertTrue(doc['caveats'])
        bare = guidance.build_guidance([dict(r, wh_kwh=None) for r in synthetic_rows(2)])
        self.assertEqual(list(bare['scenarios']), ['current'])
        self.assertEqual(guidance.build_guidance([])['profile'], {'days': 0})

    def test_find_water_heater(self):
        self.assertEqual(guidance.find_water_heater(['Main', 'Dryer', 'Water Heater']), 'Water Heater')
        self.assertIsNone(guidance.find_water_heater(['Main', 'Heat Pump']))

    def test_rows_from_hourly_uses_main_or_legs_and_scales_partial_hours(self):
        records = [
            {'hour_start': '2026-04-01T00:00:00', 'channel_name': 'Mains_A', 'kwh': 1.0, 'observed_seconds': 3600},
            {'hour_start': '2026-04-01T00:00:00', 'channel_name': 'Mains_B', 'kwh': 0.5, 'observed_seconds': 3600},
            {'hour_start': '2026-04-01T01:00:00', 'channel_name': 'Main', 'kwh': 0.9, 'observed_seconds': 3240},
            {'hour_start': '2026-04-01T02:00:00', 'channel_name': 'Main', 'kwh': 0.1, 'observed_seconds': 600},
        ]
        rows = guidance.rows_from_hourly(records, None)
        self.assertEqual(len(rows), 2)
        self.assertAlmostEqual(rows[0]['total_kwh'], 1.5)
        self.assertAlmostEqual(rows[1]['total_kwh'], 1.0)


class GuidanceRouteTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        database = patch.object(energy, 'DB_PATH', str(Path(directory.name) / 'test.db'))
        database.start()
        self.addCleanup(database.stop)
        self.addCleanup(web._guidance_cache.update, key=None, doc=None)
        web._guidance_cache.update(key=None, doc=None)
        energy.ensure_table()
        conn = energy._connect()
        with conn:
            for row in synthetic_rows(3):
                stamp = row['start'].isoformat()
                for name, kwh in (('Main', row['total_kwh']), ('Water Heater', row['wh_kwh'])):
                    conn.execute('INSERT INTO energy_hourly VALUES (?,?,?,?,?,?,?,?)',
                                 ('9', name, stamp, 'mains' if name == 'Main' else 'circuit',
                                  kwh, 3600, 60, 'now'))
            conn.execute("INSERT INTO readings(timestamp,device_gid,channel_num,channel_name,usage_kwh,cost_cents)"
                         " VALUES ('2026-04-03T23:59:00','9',1,'Main',0.01,0.1)")
        conn.close()

    def test_api_and_page_recompute_from_stored_hourly_energy(self):
        with patch.dict(os.environ, {}, clear=False):
            client = web.app.test_client()
            data = client.get('/api/guidance').get_json()
            self.assertEqual(data['water_heater_channel'], 'Water Heater')
            self.assertEqual(data['profile']['days'], 3)
            self.assertAlmostEqual(data['profile']['wh_share_pct'], 20.0)
            page = client.get('/guidance')
            self.assertEqual(page.status_code, 200)
            self.assertIn(b'Water heater removed', page.data)
            self.assertIn(b'Estimates, not a verdict', page.data)


if __name__ == '__main__':
    unittest.main()
