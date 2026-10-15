import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

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
