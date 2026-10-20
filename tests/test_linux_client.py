"""Linux client cache invariants; no desktop, collector, credentials or live data required."""
import ast
import importlib.util
import re
import tempfile
import unittest
from pathlib import Path

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
                self.assertIn('font-variant-numeric: tabular-nums !important', css)
                self.assertIn('.page { padding: 24px 24px 48px !important', css)
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
            self.assertEqual(core.load_ui_state(path), {'width': 900, 'height': 600, 'sidebar': False, 'page': '/reports',
                                                        'density': 'comfortable', 'dashboard_style': 'elementary'})
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


class ChartMathTests(unittest.TestCase):
    def test_ticks_cover_the_data_and_survive_flat_series(self):
        ticks, low, high = core.nice_ticks(0.02, 0.26, 4)
        self.assertLessEqual(low, 0.02)
        self.assertGreaterEqual(high, 0.26)
        self.assertEqual(ticks[0], low)
        flat, low, high = core.nice_ticks(72.0, 72.0, 4)
        self.assertLess(low, 72.0)
        self.assertGreater(high, 72.0)
        self.assertGreater(len(flat), 1)
        self.assertEqual(core.nice_ticks(5, 1, 4)[0][0] <= 1, True)
        with self.assertRaises(ValueError):
            core.nice_ticks(float('nan'), 1)

    def test_gaps_split_runs_and_are_never_zero(self):
        self.assertEqual(core.value_runs([1, None, 2, 3, float('nan'), 4]),
                         [[(0, 1)], [(2, 2), (3, 3)], [(5, 4)]])
        self.assertEqual(core.value_runs([None, None]), [])
        self.assertEqual(core.value_runs([True, 0]), [[(1, 0)]])  # a bool is not a reading; zero is

    def test_pointer_maps_to_nearest_bucket_and_clamps(self):
        self.assertEqual(core.index_at(48, 48, 100, 11), 0)
        self.assertEqual(core.index_at(148, 48, 100, 11), 10)
        self.assertEqual(core.index_at(98, 48, 100, 11), 5)
        self.assertEqual(core.index_at(-50, 48, 100, 11), 0)
        self.assertEqual(core.index_at(900, 48, 100, 11), 10)
        self.assertIsNone(core.index_at(10, 48, 0, 11))
        self.assertIsNone(core.index_at(10, 48, 100, 0))
        self.assertEqual(core.index_at(10, 48, 100, 1), 0)

    def test_both_windows_use_the_shared_chart_and_it_is_installed(self):
        root = Path(__file__).parents[1] / 'linux'
        for name in ('energy_monitor.py', 'temperatures.py'):
            self.assertIn('Chart(', (root / name).read_text())
        self.assertIn('chart.py', (root / 'install.sh').read_text())


class DropdownWidthTests(unittest.TestCase):
    def test_dropdown_width_does_not_depend_on_the_view(self):
        vala = (Path(__file__).parents[1] / 'linux/wingpanel/Indicator.vala').read_text()
        self.assertIn('MENU_WIDTH = 440', vala)
        self.assertIn('MENU_WIDTH_COMPACT = 340', vala)
        self.assertNotIn('showing_temperatures ? int.min (660', vala)


class PanelLayoutTests(unittest.TestCase):
    def test_panel_view_is_the_physical_layout(self):
        root = Path(__file__).parents[1] / 'linux'
        vala = (root / 'wingpanel/Indicator.vala').read_text()
        css = (root / 'theme.css').read_text()
        self.assertIn('slot % 2 == 1', vala)            # odd slots on the left, even on the right
        self.assertIn('"%d–%d".printf (row * 2 + 1, row * 2 + 2)', vala)  # bus labels 1–2, 3–4 …
        self.assertIn('span = 2', vala)                 # a double-pole breaker spans two rows
        self.assertIn('circuits_panel', (root / 'wingpanel/TemperatureView.vala').read_text())
        for name in ('energy-panel', 'energy-bus', 'energy-breaker', 'energy-handle'):
            self.assertIn(name, css)


