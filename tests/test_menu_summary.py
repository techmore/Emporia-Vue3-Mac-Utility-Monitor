import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import energy
import web


class MenuSummaryTests(unittest.TestCase):
    def snapshot(self, timestamp):
        rows = [
            {'channel_name':'Main','usage_kwh':.02,'timestamp':timestamp},
            {'channel_name':'Pump','usage_kwh':.01,'timestamp':timestamp},
            {'channel_name':'Balance','usage_kwh':.005,'timestamp':timestamp},
            {'channel_name':'Light','usage_kwh':0,'timestamp':timestamp},
        ]
        with patch.object(energy,'get_active_device_gid',return_value='A'), \
             patch.object(energy,'get_latest',return_value=rows), \
             patch.object(energy,'get_panel_layout',return_value=[{'channel_name':'Pump','label':'Well pump','amps':20,'poles':1}]), \
             patch.object(energy,'get_main_total',return_value={'total_kwh':3,'total_cents':41}), \
             patch.object(energy,'get_monthly_costs',return_value=[{'total_cents':250,'days_recorded':4}]), \
             patch.object(web,'_poller_status_snapshot',return_value={'ok':True,'poller_running':True}):
            return web.app.test_client().get('/api/menu-summary')

    def test_slots_flag_top_usage_and_relative_load(self):
        data = self.snapshot(datetime.now().isoformat()).json
        by_name = {s['channel_name']: s for s in data['breaker_slots'] if s['channel_name']}
        self.assertTrue(by_name['Pump']['is_peak'])
        self.assertFalse(by_name['Light']['is_peak'])
        self.assertEqual(by_name['Pump']['usage_state'], 'heat')
        self.assertIsNone(by_name['Light']['usage_state'])

    def test_native_summary_uses_fresh_power_and_saved_labels(self):
        response=self.snapshot(datetime.now().isoformat())
        self.assertEqual(response.status_code,200)
        data=response.json
        self.assertTrue(data['online'])
        self.assertEqual(data['current_watts'],1200)
        self.assertEqual(data['recorded_kwh'],3)
        self.assertEqual((data['cost_24h'], data['month_cost'], data['month_days_recorded']), (0.41, 2.5, 4))
        self.assertEqual([c['channel_name'] for c in data['top_circuits']],['Pump','Light'])
        self.assertEqual(data['top_circuits'][0]['display_name'],'Well pump')
        self.assertEqual(data['top_circuits'][1]['watts'],0)
        self.assertNotIn('panel_fragment',data)

    def test_offline_summary_keeps_recorded_usage_but_hides_live_power(self):
        response=self.snapshot((datetime.now()-timedelta(hours=1)).isoformat())
        data=response.json
        self.assertFalse(data['online'])
        self.assertIsNone(data['current_watts'])
        self.assertIsNone(data['cost_per_hour'])
        self.assertTrue(all(c['watts'] is None for c in data['top_circuits']))
        pump_slot = next(slot for slot in data['breaker_slots'] if slot['channel_name'] == 'Pump')
        self.assertIsNone(pump_slot['load_percent'])
        self.assertIsNone(pump_slot['load_state'])
        self.assertEqual(data['recorded_kwh'],3)


class FillGapsTests(unittest.TestCase):
    def test_missing_days_and_hours_become_empty_buckets(self):
        days = web._fill_gaps([{'day': '2026-10-01', 'total_kwh': 1}, {'day': '2026-10-04', 'total_kwh': 2}], 'day')
        self.assertEqual([d['day'] for d in days], ['2026-10-01', '2026-10-02', '2026-10-03', '2026-10-04'])
        self.assertIsNone(days[1]['total_kwh'])
        hours = web._fill_gaps([{'hour': '2026-10-01 01:00', 'total_kwh': 1}, {'hour': '2026-10-01 03:00', 'total_kwh': 1}],
                               'hour', hourly=True)
        self.assertEqual(len(hours), 3)
