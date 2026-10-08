import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import energy
import kasa_monitor
import web


class KasaProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_reads_selected_device_and_disconnects(self):
        device = SimpleNamespace(update=AsyncMock(), disconnect=AsyncMock(),
                                 is_on=True, model='fixture-switch', alias='Fixture')
        discovery = AsyncMock(return_value=device)
        with patch.dict(sys.modules, {'kasa': SimpleNamespace(
                Discover=SimpleNamespace(discover_single=discovery))}):
            result = await kasa_monitor.probe('192.168.222.10')
        self.assertTrue(result['is_on'])
        self.assertTrue(result['read_only'])
        self.assertIn('queried_at', result)
        self.assertNotIn('watts', result)
        discovery.assert_awaited_once_with('192.168.222.10', username=None, password=None,
                                          discovery_timeout=3, timeout=3)
        device.update.assert_awaited_once()
        device.disconnect.assert_awaited_once()

    async def test_update_failure_does_not_report_off_and_disconnects(self):
        device = SimpleNamespace(update=AsyncMock(side_effect=TimeoutError('fixture')),
                                 disconnect=AsyncMock())
        with patch.dict(sys.modules, {'kasa': SimpleNamespace(Discover=SimpleNamespace(
                discover_single=AsyncMock(return_value=device)))}):
            with self.assertRaises(TimeoutError):
                await kasa_monitor.probe('192.168.222.10')
        device.disconnect.assert_awaited_once()

    async def test_unavailable_device_and_partial_credentials_rejected(self):
        with patch.dict(sys.modules, {'kasa': SimpleNamespace(Discover=SimpleNamespace(
                discover_single=AsyncMock(return_value=None)))}):
            with self.assertRaises(ConnectionError):
                await kasa_monitor.probe('192.168.222.10')
            with self.assertRaises(ValueError):
                await kasa_monitor.probe('192.168.222.10', 'account', None)

    def test_no_public_dns_or_multicast_targets(self):
        for host in ('example.com', '8.8.8.8', '224.0.0.1', '0.0.0.0', '::1', '100.64.0.1'):
            with self.subTest(host=host), self.assertRaises(ValueError):
                kasa_monitor.validate_host(host)


class KasaSettingsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db = patch.object(energy, 'DB_PATH', str(Path(self.directory.name) / 'energy.db'))
        self.db.start()
        energy.ensure_table()
        self.client = web.app.test_client()

    def tearDown(self):
        self.db.stop()
        self.directory.cleanup()

    def test_success_returns_read_only_state_without_credentials(self):
        snapshot = {'host': '192.168.222.10', 'model': 'fixture', 'alias': 'Fixture',
                    'is_on': False, 'queried_at': '2026-10-07T12:00:00+00:00', 'read_only': True}
        with patch.object(kasa_monitor, 'probe', new=AsyncMock(return_value=snapshot)) as probe:
            response = self.client.post('/api/kasa/probe', json={
                'host': snapshot['host'], 'username': 'private-account', 'password': 'private-secret'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()['is_on'])
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertNotIn('private-', response.get_data(as_text=True))
        probe.assert_awaited_once_with(snapshot['host'], 'private-account', 'private-secret')

    def test_failures_are_unknown_not_off_and_hide_exception_payload(self):
        for error, code in ((TimeoutError('secret'), 504), (RuntimeError('private-secret'), 502)):
            with patch.object(kasa_monitor, 'probe', new=AsyncMock(side_effect=error)):
                response = self.client.post('/api/kasa/probe', json={'host': '192.168.222.10'})
            self.assertEqual(response.status_code, code)
            self.assertNotIn('is_on', response.get_json())
            self.assertNotIn('secret', response.get_data(as_text=True))
            self.assertEqual(response.headers['Cache-Control'], 'no-store')

    def test_request_guard_prevents_cross_origin_and_oversized_queries(self):
        with patch.object(kasa_monitor, 'probe', new=AsyncMock()) as probe:
            response = self.client.post('/api/kasa/probe', json={'host': '192.168.222.10'},
                                        headers={'Origin': 'https://untrusted.example'})
            self.assertEqual(response.status_code, 403)
            self.assertEqual(self.client.post('/api/kasa/probe', json={'host': 'a' * 9000}).status_code, 413)
            self.assertEqual(self.client.post('/api/kasa/probe', json={'host': 1}).status_code, 400)
            probe.assert_not_awaited()

    def test_settings_has_active_read_only_form_and_no_subnet_scan_claim(self):
        html = self.client.get('/settings').get_data(as_text=True)
        self.assertIn('id="kasaProbeForm"', html)
        self.assertIn('Check state — no control', html)
        self.assertNotIn('id="kasaSubnet"', html)
        self.assertNotIn('No cloud credentials needed for local access', html)
