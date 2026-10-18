import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import energy
import web
from werkzeug.middleware.proxy_fix import ProxyFix
from panel_model import breaker_load
from runtime_store import write_private_json


class ReleaseHardeningTests(unittest.TestCase):
    def test_private_write_replaces_atomically_and_keeps_previous_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            write_private_json(path, {'old': True})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with patch('runtime_store.os.replace', side_effect=OSError('disk failure')):
                with self.assertRaises(OSError):
                    write_private_json(path, {'new': True})
            self.assertEqual(json.loads(path.read_text()), {'old': True})
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_unconfigured_rating_does_not_create_a_capacity_warning(self):
        load = breaker_load(4000, None, 1)
        self.assertFalse(load['rating_known'])
        self.assertEqual(load['safe_cls'], '')
        self.assertEqual(load['load_label'], 'Rating not set')
        self.assertEqual(load['safe_bar_pct'], 0)

    def test_configured_two_pole_rating_uses_240_volts(self):
        load = breaker_load(3840, 20, 2)
        self.assertEqual(load['load_label'], '16.0/20A')
        self.assertEqual(load['safe_cls'], 'danger')
        self.assertEqual(load['safe_bar_pct'], 100)

    def test_foreign_origin_and_rebinding_host_cannot_change_settings(self):
        client = web.app.test_client()
        with patch.object(energy, '_write_json_file') as write:
            for headers in ({'Origin': 'https://untrusted.example'},
                            {'Sec-Fetch-Site': 'cross-site'},
                            {'Host': 'untrusted.example'}):
                response = client.post('/api/settings/config', json={'rate_cents': 12}, headers=headers)
                self.assertEqual(response.status_code, 403)
            write.assert_not_called()

    def test_public_origin_requires_https_and_preserves_same_origin_protection(self):
        previous_wsgi = web.app.wsgi_app
        web.app.wsgi_app = ProxyFix(previous_wsgi, x_for=0, x_proto=1, x_host=1,
                                    x_port=0, x_prefix=0)
        try:
            with patch.object(web, 'PUBLIC_ORIGIN', 'https://energy.stoverparc.org'):
                client = web.app.test_client()
                headers = {'Host': '127.0.0.1:5051',
                           'X-Forwarded-Host': 'energy.stoverparc.org',
                           'X-Forwarded-Proto': 'https'}
                self.assertEqual(client.get('/api/version', headers=headers).status_code, 200)
                self.assertEqual(client.get('/api/version', headers={
                    **headers, 'X-Forwarded-Proto': 'http',
                }).status_code, 403)
                with patch.object(energy, '_write_json_file') as write:
                    response = client.post('/api/settings/config', json={'rate_cents': 12},
                                           headers={**headers,
                                                    'Origin': 'https://attacker.example'})
                    self.assertEqual(response.status_code, 403)
                    write.assert_not_called()
        finally:
            web.app.wsgi_app = previous_wsgi

    def test_public_origin_configuration_must_be_exact_https_origin(self):
        for invalid in ('http://energy.stoverparc.org',
                        'https://energy.stoverparc.org/settings',
                        'https://user@energy.stoverparc.org',
                        'https://energy.stoverparc.org:8443'):
            with self.subTest(origin=invalid), patch.dict(os.environ,
                                                         {'ENERGY_PUBLIC_ORIGIN': invalid}):
                with self.assertRaises(ValueError):
                    web._public_origin_from_environment()

    def test_tailnet_origin_requires_exact_identity_and_same_origin(self):
        origin = 'https://collector.example.ts.net:8445'
        with patch.object(web, 'TAILNET_ORIGIN', origin), patch.object(web, 'TAILNET_USER', 'owner@example.org'):
            client = web.app.test_client()
            for user in (None, 'other@example.org'):
                headers = {} if user is None else {'Tailscale-User-Login': user}
                self.assertEqual(client.get('/api/version', base_url=origin,
                                            headers=headers).status_code, 403)
            headers = {'Tailscale-User-Login': 'owner@example.org'}
            self.assertEqual(client.get('/api/version', base_url=origin, headers=headers).status_code, 200)
            self.assertEqual(client.get('/api/version', base_url=origin.replace('https:', 'http:'),
                                        headers=headers).status_code, 403)
            with patch.object(energy, '_write_json_file') as write:
                response = client.post('/api/settings/config', base_url=origin, json={'rate_cents': 12},
                                       headers={**headers, 'Origin': 'https://attacker.example'})
                self.assertEqual(response.status_code, 403)
                write.assert_not_called()

    def test_tailnet_configuration_rejects_incomplete_and_unsafe_origins(self):
        for origin, user in (('https://collector.example.ts.net', ''),
                             ('', 'owner@example.org'),
                             ('http://collector.example.ts.net', 'owner@example.org'),
                             ('https://public.example.org', 'owner@example.org'),
                             ('https://collector.example.ts.net/path', 'owner@example.org'),
                             ('https://x@collector.example.ts.net', 'owner@example.org')):
            with self.subTest(origin=origin), patch.dict(os.environ, {
                    'ENERGY_TAILNET_ORIGIN': origin, 'ENERGY_TAILNET_USER': user}):
                with self.assertRaises(ValueError):
                    web._tailnet_access_from_environment()
        with patch.dict(os.environ, {'ENERGY_TAILNET_ORIGIN': 'https://collector.example.ts.net:8445',
                                     'ENERGY_TAILNET_USER': 'owner@example.org'}):
            self.assertEqual(web._tailnet_access_from_environment(),
                             ('https://collector.example.ts.net:8445', 'owner@example.org'))

    def test_mutations_require_json_objects(self):
        client = web.app.test_client()
        self.assertEqual(client.post('/api/settings/config', data='{}', content_type='text/plain').status_code, 415)
        for value in ([], None, 'bad'):
            response = client.post('/api/panel-layout', data=json.dumps(value), content_type='application/json')
            self.assertEqual(response.status_code, 400)

    def test_invalid_later_slot_does_not_partially_save_panel(self):
        client = web.app.test_client()
        with patch.object(energy, 'save_panel_layout') as save:
            response = client.post('/api/panel-layout', json={'slots': [
                {'slot': 1, 'amps': 20}, {'slot': 2, 'amps': -1},
            ]})
            self.assertEqual(response.status_code, 400)
            save.assert_not_called()

    def test_settings_writes_invalidate_dashboard(self):
        client = web.app.test_client()
        with patch.object(energy, '_write_json_file'), patch.object(web, '_refresh_runtime_config'):
            web._dashboard_cache['common'] = {'stale': True}
            response = client.post('/api/settings/config', json={'rate_cents': 12},
                                   headers={'Origin': 'http://localhost'})
            self.assertEqual(response.status_code, 200)
            self.assertIsNone(web._dashboard_cache['common'])

    def test_panel_transaction_rolls_back_on_database_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            previous = energy.DB_PATH
            energy.DB_PATH = os.path.join(directory, 'test.db')
            try:
                energy.ensure_table()
                with self.assertRaises((TypeError, ValueError, sqlite3.Error)):
                    energy.save_panel_layout([{'slot': 1, 'label': 'first'},
                                              {'slot': 2, 'label': {'bad': 'type'}}])
                self.assertEqual(energy.get_panel_layout(), [])
            finally:
                energy.DB_PATH = previous
