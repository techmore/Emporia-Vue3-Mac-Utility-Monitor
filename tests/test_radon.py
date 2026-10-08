import math
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import energy
import radon
import web


class RadonTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(energy, 'DB_PATH', str(Path(self.directory.name) / 'radon.db'))
        self.db_patch.start()
        energy.ensure_table()
        self.now = datetime.now(timezone.utc)
        self.row = {'source': 'ecosense', 'sensor_id': 'one', 'name': 'Basement',
                    'timestamp': self.now.isoformat(), 'value': 2.5, 'unit': 'pCi/L'}

    def tearDown(self):
        self.db_patch.stop()
        self.directory.cleanup()

    def test_api_ingestion_preserves_units_and_displays_verified_observation(self):
        client = web.app.test_client()
        observation = {**self.row, 'sensor_id': 'a', 'value': 0.7}
        response = client.post('/api/radon/readings', json={'observations': [observation]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['inserted'], 1)
        rows = radon.get_history('ecosense', 'a')
        self.assertEqual(rows[0]['measured_unit'], 'pCi/L')
        self.assertAlmostEqual(rows[0]['radon_bq_m3'], 25.9)
        html = client.get('/radon?source=ecosense&sensor_id=a&days=1').get_data(as_text=True)
        self.assertIn('0.7 pCi/L', html)
        self.assertIn(observation['timestamp'], html)
        duplicate = client.post('/api/radon/readings', json={'observations': [observation]})
        self.assertEqual(duplicate.get_json()['duplicates'], 1)

    def test_connection_endpoint_requires_local_origin_and_hides_password(self):
        client = web.app.test_client()
        credentials = {'email': 'test@example.com', 'password': 'private-password'}
        with patch('ecosense_collect.connect_account', return_value={
            'ok': True, 'state': 'collecting', 'devices': 1, 'inserted': 1,
        }) as connect:
            rejected = client.post('/api/ecosense/connect', json=credentials,
                                   headers={'Origin': 'https://untrusted.example'})
            self.assertEqual(rejected.status_code, 403)
            connect.assert_not_called()
            response = client.post('/api/ecosense/connect', json=credentials)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn('private-password', response.get_data(as_text=True))
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            connect.assert_called_once_with('test@example.com', 'private-password')

    def test_api_rejects_invalid_batches_without_partial_history(self):
        client = web.app.test_client()
        for invalid in ({**self.row, 'timestamp': 'unknown'},
                        {**self.row, 'timestamp': self.now.replace(tzinfo=None).isoformat()},
                        {**self.row, 'unit': 'pCL/L'}, {**self.row, 'value': None},
                        {**self.row, 'value': 10 ** 400},
                        {**self.row, 'timestamp': '0001-01-01T00:00:00+01:00'},
                        {**self.row, 'timestamp': '9999-12-31T23:59:59-01:00'}):
            response = client.post('/api/radon/readings',
                                   json={'observations': [self.row, invalid]})
            self.assertEqual(response.status_code, 400)
            self.assertEqual(radon.get_sensors(), [])
        self.assertEqual(client.post('/api/radon/readings', json=[]).status_code, 400)
        self.assertEqual(client.post('/api/radon/readings', json={'observations': [self.row]},
                                    headers={'Origin': 'https://untrusted.example'}).status_code, 403)
        self.assertEqual(radon.get_sensors(), [])

    def test_conversion_original_units_deduplication_and_scope(self):
        radon.ingest_observations([self.row], self.now)
        equivalent = {**self.row, 'timestamp': self.now.astimezone(
            timezone(timedelta(hours=-4))).isoformat()}
        self.assertEqual(radon.ingest_observations([equivalent], self.now)['duplicates'], 1)
        radon.ingest_observations([{**self.row, 'sensor_id': 'two', 'value': 90,
                                   'unit': 'Bq/m3'}], self.now)
        rows = radon.get_history('ecosense', 'one', now=self.now)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['radon_bq_m3'], 92.5)
        self.assertEqual(rows[0]['measured_value'], 2.5)
        self.assertEqual(rows[0]['measured_unit'], 'pCi/L')
        self.assertEqual(radon.get_history('ecosense', 'two', now=self.now)[0]['radon_bq_m3'], 90)
        self.assertEqual(radon.get_history('manual', 'one', now=self.now), [])

    def test_invalid_batch_never_partially_writes(self):
        for change in ({'value': math.nan}, {'value': math.inf}, {'value': True},
                       {'value': -1}, {'value': None}, {'unit': 'ppm'}, {'source': 'demo'},
                       {'timestamp': self.now.replace(tzinfo=None).isoformat()},
                       {'timestamp': (self.now + timedelta(hours=1)).isoformat()}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                radon.ingest_observations([self.row, {**self.row, **change}], self.now)
            self.assertEqual(radon.get_history('ecosense', 'one', now=self.now), [])

    def test_conflicting_retry_rolls_back_entire_batch(self):
        radon.ingest_observations([self.row], self.now)
        with self.assertRaises(ValueError):
            radon.ingest_observations([{**self.row, 'sensor_id': 'two'},
                                      {**self.row, 'value': 3}], self.now)
        self.assertEqual(radon.get_history('ecosense', 'two', now=self.now), [])
        self.assertEqual(radon.get_history('ecosense', 'one', now=self.now)[0]['measured_value'], 2.5)

    def test_history_bounds_and_no_synthetic_samples(self):
        self.assertEqual(radon.get_history('ecosense', 'one', now=self.now), [])
        for days in (True, 0, 366):
            with self.assertRaises(ValueError):
                radon.get_history('ecosense', 'one', days, self.now)

    def test_read_only_page_empty_state_and_sensor_isolation(self):
        client = web.app.test_client()
        empty = client.get('/radon')
        self.assertEqual(empty.status_code, 200)
        self.assertIn(b'No recorded radon readings', empty.data)
        radon.ingest_observations([self.row, {**self.row, 'sensor_id': 'two', 'value': 999}], self.now)
        page = client.get('/radon?source=ecosense&sensor_id=one')
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'2.5 pCi/L', page.data)
        self.assertNotIn(b'999.0', page.data)
        self.assertIn(b'not a live-status indicator', page.data)
        self.assertEqual(client.post('/radon', json={}).status_code, 405)

    def test_page_escapes_sensor_content_and_labels_old_data(self):
        old = {**self.row, 'sensor_id': '<script>bad()</script>',
               'timestamp': (self.now - timedelta(days=8)).isoformat()}
        radon.ingest_observations([old], self.now)
        response = web.app.test_client().get('/radon', query_string={
            'source': 'ecosense', 'sensor_id': old['sensor_id']})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b'<script>bad()</script>', response.data)
        self.assertIn(b'&lt;script&gt;', response.data)
        self.assertIn(b'Older history is not a current reading', response.data)

    def test_chart_averages_only_recorded_hours_and_preserves_zero(self):
        hour = self.now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=2)
        rows = [{'timestamp': hour.isoformat(), 'radon_bq_m3': 37},
                {'timestamp': (hour + timedelta(minutes=5)).isoformat(), 'radon_bq_m3': 111},
                {'timestamp': (hour + timedelta(hours=2)).isoformat(), 'radon_bq_m3': 0},
                {'timestamp': (hour - timedelta(days=8)).isoformat(), 'radon_bq_m3': 999}]
        chart = radon.hourly_chart(rows, self.now)
        self.assertEqual(len(chart['points']), 2)
        self.assertEqual(chart['points'][0]['mean'], 74)
        self.assertEqual(chart['points'][0]['samples'], 2)
        self.assertEqual(chart['points'][1]['mean'], 0)
        self.assertEqual(chart['points'][1]['y'], 180)
        self.assertEqual(chart['scale'], 74)
        self.assertLess(chart['points'][0]['x'], chart['points'][1]['x'])
        self.assertEqual(radon.hourly_chart([], self.now)['points'], [])

    def test_dashboard_plots_every_reading_instead_of_hourly_averages(self):
        start = self.now - timedelta(hours=4)
        observations = [{**self.row,
                         'timestamp': (start + timedelta(minutes=10 * index)).isoformat(),
                         'value': 0.5 + index / 100}
                        for index in range(20)]
        radon.ingest_observations(observations, self.now)
        for days in (1, 7, 30, 365):
            html = web.app.test_client().get(f'/radon?days={days}').get_data(as_text=True)
            self.assertEqual(html.count('<circle cx='), 20)
            self.assertIn('20 recorded readings plotted', html)
        rows = radon.get_history('ecosense', 'one', now=self.now)
        chart = radon.observation_chart(rows, self.now)
        self.assertEqual(len(chart['points']), len(rows))
        self.assertEqual(chart['points'][0]['timestamp'], rows[0]['timestamp'])
        self.assertEqual(chart['points'][0]['value'], rows[0]['radon_bq_m3'])
        self.assertGreater(chart['points'][-1]['x'] - chart['points'][0]['x'], 800)

    def test_dashboard_windows_auto_select_sensor_and_preserve_old_history(self):
        old = {**self.row, 'timestamp': (self.now - timedelta(days=90)).isoformat()}
        radon.ingest_observations([old], self.now)
        client = web.app.test_client()
        week = client.get('/radon')
        self.assertEqual(week.status_code, 200)
        self.assertIn(b'No measurements recorded', week.data)
        year = client.get('/radon?days=365')
        self.assertEqual(year.status_code, 200)
        self.assertIn(b'Latest recorded: <strong>2.50 pCi/L</strong>', year.data)
        self.assertIn(b'one dot per measurement, with no averaging', year.data)
        for window in (1, 7, 30, 365):
            self.assertEqual(client.get(f'/radon?days={window}').status_code, 200)
        for window in ('0', '366', 'bad'):
            self.assertEqual(client.get(f'/radon?days={window}').status_code, 400)
        missing = client.get('/radon?source=ecosense&sensor_id=missing&days=365')
        self.assertNotIn(b'Latest recorded: 2.5', missing.data)
