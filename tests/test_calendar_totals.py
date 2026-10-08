import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import energy
import radon
import web


class FixedDate(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 8, 14, 0, 0)


class CalendarCircuitTotalsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = patch.object(energy, "DB_PATH", str(Path(self.directory.name) / "energy.db"))
        self.database.start()
        energy.ensure_table()
        self.clock = patch.object(energy, "datetime", FixedDate)
        self.clock.start()
        connection = energy._connect()
        try:
            connection.executemany(
                "INSERT INTO readings(timestamp,device_gid,channel_name,usage_kwh,cost_cents) "
                "VALUES (?,?,?,?,?)",
                [("2026-10-01T12:00:00", "A", "one", 1, 22.58),
                 ("2026-10-05T12:00:00", "A", "one", 2, 45.16),
                 ("2026-10-08T00:00:00", "A", "one", 3, 67.74),
                 ("2026-10-08T14:00:00", "A", "one", 4, 90.32),
                 ("2026-10-08T15:00:00", "A", "one", 99, 999),
                 ("2026-09-30T12:00:00", "A", "one", 88, 1987),
                 ("2026-10-08T12:00:00", "B", "one", 123, 999),
                 ("2026-10-08T12:00:00", "A", "Main", 1234, 8888)],
            )
            connection.commit()
        finally:
            connection.close()

    def tearDown(self):
        self.clock.stop()
        self.database.stop()
        self.directory.cleanup()

    def test_day_week_month_boundaries_preserve_device_and_circuit_scope(self):
        for period, expected in [("day", 7), ("week", 9), ("month", 10)]:
            with self.subTest(period=period):
                rows = energy.get_today_circuit_totals("A", period)
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["channel_name"], "one")
                self.assertEqual(rows[0]["total_kwh"], expected)
                self.assertAlmostEqual(rows[0]["total_cents"], expected * 22.58)

    def test_invalid_period_and_no_data_are_not_silent_zero(self):
        with self.assertRaises(ValueError):
            energy.get_today_circuit_totals("A", "year")
        self.assertEqual(energy.get_today_circuit_totals("missing"), [])

    def test_native_menu_contract_keeps_additive_fields(self):
        with patch.object(radon, "get_latest_indicator", return_value={
                "pci_l": 0.7, "timestamp": "2026-10-08T17:00:00+00:00", "stale": False}), \
                patch.object(energy, "get_active_device_gid", return_value="A"):
            response = web.app.test_client().get("/api/menu-summary")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["radon_reading"]["pci_l"], 0.7)
        for row in data["breaker_slots"]:
            self.assertIn("today_kwh", row)
            self.assertIn("today_cost", row)
            self.assertEqual(set(row["period_totals"]), {"week", "month"})
