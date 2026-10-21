import io
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import device_identity
import energy
import web

CLOUD_GID = '551741'
MANUFACTURER = 'XQ7T-0000-8C9E94'   # synthetic; ends with the export prefix
PREFIX = '8C9E94'


def device(gid, manufacturer=None, channels=()):
    return SimpleNamespace(device_gid=gid, manufacturer_id=manufacturer, channels=list(channels))


class IdentityBase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        database = patch.object(energy, 'DB_PATH', str(self.root / 'energy.db'))
        database.start()
        self.addCleanup(database.stop)
        settings = patch.object(energy, '_load_settings', return_value={})
        settings.start()
        self.addCleanup(settings.stop)
        energy.ensure_table()

    def discover(self, *devices):
        energy._register_discovered_identity(list(devices))

    def csv(self, prefix=PREFIX, stamp='10/01/2026 12:00:00', value=3, name='Pump'):
        path = self.root / f'{prefix}-Barn-1H.csv'
        path.write_text(f'Time Bucket,{name} (kWhs)\n{stamp},{value}\n')
        return str(path), path.name

    def import_csv(self, prefix=PREFIX, device_gid=None, **kw):
        path, name = self.csv(prefix, **kw)
        return energy.import_emporia_csv(path, device_gid=device_gid, original_filename=name)

    def rows(self, table='readings', order='1'):
        conn = energy._connect()
        try:
            return [tuple(r) for r in conn.execute(f'SELECT * FROM {table} ORDER BY {order}')]
        finally:
            conn.close()

    def seed_live(self, gid, stamp, value=0.05, name='Pump'):
        conn = energy._connect()
        with conn:
            cursor = conn.execute(
                'INSERT INTO readings(timestamp,device_gid,channel_name,usage_kwh,cost_cents) VALUES (?,?,?,?,?)',
                (stamp, gid, name, value, value * 10))
            conn.execute("INSERT INTO reading_provenance(reading_id,source,interval_seconds,source_unit) "
                         "VALUES (?, 'live_poll', 60, 'kWh')", (cursor.lastrowid,))
        conn.close()


class DiscoveryRegistryTests(IdentityBase):
    def test_parent_and_nested_objects_for_one_gid_merge_into_one_monitor(self):
        main = device(int(CLOUD_GID), MANUFACTURER, channels=['Main'])
        nested = device(int(CLOUD_GID), 'NESTED-' + PREFIX, channels=[f'C{i}' for i in range(1, 17)])
        self.discover(main, nested)
        self.discover(main, nested)  # idempotent
        conn = energy._connect()
        try:
            self.assertEqual(device_identity.known_cloud_gids(conn), [CLOUD_GID])
            aliases = [r[0] for r in conn.execute("SELECT alias FROM device_aliases ORDER BY alias")]
        finally:
            conn.close()
        self.assertEqual(aliases, [('NESTED-' + PREFIX), MANUFACTURER])

    def test_get_devices_with_channels_keeps_every_channel_and_registers_identity(self):
        main = device(int(CLOUD_GID), MANUFACTURER, channels=['Main'])
        nested = device(int(CLOUD_GID), 'NESTED-' + PREFIX, channels=[f'C{i}' for i in range(16)])
        vue = SimpleNamespace(get_devices=lambda: [main, nested])
        gids, info = energy.get_devices_with_channels(vue)
        self.assertEqual(gids, [int(CLOUD_GID)])
        self.assertEqual(len(info[int(CLOUD_GID)].channels), 17)
        self.assertEqual([m['device_gid'] for m in energy.get_registered_monitors()], [CLOUD_GID])

    def test_polled_cloud_gid_is_registered_with_its_readings(self):
        usage = {int(CLOUD_GID): SimpleNamespace(channels={1: SimpleNamespace(name='Main', usage=0.1)})}
        vue = SimpleNamespace(get_device_list_usage=lambda **kw: usage)
        energy.poll_and_store(vue, [int(CLOUD_GID)])
        self.assertEqual([m['device_gid'] for m in energy.get_registered_monitors()], [CLOUD_GID])
        self.assertEqual(energy.get_known_devices(), [CLOUD_GID])

    def test_registration_failure_never_blocks_discovery(self):
        vue = SimpleNamespace(get_devices=lambda: [device(int(CLOUD_GID), MANUFACTURER)])
        with patch.object(device_identity, 'register_discovered_devices', side_effect=RuntimeError('boom')):
            gids, _ = energy.get_devices_with_channels(vue)
        self.assertEqual(gids, [int(CLOUD_GID)])

    def test_manufacturer_id_collision_keeps_first_binding(self):
        self.discover(device(1, 'SHARED-ID'))
        self.discover(device(2, 'SHARED-ID'))
        conn = energy._connect()
        try:
            self.assertEqual([tuple(r) for r in conn.execute("SELECT alias, cloud_gid FROM device_aliases")],
                             [('SHARED-ID', '1')])
        finally:
            conn.close()

    def test_existing_database_seeds_only_live_verified_gids(self):
        self.seed_live('777', '2026-10-01T12:00:00')
        conn = energy._connect()
        with conn:
            cursor = conn.execute("INSERT INTO readings(timestamp,device_gid,channel_name,usage_kwh,cost_cents) "
                                  "VALUES ('2026-10-01T12:00:00','8C9E94','Pump',1,1)")
            conn.execute("INSERT INTO reading_provenance(reading_id,source,interval_seconds,source_unit) "
                         "VALUES (?, 'csv_import', 3600, 'kWh')", (cursor.lastrowid,))
            conn.execute("DELETE FROM device_registry")
            conn.execute("DELETE FROM migrations WHERE name='device_registry_seed_v1'")
        conn.close()
        energy.ensure_table()
        conn = energy._connect()
        try:
            self.assertEqual(device_identity.known_cloud_gids(conn), ['777'])
        finally:
            conn.close()


