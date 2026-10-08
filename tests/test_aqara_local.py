import csv
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import aqara_local
import energy
import web
from aqara_local import decode_sensors


class MatterSensorDecodingTests(unittest.TestCase):
    def test_bridge_children_units_and_reachability(self):
        node = {'available': True, 'attributes': {
            '5/57/18': 'lumi.abc123', '5/29/3': [6, 7],
            '5/57/17': False, '5/47/12': 160,
            '6/1026/0': 2227, '7/1029/0': 5346,
        }}
        sensor, = decode_sensors(node)
        self.assertEqual(sensor['temperature'], 22.27)
        self.assertEqual(sensor['humidity'], 53.46)
        self.assertEqual(sensor['battery'], 80)
        self.assertFalse(sensor['online'])
        self.assertEqual(sensor['did'], 'lumi.abc123')

    def test_matter_null_values_are_not_measurements(self):
        node = {'available': True, 'attributes': {
            '5/57/18': 'lumi.abc123', '5/29/3': [6, 7],
            '6/1026/0': -32768, '7/1029/0': 65535,
        }}
        self.assertEqual(decode_sensors(node), [])

    def test_unavailable_hub_marks_child_offline(self):
        node = {'available': False, 'attributes': {
            '5/57/18': 'lumi.abc123', '5/29/3': [6],
            '5/57/17': True, '6/1026/0': 2000,
        }}
        self.assertFalse(decode_sensors(node)[0]['online'])

    def test_invalid_types_and_nonfinite_values_are_not_readings(self):
        for value in (True, float('nan'), float('inf'), -32768, 9000):
            node = {'available': True, 'attributes': {
                '5/57/18': 'lumi.abc', '5/29/3': [6], '6/1026/0': value,
            }}
            self.assertEqual(decode_sensors(node), [])
        self.assertEqual(decode_sensors({'attributes': []}), [])
        self.assertEqual(decode_sensors(None), [])

    def test_unknown_reachability_is_not_online(self):
        node = {'available': 'false', 'attributes': {
            '5/57/18': 'lumi.abc', '5/29/3': [6], '5/57/17': 'true',
            '6/1026/0': 0, '5/47/12': 255,
        }}
        sensor, = decode_sensors(node)
        self.assertFalse(sensor['online'])
        self.assertEqual(sensor['temperature'], 0)
        self.assertIsNone(sensor['battery'])


class AqaraLocalStorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db = patch.object(energy, 'DB_PATH', str(Path(self.directory.name) / 'test.db'))
        self.db.start()
        energy.ensure_table()
        aqara_local.record_node({'available': True, 'attributes': {
            '5/57/18':'lumi.abc', '5/29/3':[6], '5/57/17':True, '6/1026/0':2200,
        }}, 'matter_snapshot')

    def tearDown(self):
        self.db.stop()
        self.directory.cleanup()

    def test_local_page_uses_recorded_data_not_cloud(self):
        with patch('aqara.get_sensors') as cloud:
            response = web.app.test_client().get('/aqara')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Last collector observation', response.get_data(as_text=True))
        cloud.assert_not_called()

    def test_csv_streams_and_escapes_spreadsheet_formulas(self):
        conn = energy._connect()
        try:
            with conn:
                conn.execute('UPDATE aqara_local_observations SET name=?', ('=HYPERLINK("bad")',))
        finally:
            conn.close()
        response = web.app.test_client().get('/api/aqara/local/history.csv')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.is_streamed)
        rows = list(csv.DictReader(io.StringIO(response.get_data(as_text=True))))
        self.assertTrue(rows[0]['name'].startswith("'="))
        self.assertEqual(rows[0]['temperature'], '22.0')
        self.assertEqual(response.headers['Cache-Control'], 'no-store')

    def test_unknown_source_is_rejected_without_writing(self):
        with self.assertRaises(ValueError):
            aqara_local.record_node({}, 'arbitrary')
