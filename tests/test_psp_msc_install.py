"""Real temporary files/backups; hardware-only imports are isolated stubs."""
import importlib
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

from tests.test_psp_msc_package import fixture_package
from tools.psp.psp_msc_package import REMOTE_FOLDER, FILES, digest, verify_package


def load_without_hardware():
    def forbidden(*args, **kwargs):
        raise AssertionError('Unit test attempted hardware discovery')
    dock = ModuleType('dock')
    dock.ROOT = Path(__file__).parent
    dock.windows_devices = forbidden
    eject = ModuleType('lab_eject')
    eject.ppsspp_open = eject.real_dock_open = forbidden
    usb = ModuleType('psp_usb')
    usb.identify = forbidden
    usb.PspStorage = object
    usb.UsbTransitionError = type('UsbTransitionError', (RuntimeError,), {})
    # Restore sys.modules on exit, including any previously loaded production
    # modules. Other tests must not retain these hardware stubs accidentally.
    with patch.dict(sys.modules, {'dock': dock, 'tools.labs.lab_eject': eject, 'psp_dock.devices.psp_usb': usb}):
        for name in ('tools.psp.psp_backup', 'tools.psp.psp_composite_install', 'tools.psp.psp_msc_install'):
            sys.modules.pop(name, None)
        backup = importlib.import_module('tools.psp.psp_backup')
        installer = importlib.import_module('tools.psp.psp_msc_install')
    return backup, installer


class MSCInstallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backup, cls.installer = load_without_hardware()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.psp = self.root / 'psp'
        self.package = fixture_package(self.root / 'package')
        self.original = self.psp / 'PSP/GAME/PSPDock/EBOOT.PBP'
        self.original.parent.mkdir(parents=True)
        self.original.write_bytes(b'ORIGINAL WORKING HOMEBREW')
        self.savedata = self.psp / 'PSP/SAVEDATA/SLOT/DATA.BIN'
        self.savedata.parent.mkdir(parents=True)
        self.savedata.write_bytes(b'ORIGINAL SAVEDATA')
        result = self.backup.backup(self.psp, self.root / 'backups', 'critical',
                                   'SERIAL', 'USB-ID', verify_identity=lambda: True,
                                   reserve_bytes=0)
        self.snapshot = Path(result['directory'])
        self.target = self.psp / REMOTE_FOLDER

    def tearDown(self):
        self.tmp.cleanup()

    def plan(self, serial='SERIAL', usb_id='USB-ID'):
        return self.installer.plan_install(self.package, self.psp, self.snapshot, serial, usb_id)

    def install(self, identity=lambda: True):
        return self.installer.install_package(self.package, self.psp, self.snapshot,
                                             'SERIAL', 'USB-ID', same_device=identity)

    def test_plan_checks_current_verified_backup_without_writing_to_psp(self):
        before = {str(p.relative_to(self.psp)): p.read_bytes()
                  for p in self.psp.rglob('*') if p.is_file()}
        result = self.plan()
        after = {str(p.relative_to(self.psp)): p.read_bytes()
                 for p in self.psp.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        self.assertFalse(result['installed'])
        self.assertFalse(result['autorun'])
        self.assertFalse(result['hardware_execution_verified'])

    def test_installs_only_new_folder_and_preserves_originals(self):
        result = self.install()
        self.assertTrue(result['installed'])
        self.assertEqual({p.name for p in self.target.iterdir()}, set(FILES))
        for name in FILES:
            self.assertEqual((self.target / name).read_bytes(),
                             (self.package / REMOTE_FOLDER / name).read_bytes())
        self.assertEqual(self.original.read_bytes(), b'ORIGINAL WORKING HOMEBREW')
        self.assertEqual(self.savedata.read_bytes(), b'ORIGINAL SAVEDATA')

    def test_same_size_changed_savedata_invalidates_backup_before_staging(self):
        self.savedata.write_bytes(b'X' + self.savedata.read_bytes()[1:])
        with self.assertRaises(ValueError):
            self.install()
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.target.parent.glob('.pspdock-msc-*')), [])

    def test_backup_identity_mismatch_is_rejected(self):
        for serial, usb in (('OTHER', 'USB-ID'), ('SERIAL', 'OTHER')):
            with self.subTest(serial=serial, usb=usb), self.assertRaises(ValueError):
                self.plan(serial, usb)
        self.assertFalse(self.target.exists())

    def test_incomplete_or_damaged_backup_blocks_install(self):
        marker = self.snapshot / 'BACKUP-INCOMPLETE.txt'
        marker.write_text('INCOMPLETE')
        with self.assertRaises(ValueError):
            self.install()
        marker.unlink()
        (self.snapshot / 'data/PSP/SAVEDATA/SLOT/DATA.BIN').write_bytes(b'CORRUPT')
        with self.assertRaises(ValueError):
            self.install()
        self.assertFalse(self.target.exists())

    def test_changed_device_before_copy_makes_no_staging_directory(self):
        with self.assertRaises(ValueError):
            self.install(lambda: False)
        self.assertEqual(list(self.target.parent.glob('.pspdock-msc-*')), [])

    def test_changed_device_during_copy_leaves_unpublished_partial_without_deletion(self):
        identity = Mock(side_effect=[True, False])
        with self.assertRaises(ValueError):
            self.install(identity)
        self.assertFalse(self.target.exists())
        stages = list(self.target.parent.glob('.pspdock-msc-*'))
        self.assertEqual(len(stages), 1)
        self.assertEqual({p.name for p in stages[0].iterdir()}, set(FILES))

    def test_existing_destination_is_not_overwritten(self):
        self.target.mkdir()
        sentinel = self.target / 'EBOOT.PBP'
        sentinel.write_bytes(b'KEEP EXISTING TEST')
        with self.assertRaises(FileExistsError):
            self.install()
        self.assertEqual(sentinel.read_bytes(), b'KEEP EXISTING TEST')

    def test_destination_appearing_during_copy_is_not_overwritten(self):
        checks = 0
        def same_device():
            nonlocal checks
            checks += 1
            if checks == 2:
                self.target.mkdir()
                (self.target / 'EBOOT.PBP').write_bytes(b'RACE PRESERVED')
            return True
        with self.assertRaises(FileExistsError):
            self.install(same_device)
        self.assertEqual((self.target / 'EBOOT.PBP').read_bytes(), b'RACE PRESERVED')

    def test_copied_binary_corruption_is_not_published(self):
        original_copy = self.installer.shutil.copyfileobj
        def corrupt(reader, writer):
            original_copy(reader, writer)
            writer.write(b'CORRUPTION')
        with patch.object(self.installer.shutil, 'copyfileobj', side_effect=corrupt):
            with self.assertRaises(ValueError):
                self.install()
        self.assertFalse(self.target.exists())
        verify_package(self.package)
        self.assertEqual(self.original.read_bytes(), b'ORIGINAL WORKING HOMEBREW')

    def prepare_upgrade(self):
        old = self.install()
        old['revision'] = '0206'
        old['identity'] = {'volume_serial': 'SERIAL', 'usb_device_id': 'USB-ID'}
        self.receipt = self.root / 'previous.json'
        self.receipt.write_text(json.dumps(old))
        self.archive = self.root / 'previous-test'
        self.previous = {name: (self.target / name).read_bytes() for name in FILES}
        binary = self.package / REMOTE_FOLDER / 'dockmsc.prx'
        binary.write_bytes(binary.read_bytes() + b'NEW-0207')
        manifest_file = self.package / 'manifest.json'
        manifest = json.loads(manifest_file.read_text())
        manifest['files']['dockmsc.prx'] = {'size': binary.stat().st_size, 'sha256': digest(binary)}
        manifest_file.write_text(json.dumps(manifest))

    def upgrade(self, identity=lambda: True):
        return self.installer.upgrade_package(self.package, self.psp, self.snapshot,
            'SERIAL', 'USB-ID', self.receipt, self.archive, same_device=identity)

    def test_upgrade_archives_exact_old_version_and_verifies_new_version(self):
        self.prepare_upgrade()
        result = self.upgrade()
        self.assertTrue(result['installed'])
        self.assertTrue(result['previous_archived_and_verified'])
        for name in FILES:
            self.assertEqual((self.archive / 'files' / name).read_bytes(), self.previous[name])
            self.assertEqual((self.target / name).read_bytes(),
                             (self.package / REMOTE_FOLDER / name).read_bytes())
        phases = [json.loads(line)['phase'] for line in
                  (self.archive / 'upgrade.jsonl').read_text().splitlines()]
        self.assertEqual(phases, ['planned', 'previous_archived', 'previous_held', 'published', 'verified'])
        self.assertEqual(list(self.target.parent.glob('.pspdock-msc-*')), [])
        self.assertEqual(self.original.read_bytes(), b'ORIGINAL WORKING HOMEBREW')
        self.assertEqual(self.savedata.read_bytes(), b'ORIGINAL SAVEDATA')

    def test_upgrade_refuses_changed_previous_file_extra_file_and_wrong_identity(self):
        for case in ('changed', 'extra', 'identity'):
            with self.subTest(case=case):
                # Each subcase has an independent device and receipt.
                if self.target.exists():
                    for path in self.target.iterdir():
                        path.unlink()
                    self.target.rmdir()
                self.prepare_upgrade()
                if case == 'changed':
                    (self.target / 'dockmsc.prx').write_bytes(b'UNKNOWN BINARY')
                elif case == 'extra':
                    (self.target / 'NOT-OURS').write_bytes(b'KEEP')
                else:
                    data = json.loads(self.receipt.read_text())
                    data['identity']['usb_device_id'] = 'OTHER'
                    self.receipt.write_text(json.dumps(data))
                before = {p.name: p.read_bytes() for p in self.target.iterdir()}
                with self.assertRaises(ValueError):
                    self.upgrade()
                self.assertFalse(self.archive.exists())
                self.assertEqual(before, {p.name: p.read_bytes() for p in self.target.iterdir()})

    def test_upgrade_refuses_corrupt_new_package_before_backup_or_psp_write(self):
        self.prepare_upgrade()
        (self.package / REMOTE_FOLDER / 'dockmsc.prx').write_bytes(b'CORRUPT')
        with self.assertRaises(ValueError):
            self.upgrade()
        self.assertFalse(self.archive.exists())
        self.assertEqual(self.previous, {p.name: p.read_bytes() for p in self.target.iterdir()})

    def test_upgrade_device_change_during_staging_leaves_old_published(self):
        self.prepare_upgrade()
        with self.assertRaises(ValueError):
            self.upgrade(Mock(side_effect=[True, True, False]))
        self.assertEqual(self.previous, {p.name: p.read_bytes() for p in self.target.iterdir()})
        self.assertEqual(len(list(self.target.parent.glob('.pspdock-msc-*'))), 1)
        self.assertEqual((self.archive / 'files/dockmsc.prx').read_bytes(), self.previous['dockmsc.prx'])

    def test_upgrade_failed_publish_restores_verified_old_directory(self):
        self.prepare_upgrade()
        original_rename = Path.rename
        def fail_publish(source, destination):
            if source.name.startswith('.pspdock-msc-') and 'previous-' not in source.name:
                raise OSError('simulated rename failure')
            return original_rename(source, destination)
        with patch.object(Path, 'rename', fail_publish), self.assertRaises(OSError):
            self.upgrade()
        self.assertEqual(self.previous, {p.name: p.read_bytes() for p in self.target.iterdir()})
        self.assertIn('previous_restored', (self.archive / 'upgrade.jsonl').read_text())
        self.assertEqual(list(self.target.parent.glob('.pspdock-msc-previous-*')), [])

    def test_upgrade_failed_journal_after_old_rename_restores_verified_old_directory(self):
        self.prepare_upgrade()
        original_dump = self.installer.json.dump
        def fail_held_note(record, writer, **kwargs):
            if record.get('phase') == 'previous_held':
                raise OSError('simulated PC journal failure')
            return original_dump(record, writer, **kwargs)
        with patch.object(self.installer.json, 'dump', side_effect=fail_held_note):
            with self.assertRaises(OSError):
                self.upgrade()
        self.assertEqual(self.previous, {p.name: p.read_bytes() for p in self.target.iterdir()})
        self.assertIn('previous_restored', (self.archive / 'upgrade.jsonl').read_text())
        self.assertEqual(list(self.target.parent.glob('.pspdock-msc-previous-*')), [])
        self.assertEqual(self.original.read_bytes(), b'ORIGINAL WORKING HOMEBREW')

    def test_upgrade_device_change_after_old_rename_never_rolls_back_on_unknown_mount(self):
        self.prepare_upgrade()
        with self.assertRaises(ValueError):
            self.upgrade(Mock(side_effect=[True, True, True, False, False]))
        self.assertFalse(self.target.exists())
        held = list(self.target.parent.glob('.pspdock-msc-previous-*'))
        self.assertEqual(len(held), 1)
        self.assertEqual((held[0] / 'dockmsc.prx').read_bytes(), self.previous['dockmsc.prx'])
        self.assertIn('manual_recovery_required', (self.archive / 'upgrade.jsonl').read_text())

    def test_upgrade_archive_on_psp_is_rejected_without_write(self):
        self.prepare_upgrade()
        self.archive = self.psp / 'previous-test'
        with self.assertRaises(ValueError):
            self.upgrade()
        self.assertFalse(self.archive.exists())


if __name__ == '__main__':
    unittest.main()
