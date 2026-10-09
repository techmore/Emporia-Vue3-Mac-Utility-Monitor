import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import energy
import web
from utc_migration import rehearse_utc_copy


class UtcDurationQueryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        database = patch.object(energy, 'DB_PATH', str(self.root/'legacy.db'))
        database.start()
        self.addCleanup(database.stop)
        self.addCleanup(self.directory.cleanup)
        energy.ensure_table()
        self.original_connect = energy._connect
        self.artifact = self.root/'converted.db'

    def convert(self, rows, zone='America/New_York'):
        connection = self.original_connect()
        try:
            with connection:
                connection.executemany(
                    'INSERT INTO readings(timestamp,device_gid,channel_name,usage_kwh,cost_cents) VALUES (?,?,?,?,?)',
                    rows,
                )
        finally:
            connection.close()
        archive = self.root/'archive.db'
        energy.backup_database(archive)
        self.receipt = rehearse_utc_copy(
            archive, self.artifact,
            expected_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
            legacy_timezone=zone, reporting_timezone=zone,
        )

    def queries(self):
        return patch.object(energy, '_connect', side_effect=lambda: self.original_connect(
            self.artifact, allow_utc_rehearsal=True, read_only=True,
        ))

    def test_exact_elapsed_window_stored_cents_and_device_isolation(self):
        # A 24h window across fall-back starts at 01:00 EDT, not local midnight.
        now = datetime(2026, 11, 2, 5, tzinfo=timezone.utc)
        start = now-timedelta(hours=24)
        rows = []
        for instant, value in ((start-timedelta(microseconds=1), 100), (start, 1),
                               (now, 2), (now+timedelta(microseconds=1), 200)):
            for channel in ('Main', 'Dryer', 'Mains_A'):
                rows.append((instant.isoformat(), 'A', channel, value, value*23.41))
        rows.append((now.isoformat(), 'B', 'Main', 999, 999))
        self.convert(rows)
        with self.queries():
            main = energy.get_main_total(24, 'A', now=now)
            summary = energy.get_summary(24, 'A', now=now)
            legs = energy.get_channel_totals(['Mains_A'], 24, 'A', now=now)
        self.assertEqual(main['total_kwh'], 3)
        self.assertAlmostEqual(main['total_cents'], 70.23)
        self.assertEqual(main['readings'], 2)
        self.assertEqual([row['channel_name'] for row in summary], ['Dryer'])
        self.assertEqual(summary[0]['total_kwh'], 3)
        self.assertEqual(legs[0]['total_kwh'], 3)
        self.assertEqual(hashlib.sha256(self.artifact.read_bytes()).hexdigest(), self.receipt['artifact_sha256'])

    def test_hourly_and_circuit_chart_keep_both_folds_and_gap(self):
        self.convert([(stamp, 'A', channel, value, value*20)
                      for stamp, value in [('2026-11-01T00:20:00-04:00', 0),
                                           ('2026-11-01T01:20:00-04:00', 1),
                                           ('2026-11-01T01:20:00-05:00', 2),
                                           ('2026-11-01T03:20:00-05:00', 3)]
                      for channel in ('Main', 'Dryer')])
        now = datetime(2026, 11, 1, 9, tzinfo=timezone.utc)
        with self.queries():
            hourly = energy.get_hourly_data(1, 'A', now=now)
            circuit = energy.get_circuit_data('Dryer', 'hour', 'A', now=now)
        filled = web._fill_gaps(hourly, 'hour', hourly=True)
        self.assertEqual([row['hour'] for row in filled], [
            '2026-11-01T00:00-04:00', '2026-11-01T01:00-04:00',
            '2026-11-01T01:00-05:00', '2026-11-01T02:00-05:00', '2026-11-01T03:00-05:00',
        ])
        self.assertEqual([row['total_kwh'] for row in filled], [0, 1, 2, None, 3])
        self.assertEqual(len({row['bucket_utc'] for row in filled}), 5)
        self.assertEqual([row['period'] for row in circuit['data']], [row['hour'] for row in hourly])
        self.assertEqual(circuit['total']['total_kwh'], 6)

    def test_spring_chart_does_not_invent_nonexistent_hour(self):
        self.convert([('2026-03-08T01:20:00-05:00', 'A', 'Main', 1, 20),
                      ('2026-03-08T04:20:00-04:00', 'A', 'Main', 2, 40)])
        with self.queries():
            hourly = energy.get_hourly_data(1, 'A', now=datetime(2026, 3, 8, 9, tzinfo=timezone.utc))
        filled = web._fill_gaps(hourly, 'hour', hourly=True)
        self.assertEqual([row['hour'] for row in filled], [
            '2026-03-08T01:00-05:00', '2026-03-08T03:00-04:00', '2026-03-08T04:00-04:00',
        ])
        self.assertIsNone(filled[1]['total_kwh'])

    def test_half_hour_day_boundary_is_not_skipped_by_gap_filling(self):
        self.convert([('2026-10-04T22:45:00+11:00', 'A', 'Main', 1, 20),
                      ('2026-10-05T00:20:00+11:00', 'A', 'Main', 2, 40)], 'Australia/Lord_Howe')
        now = datetime.fromisoformat('2026-10-05T01:00:00+11:00')
        with self.queries():
            hourly = energy.get_hourly_data(1, 'A', now=now)
        filled = web._fill_gaps(hourly, 'hour', hourly=True)
        self.assertEqual([row['hour'] for row in filled], [
            '2026-10-04T22:30+11:00', '2026-10-04T23:30+11:00', '2026-10-05T00:00+11:00',
        ])
        self.assertIsNone(filled[1]['total_kwh'])

    def test_reporting_day_month_and_year_not_utc_calendar(self):
        self.convert([(stamp, 'A', channel, value, value*20)
                      for stamp, value in [('2026-01-01T04:59:59Z', 1),
                                           ('2026-01-01T05:00:00Z', 2),
                                           ('2026-01-01T06:00:00.000001Z', 100)]
                      for channel in ('Main', 'Dryer')])
        now = datetime(2026, 1, 1, 6, tzinfo=timezone.utc)
        with self.queries():
            daily = energy.get_daily_data(7, 'A', now=now)
            month = energy.get_month_comparison('A', now=now)
            projection = energy.get_monthly_projection('A', now=now)
            charts = {period: energy.get_circuit_data('Dryer', period, 'A', now=now)
                      for period in ('day', 'week', 'month', 'year', 'invalid')}
        self.assertEqual([row['day'] for row in daily], ['2025-12-31', '2026-01-01'])
        self.assertEqual(month['last_month']['month'], '2025-12')
        self.assertEqual(month['last_month']['total_kwh'], 1)
        self.assertEqual(month['this_month']['total_kwh'], 2)
        self.assertEqual(projection, {'month': '2026-01', 'total_kwh': 2, 'total_cents': 40})
        self.assertEqual([row['period'] for row in charts['month']['data']], ['2025-12', '2026-01'])
        self.assertEqual([row['period'] for row in charts['year']['data']], ['2025', '2026'])
        for chart in charts.values():
            self.assertEqual(chart['total']['total_kwh'], 3)
            self.assertEqual(chart['total']['readings'], 2)
        self.assertEqual(charts['invalid'], charts['day'])

    def test_reporting_month_does_not_advance_at_utc_midnight(self):
        self.convert([('2026-10-01T02:00:00Z', 'A', 'Main', 1, 20),
                      ('2026-10-01T04:00:00Z', 'A', 'Main', 2, 40)])
        with self.queries():
            result = energy.get_month_comparison('A', now=datetime(2026, 10, 1, 3, tzinfo=timezone.utc))
            projection = energy.get_monthly_projection('A', now=datetime(2026, 10, 1, 3, tzinfo=timezone.utc))
        self.assertEqual(result['this_month']['month'], '2026-09')
        self.assertEqual(result['this_month']['total_kwh'], 1)
        self.assertIsNone(result['last_month'])
        self.assertEqual(projection['month'], '2026-09')

    def test_ghost_compatibility_alias_uses_real_device_not_zero_rows(self):
        self.convert([('2026-10-08T12:00:00Z', 'A', 'Main', 1, 20),
                      ('2026-10-08T12:00:00Z', energy._GHOST_DEVICE, 'Main', 0, 0)])
        now = datetime(2026, 10, 8, 13, tzinfo=timezone.utc)
        with self.queries():
            self.assertEqual(energy.get_main_total(device_gid=energy._GHOST_DEVICE, now=now)['total_kwh'], 1)
            self.assertEqual(energy.get_daily_data(device_gid=energy._GHOST_DEVICE, now=now)[0]['total_kwh'], 1)

    def test_trend_uses_elapsed_reporting_days_across_capture_gaps(self):
        self.convert([('2026-10-01T03:00:00Z', 'A', 'Main', 2, 40),
                      ('2026-10-04T03:00:00Z', 'A', 'Main', 8, 160),
                      ('2026-10-04T04:00:00.000001Z', 'A', 'Main', 100, 2000)])
        with self.queries():
            trend = energy.get_trend(7, 'A', now=datetime(2026, 10, 4, 4, tzinfo=timezone.utc))
        self.assertEqual([row['day'] for row in trend['daily']], ['2026-09-30', '2026-10-03'])
        self.assertEqual(trend['slope'], 2)
        self.assertEqual(trend['avg_kwh'], 5)

    def test_empty_and_unknown_devices_do_not_invent_energy(self):
        self.convert([('2026-10-08T12:00:00Z', 'A', 'Main', 0, 0)])
        now = datetime(2026, 10, 8, 13, tzinfo=timezone.utc)
        with self.queries():
            self.assertIsNone(energy.get_main_total(device_gid='missing', now=now))
            self.assertEqual(energy.get_summary(device_gid='missing', now=now), [])
            self.assertEqual(energy.get_hourly_data(device_gid='missing', now=now), [])
            self.assertEqual(energy.get_daily_data(device_gid='missing', now=now), [])
            self.assertEqual(energy.get_channel_totals([], now=now), [])
            self.assertIsNone(energy.get_monthly_projection('missing', now=now))
            self.assertIsNone(energy.get_month_comparison('missing', now=now)['this_month'])
            self.assertEqual(energy.get_circuit_data('missing', device_gid='A', now=now)['total']['readings'], 0)
            self.assertEqual(energy.get_main_total(device_gid='A', now=now)['total_kwh'], 0)

    def test_history_elapsed_windows_fold_minutes_gaps_and_live_age(self):
        now = datetime(2026, 11, 1, 7, tzinfo=timezone.utc)
        rows = [((now-timedelta(hours=2)+timedelta(minutes=i)).isoformat(),
                 'A', 'Dryer', 0, 0) for i in range(120)]
        rows += [((now-timedelta(seconds=30)).isoformat(), 'A', 'Dryer', 0.01, 0.2),
                 ((now-timedelta(days=2)).isoformat(), 'A', 'Dryer', 2, 40),
                 ((now-timedelta(days=10)).isoformat(), 'A', 'Dryer', 4, 80),
                 ((now+timedelta(microseconds=1)).isoformat(), 'A', 'Dryer', 100, 2000),
                 ((now-timedelta(seconds=30)).isoformat(), 'B', 'Dryer', 999, 999)]
        self.convert(rows)
        with self.queries():
            result = energy.get_circuit_history('Dryer', 'A', now)
            self.assertIsNone(energy.get_circuit_history('missing', 'A', now))
        self.assertEqual(result['live_watts'], 600)
        self.assertEqual([row['total_kwh'] for row in result['windows']], [0.01, 2.01, 6.01])
        self.assertEqual(result['windows'][0]['sampled_minutes'], 120)
        self.assertTrue(all(row['change_pct'] is None for row in result['windows']))
        series = result['windows'][0]['series']
        folds = [row for row in series if row['period'].startswith('2026-11-01T01:')]
        self.assertEqual([row['period'] for row in folds], ['2026-11-01T01:00-04:00', '2026-11-01T01:00-05:00'])
        self.assertEqual([row['readings'] for row in folds], [60, 61])
        self.assertEqual(folds[0]['total_kwh'], 0)
        self.assertTrue(any(row['total_kwh'] is None for row in series))
        self.assertEqual(sum(row['total_kwh'] or 0 for row in series), 0.01)

    def test_history_reporting_days_preserve_actual_dst_duration(self):
        self.convert([('2026-11-01T12:00:00-05:00', 'A', 'Dryer', 1, 20)])
        now = datetime(2026, 11, 2, 5, tzinfo=timezone.utc)
        with self.queries():
            result = energy.get_circuit_history('Dryer', 'A', now)
        self.assertIsNone(result['live_watts'])
        day = next(row for row in result['windows'][1]['series'] if row['period'] == '2026-11-01')
        self.assertEqual(day['interval_minutes'], 25*60)
        self.assertFalse(day['partial_bucket'])
        self.assertEqual(day['total_kwh'], 1)

    def test_history_half_hour_partial_bins_have_actual_duration(self):
        self.convert([('2026-10-04T23:40:00+11:00', 'A', 'Dryer', 1, 20)], 'Australia/Lord_Howe')
        now = datetime.fromisoformat('2026-10-05T00:10:00+11:00')
        with self.queries():
            result = energy.get_circuit_history('Dryer', 'A', now)
        series = result['windows'][0]['series']
        self.assertEqual(series[-2]['period'], '2026-10-04T23:30+11:00')
        self.assertEqual(series[-2]['interval_minutes'], 30)
        self.assertFalse(series[-2]['partial_bucket'])
        self.assertEqual(series[-2]['total_kwh'], 1)
        self.assertTrue(series[0]['partial_bucket'])
        self.assertTrue(series[-1]['partial_bucket'])

    def test_naive_clock_is_rejected_and_query_connection_closed(self):
        self.convert([('2026-10-08T12:00:00Z', 'A', 'Main', 1, 20)])
        for query in (energy.get_main_total, energy.get_summary, energy.get_hourly_data,
                      energy.get_daily_data, energy.get_month_comparison, energy.get_monthly_projection,
                      energy.get_trend, lambda **kw: energy.get_channel_totals(['Main'], **kw),
                      lambda **kw: energy.get_circuit_data('Main', **kw),
                      lambda **kw: energy.get_circuit_history('Main', **kw)):
            with self.subTest(query=query):
                connection = self.original_connect(self.artifact, allow_utc_rehearsal=True, read_only=True)
                with patch.object(energy, '_connect', return_value=connection), self.assertRaises(ValueError):
                    query(device_gid='A', now=datetime(2026, 10, 8, 13))
                with self.assertRaises(energy.sqlite3.ProgrammingError):
                    connection.execute('SELECT 1')

    def test_mixed_chart_policies_are_rejected(self):
        rows = [{'hour': '2026-10-08T12:00+00:00', 'bucket_utc': '2026-10-08T12:00:00.000000+00:00',
                 'reporting_timezone': 'UTC'}, {'hour': '2026-10-08T13:00+00:00'}]
        with self.assertRaises(ValueError):
            web._fill_gaps(rows, 'hour', hourly=True)

    @unittest.skipUnless(shutil.which('node'), 'Node required for actual chart script verification')
    def test_actual_trends_chart_script_displays_fold_offsets(self):
        rows = [{'hour': f'2026-11-01T01:00{offset}', 'total_kwh': value,
                 'bucket_utc': f'2026-11-01T0{value+4}:00:00.000000+00:00',
                 'reporting_timezone': 'America/New_York'}
                for value, offset in ((1, '-04:00'), (2, '-05:00'))]
        beginning = web.TRENDS_HTML.index('const trendData')
        script = web.TRENDS_HTML[beginning:web.TRENDS_HTML.index('</script>', beginning)]
        script = script.replace('{{ trend_json|tojson }}', '[]').replace('{{ hourly_json|tojson }}', json.dumps(rows))
        harness = '''const assert = require('node:assert/strict');
const charts = {};
global.document = {documentElement:{}, getElementById:id=>({id})};
global.getComputedStyle = ()=>({getPropertyValue:()=>''});
global.Chart = class {constructor(canvas,config){charts[canvas.id]=config;}};
''' + script + '''
assert.deepEqual(charts.hourlyChart.data.labels, ['01:00 -04:00','01:00 -05:00']);
assert.deepEqual(charts.hourlyChart.data.datasets[0].data, [1,2]);
'''
        path = self.root/'chart.cjs'
        path.write_text(harness)
        result = subprocess.run(['node', str(path)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_actual_process_host_timezones_do_not_change_query_results(self):
        self.convert([('2026-10-01T03:00:00Z', 'A', 'Main', 1, 20),
                      ('2026-10-01T04:00:00Z', 'A', 'Main', 2, 40)])
        script = '''import json, sys
from datetime import datetime, timezone
from unittest.mock import patch
import energy
original = energy._connect
now = datetime(2026, 10, 1, 4, tzinfo=timezone.utc)
with patch.object(energy, '_connect', side_effect=lambda: original(sys.argv[1],allow_utc_rehearsal=True,read_only=True)):
    print(json.dumps([energy.get_main_total(device_gid='A',now=now),
        energy.get_daily_data(device_gid='A',now=now),energy.get_hourly_data(device_gid='A',now=now),
        energy.get_month_comparison('A',now=now),energy.get_circuit_data('Main','year','A',now=now)],sort_keys=True))
'''
        outputs = []
        for index, zone in enumerate(('UTC', 'America/New_York', 'Asia/Tokyo')):
            result = subprocess.run([sys.executable, '-c', script, str(self.artifact)],
                                    capture_output=True, text=True,
                                    env={**os.environ, 'TZ': zone, 'DB_PATH': str(self.root/f'bootstrap-{index}.db')})
            self.assertEqual(result.returncode, 0, result.stderr)
            outputs.append(json.loads(result.stdout))
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(outputs[0], outputs[2])


if __name__ == '__main__':
    unittest.main()
