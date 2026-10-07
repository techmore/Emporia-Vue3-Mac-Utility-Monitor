import json
import os
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import MagicMock, patch

import energy
import web


class ReadingJournalTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = patch.object(
            energy, "DB_PATH", str(Path(self.directory.name) / "energy.db"),
        )
        self.database.start()
        energy.ensure_table()

    def tearDown(self):
        self.database.stop()
        self.directory.cleanup()

    def insert(self, conn):
        return conn.execute(
            "INSERT INTO readings(timestamp,device_gid,channel_name,usage_kwh,cost_cents) "
            "VALUES ('2026-10-07T12:00:00','A','Main',1,22.58)"
        ).lastrowid

    def test_insert_update_delete_are_ordered_and_paginated(self):
        conn = energy._connect()
        try:
            reading_id = self.insert(conn)
            conn.execute("UPDATE readings SET cost_cents=23 WHERE id=?", (reading_id,))
            conn.execute("DELETE FROM readings WHERE id=?", (reading_id,))
            conn.commit()
        finally:
            conn.close()
        first = energy.get_reading_changes(limit=2)
        self.assertEqual([r["operation"] for r in first["changes"]], ["upsert", "upsert"])
        self.assertEqual(first["changes"][1]["cost_cents"], 23)
        self.assertTrue(first["has_more"])
        second = energy.get_reading_changes(first["next_cursor"], limit=2)
        self.assertEqual(second["changes"][0]["operation"], "delete")
        self.assertEqual(second["changes"][0]["reading_id"], reading_id)
        self.assertFalse(second["has_more"])
        self.assertEqual(first["source_id"], second["source_id"])

    def test_rollback_does_not_publish_change(self):
        conn = energy._connect()
        try:
            self.insert(conn)
            conn.rollback()
        finally:
            conn.close()
        self.assertEqual(energy.get_reading_changes()["changes"], [])

    def test_existing_history_is_seeded_once_and_identity_persists(self):
        conn = energy._connect()
        try:
            conn.execute("DROP TRIGGER readings_sync_insert")
            self.insert(conn)
            conn.execute("DELETE FROM migrations WHERE name='reading_sync_seed_v1'")
            conn.commit()
        finally:
            conn.close()
        energy.ensure_table()
        first = energy.get_reading_changes()
        self.assertEqual(len(first["changes"]), 1)
        energy.ensure_table()
        self.assertEqual(energy.get_reading_changes(), first)

    def test_invalid_and_ahead_cursors_are_rejected(self):
        for after, limit in [(-1, 500), (True, 500), (0, 0), (0, 1001), (1, 500)]:
            with self.assertRaises(ValueError):
                energy.get_reading_changes(after, limit)

    def test_export_requires_configured_token_and_rejects_identity_change(self):
        client = web.app.test_client()
        with patch.dict(web.os.environ, {"ENERGY_SYNC_TOKEN": ""}):
            self.assertEqual(client.get("/api/sync/readings").status_code, 503)
        with patch.dict(web.os.environ, {"ENERGY_SYNC_TOKEN": "a" * 32}):
            self.assertEqual(client.get("/api/sync/readings").status_code, 401)
            headers = {"Authorization": "Bearer " + "a" * 32}
            response = client.get("/api/sync/readings", headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertEqual(client.get(
                "/api/sync/readings?source_id=wrong", headers=headers,
            ).status_code, 409)
            self.assertEqual(client.get(
                "/api/sync/readings?limit=1001", headers=headers,
            ).status_code, 400)

    def test_cache_resumes_and_applies_updates_and_deletes(self):
        conn = energy._connect()
        try:
            reading_id = self.insert(conn)
            conn.execute("UPDATE readings SET cost_cents=24 WHERE id=?", (reading_id,))
            conn.execute("DELETE FROM readings WHERE id=?", (reading_id,))
            conn.commit()
        finally:
            conn.close()
        first = energy.get_reading_changes(limit=1)
        state = energy.apply_reading_changes(first)
        self.assertIsNone(state["synchronized_at"])
        second = energy.get_reading_changes(state["cursor"], limit=1)
        state = energy.apply_reading_changes(second)
        conn = energy._connect()
        try:
            self.assertEqual(conn.execute(
                "SELECT cost_cents FROM sync_cached_readings"
            ).fetchone()[0], 24)
        finally:
            conn.close()
        energy.apply_reading_changes(energy.get_reading_changes(state["cursor"]))
        self.assertIsNotNone(energy.get_sync_cache_status()["synchronized_at"])
        conn = energy._connect()
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM sync_cached_readings").fetchone()[0], 0)
        finally:
            conn.close()

    def test_cache_rejects_identity_mismatch_and_stale_cursor(self):
        page = energy.get_reading_changes()
        energy.apply_reading_changes(page)
        wrong = deepcopy(page)
        wrong["source_id"] = "b" * 32
        with self.assertRaises(ValueError):
            energy.apply_reading_changes(wrong)
        conn = energy._connect()
        try:
            self.insert(conn)
            conn.commit()
        finally:
            conn.close()
        page = energy.get_reading_changes()
        energy.apply_reading_changes(page)
        with self.assertRaises(ValueError):
            energy.apply_reading_changes(page)

    def test_cursor_write_failure_rolls_back_all_cached_rows(self):
        conn = energy._connect()
        try:
            self.insert(conn)
            conn.commit()
        finally:
            conn.close()
        page = energy.get_reading_changes()
        conn = energy._connect()
        proxy = MagicMock(wraps=conn)

        def execute(sql, *args):
            if sql.startswith("INSERT INTO sync_cache_state"):
                raise RuntimeError("simulated disk failure")
            return conn.execute(sql, *args)

        proxy.execute.side_effect = execute
        with patch.object(energy, "_connect", return_value=proxy):
            with self.assertRaises(RuntimeError):
                energy.apply_reading_changes(page)
        self.assertEqual(energy.get_sync_cache_status()["cursor"], 0)
        conn = energy._connect()
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM sync_cached_readings").fetchone()[0], 0)
        finally:
            conn.close()

    def test_invalid_payload_cannot_advance_cache_cursor(self):
        conn = energy._connect()
        try:
            self.insert(conn)
            conn.commit()
        finally:
            conn.close()
        page = energy.get_reading_changes()
        page["changes"][0]["usage_kwh"] = float("nan")
        with self.assertRaises(ValueError):
            energy.apply_reading_changes(page)
        self.assertEqual(energy.get_sync_cache_status()["cursor"], 0)

    def test_real_http_download_and_resume_in_separate_cache_process(self):
        conn = energy._connect()
        try:
            reading_id = self.insert(conn)
            conn.commit()
        finally:
            conn.close()
        root = Path(__file__).resolve().parents[1]
        env = dict(os.environ, DB_PATH=energy.DB_PATH, ENERGY_SYNC_TOKEN="a" * 32)
        code = (
            "import web; from werkzeug.serving import make_server; "
            "s=make_server('127.0.0.1',0,web.app); "
            "print(s.server_port,flush=True); s.serve_forever()"
        )
        server = subprocess.Popen(
            [sys.executable, "-c", code], cwd=root, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        )
        try:
            port = int(server.stdout.readline())
            cache = Path(self.directory.name) / "cache.db"
            command = [sys.executable, str(root / "sync_history.py"),
                       "--collector", f"http://127.0.0.1:{port}", "--cache", str(cache)]
            first = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(first.returncode, 0, first.stderr)
            state = json.loads(first.stdout)
            self.assertIsNotNone(state["synchronized_at"])
            conn = energy._connect()
            try:
                conn.execute("UPDATE readings SET cost_cents=30 WHERE id=?", (reading_id,))
                conn.commit()
            finally:
                conn.close()
            second = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertGreater(json.loads(second.stdout)["cursor"], state["cursor"])
            conn = energy._connect(cache)
            try:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0], 0)
                rows = conn.execute("SELECT * FROM sync_cached_readings").fetchall()
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["cost_cents"], 30)
            finally:
                conn.close()
            self.assertEqual(cache.stat().st_mode & 0o777, 0o600)
        finally:
            server.terminate()
            server.wait(timeout=10)
            server.stdout.close()
