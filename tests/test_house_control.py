import copy
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import energy
import house_control
import web


class HouseControlTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = patch.object(energy, 'DB_PATH', str(Path(self.directory.name) / 'energy.db'))
        self.database.start()
        energy.ensure_table()
        connection = energy._connect()
        with connection:
            connection.executemany(
                'INSERT INTO readings(timestamp,device_gid,channel_name,usage_kwh,cost_cents) '
                'VALUES (?,?,?,?,?)',
                [(datetime.now().isoformat(), 'A', 'Kitchen', 1.5, 30),
                 (datetime.now().isoformat(), 'B', 'Kitchen', 90, 1800)])
        connection.close()
        self.client = web.app.test_client()

    def tearDown(self):
        self.database.stop()
        self.directory.cleanup()

    def payload(self, bindings=None):
        config = house_control.load_config()
        return {'plan': copy.deepcopy(config['plan']), 'bindings': bindings or [],
                'revision': config['revision']}

    def binding(self, channel='Kitchen', room='kitchen', gid='A'):
        return {'device_gid': gid, 'channel_name': channel, 'room_id': room}

    def menu(self):
        return {'active_device_gid': 'A', 'online': True, 'current_watts': 100,
                'last_reading': datetime.now().isoformat(), 'breaker_slots': [
                    {'channel_name': 'Kitchen', 'display_name': 'Kitchen outlets', 'watts': 0},
                    {'channel_name': 'Kitchen', 'display_name': 'Kitchen outlets', 'watts': 0},
                    {'channel_name': None, 'display_name': 'Empty', 'watts': None}]}

    def test_default_is_example_and_does_not_create_runtime_configuration(self):
        config = self.client.get('/api/house-control/config')
        self.assertTrue(config.json['example'])
        self.assertEqual(config.json['bindings'], [])
        self.assertFalse(house_control._path().exists())
        page = self.client.get('/home-3d')
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'/static/house-control/home-3d.js', page.data)

    def test_mapping_persists_privately_and_rejects_stale_tab_revision(self):
        payload = self.payload([self.binding()])
        response = self.client.put('/api/house-control/config', json=payload,
                                   headers={'Origin': 'http://localhost'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(house_control._path().stat().st_mode & 0o777, 0o600)
        self.assertEqual(house_control.load_config()['bindings'], [self.binding()])
        self.assertTrue(house_control.load_config()['example'])
        self.assertEqual(self.client.put('/api/house-control/config', json=payload,
                                        headers={'Origin': 'http://localhost'}).status_code, 409)
        self.assertNotEqual(response.json['revision'], payload['revision'])

    def test_unknown_duplicate_main_and_cross_panel_circuits_are_rejected(self):
        for bindings in ([self.binding('Unknown')], [self.binding(gid='missing')],
                         [self.binding('Main')], [self.binding(room='missing')],
                         [self.binding(), self.binding(room='living')]):
            with self.subTest(bindings=bindings):
                with self.assertRaises(ValueError):
                    house_control.save_config(self.payload(bindings))
        self.assertFalse(house_control._path().exists())

    def test_plan_import_omits_hardware_links_and_scene_secrets(self):
        raw = copy.deepcopy(house_control.DEFAULT_PLAN)
        raw['lights'] = [{'tuya': {'deviceId': 'private'}}]
        raw['scenes'] = [{'secret': 'private'}]
        raw['client_secret'] = 'private'
        result = house_control.validate_plan(raw)
        self.assertNotIn('private', json.dumps(result))
        self.assertEqual(len(result['rooms']), 4)

    def test_malformed_or_unbounded_geometry_never_saves(self):
        changes = [('rooms', []), ('wallHeight', float('nan')),
                   ('wallHeight', True), ('wallHeight', 10 ** 1000), ('camera', []),
                   ('furniture', [{'type': 'rug', 'pos': [0, 0], 'size': [1]}]),
                   ('openings', [{'from': [0, 0], 'to': [1, 1]}])]
        for key, value in changes:
            payload = self.payload()
            payload['plan'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                house_control.save_config(payload)
        payload = self.payload()
        payload['plan']['rooms'][1]['id'] = 'living'
        with self.assertRaises(ValueError):
            house_control.save_config(payload)
        self.assertFalse(house_control._path().exists())

    def test_mutations_require_explicit_same_origin_and_bounded_json(self):
        payload = self.payload()
        for headers in ({}, {'Origin': 'https://attacker.example'}):
            self.assertEqual(self.client.put('/api/house-control/config', json=payload,
                                            headers=headers).status_code, 403)
        response = self.client.put('/api/house-control/config', json={'plan': 'x' * (260 * 1024)},
                                   headers={'Origin': 'http://localhost'})
        self.assertEqual(response.status_code, 413)
        self.assertFalse(house_control._path().exists())

    def test_state_scopes_history_to_panel_and_preserves_zero_without_double_counting(self):
        house_control.save_config(self.payload([self.binding()]))
        with patch.object(web, '_build_menu_summary_payload', return_value=self.menu()):
            response = self.client.get('/api/house-control/state')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        room = next(r for r in response.json['rooms'] if r['id'] == 'kitchen')
        self.assertEqual(room['watts'], 0)
        self.assertEqual(room['kwh_24h'], 1.5)
        self.assertEqual(room['cost_24h'], 0.30)
        self.assertEqual(len(response.json['circuits']), 1)
        self.assertEqual(room['mapped_circuits'], 1)

    def test_missing_or_stale_circuits_do_not_look_like_zero_or_partial_room_totals(self):
        config = house_control.load_config()
        config['bindings'] = [self.binding(), self.binding('Missing')]
        menu = self.menu()
        summary = [{'channel_name': 'Kitchen', 'total_kwh': 1, 'total_cents': 20}]
        result = house_control.project_state(config, menu, summary)
        room = next(r for r in result['rooms'] if r['id'] == 'kitchen')
        self.assertIsNone(room['watts'])
        self.assertIsNone(room['kwh_24h'])
        self.assertIsNone(room['cost_24h'])
        self.assertEqual(room['mapped_circuits'], 2)
        missing = next(r for r in result['circuits'] if r['channel_name'] == 'Missing')
        self.assertTrue(missing['missing'])
        self.assertEqual(missing['room_id'], 'kitchen')
        config['bindings'] = [self.binding()]
        menu['online'] = False
        result = house_control.project_state(config, menu, summary)
        room = next(r for r in result['rooms'] if r['id'] == 'kitchen')
        self.assertIsNone(room['watts'])
        self.assertEqual(room['kwh_24h'], 1)
        self.assertIsNone(result['current_watts'])

    def test_other_panel_mapping_never_attaches_by_channel_name_alone(self):
        config = house_control.load_config()
        config['bindings'] = [self.binding(gid='B')]
        result = house_control.project_state(config, self.menu(), [])
        self.assertEqual(result['other_panel_bindings'], 1)
        self.assertTrue(all(r['mapped_circuits'] == 0 for r in result['rooms']))
