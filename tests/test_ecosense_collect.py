import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import ecosense_collect as collector


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)

    def test_timestamp_formats_and_no_poll_time_fallback(self):
        expected = self.now.isoformat()
        for value in ('2026-10-08T12:00:00Z', self.now.timestamp(),
                      self.now.timestamp() * 1000):
            self.assertEqual(collector.measurement_time(value, self.now), expected)
        for value in (None, True, '2026-10-08 12:00:00', 0, float('nan')):
            with self.assertRaises(ValueError):
                collector.measurement_time(value, self.now)

    def test_unavailable_sensor_does_not_record_zero_or_retrieval_time(self):
        device = {'serial_number': 'test', 'radon_level': 19,
                  'last_radon_update_time': '2026-10-08T12:00:00Z'}
        rows, skipped = collector.observations([
            device, {**device, 'radon_level': 0},
            {**device, 'last_radon_update_time': None},
        ], self.now)
        self.assertEqual(skipped, 2)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['value'], 19)
        self.assertEqual(rows[0]['unit'], 'Bq/m3')
        self.assertEqual(rows[0]['timestamp'], self.now.isoformat())

    def test_real_naive_timestamp_requires_independent_utc_corroboration(self):
        device = {'serial_number': 'test', 'radon_level': 23,
                  'last_radon_update_time': '2026-10-08T11:59:00.203726',
                  'last_update_time': '2026-10-08T12:00:00.203726',
                  'last_update_ts': int(self.now.timestamp()), 'time_zone': '-4'}
        rows, skipped = collector.observations([device], self.now)
        self.assertEqual(skipped, 0)
        self.assertEqual(rows[0]['timestamp'], '2026-10-08T11:59:00.203726+00:00')
        # Display timezone is not the measurement timezone; no four-hour shift.
        for invalid in ({**device, 'last_update_ts': None},
                        {**device, 'last_update_ts': self.now.timestamp() + 14400}):
            self.assertEqual(collector.observations([invalid], self.now), ([], 1))

    def test_connection_saves_once_and_imports_without_exposing_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(collector, 'CONFIG_PATH', root/'credentials.json'), \
                 patch.object(collector, 'SNAPSHOT_PATH', root/'snapshot.json'), \
                 patch.object(collector, 'STATUS_PATH', root/'status.json'), \
                 patch.object(collector, 'EcoSenseClient') as client, \
                 patch.object(collector.radon, 'ingest_observations') as ingest:
                client.return_value.get_devices.return_value = [{
                    'serial_number': 'test', 'radon_level': 19,
                    'last_radon_update_time': datetime.now(timezone.utc).isoformat(),
                }]
                ingest.return_value = {'inserted': 1, 'duplicates': 0}
                result = collector.connect_account('test@example.com', 'private-password')
                self.assertEqual(result['inserted'], 1)
                self.assertEqual((root/'credentials.json').stat().st_mode & 0o777, 0o600)
                self.assertNotIn('private-password', json.dumps(result))
                self.assertNotIn('private-password', (root/'status.json').read_text())
                before = (root/'credentials.json').read_text()
                client.return_value.get_devices.side_effect = RuntimeError('rejected')
                with self.assertRaises(RuntimeError):
                    collector.connect_account('test@example.com', 'wrong-password')
                self.assertEqual((root/'credentials.json').read_text(), before)
