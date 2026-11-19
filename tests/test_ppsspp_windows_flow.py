from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,MagicMock

import psp_dock.emulation.ppsspp_windows_flow as flow


BASE = """[ControlMapping]
Cross = 1-54,20-96,10-188
Circle = 1-52,20-97,10-189
Square = 10-190
Triangle = 10-191
Pause = 1-111,20-4034,20-3,10-109
Start = 10-195

[Other]
Keep = YES
"""


class HomePauseAndFocusTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.home=Path(self.temp.name)
        self.profile=self.home/"PC/PSP/SYSTEM/controls.ini"
        self.profile.parent.mkdir(parents=True)
        self.profile.write_text(BASE)
        self.backup=self.home/"PC/backups"

    def tearDown(self):
        self.temp.cleanup()

    def test_psp_home_directinput_button_maps_to_ppsspp_native_pause(self):
        report=flow.add_home_pause_mapping(self.profile,self.backup)
        self.assertTrue(report["changed"])
        after=self.profile.read_text()
        self.assertIn("Pause =1-111,20-4034,20-3,10-109,10-196",after)
        self.assertIn("Cross = 1-54,20-96,10-188",after)
        self.assertIn("[Other]\nKeep = YES",after)
        self.assertEqual(Path(report["backup"]).read_text(),BASE)
        second=flow.add_home_pause_mapping(self.profile,self.backup)
        self.assertFalse(second["changed"])
        self.assertEqual(len(list(self.backup.iterdir())),1)

    def test_custom_or_corrupt_profile_blocks_unknown_mappings(self):
        for changed in (BASE.replace("Pause = ","PAUSE_UNKNOWN = "),
                        BASE.replace("10-191","UNKNOWN"),
                        BASE.replace("Pause = 1-111","Pause = bad")):
            self.profile.write_text(changed)
            with self.assertRaises(flow.WindowFlowError):
                flow.add_home_pause_mapping(self.profile,self.backup)

    def test_exact_single_window_owned_by_ppsspp_only(self):
        def entry(pid,handle,title="PPSSPP v1.20.4"):
            return {"pid":pid,"handle":handle,"visible":True,"title":title}
        self.assertEqual(flow.find_window_for_pid(400,enumerate_rows=lambda:[
            entry(400,4),entry(900,9,"PSP Dock")]),4)
        self.assertIsNone(flow.find_window_for_pid(400,enumerate_rows=lambda:[
            entry(400,4),entry(400,5)]))
        self.assertIsNone(flow.find_window_for_pid(400,enumerate_rows=lambda:[
            entry(800,5)]))
        self.assertIsNone(flow.find_window_for_pid(0,enumerate_rows=lambda:[]))

    def test_nonwindows_focus_safely_noop(self):
        with patch.object(flow.os,"name","posix"):
            self.assertFalse(flow.focus_when_ready(MagicMock(pid=400)))
            self.assertFalse(flow.focus_process_window(400))

    def test_after_ppsspp_closes_we_restore_only_our_xmb(self):
        with patch("psp_dock.core.xmb_window.maximize_existing_xmb",return_value=True) as target:
            self.assertTrue(flow.return_to_xmb())
            target.assert_called_once()


if __name__=="__main__":
    unittest.main()
