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

    def test_channel_autoplacement_preserves_reserved_unmonitored_breakers(self):
        for attributes in ({'label': 'Unmonitored oven'}, {'note': 'Physical breaker'},
                           {'amps': 30}, {'poles': 2}):
            reserved = {'slot': 1, 'channel_name': None, **attributes}
            layout = {1: reserved, 2: {'slot': 2, 'channel_name': None}}
            seeded = web._seed_layout_from_latest(layout, [
                {'channel_name': 'New sensor', 'channel_num': 1}], ['New sensor'])
            self.assertEqual(seeded[1], reserved)
            normalized, size = web._normalize_panel_layout(seeded, ['New sensor'], 40)
            self.assertEqual(normalized[1], reserved)
            self.assertEqual(normalized[2]['channel_name'], 'New sensor')
            self.assertEqual(size, 40)
            self.assertEqual(layout[1], reserved)

    def test_full_panel_distinguishes_unmonitored_empty_and_no_measurement(self):
        energy.save_panel_layout([{'slot': 1, 'channel_name': None,
                                  'label': 'Unmonitored oven', 'amps': 30, 'poles': 2}])
        with patch.object(web, '_load_panel_slots', return_value=40):
            response = self.client.get('/api/menu-summary')
            self.assertEqual(response.status_code, 200)
            slots = response.get_json()['breaker_slots']
            self.assertEqual(len(slots), 40)
            self.assertEqual({row['slot'] for row in slots}, set(range(1, 41)))
            self.assertEqual(slots[0]['slot_state'], 'unmonitored')
            self.assertIsNone(slots[0]['watts'])
            self.assertIsNone(slots[0]['load_percent'])
            self.assertEqual(slots[1]['slot_state'], 'empty')
            page = self.client.get('/circuits').get_data(as_text=True)
            self.assertIn('Unmonitored oven', page)
            self.assertIn('Unmonitored', page)
            self.assertIn('2P/30A', page)

    def test_editor_can_expand_small_panel_to_forty_slots(self):
        with patch.object(web, '_load_panel_slots', return_value=16):
            html = self.client.get('/panel').get_data(as_text=True)
        self.assertEqual(html.count('class="slot-row" data-slot='), 40)
        self.assertIn('data-slot="40" style="display:none"', html)
        self.assertIn('parseInt(row.dataset.slot) <= panelSize', html)
