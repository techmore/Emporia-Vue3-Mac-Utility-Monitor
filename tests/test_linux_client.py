"""Linux client cache invariants; no desktop, collector, credentials or live data required."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('linux_client_core', Path(__file__).parents[1] / 'linux/client_core.py')
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


class LinuxClientTests(unittest.TestCase):
    def test_temperature_bands_and_cached_readings_never_look_live(self):
        self.assertEqual([core.temperature_band(v) for v in (0, 69.9, 70, 75, 75.1, 77.9, 78, 90)],
                         ['cold', 'cold', 'comfortable', 'comfortable', 'warm', 'warm', 'hot', 'hot'])
        self.assertIsNone(core.temperature_band(True))
        record = {'origin': 'https://example.org', 'fetched_at': 100,
                  'data': {'online': True, 'temperature_sensors': [{'stale': False, 'metrics': {'temperature_f': 72}}]}}
        cached = core.display_state(record, record['origin'], False, 101)
        self.assertTrue(cached['temperature_sensors'][0]['stale'])
        self.assertEqual(cached['temperature_sensors'][0]['metrics']['temperature_f'], 72)
        self.assertFalse(record['data']['temperature_sensors'][0]['stale'])

    def test_temperature_history_axis_and_values_are_bounded(self):
        payload = {'window': '4h', 'seconds': 300, 'start': '2026-10-18T00:00:00+00:00',
                   'end': '2026-10-18T00:05:00+00:00',
                   'times': ['2026-10-18T00:00:00+00:00', '2026-10-18T00:05:00+00:00'],
                   'series': [{'source': 'aqara_local', 'sensor_id': 'a', 'name': 'Attic', 'values': [0, None]}]}
        self.assertEqual(core.validate_temperature_history(payload), payload)
        for values in ([False, None], [float('nan'), None], [10]):
            bad = {**payload, 'series': [{**payload['series'][0], 'values': values}]}
            with self.assertRaises(ValueError):
                core.validate_temperature_history(bad)
    def test_origin_rejects_credentials_and_remote_plaintext(self):
        for value in ('http://192.168.1.2:5051', 'https://user:pass@example.org',
                      'https://example.org/settings', 'https://example.org?token=x',
                      'https://example.org:99999', 'file:///tmp/a'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                core.validate_origin(value)
        self.assertEqual(core.validate_origin('http://127.0.0.1:15001/'), 'http://127.0.0.1:15001')

    def test_offline_and_old_cache_withhold_live_values(self):
        record = {'origin': 'https://example.org', 'fetched_at': 100,
                  'data': {'online': True, 'current_watts': 400, 'cost_per_hour': .1,
                           'recorded_kwh': 12, 'top_circuits': [{'watts': 300}]}}
        for connected, now in ((False, 101), (True, 281), (True, 90)):
            state = core.display_state(record, record['origin'], connected, now)
            self.assertFalse(state['online'])
            self.assertIsNone(state['current_watts'])
            self.assertIsNone(state['top_circuits'][0]['watts'])
            self.assertEqual(state['recorded_kwh'], 12)
        self.assertEqual(record['data']['current_watts'], 400)

    def test_cross_collector_cache_is_not_reused(self):
        state = core.display_state({'origin': 'https://old.example', 'data': {'online': True}},
                                   'https://new.example', True, 100)
        self.assertFalse(state['online'])
        self.assertNotIn('recorded_kwh', state)

    def test_private_atomic_cache_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            core.private_json(path, {'value': 1})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(core.read_json(path), {'value': 1})

    def test_invalid_server_payload_is_rejected(self):
        for value in ({}, {'version': '1', 'online': True, 'current_watts': float('nan')},
                      {'version': '1', 'online': 'yes'}):
            with self.assertRaises(ValueError):
                core.validate_summary(value)
