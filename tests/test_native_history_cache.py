import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import energy


@unittest.skipUnless(shutil.which("swift"), "Swift runtime required")
class NativeHistoryCacheTests(unittest.TestCase):
    def test_utc_reporting_calendar_gaps_clipped_hours_and_invalid_policy(self):
        source = (Path(__file__).resolve().parents[1] / 'EnergyMonitorApp/Sources/MenuPopover.swift').read_text()
        models = source[source.index('struct CircuitBucket:'):source.index('struct StoredMenuResponse:')]
        reader = source[source.index('struct DownloadedHistoryReader'):source.index('final class MenuMonitor:')]
        harness = 'import Foundation\nimport SQLite3\n' + models + reader + '''
let reader = DownloadedHistoryReader(database: URL(fileURLWithPath:CommandLine.arguments[1]))
let iso = ISO8601DateFormatter()
NSTimeZone.default = TimeZone(identifier:"Asia/Tokyo")!
let now = iso.date(from:CommandLine.arguments[2])!
let result = reader.history(channel:"Pump",device:"A",source:String(repeating:"a",count:32),now:now)
if CommandLine.arguments[3] == "invalid" {
  precondition(result == nil, "invalid cache policy must fail closed")
} else {
  guard let (history,_) = result else { fatalError("UTC calendar unavailable") }
  let hourly = history.windows[0].series
  let recorded = hourly.filter{$0.totalKwh != nil}
  precondition(recorded.map{$0.totalKwh!} == [1,2])
  precondition(recorded.map{$0.period} == Array(CommandLine.arguments.dropFirst(3)))
  precondition(history.windows[1].series.reduce(0) { $0 + ($1.totalKwh ?? 0) } == 3)
  if CommandLine.arguments[3].contains("2026-03-08") {
    precondition(!hourly.contains{$0.period.contains("2026-03-08 02:")})
  }
}
print("Native reporting calendar verified")
'''
        cases = [
            ('America/New_York', '2026-03-08T09:00:00Z',
             ['2026-03-08T06:30:00.000000+00:00', '2026-03-08T07:30:00.000000+00:00'],
             ['2026-03-08 01:00 -05:00', '2026-03-08 03:00 -04:00']),
            ('Australia/Lord_Howe', '2026-10-04T16:00:00Z',
             ['2026-10-04T12:45:00.000000+00:00', '2026-10-04T13:15:00.000000+00:00'],
             ['2026-10-04 23:30 +11:00', '2026-10-05 00:00 +11:00']),
            ('America/New_York', '2026-11-02T08:00:00Z',
             ['2026-11-02T04:30:00.000000+00:00', '2026-11-02T05:30:00.000000+00:00'],
             ['2026-11-01 23:00 -05:00', '2026-11-02 00:00 -05:00']),
        ]
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / 'calendar.swift'
            script.write_text(harness)
            for index, (zone, now, stamps, labels) in enumerate(cases):
                with self.subTest(zone=zone, now=now):
                    database = Path(directory) / f'cache-{index}.db'
                    with patch.object(energy, 'DB_PATH', str(database)):
                        energy.ensure_table()
                        conn = energy._connect()
                        conn.execute('INSERT INTO sync_cache_format VALUES(1,?,?,?)',
                                     ('utc_v1', zone, 'interval_v1'))
                        conn.execute('INSERT INTO sync_cache_state VALUES(1,?,2,2,?)',
                                     ('a'*32, stamps[0]))
                        for reading_id, stamp in enumerate(stamps, 1):
                            conn.execute('INSERT INTO sync_cached_utc_readings(reading_id,timestamp,device_gid,channel_name,usage_kwh,cost_cents) VALUES(?,?,?,?,?,?)',
                                         (reading_id, stamp, 'A', 'Pump', reading_id, reading_id*22.58))
                        conn.commit()
                        conn.close()
                    result = subprocess.run(['swift', str(script), str(database), now, *labels],
                                            capture_output=True, text=True, timeout=60)
                    self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
            for field, value in [('reporting_timezone', 'Invalid/Zone'),
                                 ('measurement_model', 'instantaneous'),
                                 ('timestamp_format', 'future_v9')]:
                with self.subTest(field=field):
                    with patch.object(energy, 'DB_PATH', str(database)):
                        conn = energy._connect()
                        conn.execute('UPDATE sync_cache_format SET timestamp_format=?,reporting_timezone=?,measurement_model=?',
                                     ('utc_v1', zone, 'interval_v1'))
                        conn.execute(f'UPDATE sync_cache_format SET {field}=?', (value,))
                        conn.commit()
                        conn.close()
                    result = subprocess.run(['swift', str(script), str(database), now, 'invalid'],
                                            capture_output=True, text=True, timeout=60)
                    self.assertEqual(result.returncode, 0, result.stdout+result.stderr)

    def test_utc_reader_preserves_folds_zeroes_microseconds_and_host_isolation(self):
        source = (Path(__file__).resolve().parents[1] / 'EnergyMonitorApp/Sources/MenuPopover.swift').read_text()
        models = source[source.index('struct CircuitBucket:'):source.index('struct StoredMenuResponse:')]
        reader = source[source.index('struct DownloadedHistoryReader'):source.index('final class MenuMonitor:')]
        harness = 'import Foundation\nimport SQLite3\n' + models + reader + '''
let reader = DownloadedHistoryReader(database: URL(fileURLWithPath:CommandLine.arguments[1]))
let iso = ISO8601DateFormatter()
let now = iso.date(from:"2026-11-01T08:00:00Z")!.addingTimeInterval(0.25)
for zone in ["UTC","Asia/Tokyo","America/Los_Angeles"] {
  NSTimeZone.default = TimeZone(identifier:zone)!
  guard let (history,synced) = reader.history(channel:"Pump",device:"A",source:String(repeating:"a",count:32),now:now) else { fatalError("UTC history unavailable") }
  let window = history.windows[0]
  precondition(window.totalKwh == 3.25, "wrong elapsed/microsecond cutoff")
  precondition(window.readings == 4)
  precondition(window.series.contains{$0.totalKwh == nil})
  precondition(window.series.contains{$0.totalKwh == 0})
  let folds = window.series.filter{$0.period.contains("2026-11-01 01:00")}
  precondition(folds.count == 2 && Set(folds.map{$0.period}).count == 2)
  precondition(folds.map{$0.totalKwh!} == [1,2])
  let expected = iso.date(from:"2026-11-01T07:00:00Z")!.addingTimeInterval(0.123456)
  precondition(abs(synced.timeIntervalSince(expected)) < 0.000001, "receipt precision/offset lost")
  precondition(history.lastReading == "2026-11-01T08:00:00.249999+00:00")
}
print("UTC native history verified")
'''
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'cache.db'
            with patch.object(energy, 'DB_PATH', str(database)):
                energy.ensure_table()
                conn = energy._connect()
                conn.execute("INSERT INTO sync_cache_format VALUES(1,'utc_v1','America/New_York','interval_v1')")
                conn.execute('INSERT INTO sync_cache_state VALUES(1,?,8,8,?)',
                             ('a'*32, '2026-11-01T07:00:00.123456+00:00'))
                stamps = [('2026-11-01T05:30:00.000000+00:00', 1),
                          ('2026-11-01T06:30:00.000000+00:00', 2),
                          ('2026-11-01T07:30:00.000000+00:00', 0),
                          ('2026-11-01T08:00:00.249999+00:00', .25),
                          ('2026-11-01T08:00:00.250000+00:00', 100),
                          ('2026-11-02T08:00:00.000000+00:00', 100)]
                for index, (stamp, kwh) in enumerate(stamps, 1):
                    conn.execute('INSERT INTO sync_cached_utc_readings(reading_id,timestamp,device_gid,channel_name,usage_kwh,cost_cents) VALUES(?,?,?,?,?,?)',
                                 (index, stamp, 'A', 'Pump', kwh, kwh*22.58))
                conn.commit()
                conn.close()
            script = Path(directory) / 'utc.swift'
            script.write_text(harness)
            result = subprocess.run(['swift', str(script), str(database)], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertIn('UTC native history verified', result.stdout)

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
