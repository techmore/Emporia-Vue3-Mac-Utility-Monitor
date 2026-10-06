from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import climate
import climate_collect
import energy
import web


class ClimateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(energy, 'DB_PATH', str(Path(self.directory.name) / 'history.db'))
        self.db_patch.start()
        energy.ensure_table()
        self.now = datetime.now(timezone.utc).replace(minute=15, second=0, microsecond=0)
        self.row = {'source': 'aqara', 'sensor_id': 'indoor', 'name': 'Office',
                    'timestamp': (self.now - timedelta(hours=1)).isoformat(), 'temperature_c': 21.5}

    def tearDown(self):
        self.db_patch.stop()
        self.directory.cleanup()

    def test_batch_atomic_deduplicates_and_preserves_placement(self):
        self.assertEqual(climate.ingest_observations([self.row], self.now)['inserted'], 1)
        climate.place_sensor('aqara', 'indoor', 'office')
        self.assertEqual(climate.ingest_observations([self.row], self.now)['duplicates'], 1)
        climate.ingest_observations([{**self.row, 'name': 'Renamed', 'timestamp': self.now.isoformat()}], self.now)
        data = climate.get_replay(now=self.now)
        self.assertEqual(data['sensors'][0]['room_id'], 'office')
        self.assertEqual(data['sensors'][0]['name'], 'Renamed')
        with self.assertRaises(ValueError):
            climate.ingest_observations([{**self.row, 'sensor_id': 'new'}, {**self.row, 'temperature_c': math.nan}], self.now)
        self.assertEqual(len(climate.get_replay(now=self.now)['sensors']), 1)

    def test_timezone_normalization_hourly_average_and_gaps(self):
        stamp = self.now - timedelta(hours=1)
        rows = [self.row, {**self.row, 'timestamp': (stamp + timedelta(minutes=5)).astimezone(timezone(timedelta(hours=-4))).isoformat(), 'temperature_c': 23.5}]
        climate.ingest_observations(rows, self.now)
        data = climate.get_replay(now=self.now)
        self.assertEqual(data['frames'][-2]['temperatures']['aqara:indoor'], 22.5)
        self.assertEqual(data['frames'][-1]['temperatures'], {})
        self.assertEqual(data['frames'][0]['temperatures'], {})

    def test_invalid_missing_future_and_unzoned_observations_rejected(self):
        for change in ({'timestamp': self.now.replace(tzinfo=None).isoformat()},
                       {'timestamp': (self.now+timedelta(hours=1)).isoformat()},
                       {'temperature_c': True}, {'temperature_c': None}, {'humidity_pct': 101},
                       {'source': 'demo'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                climate.ingest_observations([{**self.row, **change}], self.now)
        self.assertEqual(climate.get_replay(now=self.now)['sensors'], [])

    def test_demo_never_writes_and_replay_bounds(self):
        data = climate.get_replay(30, demo=True, now=self.now)
        self.assertEqual(len(data['frames']), 721)
        self.assertTrue(data['demo'])
        self.assertEqual(climate.get_replay(now=self.now)['sensors'], [])
        with self.assertRaises(ValueError):
            climate.get_replay(365)

    def test_api_validation_and_origin_guard(self):
        client = web.app.test_client()
        self.assertEqual(client.get('/house').status_code, 200)
        self.assertEqual(client.get('/api/climate/replay?days=no').status_code, 400)
        self.assertEqual(client.post('/api/climate/readings', json={'observations': []}).status_code, 400)
        self.assertEqual(client.post('/api/climate/readings', json={'observations': [self.row]}, headers={'Origin': 'https://elsewhere.test'}).status_code, 403)
        self.assertEqual(client.post('/api/climate/readings', json={'observations': [self.row]}).status_code, 200)
        self.assertEqual(client.post('/api/climate/placement', json={'source':'aqara','sensor_id':'indoor','room_id':[]}).status_code, 400)
        self.assertEqual(client.post('/api/climate/placement', json={'source':'aqara','sensor_id':'indoor','room_id':'office'}).status_code, 200)

    def test_home_assistant_normalization_and_unavailable_states(self):
        observed = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=5)

        def state(entity, value, unit='°F'):
            return {'entity_id':entity,'state':value,'last_updated':observed.isoformat(),
                    'attributes':{'device_class':'temperature','unit_of_measurement':unit}}
        rows = climate_collect.home_assistant_observations([
            state('sensor.office', '68'), state('sensor.outside', 'unavailable'),
            state('sensor.other', '55'), state('sensor.unknown', '20', 'unknown')],
            ['sensor.office', 'sensor.outside', 'sensor.unknown'])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['temperature_c'], 20)
        self.assertEqual(rows[0]['timestamp'], observed.isoformat())
