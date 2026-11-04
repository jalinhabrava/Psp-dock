"""Regression tests for PSP Dock's opt-in Windows retro bridge.

No actual executable, game, removable storage or USB device is touched.
"""
from pathlib import Path
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from dock import Dock
from psp_retro import SYSTEMS


class RetroBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.psp = self.root / 'synthetic-psp'
        homebrew = self.psp / 'PSP' / 'GAME' / 'gpSP'
        homebrew.mkdir(parents=True)
        (homebrew / 'EBOOT.PBP').write_bytes(b'synthetic homebrew')
        (homebrew / 'ROMS').mkdir()
        self.rom_path = homebrew / 'ROMS' / 'Example.gba'
        self.rom_path.write_bytes(b'FAKE-GBA-DATA-NEVER-EXECUTE')
        (self.psp / 'PSP' / 'SAVEDATA').mkdir()
        # Never enumerate a real USB mass-storage drive while running tests.
        empty_devices = patch('dock.windows_devices',return_value=[])
        empty_devices.start()
        self.addCleanup(empty_devices.stop)
        self.dock = Dock(self.root / 'data')
        # These are synthetic ROM fixtures; the production default now
        # provisions RetroArch automatically. Never allow tests to download.
        self.dock.settings({'psp_root':str(self.psp),'retro_install_policy':'off'})

    def item(self):
        roms = self.dock.retro_catalog()['roms']
        self.assertEqual(len(roms),1)
        return roms[0]

    def copy(self):
        missing = self.item()
        self.assertEqual(missing['availability'],'missing')
        self.dock.transfer_retro([missing['id']])
        return self.item()

    def install_pc_emulator(self):
        exe = self.root / 'RetroArch' / 'retroarch.exe'
        exe.parent.mkdir(exist_ok=True)
        exe.write_bytes(b'NOT-A-REAL-EXECUTABLE')
        core = exe.parent / 'cores' / SYSTEMS['gba'][1]
        core.parent.mkdir()
        core.write_bytes(b'NOT-A-REAL-CORE')
        self.dock.settings({'retroarch':str(exe)})
        return exe,core

    def test_separates_emulators_from_games_in_state(self):
        self.assertFalse(self.dock.games())
        retro = self.dock.retro_catalog()
        self.assertEqual(retro['emulators'][0]['name'],'gpSP Kai')
        self.assertEqual(retro['roms'][0]['system_name'],'Game Boy Advance')
        self.assertFalse(retro['roms'][0]['core_available'])

    def test_previous_game_cache_does_not_keep_emulator_as_psp_game(self):
        data = self.root / 'old-game-cache'
        data.mkdir()
        (data / 'inventory-cache.json').write_text(json.dumps({
            'volume_serial':'previous-volume', 'games':[
                {'id':'a'*24,'relative':'PSP/GAME/gpSP/EBOOT.PBP',
                 'title':'gpSP Kai','format':'PBP','size':10},
                {'id':'b'*24,'relative':'PSP/GAME/Homebrew/EBOOT.PBP',
                 'title':'Homebrew','format':'PBP','size':10},
            ]}),encoding='utf-8')
        other = Dock(data)
        self.assertEqual([g['title'] for g in other.snapshot['games']], ['Homebrew'])

    def test_explicit_copy_is_sha_verified_without_psp_mutations(self):
        before = self.rom_path.read_bytes()
        ready = self.copy()
        self.assertEqual(ready['availability'],'ready')
        self.assertEqual(self.rom_path.read_bytes(),before)
        local = Path(self.dock.config['library']) / ready['relative']
        self.assertEqual(local.read_bytes(),before)
        self.assertEqual(self.dock.transfer_progress()['phase'],'done')
        self.assertFalse(list(local.parent.glob('.dock-transfer-*')))

    def test_cached_rom_remains_read_only_in_hid_without_new_source_reads(self):
        ready = self.copy()
        with patch.object(self.dock,'root',return_value=None), patch('dock.emulator_inventory',
                side_effect=AssertionError('No PSP reads after USB disappears')):
            self.assertEqual(self.dock.retro_catalog()['roms'][0]['availability'],'ready')
            self.assertEqual(self.dock.retro_catalog()['roms'][0]['id'],ready['id'])

    def test_no_implicit_copy_and_refuse_existing_different_content(self):
        item = self.item()
        destination = Path(self.dock.config['library']) / item['relative']
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b'LOCAL-OTHER-DATA')
        self.assertEqual(self.item()['availability'],'different')
        with self.assertRaises(ValueError):
            self.dock.transfer_retro([item['id']])
        self.assertEqual(destination.read_bytes(),b'LOCAL-OTHER-DATA')

    def test_no_usb_mode_no_transfer(self):
        item = self.item()
        with patch.object(self.dock,'root',return_value=None):
            with self.assertRaises(ValueError):
                self.dock.transfer_retro([item['id']])

    def test_block_unknown_or_duplicate_ids(self):
        item = self.item()
        for ids in ([item['id'],item['id']],['not-in-catalog'],[]):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                self.dock.transfer_retro(ids)

    def test_pc_rom_mutation_revokes_ready(self):
        ready = self.copy()
        destination = Path(self.dock.config['library']) / ready['relative']
        destination.write_bytes(b'DIFFERENT-FAKE-GBA-DATA')
        os.utime(destination,None)
        self.assertEqual(self.item()['availability'],'different')

    def test_retro_only_session_can_prepare_saves_without_ppsspp(self):
        self.copy()
        self.assertTrue(self.dock.prepare_identical_saves_without_copy())
        self.assertEqual(self.dock.session['prepared_volume'],self.dock.retro_volume)
        self.assertFalse(self.dock.prepared_runtime_path())

    def test_live_emulator_launch_uses_core_and_same_verified_hid_twice(self):
        ready = self.copy()
        exe,core = self.install_pc_emulator()
        self.dock.session['prepared_volume'] = self.dock.retro_volume
        self.dock.retro_save_plan()
        with patch.object(self.dock,'root',return_value=None), \
             patch('ppsspp_hid_session.validate_hid_only',side_effect=['stable','stable']) as hid, \
             patch('dock.subprocess.Popen') as launch, \
             patch('ppsspp_windows_flow.focus_async'):
            self.dock.launch_retro(ready['id'])
        self.assertEqual(hid.call_count,2)
        cmd = launch.call_args.args[0]
        self.assertEqual(cmd[0],str(exe))
        self.assertIn('--appendconfig',cmd)
        self.assertIn('--save',cmd)
        self.assertIn('--savestate',cmd)
        self.assertEqual(cmd[-4:-2],['-L',str(core)])
        self.assertEqual(cmd[-2],'-f')
        self.assertTrue(cmd[-1].endswith('Example.gba'))
        self.assertTrue(self.dock.session['pending_return'])
        self.assertEqual(self.dock.session['launcher'],'retroarch')

    def test_changing_hid_blocks_spawn_and_does_not_set_pending(self):
        ready = self.copy()
        self.install_pc_emulator()
        self.dock.session['prepared_volume'] = self.dock.retro_volume
        with patch.object(self.dock,'root',return_value=None), \
             patch('ppsspp_hid_session.validate_hid_only',side_effect=['A','B']), \
             patch('dock.subprocess.Popen') as launch:
            with self.assertRaises(ValueError):
                self.dock.launch_retro(ready['id'])
        launch.assert_not_called()
        self.assertFalse(self.dock.session.get('pending_return'))

    def test_wrong_psp_save_volume_cannot_launch(self):
        ready = self.copy()
        self.install_pc_emulator()
        self.dock.session['prepared_volume']='wrong-volume'
        with patch.object(self.dock,'root',return_value=None), \
             patch('dock.subprocess.Popen') as launch:
            with self.assertRaises(ValueError):
                self.dock.launch_retro(ready['id'])
        launch.assert_not_called()

    def test_missing_windows_core_is_not_an_executable_fallback(self):
        ready = self.copy()
        self.dock.session['prepared_volume']=self.dock.retro_volume
        with patch.object(self.dock,'root',return_value=None), \
             patch('dock.subprocess.Popen') as launch:
            with self.assertRaises(ValueError):
                self.dock.launch_retro(ready['id'])
        launch.assert_not_called()

    def test_untrusted_windows_executable_is_not_accepted(self):
        with self.assertRaises(ValueError):
            self.dock.settings({'retroarch':str(self.root/'calc.exe')})


if __name__ == '__main__':
    unittest.main()
