from pathlib import Path
import unittest


DIR = Path(__file__).resolve().parents[1] / "homebrew/hid-only-lab"


class HidOnlyLabTests(unittest.TestCase):
    def test_kernel_guards_storage_and_usb_before_hid(self):
        src = (DIR / "lab_core.c").read_text()
        self.assertIn("hid_lab_safe_to_start(usb, storage, bus)", src)
        gate = (DIR / "lab_gate.h").read_text()
        self.assertIn("HID_LAB_USB_BUSY_MASK", gate)
        self.assertIn("HID_LAB_STORAGE_NOENT 0x80243005u", gate)
        self.assertIn("sceUsbGetDrvState(PSP_USBSTOR_DRIVERNAME)", src)
        self.assertIn("bridgeHidStart(session)", src)
        self.assertIn("bridgeHidStop()", src)
        self.assertIn("sceUsbStop(PSP_USBBUS_DRIVERNAME", src)
        self.assertIn("if (bus_owned)", src)
        self.assertIn("TIMEOUT_US 90000000u", src)
        for forbidden in ("sceUsbStart(PSP_USBSTOR", "sceUsbStop(PSP_USBSTOR",
                          "sceUsbstorBoot", "sceIoOpen(", "sceIoWrite(",
                          "sceKernelLoadModule(", "flash0:", "PSP/SAVEDATA"):
            with self.subTest(symbol=forbidden):
                self.assertNotIn(forbidden, src)

    def test_user_eboot_has_no_usb_and_manual_launch(self):
        code = (DIR / "main.c").read_text()
        self.assertIn("PSP_MODULE_USER", code)
        self.assertIn("sceKernelLoadModule", code)
        self.assertIn("sceKernelStartModule", code)
        self.assertIn("bridgehid.prx", code)
        self.assertIn("hidonlycore.prx", code)
        for forbidden in ("sceUsbStart(", "sceUsbStop(", "sceUsbActivate(",
                          "sceUsbGetState(", "sceUsbstor", "flash0:",
                          "sceIoOpen(", "sceIoWrite("):
            with self.subTest(symbol=forbidden):
                self.assertNotIn(forbidden, code)
        self.assertNotIn("PLUGINS.TXT", (DIR / "build-wsl.sh").read_text())

    def test_licensing_and_time_limit(self):
        readme = (DIR / "README.md").read_text()
        self.assertIn("GPL-3.0", readme)
        self.assertIn("90 segundos", readme)
        self.assertIn("no", readme.lower())

    def test_dynamic_kernel_hid_import_flags_match_sdk_generated_stub(self):
        stub = (DIR / "PSPDockHid.S").read_text()
        self.assertIn('STUB_START "PSPDockHid",0x00090000', stub)
        for nid in ("0x7a1d88aa", "0xdcd83471", "0x57190859", "0x9237d589"):
            self.assertIn(nid, stub)


if __name__ == "__main__":
    unittest.main()
