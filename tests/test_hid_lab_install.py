from pathlib import Path
import tempfile
import unittest

import tools.psp.psp_backup as psp_backup
import tools.labs.hid_lab_install as lab
from tools.psp.vsh_plugin_stage import CONFIG


SERIAL = "2958455453"
USB = "USB\\VID_054C&PID_02D2\\HID_LAB_TEST"


class HidLabInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.psp = Path(self.temp.name) / "PSP-volume"
        self.pc = Path(self.temp.name) / "PC"
        (self.psp / "PSP/GAME").mkdir(parents=True)
        (self.psp / "PSP/SAVEDATA/IMPORTANT").mkdir(parents=True)
        self.save = self.psp / "PSP/SAVEDATA/IMPORTANT/DATA.BIN"
        self.save.write_bytes(b"do not change")
        (self.psp / "SEPLUGINS").mkdir()
        self.config = self.psp / CONFIG
        self.original = b"vsh, ms0:/SEPLUGINS/PSPDockProbe/pspdockvsh.prx, on\n"
        self.config.write_bytes(self.original)
        self.archive = psp_backup.backup(
            self.psp, self.pc, "critical", SERIAL, USB, reserve_bytes=0
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_install_in_new_folder_without_changes_to_saves_or_plugins(self):
        plan = lab.plan_install(self.psp, self.pc, SERIAL, USB)
        self.assertFalse(plan["target"].exists())
        self.assertEqual(self.config.read_bytes(), self.original)
        result = lab.install_lab(self.psp, self.pc, SERIAL, USB)
        self.assertTrue(result["installed"])
        self.assertFalse(result["autorun"])
        self.assertEqual(self.save.read_bytes(), b"do not change")
        self.assertEqual(self.config.read_bytes(), self.original)
        for filename, (relative, expected) in lab.SOURCES.items():
            self.assertEqual((self.psp / lab.TARGET / filename).read_bytes(),
                             (lab.ROOT / relative).read_bytes())
        with self.assertRaisesRegex(lab.HidLabInstallError, "existe"):
            lab.plan_install(self.psp, self.pc, SERIAL, USB)

    def test_corrupted_source_or_backup_identity_fails_without_psp_writes(self):
        with self.assertRaises(Exception):
            lab.plan_install(self.psp, self.pc, SERIAL, "USB\\FOREIGN")
        self.config.write_bytes(self.original + b"vsh, other.prx, on\n")
        with self.assertRaises(Exception):
            lab.plan_install(self.psp, self.pc, SERIAL, USB)
        self.assertFalse((self.psp / lab.TARGET).exists())
        self.assertEqual(self.save.read_bytes(), b"do not change")

    def test_no_overwrite_of_preexisting_directory(self):
        directory = self.psp / lab.TARGET
        directory.mkdir()
        (directory / "EBOOT.PBP").write_bytes(b"foreign")
        with self.assertRaisesRegex(Exception, "existe"):
            lab.install_lab(self.psp, self.pc, SERIAL, USB)
        self.assertEqual((directory / "EBOOT.PBP").read_bytes(), b"foreign")


if __name__ == "__main__":
    unittest.main()
