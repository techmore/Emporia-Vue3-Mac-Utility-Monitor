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
from energy_clock import EnergyClock
from utc_migration import rehearse_utc_copy


class UtcLiveQueryTests(unittest.TestCase):
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

    def convert(self, rows, zone='America/New_York', minute=False, health=(), snapshots=False):
        connection = self.original_connect()
        try:
            with connection:
                connection.executemany(
                    '''INSERT INTO readings(timestamp,device_gid,channel_name,usage_kwh,cost_cents,
                       measurement_seconds,measurement_source) VALUES (?,?,?,?,?,?,?)''',
                    [(*row, 60 if minute else None, 'emporia_minute' if minute else None) for row in rows],
                )
                connection.executemany('INSERT INTO poller_health_events(timestamp,ok) VALUES (?,?)', health)
        finally:
            connection.close()
        if snapshots:
            energy.rebuild_latest_channel_snapshot()
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

    def test_wall_reference_changes_elapsed_offset_across_dst(self):
        now = datetime.fromisoformat('2026-03-09T01:30:00-04:00')
        prior = datetime.fromisoformat('2026-03-08T01:30:00-05:00')
        self.convert([(now.isoformat(), 'A', 'Main', 2, 40),
                      (prior.isoformat(), 'A', 'Main', 1, 20)])
        with self.queries():
            result = energy.get_now_vs_context(60, 'A', now=now)
        self.assertEqual(result['current_kwh'], 2)
        self.assertEqual(result['yesterday_kwh'], 1)
        self.assertEqual(result['window_ranges']['yesterday']['end'], '2026-03-08T06:30:00.000000+00:00')
        self.assertIsNone(result['vs_yesterday_pct'])
        self.assertFalse(result['comparison_sampled']['yesterday'])

    def test_gap_and_fold_references_are_unknown_not_guessed(self):
        for now, status in (
            ('2026-03-09T02:30:00-04:00', 'nonexistent'),
            ('2026-11-02T01:30:00-05:00', 'ambiguous'),
        ):
            clock = EnergyClock('America/New_York')
            moment = datetime.fromisoformat(now)
            self.assertEqual(clock.previous_wall_time(moment, 1), (None, status))
        self.convert([('2026-11-02T01:30:00-05:00', 'A', 'Main', 2, 40),
                      ('2026-11-01T01:30:00-04:00', 'A', 'Main', 1, 20),
                      ('2026-11-01T01:30:00-05:00', 'A', 'Main', 3, 60)])
        with self.queries():
            result = energy.get_now_vs_context(60, 'A', now=datetime.fromisoformat('2026-11-02T01:30:00-05:00'))
        self.assertEqual(result['context_status']['yesterday'], 'ambiguous')
        self.assertIsNone(result['yesterday_kwh'])
        self.assertIsNone(result['window_ranges']['yesterday'])
        self.assertIsNone(result['vs_yesterday_pct'])

    def test_current_previous_boundaries_do_not_double_count_and_missing_is_null(self):
        now = datetime(2026, 10, 8, 16, tzinfo=timezone.utc)
        rows = [(stamp.isoformat(), gid, channel, value, value*20)
                for stamp, value in ((now-timedelta(hours=1), 1), (now, 2),
                                     (now+timedelta(microseconds=1), 100))
                for gid in ('A', 'B') for channel in ('Main', 'Pump')]
        self.convert(rows)
        with self.queries():
            result = energy.get_now_vs_context(60, 'A', now=now)
            circuit = energy.get_channel_context('Pump', 60, 'A', now=now)
        self.assertEqual(result['current_kwh'], 2)
        self.assertEqual(result['sampled_minutes']['current'], 1)
        self.assertEqual(result['sampled_minutes']['previous'], 1)
        self.assertEqual(circuit['current_kwh'], 2)
        self.assertIsNone(result['yesterday_kwh'])
        self.assertEqual({row['kwh'] for row in result['circuits']}, {2})
        self.assertEqual({row['usage_kwh'] for row in result['latest']}, {2})
        self.assertTrue(all(row['timestamp'] <= EnergyClock.stamp(now) for row in result['latest']))

    def test_dense_equal_length_windows_allow_percentages_but_sparse_history_does_not(self):
        now = datetime(2026, 10, 8, 16, tzinfo=timezone.utc)
        rows = []
        for end, value in ((now, 0.002), (now-timedelta(hours=1), 0.001),
                           (now-timedelta(days=1), 0.001), (now-timedelta(days=7), 0.001),
                           (now-timedelta(days=30), 0.001)):
            rows += [((end-timedelta(minutes=i)).isoformat(), 'A', 'Main', value, value*20)
                     for i in range(60)]
        self.convert(rows)
        with self.queries():
            result = energy.get_now_vs_context(60, 'A', now=now)
        self.assertTrue(all(result['comparison_sampled'].values()))
        self.assertEqual(result['change_pct'], 100)
        self.assertEqual(result['vs_yesterday_pct'], 100)
        self.assertEqual(result['vs_last_week_pct'], 100)
        self.assertEqual(result['vs_last_month_pct'], 100)
        self.assertTrue(all(count == 60 for count in result['sampled_minutes'].values()))

    def test_multiwindow_comparison_uses_one_read_snapshot_during_concurrent_import(self):
        now = datetime(2026, 10, 8, 12)
        previous = (now-timedelta(hours=1)).isoformat()
        writer = self.original_connect()
        with writer:
            writer.executemany('INSERT INTO readings(timestamp,device_gid,channel_name,usage_kwh) VALUES (?,?,?,?)',
                               [(now.isoformat(), 'A', 'Main', 2), (previous, 'A', 'Main', 1)])
        reader = self.original_connect()
        changed = []

        def concurrent_import(sql):
            if 'SELECT SUM(usage_kwh) kwh' in sql and not changed:
                with writer:
                    writer.execute('UPDATE readings SET usage_kwh=999 WHERE timestamp=?', (previous,))
                changed.append(True)

        reader.set_trace_callback(concurrent_import)
        try:
            with patch.object(energy, '_connect', return_value=reader):
                result = energy.get_now_vs_context(60, 'A', now=now)
            self.assertEqual(changed, [True])
            self.assertEqual(result['current_kwh'], 2)
            self.assertEqual(result['previous_kwh'], 1)
            self.assertEqual(result['change_pct'], None)
            # Verify the context's previous window, not just its sample count.
            self.assertEqual(result['window_ranges']['previous']['end'], previous)
            self.assertEqual(writer.execute('SELECT usage_kwh FROM readings WHERE timestamp=?', (previous,)).fetchone()[0], 999)
        finally:
            writer.close()

    def test_nonexistent_yesterday_context_does_not_normalize_to_different_clock_hour(self):
        self.convert([('2026-03-09T02:30:00-04:00', 'A', 'Main', 2, 40),
                      ('2026-03-08T03:30:00-04:00', 'A', 'Main', 999, 999)])
        with self.queries():
            result = energy.get_now_vs_context(60, 'A', now=datetime.fromisoformat('2026-03-09T02:30:00-04:00'))
        self.assertIsNone(result['yesterday_kwh'])
        self.assertEqual(result['context_status']['yesterday'], 'nonexistent')

    def test_future_snapshot_cannot_mask_valid_reading_for_one_channel(self):
        now = datetime(2026, 10, 8, 16, tzinfo=timezone.utc)
        self.convert([((now-timedelta(seconds=30)).isoformat(), 'A', 'Main', 1, 20),
                      ((now+timedelta(days=1)).isoformat(), 'A', 'Main', 100, 2000),
                      (now.isoformat(), 'A', 'Pump', 2, 40)], snapshots=True)
        with self.queries():
            result = energy.get_now_vs_context(60, 'A', now=now)
        by_name = {row['channel_name']: row for row in result['latest']}
        self.assertEqual(by_name['Main']['usage_kwh'], 1)
        self.assertEqual(by_name['Pump']['usage_kwh'], 2)
        self.assertEqual(len(result['latest']), 2)

    def test_healthy_snapshot_fast_path_does_not_scan_whole_history(self):
        now = datetime(2026, 10, 8, 16, tzinfo=timezone.utc)
        self.convert([(now.isoformat(), 'A', 'Main', 1, 20)], snapshots=True)
        statements = []
        connection = self.original_connect(self.artifact, allow_utc_rehearsal=True, read_only=True)
        connection.set_trace_callback(statements.append)
        with patch.object(energy, '_connect', return_value=connection):
            result = energy.get_now_vs_context(60, 'A', now=now)
        self.assertEqual(result['latest'][0]['usage_kwh'], 1)
        self.assertFalse(any('ROW_NUMBER' in sql for sql in statements))

    def test_intraday_rows_keep_both_folds_zero_missing_and_independent_labels(self):
        self.convert([('2026-11-01T01:10:00-04:00', 'A', 'Main', 0, 0),
                      ('2026-11-01T01:10:00-05:00', 'A', 'Main', 2, 40),
                      ('2026-11-02T01:10:00-05:00', 'A', 'Main', 3, 60),
                      ('2026-11-02T01:10:00-05:00', 'B', 'Main', 999, 999),
                      ('2026-11-02T02:00:00.000001-05:00', 'A', 'Main', 100, 2000)])
        now = datetime.fromisoformat('2026-11-02T02:00:00-05:00')
        with self.queries():
            result = energy.get_intraday_comparison('A', now=now)
        self.assertEqual(len(result['today']), 24)
        self.assertEqual(len(result['yesterday']), 25)
        self.assertEqual(result['yesterday_labels'][1:3], ['1a -0400', '1a -0500'])
        self.assertEqual(result['yesterday'][1:3], [0, 2])
        self.assertIsNone(result['yesterday'][0])
        self.assertEqual(result['today'][1], 3)
        self.assertTrue(all(value is None for value in result['today'][2:]))
        self.assertEqual(len(set(result['yesterday_hours'])), 25)
        self.assertEqual(hashlib.sha256(self.artifact.read_bytes()).hexdigest(), self.receipt['artifact_sha256'])

    def test_spring_intraday_has_no_fabricated_gap_hour(self):
        self.convert([('2026-03-08T01:10:00-05:00', 'A', 'Main', 1, 20),
                      ('2026-03-08T03:10:00-04:00', 'A', 'Main', 2, 40)])
        with self.queries():
            result = energy.get_intraday_comparison('A', now=datetime.fromisoformat('2026-03-08T04:00:00-04:00'))
        self.assertEqual(len(result['today']), 23)
        self.assertEqual(result['today_labels'][1:3], ['1a', '3a'])
        self.assertEqual(result['today'][1:3], [1, 2])
        self.assertEqual(len(result['yesterday']), 24)

    def test_capture_folds_minutes_health_and_completed_hour_cutoff(self):
        now = datetime(2026, 11, 1, 7, 30, tzinfo=timezone.utc)
        first, second = now.replace(hour=5, minute=0), now.replace(hour=6, minute=0)
        rows = [((end+timedelta(minutes=i)).isoformat(), 'A', 'Main', 0, 0)
                for end, count in ((first, 57), (second, 60)) for i in range(count)]
        rows += [(first.isoformat().replace('00:00+00:00', '00:20+00:00'), 'A', 'Main', 0, 0),
                 ('2026-11-01T07:00:00Z', 'A', 'Main', 100, 2000),
                 (second.isoformat(), 'B', 'Main', 1, 20)]
        self.convert(rows, health=[(first.isoformat(), 1), (second.isoformat(), 0), ('2026-11-01T07:00:00Z', 0)])
        with self.queries():
            capture = energy.get_capture_history(48, 'A', now)
        self.assertEqual(len(capture), 48)
        self.assertEqual([row['minutes'] for row in capture[-2:]], [57, 60])
        self.assertEqual([row['hour'] for row in capture[-2:]], ['2026-11-01T01:00-04:00', '2026-11-01T01:00-05:00'])
        self.assertEqual([row['health_reports'] for row in capture[-2:]], [1, 1])
        self.assertEqual([row['reported_errors'] for row in capture[-2:]], [0, 1])
        self.assertTrue(all(row['state'] == 'dense' for row in capture[-2:]))
        self.assertEqual(sum(row['expected_minutes'] for row in capture), 48*60)

    def test_half_hour_capture_uses_actual_partial_denominators(self):
        now = datetime.fromisoformat('2026-10-05T01:10:00+11:00')
        clock = EnergyClock('Australia/Lord_Howe')
        end = clock.parse(clock.hour_key(clock.stamp(now)))
        start = end-timedelta(hours=48)
        self.convert([((start+timedelta(minutes=i)).isoformat(), 'A', 'Main', 0, 0)
                      for i in range(48*60)], 'Australia/Lord_Howe')
        with self.queries():
            capture = energy.get_capture_history(48, 'A', now)
            solar_capture = energy.get_capture_history(24, 'A', now)
        self.assertEqual(sum(row['expected_minutes'] for row in capture), 48*60)
        self.assertTrue(all(row['coverage_pct'] == 100 for row in capture))
        partial = [row for row in capture if row['expected_minutes'] == 30]
        self.assertEqual(len(partial), 2)
        self.assertTrue(capture[0]['partial_bucket'])
        section = web.LOG_HTML.split('  <div class="section-head"', 1)[0]
        with web.app.app_context():
            markup = web.render_template_string(section, capture_history=[(48, capture)])
        self.assertIn('30/30 minutes (100%)', markup)
        self.assertIn(f'{len(capture)} capture intervals', markup)
        self.assertEqual(sum(row['expected_minutes'] for row in solar_capture), 24*60)
        self.assertEqual(sum(row['expected_minutes']/60 for row in solar_capture if row['state'] == 'dense'), 24)

    def test_peak_hour_day_and_label_use_reporting_clock_and_reject_future(self):
        self.convert([('2026-10-09T03:30:00Z', 'A', 'Main', 0.01, 0.2),
                      ('2026-10-09T03:30:00Z', 'A', 'Pump', 0.01, 0.2),
                      ('2026-10-09T04:00:00.000001Z', 'A', 'Pump', 100, 2000),
                      ('2026-10-09T04:00:00.000001Z', 'A', 'Main', 100, 2000),
                      ('2026-10-09T03:30:00Z', 'B', 'Main', 999, 999)], minute=True)
        now = datetime(2026, 10, 9, 4, tzinfo=timezone.utc)
        with self.queries():
            peak = energy.get_peak_usage('A', now=now)
            instant = energy.get_peak_24h('A', now=now)
        self.assertEqual(peak['peak_hours'], [{'hour': '23', 'avg_kwh': 0.01}])
        self.assertEqual(peak['peak_days'][0]['day'], 'Thursday')
        self.assertEqual(instant, {'peak_watts': 600, 'peak_time': '11 PM (10/08) -0400', 'measurement_seconds': 60})

    def test_same_named_circuit_context_is_device_scoped_and_route_uses_shared_query(self):
        now = datetime(2026, 10, 8, 16, tzinfo=timezone.utc)
        self.convert([(now.isoformat(), 'A', 'Pump', 1, 20), (now.isoformat(), 'B', 'Pump', 999, 999)])
        with self.queries():
            actual = energy.get_channel_context('Pump', 60, 'A', now=now)
        self.assertEqual(actual['current_kwh'], 1)
        com = {'active_device_gid': 'A'}
        with patch.object(web, '_common', return_value=com), patch.object(web, '_render', return_value='ok'), \
             patch.object(energy, 'get_circuit_data', return_value={'data': [], 'total': {}}) as chart, \
             patch.object(energy, 'get_channel_context', return_value=actual) as context:
            response = web.app.test_client().get('/circuit/Pump/hour')
        self.assertEqual(response.status_code, 200)
        chart.assert_called_once_with('Pump', 'hour', 'A')
        context.assert_called_once_with('Pump', 60, 'A')

    def test_log_retains_canonical_timestamp_and_displays_local_fold_offset(self):
        self.convert([('2026-11-01T01:10:00-04:00', 'A', 'Main', 1, 20),
                      ('2026-11-01T01:10:00-05:00', 'A', 'Main', 2, 40)])
        with self.queries():
            entries = energy.get_log_entries()
        self.assertEqual([row['local_timestamp'] for row in entries], [
            '2026-11-01T01:10:00-05:00', '2026-11-01T01:10:00-04:00'])
        self.assertEqual(entries[0]['timestamp'], '2026-11-01T06:10:00.000000+00:00')
        with web.app.app_context():
            markup = web.render_template_string(web.LOG_HTML, capture_history=[], entries=entries)
        self.assertIn('2026-11-01T01:10:00-05:00', markup)
        self.assertIn('2026-11-01T01:10:00-04:00', markup)

    def test_real_flask_read_routes_render_against_read_only_utc_fixture(self):
        instant = datetime(2026, 11, 2, 7, tzinfo=timezone.utc)

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return instant.astimezone(tz) if tz is not None else instant.astimezone().replace(tzinfo=None)

        self.convert([('2026-11-01T01:10:00-04:00', 'A', 'Main', 0.001, 0.02),
                      ('2026-11-01T01:10:00-05:00', 'A', 'Main', 0.002, 0.04),
                      ('2026-11-02T06:59:30Z', 'A', 'Main', 0.01, 0.2),
                      ('2026-11-02T06:59:30Z', 'A', 'Pump', 0.005, 0.1)], snapshots=True, minute=True)
        heartbeat = {'timestamp': EnergyClock.stamp(instant), 'ok': True, 'error': None}
        with self.queries(), patch.object(energy, 'datetime', Clock), patch.object(web, 'datetime', Clock), \
             patch.object(energy, 'read_poller_status', return_value=heartbeat):
            client = web.app.test_client()
            for route in ('/', '/reports', '/trends', '/circuit/Pump', '/log', '/api/menu-summary'):
                with self.subTest(route=route):
                    response = client.get(route)
                    self.assertEqual(response.status_code, 200)
                    if route == '/api/menu-summary':
                        self.assertTrue(response.json['online'])
                        self.assertEqual(response.json['current_watts'], 600)
        self.assertEqual(hashlib.sha256(self.artifact.read_bytes()).hexdigest(), self.receipt['artifact_sha256'])

    def test_empty_history_zero_and_invalid_clock_close_connection(self):
        now = datetime(2026, 10, 8, 16, tzinfo=timezone.utc)
        self.convert([(now.isoformat(), 'A', 'Main', 0, 0)])
        with self.queries():
            zero = energy.get_now_vs_context(60, 'A', now=now)
            missing = energy.get_now_vs_context(60, 'missing', now=now)
        self.assertEqual(zero['current_kwh'], 0)
        self.assertIsNone(missing['current_kwh'])
        self.assertEqual(missing['latest'], [])
        for query in (energy.get_now_vs_context, energy.get_peak_usage, energy.get_peak_24h,
                      energy.get_intraday_comparison, lambda **kw: energy.get_capture_history(48, **kw),
                      lambda **kw: energy.get_channel_context('Main', **kw)):
            connection = self.original_connect(self.artifact, allow_utc_rehearsal=True, read_only=True)
            with patch.object(energy, '_connect', return_value=connection), self.assertRaises(ValueError):
                query(device_gid='A', now=now.replace(tzinfo=None))
            with self.assertRaises(energy.sqlite3.ProgrammingError):
                connection.execute('SELECT 1')

    def test_invalid_context_windows_and_prior_dates_are_rejected(self):
        for value in (True, 0, -1, 525601, '60', None, float('nan')):
            with self.subTest(value=value), patch.object(energy, '_connect') as connect, self.assertRaises(ValueError):
                energy.get_now_vs_context(value)
            connect.assert_not_called()
        for value in (True, 0, -1, '1'):
            with self.assertRaises(ValueError):
                EnergyClock('UTC').previous_wall_time(datetime.now(timezone.utc), value)

    @unittest.skipUnless(shutil.which('node'), 'Node required for actual banner chart script verification')
    def test_actual_banner_chart_script_uses_each_day_labels(self):
        start = web.DASH_HTML.index('const daily  =')
        script = web.DASH_HTML[start:web.DASH_HTML.index('</script>', start)]
        payload = {'labels': ['12a'], 'today_labels': ['12a', '1a'], 'today': [0, None],
                   'yesterday_labels': ['12a', '1a -0400', '1a -0500'], 'yesterday': [None, 1, 2]}
        from jinja2 import Template
        script = Template(script).render(daily_json=[], hourly_json=[], intraday_comparison=payload)
        harness = '''const assert = require('node:assert/strict');
const charts = {};
global.document = {documentElement:{},getElementById:id=>id.endsWith('RowChart')?{id}:null};
global.getComputedStyle = ()=>({getPropertyValue:()=>''});
global.Chart = class {constructor(canvas,config){charts[canvas.id]=config;}};
global.setTimeout = ()=>{};
''' + script + '''
assert.deepEqual(charts.todayRowChart.data.labels,['12a','1a']);
assert.deepEqual(charts.yesterdayRowChart.data.labels,['12a','1a -0400','1a -0500']);
assert.deepEqual(charts.todayRowChart.data.datasets[0].data,[0,null]);
'''
        path = self.root/'banner.cjs'
        path.write_text(harness)
        result = subprocess.run(['node', str(path)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_host_timezones_cannot_change_live_query_results(self):
        self.convert([('2026-11-01T01:10:00-04:00', 'A', 'Main', 1, 20),
                      ('2026-11-01T01:10:00-05:00', 'A', 'Main', 2, 40)])
        script = '''import json,sys
from datetime import datetime,timezone
from unittest.mock import patch
import energy
original=energy._connect
now=datetime(2026,11,1,8,tzinfo=timezone.utc)
with patch.object(energy,'_connect',side_effect=lambda:original(sys.argv[1],allow_utc_rehearsal=True,read_only=True)):
    print(json.dumps([energy.get_now_vs_context(60,'A',now=now),
        energy.get_intraday_comparison('A',now=now),energy.get_peak_usage('A',now=now),
        energy.get_capture_history(48,'A',now)],sort_keys=True))
'''
        outputs = []
        for index, zone in enumerate(('UTC', 'America/New_York', 'Asia/Tokyo')):
            result = subprocess.run([sys.executable, '-c', script, str(self.artifact)], capture_output=True, text=True,
                                    env={**os.environ, 'TZ': zone, 'DB_PATH': str(self.root/f'bootstrap-{index}.db')})
            self.assertEqual(result.returncode, 0, result.stderr)
            outputs.append(json.loads(result.stdout))
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(outputs[0], outputs[2])


if __name__ == '__main__':
    unittest.main()
