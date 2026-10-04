from datetime import datetime, timedelta
import unittest
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
             patch.object(energy,'get_panel_layout',return_value=[{'channel_name':'Pump','label':'Well pump'}]), \
             patch.object(energy,'get_main_total',return_value={'total_kwh':3}), \
             patch.object(web,'_poller_status_snapshot',return_value={'ok':True,'poller_running':True}):
            return web.app.test_client().get('/api/menu-summary')

    def test_native_summary_uses_fresh_power_and_saved_labels(self):
        response=self.snapshot(datetime.now().isoformat())
        self.assertEqual(response.status_code,200)
        data=response.json
        self.assertTrue(data['online'])
        self.assertEqual(data['current_watts'],1200)
        self.assertEqual(data['recorded_kwh'],3)
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
        self.assertEqual(data['recorded_kwh'],3)
