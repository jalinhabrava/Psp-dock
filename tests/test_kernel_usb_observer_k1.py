from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1] / "homebrew/kernel-usb-observer-k1"


class KernelUsbObserverK1Tests(unittest.TestCase):
    def test_no_usb_mutation_or_fat_imports_in_frozen_source(self):
        code = (ROOT / "main.c").read_text()
        statements = [
            line.strip() for line in code.splitlines()
            if not line.strip().startswith("*") and
               not line.strip().startswith("/*")
        ]
        executable = "\n".join(statements)
        self.assertIn("sceUsbGetState()", executable)
        self.assertIn("sceUsbGetDrvState(", executable)
        for forbidden in (
            "sceUsbStart(", "sceUsbStop(", "sceUsbActivate(",
            "sceUsbDeactivate(", "sceUsbbdRegister(",
            "sceUsbbdUnregister(", "sceUsbstorBoot",
            "sceKernelLoadModule(", "sceIoOpen(", "sceIoWrite(",
            "flash0:", "sceKernelExitGame(", "sceUsbWaitState("
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, executable)

    def test_no_role_capability_or_storage_mutation_abi(self):
        header = (ROOT / "protocol.h").read_text()
        self.assertIn("#define PSPDOCK_K1_CAPABILITIES 0u", header)
        self.assertIn("PspDockK1Snapshot", header)
        self.assertNotIn("RequestRole", header)
        self.assertNotIn("Switch", header)
        exports = (ROOT / "exports.exp").read_text()
        self.assertIn("PSP_EXPORT_FUNC(pspDockK1Read)", exports)
        self.assertNotIn("RequestRole", exports)

    def test_no_installer_or_boot_config_in_experimental_tree(self):
        self.assertFalse((ROOT / "Install.cmd").exists())
        self.assertFalse((ROOT / "PLUGINS.TXT").exists())
        build = (ROOT / "build-wsl.sh").read_text()
        self.assertNotIn("SEPLUGINS/", build)
        self.assertIn("psp-readelf -Ws", build)


if __name__ == "__main__":
    unittest.main()
