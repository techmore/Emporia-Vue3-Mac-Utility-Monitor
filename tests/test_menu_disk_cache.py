import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which("swift"), "Swift runtime required")
class MenuDiskCacheTests(unittest.TestCase):
    def test_real_swift_cache_persistence_isolation_and_private_permissions(self):
        source = (Path(__file__).resolve().parents[1]
                  / "EnergyMonitorApp/Sources/MenuPopover.swift").read_text()
        helpers = source[source.index("struct StoredMenuResponse:"):
                         source.index("final class MenuMonitor:")]
        harness = "import Foundation\nimport CryptoKit\n" + helpers + '''
let directory = URL(fileURLWithPath: CommandLine.arguments[1]).appendingPathComponent("cache")
let cache = MenuDiskCache(directory: directory)
let first = URL(string: "https://collector-a.example/api/menu-summary")!
let other = URL(string: "https://collector-b.example/api/menu-summary")!
let data = Data("{\\"version\\":\\"2.3.3\\"}".utf8)
try cache.write(data, for: first)
let reloaded = MenuDiskCache(directory: directory)
precondition(reloaded.read(first)?.data == data)
precondition(reloaded.read(other) == nil)
let attributes = try FileManager.default.attributesOfItem(atPath: cache.file(for: first).path)
precondition((attributes[.posixPermissions] as? NSNumber)?.intValue == 0o600)
let directoryAttributes = try FileManager.default.attributesOfItem(atPath: directory.path)
precondition((directoryAttributes[.posixPermissions] as? NSNumber)?.intValue == 0o700)
try Data("broken".utf8).write(to: cache.file(for: first))
precondition(cache.read(first) == nil)
try cache.write(Data(count: 4 * 1024 * 1024 + 1), for: other)
precondition(cache.read(other) == nil)
print("Cache verification passed")
'''
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "main.swift"
            script.write_text(harness)
            result = subprocess.run(
                ["swift", str(script), directory], capture_output=True, text=True, timeout=60,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Cache verification passed", result.stdout)
