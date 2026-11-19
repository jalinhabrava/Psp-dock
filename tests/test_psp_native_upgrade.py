"""Current 0307-to-0308 upgrade, interruption and rollback safety tests.

No hardware discovery: only four synthetic file hashes replace the reviewed
0307 constants, and every device identity callback is an explicit test double.
Older firmware's large test matrix remains available from Git history.
"""
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import tools.psp.psp_backup as psp_backup
import tools.psp.psp_native_upgrade as upgrade
from tools.psp.psp_native_package import FILES, REMOTE_FOLDER, digest
from tests.test_psp_native_package import fixture_native_package


SERIAL = '12345678'
USB = r'USB\VID_054C&PID_02D2\NATIVE-UPGRADE-TEST'


class NativeUpgradeTests(unittest.TestCase):
    previous_revision = '0307'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.psp = self.home / 'psp'
        self.target = self.psp / REMOTE_FOLDER
        self.target.mkdir(parents=True)
        self.old = {name: ('RETIRED ' + self.previous_revision + ' FIXTURE ' + name).encode() for name in FILES}
        self.old_records = {name: {'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
                            for name, raw in self.old.items()}
        for name, raw in self.old.items():
            (self.target / name).write_bytes(raw)
        self.pin = patch.object(upgrade, 'KNOWN_' + self.previous_revision + '_FILES', self.old_records)
        self.pin.start();self.addCleanup(self.pin.stop)
        self.kept = {}
        for relative in ('PSP/GAME/PSPDock/EBOOT.PBP', 'PSP/GAME/PSPDockStorageTest/EBOOT.PBP',
                         'PSP/SAVEDATA/SLOT/DATA.BIN', 'PSP/GAME/gpSP/SAVES/Game.sav',
                         'PSP/GAME/gpSP/STATES/Game.svs',
                         'PSP/SYSTEM/PSPDock/xmb/system_plugin.rco'):
            path = self.psp / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(('KEEP ' + relative).encode())
            self.kept[path] = path.read_bytes()
        self.package = fixture_native_package(self.home / 'package0308')
        self.new_records = json.loads((self.package / 'manifest.json').read_text())['files']
        snapshot = psp_backup.backup(self.psp, self.home / 'critical', 'critical', SERIAL, USB,
                                     verify_identity=lambda: True, reserve_bytes=0)
        self.snapshot = Path(snapshot['directory'])
        self.receipt = self.home / 'evidence' / 'upgrade.json'
        self.app_backup = self.home / ('application-backup' + self.previous_revision)
        self.restore_receipt = self.home / 'evidence' / 'restore.json'

    def plan(self, serial=SERIAL, usb=USB):
        return upgrade.plan_upgrade(self.package, self.psp, self.snapshot, serial, usb)

    def install(self, same_device=lambda: True):
        return upgrade.upgrade_package(self.package, self.psp, self.snapshot, SERIAL, USB,
            same_device=same_device, receipt=self.receipt, app_backup=self.app_backup)

    def restore(self, same_device=lambda: True):
        return upgrade.rollback_package(self.receipt, self.psp, SERIAL, USB,
            same_device=same_device, receipt=self.restore_receipt)

    def assert_kept(self):
        for path, raw in self.kept.items():
            self.assertEqual(path.read_bytes(), raw, str(path))

    def assert_files(self, folder, expected):
        self.assertEqual({p.name for p in folder.iterdir()}, set(expected))
        self.assertEqual({p.name: {'size': p.stat().st_size, 'sha256': digest(p)}
                          for p in folder.iterdir()}, expected)

    def journal(self, receipt=None):
        receipt = receipt or self.receipt
        return [json.loads(row) for row in receipt.with_name(receipt.name + '.journal.jsonl').read_text().splitlines()]

    def test_plan_is_read_only(self):
        before = {p: p.read_bytes() for p in self.psp.rglob('*') if p.is_file()}
        result = self.plan()
        self.assertEqual((result['from_revision'], result['to_revision']), (self.previous_revision, '0308'))
        self.assertEqual(result['reference_installation_receipt_sha256'],
                         upgrade._reference_receipt(self.previous_revision))
        self.assertFalse(result['installed']);self.assertFalse(result['autorun'])
        self.assertEqual(before, {p: p.read_bytes() for p in self.psp.rglob('*') if p.is_file()})
        self.assertFalse(self.receipt.exists());self.assertFalse(self.app_backup.exists())

    def test_backup_is_verified_before_first_psp_write_and_swap_keeps_old_outside_game(self):
        mkdir = Path.mkdir
        observed = []
        def checked_mkdir(path, *args, **kwargs):
            if path.parent == self.psp / 'PSP' and path.name.startswith('.pspdock-native-'):
                self.assertTrue((self.app_backup / 'manifest.json').is_file())
                self.assert_files(self.app_backup / 'files', self.old_records)
                observed.append(path)
            return mkdir(path, *args, **kwargs)
        identity = Mock(return_value=True)
        with patch.object(Path, 'mkdir', checked_mkdir):
            result = self.install(identity)
        self.assertEqual(len(observed), 1)
        self.assertGreater(identity.call_count, 20)
        self.assertTrue(result['installed']);self.assertFalse(result['autorun'])
        self.assertFalse(result['hardware_execution_verified'])
        self.assertFalse(Path(result['parked']).is_relative_to(self.psp / 'PSP/GAME'))
        self.assert_files(self.target, self.new_records)
        self.assert_files(Path(result['parked']), self.old_records)
        self.assert_files(self.app_backup / 'files', self.old_records)
        self.assertEqual(json.loads(self.receipt.read_text()), result)
        phases = [row['phase'] for row in self.journal()]
        self.assertLess(phases.index('application_backup_verified'), phases.index('ready_to_publish'))
        self.assertLess(phases.index('old_application_parked'), phases.index('published'))
        self.assertEqual(phases[-1], 'verified')
        self.assert_kept()

    def test_any_old_hash_or_extra_file_blocks_before_backup_or_receipt(self):
        for name in FILES:
            path = self.target / name
            path.write_bytes(b'X' + self.old[name][1:])
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.install()
            self.assertFalse(self.receipt.exists());self.assertFalse(self.app_backup.exists())
            path.write_bytes(self.old[name])
        (self.target / 'UNKNOWN.PRX').write_bytes(b'UNREVIEWED')
        with self.assertRaises(ValueError):
            self.install()
        self.assertFalse(self.receipt.exists());self.assertFalse(self.app_backup.exists())
        self.assertEqual(list((self.psp / 'PSP').glob('.pspdock-native-*')), [])
        self.assert_kept()

    def test_wrong_identity_and_stale_critical_backup_block_plan(self):
        for serial, usb in (('87654321', USB), (SERIAL, USB.replace('02D2', '01CD')),
                            (SERIAL, USB.replace('NATIVE-UPGRADE-TEST', 'OTHER'))):
            with self.subTest(serial=serial, usb=usb), self.assertRaises(ValueError):
                self.plan(serial, usb)
        (self.psp / 'PSP/SAVEDATA/SLOT/DATA.BIN').write_bytes(b'changed after backup')
        with self.assertRaises(ValueError):
            self.install()
        self.assertFalse(self.app_backup.exists());self.assertFalse(self.receipt.exists())

    def test_identity_loss_after_backup_prevents_first_psp_write(self):
        with self.assertRaises(ValueError):
            self.install(lambda: not (self.app_backup / 'manifest.json').exists())
        self.assert_files(self.target, self.old_records)
        self.assert_files(self.app_backup / 'files', self.old_records)
        self.assertEqual(list((self.psp / 'PSP').glob('.pspdock-native-*')), [])
        self.assertFalse(json.loads(self.receipt.read_text())['installed'])
        self.assertEqual(self.journal()[-1]['phase'], 'incomplete')

    def test_identity_loss_during_staging_preserves_partial_and_original(self):
        def same():
            return not list((self.psp / 'PSP').glob('.pspdock-native-upgrade-*/candidate/EBOOT.PBP'))
        with self.assertRaises(ValueError):
            self.install(same)
        stages = list((self.psp / 'PSP').glob('.pspdock-native-upgrade-*/candidate'))
        self.assertEqual(len(stages), 1)
        self.assertEqual({p.name for p in stages[0].iterdir()}, {'EBOOT.PBP'})
        self.assert_files(self.target, self.old_records)
        self.assertFalse(json.loads(self.receipt.read_text())['installed'])
        self.assert_kept()

    def test_disconnect_between_renames_can_be_restored_explicitly_from_pc(self):
        with self.assertRaises(ValueError):
            self.install(lambda: self.target.exists())
        self.assertFalse(self.target.exists())
        saved = json.loads(self.receipt.read_text())
        self.assert_files(Path(saved['parked']), self.old_records)
        self.assert_files(Path(saved['staging']), self.new_records)
        self.assertFalse(saved['installed'])
        planned = upgrade.plan_rollback(self.receipt, self.psp, SERIAL, USB)
        self.assertEqual(planned['target_state'], 'absent')
        self.assertFalse(self.target.exists())
        result = self.restore()
        self.assertTrue(result['restored']);self.assertTrue(result['retired_revision'])
        self.assertFalse(result['autorun']);self.assertFalse(result['hardware_execution_verified'])
        self.assert_files(self.target, self.old_records)
        self.assert_files(Path(saved['staging']), self.new_records)
        self.assert_files(Path(saved['parked']), self.old_records)
        self.assert_kept()

    def test_disconnect_after_publish_never_removes_new_files_or_claims_success(self):
        def same():
            file = self.target / 'EBOOT.PBP'
            return not file.exists() or digest(file) != self.new_records['EBOOT.PBP']['sha256']
        with self.assertRaises(ValueError):
            self.install(same)
        self.assert_files(self.target, self.new_records)
        self.assertFalse(json.loads(self.receipt.read_text())['installed'])
        self.assertIn('published', [row['phase'] for row in self.journal()])
        self.assert_files(self.app_backup / 'files', self.old_records)
        self.assert_kept()

    def test_explicit_rollback_preserves_replacement_and_second_restore_writes_no_psp_files(self):
        installed = self.install()
        result = self.restore()
        self.assertTrue(result['restored']);self.assertFalse(result['already_original'])
        self.assert_files(self.target, self.old_records)
        self.assert_files(Path(result['parked']), self.new_records)
        self.assert_files(Path(installed['parked']), self.old_records)
        before = {str(p.relative_to(self.psp)): p.read_bytes() for p in self.psp.rglob('*') if p.is_file()}
        self.restore_receipt = self.home / 'evidence' / 'restore-again.json'
        again = self.restore()
        self.assertTrue(again['already_original'])
        self.assertEqual(again['filesystem_writes_only'], [])
        self.assertEqual(before, {str(p.relative_to(self.psp)): p.read_bytes() for p in self.psp.rglob('*') if p.is_file()})
        self.assertFalse(Path(again['workspace']).exists())
        self.assert_kept()

    def test_rollback_stops_on_identity_loss_after_parking_and_can_recover_later(self):
        self.install()
        with self.assertRaises(ValueError):
            self.restore(lambda: self.target.exists())
        self.assertFalse(self.target.exists())
        partial = json.loads(self.restore_receipt.read_text())
        self.assert_files(Path(partial['parked']), self.new_records)
        self.assertFalse(partial['restored'])
        self.restore_receipt = self.home / 'evidence' / 'restore-after-disconnect.json'
        self.assertTrue(self.restore()['restored'])
        self.assert_files(self.target, self.old_records)
        self.assert_files(Path(partial['parked']), self.new_records)
        self.assert_kept()

    def test_rollback_rejects_other_device_corrupt_backup_and_unrecognized_current_files(self):
        self.install()
        for serial, usb in (('98765432', USB), (SERIAL, USB.replace('TEST', 'OTHER'))):
            with self.subTest(serial=serial, usb=usb), self.assertRaises(ValueError):
                upgrade.plan_rollback(self.receipt, self.psp, serial, usb)
        file = self.app_backup / 'files/EBOOT.PBP'
        file.write_bytes(b'X' + self.old['EBOOT.PBP'][1:])
        with self.assertRaises(ValueError):
            self.restore()
        self.assertFalse(self.restore_receipt.exists())
        file.write_bytes(self.old['EBOOT.PBP'])
        current = self.target / 'docknative.prx'
        current.write_bytes(b'UNKNOWN USER CHANGES')
        with self.assertRaises(ValueError):
            self.restore()
        self.assertFalse(self.restore_receipt.exists())
        self.assertEqual(current.read_bytes(), b'UNKNOWN USER CHANGES')
        self.assert_kept()

    def test_package_or_backup_change_before_publish_cannot_remove_original(self):
        copy = upgrade._copy_files
        for changed in ('package', 'backup'):
            with self.subTest(changed=changed):
                source = self.package / REMOTE_FOLDER / 'docknative.prx'
                original = source.read_bytes()
                def mutate_after_staging(src, dst, records, **kwargs):
                    copy(src, dst, records, **kwargs)
                    if src == self.package / REMOTE_FOLDER:
                        if changed == 'package':
                            source.write_bytes(b'X' + original[1:])
                        else:
                            (self.app_backup / 'files/docknative.prx').write_bytes(b'corrupt backup')
                with patch.object(upgrade, '_copy_files', side_effect=mutate_after_staging), self.assertRaises(ValueError):
                    self.install()
                self.assert_files(self.target, self.old_records)
                self.assertFalse(json.loads(self.receipt.read_text())['installed'])
                source.write_bytes(original)
                self.receipt = self.home / 'evidence' / 'upgrade-second.json'
                self.app_backup = self.home / 'application-backup-second'
        self.assert_kept()

    def test_original_changed_during_staging_is_not_parked_or_overwritten(self):
        copy = upgrade._copy_files
        def change_old(src, dst, records, **kwargs):
            copy(src, dst, records, **kwargs)
            if src == self.package / REMOTE_FOLDER:
                (self.target / 'docknative.prx').write_bytes(b'USER CHANGED ORIGINAL')
        with patch.object(upgrade, '_copy_files', side_effect=change_old), self.assertRaises(ValueError):
            self.install()
        saved = json.loads(self.receipt.read_text())
        self.assertFalse(Path(saved['parked']).exists())
        self.assertEqual((self.target / 'docknative.prx').read_bytes(), b'USER CHANGED ORIGINAL')
        self.assert_files(self.app_backup / 'files', self.old_records)

    def test_destination_appearing_after_parking_is_preserved_and_blocks_rollback(self):
        rename = Path.rename
        def competing_target(path, destination):
            result = rename(path, destination)
            if path == self.target:
                self.target.mkdir()
                (self.target / 'KEEP').write_bytes(b'FOREIGN TARGET')
            return result
        with patch.object(Path, 'rename', competing_target), self.assertRaises(FileExistsError):
            self.install()
        self.assertEqual((self.target / 'KEEP').read_bytes(), b'FOREIGN TARGET')
        with self.assertRaises(ValueError):
            self.restore()
        self.assertEqual((self.target / 'KEEP').read_bytes(), b'FOREIGN TARGET')
        self.assert_files(self.app_backup / 'files', self.old_records)

    def test_recovery_ignores_recorded_psp_paths_and_uses_fixed_application_target(self):
        self.install()
        record = json.loads(self.receipt.read_text())
        record.update(target=str(self.psp / 'PSP/GAME/PSPDock'),
                      staging=str(self.psp / 'PSP/SAVEDATA'), parked=str(self.psp / 'PSP/SAVEDATA'))
        self.receipt.write_text(json.dumps(record))
        self.assertTrue(self.restore()['restored'])
        self.assert_files(self.target, self.old_records)
        self.assert_kept()

    def test_existing_pc_evidence_and_backup_on_psp_are_never_overwritten(self):
        self.receipt.parent.mkdir()
        self.receipt.write_text('KEEP RECEIPT')
        with self.assertRaises(FileExistsError):
            self.install()
        self.assertEqual(self.receipt.read_text(), 'KEEP RECEIPT')
        self.receipt = self.home / 'evidence' / 'new-receipt.json'
        self.app_backup = self.psp / 'backup'
        with self.assertRaises(ValueError):
            self.install()
        self.assertFalse(self.receipt.exists());self.assertFalse(self.app_backup.exists())
        self.assert_files(self.target, self.old_records)

    def test_symlink_application_is_rejected_before_any_evidence(self):
        moved = self.psp / 'PSP' / 'other-folder'
        self.target.rename(moved)
        try:
            self.target.symlink_to(moved, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('This host cannot create test symlinks')
        with self.assertRaises(ValueError):
            self.install()
        self.assertFalse(self.receipt.exists());self.assertFalse(self.app_backup.exists())
        self.assert_files(moved, self.old_records)

    def test_cli_plan_is_default_and_both_mutations_require_explicit_options(self):
        args = ['--drive', 'G:\\', '--package', str(self.package), '--backup', str(self.snapshot)]
        device = SimpleNamespace(drive='G:\\', volume_serial=SERIAL, usb_device_id=USB)
        fake_os = SimpleNamespace(name='nt', environ={})
        with patch.object(upgrade, 'os', fake_os), patch.object(upgrade, 'identify', return_value=(device, 1)), \
                patch.object(upgrade, 'plan_upgrade', return_value={'installed': False}) as plan, \
                patch.object(upgrade, 'upgrade_package', return_value={'installed': True}) as install, \
                patch.object(upgrade, 'plan_rollback', return_value={'restored': False}) as plan_back, \
                patch.object(upgrade, 'rollback_package', return_value={'restored': True}) as restore, \
                patch('builtins.print'):
            self.assertEqual(upgrade.main(args), 0)
            plan.assert_called_once();install.assert_not_called()
            self.assertEqual(upgrade.main(args + ['--upgrade']), 1)
            install.assert_not_called()
            self.assertEqual(upgrade.main(args + ['--upgrade', '--receipt', str(self.receipt),
                                                   '--app-backup', str(self.app_backup)]), 0)
            install.assert_called_once()
            back = ['--drive', 'G:\\', '--rollback', str(self.receipt)]
            self.assertEqual(upgrade.main(back), 0)
            plan_back.assert_called_once();restore.assert_not_called()
            self.assertEqual(upgrade.main(back + ['--restore']), 1)
            restore.assert_not_called()
            self.assertEqual(upgrade.main(back + ['--restore', '--receipt', str(self.restore_receipt)]), 0)
            restore.assert_called_once()

# Tests exercise the final 0308 upgrade from the last 0307 release.
# Older historical upgrade matrices remain available in Git history.

if __name__ == '__main__':
    unittest.main()
