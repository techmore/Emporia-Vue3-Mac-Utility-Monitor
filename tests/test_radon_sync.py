import io
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import energy
import radon
import sync_history
import web


class RadonJournalTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db = patch.object(energy, 'DB_PATH', str(Path(self.directory.name) / 'energy.db'))
        self.db.start()
        energy.ensure_table()
        self.row = {'source': 'ecosense', 'sensor_id': 'a', 'name': 'Basement',
                    'timestamp': datetime.now(timezone.utc).isoformat(),
                    'value': 0.7, 'unit': 'pCi/L'}

    def tearDown(self):
        self.db.stop()
        self.directory.cleanup()

    def test_insert_retry_and_deletion_are_ordered(self):
        radon.ingest_observations([self.row])
        radon.ingest_observations([self.row])
        conn = energy._connect()
        try:
            with conn:
                conn.execute('DELETE FROM radon_readings')
        finally:
            conn.close()
        first = radon.get_changes(limit=1)
        self.assertTrue(first['has_more'])
        self.assertEqual(first['changes'][0]['operation'], 'upsert')
        self.assertAlmostEqual(first['changes'][0]['radon_bq_m3'], 25.9)
        second = radon.get_changes(first['next_cursor'])
        self.assertEqual(second['changes'][0]['operation'], 'delete')
        self.assertEqual(second['changes'][0]['timestamp'], self.row['timestamp'])
        self.assertFalse(second['has_more'])
        self.assertEqual(first['source_id'], second['source_id'])

    def test_rollback_has_no_exported_changes(self):
        radon.ingest_observations([self.row])
        before = radon.get_changes()
        conn = energy._connect()
        try:
            conn.execute('DELETE FROM radon_readings')
            conn.rollback()
        finally:
            conn.close()
        self.assertEqual(radon.get_changes(), before)

    def test_identity_update_removes_old_key_before_new_observation(self):
        radon.ingest_observations([self.row])
        conn = energy._connect()
        try:
            with conn:
                conn.execute("UPDATE radon_readings SET sensor_id='b'")
        finally:
            conn.close()
        changes = radon.get_changes()['changes']
        self.assertEqual([(row['operation'], row['sensor_id']) for row in changes],
                         [('upsert', 'a'), ('delete', 'a'), ('upsert', 'b')])

    def test_existing_history_seeded_once(self):
        radon.ingest_observations([self.row])
        conn = energy._connect()
        try:
            with conn:
                conn.execute('DELETE FROM radon_changes')
                conn.execute("DELETE FROM migrations WHERE name='radon_sync_seed_v1'")
        finally:
            conn.close()
        energy.ensure_table()
        first = radon.get_changes()
        self.assertEqual(len(first['changes']), 1)
        energy.ensure_table()
        self.assertEqual(radon.get_changes(), first)

    def test_invalid_and_ahead_cursor_rejected(self):
        for after, limit in [(True, 1), (-1, 1), (10**400, 1), (1, 1), (0, 0), (0, 1001)]:
            with self.subTest(after=after, limit=limit), self.assertRaises(ValueError):
                radon.get_changes(after, limit)

    def test_export_requires_token_and_checks_collector_identity(self):
        client = web.app.test_client()
        with patch.dict('os.environ', {'ENERGY_SYNC_TOKEN': ''}):
            self.assertEqual(client.get('/api/sync/radon').status_code, 503)
        with patch.dict('os.environ', {'ENERGY_SYNC_TOKEN': 'a' * 32}):
            self.assertEqual(client.get('/api/sync/radon').status_code, 401)
            headers = {'Authorization': 'Bearer ' + 'a' * 32}
            radon.ingest_observations([self.row])
            response = client.get('/api/sync/radon?limit=1', headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            self.assertAlmostEqual(response.get_json()['changes'][0]['radon_bq_m3'], 25.9)
            self.assertEqual(client.get('/api/sync/radon?source_id=wrong',
                                        headers=headers).status_code, 409)
            for query in ('after=-1', 'after=unknown', 'limit=1001', 'after=' + '9' * 400):
                self.assertEqual(client.get('/api/sync/radon?' + query,
                                            headers=headers).status_code, 400)

    def test_cache_pages_publish_rows_and_cursor_atomically(self):
        radon.ingest_observations([self.row])
        page = radon.get_changes()
        with patch.object(energy, 'DB_PATH', str(Path(self.directory.name) / 'cache.db')):
            energy.ensure_table()
            invalid = deepcopy(page)
            invalid['changes'][0]['radon_bq_m3'] = 999
            with self.assertRaises(ValueError):
                radon.apply_changes(invalid)
            self.assertEqual(radon.get_cache_status()['cursor'], 0)
            state = radon.apply_changes(page)
            self.assertEqual(state['cursor'], page['next_cursor'])
            conn = energy._connect()
            try:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM radon_readings').fetchone()[0], 0)
                self.assertAlmostEqual(conn.execute('SELECT radon_bq_m3 FROM radon_cached_readings').fetchone()[0], 25.9)
            finally:
                conn.close()
            with self.assertRaises(ValueError):
                radon.apply_changes(page)

    def test_downloader_resumes_and_dashboard_reads_cache(self):
        radon.ingest_observations([self.row])
        page = radon.get_changes()
        with patch.object(energy, 'DB_PATH', str(Path(self.directory.name) / 'cache.db')):
            energy.ensure_table()
            with patch.object(sync_history, 'fetch_page', return_value=page) as fetch:
                state = sync_history.sync_radon_once('http://127.0.0.1', 'a' * 32)
                self.assertEqual(state['cursor'], page['next_cursor'])
                self.assertEqual(fetch.call_args.kwargs['stream'], 'radon')
            self.assertEqual(radon.get_sensors()[0]['sensor_id'], 'a')
            self.assertAlmostEqual(radon.get_history('ecosense', 'a')[0]['radon_bq_m3'], 25.9)
            html = web.app.test_client().get('/radon?source=ecosense&sensor_id=a').get_data(as_text=True)
            self.assertIn('Collector cache', html)
            self.assertIn('0.7 pCi/L', html)
            with self.assertRaises(ValueError):
                radon.ingest_observations([self.row])
            with patch.object(sync_history, 'fetch_page', side_effect=RuntimeError('offline')):
                with self.assertRaises(RuntimeError):
                    sync_history.sync_radon_once('http://127.0.0.1', 'a' * 32)
            self.assertEqual(radon.get_cache_status(), state)

    def test_sync_refuses_local_history_without_changing_it(self):
        radon.ingest_observations([self.row])
        page = radon.get_changes()
        with self.assertRaises(ValueError):
            radon.apply_changes(page)
        self.assertEqual(radon.get_cache_status()['cursor'], 0)
        self.assertEqual(len(radon.get_history('ecosense', 'a')), 1)

    def test_cache_write_failure_rolls_back_rows_and_cursor(self):
        radon.ingest_observations([self.row])
        page = radon.get_changes()
        with patch.object(energy, 'DB_PATH', str(Path(self.directory.name) / 'failed-write.db')):
            energy.ensure_table()
            conn = energy._connect()
            try:
                conn.execute('''CREATE TRIGGER reject_radon_state BEFORE INSERT ON radon_sync_cache_state
                    BEGIN SELECT RAISE(ABORT, 'simulated disk write failure'); END''')
                conn.commit()
            finally:
                conn.close()
            with self.assertRaisesRegex(sqlite3.IntegrityError, 'simulated disk write failure'):
                radon.apply_changes(page)
            self.assertEqual(radon.get_cache_status()['cursor'], 0)
            conn = energy._connect()
            try:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM radon_cached_readings').fetchone()[0], 0)
            finally:
                conn.close()

    def test_checkpoint_recovery_preserves_old_cache_on_failure_and_other_tables(self):
        radon.ingest_observations([self.row])
        initial = radon.get_changes()
        conn = energy._connect()
        try:
            with conn:
                conn.execute("UPDATE radon_readings SET name='Renamed'")
                conn.execute("UPDATE radon_readings SET name='Final'")
        finally:
            conn.close()
        self.assertTrue(radon.compact_journal(1))
        replacement = radon.get_changes()
        self.assertNotEqual(initial['generation_id'], replacement['generation_id'])
        self.assertEqual(len(replacement['changes']), 1)
        headers = {'Authorization': 'Bearer ' + 'a' * 32}
        with patch.dict('os.environ', {'ENERGY_SYNC_TOKEN': 'a' * 32}):
            response = web.app.test_client().get('/api/sync/radon?source_id=' + initial['source_id'] +
                '&generation_id=' + initial['generation_id'], headers=headers)
            self.assertEqual(response.status_code, 409)
            self.assertTrue(response.get_json()['reset_required'])
        cache = str(Path(self.directory.name) / 'recover.db')
        with patch.object(energy, 'DB_PATH', cache):
            energy.ensure_table()
            radon.apply_changes(initial)
            conn = energy._connect()
            try:
                with conn:
                    conn.execute("INSERT INTO circuit_labels(slot,label) VALUES (40,'Keep')")
            finally:
                conn.close()
            for failed in (True, False):
                error = urllib.error.HTTPError('http://localhost', 409, 'reset', {}, io.BytesIO(
                    json.dumps({'reset_required': True, 'source_id': initial['source_id']}).encode()))
                outcome = RuntimeError('offline') if failed else replacement
                with patch.object(sync_history, 'fetch_page', side_effect=[error, outcome]):
                    if failed:
                        with self.assertRaises(RuntimeError):
                            sync_history.sync_radon_once('http://localhost', 'a' * 32)
                        self.assertEqual(radon.get_cache_status()['generation_id'], initial['generation_id'])
                        self.assertEqual(radon.get_history('ecosense', 'a')[0]['name'], 'Basement')
                    else:
                        sync_history.sync_radon_once('http://localhost', 'a' * 32)
                        self.assertEqual(radon.get_cache_status()['generation_id'], replacement['generation_id'])
                        self.assertEqual(radon.get_history('ecosense', 'a')[0]['name'], 'Final')
                self.assertEqual(energy.DB_PATH, cache)
                conn = energy._connect()
                try:
                    self.assertEqual(conn.execute('SELECT label FROM circuit_labels WHERE slot=40').fetchone()[0], 'Keep')
                finally:
                    conn.close()

    def test_real_http_download_deletion_and_checkpoint_reconnect(self):
        radon.ingest_observations([self.row])
        collector = energy.DB_PATH
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
        root = Path(__file__).resolve().parents[1]
        origin = f'http://127.0.0.1:{port}'
        token = 'a' * 32
        environment = {**os.environ, 'DB_PATH': collector, 'ENERGY_SYNC_TOKEN': token}
        log_path = Path(self.directory.name) / 'collector.log'
        log = log_path.open('w')
        process = subprocess.Popen(
            [sys.executable, '-u', '-c',
             f'import sys,faulthandler;faulthandler.dump_traceback_later(10,repeat=True);'
             f'sys.path.insert(0,{str(root)!r});import web;'
             f'web.app.run(host="127.0.0.1",port={port})'],
            cwd=self.directory.name, env=environment,
            stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 30
            probe = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            while True:
                if process.poll() is not None:
                    self.fail('Isolated collector exited before readiness: ' + log_path.read_text())
                try:
                    with probe.open(origin + '/api/version', timeout=1):
                        break
                except urllib.error.URLError:
                    if time.monotonic() >= deadline:
                        self.fail('Isolated collector did not become ready: ' + log_path.read_text())
                    time.sleep(0.05)
            with patch.object(energy, 'DB_PATH', str(Path(self.directory.name) / 'http-cache.db')):
                energy.ensure_table()
                first = sync_history.sync_radon_once(origin, token)
                self.assertAlmostEqual(radon.get_history('ecosense', 'a')[0]['radon_bq_m3'], 25.9)
                with self.assertRaises(urllib.error.HTTPError) as rejected:
                    sync_history.sync_radon_once(origin, 'b' * 32)
                self.assertEqual(rejected.exception.code, 401)
                self.assertEqual(radon.get_cache_status(), first)
                with patch.object(energy, 'DB_PATH', collector):
                    conn = energy._connect()
                    try:
                        with conn:
                            conn.execute("UPDATE radon_readings SET name='First rename'")
                            conn.execute("UPDATE radon_readings SET name='Second rename'")
                    finally:
                        conn.close()
                    self.assertTrue(radon.compact_journal(1))
                replacement = sync_history.sync_radon_once(origin, token)
                self.assertNotEqual(first['generation_id'], replacement['generation_id'])
                self.assertEqual(radon.get_history('ecosense', 'a')[0]['name'], 'Second rename')
                with patch.object(energy, 'DB_PATH', collector):
                    conn = energy._connect()
                    try:
                        with conn:
                            conn.execute('DELETE FROM radon_readings')
                    finally:
                        conn.close()
                sync_history.sync_radon_once(origin, token)
                self.assertEqual(radon.get_sensors(), [])
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            log.close()
