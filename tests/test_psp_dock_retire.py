"""Retirement boundaries on temporary files only; no USB discovery or firmware."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import psp_dock_retire as retire
from psp_native_package import REMOTE_FOLDER
from tests.test_psp_native_package import fixture_native_package


SERIAL = '12345678'
USB = r'USB\VID_054C&PID_02D2\RETIRE-TEST-ONLY'


def record(raw):
    return {'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


class RetireDockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.psp = self.home / 'psp'
        self.package = fixture_native_package(self.home / 'package')
        shutil.copytree(self.package / REMOTE_FOLDER, self.psp / REMOTE_FOLDER)
        self.old = {
            'PSP/GAME/PSPDock': {'EBOOT.PBP': b'OLD DOCK APP', 'dockhid.prx': b'OLD DOCK KERNEL', 'LICENSE.txt': b'OLD LICENSE'},
            'PSP/GAME/PSPDockStorageTest': {'EBOOT.PBP': b'OLD STORAGE APP', 'dockmsc.prx': b'OLD STORAGE KERNEL',
                                         'LICENSE.txt': b'STORAGE LICENSE', 'FONT.txt': b'FONT LICENSE'},
        }
        for folder, files in self.old.items():
            target = self.psp / folder
            target.mkdir(parents=True)
            for name, raw in files.items():
                (target / name).write_bytes(raw)
        self.approved = {'schema': retire.REVIEW_SCHEMA, 'reviewed': True,
                         'identity': {'volume_serial': SERIAL, 'usb_device_id': USB},
                         'applications': {folder: {'files': {name: record(raw) for name, raw in files.items()}}
                                          for folder, files in self.old.items()}}
        self.backup = self.home / 'preserved-old-dock'
        self.receipt = self.home / 'evidence' / 'retirement.json'
        for relative in ('PSP/SAVEDATA/SLOT/DATA.BIN', 'PSP/GAME/gpSP/EBOOT.PBP',
                         'PSP/GAME/gpSP/SAVES/Game.sav', 'PSP/GAME/gpSP/STATES/Game.svs',
                         'PSP/.pspdock-native-upgrade-earlier/previous/EBOOT.PBP'):
            path = self.psp / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(('KEEP ' + relative).encode())
        self.kept = {path: path.read_bytes() for path in self.psp.rglob('*') if path.is_file()
                     and not any(path.is_relative_to(self.psp / folder) for folder in self.old)}

    def plan(self, approved=None, serial=SERIAL, usb=USB):
        return retire.plan_retirement(self.package, self.psp, self.approved if approved is None else approved, serial, usb)

    def execute(self, same_device=lambda: True):
        return retire.retire_applications(self.package, self.psp, self.approved, SERIAL, USB,
                                         same_device=same_device, backup=self.backup, receipt=self.receipt)

    def all_psp_files(self):
        return {str(path.relative_to(self.psp)): path.read_bytes() for path in self.psp.rglob('*') if path.is_file()}

    def assert_old(self):
        for folder, files in self.old.items():
            self.assertEqual({path.name: path.read_bytes() for path in (self.psp / folder).iterdir()}, files)

    def assert_kept(self):
        for path, raw in self.kept.items():
            self.assertEqual(path.read_bytes(), raw, str(path))

    def journal(self):
        path = self.receipt.with_name(self.receipt.name + '.journal.jsonl')
        return [json.loads(line) for line in path.read_text().splitlines()]

    def assert_complete_backup(self):
        manifest = json.loads((self.backup / 'manifest.json').read_text())
        self.assertTrue(manifest['complete'])
        self.assertEqual(manifest['applications'], self.approved['applications'])
        for folder, files in self.old.items():
            self.assertEqual({path.name: path.read_bytes() for path in (self.backup / folder).iterdir()}, files)

    def test_plan_is_read_only_and_lists_only_explicit_allowlist(self):
        before = self.all_psp_files()
        plan = self.plan()
        self.assertEqual(plan['filesystem_deletions_only'], sorted(self.old))
        self.assertFalse(plan['retired']);self.assertFalse(plan['backup_verified']);self.assertFalse(plan['autorun'])
        self.assertEqual(plan['current_application']['folder'], REMOTE_FOLDER)
        self.assertEqual(plan['current_application']['revision'], '0302')
        self.assertEqual(plan['file_count'], 7)
        self.assertEqual(self.all_psp_files(), before)
        self.assertFalse(self.backup.exists());self.assertFalse(self.receipt.exists())

    def test_all_apps_backed_up_and_journaled_before_first_deletion(self):
        unlink = Path.unlink
        deleted = []
        def guarded(path, *args, **kwargs):
            if path.is_relative_to(self.psp):
                self.assert_complete_backup()
                self.assertIn('backup_verified', [row['phase'] for row in self.journal()])
                self.assertIn('ready_to_retire', [row['phase'] for row in self.journal()])
                self.assert_kept()
                deleted.append(path.relative_to(self.psp).as_posix())
            return unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', guarded):
            result = self.execute()
        self.assertTrue(result['retired']);self.assertTrue(result['backup_verified'])
        self.assertTrue(result['current_application_verified'])
        self.assertFalse(result['hardware_execution_verified']);self.assertFalse(result['safe_disconnect_verified'])
        expected = {folder + '/' + name for folder, files in self.old.items() for name in files}
        self.assertEqual(set(deleted), expected)
        for folder in self.old:
            self.assertFalse((self.psp / folder).exists())
            self.assertEqual(next(path for path in deleted if path.startswith(folder + '/')), folder + '/EBOOT.PBP')
        self.assertEqual(json.loads(self.receipt.read_text()), result)
        self.assertEqual(self.journal()[-1]['phase'], 'verified')
        self.assert_complete_backup();self.assert_kept()

    def test_explicit_subset_does_not_retire_other_allowed_app(self):
        self.approved['applications'].pop('PSP/GAME/PSPDockStorageTest')
        kept = {name: (self.psp / 'PSP/GAME/PSPDockStorageTest' / name).read_bytes()
                for name in self.old['PSP/GAME/PSPDockStorageTest']}
        self.assertTrue(self.execute()['retired'])
        self.assertEqual({path.name: path.read_bytes() for path in (self.psp / 'PSP/GAME/PSPDockStorageTest').iterdir()}, kept)
        self.assert_kept()

    def test_unreviewed_wrong_identity_and_out_of_scope_plans_rejected(self):
        cases = [dict(self.approved, reviewed=False), dict(self.approved, reviewed='true')]
        for folder in (REMOTE_FOLDER, 'PSP/SAVEDATA', 'PSP/GAME/gpSP', 'PSP/GAME/PSPDockOther',
                       'PSP/GAME/PSPDock/../PSPDockNative'):
            cases.append(dict(self.approved, applications={folder: self.approved['applications']['PSP/GAME/PSPDock']}))
        before = self.all_psp_files()
        for value in cases:
            with self.subTest(value=value.get('applications')), self.assertRaises(ValueError):
                self.plan(value)
        for serial, usb in (('87654321', USB), (SERIAL, USB.replace('02D2', '01CD')), (SERIAL, USB + '-OTHER')):
            with self.subTest(serial=serial, usb=usb), self.assertRaises(ValueError):
                self.plan(serial=serial, usb=usb)
        self.assertEqual(self.all_psp_files(), before)
        self.assertFalse(self.backup.exists());self.assertFalse(self.receipt.exists())

    def test_file_paths_case_aliases_and_device_names_are_not_reviewable(self):
        for name in ('../EBOOT.PBP', 'PSP/SAVEDATA/X', r'..\PSPDockNative\EBOOT.PBP',
                     'C:SECRET', 'NUL.txt', 'COM1', 'name.', 'name ', '.hidden'):
            value = json.loads(json.dumps(self.approved))
            value['applications']['PSP/GAME/PSPDock']['files'] = {name: record(b'X')}
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.plan(value)
        value['applications']['PSP/GAME/PSPDock']['files'] = {'A.PRX': record(b'A'), 'a.prx': record(b'A')}
        with self.assertRaises(ValueError):
            self.plan(value)

    def test_unknown_file_or_subdirectory_blocks_before_backup(self):
        folder = self.psp / 'PSP/GAME/PSPDock'
        extra = folder / 'UNKNOWN.DAT'
        extra.write_bytes(b'USER DATA')
        with self.assertRaises(ValueError):
            self.execute()
        self.assertFalse(self.backup.exists());self.assertFalse(self.receipt.exists())
        extra.unlink()
        extra.mkdir()
        self.approved['applications']['PSP/GAME/PSPDock']['files'][extra.name] = record(b'')
        with self.assertRaises(ValueError):
            self.execute()
        self.assertFalse(self.backup.exists());self.assertFalse(self.receipt.exists())
        extra.rmdir();self.assert_old();self.assert_kept()

    def test_additional_regular_file_requires_its_explicit_reviewed_hash(self):
        extra = self.psp / 'PSP/GAME/PSPDock/README.txt'
        extra.write_bytes(b'REVIEWED OLD APPLICATION README')
        self.approved['applications']['PSP/GAME/PSPDock']['files'][extra.name] = record(extra.read_bytes())
        self.assertTrue(self.execute()['retired'])
        self.assertEqual((self.backup / 'PSP/GAME/PSPDock/README.txt').read_bytes(), b'REVIEWED OLD APPLICATION README')
        self.assert_kept()

    def test_current_dock_must_match_approved_package_before_any_backup(self):
        target = self.psp / REMOTE_FOLDER / 'docknative.prx'
        target.write_bytes(b'DIFFERENT CURRENT DOCK')
        before = self.all_psp_files()
        with self.assertRaises(ValueError):
            self.execute()
        self.assertEqual(self.all_psp_files(), before)
        self.assertFalse(self.backup.exists());self.assertFalse(self.receipt.exists())

    def test_old_hash_change_blocks_before_any_backup(self):
        target = self.psp / 'PSP/GAME/PSPDock/dockhid.prx'
        target.write_bytes(b'UNKNOWN CHANGED APP')
        before = self.all_psp_files()
        with self.assertRaises(ValueError):
            self.execute()
        self.assertEqual(self.all_psp_files(), before)
        self.assertFalse(self.backup.exists());self.assertFalse(self.receipt.exists())

    def test_identity_loss_during_backup_keeps_all_psp_files(self):
        first_copy = self.backup / 'PSP/GAME/PSPDock/EBOOT.PBP'
        with self.assertRaises(ValueError):
            self.execute(lambda: not first_copy.exists())
        self.assertTrue(first_copy.exists())
        self.assertFalse((self.backup / 'manifest.json').exists())
        self.assertFalse(json.loads(self.receipt.read_text())['retired'])
        self.assertEqual(self.journal()[-1]['phase'], 'incomplete')
        self.assert_old();self.assert_kept()

    def test_identity_loss_after_complete_backup_prevents_first_deletion(self):
        with self.assertRaises(ValueError):
            self.execute(lambda: not (self.backup / 'manifest.json').exists())
        self.assert_complete_backup();self.assert_old();self.assert_kept()
        self.assertNotIn('file_retired', [row['phase'] for row in self.journal()])
        self.assertFalse(json.loads(self.receipt.read_text())['retired'])

    def test_identity_loss_after_one_deletion_does_not_continue_or_resume(self):
        first = self.psp / 'PSP/GAME/PSPDock/EBOOT.PBP'
        with self.assertRaises(ValueError):
            self.execute(lambda: first.exists())
        self.assertFalse(first.exists())
        self.assert_complete_backup();self.assert_kept()
        deleted = [row for row in self.journal() if row['phase'] == 'file_retired']
        self.assertEqual([(row['folder'], row['file']) for row in deleted], [('PSP/GAME/PSPDock', 'EBOOT.PBP')])
        before = self.all_psp_files()
        self.backup = self.home / 'another-backup'
        self.receipt = self.home / 'another-receipt.json'
        with self.assertRaises(ValueError):
            self.execute()
        self.assertEqual(self.all_psp_files(), before)
        self.assertFalse(self.backup.exists());self.assertFalse(self.receipt.exists())

    def test_changed_source_after_backup_is_preserved_without_deletion(self):
        original_note = retire._Evidence.note
        target = self.psp / 'PSP/GAME/PSPDock/dockhid.prx'
        def change(evidence, phase, **facts):
            original_note(evidence, phase, **facts)
            if phase == 'backup_verified':
                target.write_bytes(b'USER CHANGED THIS AFTER BACKUP')
        with patch.object(retire._Evidence, 'note', change), self.assertRaises(ValueError):
            self.execute()
        self.assertEqual(target.read_bytes(), b'USER CHANGED THIS AFTER BACKUP')
        self.assertTrue((target.parent / 'EBOOT.PBP').exists())
        self.assert_complete_backup();self.assert_kept()
        self.assertNotIn('file_retired', [row['phase'] for row in self.journal()])

    def test_unknown_file_appearing_after_backup_blocks_deletion(self):
        original_note = retire._Evidence.note
        extra = self.psp / 'PSP/GAME/PSPDock/KEEP-UNKNOWN.txt'
        def change(evidence, phase, **facts):
            original_note(evidence, phase, **facts)
            if phase == 'ready_to_retire':
                extra.write_bytes(b'KEEP')
        with patch.object(retire._Evidence, 'note', change), self.assertRaises(ValueError):
            self.execute()
        self.assertEqual(extra.read_bytes(), b'KEEP')
        self.assertTrue((extra.parent / 'EBOOT.PBP').exists())
        self.assert_complete_backup();self.assert_kept()
        self.assertNotIn('file_retired', [row['phase'] for row in self.journal()])

    def test_corrupt_pc_backup_after_verification_stops_before_deletion(self):
        original_note = retire._Evidence.note
        def change(evidence, phase, **facts):
            original_note(evidence, phase, **facts)
            if phase == 'ready_to_retire':
                (self.backup / 'PSP/GAME/PSPDock/EBOOT.PBP').write_bytes(b'CORRUPT')
        with patch.object(retire._Evidence, 'note', change), self.assertRaises(ValueError):
            self.execute()
        self.assert_old();self.assert_kept()
        self.assertNotIn('file_retired', [row['phase'] for row in self.journal()])

    def test_current_app_change_after_backup_stops_before_deletion(self):
        original_note = retire._Evidence.note
        current = self.psp / REMOTE_FOLDER / 'docknative.prx'
        def change(evidence, phase, **facts):
            original_note(evidence, phase, **facts)
            if phase == 'backup_verified':
                current.write_bytes(b'EXTERNAL CURRENT APP CHANGE')
        with patch.object(retire._Evidence, 'note', change), self.assertRaises(ValueError):
            self.execute()
        self.assert_old();self.assert_complete_backup()
        self.assertEqual(current.read_bytes(), b'EXTERNAL CURRENT APP CHANGE')
        self.assertNotIn('file_retired', [row['phase'] for row in self.journal()])

    def test_delete_failure_keeps_verified_backup_and_incomplete_receipt(self):
        unlink = Path.unlink
        def reject(path, *args, **kwargs):
            if path.is_relative_to(self.psp):
                raise PermissionError('Simulated Windows refusal')
            return unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', reject), self.assertRaises(PermissionError):
            self.execute()
        self.assert_old();self.assert_kept();self.assert_complete_backup()
        self.assertFalse(json.loads(self.receipt.read_text())['retired'])
        self.assertEqual(self.journal()[-1]['phase'], 'incomplete')

    def test_existing_evidence_and_psp_backups_are_rejected(self):
        self.receipt.parent.mkdir()
        journal = self.receipt.with_name(self.receipt.name + '.journal.jsonl')
        journal.write_text('KEEP JOURNAL')
        with self.assertRaises(FileExistsError):
            self.execute()
        self.assertEqual(journal.read_text(), 'KEEP JOURNAL')
        self.assertFalse(self.receipt.exists());self.assertFalse(self.backup.exists())
        self.receipt = self.home / 'fresh.json'
        self.backup = self.psp / 'unsafe-backup'
        with self.assertRaises(ValueError):
            self.execute()
        self.assertFalse(self.receipt.exists());self.assertFalse(self.backup.exists())
        self.assert_old();self.assert_kept()

    def test_backup_and_receipt_must_not_overlap_or_modify_package(self):
        self.receipt = self.backup / 'receipt.json'
        with self.assertRaises(ValueError):
            self.execute()
        self.assertFalse(self.backup.exists())
        self.receipt = self.home / 'fresh.json'
        self.backup = self.package / 'preserved-old'
        with self.assertRaises(ValueError):
            self.execute()
        self.assertFalse(self.backup.exists());self.assertFalse(self.receipt.exists())
        self.assert_old();self.assert_kept()

    def test_symlink_in_old_application_is_rejected(self):
        file = self.psp / 'PSP/GAME/PSPDock/dockhid.prx'
        raw = file.read_bytes()
        other = self.home / 'unrelated.prx';other.write_bytes(raw)
        file.unlink()
        try:
            file.symlink_to(other)
        except (OSError, NotImplementedError):
            self.skipTest('This platform cannot create fixture symlinks')
        with self.assertRaises(ValueError):
            self.execute()
        self.assertEqual(other.read_bytes(), raw)
        self.assertFalse(self.backup.exists());self.assertFalse(self.receipt.exists())
        self.assert_kept()

    def test_junction_application_is_rejected(self):
        target = self.psp / 'PSP/GAME/PSPDock'
        with patch.object(Path, 'is_junction', lambda path: path == target, create=True), self.assertRaises(ValueError):
            self.execute()
        self.assertFalse(self.backup.exists());self.assertFalse(self.receipt.exists())
        self.assert_old();self.assert_kept()

    def test_cli_defaults_to_plan_and_requires_explicit_retire_destinations(self):
        approved_file = self.home / 'reviewed.json'
        approved_file.write_text(json.dumps(self.approved))
        args = ['--drive', 'G:\\', '--package', str(self.package), '--reviewed-plan', str(approved_file)]
        device = SimpleNamespace(drive='G:\\', volume_serial=SERIAL, usb_device_id=USB)
        with patch.object(retire, 'os', SimpleNamespace(name='nt', environ={})), \
                patch.object(retire, 'identify', return_value=(device, 1)), \
                patch.object(retire, 'plan_retirement', return_value={'retired': False}) as plan, \
                patch.object(retire, 'retire_applications', return_value={'retired': True}) as execute, \
                patch('builtins.print'):
            self.assertEqual(retire.main(args), 0)
            plan.assert_called_once();execute.assert_not_called()
            self.assertEqual(retire.main(args + ['--retire']), 1)
            execute.assert_not_called()
            self.assertEqual(retire.main(args + ['--retire', '--backup', str(self.backup), '--receipt', str(self.receipt)]), 0)
            execute.assert_called_once()


if __name__ == '__main__':
    unittest.main()
