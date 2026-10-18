import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
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

    def test_native_preview_projects_saved_geometry_without_equipment_or_bindings(self):
        config = {'plan': copy.deepcopy(house_control.DEFAULT_PLAN), 'bindings': [self.binding()],
                  'sensors': [{'source': 'private-device', 'sensor_id': 'private-id'}]}
        config['plan']['private_equipment'] = 'do not project'
        config['plan']['furniture'] = [{'type': 'sofa', 'pos': [2, 2], 'width': 2.5,
                                     'rot': 90, 'device_secret': 'do not project'}]
        config['plan']['openings'] = [{'type': 'window', 'from': [0, 1], 'to': [0, 2],
                                    'device_secret': 'do not project'}]
        preview = house_control.house_preview(config)
        self.assertEqual(preview['rooms'][0]['rect'], config['plan']['rooms'][0]['rect'])
        self.assertEqual(preview['levels'], [{'id': 'first', 'name': 'First floor', 'elevation': 0}])
        self.assertNotIn('bindings', preview)
        self.assertNotIn('sensors', preview)
        self.assertNotIn('private_equipment', preview)
        self.assertEqual(preview['wallHeight'], config['plan']['wallHeight'])
        self.assertEqual(preview['interiorWallHeight'], config['plan']['interiorWallHeight'])
        self.assertEqual(preview['camera'], config['plan']['camera'])
        self.assertEqual(preview['rooms'][0]['floor'], config['plan']['rooms'][0]['floor'])
        self.assertEqual(preview['furniture'], [{'type': 'sofa', 'pos': [2, 2],
                                              'width': 2.5, 'rot': 90}])
        self.assertEqual(preview['openings'], [{'type': 'window', 'from': [0, 1], 'to': [0, 2]}])
        config['plan']['levels'] = [{'id': 'upper', 'name': 'Upstairs', 'elevation': 3}]
        config['plan']['rooms'][0]['level'] = 'upper'
        config['plan']['accesses'] = [{'id': 'hatch', 'level': 'upper', 'toLevel': 'attic',
                                     'rect': [1, 2, 1, 1]}]
        projected = house_control.house_preview(config)
        self.assertEqual(projected['rooms'][0]['level'], 'upper')
        self.assertEqual(projected['accesses'], config['plan']['accesses'])
        config['sensors'] = []
        with patch.object(house_control, 'load_config', return_value=config):
            summary = self.client.get('/api/menu-summary')
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.json['house_preview'], projected)

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

    def test_attic_hatch_and_pole_mount_survive_private_save(self):
        self.seed_sensors()
        payload = self.payload()
        payload['plan']['levels'] = [{'id': 'first', 'name': 'First floor', 'elevation': 0},
                                     {'id': 'attic', 'name': 'Attic', 'elevation': 5.8}]
        payload['plan']['rooms'][0]['level'] = 'attic'
        payload['plan']['accesses'] = [{'id': 'hatch', 'name': 'Hallway access', 'level': 'first',
                                       'toLevel': 'attic', 'rect': [1, 1, .75, .75]}]
        placement = {**self.placement(), 'height': .1, 'mount': 'pole-base'}
        payload['sensors'] = [placement]
        saved = house_control.save_config(payload)
        self.assertEqual(saved['sensors'], [placement])
        self.assertEqual(saved['plan']['accesses'], payload['plan']['accesses'])

    def test_temperature_history_preserves_gaps_zero_and_sensor_sources(self):
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        rows = [{'source': source, 'sensor_id': 'one', 'name': source, 'stale': False,
                 'metrics': {'temperature_f': 32}, 'placement': None}
                for source in ('aqara_local', 'climate:manual')]
        conn = energy._connect()
        with conn:
            for age, temperature, online in ((10, 0, 1), (20, 30, 0)):
                conn.execute('INSERT INTO aqara_local_observations '
                    '(device_id,timestamp,name,temperature,online,source) VALUES(?,?,?,?,?,?)',
                    ('one', (now-timedelta(minutes=age)).isoformat(), 'Aqara', temperature, online, 'matter_snapshot'))
        conn.close()
        with patch.object(house_control, 'sensor_catalog', return_value=rows):
            response = self.client.get('/api/house-control/temperatures',
                query_string={'window': '4h', 'end': now.isoformat()})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        data = response.json
        self.assertLessEqual(len(data['times']), 121)
        local, manual = data['series']
        self.assertEqual([value for value in local['values'] if value is not None], [32])
        self.assertTrue(any(value is None for value in local['values']))
        self.assertTrue(all(value is None for value in manual['values']))
        for query in ({'window': 'all'}, {'end': 'not-a-time'}, {'end': '2026-01-01T00:00:00'},
                      {'end': (now+timedelta(days=1)).isoformat()}):
            self.assertEqual(self.client.get('/api/house-control/temperatures', query_string=query).status_code, 400)

    def test_stacked_levels_round_trip_without_moving_sensor_or_circuit_identities(self):
        self.seed_sensors()
        payload = self.payload([self.binding()])
        payload['plan']['levels'] = [
            {'id': 'first', 'name': 'First floor', 'elevation': 0},
            {'id': 'upstairs', 'name': 'Upstairs', 'elevation': 2.9}]
        payload['plan']['rooms'].append({'id': 'upper', 'name': 'Upstairs living room',
                                         'rect': [0, 0, 5, 4], 'floor': 'oak', 'level': 'upstairs'})
        payload['plan']['stairs'] = [{'id': 'rear', 'level': 'first', 'toLevel': 'upstairs',
                                      'path': [[4, 1], [4, 3], [1, 3]], 'width': 1, 'steps': 14}]
        payload['sensors'] = [{**self.placement(), 'room_id': 'upper', 'position': [2, 2]}]
        result = house_control.save_config(payload)
        self.assertEqual(result['plan']['levels'], payload['plan']['levels'])
        self.assertEqual(result['plan']['stairs'], payload['plan']['stairs'])
        self.assertEqual(result['sensors'], payload['sensors'])
        self.assertEqual(result['bindings'], payload['bindings'])
        saved = house_control.load_config()
        self.assertEqual(saved, result)

    def test_invalid_levels_and_stairs_are_rejected_without_saving(self):
        plan = copy.deepcopy(house_control.DEFAULT_PLAN)
        plan['levels'] = [{'id': 'first', 'name': 'First floor', 'elevation': 0},
                          {'id': 'upstairs', 'name': 'Upstairs', 'elevation': 2.9}]
        variants = []
        for levels in ([], [{'id': 'first', 'name': 'First', 'elevation': float('nan')}],
                       [plan['levels'][0], plan['levels'][0]],
                       [plan['levels'][0], {**plan['levels'][1], 'elevation': 0}]):
            variants.append({**plan, 'levels': levels})
        variants.append({**plan, 'rooms': [{**plan['rooms'][0], 'level': 'missing'}]})
        for change in ({'toLevel': 'first'}, {'toLevel': 'missing'}, {'steps': True},
                       {'path': [[0, 0], [1, 1]]}, {'path': [[0, 0], [0, 0]]}):
            variants.append({**plan, 'stairs': [{'id': 's', 'level': 'first',
                'toLevel': 'upstairs', 'path': [[0, 0], [0, 2]], **change}]})
        for raw in variants:
            with self.subTest(plan=raw), self.assertRaises(ValueError):
                house_control.save_config({**self.payload(), 'plan': raw})
        self.assertFalse(house_control._path().exists())

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

    def seed_sensors(self):
        now = datetime.now(timezone.utc)
        conn = energy._connect()
        with conn:
            conn.executemany('INSERT INTO climate_sensors(source,sensor_id,name) VALUES(?,?,?)',
                             [('manual', 'shared', 'Climate'), ('manual', 'empty', 'Empty')])
            conn.executemany('INSERT INTO climate_readings VALUES(?,?,?,?,?,?)', [
                ('manual', 'shared', (now - timedelta(days=1)).isoformat(), 50, 50, 90),
                ('manual', 'shared', now.isoformat(), 0, 0, None)])
            conn.execute('INSERT INTO aqara_local_observations VALUES(?,?,?,?,?,?,?,?,?)',
                         ('shared', now.isoformat(), 'Aqara', '', 20, 40, 80, 1, 'matter_event'))
            conn.executemany('INSERT INTO radon_readings VALUES(?,?,?,?,?,?,?,?)', [
                ('ecoqube', 'a', (now - timedelta(days=1)).isoformat(), 'Radon',
                 0.7, 'pCi/L', 25.9, now.isoformat()),
                ('ecoqube', 'a', now.isoformat(), 'Radon', 0, 'pCi/L', 0, now.isoformat())])
        conn.close()
        return now

    def placement(self, source='climate:manual', sensor_id='shared'):
        return {'source': source, 'sensor_id': sensor_id, 'room_id': 'living', 'position': [1, 1]}

    def test_catalog_latest_observation_namespaces_fahrenheit_zero_and_freshness(self):
        now = self.seed_sensors()
        rows = {(row['source'], row['sensor_id']): row for row in house_control.sensor_catalog(now)}
        self.assertEqual(len(rows), 4)
        climate = rows[('climate:manual', 'shared')]
        self.assertEqual(climate['metrics']['temperature_f'], 32)
        self.assertEqual(climate['metrics']['humidity_pct'], 0)
        self.assertIsNone(climate['metrics']['battery_pct'])
        self.assertFalse(climate['stale'])
        self.assertTrue(rows[('climate:manual', 'empty')]['stale'])
        self.assertIsNone(rows[('climate:manual', 'empty')]['metrics']['temperature_f'])
        self.assertEqual(rows[('aqara_local', 'shared')]['metrics']['temperature_f'], 68)
        self.assertEqual(rows[('radon:ecoqube', 'a')]['metrics']['radon_pci_l'], 0)
        later = house_control.sensor_catalog(now + timedelta(hours=4))
        self.assertTrue(all(row['stale'] for row in later))
        future = house_control.sensor_catalog(now - timedelta(hours=1))
        self.assertTrue(all(row['stale'] for row in future))

    def test_sensor_placement_is_private_persistent_and_legacy_clients_preserve_it(self):
        self.seed_sensors()
        payload = self.payload()
        payload['sensors'] = [self.placement(), self.placement('aqara_local')]
        response = self.client.put('/api/house-control/config', json=payload,
                                   headers={'Origin': 'http://localhost'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['sensors'], payload['sensors'])
        self.assertEqual(house_control._path().stat().st_mode & 0o777, 0o600)
        result = house_control.save_config(self.payload([self.binding()]))
        self.assertEqual(result['sensors'], payload['sensors'])
        with self.assertRaises(house_control.RevisionConflict):
            house_control.save_config(payload)
        result = house_control.project_state(result, self.menu(), [])
        placed = [row for row in result['sensors'] if row['placement']]
        self.assertEqual(len(placed), 2)
        self.assertTrue(all(row['placement']['room_id'] == 'living' for row in placed))

    def test_invalid_placement_rejects_entire_save_and_never_writes_runtime_config(self):
        self.seed_sensors()
        variants = [None, {}, [self.placement(), self.placement()],
                    [self.placement(sensor_id='unknown')],
                    [{**self.placement(), 'position': [99, 99]}],
                    [{**self.placement(), 'position': [True, 1]}],
                    [{**self.placement(), 'position': [float('nan'), 1]}],
                    [{**self.placement(), 'room_id': 'unknown'}],
                    [{**self.placement(), 'secret': 'no'}]]
        for sensors in variants:
            payload = {**self.payload(), 'sensors': sensors}
            with self.subTest(sensors=sensors), self.assertRaises(ValueError):
                house_control.save_config(payload)
        self.assertFalse(house_control._path().exists())

    def test_room_removal_requires_explicit_placement_removal_and_orphans_stay_editable(self):
        self.seed_sensors()
        house_control.save_config({**self.payload(), 'sensors': [self.placement()]})
        payload = self.payload()
        payload['plan']['rooms'] = payload['plan']['rooms'][1:]
        with self.assertRaises(ValueError):
            house_control.save_config(payload)
        payload['sensors'] = []
        self.assertEqual(house_control.save_config(payload)['sensors'], [])
        house_control.save_config({**self.payload(), 'sensors': [{
            **self.placement(), 'room_id': 'kitchen', 'position': [6, 1]}]})
        conn = energy._connect()
        with conn:
            conn.execute('DELETE FROM climate_readings')
            conn.execute('DELETE FROM climate_sensors')
        conn.close()
        config = house_control.load_config()
        state = house_control.project_state(config, self.menu(), [])
        orphan = next(row for row in state['sensors'] if row['source'] == 'climate:manual')
        self.assertFalse(orphan['available'])
        self.assertTrue(orphan['stale'])
        house_control.save_config(self.payload())
        self.assertEqual(house_control.load_config()['sensors'], config['sensors'])

    def test_radon_catalog_uses_remote_cache_when_present(self):
        now = self.seed_sensors()
        conn = energy._connect()
        with conn:
            conn.execute('INSERT INTO radon_sync_cache_state VALUES(1,?,?,?,?,?)',
                         ('remote', 'generation', 1, 1, now.isoformat()))
            conn.execute('INSERT INTO radon_cached_readings VALUES(?,?,?,?,?,?,?,?)',
                         ('ecoqube', 'remote', now.isoformat(), 'Remote radon',
                          0.7, 'pCi/L', 25.9, now.isoformat()))
        conn.close()
        rows = [row for row in house_control.sensor_catalog() if row['source'].startswith('radon:')]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['sensor_id'], 'remote')
        self.assertAlmostEqual(rows[0]['metrics']['radon_pci_l'], 0.7)
