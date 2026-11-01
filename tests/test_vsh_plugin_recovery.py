import hashlib
from pathlib import Path
import tempfile
import unittest

import vsh_plugin_recovery as rollback
from vsh_plugin_stage import CONFIG, ENTRY


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = self.root / CONFIG
        self.config.parent.mkdir(parents=True)
        self.pc = self.root / "PC-BACKUP"
        self.old = b"vsh, other.prx, on\n"
        self.enabled = ENTRY.replace(b", off", b", on")
        self.config.write_bytes(self.old + self.enabled)

    def tearDown(self):
        self.temp.cleanup()

    def test_exact_on_to_off_preserves_other_plugins_and_backups(self):
        before = self.config.read_bytes()
        result = rollback.restore_off(self.root, self.pc)
        self.assertTrue(result["changed"])
        self.assertEqual(self.config.read_bytes(), self.old + ENTRY)
        copy = Path(result["backup"])
        self.assertEqual(copy.read_bytes(), before)
        self.assertEqual(copy.name,
                         "PLUGINS.before-recovery-" +
                         hashlib.sha256(before).hexdigest() + ".bak")
        self.assertFalse(rollback.restore_off(self.root, self.pc)["changed"])

    def test_refuses_multiple_or_unknown_plugin_lines(self):
        for bad in (
            self.old + self.enabled + self.enabled,
            self.old + b"vsh, ms0:/SEPLUGINS/PSPDockProbe/pspdockvsh.prx, game\n",
            self.old + b"vsh, ms0:/SEPLUGINS/PSPDockProbe/fake.prx, on\n",
            self.old,
            b"\x00" + self.enabled
        ):
            with self.subTest(contents=bad[:50]):
                self.config.write_bytes(bad)
                with self.assertRaises(ValueError):
                    rollback.restore_off(self.root, self.pc)
                self.assertEqual(self.config.read_bytes(), bad)

    def test_on_to_off_preserves_crlf(self):
        content = self.old.replace(b"\n", b"\r\n") + self.enabled.replace(b"\n", b"\r\n")
        self.config.write_bytes(content)
        rollback.restore_off(self.root, self.pc)
        self.assertEqual(self.config.read_bytes(),
                         self.old.replace(b"\n", b"\r\n") +
                         ENTRY.replace(b"\n", b"\r\n"))

    def test_failed_backup_write_preserves_plugin_config(self):
        original = self.config.read_bytes()
        self.pc.mkdir()
        import unittest.mock
        with unittest.mock.patch.object(rollback.os, "replace",
                                        side_effect=OSError("simulated unplug")):
            with self.assertRaises(OSError):
                rollback.restore_off(self.root, self.pc)
        self.assertEqual(self.config.read_bytes(), original)
        self.assertTrue(list(self.pc.glob("PLUGINS.before-recovery-*.bak")))


if __name__ == "__main__":
    unittest.main()
