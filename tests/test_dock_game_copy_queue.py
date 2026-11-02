"""A PSP HID menu selection queues only: copies wait for original Sony storage."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dock import Dock, volume_identity


HID_ABSENT = {'supported':True,'errors':[],'devices':[]}
HID_PRESENT = {'supported':True,'errors':[],
               'devices':[{'path':'synthetic-PSP-HID'}]}


class DeferredXmbGameCopyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.home=Path(self.tmp.name)
        self.psp=self.home/'synthetic-psp'
        (self.psp/'PSP/SAVEDATA').mkdir(parents=True)
        (self.psp/'ISO').mkdir(parents=True)
        (self.psp/'ISO/GameMissing.iso').write_bytes(b'PSP_SYNTHETIC_GAME')
        self.dock=Dock(self.home/'data')
        self.dock.settings({'psp_root':str(self.psp)})
        self.games=self.dock.games()
        self.game=self.games[0]
        self.assertEqual(self.game['availability'],'missing')
        self.dock.session['prepared_volume']=volume_identity(self.psp)
        self.dock.snapshot={'controller_status':{'connected':True,'mode':'hid_manual',
                                                 'can_play':True},
                            'selected_device':None}

    def tearDown(self):
        self.tmp.cleanup()

    def queue(self):
        with patch.object(self.dock,'transfer',
                          side_effect=AssertionError('Never copy while in HID')), \
             patch.object(self.dock,'root',return_value=None):
            return self.dock.request_game_copy_from_hid(self.game['id'])

    def test_triangle_game_menu_only_queues_after_confirmation(self):
        queued=self.queue()
        self.assertEqual(queued['status'],'waiting_for_storage')
        self.assertEqual(queued['volume_serial'],volume_identity(self.psp))
        self.assertFalse((self.home/'data/library/ISO/GameMissing.iso').exists())
        again=Dock(self.home/'data')
        self.assertEqual(again.copy_queue['status'],'waiting_for_storage')
        with self.assertRaisesRegex(ValueError,'pendiente'):
            self.queue()

    def test_same_volume_and_no_hid_completes_a_single_copy(self):
        self.queue()
        with patch('bridge_hid.probe',return_value=HID_ABSENT):
            self.assertTrue(self.dock.complete_queued_game_copy())
        copied=Path(self.dock.config['library'])/'ISO/GameMissing.iso'
        self.assertEqual(copied.read_bytes(),b'PSP_SYNTHETIC_GAME')
        self.assertEqual(self.dock.copy_queue['status'],'copied')
        with patch('bridge_hid.probe',side_effect=AssertionError('No duplicate')):
            self.assertFalse(self.dock.complete_queued_game_copy())

    def test_returning_wrong_volume_or_hid_still_connected_does_not_copy(self):
        self.queue()
        with patch('dock.volume_identity',return_value='another-serial'),\
             patch('bridge_hid.probe',side_effect=AssertionError('No USB commands')):
            self.assertFalse(self.dock.complete_queued_game_copy())
        with patch('bridge_hid.probe',return_value=HID_PRESENT):
            self.assertFalse(self.dock.complete_queued_game_copy())
        self.assertEqual(self.dock.copy_queue['status'],'waiting_for_storage')
        self.assertFalse((self.home/'data/library/ISO/GameMissing.iso').exists())

    def test_cancel_does_not_call_storage_or_write_game(self):
        self.queue()
        with patch.object(self.dock,'transfer',side_effect=AssertionError('No copy')):
            self.assertEqual(self.dock.cancel_game_copy_queue()['status'],'cancelled')
            self.assertFalse(self.dock.complete_queued_game_copy())

    def test_interruption_is_recorded_without_implicit_retry(self):
        self.queue()
        with patch('bridge_hid.probe',return_value=HID_ABSENT),\
             patch.object(self.dock,'transfer',
                          side_effect=OSError('Synthetic PC disk full')):
            self.assertFalse(self.dock.complete_queued_game_copy())
        self.assertEqual(self.dock.copy_queue['status'],'interrupted_review')
        again=Dock(self.home/'data')
        self.assertEqual(again.copy_queue['status'],'interrupted_review')
        with patch.object(again,'transfer',side_effect=AssertionError('Blind retry')):
            self.assertFalse(again.complete_queued_game_copy())


if __name__=='__main__':
    unittest.main()
