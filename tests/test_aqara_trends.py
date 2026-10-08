import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import aqara_local
import aqara_trends
import energy
import web


class AqaraTrendsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db = patch.object(energy, 'DB_PATH', str(Path(self.directory.name) / 'test.db'))
        self.db.start()
        energy.ensure_table()
        self.now = datetime(2026, 10, 8, 23, tzinfo=timezone.utc)
        self.sensor = {'did': 'lumi.one', 'name': 'First room'}
        self.client = web.app.test_client()

    def tearDown(self):
        self.db.stop()
        self.directory.cleanup()

    def record(self, age, temperature=20, *, device='lumi.one', humidity=40, online=1):
        conn = energy._connect()
        try:
            with conn:
                conn.execute('''INSERT INTO aqara_local_observations
                    (device_id,timestamp,name,temperature,humidity,online,source)
                    VALUES (?,?,?,?,?,?,?)''',
                    (device, (self.now - age).isoformat(), 'Sensor ' + device,
                     temperature, humidity, online, 'matter_snapshot'))
        finally:
            conn.close()

    def test_default_window_scope_future_exclusion_and_fahrenheit_zero(self):
        self.record(timedelta(hours=1), 0)
        self.record(timedelta(hours=5), 50)
        self.record(timedelta(minutes=-1), 60)
        self.record(timedelta(hours=1), 70, device='lumi.two')
        trends = aqara_trends.get_trends(device_id='lumi.one', now=self.now)
        self.assertEqual(set(trends['series']), {'lumi.one'})
        self.assertEqual(len(trends['series']['lumi.one']), 1)
        chart = aqara_trends.chart_model(trends, [self.sensor])
        self.assertEqual(chart['series'][0]['points'][0]['value'], 32)
        self.assertEqual(chart['unit'], 'F')
        self.assertTrue(chart['has_data'])
        self.assertEqual(aqara_trends.chart_model(trends, [self.sensor], 'C')
                         ['series'][0]['points'][0]['value'], 0)

    def test_missing_offline_and_null_buckets_break_segments(self):
        self.record(timedelta(minutes=22), 20)
        self.record(timedelta(minutes=17), 21)
        self.record(timedelta(minutes=12), 55, online=0)
        self.record(timedelta(minutes=7), None)
        self.record(timedelta(minutes=2), 22)
        trends = aqara_trends.get_trends(now=self.now)
        chart = aqara_trends.chart_model(trends, [self.sensor])
        series, = chart['series']
        self.assertEqual(len(series['points']), 3)
        self.assertEqual([len(segment) for segment in series['segments']], [2, 1])
        self.assertEqual([point['samples'] for point in series['points']], [1, 1, 1])

    def test_bucket_means_preserve_min_max_and_humidity_zero(self):
        self.record(timedelta(minutes=2), 20, humidity=0)
        self.record(timedelta(minutes=1), 22, humidity=0)
        trends = aqara_trends.get_trends(now=self.now)
        row, = trends['series']['lumi.one']
        self.assertEqual((row['value'], row['minimum'], row['maximum'], row['samples']),
                         (21, 20, 22, 2))
        humidity = aqara_trends.get_trends(metric='humidity', now=self.now)
        chart = aqara_trends.chart_model(humidity, [self.sensor])
        self.assertEqual(chart['unit'], '% RH')
        self.assertEqual(chart['series'][0]['points'][0]['value'], 0)

    def test_all_history_is_bounded_per_sensor_and_empty_history_not_zero(self):
        for minute in range(3000):
            self.record(timedelta(minutes=minute + 1))
        trends = aqara_trends.get_trends('all', now=self.now)
        self.assertLessEqual(len(trends['series']['lumi.one']), 121)
        chart = aqara_trends.chart_model(trends, [self.sensor])
        self.assertTrue(all(30 <= p['x'] <= 380 for p in chart['series'][0]['points']))
        empty = aqara_trends.get_trends(device_id='unknown', now=self.now)
        empty_chart = aqara_trends.chart_model(empty, [{'did': 'unknown', 'name': 'Unknown'}])
        self.assertFalse(empty_chart['has_data'])
        self.assertEqual(empty_chart['series'][0]['points'], [])

    def test_all_windows_and_invalid_options(self):
        self.record(timedelta(days=40))
        for window in ('4h', '24h', '7d', '30d'):
            self.assertEqual(aqara_trends.get_trends(window, now=self.now)['series'], {})
        self.assertTrue(aqara_trends.get_trends('all', now=self.now)['series'])
        for options in ({'window': 'forever'}, {'metric': 'temperature; DROP TABLE readings'},
                        {'device_id': ''}, {'now': self.now.replace(tzinfo=None)}):
            with self.assertRaises(ValueError):
                aqara_trends.get_trends(**options)
        with self.assertRaises(ValueError):
            aqara_trends.chart_model(aqara_trends.get_trends(now=self.now), [], 'unknown')

    def test_page_default_fahrenheit_own_tab_and_label_persistence(self):
        self.record(timedelta(minutes=1), 0)
        aqara_local.save_label('lumi.one', 'Verified room')
        with patch('aqara.get_sensors') as cloud:
            response = self.client.get('/aqara')
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('32.0<span class="unit">°F</span>', html)
        self.assertIn('Verified room', html)
        self.assertIn('href="/aqara" class="active">Aqara', html)
        self.assertIn('value="4h" selected', html)
        self.assertIn('Explore history', html)
        self.assertIn('aqara-sensor-grid', web.AQARA_HTML)
        self.assertNotIn('aqara-sensor-grid', web.CIRCUIT_HTML)
        self.assertNotIn('Settings Workspace', html)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        cloud.assert_not_called()
        self.assertEqual(aqara_local.get_history()[0]['temperature'], 0)

    def test_detail_plot_all_celsius_and_unknown_options(self):
        self.record(timedelta(minutes=1), 0)
        self.record(timedelta(minutes=1), 10, device='lumi.two')
        detail = self.client.get('/aqara?sensor=lumi.one&unit=C&window=24h').get_data(as_text=True)
        self.assertIn('0.0<span class="unit">°C</span>', detail)
        self.assertNotIn('Sensor lumi.two', detail)
        comparison = self.client.get('/aqara?view=all&metric=humidity&window=7d')
        self.assertIn('All sensors · Humidity', comparison.get_data(as_text=True))
        for query in ('window=bad', 'unit=bad', 'metric=bad'):
            self.assertEqual(self.client.get('/aqara?' + query).status_code, 400)
        self.assertEqual(self.client.get('/aqara?sensor=unknown').status_code, 404)

    def test_labels_require_known_identity_valid_name_and_same_origin(self):
        self.record(timedelta(minutes=1))
        url, payload = '/api/aqara/local/label', {'device_id': 'lumi.one', 'name': 'Room'}
        self.assertEqual(self.client.post(url, json=payload).status_code, 403)
        self.assertEqual(self.client.post(url, json=payload, headers={
            'Origin': 'https://untrusted.example'}).status_code, 403)
        headers = {'Origin': 'http://localhost'}
        for invalid in ({**payload, 'name': 'x' * 121}, {**payload, 'name': '\n'},
                        {**payload, 'name': '\x7f'}, {**payload, 'name': None},
                        {**payload, 'device_id': 'unknown'}, {**payload, 'extra': True}):
            self.assertEqual(self.client.post(url, json=invalid, headers=headers).status_code, 400)
        self.assertEqual(self.client.post(url, json=payload, headers=headers).status_code, 200)
        self.assertEqual(aqara_local.get_sensors()[0]['name'], 'Room')
        aqara_local.record_node({'attributes': {
            '5/57/18': 'lumi.one', '5/29/3': [6], '6/1026/0': 2000,
        }}, 'matter_event')
        self.assertEqual(aqara_local.get_sensors()[0]['name'], 'Room')
        self.assertEqual(self.client.post(url, json={**payload, 'name': ''},
                                         headers=headers).status_code, 200)
        self.assertNotEqual(aqara_local.get_sensors()[0]['name'], 'Room')

    def test_room_label_html_is_escaped_and_oversized_requests_are_rejected(self):
        self.record(timedelta(minutes=1))
        aqara_local.save_label('lumi.one', '<script>alert("no")</script>')
        html = self.client.get('/aqara?view=all').get_data(as_text=True)
        self.assertNotIn('<script>alert("no")</script>', html)
        self.assertIn('&lt;script&gt;', html)
        response = self.client.post('/api/aqara/local/label', json={
            'device_id': 'lumi.one', 'name': 'x' * 9000}, headers={'Origin': 'http://localhost'})
        self.assertEqual(response.status_code, 413)
