import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

import energy
import web


class CircuitDetailTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        database = patch.object(energy, 'DB_PATH', str(Path(directory.name) / 'detail.db'))
        database.start()
        self.addCleanup(database.stop)
        energy.ensure_table()
        self.now = datetime.now().replace(microsecond=0)
        self.client = web.app.test_client()

    def seed(self, rows):
        conn = energy._connect()
        try:
            with conn:
                conn.executemany(
                    'INSERT INTO readings(timestamp,device_gid,channel_name,usage_kwh,cost_cents) VALUES (?,?,?,?,?)',
                    rows,
                )
        finally:
            conn.close()

    def test_full_page_and_all_period_tabs_render_without_dashboard_context(self):
        self.seed([(self.now.isoformat(), 'A', 'Pump', 0.002, 0.04)])
        for suffix in ('', '/hour', '/day', '/week', '/month', '/year'):
            with self.subTest(suffix=suffix):
                response = self.client.get('/circuit/Pump' + suffix)
                self.assertEqual(response.status_code, 200)
                self.assertIn(b'id="usageChart"', response.data)
                self.assertNotIn(b'id="operational-review"', response.data)
                self.assertNotIn(b'Action Center', response.data)

    def test_context_and_chart_use_the_same_selected_device(self):
        self.seed([
            (self.now.isoformat(), 'A', 'Pump', 100, 2000),
            ((self.now - timedelta(minutes=1)).isoformat(), 'B', 'Pump', 2, 40),
            ((self.now - timedelta(days=1, minutes=1)).isoformat(), 'B', 'Pump', 1, 20),
        ])
        common = web._common()
        common['active_device_gid'] = 'B'
        with patch.object(web, '_common', return_value=common), patch.object(
            web, '_render', wraps=web._render,
        ) as render:
            response = self.client.get('/circuit/Pump/hour')
        self.assertEqual(response.status_code, 200)
        context = render.call_args.kwargs
        self.assertEqual(context['total']['total_kwh'], 2)
        self.assertEqual(context['ctx']['current_kwh'], 2)
        self.assertEqual(context['ctx']['yesterday_kwh'], 1)

    def test_recorded_zero_is_visible_and_missing_comparison_stays_unknown(self):
        self.seed([
            (self.now.isoformat(), 'A', 'Idle', 0, 0),
            ((self.now - timedelta(days=1, minutes=1)).isoformat(), 'A', 'Idle', 0, 0),
        ])
        with patch.object(web, '_render', wraps=web._render) as render:
            response = self.client.get('/circuit/Idle/hour')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'0.000 kWh', response.data)
        context = render.call_args.kwargs['ctx']
        self.assertEqual(context['yesterday_kwh'], 0)
        self.assertIsNone(context['last_week_kwh'])
        self.assertIsNone(context['vs_yesterday_pct'])

    def test_empty_database_renders_circuit_page(self):
        response = self.client.get('/circuit/Missing')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'N/A', response.data)
        self.assertIn(b'id="usageChart"', response.data)

    def test_encoded_name_is_escaped_and_poll_cadence_is_per_hour(self):
        name = 'Kitchen & <script>not executable</script>'
        self.seed([(self.now.isoformat(), 'A', name, 0, 0)])
        with patch.object(energy, 'POLL_INTERVAL', 120):
            response = self.client.get('/circuit/' + quote(name, safe=''))
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Kitchen &amp; &lt;script&gt;', response.data)
        self.assertNotIn(b'<script>not executable</script>', response.data)
        self.assertIn(b'30.0 polls/hr', response.data)


if __name__ == '__main__':
    unittest.main()
