import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which("swift"), "Swift runtime required")
class NativeSyncRunnerTests(unittest.TestCase):
    def test_stop_and_timeout_reap_downloader_ignoring_termination(self):
        source = (Path(__file__).resolve().parents[1]
                  / "EnergyMonitorApp/Sources/CollectorSync.swift").read_text()
        runner = source[source.index("final class CollectorSyncRunner") :]
        for mode in ("stop", "timeout"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                pid_file = root / "child.pid"
                fake = root / "download.py"
                fake.write_text(
                    "import os,signal,sys,time\nfrom pathlib import Path\n"
                    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                    "Path(sys.argv[4]).write_text(str(os.getpid()))\n"
                    "while True: time.sleep(0.1)\n"
                )
                harness = "import Foundation\nimport Darwin\n" + runner + '''
let runner = CollectorSyncRunner(timeout: 1, terminationGrace: 0.1)
var complete = false
runner.start(python: CommandLine.arguments[1], script: CommandLine.arguments[2],
             origin: URL(string: "http://127.0.0.1:15001")!,
             cache: CommandLine.arguments[3], token: String(repeating: "a", count: 32)) {
    error in
    precondition(error != nil)
    complete = true
}
let deadline = Date().addingTimeInterval(8)
while !FileManager.default.fileExists(atPath: CommandLine.arguments[3]) && Date() < deadline {
    RunLoop.main.run(until: Date().addingTimeInterval(0.02))
}
precondition(FileManager.default.fileExists(atPath: CommandLine.arguments[3]))
if CommandLine.arguments[4] == "stop" { runner.stop() }
while !complete && Date() < deadline {
    RunLoop.main.run(until: Date().addingTimeInterval(0.02))
}
precondition(complete)
runner.stop()
'''
                script = root / "main.swift"
                script.write_text(harness)
                try:
                    result = subprocess.run(
                        ["swift", str(script), sys.executable, str(fake), str(pid_file), mode],
                        capture_output=True, text=True, timeout=30,
                    )
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    pid = int(pid_file.read_text())
                    with self.assertRaises(ProcessLookupError):
                        os.kill(pid, 0)
                finally:
                    if pid_file.exists():
                        try:
                            os.kill(int(pid_file.read_text()), signal.SIGKILL)
                        except ProcessLookupError:
                            pass

    def test_runner_passes_token_only_in_environment_and_prevents_overlap(self):
        source = (Path(__file__).resolve().parents[1]
                  / "EnergyMonitorApp/Sources/CollectorSync.swift").read_text()
        runner = source[source.index("final class CollectorSyncRunner") :]
        harness = "import Foundation\nimport Darwin\n" + runner + '''
let runner = CollectorSyncRunner()
var complete = false
var count = 0
let callback: (String?) -> Void = { error in
    precondition(error == nil)
    count += 1
    complete = true
}
for _ in 0..<2 {
    runner.start(python: CommandLine.arguments[1], script: CommandLine.arguments[2],
                 origin: URL(string: "http://127.0.0.1:15001")!,
                 cache: CommandLine.arguments[3], token: String(repeating: "a", count: 32),
                 completion: callback)
}
let deadline = Date().addingTimeInterval(10)
while !complete && Date() < deadline {
    RunLoop.main.run(until: Date().addingTimeInterval(0.05))
}
runner.stop()
precondition(complete && count == 1)
print("Runner verified")
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "main.swift"
            script.write_text(harness)
            fake = root / "download.py"
            fake.write_text(
                "import json,os,sys,time\nfrom pathlib import Path\n"
                "time.sleep(0.2)\n"
                "Path(sys.argv[4]).write_text(json.dumps({'argv':sys.argv,"
                "'token_correct':os.environ.get('ENERGY_SYNC_TOKEN')=='a'*32}))\n"
            )
            report = root / "report.json"
            result = subprocess.run(
                ["swift", str(script), sys.executable, str(fake), str(report)],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            saved = json.loads(report.read_text())
        self.assertTrue(saved["token_correct"])
        self.assertNotIn("a" * 32, " ".join(saved["argv"]))
