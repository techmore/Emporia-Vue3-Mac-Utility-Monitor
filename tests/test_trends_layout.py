import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

import energy
import web


class PageContainment(HTMLParser):
    def __init__(self):
        super().__init__()
        self.divs = []
        self.inside = {}
        self.section_headings = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'div':
            self.divs.append('page' in attrs.get('class', '').split())
        if attrs.get('id') in {'trendChart', 'hourlyChart', 'month-comparison', 'load-review'}:
            self.inside[attrs['id']] = any(self.divs)
        if tag == 'h2':
            self.section_headings.append(any(self.divs))

    def handle_endtag(self, tag):
        if tag == 'div' and self.divs:
            self.divs.pop()


class TrendsLayoutTests(unittest.TestCase):
    def test_charts_and_lower_sections_remain_inside_page_wrapper(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(energy, 'DB_PATH', str(Path(directory) / 'trends.db')):
            energy.ensure_table()
            response = web.app.test_client().get('/trends')
        self.assertEqual(response.status_code, 200)
        parser = PageContainment()
        parser.feed(response.get_data(as_text=True))
        self.assertEqual(parser.inside, {key: True for key in (
            'trendChart', 'hourlyChart', 'month-comparison')})
        self.assertGreaterEqual(len(parser.section_headings), 5)
        self.assertTrue(all(parser.section_headings))
