import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import energy
import web


class SettingsWorkspaceTests(unittest.TestCase):
    def test_workspace_routes_keep_direct_links_and_selected_navigation(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(energy, 'DB_PATH', str(Path(directory) / 'workspace.db')):
            energy.ensure_table()
            client = web.app.test_client()
            for path in web.WORKSPACE_PAGES:
                with self.subTest(path=path):
                    response = client.get(path)
                    self.assertEqual(response.status_code, 200)
                    html = response.get_data(as_text=True)
                    self.assertEqual(html.count('aria-label="Settings workspace"'), 1)
                    self.assertIn(f'href="{path}"\n     aria-current="page"', html)
                    self.assertIn('class="workspace-content"', html)
                    self.assertNotIn('<iframe', html)
                    if path == '/panel':
                        self.assertIn('class="panel-editor-scroll" role="region"', html)
                        self.assertIn('aria-label="Breaker configuration fields" tabindex="0"', html)
            self.assertNotIn('aria-label="Settings workspace"',
                             client.get('/').get_data(as_text=True))
