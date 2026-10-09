import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from sqlite3 import SQLITE_DENY, SQLITE_INSERT, SQLITE_OK, DatabaseError
from unittest.mock import patch
from zoneinfo import ZoneInfo

import energy
from energy_clock import EnergyClock


class CompactionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        patcher = patch.object(energy, 'DB_PATH', str(Path(self.directory.name) / 'h.db'))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.directory.cleanup)
        energy.ensure_table()

    def connect(self):
        conn = energy._connect()
        self.addCleanup(conn.close)
        return conn

    def seed(self, conn, stamp, *, seconds=60, source='emporia_minute', provider=None,
             name='Main', kwh=.01, cents=1):
        conn.execute('''INSERT INTO readings(timestamp,device_gid,channel_name,usage_kwh,cost_cents,
                    measurement_seconds,measurement_source,provider_timestamp) VALUES (?,?,?,?,?,?,?,?)''',
                     (stamp, 'A', name, kwh, cents, seconds, source, provider))

    def snapshot(self, conn):
        return {table: [tuple(row) for row in conn.execute(f'SELECT * FROM {table} ORDER BY 1')]
                for table in ('readings', 'reading_changes')}

    def old(self):
        return (datetime.now() - timedelta(days=40)).replace(minute=0, second=0, microsecond=0)

    def test_unknown_and_imported_intervals_are_preserved_exactly(self):
        conn = self.connect()
        old = self.old()
        for source, seconds, name in ((None, None, 'Unknown'), ('csv_energy', 3600, 'Hourly'),
                                      ('csv_power', 60, 'CSV minute')):
            for minute in (1, 15):
                self.seed(conn, (old+timedelta(minutes=minute)).isoformat(),
                          source=source, seconds=seconds, name=name)
        before = self.snapshot(conn)
        self.assertEqual(energy.compact_minute_readings(conn, 30), 0)
        self.assertEqual(self.snapshot(conn), before)

    def test_hourly_import_collision_keeps_entire_group_and_journal(self):
        conn = self.connect()
        old = self.old()
        self.seed(conn, old.isoformat(), seconds=3600, source='csv_energy', kwh=3, cents=30)
        for minute in (1, 2):
            self.seed(conn, (old+timedelta(minutes=minute)).isoformat())
        before = self.snapshot(conn)
        self.assertEqual(energy.compact_minute_readings(conn, 30), 0)
        self.assertEqual(self.snapshot(conn), before)

    def test_single_minute_keeps_its_duration_and_provider_evidence(self):
        conn = self.connect()
        self.seed(conn, (self.old()+timedelta(minutes=15)).isoformat())
        before = self.snapshot(conn)
        self.assertEqual(energy.compact_minute_readings(conn, 30), 0)
        self.assertEqual(self.snapshot(conn), before)

    def test_repeated_provider_instants_are_not_collapsed_and_erased(self):
        conn = self.connect()
        old = self.old()
        provider = old.astimezone(timezone.utc).isoformat()
        for minute in (1, 2):
            self.seed(conn, (old+timedelta(minutes=minute)).isoformat(), provider=provider)
        before = self.snapshot(conn)
        self.assertEqual(energy.compact_minute_readings(conn, 30), 0)
        self.assertEqual(self.snapshot(conn), before)

    def test_equivalent_provider_offsets_are_the_same_observation(self):
        conn = self.connect()
        old = self.old()
        provider = old.astimezone(timezone.utc).isoformat()
        self.seed(conn, (old+timedelta(minutes=1)).isoformat(), provider=provider)
        self.seed(conn, (old+timedelta(minutes=2)).isoformat(), provider=provider.replace('+00:00', 'Z'))
        before = self.snapshot(conn)
        self.assertEqual(energy.compact_minute_readings(conn, 30), 0)
        self.assertEqual(self.snapshot(conn), before)

    def test_success_does_not_commit_the_callers_transaction(self):
        conn = self.connect()
        old = self.old()
        for minute in (1, 2):
            self.seed(conn, (old+timedelta(minutes=minute)).isoformat())
        conn.commit()
        before = self.snapshot(conn)
        self.assertEqual(energy.compact_minute_readings(conn, 30), 2)
        conn.rollback()
        self.assertEqual(self.snapshot(conn), before)

    def test_offset_timestamps_are_not_rewritten_as_legacy_local(self):
        conn = self.connect()
        old = self.old().replace(tzinfo=timezone.utc)
        for minute in (1, 2):
            self.seed(conn, (old+timedelta(minutes=minute)).isoformat())
        before = self.snapshot(conn)
        self.assertEqual(energy.compact_minute_readings(conn, 30), 0)
        self.assertEqual(self.snapshot(conn), before)

    def test_different_channel_identity_or_source_zone_protects_group(self):
        conn = self.connect()
        old = self.old()
        for minute in (1, 2):
            self.seed(conn, (old+timedelta(minutes=minute)).isoformat(), name='Identity')
            self.seed(conn, (old+timedelta(minutes=minute)).isoformat(), name='Zone')
        conn.execute("UPDATE readings SET channel_num=CASE WHEN substr(timestamp,15,2)='01' THEN 1 ELSE 2 END WHERE channel_name='Identity'")
        conn.execute("UPDATE readings SET source_timezone=CASE WHEN substr(timestamp,15,2)='01' THEN 'America/New_York' ELSE 'America/Chicago' END WHERE channel_name='Zone'")
        before = self.snapshot(conn)
        self.assertEqual(energy.compact_minute_readings(conn, 30), 0)
        self.assertEqual(self.snapshot(conn), before)

    def test_signed_energy_known_zone_and_journal_survive_valid_compaction(self):
        conn = self.connect()
        old = self.old()
        for minute in (1, 2):
            self.seed(conn, (old+timedelta(minutes=minute)).isoformat(), kwh=-.02, cents=-.4516)
        conn.execute("UPDATE readings SET source_timezone='America/New_York'")
        self.assertEqual(energy.compact_minute_readings(conn, 30), 2)
        row = dict(conn.execute('SELECT * FROM readings').fetchone())
        self.assertAlmostEqual(row['usage_kwh'], -.04)
        self.assertAlmostEqual(row['cost_cents'], -.9032)
        self.assertEqual(row['source_timezone'], 'America/New_York')
        self.assertEqual(row['measurement_source'], 'compacted')
        self.assertIsNone(row['measurement_seconds'])
        self.assertIsNone(row['provider_timestamp'])
        self.assertIsNone(energy.reading_average_watts(row))
        changes = [dict(row) for row in conn.execute('SELECT * FROM reading_changes ORDER BY sequence')]
        self.assertEqual([change['operation'] for change in changes[-3:]], ['delete', 'delete', 'upsert'])
        for field in ('timestamp', 'usage_kwh', 'cost_cents', *energy.MEASUREMENT_FIELDS):
            self.assertEqual(changes[-1][field], row[field])

    def test_overflow_or_invalid_evidence_does_not_delete_originals(self):
        conn = self.connect()
        old = self.old()
        for minute in (1, 2):
            self.seed(conn, (old+timedelta(minutes=minute)).isoformat(), name='Overflow', kwh=1e308, cents=1e308)
            self.seed(conn, (old+timedelta(minutes=minute)).isoformat(), name='Invalid', seconds=3600)
        before = self.snapshot(conn)
        self.assertEqual(energy.compact_minute_readings(conn, 30), 0)
        self.assertEqual(self.snapshot(conn), before)

    def test_boundary_hour_is_retained_and_complete_older_hour_compacts(self):
        conn = self.connect()
        now = datetime(2026, 10, 9, 12, 30)
        boundary = datetime(2026, 9, 9, 12)
        for base in (boundary-timedelta(hours=1), boundary):
            for minute in (1, 2):
                self.seed(conn, (base+timedelta(minutes=minute)).isoformat())
        self.assertEqual(energy.compact_minute_readings(conn, 30, now=now), 2)
        rows = conn.execute('SELECT timestamp FROM readings ORDER BY timestamp').fetchall()
        self.assertEqual([row[0] for row in rows], ['2026-09-09T11:00:00',
                                                '2026-09-09T12:01:00', '2026-09-09T12:02:00'])

    def test_real_journal_cache_round_trip_preserves_compacted_totals(self):
        conn = self.connect()
        old = self.old()
        for minute in (1, 2):
            self.seed(conn, (old+timedelta(minutes=minute)).isoformat())
        conn.commit()
        initial = energy.get_reading_changes()
        cache_path = str(Path(self.directory.name) / 'cache.db')
        with patch.object(energy, 'DB_PATH', cache_path):
            energy.ensure_table()
            state = energy.apply_reading_changes(initial)
        self.assertEqual(energy.compact_minute_readings(conn, 30), 2)
        conn.commit()
        page = energy.get_reading_changes(after=state['cursor'])
        self.assertEqual(page['source_id'], initial['source_id'])
        self.assertEqual(page['generation_id'], initial['generation_id'])
        with patch.object(energy, 'DB_PATH', cache_path):
            final = energy.apply_reading_changes(page)
            cache = self.connect()
            rows = [dict(row) for row in cache.execute('SELECT * FROM sync_cached_readings')]
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]['usage_kwh'], .02)
        self.assertAlmostEqual(rows[0]['cost_cents'], 2)
        self.assertEqual(rows[0]['measurement_source'], 'compacted')
        self.assertIsNone(rows[0]['measurement_seconds'])
        self.assertEqual(final['cursor'], page['next_cursor'])

    def test_insert_failure_rolls_back_only_compaction_including_journal(self):
        conn = self.connect()
        old = self.old()
        for minute in (1, 2):
            self.seed(conn, (old+timedelta(minutes=minute)).isoformat())
        conn.commit()
        before = self.snapshot(conn)
        conn.execute("INSERT INTO circuit_labels(slot,label) VALUES(1,'Unrelated pending edit')")
        conn.set_authorizer(lambda action, table, *_: SQLITE_DENY
                            if action == SQLITE_INSERT and table == 'readings' else SQLITE_OK)
        with self.assertRaises(DatabaseError):
            energy.compact_minute_readings(conn, 30)
        conn.set_authorizer(None)
        conn.commit()
        self.assertEqual(self.snapshot(conn), before)
        self.assertEqual(conn.execute('SELECT label FROM circuit_labels').fetchone()[0],
                         'Unrelated pending edit')

    def test_compaction_never_drops_a_permanent_same_named_table(self):
        conn = self.connect()
        conn.execute('CREATE TABLE _compact(value TEXT)')
        conn.execute("INSERT INTO _compact VALUES('preserve')")
        energy.compact_minute_readings(conn, 30)
        self.assertEqual(conn.execute('SELECT value FROM main._compact').fetchone()[0], 'preserve')

    def test_utc_clock_compaction_keeps_fall_folds_and_canonical_keys(self):
        conn = self.connect()
        clock = EnergyClock('America/New_York')
        for hour in (5, 6):
            for minute in (1, 2):
                self.seed(conn, clock.stamp(datetime(2026, 11, 1, hour, minute, tzinfo=timezone.utc)))
        with patch.object(energy, '_utc_clock', return_value=clock):
            self.assertEqual(energy.compact_minute_readings(
                conn, 30, now=datetime(2026, 12, 15, tzinfo=timezone.utc)), 4)
        rows = conn.execute('SELECT timestamp,usage_kwh,measurement_seconds FROM readings ORDER BY timestamp').fetchall()
        self.assertEqual([row['timestamp'] for row in rows],
                         ['2026-11-01T05:00:00.000000+00:00', '2026-11-01T06:00:00.000000+00:00'])
        self.assertTrue(all(row['measurement_seconds'] is None for row in rows))
        self.assertAlmostEqual(sum(row['usage_kwh'] for row in rows), .04)

    def test_retention_cutoff_uses_elapsed_days_for_equivalent_offset_clocks(self):
        conn = self.connect()
        clock = EnergyClock('America/New_York')
        now = datetime(2026, 11, 15, 17, tzinfo=timezone.utc)
        for minute in (10, 20):
            self.seed(conn, clock.stamp(datetime(2026, 10, 16, 16, minute, tzinfo=timezone.utc)))
        conn.commit()
        before = self.snapshot(conn)
        for moment in (now.astimezone(ZoneInfo('America/New_York')), now):
            with patch.object(energy, '_utc_clock', return_value=clock):
                self.assertEqual(energy.compact_minute_readings(conn, 30, now=moment), 2)
            conn.rollback()
            self.assertEqual(self.snapshot(conn), before)

    def test_old_minutes_fold_into_hours_and_recent_rows_stay(self):
        old = (datetime.now() - timedelta(days=40)).replace(minute=0, second=0, microsecond=0)
        recent = datetime.now() - timedelta(minutes=5)
        conn = energy._connect()
        for i in range(3):
            conn.execute("INSERT INTO readings (timestamp,device_gid,channel_num,channel_name,usage_kwh,cost_cents,measurement_seconds,measurement_source)"
                         " VALUES (?,?,?,?,?,?,60,'emporia_minute')", ((old + timedelta(minutes=i)).isoformat(), 'A', 1, 'Main', .01, 1))
        conn.execute("INSERT INTO readings (timestamp,device_gid,channel_num,channel_name,usage_kwh,cost_cents)"
                     " VALUES (?,?,?,?,?,?)", (recent.isoformat(), 'A', 1, 'Main', .02, 2))
        self.assertEqual(energy.compact_minute_readings(conn, 30), 3)
        conn.commit()
        rows = conn.execute("SELECT timestamp, usage_kwh FROM readings ORDER BY timestamp").fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][0], old.isoformat(timespec='seconds'))
        self.assertAlmostEqual(rows[0][1], 0.03)
        self.assertEqual(rows[1][0], recent.isoformat())
        # Idempotent: a second pass finds only one row per hour and changes nothing.
        energy.compact_minute_readings(conn, 30)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0], 2)
        self.assertEqual(energy.compact_minute_readings(conn, 0), 0)
        conn.close()


