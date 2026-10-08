import io
import json
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

import ecosense


class EcoSenseTests(unittest.TestCase):
    def setUp(self):
        self.auth_patch = patch('ecosense.Cognito')
        self.auth = self.auth_patch.start()
        self.auth.return_value.id_token = 'private-token'
        self.client = ecosense.EcoSenseClient('account@example.com', 'private-password')

    def tearDown(self):
        self.auth_patch.stop()

    def test_bounded_request_and_header_authorization(self):
        response = MagicMock()
        response.status = 200
        response.read.return_value = json.dumps([{'serial_number': 'one', 'radon_level': 74}]).encode()
        response.__enter__.return_value = response
        with patch('ecosense.build_opener') as opener:
            opener.return_value.open.return_value = response
            self.assertEqual(self.client.get_devices()[0]['radon_level'], 74)
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(request.get_header('Authorization'), 'Bearer private-token')
            self.assertNotIn('private-token', request.full_url)
            self.assertNotIn('private-password', request.full_url)
            self.assertEqual(opener.return_value.open.call_args.kwargs['timeout'], 15)
            response.read.assert_called_once_with(ecosense.MAX_RESPONSE_BYTES + 1)

    def test_refreshes_401_once_and_does_not_retry_other_errors(self):
        unauthorized = HTTPError(ecosense.API_URL, 401, 'expired', {}, io.BytesIO())
        with patch.object(self.client, '_request_devices', side_effect=[unauthorized, []]) as request:
            self.assertEqual(self.client.get_devices(), [])
            self.assertEqual(request.call_count, 2)
            self.assertEqual(self.auth.return_value.authenticate.call_count, 2)
        denied = HTTPError(ecosense.API_URL, 403, 'denied', {}, io.BytesIO())
        with patch.object(self.client, '_request_devices', side_effect=denied) as request:
            with self.assertRaises(HTTPError):
                self.client.get_devices()
            self.assertEqual(request.call_count, 1)

    def test_diagnostic_does_not_fabricate_measurement_time_or_hide_zero(self):
        result = ecosense.describe_devices([{'serial_number': 'private-serial', 'radon_level': 0},
                                            {'radon_level': 'nan'}, {'radon_level': True}])
        self.assertEqual(result[0]['candidate_radon_bq_m3'], 0)
        self.assertIsNone(result[1]['candidate_radon_bq_m3'])
        self.assertIsNone(result[2]['candidate_radon_bq_m3'])
        self.assertFalse(result[0]['measurement_time_verified'])
        self.assertFalse(result[0]['history_ingested'])
        self.assertNotIn('private-serial', json.dumps(result))

    def test_oversize_or_wrong_shape_response_rejected(self):
        for body in (b'x' * (ecosense.MAX_RESPONSE_BYTES + 1), b'{}', b'[1]'):
            response = MagicMock()
            response.status = 200
            response.read.return_value = body
            response.__enter__.return_value = response
            with patch('ecosense.build_opener') as opener:
                opener.return_value.open.return_value = response
                with self.assertRaises(ValueError):
                    self.client.get_devices()
