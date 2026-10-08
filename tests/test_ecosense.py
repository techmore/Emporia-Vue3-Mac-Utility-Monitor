import io
import json
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

import ecosense


class EcoSenseTests(unittest.TestCase):
    def test_candidate_point_seven_does_not_imply_a_unit_or_ingestion(self):
        result = ecosense.describe_devices([{'radon_level': 0.7}])[0]
        self.assertEqual(result['candidate_radon_value'], 0.7)
        self.assertFalse(result['measurement_unit_verified'])
        self.assertFalse(result['history_ingested'])
        self.assertNotIn('candidate_radon_bq_m3', result)

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
        self.assertEqual(result[0]['candidate_radon_value'], 0)
        self.assertIsNone(result[1]['candidate_radon_value'])
        self.assertIsNone(result[2]['candidate_radon_value'])
        self.assertFalse(result[0]['measurement_unit_verified'])
        self.assertNotIn('candidate_radon_bq_m3', result[0])
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

    def test_interactive_login_does_not_print_or_persist_credentials(self):
        output = io.StringIO()
        with patch('sys.argv', ['ecosense.py', '--login']), \
             patch('builtins.input', return_value='account@example.com'), \
             patch('ecosense.getpass.getpass', return_value='private-password'), \
             patch('ecosense.EcoSenseClient') as client, patch('sys.stdout', output):
            client.return_value.get_devices.return_value = []
            ecosense.main()
            client.assert_called_once_with('account@example.com', 'private-password')
        self.assertNotIn('private-password', output.getvalue())
        self.assertNotIn('account@example.com', output.getvalue())
        self.assertEqual(json.loads(output.getvalue()), {'devices': []})

    def test_timestamp_candidates_are_normalized_but_never_verified(self):
        result = ecosense.describe_devices([{
            'radon_level': 25.9, 'serial_number': 'private-serial',
            'timestamp': '2026-10-07T20:00:00-04:00',
            'updated_at': '2026-10-08T00:01:00Z',
            'measured_at': '2026-10-07T20:00:00',
            'last_seen': 'private-token', 'created_at': 1234567890,
            'email': 'private@example.com', 'password': 'private-password',
        }])[0]
        self.assertEqual(result['candidate_timestamps_utc'], {
            'timestamp': '2026-10-08T00:00:00+00:00',
            'updated_at': '2026-10-08T00:01:00+00:00'})
        self.assertFalse(result['measurement_time_verified'])
        self.assertFalse(result['history_ingested'])
        output = json.dumps(result)
        for private in ('private-serial', 'private-token', 'private@example.com', 'private-password'):
            self.assertNotIn(private, output)

    def test_timestamp_normalization_overflow_is_omitted(self):
        for timestamp in ('0001-01-01T00:00:00+14:00', '9999-12-31T23:59:59-14:00'):
            with self.subTest(timestamp=timestamp):
                result = ecosense.describe_devices([{'timestamp': timestamp, 'radon_level': 25.9}])[0]
                self.assertEqual(result['candidate_timestamps_utc'], {})
                self.assertEqual(result['candidate_radon_value'], 25.9)
                self.assertFalse(result['measurement_time_verified'])
