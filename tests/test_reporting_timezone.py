import contextlib
import os
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import device_identity
import energy

# 2026-10-21 16:00 UTC is 12:00 in New York (EDT) and 01:00 on Oct 22 in Tokyo.
INSTANT = datetime(2026, 10, 21, 16, 0, tzinfo=timezone.utc)
NEW_YORK_WALL_CLOCK = '2026-10-21T12:00:00'


class FixedInstant(datetime):
    @classmethod
    def now(cls, tz=None):
        return INSTANT.astimezone(tz) if tz else INSTANT.astimezone().replace(tzinfo=None)


@contextlib.contextmanager
def host_timezone(name):
    """Run as if the machine were in ``name`` (the relocation scenario)."""
    previous = os.environ.get('TZ')
    os.environ['TZ'] = name
    time.tzset()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop('TZ', None)
        else:
            os.environ['TZ'] = previous
        time.tzset()


class ReportingTimezoneBase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for target, value in [('DB_PATH', str(self.root / 'energy.db')), ('datetime', FixedInstant)]:
            patcher = patch.object(energy, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        energy._REPORTING_ZONE_CACHE.clear()
        self.addCleanup(energy._REPORTING_ZONE_CACHE.clear)
        settings = patch.object(energy, '_load_settings', return_value={})
        settings.start()
        self.addCleanup(settings.stop)
        energy.ensure_table()

    def poll(self, gid=551741, usage=0.1):
        usage_by_gid = {gid: SimpleNamespace(channels={1: SimpleNamespace(name='Main', usage=usage)})}
        energy.poll_and_store(SimpleNamespace(get_device_list_usage=lambda **kw: usage_by_gid), [gid])

    def stamps(self):
        conn = energy._connect()
        try:
            return [r[0] for r in conn.execute('SELECT timestamp FROM readings ORDER BY id')]
        finally:
            conn.close()


class RecordingTests(ReportingTimezoneBase):
    def test_unset_zone_keeps_the_legacy_host_clock(self):
        self.assertIsNone(energy.get_reporting_timezone())
        with host_timezone('Asia/Tokyo'):
            self.poll()
        self.assertEqual(self.stamps(), ['2026-10-22T01:00:00'])

    def test_zone_is_validated_recorded_once_and_immutable(self):
        for bad in ('', 'Not/AZone', '../etc/passwd'):
            with self.assertRaises(ValueError):
                energy.set_reporting_timezone(bad)
        self.assertEqual(energy.set_reporting_timezone('America/New_York')['status'], 'recorded')
        self.assertEqual(energy.get_reporting_timezone(), 'America/New_York')
        self.assertEqual(energy.set_reporting_timezone('America/New_York')['status'], 'unchanged')
        with self.assertRaises(ValueError):
            energy.set_reporting_timezone('UTC')
        conn = energy._connect()
        try:
            for sql in ("UPDATE collector_timezone SET zone='UTC'", "DELETE FROM collector_timezone"):
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(sql)
        finally:
            conn.close()
        self.assertEqual(energy.get_reporting_timezone(), 'America/New_York')

    def test_recording_a_zone_over_existing_history_needs_an_explicit_assertion(self):
        self.poll()
        before = self.stamps()
        with self.assertRaises(ValueError):
            energy.set_reporting_timezone('America/New_York')
        self.assertIsNone(energy.get_reporting_timezone())
        result = energy.set_reporting_timezone('America/New_York', existing_history_in_zone=True)
        self.assertEqual(result['existing_readings'], 1)
        # Existing stored values are never reinterpreted or rewritten.
        self.assertEqual(self.stamps(), before)

    def test_a_zone_recorded_by_another_process_is_noticed(self):
        self.assertIsNone(energy.get_reporting_timezone())
        other = sqlite3.connect(energy.DB_PATH)
        with other:
            other.execute("INSERT INTO collector_timezone VALUES (1,'America/New_York','x',0,NULL)")
        other.close()
        with patch.object(energy.time, 'monotonic', return_value=time.monotonic() + 60):
            self.assertEqual(energy.get_reporting_timezone(), 'America/New_York')

    def test_host_zone_detection_and_mismatch_warning(self):
        with patch.dict(os.environ, {'TZ': 'Asia/Tokyo'}):
            self.assertEqual(energy.detect_host_timezone(), 'Asia/Tokyo')
        with patch.dict(os.environ, {'TZ': 'Bogus/Zone'}), \
                patch('energy.Path.read_text', side_effect=OSError), \
                patch('energy.os.path.realpath', return_value='/etc/localtime'):
            self.assertIsNone(energy.detect_host_timezone())
        energy.set_reporting_timezone('America/New_York')
        energy._host_zone_warned = False
        self.addCleanup(setattr, energy, '_host_zone_warned', False)
        with patch.object(energy, 'detect_host_timezone', return_value='Asia/Tokyo'):
            self.assertIs(energy.reporting_timezone_status()['host_agrees'], False)
            with self.assertLogs(energy.logger, 'WARNING') as logs:
                self.poll()
                self.poll(gid=552, usage=0.2)
        mismatches = [m for m in logs.output if 'differs from the recorded reporting zone' in m]
        self.assertEqual(len(mismatches), 1)  # once per process, not once per poll


class RelocationTests(ReportingTimezoneBase):
    """Moving the database between hosts in different zones must not mix clocks."""

    def setUp(self):
        super().setUp()
        energy.set_reporting_timezone('America/New_York')

    def test_polls_use_the_reporting_clock_on_any_host(self):
        for gid, host in enumerate(('America/New_York', 'UTC', 'Asia/Tokyo', 'Pacific/Auckland'), start=101):
            with host_timezone(host):
                self.poll(gid=gid)
        self.assertEqual(self.stamps(), [NEW_YORK_WALL_CLOCK] * 4)

    def test_live_provenance_records_the_verified_zone(self):
        self.poll()
        conn = energy._connect()
        try:
            row = conn.execute("SELECT source, source_timezone FROM reading_provenance").fetchone()
        finally:
            conn.close()
        self.assertEqual(tuple(row), ('live_poll', 'America/New_York'))

    def test_without_a_recorded_zone_provenance_stays_unknown(self):
        other = Path(self.root / 'other.db')
        with patch.object(energy, 'DB_PATH', str(other)):
            energy.ensure_table()
            self.poll()
            conn = energy._connect()
            try:
                self.assertIsNone(conn.execute("SELECT source_timezone FROM reading_provenance").fetchone()[0])
            finally:
                conn.close()

    def test_daily_totals_follow_the_reporting_calendar_day(self):
        conn = energy._connect()
        with conn:
            conn.execute("INSERT INTO readings(timestamp,device_gid,channel_name,usage_kwh,cost_cents) "
                         "VALUES ('2026-10-21T01:00:00','A','Pump',2,20)")
            conn.execute("INSERT INTO readings(timestamp,device_gid,channel_name,usage_kwh,cost_cents) "
                         "VALUES ('2026-10-20T23:00:00','A','Pump',50,500)")
        conn.close()
        results = []
        for host in ('America/New_York', 'Asia/Tokyo', 'Pacific/Honolulu'):
            with host_timezone(host):
                results.append(energy.get_today_circuit_totals('A', 'day'))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0], results[2])
        self.assertEqual([r['total_kwh'] for r in results[0]], [2])

    def test_same_export_is_a_duplicate_when_reimported_from_another_host(self):
        device_identity_conn = energy._connect()
        with device_identity_conn:
            device_identity.register_cloud_gid(device_identity_conn, 'QA')
        device_identity_conn.close()
        path = self.root / 'QA-Barn-1H.csv'
        path.write_text('Time Bucket (UTC),Pump (kWhs)\n10/18/2026 17:00:00,3\n')
        results = []
        for host in ('UTC', 'Asia/Tokyo', 'America/New_York'):
            with host_timezone(host):
                results.append(energy.import_emporia_csv(str(path), device_gid='QA'))
        self.assertEqual([r['imported'] for r in results], [1, 0, 0])
        self.assertEqual(self.stamps(), ['2026-10-18T13:00:00'])  # 17:00 UTC is 13:00 EDT

    def test_import_fold_rejection_uses_the_reporting_zone(self):
        conn = energy._connect()
        with conn:
            device_identity.register_cloud_gid(conn, 'QA')
        conn.close()
        path = self.root / 'QA-Barn-1H.csv'
        # 05:30 UTC and 06:30 UTC on Nov 1 are both 01:30 New York wall clock (the repeated hour).
        path.write_text('Time Bucket (UTC),Pump (kWhs)\n11/01/2026 05:30:00,1\n11/01/2026 06:30:00,1\n')
        for host in ('UTC', 'Asia/Tokyo'):
            with host_timezone(host):
                result = energy.import_emporia_csv(str(path), device_gid='QA')
            self.assertEqual((result['imported'], result['ambiguous_timestamps']), (0, 2))
        self.assertEqual(self.stamps(), [])

    def test_interval_overlap_detection_is_host_independent(self):
        conn = energy._connect()
        with conn:
            device_identity.register_cloud_gid(conn, 'QA')
        conn.close()
        hourly = self.root / 'QA-Barn-1H.csv'
        hourly.write_text('Time Bucket (America/New_York),Pump (kWhs)\n10/18/2026 13:00:00,3\n')
        fine = self.root / 'QA-Barn-15MIN.csv'
        fine.write_text('Time Bucket (America/New_York),Pump (kWhs)\n10/18/2026 13:15:00,1\n')
        for host in ('Asia/Tokyo', 'UTC'):
            with host_timezone(host):
                energy.import_emporia_csv(str(hourly), device_gid='QA')
                overlap = energy.import_emporia_csv(str(fine), device_gid='QA')
            self.assertEqual((overlap['imported'], overlap['interval_conflicts']), (0, 1))


if __name__ == '__main__':
    unittest.main()
