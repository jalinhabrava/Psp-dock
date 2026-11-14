"""Web-only guest sessions pause PSP polling without altering saves or USB."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dock import Dock


class GuestWebModeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dock = Dock(Path(self.temp.name) / 'live')

    def test_choosing_web_only_never_polls_hardware(self):
        self.assertFalse(self.dock.guest_mode)
        self.assertEqual(self.dock.set_guest_mode(True),
                         {'ok': True, 'guest_mode': True})
        self.assertTrue(self.dock.snapshot['guest_mode'])
        self.assertFalse(self.dock.snapshot['scanning'])

        def finish(_):
            self.dock.stop.set()
            return True

        with patch.object(self.dock, 'devices',
                          side_effect=AssertionError('No USB enumeration while guest')), \
             patch.object(self.dock.product_session, 'tick',
                          side_effect=AssertionError('No PSP handshake while guest')), \
             patch.object(self.dock.stop, 'wait', side_effect=finish):
            self.dock.watch()

        self.assertEqual(self.dock.snapshot['connection_state'], 'guest_web')
        self.assertEqual(self.dock.snapshot['controller_status']['mode'], 'guest_web')
        self.assertFalse(self.dock.snapshot['controller_status']['connected'])
        self.assertFalse(self.dock.snapshot['can_launch_game'])
        self.assertFalse(self.dock.snapshot['can_launch_retro'])
        self.dock.stop.clear()
        self.assertEqual(self.dock.set_guest_mode(False),
                         {'ok': True, 'guest_mode': False})
        self.assertFalse(self.dock.guest_mode)

    def test_guest_never_hides_an_attached_psp_or_pending_return(self):
        self.dock.snapshot['selected_device'] = 'E:\\'
        with self.assertRaisesRegex(ValueError, 'sesión PSP'):
            self.dock.set_guest_mode(True)
        self.assertFalse(self.dock.guest_mode)
        self.dock.snapshot['selected_device'] = None

        self.dock.session['pending_return'] = True
        with self.assertRaisesRegex(ValueError, 'sesión PSP'):
            self.dock.set_guest_mode(True)
        self.assertFalse(self.dock.guest_mode)
        self.dock.session['pending_return'] = False

        self.dock.snapshot['controller_status'] = {'connected':True}
        with self.assertRaisesRegex(ValueError, 'sesión PSP'):
            self.dock.set_guest_mode(True)
        self.assertFalse(self.dock.guest_mode)

        with self.assertRaises(ValueError):
            self.dock.set_guest_mode('true')

    def test_guest_is_volatile_and_does_not_write_psp_settings(self):
        self.dock.snapshot['scanning'] = False
        settings_before = self.dock.config_path.read_bytes() if self.dock.config_path.exists() else None
        session_before = self.dock.data/'session.json'
        saved_before = session_before.read_bytes() if session_before.exists() else None
        self.dock.set_guest_mode(True)
        self.dock.set_guest_mode(False)
        self.assertEqual(settings_before,
                         self.dock.config_path.read_bytes() if self.dock.config_path.exists() else None)
        self.assertEqual(saved_before,
                         session_before.read_bytes() if session_before.exists() else None)
        fresh = Dock(self.dock.data)
        self.assertFalse(fresh.guest_mode)


if __name__ == '__main__':
    unittest.main()
