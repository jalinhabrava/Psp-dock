"""Pure and filesystem tests, never load a real RetroArch executable."""
from pathlib import Path
import tempfile
import unittest

from bridge_hid import VID, PID
from retro_runtime import (joypad_autoconfig, prepare_joypad_profile,
                           private_save_paths, retroarch_arguments)


class RetroRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def test_pdk1_dinput_profile_uses_exact_descriptor_order(self):
        text=joypad_autoconfig().decode('utf-8')
        for key,value in {
            'input_vendor_id':VID, 'input_product_id':PID,
            'input_b_btn':0, 'input_a_btn':1, 'input_y_btn':2,
            'input_x_btn':3, 'input_l_btn':4, 'input_r_btn':5,
            'input_select_btn':6, 'input_start_btn':7,
            'input_enable_hotkey_btn':6, 'input_menu_toggle_btn':3,
        }.items():
            self.assertIn(f'{key} = "{value}"',text)
        self.assertIn('input_up_btn = "h0up"',text)
        self.assertIn('input_l_y_minus_axis = "-1"',text)
        self.assertNotIn('input_exit_emulator_btn',text)
        self.assertNotIn('input_menu_toggle_btn = "7"',text)

    def test_psp_layout_reverses_only_cross_and_circle(self):
        normal=joypad_autoconfig().decode()
        alt=joypad_autoconfig('psp').decode()
        self.assertIn('input_b_btn = "0"',normal)
        self.assertIn('input_b_btn = "1"',alt)
        self.assertIn('input_a_btn = "0"',alt)
        self.assertIn('input_b_btn_label = "Circle"',alt)
        self.assertIn('input_a_btn_label = "Cross"',alt)
        self.assertIn('input_start_btn = "7"',alt)
        with self.assertRaises(ValueError):
            joypad_autoconfig('malicious')

    def test_generates_session_only_files_and_idempotent(self):
        overlay=prepare_joypad_profile(self.root)
        profile=overlay.parent/'autoconfig/dinput/PSP Dock HID.cfg'
        self.assertTrue(profile.is_file())
        self.assertEqual(overlay,prepare_joypad_profile(self.root))
        cfg=overlay.read_text()
        self.assertIn('config_save_on_exit = "false"',cfg)
        self.assertIn('joypad_autoconfig_dir = "',cfg)
        self.assertIn('input_joypad_driver = "dinput"',cfg)
        self.assertFalse((self.root/'retroarch.cfg').exists())

    def test_rejects_symlinked_profile_folder(self):
        outside=self.root/'outside'
        outside.mkdir()
        try:
            (self.root/'retro-profiles').symlink_to(outside,target_is_directory=True)
        except (OSError,NotImplementedError):
            self.skipTest('Symlinks unavailable')
        with self.assertRaises(ValueError):
            prepare_joypad_profile(self.root)

    def test_per_psp_and_per_rom_saves_do_not_overlap(self):
        id1='a'*24
        id2='b'*24
        a,astate=private_save_paths(self.root,'memory-stick-a',id1)
        b,_=private_save_paths(self.root,'memory-stick-b',id1)
        c,_=private_save_paths(self.root,'memory-stick-a',id2)
        self.assertEqual(len({a,b,c}),3)
        self.assertTrue(a.is_relative_to(self.root))
        self.assertNotEqual(a.parent,astate.parent)
        with self.assertRaises(ValueError):
            private_save_paths(self.root,'','a'*24)
        with self.assertRaises(ValueError):
            private_save_paths(self.root,'memory-stick-a','../wrong')

    def test_cli_is_exclusively_per_game_and_not_global(self):
        args=retroarch_arguments(Path('retroarch.exe'),Path('mgba_libretro.dll'),
            Path('example.gba'),Path('private.cfg'),Path('example.srm'),Path('example.state'))
        self.assertEqual(args[1],'--appendconfig')
        self.assertEqual(args[3:7],['--save','example.srm','--savestate','example.state'])
        self.assertEqual(args[-4:],['-L','mgba_libretro.dll','-f','example.gba'])

if __name__=='__main__':
    unittest.main()
