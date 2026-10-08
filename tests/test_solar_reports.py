import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import energy
import web


class SolarReportsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db = patch.object(energy, 'DB_PATH', str(Path(self.directory.name) / 'test.db'))
        self.db.start()
        energy.ensure_table()
        self.client = web.app.test_client()

    def tearDown(self):
        self.db.stop()
        self.directory.cleanup()

    def test_missing_main_is_not_replaced_with_zero(self):
        html = self.client.get('/reports?solar_generation=10&solar_self_pct=60').get_data(as_text=True)
        self.assertIn('No recorded Main consumption', html)
        self.assertIn('Recorded Main consumption is required', html)
        self.assertNotIn('Combined modeled value</dt>', html)

    def test_known_consumption_and_unknown_export_are_displayed_separately(self):
        main = {'total_kwh': 20, 'total_cents': 451.6, 'readings': 1}
        with patch.object(energy, 'get_main_total', return_value=main), \
             patch.object(energy, 'RATE_CENTS', 22.58):
            response = self.client.get('/reports?solar_generation=10&solar_self_pct=60')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('$1.35', html)
        self.assertIn('Unknown: no export rate supplied', html)
        self.assertIn('Fixed monthly charges remain unchanged', html)
        self.assertIn('0/24 completed hours', html)

    def test_bad_inputs_fail_without_500_or_unsafe_echo(self):
        main = {'total_kwh': 20, 'total_cents': 451.6, 'readings': 1}
        with patch.object(energy, 'get_main_total', return_value=main):
            for value in ('nan', 'inf', '-1', 'bad', '<script>alert(1)</script>'):
                response = self.client.get('/reports', query_string={
                    'solar_generation': value, 'solar_self_pct': '101'})
                self.assertEqual(response.status_code, 200)
                self.assertIn('role="alert"', response.get_data(as_text=True))
                self.assertNotIn('<script>alert(1)</script>', response.get_data(as_text=True))

    def test_reports_uses_scoped_responsive_groups(self):
        html = self.client.get('/reports').get_data(as_text=True)
        self.assertIn('class="page reports-page"', html)
        self.assertIn('class="reports-columns"', html)
        self.assertIn('class="reports-review-copy"', html)
        self.assertNotIn('grid-template-columns:1.15fr 1fr', html)
        self.assertNotIn('grid-template-columns:1.2fr 1fr', html)
