import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which("swift"), "Swift runtime required")
class CollectorAddressTests(unittest.TestCase):
    def test_production_validator_accepts_tunnels_and_rejects_unsafe_origins(self):
        source = (Path(__file__).resolve().parents[1]
                  / "EnergyMonitorApp/Sources/main.swift").read_text()
        start = source.index("private func validateCollectorURL(")
        end = source.index("\nprivate func validateProjectRoot", start)
        validator = source[start:end]
        harness = '''
import Foundation
private let collectorURLSetting: String? = nil
'''+validator+'''
let valid: [String?] = [nil, "http://127.0.0.1:15001", "http://localhost:15001/",
                       "https://collector.example"]
let invalid = ["", "http://ser8:5001", "ftp://localhost", "https://user:pass@ser8",
               "https://ser8/path", "https://ser8?token=secret", "https://ser8#fragment"]
for value in valid { precondition(validateCollectorURL(value) == nil, "Rejected valid origin") }
for value in invalid { precondition(validateCollectorURL(value) != nil, "Accepted unsafe origin") }
print("Collector origins verified")
'''
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "main.swift"
            script.write_text(harness)
            result = subprocess.run(
                ["swift", str(script)], capture_output=True, text=True, timeout=60,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Collector origins verified", result.stdout)