class ImportResolutionTests(IdentityBase):
    def test_export_suffix_resolves_to_cloud_gid_and_keeps_original_evidence(self):
        self.discover(device(int(CLOUD_GID), MANUFACTURER))
        result = self.import_csv()
        self.assertEqual((result['imported'], result['device_gid']), (1, CLOUD_GID))
        self.assertEqual(result['identity_method'], 'manufacturer_id_suffix')
        self.assertEqual(energy.get_known_devices(), [CLOUD_GID])
        evidence = self.rows('import_identity_evidence')
        self.assertEqual([(r[2], r[3], r[4], r[5], r[6]) for r in evidence],
                         [(f'{PREFIX}-Barn-1H.csv', PREFIX, CLOUD_GID, 'manufacturer_id_suffix', 1)])

    def test_live_and_imported_history_share_one_scope(self):
        self.discover(device(int(CLOUD_GID), MANUFACTURER))
        now = datetime(2026, 10, 4, 12, 0)
        self.seed_live(CLOUD_GID, (now - timedelta(minutes=1)).isoformat())
        self.import_csv(stamp='10/01/2026 12:00:00', value=3)
        history = energy.get_circuit_history('Pump', CLOUD_GID, now)
        self.assertAlmostEqual(history['windows'][1]['total_kwh'], 3.05)

    def test_cloud_gid_prefix_resolves_exactly(self):
        self.discover(device(int(CLOUD_GID), MANUFACTURER))
        result = self.import_csv(prefix=CLOUD_GID)
        self.assertEqual((result['device_gid'], result['identity_method']), (CLOUD_GID, 'cloud_gid'))

    def test_unknown_prefix_is_rejected_without_any_write(self):
        self.discover(device(int(CLOUD_GID), MANUFACTURER))
        before = {t: self.rows(t) for t in ('readings', 'latest_channel_snapshot', 'reading_changes',
                                            'device_capabilities', 'import_identity_evidence', 'device_aliases')}
        with self.assertRaises(device_identity.DeviceIdentityError) as ctx:
            self.import_csv(prefix='ABCDEF')
        self.assertEqual(ctx.exception.status, 'unknown')
        self.assertEqual(ctx.exception.candidates, (CLOUD_GID,))
        self.assertEqual(before, {t: self.rows(t) for t in before})

    def test_empty_registry_fails_closed_instead_of_storing_the_prefix(self):
        with self.assertRaises(device_identity.DeviceIdentityError):
            self.import_csv()
        self.assertEqual(self.rows(), [])

    def test_short_fragments_are_not_suffix_evidence(self):
        self.discover(device(int(CLOUD_GID), MANUFACTURER))
        with self.assertRaises(device_identity.DeviceIdentityError):
            self.import_csv(prefix='E94')
        with self.assertRaises(device_identity.DeviceIdentityError):
            self.import_csv(prefix='9E94')

    def test_distinct_monitors_sharing_a_suffix_fail_closed_until_selected(self):
        self.discover(device(1, 'AAA-' + PREFIX), device(2, 'BBB-' + PREFIX))
        with self.assertRaises(device_identity.DeviceIdentityError) as ctx:
            self.import_csv()
        self.assertEqual((ctx.exception.status, ctx.exception.candidates), ('ambiguous', ('1', '2')))
        self.assertEqual(self.rows(), [])
        result = self.import_csv(device_gid='2')
        self.assertEqual((result['device_gid'], result['identity_method']), ('2', 'operator_selection'))
        # The selection is a source-local binding, not a global suffix alias.
        self.assertEqual(self.import_csv(stamp='10/02/2026 12:00:00')['device_gid'], '2')
        conn = energy._connect()
        try:
            self.assertEqual(device_identity.resolve_export_prefix(conn, 'FFFFFF').status, 'unknown')
        finally:
            conn.close()

    def test_selection_contradicting_discovery_is_rejected(self):
        self.discover(device(1, 'AAA-' + PREFIX), device(2, 'ZZZ-123456'))
        with self.assertRaises(device_identity.DeviceIdentityError) as ctx:
            self.import_csv(device_gid='2')
        self.assertEqual(ctx.exception.status, 'contradiction')
        self.assertEqual(self.rows(), [])

    def test_two_monitors_with_different_suffixes_stay_separate(self):
        self.discover(device(1, 'AAA-111111'), device(2, 'BBB-222222'))
        self.assertEqual(self.import_csv(prefix='111111')['device_gid'], '1')
        self.assertEqual(self.import_csv(prefix='222222')['device_gid'], '2')
        self.assertEqual(energy.get_known_devices(), ['1', '2'])

    def test_unknown_prefix_selection_of_registered_monitor_binds_only_that_prefix(self):
        self.discover(device(1, 'AAA-111111'))
        result = self.import_csv(prefix='LABEL1', device_gid='1')
        self.assertEqual(result['identity_method'], 'operator_selection')
        self.assertEqual(self.import_csv(prefix='LABEL1', stamp='10/02/2026 12:00:00')['identity_method'],
                         'operator_binding')

    def test_failed_import_rolls_back_selection_binding(self):
        self.discover(device(1, 'AAA-111111'))
        with patch.object(energy, '_save_device_capabilities_with_conn', side_effect=RuntimeError('boom')):
            with self.assertRaises(RuntimeError):
                self.import_csv(prefix='LABEL1', device_gid='1')
        self.assertEqual(self.rows(), [])
        self.assertEqual([r for r in self.rows('device_aliases', 'alias') if r[0] == 'export_prefix'], [])
        self.assertEqual(self.rows('import_identity_evidence'), [])

    def test_explicit_unregistered_scope_remains_available_to_library_callers(self):
        result = self.import_csv(prefix='QA', device_gid='QA')
        self.assertEqual((result['device_gid'], result['identity_method']), ('QA', 'explicit_unregistered'))

    def test_legacy_prefix_history_blocks_new_publication_until_rebound(self):
        self.import_csv(prefix=PREFIX, device_gid=PREFIX)  # legacy behaviour: prefix stored as scope
        self.discover(device(int(CLOUD_GID), MANUFACTURER))
        before = self.rows()
        with self.assertRaises(device_identity.DeviceIdentityError) as ctx:
            self.import_csv(stamp='10/02/2026 12:00:00')
        self.assertEqual((ctx.exception.status, ctx.exception.candidates), ('legacy_history', (PREFIX,)))
        self.assertEqual(before, self.rows())


