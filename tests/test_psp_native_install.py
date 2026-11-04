"""Real temporary files and backups; no hardware discovery or PSP writes."""
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tools.psp.psp_native_package import FILES, REMOTE_FOLDER
from tests.test_psp_native_package import fixture_native_package

SERIAL = '12345678'
USB = r'USB\VID_054C&PID_02D2\TEST-PSP'


def load_isolated():
    def forbidden(*args, **kwargs):
        raise AssertionError('Unit test attempted hardware discovery')
    dock = ModuleType('dock');dock.ROOT = Path(__file__).parent;dock.windows_devices = forbidden
    eject = ModuleType('lab_eject');eject.ppsspp_open = eject.real_dock_open = forbidden
    usb = ModuleType('psp_usb');usb.identify = forbidden;usb.PspStorage = object
    usb.UsbTransitionError = type('UsbTransitionError', (RuntimeError,), {})
    with patch.dict(sys.modules, {'dock': dock, 'tools.labs.lab_eject': eject, 'psp_dock.devices.psp_usb': usb}):
        for name in ('tools.psp.psp_backup', 'tools.psp.psp_composite_install', 'tools.psp.psp_native_install'):
            sys.modules.pop(name, None)
        backup = importlib.import_module('tools.psp.psp_backup')
        installer = importlib.import_module('tools.psp.psp_native_install')
    return backup, installer


class NativeInstallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backup, cls.installer = load_isolated()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory();self.root = Path(self.tmp.name)
        self.psp = self.root / 'psp'
        self.package = fixture_native_package(self.root / 'package')
        self.old = self.psp / 'PSP/GAME/PSPDockStorageTest/EBOOT.PBP'
        self.old.parent.mkdir(parents=True);self.old.write_bytes(b'0207 KEEP')
        self.original = self.psp / 'PSP/GAME/PSPDock/EBOOT.PBP'
        self.original.parent.mkdir();self.original.write_bytes(b'ORIGINAL KEEP')
        self.save = self.psp / 'PSP/SAVEDATA/SLOT/DATA.BIN'
        self.save.parent.mkdir(parents=True);self.save.write_bytes(b'SAVE KEEP')
        backup = self.backup.backup(self.psp, self.root / 'backups', 'critical', SERIAL, USB,
                                    verify_identity=lambda: True, reserve_bytes=0)
        self.snapshot = Path(backup['directory'])
        self.target = self.psp / REMOTE_FOLDER
        self.receipt = self.root / 'evidence' / 'native-install.json'

    def tearDown(self):
        self.tmp.cleanup()

    def plan(self, serial=SERIAL, usb_id=USB):
        return self.installer.plan_install(self.package, self.psp, self.snapshot, serial, usb_id)

    def install(self, same_device=lambda: True):
        return self.installer.install_package(self.package, self.psp, self.snapshot, SERIAL, USB,
                                               same_device=same_device, receipt=self.receipt)

    def test_plan_only_writes_nothing(self):
        before = {str(p): p.read_bytes() for p in self.psp.rglob('*') if p.is_file()}
        result = self.plan()
        self.assertFalse(result['installed']);self.assertFalse(result['autorun'])
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.psp.rglob('*') if p.is_file()})
        self.assertFalse(self.receipt.exists())

    def test_new_install_preserves_baselines_and_persists_verified_receipt(self):
        identity = Mock(return_value=True);result = self.install(identity)
        self.assertGreaterEqual(identity.call_count, 12)
        self.assertTrue(result['installed'])
        self.assertEqual(set(p.name for p in self.target.iterdir()), set(FILES))
        self.assertEqual(json.loads(self.receipt.read_text()), result)
        records = [json.loads(line) for line in Path(result['journal']).read_text().splitlines()]
        self.assertEqual(records[0]['phase'], 'planned');self.assertEqual(records[-1]['phase'], 'verified')
        self.assertEqual(self.old.read_bytes(), b'0207 KEEP')
        self.assertEqual(self.original.read_bytes(), b'ORIGINAL KEEP')
        self.assertEqual(self.save.read_bytes(), b'SAVE KEEP')

    def test_only_exact_xmb_02d2_identity_and_matching_backup_are_accepted(self):
        for serial, usb in [(SERIAL, USB.replace('02D2', '01CD')), (SERIAL, USB.replace('02D2', '01CC')),
                            (SERIAL, USB.replace('02D2', '01C8')), (SERIAL, USB + r'\OTHER'),
                            ('87654321', USB), (SERIAL, USB.replace('TEST-PSP', 'OTHER-PSP'))]:
            with self.subTest(serial=serial, usb=usb), self.assertRaises(ValueError):
                self.plan(serial, usb)
        self.assertFalse(self.target.exists());self.assertFalse(self.receipt.exists())

    def test_changed_critical_file_blocks_before_receipt_or_psp_staging(self):
        self.save.write_bytes(b'XAVE KEEP')
        with self.assertRaises(ValueError):
            self.install()
        self.assertFalse(self.receipt.exists())
        self.assertEqual(list(self.target.parent.glob('.pspdock-native-*')), [])

    def test_incomplete_backup_and_corrupt_package_block_installation(self):
        marker = self.snapshot / 'BACKUP-INCOMPLETE.txt';marker.write_text('incomplete')
        with self.assertRaises(ValueError):
            self.install()
        marker.unlink();(self.package / REMOTE_FOLDER / 'docknative.prx').write_bytes(b'changed')
        with self.assertRaises(ValueError):
            self.install()
        self.assertFalse(self.target.exists());self.assertFalse(self.receipt.exists())

    def test_identity_change_during_copy_preserves_partial_and_never_publishes(self):
        with self.assertRaises(ValueError):
            self.install(Mock(side_effect=[True, True, True, False]))
        self.assertFalse(self.target.exists())
        stages = list(self.target.parent.glob('.pspdock-native-*'))
        self.assertEqual(len(stages), 1)
        self.assertEqual(set(p.name for p in stages[0].iterdir()), {'EBOOT.PBP'})
        receipt = json.loads(self.receipt.read_text())
        self.assertFalse(receipt['installed'])
        self.assertIn('incomplete', Path(receipt['journal']).read_text())
        self.assertEqual(self.save.read_bytes(), b'SAVE KEEP')

    def test_disconnect_after_publish_never_removes_the_new_folder(self):
        with self.assertRaises(ValueError):
            self.install(lambda: not self.target.exists())
        self.assertTrue(self.target.is_dir())
        self.assertFalse(json.loads(self.receipt.read_text())['installed'])
        journal = self.receipt.with_name(self.receipt.name + '.journal.jsonl').read_text()
        self.assertIn('published', journal);self.assertIn('incomplete', journal)

    def test_existing_target_or_receipt_is_never_overwritten(self):
        self.target.mkdir();(self.target / 'KEEP').write_bytes(b'existing')
        with self.assertRaises(FileExistsError):
            self.install()
        self.assertEqual((self.target / 'KEEP').read_bytes(), b'existing')
        (self.target / 'KEEP').unlink();self.target.rmdir()
        self.receipt.parent.mkdir();self.receipt.write_text('PC KEEP')
        with self.assertRaises(FileExistsError):
            self.install()
        self.assertFalse(self.target.exists());self.assertEqual(self.receipt.read_text(), 'PC KEEP')

    def test_receipt_on_psp_is_rejected_before_staging(self):
        self.receipt = self.psp / 'evidence.json'
        with self.assertRaises(ValueError):
            self.install()
        self.assertFalse(self.receipt.exists());self.assertFalse(self.target.exists())

    def test_cli_defaults_to_plan_and_requires_receipt_for_install(self):
        args = ['--package', str(self.package), '--backup', str(self.snapshot), '--drive', 'G:\\']
        device = SimpleNamespace(drive='G:\\', volume_serial=SERIAL, usb_device_id=USB)
        fake_os = SimpleNamespace(name='nt', environ={}, fsync=os.fsync)
        with patch.object(self.installer, 'os', fake_os), patch.object(self.installer, 'identify', return_value=(device, 1)), \
                patch.object(self.installer, 'asdict', return_value={'volume_serial': SERIAL, 'usb_device_id': USB}), \
                patch.object(self.installer, 'plan_install', return_value={'installed': False}) as plan, \
                patch.object(self.installer, 'install_package') as install:
            self.assertEqual(self.installer.main(args), 0);plan.assert_called_once();install.assert_not_called()
            self.assertEqual(self.installer.main(args + ['--install']), 1);install.assert_not_called()


if __name__ == '__main__':
    unittest.main()
