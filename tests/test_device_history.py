import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import device_history
import energy
import web


class DeviceHistoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db = patch.object(energy, 'DB_PATH', str(Path(self.directory.name) / 'test.db'))
        self.db.start()
        energy.ensure_table()
        self.now = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)
        conn = energy._connect()
        for identity in ('first', 'second'):
            conn.execute('INSERT INTO kasa_devices VALUES (?,?,?,?,?)',
                         (identity, identity, identity, identity, self.now.isoformat()))
        conn.commit()
        conn.close()

    def tearDown(self):
        self.db.stop()
        self.directory.cleanup()

    def observe(self, seconds, enabled, identity='first'):
        stamp = (self.now + timedelta(seconds=seconds)).isoformat()
        conn = energy._connect()
        conn.execute('INSERT INTO kasa_observations VALUES (?,?,?,?,?,?,?)',
                     (identity, stamp, 'ok' if enabled is not None else 'unavailable',
                      enabled, 'HS220', identity, None))
        conn.execute('INSERT INTO kasa_query_metrics VALUES (?,?,?,?,?)',
                     (identity, stamp, 12, 0, 'collector'))
        conn.commit()
        conn.close()

    def test_failed_queries_and_long_gaps_are_not_enabled_time(self):
        for seconds, enabled in [(-600, 1), (-590, 1), (-580, None),
                                 (-570, 1), (-200, 1), (-190, 0), (-180, 0)]:
            self.observe(seconds, enabled)
        series = device_history.get_history('kasa', now=self.now)['series'][0]
        self.assertEqual(series['summary']['covered_seconds'], 30)
        self.assertEqual(series['summary']['enabled_seconds'], 20)
        self.assertEqual(series['summary']['samples'], 7)
        self.assertEqual(series['summary']['valid_samples'], 6)
        self.assertEqual(series['points'][0]['brightness'], 0)

    def test_window_boundary_is_clipped_and_future_not_counted(self):
        for seconds in (-86410, -86390, 10):
            self.observe(seconds, 1)
        summary = device_history.get_history('kasa', now=self.now)['series'][0]['summary']
        self.assertEqual(summary['enabled_seconds'], 10)
        self.assertEqual(summary['samples'], 1)

    def test_all_windows_bounded_and_colors_stable_distinct(self):
        for hour in range(721):
            self.observe(-hour * 3600, hour % 2)
        colors = None
        for window in device_history.WINDOWS:
            result = device_history.get_history('kasa', window, now=self.now)
            self.assertLessEqual(len(result['series'][0]['points']), 241)
            current = [item['color_index'] for item in result['series']]
            self.assertEqual(len(set(current)), 2)
            if colors is not None:
                self.assertEqual(colors, current)
            colors = current

    def test_hvac_zero_temperature_and_unknown_state(self):
        conn = energy._connect()
        for seconds in (-60, 0):
            conn.execute('INSERT INTO mitsubishi_observations VALUES (?,?,?)', (
                'zone', (self.now + timedelta(seconds=seconds)).isoformat(), json.dumps({
                    'name': '<img src=x>', 'connected': True, 'is_on': None,
                    'temperature_c': 0, 'heat_setpoint_c': 20, 'mode': 'heat',
                })))
        conn.commit()
        conn.close()
        item = device_history.get_history('mitsubishi', now=self.now)['series'][0]
        self.assertEqual(item['points'][0]['temperature'], 0)
        self.assertEqual(item['summary']['covered_seconds'], 0)
        self.assertEqual(item['summary']['modes'], {'heat': 2})

    def test_invalid_queries_and_cache_policy(self):
        client = web.app.test_client()
        for query in ('window=forever', 'window=7d&window=24h', 'secret=x',
                      'end=2099-01-01T00:00:00%2B00:00', 'end=2026-01-01T00:00:00'):
            self.assertEqual(client.get('/api/kasa/history?' + query).status_code, 400)
        self.assertEqual(client.get('/api/unknown/history').status_code, 404)
        response = client.get('/api/kasa/history?window=30d')
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response.headers['Cache-Control'])
        for path in ('/kasa', '/mitsubishi'):
            response = client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn(b'device-history.js', response.data)

    def test_timezone_required_and_unique_palette(self):
        with self.assertRaises(ValueError):
            device_history.bounds('24h', now=datetime(2026, 10, 9))
        self.assertEqual(len(set(device_history.color_slots(
            [str(index) for index in range(16)]).values())), 16)
        self.assertEqual(device_history.color_slots(['upstairs', 'downstairs']),
                         {'downstairs': 0, 'upstairs': 1})
