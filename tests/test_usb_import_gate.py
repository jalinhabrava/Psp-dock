from pathlib import Path
import re
import unittest


class UsbImportGateTests(unittest.TestCase):
    base = Path(__file__).resolve().parents[1] / "homebrew/usb-import-gate"

    def test_user_eboot_has_no_usb_or_kernel_import_library(self):
        eboot = (self.base / "dist/EBOOT.PBP").read_bytes()
        self.assertGreater(len(eboot), 1000)
        for name in (
            b"ModuleMgrForKernel", b"ThreadManForKernel", b"LoadExecForKernel",
            b"SysclibForKernel", b"sceUsbstorBoot", b"sceUsb", b"sceUsb_driver"
        ):
            with self.subTest(name=name):
                self.assertNotIn(name, eboot)
        self.assertIn(b"ModuleMgrForUser", eboot)

    def test_plugin_imports_usb_but_no_storage_kernel_or_driver(self):
        plugin = (self.base / "dist/usbstate.prx").read_bytes()
        self.assertGreater(len(plugin), 100)
        self.assertIn(b"sceUsb", plugin)
        for name in (b"ModuleMgrForKernel", b"ThreadManForKernel",
                     b"SysclibForKernel", b"sceUsbstorBoot", b"sceUsb_driver"):
            self.assertNotIn(name, plugin)

    def test_code_is_read_only_and_ui_is_visibly_loaded_first(self):
        ui = (self.base / "ui.c").read_text()
        plugin = (self.base / "usbstate.c").read_text()
        for source in (ui, plugin):
            self.assertIn("PSP_MODULE_USER", source)
        self.assertLess(ui.index("paint();"), ui.index("sceKernelLoadModule("))
        self.assertIsNone(re.search(r"\bsceUsb\w*\s*\(", ui))
        self.assertIn("sceUsbGetState()", plugin)
        self.assertNotIn("sceUsbStart(", plugin)
        self.assertNotIn("sceUsbActivate(", plugin)
        self.assertIsNone(re.search(r"\bsceUsbstorBoot\w*\s*\(", plugin))
        self.assertNotIn("sceIo", plugin)
        self.assertIn("UNSAFE UI IMPORTS: build aborted",
                      (self.base / "build-wsl.sh").read_text())


if __name__ == "__main__":
    unittest.main()
