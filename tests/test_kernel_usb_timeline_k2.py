from pathlib import Path
import unittest


DIR = Path(__file__).resolve().parents[1] / "homebrew/kernel-usb-timeline-k2"


class KernelUsbTimelineTests(unittest.TestCase):
    def test_kernel_never_controls_usb_or_writes_files(self):
        src = (DIR / "main.c").read_text()
        self.assertIn("sceUsbGetState()", src)
        self.assertIn("sceUsbGetDrvState(", src)
        self.assertIn("pspDockK2Capture(", src)
        for forbidden in ("sceUsbStart(", "sceUsbStop(", "sceUsbActivate(",
                          "sceUsbDeactivate(", "sceIoOpen(", "sceIoWrite(",
                          "sceUsbstorBootRegisterNotify(", "sctrlHENPatch",
                          "sceKernelLoadModule(", "sceUsbbdRegister("):
            with self.subTest(symbol=forbidden):
                self.assertNotIn(forbidden, src)

    def test_zero_capabilities_and_kernel_only_snapshot(self):
        hdr = (DIR / "timeline.h").read_text()
        self.assertIn("#define PSPDOCK_K2_CAPABILITIES 0u", hdr)
        self.assertIn("PSPDOCK_K2_HISTORY_MAX 16u", hdr)
        self.assertIn("sizeof(PspDockK2Timeline) == 344", hdr)
        exp = (DIR / "exports.exp").read_text()
        self.assertIn("PSP_EXPORT_FUNC(pspDockK2GetSnapshot)", exp)
        self.assertNotIn("RequestRole", exp)

    def test_build_has_no_psp_installer(self):
        build = (DIR / "build-wsl.sh").read_text()
        self.assertIn("psp-readelf -Ws", build)
        self.assertNotIn("SEPLUGINS", build)
        self.assertFalse((DIR / "Install.cmd").exists())
        self.assertFalse((DIR / "PLUGINS.TXT").exists())


if __name__ == "__main__":
    unittest.main()
