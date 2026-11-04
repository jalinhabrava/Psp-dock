import gzip
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import psp_dock.emulation.psp_retro_backup as retro


SERIAL = '2958455453'
USB = r'USB\VID_054C&PID_02D2\8A67FB08B1B4B5A6'


class RetroBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.psp = self.base / 'psp'
        self.emulator = self.psp / 'PSP/GAME/gpSP-Kai'
        self.emulator.mkdir(parents=True)
        (self.emulator / 'EBOOT.PBP').write_bytes(b'not an executable test fixture')
        self.put('GBA/SAV/game.sav', b'one')
        self.put('GBA/SS/game.svs', b'opaque PSP state')
        self.put('GBA/ROM/game.gba', b'ROM must not be copied')
        self.target = self.base / 'archive'

    def put(self, name, data):
        path = self.emulator / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def create(self, **kwargs):
        return retro.backup(self.psp, self.target, SERIAL, USB,
                            same_device=kwargs.pop('same_device', lambda: True), reserve_bytes=0, **kwargs)

    def test_selection_preserves_opaque_states_and_saves_but_no_roms_or_programs(self):
        self.put('GBA/SS/accidental.gba', b'not a state')
        self.put('GBA/SS/bios.bin', b'not a state')
        self.put('GBA/SS/slot-preview.png', b'opaque thumbnail')
        self.put('game.000', b'numbered state')
        self.put('game.state1', b'numbered state')
        self.put('unclassified.txt', b'not selected')
        unknown = self.psp / 'PSP/GAME/UnknownApp/SAV'
        unknown.mkdir(parents=True)
        (unknown.parent / 'EBOOT.PBP').write_bytes(b'unknown')
        (unknown / 'unrecognized.sav').write_bytes(b'keep outside scope')
        result = retro.selection(self.psp)
        selected = set(result['files'])
        self.assertEqual(len(selected), 5)
        self.assertTrue(any(path.endswith('game.svs') for path in selected))
        self.assertFalse(any(path.endswith(('.gba', '.bin', '.PBP', 'unrecognized.sav')) for path in selected))
        self.assertEqual(result['unclassified_count'], 1)
        self.assertEqual(len(result['excluded_in_save_dirs']), 2)
        self.assertFalse(result['savestate_compatibility_verified'])

    def test_backup_reads_psp_only_and_verifies_identity_hashes_and_exact_archive(self):
        real_open = Path.open
        def readonly_source(path, mode='r', *args, **kwargs):
            if Path(path).is_relative_to(self.psp) and any(flag in mode for flag in 'wax+'):
                self.fail('A PSP file was opened for writing: ' + str(path))
            return real_open(path, mode, *args, **kwargs)
        with patch.object(Path, 'open', readonly_source):
            result = self.create()
        self.assertEqual(result['file_count'], 2)
        self.assertFalse((self.target / retro.INCOMPLETE).exists())
        verified = retro.verify_archive(self.target)
        self.assertEqual((verified['volume_serial'], verified['usb_device_id']), (SERIAL, USB))
        self.assertEqual(retro.matching_backup(self.psp, self.target, SERIAL, USB,
                                               same_device=lambda: True)['status'], 'verified')
        manifest = json.loads((self.target / 'manifest.json').read_text())
        self.assertTrue(all(len(row['sha256']) == 64 for row in manifest['files'].values()))
        self.assertEqual((self.emulator / 'GBA/SAV/game.sav').read_bytes(), b'one')

    def test_masterboy_numbered_gzip_state_is_copied_opaque_without_conversion(self):
        emulator = self.psp / 'PSP/GAME/masterboy 20.1'
        state = emulator / 'Roms GG/SAVE/Aah! Harimanada (J).sv0.gz'
        state.parent.mkdir(parents=True)
        (emulator / 'EBOOT.PBP').write_bytes(b'known folder test fixture')
        original = gzip.compress(b'opaque MasterBoy PSP savestate')
        state.write_bytes(original)
        relative = state.relative_to(self.psp).as_posix()
        selected = retro.selection(self.psp)
        self.assertEqual(selected['files'][relative]['reason'], 'compressed_save_opaque')
        self.assertNotIn(relative, selected['excluded_in_save_dirs'])
        with patch.object(gzip, 'decompress', side_effect=AssertionError('state decompressed')):
            result = self.create()
        self.assertEqual(result['file_count'], 3)
        self.assertEqual((self.target / 'data' / relative).read_bytes(), original)
        self.assertEqual(state.read_bytes(), original)
        self.assertFalse(result['savestate_compatibility_verified'])
        self.assertEqual(retro.verify_archive(self.target)['status'], 'verified')

    def test_compressed_roms_generic_gzip_and_states_outside_known_save_dirs_stay_excluded(self):
        self.put('GBA/SAV/ROM.gba.gz', b'compressed ROM')
        self.put('GBA/SAV/ROM.zip', b'ROM archive')
        self.put('GBA/SAV/notes.gz', b'generic archive')
        self.put('GBA/ROM/game.sv0.gz', b'outside save directory')
        unknown = self.psp / 'PSP/GAME/UnknownApp/SAVE'
        unknown.mkdir(parents=True)
        (unknown.parent / 'EBOOT.PBP').write_bytes(b'unknown emulator')
        (unknown / 'game.sv0.gz').write_bytes(b'unknown emulator state')
        selected = retro.selection(self.psp)
        self.assertEqual(selected['file_count'], 2)
        self.assertFalse(any(path.endswith(('.gz', '.zip')) for path in selected['files']))
        self.assertEqual(len(selected['excluded_in_save_dirs']), 3)

    def test_identity_change_after_copy_keeps_incomplete_bytes_and_never_verifies(self):
        calls = 0
        def same():
            nonlocal calls
            calls += 1
            return calls < 4
        with self.assertRaisesRegex(retro.RetroBackupError, 'identidad'):
            self.create(same_device=same)
        self.assertTrue((self.target / retro.INCOMPLETE).is_file())
        self.assertTrue(any((self.target / 'data').rglob('*.sav')))
        with self.assertRaises(retro.RetroBackupError):
            retro.verify_archive(self.target)

    def test_fresh_source_hash_catches_earlier_file_changed_with_same_size_and_mtime(self):
        original = retro.copy_verified
        first = self.emulator / 'GBA/SAV/game.sav'
        stamp = first.stat()
        copies = 0
        def changed(source, target, size):
            nonlocal copies
            result = original(source, target, size)
            copies += 1
            if copies == 2:
                first.write_bytes(b'two')
                os.utime(first, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            return result
        with patch.object(retro, 'copy_verified', changed):
            with self.assertRaisesRegex(retro.RetroBackupError, 'después'):
                self.create()
        self.assertTrue((self.target / retro.INCOMPLETE).exists())

    def test_current_evidence_rejects_new_or_changed_source_files_and_wrong_identity(self):
        self.create()
        with self.assertRaisesRegex(retro.RetroBackupError, 'otra PSP'):
            retro.matching_backup(self.psp, self.target, '123', USB, same_device=lambda: True)
        self.put('GBA/SAV/new.sav', b'new')
        with self.assertRaisesRegex(retro.RetroBackupError, 'difieren'):
            retro.matching_backup(self.psp, self.target, SERIAL, USB, same_device=lambda: True)
        (self.emulator / 'GBA/SAV/new.sav').unlink()
        self.put('GBA/SAV/game.sav', b'two')
        with self.assertRaisesRegex(retro.RetroBackupError, 'actual'):
            retro.matching_backup(self.psp, self.target, SERIAL, USB, same_device=lambda: True)

    def test_offline_verify_rejects_corruption_and_unrecorded_data(self):
        self.create()
        copied = self.target / 'data/PSP/GAME/gpSP-Kai/GBA/SAV/game.sav'
        copied.write_bytes(b'two')
        with self.assertRaisesRegex(retro.RetroBackupError, 'corrupto'):
            retro.verify_archive(self.target)
        copied.write_bytes(b'one')
        (copied.parent / 'extra.sav').write_bytes(b'unrecorded')
        with self.assertRaisesRegex(retro.RetroBackupError, 'sin manifiesto'):
            retro.verify_archive(self.target)

    def test_manifest_absolute_traversal_ads_and_case_duplicates_are_rejected(self):
        self.create()
        file = self.target / 'manifest.json'
        original = json.loads(file.read_text())
        for unsafe in ('/PSP/GAME/gpSP/file.sav', '../PSP/GAME/gpSP/file.sav',
                       'PSP/GAME/gpSP/../file.sav', 'PSP/GAME/gpSP/file.sav:ads',
                       'PSP/GAME/gpSP//file.sav'):
            with self.subTest(path=unsafe):
                manifest = dict(original)
                manifest['files'] = {unsafe: next(iter(original['files'].values()))}
                manifest['file_count'] = 1
                file.write_text(json.dumps(manifest))
                with self.assertRaises(retro.RetroBackupError):
                    retro.verify_archive(self.target)
        file.write_text(json.dumps(original))

    def test_symlink_source_and_archive_data_are_rejected(self):
        link = self.emulator / 'GBA/SAV/linked.sav'
        try:
            link.symlink_to(self.emulator / 'GBA/SAV/game.sav')
        except OSError:
            self.skipTest('symlinks not supported')
        with self.assertRaisesRegex(retro.RetroBackupError, 'enlaces'):
            retro.selection(self.psp)
        link.unlink()
        self.create()
        file = self.target / 'data/PSP/GAME/gpSP-Kai/GBA/SAV/game.sav'
        file.unlink()
        file.symlink_to(self.emulator / 'GBA/SAV/game.sav')
        with self.assertRaisesRegex(retro.RetroBackupError, 'enlaces'):
            retro.verify_archive(self.target)

    def test_new_destination_required_and_psp_destination_refused(self):
        self.target.mkdir()
        with self.assertRaises(FileExistsError):
            self.create()
        with self.assertRaises(retro.RetroBackupError):
            retro.backup(self.psp, self.psp / 'archive', SERIAL, USB,
                         same_device=lambda: True, reserve_bytes=0)
        self.assertFalse((self.psp / 'archive').exists())

    def test_selected_large_file_fails_instead_of_claiming_complete_backup(self):
        with patch.object(retro, 'MAX_FILE_BYTES', 8):
            with self.assertRaisesRegex(retro.RetroBackupError, '64 MiB'):
                retro.selection(self.psp)

    def test_empty_snapshot_is_verified_and_detects_later_created_save(self):
        (self.emulator / 'GBA/SAV/game.sav').unlink()
        (self.emulator / 'GBA/SS/game.svs').unlink()
        self.assertEqual(self.create()['file_count'], 0)
        self.assertEqual(retro.verify_archive(self.target)['file_count'], 0)
        self.put('GBA/SAV/new.sav', b'new')
        with self.assertRaises(retro.RetroBackupError):
            retro.matching_backup(self.psp, self.target, SERIAL, USB, same_device=lambda: True)

    def test_cli_offline_verify_never_identifies_usb(self):
        self.create()
        with patch.object(retro, 'identify', side_effect=AssertionError('USB accessed')), patch('builtins.print'):
            self.assertEqual(retro.main(['--verify', str(self.target)]), 0)


if __name__ == '__main__':
    unittest.main()
