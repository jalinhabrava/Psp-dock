"""A remembered PSP emulator can trigger a PC-only install while PSP is HID.

No real network, removable disk, external emulator or hardware is touched.
"""
from __future__ import annotations

from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from dock import Dock
from psp_dock.emulation.psp_retro import SYSTEMS
from psp_dock.emulation.retro_installer import managed_retroarch


class RetroInstallDuringHid(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.psp = self.home / "synthetic-psp"
        emulator = self.psp / "PSP/GAME/gpSP"
        (emulator / "ROMS").mkdir(parents=True)
        (emulator / "EBOOT.PBP").write_bytes(b"synthetic PSP emulator")
        self.rom = emulator / "ROMS/Synthetic.gba"
        self.rom.write_bytes(b"fake ROM, never run")
        (self.psp / "PSP/SAVEDATA").mkdir(parents=True)
        usb = patch("dock.windows_devices", return_value=[])
        usb.start()
        self.addCleanup(usb.stop)
        self.dock = Dock(self.home / "data")
        # Initial catalog is read while USB storage is available. Disable
        # network while creating the synthetic inventory, then opt in.
        self.dock.settings({"psp_root": str(self.psp),
                            "retro_install_policy": "off"})
        self.assertFalse(self.dock.retro_catalog()["roms"][0]["core_available"])
        self.dock.settings({"retro_install_policy": "auto"})

    def test_installs_on_pc_when_usb_polling_is_paused_for_hid(self):
        observed_rom = self.rom.read_bytes()
        exe = managed_retroarch(self.dock.config["storage_root"])
        started = threading.Event()

        def install(storage, emulators, **kwargs):
            self.assertEqual(Path(storage), Path(self.dock.config["storage_root"]))
            self.assertEqual(emulators[0]["systems"], ["gba"])
            exe.parent.mkdir(parents=True)
            exe.write_bytes(b"fake PC executable - not launched")
            dll = exe.parent / "cores" / SYSTEMS["gba"][1]
            dll.parent.mkdir()
            dll.write_bytes(b"fake PC DLL")
            started.set()
            return {"installed_cores": [dll.name], "downloaded_bytes": 0}

        self.dock.pause_storage_polling(True)
        self.assertIsNone(self.dock.root())
        with (patch("dock.install_pc_retro", side_effect=install) as network,
              patch("dock.emulator_inventory", side_effect=AssertionError("USB rescan")),
              patch.object(self.dock, "transfer_retro", side_effect=AssertionError("ROM copy")),
              patch.object(self.dock, "retro_save_plan", side_effect=AssertionError("save IO"))):
            self.assertTrue(self.dock.start_retro_install(automatic=True))
            self.assertTrue(started.wait(timeout=4))
            for _ in range(400):
                with self.dock.retro_installer_lock:
                    if not self.dock.retro_install_progress["active"]:
                        break
                threading.Event().wait(0.01)
            self.assertFalse(self.dock.retro_install_progress["active"])
            network.assert_called_once()

        self.assertEqual(self.rom.read_bytes(), observed_rom)
        self.assertTrue(exe.is_file())
        self.assertTrue(self.dock.cached_retro_results["roms"][0]["core_available"])
        self.assertEqual(self.dock.retro_install_progress["phase"], "ready")
        self.assertFalse(self.dock.retro_install_state()["volume_present"])

    def test_explicit_exit_still_blocks_background_install(self):
        self.dock.pause_storage_polling(True)
        self.dock.session["exit_requested"] = True
        with patch("dock.install_pc_retro", side_effect=AssertionError("network")):
            self.assertFalse(self.dock.start_retro_install(automatic=True))
            with self.assertRaises(ValueError):
                self.dock.start_retro_install(automatic=False)


if __name__ == "__main__":
    unittest.main()
