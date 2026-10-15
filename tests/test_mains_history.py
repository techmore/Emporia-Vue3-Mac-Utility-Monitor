import copy
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import mock_open, patch

import emporia_mains as mains
import energy


class MainsHistoryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        db = patch.object(energy, 'DB_PATH', str(Path(directory.name) / 'energy.db'))
        db.start()
        self.addCleanup(db.stop)
        energy.ensure_table()
        self.end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        self.start = self.end - timedelta(hours=24)

    def samples(self, minutes=1440):
        return [{'channel_name': name, 'timestamp': (self.start + timedelta(minutes=i)).isoformat(),
                 'usage_kwh': value} for name, value in (('Main', .008), ('Mains_A', .003), ('Mains_B', .005))
                for i in range(minutes)]

    def test_real_full_day_totals_are_idempotent_and_not_added_to_legacy_readings(self):
        rows = self.samples()
        energy.store_mains_intervals('551741', rows, 22.58)
        energy.store_mains_intervals('551741', rows, 22.58)
        history = energy.get_mains_history('551741', self.end)
        self.assertTrue(history['Mains_A']['complete'])
        self.assertEqual(history['Mains_A']['observed_minutes'], 1440)
        self.assertAlmostEqual(history['Mains_A']['kwh_24h'], 4.32)
        self.assertAlmostEqual(history['Mains_B']['kwh_24h'], 7.2)
        self.assertAlmostEqual(history['Mains_A']['cost_24h'], 4.32 * .2258)
        self.assertAlmostEqual(history['Main']['kwh_24h'],
                               history['Mains_A']['kwh_24h'] + history['Mains_B']['kwh_24h'])
        self.assertEqual(energy.get_mains_history('other', self.end), {})
        conn = energy._connect()
        try:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM mains_intervals').fetchone()[0], 4320)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM readings').fetchone()[0], 0)
        finally:
            conn.close()

    def test_gaps_zero_revisions_boundary_and_rate_are_not_fabricated(self):
        rows = self.samples(2)
        rows[0]['usage_kwh'] = 0
        energy.store_mains_intervals('551741', rows, 22.58)
        rows[1]['usage_kwh'] = .01
        energy.store_mains_intervals('551741', rows, 99)
        history = energy.get_mains_history('551741', self.end)
        self.assertFalse(history['Main']['complete'])
        self.assertEqual(history['Main']['observed_minutes'], 2)
        self.assertEqual(history['Main']['kwh_24h'], .01)
        self.assertAlmostEqual(history['Main']['cost_24h'], .01 * .2258)
        later = energy.get_mains_history('551741', self.end + timedelta(minutes=1))
        self.assertEqual(later['Main']['observed_minutes'], 1)
        self.assertEqual(energy.mains_history_start('empty', self.end), self.end - timedelta(minutes=1439))
        self.assertEqual(energy.mains_history_start('551741', self.end), self.end - timedelta(minutes=1439))

    def test_validation_is_atomic_for_duplicates_bad_values_and_future_intervals(self):
        rows = self.samples(1)
        for bad in (
            rows + rows,
            rows + [dict(rows[0], usage_kwh=float('inf'))],
            rows + [dict(rows[0], timestamp=self.end.isoformat())],
            rows + [dict(rows[0], timestamp=self.start.replace(tzinfo=None).isoformat())],
            rows + [dict(rows[0], channel_name='Branch_1')],
        ):
            with self.assertRaises(ValueError):
                energy.store_mains_intervals('551741', bad, 22.58)
            self.assertEqual(energy.get_mains_history('551741', self.end), {})

    def test_decode_keeps_zero_omits_null_and_uses_actual_utc_interval(self):
        end = self.start + timedelta(minutes=2)
        data = {'firstUsageInstant': self.start.isoformat(), 'usageList': [.003, 0, None]}
        decoded = mains.intervals('Mains_A', data, self.start, end)
        self.assertEqual([row['usage_kwh'] for row in decoded], [.003, 0])
        self.assertEqual(decoded[1]['timestamp'], (self.start + timedelta(minutes=1)).isoformat())
        for field, value in [('usageList', [True]), ('usageList', [float('nan')]),
                             ('firstUsageInstant', self.start.replace(second=1).isoformat())]:
            bad = copy.deepcopy(data)
            bad[field] = value
            with self.assertRaises(ValueError):
                mains.intervals('Mains_A', bad, self.start, end)
        with self.assertRaises(ValueError):
            mains.intervals('Mains_A', data, self.start + timedelta(minutes=1), end)

    def test_poller_collects_phases_and_phase_failure_does_not_rollback_branches(self):
        usage = {'551741': SimpleNamespace(channels={'1,2,3': SimpleNamespace(name='Main', usage=.008),
                                                     '4': SimpleNamespace(name='Kitchen', usage=.002)})}
        vue = SimpleNamespace(get_device_list_usage=lambda **kwargs: usage)
        with patch.dict('os.environ', {'EMPORIA_MAINS_HISTORY': '1'}), \
                patch.object(energy, '_last_compaction', time.monotonic()), \
                patch.object(energy, 'collect_mains_history', side_effect=TimeoutError('secret')) as collect:
            energy.poll_and_store(vue, ['551741'])
        collect.assert_called_once_with(vue, '551741')
        conn = energy._connect()
        try:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM readings').fetchone()[0], 2)
        finally:
            conn.close()

    def test_native_collector_and_preview_share_interval_persistence(self):
        snapshot = {'intervals': self.samples(2)}
        with patch('emporia_mains.collect', return_value=snapshot) as collect:
            energy.collect_mains_history(object(), '551741')
        self.assertEqual(collect.call_args.args[1], '551741')
        self.assertEqual(energy.get_mains_history('551741', self.end)['Mains_A']['observed_minutes'], 2)
        latest = {r['channel_name']: r for r in energy.get_latest('551741')}
        self.assertEqual(set(latest), {'Mains_A', 'Mains_B'})
        self.assertEqual(latest['Mains_A']['timestamp'], (self.start + timedelta(minutes=1)).isoformat())

    def test_cloud_full_day_is_requested_in_verified_nonoverlapping_chunks(self):
        calls = []
        vue = SimpleNamespace(auth=SimpleNamespace(tokens={'id_token': 'remote-only'}))

        def get(url, headers, params=None, timeout=None):
            if params is None:
                data = {'devices': [{'device_gid': 551741, 'channels': [
                    {'channel_id': name, 'has_data': True} for name in ('Mains', 'Mains_A', 'Mains_B')]}]}
            else:
                calls.append(params)
                first, last = mains.utc(params['start']), mains.utc(params['end'])
                count = int((last - first).total_seconds() / 60) + 1
                self.assertLessEqual(count, 720)
                data = {'firstUsageInstant': first.isoformat(), 'usageList': [.003] * count}
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: data)

        with patch('requests.get', side_effect=get):
            snapshot = mains.collect(vue, '551741', self.start, self.end - timedelta(minutes=1))
        self.assertEqual(len(calls), 6)
        self.assertEqual(len(snapshot['intervals']), 4320)
        energy.store_mains_intervals('551741', snapshot['intervals'], 22.58)
        self.assertTrue(energy.get_mains_history('551741', self.end)['Mains_A']['complete'])

    def test_poller_login_accepts_current_and_legacy_token_files(self):
        import json
        from unittest.mock import Mock
        for keys in (('id_token', 'access_token', 'refresh_token'), ('idToken', 'accessToken', 'refreshToken')):
            tokens = dict(zip(keys, ('id', 'access', 'refresh'), strict=True))
            vue = SimpleNamespace(login=Mock())
            with patch.object(energy.pyemvue, 'PyEmVue', return_value=vue), \
                    patch.object(energy, '_load_settings', return_value={}), \
                    patch('os.path.exists', return_value=True), \
                    patch('builtins.open', mock_open(read_data=json.dumps(tokens))), \
                    patch.object(energy, '_chmod_owner_only'):
                energy.login_vue()
            vue.login.assert_called_once_with(id_token='id', access_token='access',
                                               refresh_token='refresh', token_storage_file='keys.json')

    def test_chart_uses_measured_minutes_zero_and_null_gaps_without_other_panels_or_c(self):
        rows = self.samples(3)
        rows = [r for r in rows if not (r['channel_name'] == 'Mains_A'
                and r['timestamp'] == (self.start + timedelta(minutes=1)).isoformat())]
        rows[0]['usage_kwh'] = 0
        energy.store_mains_intervals('551741', rows, 22.58)
        energy.store_mains_intervals('99', self.samples(3), 22.58)
        chart = energy.get_mains_history_chart('551741', self.end)
        self.assertEqual(chart['interval_seconds'], 60)
        self.assertEqual(chart['start'], self.start.isoformat())
        self.assertEqual(chart['end'], self.end.isoformat())
        self.assertEqual([r['name'] for r in chart['series']], ['Total', 'Leg A', 'Leg B'])
        self.assertTrue(all(len(r['values']) == 1440 for r in chart['series']))
        self.assertEqual(chart['series'][0]['values'][:4], [0, 480, 480, None])
        self.assertEqual(chart['series'][1]['values'][:4], [180, None, 180, None])
        self.assertEqual(chart['series'][2]['values'][:4], [300, 300, 300, None])
        empty = energy.get_mains_history_chart('100', self.end)
        self.assertTrue(all(value is None for r in empty['series'] for value in r['values']))

    def test_chart_window_is_exactly_24_hours_across_dst(self):
        from zoneinfo import ZoneInfo
        end = datetime(2026, 3, 8, 12, tzinfo=ZoneInfo('America/New_York'))
        chart = energy.get_mains_history_chart('551741', end)
        self.assertEqual(mains.utc(chart['end']) - mains.utc(chart['start']), timedelta(hours=24))


if __name__ == '__main__':
    unittest.main()
