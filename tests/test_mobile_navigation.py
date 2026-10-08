import unittest

import web


class MobileNavigationTests(unittest.TestCase):
    def test_sections_render_with_current_location(self):
        for path in ('/circuits', '/aqara', '/radon', '/mitsubishi', '/kasa'):
            with self.subTest(path=path), web.app.test_request_context(path):
                page = web._render('<main>Fixture</main>')
                self.assertIn(f'href="{path}" aria-current="page"', page)
                nav = page.split('<nav class="mobile-sections"', 1)[1].split("</nav>", 1)[0]
                self.assertEqual(nav.count('aria-current="page"'), 1)
                self.assertIn('/static/mobile-navigation.js', page)

    def test_aqara_is_an_independent_section(self):
        self.assertNotIn('/aqara', web.WORKSPACE_PAGES)

    def test_render_without_request_context(self):
        with web.app.app_context():
            page = web._render('<main>Fixture</main>')
            nav = page.split('<nav class="mobile-sections"', 1)[1].split("</nav>", 1)[0]
            self.assertNotIn('aria-current="page"', nav)
