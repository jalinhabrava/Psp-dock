"""Synthetic NTFS 8.3 aliases exercise the same roots used by Windows CI."""
import ctypes
import os
from pathlib import Path
import tempfile
import unittest

from dock import copy_game, game_inventory, tree_signature
from psp_dock.emulation.retro_runtime import private_save_paths
from psp_dock.emulation.retro_saves import copy_sram_verified, plan_retro_saves, _sha


@unittest.skipUnless(os.name == 'nt', 'NTFS short paths require Windows')
class WindowsPathAliasTests(unittest.TestCase):
    def setUp(self):
        from ctypes import wintypes

        temporary = tempfile.TemporaryDirectory(prefix='psp-dock-path-alias-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / 'Synthetic Dock Directory'
        self.root.mkdir()
        get_short = ctypes.WinDLL('kernel32', use_last_error=True).GetShortPathNameW
        get_short.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        get_short.restype = wintypes.DWORD
        required = get_short(str(self.root), None, 0)
        if not required:
            raise ctypes.WinError(ctypes.get_last_error())
        buffer = ctypes.create_unicode_buffer(required)
        written = get_short(str(self.root), buffer, required)
        if not written or written >= required:
            raise ctypes.WinError(ctypes.get_last_error())
        self.short = Path(buffer.value)
        if os.path.normcase(str(self.short)) == os.path.normcase(str(self.root)):
            self.skipTest('This synthetic directory has no NTFS 8.3 alias')
        self.assertEqual(self.short.resolve(), self.root)

    def test_inventory_preserves_relative_ids_for_short_and_long_roots(self):
        iso = self.root / 'ISO/Synthetic.iso'
        iso.parent.mkdir()
        iso.write_bytes(b'synthetic ISO inventory fixture')
        eboot = self.root / 'PSP/GAME/PocketApp/EBOOT.PBP'
        eboot.parent.mkdir(parents=True)
        eboot.write_bytes(b'synthetic homebrew inventory fixture')
        canonical = game_inventory(self.root)
        self.assertEqual(len(canonical), 2)
        self.assertEqual(game_inventory(self.short), canonical)

    def test_directory_transfer_and_signatures_accept_short_source_root(self):
        source = self.root / 'Source Homebrew'
        (source / 'assets').mkdir(parents=True)
        (source / 'EBOOT.PBP').write_bytes(b'synthetic homebrew fixture')
        (source / 'assets/image.bin').write_bytes(b'synthetic asset fixture')
        source_alias = self.short / source.name
        signature = tree_signature(source)
        self.assertEqual(tree_signature(source_alias), signature)
        target = self.root / 'Library/Homebrew'
        self.assertEqual(copy_game(source_alias, target), signature)
        self.assertEqual(tree_signature(target), signature)
        self.assertEqual((source / 'assets/image.bin').read_bytes(), b'synthetic asset fixture')

    def test_sram_plan_copy_and_backup_accept_mixed_path_spellings(self):
        psp = self.root / 'PSP fixture'
        storage = self.root / 'PC fixture'
        storage.mkdir()
        folder = psp / 'PSP/GAME/gpSP'
        (folder / 'ROMS').mkdir(parents=True)
        (folder / 'SAVES').mkdir()
        (folder / 'ROMS/Golden.gba').write_bytes(b'synthetic ROM fixture')
        source = folder / 'SAVES/Golden.sav'
        source.write_bytes(b'a' * 32768)
        rom_id, emulator_id, volume = 'a' * 24, 'b' * 24, 'synthetic volume'
        roms = [{'id': rom_id, 'emulator_id': emulator_id, 'system': 'gba',
                 'format': 'GBA', 'title': 'Golden',
                 'relative': 'PSP/GAME/gpSP/ROMS/Golden.gba'}]
        emulators = [{'id': emulator_id, 'name': 'gpSP Kai',
                      'relative': 'PSP/GAME/gpSP/EBOOT.PBP', 'systems': ['gba']}]
        target = private_save_paths(storage, volume, rom_id)[0]
        target.parent.mkdir(parents=True)
        target.write_bytes(b'b' * 32768)

        def plan(root, storage_root):
            return plan_retro_saves(root=root, storage_root=storage_root, volume=volume,
                                    roms=roms, emulators=emulators, baselines={})

        canonical = plan(psp, storage)
        psp_alias, storage_alias = self.short / psp.name, self.short / storage.name
        self.assertEqual(canonical[0]['status'], 'conflict')
        self.assertEqual(plan(psp_alias, storage_alias), canonical)
        result = copy_sram_verified(
            source=source, target=target, source_root=psp_alias,
            target_root=storage_alias, storage_root=storage_alias,
            backup_dir=storage / 'backups', journal_dir=storage / 'journals',
            expected_source=_sha(source), expected_target=_sha(target), system='gba')
        self.assertEqual(result, _sha(source))
        self.assertEqual(target.read_bytes(), b'a' * 32768)
        backups = list((storage / 'backups').glob('*.bak'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), b'b' * 32768)
        self.assertFalse(list((storage / 'journals').glob('*.json')))
        self.assertEqual(plan(psp_alias, storage_alias)[0]['status'], 'identical')


if __name__ == '__main__':
    unittest.main()
