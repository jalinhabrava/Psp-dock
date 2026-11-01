"""PBP layout/artwork identity and package corruption rejection."""
import hashlib
import json
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import psp_msc_package as package


def sfo(title=package.TITLE, *, duplicate=False):
    text = title.encode('utf-8') + b'\0'
    capacity = (len(text) + 3) & ~3
    count = 2 if duplicate else 1
    keys = 20 + count * 16
    data = keys + 8
    header = struct.pack('<5I', 0x46535000, 0x101, keys, data, count)
    entry = struct.pack('<HHIII', 0, 0x204, len(text), capacity, 0)
    return header + entry * count + b'TITLE\0\0\0' + text.ljust(capacity, b'\0')


def pbp(title=package.TITLE, *, sfo_data=None, replacements=None):
    replacements = replacements or {}
    art = {name: replacements.get(name, (package.FIRMWARE / 'assets' / name).read_bytes())
           for _, name, _ in package.ARTWORK}
    sections = [sfo(title) if sfo_data is None else sfo_data, art['ICON0.PNG'], b'',
                art['PIC0.PNG'], art['PIC1.PNG'], b'',
                b'\x7fELF' + bytes(32000), b'']
    offsets, cursor = [], 40
    for section in sections:
        offsets.append(cursor)
        cursor += len(section)
    return struct.pack('<4sI8I', b'\0PBP', 0x10000, *offsets) + b''.join(sections)


