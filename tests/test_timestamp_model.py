import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfoNotFoundError

from scripts.audit_timestamps import reading_rows
from timestamp_model import (
    audit_reading_timestamps,
    classify_timestamp,
    reporting_day_bounds,
)


class TimestampInterpretationTests(unittest.TestCase):
    def test_spring_gap_is_not_shifted_into_a_real_reading(self):
        result = classify_timestamp("2026-03-08T02:30:00", "America/New_York")
        self.assertEqual(result, {"status": "nonexistent", "utc_candidates": []})

    def test_fall_fold_lists_both_instants_without_choosing(self):
        result = classify_timestamp("2026-11-01T01:30:00", "America/New_York")
        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual(result["utc_candidates"], [
            "2026-11-01T05:30:00.000000+00:00",
            "2026-11-01T06:30:00.000000+00:00",
        ])

    def test_explicit_offsets_distinguish_both_folds(self):
        first = classify_timestamp("2026-11-01T01:30:00-04:00")
        second = classify_timestamp("2026-11-01T01:30:00-05:00")
        self.assertEqual(first["status"], "aware")
        difference = (datetime.fromisoformat(second["utc_candidates"][0]) -
                      datetime.fromisoformat(first["utc_candidates"][0]))
        self.assertEqual(difference, timedelta(hours=1))

    def test_explicit_offset_is_not_reinterpreted_using_legacy_zone(self):
        result = classify_timestamp("2026-10-08T12:34:56.123456Z", "Asia/Tokyo")
        self.assertEqual(result["utc_candidates"], ["2026-10-08T12:34:56.123456+00:00"])

    def test_unique_legacy_timestamp_preserves_microseconds(self):
        result = classify_timestamp("2026-10-08T20:35:43.285363", "America/New_York")
        self.assertEqual(result["status"], "legacy_unique")
        self.assertEqual(result["utc_candidates"], ["2026-10-09T00:35:43.285363+00:00"])

    def test_missing_provenance_is_not_assumed_to_be_host_timezone(self):
        self.assertEqual(classify_timestamp("2026-10-08T12:00:00")["status"],
                         "missing_source_timezone")

    def test_non_hour_dst_transition_is_detected(self):
        gap = classify_timestamp("2026-10-04T02:15:00", "Australia/Lord_Howe")
        fold = classify_timestamp("2026-04-05T01:45:00", "Australia/Lord_Howe")
        self.assertEqual(gap["status"], "nonexistent")
        self.assertEqual(fold["status"], "ambiguous")
        first, second = map(datetime.fromisoformat, fold["utc_candidates"])
        self.assertEqual(second - first, timedelta(minutes=30))

    def test_malformed_and_out_of_range_values_are_invalid(self):
        for value in (None, 123, "", "2026-10-08", "2026-10-08T12:00:00\n",
                      "2026-02-30T12:00:00", "2026-10-08T25:00:00",
                      "0001-01-01T00:00:00+14:00"):
            with self.subTest(value=value):
                self.assertEqual(classify_timestamp(value)["status"], "invalid")

    def test_unknown_zone_fails_closed(self):
        with self.assertRaises(ZoneInfoNotFoundError):
            classify_timestamp("2026-10-08T12:00:00", "Invalid/Zone")

    def test_reporting_days_have_actual_dst_duration(self):
        for day, hours in ((date(2026, 3, 8), 23), (date(2026, 11, 1), 25),
                           (date(2026, 10, 8), 24)):
            with self.subTest(day=day):
                start, end = reporting_day_bounds(day, "America/New_York")
                self.assertEqual(end-start, timedelta(hours=hours))
                self.assertEqual(start.utcoffset(), timedelta(0))

    def test_skipped_calendar_day_is_not_fabricated(self):
        with self.assertRaises(ValueError):
            reporting_day_bounds(date(2011, 12, 30), "Pacific/Apia")

    def test_calendar_bounds_reject_a_datetime(self):
        with self.assertRaises(ValueError):
            reporting_day_bounds(datetime(2026, 10, 8), "UTC")


