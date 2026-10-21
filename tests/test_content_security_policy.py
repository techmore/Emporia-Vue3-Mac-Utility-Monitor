import re
import unittest
from pathlib import Path
from unittest.mock import patch

import web

ROOT = Path(__file__).resolve().parent.parent
INLINE_HANDLER = re.compile(r'<[^>]+\son[a-z]+\s*=', re.IGNORECASE)
INLINE_SCRIPT = re.compile(r'<script(?![^>]*\bsrc=)([^>]*)>', re.IGNORECASE)


def html_routes():
    """Every parameterless GET page plus a parameterised circuit page."""
    paths = [rule.rule for rule in web.app.url_map.iter_rules()
             if 'GET' in rule.methods and '<' not in rule.rule
             and not rule.rule.startswith(('/static', '/api'))]
    return sorted(paths) + ['/circuit/Main', '/circuit/Main/week']


class ContentSecurityPolicyTests(unittest.TestCase):
    def test_every_page_enforces_a_nonce_policy_its_own_scripts_satisfy(self):
        client = web.app.test_client()
        checked = 0
        for path in html_routes():
            response = client.get(path, follow_redirects=True)
            if response.mimetype != 'text/html':
                response.close()
                continue
            checked += 1
            self.assertEqual(response.status_code, 200, path)
            policy = response.headers.get('Content-Security-Policy', '')
            nonce = re.search(r"script-src 'self' 'nonce-([A-Za-z0-9_-]{20,})'", policy)
            self.assertIsNotNone(nonce, f'{path}: {policy!r}')
            html = response.get_data(as_text=True)
            for attributes in INLINE_SCRIPT.findall(html):
                self.assertIn(f'nonce="{nonce.group(1)}"', attributes, path)
            self.assertNotIn('csp_nonce', html, f'{path}: unrendered nonce placeholder')
            handler = INLINE_HANDLER.search(html)
            self.assertIsNone(handler, f'{path}: inline handler {handler and handler.group(0)}')
            self.assertIn('/static/actions.js', html, path)
        self.assertGreaterEqual(checked, 15)

    def test_policy_blocks_the_dangerous_sources(self):
        policy = web.content_security_policy('abc')
        for directive in ("object-src 'none'", "base-uri 'none'", "frame-ancestors 'none'",
                          "form-action 'self'", "connect-src 'self'", "font-src 'self'"):
            self.assertIn(directive, policy)
        script_src = re.search(r"script-src ([^;]+)", policy).group(1)
        for unsafe in ("'unsafe-inline'", "'unsafe-eval'", 'http:', 'https:', '*', 'data:'):
            self.assertNotIn(unsafe, script_src)

    def test_nonce_is_fresh_for_every_request(self):
        client = web.app.test_client()
        nonces = {re.search(r"'nonce-([^']+)'",
                            client.get('/guide').headers['Content-Security-Policy']).group(1)
                  for _ in range(5)}
        self.assertEqual(len(nonces), 5)

    def test_json_responses_do_not_carry_a_page_policy(self):
        response = web.app.test_client().get('/api/version')
        self.assertNotIn('Content-Security-Policy', response.headers)

    def test_report_only_and_off_modes(self):
        client = web.app.test_client()
        with patch.object(web, 'CSP_MODE', 'report-only'):
            response = client.get('/guide')
            self.assertNotIn('Content-Security-Policy', response.headers)
            self.assertIn("'nonce-", response.headers['Content-Security-Policy-Report-Only'])
        with patch.object(web, 'CSP_MODE', 'off'):
            response = client.get('/guide')
            self.assertNotIn('Content-Security-Policy', response.headers)
            self.assertNotIn('Content-Security-Policy-Report-Only', response.headers)

    def test_mode_is_validated(self):
        for value, expected in (('', None), ('enforce', 'enforce'), ('Report-Only', 'report-only'),
                                ('off', 'off')):
            with patch.dict('os.environ', {'ENERGY_CSP': value}):
                if expected is None:
                    with self.assertRaises(ValueError):
                        web._csp_mode_from_environment()
                else:
                    self.assertEqual(web._csp_mode_from_environment(), expected)
        with patch.dict('os.environ', {}, clear=True):
            self.assertEqual(web._csp_mode_from_environment(), 'enforce')

    def test_no_script_selects_elements_by_inline_handler(self):
        for path in [ROOT / 'web.py', *ROOT.glob('templates/*.html'), *ROOT.glob('static/*.js')]:
            self.assertNotIn('[onclick', path.read_text(), path.name)


if __name__ == '__main__':
    unittest.main()
