import unittest
from unittest.mock import patch

from flask import current_app

import web


class EventStreamTests(unittest.TestCase):
    def test_event_stream_keeps_context_without_prior_dashboard_request(self):
        def uncached_payload():
            self.assertEqual(current_app.name, web.app.name)
            return {'cold_start': True}

        with patch.object(web, '_build_live_dashboard_payload', side_effect=uncached_payload):
            response = web.app.test_client().get('/api/events', buffered=False)
            try:
                self.assertEqual(response.status_code, 200)
                self.assertIn(b'"cold_start":true', next(response.response))
            finally:
                response.close()
