"""Manual Windows launch must not recreate the retired startup registrations."""
import unittest
from unittest.mock import patch

import psp_dock.core.setup_startup as setup_startup
import psp_dock.core.setup_usb_event as setup_usb_event


class ManualStartupPolicyTests(unittest.TestCase):
    def test_old_startup_command_cannot_install_a_login_watcher(self):
        with patch('sys.argv', ['psp_dock.core.setup_startup.py', '--install']), \
             patch.object(setup_startup, 'install_entry') as install:
            self.assertEqual(setup_startup.main(), 1)
        install.assert_not_called()

    def test_old_pnp_command_cannot_restore_the_unreliable_event_task(self):
        with patch('sys.argv', ['psp_dock.core.setup_usb_event.py', '--install']), \
             patch.object(setup_usb_event, 'install_task') as install:
            self.assertEqual(setup_usb_event.main(), 1)
        install.assert_not_called()


if __name__ == '__main__':
    unittest.main()
