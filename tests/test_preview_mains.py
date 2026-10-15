import contextlib
import copy
import io
import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import preview_mains as mains


class PreviewMainsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.snapshot = {'device_gid': '551741', 'captured_at': datetime.now(timezone.utc).isoformat(),
                         'rows': [{'channel_name': name, 'usage_kwh': value,
                                   'timestamp': datetime.now(timezone.utc).isoformat(), 'age_seconds': 60}
                                  for name, value in (('Main', .008), ('Mains_A', .003), ('Mains_B', .005))]}
        self.command = ['ssh', '-T', 'host', 'incus exec energy-monitor -- python3 -']

    def test_auth_stays_remote_and_phase_fetch_is_throttled(self):
        result = subprocess.CompletedProcess([], 0, stdout=json.dumps(self.snapshot), stderr='')
        with patch.object(mains.subprocess, 'run', return_value=result) as run:
            mains.refresh(self.root, self.command, '/opt/existing/venv/bin/python3', '551741')
            mains.refresh(self.root, self.command, '/opt/existing/venv/bin/python3', '551741')
        self.assertEqual(run.call_count, 1)
        self.assertIn('/opt/existing/venv/bin/python3', run.call_args.args[0][-1])
        script = run.call_args.kwargs['input']
        self.assertIn('token_storage_file=None', script)
        self.assertIn("'channel':channel", script)
        self.assertNotIn('write_text', script)
        self.assertNotIn('sqlite3', script)
        self.assertEqual((self.root / 'mains-cache.json').stat().st_mode & 0o777, 0o600)
        rows = mains.readings(self.root, '551741', datetime.now(timezone.utc))
        self.assertEqual(len(rows), 3)
        self.assertAlmostEqual(rows[1]['usage_kwh'] * 60000, 180)
        self.assertEqual(mains.readings(self.root, 'other', datetime.now(timezone.utc)), [])
        self.assertEqual(mains.readings(self.root, '551741', datetime.now(timezone.utc) + timedelta(minutes=4)), [])

    def test_invalid_units_values_duplicates_and_timestamps_are_rejected(self):
        for mutate in (
            lambda s: s.update(device_gid='other'),
            lambda s: s['rows'].append(s['rows'][0]),
            lambda s: s['rows'][0].update(channel_name='Branch_1'),
            lambda s: s['rows'][0].update(usage_kwh=float('nan')),
            lambda s: s['rows'][0].update(usage_kwh=True),
            lambda s: s['rows'][0].update(timestamp='2026-10-15T12:00:00'),
        ):
            data = copy.deepcopy(self.snapshot)
            mutate(data)
            with self.assertRaises(ValueError):
                mains.validate(data, '551741')
        self.snapshot['rows'][1]['usage_kwh'] = 0
        self.snapshot['rows'][2]['usage_kwh'] = None
        self.assertEqual(mains.validate(self.snapshot, '551741'), self.snapshot)

    def test_failure_does_not_leak_stderr_or_retry_every_cycle(self):
        with patch.object(mains.subprocess, 'run', side_effect=subprocess.TimeoutExpired('secret', 70)) as run:
            mains.refresh(self.root, self.command, '/opt/venv/bin/python3', '551741')
            mains.refresh(self.root, self.command, '/opt/venv/bin/python3', '551741')
        self.assertEqual(run.call_count, 1)
        cache = (self.root / 'mains-cache.json').read_text()
        self.assertNotIn('secret', cache)
        self.assertEqual(mains.readings(self.root, '551741', datetime.now(timezone.utc)), [])

    def test_remote_program_validates_measurement_units_and_cadence(self):
        compile(mains.REMOTE_SCRIPT, '<remote-mains>', 'exec')
        self.assertIn("'scale':'1MIN','energyUnit':'KilowattHours'", mains.REMOTE_SCRIPT)
        self.assertIn("data['firstUsageInstant']", mains.REMOTE_SCRIPT)
        self.assertIn("get('has_data')", mains.REMOTE_SCRIPT)

    def test_remote_program_uses_explicit_phase_channels_and_actual_sample_timestamp(self):
        calls = []
        login = []
        vue = SimpleNamespace(auth=SimpleNamespace(tokens={'id_token': 'remote-only'}),
                              login=lambda **kwargs: login.append(kwargs))
        channels = [{'channel_id': name, 'has_data': name != 'Mains_C'}
                    for name in ('Mains', 'Mains_A', 'Mains_B', 'Mains_C')]

        def get(url, headers, params=None, timeout=None):
            self.assertEqual(headers, {'Authorization': 'remote-only'})
            if params is None:
                data = {'devices': [{'device_gid': 551741, 'channels': channels}]}
            else:
                calls.append(params)
                data = {'firstUsageInstant': '2026-10-15T14:32:00Z',
                        'usageList': [0.003084773050110197, 0.0029958173073662653, 0.003000787438286675]}
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: data)

        output = io.StringIO()
        with patch.dict('sys.modules', {'pyemvue': SimpleNamespace(PyEmVue=lambda: vue)}), \
                patch.object(Path, 'read_text', return_value=json.dumps({
                    'id_token': 'id', 'access_token': 'access', 'refresh_token': 'refresh'})), \
                patch('requests.get', side_effect=get), contextlib.redirect_stdout(output):
            exec(mains.REMOTE_SCRIPT, {'GID': '551741'})
        snapshot = mains.validate(json.loads(output.getvalue()), '551741')
        self.assertEqual([p['channel'] for p in calls], ['Mains', 'Mains_A', 'Mains_B'])
        self.assertTrue(all(p['scale'] == '1MIN' and p['energyUnit'] == 'KilowattHours' for p in calls))
        self.assertEqual(snapshot['rows'][1]['timestamp'], '2026-10-15T14:34:00+00:00')
        self.assertEqual(snapshot['rows'][1]['usage_kwh'], 0.003000787438286675)
        self.assertIsNone(login[0]['token_storage_file'])
        self.assertNotIn('remote-only', output.getvalue())


if __name__ == '__main__':
    unittest.main()
