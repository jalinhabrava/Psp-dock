from pathlib import Path
import json
import tempfile
import unittest

from psp_backup import backup
from psp_composite_install import install_package, plan_install
from psp_composite_package import FILES, REMOTE_FOLDER, digest


SERIAL = '123456789'
USB_ID = r'USB\VID_054C&PID_02D2\COMPOSITE_TEST_ONLY'


class CompositeInstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.psp = self.base / 'fake-PSP'
        (self.psp / 'PSP/GAME/PSPDock').mkdir(parents=True)
        (self.psp / 'PSP/GAME/PSPDock/EBOOT.PBP').write_bytes(b'stable app')
        self.savedata = self.psp / 'PSP/SAVEDATA/TEST/SAVE.BIN'
        self.savedata.parent.mkdir(parents=True)
        self.savedata.write_bytes(b'precious save')
        self.snapshot = Path(backup(self.psp, self.base / 'backups', 'critical',
            SERIAL, USB_ID, reserve_bytes=0)['directory'])
        self.package = self.base / 'package'
        folder = self.package / REMOTE_FOLDER
        folder.mkdir(parents=True)
        records = {}
        for name in FILES:
            path = folder / name
            path.write_bytes(('test fixture ' + name).encode('ascii'))
            records[name] = {'size': path.stat().st_size, 'sha256': digest(path)}
        (self.package / 'manifest.json').write_text(json.dumps({
            'schema': 'pspdock-composite-test-package-v1', 'revision': '0204',
            'folder': REMOTE_FOLDER, 'files': records}), encoding='utf-8')

    def install(self, same_device=lambda: True):
        return install_package(self.package, self.psp, self.snapshot, SERIAL,
                               USB_ID, same_device=same_device)

    def test_only_new_folder_is_published_and_all_hashes_match(self):
        result = self.install()
        self.assertTrue(result['installed'])
        self.assertFalse(result['hardware_execution_verified'])
        self.assertFalse(result['autorun'])
        self.assertEqual(self.savedata.read_bytes(), b'precious save')
        self.assertEqual((self.psp / 'PSP/GAME/PSPDock/EBOOT.PBP').read_bytes(), b'stable app')
        for name in FILES:
            self.assertEqual((self.psp / REMOTE_FOLDER / name).read_bytes(),
                             (self.package / REMOTE_FOLDER / name).read_bytes())
        with self.assertRaises(FileExistsError):
            self.install()

    def test_changed_backup_or_corrupt_package_fails_before_psp_write(self):
        self.savedata.write_bytes(b'changed after backup')
        with self.assertRaisesRegex(ValueError, 'actual'):
            self.install()
        self.assertFalse((self.psp / REMOTE_FOLDER).exists())
        self.savedata.write_bytes(b'precious save')
        (self.package / REMOTE_FOLDER / 'dockcomposite.prx').write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'Integridad'):
            self.install()
        self.assertFalse((self.psp / REMOTE_FOLDER).exists())

    def test_wrong_psp_or_mid_copy_disconnect_is_not_published(self):
        with self.assertRaisesRegex(ValueError, 'corresponde'):
            plan_install(self.package, self.psp, self.snapshot, SERIAL, USB_ID + '-other')
        checks = iter((True, False))
        with self.assertRaisesRegex(ValueError, 'durante'):
            self.install(same_device=lambda: next(checks))
        self.assertFalse((self.psp / REMOTE_FOLDER).exists())
        self.assertEqual(self.savedata.read_bytes(), b'precious save')


if __name__ == '__main__':
    unittest.main()
