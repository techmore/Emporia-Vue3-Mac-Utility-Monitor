import re
import tempfile
import time
import unittest
from pathlib import Path
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import energy
import web


class UnknownPowerDisplayTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        database = patch.object(energy, 'DB_PATH', str(self.root / 'data.db'))
        database.start()
        self.addCleanup(database.stop)
        energy.ensure_table()
        self.addCleanup(setattr, energy, 'RATE_CENTS', energy.RATE_CENTS)
        self.addCleanup(web._dashboard_cache.update, {'common': None, 'context': None})
        vue = SimpleNamespace(get_device_list_usage=lambda **kwargs: {'QA': SimpleNamespace(channels={
            1: SimpleNamespace(name='Main', usage=.02), 2: SimpleNamespace(name='Idle', usage=0)})})
        with patch.dict(energy.os.environ, {'EMPORIA_MAINS_HISTORY': '0'}), \
                patch.object(energy, '_last_compaction', time.monotonic()):
            energy.poll_and_store(vue, ['QA'])
        csv = self.root / 'QA-Panel-1H.csv'
        csv.write_text('Time Bucket (UTC),QA-Pump (kWhs)\n'+
                       datetime.now(timezone.utc).strftime('%m/%d/%Y %H:%M:%S')+',1\n')
        energy.import_emporia_csv(str(csv), device_gid='QA')
        energy.save_panel_layout([{'slot': 1, 'channel_name': 'Pump', 'amps': 20},
                                  {'slot': 2, 'channel_name': 'Idle', 'amps': 20}])

    def context(self):
        with web.app.test_request_context('/'):
            return web._build_dashboard_context('QA', 'QA')

    def test_dashboard_panel_and_live_payload_preserve_null_and_zero(self):
        context = self.context()
        breakers = {row['channel_name']: row for row in context['dash_breakers'] if row['channel_name']}
        self.assertIsNone(breakers['Pump']['watts'])
        self.assertEqual(breakers['Pump']['load_label'], 'No current reading')
        self.assertEqual(breakers['Pump']['safe_cls'], '')
        self.assertEqual(breakers['Idle']['watts'], 0)
        for name, value in (('Pump', '—'), ('Idle', '0')):
            self.assertRegex(context['panel_fragment'],
                rf'<div class="breaker-name">{name}</div>\s*<div class="breaker-watts">\s*{value} W')
        with patch.object(web, '_get_cached_dashboard', return_value=({}, context)):
            payload = web._build_live_dashboard_payload()
        rows = {row['channel_name']: row for row in payload['top_circuits']}
        self.assertIsNone(rows['Pump']['watts'])
        self.assertEqual(rows['Idle']['watts'], 0)
        self.assertEqual(payload['current_watts'], 1200)
        self.assertIn("Number.isFinite(circuit.watts)", web.DASH_HTML)

    def test_all_web_views_render_unknown_power_without_errors(self):
        web._dashboard_cache.update(common=None, context=None)
        client = web.app.test_client()
        for path in ('/', '/circuits', '/reports', '/trends'):
            with self.subTest(path=path):
                response = client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertNotIn('None W', response.get_data(as_text=True))

    def test_unknown_main_withholds_cost_and_power_while_zero_main_is_known(self):
        conn = energy._connect()
        with conn:
            conn.execute("DELETE FROM reading_provenance WHERE reading_id IN (SELECT id FROM readings WHERE channel_name='Main')")
        conn.close()
        context = self.context()
        self.assertIsNone(context['current_watts'])
        self.assertIsNone(context['cost_per_hour'])
        with patch.object(web, '_get_cached_dashboard', return_value=({}, context)):
            payload = web._build_live_dashboard_payload()
        self.assertIsNone(payload['current_watts'])
        self.assertIsNone(payload['cost_per_hour'])
        zero = {'usage_kwh': 0, 'interval_seconds': 60, 'observation_source': 'live_poll',
                'timestamp': datetime.now().isoformat()}
        self.assertEqual(web._watts_estimate(zero), 0)
        stale = {**zero, 'timestamp': '2020-01-01T00:00:00'}
        self.assertIsNone(web._watts_estimate(stale))
