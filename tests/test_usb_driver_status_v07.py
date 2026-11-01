"""Host-only regression guard for the next read-only PSP USB probe."""
import hashlib
from pathlib import Path
import re
import tempfile
import unittest

from install_usb_import_gate import install_package
import install_usb_import_gate_v07 as v07


class DriverStatusV07Tests(unittest.TestCase):
    root = Path(__file__).resolve().parents[1]
    src = root / "homebrew/usb-driver-status-v07"

    def test_user_ui_never_imports_usb_or_kernel(self):
        binary = (self.src / "dist/EBOOT.PBP").read_bytes()
        self.assertGreater(len(binary), 100000)
        for forbidden in (b"sceUsb", b"sceUsbstorBoot", b"ModuleMgrForKernel",
                          b"ThreadManForKernel", b"LoadExecForKernel", b"SysclibForKernel"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, binary)
        self.assertIn(b"ModuleMgrForUser", binary)

    def test_prx_is_readonly_and_three_status_samples(self):
        binary = (self.src / "dist/usbstate.prx").read_bytes()
        self.assertIn(b"sceUsb", binary)
        self.assertNotIn(b"sceUsbstorBoot", binary)
        self.assertNotIn(b"ModuleMgrForKernel", binary)
        code = (self.src / "usbstate.c").read_text()
        for call in ("sceUsbGetState()", "sceUsbGetDrvState(PSP_USBBUS_DRIVERNAME)",
                     "sceUsbGetDrvState(PSP_USBSTOR_DRIVERNAME)"):
            self.assertIn(call, code)
        for dangerous in ("sceUsbActivate(", "sceUsbDeactivate(", "sceUsbStart(",
                          "sceUsbStop(", "sceIo", "sceKernelLoadModule(",
                          "sceUsbstorBoot"):
            self.assertNotIn(dangerous, code)
        self.assertRegex(code, r"target->storage_driver_state\s*=\s*sceUsbGetDrvState")
        self.assertIn("return 0;", code)

    def test_ui_and_protocol_separate_results_from_lifecycle(self):
        ui = (self.src / "ui.c").read_text()
        protocol = (self.src / "gate_protocol.h").read_text()
        self.assertIn("PSP_MODULE_USER", ui)
        self.assertIn('PSPDockUsbStatus07/usbstate.prx', ui)
        self.assertIn("if (start_api == module)", ui)
        self.assertIn("if (stop_api == 0 && stop_status == 0)", ui)
        self.assertLess(ui.index("paint();"), ui.index("sceKernelLoadModule("))
        self.assertIsNone(re.search(r"\bsceUsb[A-Za-z]*\s*\(", ui))
        self.assertIn("sizeof(GateResult) == 16", protocol)

    def test_v07_manifest_and_separate_target(self):
        self.assertEqual(v07.DEST, "PSP/GAME/PSPDockUsbStatus07")
        for name, (rel, sha) in v07.PACKAGE.items():
            self.assertEqual(hashlib.sha256((self.root / rel).read_bytes()).hexdigest(), sha)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / "PSP/GAME/PSPDockUsbGate06/EBOOT.PBP"
            old.parent.mkdir(parents=True)
            old.write_bytes(b"v0.6 verified")
            save = root / "PSP/SAVEDATA/KEEP/DATA.BIN"
            save.parent.mkdir(parents=True)
            save.write_bytes(b"preserve")
            sources = {}
            for name in v07.PACKAGE:
                candidate = root / ("source_" + name)
                candidate.write_bytes(("synthetic_" + name).encode())
                sources[name] = (candidate, hashlib.sha256(candidate.read_bytes()).hexdigest())
            result = install_package(root, sources, relative_dest=v07.DEST)
            self.assertEqual(result, root / v07.DEST)
            self.assertEqual(old.read_bytes(), b"v0.6 verified")
            self.assertEqual(save.read_bytes(), b"preserve")
            with self.assertRaisesRegex(ValueError, "No se sobrescribe"):
                install_package(root, sources, relative_dest=v07.DEST)


if __name__ == "__main__":
    unittest.main()
