"""Offline regression tests. A successful build is NOT a real PSP USB test."""
import hashlib
from pathlib import Path
import re
import tempfile
import unittest

from install_usb_import_gate import install_package
import install_usb_bus_cycle_v08 as version


class UsbBusCycleV08Tests(unittest.TestCase):
    root = Path(__file__).resolve().parents[1]
    source = root / "homebrew/usb-bus-cycle-v08"

    def test_ui_eboot_imports_no_usb_or_kernel_module(self):
        binary = (self.source / "dist/EBOOT.PBP").read_bytes()
        self.assertGreater(len(binary), 100000)
        self.assertIn(b"ModuleMgrForUser", binary)
        for name in (b"ModuleMgrForKernel", b"ThreadManForKernel",
                     b"LoadExecForKernel", b"SysclibForKernel",
                     b"sceUsb", b"sceUsbstorBoot"):
            with self.subTest(name=name):
                self.assertNotIn(name, binary)

    def test_prx_imports_only_user_usb_and_never_storage_api(self):
        binary = (self.source / "dist/usbstate.prx").read_bytes()
        self.assertIn(b"sceUsb", binary)
        for name in (b"ModuleMgrForKernel", b"ThreadManForKernel",
                     b"SysclibForKernel", b"sceUsbstorBoot", b"sceUsb_driver"):
            self.assertNotIn(name, binary)
        code = (self.source / "usbstate.c").read_text()
        self.assertIn("sceUsbGetState()", code)
        self.assertIn("sceUsbStart(PSP_USBBUS_DRIVERNAME, 0, 0)", code)
        self.assertIn("sceUsbStop(PSP_USBBUS_DRIVERNAME, 0, 0)", code)
        self.assertIn("gate_v08_precheck", code)
        self.assertLess(code.index("gate_v08_precheck"), code.index("sceUsbStart("))
        self.assertIn("if (out->guard != GATE_V08_OK)", code)
        self.assertIn("if (out->start_result == 0)", code)
        for forbidden in ("sceUsbActivate(", "sceUsbDeactivate(",
                          "sceUsbstorBoot", "sceKernelLoadModule(",
                          "sceUsbStart(PSP_USBSTOR_DRIVERNAME",
                          "sceUsbStop(PSP_USBSTOR_DRIVERNAME",
                          "sceIo", "flash0:"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, code)

    def test_strict_guard_is_shared_with_prx_and_has_native_tests(self):
        guard = (self.source / "gate_guard.h").read_text()
        tests = (self.source / "test_guard.c").read_text()
        build = (self.source / "build-wsl.sh").read_text()
        for state in ("GATE_V08_BLOCK_USB_ERROR", "GATE_V08_BLOCK_CONNECTION",
                      "GATE_V08_BLOCK_BUS_OWNERSHIP", "GATE_V08_BLOCK_STORAGE"):
            self.assertIn(state, guard)
        self.assertIn("0x80243005", guard)
        self.assertIn("storage != GATE_V08_STOPPED", guard)
        self.assertIn("usb < 0", guard)
        self.assertGreaterEqual(tests.count("assert(gate_v08_precheck"), 10)
        self.assertIn("gcc -std=c11", build)
        self.assertIn('"$build/test_guard"', build)

    def test_ui_requires_three_buttons_and_one_attempt(self):
        code = (self.source / "ui.c").read_text()
        self.assertIn("PSP_MODULE_USER", code)
        self.assertIn("PSP_CTRL_CROSS | PSP_CTRL_LTRIGGER | PSP_CTRL_RTRIGGER", code)
        self.assertIn("!attempted", code)
        self.assertIn("attempted = 1;", code)
        self.assertIn('PSPDockBusCycle08/usbstate.prx', code)
        self.assertLess(code.index("paint();"), code.index("sceKernelLoadModule("))
        self.assertIsNone(re.search(r"\bsceUsb\w*\s*\(", code))
        self.assertIn("if (start_api == module)", code)
        self.assertIn("if (stop_api == 0 && stop_status == 0)", code)

    def test_manifest_and_new_install_preserve_07_and_savedata(self):
        self.assertEqual(version.DEST, "PSP/GAME/PSPDockBusCycle08")
        for name, (relative, expected) in version.PACKAGE.items():
            with self.subTest(name=name):
                payload = (self.root / relative).read_bytes()
                self.assertEqual(hashlib.sha256(payload).hexdigest(), expected)
        with tempfile.TemporaryDirectory() as directory:
            psp = Path(directory)
            old = psp / "PSP/GAME/PSPDockUsbStatus07/EBOOT.PBP"
            old.parent.mkdir(parents=True)
            old.write_bytes(b"real world test fixture 0.7")
            save = psp / "PSP/SAVEDATA/GAME1/DATA.BIN"
            save.parent.mkdir(parents=True)
            save.write_bytes(b"save untouched")
            fixtures = {}
            for name in version.PACKAGE:
                file = psp / ("fixture_" + name)
                file.write_bytes(("synthetic " + name).encode())
                fixtures[name] = (file, hashlib.sha256(file.read_bytes()).hexdigest())
            new_folder = install_package(psp, fixtures, relative_dest=version.DEST)
            self.assertTrue((new_folder / "EBOOT.PBP").exists())
            self.assertTrue((new_folder / "usbstate.prx").exists())
            self.assertEqual(old.read_bytes(), b"real world test fixture 0.7")
            self.assertEqual(save.read_bytes(), b"save untouched")
            with self.assertRaisesRegex(ValueError, "No se sobrescribe"):
                install_package(psp, fixtures, relative_dest=version.DEST)


if __name__ == "__main__":
    unittest.main()