def fixture_package(root: Path) -> Path:
    directory = root / package.REMOTE_FOLDER
    directory.mkdir(parents=True)
    data = {'EBOOT.PBP': pbp(), 'dockmsc.prx': b'~PSP-SYNTHETIC-TEST-FIXTURE',
            'LICENSE-FuSa-GPL-3.0.txt': b'Fixture GPL text',
            'LICENSE-DejaVu.txt': b'Fixture font license'}
    records = {}
    for name, raw in data.items():
        (directory / name).write_bytes(raw)
        records[name] = {'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw)}
    manifest = {'schema': 'pspdock-msc-test-package-v1', 'revision': '0206',
                'expected_usb_identity': dict(package.USB_IDENTITY),
                'folder': package.REMOTE_FOLDER, 'synthetic_storage': True,
                'real_memory_stick_access': False, 'storage_read_only': True,
                'files': records, 'xmb': package.audit_pbp(data['EBOOT.PBP'])}
    (root / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    return root


class MSCPackageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_valid_pbp_contains_exact_reviewed_art_and_title(self):
        report = package.audit_pbp(pbp(), assets=package.FIRMWARE / 'assets')
        self.assertEqual(report['title'], package.TITLE)
        self.assertTrue(report['embedded'])
        self.assertEqual(report['artwork']['PIC1.PNG']['width'], 480)
        self.assertEqual(report['artwork']['ICON0.PNG']['height'], 80)

    def test_bad_pbp_headers_offsets_or_executable_are_rejected(self):
        valid = pbp()
        mutations = []
        for offset, value in ((4, 0x9999), (8, 39), (12, 0), (36, len(valid) + 1)):
            raw = bytearray(valid)
            struct.pack_into('<I', raw, offset, value)
            mutations.append(raw)
        raw = bytearray(valid)
        executable = struct.unpack_from('<I', raw, 32)[0]
        raw[executable:executable + 4] = b'NOPE'
        mutations.extend([raw, valid[:39]])
        for raw in mutations:
            with self.subTest(length=len(raw)), self.assertRaises(ValueError):
                package.audit_pbp(bytes(raw))

    def test_sfo_version_overlapping_keytable_type_and_duplicates_are_rejected(self):
        variants = []
        for offset, fmt, value in ((4, '<I', 0x999), (22, '<H', 0x404)):
            data = bytearray(sfo())
            struct.pack_into(fmt, data, offset, value)
            variants.append(data)
        data = bytearray(sfo())
        # Both pointers still locate TITLE, but the declared key table
        # illegally overlaps the entry array. The old parser accepted it.
        struct.pack_into('<I', data, 8, 20)
        struct.pack_into('<H', data, 20, 16)
        variants.append(data)
        variants.append(sfo(duplicate=True))
        for data in variants:
            with self.subTest(data=data[:40]), self.assertRaises(ValueError):
                package.audit_pbp(pbp(sfo_data=bytes(data)))
        with self.assertRaises(ValueError):
            package.audit_pbp(pbp(title='Unrelated homebrew'))

    def test_missing_wrong_size_or_different_embedded_art_is_rejected(self):
        with self.assertRaises(ValueError):
            package.audit_pbp(pbp(replacements={'PIC0.PNG': b''}))
        original = (package.FIRMWARE / 'assets/ICON0.PNG').read_bytes()
        wrong_size = bytearray(original)
        struct.pack_into('>I', wrong_size, 16, 143)
        with self.assertRaises(ValueError):
            package.audit_pbp(pbp(replacements={'ICON0.PNG': bytes(wrong_size)}))
        changed = original[:-1] + bytes([original[-1] ^ 1])
        with self.assertRaises(ValueError):
            package.audit_pbp(pbp(replacements={'ICON0.PNG': changed}),
                              assets=package.FIRMWARE / 'assets')

    def test_kernel_corruption_and_unexpected_package_file_are_rejected(self):
        root = fixture_package(self.root / 'package')
        self.assertEqual(package.verify_package(root)['revision'], '0206')
        binary = root / package.REMOTE_FOLDER / 'dockmsc.prx'
        original = binary.read_bytes()
        binary.write_bytes(b'X' + original[1:])
        with self.assertRaises(ValueError):
            package.verify_package(root)
        binary.write_bytes(original)
        (binary.parent / 'EXTRA.PRX').write_bytes(b'UNREVIEWED')
        with self.assertRaises(ValueError):
            package.verify_package(root)

    def test_artwork_manifest_cannot_disagree_with_eboot(self):
        root = fixture_package(self.root / 'package')
        path = root / 'manifest.json'
        data = json.loads(path.read_text())
        data['xmb']['artwork']['PIC1.PNG']['sha256'] = '0' * 64
        path.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            package.verify_package(root)

    def test_package_for_old_pid_cannot_be_installed_with_msc_host(self):
        root = fixture_package(self.root / 'package')
        path = root / 'manifest.json'
        data = json.loads(path.read_text())
        data['expected_usb_identity']['pid'] = '01CB'
        path.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            package.verify_package(root)

    def test_missing_required_build_file_stops_before_package_creation(self):
        dist = self.root / 'dist'
        dist.mkdir()
        for name in package.FILES:
            (dist / name).write_bytes(b'present')
        target = self.root / 'new-package'
        with self.assertRaises(ValueError):
            package.make_package(target, dist=dist)
        self.assertFalse(target.exists())

    def test_packaging_audits_elf_then_copies_exact_artifacts_without_overwrite(self):
        source_package = fixture_package(self.root / 'fixture')
        dist = source_package / package.REMOTE_FOLDER
        (dist / 'dockmsc.elf').write_bytes(b'ELF-AUDITOR-INPUT')
        auditor = SimpleNamespace(audit=Mock(return_value={'physical_enumeration_verified': False}))
        loader = SimpleNamespace(exec_module=Mock())
        spec = SimpleNamespace(loader=loader)
        target = self.root / 'new-package'
        with patch.object(package.importlib.util, 'spec_from_file_location', return_value=spec), \
             patch.object(package.importlib.util, 'module_from_spec', return_value=auditor):
            report = package.make_package(target, dist=dist)
        auditor.audit.assert_called_once_with(b'ELF-AUDITOR-INPUT')
        self.assertFalse(report['installed_on_psp'])
        self.assertFalse(report['hardware_execution_verified'])
        package.verify_package(target)
        before = (target / 'manifest.json').read_bytes()
        with self.assertRaises(FileExistsError):
            package.make_package(target, dist=dist)
        self.assertEqual((target / 'manifest.json').read_bytes(), before)

    def test_digest_does_not_require_python311_file_digest(self):
        path = self.root / 'data.bin'
        data = bytes(range(256)) * 5000
        path.write_bytes(data)
        with patch.object(hashlib, 'file_digest', create=True,
                          side_effect=AssertionError('Unavailable on Python 3.10')):
            self.assertEqual(package.digest(path), hashlib.sha256(data).hexdigest())


if __name__ == '__main__':
    unittest.main()
