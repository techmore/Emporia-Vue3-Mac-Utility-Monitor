import unittest
from unittest.mock import patch

from flask import current_app

import web


class EventStreamTests(unittest.TestCase):
    def test_event_stream_keeps_context_without_prior_dashboard_request(self):
        calls = 0

        def uncached_payload():
            nonlocal calls
            self.assertEqual(current_app.name, web.app.name)
            calls += 1
            return {'rebuild': calls}

        with patch.object(web, '_build_live_dashboard_payload', side_effect=uncached_payload), \
             patch.object(web.time, 'sleep'):
            response = web.app.test_client().get('/api/events', buffered=False)
            try:
                self.assertEqual(response.status_code, 200)
                self.assertIn(b'"rebuild":1', next(response.response))
                self.assertIn(b'"rebuild":2', next(response.response))
            finally:
                response.close()
