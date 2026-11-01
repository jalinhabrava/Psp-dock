import hashlib
from pathlib import Path
import tempfile
import unittest

import psp_backup
from vsh_plugin_stage import ENTRY, FILES, CONFIG, STAGE_DIR
from vsh_plugin_arm_enabled import plan_armed_on, arm_for_next_boot
from vsh_plugin_enable import EnableError


SERIAL = "2958455453"
USB = "USB\\VID_054C&PID_02D2\\PSP_TEST"


class ArmedEnabledTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.root = Path(self.t.name) / "card"
        self.pc = Path(self.t.name) / "backup"
        (self.root / "PSP/GAME").mkdir(parents=True)
        (self.root / "PSP/SAVEDATA/USER").mkdir(parents=True)
        (self.root / "PSP/SAVEDATA/USER/DATA.BIN").write_bytes(b"do not touch")
        folder = self.root / STAGE_DIR
        folder.mkdir(parents=True)
        base = Path(__file__).resolve().parents[1]
        for filename, (relative, checksum) in FILES.items():
            (folder / filename).write_bytes((base / relative).read_bytes())
        (self.root / CONFIG).write_bytes(ENTRY)
        self.snapshot = psp_backup.backup(self.root, self.pc, "critical",
                                          SERIAL, USB, reserve_bytes=0)
        (self.root / CONFIG).write_bytes(ENTRY.replace(b", off", b", on"))

    def tearDown(self):
        self.t.cleanup()

    def test_can_arm_on_after_verified_first_boot_without_config_toggling(self):
        self.assertEqual((self.root / CONFIG).read_bytes(),
                         ENTRY.replace(b", off", b", on"))
        report = plan_armed_on(self.root, self.pc, SERIAL, USB)
        self.assertEqual(Path(report["backup"]), Path(self.snapshot["directory"]))
        armed = arm_for_next_boot(self.root, self.pc, SERIAL, USB)
        self.assertTrue(armed["armed"])
        self.assertEqual((self.root / CONFIG).read_bytes(),
                         ENTRY.replace(b", off", b", on"))
        self.assertEqual((self.root / "PSP/SAVEDATA/USER/DATA.BIN").read_bytes(),
                         b"do not touch")
        with self.assertRaises(EnableError):
            arm_for_next_boot(self.root, self.pc, SERIAL, USB)

    def test_requires_backup_identity_and_frozen_prx(self):
        with self.assertRaises(EnableError):
            plan_armed_on(self.root, self.pc, SERIAL, "USB\\VID_054C&PID_9999")
        (self.root / STAGE_DIR / "usbstate.prx").write_bytes(b"tampered")
        with self.assertRaisesRegex(EnableError, "PRX"):
            plan_armed_on(self.root, self.pc, SERIAL, USB)

    def test_no_arm_if_plugin_off_or_old_boot_marker(self):
        (self.root / CONFIG).write_bytes(ENTRY)
        with self.assertRaises(EnableError):
            plan_armed_on(self.root, self.pc, SERIAL, USB)
        (self.root / CONFIG).write_bytes(ENTRY.replace(b", off", b", on"))
        (self.root / STAGE_DIR / "BOOT.TXT").write_bytes(b"old")
        with self.assertRaisesRegex(EnableError, "BOOT"):
            plan_armed_on(self.root, self.pc, SERIAL, USB)


if __name__ == "__main__":
    unittest.main()
