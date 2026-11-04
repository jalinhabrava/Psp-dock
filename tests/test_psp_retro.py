"""Synthetic PSP homebrew/ROM discovery; no real console or game files."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from dock import game_inventory
from psp_dock.emulation.psp_retro import emulator_inventory, emulator_profile, retroarch_core, SYSTEMS


class RetroInventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.game = self.root / 'PSP' / 'GAME'
        self.game.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def install(self, name, roms=None):
        folder = self.game / name
        folder.mkdir()
        (folder / 'EBOOT.PBP').write_bytes(b'synthetic eboot')
        for filename in roms or []:
            dest = folder / filename
            dest.parent.mkdir(exist_ok=True, parents=True)
            dest.write_bytes(b'synthetic rom')
        return folder

    def test_known_emulator_is_no_longer_a_psp_game(self):
        self.install('gpSP', ['ROMS/Metroid Fusion.gba'])
        self.install('MyHomebrew')
        games = game_inventory(self.root)
        self.assertEqual([g['relative'] for g in games], ['PSP/GAME/MyHomebrew/EBOOT.PBP'])
        retro = emulator_inventory(self.root)
        self.assertEqual(retro['emulators'][0]['systems'], ['gba'])
        self.assertEqual(retro['roms'][0]['title'], 'Metroid Fusion')
        self.assertEqual(retro['roms'][0]['system'], 'gba')

    def test_multiple_psp_emulators_translate_to_windows_console(self):
        for folder, rom, target in (
            ('gpSP Kai','roms/test.gba','gba'),
            ('Snes9xTYL','roms/test.sfc','snes'),
            ('NesterJ','roms/test.nes','nes'),
            ('PicoDrive','roms/test.md','md'),
            ('DaedalusX64','roms/test.z64','n64'),
            ('MasterBoy','roms/test.gbc','gbc'),
        ):
            self.install(folder, [rom])
        retro = emulator_inventory(self.root)
        self.assertEqual({rom['system'] for rom in retro['roms']},
                         {'gba','snes','nes','md','n64','gbc'})
        self.assertEqual(len(retro['emulators']), 6)
        self.assertEqual(len(game_inventory(self.root)), 0)

    def test_real_world_psp_homebrew_labels_have_specific_windows_cores(self):
        for title,expected in (
            ('UO gpSP kai 3.4 test 4 Build 225','gba'),
            ('Snes9xTYLmecm 0.4.2 Mod Rev.26','snes'),
            ('CPS1PSP 2.3.5 for PSP','cps1'),
            ('CPS2PSP 2.3.5 for PSP slim','cps2'),
            ('MVSPSP 2.3.5 for PSP','neogeo'),
            ('Mame4All PSP v4.9r2','arcade'),
            ('PSPHUGO-v1.3.1-fw5x','pce'),
            ('RACE! PSP 2.16','ngp'),
            ('PicoDrive','md'),
        ):
            with self.subTest(title=title):
                folder=self.install('HOME_'+str(len(list(self.game.iterdir()))))
                with patch('psp_dock.emulation.psp_retro.read_metadata',return_value={'TITLE':title}):
                    profile=emulator_profile(folder,folder/'EBOOT.PBP')
                self.assertIsNotNone(profile)
                self.assertIn(expected,profile['systems'])
                self.assertIn(expected,SYSTEMS)

    def test_arcade_zip_romset_is_detected_but_never_extracted(self):
        folder=self.install('cps1psp')
        with zipfile.ZipFile(folder/'sf2.zip','w') as archive:
            archive.writestr('sf2_23.bin',b'fixture')
            archive.writestr('sf2_22.bin',b'fixture')
        roms=emulator_inventory(self.root)['roms']
        self.assertEqual(len(roms),1)
        self.assertEqual(roms[0]['system'],'cps1')
        self.assertFalse((folder/'sf2_23.bin').exists())

    def test_licensed_alternatives_are_selected_by_default(self):
        self.assertEqual(SYSTEMS['snes'][1],'bsnes_libretro.dll')
        self.assertEqual(SYSTEMS['md'][1],'blastem_libretro.dll')
        self.assertEqual(SYSTEMS['32x'][1],'blastem_libretro.dll')
        self.assertEqual(SYSTEMS['sms'][1],'gearsystem_libretro.dll')
        self.assertEqual(SYSTEMS['gg'][1],'gearsystem_libretro.dll')

    def test_sfo_title_matches_when_folder_name_is_generic(self):
        folder = self.install('GAMES_01')
        with patch('psp_dock.emulation.psp_retro.read_metadata',return_value={'TITLE':'TempGBA4PSP v1.45'}):
            self.assertEqual(emulator_profile(folder,folder/'EBOOT.PBP')['systems'],['gba'])
        self.assertIsNone(emulator_profile(folder,folder/'EBOOT.PBP'))

    def test_never_include_bios_saves_non_rom_or_unrelated_system(self):
        self.install('gpSP', ['ROMS/yes.gba','ROMS/other.snes','BIOS/bios.gba',
                              'saves/other.gba','ROMS/state.sav','ROMS/firmware.bin'])
        roms = emulator_inventory(self.root)['roms']
        self.assertEqual([rom['title'] for rom in roms], ['yes'])

    def test_zip_single_rom_only_and_never_extract(self):
        folder = self.install('NesterJ')
        with zipfile.ZipFile(folder/'single.zip','w') as archive:
            archive.writestr('game.nes',b'a')
            archive.writestr('readme.txt',b'readme')
        with zipfile.ZipFile(folder/'multi.zip','w') as archive:
            archive.writestr('one.nes',b'a')
            archive.writestr('two.nes',b'b')
        with zipfile.ZipFile(folder/'wrong.zip','w') as archive:
            archive.writestr('other.gba',b'a')
        self.assertEqual([r['title'] for r in emulator_inventory(self.root)['roms']],['single'])
        self.assertFalse((folder/'game.nes').exists())

    def test_no_scan_inside_unknown_homebrew(self):
        self.install('Homebrew', ['ROMS/Test.gba'])
        self.assertEqual(emulator_inventory(self.root)['roms'],[])

    def test_outside_symlink_never_ingested(self):
        folder = self.install('gpSP')
        outside = self.root.parent / 'test-pspdock-external.gba'
        try:
            (folder / 'external.gba').symlink_to(outside)
        except (OSError,NotImplementedError):
            self.skipTest('Windows symlink privilege unavailable')
        self.assertRaises(ValueError,emulator_inventory,self.root)

    def test_retroarch_requires_exact_explicit_local_core(self):
        exe = self.root / 'RetroArch' / 'retroarch.exe'
        exe.parent.mkdir()
        exe.write_bytes(b'synthetic executable, never launched')
        self.assertIsNone(retroarch_core(str(exe),'gba'))
        core = exe.parent / 'cores' / SYSTEMS['gba'][1]
        core.parent.mkdir()
        core.write_bytes(b'synthetic core, never loaded')
        self.assertEqual(retroarch_core(str(exe),'gba'),(exe,core))
        self.assertIsNone(retroarch_core(str(exe),'snes'))
        self.assertIsNone(retroarch_core(str(exe.parent/'not-retroarch.exe'),'gba'))
        self.assertIsNone(retroarch_core(str(exe),'bad-system'))


if __name__ == '__main__':
    unittest.main()
