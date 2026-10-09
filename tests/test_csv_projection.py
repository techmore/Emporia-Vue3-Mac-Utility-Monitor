import hashlib
import random
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import energy
from csv_projection import select_intervals
from energy_clock import EnergyClock
from utc_migration import _convert


class CSVProjectionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.database = str(self.root / "energy.db")
        database = patch.object(energy, "DB_PATH", self.database)
        database.start()
        self.addCleanup(database.stop)
        rate = patch.object(energy, "RATE_CENTS", 22.58)
        rate.start()
        self.addCleanup(rate.stop)
        energy.ensure_table()

    def upload(self, interval, *, minutes=60, offset=0, value=None, day=8, gid="QA"):
        path = self.root / f"{gid}-Panel-{interval}.csv"
        start = datetime(2026, 10, day, 12) + timedelta(minutes=offset)
        values = (
            [(start, 3 if value is None else value)]
            if interval != "1MIN"
            else [
                (start + timedelta(minutes=index), 0.05 if value is None else value)
                for index in range(minutes)
            ]
        )
        path.write_text(
            "Time Bucket (America/New_York),QA-Pump (kWhs)\n"
            + "".join(f"{stamp:%m/%d/%Y %H:%M:%S},{number}\n" for stamp, number in values)
        )
        return energy.import_emporia_csv(str(path), device_gid=gid)

    def rows(self, table):
        conn = energy._connect()
        try:
            return [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY 1")]
        finally:
            conn.close()

    def total(self, gid="QA"):
        result = energy.get_circuit_history("Pump", gid, now=datetime(2026, 10, 10, 14))
        return result["windows"][1]["total_kwh"]

    def reset(self):
        energy.DB_PATH = str(self.root / f"case-{len(list(self.root.glob('*.db')))}.db")
        energy.ensure_table()

    def state(self):
        return {
            table: self.rows(table)
            for table in (
                "readings",
                "reading_changes",
                "latest_channel_snapshot",
                "csv_source_batches",
                "csv_source_observations",
                "csv_reading_projection",
            )
        }

    def test_complete_fine_coverage_is_order_independent_without_double_counting(self):
        for order in (("1H", "1MIN"), ("1MIN", "1H")):
            with self.subTest(order=order):
                self.reset()
                for interval in order:
                    self.upload(interval)
                self.assertAlmostEqual(self.total(), 3)
                self.assertEqual(len(self.rows("readings")), 60)
                self.assertEqual(len(self.rows("csv_source_observations")), 61)

    def test_partial_fine_coverage_keeps_complete_hour_in_both_orders(self):
        for offset in (0, 10):
            for order in (("1H", "1MIN"), ("1MIN", "1H")):
                with self.subTest(offset=offset, order=order):
                    self.reset()
                    for interval in order:
                        self.upload(
                            interval, minutes=20, offset=offset if interval == "1MIN" else 0
                        )
                    self.assertAlmostEqual(self.total(), 3)
                    self.assertEqual(len(self.rows("readings")), 1)
                    self.assertEqual(self.rows("readings")[0]["measurement_seconds"], 3600)

    def test_later_complete_file_activates_retained_finer_observations(self):
        self.upload("1H")
        self.upload("1MIN", minutes=30)
        result = self.upload("1MIN")
        self.assertAlmostEqual(self.total(), 3)
        self.assertEqual(len(self.rows("readings")), 60)
        self.assertEqual(len(self.rows("csv_source_observations")), 91)
        self.assertEqual(result["imported"], 60)

    def test_adjacent_intervals_are_not_overlapping(self):
        for order in (("1H", "1MIN"), ("1MIN", "1H")):
            self.reset()
            for interval in order:
                self.upload(interval, minutes=10, offset=60 if interval == "1MIN" else 0)
            self.assertAlmostEqual(self.total(), 3.5)
            self.assertEqual(len(self.rows("readings")), 11)

    def test_crossing_buckets_need_review_until_complete_fine_coverage_exists(self):
        path = self.root / "QA-Panel-1H.csv"
        path.write_text(
            "Time Bucket (America/New_York),QA-Pump (kWhs)\n"
            "10/08/2026 12:00:00,3\n10/08/2026 12:30:00,3\n"
        )
        result = energy.import_emporia_csv(str(path))
        self.assertEqual(result["imported"], 0)
        self.assertGreater(result["warnings"], 0)
        self.assertEqual(self.rows("readings"), [])
        result = self.upload("1MIN", minutes=90)
        self.assertEqual(result["warnings"], 0)
        self.assertEqual(result["imported"], 90)
        self.assertAlmostEqual(self.total(), 4.5)

    def test_bom_and_source_cell_whitespace_are_retained_exactly(self):
        path = self.root / "QA-Panel-1H.csv"
        original = (
            "\ufeffTime Bucket (America/New_York),QA-Pump (kWhs)\n 10/08/2026 12:00:00 , 3 \n"
        ).encode("utf-8")
        path.write_bytes(original)
        energy.import_emporia_csv(str(path))
        self.assertEqual(self.rows("csv_source_batches")[0]["content"], original)
        row = self.rows("csv_source_observations")[0]
        self.assertEqual(row["raw_timestamp"], " 10/08/2026 12:00:00 ")
        self.assertEqual(row["raw_value"], " 3 ")
        self.assertAlmostEqual(self.total(), 3)

    def test_exact_file_bytes_and_source_cells_survive_projection(self):
        self.upload("1H")
        source = (self.root / "QA-Panel-1H.csv").read_bytes()
        self.upload("1MIN")
        (batch,) = [row for row in self.rows("csv_source_batches") if row["interval"] == "1H"]
        self.assertEqual(batch["content"], source)
        self.assertEqual(batch["sha256"], hashlib.sha256(source).hexdigest())
        (observation,) = [
            row for row in self.rows("csv_source_observations") if row["batch_id"] == batch["id"]
        ]
        self.assertEqual(observation["raw_value"], "3")
        self.assertEqual(observation["raw_timestamp"], "10/08/2026 12:00:00")
        self.assertEqual(observation["start_utc"], "2026-10-08T16:00:00.000000+00:00")
        self.assertEqual(observation["end_utc"], "2026-10-08T17:00:00.000000+00:00")
        self.assertEqual(observation["usage_kwh"], 3)
        self.assertAlmostEqual(observation["cost_cents"], 67.74)

    def test_same_file_is_idempotent_even_after_rate_change(self):
        self.upload("1H")
        before = self.state()
        with patch.object(energy, "RATE_CENTS", 99):
            result = self.upload("1H")
        self.assertEqual(self.state(), before)
        self.assertEqual(result["observations_recorded"], 0)
        self.assertEqual(result["imported"], 0)
        self.assertEqual(result["skipped"], 1)

    def test_first_source_precedence_survives_vacuum(self):
        self.upload("1H")
        self.upload("1H", value=999)
        before = self.state()
        conn = energy._connect()
        conn.execute("VACUUM")
        conn.close()
        result = self.upload("1H", value=999)
        self.assertGreater(result["warnings"], 0)
        self.assertEqual(self.state(), before)
        self.assertAlmostEqual(self.total(), 3)

    def test_conflicting_duplicate_is_retained_but_cannot_overwrite_accepted_energy(self):
        self.upload("1H")
        before = (
            self.rows("readings"),
            self.rows("reading_changes"),
            self.rows("latest_channel_snapshot"),
        )
        result = self.upload("1H", value=999)
        self.assertEqual(
            (
                self.rows("readings"),
                self.rows("reading_changes"),
                self.rows("latest_channel_snapshot"),
            ),
            before,
        )
        self.assertEqual(len(self.rows("csv_source_observations")), 2)
        self.assertGreater(result["warnings"], 0)
        self.assertEqual(result["imported"], 0)

    def test_disagreeing_complete_resolutions_require_review_and_keep_prior_projection(self):
        for order, expected in ((("1H", "1MIN"), 3), (("1MIN", "1H"), 6)):
            self.reset()
            result = None
            for interval in order:
                result = self.upload(interval, value=0.1 if interval == "1MIN" else 3)
            self.assertAlmostEqual(self.total(), expected)
            self.assertGreater(result["warnings"], 0)
            self.assertEqual(result["imported"], 0)

    def test_unmanaged_unknown_history_is_not_guessed_or_doubled(self):
        conn = energy._connect()
        conn.execute(
            "INSERT INTO readings(timestamp,device_gid,channel_name,usage_kwh,cost_cents) "
            "VALUES ('2026-10-08T12:00:30','QA','Pump',3,67.74)"
        )
        conn.commit()
        conn.close()
        before = self.rows("readings"), self.rows("reading_changes")
        result = self.upload("1H")
        self.assertEqual((self.rows("readings"), self.rows("reading_changes")), before)
        self.assertEqual(len(self.rows("csv_source_observations")), 1)
        self.assertGreater(result["warnings"], 0)
        self.assertEqual(result["imported"], 0)

    def test_unknown_interval_is_preserved_and_marked_unresolved(self):
        result = self.upload("UNKNOWN")
        self.assertEqual(result["imported"], 1)
        self.assertIsNone(self.rows("readings")[0]["measurement_seconds"])
        self.assertGreater(result["warnings"], 0)
        second = self.upload("1H")
        self.assertEqual(second["imported"], 0)
        self.assertGreater(second["warnings"], 0)
        self.assertEqual(len(self.rows("readings")), 1)

    def test_failed_publication_rolls_back_source_ledger_projection_and_journal(self):
        self.upload("1H")
        before = self.state()
        with patch.object(
            energy,
            "_save_device_capabilities_with_conn",
            side_effect=RuntimeError("fixture failure"),
        ):
            with self.assertRaises(RuntimeError):
                self.upload("1MIN")
        self.assertEqual(self.state(), before)

    def test_source_evidence_is_immutable_at_the_database_boundary(self):
        self.upload("1H")
        before = self.state()
        conn = energy._connect()
        try:
            for table in ("csv_source_batches", "csv_source_observations"):
                for query in (f"UPDATE {table} SET id=id", f"DELETE FROM {table}"):
                    with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                        conn.execute(query)
                    conn.rollback()
        finally:
            conn.close()
        self.assertEqual(self.state(), before)

    def test_failure_during_projection_update_leaves_no_partial_ledger_or_journal(self):
        self.upload("1H")
        before = self.state()
        conn = energy._connect()

        def deny_update(action, table, column, database, trigger):
            return (
                sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_UPDATE and table == "readings"
                else sqlite3.SQLITE_OK
            )

        conn.set_authorizer(deny_update)
        with patch.object(energy, "_connect", return_value=conn):
            with self.assertRaises(sqlite3.DatabaseError):
                self.upload("1MIN")
        self.assertEqual(self.state(), before)

    def test_unmanaged_verified_csv_is_not_adopted_but_disjoint_new_history_can_publish(self):
        conn = energy._connect()
        conn.execute(
            "INSERT INTO readings(timestamp,device_gid,channel_name,usage_kwh,cost_cents,"
            "measurement_seconds,measurement_source,source_timezone) VALUES "
            "('2026-10-08T12:00:00','QA','Pump',3,67.74,3600,'csv_energy','America/New_York')"
        )
        conn.commit()
        conn.close()
        blocked = self.upload("1MIN")
        self.assertEqual(blocked["imported"], 0)
        self.assertGreater(blocked["warnings"], 0)
        self.assertEqual(len(self.rows("readings")), 1)
        disjoint = self.upload("1H", day=9)
        self.assertEqual(disjoint["imported"], 1)
        self.assertAlmostEqual(self.total(), 6)

    def test_source_ledger_survives_real_utc_conversion_and_private_projection_writes(self):
        self.upload("1H")
        original_batch = self.rows("csv_source_batches")
        original_observations = self.rows("csv_source_observations")
        archive = self.root / "archive.db"
        energy.backup_database(archive)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        # Disposable pre-opened handles only; ordinary writable UTC remains guarded.
        conversion, writer = energy._connect(), energy._connect()
        self.addCleanup(conversion.close)
        self.addCleanup(writer.close)
        with conversion:
            conversion.execute("BEGIN IMMEDIATE")
            _convert(conversion, "America/New_York", "America/New_York", digest)
        with patch.object(energy, "_connect", return_value=writer):
            result = self.upload("1MIN")
        self.assertEqual(result["timestamp_format"], "utc_v1")
        inspection = energy._connect(allow_utc_rehearsal=True, read_only=True)
        try:
            self.assertEqual(
                [
                    dict(row)
                    for row in inspection.execute(
                        'SELECT * FROM csv_source_batches WHERE interval="1H"'
                    )
                ],
                original_batch,
            )
            self.assertEqual(
                [
                    dict(row)
                    for row in inspection.execute(
                        "SELECT * FROM csv_source_observations WHERE measurement_seconds=3600"
                    )
                ],
                original_observations,
            )
            rows = [dict(row) for row in inspection.execute("SELECT * FROM readings")]
            self.assertEqual(len(rows), 60)
            self.assertAlmostEqual(sum(row["usage_kwh"] for row in rows), 3)
            self.assertTrue(all(EnergyClock.parse(row["timestamp"]) for row in rows))
        finally:
            inspection.close()
        self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), digest)
        with self.assertRaises(RuntimeError):
            energy._connect()

    def test_retention_removes_membership_without_resurrecting_unrelated_archived_sources(self):
        self.upload("1H")
        self.upload("1MIN")
        conn = energy._connect()
        conn.execute("DELETE FROM readings")
        conn.commit()
        conn.close()
        self.assertEqual(self.rows("csv_reading_projection"), [])
        self.upload("1H", day=9)
        self.assertEqual(len(self.rows("readings")), 1)
        self.assertTrue(self.rows("readings")[0]["timestamp"].startswith("2026-10-09"))

    def test_explicit_reimport_can_restore_verified_retained_fine_sources_after_pruning(self):
        self.upload("1H")
        self.upload("1MIN")
        conn = energy._connect()
        conn.execute("DELETE FROM readings")
        conn.commit()
        conn.close()
        result = self.upload("1H")
        self.assertEqual(result["imported"], 0)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["inserted"], 60)
        self.assertAlmostEqual(self.total(), 3)

    def test_projection_is_device_scoped(self):
        self.upload("1H", gid="Other")
        before = self.rows("readings")[0]
        self.upload("1H")
        self.upload("1MIN")
        self.assertAlmostEqual(self.total("Other"), 3)
        self.assertEqual(
            [row for row in self.rows("readings") if row["device_gid"] == "Other"], [before]
        )

    def test_signed_and_zero_energy_survive_resolution_selection(self):
        for value in (0, -0.05):
            self.reset()
            self.upload("1H", value=value * 60)
            self.upload("1MIN", value=value)
            self.assertAlmostEqual(self.total(), value * 60)
            self.assertEqual(len(self.rows("readings")), 60)

    def test_sync_replaces_coarse_projection_without_reseeding_identity(self):
        self.upload("1H")
        first = energy.get_reading_changes()
        with patch.object(energy, "DB_PATH", str(self.root / "cache.db")):
            energy.ensure_table()
            state = energy.apply_reading_changes(first)
        self.upload("1MIN")
        second = energy.get_reading_changes(state["cursor"])
        self.assertEqual(first["source_id"], second["source_id"])
        self.assertEqual(first["generation_id"], second["generation_id"])
        with patch.object(energy, "DB_PATH", str(self.root / "cache.db")):
            energy.apply_reading_changes(second)
            rows = self.rows("sync_cached_readings")
            self.assertEqual(len(rows), 60)
            self.assertAlmostEqual(sum(row["usage_kwh"] for row in rows), 3)

    def test_single_event_sync_pages_never_mix_superseded_and_replacement_intervals(self):
        for old, new in (("1H", "1MIN"), ("1MIN", "1H")):
            with self.subTest(old=old):
                self.reset()
                self.upload(old, minutes=20)
                initial = energy.get_reading_changes()
                cache = str(self.root / f"paged-{old}.db")
                with patch.object(energy, "DB_PATH", cache):
                    energy.ensure_table()
                    state = energy.apply_reading_changes(initial)
                self.upload(new, minutes=60)
                while True:
                    page = energy.get_reading_changes(state["cursor"], limit=1)
                    with patch.object(energy, "DB_PATH", cache):
                        state = energy.apply_reading_changes(page)
                        rows = sorted(
                            self.rows("sync_cached_readings"), key=lambda row: row["timestamp"]
                        )
                    for left, right in zip(rows, rows[1:], strict=False):
                        self.assertLessEqual(
                            datetime.fromisoformat(left["timestamp"])
                            + timedelta(seconds=left["measurement_seconds"]),
                            datetime.fromisoformat(right["timestamp"]),
                        )
                    if not page["has_more"]:
                        break
                self.assertAlmostEqual(sum(row["usage_kwh"] for row in rows), 3)


