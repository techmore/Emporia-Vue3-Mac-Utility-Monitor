import io
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import energy
import web


@unittest.skipUnless(shutil.which('node'), 'Node is required to execute the import UI')
class ImportUITests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        database = patch.object(energy, 'DB_PATH', str(Path(directory.name) / 'energy.db'))
        database.start()
        self.addCleanup(database.stop)
        energy.ensure_table()
        self.client = web.app.test_client()
        response = self.client.get('/import')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        scripts = re.findall(r'<script>(.*?)</script>', html, re.S)
        self.script, = [script for script in scripts if "fetch('/api/import-csv'" in script]
        self.escape_script = re.search(r'window.escapeHTML = function\(value\) \{.*?\n\};', html, re.S).group()

    def run_ui(self, responses):
        runner = Path(__file__).resolve().parents[1] / 'scripts/test_import_ui.cjs'
        result = subprocess.run(
            [shutil.which('node'), str(runner)],
            input=json.dumps({'script': self.script, 'escapeScript': self.escape_script,
                              'responses': responses}),
            text=True, capture_output=True, timeout=10, check=True,
        )
        state = json.loads(result.stdout)
        self.assertFalse(state['disabled'])
        self.assertEqual(state['textContent'], 'Import')
        self.assertEqual(state['calls'], len(responses))
        self.assertNotIn('undefined', state['html'])
        return state['html']

    def assert_failed(self, response, message):
        html = self.run_ui([response])
        self.assertIn('import-result err', html)
        self.assertNotIn('import-result ok', html)
        self.assertIn(message, html)
        return html

    def api_response(self, response):
        return {'status': response.status_code, 'body': response.get_json()}

    def test_missing_file_and_wrong_extension_show_actual_400_error(self):
        responses = [
            self.client.post('/api/import-csv'),
            self.client.post('/api/import-csv', data={'file': (io.BytesIO(b'x'), 'panel.txt')}),
        ]
        for response in responses:
            with self.subTest(body=response.get_json()):
                self.assertEqual(response.status_code, 400)
                self.assert_failed(self.api_response(response), response.get_json()['error'])

    def test_oversized_upload_shows_413_limit(self):
        with patch.dict(web.app.config, MAX_CONTENT_LENGTH=512):
            response = self.client.post('/api/import-csv', data={
                'file': (io.BytesIO(b'x' * 1024), 'panel-1H.csv'),
            })
        self.assertEqual(response.status_code, 413)
        self.assert_failed(self.api_response(response), 'Upload too large')

    def test_same_origin_rejection_shows_403(self):
        response = self.client.post('/api/import-csv', headers={'Origin': 'https://other.example'})
        self.assertEqual(response.status_code, 403)
        self.assert_failed(self.api_response(response), 'Same-origin request required')

    def test_publication_failure_shows_safe_500_message(self):
        with patch.object(energy, 'import_emporia_csv', side_effect=RuntimeError('private-token')):
            response = self.client.post('/api/import-csv', data={
                'file': (io.BytesIO(b'x'), 'panel-1H.csv'),
            })
        self.assertEqual(response.status_code, 500)
        html = self.assert_failed(self.api_response(response), 'no changes were published')
        self.assertNotIn('private-token', html)

    def test_proxy_html_error_preserves_http_status(self):
        self.assert_failed({'status': 502, 'invalidJSON': True}, 'HTTP 502')

    def test_non_success_status_overrides_success_shaped_counts(self):
        self.assert_failed({'status': 500, 'body': {
            'imported': 0, 'skipped': 0, 'errors': 0,
        }}, 'HTTP 500')
        self.assert_failed({'status': 404, 'body': None}, 'HTTP 404')

    def test_review_warnings_and_retained_sources_are_not_clean_success(self):
        html = self.assert_failed({'status': 200, 'body': {
            'imported': 0, 'skipped': 60, 'errors': 0, 'warnings': 1,
            'observations_recorded': 60, 'message': 'Source evidence retained; review conflicts.',
        }}, 'review warnings 1')
        self.assertIn('source observations retained 60', html)
        for field in ('warnings', 'superseded', 'updated', 'inserted', 'deleted', 'observations_recorded'):
            self.assert_failed({'status': 200, 'body': {
                'imported': 1, 'skipped': 0, 'errors': 0, field: '<img src=x>',
            }}, 'Invalid import response')

    def test_reactivated_archived_projection_is_visible_even_when_upload_rows_are_skipped(self):
        html = self.run_ui([{'status': 200, 'body': {
            'imported': 0, 'skipped': 1, 'errors': 0, 'observations_recorded': 0,
            'inserted': 60, 'updated': 0, 'deleted': 0,
        }}])
        self.assertIn('projection: inserted 60, updated 0, removed 0', html)

    def test_invalid_success_response_cannot_be_green(self):
        for body in (None, [], {'error': 'unexpected'},
                     {'imported': -1, 'skipped': 0, 'errors': 0},
                     {'imported': '<img src=x>', 'skipped': 0, 'errors': 0},
                     {'imported': 1, 'skipped': 0.5, 'errors': 0},
                     {'imported': 1, 'skipped': 0, 'errors': False}):
            with self.subTest(body=body):
                self.assert_failed({'status': 200, 'body': body}, 'Invalid import response')
        self.assert_failed({'status': 200, 'invalidJSON': True}, 'Invalid import response')

    def test_partial_import_keeps_counts_and_error_state(self):
        csv = (b'Time Bucket (America/New_York),QA-Pump (kWhs)\n'
               b'10/08/2026 12:00:00,3\n10/08/2026 13:00:00,NaN\n')
        response = self.client.post('/api/import-csv', data={
            'file': (io.BytesIO(csv), 'QA-Panel-1H.csv'),
        })
        self.assertEqual(response.status_code, 200)
        self.assert_failed(self.api_response(response), 'imported 1, skipped 0, errors 1')
        conn = energy._connect()
        try:
            self.assertEqual(conn.execute('SELECT SUM(usage_kwh) FROM readings').fetchone()[0], 3)
        finally:
            conn.close()

    def test_error_markup_is_escaped_and_next_file_still_imports(self):
        html = self.run_ui([
            {'status': 400, 'body': {'error': '<img src=x onerror=alert(1)>'},
             'filename': '<script>alert(1)</script>.csv'},
            {'status': 200, 'body': {'imported': 2, 'skipped': 1, 'errors': 0}},
        ])
        self.assertEqual(html.count('import-result err'), 1)
        self.assertEqual(html.count('import-result ok'), 1)
        self.assertIn('imported 2, skipped 1, errors 0', html)
        self.assertNotIn('<img', html)
        self.assertNotIn('<script', html)

    def test_success_message_cannot_inject_markup(self):
        html = self.run_ui([{'status': 200, 'body': {
            'imported': 0, 'skipped': 1, 'errors': 0, 'message': '<img src=x>',
        }}])
        self.assertIn('import-result ok', html)
        self.assertIn('&lt;img src=x&gt;', html)
        self.assertNotIn('<img', html)

    def test_transport_failure_does_not_claim_database_rollback(self):
        self.assert_failed({'networkError': 'Failed to fetch'}, 'outcome unknown')


if __name__ == '__main__':
    unittest.main()
