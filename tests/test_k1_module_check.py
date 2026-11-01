from pathlib import Path
import unittest


PROJECT = Path(__file__).resolve().parents[1] / "homebrew/k1-module-check"


class K1ModuleCheckTests(unittest.TestCase):
    def test_user_read_only_without_usb_flash_or_kernel_mode(self):
        src = (PROJECT / "main.c").read_text()
        self.assertIn('PSP_MODULE_INFO("PSPDockK1Check", PSP_MODULE_USER', src)
        self.assertIn("sceKernelGetModuleIdList(", src)
        self.assertIn("sceKernelQueryModuleInfo(", src)
        for forbidden in ("sceUsbStart(", "sceUsbStop(", "sceUsbActivate(",
                          "sceUsbstor", "sceIoOpen(", "sceIoWrite(",
                          "sceKernelLoadModule(", "sctrlHEN",
                          "pspSdkInstall", "flash0:"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, src)

    def test_missing_kernel_module_does_not_assert_absence(self):
        src = (PROJECT / "main.c").read_text()
        self.assertIn("NO / INCONCLUSIVE", src)
        self.assertIn("PSP_CTRL_START | PSP_CTRL_SELECT", src)
        self.assertNotIn("sceKernelTerminate", src)


if __name__ == "__main__":
    unittest.main()
