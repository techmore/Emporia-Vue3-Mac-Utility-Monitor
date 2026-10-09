import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from sqlite3 import ProgrammingError
from types import SimpleNamespace
from unittest.mock import Mock, patch

import energy
from energy_clock import EnergyClock
from utc_migration import _convert


class FrozenDatetime(datetime):
    moment = datetime(2026, 11, 1, 6, 30, 0, 123456, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        local = cls.moment.astimezone(tz) if tz else cls.moment.astimezone().replace(tzinfo=None)
        return cls.fromisoformat(local.isoformat())


class UtcWriterTests(unittest.TestCase):
    """Private injected-clock writes; the ordinary UTC connection guard stays intact."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        database = patch.object(energy, 'DB_PATH', str(self.root/'fixture.db'))
        database.start()
        self.addCleanup(database.stop)
        energy.ensure_table()
        self.real_clock = energy._utc_clock
        rate = patch.object(energy, 'RATE_CENTS', energy.RATE_CENTS)
        rate.start()
        self.addCleanup(rate.stop)
        self.clock = EnergyClock('America/New_York')
        clock = patch.object(energy, '_utc_clock', return_value=self.clock)
        clock.start()
        self.addCleanup(clock.stop)
        instant = patch.object(energy, 'datetime', FrozenDatetime)
        instant.start()
        self.addCleanup(instant.stop)

    def rows(self, table):
        conn = energy._connect()
        try:
            return [dict(row) for row in conn.execute(f'SELECT * FROM {table} ORDER BY 1')]
        finally:
            conn.close()

    def upload(self, *, zone='America/Chicago', interval='1H', rows=None, device_gid='A'):
        path = self.root/f'Panel-{interval}.csv'
        header = f'Time Bucket ({zone})' if zone else 'Time Bucket'
        rows = rows or [('10/08/2026 12:00:00', 3)]
        path.write_text(header+',Panel-Main (kWhs)\n'+
                        ''.join(f'{stamp},{value}\n' for stamp, value in rows))
        with patch.object(energy, 'RATE_CENTS', 22.58):
            return energy.import_emporia_csv(str(path), device_gid=device_gid)

    def poll(self, channels=None):
        vue = Mock()
        channels = channels or {1: SimpleNamespace(name='Main', usage=.05,
                         timestamp=FrozenDatetime.fromisoformat(
                             (FrozenDatetime.moment-timedelta(minutes=1)).isoformat()))}
        vue.get_device_list_usage.return_value = {'A': SimpleNamespace(channels=channels)}
        with patch.object(energy, '_last_compaction', float('inf')), \
                patch.object(energy, '_read_rate_cents', return_value=22.58):
            energy.poll_and_store(vue, ['A'])
        return vue

    def test_poll_receipt_snapshot_journal_and_capability_use_one_utc_instant(self):
        self.poll()
        expected = EnergyClock.stamp(FrozenDatetime.moment)
        for table in ('readings', 'latest_channel_snapshot', 'reading_changes'):
            row = self.rows(table)[0]
            self.assertEqual(row['timestamp'], expected, table)
            self.assertEqual(row['measurement_seconds'], 60)
            self.assertEqual(row['measurement_source'], 'emporia_minute')
            self.assertEqual(datetime.fromisoformat(row['provider_timestamp']),
                             FrozenDatetime.moment-timedelta(minutes=1))
            self.assertAlmostEqual(row['usage_kwh'], .05)
            self.assertAlmostEqual(row['cost_cents'], 1.129)
        self.assertEqual(self.rows('device_capabilities')[0]['updated_at'], expected)

    def test_poll_retention_uses_canonical_elapsed_microsecond_boundary(self):
        cutoff = FrozenDatetime.moment-timedelta(days=1)
        conn = energy._connect()
        for delta in (-1, 0, 1):
            conn.execute('INSERT INTO readings(timestamp,device_gid,channel_name,usage_kwh) VALUES(?,?,?,1)',
                         (EnergyClock.stamp(cutoff+timedelta(microseconds=delta)), 'A', str(delta)))
        conn.commit()
        conn.close()
        with patch.object(energy, 'DB_RETENTION_DAYS', 1):
            self.poll()
        self.assertEqual({row['channel_name'] for row in self.rows('readings')}, {'0', '1', 'Main'})

    def test_invalid_poll_does_not_publish_partial_rows_or_prune_history(self):
        self.upload()
        before = self.rows('readings')
        with self.assertRaises(ValueError):
            self.poll({1: SimpleNamespace(name='Main', usage=.05),
                       2: SimpleNamespace(name='Pump', usage=float('nan'))})
        self.assertEqual(self.rows('readings'), before)

    def test_poll_reads_policy_inside_the_publication_transaction(self):
        def clock(conn):
            self.assertTrue(conn.in_transaction, 'Policy must be stable through row publication')
            return self.clock
        with patch.object(energy, '_utc_clock', side_effect=clock):
            self.poll()

    def test_fold_polls_keep_both_instants_and_evidenced_live_power(self):
        for hour in (5, 6):
            with patch.object(FrozenDatetime, 'moment', datetime(2026, 11, 1, hour, 30, tzinfo=timezone.utc)):
                self.poll()
        rows = self.rows('readings')
        self.assertEqual([row['timestamp'] for row in rows],
                         ['2026-11-01T05:30:00.000000+00:00', '2026-11-01T06:30:00.000000+00:00'])
        self.assertEqual(energy.reading_live_watts(rows[1], now=datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc)),
                         3000)

    def test_utc_storage_helper_refuses_unknown_naive_instant(self):
        conn = energy._connect()
        try:
            with self.assertRaises(ValueError):
                energy._storage_timestamp(conn, datetime(2026, 11, 1, 1, 30))
            self.assertEqual(energy._storage_timestamp(conn, datetime.fromisoformat('2026-11-01T01:30:00.123456-05:00')),
                             '2026-11-01T06:30:00.123456+00:00')
        finally:
            conn.close()

    def test_capability_publication_failure_closes_and_rolls_back(self):
        conn = energy._connect()
        with patch.object(energy, '_connect', return_value=conn), \
                patch.object(energy, '_storage_timestamp', side_effect=ValueError('Invalid policy')):
            with self.assertRaises(ValueError):
                energy.save_device_capabilities(device_gid='A', service_mode='aggregate_only',
                    has_main=True, has_mains_a=False, has_mains_b=False, has_mains_c=False,
                    mains_c_no_ct=False, source='live_poll')
        with self.assertRaises(ProgrammingError):
            conn.execute('SELECT 1')
        self.assertEqual(self.rows('device_capabilities'), [])

    def test_legacy_heartbeat_keeps_local_db_rows_but_file_is_aware(self):
        path = self.root/'status.json'
        with patch.object(energy, '_utc_clock', return_value=None), \
                patch.object(energy, 'POLLER_STATUS_FILE', str(path)):
            energy.write_poller_status(True)
        row = self.rows('poller_health_events')[0]
        self.assertEqual(datetime.fromisoformat(row['timestamp']).tzinfo, None)
        self.assertEqual(datetime.fromisoformat(json.loads(path.read_text())['timestamp']), FrozenDatetime.moment)

    def test_converted_private_copy_writers_use_real_persisted_policy(self):
        conn = energy._connect()
        conn.execute("INSERT INTO readings(timestamp,device_gid,channel_name,usage_kwh,cost_cents) VALUES('2026-10-08T12:00:00','A','Main',1,22.58)")
        conn.commit()
        conn.close()
        archive = self.root/'archive.db'
        energy.backup_database(archive)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        # All handles are opened before conversion in this disposable fixture only.
        # No new writable UTC connection, activation flag, or production path is introduced.
        handles = [energy._connect() for _ in range(4)]
        for handle in handles:
            self.addCleanup(handle.close)
        conversion, poll, csv, heartbeat = handles
        with conversion:
            conversion.execute('BEGIN IMMEDIATE')
            report = _convert(conversion, 'America/New_York', 'America/New_York', digest)
        conversion.close()
        with patch.object(energy, '_utc_clock', wraps=self.real_clock):
            with patch.object(energy, '_connect', return_value=poll):
                self.poll()
            with patch.object(energy, '_connect', return_value=csv):
                # Clock acceptance is independent of unresolved pre-ledger/live bounds.
                # The projection tests separately require quarantine for that history.
                self.assertEqual(self.upload(device_gid='CSV')['imported'], 1)
            with patch.object(energy, '_connect', return_value=heartbeat), \
                    patch.object(energy, 'POLLER_STATUS_FILE', str(self.root/'status.json')):
                energy.write_poller_status(True)
            original_connect = energy._connect
            with patch.object(energy, '_connect', side_effect=lambda: original_connect(
                    allow_utc_rehearsal=True, read_only=True)):
                page = energy.get_reading_changes(protocol_version=3)
                self.assertEqual(page['time_policy']['reporting_timezone'], 'America/New_York')
                self.assertEqual(page['generation_id'], report['stream_transition']['new_generation_id'])
                self.assertTrue(all(EnergyClock.parse(row['timestamp']) for row in page['changes']
                                    if row['operation'] == 'upsert'))
                monthly = energy.get_monthly_costs(1, 'A', now=FrozenDatetime.moment)
                self.assertAlmostEqual(monthly[0]['total_kwh'], .05)
                with self.assertRaises(energy.SyncUpgradeRequired):
                    energy.get_reading_changes()
                inspection = energy._connect()
                try:
                    rows = [dict(row) for row in inspection.execute('SELECT * FROM readings ORDER BY id')]
                    self.assertEqual(rows[0]['id'], 1)
                    self.assertEqual(rows[0]['usage_kwh'], 1)
                    self.assertEqual(rows[0]['timestamp'], '2026-10-08T16:00:00.000000+00:00')
                    self.assertEqual(rows[2]['timestamp'], '2026-10-08T17:00:00.000000+00:00')
                    self.assertEqual(inspection.execute('SELECT timestamp FROM latest_channel_snapshot').fetchone()[0],
                                     EnergyClock.stamp(FrozenDatetime.moment))
                    self.assertEqual(inspection.execute('SELECT timestamp FROM poller_health_events').fetchone()[0],
                                     EnergyClock.stamp(FrozenDatetime.moment))
                finally:
                    inspection.close()
        self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), digest)
        with self.assertRaises(RuntimeError):
            energy._connect()

    def test_heartbeat_file_and_utc_health_rows_share_aware_instant(self):
        path = self.root/'status.json'
        cutoff = FrozenDatetime.moment-timedelta(days=1)
        conn = energy._connect()
        for delta in (-1, 0, 1):
            conn.execute('INSERT INTO poller_health_events(timestamp,ok) VALUES(?,0)',
                         (EnergyClock.stamp(cutoff+timedelta(microseconds=delta)),))
        conn.commit()
        conn.close()
        with patch.object(energy, 'POLLER_STATUS_FILE', str(path)), \
                patch.object(energy, 'DB_RETENTION_DAYS', 1):
            energy.write_poller_status(True)
        payload = json.loads(path.read_text())
        self.assertEqual(payload['timestamp'], EnergyClock.stamp(FrozenDatetime.moment))
        rows = self.rows('poller_health_events')
        self.assertEqual([row['timestamp'] for row in rows],
                         [EnergyClock.stamp(cutoff), EnergyClock.stamp(cutoff+timedelta(microseconds=1)),
                          payload['timestamp']])

    def test_utc_csv_uses_declared_source_zone_in_all_timestamp_replicas(self):
        result = self.upload()
        for table in ('readings', 'latest_channel_snapshot', 'reading_changes'):
            row = self.rows(table)[0]
            self.assertEqual(row['timestamp'], '2026-10-08T17:00:00.000000+00:00', table)
            self.assertEqual(row['source_timezone'], 'America/Chicago')
            self.assertEqual(row['usage_kwh'], 3)
            self.assertAlmostEqual(row['cost_cents'], 67.74)
        self.assertEqual(result['imported'], 1)
        self.assertEqual(result['timestamp_format'], 'utc_v1')
        self.assertEqual(result['reporting_timezone'], 'America/New_York')

    def test_utc_csv_missing_zone_rejects_without_any_publication(self):
        before = {table: self.rows(table) for table in
                  ('readings', 'latest_channel_snapshot', 'reading_changes', 'device_capabilities')}
        with self.assertRaisesRegex(ValueError, 'source timezone'):
            self.upload(zone=None)
        self.assertEqual({table: self.rows(table) for table in before}, before)

    def test_utc_daily_exports_preserve_actual_duration_and_reporting_day(self):
        for stamp, utc, seconds in [('03/08/2026 00:00:00', '2026-03-08T05:00:00.000000+00:00', 23*3600),
                                    ('11/01/2026 00:00:00', '2026-11-01T04:00:00.000000+00:00', 25*3600)]:
            result = self.upload(zone='America/New_York', interval='1DAY', rows=[(stamp, 3)])
            row = next(row for row in self.rows('readings') if row['timestamp'] == utc)
            self.assertEqual(row['measurement_seconds'], seconds)
            self.assertEqual(result['interval_seconds'], seconds)
            self.assertEqual(row['usage_kwh'], 3)

    def test_utc_csv_gaps_and_folds_are_reported_not_guessed(self):
        result = self.upload(zone='America/New_York', rows=[('03/08/2026 02:30:00', 99),
                             ('11/01/2026 01:30:00', 99), ('11/01/2026 02:30:00', 3)])
        self.assertEqual((result['errors'], result['ambiguous_timestamps'], result['nonexistent_timestamps']),
                         (2, 1, 1))
        self.assertEqual(self.rows('readings')[0]['timestamp'], '2026-11-01T07:30:00.000000+00:00')
        self.assertEqual(len(self.rows('readings')), 1)

    def test_utc_duplicate_import_keeps_canonical_id_values_and_journal(self):
        self.upload()
        before = {table: self.rows(table) for table in ('readings', 'latest_channel_snapshot', 'reading_changes')}
        result = self.upload(rows=[('10/08/2026 12:00:00', 999)])
        self.assertEqual(result['imported'], 0)
        self.assertEqual({table: self.rows(table) for table in before}, before)

    def test_utc_snapshot_rejects_noncanonical_timestamp_before_write(self):
        conn = energy._connect()
        try:
            with self.assertRaises(ValueError):
                energy._upsert_latest_snapshot_with_conn(conn, device_gid='A', channel_name='Main',
                    channel_num=1, usage_kwh=.05, cost_cents=1.129, timestamp='2026-11-01T06:30:00')
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM latest_channel_snapshot').fetchone()[0], 0)
        finally:
            conn.close()

    def test_legacy_poll_and_csv_keep_naive_local_storage(self):
        with patch.object(energy, '_utc_clock', return_value=None):
            self.poll()
            self.assertEqual(self.upload(device_gid='CSV')['imported'], 1)
        self.assertTrue(all(datetime.fromisoformat(row['timestamp']).tzinfo is None for row in self.rows('readings')))
        self.assertIn('2026-10-08T12:00:00', {row['timestamp'] for row in self.rows('readings')})

    def test_writer_results_do_not_depend_on_host_timezone(self):
        root = Path(__file__).resolve().parents[1]
        code = '''
import json
from unittest.mock import patch
from test_utc_writers import UtcWriterTests
test = UtcWriterTests()
test.setUp()
try:
    test.poll()
    test.upload()
    print(json.dumps([{key: row[key] for key in ('timestamp','usage_kwh','cost_cents','measurement_seconds','measurement_source','source_timezone')}
                     for row in test.rows('readings')], sort_keys=True))
finally:
    test.doCleanups()
'''
        results = []
        for index, zone in enumerate(('UTC', 'Asia/Tokyo', 'America/Los_Angeles')):
            env = dict(os.environ, TZ=zone, DB_PATH=str(self.root/f'boot-{index}.db'),
                       PYTHONPATH=os.pathsep.join((str(root/'tests'), str(root))))
            result = subprocess.run([sys.executable, '-c', code], cwd=root, env=env,
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
            results.append(json.loads(result.stdout))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0], results[2])
