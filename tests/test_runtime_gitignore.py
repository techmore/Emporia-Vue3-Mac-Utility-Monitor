import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which("git"), "Git is required for ignore-rule verification")
class RuntimeGitignoreTests(unittest.TestCase):
    def test_private_runtime_is_excluded_but_deployment_sources_are_not(self):
        root = Path(__file__).resolve().parents[1]
        private = [
            "keys.json", "settings.json", "poller_status.json", "collector.env",
            "ecosense-private.json", "ecosense-status.json", "ecosense-device-snapshot.json",
            "mitsubishi-private.json", "energy.db", "cache.db", "cache.db-wal",
            "cache.db-shm", "cache.db-journal", "backups/history.db",
        ]
        public = [
            "setup/energy-dashboard.service", "setup/kasa-collector.service",
            "scripts/prepare_linux_deployment.py", "docs/DEPLOYMENT_OPTIONS.md",
            "requirements.lock", "VERSION", "collector.env.example",
            "tests/fixtures/sample.json",
        ]
        with tempfile.TemporaryDirectory() as directory:
            shutil.copyfile(root / ".gitignore", Path(directory) / ".gitignore")
            subprocess.run(["git", "init", "--quiet"], cwd=directory, check=True,
                           capture_output=True)
            result = subprocess.run(
                ["git", "check-ignore", "--stdin"], cwd=directory, check=True,
                input="\n".join(private + public) + "\n", capture_output=True, text=True,
            )
            self.assertEqual(set(result.stdout.splitlines()), set(private))
