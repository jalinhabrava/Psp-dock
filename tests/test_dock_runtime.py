"""Prewarm official PPSSPP entirely on PC before short-lived PSP HID LAB."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from dock import Dock


@unittest.skipUnless(os.name == "nt", "Windows-only official PPSSPP runtime")
class RuntimePrewarmingTests(unittest.TestCase):
    def setUp(self):
        # The launcher now classifies the Dock before its second PDK1 check.
        # This fixture is deliberately independent of any connected PSP.
        identity = {'session': 'a' * 32, 'path': 'FAKE_ONLY',
                    'mode': 'hid_manual', 'can_play': True,
                    'product_id': 0x01CB, 'revision': 0x0104,
                    'synthetic_storage': False}
        self.identity_patch = patch('psp_dock.devices.psp_dock_identity.probe_dock', return_value=identity)
        self.identity_patch.start()
        self.addCleanup(self.identity_patch.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.psp = self.home / "psp-fixture"
        (self.psp / "ISO").mkdir(parents=True)
        (self.psp / "PSP/SAVEDATA").mkdir(parents=True)
        (self.psp / "ISO/Example.iso").write_bytes(b"GAME FIXTURE")
        self.exe = self.home / "official-ppsspp/PPSSPPWindows64.exe"
        self.exe.parent.mkdir(parents=True)
        (self.exe.parent / "assets").mkdir()
        self.exe.write_bytes(b"FAKE executable never launched outside a mock")
        self.dock = Dock(self.home / "dock-data")
        self.dock.settings({"psp_root": str(self.psp), "emulator": str(self.exe)})
        self.id = self.dock.games()[0]["id"]
        self.dock.transfer([self.id])
        self.dock.sync()  # Explicit consent in synthetic fixture.
        self.runtime = self.dock.data / "runtime/test/PPSSPPWindows64.exe"
        self.runtime.parent.mkdir(parents=True, exist_ok=True)
        self.runtime.write_bytes(b"FAKE sandbox PPSSPP")
        (self.runtime.parent / "installed.txt").write_text(
            str(Path(self.dock.config["memstick"]).resolve()) + "\n", encoding="utf-8"
        )
        ini = Path(self.dock.config["memstick"]) / "PSP/SYSTEM/ppsspp.ini"
        ini.parent.mkdir(parents=True, exist_ok=True)
        ini.write_text("[Graphics]\nInternalResolution = 3\n")

    def tearDown(self):
        self.temp.cleanup()

    def prepare(self):
        import types
        with patch("dock.subprocess.run", return_value=types.SimpleNamespace(stdout="1.20.4")), \
             patch("dock.prepare_emulator", return_value=self.runtime) as staged:
            prepared = self.dock.prepare_runtime()
        staged.assert_called_once()
        return prepared

    def test_prepares_pc_runtime_and_controls_only_once(self):
        self.assertIsNone(self.dock.prepared_runtime_path())
        prepared = self.prepare()
        self.assertTrue(prepared["ready"])
        self.assertFalse(prepared["cached"])
        self.assertEqual(self.dock.prepared_runtime_path(verify_digest=True), self.runtime)
        with patch("dock.prepare_emulator", side_effect=AssertionError("Unneeded PC recopy")):
            again = self.dock.prepare_runtime()
        self.assertTrue(again["cached"])

    def test_new_graphics_settings_invalidate_preparation_without_losing_psp_catalog(self):
        self.prepare()
        self.dock.settings({"scale": 4})
        self.assertIsNone(self.dock.prepared_runtime_path())
        self.assertEqual(len(self.dock.games()), 1)
        self.assertTrue(self.dock.session.get("prepared_volume"))

    def test_changed_or_corrupt_runtime_requires_new_preparation(self):
        self.prepare()
        self.runtime.write_bytes(b"MODIFIED after preparation")
        self.assertIsNone(self.dock.prepared_runtime_path(verify_digest=True))
        self.assertEqual(self.dock.prepared_runtime_path(), self.runtime)
        (self.runtime.parent/"installed.txt").write_text("G:\\\n")
        self.assertIsNone(self.dock.prepared_runtime_path())

    def test_same_live_hid_session_checked_twice_just_before_ppsspp_spawn(self):
        self.prepare()
        fake = {"session": "a" * 32, "path": "FAKE_ONLY"}
        with patch.object(self.dock, "root", return_value=None), \
             patch("psp_dock.emulation.ppsspp_hid_session.validate_hid_only", return_value=fake) as check, \
             patch("dock.subprocess.Popen") as popen, \
             patch("dock.prepare_emulator", side_effect=AssertionError("No hash during HID")):
            popen.return_value.poll.return_value = None
            self.dock.launch(self.id)
        self.assertEqual(check.call_count, 2)
        popen.assert_called_once()
        self.assertEqual(popen.call_args.args[0][0], str(self.runtime))
        self.assertTrue(self.dock.session["pending_return"])
        self.assertTrue(self.dock.session["running"])
        self.assertEqual(popen.call_args.args[0][-1],
                         str(Path(self.dock.config["library"])/"ISO/Example.iso"))

    def test_hid_watchdog_or_change_cancels_game_without_setting_pending(self):
        self.prepare()
        fake1 = {"session": "a"*32, "path": "FAKE_ONLY"}
        fake2 = {"session": "b"*32, "path": "FAKE_ONLY"}
        with patch.object(self.dock, "root", return_value=None), \
             patch("psp_dock.emulation.ppsspp_hid_session.validate_hid_only",
                   side_effect=[fake1, fake2]), \
             patch("dock.subprocess.Popen") as popen:
            with self.assertRaisesRegex(ValueError, "HID cambió"):
                self.dock.launch(self.id)
        popen.assert_not_called()
        self.assertFalse(self.dock.session["pending_return"])

    def test_uncached_runtime_blocks_late_heavy_work_during_hid(self):
        with patch.object(self.dock, "root", return_value=None), \
             patch("psp_dock.emulation.ppsspp_hid_session.validate_hid_only",
                   return_value={"session": "a"*32, "path": "FAKE_ONLY"}), \
             patch("dock.prepare_emulator", side_effect=AssertionError("No prep in HID")):
            with self.assertRaisesRegex(ValueError, "MIENTRAS USBSTOR"):
                self.dock.launch(self.id)


if __name__ == "__main__":
    unittest.main()