class TimestampAuditTests(unittest.TestCase):
    def row(self, identifier=1, timestamp="2026-10-08T12:00:00", **extra):
        return {"id": identifier, "timestamp": timestamp, "device_gid": "A",
                "channel_name": "Main", **extra}

    def test_assumption_is_explicit_and_does_not_approve_migration(self):
        report = audit_reading_timestamps([self.row()], legacy_timezone="America/New_York")
        self.assertTrue(report["candidate_conversion_unblocked"])
        self.assertFalse(report["migration_ready"])
        self.assertTrue(report["dry_run"])
        self.assertEqual(report["legacy_timezone_assumption"], "America/New_York")
        self.assertEqual(report["projected_utc_range"]["first"],
                         "2026-10-08T16:00:00.000000+00:00")

    def test_per_row_zone_overrides_dataset_assumption(self):
        report = audit_reading_timestamps(
            [self.row(source_timezone="Asia/Tokyo")], legacy_timezone="America/New_York",
        )
        self.assertEqual(report["projected_utc_range"]["first"],
                         "2026-10-08T03:00:00.000000+00:00")

    def test_explicit_null_provenance_is_unresolved(self):
        report = audit_reading_timestamps([self.row(source_timezone=None)], legacy_timezone="UTC")
        self.assertFalse(report["candidate_conversion_unblocked"])
        self.assertEqual(report["unresolved_rows"], 1)

    def test_mixed_naive_and_aware_values_expose_unique_key_collision(self):
        rows = [self.row(), self.row(2, "2026-10-08T16:00:00Z")]
        report = audit_reading_timestamps(rows, legacy_timezone="America/New_York")
        self.assertFalse(report["candidate_conversion_unblocked"])
        self.assertEqual(report["utc_key_collisions"], 1)
        self.assertEqual(report["examples"][0]["conflicting_reading_id"], 1)

    def test_different_devices_channels_and_null_channels_do_not_collide(self):
        rows = [self.row(), self.row(2, device_gid="B"),
                self.row(3, channel_name="Heat Pump"),
                self.row(4, channel_name=None), self.row(5, channel_name=None)]
        self.assertEqual(audit_reading_timestamps(rows, legacy_timezone="UTC")
                         ["utc_key_collisions"], 0)

    def test_duplicate_reading_ids_block_even_for_distinct_keys(self):
        report = audit_reading_timestamps(
            [self.row(), self.row(channel_name="Heat Pump")], legacy_timezone="UTC",
        )
        self.assertEqual(report["duplicate_reading_ids"], 1)
        self.assertFalse(report["candidate_conversion_unblocked"])

    def test_ambiguous_nonexistent_and_invalid_rows_are_all_counted(self):
        rows = [self.row(1, "2026-11-01T01:30:00"),
                self.row(2, "2026-03-08T02:30:00"), self.row(3, "private-secret"),
                self.row(True), self.row(0), self.row(5, device_gid=12), []]
        report = audit_reading_timestamps(rows, legacy_timezone="America/New_York", example_limit=1)
        self.assertEqual(report["unresolved_rows"], 7)
        self.assertEqual(len(report["examples"]), 1)
        self.assertEqual(report["classifications"]["invalid_row"], 4)
        self.assertNotIn("private-secret", json.dumps(report))

    def test_zero_example_limit_does_not_hide_blockers(self):
        report = audit_reading_timestamps([self.row()], example_limit=0)
        self.assertEqual(report["examples"], [])
        self.assertEqual(report["unresolved_rows"], 1)

    def test_empty_export_is_not_ready(self):
        report = audit_reading_timestamps([], legacy_timezone="UTC")
        self.assertFalse(report["candidate_conversion_unblocked"])
        self.assertIsNone(report["projected_utc_range"]["first"])

    def test_example_limit_validation(self):
        for value in (True, -1, 101, "20"):
            with self.assertRaises(ValueError):
                audit_reading_timestamps([], example_limit=value)

    def test_unknown_or_nonstring_provenance_fails_closed(self):
        with self.assertRaises(ZoneInfoNotFoundError):
            audit_reading_timestamps([self.row(source_timezone="Invalid/Zone")])
        with self.assertRaises(ValueError):
            audit_reading_timestamps([self.row(source_timezone=12)])

    def test_json_lines_rejects_malformed_and_oversized_records(self):
        for text in ("\n", "private-secret", '"' + "x"*65536 + '"\n'):
            with self.assertRaises(ValueError) as caught:
                list(reading_rows(io.StringIO(text)))
            self.assertNotIn("private-secret", str(caught.exception))

    def test_cli_is_read_only_and_does_not_import_app_schema_initializer(self):
        script = Path(__file__).resolve().parents[1] / "scripts/audit_timestamps.py"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "readings.jsonl"
            source.write_text(json.dumps(self.row()) + "\n")
            before = source.read_bytes()
            environment = {**os.environ, "DB_PATH": str(root / "must-not-exist.db")}
            for zone in ("UTC", "Asia/Tokyo"):
                process = subprocess.run(
                    [sys.executable, str(script), "--input", str(source),
                     "--legacy-timezone", "America/New_York"],
                    env={**environment, "TZ": zone}, capture_output=True, text=True,
                    cwd=root, timeout=10,
                )
                self.assertEqual(process.returncode, 0, process.stderr)
                self.assertFalse(json.loads(process.stdout)["migration_ready"])
                self.assertEqual(source.read_bytes(), before)
                self.assertEqual(sorted(path.name for path in root.iterdir()), ["readings.jsonl"])
            source.write_text(json.dumps(self.row(timestamp="private-secret")) + "\n")
            process = subprocess.run([sys.executable, str(script), "--input", str(source)],
                                     capture_output=True, text=True, cwd=root, timeout=10)
            self.assertEqual(process.returncode, 2)
            self.assertNotIn("private-secret", process.stdout + process.stderr)
            source.write_text("private-secret\n")
            process = subprocess.run([sys.executable, str(script), "--input", str(source)],
                                     capture_output=True, text=True, cwd=root, timeout=10)
            self.assertEqual(process.returncode, 1)
            self.assertNotIn("private-secret", process.stdout + process.stderr)
