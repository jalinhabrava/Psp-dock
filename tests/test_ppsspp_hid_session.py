import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import psp_dock.emulation.ppsspp_hid_session as hid


DEVICE = {
    "path": r"\\?\hid#vid_054c&pid_01cb#demo",
    "status": {
        "connected": True, "pending": False, "consumed": False,
        "mode": 2, "version": 1, "session": "ab" * 16,
    },
}


class HidPpssppSessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.live = self.root / "PC/live"
        self.library = self.live / "library/ISO"
        self.library.mkdir(parents=True)
        self.game = self.library / "Example.iso"
        self.game.write_bytes(b"\0" * hid.MIN_GAME_BYTES)
        with self.game.open("r+b") as f:
            f.seek(16 * 2048 + 1)
            f.write(b"CD001")
        (self.live / "session.json").write_text(
            json.dumps({"running": False, "pending_return": False}), encoding="utf-8"
        )
        self.emu = self.root / "ppsspp/PPSSPPWindows64.exe"
        self.emu.parent.mkdir()
        (self.emu.parent / "assets").mkdir()
        self.emu.write_bytes(b"test fixture, not runnable")
        self.private = self.root / "pc-hid-sandbox"

    def tearDown(self):
        self.tmp.cleanup()

    @patch.object(hid, "windows_devices", return_value=[])
    def test_local_only_game_selected_without_opening_psp(self, devices):
        plan = hid.plan_game(self.live, private_data=self.private, emulator=self.emu)
        self.assertEqual(plan.game, self.game)
        self.assertEqual(plan.relative, "ISO/Example.iso")
        self.assertEqual(plan.private_memstick, self.private / "memstick")
        self.assertFalse(plan.private_data.exists())
        self.assertEqual(devices.call_count, 2)

    @patch.object(hid, "windows_devices", return_value=[])
    def test_iso_requires_valid_local_descriptor_and_size(self, devices):
        self.game.write_bytes(b"fake")
        with self.assertRaisesRegex(hid.HidPpssppError, "completa"):
            hid.plan_game(self.live, private_data=self.private, emulator=self.emu)
        self.game.write_bytes(b"x" * hid.MIN_GAME_BYTES)
        with self.assertRaisesRegex(hid.HidPpssppError, "ISO9660"):
            hid.plan_game(self.live, private_data=self.private, emulator=self.emu)

    @patch.object(hid, "windows_devices", return_value=[{"root": "G:\\"}])
    def test_storage_mounted_blocks_hid_play(self, devices):
        with self.assertRaisesRegex(hid.HidPpssppError, "USBSTOR"):
            hid.validate_hid_only()

    @patch.object(hid, "hid_probe", return_value={"supported": True,
                                                   "errors": [],
                                                   "devices": [DEVICE]})
    @patch.object(hid, "windows_devices", return_value=[])
    def test_exact_active_pdk1_required(self, devices, probe):
        self.assertEqual(hid.validate_hid_only()["session"], "ab" * 16)
        for status in (
            {**DEVICE["status"], "connected": False},
            {**DEVICE["status"], "pending": True},
            {**DEVICE["status"], "consumed": True},
            {**DEVICE["status"], "session": ""},
            {**DEVICE["status"], "mode": 1},
            {**DEVICE["status"], "version": 2},
        ):
            with self.subTest(status=status):
                probe.return_value = {
                    "supported": True, "errors": [],
                    "devices": [{"path": DEVICE["path"], "status": status}],
                }
                with self.assertRaises(hid.HidPpssppError):
                    hid.validate_hid_only()
        probe.return_value = {
            "supported": True, "errors": [{"path": "invalid"}], "devices": [DEVICE],
        }
        with self.assertRaises(hid.HidPpssppError):
            hid.validate_hid_only()
        probe.return_value = {
            "supported": True, "errors": [], "devices": [DEVICE, DEVICE],
        }
        with self.assertRaises(hid.HidPpssppError):
            hid.validate_hid_only()

    @patch.object(hid, "windows_devices", return_value=[])
    def test_session_pending_and_local_journal_block_preparation(self, devices):
        plan = hid.plan_game(self.live, private_data=self.private, emulator=self.emu)
        with patch.object(hid, "ppsspp_open", return_value=False), \
                patch.object(hid, "real_dock_open", return_value=False):
            (self.live / "session.json").write_text(
                json.dumps({"running": False, "pending_return": True}))
            with self.assertRaisesRegex(hid.HidPpssppError, "pendiente"):
                hid._prevent_concurrent_usage(plan)
            (self.live / "session.json").write_text(
                json.dumps({"running": False, "pending_return": False}))
            folder = self.live / "memstick/PSP/SAVEDATA"
            folder.mkdir(parents=True)
            (folder / ".dock-journal-test.json").write_text("{}")
            with self.assertRaisesRegex(hid.HidPpssppError, "journal"):
                hid._prevent_concurrent_usage(plan)

    @patch.object(hid, "windows_devices", return_value=[])
    def test_ppsspp_launch_must_see_same_hid_twice_and_use_pc_only(self, devices):
        plan = hid.plan_game(self.live, private_data=self.private, emulator=self.emu)
        with patch.object(hid, "_prevent_concurrent_usage"), \
                patch.object(hid, "validate_psp_controls"), \
                patch.object(hid, "verified_prepared_runtime", return_value=self.emu), \
                patch.object(hid, "validate_hid_only", side_effect=[
                    {"session": "ab" * 16, "path": DEVICE["path"]},
                    {"session": "ab" * 16, "path": DEVICE["path"]},
                ]) as checked, \
                patch.object(hid.subprocess, "Popen") as popen:
            popen.return_value.wait.return_value = 0
            self.assertEqual(hid.start_ppsspp(plan), 0)
            self.assertEqual(checked.call_count, 2)
            args = popen.call_args.args[0]
            self.assertEqual(args[0], str(self.emu))
            self.assertEqual(args[-1], str(self.game))
            self.assertNotIn("G:\\", str(args))

        with patch.object(hid, "_prevent_concurrent_usage"), \
                patch.object(hid, "validate_psp_controls"), \
                patch.object(hid, "verified_prepared_runtime", return_value=self.emu), \
                patch.object(hid, "validate_hid_only", side_effect=[
                    {"session": "ab" * 16, "path": DEVICE["path"]},
                    {"session": "cd" * 16, "path": DEVICE["path"]},
                ]), \
                patch.object(hid.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(hid.HidPpssppError, "cambió"):
                hid.start_ppsspp(plan)
            popen.assert_not_called()

    @patch.object(hid, "windows_devices", return_value=[])
    def test_preparation_manifest_verifies_isolation_without_copying_on_play(self, devices):
        plan = hid.plan_game(self.live, private_data=self.private, emulator=self.emu)
        with self.assertRaisesRegex(hid.HidPpssppError, "Prepara"):
            hid.verified_prepared_runtime(plan)
        private_exe = self.private / "runtime/abc/PPSSPPWindows64.exe"
        private_exe.parent.mkdir(parents=True)
        private_exe.write_bytes(self.emu.read_bytes())
        (private_exe.parent / "installed.txt").write_text(str(plan.private_memstick) + "\n")
        ini = plan.private_memstick / "PSP/SYSTEM/ppsspp.ini"
        ini.parent.mkdir(parents=True)
        ini.write_text("[Graphics]\nInternalResolution=3\n")
        with patch.object(hid, "_prevent_concurrent_usage"), \
                patch.object(hid, "prepare_emulator", return_value=private_exe):
            self.assertEqual(hid.prepare_private_runtime(plan), private_exe)
        self.assertEqual(hid.verified_prepared_runtime(plan), private_exe)
        (private_exe.parent / "installed.txt").write_text("G:\\\n")
        with self.assertRaisesRegex(hid.HidPpssppError, "Memory Stick local"):
            hid.verified_prepared_runtime(plan)

    def test_library_on_psp_is_never_accepted(self):
        drive = self.root / "USB-PSP"
        (drive / "library").mkdir(parents=True)
        with patch.object(hid, "windows_devices", return_value=[{"root": str(drive)}]):
            with self.assertRaisesRegex(hid.HidPpssppError, "PSP USB"):
                hid.plan_game(drive, private_data=self.private, emulator=self.emu)

    @patch.object(hid, "windows_devices", return_value=[])
    def test_gameplay_requires_correct_ppsspp_psp_controls(self, devices):
        import psp_dock.emulation.ppsspp_psp_button_map as mapping

        plan = hid.plan_game(self.live, private_data=self.private, emulator=self.emu)
        controls = plan.private_memstick / "PSP/SYSTEM/controls.ini"
        controls.parent.mkdir(parents=True)
        stock = b"[ControlMapping]\n" + b"".join(
            f"{key} = 1-54,20-96,10-{value}\n".encode()
            for key, value in mapping.STOCK_DINPUT.items()
        )
        controls.write_bytes(stock)
        with self.assertRaisesRegex(hid.HidPpssppError, "cruzados"):
            hid.validate_psp_controls(plan)
        fixed, _ = mapping.remap_controls(stock)
        controls.write_bytes(fixed)
        self.assertEqual(hid.validate_psp_controls(plan), controls)
        controls.write_bytes(b"totally unknown")
        with self.assertRaisesRegex(hid.HidPpssppError, "no reconocido"):
            hid.validate_psp_controls(plan)

    @patch.object(hid, "windows_devices", return_value=[])
    def test_wait_watches_storage_then_hid_and_launches_only_once(self, devices):
        plan = hid.plan_game(self.live, private_data=self.private, emulator=self.emu)
        devices.reset_mock()
        devices.side_effect = [[{"root": "G:\\"}], [], []]
        empty = {"supported": True, "errors": [], "devices": []}
        ready = {"supported": True, "errors": [], "devices": [DEVICE]}
        with patch.object(hid, "_prevent_concurrent_usage"), \
                patch.object(hid, "validate_psp_controls"), \
                patch.object(hid, "verified_prepared_runtime"), \
                patch.object(hid, "hid_probe", side_effect=[empty, empty, ready]), \
                patch.object(hid, "validate_hid_only", return_value={
                    "path": DEVICE["path"], "session": "ab" * 16,
                }) as validated, \
                patch.object(hid, "start_ppsspp", return_value=0) as start, \
                patch.object(hid.time, "sleep"):
            self.assertEqual(hid.wait_for_hid_and_play(plan, 180), 0)
        validated.assert_called_once()
        start.assert_called_once_with(plan)

    @patch.object(hid, "windows_devices", return_value=[])
    def test_wait_refuses_ambiguous_dual_usb_and_invalid_timeout(self, devices):
        plan = hid.plan_game(self.live, private_data=self.private, emulator=self.emu)
        with patch.object(hid, "_prevent_concurrent_usage"), \
                patch.object(hid, "validate_psp_controls"), \
                patch.object(hid, "verified_prepared_runtime"), \
                patch.object(hid, "hid_probe", return_value={
                    "supported": True, "errors": [], "devices": [DEVICE],
                }), \
                patch.object(hid, "start_ppsspp") as start:
            devices.return_value = [{"root": "G:\\"}]
            with self.assertRaisesRegex(hid.HidPpssppError, "a la vez"):
                hid.wait_for_hid_and_play(plan)
            devices.return_value = []
            with self.assertRaisesRegex(hid.HidPpssppError, "límite"):
                hid.wait_for_hid_and_play(plan, seconds=3)
            start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
