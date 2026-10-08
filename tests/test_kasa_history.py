import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import energy
import kasa_collect
import kasa_history
import kasa_monitor


class KasaHistoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db = patch.object(energy, 'DB_PATH', str(Path(self.directory.name) / 'energy.db'))
        self.db.start()
        energy.ensure_table()
        self.identifier = kasa_history.register_device('192.168.222.10', 'Light')
        self.snapshot = {'host': '192.168.222.10', 'model': 'fixture', 'alias': '',
                         'device_id': 'hardware-fixture', 'is_on': False}

    def tearDown(self):
        self.db.stop()
        self.directory.cleanup()

    def test_registration_is_unqueried_and_rejects_duplicates_public_hosts(self):
        device = kasa_history.get_devices()[0]
        self.assertIsNone(device['status'])
        self.assertIsNone(device['is_on'])
        for host in ('192.168.222.10', '8.8.8.8', 'example.com'):
            with self.assertRaises(ValueError):
                kasa_history.register_device(host, 'Duplicate')

    def test_failed_query_is_unknown_not_previous_off(self):
        self.assertTrue(kasa_history.record_query(self.identifier, self.snapshot))
        self.assertEqual(kasa_history.get_devices()[0]['is_on'], 0)
        kasa_history.record_query(self.identifier, None, 'TimeoutError')
        device = kasa_history.get_devices()[0]
        self.assertEqual(device['status'], 'unavailable')
        self.assertIsNone(device['is_on'])
        self.assertEqual(device['reported_device_id'], 'hardware-fixture')

    def test_changed_hardware_identity_is_not_silently_accepted(self):
        kasa_history.record_query(self.identifier, self.snapshot)
        self.assertFalse(kasa_history.record_query(self.identifier,
            {**self.snapshot, 'device_id': 'different-hardware', 'is_on': True}))
        device = kasa_history.get_devices()[0]
        self.assertEqual(device['error_type'], 'DeviceIdentityChanged')
        self.assertIsNone(device['is_on'])
        self.assertEqual(device['reported_device_id'], 'hardware-fixture')

    async def test_collector_records_success_failure_without_credentials(self):
        kasa_history.register_device('192.168.222.11', 'Second')
        with patch.object(kasa_monitor, 'probe', new=AsyncMock(
                side_effect=[self.snapshot, TimeoutError('private-secret')])):
            result = await kasa_collect.poll_once('account', 'private-secret')
        self.assertEqual(result, {'queried': 2, 'ok': 1, 'unavailable': 1, 'skipped': 0})
        self.assertNotIn('private-secret', str(kasa_history.get_devices()))

    def test_removal_deletes_history_and_future_writes_are_rejected(self):
        kasa_history.record_query(self.identifier, self.snapshot)
        self.assertTrue(kasa_history.remove_device(self.identifier))
        self.assertFalse(kasa_history.remove_device(self.identifier))
        self.assertEqual(kasa_history.get_devices(), [])
        with self.assertRaises(ValueError):
            kasa_history.record_query(self.identifier, self.snapshot)
