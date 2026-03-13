import os
import tempfile
import unittest
from pathlib import Path

import energy
import web


class EnergyTests(unittest.TestCase):

    def _seed_ui_data(self):
        conn = energy._connect()
        now = energy.datetime.now().replace(microsecond=0)
        ts = now.isoformat()
        earlier = (now - energy.timedelta(minutes=30)).isoformat()
        conn.executemany(
            """INSERT INTO readings
               (timestamp, device_gid, channel_num, channel_name, usage_kwh, cost_cents)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [
                (ts, "A", 1, "Main", 1.2, 12.0),
                (ts, "A", 2, "Mains_A", 0.6, 6.0),
                (ts, "A", 3, "Mains_B", 0.6, 6.0),
                (ts, "A", 4, "Dryer", 0.3, 3.0),
                (ts, "A", 5, "HVAC", 0.2, 2.0),
                (earlier, "A", 1, "Main", 1.0, 10.0),
                (earlier, "A", 4, "Dryer", 0.25, 2.5),
            ],
        )
        conn.executemany(
            """INSERT INTO circuit_labels
               (slot, channel_name, label, note, amps, poles)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [
                (1, "Dryer", "Dryer", None, 30, 2),
                (2, "HVAC", "HVAC", None, 20, 2),
            ],
        )
        conn.commit()
        conn.close()

    def setUp(self):
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        Path(db_path).unlink(missing_ok=True)
        self.db_path = db_path
        self.original_db_path = energy.DB_PATH
        energy.DB_PATH = db_path
        energy.ensure_table()

    def tearDown(self):
        energy.DB_PATH = self.original_db_path
        Path(self.db_path).unlink(missing_ok=True)

    def test_get_latest_uses_newest_timestamp_not_last_inserted_id(self):
        conn = energy._connect()
        conn.executemany(
            """INSERT INTO readings
               (timestamp, device_gid, channel_num, channel_name, usage_kwh, cost_cents)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [
                ("2026-03-13T11:00:00.000001", "551741", 2, "Dryer", 0.7, 7.0),
                ("2026-03-13T09:30:00", "IMPORT", None, "Dryer", 9.9, 99.0),
            ],
        )
        conn.commit()
        conn.close()

        latest = energy.get_latest()
        dryer = next(row for row in latest if row["channel_name"] == "Dryer")
        self.assertEqual(dryer["timestamp"], "2026-03-13T11:00:00.000001")
        self.assertEqual(dryer["usage_kwh"], 0.7)

    def test_normalize_channel_name_trims_live_channel_whitespace(self):
        self.assertEqual(energy._normalize_channel_name("Well "), "Well")
        self.assertEqual(energy._normalize_channel_name(" Barn-Mains_A (kWatts) "), "Mains_A")

    def test_import_emporia_csv_skips_duplicates_via_unique_index(self):
        csv_text = (
            "Time Bucket (America/New_York),Barn-Dryer (kWhs)\n"
            "03/13/2026 10:00:00,1.5\n"
            "03/13/2026 11:00:00,1.6\n"
        )
        fd, csv_path = tempfile.mkstemp(suffix=".csv")
        os.close(fd)
        Path(csv_path).write_text(csv_text, encoding="utf-8")
        try:
            first = energy.import_emporia_csv(
                csv_path,
                device_gid="TEST",
                original_filename="TEST-Panel-1MIN.csv",
            )
            second = energy.import_emporia_csv(
                csv_path,
                device_gid="TEST",
                original_filename="TEST-Panel-1MIN.csv",
            )
        finally:
            Path(csv_path).unlink(missing_ok=True)

        self.assertEqual(first["imported"], 2)
        self.assertEqual(first["skipped"], 0)
        self.assertEqual(second["imported"], 0)
        self.assertEqual(second["skipped"], 2)

    def test_fix_csv_kwatts_import_runs_only_once(self):
        conn = energy._connect()
        conn.execute(
            """INSERT INTO readings
               (timestamp, device_gid, channel_num, channel_name, usage_kwh, cost_cents)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("2026-03-13T10:00:00", "IMPORT", None, "Dryer", 60.0, 6.0),
        )
        conn.commit()
        conn.close()

        first = energy.fix_csv_kwatts_import()
        second = energy.fix_csv_kwatts_import()

        conn = energy._connect()
        row = conn.execute(
            "SELECT usage_kwh, cost_cents FROM readings WHERE channel_name = 'Dryer'"
        ).fetchone()
        conn.close()

        self.assertEqual(first["fixed"], 1)
        self.assertEqual(second["fixed"], 0)
        self.assertEqual(tuple(row), (1.0, 0.1))

    def test_render_template_string_autoescapes_user_content(self):
        with web.app.app_context():
            rendered = web._render("{{ value }}", value="<script>alert(1)</script>")
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", rendered)
        self.assertNotIn("<script>alert(1)</script>", rendered)

    def test_queries_default_to_primary_device_gid(self):
        conn = energy._connect()
        now = energy.datetime.now()
        a_time = now.replace(microsecond=0).isoformat()
        b_time = (now + energy.timedelta(minutes=1)).replace(microsecond=0).isoformat()
        conn.executemany(
            """INSERT INTO readings
               (timestamp, device_gid, channel_num, channel_name, usage_kwh, cost_cents)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [
                (a_time, "A", 1, "Main", 1.0, 10.0),
                (a_time, "A", 2, "Dryer", 0.3, 3.0),
                (a_time, "B", 1, "Main", 9.0, 90.0),
                (a_time, "B", 2, "Dryer", 4.0, 40.0),
                (b_time, "B", 1, "Main", 10.0, 100.0),
                (b_time, "B", 2, "Dryer", 5.0, 50.0),
            ],
        )
        conn.commit()
        conn.close()

        settings = Path('settings.json')
        original = settings.read_text() if settings.exists() else None
        try:
            settings.write_text('{\n  "primary_device_gid": "A"\n}', encoding='utf-8')
            summary = energy.get_summary(48)
            hourly = energy.get_hourly_data(7)
            latest = energy.get_latest()
            context = energy.get_now_vs_context(60)
        finally:
            if original is None:
                settings.unlink(missing_ok=True)
            else:
                settings.write_text(original, encoding='utf-8')

        self.assertEqual(summary[0]["total_kwh"], 0.3)
        self.assertEqual(hourly[0]["total_kwh"], 1.0)
        self.assertEqual(next(r for r in latest if r["channel_name"] == "Main")["usage_kwh"], 1.0)
        self.assertEqual(context["current_kwh"], 1.0)

    def test_get_channel_totals_includes_meta_channels(self):
        conn = energy._connect()
        conn.executemany(
            """INSERT INTO readings
               (timestamp, device_gid, channel_num, channel_name, usage_kwh, cost_cents)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [
                ("2026-03-13T11:00:00", "A", 1, "Mains_A", 0.5, 5.0),
                ("2026-03-13T11:00:00", "A", 2, "Mains_B", 0.6, 6.0),
                ("2026-03-13T11:00:00", "A", 3, "Dryer", 0.2, 2.0),
            ],
        )
        conn.commit()
        conn.close()

        totals = {r["channel_name"]: r for r in energy.get_channel_totals(["Mains_A", "Mains_B"], 24)}
        self.assertEqual(totals["Mains_A"]["total_kwh"], 0.5)
        self.assertEqual(totals["Mains_B"]["total_kwh"], 0.6)

    def test_dashboard_route_renders_panel_invariants(self):
        self._seed_ui_data()
        client = web.app.test_client()

        response = client.get("/")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Service Feed", body)
        self.assertIn("Leg A", body)
        self.assertIn("Leg B", body)
        self.assertIn("Bus bar", body)
        self.assertIn("Circuit Breakers", body)
        self.assertIn("Top Active Circuits", body)
        self.assertIn("Recommendations", body)
        self.assertGreaterEqual(body.count('class="breaker '), 2)
        self.assertLess(body.index("Service Feed"), body.index("Bus bar"))
        self.assertLess(body.index("Bus bar"), body.index('data-panel-section="breakers"'))
        self.assertLess(body.index('data-panel-section="digital-panel"'), body.index('data-panel-section="sidebar-metrics"'))

    def test_circuits_route_renders_action_center_and_panel(self):
        self._seed_ui_data()
        client = web.app.test_client()

        response = client.get("/circuits")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Action Center", body)
        self.assertIn("Service Feed", body)
        self.assertIn("Safety Watch", body)
        self.assertIn("Live Heavy Hitters", body)
        self.assertIn("Always-On Loads", body)
        self.assertIn("Bus bar", body)
        self.assertLess(body.index("Service Feed"), body.index("Bus bar"))
        self.assertLess(body.index("Bus bar"), body.index('data-panel-section="breakers"'))

    def test_secondary_pages_render_expected_sections(self):
        self._seed_ui_data()
        client = web.app.test_client()

        expectations = {
            "/reports": ["24h Cost", "Peak Today", "Biggest 24h Load"],
            "/guide": ["First-Time Setup", "Metric Meanings", "Panel view"],
            "/recommendations": ["Next Best Actions", "Shortcuts", "When To Call An Electrician"],
        }
        for path, snippets in expectations.items():
            response = client.get(path)
            body = response.get_data(as_text=True)
            self.assertEqual(response.status_code, 200, path)
            for snippet in snippets:
                self.assertIn(snippet, body, f"{snippet} missing from {path}")

    def test_live_event_stream_and_dashboard_payload(self):
        self._seed_ui_data()
        client = web.app.test_client()

        dashboard = client.get("/api/live-dashboard")
        self.assertEqual(dashboard.status_code, 200)
        payload = dashboard.get_json()
        self.assertIn("current_watts", payload)
        self.assertIn("top_circuits", payload)

        stream = client.get("/api/events", buffered=False)
        first_chunk = next(stream.response).decode("utf-8")
        self.assertIn("event: update", first_chunk)
        self.assertIn("\"dashboard\":", first_chunk)
        self.assertIn("\"status\":", first_chunk)

    def test_panel_slots_setting_supports_sixteen_slot_panels(self):
        settings = Path("settings.json")
        original = settings.read_text() if settings.exists() else None
        try:
            settings.write_text('{\n  "panel_slots": 16\n}', encoding="utf-8")
            normalized, panel_slots = web._normalize_panel_layout({}, ["Dryer", "HVAC"], minimum_slots=web._load_panel_slots())
            self.assertEqual(panel_slots, 16)
            self.assertEqual(sorted(normalized.keys()), [1, 2])
        finally:
            if original is None:
                settings.unlink(missing_ok=True)
            else:
                settings.write_text(original, encoding="utf-8")

    def test_partial_panel_layout_still_renders_unmapped_circuits(self):
        conn = energy._connect()
        now = energy.datetime.now().replace(microsecond=0).isoformat()
        conn.executemany(
            """INSERT INTO readings
               (timestamp, device_gid, channel_num, channel_name, usage_kwh, cost_cents)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [
                (now, "A", 1, "Main", 1.0, 10.0),
                (now, "A", 2, "Dryer", 0.3, 3.0),
                (now, "A", 3, "HVAC", 0.2, 2.0),
                (now, "A", 4, "Office", 0.1, 1.0),
            ],
        )
        conn.execute(
            """INSERT INTO circuit_labels
               (slot, channel_name, label, note, amps, poles)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (1, "Dryer", "Dryer", None, 30, 2),
        )
        conn.commit()
        conn.close()

        with web.app.app_context():
            dashboard = web._build_dashboard_context("Service Panel")
        dashboard_names = {row["channel_name"] for row in dashboard["dash_breakers"] if row["channel_name"]}
        self.assertTrue({"Dryer", "HVAC", "Office"}.issubset(dashboard_names))

        client = web.app.test_client()
        circuits_body = client.get("/circuits").get_data(as_text=True)
        self.assertIn("HVAC", circuits_body)
        self.assertIn("Office", circuits_body)


if __name__ == "__main__":
    unittest.main()
