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
from psp_msc_package import REMOTE_FOLDER, FILES, verify_package


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
    with patch.dict(sys.modules, {'dock': dock, 'lab_eject': eject, 'psp_usb': usb}):
        for name in ('psp_backup', 'psp_composite_install', 'psp_msc_install'):
            sys.modules.pop(name, None)
        backup = importlib.import_module('psp_backup')
        installer = importlib.import_module('psp_msc_install')
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


if __name__ == '__main__':
    unittest.main()
