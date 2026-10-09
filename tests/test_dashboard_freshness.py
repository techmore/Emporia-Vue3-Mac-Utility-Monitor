import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import energy
import web


class DashboardFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.instant = datetime(2026, 10, 8, 16, 0, 0, 250000, tzinfo=timezone.utc)
        test = self

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                if tz is None:
                    return test.instant.astimezone(ZoneInfo("America/New_York")).replace(tzinfo=None)
                return test.instant.astimezone(tz)

        clock = patch.object(web, "datetime", Clock)
        clock.start()
        self.addCleanup(clock.stop)

    def stamp(self, age):
        return (self.instant - timedelta(seconds=age)).isoformat()

    def test_offsets_and_z_refer_to_the_same_instant(self):
        for stamp in ("2026-10-08T15:59:30.250000Z",
                      "2026-10-08T15:59:30.250000+00:00",
                      "2026-10-08T11:59:30.250000-04:00",
                      "2026-10-09T00:59:30.250000+09:00"):
            with self.subTest(stamp=stamp):
                self.assertEqual(web._timestamp_age_secs(stamp), 30)
                self.assertTrue(web._reading_fresh({"timestamp": stamp}))
                self.assertEqual(web._status(stamp), ("live", "Live · 0m ago", 30/3600))

    def test_legacy_local_timestamp_retains_current_semantics(self):
        stamp = "2026-10-08T11:59:30.250000"
        self.assertEqual(web._timestamp_age_secs(stamp), 30)
        self.assertTrue(web._reading_fresh({"timestamp": stamp}))

    def test_microseconds_determine_exact_reading_boundary(self):
        for age, fresh in ((299.999999, True), (300, False), (300.000001, False)):
            with self.subTest(age=age):
                self.assertEqual(web._reading_fresh({"timestamp": self.stamp(age)}), fresh)

    def test_future_skew_is_bounded_without_negative_status_labels(self):
        for age, fresh in ((-60, True), (-60.000001, False), (-7200, False)):
            stamp = self.stamp(age)
            with self.subTest(age=age):
                self.assertEqual(web._reading_fresh({"timestamp": stamp}), fresh)
                status = web._status(stamp)
                self.assertEqual(status, ("live", "Live · 0m ago", 0) if fresh
                                 else ("dead", "Unknown", 999))

    def test_stale_and_offline_status_boundaries_are_preserved(self):
        for age, state in ((3599.999999, "live"), (3600, "stale"),
                           (21599.999999, "stale"), (21600, "dead")):
            with self.subTest(age=age):
                status = web._status(self.stamp(age))
                self.assertEqual(status[0], state)
                self.assertEqual(status[2], age/3600)

    def test_repeated_wall_hour_offsets_remain_distinct(self):
        self.instant = datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc)
        first = "2026-11-01T01:30:00-04:00"
        second = "2026-11-01T01:30:00-05:00"
        self.assertEqual(web._timestamp_age_secs(first), 3600)
        self.assertEqual(web._timestamp_age_secs(second), 0)
        self.assertFalse(web._reading_fresh({"timestamp": first}))
        self.assertTrue(web._reading_fresh({"timestamp": second}))

    def test_invalid_timestamps_fail_closed_in_all_consumers(self):
        for stamp in (None, 123, True, "", "2026-10-08", "2026-02-30T12:00:00",
                      "2026-10-08T12:00:00Z\n", "0001-01-01T00:00:00+14:00"):
            with self.subTest(stamp=stamp):
                self.assertIsNone(web._timestamp_age_secs(stamp))
                self.assertFalse(web._reading_fresh({"timestamp": stamp}))
                self.assertEqual(web._status(stamp), ("dead", "Unknown", 999))
                with patch.object(energy, "read_poller_status", return_value={"timestamp": stamp}):
                    status = web._poller_status_snapshot()
                self.assertIsNone(status["age_secs"])
                self.assertFalse(status["poller_running"])
        self.assertEqual(web._status("N/A"), ("dead", "No data", 999))
        self.assertFalse(web._reading_fresh({}))

    def test_poller_http_boundary_and_future_clock_skew(self):
        client = web.app.test_client()
        for age, running in ((179.999999, True), (180, False),
                             (-60, True), (-60.000001, False)):
            heartbeat = {"timestamp": self.stamp(age), "ok": True, "error": None}
            with self.subTest(age=age), patch.object(energy, "read_poller_status", return_value=heartbeat):
                response = client.get("/api/poller-status")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json["age_secs"], int(age))
                self.assertEqual(response.json["poller_running"], running)
                self.assertEqual(response.json["timestamp"], self.stamp(age))

    def test_native_menu_http_never_displays_stale_or_future_live_watts(self):
        replacements = {
            "get_active_device_gid": "A", "get_panel_layout": [], "get_device_labels": {},
            "get_today_circuit_totals": [], "get_main_total": {"total_cents": 200, "total_kwh": 10},
            "get_monthly_costs": [{"total_cents": 1000, "days_recorded": 3}],
            "get_reading_changes": {"source_id": "a" * 32},
        }
        with ExitStack() as stack:
            for name, result in replacements.items():
                stack.enter_context(patch.object(energy, name, return_value=result))
            stack.enter_context(patch.object(web, "_load_panel_slots", return_value=16))
            stack.enter_context(patch.object(web, "_load_panel_display_settings", return_value={}))
            stack.enter_context(patch.object(web.radon, "get_latest_indicator", return_value=None))
            stack.enter_context(patch.object(energy, "read_poller_status", return_value={
                "timestamp": self.stamp(30), "ok": True,
            }))
            for age, online in ((30, True), (180, False), (-60.000001, False)):
                reading = {"timestamp": self.stamp(age), "device_gid": "A",
                           "channel_name": "Main", "usage_kwh": 0.01}
                with self.subTest(age=age), patch.object(energy, "get_latest", return_value=[reading]):
                    response = web.app.test_client().get("/api/menu-summary")
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json["online"], online)
                    self.assertEqual(response.json["current_watts"], 600 if online else None)
                    self.assertEqual(response.json["cost_24h"], 2)
                    self.assertEqual(response.json["month_cost"], 10)

    def test_aware_readiness_is_independent_of_real_host_timezone(self):
        script = """
from datetime import datetime, timedelta, timezone
import web
stamp = (datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat()
age = web._timestamp_age_secs(stamp)
assert 89 <= age <= 91, age
assert web._reading_fresh({'timestamp': stamp}, 180)
assert web._status(stamp)[0] == 'live'
print('UTC readiness verified')
"""
        with tempfile.TemporaryDirectory() as directory:
            for index, zone in enumerate(("UTC", "America/New_York", "Asia/Tokyo")):
                with self.subTest(zone=zone):
                    result = subprocess.run(
                        [sys.executable, "-c", script], capture_output=True, text=True, timeout=30,
                        cwd=Path(web.__file__).parent,
                        env={**os.environ, "TZ": zone, "DB_PATH": str(Path(directory)/f"{index}.db")},
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("UTC readiness verified", result.stdout)


if __name__ == "__main__":
    unittest.main()
