"""Ensure the compiled kernel prototype CANNOT change PSP USB roles."""
import hashlib
from pathlib import Path
import unittest


class KernelRoleStubTests(unittest.TestCase):
    root = Path(__file__).resolve().parents[1]
    location = root / "homebrew/kernel-role-stub"

    def test_binary_is_compiled_but_not_in_psp_stage_manifest(self):
        binary = (self.location / "dist/pspdockkernelstub.prx").read_bytes()
        self.assertGreater(len(binary), 100)
        self.assertEqual(
            hashlib.sha256(binary).hexdigest(),
            "759ff121788b4fbbffd98a2d903f20ecb2163bdcfb3269f4d1bf1b654cd62172"
        )
        import tools.psp.vsh_plugin_stage as vsh_plugin_stage
        self.assertNotIn("pspdockkernelstub.prx", vsh_plugin_stage.FILES)

    def test_kernel_stub_exposes_only_inert_role_abi(self):
        source = (self.location / "main.c").read_text()
        headers = (self.location / "role_protocol.h").read_text()
        self.assertIn("PSP_MODULE_KERNEL", source)
        self.assertIn("return PSPDOCK_KSTUB_UNAVAILABLE;", source)
        self.assertIn("#define PSPDOCK_KSTUB_CAPS 0u", headers)
        self.assertIn("#define PSPDOCK_KSTUB_IDLE 0u", headers)
        for function in ("sceUsbStart(", "sceUsbStop(", "sceUsbActivate(",
                         "sceUsbDeactivate(", "sceIo", "flash0:", "sctrlHENPatch"):
            self.assertNotIn(function, source)


if __name__ == "__main__":
    unittest.main()
