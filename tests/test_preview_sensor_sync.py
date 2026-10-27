import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import energy
import preview_sensor_sync as sync


class PreviewSensorSyncTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.database = self.root / 'energy.db'
        self.env = patch.dict(os.environ, {'DB_PATH': str(self.database)})
        self.db = patch.object(energy, 'DB_PATH', str(self.database))
        self.env.start(); self.db.start()
        energy.ensure_table()
        (self.root / 'preview-only.json').write_text(json.dumps({'purpose': 'house-control-local-preview'}))
        self.plan = self.root / 'house-control.json'
        self.plan.write_text('{"plan":"local geometry", "sensors":["local placements"]}')
        self.snapshot = {table: [] for table in sync.TABLES}
        self.snapshot['aqara_local_observations'] = [dict(zip(sync.TABLES['aqara_local_observations'],
            ('aqara-1', datetime.now(timezone.utc).isoformat(), 'Real sensor', '', 20, 0, 80, 1, 'matter_event'), strict=True))]
        self.snapshot['aqara_local_labels'] = [{'device_id': 'aqara-1', 'name': 'Barn'}]
        self.snapshot['power'] = {
            'active_device_gid': 'live-panel', 'captured_at': datetime.now(timezone.utc).isoformat(),
            'poller_ok': True, 'mains_c_no_ct': True, 'latest': [], 'summary': [],
        }
        for name, value in [('Main', .1), ('Mains_A', .04), ('Mains_B', .06),
                            ('Mains_C', 0), ('Kitchen', 0), ('Missing', None)]:
            self.snapshot['power']['latest'].append({
                'device_gid': 'live-panel', 'channel_name': name, 'channel_num': None,
                'usage_kwh': value, 'cost_cents': 0, 'timestamp': datetime.now().isoformat(),
                'age_seconds': 1,
            })
            self.snapshot['power']['summary'].append({
                'channel_name': name, 'total_kwh': value, 'total_cents': 0, 'readings': 1,
            })

    def tearDown(self):
        self.db.stop(); self.env.stop(); self.directory.cleanup()

    def test_refresh_is_idempotent_and_never_changes_local_plan_or_measurement_time(self):
        original = self.plan.read_bytes()
        for _ in range(2):
            sync.import_snapshot(self.snapshot)
        conn = energy._connect()
        try:
            rows = conn.execute('SELECT * FROM aqara_local_observations').fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['timestamp'], self.snapshot['aqara_local_observations'][0]['timestamp'])
            self.assertEqual(rows[0]['humidity'], 0)
        finally:
            conn.close()
        self.assertEqual(self.plan.read_bytes(), original)

    def test_missing_preview_marker_refuses_writes(self):
        (self.root / 'preview-only.json').unlink()
        with self.assertRaises(ValueError):
            sync.import_snapshot(self.snapshot)

    def test_incomplete_invalid_and_failed_transaction_leave_previous_observations(self):
        sync.import_snapshot(self.snapshot)
        for invalid in ({}, {**self.snapshot, 'climate_readings': [{}]},
                        {**self.snapshot, 'aqara_local_labels': [self.snapshot['aqara_local_labels'][0]] * 2}):
            with self.assertRaises((ValueError, sqlite3.IntegrityError)):
                sync.import_snapshot(invalid)
            conn = energy._connect()
            try:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM aqara_local_observations').fetchone()[0], 1)
            finally:
                conn.close()

    def test_failed_ssh_retains_observations_and_last_success_and_never_prompts(self):
        completed = subprocess.CompletedProcess([], 0, stdout=json.dumps(self.snapshot), stderr='')
        with patch.object(sync.subprocess, 'run', return_value=completed) as run:
            success = sync.refresh('user@192.0.2.1', 'energy-monitor')
        self.assertTrue(success['ok'])
        self.assertIn('BatchMode=yes', run.call_args.args[0])
        self.assertIn('StrictHostKeyChecking=yes', run.call_args.args[0])
        self.assertIn('mode=ro', run.call_args.kwargs['input'])
        with patch.object(sync.subprocess, 'run', side_effect=subprocess.TimeoutExpired('ssh', 20)):
            failed = sync.refresh('user@192.0.2.1', 'energy-monitor')
        self.assertFalse(failed['ok'])
        self.assertEqual(failed['last_success'], success['last_success'])
        self.assertFalse(sync.sync_status()['ok'])
        self.assertEqual((self.root / 'sensor-sync-status.json').stat().st_mode & 0o777, 0o600)

    def test_expired_worker_status_never_looks_connected(self):
        (self.root / 'sensor-sync-status.json').write_text(json.dumps({
            'ok': True, 'last_success': (datetime.now(timezone.utc)-timedelta(seconds=91)).isoformat()}))
        self.assertFalse(sync.sync_status()['ok'])

    def test_remote_destination_cannot_inject_options_or_shell_commands(self):
        with patch.object(sync.subprocess, 'run') as run:
            for host, container in [('-oProxyCommand=bad', 'energy-monitor'),
                                    ('user@host;bad', 'energy-monitor'), ('user@host', 'a;bad')]:
                with self.assertRaises(ValueError):
                    sync.refresh(host, container)
            run.assert_not_called()

    def cache_power(self):
        sync.import_snapshot(self.snapshot)
        (self.root / 'sensor-sync-status.json').write_text(json.dumps({
            'ok': True, 'last_success': datetime.now(timezone.utc).isoformat()}))

    def test_live_power_is_panel_scoped_preserves_zero_and_does_not_sum_main_with_legs(self):
        self.cache_power()
        panel = sync.power_snapshot()
        self.assertTrue(panel['menu']['online'])
        self.assertEqual(panel['menu']['current_watts'], 6000)
        self.assertEqual(panel['menu']['active_device_gid'], 'live-panel')
        self.assertEqual([r['channel_name'] for r in panel['mains']], ['Main', 'Mains_A', 'Mains_B'])
        kitchen = next(row for row in panel['menu']['breaker_slots'] if row['channel_name'] == 'Kitchen')
        missing = next(row for row in panel['menu']['breaker_slots'] if row['channel_name'] == 'Missing')
        self.assertEqual(kitchen['watts'], 0)
        self.assertIsNone(missing['watts'])
        self.assertEqual(sync.power_catalog(), {'live-panel': {'Kitchen', 'Missing'}})
        self.assertEqual({row['channel_name'] for row in panel['summary']}, {'Kitchen', 'Missing'})

    def test_transport_failure_expired_cache_stale_measurement_and_stopped_poller_clear_power(self):
        for reason in ('transport', 'cache', 'measurement', 'poller'):
            self.snapshot['power']['poller_ok'] = True
            self.snapshot['power']['captured_at'] = datetime.now(timezone.utc).isoformat()
            for row in self.snapshot['power']['latest']:
                row['age_seconds'] = 1
            if reason == 'cache':
                self.snapshot['power']['captured_at'] = (datetime.now(timezone.utc)-timedelta(seconds=91)).isoformat()
            if reason == 'measurement':
                for row in self.snapshot['power']['latest']:
                    row['age_seconds'] = 181
            if reason == 'poller':
                self.snapshot['power']['poller_ok'] = False
            self.cache_power()
            if reason == 'transport':
                (self.root / 'sensor-sync-status.json').write_text(json.dumps({'ok': False, 'last_success': None}))
            with self.subTest(reason=reason):
                panel = sync.power_snapshot()
                self.assertFalse(panel['menu']['online'])
                self.assertIsNone(panel['menu']['current_watts'])
                self.assertTrue(all(row['watts'] is None for row in panel['mains']))
                self.assertTrue(all(row['watts'] is None for row in panel['menu']['breaker_slots']))
                self.assertTrue(panel['summary'])  # Clearly retained historical data, not fake zero.

    def test_invalid_cross_panel_power_rejects_the_whole_refresh_without_changing_cache(self):
        self.cache_power()
        previous = (self.root / 'power-cache.json').read_bytes()
        self.snapshot['power']['latest'][0]['device_gid'] = 'other-panel'
        with self.assertRaises(ValueError):
            sync.import_snapshot(self.snapshot)
        self.assertEqual((self.root / 'power-cache.json').read_bytes(), previous)

    def test_live_mapping_can_use_a_new_source_panel_without_importing_history_or_remote_layouts(self):
        import house_control
        self.cache_power()
        # Remove the dummy plan fixture; use the real private config API.
        self.plan.unlink()
        config = house_control.load_config()
        before_snapshot = (self.root / 'power-cache.json').read_bytes()
        payload = {'plan': config['plan'], 'sensors': [], 'revision': config['revision'],
                   'bindings': [{'device_gid': 'live-panel', 'channel_name': 'Kitchen', 'room_id': 'kitchen'}]}
        saved = house_control.save_config(payload, known_circuits=sync.power_catalog())
        self.assertEqual(saved['bindings'], payload['bindings'])
        self.assertEqual((self.root / 'power-cache.json').read_bytes(), before_snapshot)
        plan_bytes = self.plan.read_bytes()
        sync.import_snapshot(self.snapshot)
        self.assertEqual(self.plan.read_bytes(), plan_bytes)

    def test_exporter_reads_actual_schema_and_rolling_totals_without_writing_source_data(self):
        conn = energy._connect()
        now = datetime.now().isoformat()
        with conn:
            conn.execute('INSERT INTO readings(timestamp,device_gid,channel_name,usage_kwh,cost_cents) VALUES (?,?,?,?,?)',
                         (now, 'source-panel', 'Main', .2, 4))
            conn.execute('INSERT INTO latest_channel_snapshot VALUES(?,?,?,?,?,?)',
                         ('source-panel', 'Main', None, .2, 4, now))
        conn.close()
        (self.root / 'poller_status.json').write_text(json.dumps({'ok': True, 'timestamp': now}))
        script = sync.REMOTE_SCRIPT.replace('/var/lib/energy-monitor/collector', str(self.root))
        import sys
        result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, check=True)
        exported = json.loads(result.stdout)
        self.assertTrue(exported['power']['poller_ok'])
        self.assertEqual(exported['power']['active_device_gid'], 'source-panel')
        self.assertEqual(exported['power']['summary'][0]['total_kwh'], .2)
        self.assertEqual(sync.validate_power(exported['power']), exported['power'])

    def test_exporter_ages_naive_stamps_in_the_recorded_reporting_zone_not_the_host_clock(self):
        # #127: a collector relocated to a host in another zone must keep one clock.
        energy.set_reporting_timezone('Asia/Tokyo')
        now = datetime.now(ZoneInfo('Asia/Tokyo')).replace(tzinfo=None).isoformat()
        conn = energy._connect()
        with conn:
            conn.execute('INSERT INTO readings(timestamp,device_gid,channel_name,usage_kwh,cost_cents) VALUES (?,?,?,?,?)',
                         (now, 'source-panel', 'Main', .2, 4))
            conn.execute('INSERT INTO latest_channel_snapshot VALUES(?,?,?,?,?,?)',
                         ('source-panel', 'Main', None, .2, 4, now))
        conn.close()
        (self.root / 'poller_status.json').write_text(json.dumps({'ok': True, 'timestamp': now}))
        script = sync.REMOTE_SCRIPT.replace('/var/lib/energy-monitor/collector', str(self.root))
        import sys
        result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, check=True,
                                env={**os.environ, 'TZ': 'Pacific/Honolulu'})
        power = json.loads(result.stdout)['power']
        self.assertTrue(power['poller_ok'])
        self.assertLess(abs(power['latest'][0]['age_seconds']), 60)
        self.assertEqual(power['summary'][0]['total_kwh'], .2)

    def test_actual_cloud_legs_replace_imports_without_splitting_total_or_touching_plan(self):
        self.cache_power()
        before = self.plan.read_bytes()
        now = datetime.now(timezone.utc)
        cloud = [{'channel_name': name, 'usage_kwh': value, 'timestamp': now.isoformat(),
                  'age_seconds': 60} for name, value in
                 (('Main', .008), ('Mains_A', .003), ('Mains_B', .005))]
        with patch('preview_mains.readings', return_value=cloud):
            state = sync.power_snapshot(now)
        self.assertEqual([r['watts'] for r in state['mains']], [480, 180, 300])
        self.assertEqual(state['menu']['current_watts'], 480)
        self.assertIsNone(state['mains'][1]['kwh_24h'])
        self.assertEqual(self.plan.read_bytes(), before)
        cloud[1]['age_seconds'] = 200
        with patch('preview_mains.readings', return_value=cloud):
            self.assertIsNone(sync.power_snapshot(now)['mains'][1]['watts'])
