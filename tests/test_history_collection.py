import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

import energy
import history_collection
import web
from energy_clock import EnergyClock
from utc_migration import _convert


class HistoryCollectionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        database = patch.object(energy, 'DB_PATH', str(self.root / 'collector.db'))
        database.start()
        self.addCleanup(database.stop)
        rate = patch.object(energy, 'RATE_CENTS', 22.58)
        rate.start()
        self.addCleanup(rate.stop)
        energy.ensure_table()
        self.discover()
        self.start = datetime(2026, 10, 8, 16, tzinfo=timezone.utc)
        self.now = self.start + timedelta(minutes=20)
        self.vue = SimpleNamespace(auth=SimpleNamespace(max_retry_attempts=1))

    def discover(self, number='1', name='Pump', gid='QA'):
        device = SimpleNamespace(device_gid=gid, device_name='Panel', time_zone='America/New_York',
            manufacturer_id='F2518A001AECE334ABC123',
            channels=[SimpleNamespace(channel_num=number, name=name)])
        energy.get_devices_with_channels(Mock(get_devices=Mock(return_value=[device])))

    def configure(self, **kwargs):
        options = {'legacy_storage_timezone': 'America/New_York', 'window_minutes': 10,
                   'retry_seconds': 60, 'requests_hour': 20, 'requests_day': 40, 'now': self.now}
        options.update(kwargs)
        return energy.configure_completed_collection('QA', '1', self.start, **options)

    def rows(self, table):
        conn = energy._connect()
        try:
            return [dict(row) for row in conn.execute(f'SELECT * FROM {table} ORDER BY 1')]
        finally:
            conn.close()

    def reserve(self, now=None, weight=2):
        conn = energy._connect()
        try:
            with conn:
                conn.execute('BEGIN IMMEDIATE')
                return history_collection.reserve(conn, now or self.now, energy._utc_clock(conn), request_weight=weight)
        finally:
            conn.close()

    def response(self, vue, gid, number, start, end, *, values=None, now=None):
        count = int((end - start).total_seconds() // 60)
        response = {'firstUsageInstant': start.isoformat(), 'usageList': [.05] * count if values is None else values}
        capture = {'schema': 'emporia_chart_v1', 'request': {'device_gid': gid, 'channel_num': number,
            'start': EnergyClock.stamp(start), 'end': EnergyClock.stamp(end), 'scale': '1MIN', 'unit': 'KilowattHours'},
            'received_at': EnergyClock.stamp(now or self.now), 'response': response}
        return capture, json.dumps(response).encode()

    def run_collection(self, *, now=None, values=None, max_requests=1):
        moment = now or self.now
        with patch.object(energy, 'capture_chart', side_effect=lambda *args: self.response(*args, values=values, now=moment)):
            return energy.run_completed_collection(self.vue, now=moment, max_requests=max_requests)

    def test_no_configuration_means_no_request_or_lease(self):
        with patch.object(energy, 'capture_chart') as capture:
            self.assertEqual(energy.run_completed_collection(self.vue, now=self.now)['attempted'], 0)
            capture.assert_not_called()
        self.assertEqual(self.rows('energy_history_attempts'), [])

    def test_initial_live_discovery_records_missing_main_label_without_pollution(self):
        self.discover(number='1,2,3', name='')
        device = SimpleNamespace(device_gid='QA', device_name='Panel', time_zone='America/New_York',
            manufacturer_id='F2518A001AECE334ABC123', channels=[SimpleNamespace(channel_num='1,2,3', name='')])
        usage = SimpleNamespace(channels={'1,2,3': SimpleNamespace(name='Main', usage=.05)})
        vue = Mock(get_devices=Mock(return_value=[device]), get_device_list_usage=Mock(return_value={'QA': usage}))
        result = energy.discover_completed_collection_channels(vue)
        self.assertIn({'device_gid': 'QA', 'channel_num': '1,2,3', 'channel_name': 'Main'}, result['channels'])
        self.assertEqual(self.rows('readings'), [])
        self.assertEqual(self.rows('latest_channel_snapshot'), [])
        energy.configure_completed_collection('QA', '1,2,3', self.start, legacy_storage_timezone='America/New_York',
            requests_hour=20, requests_day=40, now=self.now)

    def test_configuration_is_explicit_idempotent_and_immutable(self):
        first = self.configure()
        self.assertEqual(self.configure(), first)
        for options in ({'legacy_storage_timezone': None}, {'window_minutes': 9}, {'requests_day': 41}):
            with self.assertRaises(ValueError):
                self.configure(**options)
        self.assertEqual(energy.get_completed_collection_status(), first)

    def test_invalid_start_budget_or_zone_rolls_back_configuration(self):
        for start in (self.start.replace(tzinfo=None), self.start + timedelta(seconds=1), self.now + timedelta(minutes=1)):
            with self.assertRaises(ValueError):
                energy.configure_completed_collection('QA', '1', start, legacy_storage_timezone='America/New_York',
                    requests_hour=20, requests_day=40, now=self.now)
        for options in ({'requests_hour': True}, {'requests_hour': 41}, {'retry_seconds': 0},
                        {'window_minutes': 361}, {'legacy_storage_timezone': None}):
            with self.assertRaises(ValueError):
                self.configure(**options)
        self.assertEqual(self.rows('energy_history_channels'), [])
        self.assertEqual(self.rows('energy_history_limits'), [])

    def test_unregistered_ambiguous_and_unowned_channels_cannot_be_promoted(self):
        with self.assertRaises(ValueError):
            energy.configure_completed_collection('unknown', '1', self.start, requests_hour=20, requests_day=40)
        conn = energy._connect()
        with conn:
            conn.execute("INSERT INTO readings(timestamp,device_gid,channel_name,usage_kwh,cost_cents) VALUES('old','QA','Pump',3,99)")
        conn.close()
        before = self.rows('readings')
        with self.assertRaisesRegex(ValueError, 'unowned'):
            self.configure()
        self.assertEqual(self.rows('readings'), before)
        self.discover(name='Renamed')
        with self.assertRaisesRegex(ValueError, 'renamed'):
            self.configure()
        self.assertEqual(self.rows('energy_history_channels'), [])

    def test_completed_windows_resume_without_repeating_or_prorating_energy(self):
        self.configure()
        self.assertEqual(self.run_collection()['outcomes'], ['complete'])
        first_ids = [row['id'] for row in self.rows('readings')]
        first_costs = [row['cost_cents'] for row in self.rows('readings')]
        energy.ensure_table()  # Real restart initialization preserves progress/evidence.
        self.assertEqual(self.run_collection()['outcomes'], ['complete'])
        self.assertEqual(self.run_collection()['attempted'], 0)
        rows = self.rows('readings')
        self.assertEqual(len(rows), 15)
        self.assertEqual([row['id'] for row in rows[:10]], first_ids)
        self.assertEqual([row['cost_cents'] for row in rows[:10]], first_costs)
        self.assertAlmostEqual(sum(row['usage_kwh'] for row in rows), .75)
        status = energy.get_completed_collection_status()['channels'][0]
        self.assertEqual(status['scan_cursor_utc'], EnergyClock.stamp(self.start + timedelta(minutes=15)))
        self.assertEqual(status['verified_until_utc'], status['scan_cursor_utc'])

    def test_lost_worker_lease_survives_restart_then_expires_without_budget_refund(self):
        self.configure()
        first = self.reserve()
        energy.ensure_table()
        self.assertIsNone(self.reserve(self.now + timedelta(minutes=14)))
        second = self.reserve(self.now + timedelta(minutes=16))
        self.assertEqual(second['id'], first['id'])
        self.assertNotEqual(second['lease_id'], first['lease_id'])
        self.assertEqual(len(self.rows('energy_history_attempts')), 2)
        self.assertEqual(self.rows('energy_history_channels')[0]['scan_cursor_utc'], EnergyClock.stamp(self.start))
        conn = energy._connect()
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            with self.assertRaises(RuntimeError):
                history_collection.check_lease(conn, first, self.now + timedelta(minutes=16), None)
        conn.close()

    def test_wire_retry_budget_covers_replays_and_is_persistent(self):
        self.configure()
        self.assertIsNotNone(self.reserve(weight=20))
        # Expired lease alone cannot replenish a rolling-hour allowance.
        self.assertIsNone(self.reserve(self.now + timedelta(minutes=16), weight=20))
        self.assertIsNotNone(self.reserve(self.now + timedelta(hours=1), weight=20))
        self.assertIsNone(self.reserve(self.now + timedelta(hours=2), weight=20))
        self.assertIsNotNone(self.reserve(self.now + timedelta(days=1), weight=20))
        self.assertEqual(sum(row['request_weight'] for row in self.rows('energy_history_attempts')), 60)

    def test_acquisition_failure_retains_cursor_and_retries_same_window_after_delay(self):
        self.configure()
        with patch.object(energy, 'capture_chart', side_effect=requests.exceptions.Timeout('private token')):
            result = energy.run_completed_collection(self.vue, now=self.now)
        self.assertEqual(result['outcomes'], ['acquisition_error'])
        original = self.rows('energy_history_jobs')[0]
        self.assertEqual(original['status'], 'error')
        self.assertNotIn('private token', json.dumps(self.rows('energy_history_attempt_results')))
        self.assertEqual(self.rows('energy_source_batches'), [])
        self.assertEqual(self.run_collection(now=self.now + timedelta(seconds=59))['attempted'], 0)
        self.assertEqual(self.run_collection(now=self.now + timedelta(minutes=1))['outcomes'], ['complete'])
        self.assertEqual(self.rows('energy_history_jobs')[0]['id'], original['id'])

    def test_null_gap_advances_scan_not_verified_prefix_and_later_fills_without_repricing(self):
        self.configure()
        values = [.05] * 10
        values[2] = None
        self.assertEqual(self.run_collection(values=values)['outcomes'], ['gap'])
        status = energy.get_completed_collection_status()['channels'][0]
        self.assertEqual(status['scan_cursor_utc'], EnergyClock.stamp(self.start + timedelta(minutes=10)))
        self.assertEqual(status['verified_until_utc'], EnergyClock.stamp(self.start))
        previous = {row['timestamp']: (row['id'], row['cost_cents']) for row in self.rows('readings')}
        with patch.object(energy, 'RATE_CENTS', 50):
            self.assertEqual(self.run_collection(now=self.now + timedelta(minutes=1), max_requests=3)['outcomes'], ['complete', 'complete'])
        rows = self.rows('readings')
        self.assertEqual(len(rows), 16)
        for row in rows:
            if row['timestamp'] in previous:
                self.assertEqual((row['id'], row['cost_cents']), previous[row['timestamp']])
        self.assertEqual(len(self.rows('energy_source_batches')), 3)
        self.assertAlmostEqual(sum(row['usage_kwh'] for row in rows), .8)

    def test_empty_cloud_history_remains_a_recorded_gap_not_zero_readings(self):
        self.configure()
        self.assertEqual(self.run_collection(values=[])['outcomes'], ['gap'])
        self.assertEqual(len(self.rows('energy_source_batches')), 1)
        self.assertEqual(self.rows('readings'), [])
        self.assertEqual(self.rows('reading_changes'), [])
        self.assertEqual(self.rows('energy_history_jobs')[0]['status'], 'gap')

    def test_recent_complete_window_is_rechecked_and_changed_energy_requires_review(self):
        self.configure()
        self.run_collection()
        before = self.rows('readings')
        # Recheck the completed window at the documented 30-minute interval.
        result = self.run_collection(now=self.now + timedelta(minutes=30), values=[.06] * 10, max_requests=3)
        self.assertEqual(result['outcomes'], ['complete', 'complete', 'review'])
        self.assertEqual(self.rows('readings')[:10], before)
        original = next(job for job in self.rows('energy_history_jobs') if job['start_utc'] == EnergyClock.stamp(self.start))
        self.assertEqual(original['status'], 'review')
        self.assertEqual(energy.get_completed_collection_status()['channels'][0]['verified_until_utc'], EnergyClock.stamp(self.start))

    def test_source_projection_cursor_and_receipt_rollback_together(self):
        self.configure()
        conn = energy._connect()
        conn.execute("CREATE TRIGGER fail_history_receipt BEFORE INSERT ON energy_history_attempt_results BEGIN SELECT RAISE(ABORT,'injected'); END")
        conn.commit()
        conn.close()
        self.assertEqual(self.run_collection()['outcomes'], ['publication_outcome_unknown'])
        for table in ('energy_source_batches', 'energy_source_observations', 'energy_reading_projection',
                      'readings', 'reading_changes', 'energy_history_attempt_results'):
            self.assertEqual(self.rows(table), [], table)
        self.assertEqual(len(self.rows('energy_history_attempts')), 1)
        self.assertIsNotNone(self.rows('energy_history_jobs')[0]['lease_id'])
        self.assertEqual(self.rows('energy_history_channels')[0]['scan_cursor_utc'], EnergyClock.stamp(self.start))

    def test_cross_channel_fairness_and_no_second_lease_on_same_channel(self):
        self.configure()
        self.discover(number='2', name='Lights')
        energy.configure_completed_collection('QA', '2', self.start, legacy_storage_timezone='America/New_York',
            window_minutes=10, retry_seconds=60, requests_hour=20, requests_day=40, now=self.now)
        first, second = self.reserve(), self.reserve()
        self.assertEqual(first['channel_num'], '1')
        self.assertEqual(second['channel_num'], '2')
        self.assertIsNone(self.reserve())

    def test_pausing_cancels_lease_without_resuming_live_history_append(self):
        self.configure()
        lease = self.reserve()
        energy.set_completed_collection_enabled('QA', '1', False)
        self.assertIsNone(self.reserve())
        conn = energy._connect()
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            self.assertTrue(history_collection.owns_live_channel(conn, 'QA', '1', 'Pump'))
            with self.assertRaises(RuntimeError):
                history_collection.check_lease(conn, lease, self.now, None)
        conn.close()
        energy.set_completed_collection_enabled('QA', '1', True)
        self.assertIsNotNone(self.reserve())

    def test_promoted_live_poll_updates_snapshot_only_and_unconfigured_channels_still_archive(self):
        self.configure()
        self.discover(number='2', name='Lights')
        channels = {number: SimpleNamespace(name=name, usage=.05, timestamp=self.now)
                    for number, name in (('1', 'Pump'), ('2', 'Lights'))}
        vue = Mock(get_device_list_usage=Mock(return_value={'QA': SimpleNamespace(channels=channels)}))
        with patch.object(energy, '_read_rate_cents', return_value=22.58), \
                patch.object(energy, '_last_compaction', time.monotonic()):
            energy.poll_and_store(vue, ['QA'])
        self.assertEqual([row['channel_name'] for row in self.rows('readings')], ['Lights'])
        self.assertEqual(len(self.rows('latest_channel_snapshot')), 2)
        energy.set_completed_collection_enabled('QA', '1', False)
        with patch.object(energy, '_last_compaction', time.monotonic()):
            energy.poll_and_store(vue, ['QA'])
        self.assertNotIn('Pump', [row['channel_name'] for row in self.rows('readings')])

    def test_claim_change_during_request_cannot_publish_or_advance(self):
        self.configure()

        def capture(*args):
            self.discover(name='Renamed')
            return self.response(*args)

        with patch.object(energy, 'capture_chart', side_effect=capture):
            self.assertEqual(energy.run_completed_collection(self.vue, now=self.now)['outcomes'], ['publication_outcome_unknown'])
        self.assertEqual(self.rows('energy_source_batches'), [])
        self.assertEqual(self.rows('readings'), [])
        self.assertEqual(self.rows('energy_history_channels')[0]['scan_cursor_utc'], EnergyClock.stamp(self.start))

    def test_clock_regression_and_unknown_retry_budget_do_not_request(self):
        self.configure()
        self.reserve()
        with self.assertRaises(ValueError):
            self.reserve(self.now - timedelta(seconds=1))
        with patch.object(energy, 'capture_chart') as capture:
            self.vue.auth.max_retry_attempts = True
            with self.assertRaises(ValueError):
                energy.run_completed_collection(self.vue, now=self.now)
            capture.assert_not_called()

    def test_attempt_and_scope_evidence_are_immutable(self):
        self.configure()
        self.run_collection()
        conn = energy._connect()
        for table, field in (('energy_history_attempts', 'request_weight'), ('energy_history_attempt_results', 'result_json'),
                             ('energy_history_channels', 'window_minutes'), ('energy_history_jobs', 'start_utc'),
                             ('energy_history_limits', 'requests_hour')):
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(f"UPDATE {table} SET {field}='changed'")
            conn.rollback()
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(f'DELETE FROM {table}')
            conn.rollback()
        self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])
        conn.close()

    def test_private_utc_policy_works_but_legacy_configuration_cannot_cross_cutover(self):
        path = self.root / 'ABC123-Panel-1MIN.csv'
        path.write_text('Time Bucket (America/New_York),Panel-Pump (kWhs)\n10/08/2026 12:00:00,.05\n')
        energy.import_emporia_csv(str(path))
        self.configure()
        writer = energy._connect()  # Private handle opened before guarded conversion.
        self.addCleanup(writer.close)
        conn = energy._connect()
        _convert(conn, 'America/New_York', 'America/New_York', 'a' * 64)
        conn.commit()
        conn.close()
        with self.assertRaises(RuntimeError):
            energy._connect()
        with patch.object(energy, '_connect', return_value=writer):
            with self.assertRaisesRegex(ValueError, 'policy changed'):
                self.reserve()
        # Fresh private UTC fixture: no ordinary writable-UTC guard bypass in app code.
        energy.DB_PATH = str(self.root / 'utc.db')
        energy.ensure_table()
        self.discover()
        energy.import_emporia_csv(str(path))
        writers = [energy._connect() for _ in range(5)]
        self.addCleanup(lambda: [writer.close() for writer in writers])
        conn = energy._connect()
        _convert(conn, 'America/New_York', 'America/New_York', 'b' * 64)
        conn.commit()
        conn.close()
        with patch.object(energy, '_connect', side_effect=lambda: writers.pop()):
            self.configure(legacy_storage_timezone=None)
            self.assertEqual(self.run_collection()['outcomes'], ['complete'])
            self.assertTrue(all(row['timestamp'].endswith('+00:00') for row in self.rows('readings')))

    def test_cli_status_is_read_only_and_run_without_configuration_never_logs_in(self):
        script = Path(__file__).resolve().parents[1] / 'scripts/collect_completed_history.py'
        conn = energy._connect()
        conn.close()
        database = Path(energy.DB_PATH)
        before = database.read_bytes()
        command = [sys.executable, str(script), '--database', str(database)]
        result = subprocess.run([*command, 'status'], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['channels'], [])
        self.assertEqual(database.read_bytes(), before)
        run = subprocess.run([*command, 'run'], capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 1)
        self.assertFalse((self.root / 'keys.json').exists())

    def test_cli_rejects_missing_confirmation_and_unsafe_database(self):
        script = Path(__file__).resolve().parents[1] / 'scripts/collect_completed_history.py'
        database = Path(energy.DB_PATH)
        command = [sys.executable, str(script), '--database', str(database)]
        no_confirmation = subprocess.run([*command, 'configure', '--device', 'QA', '--channel', '1',
            '--start', self.start.isoformat(), '--requests-hour', '20', '--requests-day', '40'],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(no_confirmation.returncode, 2)
        link = self.root / 'linked.db'
        link.symlink_to(database)
        result = subprocess.run([sys.executable, str(script), '--database', str(link), 'status'],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.rows('energy_history_channels'), [])

    def test_promoted_snapshot_selects_active_device_before_first_history_window(self):
        self.configure()
        channels = {'1': SimpleNamespace(name='Pump', usage=.05, timestamp=self.now)}
        vue = Mock(get_device_list_usage=Mock(return_value={'QA': SimpleNamespace(channels=channels)}))
        with patch.object(energy, '_last_compaction', time.monotonic()):
            energy.poll_and_store(vue, ['QA'])
        self.assertEqual(self.rows('readings'), [])
        self.assertEqual(energy.get_active_device_gid(), 'QA')
        with patch.object(energy, '_load_settings', return_value={'primary_device_gid': 'QA'}):
            self.assertEqual(energy.get_active_device_gid(), 'QA')

    def test_poller_invokes_scheduler_only_when_explicitly_enabled_and_failure_is_separate(self):
        for flag in ('0', '1'):
            with patch.dict(energy.os.environ, {'EMPORIA_COMPLETED_HISTORY': flag}), \
                    patch.object(energy, 'login_vue', return_value=self.vue), \
                    patch.object(energy, 'get_devices_with_channels', return_value=(['QA'], {})), \
                    patch.object(energy, 'poll_and_store'), patch.object(energy, 'write_poller_status') as health, \
                    patch.object(energy, 'compact_reading_journal', return_value=False), \
                    patch.object(energy.time, 'sleep', side_effect=SystemExit), \
                    patch.object(energy, 'run_completed_collection', side_effect=RuntimeError('fixture')) as collect:
                with self.assertRaises(SystemExit):
                    energy._run_continuous()
                self.assertEqual(collect.call_count, int(flag))
                self.assertTrue(all(call.args[0] for call in health.call_args_list))

    def test_status_api_reports_scan_and_verified_progress_without_cloud_io(self):
        self.configure()
        self.run_collection(values=[])
        response = web.app.test_client().get('/api/completed-history/status')
        self.assertEqual(response.status_code, 200)
        channel = response.json['channels'][0]
        self.assertNotEqual(channel['scan_cursor_utc'], channel['verified_until_utc'])
        self.assertEqual(channel['job_counts'], {'gap': 1})
        self.assertFalse(response.json['continuous_capture_verified'])

    @unittest.skipUnless(shutil.which('swift'), 'Swift runtime required')
    def test_real_http_acquisition_publication_sync_and_native_history(self):
        root = Path(__file__).resolve().parents[1]
        now = datetime.now(timezone.utc)
        start = (now - timedelta(minutes=20)).replace(second=0, microsecond=0)
        energy.configure_completed_collection('QA', '1', start, legacy_storage_timezone='America/New_York',
            window_minutes=10, requests_hour=20, requests_day=40)
        provider_code = '''
from datetime import datetime
from flask import Flask, request, jsonify
from werkzeug.serving import make_server
app=Flask(__name__)
@app.route('/AppAPI')
def chart():
    assert request.headers.get('Authorization')=='Bearer '+ 'a'*32
    assert request.args['deviceGid']=='QA' and request.args['channel']=='1'
    assert request.args['apiMethod']=='getChartUsage' and request.args['energyUnit']=='KilowattHours'
    start,end=(datetime.fromisoformat(request.args[key]) for key in ('start','end'))
    return jsonify(firstUsageInstant=start.isoformat(),usageList=[.05]*(int((end-start).total_seconds()//60)+1))
server=make_server('127.0.0.1',0,app)
print(server.server_port,flush=True)
server.serve_forever()
'''
        with (self.root / 'provider.log').open('w') as log:
            provider = subprocess.Popen([sys.executable, '-u', '-c', provider_code], cwd=root,
                                        stdout=subprocess.PIPE, stderr=log, text=True)
            try:
                port = provider.stdout.readline().strip()
                self.assertTrue(port.isdigit(), (self.root / 'provider.log').read_text())

                def get(method, path):
                    return requests.request(method, 'http://127.0.0.1:' + port + '/' + path,
                        headers={'Authorization': 'Bearer ' + 'a' * 32}, timeout=5)

                vue = SimpleNamespace(auth=SimpleNamespace(max_retry_attempts=1, request=get))
                self.assertEqual(energy.run_completed_collection(vue)['outcomes'], ['complete'])
            finally:
                provider.terminate()
                provider.wait(timeout=10)
                provider.stdout.close()
        self.assertEqual(len(self.rows('readings')), 10)  # Inclusive end excluded.
        source, cache = self.root / 'source.db', self.root / 'download.db'
        energy.backup_database(source)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        server_code = '''
import os,energy,web
from werkzeug.serving import make_server
original=energy._connect
energy._connect=lambda:original(os.environ['ARTIFACT'],read_only=True)
server=make_server('127.0.0.1',0,web.app)
print(server.server_port,flush=True)
server.serve_forever()
'''
        env = dict(os.environ, DB_PATH=str(self.root / 'server-boot.db'), ARTIFACT=str(source), ENERGY_SYNC_TOKEN='a' * 32)
        with (self.root / 'collector.log').open('w') as log:
            server = subprocess.Popen([sys.executable, '-u', '-c', server_code], cwd=root, env=env,
                                      stdout=subprocess.PIPE, stderr=log, text=True)
            try:
                port = server.stdout.readline().strip()
                self.assertTrue(port.isdigit(), (self.root / 'collector.log').read_text())
                command = [sys.executable, str(root / 'sync_history.py'), '--collector',
                           'http://127.0.0.1:' + port, '--cache', str(cache)]
                result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                state = json.loads(result.stdout)
            finally:
                server.terminate()
                server.wait(timeout=10)
                server.stdout.close()
        source_text = (root / 'EnergyMonitorApp/Sources/MenuPopover.swift').read_text()
        models = source_text[source_text.index('struct CircuitBucket:'):source_text.index('struct StoredMenuResponse:')]
        reader = source_text[source_text.index('struct DownloadedHistoryReader'):source_text.index('final class MenuMonitor:')]
        script = self.root / 'native.swift'
        script.write_text('import Foundation\nimport SQLite3\n' + models + reader + f'''
NSTimeZone.default = TimeZone(identifier:"America/New_York")!
let now = Date(timeIntervalSince1970:{now.timestamp()})
let reader = DownloadedHistoryReader(database:URL(fileURLWithPath:CommandLine.arguments[1]))
guard let (history,_) = reader.history(channel:"Pump",device:"QA",source:CommandLine.arguments[2],now:now),
      let kwh=history.windows[0].totalKwh, let cents=history.windows[0].totalCents else {{ fatalError("History unavailable") }}
precondition(abs(kwh - 0.5) < 0.000000001 && abs(cents - 11.29) < 0.000000001)
precondition(history.windows[0].readings == 10)
print("Scheduled chart HTTP/cache/native totals verified")
''')
        native = subprocess.run(['swift', str(script), str(cache), state['source_id']],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(native.returncode, 0, native.stdout + native.stderr)
        self.assertIn('Scheduled chart HTTP/cache/native totals verified', native.stdout)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), digest)
