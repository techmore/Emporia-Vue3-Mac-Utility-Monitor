import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import energy


@unittest.skipUnless(shutil.which("swift"), "Swift runtime required")
class NativeHistoryCacheTests(unittest.TestCase):
    def test_swift_reads_downloaded_history_with_source_and_device_isolation(self):
        source = (Path(__file__).resolve().parents[1]
                  / "EnergyMonitorApp/Sources/MenuPopover.swift").read_text()
        models = source[source.index("struct CircuitBucket:"):source.index("struct StoredMenuResponse:")]
        reader = source[source.index("struct DownloadedHistoryReader"):
                        source.index("final class MenuMonitor:")]
        harness = "import Foundation\nimport SQLite3\n" + models + reader + '''
let reader = DownloadedHistoryReader(database: URL(fileURLWithPath: CommandLine.arguments[1]))
let formatter = DateFormatter()
formatter.locale = Locale(identifier: "en_US_POSIX")
formatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
let now = formatter.date(from: "2026-10-07T13:00:00")!
let result = reader.history(channel: "Heat Pump", device: "A", source: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", now: now)
guard let result = result else { print("No history returned"); exit(1) }
precondition(result.0.windows.count == 3)
precondition(result.0.windows[0].totalKwh == 1.25)
precondition(result.0.windows[0].totalCents == 28.225)
precondition(result.0.windows[0].series.contains { $0.totalKwh == nil })
precondition(reader.history(channel: "Heat Pump", device: "A", source: "wrong", now: now) == nil)
precondition(reader.history(channel: "Unknown", device: "A", source: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", now: now) == nil)
var connection: OpaquePointer?
precondition(sqlite3_open(CommandLine.arguments[1], &connection) == SQLITE_OK)
precondition(sqlite3_exec(connection, "UPDATE sync_cache_state SET high_watermark=3", nil, nil, nil) == SQLITE_OK)
sqlite3_close(connection)
precondition(reader.history(channel: "Heat Pump", device: "A", source: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", now: now) == nil)
let missing = URL(fileURLWithPath: CommandLine.arguments[1] + ".missing")
precondition(DownloadedHistoryReader(database: missing).history(channel: "Heat Pump", device: "A", source: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", now: now) == nil)
precondition(!FileManager.default.fileExists(atPath: missing.path))
print("Downloaded history verified")
'''
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "cache.db"
            with patch.object(energy, "DB_PATH", str(database)):
                energy.ensure_table()
                conn = energy._connect()
                try:
                    conn.execute("INSERT INTO sync_cache_state VALUES(1,?,2,2,?)",
                                 ("a" * 32, "2026-10-07T12:30:00"))
                    conn.executemany(
                        "INSERT INTO sync_cached_readings(reading_id,timestamp,device_gid,channel_num,channel_name,usage_kwh,cost_cents) VALUES(?,?,?,?,?,?,?)",
                        [(1, "2026-10-07T12:00:00", "A", 1, "Heat Pump", 1.25, 28.225),
                         (2, "2026-10-07T12:00:00", "B", 1, "Heat Pump", 999, 999)],
                    )
                    conn.commit()
                finally:
                    conn.close()
            script = Path(directory) / "main.swift"
            script.write_text(harness)
            result = subprocess.run(["swift", str(script), str(database)],
                                    capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Downloaded history verified", result.stdout)