class LegacyRebindTests(IdentityBase):
    def setUp(self):
        super().setUp()
        # Pre-fix state: an export stored under its prefix, plus live history under the cloud gid.
        self.import_csv(prefix=PREFIX, device_gid=PREFIX, stamp='10/01/2026 12:00:00', value=3)
        self.import_csv(prefix=PREFIX, device_gid=PREFIX, stamp='10/01/2026 13:00:00', value=4)
        self.seed_live(CLOUD_GID, '2026-10-04T12:00:00')
        self.discover(device(int(CLOUD_GID), MANUFACTURER))

    def test_audit_reports_legacy_scope_without_modifying_anything(self):
        before = self.rows()
        report = energy.audit_device_identities()
        monitor = next(m for m in report['monitors'] if m['device_gid'] == CLOUD_GID)
        self.assertEqual([(s['scope'], s['readings']) for s in monitor['legacy_scopes']], [(PREFIX, 2)])
        self.assertEqual(report['unresolved_scopes'], [])
        self.assertEqual(before, self.rows())

    def test_dry_run_changes_nothing(self):
        before = self.rows()
        plan = energy.rebind_device_history(PREFIX, CLOUD_GID)
        self.assertEqual((plan['status'], plan['readings'], plan['blockers']), ('dry_run', 2, []))
        self.assertEqual(before, self.rows())

    def test_apply_requires_a_backup_and_never_overwrites_one(self):
        with self.assertRaises(ValueError):
            energy.rebind_device_history(PREFIX, CLOUD_GID, apply=True)
        existing = self.root / 'existing.db'
        existing.write_text('keep')
        before = self.rows()
        with self.assertRaises(FileExistsError):
            energy.rebind_device_history(PREFIX, CLOUD_GID, apply=True, backup_path=str(existing))
        self.assertEqual(existing.read_text(), 'keep')
        self.assertEqual(before, self.rows())

    def test_apply_preserves_ids_values_and_journal_and_is_idempotent(self):
        before = {r[0]: r for r in self.rows()}
        result = energy.rebind_device_history(PREFIX, CLOUD_GID, apply=True, backup_path=str(self.root / 'b.db'))
        self.assertEqual(result['status'], 'applied')
        after = {r[0]: r for r in self.rows()}
        self.assertEqual(set(before), set(after))
        for reading_id, row in before.items():
            # Everything but the device scope is untouched: id, time, channel, kWh, cents.
            self.assertEqual(row[:2] + row[3:], after[reading_id][:2] + after[reading_id][3:])
        self.assertEqual({r[2] for r in after.values()}, {CLOUD_GID})
        changes = self.rows('reading_changes', 'sequence')
        moved = [c for c in changes if c[1] == 'upsert' and c[4] == CLOUD_GID]
        self.assertEqual(sorted({c[2] for c in moved}), sorted(before))  # same-ID upserts
        # The backup is an intact, unmodified copy of the pre-rebind state.
        import sqlite3
        backup = sqlite3.connect(result['backup']['path'])
        self.assertEqual(sorted(r[0] for r in backup.execute("SELECT device_gid FROM readings")),
                         sorted([PREFIX, PREFIX, CLOUD_GID]))
        backup.close()
        again = energy.rebind_device_history(PREFIX, CLOUD_GID, apply=True, backup_path=str(self.root / 'c.db'))
        self.assertEqual(again['status'], 'nothing_to_rebind')
        self.assertFalse((self.root / 'c.db').exists())
        self.assertEqual(len(self.rows('device_rebindings')), 1)

    def test_imports_work_after_rebind_and_show_in_scoped_queries(self):
        energy.rebind_device_history(PREFIX, CLOUD_GID, apply=True, backup_path=str(self.root / 'b.db'))
        self.assertEqual(self.import_csv(stamp='10/02/2026 12:00:00', value=5)['device_gid'], CLOUD_GID)
        history = energy.get_circuit_history('Pump', CLOUD_GID, datetime(2026, 10, 4, 12, 30))
        self.assertAlmostEqual(history['windows'][1]['total_kwh'], 12.05)

    def test_revert_restores_original_scope_exactly(self):
        before = self.rows()
        applied = energy.rebind_device_history(PREFIX, CLOUD_GID, apply=True, backup_path=str(self.root / 'b.db'))
        reverted = energy.revert_device_rebinding(applied['rebinding_id'])
        self.assertEqual((reverted['status'], reverted['restored'], reverted['missing']), ('reverted', 2, 0))
        self.assertEqual(before, self.rows())
        self.assertEqual(energy.revert_device_rebinding(applied['rebinding_id'])['status'], 'already_reverted')

    def test_key_collision_blocks_everything(self):
        self.seed_live(CLOUD_GID, '2026-10-01T12:00:00')  # same time and channel as an alias reading
        before = self.rows()
        result = energy.rebind_device_history(PREFIX, CLOUD_GID, apply=True, backup_path=str(self.root / 'b.db'))
        self.assertEqual(result['status'], 'blocked')
        self.assertTrue(any('share a timestamp' in b for b in result['blockers']))
        self.assertEqual(before, self.rows())
        self.assertFalse((self.root / 'b.db').exists())

    def test_live_data_inside_an_imported_interval_blocks(self):
        self.seed_live(CLOUD_GID, '2026-10-01T12:30:00')
        result = energy.rebind_device_history(PREFIX, CLOUD_GID)
        self.assertEqual(result['status'], 'blocked')
        self.assertTrue(any('overlap' in b for b in result['blockers']))

    def test_unverified_or_registered_alias_is_never_merged(self):
        self.import_csv(prefix='LABEL1', device_gid='LABEL1')
        self.assertEqual(energy.rebind_device_history('LABEL1', CLOUD_GID)['status'], 'blocked')
        self.discover(device(2, 'OTHER-222222'))
        self.assertEqual(energy.rebind_device_history('2', CLOUD_GID)['status'], 'blocked')
        # An explicit, recorded operator binding makes the alias verifiable.
        energy.bind_export_prefix('LABEL1', CLOUD_GID)
        self.assertEqual(energy.rebind_device_history('LABEL1', CLOUD_GID)['status'], 'dry_run')

    def test_failure_mid_apply_rolls_back_scope_changes(self):
        before = self.rows()
        with patch.object(energy, '_refresh_import_snapshot_with_conn', side_effect=RuntimeError('boom')):
            with self.assertRaises(RuntimeError):
                energy.rebind_device_history(PREFIX, CLOUD_GID, apply=True, backup_path=str(self.root / 'b.db'))
        self.assertEqual(before, self.rows())
        self.assertEqual(self.rows('device_rebindings'), [])


