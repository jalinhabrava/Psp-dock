"""Native package bytes, branded PBP and independent 0300 schema gates."""
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import psp_native_package as package
from tests.test_psp_msc_package import pbp, sfo


def abi_for(raw=b'NATIVE-ELF-FIXTURE'):
    return {'revision': '0300', 'elf_machine': 'MIPS', 'kernel_module_verified': True,
            'function_descriptors_verified': True, 'hid_function_interfaces': 1,
            'hid_input_bytes': 8, 'hid_feature_bytes': 32, 'protocol': 'PDK2/v2',
            'storage_backend': 'native Sony USBStor_Driver', 'kernel_filesystem_imports': [],
            'early_sony_boot_imports': [], 'expected_pid': '01CD',
            'wire_identity_verified': False, 'sony_composition_physically_verified': False,
            'real_memory_stick_access_physically_verified': False,
            'safe_phase_is_windows_eject_proof': False, 'sha256': hashlib.sha256(raw).hexdigest()}


def fixture_native_package(root: Path) -> Path:
    folder = root / package.REMOTE_FOLDER
    folder.mkdir(parents=True)
    blobs = {'EBOOT.PBP': pbp(title=package.TITLE), 'docknative.prx': b'~PSP-NATIVE-TEST-FIXTURE',
             'LICENSE-FuSa-GPL-3.0.txt': b'Fixture GPL text', 'LICENSE-DejaVu.txt': b'Fixture font license'}
    records = {}
    for name, raw in blobs.items():
        (folder / name).write_bytes(raw)
        records[name] = {'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw)}
    manifest = {'schema': package.SCHEMA, 'revision': '0300', 'folder': package.REMOTE_FOLDER,
                'expected_usb_identity': dict(package.USB_IDENTITY), 'synthetic_storage': False,
                'storage_backend': 'native Sony USBStor_Driver', 'storage_read_only': False,
                'real_memory_stick_access_requested': True, 'installed_on_psp': False,
                'hardware_execution_verified': False, 'compiled_abi': abi_for(),
                'user_module_verified': True, 'files': records,
                'xmb': package.audit_pbp(blobs['EBOOT.PBP'])}
    (root / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    return root


class NativePackageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_exact_native_title_and_approved_art(self):
        visual = package.audit_pbp(pbp(title=package.TITLE))
        self.assertEqual(visual['title'], package.TITLE)
        self.assertEqual(set(visual['artwork']), {'ICON0.PNG', 'PIC0.PNG', 'PIC1.PNG'})
        for title in ('PSP Dock - USB Storage + HID 0207', 'PSP Dock'):
            with self.subTest(title=title), self.assertRaises(ValueError):
                package.audit_pbp(pbp(title=title))

    def test_sfo_duplicate_and_corrupt_art_are_rejected(self):
        with self.assertRaises(ValueError):
            package.audit_pbp(pbp(sfo_data=sfo(package.TITLE, duplicate=True)))
        original = (package.ASSETS / 'ICON0.PNG').read_bytes()
        changed = original[:-1] + bytes([original[-1] ^ 1])
        with self.assertRaises(ValueError):
            package.audit_pbp(pbp(title=package.TITLE, replacements={'ICON0.PNG': changed}))

    def test_native_schema_rejects_legacy_identity_or_fake_physical_acceptance(self):
        root = fixture_native_package(self.root / 'package')
        file = root / 'manifest.json'
        original = json.loads(file.read_text())
        for key, value in [('schema', 'pspdock-msc-test-package-v1'), ('revision', '0207'),
                           ('folder', 'PSP/GAME/PSPDockStorageTest'), ('synthetic_storage', True),
                           ('storage_read_only', True), ('hardware_execution_verified', True)]:
            changed = {**original, key: value};file.write_text(json.dumps(changed))
            with self.subTest(key=key), self.assertRaises(ValueError):
                package.verify_package(root)
        file.write_text(json.dumps(original))
        self.assertEqual(package.verify_package(root)['revision'], '0300')

    def test_changed_firmware_or_extra_file_is_rejected(self):
        root = fixture_native_package(self.root / 'package')
        binary = root / package.REMOTE_FOLDER / 'docknative.prx'
        old = binary.read_bytes();binary.write_bytes(b'X' + old[1:])
        with self.assertRaises(ValueError):
            package.verify_package(root)
        binary.write_bytes(old)
        (binary.parent / 'OTHER.PRX').write_bytes(b'not reviewed')
        with self.assertRaises(ValueError):
            package.verify_package(root)

    def test_package_preparation_audits_actual_elf_and_requires_matching_user_build(self):
        source = fixture_native_package(self.root / 'source') / package.REMOTE_FOLDER
        raw = b'NATIVE-ELF-FIXTURE';(source / 'docknative.elf').write_bytes(raw)
        abi = abi_for(raw)
        producer = {**abi, 'pbp_sha256': package.digest(source / 'EBOOT.PBP'),
                    'user_app': {'user_module_verified': True, 'privileged_usb_or_kernel_imports': []}}
        (source / 'compiled-audit.json').write_text(json.dumps(producer))
        auditor = SimpleNamespace(audit=Mock(return_value=abi))
        with patch.object(package, '_kernel_auditor', return_value=auditor):
            result = package.make_package(self.root / 'native', dist=source)
        auditor.audit.assert_called_once_with(raw)
        self.assertFalse(result['hardware_execution_verified'])
        self.assertEqual(result['files'], 4)
        package.verify_package(self.root / 'native')
        producer['pbp_sha256'] = '0'*64
        (source / 'compiled-audit.json').write_text(json.dumps(producer))
        with patch.object(package, '_kernel_auditor', return_value=auditor), self.assertRaises(ValueError):
            package.make_package(self.root / 'rejected', dist=source)
        self.assertFalse((self.root / 'rejected').exists())

    def test_missing_build_or_existing_destination_is_never_overwritten(self):
        empty = self.root / 'empty';empty.mkdir()
        with self.assertRaises(ValueError):
            package.make_package(self.root / 'absent', dist=empty)
        self.assertFalse((self.root / 'absent').exists())
        target = self.root / 'existing';target.mkdir();(target / 'KEEP').write_text('keep')
        with self.assertRaises(FileExistsError):
            package.make_package(target, dist=empty)
        self.assertEqual((target / 'KEEP').read_text(), 'keep')


if __name__ == '__main__':
    unittest.main()