class SidebarFooterTests(unittest.TestCase):
    def test_footer_shows_live_power_only_when_live(self):
        self.assertEqual(core.sidebar_footer({'online': True, 'current_watts': 412.4, 'cost_per_hour': .0453}),
                         ('412 W', 'Live now · $0.05/h', True))
        self.assertEqual(core.sidebar_footer({'online': True, 'current_watts': 0})[0], '0 W')
        for state in ({'online': False, 'current_watts': 412}, {'online': True, 'current_watts': None},
                      {'online': True, 'current_watts': float('nan')}, {'online': True, 'current_watts': True}, {}):
            headline, caption, live = core.sidebar_footer(state)
            self.assertEqual((headline, live), ('Offline', False))
        # The collector sends ISO 8601 with microseconds; the old [-8:-3] slice showed "1.880" for this.
        self.assertEqual(core.sidebar_footer({'last_reading': '2026-10-20T06:21:11.880900'})[1], 'Last recorded 06:21')
        self.assertEqual(core.sidebar_footer({'last_reading': '2026-10-19 03:59:00'})[1], 'Last recorded 03:59')
        self.assertEqual(core.sidebar_footer({'last_reading': 'yesterday'})[1], 'No recorded reading')
        self.assertEqual(core.sidebar_footer({'last_reading': None})[1], 'No recorded reading')
        self.assertEqual(core.sidebar_footer({})[1], 'No recorded reading')

    def test_reading_clock_honours_offsets_and_rejects_garbage(self):
        self.assertEqual(core.reading_clock('2026-10-20T06:21:11.880900'), '06:21')
        self.assertEqual(core.reading_clock(' 2026-10-19 03:59:00 '), '03:59')
        aware = core.reading_clock('2026-10-20T10:21:11+00:00')
        self.assertRegex(aware, r'^\d\d:\d\d$')
        self.assertEqual(aware, __import__('datetime').datetime.fromisoformat('2026-10-20T10:21:11+00:00').astimezone().strftime('%H:%M'))
        for value in ('', '1.880', 'not a time', 12, None, '2026-13-45T99:99:99'):
            self.assertEqual(core.reading_clock(value), '')


class ShortcutsWindowTests(unittest.TestCase):
    def test_help_window_lists_every_registered_accelerator(self):
        import xml.etree.ElementTree as ET
        root = Path(__file__).parents[1] / 'linux'
        source = (root / 'energy_monitor.py').read_text()
        tree = ast.parse(source)
        func = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'shortcuts_xml')
        namespace = {}
        exec(compile(ast.Module([func], []), 'shortcuts_xml', 'exec'), namespace)
        xml = namespace['shortcuts_xml']([('Dashboard', '/'), ('Reports', '/reports')])
        document = ET.fromstring(xml)
        accelerators = {e.text for e in document.iter('property') if e.get('name') == 'accelerator'}
        for accelerator in ('<Control>1', '<Control>2', '<Control>k', 'F9', '<Control>r F5', 'F1', '<Control>q', '<Control>question'):
            self.assertIn(accelerator, accelerators)
        for registered in ('<Control>k', '<Control>question', 'F1', '<Control>q'):
            self.assertIn(registered, source)


