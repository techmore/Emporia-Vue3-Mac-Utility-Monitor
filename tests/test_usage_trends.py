import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import energy


class CircuitWeekTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = patch.object(
            energy, "DB_PATH", str(Path(self.directory.name) / "energy.db"),
        )
        self.database.start()
        energy.ensure_table()
        self.end = datetime(2026, 10, 7)

    def tearDown(self):
        self.database.stop()
        self.directory.cleanup()

    def seed(self, current_minutes=10080, previous_minutes=10080, baseline=0.001):
        start = self.end - timedelta(days=14)
        middle = self.end - timedelta(days=7)
        rows = [
            ((start + timedelta(minutes=i)).isoformat(), "A", "Heat Pump", baseline)
            for i in range(previous_minutes)
        ] + [
            ((middle + timedelta(minutes=i)).isoformat(), "A", "Heat Pump", 0.002)
            for i in range(current_minutes)
        ]
        conn = energy._connect()
        try:
            conn.executemany(
                "INSERT INTO readings(timestamp,device_gid,channel_name,usage_kwh) "
                "VALUES (?,?,?,?)", rows,
            )
            conn.commit()
        finally:
            conn.close()

    def test_complete_windows_report_measured_change(self):
        self.seed()
        row = energy.get_circuit_week_comparison("A", end=self.end)[0]
        self.assertTrue(row["comparable"])
        self.assertAlmostEqual(row["change_pct"], 100)
        self.assertEqual(row["current_coverage_pct"], 100)

    def test_missing_week_is_not_reported_as_savings(self):
        self.seed(current_minutes=120)
        row = energy.get_circuit_week_comparison("A", end=self.end)[0]
        self.assertFalse(row["comparable"])
        self.assertIsNone(row["change_pct"])

    def test_unequal_capture_suppresses_change_even_above_threshold(self):
        self.seed(current_minutes=9800)
        row = energy.get_circuit_week_comparison("A", end=self.end)[0]
        self.assertGreater(row["current_coverage_pct"], 95)
        self.assertFalse(row["comparable"])
        self.assertIsNone(row["change_pct"])

    def test_zero_baseline_does_not_divide_by_zero(self):
        self.seed(baseline=0)
        row = energy.get_circuit_week_comparison("A", end=self.end)[0]
        self.assertTrue(row["comparable"])
        self.assertIsNone(row["change_pct"])

    def test_other_device_and_current_partial_day_are_excluded(self):
        self.seed(current_minutes=1, previous_minutes=1)
        conn = energy._connect()
        try:
            conn.executemany(
                "INSERT INTO readings(timestamp,device_gid,channel_name,usage_kwh) "
                "VALUES (?,?,?,?)",
                [(self.end.isoformat(), "A", "Heat Pump", 999),
                 ((self.end-timedelta(days=1)).isoformat(), "B", "Other", 999),
                 ((self.end-timedelta(days=1)).isoformat(), "A", "Main", 999)],
            )
            conn.commit()
        finally:
            conn.close()
        rows = energy.get_circuit_week_comparison("A", end=self.end)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["current_kwh"], 0.002)
