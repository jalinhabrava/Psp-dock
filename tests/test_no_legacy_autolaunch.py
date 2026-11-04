from pathlib import Path
import unittest
from unittest.mock import patch

import psp_dock.emulation.ppsspp_hid_session as legacy


class LegacyAutolaunchTests(unittest.TestCase):
    def test_old_one_click_launcher_does_not_wait_and_play(self):
        cmd = (Path(__file__).resolve().parents[1] /
               "Jugar-PPSSPP-con-PSP-HID.cmd").read_text(encoding='utf-8')
        self.assertNotIn("python ppsspp_hid_session.py --wait-and-play",cmd)
        self.assertIn("DESACTIVADO",cmd)

    def test_old_cli_requires_explicit_autoplay_opt_in(self):
        with patch("sys.argv", ["psp_dock.emulation.ppsspp_hid_session.py","--wait-and-play"]), \
             patch.object(legacy,"plan_game") as plan, \
             patch.object(legacy,"wait_for_hid_and_play") as play:
            self.assertEqual(legacy.main(),1)
        plan.assert_called_once()
        play.assert_not_called()

    def test_regular_preview_never_autoplays(self):
        with patch("sys.argv", ["psp_dock.emulation.ppsspp_hid_session.py"]), \
             patch.object(legacy,"plan_game"), \
             patch.object(legacy,"start_ppsspp") as play:
            self.assertEqual(legacy.main(),0)
        play.assert_not_called()

if __name__ == "__main__":
    unittest.main()
