from pathlib import Path
import tempfile
import unittest

from tools.psp.psp_composite_package import FIRMWARE, FILES, REMOTE_FOLDER, make_package, verify_package


@unittest.skipUnless((FIRMWARE / 'dist/dockcomposite.elf').is_file(),
                     'Compile the read-only PSP driver before packaging')
class ReadOnlyPackageTests(unittest.TestCase):
    def test_package_contains_only_test_app_probe_and_license_not_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'new-PC-package'
            report = make_package(target)
            self.assertFalse(report['installed_on_psp'])
            self.assertFalse(report['hardware_execution_verified'])
            manifest = verify_package(target)
            self.assertEqual(set(manifest['files']), set(FILES))
            self.assertIsNone(manifest['session_time_limit'])
            self.assertFalse(manifest['save_return_supported'])
            self.assertFalse((target / 'PSP/GAME/PSPDock').exists())
            self.assertFalse((target / 'PSP/SAVEDATA').exists())
            with self.assertRaises(FileExistsError):
                make_package(target)
            probe = target / REMOTE_FOLDER / 'PROBE.DAT'
            probe.write_bytes(b'ALTERED')
            with self.assertRaisesRegex(ValueError, 'Integridad'):
                verify_package(target)


if __name__ == '__main__':
    unittest.main()
