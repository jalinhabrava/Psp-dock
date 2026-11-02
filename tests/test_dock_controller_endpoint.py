from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dock import Dock
from psp_launcher_input import normalize_psp_joystick


class PSPControllerEndpointTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.dock=Dock(Path(self.temp.name)/'data')

    def tearDown(self):
        self.temp.cleanup()

    def test_fails_closed_when_hid_not_verified(self):
        frame = self.dock.psp_launcher_input()
        self.assertFalse(frame['connected'])
        self.assertFalse(frame['accept'])
        with patch('psp_launcher_input.PSPWinMMReader.read',
                   side_effect=AssertionError('must not touch joystick')):
            self.assertFalse(self.dock.psp_launcher_input()['connected'])
        self.dock.pause_storage_polling(True)
        self.assertFalse(self.dock.psp_launcher_input()['connected'])

    def test_verified_hid_snapshot_allows_read_only_native_navigation(self):
        self.dock.snapshot['controller_status']={'connected':True,'mode':'hid_manual',
                                                 'product_id':0x01CB,'revision':0x0104}
        expected=normalize_psp_joystick(buttons=1,pov=9000,x=32767,y=32767)
        with patch('psp_launcher_input.PSPWinMMReader.read',return_value=expected) as getter:
            self.assertEqual(self.dock.psp_launcher_input(),expected)
            getter.assert_called_once()

    def test_hid_disappearance_does_not_infer_active_buttons(self):
        self.dock.snapshot['controller_status']={'connected':True,'mode':'hid_manual',
                                                 'product_id':0x01CB,'revision':0x0104}
        with patch('psp_launcher_input.PSPWinMMReader.read',
                   side_effect=ValueError('PSP HID disconnected')):
            frame=self.dock.psp_launcher_input()
        self.assertFalse(frame['connected'])
        self.assertFalse(frame['accept'])

if __name__=='__main__':
    unittest.main()
