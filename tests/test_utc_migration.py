import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import energy
import utc_migration
from utc_migration import rehearse_utc_copy


class UtcRehearsalTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.database = patch.object(energy, "DB_PATH", str(self.root / "live.db"))
        self.database.start()
        energy.ensure_table()
        connection = energy._connect()
        try:
            with connection:
                connection.executemany(
                    "INSERT INTO readings(timestamp,device_gid,channel_name,usage_kwh,cost_cents) "
                    "VALUES (?,?,?,?,?)",
                    [("2026-03-08T01:59:00.123456", "A", "Main", 1.5, 30.1),
                     ("2026-03-08T03:00:00", "A", "Main", 2.5, 60.2),
                     ("2026-11-01T01:30:00-04:00", "A", "Main", 3.5, 90.3),
                     ("2026-11-01T01:30:00-05:00", "A", "Main", 4.5, 100.4)],
                )
                connection.execute("INSERT INTO reading_changes(operation,reading_id) VALUES ('delete',999)")
                connection.execute("INSERT INTO poller_health_events(timestamp,ok) VALUES ('2026-10-08T20:00:00',1)")
                connection.execute("INSERT INTO latest_channel_snapshot(device_gid,channel_name,channel_num,usage_kwh,cost_cents,timestamp) VALUES ('A','Main','1',4.5,100.4,'2026-11-01T01:30:00-05:00')")
                connection.execute("INSERT INTO radon_readings VALUES ('manual','sensor','2026-10-09T00:00:00+00:00','Label',0.7,'pCi/L',25.9,'2026-10-09T00:01:00+00:00')")
                connection.execute("INSERT INTO sync_cache_state VALUES (1,'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',3,3,'2026-10-08T20:00:00')")
            self.identity = connection.execute("SELECT source_id FROM collector_identity").fetchone()[0]
            self.generation = connection.execute("SELECT generation_id FROM reading_stream_generation").fetchone()[0]
        finally:
            connection.close()
        self.snapshot = self.root / "archive.db"
        energy.backup_database(self.snapshot)
        self.hash = self.digest(self.snapshot)
        self.destination = self.root / "converted.utc-rehearsal.db"

    def tearDown(self):
        self.database.stop()
        self.directory.cleanup()

    def digest(self, path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_csv_source_zone_overrides_assumption_without_reinterpreting_poll_receipt(self):
        connection = energy._connect()
        try:
            connection.execute("INSERT INTO readings(timestamp,device_gid,channel_name,usage_kwh,cost_cents,measurement_seconds,measurement_source,source_timezone) VALUES ('2026-10-08T12:00:00','B','Main',3,67.74,3600,'csv_energy','America/Chicago')")
            connection.execute("INSERT INTO readings(timestamp,device_gid,channel_name,usage_kwh,cost_cents,measurement_seconds,measurement_source,provider_timestamp) VALUES ('2026-10-08T12:00:00','C','Main',.05,1.129,60,'emporia_minute','2026-10-08T16:00:00+00:00')")
            connection.commit()
        finally:
            connection.close()
        energy.rebuild_latest_channel_snapshot()
        snapshot = self.root / 'with-source-evidence.db'
        energy.backup_database(snapshot)
        digest = self.digest(snapshot)
        destination = self.root / 'with-source-evidence.utc-rehearsal.db'
        rehearse_utc_copy(snapshot, destination, expected_sha256=digest,
                          legacy_timezone='America/New_York', reporting_timezone='America/New_York')
        connection = energy._connect(destination, allow_utc_rehearsal=True, read_only=True)
        try:
            for table in ('readings', 'latest_channel_snapshot', 'reading_changes'):
                rows = {row['device_gid']: dict(row) for row in connection.execute(
                    f"SELECT * FROM {table} WHERE device_gid IN ('B','C') ORDER BY 1"
                )}
                self.assertEqual(rows['B']['timestamp'], '2026-10-08T17:00:00.000000+00:00')
                self.assertEqual(rows['C']['timestamp'], '2026-10-08T16:00:00.000000+00:00')
                self.assertEqual(rows['B']['source_timezone'], 'America/Chicago')
                self.assertEqual(rows['C']['provider_timestamp'], '2026-10-08T16:00:00+00:00')
                self.assertEqual(rows['B']['usage_kwh'], 3)
        finally:
            connection.close()
        self.assertEqual(self.digest(snapshot), digest)

    def convert(self, **changes):
        options = {"expected_sha256": self.hash, "legacy_timezone": "America/New_York",
                   "reporting_timezone": "America/New_York", **changes}
        return rehearse_utc_copy(self.snapshot, self.destination, **options)

    def refresh_snapshot(self):
        self.snapshot.unlink()
        energy.backup_database(self.snapshot)
        self.hash = self.digest(self.snapshot)

    def test_exact_conversion_preserves_ids_costs_nulls_other_streams_and_archive(self):
        report = self.convert()
        self.assertEqual(self.digest(self.snapshot), self.hash)
        self.assertEqual(self.digest(self.destination), report["artifact_sha256"])
        self.assertTrue(report["source_unchanged"])
        self.assertFalse(report["live_ready"])
        self.assertEqual(report["timestamp_format"], "utc_v1")
        self.assertEqual(report["canonical_upserts_appended"], 4)
        self.assertEqual(self.destination.stat().st_mode & 0o777, 0o600)
        self.assertFalse(Path(str(self.destination)+"-wal").exists())
        connection = energy._connect(self.destination, allow_utc_rehearsal=True, read_only=True)
        try:
            rows = [dict(row) for row in connection.execute("SELECT * FROM readings ORDER BY id")]
            self.assertEqual(dict(connection.execute("SELECT * FROM energy_time_policy").fetchone()), {
                "singleton": 1, "timestamp_format": "utc_v1",
                "reporting_timezone": "America/New_York", "legacy_timezone": "America/New_York",
            })
            self.assertEqual([row["id"] for row in rows], [1, 2, 3, 4])
            self.assertEqual([row["timestamp"] for row in rows], [
                "2026-03-08T06:59:00.123456+00:00", "2026-03-08T07:00:00.000000+00:00",
                "2026-11-01T05:30:00.000000+00:00", "2026-11-01T06:30:00.000000+00:00",
            ])
            self.assertEqual([row["usage_kwh"] for row in rows], [1.5, 2.5, 3.5, 4.5])
            self.assertEqual([row["cost_cents"] for row in rows], [30.1, 60.2, 90.3, 100.4])
            self.assertEqual(connection.execute("SELECT source_id FROM collector_identity").fetchone()[0], self.identity)
            new_generation = connection.execute("SELECT generation_id FROM reading_stream_generation").fetchone()[0]
            self.assertNotEqual(new_generation, self.generation)
            self.assertEqual(report['stream_transition'], {
                'reason': 'utc_timestamp_format', 'previous_generation_id': self.generation,
                'new_generation_id': new_generation,
            })
            self.assertNotIn('reading_stream_generation', report['preserved_non_timestamp_fingerprints'])
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM reading_changes").fetchone()[0], 9)
            self.assertIsNone(connection.execute("SELECT timestamp FROM reading_changes WHERE operation='delete'").fetchone()[0])
            self.assertEqual(connection.execute("SELECT original_timestamp FROM utc_timestamp_evidence WHERE table_name='readings' AND row_key='[1]'").fetchone()[0], "2026-03-08T01:59:00.123456")
            self.assertEqual(connection.execute("SELECT timestamp FROM radon_readings").fetchone()[0], "2026-10-09T00:00:00+00:00")
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        finally:
            connection.close()
        self.assertEqual(self.digest(self.destination), report["artifact_sha256"])

    def test_normal_app_and_writable_maintenance_connections_reject_artifact(self):
        report = self.convert()
        for options in ({}, {"read_only": True}, {"allow_utc_rehearsal": True}):
            with self.assertRaises(RuntimeError):
                energy._connect(self.destination, **options)
        with self.assertRaises(RuntimeError):
            energy.ensure_table(self.destination)
        self.assertEqual(self.digest(self.destination), report["artifact_sha256"])
        self.assertFalse(Path(str(self.destination)+"-wal").exists())

    def test_read_only_maintenance_cannot_write_and_does_not_create_missing_database(self):
        self.convert()
        connection = energy._connect(self.destination, allow_utc_rehearsal=True, read_only=True)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("DELETE FROM readings")
        finally:
            connection.close()
        missing = self.root / "missing.db"
        with self.assertRaises(sqlite3.OperationalError):
            energy._connect(missing, read_only=True)
        self.assertFalse(missing.exists())

    def test_read_only_uri_escapes_path_metacharacters(self):
        special = self.root / "archive ?#.db"
        special.write_bytes(self.snapshot.read_bytes())
        before = self.digest(special)
        connection = energy._connect(special, read_only=True)
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM readings").fetchone()[0], 4)
        finally:
            connection.close()
        self.assertEqual(self.digest(special), before)

    def test_wrong_hash_and_unknown_timezone_never_publish(self):
        for options in ({"expected_sha256": "0"*64}, {"legacy_timezone": "Invalid/Zone"},
                        {"reporting_timezone": "Invalid/Zone"}, {"expected_sha256": "secret"}):
            with self.assertRaises((ValueError, KeyError)):
                self.convert(**options)
            self.assertFalse(self.destination.exists())
            self.assertEqual(self.digest(self.snapshot), self.hash)

    def test_gap_in_nonreading_replica_blocks_whole_conversion(self):
        connection = energy._connect()
        with connection:
            connection.execute("UPDATE poller_health_events SET timestamp='2026-03-08T02:30:00'")
        connection.close()
        self.refresh_snapshot()
        with self.assertRaisesRegex(ValueError, "poller_health_events"):
            self.convert()
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.digest(self.snapshot), self.hash)

    def test_ambiguous_journal_timestamp_blocks_even_when_readings_are_unique(self):
        connection = energy._connect()
        with connection:
            connection.execute("UPDATE reading_changes SET timestamp='2026-11-01T01:30:00' WHERE sequence=1")
        connection.close()
        self.refresh_snapshot()
        with self.assertRaisesRegex(ValueError, "reading_changes"):
            self.convert()
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.digest(self.snapshot), self.hash)

    def test_projected_unique_key_collision_never_drops_a_row(self):
        connection = energy._connect()
        with connection:
            connection.execute("INSERT INTO readings(timestamp,device_gid,channel_name,usage_kwh) VALUES ('2026-03-08T07:00:00Z','A','Main',999)")
        connection.close()
        self.refresh_snapshot()
        with self.assertRaisesRegex(ValueError, "collisions"):
            self.convert()
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.digest(self.snapshot), self.hash)

    def test_reseeding_archived_history_is_not_silently_accepted(self):
        connection = energy._connect()
        with connection:
            connection.execute("DELETE FROM migrations WHERE name='reading_sync_seed_v1'")
        connection.close()
        self.refresh_snapshot()
        with self.assertRaisesRegex(ValueError, "initialization changed"):
            self.convert()
        self.assertFalse(self.destination.exists())

    def test_existing_target_symlinks_and_live_sidecars_are_rejected(self):
        self.destination.write_text("preserve-me")
        with self.assertRaises(ValueError):
            self.convert()
        self.assertEqual(self.destination.read_text(), "preserve-me")
        self.destination.unlink()
        self.destination.symlink_to(self.snapshot)
        with self.assertRaises(ValueError):
            self.convert()
        self.destination.unlink()
        sidecar = Path(str(self.snapshot)+"-wal")
        sidecar.write_bytes(b"")
        with self.assertRaises(ValueError):
            self.convert()
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.digest(self.snapshot), self.hash)

    def test_public_permissions_are_rejected(self):
        self.snapshot.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "private regular"):
            self.convert()
        self.snapshot.chmod(0o600)
        public = self.root / "public"
        public.mkdir(mode=0o755)
        public.chmod(0o755)
        with self.assertRaisesRegex(ValueError, "private"):
            rehearse_utc_copy(self.snapshot, public/"copy.db", expected_sha256=self.hash,
                              legacy_timezone="UTC", reporting_timezone="UTC")

    def test_failure_after_conversion_rolls_back_and_cleans_temporary_files(self):
        with patch("utc_migration.os.link", side_effect=OSError("injected disk failure")):
            with self.assertRaises(OSError):
                self.convert()
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.digest(self.snapshot), self.hash)
        self.assertFalse(list(self.root.glob(".utc-rehearsal-*")))

    def test_invalid_generation_cannot_publish_a_format_transition(self):
        connection = energy._connect()
        connection.execute("UPDATE reading_stream_generation SET generation_id='invalid'")
        connection.commit()
        connection.close()
        self.refresh_snapshot()
        with self.assertRaisesRegex(ValueError, 'generation'):
            self.convert()
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.digest(self.snapshot), self.hash)
        self.assertFalse(list(self.root.glob('.utc-rehearsal-*')))

    def test_generation_rotation_failure_rolls_back_timestamps_and_policy(self):
        connection = energy._connect()
        before = [tuple(row) for row in connection.execute('SELECT * FROM readings ORDER BY id')]
        def deny_generation_update(action, table, column, database, trigger):
            return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_UPDATE and table == 'reading_stream_generation' else sqlite3.SQLITE_OK
        connection.set_authorizer(deny_generation_update)
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                with connection:
                    connection.execute('BEGIN IMMEDIATE')
                    utc_migration._convert(connection, 'America/New_York', 'America/New_York', self.hash)
            connection.set_authorizer(None)
            self.assertEqual([tuple(row) for row in connection.execute('SELECT * FROM readings ORDER BY id')], before)
            self.assertEqual(connection.execute('SELECT generation_id FROM reading_stream_generation').fetchone()[0], self.generation)
            for table in ('utc_rehearsal', 'energy_time_policy', 'utc_timestamp_evidence'):
                self.assertEqual(connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0)
        finally:
            connection.close()

    def test_business_value_change_is_detected_before_publication(self):
        original = utc_migration._fingerprints

        def corrupt_and_check(connection, tables, watermark):
            if connection.execute("SELECT 1 FROM utc_timestamp_evidence LIMIT 1").fetchone():
                connection.execute("UPDATE readings SET cost_cents=999 WHERE id=1")
            return original(connection, tables, watermark)

        with patch("utc_migration._fingerprints", side_effect=corrupt_and_check):
            with self.assertRaisesRegex(RuntimeError, "non-timestamp"):
                self.convert()
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.digest(self.snapshot), self.hash)
        self.assertFalse(list(self.root.glob(".utc-rehearsal-*")))

    def test_null_channel_duplicates_keep_sqlite_unique_index_semantics(self):
        connection = energy._connect()
        with connection:
            connection.executemany(
                "INSERT INTO readings(timestamp,device_gid,channel_name,usage_kwh) "
                "VALUES ('2026-10-08T12:00:00','A',NULL,?)", [(1,), (2,)],
            )
        connection.close()
        self.refresh_snapshot()
        report = self.convert()
        self.assertEqual(report["canonical_upserts_appended"], 6)
        connection = energy._connect(self.destination, allow_utc_rehearsal=True, read_only=True)
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM readings WHERE channel_name IS NULL").fetchone()[0], 2)
        finally:
            connection.close()

    def test_cli_isolates_schema_bootstrap_and_preserves_environment_database(self):
        script = Path(__file__).resolve().parents[1]/"scripts/rehearse_utc_migration.py"
        unrelated = self.root/"must-not-exist.db"
        command = [sys.executable, str(script), "--snapshot", str(self.snapshot),
                   "--destination", str(self.destination), "--expected-sha256", self.hash,
                   "--legacy-timezone", "America/New_York", "--reporting-timezone", "America/New_York"]
        result = subprocess.run(command, cwd=self.root, capture_output=True, text=True,
                                env={**os.environ, "DB_PATH": str(unrelated)}, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)["live_ready"])
        self.assertFalse(unrelated.exists())
        self.assertEqual(self.digest(self.snapshot), self.hash)
        result = subprocess.run(command, cwd=self.root, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn(str(self.snapshot), result.stderr)

    def test_connection_flags_require_explicit_boolean_values(self):
        for options in ({"read_only": "true"}, {"allow_utc_rehearsal": 1}):
            with self.assertRaises(ValueError):
                energy._connect(self.snapshot, **options)

    def test_incomplete_source_failure_does_not_echo_private_import_error(self):
        partial = self.root / "partial-source"
        (partial / "scripts").mkdir(parents=True)
        source = Path(__file__).resolve().parents[1]/"scripts/rehearse_utc_migration.py"
        script = partial / "scripts/rehearse_utc_migration.py"
        shutil.copyfile(source, script)
        (partial / "utc_migration.py").write_text("raise ModuleNotFoundError('private-secret')\n")
        result = subprocess.run(
            [sys.executable, str(script), "--snapshot", str(self.snapshot),
             "--destination", str(self.destination), "--expected-sha256", self.hash,
             "--legacy-timezone", "UTC", "--reporting-timezone", "UTC"],
            capture_output=True, text=True, cwd=partial, timeout=10,
        )
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("private-secret", result.stdout+result.stderr)
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.digest(self.snapshot), self.hash)

    def test_fifo_source_is_rejected_without_waiting_for_a_writer(self):
        fifo = self.root / "input-fifo"
        os.mkfifo(fifo, 0o600)
        with self.assertRaisesRegex(ValueError, "private regular"):
            rehearse_utc_copy(fifo, self.destination, expected_sha256=self.hash,
                              legacy_timezone="UTC", reporting_timezone="UTC")
        self.assertFalse(self.destination.exists())

    def test_archive_drift_during_conversion_prevents_publication(self):
        original = utc_migration._convert

        def mutate_archive(*args):
            report = original(*args)
            with self.snapshot.open("ab") as stream:
                stream.write(b"external-change")
            return report

        with patch("utc_migration._convert", side_effect=mutate_archive):
            with self.assertRaisesRegex(RuntimeError, "Archived snapshot changed"):
                self.convert()
        self.assertFalse(self.destination.exists())
        self.assertFalse(list(self.root.glob(".utc-rehearsal-*")))