class IntervalSelectionTests(unittest.TestCase):
    def test_selection_matches_exhaustive_coverage_and_resolution_search(self):
        randomizer = random.Random(135)
        epoch = datetime(2026, 10, 8, tzinfo=timezone.utc)
        for _ in range(20):
            rows = []
            for index in range(8):
                start = randomizer.randrange(12)
                end = start + randomizer.randrange(1, 8)
                rows.append(
                    {
                        "id": str(index),
                        "start_utc": (epoch + timedelta(seconds=start)).isoformat(
                            timespec="microseconds"
                        ),
                        "end_utc": (epoch + timedelta(seconds=end)).isoformat(
                            timespec="microseconds"
                        ),
                        "start": start,
                        "end": end,
                    }
                )
            best = (0, 0)
            for mask in range(1 << len(rows)):
                candidate = sorted(
                    [row for index, row in enumerate(rows) if mask & (1 << index)],
                    key=lambda row: row["start"],
                )
                if all(
                    left["end"] <= right["start"]
                    for left, right in zip(candidate, candidate[1:], strict=False)
                ):
                    best = max(
                        best, (sum(row["end"] - row["start"] for row in candidate), len(candidate))
                    )
            selected = select_intervals(rows)
            self.assertEqual(
                (sum(row["end"] - row["start"] for row in selected), len(selected)), best
            )
            self.assertEqual(select_intervals(list(reversed(rows))), selected)

    def test_unknown_or_noncanonical_bounds_cannot_be_selected(self):
        for start, end in (
            (None, None),
            ("2026-10-08T12:00:00", "2026-10-08T13:00:00"),
            ("2026-10-08T12:00:00.000000+00:00", "2026-10-08T12:00:00.000000+00:00"),
        ):
            with self.assertRaises(ValueError):
                select_intervals([{"id": "x", "start_utc": start, "end_utc": end}])


if __name__ == "__main__":
    unittest.main()
