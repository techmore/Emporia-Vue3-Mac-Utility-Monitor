import os
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from threading import Barrier
from unittest.mock import patch

import energy
import web


class PollIntervalAccountingTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        database = patch.object(energy, 'DB_PATH', str(self.root / 'test.db'))
        database.start()
        self.addCleanup(database.stop)
        energy.ensure_table()
        self.clock = datetime.now().replace(hour=12, minute=5, second=0, microsecond=0)
        owner = self

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return owner.clock if tz is None else owner.clock.astimezone(tz)

        for target, value in [('datetime', Clock), ('_last_compaction', time.monotonic()),
                              ('_last_reports', time.monotonic())]:
            override = patch.object(energy, target, value)
            override.start()
            self.addCleanup(override.stop)
        web_clock = patch.object(web, 'datetime', Clock)
        web_clock.start()
        self.addCleanup(web_clock.stop)
        self.addCleanup(setattr, energy, 'RATE_CENTS', energy.RATE_CENTS)
        environment = patch.dict(os.environ, {'EMPORIA_MAINS_HISTORY': '0', 'RATE_CENTS': '10'})
        environment.start()
        self.addCleanup(environment.stop)

    def upload(self, gid='QA', interval='1H'):
        stamp = self.clock.replace(minute=0).astimezone(timezone.utc)
        path = self.root / f'{gid}-Panel-{interval}.csv'
        path.write_text('Time Bucket (UTC),QA-Main (kWhs)\n' +
                        stamp.strftime('%m/%d/%Y %H:%M:%S') + ',1\n')
        return energy.import_emporia_csv(str(path), device_gid=gid)

    def poll(self, gid='QA', value=.01):
        vue = SimpleNamespace(get_device_list_usage=lambda **kwargs: {
            gid: SimpleNamespace(channels={1: SimpleNamespace(name='Main', usage=value),
                                          2: SimpleNamespace(name='Pump', usage=.02)})})
        energy.poll_and_store(vue, [gid])

    def rows(self, table):
        conn = energy._connect()
        try:
            return [dict(row) for row in conn.execute(f'SELECT * FROM {table}')]
        finally:
            conn.close()

    def test_import_then_poll_preserves_totals_and_fresh_power_and_channel_scope(self):
        self.assertEqual(self.upload()['imported'], 1)
        self.poll()
        self.assertEqual(energy.get_main_total(24, 'QA')['total_kwh'], 1)
        latest = {row['channel_name']: row for row in energy.get_latest('QA')}
        self.assertEqual(web._watts_estimate(latest['Main']), 600)
        history = energy.get_circuit_history('Main', 'QA', now=self.clock + timedelta(seconds=1))
        self.assertEqual(history['live_watts'], 600)
        self.assertEqual(history['windows'][0]['total_kwh'], 1)
        self.assertEqual(len(self.rows('covered_live_observations')), 1)
        self.assertEqual(len([row for row in self.rows('readings') if row['channel_name'] == 'Pump']), 1)
        self.poll(gid='other')
        self.assertEqual(energy.get_main_total(24, 'other')['total_kwh'], .01)

    def test_poll_then_import_rejects_overlap(self):
        self.poll()
        result = self.upload()
        self.assertEqual((result['imported'], result['interval_conflicts']), (0, 1))
        self.assertEqual(energy.get_main_total(24, 'QA')['total_kwh'], .01)

    def test_live_zero_and_resumption_at_import_end(self):
        self.upload()
        self.poll(value=0)
        main = next(row for row in energy.get_latest('QA') if row['channel_name'] == 'Main')
        self.assertEqual(web._watts_estimate(main), 0)
        self.clock = self.clock.replace(hour=13, minute=0)
        self.poll()
        self.assertEqual(energy.get_main_total(24, 'QA')['total_kwh'], 1.01)
        self.assertEqual(self.rows('covered_live_observations'), [])
        main = next(row for row in energy.get_latest('QA') if row['channel_name'] == 'Main')
        self.assertEqual(web._watts_estimate(main), 600)

    def test_unknown_import_uses_same_conservative_hour_policy(self):
        self.upload(interval='UNKNOWN')
        self.poll()
        self.assertEqual(energy.get_main_total(24, 'QA')['total_kwh'], 1)
        self.assertEqual(len(self.rows('covered_live_observations')), 1)

    def test_observation_and_other_channel_writes_roll_back_together(self):
        self.upload()
        before = self.rows('readings')
        with patch.object(energy, '_save_device_capabilities_with_conn', side_effect=RuntimeError('fail')):
            with self.assertRaises(RuntimeError):
                self.poll()
        self.assertEqual(self.rows('readings'), before)
        self.assertEqual(self.rows('covered_live_observations'), [])

    def test_concurrent_import_and_poll_never_double_count(self):
        gate = Barrier(2)

        def upload():
            gate.wait(timeout=5)
            return self.upload()

        def poll():
            gate.wait(timeout=5)
            self.poll()

        with ThreadPoolExecutor(max_workers=2) as pool:
            incoming = pool.submit(upload)
            live = pool.submit(poll)
            result = incoming.result(timeout=10)
            live.result(timeout=10)
        expected = 1 if result['imported'] else .01
        self.assertEqual(energy.get_main_total(24, 'QA')['total_kwh'], expected)
        latest = next(row for row in energy.get_latest('QA') if row['channel_name'] == 'Main')
        self.assertEqual(web._watts_estimate(latest), 600)


    def test_report_failure_keeps_collector_healthy_and_retries_next_poll(self):
        class EndLoop(BaseException):
            pass

        vue = SimpleNamespace(get_device_list_usage=lambda **kwargs: {
            'QA': SimpleNamespace(channels={1: SimpleNamespace(name='Main', usage=.01)})})
        month = (self.clock.replace(day=1) - timedelta(days=1)).strftime('%Y-%m')
        fixture = [{'month': month, 'total_kwh': 1, 'total_cents': 100,
                    'days_recorded': 1, 'circuits': []}]
        statuses = []
        with patch.object(energy, '_last_compaction', float('-inf')), \
                patch.object(energy, '_last_reports', float('-inf')), \
                patch.dict(os.environ, {'EMPORIA_MAINS_HISTORY': '1'}), \
                patch.object(energy, 'login_vue', return_value=vue), \
                patch.object(energy, 'get_devices_with_channels', return_value=(['QA'], [])), \
                patch.object(energy, 'get_monthly_costs', return_value=fixture), \
                patch.object(energy, 'compact_minute_readings', wraps=energy.compact_minute_readings) as compact, \
                patch.object(energy, 'publish_private_text', side_effect=PermissionError('report denied')) as publish, \
                patch.object(energy, 'collect_mains_history') as mains, \
                patch.object(energy, 'write_poller_status', side_effect=lambda ok, **kw: statuses.append(ok)), \
                patch.object(energy.time, 'sleep', side_effect=EndLoop):
            with self.assertLogs(energy.logger, level='ERROR'):
                with self.assertRaises(EndLoop):
                    energy._run_continuous()
            self.assertTrue(all(statuses))
            self.assertEqual(mains.call_count, 1)
            self.assertEqual(len(self.rows('readings')), 1)
            self.clock += timedelta(minutes=1)
            with self.assertLogs(energy.logger, level='ERROR'):
                energy.poll_and_store(vue, ['QA'])
            self.assertEqual(publish.call_count, 2)
            self.assertEqual(mains.call_count, 2)
            self.assertEqual(compact.call_count, 1)
            self.assertEqual(len(self.rows('readings')), 2)
            publish.side_effect = None
            publish.return_value = True
            self.clock += timedelta(minutes=1)
            energy.poll_and_store(vue, ['QA'])
            self.clock += timedelta(minutes=1)
            energy.poll_and_store(vue, ['QA'])
            self.assertEqual(publish.call_count, 3)
