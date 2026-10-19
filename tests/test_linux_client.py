"""Linux client cache invariants; no desktop, collector, credentials or live data required."""
import ast
import re
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('linux_client_core', Path(__file__).parents[1] / 'linux/client_core.py')
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)

web_spec = importlib.util.spec_from_file_location('linux_web_theme', Path(__file__).parents[1] / 'linux/web_theme.py')
web_theme = importlib.util.module_from_spec(web_spec)
web_spec.loader.exec_module(web_theme)


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


class TemperatureTrendCacheTests(unittest.TestCase):
    def record(self):
        return {'origin': 'https://example.org', 'source_id': 'collector-A', 'fetched_at': 100,
                'data': {'window': '24h', 'seconds': 1800,
                         'start': '2026-10-17T00:00:00+00:00', 'end': '2026-10-18T00:00:00+00:00',
                         'times': ['2026-10-18T00:00:00+00:00'],
                         'series': [{'source': 'aqara_local', 'sensor_id': 'a',
                                     'name': 'Barn Master', 'values': [60.7]}]}}

    def test_offline_migration_survives_interactive_window_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            record = self.record()
            legacy = Path(directory) / core.cache_key(record['origin'], '/api/house-control/temperatures?window=24h')
            dedicated = Path(directory) / core.cache_key(record['origin'], core.TEMPERATURE_TREND_CACHE_KEY)
            core.private_json(legacy, record)
            self.assertEqual(core.load_temperature_trend_cache(directory, record['origin'], 'collector-A'), record)
            self.assertEqual(core.read_json(dedicated), record)
            window_record = dict(record)
            window_record.pop('source_id')
            core.private_json(legacy, window_record)
            self.assertEqual(core.load_temperature_trend_cache(directory, record['origin'], 'collector-A'), record)
            self.assertEqual(dedicated.stat().st_mode & 0o777, 0o600)

    def test_unscoped_wrong_source_and_wrong_origin_are_never_restored(self):
        with tempfile.TemporaryDirectory() as directory:
            original = self.record()
            path = Path(directory) / core.cache_key(original['origin'], '/api/house-control/temperatures?window=24h')
            for update in ({'source_id': None}, {'source_id': 'collector-B'},
                           {'origin': 'https://other.example'}, {'fetched_at': 'invalid'},
                           {'data': {}}, {'data': {**original['data'], 'window': '4h'}}):
                core.private_json(path, {**original, **update})
                self.assertIsNone(core.load_temperature_trend_cache(directory, original['origin'], 'collector-A'))
            path.write_text('broken JSON')
            self.assertIsNone(core.load_temperature_trend_cache(directory, original['origin'], 'collector-A'))
            core.private_json(path, original)
            self.assertIsNone(core.load_temperature_trend_cache(directory, original['origin'], None))

    def test_web_theme_follows_system_palette_and_accent(self):
        light, dark = web_theme.build_css(False, '#3689e6'), web_theme.build_css(True, '#3689e6')
        self.assertIn('color-scheme: light', light)
        self.assertIn('color-scheme: dark', dark)
        self.assertIn('--accent:', light)
        self.assertIn('nav.topnav { display: none !important; }', light)
        self.assertNotEqual(light, web_theme.build_css(False, '#ed5353'))

    def test_web_theme_text_reaches_aa_for_every_accent(self):
        accents = ['#ed5353', '#ffa154', '#f9c440', '#9bdb4d', '#28bca3', '#3689e6', '#a56de2',
                   '#de3e80', '#715344', '#667885', '#cba88a']
        for dark in (False, True):
            for accent in accents:
                p = web_theme.palette(dark, web_theme.parse_accent(accent))
                surfaces = [p['bg'], p['surface'], p['surface2']]
                for name in ('accent-text', 'green', 'red', 'amber'):
                    for surface in surfaces:
                        self.assertGreaterEqual(web_theme.contrast(p[name], surface), 4.5, (dark, accent, name))
                self.assertGreaterEqual(web_theme.contrast(p['button'], web_theme.WHITE), 4.5, (accent, 'button'))
                css = web_theme.build_css(dark, accent)
                self.assertIn('.now-panel', css)
                self.assertIn('prefers-reduced-motion', css)
        self.assertEqual(web_theme.parse_accent('garbage'), (0x36, 0x89, 0xe6))


