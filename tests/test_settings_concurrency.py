"""Exercise actual persisted settings under overlapping requests and processes."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import aqara
import energy
import runtime_store
import web


class SettingsConcurrencyTests(unittest.TestCase):
    def test_overlapping_budget_label_and_token_updates_preserve_all_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            runtime_store.write_private_json(path, {
                'monthly_budget': 150, 'device_labels': {}, 'aqara': {'account': 'retained'}})
            previous = os.getcwd()
            first_writing = threading.Event()
            release = threading.Event()
            real_write = runtime_store.write_private_json
            def delayed_write(target, value):
                if value.get('monthly_budget') == 222 and not first_writing.is_set():
                    first_writing.set()
                    if not release.wait(5):
                        raise TimeoutError('Concurrent request did not start')
                real_write(target, value)
            def post(url, data):
                return web.app.test_client().post(url, json=data).status_code
            try:
                os.chdir(directory)
                with patch.object(runtime_store, 'write_private_json', delayed_write), \
                        patch.object(web, '_refresh_runtime_config'), \
                        patch.object(aqara, 'SETTINGS_FILE', path), \
                        ThreadPoolExecutor(max_workers=3) as pool:
                    budget = pool.submit(post, '/api/settings/config', {'monthly_budget': 222})
                    self.assertTrue(first_writing.wait(5))
                    labels = pool.submit(post, '/api/settings/device-labels', {'barn': 'Barn'})
                    token = pool.submit(aqara._save_aqara_config, {'access_token': 'test-token'})
                    time.sleep(.05)
                    release.set()
                    self.assertEqual((budget.result(), labels.result()), (200, 200))
                    token.result()
                data = json.loads(path.read_text())
                self.assertEqual(data['monthly_budget'], 222)
                self.assertEqual(data['device_labels'], {'barn': 'Barn'})
                self.assertEqual(data['aqara'], {'account': 'retained', 'access_token': 'test-token'})
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            finally:
                release.set()
                os.chdir(previous)

    def test_cross_process_updates_use_one_stable_private_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            runtime_store.update_private_json(path, lambda data: data.update(count=0))
            lock = path.with_name('.settings.json.lock')
            inode = lock.stat().st_ino
            program = '''import sys,time
from runtime_store import update_private_json
def increment(data):
    value=data['count']
    time.sleep(.003)
    data['count']=value+1
for _ in range(15): update_private_json(sys.argv[1], increment)
'''
            processes = [subprocess.Popen([sys.executable, '-c', program, str(path)],
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                         for _ in range(3)]
            for process in processes:
                output, error = process.communicate(timeout=20)
                self.assertEqual(process.returncode, 0, error.decode())
            self.assertEqual(json.loads(path.read_text())['count'], 45)
            self.assertEqual(lock.stat().st_ino, inode)
            self.assertEqual(lock.stat().st_mode & 0o777, 0o600)

    def test_invalid_state_and_failed_change_preserve_file_and_release_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            for raw in ('{broken', '[]'):
                path.write_text(raw)
                with self.assertRaises(ValueError):
                    energy.update_settings({'monthly_budget': 222}, path=path)
                self.assertEqual(path.read_text(), raw)
            path.write_text('{"retained": true}')
            def fail(data):
                data['retained'] = False
                raise RuntimeError('cancel update')
            with self.assertRaises(RuntimeError):
                runtime_store.update_private_json(path, fail)
            self.assertEqual(json.loads(path.read_text()), {'retained': True})
            energy.update_settings({'monthly_budget': 222}, path=path)
            self.assertTrue(json.loads(path.read_text())['retained'])
