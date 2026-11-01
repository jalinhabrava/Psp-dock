"""Production launcher preparation: PC-only and verified PSP button layout."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dock import Dock, prepare_psp_controls
from ppsspp_psp_button_map import remap_controls
from tests.test_ppsspp_psp_button_map import DEFAULT_PROFILE


class LauncherControlsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.memstick = self.home / "private-ppsspp"
        self.backups = self.home / "profile-backups"
        self.profile = self.memstick / "PSP/SYSTEM/controls.ini"

    def tearDown(self):
        self.temp.cleanup()

    def test_first_launch_seeds_only_pc_with_physically_verified_controls(self):
        report = prepare_psp_controls(self.memstick, self.backups)
        self.assertEqual(report["status"], "PSP-CORRECT")
        self.assertTrue(self.profile.is_file())
        self.assertFalse(remap_controls(self.profile.read_bytes())[1])
        with patch("ppsspp_psp_button_map.ppsspp_open", return_value=False):
            again = prepare_psp_controls(self.memstick, self.backups)
        self.assertFalse(again["changed"])
        self.assertFalse(self.backups.exists())

    def test_known_stock_profile_corrects_only_with_pc_backup(self):
        self.profile.parent.mkdir(parents=True)
        self.profile.write_bytes(DEFAULT_PROFILE)
        with patch("ppsspp_psp_button_map.ppsspp_open", return_value=False):
            result = prepare_psp_controls(self.memstick, self.backups)
        self.assertTrue(result["changed"])
        self.assertEqual(Path(result["backup"]).read_bytes(), DEFAULT_PROFILE)
        self.assertFalse(remap_controls(self.profile.read_bytes())[1])

    def test_custom_profile_cannot_be_overwritten(self):
        self.profile.parent.mkdir(parents=True)
        custom = DEFAULT_PROFILE.replace(b"10-189", b"10-207")
        self.profile.write_bytes(custom)
        with patch("ppsspp_psp_button_map.ppsspp_open", return_value=False):
            with self.assertRaisesRegex(ValueError, "No se modificó"):
                prepare_psp_controls(self.memstick, self.backups)
        self.assertEqual(self.profile.read_bytes(), custom)
        self.assertFalse(self.backups.exists())

    def test_controller_status_is_observational_and_manual(self):
        dock = Dock(self.home / "dock-data")
        self.assertEqual(dock.controller_status(True)["mode"], "usb_storage")
        if __import__("os").name == "nt":
            with patch("ppsspp_hid_session.validate_hid_only",
                       return_value={"session": "ab" * 16, "path": "synthetic"}):
                report = dock.controller_status(False)
                self.assertEqual(report["mode"], "hid_manual")
                self.assertTrue(report["connected"])
                self.assertIn("no es prueba", report["reason"])
            with patch("ppsspp_hid_session.validate_hid_only",
                       side_effect=ValueError("HID not present")):
                self.assertEqual(dock.controller_status(False)["mode"], "await_hid")

    def test_future_host_lock_pause_blocks_windows_remount_probe(self):
        dock = Dock(self.home / "dock-data")
        with self.assertRaises(ValueError):
            dock.pause_storage_polling("yes")
        dock.pause_storage_polling(True)
        with patch("dock.windows_devices",
                   side_effect=AssertionError("GetLogicalDrives while paused")):
            self.assertEqual(dock.devices(), [])
            self.assertIsNone(dock.root())
            state = dock.state()
            self.assertEqual(state["controller_status"]["mode"], "host_lock_preparation")
            self.assertFalse(state["can_launch_game"])
            with self.assertRaisesRegex(ValueError, "volumen en curso"):
                dock.launch("unused")
        dock.pause_storage_polling(False)


if __name__ == "__main__":
    unittest.main()
