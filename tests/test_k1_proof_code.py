import re
from pathlib import Path
import unittest


BASE = Path(__file__).resolve().parents[1] / "homebrew/k1-proof-vsh"


class K1ProofCodeTests(unittest.TestCase):
    def test_has_opt_in_and_original_k1_export(self):
        code = (BASE / "main.c").read_text()
        self.assertIn('PSP_MODULE_INFO("PSPDockK1ProofVSH", PSP_MODULE_KERNEL', code)
        self.assertIn("pspDockK1Read(&sample)", code)
        self.assertIn("sceKernelDelayThread(6000000)", code)
        self.assertIn("!armed()", code)
        self.assertIn("PSP_O_EXCL", code)
        self.assertIn("sample.capabilities != 0", code)
        self.assertIn("!idle_and_not_mounted()", code)
        self.assertIn("ROLE_SWITCHED=0", code)

    def test_no_usb_mutations_or_flash(self):
        code = (BASE / "main.c").read_text()
        executable = "\n".join(
            line for line in code.splitlines()
            if not line.lstrip().startswith(("*", "/*", "//"))
        )
        for forbidden in ("sceUsbStart(", "sceUsbStop(", "sceUsbActivate(",
                          "sceUsbDeactivate(", "sceUsbbdRegister(",
                          "sceKernelLoadModule(", "flash0:", "PSP/SAVEDATA"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, executable)
        self.assertEqual(len(re.findall(r"sceIoWrite\(", code)), 1)

    def test_nid_stub_matches_k1_exports(self):
        stub = (BASE / "PSPDockK1Observer.S").read_text()
        exports = (BASE.parent / "kernel-usb-observer-k1/exports.exp").read_text()
        self.assertIn("PSP_EXPORT_FUNC(pspDockK1Read)", exports)
        self.assertIn("0xC81C4981,pspDockK1Read", stub)
        self.assertIn('STUB_START "PSPDockK1Observer"', stub)


if __name__ == "__main__":
    unittest.main()
