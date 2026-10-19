"""Exercise production request handlers with fake widgets; no display required."""
import ast
import importlib.util
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from urllib.parse import urlencode, urlsplit

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('window_scope_core', ROOT / 'linux/client_core.py')
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


def methods(filename, class_name, names, namespace):
    tree = ast.parse((ROOT / 'linux' / filename).read_text())
    source = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    source.bases = []
    source.keywords = []
    source.body = [node for node in source.body if isinstance(node, ast.FunctionDef) and node.name in names]
    module = ast.Module(body=[source], type_ignores=[])
    exec(compile(module, str(ROOT / 'linux' / filename), 'exec'), namespace)
    return namespace[class_name]


Window = methods('temperatures.py', 'TemperatureWindow', {'fetch', 'reset_collector'}, {
    'time': time, 'urlencode': urlencode,
    'temperature_history_cache_key': core.temperature_history_cache_key,
    'load_temperature_window_cache': core.load_temperature_window_cache,
    'validate_temperature_history': core.validate_temperature_history,
    'private_json': core.private_json,
    'Gtk': SimpleNamespace(StringList=SimpleNamespace(new=lambda strings: strings)),
})
Client = methods('energy_monitor.py', 'EnergyMonitor', {'receive_temperatures'}, {
    'json': json, 'urlsplit': urlsplit,
})


class TemperatureWindowScopeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.origin = 'https://example.org'
        self.source = 'collector-A'
        self.requests = []
        self.installed = []
        self.messages = []
        self.app = SimpleNamespace(origin=self.origin, identity=lambda: (self.source, 'panel'),
            cache_file=lambda: self.directory / 'summary.json',
            fetch_temperature_history=lambda endpoint, callback: self.requests.append((endpoint, callback)))
        self.window = Window()
        self.window.__dict__.update(closed=False, period=SimpleNamespace(get_selected=lambda: 1),
            end=None, app=self.app, collector_source=self.source, generation=0, payload=None,
            pending=False, status=SimpleNamespace(set_label=self.messages.append),
            sensor=SimpleNamespace(set_model=lambda model: None),
            readout=SimpleNamespace(set_label=lambda text: None),
            chart=SimpleNamespace(queue_draw=lambda: None, set_series=lambda *a: None, set_selected=lambda *a: None), chart_key=None, render=lambda: None)
        def install(payload, cached=False):
            self.window.payload = payload
            self.installed.append((payload, cached))
        self.window.install = install

    def payload(self, source='collector-A'):
        return {'window': '24h', 'seconds': 1800, 'collector_source_id': source,
                'start': '2026-10-17T00:00:00+00:00', 'end': '2026-10-18T00:00:00+00:00',
                'times': ['2026-10-18T00:00:00+00:00'],
                'series': [{'source': 'aqara_local', 'sensor_id': 'same-id',
                            'name': source, 'values': [55]}]}

    def test_offline_reopen_restores_only_matching_source_scoped_history(self):
        self.window.fetch()
        self.requests[-1][1](self.payload())
        self.assertEqual(len(self.installed), 1)
        self.window.payload = None
        self.window.fetch()
        self.assertTrue(self.installed[-1][1])
        self.requests[-1][1](None)
        self.assertEqual(self.window.payload['series'][0]['name'], 'collector-A')
        self.source = 'collector-B'
        self.window.fetch()
        self.requests[-1][1](None)
        self.assertIsNone(self.window.payload)
        self.assertEqual(self.window.collector_source, 'collector-B')
        self.assertEqual(self.window.ids, [])
        path = self.directory / core.temperature_history_cache_key(self.origin, 'collector-A', self.requests[0][0])
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_old_unscoped_cache_is_never_trusted(self):
        endpoint = '/api/house-control/temperatures?window=24h'
        core.private_json(self.directory / core.cache_key(self.origin, endpoint), {
            'origin': self.origin, 'fetched_at': time.time(), 'data': self.payload('OLD')})
        self.window.fetch()
        self.requests[-1][1](None)
        self.assertEqual(self.installed, [])

    def test_source_change_and_server_identity_mismatch_discard_responses(self):
        self.window.fetch()
        callback = self.requests[-1][1]
        self.source = 'collector-B'
        callback(self.payload('collector-A'))
        self.assertEqual(self.installed, [])
        self.window.fetch()
        self.requests[-1][1](self.payload('collector-A'))
        self.assertEqual(self.installed, [])
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_superseded_request_cannot_overwrite_newer_response(self):
        self.window.fetch()
        first = self.requests[-1][1]
        self.window.fetch()
        second = self.requests[-1][1]
        second(self.payload())
        first(self.payload('OLD'))
        self.assertEqual(len(self.installed), 1)
        self.assertEqual(self.window.payload['collector_source_id'], 'collector-A')

    def test_transport_callback_is_also_bound_to_source_identity(self):
        called = []
        app = SimpleNamespace(origin=self.origin, identity=self.app.identity,
                              web=SimpleNamespace(get_uri=lambda: self.origin),
                              temperature_callbacks={1: (self.origin, 'collector-A', called.append)})
        self.source = 'collector-B'
        value = SimpleNamespace(to_string=lambda: json.dumps({'ticket': 1, 'data': self.payload()}))
        Client.receive_temperatures(app, None, value)
        self.assertEqual(called, [])
        self.assertEqual(app.temperature_callbacks, {})

    def test_offline_period_switch_uses_matching_cached_window(self):
        self.window.fetch()
        self.requests[-1][1](self.payload())
        seven = {**self.payload(), 'window': '7d', 'seconds': 10800,
                 'start': '2026-10-11T00:00:00+00:00'}
        endpoint = '/api/house-control/temperatures?window=7d'
        core.private_json(self.directory / core.temperature_history_cache_key(self.origin, self.source, endpoint),
                          {'origin': self.origin, 'source_id': self.source, 'data': seven})
        self.window.period.get_selected = lambda: 2
        self.window.fetch()
        self.requests[-1][1](None)
        self.assertEqual(self.window.payload['window'], '7d')
        self.assertTrue(self.installed[-1][1])
        self.window.period.get_selected = lambda: 1
        self.window.fetch()
        self.requests[-1][1](None)
        self.assertEqual(self.window.payload['window'], '24h')

    def test_uncached_range_does_not_display_previous_range(self):
        self.window.fetch()
        self.requests[-1][1](self.payload())
        self.window.period.get_selected = lambda: 2
        self.window.fetch()
        self.requests[-1][1](None)
        self.assertIsNone(self.window.payload)
