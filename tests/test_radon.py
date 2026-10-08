import math
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import energy
import radon


class RadonTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(energy, 'DB_PATH', str(Path(self.directory.name) / 'radon.db'))
        self.db_patch.start()
        energy.ensure_table()
        self.now = datetime.now(timezone.utc)
        self.row = {'source': 'ecosense', 'sensor_id': 'one', 'name': 'Basement',
                    'timestamp': self.now.isoformat(), 'value': 2.5, 'unit': 'pCi/L'}

    def tearDown(self):
        self.db_patch.stop()
        self.directory.cleanup()

    def test_conversion_original_units_deduplication_and_scope(self):
        radon.ingest_observations([self.row], self.now)
        equivalent = {**self.row, 'timestamp': self.now.astimezone(
            timezone(timedelta(hours=-4))).isoformat()}
        self.assertEqual(radon.ingest_observations([equivalent], self.now)['duplicates'], 1)
        radon.ingest_observations([{**self.row, 'sensor_id': 'two', 'value': 90,
                                   'unit': 'Bq/m3'}], self.now)
        rows = radon.get_history('ecosense', 'one', now=self.now)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['radon_bq_m3'], 92.5)
        self.assertEqual(rows[0]['measured_value'], 2.5)
        self.assertEqual(rows[0]['measured_unit'], 'pCi/L')
        self.assertEqual(radon.get_history('ecosense', 'two', now=self.now)[0]['radon_bq_m3'], 90)
        self.assertEqual(radon.get_history('manual', 'one', now=self.now), [])

    def test_invalid_batch_never_partially_writes(self):
        for change in ({'value': math.nan}, {'value': math.inf}, {'value': True},
                       {'value': -1}, {'value': None}, {'unit': 'ppm'}, {'source': 'demo'},
                       {'timestamp': self.now.replace(tzinfo=None).isoformat()},
                       {'timestamp': (self.now + timedelta(hours=1)).isoformat()}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                radon.ingest_observations([self.row, {**self.row, **change}], self.now)
            self.assertEqual(radon.get_history('ecosense', 'one', now=self.now), [])

    def test_conflicting_retry_rolls_back_entire_batch(self):
        radon.ingest_observations([self.row], self.now)
        with self.assertRaises(ValueError):
            radon.ingest_observations([{**self.row, 'sensor_id': 'two'},
                                      {**self.row, 'value': 3}], self.now)
        self.assertEqual(radon.get_history('ecosense', 'two', now=self.now), [])
        self.assertEqual(radon.get_history('ecosense', 'one', now=self.now)[0]['measured_value'], 2.5)

    def test_history_bounds_and_no_synthetic_samples(self):
        self.assertEqual(radon.get_history('ecosense', 'one', now=self.now), [])
        for days in (True, 0, 366):
            with self.assertRaises(ValueError):
                radon.get_history('ecosense', 'one', days, self.now)
