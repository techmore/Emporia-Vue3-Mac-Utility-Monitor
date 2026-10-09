import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import energy
import sync_history
import web
from utc_migration import rehearse_utc_copy


class ChartSyncContractTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        database = patch.object(energy, 'DB_PATH', str(self.root / 'collector.db'))
        database.start()
        self.addCleanup(database.stop)
        energy.ensure_table()
        device = SimpleNamespace(device_gid='QA', device_name='Panel', time_zone='America/New_York',
            manufacturer_id='F2518A001AECE334ABC123',
            channels=[SimpleNamespace(channel_num='1', name='Pump')])
        energy.get_devices_with_channels(Mock(get_devices=Mock(return_value=[device])))
        with patch.object(energy, 'RATE_CENTS', 22.58):
            self.csv('1H', '12:00:00', 3)

    def csv(self, scale, instant, value):
        path = self.root / f'ABC123-Panel-{scale}.csv'
        path.write_text('Time Bucket (America/New_York),Panel-Pump (kWhs)\n'
                        f'10/08/2026 {instant},{value}\n')
        return energy.import_emporia_csv(str(path))

    def publish(self):
        # The first replacement event is supported CSV, not a chart event.
        with patch.object(energy, 'RATE_CENTS', 22.58):
            self.csv('1MIN', '12:00:00', .05)
            capture = {'schema': 'emporia_chart_v1', 'request': {'device_gid': 'QA',
                'channel_num': '1', 'start': '2026-10-08T16:01:00Z',
                'end': '2026-10-08T17:00:00Z', 'scale': '1MIN', 'unit': 'KilowattHours'},
                'received_at': '2026-10-08T17:10:00Z',
                'response': {'firstUsageInstant': '2026-10-08T16:01:00Z', 'usageList': [.05] * 59}}
            return energy.publish_completed_history(capture, json.dumps(capture['response']).encode(),
                legacy_storage_timezone='America/New_York')

    def page(self, after=0, limit=500):
        return energy.get_reading_changes(after, limit, protocol_version=3,
                                          measurement_model='interval_v2')

    def seed_cache(self, initial):
        cache = self.root / 'cache.db'
        with patch.object(energy, 'DB_PATH', str(cache)):
            energy.ensure_table()
            state = energy.apply_reading_changes(initial)
            conn = energy._connect()
            with conn:
                conn.execute("INSERT INTO circuit_labels(slot,label) VALUES(1,'Preserve')")
            conn.close()
        return cache, state

    def test_old_contract_refused_before_supported_csv_event_or_empty_page(self):
        initial = energy.get_reading_changes()
        self.publish()
        first = self.page(initial['next_cursor'], 1)
        self.assertEqual(first['changes'][0]['measurement_source'], 'csv_energy')
        for after in (0, initial['next_cursor'], first['high_watermark']):
            for protocol, model in ((2, None), (3, None), (3, 'interval_v1')):
                with self.subTest(after=after, protocol=protocol, model=model):
                    with self.assertRaises(energy.SyncUpgradeRequired) as refused:
                        energy.get_reading_changes(after, 1, protocol_version=protocol,
                                                  measurement_model=model)
                    self.assertEqual(refused.exception.measurement_model, 'interval_v2')

    def test_http_gate_precedes_identity_reset_and_contains_no_changes(self):
        initial = energy.get_reading_changes()
        self.publish()
        with patch.dict(web.os.environ, {'ENERGY_SYNC_TOKEN': 'a' * 32}):
            client = web.app.test_client()
            headers = {'Authorization': 'Bearer ' + 'a' * 32}
            queries = ('', '?protocol_version=3', '?protocol_version=3&measurement_model=interval_v1',
                f"?protocol_version=3&source_id={initial['source_id']}&generation_id={'b' * 32}")
            for query in queries:
                with self.subTest(query=query):
                    response = client.get('/api/sync/readings' + query, headers=headers)
                    self.assertEqual(response.status_code, 426)
                    self.assertEqual(response.json['required_measurement_model'], 'interval_v2')
                    self.assertNotIn('changes', response.json)
                    self.assertNotIn('reset_required', response.json)
                    self.assertEqual(response.headers['Cache-Control'], 'no-store')
            response = client.get('/api/sync/readings?protocol_version=3&measurement_model=interval_v2',
                                  headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json['time_policy']['measurement_model'], 'interval_v2')

    def test_gate_remains_after_reading_prune_and_journal_checkpoint(self):
        self.publish()
        conn = energy._connect()
        with conn:
            conn.execute('DELETE FROM readings')
        conn.close()
        self.assertTrue(energy.compact_reading_journal(max_entries=1))
        self.assertEqual(self.page()['changes'], [])
        with self.assertRaises(energy.SyncUpgradeRequired):
            energy.get_reading_changes(protocol_version=3)

    def test_invalid_or_duplicate_negotiation_is_rejected(self):
        with patch.dict(web.os.environ, {'ENERGY_SYNC_TOKEN': 'a' * 32}):
            client = web.app.test_client()
            for query in ('protocol_version=2&measurement_model=interval_v2',
                'protocol_version=3&measurement_model=', 'protocol_version=3&measurement_model=future',
                'protocol_version=3&measurement_model=interval_v1&measurement_model=interval_v2',
                'protocol_version=2&protocol_version=3&measurement_model=interval_v2'):
                response = client.get('/api/sync/readings?' + query,
                                      headers={'Authorization': 'Bearer ' + 'a' * 32})
                self.assertEqual(response.status_code, 400, query)
                self.assertNotIn('changes', response.json)

    def test_new_client_upgrades_known_contract_without_reset_or_repricing(self):
        seeds = [energy.get_reading_changes(protocol_version=version) for version in (2, 3)]
        self.publish()
        for initial in seeds:
            cache, before = self.seed_cache(initial)
            state = before
            while True:
                page = self.page(state['cursor'], 1)
                with patch.object(energy, 'DB_PATH', str(cache)):
                    state = energy.apply_reading_changes(page)
                if not page['has_more']:
                    break
            self.assertEqual(state['source_id'], before['source_id'])
            self.assertEqual(state['generation_id'], before['generation_id'])
            self.assertEqual(state['measurement_model'], 'interval_v2')
            with patch.object(energy, 'DB_PATH', str(cache)):
                conn = energy._connect()
                total = conn.execute('SELECT SUM(usage_kwh), SUM(cost_cents),COUNT(*) FROM sync_cached_readings').fetchone()
                self.assertAlmostEqual(total[0], 3)
                self.assertAlmostEqual(total[1], 3 * 22.58)
                self.assertEqual(total[2], 60)
                self.assertEqual(conn.execute('SELECT label FROM circuit_labels').fetchone()[0], 'Preserve')
                actual = [tuple(row) for row in conn.execute('SELECT reading_id,timestamp,usage_kwh,cost_cents FROM sync_cached_readings ORDER BY reading_id')]
                conn.close()
            conn = energy._connect()
            expected = [tuple(row) for row in conn.execute('SELECT id,timestamp,usage_kwh,cost_cents FROM readings ORDER BY id')]
            conn.close()
            self.assertEqual(actual, expected)
            cache.unlink()

    def test_v1_to_v2_is_additive_but_downgrade_zone_and_format_changes_are_not(self):
        initial = energy.get_reading_changes(protocol_version=3)
        cache, before = self.seed_cache(initial)
        upgraded = deepcopy(initial)
        upgraded.update(changes=[], after_cursor=before['cursor'], next_cursor=before['cursor'])
        upgraded['time_policy']['measurement_model'] = 'interval_v2'
        with patch.object(energy, 'DB_PATH', str(cache)):
            state = energy.apply_reading_changes(upgraded)
            for field, value in (('measurement_model', 'interval_v1'),
                                 ('measurement_model', 'future'),
                                 ('reporting_timezone', 'UTC'), ('timestamp_format', 'utc_v1')):
                invalid = deepcopy(upgraded)
                invalid['time_policy'][field] = value
                with self.assertRaises(ValueError):
                    energy.apply_reading_changes(invalid)
                self.assertEqual(energy.get_sync_cache_status(), state)
            legacy = deepcopy(upgraded)
            legacy['protocol_version'] = 2
            del legacy['time_policy']
            with self.assertRaises(ValueError):
                energy.apply_reading_changes(legacy)
            self.assertEqual(energy.get_sync_cache_status(), state)

    def test_chart_under_missing_or_v1_contract_cannot_mutate_cache(self):
        self.publish()
        page = self.page()
        for version in (2, 3):
            invalid = deepcopy(page)
            if version == 2:
                invalid['protocol_version'] = 2
                del invalid['time_policy']
            else:
                invalid['time_policy']['measurement_model'] = 'interval_v1'
            with patch.object(energy, 'DB_PATH', str(self.root / 'invalid-cache.db')):
                energy.ensure_table()
                with self.assertRaisesRegex(ValueError, 'interval_v2 contract'):
                    energy.apply_reading_changes(invalid)
                self.assertEqual(energy.get_sync_cache_status()['cursor'], 0)

    def test_client_refuses_unacknowledged_model_before_applying_supported_csv(self):
        initial = energy.get_reading_changes(protocol_version=3)
        cache, state = self.seed_cache(initial)
        self.publish()
        page = self.page(state['cursor'], 1)
        self.assertEqual(page['changes'][0]['measurement_source'], 'csv_energy')
        page['time_policy']['measurement_model'] = 'interval_v1'
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = json.dumps(page).encode()
        opener = Mock(open=Mock(return_value=response))
        with patch.object(energy, 'DB_PATH', str(cache)), \
                patch.object(sync_history.urllib.request, 'build_opener', return_value=opener):
            with self.assertRaisesRegex(ValueError, 'upgrade the collector'):
                sync_history.sync_once('http://localhost', 'a' * 32)
            self.assertEqual(energy.get_sync_cache_status(), state)
            conn = energy._connect()
            self.assertEqual(conn.execute('SELECT SUM(usage_kwh) FROM sync_cached_readings').fetchone()[0], 3)
            conn.close()
            self.assertEqual(opener.open.call_count, 1)

    def test_concurrent_chart_publication_cannot_mix_a_legacy_read_snapshot(self):
        original = energy._utc_clock

        def publish_after_snapshot(conn):
            clock = original(conn)
            with patch.object(energy, '_utc_clock', original):
                self.publish()
            return clock

        with patch.object(energy, '_utc_clock', side_effect=publish_after_snapshot):
            page = energy.get_reading_changes(protocol_version=3, limit=1)
        self.assertEqual(page['changes'][0]['usage_kwh'], 3)
        self.assertEqual(page['time_policy']['measurement_model'], 'interval_v1')
        self.assertFalse(page['has_more'])
        with self.assertRaises(energy.SyncUpgradeRequired):
            energy.get_reading_changes(page['next_cursor'], protocol_version=3)

    @unittest.skipUnless(shutil.which('swift'), 'Swift runtime required')
    def test_real_http_resume_and_native_reader_with_legacy_and_utc_chart_contract(self):
        initial = energy.get_reading_changes(protocol_version=3)
        legacy_cache, _ = self.seed_cache(initial)
        self.publish()
        archive, artifact = self.root / 'archive.db', self.root / 'utc.db'
        energy.backup_database(archive)
        archive_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
        rehearse_utc_copy(archive, artifact, expected_sha256=archive_hash,
                          legacy_timezone='America/New_York', reporting_timezone='America/New_York')
        root = Path(__file__).resolve().parents[1]
        source = (root / 'EnergyMonitorApp/Sources/MenuPopover.swift').read_text()
        models = source[source.index('struct CircuitBucket:'):source.index('struct StoredMenuResponse:')]
        reader = source[source.index('struct DownloadedHistoryReader'):source.index('final class MenuMonitor:')]
        script = self.root / 'native.swift'
        script.write_text('import Foundation\nimport SQLite3\n' + models + reader + '''
NSTimeZone.default = TimeZone(identifier:"America/New_York")!
let now = ISO8601DateFormatter().date(from:"2026-10-08T18:00:00Z")!
let reader = DownloadedHistoryReader(database:URL(fileURLWithPath:CommandLine.arguments[1]))
guard let (history,_) = reader.history(channel:"Pump",device:"QA",source:CommandLine.arguments[2],now:now),
      let kwh = history.windows[0].totalKwh, let cents = history.windows[0].totalCents else { fatalError("History missing") }
precondition(abs(kwh - 3) < 0.000000001 && abs(cents - 67.74) < 0.000000001)
precondition(history.windows[0].readings == 60)
print("Chart contract native totals verified")
''')
        code = '''
import os
import energy
import web
from werkzeug.serving import make_server
original = energy._connect
energy._connect = lambda: original(os.environ['ARTIFACT'], allow_utc_rehearsal=True, read_only=True)
server = make_server('127.0.0.1', 0, web.app)
print(server.server_port, flush=True)
server.serve_forever()
'''
        for storage, database in (('legacy_local_v1', archive), ('utc_v1', artifact)):
            before = hashlib.sha256(database.read_bytes()).hexdigest()
            env = dict(os.environ, DB_PATH=str(self.root / f'{storage}-boot.db'),
                       ARTIFACT=str(database), ENERGY_SYNC_TOKEN='a' * 32)
            with (self.root / f'{storage}-server.log').open('w') as log:
                server = subprocess.Popen([sys.executable, '-u', '-c', code], cwd=root, env=env,
                                          stdout=subprocess.PIPE, stderr=log, text=True)
                try:
                    port = server.stdout.readline().strip()
                    self.assertTrue(port.isdigit(), (self.root / f'{storage}-server.log').read_text())
                    origin = 'http://127.0.0.1:' + port
                    request = urllib.request.Request(origin + '/api/sync/readings?protocol_version=3&limit=1',
                                                     headers={'Authorization': 'Bearer ' + 'a' * 32})
                    with self.assertRaises(urllib.error.HTTPError) as rejected:
                        urllib.request.urlopen(request, timeout=5)
                    self.assertEqual(rejected.exception.code, 426)
                    self.assertNotIn('changes', json.loads(rejected.exception.read()))
                    rejected.exception.close()
                    cache = legacy_cache if storage == 'legacy_local_v1' else self.root / 'utc-cache.db'
                    command = [sys.executable, str(root / 'sync_history.py'), '--collector', origin, '--cache', str(cache)]
                    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    state = json.loads(result.stdout)
                    self.assertEqual(state['timestamp_format'], storage)
                    self.assertEqual(state['measurement_model'], 'interval_v2')
                    resumed = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
                    self.assertEqual(resumed.returncode, 0, resumed.stderr)
                    self.assertEqual(json.loads(resumed.stdout)['cursor'], state['cursor'])
                    native = subprocess.run(['swift', str(script), str(cache), state['source_id']],
                                            capture_output=True, text=True, timeout=60)
                    self.assertEqual(native.returncode, 0, native.stdout + native.stderr)
                    self.assertIn('Chart contract native totals verified', native.stdout)
                finally:
                    server.terminate()
                    server.wait(timeout=10)
                    server.stdout.close()
            self.assertEqual(hashlib.sha256(database.read_bytes()).hexdigest(), before)
        self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), archive_hash)
        with self.assertRaises(RuntimeError):
            energy._connect(artifact)
