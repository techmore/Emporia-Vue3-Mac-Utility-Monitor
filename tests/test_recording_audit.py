"""Regression cases from the live recording/house-control audit."""
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import aqara_local
import climate_collect
import house_control
import emporia_mains
import energy
import web

spec = importlib.util.spec_from_file_location('matter_ready', Path(__file__).parents[1]/'scripts/check_matter_ready.py')
ready = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ready)


class RecordingAuditTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        database = patch.object(energy, 'DB_PATH', str(Path(directory.name)/'audit.db'))
        database.start()
        self.addCleanup(database.stop)
        energy.ensure_table()

    def test_timezone_offsets_do_not_turn_recent_samples_into_future_or_stale_data(self):
        now = datetime.now(timezone.utc)
        for zone in (timezone.utc, timezone(timedelta(hours=-4)), timezone(timedelta(hours=9))):
            self.assertTrue(web._reading_fresh({'timestamp': now.astimezone(zone).isoformat()}, 180))
            self.assertFalse(web._reading_fresh({'timestamp': (now-timedelta(minutes=4)).astimezone(zone).isoformat()}, 180))
        self.assertFalse(web._reading_fresh({'timestamp':'not-a-time'}))
        self.assertTrue(web._reading_fresh({'timestamp':datetime.now().isoformat()}))

    def test_one_sensor_event_does_not_refresh_unrelated_sensors(self):
        node = {'available':True, 'attributes':{
            '5/57/18':'one', '5/29/3':[6], '6/1026/0':2000,
            '10/57/18':'two', '10/29/3':[11], '11/1026/0':2100}}
        self.assertEqual(aqara_local.record_node(node, 'matter_snapshot'), 2)
        before = {r['did']:r['observed_at'] for r in aqara_local.get_sensors()}
        node['attributes']['6/1026/0'] = 2250
        self.assertEqual(aqara_local.record_node(node, 'matter_event', changed_path='6/1026/0'), 1)
        after = {r['did']:r for r in aqara_local.get_sensors()}
        self.assertEqual(after['two']['observed_at'],before['two'])
        self.assertEqual(after['one']['temperature'],22.5)
        self.assertEqual(aqara_local.record_node(node, 'matter_event', changed_path='99/1026/0'), 0)

    def test_invalid_cloud_values_cannot_partially_record_a_poll(self):
        for bad in (True, float('nan'), float('inf'), '0.2'):
            vue = SimpleNamespace(get_device_list_usage=lambda **_: {'123':SimpleNamespace(channels={
                '1':SimpleNamespace(name='Main',usage=.01),
                '2':SimpleNamespace(name='bad',usage=bad)})})
            with self.subTest(value=bad), self.assertRaises(ValueError):
                energy.poll_and_store(vue, ['123'])
            conn = energy._connect()
            try:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM readings').fetchone()[0],0)
            finally:
                conn.close()

    def test_cloud_redirect_cannot_forward_a_token(self):
        response=SimpleNamespace(status_code=302)
        vue=SimpleNamespace(auth=SimpleNamespace(tokens={'id_token':'fixture'}))
        end=datetime.now(timezone.utc).replace(second=0,microsecond=0)-timedelta(minutes=1)
        with patch('emporia_mains.requests.get', return_value=response) as get:
            with self.assertRaises(ValueError):
                emporia_mains.collect(vue,'123',end,end)
            self.assertFalse(get.call_args.kwargs['allow_redirects'])

    def test_malformed_home_assistant_time_does_not_abort_other_sensor_imports(self):
        now = datetime.now(timezone.utc).isoformat()
        rows = [{'entity_id':'sensor.bad','state':'20','last_updated':123,
                 'attributes':{'device_class':'temperature','unit_of_measurement':'°C'}},
                {'entity_id':'sensor.good','state':'0','last_updated':now,
                 'attributes':{'device_class':'temperature','unit_of_measurement':'°C'}}]
        observations = climate_collect.home_assistant_observations(rows,['sensor.bad','sensor.good'])
        self.assertEqual(len(observations),1)
        self.assertEqual(observations[0]['temperature_c'],0)

    def test_house_projection_rejects_explicit_cross_panel_summary(self):
        with self.assertRaises(ValueError):
            house_control.project_state({}, {'active_device_gid':'123'},
                                        [{'device_gid':'other','channel_name':'Kitchen'}])

    def test_standard_house_endpoint_exposes_measured_history_without_poll_sum_fallback(self):
        menu = {'active_device_gid':'123', 'online':True, 'last_reading':None,
                'current_watts':0, 'breaker_slots':[]}
        with patch.object(web, '_build_menu_summary_payload', return_value=menu), \
                patch.object(energy, 'get_summary', return_value=[]), \
                patch.object(energy, 'get_latest', return_value=[]):
            result = web.app.test_client().get('/api/house-control/state')
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json['mains'],[])
        self.assertEqual(len(result.json['mains_history']['series'][0]['values']),1440)
        self.assertTrue(all(v is None for v in result.json['mains_history']['series'][0]['values']))

    def test_matter_missing_listener_fails_readiness(self):
        with patch.object(ready.socket,'create_connection',side_effect=OSError('no listener')):
            self.assertFalse(ready.wait_ready(timeout=0))
        with patch.object(ready.socket,'create_connection'):
            self.assertTrue(ready.wait_ready(timeout=0))
