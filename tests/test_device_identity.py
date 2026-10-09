import io
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import energy
import web


def device(gid=551741, manufacturer='F2518A001AECE3348C9E94', name='Barn', channels=()):
    return SimpleNamespace(device_gid=gid, manufacturer_id=manufacturer,
                           device_name=name, time_zone='America/New_York', channels=list(channels))


class DeviceIdentityTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        database = patch.object(energy, 'DB_PATH', str(self.root / 'fixture.db'))
        database.start()
        self.addCleanup(database.stop)
        energy.ensure_table()
        self.client = web.app.test_client()

    def discover(self, *devices):
        return energy.get_devices_with_channels(Mock(get_devices=Mock(return_value=list(devices))))

    def upload(self, prefix='8C9E94', selected=None):
        data = {'file': (io.BytesIO(
            b'Time Bucket (America/New_York),Barn-Pump (kWhs)\n10/08/2026 12:00:00,3\n'
        ), f'{prefix}-Barn-1H.csv')}
        if selected is not None:
            data['device_gid'] = selected
        return self.client.post('/api/import-csv', data=data)

    def rows(self, table):
        conn = energy._connect()
        try:
            return [dict(row) for row in conn.execute(f'SELECT * FROM {table} ORDER BY 1')]
        finally:
            conn.close()

    def test_actual_route_resolves_export_suffix_to_cloud_device_and_scoped_history(self):
        self.discover(device())
        result = self.upload()
        self.assertEqual(result.status_code, 200, result.get_json())
        self.assertEqual(result.get_json()['device_gid'], '551741')
        self.assertEqual({row['device_gid'] for row in self.rows('readings')}, {'551741'})
        history = energy.get_circuit_history('Pump', '551741', now=datetime(2026, 10, 8, 13))
        self.assertEqual(history['windows'][0]['total_kwh'], 3)
        self.assertIsNone(energy.get_circuit_history('Pump', '8C9E94', now=datetime(2026, 10, 8, 13)))
        self.assertEqual(self.rows('csv_source_bindings')[0]['export_identity'], '8C9E94')
        batch = self.rows('csv_source_batches')[0]
        self.assertEqual(batch['original_filename'], '8C9E94-Barn-1H.csv')
        self.assertIn(b'12:00:00,3', batch['content'])

    def test_unknown_identity_rejects_without_writes(self):
        result = self.upload()
        self.assertEqual(result.status_code, 400)
        self.assertIn('select', result.get_json()['message'].lower())
        for table in ('readings', 'csv_source_batches', 'csv_source_bindings', 'reading_changes'):
            self.assertEqual(self.rows(table), [])

    def test_explicit_unknown_export_requires_a_registered_monitor_at_http_boundary(self):
        self.assertEqual(self.upload('UNKNOWN', 'invented').status_code, 400)
        self.discover(device())
        result = self.upload('UNKNOWN', '551741')
        self.assertEqual(result.status_code, 200, result.get_json())
        self.assertEqual(self.rows('csv_source_bindings')[0]['resolution'], 'operator_selected')
        self.assertEqual(self.upload('UNKNOWN').status_code, 400)  # no global alias invented

    def test_suffix_collision_is_not_resolved_by_display_name(self):
        self.discover(device(), device(999, 'DIFFERENT8C9E94'))
        self.assertEqual(self.upload().status_code, 400)
        self.assertEqual(self.upload(selected='551741').status_code, 200)
        self.assertEqual(self.upload(selected='unrelated').status_code, 400)
        self.assertEqual(len(self.rows('device_identities')), 2)

    def test_explicit_choice_cannot_contradict_unique_discovery(self):
        self.discover(device(), device(999, 'DIFFERENTABCDEF'))
        self.assertEqual(self.upload(selected='999').status_code, 400)
        self.assertEqual(self.rows('csv_source_batches'), [])

    def test_parent_nested_discovery_deduplicates_without_mutating_sdk_objects(self):
        main = SimpleNamespace(channel_num='1,2,3', name='Main')
        branches = [SimpleNamespace(channel_num=str(i), name=f'Circuit {i}') for i in range(1, 17)]
        parent = device(channels=[main])
        nested = device(manufacturer='SXF2518A001AECE3348C9E94', name='', channels=branches)
        for _ in range(2):
            gids, info = self.discover(parent, nested, nested)
            self.assertEqual(gids, [551741])
            self.assertEqual(len(info[551741].channels), 17)
            self.assertEqual(info[551741].device_name, 'Barn')
        self.assertEqual(len(parent.channels), 1)
        self.assertEqual(len(nested.channels), 16)
        self.assertEqual(self.upload().status_code, 200)
        self.assertEqual(len(self.rows('device_identities')), 1)

    def test_discovery_is_atomic_and_does_not_create_claims_from_names(self):
        with self.assertRaises(ValueError):
            self.discover(device(), device(gid=None))
        self.assertEqual(self.rows('device_identities'), [])
        self.discover(device(manufacturer='', name='8C9E94'))
        self.assertEqual(self.upload().status_code, 400)

    def test_previous_claims_are_not_erased_by_later_discovery(self):
        self.discover(device())
        self.discover(device(999, 'OTHER8C9E94'))
        self.assertEqual(self.upload().status_code, 400)
        self.assertEqual(len(self.rows('device_identities')), 2)

    def test_idempotence_and_immutable_binding(self):
        self.discover(device())
        self.assertEqual(self.upload().get_json()['imported'], 1)
        before = self.rows('csv_source_bindings')
        self.assertEqual(self.upload().get_json()['imported'], 0)
        self.assertEqual(self.rows('csv_source_bindings'), before)
        conn = energy._connect()
        try:
            for statement in ('DELETE FROM csv_source_bindings',
                              "UPDATE csv_source_bindings SET canonical_gid='999'"):
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(statement)
            conn.rollback()
        finally:
            conn.close()

    def test_publication_failure_rolls_back_source_binding_and_closes_connection(self):
        self.discover(device())
        with patch.object(energy, '_save_device_capabilities_with_conn', side_effect=RuntimeError('failure')):
            self.assertEqual(self.upload().status_code, 500)
        for table in ('readings', 'csv_source_batches', 'csv_source_bindings', 'reading_changes'):
            self.assertEqual(self.rows(table), [])

    def test_legacy_split_history_is_gated_not_aliased_or_rewritten(self):
        path = self.root / '8C9E94-Barn-1H.csv'
        path.write_text('Time Bucket (America/New_York),Barn-Pump (kWhs)\n10/08/2026 12:00:00,3\n')
        energy.import_emporia_csv(str(path), device_gid='8C9E94')
        before = self.rows('readings'), self.rows('csv_source_batches')
        self.discover(device())
        result = self.upload()
        self.assertEqual(result.status_code, 400)
        self.assertIn('reconciliation', result.get_json()['message'])
        self.assertEqual((self.rows('readings'), self.rows('csv_source_batches')), before)

    def test_import_selector_lists_only_registered_monitors_and_escapes_labels(self):
        self.discover(device(name='<script>private</script>'))
        html = self.client.get('/import').get_data(as_text=True)
        self.assertIn('id="import-device"', html)
        self.assertIn('value="551741"', html)
        self.assertNotIn('<script>private</script>', html)
        self.assertIn('&lt;script&gt;private&lt;/script&gt;', html)

    def test_canonical_identity_does_not_bypass_unknown_live_coverage_guard(self):
        self.discover(device())
        conn = energy._connect()
        try:
            conn.execute("INSERT INTO readings(timestamp,device_gid,channel_name,usage_kwh,cost_cents) VALUES ('2026-10-08T12:00:00','551741','Pump',0.05,1)")
            conn.commit()
        finally:
            conn.close()
        before = self.rows('readings'), self.rows('reading_changes')
        result = self.upload()
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.get_json()['imported'], 0)
        self.assertGreater(result.get_json()['warnings'], 0)
        self.assertEqual((self.rows('readings'), self.rows('reading_changes')), before)
        self.assertEqual(self.rows('csv_source_batches')[0]['device_gid'], '551741')

    def test_schema_upgrade_preserves_old_ids_values_and_journal(self):
        self.discover(device())
        self.upload()
        before = {table: self.rows(table) for table in ('readings', 'reading_changes', 'csv_source_batches', 'csv_source_observations', 'csv_source_bindings')}
        energy.ensure_table()
        self.assertEqual({table: self.rows(table) for table in before}, before)

    def test_other_export_prefix_does_not_bypass_old_split_history_gate(self):
        path = self.root / '8C9E94-Barn-1H.csv'
        path.write_text('Time Bucket (America/New_York),Barn-Pump (kWhs)\n10/08/2026 12:00:00,3\n')
        energy.import_emporia_csv(str(path), device_gid='8C9E94')
        self.discover(device())
        for prefix, selected in [('551741', None), ('UNKNOWN', '551741')]:
            result = self.upload(prefix, selected)
            self.assertEqual(result.status_code, 400)
            self.assertIn('reconciliation', result.get_json()['message'])

    def test_canonical_and_full_manufacturer_export_ids_resolve_without_name_matching(self):
        self.discover(device())
        for prefix in ('551741', 'F2518A001AECE3348C9E94', '8c9e94'):
            with self.subTest(prefix=prefix):
                result = self.upload(prefix)
                self.assertEqual(result.status_code, 200, result.get_json())
                self.assertEqual(result.get_json()['device_gid'], '551741')
        self.assertEqual(len(self.rows('readings')), 1)

    def test_discovery_claims_remain_immutable(self):
        self.discover(device())
        before = self.rows('device_identity_aliases')
        conn = energy._connect()
        try:
            for statement in ('DELETE FROM device_identity_aliases',
                              "UPDATE device_identity_aliases SET canonical_gid='999'"):
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(statement)
            conn.rollback()
        finally:
            conn.close()
        self.assertEqual(self.rows('device_identity_aliases'), before)


if __name__ == '__main__':
    unittest.main()
