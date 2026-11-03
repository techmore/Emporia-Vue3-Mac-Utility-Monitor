import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import energy
import web


class HeatmapKeyboardTests(unittest.TestCase):
    """Each Trends heatmap is one Tab stop; arrow keys move between cells."""

    def render_trends(self):
        hours = [f'2026-10-0{1 + h // 24}T{h % 24:02d}:00' for h in range(168)]
        cell = {'kwh': 0.5, 'level': 3, 'samples': 60}
        heatmap = {'days': ['Thu'] * 7, 'hours': hours, 'circuits': [
            {'name': 'Kitchen', 'cells': [cell] * 167 + [None]},
            {'name': 'Garage', 'cells': [cell] * 168},
        ]}
        week_cell = {'kwh': 0.4, 'weeks': 2, 'low': 0.3, 'high': 0.5}
        pattern = {'main': None, 'circuits': [
            {'name': 'Kitchen', 'cells': [week_cell] * 167 + [None], 'supported': 167, 'weekly_kwh': None},
            {'name': 'Garage', 'cells': [week_cell] * 168, 'supported': 168, 'weekly_kwh': 67.2},
        ]}
        with tempfile.TemporaryDirectory() as directory, patch.object(
            energy, 'DB_PATH', str(Path(directory) / 'heatmap.db'),
        ):
            energy.ensure_table()
            with patch.object(energy, 'get_power_heatmap', return_value=heatmap), \
                    patch.object(energy, 'get_weekly_power_pattern', return_value=pattern):
                response = web.app.test_client().get('/trends')
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    def test_one_tab_stop_per_heatmap(self):
        html = self.render_trends()
        cells = re.findall(r'<td[^>]*class="heat-cell[^"]*"[^>]*>', html, re.S)
        self.assertEqual(len(cells), 4 * 168)
        stops = [cell for cell in cells if 'tabindex="0"' in cell]
        self.assertEqual(len(stops), 2)
        self.assertEqual(sum('tabindex="-1"' in cell for cell in cells), 4 * 168 - 2)
        self.assertEqual(html.count('class="power-heatmap" role="grid"'), 2)
        self.assertIn('id="heatmap-keys"', html)
        self.assertIn("grid.addEventListener('keydown'", html)

    def test_typical_week_cells_have_spoken_names(self):
        html = self.render_trends()
        self.assertIn('aria-label="Garage Mon 00:00: 0.400 kWh typical"', html)
        self.assertIn('aria-label="Kitchen Sun 23:00: insufficient history"', html)


if __name__ == '__main__':
    unittest.main()
