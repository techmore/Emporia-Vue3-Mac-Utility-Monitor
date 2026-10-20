import builtins
import os
import unittest
from unittest.mock import patch

from werkzeug.middleware.proxy_fix import ProxyFix

import energy
import web


class ProductionServerTests(unittest.TestCase):
    def test_serve_uses_waitress_on_loopback_with_streaming_settings(self):
        with patch.dict(os.environ, {}, clear=False), patch('waitress.serve') as waitress_serve, \
                patch.object(web.app, 'run') as dev_server:
            os.environ.pop('ENERGY_DEV_SERVER', None)
            os.environ.pop('WEB_THREADS', None)
            web.serve('127.0.0.1', 5051)
        dev_server.assert_not_called()
        args, kwargs = waitress_serve.call_args
        self.assertIs(args[0], web.app)
        self.assertEqual(kwargs['host'], '127.0.0.1')
        self.assertEqual(kwargs['port'], 5051)
        self.assertEqual(kwargs['threads'], 32)
        # Server-sent events must flush immediately rather than wait for a full buffer.
        self.assertEqual(kwargs['send_bytes'], 1)
        self.assertEqual(kwargs['ident'], '')

    def test_serve_refuses_non_loopback_binding(self):
        with patch('waitress.serve') as waitress_serve:
            with self.assertRaises(ValueError):
                web.serve('0.0.0.0', 5051)
        waitress_serve.assert_not_called()

    def test_thread_count_is_validated(self):
        for value in ('abc', '2', '1000'):
            with patch.dict(os.environ, {'WEB_THREADS': value}), patch('waitress.serve') as waitress_serve:
                with self.assertRaises(ValueError):
                    web.serve('127.0.0.1', 5051)
                waitress_serve.assert_not_called()
        with patch.dict(os.environ, {'WEB_THREADS': '64'}), patch('waitress.serve') as waitress_serve:
            web.serve('127.0.0.1', 5051)
        self.assertEqual(waitress_serve.call_args.kwargs['threads'], 64)

    def test_dev_server_is_explicit_opt_in(self):
        with patch.dict(os.environ, {'ENERGY_DEV_SERVER': '1'}), patch('waitress.serve') as waitress_serve, \
                patch.object(web.app, 'run') as dev_server:
            web.serve('127.0.0.1', 5051)
        waitress_serve.assert_not_called()
        dev_server.assert_called_once()
        self.assertEqual(dev_server.call_args.kwargs['host'], '127.0.0.1')

    def test_missing_waitress_falls_back_loudly_instead_of_failing_to_start(self):
        real_import = builtins.__import__

        def no_waitress(name, *args, **kwargs):
            if name == 'waitress':
                raise ImportError(name)
            return real_import(name, *args, **kwargs)

        with patch.dict(os.environ, {}, clear=False), patch('builtins.__import__', no_waitress), \
                patch.object(web.app, 'run') as dev_server, \
                self.assertLogs(web.app.logger, level='ERROR') as logs:
            os.environ.pop('ENERGY_DEV_SERVER', None)
            web.serve('127.0.0.1', 5051)
        dev_server.assert_called_once()
        self.assertIn('requirements.lock', ''.join(logs.output))


class ResponseHardeningTests(unittest.TestCase):
    def test_security_headers_on_pages_and_no_store_on_api(self):
        client = web.app.test_client()
        page = client.get('/api/version')
        for header in ('X-Content-Type-Options', 'X-Frame-Options', 'Referrer-Policy',
                       'Cross-Origin-Opener-Policy', 'Permissions-Policy'):
            self.assertIn(header, page.headers)
        self.assertEqual(page.headers['Cache-Control'], 'no-store')
        # Plain HTTP on loopback must never advertise HSTS.
        self.assertNotIn('Strict-Transport-Security', page.headers)

    def test_api_cache_policy_set_by_a_route_is_preserved(self):
        response = web.app.test_client().get('/api/events', buffered=False)
        try:
            self.assertEqual(response.headers['Cache-Control'], 'no-cache')
        finally:
            response.close()

    def test_hsts_is_host_only_through_the_https_proxy(self):
        previous = web.app.wsgi_app
        web.app.wsgi_app = ProxyFix(previous, x_for=0, x_proto=1, x_host=1, x_port=0, x_prefix=0)
        try:
            with patch.object(web, 'PUBLIC_ORIGIN', 'https://energy.stoverparc.org'):
                response = web.app.test_client().get('/api/version', headers={
                    'Host': '127.0.0.1:5051', 'X-Forwarded-Host': 'energy.stoverparc.org',
                    'X-Forwarded-Proto': 'https'})
        finally:
            web.app.wsgi_app = previous
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Strict-Transport-Security'], 'max-age=31536000')

    def test_health_reports_database_and_poller_without_secrets(self):
        response = web.app.test_client().get('/api/health')
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body['ok'])
        self.assertEqual(body['database'], 'ok')
        self.assertEqual(body['version'], web.VERSION)
        self.assertEqual(set(body['poller']), {'running', 'heartbeat_age_secs'})

    def test_health_is_unavailable_when_the_database_cannot_open(self):
        with patch.object(energy, '_connect', side_effect=OSError('disk gone')), \
                self.assertLogs(web.app.logger, level='ERROR'):
            response = web.app.test_client().get('/api/health')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()['database'], 'unavailable')
        self.assertNotIn('disk gone', response.get_data(as_text=True))

    def test_reconnect_failure_does_not_expose_filesystem_details(self):
        with patch('builtins.open', side_effect=OSError('/private/path/reconnect.flag denied')), \
                self.assertLogs(web.app.logger, level='ERROR'):
            response = web.app.test_client().post('/api/poller-reconnect', json={})
        self.assertEqual(response.status_code, 500)
        self.assertNotIn('/private/path', response.get_data(as_text=True))


if __name__ == '__main__':
    unittest.main()
