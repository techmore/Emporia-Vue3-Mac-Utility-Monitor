import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import energy


class PollerLockTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'energy.db'
        self.db = patch.object(energy, 'DB_PATH', str(self.path))
        self.db.start()
        energy.ensure_table()

    def tearDown(self):
        self.db.stop()
        self.directory.cleanup()

    def child_attempt(self, database):
        return subprocess.run([sys.executable, '-c',
            'import energy\nwith energy._poller_lock(): print("acquired")'],
            env={**os.environ, 'DB_PATH': str(database)}, capture_output=True,
            text=True, timeout=15)

    def test_cross_process_exclusion_aliases_and_release(self):
        alias = self.path.with_name('alias.db')
        alias.symlink_to(self.path)
        other = self.path.with_name('other.db')
        with energy._poller_lock():
            for target in (self.path, alias):
                result = self.child_attempt(target)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Another Emporia poller', result.stderr)
            self.assertEqual(self.child_attempt(other).returncode, 0)
        self.assertEqual(self.child_attempt(self.path).returncode, 0)
        lock = Path(str(self.path) + '.poller.lock')
        self.assertTrue(lock.exists())
        self.assertEqual(lock.stat().st_mode & 0o777, 0o600)

    def test_both_entrypoints_reject_before_login_or_health_changes(self):
        with energy._poller_lock(), patch.object(energy, 'login_vue') as login, \
                patch.object(energy, 'write_poller_status') as status:
            for entrypoint in (energy.poll_once, energy.run_continuous):
                with self.assertRaisesRegex(RuntimeError, 'Another Emporia poller'):
                    entrypoint()
            login.assert_not_called()
            status.assert_not_called()

    def test_abrupt_process_exit_releases_kernel_ownership(self):
        result = subprocess.run([sys.executable, '-c',
            'import os, energy\nwith energy._poller_lock(): os._exit(7)'],
            env={**os.environ, 'DB_PATH': str(self.path)}, capture_output=True,
            text=True, timeout=15)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(self.child_attempt(self.path).returncode, 0)

    def test_failure_releases_lock_and_symlink_lock_is_rejected(self):
        with patch.object(energy, 'login_vue', side_effect=TimeoutError('fixture')):
            with self.assertRaises(TimeoutError):
                energy.poll_once()
        self.assertEqual(self.child_attempt(self.path).returncode, 0)
        lock = Path(str(self.path) + '.poller.lock')
        lock.unlink()
        victim = self.path.with_name('victim')
        victim.write_text('unchanged')
        lock.symlink_to(victim)
        with self.assertRaises(OSError), energy._poller_lock():
            self.fail('Symlink lock must not be followed')
        self.assertEqual(victim.read_text(), 'unchanged')
