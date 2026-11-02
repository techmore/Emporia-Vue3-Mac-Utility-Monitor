import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import energy


class FixedDate(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 11, 3, 12, 0, 0)


class MonthAndTrendTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = patch.object(energy, "DB_PATH", str(Path(self.directory.name) / "energy.db"))
        self.database.start()
        energy.ensure_table()
        self.clock = patch.object(energy, "datetime", FixedDate)
        self.clock.start()

    def tearDown(self):
        self.clock.stop()
        self.database.stop()
        self.directory.cleanup()

    def _insert_hourly(self, start, hours, kwh_for):
        rows = []
        for offset in range(hours):
            moment = start + timedelta(hours=offset)
            rows.append((moment.isoformat(), "A", "Main", kwh_for(moment), 1.0))
        connection = energy._connect()
        try:
            connection.executemany(
                "INSERT INTO readings(timestamp,device_gid,channel_name,usage_kwh,cost_cents) "
                "VALUES (?,?,?,?,?)", rows)
            connection.commit()
        finally:
            connection.close()

    def test_month_to_date_is_compared_with_the_same_days_of_last_month(self):
        # October: 1 kWh every hour. November so far: 2.5 days at 1 kWh/hour.
        self._insert_hourly(datetime(2026, 10, 1), 31 * 24, lambda _: 1.0)
        self._insert_hourly(datetime(2026, 11, 1), 60, lambda _: 1.0)
        result = energy.get_month_comparison("A")
        self.assertEqual(result["this_month"]["total_kwh"], 60)
        self.assertEqual(result["last_month"]["total_kwh"], 31 * 24)
        self.assertEqual(result["last_month_same_period"]["total_kwh"], 60)
        self.assertEqual(result["last_month_same_period"]["through"], "2026-10-03T12:00:00")

    def test_same_period_is_absent_without_last_month_history(self):
        self._insert_hourly(datetime(2026, 11, 1), 10, lambda _: 1.0)
        self.assertIsNone(energy.get_month_comparison("A")["last_month_same_period"])

    def test_trend_ignores_partial_days(self):
        # Ten whole days at 24 kWh/day, then today half recorded, plus a stray tail
        # from the day before the window would otherwise start mid-day.
        self._insert_hourly(datetime(2026, 10, 24), 10 * 24, lambda _: 1.0)
        self._insert_hourly(datetime(2026, 11, 3), 12, lambda _: 1.0)
        self._insert_hourly(datetime(2026, 10, 20, 18), 6, lambda _: 1.0)
        trend = energy.get_trend(14, "A")
        days = {row["day"]: row for row in trend["daily"]}
        self.assertTrue(days["2026-11-03"]["partial"])
        self.assertTrue(days["2026-10-20"]["partial"])
        self.assertEqual(trend["complete_days"], 10)
        self.assertEqual(trend["avg_kwh"], 24)
        self.assertEqual(trend["best_day"]["total_kwh"], 24)
        self.assertEqual(trend["direction"], "steady")

    def test_small_slope_is_steady_and_large_change_is_rising(self):
        self._insert_hourly(datetime(2026, 10, 24), 10 * 24,
                            lambda moment: 1.0 + 0.004 * (moment - datetime(2026, 10, 24)).days)
        self.assertEqual(energy.get_trend(14, "A")["direction"], "steady")

    def test_rising_usage_is_reported(self):
        self._insert_hourly(datetime(2026, 10, 24), 10 * 24,
                            lambda moment: 1.0 + 0.05 * (moment - datetime(2026, 10, 24)).days)
        trend = energy.get_trend(14, "A")
        self.assertEqual(trend["direction"], "rising")
        self.assertGreater(trend["change_pct"], 5)

    def test_intraday_comparison_leaves_unrecorded_hours_empty(self):
        self._insert_hourly(datetime(2026, 11, 3), 12, lambda _: 1.0)
        result = energy.get_intraday_comparison("A")
        self.assertEqual(result["today"][:12], [1.0] * 12)
        self.assertEqual(result["today"][12:], [None] * 12)
        self.assertEqual(result["yesterday"], [None] * 24)


if __name__ == "__main__":
    unittest.main()
