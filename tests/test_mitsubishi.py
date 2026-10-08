import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import energy
import mitsubishi
import mitsubishi_collect
import web
from runtime_store import write_private_json


class ComfortTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db = patch.object(energy, 'DB_PATH', str(Path(self.directory.name) / 'test.db'))
        self.db.start()
        energy.ensure_table()
        self.client = web.app.test_client()
        self.headers = {'Origin': 'http://localhost'}
        self.tokens = {'access': 'PRIVATE_ACCESS', 'refresh': 'PRIVATE_REFRESH'}

    def tearDown(self):
        self.db.stop()
        self.directory.cleanup()

    def responses(self, connected=True):
        return [{'token': self.tokens}, [{'id': 'site'}], [{
            'name': '<img src=x>', 'adapter': {'deviceSerial': 'fixture',
                'connected': connected, 'power': 0, 'roomTemp': 0, 'spHeat': 20, 'spCool': 25,
                'operationMode': 'heat', 'password': 'DO_NOT_STORE',
                'cryptoSerial': 'DO_NOT_STORE'}}]]

    def connect(self, connected=True):
        with patch.object(mitsubishi, '_request', side_effect=self.responses(connected)) as request:
            result = mitsubishi.connect('user@example.com', 'PRIVATE_PASSWORD')
        self.assertEqual([call.args[0] for call in request.call_args_list], ['POST', 'GET', 'GET'])
        return result

    def test_connect_keeps_only_tokens_and_sanitized_snapshot(self):
        result = self.connect()
        self.assertEqual(result['units'][0]['temperature_c'], 0)
        self.assertIs(result['units'][0]['is_on'], False)
        config = mitsubishi._path('private.json')
        self.assertEqual(config.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(config.read_text()), self.tokens)
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertNotIn('DO_NOT_STORE', json.dumps(mitsubishi.history()))
        self.assertEqual(len(mitsubishi.history()), 1)

    def test_disconnected_never_displays_cached_temperatures(self):
        unit = self.connect(False)['units'][0]
        self.assertIsNone(unit['temperature_c'])
        self.assertIsNone(unit['mode'])
        self.assertIsNone(mitsubishi._number(True))
        self.assertIsNone(mitsubishi._number(float('nan')))

    def test_remove_stops_requests_retains_history(self):
        self.connect()
        with patch.object(mitsubishi, '_request') as request:
            mitsubishi.remove()
            self.assertFalse(mitsubishi.poll()['enabled'])
            request.assert_not_called()
        self.assertFalse(mitsubishi._path('private.json').exists())
        self.assertEqual(len(mitsubishi.history()), 1)

    def test_rejected_reconnect_preserves_existing_connection(self):
        self.connect()
        original = mitsubishi._path('private.json').read_text()
        with patch.object(mitsubishi, '_request', side_effect=mitsubishi.ComfortError('authentication_rejected')):
            with self.assertRaises(mitsubishi.ComfortError):
                mitsubishi.connect('new@example.com', 'other-password')
        self.assertEqual(mitsubishi._path('private.json').read_text(), original)

    def test_stale_snapshot_is_not_live_and_failed_poll_is_unknown(self):
        self.connect()
        data = mitsubishi.status()
        data['queried_at'] = (datetime.now(timezone.utc) - timedelta(minutes=4)).isoformat()
        write_private_json(mitsubishi._path('status.json'), data)
        self.assertEqual(mitsubishi.status()['state'], 'stale')
        self.assertEqual(mitsubishi.status()['units'], [])
        with patch.object(mitsubishi, '_request', side_effect=RuntimeError('SECRET')):
            result = mitsubishi.poll()
        self.assertEqual(result['units'], [])
        self.assertNotIn('SECRET', json.dumps(result))

    def test_refresh_rotation_is_persisted(self):
        self.connect()
        refreshed = {'access': 'NEW_ACCESS', 'refresh': 'NEW_REFRESH'}
        with patch.object(mitsubishi, '_request', side_effect=[
            mitsubishi.ComfortError('authentication_rejected'), refreshed,
            [{'id': 'site'}], self.responses()[2]]):
            self.assertEqual(mitsubishi.poll()['state'], 'connected')
        self.assertEqual(json.loads(mitsubishi._path('private.json').read_text()), refreshed)

    def test_routes_require_origin_and_hide_exception_details(self):
        payload = {'email': 'user@example.com', 'password': 'SECRET'}
        with patch.object(mitsubishi, 'connect', side_effect=RuntimeError('SECRET')) as connect:
            self.assertEqual(self.client.post('/api/mitsubishi', json=payload).status_code, 403)
            connect.assert_not_called()
            response = self.client.post('/api/mitsubishi', json=payload, headers=self.headers)
            self.assertEqual(response.status_code, 502)
            self.assertNotIn('SECRET', response.get_data(as_text=True))
        self.assertEqual(self.client.delete('/api/mitsubishi', json={}, headers=self.headers).status_code, 400)
        self.assertEqual(self.client.post('/api/mitsubishi', json={'email':'x'*9000}, headers=self.headers).status_code, 413)

    def test_page_has_removal_and_escapes_history(self):
        self.connect()
        response = self.client.get('/mitsubishi')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('&lt;img src=x&gt;', html)
        self.assertIn('Remove module connection', html)
        self.assertNotIn('PRIVATE_ACCESS', html)
        self.assertIn('no-store', self.client.get('/api/mitsubishi').headers['Cache-Control'])

    def test_busy_lock_prevents_mutation(self):
        with mitsubishi._lock(), self.assertRaises(mitsubishi.ComfortError):
            mitsubishi.remove()

    def test_http_wrapper_forbids_redirects_and_limits_responses(self):
        session = MagicMock()
        session.__enter__.return_value = session
        response = MagicMock()
        response.__enter__.return_value = response
        session.request.return_value = response
        response.status_code = 302
        with patch.object(mitsubishi.requests, 'Session', return_value=session):
            with self.assertRaises(mitsubishi.ComfortError):
                mitsubishi._request('GET', '/v3/sites/', 'SECRET')
        self.assertFalse(session.request.call_args.kwargs['allow_redirects'])
        self.assertFalse(session.trust_env)
        response.status_code = 200
        response.iter_content.return_value = [b'x' * 262145]
        with patch.object(mitsubishi.requests, 'Session', return_value=session):
            with self.assertRaises(mitsubishi.ComfortError):
                mitsubishi._request('GET', '/v3/sites/')

    def test_malformed_discovery_does_not_save_connection(self):
        with patch.object(mitsubishi, '_request', side_effect=[{'token': self.tokens}, {'wrong':'shape'}]):
            with self.assertRaises(mitsubishi.ComfortError):
                mitsubishi.connect('user@example.com', 'password')
        self.assertFalse(mitsubishi._path('private.json').exists())

    def test_collector_backs_off_rejected_authentication(self):
        self.connect()
        with patch.object(sys, 'argv', ['mitsubishi_collect.py']), \
                patch.object(mitsubishi, 'poll', return_value={'state':'authentication_rejected'}) as poll, \
                patch.object(mitsubishi_collect, 'monotonic', return_value=0), \
                patch.object(mitsubishi_collect, 'sleep', side_effect=[None, KeyboardInterrupt]):
            with self.assertRaises(KeyboardInterrupt):
                mitsubishi_collect.main()
        self.assertEqual(poll.call_count, 1)

    def test_reconnect_bypasses_collector_auth_backoff(self):
        self.connect()
        sleeps = []
        def sleep(_):
            sleeps.append(True)
            if len(sleeps) == 1:
                import os
                path = mitsubishi._path('private.json')
                stamp = path.stat().st_mtime_ns + 1_000_000
                os.utime(path, ns=(stamp, stamp))
            else:
                raise KeyboardInterrupt
        with patch.object(sys, 'argv', ['mitsubishi_collect.py']), \
                patch.object(mitsubishi, 'poll', return_value={'state':'authentication_rejected'}) as poll, \
                patch.object(mitsubishi_collect, 'monotonic', return_value=0), \
                patch.object(mitsubishi_collect, 'sleep', side_effect=sleep):
            with self.assertRaises(KeyboardInterrupt):
                mitsubishi_collect.main()
        self.assertEqual(poll.call_count, 2)
