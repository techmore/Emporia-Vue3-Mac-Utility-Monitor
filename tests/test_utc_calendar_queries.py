import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import energy
import web
from energy_clock import EnergyClock
from utc_migration import rehearse_utc_copy


class HeatmapMarkup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.day_columns, self.cells, self.titles = [], 0, []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "th" and attributes.get("scope") == "colgroup":
            self.day_columns.append(int(attributes["colspan"]))
        if tag == "td" and "heat-cell" in attributes.get("class", "").split():
            self.cells += 1
            self.titles.append(attributes["title"])


class EnergyClockTests(unittest.TestCase):
    def test_period_boundaries_follow_reporting_zone_not_utc_date(self):
        clock = EnergyClock("America/New_York")
        now = datetime(2026, 10, 1, 2, tzinfo=timezone.utc)
        self.assertEqual(clock.stamp(clock.period_start(now, "day")), "2026-09-30T04:00:00.000000+00:00")
        self.assertEqual(clock.stamp(clock.period_start(now, "month")), "2026-09-01T04:00:00.000000+00:00")
        self.assertEqual(clock.stamp(clock.period_start(now, "week")), "2026-09-28T04:00:00.000000+00:00")

    def test_hour_grid_preserves_actual_dst_day_lengths(self):
        clock = EnergyClock("America/New_York")
        for end, columns in ((datetime(2026, 3, 9, tzinfo=clock.zone), 23),
                             (datetime(2026, 11, 2, tzinfo=clock.zone), 25)):
            with self.subTest(end=end):
                grid = clock.hour_grid(end)
                self.assertEqual(grid["day_columns"][-1]["columns"], columns)
                self.assertEqual(len(grid["bins"]), 6*24+columns)
                self.assertEqual(len({row["key"] for row in grid["bins"]}), len(grid["bins"]))
                self.assertEqual(sum(row["minutes"] for row in grid["bins"]), (6*24+columns)*60)

    def test_half_hour_transition_retains_partial_interval_duration(self):
        clock = EnergyClock("Australia/Lord_Howe")
        grid = clock.hour_grid(datetime(2026, 10, 5, tzinfo=clock.zone), 1)
        self.assertEqual(sum(row["minutes"] for row in grid["bins"]), 23.5*60)
        self.assertEqual(grid["bins"][-1]["minutes"], 30)
        self.assertEqual(grid["bins"][-1]["hour"], "2026-10-04T23:30+11:00")
        self.assertEqual(clock.hour_key("2026-10-04T12:40:00.000000+00:00"), grid["bins"][-1]["key"])

    def test_quarter_hour_zone_aligns_to_reporting_midnight(self):
        clock = EnergyClock("Asia/Kathmandu")
        grid = clock.hour_grid(datetime(2026, 10, 9, tzinfo=clock.zone), 1)
        self.assertEqual(grid["bins"][0]["key"], "2026-10-07T18:15:00.000000+00:00")
        self.assertEqual(grid["bins"][0]["hour"], "2026-10-08T00:00+05:45")
        self.assertEqual(clock.hour_key("2026-10-07T18:40:00.000000+00:00"), grid["bins"][0]["key"])

    def test_storage_and_query_clocks_reject_implicit_or_noncanonical_times(self):
        for value in ("2026-10-08T12:00:00", "2026-10-08T12:00:00Z",
                      "2026-10-08T12:00:00+00:00", "2026-10-08T12:00:00.000000-04:00"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                EnergyClock.parse(value)
        with self.assertRaises(ValueError):
            EnergyClock("UTC").complete_days(datetime(2026, 10, 8), 7)
        with self.assertRaises(ValueError):
            EnergyClock("Pacific/Apia").day_bounds(date(2011, 12, 30))


class UtcCalendarQueryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        database = patch.object(energy, "DB_PATH", str(self.root/"legacy.db"))
        database.start()
        self.addCleanup(database.stop)
        self.addCleanup(self.directory.cleanup)
        energy.ensure_table()
        self.original_connect = energy._connect
        self.artifact = self.root/"converted.db"

    def seed(self, rows):
        connection = self.original_connect()
        try:
            with connection:
                connection.executemany(
                    "INSERT INTO readings(timestamp,device_gid,channel_name,usage_kwh,cost_cents) VALUES (?,?,?,?,?)",
                    rows,
                )
        finally:
            connection.close()

    def convert(self, zone="America/New_York"):
        archive = self.root/"archive.db"
        energy.backup_database(archive)
        self.source_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
        self.receipt = rehearse_utc_copy(archive, self.artifact, expected_sha256=self.source_hash,
                                        legacy_timezone=zone, reporting_timezone=zone)

    def read_only_queries(self):
        def connection():
            return self.original_connect(self.artifact, allow_utc_rehearsal=True, read_only=True)
        return patch.object(energy, "_connect", side_effect=connection)

    def test_exact_now_and_calendar_boundaries_exclude_future_and_other_devices(self):
        self.seed([("2026-10-01T03:59:59.999999Z", "A", "Dryer", 1, 20),
                   ("2026-10-01T04:00:00Z", "A", "Dryer", 2, 40),
                   ("2026-10-01T04:00:00.000001Z", "A", "Dryer", 999, 999),
                   ("2026-10-01T04:00:00Z", "B", "Other", 999, 999),
                   ("2026-10-01T04:00:00Z", "A", "Main", 999, 999)])
        self.convert()
        now = datetime(2026, 10, 1, 4, tzinfo=timezone.utc)
        with self.read_only_queries():
            today = energy.get_today_circuit_totals("A", now=now)
            month = energy.get_today_circuit_totals("A", "month", now=now)
            week = energy.get_today_circuit_totals("A", "week", now=now)
        self.assertEqual(today, [{"channel_name": "Dryer", "total_kwh": 2, "total_cents": 40}])
        self.assertEqual(month, today)
        self.assertEqual(week[0]["total_kwh"], 3)
        self.assertEqual(hashlib.sha256(self.artifact.read_bytes()).hexdigest(), self.receipt["artifact_sha256"])

    def test_monthly_costs_preserve_stored_cents_and_reporting_calendar_days(self):
        self.seed([("2026-10-01T03:59:59Z", "A", "Main", 1, 22.58),
                   ("2026-10-01T04:00:00Z", "A", "Main", 2, 45.16),
                   ("2026-10-02T00:01:00Z", "A", "Main", 3, 70),
                   ("2026-10-02T04:00:00Z", "A", "Main", 4, 80),
                   ("2026-10-02T12:00:00.000001Z", "A", "Main", 999, 999),
                   ("2026-10-01T04:00:00Z", "A", "Mains_A", 999, 999),
                   ("2026-10-01T04:00:00Z", "B", "Main", 999, 999),
                   ("2026-10-01T03:59:59Z", "A", "Dryer", 1.5, 30)])
        self.convert()
        with self.read_only_queries():
            months = energy.get_monthly_costs(2, "A", now=datetime(2026, 10, 2, 12, tzinfo=timezone.utc))
        self.assertEqual([row["month"] for row in months], ["2026-10", "2026-09"])
        self.assertEqual(months[0]["total_kwh"], 9)
        self.assertAlmostEqual(months[0]["total_cents"], 195.16)
        self.assertEqual(months[0]["days_recorded"], 2)
        self.assertEqual(months[1]["total_kwh"], 1)
        self.assertEqual(months[1]["circuits"], [{"channel_name": "Dryer", "kwh": 1.5, "cents": 30}])

    def test_monthly_report_waits_for_reporting_month_to_close(self):
        self.seed([("2026-08-15T12:00:00-04:00", "A", "Main", 1, 22.58),
                   ("2026-09-30T23:00:00-04:00", "A", "Main", 2, 45.16)])
        self.convert()
        output = self.root/"reports"
        with self.read_only_queries():
            first = energy.write_monthly_reports(str(output), now=datetime(2026, 10, 1, 3, 30, tzinfo=timezone.utc))
            self.assertEqual([Path(path).name for path in first], ["energy-2026-08.md"])
            self.assertFalse((output/"energy-2026-09.md").exists())
            second = energy.write_monthly_reports(str(output), now=datetime(2026, 10, 1, 4, tzinfo=timezone.utc))
        self.assertEqual([Path(path).name for path in second], ["energy-2026-09.md"])
        self.assertIn("2.0 kWh, $0.45", Path(second[0]).read_text())
        self.assertEqual(hashlib.sha256(self.artifact.read_bytes()).hexdigest(), self.receipt["artifact_sha256"])

    def test_heatmap_retains_both_folds_and_renders_25_columns(self):
        self.seed([("2026-11-01T01:30:00-04:00", "A", "Dryer", 0.1, 1),
                   ("2026-11-01T01:30:00-05:00", "A", "Dryer", 0.2, 2),
                   ("2026-11-01T01:30:00-05:00", "B", "Other", 999, 999)])
        self.convert()
        with self.read_only_queries():
            result = energy.get_power_heatmap("A", end=datetime(2026, 11, 2, tzinfo=ZoneInfo("America/New_York")))
        self.assertEqual(len(result["hours"]), 169)
        first = result["hours"].index("2026-11-01T01:00-04:00")
        second = result["hours"].index("2026-11-01T01:00-05:00")
        self.assertNotEqual(first, second)
        cells = result["circuits"][0]["cells"]
        self.assertEqual((cells[first]["kwh"], cells[second]["kwh"]), (0.1, 0.2))
        self.assertIsNone(cells[0])
        self.assertEqual(result["hour_labels"][first], "01:00 -0400")
        markup = self.render_heatmap(result)
        self.assertEqual(markup.day_columns, [24]*6+[25])
        self.assertEqual(markup.cells, 169)
        self.assertTrue(any("01:00-04:00" in title for title in markup.titles))
        self.assertTrue(any("01:00-05:00" in title for title in markup.titles))

    def render_heatmap(self, result):
        start = web.TRENDS_HTML.index('  <section class="card"')
        end = web.TRENDS_HTML.index("</section>", start)+len("</section>")
        text = web.app.jinja_env.from_string(web.TRENDS_HTML[start:end]).render(heatmap=result, rate=0.2258)
        markup = HeatmapMarkup()
        markup.feed(text)
        return markup

    def test_heatmap_never_fabricates_spring_gap_or_conflates_missing_and_zero(self):
        self.seed([("2026-03-08T01:30:00-05:00", "A", "Dryer", 0.1, 1),
                   ("2026-03-08T03:00:00-04:00", "A", "Dryer", 0, 0)])
        self.convert()
        with self.read_only_queries():
            result = energy.get_power_heatmap("A", end=datetime(2026, 3, 9, tzinfo=ZoneInfo("America/New_York")))
        self.assertEqual(len(result["hours"]), 167)
        self.assertFalse(any("2026-03-08T02:" in hour for hour in result["hours"]))
        zero = result["hours"].index("2026-03-08T03:00-04:00")
        self.assertEqual(result["circuits"][0]["cells"][zero]["kwh"], 0)
        self.assertIsNone(result["circuits"][0]["cells"][zero+1])
        self.assertEqual(self.render_heatmap(result).day_columns, [24]*6+[23])

    def test_complete_dst_weeks_have_actual_minutes_and_full_coverage(self):
        clock = EnergyClock("America/New_York")
        for day, current_minutes in ((date(2026, 3, 9), 10020), (date(2026, 11, 2), 10140)):
            with self.subTest(day=day), tempfile.TemporaryDirectory() as directory:
                database = Path(directory)/"week.db"
                with patch.object(energy, "DB_PATH", str(database)):
                    energy.ensure_table()
                    end = clock.day_bounds(day)[0]
                    start, boundary = clock.complete_days(end, 14)
                    middle = clock.complete_days(end, 7)[0]
                    rows = []
                    cursor = start
                    while cursor < boundary:
                        rows.append((cursor.isoformat(), "A", "Dryer", 0.001 if cursor < middle else 0.002, 1))
                        cursor += timedelta(minutes=1)
                    self.seed(rows)
                    archive = Path(directory)/"archive.db"
                    energy.backup_database(archive)
                    converted = Path(directory)/"converted.db"
                    rehearse_utc_copy(archive, converted, expected_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
                                     legacy_timezone=clock.reporting_timezone, reporting_timezone=clock.reporting_timezone)
                with patch.object(energy, "_connect", side_effect=lambda converted=converted: self.original_connect(
                    converted, allow_utc_rehearsal=True, read_only=True,
                )):
                    comparison = energy.get_circuit_week_comparison("A", end=end)[0]
                self.assertEqual(comparison["current_expected_minutes"], current_minutes)
                self.assertEqual(comparison["previous_expected_minutes"], 10080)
                self.assertEqual(comparison["current_coverage_pct"], 100)
                self.assertEqual(comparison["previous_coverage_pct"], 100)
                self.assertTrue(comparison["comparable"])
                self.assertAlmostEqual(comparison["current_kwh"], current_minutes*0.002)

    def test_fold_observations_do_not_manufacture_two_weekly_repetitions(self):
        rows = []
        for offset in ("-04:00", "-05:00"):
            for minute in range(60):
                rows.append((f"2026-11-01T01:{minute:02}:00{offset}", "A", "Main", 999, 999))
        self.seed(rows)
        self.convert()
        with self.read_only_queries():
            result = energy.get_weekly_power_pattern("A", end=datetime(2026, 11, 2, tzinfo=ZoneInfo("America/New_York")))
        self.assertIsNone(result["main"]["cells"][6*24+1])
        self.assertEqual(result["main"]["supported"], 0)

    def test_forecast_uses_distinct_normal_days_not_fold_or_import_samples(self):
        rows = []
        for day, value in ((18, 2/60), (25, 4/60)):
            for minute in range(60):
                rows.append((f"2026-10-{day:02}T01:{minute:02}:00-04:00", "A", "Main", value, 1))
        for offset in ("-04:00", "-05:00"):
            for minute in range(60):
                rows.append((f"2026-11-01T01:{minute:02}:00{offset}", "A", "Main", 999, 999))
        rows.append(("2026-10-11T01:00:00-04:00", "A", "Main", 999, 999))
        self.seed(rows)
        self.convert()
        with self.read_only_queries():
            result = energy.get_weekly_power_pattern("A", end=datetime(2026, 11, 2, tzinfo=ZoneInfo("America/New_York")))
        cell = result["main"]["cells"][6*24+1]
        self.assertEqual(cell["weeks"], 2)
        self.assertAlmostEqual(cell["kwh"], 3)
        self.assertAlmostEqual(cell["low"], 2)
        self.assertAlmostEqual(cell["high"], 4)
        self.assertIsNone(result["main"]["weekly_kwh"])

    def test_persisted_utc_policy_cannot_enable_live_writes_without_marker(self):
        connection = self.original_connect()
        try:
            with connection:
                connection.execute("INSERT INTO energy_time_policy VALUES (1,'utc_v1','America/New_York','America/New_York')")
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.execute("PRAGMA journal_mode=DELETE")
        finally:
            connection.close()
        before = hashlib.sha256(Path(energy.DB_PATH).read_bytes()).hexdigest()
        for options in ({}, {"read_only": True}, {"allow_utc_rehearsal": True}):
            with self.subTest(options=options), self.assertRaisesRegex(RuntimeError, "not ready"):
                self.original_connect(**options)
        self.assertEqual(hashlib.sha256(Path(energy.DB_PATH).read_bytes()).hexdigest(), before)
        self.assertFalse(Path(energy.DB_PATH+"-wal").exists())

    def test_unknown_format_and_missing_or_mismatched_policy_fail_closed(self):
        connection = self.original_connect()
        try:
            with connection:
                connection.execute("INSERT INTO energy_time_policy VALUES (1,'unknown','UTC','UTC')")
            with self.assertRaisesRegex(RuntimeError, "Unsupported"):
                self.original_connect(allow_utc_rehearsal=True, read_only=True)
            with connection:
                connection.execute("DELETE FROM energy_time_policy")
                connection.execute("INSERT INTO utc_rehearsal VALUES (1,?,'UTC','UTC',?)", ("a"*64, "2026-10-08T00:00:00Z"))
            with self.assertRaisesRegex(RuntimeError, "lacks reporting policy"):
                energy._utc_clock(connection)
            with connection:
                connection.execute("INSERT INTO energy_time_policy VALUES (1,'utc_v1','Asia/Tokyo','UTC')")
            with self.assertRaisesRegex(RuntimeError, "Inconsistent"):
                energy._utc_clock(connection)
        finally:
            connection.close()

    def test_duplicate_same_day_patterns_do_not_count_as_distinct_weeks(self):
        rows = [{"channel_name": "Main", "hour": "2026-10-05T01:00:00", "minutes": 60, "kwh": value}
                for value in (1, 2)]
        self.assertIsNone(energy._weekly_pattern(rows)["main"]["cells"][1])

    def test_persisted_policy_controls_queries_across_three_host_timezones(self):
        self.seed([("2026-10-01T01:00:00Z", "A", "Dryer", 1, 20),
                   ("2026-10-01T04:00:00Z", "A", "Dryer", 2, 40)])
        self.convert()
        script = """
import json, sys
from datetime import datetime, timezone
import energy
original = energy._connect
energy._connect = lambda: original(sys.argv[1], allow_utc_rehearsal=True, read_only=True)
print(json.dumps(energy.get_today_circuit_totals('A', now=datetime(2026,10,1,4,tzinfo=timezone.utc))))
"""
        for index, zone in enumerate(("UTC", "America/New_York", "Asia/Tokyo")):
            with self.subTest(zone=zone):
                result = subprocess.run([sys.executable, "-c", script, str(self.artifact)],
                                        cwd=Path(energy.__file__).parent, capture_output=True, text=True, timeout=30,
                                        env={**os.environ, "TZ": zone, "DB_PATH": str(self.root/f"bootstrap-{index}.db")})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), [{"channel_name": "Dryer", "total_kwh": 2, "total_cents": 40}])


if __name__ == "__main__":
    unittest.main()
