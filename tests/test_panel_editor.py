import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import energy
import web
from panel_model import breaker_load


class PanelEditorTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db = patch.object(energy, 'DB_PATH', str(Path(self.directory.name) / 'panel.db'))
        self.db.start()
        energy.ensure_table()
        self.client = web.app.test_client()

    def tearDown(self):
        self.db.stop()
        self.directory.cleanup()

    def test_poles_and_amps_persist_and_change_estimated_load(self):
        slot = {'slot': 1, 'channel_name': 'QA', 'label': 'QA only', 'amps': 15, 'poles': 1}
        for amps, poles, expected in ((15, 1, '10.0/15A'), (15, 2, '5.0/15A'),
                                      (30, 2, '5.0/30A'), (None, 1, 'Rating not set')):
            response = self.client.post('/api/panel-layout', json={
                'slots': [{**slot, 'amps': amps, 'poles': poles}]})
            self.assertEqual(response.status_code, 200)
            saved = energy.get_panel_layout()[0]
            self.assertEqual((saved['amps'], saved['poles']), (amps, poles))
            self.assertEqual(breaker_load(1200, amps, poles)['load_label'], expected)
        self.assertGreater(breaker_load(1200, 15, 1)['safe_bar_pct'],
                           breaker_load(1200, 15, 2)['safe_bar_pct'])

    def test_invalid_bodies_and_batches_preserve_existing_layout(self):
        original = {'slot': 1, 'channel_name': 'QA', 'amps': 20, 'poles': 2}
        energy.save_panel_layout([original])
        before = energy.get_panel_layout()
        for body in ([], None, 'bad', {'slots': 'bad'},
                     {'slots': [original, {**original, 'slot': 2, 'amps': True}]},
                     {'slots': [original, original]},
                     {'slots': [{**original, 'poles': 3}]}):
            response = self.client.post('/api/panel-layout', data=json.dumps(body),
                                        content_type='application/json')
            self.assertEqual(response.status_code, 400, body)
            self.assertEqual(energy.get_panel_layout(), before)