class NativeNavigationTests(unittest.TestCase):
    def test_sidebar_preserves_internal_header_destinations(self):
        root = Path(__file__).parents[1]
        tree = ast.parse((root / 'linux/energy_monitor.py').read_text())
        navigation = next(ast.literal_eval(node.value) for node in tree.body
                          if isinstance(node, ast.Assign)
                          and any(isinstance(target, ast.Name) and target.id == 'NAVIGATION'
                                  for target in node.targets))
        web_tree = ast.parse((root / 'web.py').read_text())
        header = next(ast.literal_eval(node.value) for node in web_tree.body
                      if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == 'NAV_HTML'
                              for target in node.targets))
        destinations = set(re.findall(r'href="(/[^"{]*)"', header))
        paths = {path for _, items in navigation for _, path, _ in items}
        self.assertLessEqual(destinations, paths)


class DropdownStyleTests(unittest.TestCase):
    def test_every_dropdown_css_class_is_styled_and_state_is_not_colour_only(self):
        root = Path(__file__).parents[1]
        vala = (root / 'linux/wingpanel/Indicator.vala').read_text()
        css = (root / 'linux/theme.css').read_text()
        for name in ('energy-menu-title', 'energy-notice', 'energy-hero-stale'):
            self.assertIn(name, vala)
            self.assertIn(name, css)
        # The live dot gets an accessible label and a visible text notice when readings are stale.
        self.assertIn('AccessibleProperty.LABEL, live ? "Collector live"', vala)
        self.assertIn('notice.visible = !live', vala)
        self.assertIn('boxed-list', vala)
        self.assertNotIn('row.activate ()', vala)  # would recurse through row-activated


class ShellStateTests(unittest.TestCase):
    def test_ui_state_restores_and_rejects_untrusted_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ui.json'
            self.assertEqual(core.load_ui_state(path)['page'], '/')
            core.private_json(path, {'width': 900, 'height': 600, 'sidebar': False, 'page': '/reports'})
            self.assertEqual(core.load_ui_state(path), {'width': 900, 'height': 600, 'sidebar': False, 'page': '/reports'})
            for page in ('https://evil.example/', '//evil.example', '/a@b', 'reports', '/x' * 200):
                core.private_json(path, {'page': page, 'width': 10 ** 9, 'height': True, 'sidebar': 'yes'})
                state = core.load_ui_state(path)
                self.assertEqual(state['page'], '/')
                self.assertEqual((state['width'], state['height'], state['sidebar']), (1240, 800, True))
            path.write_text('not json')
            self.assertEqual(core.load_ui_state(path)['width'], 1240)

    def test_outage_notifier_is_quiet_and_rate_limited(self):
        n = core.OutageNotifier(grace=90, cooldown=1800, startup=60)
        self.assertIsNone(n.update(False, 0))        # startup: nothing
        self.assertIsNone(n.update(False, 80))       # not long enough
        self.assertEqual(n.update(False, 100), 'offline')
        self.assertIsNone(n.update(False, 200))      # announced once
        self.assertEqual(n.update(True, 300), 'recovered')
        self.assertIsNone(n.update(True, 400))
        self.assertIsNone(n.update(False, 500))
        self.assertIsNone(n.update(False, 700))      # cooldown (1800 s) not over
        self.assertEqual(n.update(True, 710) , None)  # no notice was sent, so no recovery either
        self.assertIsNone(n.update(False, 1000))
        self.assertEqual(n.update(False, 2000), 'offline')


class ShellWiringTests(unittest.TestCase):
    def test_shell_features_are_wired(self):
        source = (Path(__file__).parents[1] / 'linux/energy_monitor.py').read_text()
        for needle in ('<Control>k', 'app.about', 'load_ui_state(UI_STATE)', 'set_background_color',
                       'add_tick_callback(self.responsive_sidebar)', 'OutageNotifier()', 'send_notification("collector-offline"'):
            self.assertIn(needle, source)
        ast.parse(source)
