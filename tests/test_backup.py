import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import energy


class BackupTests(unittest.TestCase):
    def test_snapshot_includes_committed_wal_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(energy, "DB_PATH", str(root / "source.db")):
                energy.ensure_table()
                writer = energy._connect()
                try:
                    writer.execute(
                        "INSERT INTO readings(timestamp,device_gid,channel_name,usage_kwh) "
                        "VALUES ('2026-10-07T12:00:00','A','Main',1.25)"
                    )
                    writer.commit()
                    result = energy.backup_database(root / "snapshot.db")
                    self.assertEqual(result["readings"], 1)
                    self.assertEqual(result["integrity"], "ok")
                    self.assertFalse((root / "snapshot.db-wal").exists())
                    self.assertFalse((root / "snapshot.db-shm").exists())
                    snapshot = energy._connect(root / "snapshot.db")
                    try:
                        self.assertEqual(snapshot.execute(
                            "SELECT usage_kwh FROM readings"
                        ).fetchone()[0], 1.25)
                    finally:
                        snapshot.close()
                    self.assertEqual((root / "snapshot.db").stat().st_mode & 0o777, 0o600)
                    with self.assertRaises(FileExistsError):
                        energy.backup_database(root / "snapshot.db")
                    self.assertEqual(writer.execute(
                        "SELECT COUNT(*) FROM readings"
                    ).fetchone()[0], 1)
                    self.assertFalse(list(root.glob(".energy-backup-*")))
                finally:
                    writer.close()

    def test_failed_publication_cleans_up_without_publishing_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(energy, "DB_PATH", str(root / "source.db")):
                energy.ensure_table()
                with patch.object(energy.os, "link", side_effect=OSError("disk error")):
                    with self.assertRaises(OSError):
                        energy.backup_database(root / "snapshot.db")
                self.assertFalse((root / "snapshot.db").exists())
                self.assertFalse(list(root.glob(".energy-backup-*")))
