import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import energy
import sync_history
import web
from energy_clock import EnergyClock
from utc_migration import rehearse_utc_copy


class SyncContractTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('swift'), 'Swift runtime required')
    def test_converted_artifact_real_http_download_and_native_offline_read(self):
        initial = energy.get_reading_changes()
        legacy_cache = self.root/'legacy-download.db'
        with patch.object(energy, 'DB_PATH', str(legacy_cache)):
            energy.ensure_table()
            energy.apply_reading_changes(initial)
            conn = energy._connect()
            conn.execute("INSERT INTO circuit_labels(slot,label) VALUES(1,'Preserve')")
            conn.commit()
            conn.close()
        archive, artifact = self.root/'archive.db', self.root/'converted.db'
        energy.backup_database(archive)
        before = hashlib.sha256(archive.read_bytes()).hexdigest()
        rehearse_utc_copy(archive, artifact, expected_sha256=before,
                          legacy_timezone='America/New_York', reporting_timezone='America/New_York')
        converted_hash = hashlib.sha256(artifact.read_bytes()).hexdigest()
        with self.assertRaises(RuntimeError):
            energy._connect(artifact)
        root = Path(__file__).resolve().parents[1]
        code = '''
import os
import energy
import web
from werkzeug.serving import make_server
original = energy._connect
# This test-only adapter grants read-only inspection, not live UTC activation.
energy._connect = lambda: original(os.environ['UTC_ARTIFACT'], allow_utc_rehearsal=True, read_only=True)
server = make_server('127.0.0.1', 0, web.app)
print(server.server_port, flush=True)
server.serve_forever()
'''
        env = dict(os.environ, DB_PATH=str(self.root/'server-boot.db'),
                   UTC_ARTIFACT=str(artifact), ENERGY_SYNC_TOKEN='a'*32)
        with (self.root/'server.log').open('w') as log:
            server = subprocess.Popen([sys.executable, '-u', '-c', code], cwd=root, env=env,
                                      stdout=subprocess.PIPE, stderr=log, text=True)
            try:
                port_line = server.stdout.readline()
                self.assertTrue(port_line.strip().isdigit(), (self.root/'server.log').read_text())
                origin = f'http://127.0.0.1:{int(port_line)}'
                request = urllib.request.Request(origin+'/api/sync/readings',
                                                 headers={'Authorization': 'Bearer '+'a'*32})
                with self.assertRaises(urllib.error.HTTPError) as rejected:
                    urllib.request.urlopen(request, timeout=5)
                self.assertEqual(rejected.exception.code, 426)
                self.assertNotIn('changes', json.loads(rejected.exception.read()))
                rejected.exception.close()
                cache = self.root/'download.db'
                command = [sys.executable, str(root/'sync_history.py'), '--collector', origin,
                           '--cache', str(cache)]
                first = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
                self.assertEqual(first.returncode, 0, first.stderr)
                state = json.loads(first.stdout)
                self.assertEqual(state['timestamp_format'], 'utc_v1')
                second = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
                self.assertEqual(second.returncode, 0, second.stderr)
                self.assertEqual(json.loads(second.stdout)['cursor'], state['cursor'])
                reset = subprocess.run([*command[:-1], str(legacy_cache)], env=env,
                                       capture_output=True, text=True, timeout=30)
                self.assertEqual(reset.returncode, 0, reset.stderr)
                replacement = json.loads(reset.stdout)
                self.assertNotEqual(replacement['generation_id'], initial['generation_id'])
                self.assertEqual(replacement['source_id'], initial['source_id'])
                self.assertEqual(replacement['timestamp_format'], 'utc_v1')
                conn = energy._connect(legacy_cache)
                self.assertEqual(conn.execute('SELECT label FROM circuit_labels').fetchone()[0], 'Preserve')
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM sync_cached_readings').fetchone()[0], 0)
                conn.close()
            finally:
                server.terminate()
                server.wait(timeout=10)
                server.stdout.close()
        source = (root/'EnergyMonitorApp/Sources/MenuPopover.swift').read_text()
        models = source[source.index('struct CircuitBucket:'):source.index('struct StoredMenuResponse:')]
        reader = source[source.index('struct DownloadedHistoryReader'):source.index('final class MenuMonitor:')]
        script = self.root/'offline.swift'
        script.write_text('import Foundation\nimport SQLite3\n'+models+reader+'''
NSTimeZone.default = TimeZone(identifier:"Asia/Tokyo")!
let now = ISO8601DateFormatter().date(from:"2026-11-01T08:00:00Z")!
let reader = DownloadedHistoryReader(database:URL(fileURLWithPath:CommandLine.arguments[1]))
guard let (history,_) = reader.history(channel:"Main",device:"A",source:CommandLine.arguments[2],now:now) else { fatalError("Downloaded UTC history unavailable") }
precondition(history.windows[0].totalKwh == 1)
precondition(history.windows[0].totalCents == 22.58)
let cells = history.windows[0].series.filter{$0.totalKwh != nil}
precondition(cells.count == 1 && cells[0].period == "2026-11-01 01:00 -04:00")
print("Offline downloaded UTC history verified")
''')
        native = subprocess.run(['swift', str(script), str(cache), state['source_id']],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(native.returncode, 0, native.stdout+native.stderr)
        self.assertIn('Offline downloaded UTC history verified', native.stdout)
        self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), before)
        self.assertEqual(hashlib.sha256(artifact.read_bytes()).hexdigest(), converted_hash)
        self.assertEqual(cache.stat().st_mode & 0o777, 0o600)

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        database = patch.object(energy, 'DB_PATH', str(self.root / 'collector.db'))
        database.start()
        self.addCleanup(database.stop)
        energy.ensure_table()
        conn = energy._connect()
        conn.execute('''INSERT INTO readings(timestamp,device_gid,channel_name,usage_kwh,cost_cents,
                     measurement_seconds,measurement_source) VALUES (?,?,?,1,22.58,3600,'csv_energy')''',
                     ('2026-11-01T05:30:00.123456+00:00', 'A', 'Main'))
        conn.commit()
        conn.close()

    def utc_page(self):
        with patch.object(energy, '_utc_clock', return_value=EnergyClock('America/New_York')):
            return energy.get_reading_changes(protocol_version=3)

    def cache(self):
        return patch.object(energy, 'DB_PATH', str(self.root / 'cache.db'))

    def test_v3_declares_format_and_zone_but_v2_keeps_legacy_contract(self):
        legacy = energy.get_reading_changes()
        self.assertEqual(legacy['protocol_version'], 2)
        self.assertNotIn('time_policy', legacy)
        modern = energy.get_reading_changes(protocol_version=3)
        self.assertEqual(modern['time_policy'], {'timestamp_format': 'legacy_local_v1',
                         'reporting_timezone': None, 'measurement_model': 'interval_v1'})
        utc = self.utc_page()
        self.assertEqual(utc['time_policy']['timestamp_format'], 'utc_v1')
        self.assertEqual(utc['time_policy']['reporting_timezone'], 'America/New_York')

    def test_old_http_client_is_refused_utc_before_receiving_rows(self):
        with patch.dict(web.os.environ, {'ENERGY_SYNC_TOKEN': 'a'*32}), \
                patch.object(energy, '_utc_clock', return_value=EnergyClock('America/New_York')):
            client = web.app.test_client()
            headers = {'Authorization': 'Bearer ' + 'a'*32}
            old = client.get('/api/sync/readings', headers=headers)
            self.assertEqual(old.status_code, 426)
            self.assertEqual(old.json['required_protocol'], 3)
            self.assertNotIn('changes', old.json)
            modern = client.get('/api/sync/readings?protocol_version=3', headers=headers)
            self.assertEqual(modern.status_code, 200)
            self.assertEqual(modern.json['protocol_version'], 3)

    def test_utc_cache_isolated_from_table_read_by_old_native_apps(self):
        page = self.utc_page()
        with self.cache():
            energy.ensure_table()
            state = energy.apply_reading_changes(page)
            conn = energy._connect()
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM sync_cached_readings').fetchone()[0], 0)
            row = dict(conn.execute('SELECT * FROM sync_cached_utc_readings').fetchone())
            policy = dict(conn.execute('SELECT * FROM sync_cache_format').fetchone())
            conn.close()
        self.assertEqual(row['timestamp'], page['changes'][0]['timestamp'])
        self.assertEqual(row['measurement_seconds'], 3600)
        self.assertEqual(policy['reporting_timezone'], 'America/New_York')
        self.assertEqual(state['timestamp_format'], 'utc_v1')
        self.assertIsNotNone(datetime.fromisoformat(state['synchronized_at']).tzinfo)

    def test_invalid_policy_or_noncanonical_utc_cannot_publish_cache(self):
        page = self.utc_page()
        invalid = []
        for field, value in (('timestamp_format', 'future_v9'), ('reporting_timezone', None),
                             ('reporting_timezone', 'Not/AZone'), ('measurement_model', 'instantaneous')):
            row = deepcopy(page)
            row['time_policy'][field] = value
            invalid.append(row)
        for stamp in ('2026-11-01T05:30:00', '2026-11-01T01:30:00.123456-04:00',
                      '2026-11-01T05:30:00Z', '2026-11-01T05:30:00.123Z'):
            row = deepcopy(page)
            row['changes'][0]['timestamp'] = stamp
            invalid.append(row)
        with self.cache():
            energy.ensure_table()
            for row in invalid:
                with self.subTest(policy=row['time_policy'], stamp=row['changes'][0]['timestamp']):
                    with self.assertRaises(ValueError):
                        energy.apply_reading_changes(row)
                    self.assertEqual(energy.get_sync_cache_status()['cursor'], 0)

    def test_policy_change_without_generation_reset_is_rejected(self):
        page = energy.get_reading_changes(protocol_version=3)
        with self.cache():
            energy.ensure_table()
            before = energy.apply_reading_changes(page)
            changed = deepcopy(page)
            changed.update(after_cursor=before['cursor'], next_cursor=before['cursor'], changes=[])
            changed['time_policy'] = self.utc_page_policy()
            with self.assertRaises(ValueError):
                energy.apply_reading_changes(changed)
            self.assertEqual(energy.get_sync_cache_status(), before)

    def test_contract_downgrade_and_unidentified_old_rows_fail_closed(self):
        modern = self.utc_page()
        legacy = energy.get_reading_changes()
        with self.cache():
            energy.ensure_table()
            before = energy.apply_reading_changes(modern)
            legacy.update(after_cursor=before['cursor'], next_cursor=before['cursor'], changes=[])
            with self.assertRaises(ValueError):
                energy.apply_reading_changes(legacy)
            self.assertEqual(energy.get_sync_cache_status(), before)
        with patch.object(energy, 'DB_PATH', str(self.root/'orphan.db')):
            energy.ensure_table()
            conn = energy._connect()
            conn.execute("INSERT INTO sync_cached_readings(reading_id,timestamp,device_gid,usage_kwh) VALUES(1,'old','unknown',999)")
            conn.commit()
            conn.close()
            with self.assertRaisesRegex(ValueError, 'lack source'):
                energy.apply_reading_changes(modern)
            self.assertEqual(energy.get_sync_cache_status()['cursor'], 0)

    def test_client_negotiates_energy_format_without_changing_radon_protocol(self):
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.read.return_value = b'{"time_policy":{"measurement_model":"interval_v2"}}'
        with patch.object(sync_history.urllib.request, 'build_opener', return_value=opener):
            for stream in ('readings', 'radon'):
                sync_history.fetch_page('http://localhost', 'a'*32, {'cursor': 0}, stream=stream)
                request = opener.open.call_args.args[0]
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
                self.assertEqual(query.get('protocol_version'), ['3'] if stream == 'readings' else None)
                self.assertEqual(query.get('measurement_model'), ['interval_v2'] if stream == 'readings' else None)

    @staticmethod
    def utc_page_policy():
        return {'timestamp_format': 'utc_v1', 'reporting_timezone': 'America/New_York',
                'measurement_model': 'interval_v1'}

    def test_successful_generation_reset_replaces_format_and_retains_other_streams(self):
        initial = energy.get_reading_changes()
        replacement = self.utc_page()
        replacement['generation_id'] = 'b'*32
        error = urllib.error.HTTPError('http://localhost', 409, 'reset', {}, io.BytesIO(
            json.dumps({'reset_required': True, 'source_id': initial['source_id']}).encode()))
        with self.cache():
            energy.ensure_table()
            energy.apply_reading_changes(initial)
            conn = energy._connect()
            conn.execute("INSERT INTO circuit_labels(slot,label) VALUES(1,'Preserve')")
            conn.commit()
            conn.close()
            with patch.object(sync_history, 'fetch_page', side_effect=[error, replacement]):
                state = sync_history.sync_once('http://localhost', 'a'*32)
            self.assertEqual(state['timestamp_format'], 'utc_v1')
            conn = energy._connect()
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM sync_cached_readings').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM sync_cached_utc_readings').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT label FROM circuit_labels').fetchone()[0], 'Preserve')
            conn.close()

    def test_concurrent_cache_advance_prevents_stale_snapshot_publication(self):
        initial = energy.get_reading_changes()
        replacement = deepcopy(initial)
        replacement['generation_id'] = 'b'*32
        original = energy.DB_PATH
        error = urllib.error.HTTPError('http://localhost', 409, 'reset', {}, io.BytesIO(
            json.dumps({'reset_required': True, 'source_id': initial['source_id']}).encode()))
        with self.cache():
            cache = energy.DB_PATH
            energy.ensure_table()
            energy.apply_reading_changes(initial)

            def fetch(*args, **kwargs):
                if energy.DB_PATH == cache:
                    raise error
                # A second real cache writer progresses while the reset downloads privately.
                with patch.object(energy, 'DB_PATH', cache):
                    current = energy.get_sync_cache_status()
                    advanced = deepcopy(initial)
                    advanced.update(after_cursor=current['cursor'], next_cursor=current['cursor']+1,
                                    high_watermark=current['cursor']+1)
                    advanced['changes'][0].update(sequence=current['cursor']+1, cost_cents=99)
                    energy.apply_reading_changes(advanced)
                return replacement

            with patch.object(sync_history, 'fetch_page', side_effect=fetch):
                with self.assertRaisesRegex(ValueError, 'Cache changed'):
                    sync_history.sync_once('http://localhost', 'a'*32)
            conn = energy._connect()
            self.assertEqual(conn.execute('SELECT cost_cents FROM sync_cached_readings').fetchone()[0], 99)
            conn.close()
        self.assertEqual(energy.DB_PATH, original)