if __name__ == '__main__':
    unittest.main()


class MonthlyCostTests(unittest.TestCase):
    def test_monthly_costs_group_circuits_and_write_reports(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(energy, 'DB_PATH', str(Path(directory) / 'm.db')):
            energy.ensure_table()
            conn = energy._connect()
            for stamp, name, kwh in [('2026-08-03T10:00:00', 'Main', 10), ('2026-08-03T10:00:00', 'Heat Pump', 6),
                                     ('2026-08-04T10:00:00', 'Main', 5), ('2026-08-04T10:00:00', 'Balance', 1)]:
                conn.execute("INSERT INTO readings (timestamp,device_gid,channel_num,channel_name,usage_kwh,cost_cents)"
                             " VALUES (?,?,?,?,?,?)", (stamp, 'A', 1, name, kwh, kwh * 10))
            conn.commit()
            conn.close()
            months = energy.get_monthly_costs(24, 'A')
            aug = next(m for m in months if m['month'] == '2026-08')
            self.assertEqual((aug['total_kwh'], aug['total_cents'], aug['days_recorded']), (15, 150, 2))
            self.assertEqual([c['channel_name'] for c in aug['circuits']], ['Heat Pump'])
            with patch.object(energy, 'get_monthly_costs', return_value=[aug]):
                written = energy.write_monthly_reports(str(Path(directory) / 'reports'))
                self.assertEqual(len(written), 1)
                self.assertIn('Heat Pump', Path(written[0]).read_text())
                self.assertEqual(energy.write_monthly_reports(str(Path(directory) / 'reports')), [])
