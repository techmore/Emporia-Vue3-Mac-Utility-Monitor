import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import energy
import web
from panel_model import breaker_load


class PowerConsumerTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node required for dashboard event regression')
    def test_actual_dashboard_event_script_keeps_unknown_distinct_from_zero(self):
        start = web.DASH_HTML.rfind('  (function(){', 0, web.DASH_HTML.index("const views = ['bars'"))
        script = web.DASH_HTML[start:web.DASH_HTML.index('</script>', start)]
        harness = '''const assert = require('node:assert/strict');
const elements = new Map();
const node = id => {if(!elements.has(id)) elements.set(id, {
  textContent:'', innerHTML:'', style:{}, classList:{toggle(){}}, setAttribute(){}
}); return elements.get(id);};
global.document = {getElementById:node,querySelector:node,querySelectorAll:()=>[]};
global.localStorage = {getItem:()=>null,setItem(){}};
global.window = {subscribeEnergyEvents:fn=>global.update=fn};
global.escapeHTML = text=>text;
''' + script + '''
for (const [watts, fresh, expected] of [[null,false,'—'],[0,true,'0.0'],
  [1200,true,'1200.0'],[1200,false,'—'],[null,true,'—']]) {
  update({dashboard:{current_watts:watts,reading_fresh:fresh,
    cost_per_hour:watts==null?null:watts/10000,
    top_circuits:[{channel_name:'Pump',watts}]}});
  assert.equal(node('live-current-watts').textContent,expected);
  assert.equal(node('live-cost-per-hour').textContent,watts==null?'—':(watts/10000).toFixed(2));
  assert.ok(node('live-top-circuits-list').innerHTML.includes(watts==null?'Power unavailable':watts+' W now'));
}
'''
        path = self.root / 'dashboard.cjs'
        path.write_text(harness)
        result = subprocess.run(['node', str(path)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        database = patch.object(energy, 'DB_PATH', str(self.root / 'energy.db'))
        database.start()
        self.addCleanup(database.stop)
        energy.ensure_table()
        self.now = datetime.now()
        cache = patch.object(web, '_dashboard_cache', {
            'built_at': 0, 'common': None, 'context': None,
            'active_device_gid': None, 'latest_timestamp': None,
        })
        cache.start()
        self.addCleanup(cache.stop)
        status = patch.object(web, '_poller_status_snapshot', return_value={'ok': True, 'poller_running': True})
        status.start()
        self.addCleanup(status.stop)
        conn = energy._connect()
        conn.execute("INSERT INTO circuit_labels(slot,channel_name,label,amps,poles) VALUES (1,'Pump','Pump',15,1)")
        conn.commit()
        conn.close()

    def seed(self, name='Pump', kwh=.02, seconds=None, source=None, stamp=None, provider=None):
        conn = energy._connect()
        conn.execute('''INSERT INTO readings(timestamp,device_gid,channel_name,usage_kwh,cost_cents,
                       measurement_seconds,measurement_source,provider_timestamp)
                       VALUES (?,?,?,?,?,?,?,?)''',
                     (stamp or (self.now - timedelta(seconds=30)).isoformat(), 'A', name,
                      kwh, kwh * energy.RATE_CENTS, seconds, source, provider))
        conn.commit()
        conn.close()
        energy.rebuild_latest_channel_snapshot()

    def menu(self):
        response = web.app.test_client().get('/api/menu-summary')
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def test_unknown_duration_menu_keeps_power_and_load_unavailable(self):
        self.seed('Main')
        self.seed()
        payload = self.menu()
        self.assertIsNone(payload['current_watts'])
        self.assertIsNone(payload['cost_per_hour'])
        slot = next(row for row in payload['breaker_slots'] if row['channel_name'] == 'Pump')
        self.assertIsNone(slot['watts'])
        self.assertIsNone(slot['load_percent'])
        self.assertIsNone(slot['load_state'])
        self.assertFalse(slot['is_peak'])

    def test_verified_minute_menu_preserves_real_zero_and_load(self):
        self.seed('Main', 0, 60, 'emporia_minute')
        self.seed(kwh=.02, seconds=60, source='emporia_minute')
        payload = self.menu()
        self.assertEqual(payload['current_watts'], 0)
        self.assertEqual(payload['cost_per_hour'], 0)
        slot = next(row for row in payload['breaker_slots'] if row['channel_name'] == 'Pump')
        self.assertEqual(slot['watts'], 1200)
        self.assertAlmostEqual(slot['load_percent'], 1200 / 120 / 15 * 100)

    def test_fresh_hourly_import_is_interval_average_not_live_power(self):
        stamp = self.now.replace(second=0, microsecond=0).strftime('%m/%d/%Y %H:%M:%S')
        path = self.root / 'A-Panel-1H.csv'
        path.write_text(f'Time Bucket,Panel-Main (kWhs),Panel-Pump (kWhs)\n{stamp},3,3\n')
        energy.import_emporia_csv(str(path))
        self.assertIsNone(self.menu()['current_watts'])
        history = energy.get_circuit_history('Pump', 'A', self.now)
        self.assertIsNone(history['live_watts'])
        self.assertEqual(history['latest_average_watts'], 3000)
        self.assertEqual(history['measurement_seconds'], 3600)
        peak = energy.get_peak_24h('A', now=self.now)
        self.assertEqual(peak['peak_watts'], 3000)
        self.assertEqual(peak['measurement_seconds'], 3600)

    def test_unknown_and_compacted_history_cannot_invent_peak_power(self):
        self.seed(kwh=3)
        self.seed(name='Other', kwh=100, source='compacted', stamp=(self.now - timedelta(hours=1)).isoformat())
        peak = energy.get_peak_24h('A', now=self.now)
        self.assertIsNone(peak['peak_watts'])
        self.assertIsNone(peak['peak_time'])
        self.assertIsNone(energy.get_circuit_history('Pump', 'A', self.now)['live_watts'])

    def test_mixed_duration_peak_cohort_is_not_summed_as_simultaneous_load(self):
        self.seed(kwh=.02, seconds=60, source='csv_energy')
        self.seed('Other', 3, 3600, 'csv_energy')
        self.assertIsNone(energy.get_peak_24h('A', now=self.now)['peak_watts'])

    def test_stale_provider_timestamp_cannot_become_live_via_fresh_receipt(self):
        provider = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        self.seed('Main', .02, 60, 'emporia_minute', provider=provider)
        self.seed(seconds=60, source='emporia_minute', provider=provider)
        self.assertIsNone(self.menu()['current_watts'])
        self.assertIsNone(energy.get_circuit_history('Pump', 'A', self.now)['live_watts'])

    def test_unknown_power_renders_every_power_page_without_zero_fallback(self):
        self.seed('Main')
        self.seed()
        client = web.app.test_client()
        for route in ('/', '/circuits', '/reports', '/trends', '/circuit/Pump'):
            with self.subTest(route=route):
                response = client.get(route)
                self.assertEqual(response.status_code, 200)
                self.assertNotIn('180000 W', response.get_data(as_text=True))
        body = client.get('/circuits').get_data(as_text=True)
        self.assertIn('Power unavailable', body)
        self.assertNotIn('No breakers are near the 80% line.', body)
        payload = client.get('/api/live-dashboard').get_json()
        self.assertIsNone(payload['current_watts'])
        self.assertIsNone(payload['cost_per_hour'])
        self.assertFalse(payload['reading_fresh'])

    def test_recorded_main_zero_does_not_fall_back_to_nonzero_legs(self):
        row = dict(usage_kwh=0, measurement_seconds=60, measurement_source='emporia_minute',
                   timestamp=self.now.isoformat())
        total, legs = web._build_mains_cards({'Main': row, 'Mains_A': {**row, 'usage_kwh': .02}}, 24, 'A')
        self.assertEqual(total['watts'], 0)
        self.assertEqual(legs[0]['watts'], 1200)

    def test_missing_native_leg_does_not_invent_total_or_balance(self):
        self.seed('Mains_A', .02, 60, 'emporia_minute')
        total, _ = web._build_mains_cards({'Mains_A': energy.get_latest('A')[0]}, 24, 'A')
        self.assertIsNone(total['watts'])

    def test_inferred_legs_require_all_monitored_circuit_power(self):
        row = dict(usage_kwh=.02, measurement_seconds=60, measurement_source='emporia_minute',
                   timestamp=self.now.isoformat())
        layout = {1: {'channel_name': 'Pump', 'poles': 1}, 2: {'channel_name': 'Missing', 'poles': 1}}
        a, b, found = web._infer_live_leg_watts({'Pump': row}, layout)
        self.assertFalse(found)
        self.assertIsNone(a)
        self.assertIsNone(b)

    def test_breaker_unknown_power_and_negative_export_are_not_assumed_safe(self):
        unknown = breaker_load(None, 15, 1)
        self.assertFalse(unknown['power_known'])
        self.assertEqual(unknown['load_label'], 'Power unavailable')
        exported = breaker_load(-1800, 15, 1)
        self.assertEqual(exported['safe_cls'], 'danger')
        self.assertEqual(exported['load_label'], '15.0/15A')

    def test_live_power_requires_valid_receipt_and_preserves_skew_limits(self):
        now = datetime.now(timezone.utc)
        base = dict(usage_kwh=.02, measurement_seconds=60, measurement_source='emporia_minute')
        for age, expected in ((30, 1200), (180, None), (-60.000001, None), (-30, 1200)):
            row = {**base, 'timestamp': (now - timedelta(seconds=age)).isoformat()}
            self.assertEqual(energy.reading_live_watts(row, now=now), expected)
        for stamp in (None, 'invalid', now.date().isoformat()):
            self.assertIsNone(energy.reading_live_watts({**base, 'timestamp': stamp}, now=now))

    def test_aligned_native_legs_can_supply_total_but_three_phase_is_complete(self):
        row = dict(usage_kwh=.02, measurement_seconds=60, measurement_source='emporia_minute',
                   timestamp=self.now.isoformat())
        total, _ = web._build_mains_cards({'Mains_A': row, 'Mains_B': row}, 24, 'A')
        self.assertEqual(total['watts'], 2400)
        energy.save_device_capabilities('A', service_mode='three_phase_native', has_main=False,
                                        has_mains_a=True, has_mains_b=True, has_mains_c=True,
                                        mains_c_no_ct=False, source='csv_import')
        total, _ = web._build_mains_cards({'Mains_A': row, 'Mains_B': row}, 24, 'A')
        self.assertIsNone(total['watts'])
        total, _ = web._build_mains_cards({'Mains_A': row, 'Mains_B': row, 'Mains_C': row}, 24, 'A')
        self.assertEqual(total['watts'], 3600)

    def test_peak_preserves_real_zero_and_rejects_distinct_provider_instants(self):
        self.seed(kwh=0, seconds=60, source='emporia_minute')
        self.assertEqual(energy.get_peak_24h('A', now=self.now)['peak_watts'], 0)
        provider = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
        self.seed('Other', .02, 60, 'emporia_minute', provider=provider)
        self.assertIsNone(energy.get_peak_24h('A', now=self.now)['peak_watts'])

    def test_unknown_stale_and_hourly_readings_are_not_standby(self):
        base = dict(usage_kwh=.0005, timestamp=self.now.isoformat())
        readings = {'Unknown': base, 'Hourly': {**base, 'measurement_seconds': 3600, 'measurement_source': 'csv_energy'},
                    'Stale': {**base, 'measurement_seconds': 60, 'measurement_source': 'emporia_minute',
                              'timestamp': (self.now - timedelta(hours=1)).isoformat()},
                    'Live': {**base, 'measurement_seconds': 60, 'measurement_source': 'emporia_minute'}}
        self.assertEqual(web._standby_circuits(readings), [{'name': 'Live', 'watts': 30}])
