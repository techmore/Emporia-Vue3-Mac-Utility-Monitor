import hashlib
import json
import re
import unittest
from pathlib import Path

import web

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / 'static' / 'vendor'
PAGES = ('/', '/reports', '/guide', '/recommendations', '/circuits', '/trends', '/log', '/house',
         '/home-3d', '/radon', '/panel/photos', '/kasa', '/mitsubishi', '/aqara', '/settings')
# Anything a browser would fetch and execute or apply: scripts, stylesheets, fonts, @import.
EXTERNAL_LOAD = re.compile(
    r'''<script[^>]+src=["']?(?:https?:)?//|<link[^>]+href=["']?(?:https?:)?//'''
    r'''|@import\s+url\(\s*["']?(?:https?:)?//|url\(\s*["']?(?:https?:)?//[^)]*\.(?:woff2?|ttf|otf)''',
    re.IGNORECASE)


class VendoredAssetTests(unittest.TestCase):
    def test_recorded_hashes_match_and_every_file_is_recorded(self):
        record = json.loads((VENDOR / 'UPSTREAM.json').read_text())
        on_disk = {path.relative_to(VENDOR).as_posix() for path in VENDOR.rglob('*')
                   if path.is_file() and path.name != 'UPSTREAM.json'}
        self.assertEqual(set(record['files']), on_disk)
        for name, digest in record['files'].items():
            self.assertEqual(hashlib.sha256((VENDOR / name).read_bytes()).hexdigest(), digest, name)
        # Exact pins only, never a floating major like chart.js@4.
        for spec in record['packages']:
            self.assertRegex(spec, r'@\d+\.\d+\.\d+$')

    def test_font_css_references_only_local_files_that_exist(self):
        css = (VENDOR / 'fonts.css').read_text()
        urls = re.findall(r'url\(([^)]+)\)', css)
        self.assertTrue(urls)
        for url in urls:
            self.assertTrue(url.startswith('/static/vendor/fonts/'), url)
            self.assertTrue((ROOT / url.lstrip('/')).is_file(), url)
        for family in ("'Inter'", "'Instrument Serif'"):
            self.assertIn(family, css)

    def test_pages_load_no_third_party_scripts_styles_or_fonts(self):
        client = web.app.test_client()
        for path in PAGES:
            response = client.get(path, follow_redirects=True)
            self.assertEqual(response.status_code, 200, path)
            match = EXTERNAL_LOAD.search(response.get_data(as_text=True))
            self.assertIsNone(match, f'{path} loads an external asset: {match and match.group(0)}')

    def test_chart_pages_use_the_local_chart_build_and_it_is_served(self):
        client = web.app.test_client()
        page = client.get('/trends', follow_redirects=True).get_data(as_text=True)
        self.assertIn('/static/vendor/chart.umd.min.js', page)
        for asset, kind in (('/static/vendor/chart.umd.min.js', 'javascript'),
                            ('/static/vendor/fonts.css', 'text/css'),
                            ('/static/vendor/fonts/inter-latin-opsz-normal.woff2', 'font/woff2')):
            response = client.get(asset)
            self.assertEqual(response.status_code, 200, asset)
            self.assertIn(kind, response.content_type, asset)
            response.close()


if __name__ == '__main__':
    unittest.main()
