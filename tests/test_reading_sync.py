import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
