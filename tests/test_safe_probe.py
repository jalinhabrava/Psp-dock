import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from install_safe_probe import PROBES, confirm_install, destination_for, stage_binary


class SafeProbePackagingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "PSP/GAME").mkdir(parents=True)
        (self.root / "PSP/SAVEDATA/DO_NOT_TOUCH").mkdir(parents=True)
        (self.root / "PSP/SAVEDATA/DO_NOT_TOUCH/DATA.BIN").write_bytes(b"original save")
        self.input = self.root / "safe-fixture.pbp"
        self.input.write_bytes(b"test-only synthetic EBOOT")

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_screen_binary_installs_in_new_folder_without_overwrite(self):
        with patch.dict(PROBES, {"screen": {
            **PROBES["screen"],
            "sha256": hashlib.sha256(self.input.read_bytes()).hexdigest(),
        }}):
            output = stage_binary(self.root, self.input, "screen")
            self.assertEqual(output.name, "EBOOT.PBP")
            self.assertEqual(output.read_bytes(), self.input.read_bytes())
            with self.assertRaisesRegex(ValueError, "ya existe"):
                stage_binary(self.root, self.input, "screen")
        self.assertEqual((self.root / "PSP/SAVEDATA/DO_NOT_TOUCH/DATA.BIN").read_bytes(),
                         b"original save")

    def test_rejects_wrong_hash_and_unknown_modes(self):
        with self.assertRaisesRegex(ValueError, "distinto"):
            stage_binary(self.root, self.input, "screen")
        with self.assertRaises(ValueError):
            destination_for(self.root, "unknown")
        self.assertEqual(sorted((self.root / "PSP/GAME").iterdir()), [])

    def test_screen_code_never_imports_usb_and_both_are_user_mode(self):
        base = Path(__file__).resolve().parents[1] / "homebrew"
        first = (base / "screen-check/main.c").read_text()
        second = (base / "usb-steps/main.c").read_text()
        for text in (first, second):
            self.assertRegex(text, r"PSP_MODULE_INFO\([^\n]*PSP_MODULE_USER")
            self.assertIn("PSP_MAIN_THREAD_ATTR(PSP_THREAD_ATTR_USER)", text)
            self.assertIn("pspDebugScreenInit();", text)
        self.assertNotIn("sceUsb", first)
        self.assertNotIn("flash0:", first)
        self.assertIn("draw(); /* Visible BEFORE any USB API call. */", second)
        self.assertIn("!failed && next_step < NUM_STEPS", second)

    def test_screen_confirmation_accepts_enter_without_magic_word(self):
        self.assertTrue(confirm_install("screen", read=lambda _: ""))
        self.assertTrue(confirm_install("screen", read=lambda _: "s"))
        self.assertFalse(confirm_install("screen", read=lambda _: "n"))
        self.assertTrue(confirm_install("screen", yes=True))

    def test_automatic_confirmation_cannot_install_usb_diagnostic(self):
        with self.assertRaisesRegex(ValueError, "SIN USB"):
            confirm_install("usb-steps", yes=True)
        self.assertFalse(confirm_install("usb-steps", read=lambda _: ""))
        self.assertTrue(confirm_install("usb-steps", read=lambda _: "pantalla ok"))

    def test_retired_usb_probe_is_refused_before_any_device_operation(self):
        from unittest.mock import patch
        import install_safe_probe
        with patch("sys.argv", ["install_safe_probe.py", "--mode", "usb-steps"]), \
             patch.object(install_safe_probe, "windows_devices",
                          side_effect=AssertionError("Must not touch PSP")):
            self.assertEqual(install_safe_probe.main(), 1)


if __name__ == "__main__":
    unittest.main()
