import unittest

import web


class GuideTests(unittest.TestCase):
    def test_setup_precedes_reference_and_links_to_real_pages(self):
        response = web.app.test_client().get('/guide')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertLess(html.index('id="guide-start"'), html.index('<h2>Reference</h2>'))
        self.assertNotIn('First-Time Setup', html)
        self.assertIn('monthly fixed charge separately', html)
        self.assertIn('heartbeat alone do not prove', html)
        self.assertIn('collection is not yet connected', html)
        for path in ('/settings', '/log', '/circuits', '/trends', '/reports', '/radon'):
            self.assertIn(f'href="{path}"', html)
            self.assertEqual(web.app.test_client().get(path).status_code, 200)
