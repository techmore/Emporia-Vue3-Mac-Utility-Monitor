from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import energy
import web


class CircuitHistoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.original_db = energy.DB_PATH
        energy.DB_PATH = str(Path(self.directory.name) / 'history.db')
        energy.ensure_table()
        self.now = datetime(2026, 10, 4, 12, 0)

    def tearDown(self):
        energy.DB_PATH = self.original_db
        self.directory.cleanup()

    def seed(self, rows):
        conn = energy._connect()
        try:
            conn.executemany('INSERT INTO readings (timestamp,device_gid,channel_name,usage_kwh,cost_cents) VALUES (?,?,?,?,?)', rows)
            conn.commit()
        finally:
            conn.close()

    def test_windows_are_rolling_device_scoped_and_exclude_future_data(self):
        name = 'Pump / <test> & 20%'
        self.seed([( (self.now - timedelta(days=days)).isoformat(), gid, name, kwh, kwh*10)
                   for days,gid,kwh in [(0.01,'A',1),(2,'A',2),(10,'A',4),(40,'A',8),(-1,'A',100),(0.01,'B',500)]])
        result = energy.get_circuit_history(name, 'A', self.now)
        self.assertEqual([r['total_kwh'] for r in result['windows']], [1,3,7])
        self.assertEqual([r['total_cents'] for r in result['windows']], [10,30,70])
        self.assertTrue(all(r['change_pct'] is None for r in result['windows']))
        self.assertEqual(result['device_gid'], 'A')

    def test_missing_buckets_are_null_but_recorded_zero_is_zero(self):
        self.seed([((self.now-timedelta(minutes=1)).isoformat(),'A','Idle',0,0)])
        result = energy.get_circuit_history('Idle','A',self.now)
        series=result['windows'][0]['series']
        self.assertEqual(series[-1]['total_kwh'],0)
        self.assertTrue(any(r['total_kwh'] is None for r in series))
        self.assertEqual(result['live_watts'],0)
        self.assertEqual(result['windows'][1]['readings'],1)

    def test_comparable_minute_history_produces_trend(self):
        rows=[]
        for minute in range(1,2881):
            value=0.002 if minute <=1440 else 0.001
            rows.append(((self.now-timedelta(minutes=minute)).isoformat(),'A','Pump',value,value*10))
        self.seed(rows)
        result=energy.get_circuit_history('Pump','A',self.now)
        self.assertAlmostEqual(result['windows'][0]['change_pct'],100)
        self.assertIsNone(result['windows'][1]['change_pct'])

    def test_stale_history_does_not_show_live_power(self):
        self.seed([((self.now-timedelta(days=40)).isoformat(),'A','Old',1,10)])
        result=energy.get_circuit_history('Old','A',self.now)
        self.assertIsNone(result['live_watts'])
        self.assertTrue(all(r['total_kwh'] is None for r in result['windows']))
        self.assertIsNone(energy.get_circuit_history('Missing','A',self.now))

    def test_api_preserves_encoded_names_and_returns_not_found(self):
        name='Pump / & 20%'
        with patch.object(energy,'get_circuit_history',return_value={'channel_name':name}) as query:
            response=web.app.test_client().get('/api/circuit-history/Pump%20%2F%20%26%2020%25')
            self.assertEqual(response.status_code,200)
            query.assert_called_once_with(name)
        with patch.object(energy,'get_circuit_history',return_value=None):
            self.assertEqual(web.app.test_client().get('/api/circuit-history/missing').status_code,404)

    def test_overlay_assets_are_served_and_dialog_is_in_page(self):
        client=web.app.test_client()
        for path in ('/','/circuits'):
            response=client.get(path)
            self.assertEqual(response.status_code,200)
            self.assertIn(b'aria-labelledby="circuit-history-title"',response.data)
        with client.get('/static/circuit-overlay.js') as response:
            self.assertEqual(response.status_code,200)
