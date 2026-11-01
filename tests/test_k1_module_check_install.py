from pathlib import Path
import tempfile
import unittest

import psp_backup
import k1_module_check_install as check
from k1_plugin_enable import K1_ON_LINE
from k1_plugin_stage import K1_FILE, K1_STAGE_DIR, K1_BIN, CONFIG
from vsh_plugin_stage import FILES, STAGE_DIR


SERIAL = "2958455453"
USB = "USB\\VID_054C&PID_02D2\\SAFE_INSTALL"
VSH = b"vsh, ms0:/SEPLUGINS/PSPDockProbe/pspdockvsh.prx, on\n"


class K1CheckInstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "psp"
        self.backups = Path(self.tmp.name) / "backups"
        (self.root / "PSP/GAME").mkdir(parents=True)
        (self.root / "PSP/SAVEDATA/PLAYER").mkdir(parents=True)
        self.save = self.root / "PSP/SAVEDATA/PLAYER/PROGRESS.DAT"
        self.save.write_bytes(b"original progress")
        vsh = self.root / STAGE_DIR
        vsh.mkdir(parents=True)
        for filename, (relative, _) in FILES.items():
            (vsh / filename).write_bytes(
                (Path(__file__).parents[1] / relative).read_bytes())
        kernel = self.root / K1_STAGE_DIR
        kernel.mkdir(parents=True)
        (kernel / K1_FILE).write_bytes(K1_BIN.read_bytes())
        self.cfg = self.root / CONFIG
        self.cfg.write_bytes(VSH + K1_ON_LINE + b"\n")
        self.snap = psp_backup.backup(
            self.root, self.backups, "critical", SERIAL, USB, reserve_bytes=0)

    def tearDown(self):
        self.tmp.cleanup()

    def test_install_only_new_eboot_and_keep_plugins_saves(self):
        orig = self.cfg.read_bytes()
        plan = check.inspect_install(self.root, self.backups, SERIAL, USB)
        self.assertFalse(plan["dest"].exists())
        report = check.install_check(self.root, self.backups, SERIAL, USB)
        self.assertTrue(report["installed"])
        self.assertFalse(report["autostart"])
        self.assertEqual(self.cfg.read_bytes(), orig)
        self.assertEqual(self.save.read_bytes(), b"original progress")
        self.assertEqual((self.root / check.CHECK_DIR / "EBOOT.PBP").read_bytes(),
                         check.EBOOT.read_bytes())
        with self.assertRaisesRegex(Exception, "existe"):
            check.inspect_install(self.root, self.backups, SERIAL, USB)

    def test_reject_missing_backup_foreign_usb_or_wrong_hash(self):
        with self.assertRaises(Exception):
            check.inspect_install(self.root, self.backups, SERIAL, "USB\\OTHER")
        fake = self.root / "altered.PBP"
        fake.write_bytes(b"bad")
        with self.assertRaisesRegex(Exception, "artefacto"):
            check.inspect_install(self.root, self.backups, SERIAL, USB, fake)
        self.assertFalse((self.root / check.CHECK_DIR).exists())

    def test_reject_k1_off_and_armed_vsh(self):
        self.cfg.write_bytes(VSH + K1_ON_LINE.replace(b", on", b", off") + b"\n")
        with self.assertRaisesRegex(Exception, "cambiado"):
            check.inspect_install(self.root, self.backups, SERIAL, USB)
        self.cfg.write_bytes(VSH + K1_ON_LINE + b"\n")
        (self.root / STAGE_DIR / "ARM.TXT").write_bytes(b"unknown")
        with self.assertRaisesRegex(Exception, "desarmado"):
            check.inspect_install(self.root, self.backups, SERIAL, USB)


if __name__ == "__main__":
    unittest.main()
