"""Offline validation only; PSP real must still confirm version 0.6."""
import hashlib
from pathlib import Path
import re
import tempfile
import unittest

from tools.psp.install_usb_import_gate import install_package
import tools.psp.install_usb_import_gate_v06 as v06


class GateV06Tests(unittest.TestCase):
    root = Path(__file__).resolve().parents[1]
    sources = root / "homebrew/usb-import-gate-v06"

    def test_eboot_never_imports_usb_or_kernel(self):
        binary = (self.sources / "dist/EBOOT.PBP").read_bytes()
        self.assertGreater(len(binary), 100000)
        for module in (
            b"sceUsb", b"sceUsbstorBoot", b"ModuleMgrForKernel",
            b"ThreadManForKernel", b"LoadExecForKernel", b"SysclibForKernel",
        ):
            self.assertNotIn(module, binary)
        self.assertIn(b"ModuleMgrForUser", binary)

    def test_read_only_prx_keeps_success_separate_from_usb_state(self):
        binary = (self.sources / "dist/usbstate.prx").read_bytes()
        self.assertGreater(len(binary), 100)
        self.assertIn(b"sceUsb", binary)
        for item in (b"sceUsbstorBoot", b"ModuleMgrForKernel",
                     b"SysclibForKernel", b"sceUsb_driver"):
            self.assertNotIn(item, binary)
        code = (self.sources / "usbstate.c").read_text()
        self.assertIn("target->usb_state = sceUsbGetState();", code)
        self.assertIn("return 0;", code.split("target->usb_state = sceUsbGetState();", 1)[1])
        self.assertIn("argsize != sizeof(GateArguments)", code)
        self.assertIn("address < 0x08800000u", code)
        self.assertIn("address > 0x0bfffff8u", code)
        for name in ("sceUsbActivate(", "sceUsbDeactivate(", "sceUsbStart(", "sceUsbStop("):
            self.assertNotIn(name, code)
        self.assertNotIn("sceIo", code)

    def test_ui_only_stops_resident_module_and_no_past_versions_overwritten(self):
        ui = (self.sources / "ui.c").read_text()
        self.assertIn("PSP_MODULE_USER", ui)
        self.assertIn("if (start_api == module)", ui)
        self.assertIn("if (stop_api == 0 && stop_status == 0)", ui)
        self.assertIn('PSPDockUsbGate06/usbstate.prx', ui)
        self.assertLess(ui.index("paint();"), ui.index("sceKernelLoadModule("))
        self.assertIsNone(re.search(r"\bsceUsb[A-Za-z]*\s*\(", ui))

    def test_v06_manifest_matches_local_files(self):
        self.assertEqual(v06.DEST, "PSP/GAME/PSPDockUsbGate06")
        for name, (rel, sha) in v06.PACKAGE.items():
            with self.subTest(name=name):
                self.assertEqual(hashlib.sha256((self.root / rel).read_bytes()).hexdigest(), sha)

    def test_copy_preserves_old_05_and_savedata(self):
        with tempfile.TemporaryDirectory() as d:
            psp = Path(d)
            old = psp / "PSP/GAME/PSPDockUsbGate/EBOOT.PBP"
            old.parent.mkdir(parents=True)
            old.write_bytes(b"old approved image")
            savedata = psp / "PSP/SAVEDATA/KEEP/DATA.BIN"
            savedata.parent.mkdir(parents=True)
            savedata.write_bytes(b"keep")
            src = {}
            for name in v06.PACKAGE:
                file = psp / ("fixture_" + name)
                file.write_bytes(("synthetic" + name).encode())
                src[name] = (file, hashlib.sha256(file.read_bytes()).hexdigest())
            output = install_package(psp, src, relative_dest=v06.DEST)
            self.assertTrue((output / "EBOOT.PBP").exists())
            self.assertEqual(old.read_bytes(), b"old approved image")
            self.assertEqual(savedata.read_bytes(), b"keep")
            with self.assertRaisesRegex(ValueError, "No se sobrescribe"):
                install_package(psp, src, relative_dest=v06.DEST)


if __name__ == "__main__":
    unittest.main()
