import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import energy
import kasa_history
import kasa_monitor
import web


class KasaControlTests(unittest.IsolatedAsyncioTestCase):
    def device(self):
        device = SimpleNamespace(model='HS220', device_id='pinned', alias='Fixture',
                                 is_on=False, update=AsyncMock(), disconnect=AsyncMock())
        light = SimpleNamespace(brightness=20)
        async def brightness(value):
            light.brightness = value
            device.is_on = True
        async def on():
            device.is_on = True
        light.set_brightness = AsyncMock(side_effect=brightness)
        device.modules = {'Light': light}
        device.turn_on = AsyncMock(side_effect=on)
        device.turn_off = AsyncMock()
        return device

    async def test_identity_checked_and_outcome_read_back(self):
        device = self.device()
        module = SimpleNamespace(Discover=SimpleNamespace(discover_single=AsyncMock(return_value=device)),
                                 Module=SimpleNamespace(Light='Light'))
        with patch.dict(sys.modules, {'kasa': module}):
            result = await kasa_monitor.control('192.168.222.23', 'pinned', brightness=65)
        self.assertTrue(result['verified'])
        self.assertEqual(result['brightness'], 65)
        self.assertEqual(device.update.await_count, 2)
        device.disconnect.assert_awaited_once()

    async def test_wrong_identity_sends_no_command(self):
        device = self.device()
        module = SimpleNamespace(Discover=SimpleNamespace(discover_single=AsyncMock(return_value=device)),
                                 Module=SimpleNamespace(Light='Light'))
        with patch.dict(sys.modules, {'kasa': module}), self.assertRaises(ValueError):
            await kasa_monitor.control('192.168.222.23', 'different', is_on=True)
        device.turn_on.assert_not_awaited()
        device.disconnect.assert_awaited_once()

    async def test_unverified_command_is_not_reported_successful(self):
        device = self.device()
        device.turn_on = AsyncMock()
        module = SimpleNamespace(Discover=SimpleNamespace(discover_single=AsyncMock(return_value=device)),
                                 Module=SimpleNamespace(Light='Light'))
        with patch.dict(sys.modules, {'kasa': module}), self.assertRaises(RuntimeError):
            await kasa_monitor.control('192.168.222.23', 'pinned', is_on=True)
        device.turn_on.assert_awaited_once()
        device.disconnect.assert_awaited_once()


class KasaPaneTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db = patch.object(energy, 'DB_PATH', str(Path(self.directory.name) / 'test.db'))
        self.db.start()
        energy.ensure_table()
        self.identifier = kasa_history.register_device('192.168.222.23', '<img src=x>')
        kasa_history.record_query(self.identifier, {'host':'192.168.222.23', 'device_id':'pinned',
            'model':'HS220', 'alias':'Fixture', 'is_on':False})
        self.client = web.app.test_client()

    def tearDown(self):
        self.db.stop()
        self.directory.cleanup()

    def test_pane_escapes_labels_and_has_confirmed_controls(self):
        response = self.client.get('/kasa')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('&lt;img src=x&gt;', html)
        self.assertIn('Apply brightness', html)
        self.assertIn('confirm(', html)
        self.assertIn('not a power measurement', html)

    def test_control_requires_origin_confirmation_and_one_action(self):
        url = '/api/kasa/devices/' + self.identifier + '/control'
        with patch.object(kasa_monitor, 'control', new=AsyncMock()) as control:
            self.assertEqual(self.client.post(url, json={'confirmed':True,'is_on':True}).status_code, 403)
            for payload in ({'is_on':True}, {'confirmed':True,'is_on':True,'brightness':30}):
                self.assertEqual(self.client.post(url, json=payload,
                    headers={'Origin':'http://localhost'}).status_code, 400)
            control.assert_not_awaited()

    def test_failed_control_hides_payload_and_marks_unknown(self):
        with patch.object(kasa_monitor, 'control', new=AsyncMock(side_effect=TimeoutError('secret'))):
            response = self.client.post('/api/kasa/devices/' + self.identifier + '/control',
                json={'confirmed':True,'is_on':True}, headers={'Origin':'http://localhost'})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn('secret', response.get_data(as_text=True))
        self.assertEqual(kasa_history.get_devices()[0]['status'], 'unavailable')

    def test_mapping_validates_circuit_and_cleans_up(self):
        conn = energy._connect()
        with conn:
            conn.execute('INSERT INTO readings(timestamp,device_gid,channel_name,usage_kwh) '
                         'VALUES (?,?,?,?)', ('2026-10-08T00:00:00','A','Lights',0.1))
        conn.close()
        with self.assertRaises(ValueError):
            kasa_history.link_circuit(self.identifier, 'A', 'Main')
        kasa_history.link_circuit(self.identifier, 'A', 'Lights')
        self.assertEqual(kasa_history.circuit_links()[self.identifier]['channel_name'], 'Lights')
        kasa_history.remove_device(self.identifier)
        self.assertEqual(kasa_history.circuit_links(), {})