class ImportRouteTests(IdentityBase):
    def post(self, prefix=PREFIX, selected=None):
        client = web.app.test_client()
        path, name = self.csv(prefix)
        data = {'file': (io.BytesIO(Path(path).read_bytes()), name)}
        if selected is not None:
            data['device_gid'] = selected
        return client.post('/api/import-csv', data=data, content_type='multipart/form-data')

    def test_route_resolves_through_the_registry(self):
        self.discover(device(int(CLOUD_GID), MANUFACTURER))
        response = self.post()
        self.assertEqual((response.status_code, response.json['device_gid'], response.json['imported']),
                         (200, CLOUD_GID, 1))

    def test_route_reports_candidates_and_writes_nothing_for_unknown_identity(self):
        self.discover(device(int(CLOUD_GID), MANUFACTURER))
        response = self.post(prefix='ABCDEF')
        self.assertEqual(response.status_code, 400)
        self.assertEqual((response.json['identity_status'], response.json['candidates']), ('unknown', [CLOUD_GID]))
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.post(prefix='ABCDEF', selected=CLOUD_GID).json['imported'], 1)

    def test_route_rejects_selection_of_unseen_monitor(self):
        self.discover(device(int(CLOUD_GID), MANUFACTURER))
        response = self.post(prefix='ABCDEF', selected='999999')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.rows(), [])

    def test_import_page_lists_registered_monitors(self):
        self.discover(device(int(CLOUD_GID), MANUFACTURER))
        html = web.app.test_client().get('/import').get_data(as_text=True)
        self.assertIn(f'<option value="{CLOUD_GID}">', html)
        self.assertIn("fd.append('device_gid', chosen)", html)


if __name__ == '__main__':
    unittest.main()
