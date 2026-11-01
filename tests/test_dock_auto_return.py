"""Safe save return only after a user explicitly exits the HID XMB."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dock import Dock, volume_identity


HID_ABSENT = {'supported': True, 'errors': [], 'devices': []}
HID_PRESENT = {'supported': True, 'errors': [],
               'devices': [{'path': 'SYNTHETIC_PSP_HID'}]}


class ExplicitDockReturnTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.psp = self.home / 'synthetic-psp'
        self.save = self.psp / 'PSP/SAVEDATA/TEST0001/DATA.BIN'
        self.save.parent.mkdir(parents=True)
        self.save.write_bytes(b'INITIAL')
        self.dock = Dock(self.home / 'data')
        self.dock.settings({'psp_root': str(self.psp)})
        self.dock.sync()  # Explicit initial consent, synthetic fixture only.
        self.pc = Path(self.dock.config['memstick'])/'PSP/SAVEDATA/TEST0001/DATA.BIN'
        self.assertEqual(self.pc.read_bytes(), b'INITIAL')

    def tearDown(self):
        self.temp.cleanup()

    def change_pc_after_game(self):
        self.dock.session['pending_return'] = True
        self.pc.write_bytes(b'GAME_CHANGED_ON_PC')

    def authorize_exit_from_hid(self):
        with patch.object(self.dock, 'root', return_value=None):
            self.dock.request_exit()
            self.assertIsNone(self.dock.complete_authorized_return())
        self.assertTrue(self.dock.session['return_sync_authorized'])

    def test_exit_explicitly_authorizes_verified_one_shot_return(self):
        self.change_pc_after_game()
        self.authorize_exit_from_hid()
        self.assertEqual(self.save.read_bytes(), b'INITIAL')
        with patch('bridge_hid.probe', return_value=HID_ABSENT) as hid:
            final = self.dock.complete_authorized_return()
        hid.assert_called_once()
        self.assertFalse(final['actions'])
        self.assertFalse(final['conflicts'])
        self.assertEqual(self.save.read_bytes(),b'GAME_CHANGED_ON_PC')
        self.assertEqual(self.pc.read_bytes(),self.save.read_bytes())
        self.assertEqual(self.dock.session['return_result'],'verified')
        self.assertFalse(self.dock.session['pending_return'])
        self.assertFalse(self.dock.session['exit_requested'])
        self.assertIsNone(self.dock.complete_authorized_return())

    def test_user_can_exit_xmb_without_launching_any_game(self):
        # The product remains in HID as long as the operator wants,
        # but pressing Exit is valid with zero game launches/saves.
        with patch.object(self.dock,'root',return_value=None):
            self.assertFalse(self.dock.session['pending_return'])
            self.dock.request_exit()
            self.assertTrue(self.dock.session['exit_requested'])
            self.assertTrue(self.dock.session['return_sync_authorized'])
        with patch('bridge_hid.probe',return_value=HID_ABSENT):
            final=self.dock.complete_authorized_return()
        self.assertFalse(final['actions'])
        self.assertFalse(self.dock.session['pending_return'])
        self.assertFalse(self.dock.session['exit_requested'])
        self.assertEqual(self.dock.session['return_result'],'verified')

    def test_never_synchronize_just_because_storage_reappears(self):
        self.change_pc_after_game()
        with patch('bridge_hid.probe', side_effect=AssertionError('Not authorized')):
            self.assertIsNone(self.dock.complete_authorized_return())
        self.assertEqual(self.save.read_bytes(),b'INITIAL')

    def test_hid_still_present_blocks_return(self):
        self.change_pc_after_game()
        self.authorize_exit_from_hid()
        with patch('bridge_hid.probe', return_value=HID_PRESENT):
            with self.assertRaisesRegex(ValueError,'HID'):
                self.dock.complete_authorized_return()
        self.assertEqual(self.save.read_bytes(),b'INITIAL')
        self.assertFalse(self.dock.session['return_sync_attempted'])

    def test_wrong_original_memory_stick_blocks_return(self):
        self.change_pc_after_game()
        self.authorize_exit_from_hid()
        with patch('dock.volume_identity',return_value='111111'):
            with self.assertRaisesRegex(ValueError,'Otra Memory Stick'):
                self.dock.complete_authorized_return()
        self.assertEqual(self.save.read_bytes(),b'INITIAL')

    def test_conflicted_save_requires_explicit_resolution_without_any_write(self):
        self.change_pc_after_game()
        self.authorize_exit_from_hid()
        self.save.write_bytes(b'CHANGED_IN_PSP_TOO')
        with patch('bridge_hid.probe', return_value=HID_ABSENT):
            final = self.dock.complete_authorized_return()
        self.assertTrue(final['conflicts'])
        self.assertEqual(self.save.read_bytes(),b'CHANGED_IN_PSP_TOO')
        self.assertEqual(self.pc.read_bytes(),b'GAME_CHANGED_ON_PC')
        self.assertEqual(self.dock.session['return_result'],'conflicts')
        self.assertFalse(self.dock.session['return_sync_authorized'])

    def test_interrupted_return_records_attempt_before_mutation_and_does_not_retry(self):
        self.change_pc_after_game()
        self.authorize_exit_from_hid()
        def fails(*args, **kwargs):
            assert self.dock.session['return_sync_attempted']
            raise OSError('Simulated PC copy interruption')
        with patch('bridge_hid.probe',return_value=HID_ABSENT),\
             patch('dock.copy_slot',side_effect=fails):
            with self.assertRaisesRegex(OSError,'interruption'):
                self.dock.complete_authorized_return()
        self.assertEqual(self.save.read_bytes(),b'INITIAL')
        from dock import read_json
        durable=read_json(self.dock.data/'session.json',{})
        self.assertTrue(durable['return_sync_attempted'])
        self.assertFalse(durable['return_sync_authorized'])
        with patch('bridge_hid.probe',side_effect=AssertionError('No blind retry')):
            self.assertIsNone(self.dock.complete_authorized_return())

    def test_watch_restores_saves_only_after_explicit_exit(self):
        self.change_pc_after_game()
        self.authorize_exit_from_hid()
        def finish(_delay):
            self.dock.stop.set()
        with patch.object(self.dock.stop,'wait',side_effect=finish),\
             patch('bridge_hid.probe',return_value=HID_ABSENT):
            self.dock.watch()
        self.assertEqual(self.save.read_bytes(),b'GAME_CHANGED_ON_PC')
        self.assertEqual(self.dock.snapshot['session']['return_result'],'verified')


if __name__ == '__main__':
    unittest.main()
