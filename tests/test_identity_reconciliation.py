import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import energy
import web
from identity_reconciliation import review_identity_copy


class IdentityReconciliationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        db = patch.object(energy, 'DB_PATH', str(self.root / 'collector.db'))
        db.start()
        self.addCleanup(db.stop)
        rate = patch.object(energy, 'RATE_CENTS', 22.58)
        rate.start()
        self.addCleanup(rate.stop)
        energy.ensure_table()
        self.export = self.root / '8C9E94-Barn-1H.csv'
        self.export.write_text('Time Bucket (America/New_York),Barn-Pump (kWhs)\n'
                               '10/08/2026 12:00:00,3\n10/08/2026 13:00:00,0\n10/08/2026 14:00:00,-1\n')
        energy.import_emporia_csv(str(self.export), device_gid='8C9E94')
        self.discover()
        self.archive = self.root / 'archive.db'
        self.destination = self.root / 'reconciled.db'
        self.snapshot()

    def discover(self, gid=551741, manufacturer='F2518A001AECE3348C9E94'):
        energy.get_devices_with_channels(Mock(get_devices=Mock(return_value=[SimpleNamespace(
            device_gid=gid, manufacturer_id=manufacturer, device_name='Barn', channels=[])])))

    def snapshot(self):
        self.archive.unlink(missing_ok=True)
        energy.backup_database(self.archive)
        self.digest = self.hash(self.archive)

    @staticmethod
    def hash(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def review(self, *, destination=None, plan=None, snapshot=None, rollback=None, **extra):
        snapshot = snapshot or self.archive
        return review_identity_copy(snapshot, destination=destination,
            expected_sha256=self.hash(snapshot), source_gid=None if rollback else '8C9E94',
            canonical_gid=None if rollback else '551741', reviewed_plan_sha256=plan,
            rollback_review_id=rollback, **extra)

    def apply(self):
        plan = self.review()
        self.assertTrue(plan['candidate_unblocked'], plan)
        result = self.review(destination=self.destination, plan=plan['plan_sha256'])
        self.assertEqual(self.hash(self.archive), self.digest)
        return result

    def rows(self, path, table):
        conn = energy._connect(path, read_only=True)
        try:
            return [dict(row) for row in conn.execute(f'SELECT * FROM {table} ORDER BY 1')]
        finally:
            conn.close()

    def test_audit_is_read_only_and_application_requires_exact_review(self):
        result = self.review()
        self.assertTrue(result['candidate_unblocked'], result)
        self.assertEqual(result['candidate_readings'], 3)
        self.assertFalse(result['artifact_published'])
        self.assertEqual(self.hash(self.archive), self.digest)
        self.assertFalse(self.destination.exists())
        for approval in (None, '0' * 64):
            with self.assertRaises(ValueError):
                self.review(destination=self.destination, plan=approval)
        self.assertFalse(self.destination.exists())

    def test_apply_preserves_source_price_ids_and_unrelated_state(self):
        result = self.apply()
        before = self.rows(self.archive, 'readings')
        after = self.rows(self.destination, 'readings')
        self.assertEqual([{**row, 'device_gid': '551741'} for row in before], after)
        for table in ('csv_source_batches', 'csv_source_observations', 'csv_source_bindings',
                      'csv_reading_projection', 'collector_identity', 'reading_stream_generation', 'circuit_labels'):
            self.assertEqual(self.rows(self.archive, table), self.rows(self.destination, table), table)
        changes = self.rows(self.destination, 'reading_changes')
        original = self.rows(self.archive, 'reading_changes')
        self.assertEqual(changes[:len(original)], original)
        self.assertEqual(len(changes) - len(original), 3)
        self.assertEqual({row['device_gid'] for row in changes[len(original):]}, {'551741'})
        self.assertEqual(result['canonical_upserts_appended'], 3)
        self.assertEqual(self.destination.stat().st_mode & 0o777, 0o600)
        self.assertFalse(result['production_activation_performed'])
        self.assertEqual({row['device_gid'] for row in self.rows(self.destination, 'latest_channel_snapshot')}, {'551741'})
        self.assertEqual({row['device_gid'] for row in self.rows(self.destination, 'device_capabilities')}, {'551741'})

    def test_rollback_is_append_only_and_restores_exact_original_readings(self):
        result = self.apply()
        plan = self.review(snapshot=self.destination, rollback=result['review_id'])
        self.assertTrue(plan['candidate_unblocked'], plan)
        restored = self.root / 'restored.db'
        rollback = self.review(snapshot=self.destination, destination=restored,
                              rollback=result['review_id'], plan=plan['plan_sha256'])
        self.assertEqual(self.rows(self.archive, 'readings'), self.rows(restored, 'readings'))
        old_reviews = self.rows(self.destination, 'csv_identity_reviews')
        restored_reviews = {row['id']: row for row in self.rows(restored, 'csv_identity_reviews')}
        for row in old_reviews:
            self.assertEqual(restored_reviews[row['id']], row)
        for table in ('latest_channel_snapshot', 'device_capabilities'):
            self.assertEqual(self.rows(self.archive, table), self.rows(restored, table))
        self.assertEqual(rollback['canonical_upserts_appended'], 3)
        repeated = self.review(snapshot=restored, rollback=result['review_id'])
        self.assertFalse(repeated['candidate_unblocked'])

    def test_collision_and_unknown_live_bounds_block_without_artifact(self):
        for stamp in ('2026-10-08T12:00:00', '2026-10-09T12:00:00'):
            conn = energy._connect()
            try:
                conn.execute('INSERT INTO readings(timestamp,device_gid,channel_name,usage_kwh,cost_cents) VALUES (?,\'551741\',\'Pump\',.05,1)', (stamp,))
                conn.commit()
            finally:
                conn.close()
            self.snapshot()
            plan = self.review()
            self.assertFalse(plan['candidate_unblocked'])
            with self.assertRaises(ValueError):
                self.review(destination=self.destination, plan=plan['plan_sha256'])
            self.assertFalse(self.destination.exists())
            self.assertEqual(self.hash(self.archive), self.digest)

    def test_unowned_alias_history_and_ambiguous_discovery_are_not_guessed(self):
        self.discover(999, 'OTHER8C9E94')
        self.snapshot()
        self.assertFalse(self.review()['candidate_unblocked'])
        conn = energy._connect()
        try:
            conn.execute("INSERT INTO readings(timestamp,device_gid,channel_name,usage_kwh,cost_cents) VALUES ('2026-10-08T16:00:00','8C9E94','Other',1,2)")
            conn.commit()
        finally:
            conn.close()
        self.snapshot()
        self.assertFalse(self.review()['candidate_unblocked'])

    def test_wrong_raw_source_and_projection_drift_reject(self):
        conn = energy._connect()
        try:
            conn.execute('UPDATE readings SET cost_cents=999 WHERE id=1')
            conn.commit()
        finally:
            conn.close()
        self.snapshot()
        self.assertFalse(self.review()['candidate_unblocked'])

    def test_rollback_rejects_changed_projection_without_partial_reversal(self):
        result = self.apply()
        conn = energy._connect(self.destination)
        try:
            conn.execute('UPDATE readings SET usage_kwh=99 WHERE id=1')
            conn.commit()
            conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            conn.execute('PRAGMA journal_mode=DELETE')
        finally:
            conn.close()
        plan = self.review(snapshot=self.destination, rollback=result['review_id'])
        self.assertFalse(plan['candidate_unblocked'])

    def test_publication_failure_rolls_back_and_cleans_private_working_copy(self):
        plan = self.review()
        with patch.object(energy, '_save_device_capabilities_with_conn', side_effect=RuntimeError('failure')):
            with self.assertRaises(RuntimeError):
                self.review(destination=self.destination, plan=plan['plan_sha256'])
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.hash(self.archive), self.digest)
        self.assertEqual(list(self.root.glob('.identity-reconciliation-*')), [])

    def test_reconciled_sources_participate_in_subsequent_real_import_projection(self):
        self.apply()
        original = energy._connect
        # The route writes only the owned working copy, never the archived backup.
        with patch.object(energy, 'DB_PATH', str(self.destination)):
            response = web.app.test_client().post('/api/import-csv', data={
                'file': (io.BytesIO(self.export.read_bytes()), self.export.name),
            })
            self.assertEqual(response.status_code, 200, response.get_json())
            self.assertEqual(response.get_json()['imported'], 0)
            history = energy.get_circuit_history('Pump', '551741', now=datetime(2026, 10, 8, 15))
            self.assertEqual(history['windows'][0]['total_kwh'], 2)
        self.assertIs(energy._connect, original)

    def test_source_hash_private_paths_and_overwrite_guards(self):
        for digest in ('bad', '0' * 64):
            with self.assertRaises(ValueError):
                review_identity_copy(self.archive, expected_sha256=digest,
                                     source_gid='8C9E94', canonical_gid='551741')
        plan = self.review()
        self.destination.write_bytes(b'preserve')
        with self.assertRaises(ValueError):
            self.review(destination=self.destination, plan=plan['plan_sha256'])
        self.assertEqual(self.destination.read_bytes(), b'preserve')
        source_link = self.root / 'source-link.db'
        source_link.symlink_to(self.archive)
        with self.assertRaises(ValueError):
            self.review(snapshot=source_link)
        Path(str(self.archive) + '-wal').write_bytes(b'not standalone')
        with self.assertRaises(ValueError):
            self.review()

    def test_cli_isolates_bootstrap_and_requires_review_before_apply(self):
        script = Path(__file__).resolve().parents[1] / 'scripts/reconcile_csv_identity.py'
        args = [sys.executable, str(script), '--snapshot', str(self.archive),
                '--expected-sha256', self.digest, '--source-gid', '8C9E94', '--canonical-gid', '551741']
        result = subprocess.run(args, env=dict(os.environ, DB_PATH=str(self.archive)),
                                text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual(self.hash(self.archive), self.digest)
        result = subprocess.run([*args, '--destination', str(self.destination),
                                 '--reviewed-plan-sha256', plan['plan_sha256']],
                                text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)['artifact_published'])

    def test_unrelated_and_newer_retained_target_snapshots_survive(self):
        conn = energy._connect()
        try:
            for channel in ('Main', 'Pump'):
                energy._upsert_latest_snapshot_with_conn(conn, device_gid='551741',
                    channel_name=channel, channel_num=None, usage_kwh=.05, cost_cents=1,
                    timestamp='2026-10-09T12:00:00', measurement_seconds=60,
                    measurement_source='emporia_minute', provider_timestamp='2026-10-09T16:00:00+00:00')
            conn.commit()
        finally:
            conn.close()
        self.snapshot()
        before = [row for row in self.rows(self.archive, 'latest_channel_snapshot') if row['device_gid']=='551741']
        self.apply()
        self.assertEqual(self.rows(self.destination, 'latest_channel_snapshot'), before)

    def test_unowned_alias_snapshot_is_not_silently_dropped(self):
        conn = energy._connect()
        try:
            energy._upsert_latest_snapshot_with_conn(conn, device_gid='8C9E94',
                channel_name='Unknown', channel_num=None, usage_kwh=1, cost_cents=2,
                timestamp='2026-10-09T12:00:00')
            conn.commit()
        finally:
            conn.close()
        self.snapshot()
        self.assertFalse(self.review()['candidate_unblocked'])

    def test_real_finer_import_uses_reconciled_batch_and_invalidates_old_rollback(self):
        result = self.apply()
        content = ('Time Bucket (America/New_York),Barn-Pump (kWhs)\n' + ''.join(
            f'10/08/2026 12:{minute:02d}:00,0.05\n' for minute in range(60)))
        with patch.object(energy, 'DB_PATH', str(self.destination)):
            response = web.app.test_client().post('/api/import-csv', data={
                'file': (io.BytesIO(content.encode()), '8C9E94-Barn-1MIN.csv')})
            self.assertEqual(response.status_code, 200, response.get_json())
            self.assertEqual(response.get_json()['imported'], 60)
            history = energy.get_circuit_history('Pump', '551741', now=datetime(2026, 10, 8, 15))
            self.assertAlmostEqual(history['windows'][0]['total_kwh'], 2)
            self.assertAlmostEqual(history['windows'][0]['total_cents'], 2 * 22.58)
            conn = energy._connect()
            conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            conn.execute('PRAGMA journal_mode=DELETE')
            conn.close()
        plan = self.review(snapshot=self.destination, rollback=result['review_id'])
        self.assertFalse(plan['candidate_unblocked'])

    def test_original_cell_corruption_and_file_corruption_are_detected(self):
        for statement in ("UPDATE csv_source_observations SET raw_value='4' WHERE sequence=1",
                          "UPDATE csv_source_batches SET content=x'00'"):
            path = self.root / ('corrupt-observation.db' if 'observations' in statement else 'corrupt-file.db')
            path.write_bytes(self.archive.read_bytes())
            path.chmod(0o600)
            conn = energy._connect(path)
            try:
                for trigger in ('csv_observations_immutable_update', 'csv_batches_immutable_update'):
                    conn.execute(f'DROP TRIGGER {trigger}')
                conn.execute(statement)
                conn.commit()
                conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
                conn.execute('PRAGMA journal_mode=DELETE')
            finally:
                conn.close()
            self.assertFalse(self.review(snapshot=path)['candidate_unblocked'])

    def test_private_regular_file_hardlink_and_destination_permissions(self):
        plan = self.review()
        self.archive.chmod(0o644)
        with self.assertRaises(ValueError):
            self.review()
        self.archive.chmod(0o600)
        link = self.root / 'hardlink.db'
        os.link(self.archive, link)
        with self.assertRaises(ValueError):
            self.review()
        link.unlink()
        public = self.root / 'public'
        public.mkdir(mode=0o755)
        public.chmod(0o755)
        with self.assertRaises(ValueError):
            self.review(destination=public / 'copy.db', plan=plan['plan_sha256'])
        fifo = self.root / 'fifo.db'
        os.mkfifo(fifo, mode=0o600)
        with self.assertRaises(ValueError):
            review_identity_copy(fifo, expected_sha256='0' * 64,
                                 source_gid='8C9E94', canonical_gid='551741')

    def test_source_drift_and_failed_atomic_publication_never_publish(self):
        plan = self.review()
        from identity_reconciliation import _apply
        def drift(*args):
            result = _apply(*args)
            with self.archive.open('ab') as handle:
                handle.write(b'drift')
            return result
        with patch('identity_reconciliation._apply', side_effect=drift):
            with self.assertRaises(RuntimeError):
                self.review(destination=self.destination, plan=plan['plan_sha256'])
        self.assertFalse(self.destination.exists())
        self.snapshot()
        plan = self.review()
        with patch('verified_snapshot.os.link', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.review(destination=self.destination, plan=plan['plan_sha256'])
        self.assertEqual(self.hash(self.archive), self.digest)
        self.assertFalse(self.destination.exists())

    def test_review_tables_reject_rewriting_and_deleting_history(self):
        self.apply()
        conn = energy._connect(self.destination)
        try:
            for table in ('csv_identity_reviews', 'csv_identity_review_batches', 'csv_identity_review_rows'):
                for verb in (f'DELETE FROM {table}', f'UPDATE {table} SET rowid=rowid'):
                    with self.assertRaises(sqlite3.IntegrityError) as failure:
                        conn.execute(verb)
                    self.assertIn('immutable', str(failure.exception))
            conn.rollback()
        finally:
            conn.close()

    def test_utc_activation_guard_remains_in_force_after_identity_reconciliation(self):
        result = self.apply()
        from utc_migration import rehearse_utc_copy
        converted = self.root / 'identity-then-utc.db'
        rehearse_utc_copy(self.destination, converted, expected_sha256=result['artifact_sha256'],
                         legacy_timezone='America/New_York', reporting_timezone='America/New_York')
        with self.assertRaises(RuntimeError):
            energy._connect(converted)
        with self.assertRaises(RuntimeError):
            self.review(snapshot=converted, rollback=result['review_id'])
        self.assertEqual(self.hash(self.destination), result['artifact_sha256'])

    def test_verified_adjacent_target_is_allowed_but_different_key_overlap_blocks(self):
        conn = energy._connect()
        try:
            conn.execute("""INSERT INTO readings(timestamp,device_gid,channel_name,usage_kwh,cost_cents,
                measurement_seconds,measurement_source,source_timezone)
                VALUES ('2026-10-08T11:00:00','551741','Pump',1,22.58,3600,'csv_energy','America/New_York')""")
            conn.commit()
        finally:
            conn.close()
        self.snapshot()
        self.apply()
        self.assertEqual(len(self.rows(self.destination, 'readings')), 4)
        conn = energy._connect()
        try:
            conn.execute("""INSERT INTO readings(timestamp,device_gid,channel_name,usage_kwh,cost_cents,
                measurement_seconds,measurement_source,source_timezone)
                VALUES ('2026-10-08T12:30:00','551741','Pump',.1,2.258,900,'csv_energy','America/New_York')""")
            conn.commit()
        finally:
            conn.close()
        self.snapshot()
        plan = self.review()
        self.assertFalse(plan['candidate_unblocked'])
        self.assertIn('cross_device_coverage_overlap', plan['blocking_counts'])
        self.assertNotIn('canonical_key_collision', plan['blocking_counts'])

    def test_empty_source_batch_can_be_bound_without_inventing_readings(self):
        conn = energy._connect()
        try:
            conn.execute('DELETE FROM readings')
            conn.execute('DELETE FROM latest_channel_snapshot')
            conn.commit()
        finally:
            conn.close()
        self.snapshot()
        result = self.apply()
        self.assertEqual(result['canonical_upserts_appended'], 0)
        self.assertEqual(self.rows(self.destination, 'readings'), [])

    def test_real_http_cache_resume_and_native_read_after_binding_and_reversal(self):
        before = energy.get_reading_changes()
        cache = self.root / 'downloaded.db'
        with patch.object(energy, 'DB_PATH', str(cache)):
            energy.ensure_table()
            state = energy.apply_reading_changes(before)
        result = self.apply()
        plan = self.review(snapshot=self.destination, rollback=result['review_id'])
        restored = self.root / 'http-restored.db'
        self.review(snapshot=self.destination, rollback=result['review_id'], destination=restored,
                    plan=plan['plan_sha256'])
        root = Path(__file__).resolve().parents[1]
        code = '''import os,energy,web
from werkzeug.serving import make_server
original=energy._connect
energy._connect=lambda: original(os.environ['IDENTITY_ARTIFACT'],read_only=True)
server=make_server('127.0.0.1',0,web.app)
print(server.server_port,flush=True)
server.serve_forever()
'''
        for artifact, expected_gid in ((self.destination, '551741'), (restored, '8C9E94')):
            with self.subTest(gid=expected_gid):
                env = dict(os.environ, DB_PATH=str(self.root / f'http-boot-{expected_gid}.db'),
                           IDENTITY_ARTIFACT=str(artifact), ENERGY_SYNC_TOKEN='a' * 32)
                digest = self.hash(artifact)
                with (self.root / f'http-{expected_gid}.log').open('w') as log:
                    server = subprocess.Popen([sys.executable, '-u', '-c', code], cwd=root, env=env,
                                              stdout=subprocess.PIPE, stderr=log, text=True)
                    try:
                        port = server.stdout.readline().strip()
                        self.assertTrue(port.isdigit())
                        command = [sys.executable, str(root / 'sync_history.py'), '--collector',
                                   f'http://127.0.0.1:{port}', '--cache', str(cache)]
                        downloaded = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
                        self.assertEqual(downloaded.returncode, 0, downloaded.stderr)
                        after = json.loads(downloaded.stdout)
                        self.assertEqual(after['source_id'], state['source_id'])
                        self.assertEqual(after['generation_id'], state['generation_id'])
                        self.assertGreater(after['cursor'], state['cursor'])
                        self.assertIsNotNone(after['synchronized_at'])
                        state = after
                    finally:
                        server.terminate()
                        server.wait(timeout=10)
                        server.stdout.close()
                self.assertEqual(self.hash(artifact), digest)
                rows = self.rows(cache, 'sync_cached_readings')
                self.assertEqual(len(rows), 3)
                self.assertEqual({row['device_gid'] for row in rows}, {expected_gid})
                self.assertEqual(sum(row['usage_kwh'] for row in rows), 2)
                self.assertAlmostEqual(sum(row['cost_cents'] for row in rows), 2 * 22.58)
                if shutil.which('swift'):
                    swift = (root / 'EnergyMonitorApp/Sources/MenuPopover.swift').read_text()
                    models = swift[swift.index('struct CircuitBucket:'):swift.index('struct StoredMenuResponse:')]
                    reader = swift[swift.index('struct DownloadedHistoryReader'):swift.index('final class MenuMonitor:')]
                    harness = 'import Foundation\nimport SQLite3\n' + models + reader + '''
let reader=DownloadedHistoryReader(database:URL(fileURLWithPath:CommandLine.arguments[1]))
let formatter=DateFormatter()
formatter.locale=Locale(identifier:"en_US_POSIX")
formatter.dateFormat="yyyy-MM-dd'T'HH:mm:ss"
let now=formatter.date(from:"2026-10-08T15:00:00")!
guard let (history,_) = reader.history(channel:"Pump",device:CommandLine.arguments[2],source:CommandLine.arguments[3],now:now) else {fatalError("Missing canonical history")}
precondition(history.windows[0].totalKwh == 2)
guard let cents=history.windows[0].totalCents else {fatalError("Missing stored costs")}
precondition(abs(cents - 45.16) < 0.000001)
print("Identity history verified")
'''
                    script = self.root / 'identity.swift'
                    script.write_text(harness)
                    native = subprocess.run(['swift', str(script), str(cache), expected_gid, state['source_id']],
                                            capture_output=True, text=True, timeout=60)
                    self.assertEqual(native.returncode, 0, native.stdout + native.stderr)
                    self.assertIn('Identity history verified', native.stdout)


if __name__ == '__main__':
    unittest.main()