class VisualPreferenceTests(unittest.TestCase):
    def test_preferences_are_closed_sets_and_default_to_comfortable_native(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ui.json'
            state = core.load_ui_state(path)
            self.assertEqual((state['density'], state['dashboard_style']), ('comfortable', 'elementary'))
            core.private_json(path, {'density': 'compact', 'dashboard_style': 'original'})
            state = core.load_ui_state(path)
            self.assertEqual((state['density'], state['dashboard_style']), ('compact', 'original'))
            core.private_json(path, {'density': 'x; } body { display:none', 'dashboard_style': ['elementary']})
            state = core.load_ui_state(path)
            self.assertEqual((state['density'], state['dashboard_style']), ('comfortable', 'elementary'))

    def test_compact_css_is_denser_and_original_keeps_the_collector_design(self):
        roomy = web_theme.build_css(False, '#3689e6')
        dense = web_theme.build_css(False, '#3689e6', 'compact')
        self.assertNotIn('font-size: 12.5px', roomy)
        self.assertIn('font-size: 12.5px !important', dense)
        self.assertEqual(web_theme.build_css(True, '#3689e6', 'compact', 'original').strip(),
                         'nav.topnav { display: none !important; }')

    def test_dropdown_and_app_follow_the_density_setting(self):
        root = Path(__file__).parents[1] / 'linux'
        vala = (root / 'wingpanel/Indicator.vala').read_text()
        self.assertIn('ui.json', vala)
        self.assertIn('energy-compact', vala)
        self.assertIn('compact ? "compact" : "roomy"', vala)  # a density change must defeat the unchanged-data shortcut
        self.assertIn('.energy-compact', (root / 'theme.css').read_text())
        self.assertIn('preferences_dialog', (root / 'energy_monitor.py').read_text())


class SeriesStatsTests(unittest.TestCase):
    def test_stats_ignore_gaps_and_report_coverage(self):
        stats = core.series_stats([0.1, None, 0.3, 0.2, float('nan')])
        self.assertEqual((stats['recorded'], stats['buckets']), (3, 5))
        self.assertEqual((stats['peak'], stats['peak_index'], stats['low'], stats['low_index']), (0.3, 2, 0.1, 0))
        self.assertAlmostEqual(stats['mean'], 0.2)
        self.assertIsNone(core.series_stats([None, None]))
        self.assertIsNone(core.series_stats([]))
        self.assertEqual(core.series_stats([0, 0])['mean'], 0)  # a recorded zero is a reading


class SingleLineCompactTests(unittest.TestCase):
    def test_compact_breakers_and_rows_are_one_line(self):
        vala = (Path(__file__).parents[1] / 'linux/wingpanel/Indicator.vala').read_text()
        self.assertIn('int ROW = compact ? 26 : 46;', vala)
        self.assertIn('compact ? Gtk.Orientation.HORIZONTAL : Gtk.Orientation.VERTICAL', vala)
        self.assertIn('!compact && live && circuit.has_member ("load_percent")', vala)


class InstallCompletenessTests(unittest.TestCase):
    def test_every_local_module_the_app_imports_is_installed(self):
        root = Path(__file__).parents[1] / 'linux'
        modules = {path.stem for path in root.glob('*.py')}
        install = (root / 'install.sh').read_text()
        imported = set()
        for name in ('energy_monitor.py', 'temperatures.py', 'chart.py', 'native_auth.py', 'web_theme.py'):
            for node in ast.walk(ast.parse((root / name).read_text())):
                if isinstance(node, ast.ImportFrom) and node.module in modules:
                    imported.add(node.module)
                elif isinstance(node, ast.Import):
                    imported.update(alias.name for alias in node.names if alias.name in modules)
        self.assertTrue({'client_core', 'chart', 'web_theme', 'native_auth', 'temperatures'} <= imported)
        for module in imported:
            self.assertIn(f'linux/{module}.py', install, module + ' is imported but never installed')
        self.assertIn('linux/theme.css', install)

    def test_panel_install_failure_is_reported_not_fatal(self):
        install = (Path(__file__).parents[1] / 'linux/install.sh').read_text()
        self.assertIn('Panel library NOT installed', install)


class DropdownMinimumWidthTests(unittest.TestCase):
    def test_no_fixed_minimum_width_exceeds_the_compact_popover(self):
        import re
        for path in (Path(__file__).parents[1] / 'linux/wingpanel').glob('*.vala'):
            for match in re.finditer(r'content_width\s*=\s*(\d+)', path.read_text()):
                self.assertLessEqual(int(match.group(1)), 300, path.name + ' would force the 340 px popover wider')


class DensityToggleTests(unittest.TestCase):
    def test_dropdown_writes_density_and_app_watches_it(self):
        root = Path(__file__).parents[1] / 'linux'
        vala = (root / 'wingpanel/Indicator.vala').read_text()
        self.assertIn('Compact layout', vala)
        self.assertIn('set_string_member ("density"', vala)
        self.assertIn('FileUtils.chmod (path, 0600)', vala)
        self.assertIn('syncing_switch', vala)  # the switch following the file must not write it back
        app = (root / 'energy_monitor.py').read_text()
        self.assertIn('monitor_file', app)
        self.assertIn('reload_ui_state', app)


class ElementaryIntegrationTests(unittest.TestCase):
    def test_client_follows_system_colour_scheme_and_ships_desktop_metadata(self):
        root = Path(__file__).parents[1]
        app = (root / 'linux/energy_monitor.py').read_text()
        self.assertIn('org.gnome.desktop.interface', app)
        self.assertIn('lookup("org.gnome.desktop.interface", True)', app)  # a missing schema would abort the process
        install = (root / 'linux/install.sh').read_text()
        for needle in ('StartupNotify=true', 'X-GNOME-UsesNotifications=true', 'Actions=Temperatures;Settings;',
                       'org.techmore.EnergyMonitor.metainfo.xml'):
            self.assertIn(needle, install)
        meta = (root / 'linux/org.techmore.EnergyMonitor.metainfo.xml').read_text()
        self.assertIn('<id>org.techmore.EnergyMonitor</id>', meta)


class WebThemeTableTests(unittest.TestCase):
    def test_bare_tables_are_styled_but_heatmaps_keep_their_plate(self):
        css = web_theme.build_css(True, '#3689e6')
        self.assertIn('table:not([class]) td', css)
        self.assertIn('table:not([class]) th', css)
        self.assertNotIn('table:not([class]) .power-heatmap', css)


class QuitBehaviourTests(unittest.TestCase):
    def test_only_the_service_instance_stops_the_unit(self):
        unit = 'a1b2c3d4e5f60718293a4b5c6d7e8f90'
        self.assertTrue(core.quit_stops_service(unit, unit + '\n'))
        self.assertFalse(core.quit_stops_service(None, unit))          # launched from a terminal or the dock
        self.assertFalse(core.quit_stops_service('', unit))
        self.assertFalse(core.quit_stops_service('0000', unit))        # started by some other systemd unit
        self.assertFalse(core.quit_stops_service(unit, ''))            # the service is not running at all
        self.assertEqual(core.SERVICE_UNIT, 'energy-monitor-client.service')

    def test_quit_is_wired_through_the_service_and_the_indicator_has_no_quit(self):
        root = Path(__file__).parents[1] / 'linux'
        app = (root / 'energy_monitor.py').read_text()
        for needle in ('def quit_app', 'os.environ.get("INVOCATION_ID")', '"systemctl", "--user", "--no-block", "stop", SERVICE_UNIT',
                       '("quit", lambda *_: self.quit_app())'):
            self.assertIn(needle, app)
        self.assertNotIn('("quit", lambda *_: self.quit())', app)
        vala = (root / 'wingpanel/Indicator.vala').read_text()
        self.assertNotIn('Quit', vala)
        self.assertNotIn('--quit', vala)
        self.assertIn('Restart=always', (root / 'energy-monitor-client.service').read_text())


class ServiceUnitTests(unittest.TestCase):
    def test_client_service_orders_after_the_desktop_portals(self):
        unit = (Path(__file__).parents[1] / 'linux/energy-monitor-client.service').read_text()
        keys = {}
        for line in unit.splitlines():
            if '=' in line and not line.startswith(('#', '[')):
                key, value = line.split('=', 1)
                keys[key] = value.split()
        for portal in ('xdg-desktop-portal.service', 'xdg-desktop-portal-gtk.service'):
            self.assertIn(portal, keys['After'])
            self.assertIn(portal, keys['Wants'])
        self.assertIn('graphical-session.target', keys['After'])
        self.assertEqual(keys['PartOf'], ['graphical-session.target'])
        self.assertEqual(keys['WantedBy'], ['graphical-session.target'])
        self.assertEqual(keys['Restart'], ['always'])
        self.assertEqual(keys['UMask'], ['0077'])


class PortalAccentTests(unittest.TestCase):
    def test_portal_triples_become_hex_and_no_preference_is_none(self):
        self.assertEqual(core.portal_accent((0.211765, 0.537255, 0.901961)), core.BLUEBERRY)  # elementary's default
        self.assertEqual(core.portal_accent([1, 0, 0]), '#ff0000')
        self.assertEqual(core.portal_accent((0, 0, 0)), '#000000')
        for value in ((-1.0, -1.0, -1.0), (0.5, 0.5), (2, 0, 0), ('a', 'b', 'c'), None, 7, (float('nan'), 0, 0), (True, 0, 0)):
            self.assertIsNone(core.portal_accent(value), value)
        self.assertEqual(web_theme.build_css(False, core.portal_accent((0.211765, 0.537255, 0.901961))),
                         web_theme.build_css(False, None))  # the fallback and the portal's Blueberry agree

    def test_app_reads_the_settings_portal_instead_of_the_deprecated_lookup(self):
        app = (Path(__file__).parents[1] / 'linux/energy_monitor.py').read_text()
        self.assertNotIn('lookup_color', app)
        for needle in ('"org.freedesktop.portal.Settings"', '"org.freedesktop.appearance", "accent-color"', '"ReadOne"',
                       '"SettingChanged"', 'PORTAL_TIMEOUT_MS = 1000', 'Gio.DBusProxy.new_for_bus(', 'web_css(dark, self.accent'):
            self.assertIn(needle, app)


class OfflineSensorPickerTests(unittest.TestCase):
    def test_cached_sensor_list_feeds_the_picker(self):
        sensors = [{'source': 'aqara_local', 'sensor_id': 'a', 'name': 'Attic'},
                   {'source': 'aqara_local', 'sensor_id': 'a', 'name': 'Attic again'},   # duplicate key
                   {'source': 'aqara_local', 'sensor_id': '', 'name': 'Nameless id'},
                   {'source': None, 'sensor_id': 'b', 'name': 'No source'},
                   'garbage', {'source': 'ecosense', 'sensor_id': 'r', 'name': 'Radon'}]
        self.assertEqual(core.sensor_choices(sensors), [(('aqara_local', 'a'), 'Attic'), (('ecosense', 'r'), 'Radon')])
        self.assertEqual(core.sensor_choices(None), [])
        source = (Path(__file__).parents[1] / 'linux/temperatures.py').read_text()
        self.assertIn('def offer_current_sensors', source)
        self.assertIn('sensor_choices(sensors)', source)


class UnnamedCircuitTests(unittest.TestCase):
    def test_unnamed_circuits_read_as_dimmed_slots_in_both_views(self):
        root = Path(__file__).parents[1] / 'linux/wingpanel'
        labels = (root / 'Labels.vala').read_text()
        self.assertIn('"Slot %d".printf (slot)', labels)
        self.assertIn('has_prefix ("Circuit_")', labels)
        vala = (root / 'Indicator.vala').read_text()
        self.assertEqual(vala.count('EnergyLabels.circuit_title ('), 2)  # breaker_cell and the list row
        self.assertIn('if (state != "monitored" || unnamed) { name_label.add_css_class (Granite.CssClass.DIM); }', vala)
        self.assertIn('if (EnergyLabels.is_unnamed (string_member (circuit, "display_name", channel))) name_label.add_css_class (Granite.CssClass.DIM);', vala)
        meson = (root / 'meson.build').read_text()
        self.assertIn("'Labels.vala'", meson)
        self.assertIn("test('labels'", meson)
        self.assertIn('EnergyLabels.circuit_title ("Circuit_14", 17) == "Slot 17"', (root / 'test_labels.vala').read_text())


class DropdownStatusTests(unittest.TestCase):
    def test_offline_status_formats_the_stamp_with_a_relative_age(self):
        vala = (Path(__file__).parents[1] / 'linux/wingpanel/Indicator.vala').read_text()
        self.assertNotIn('stamp.substring (separator + 1, 5)', vala)
        self.assertIn('EnergyLabels.reading_clock (stamp)', vala)
        self.assertIn('"Last reading " + clock', vala)
        self.assertIn('+ age_text;', vala)  # the age must be able to refresh the line when summary.json has not changed
        self.assertIn('reading_clock ("2026-10-20T06:21:11.880900") == "06:21"',
                      (Path(__file__).parents[1] / 'linux/wingpanel/test_labels.vala').read_text())
