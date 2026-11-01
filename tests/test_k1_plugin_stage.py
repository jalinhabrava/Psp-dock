import hashlib
from pathlib import Path
import tempfile
import unittest

import psp_backup
import k1_plugin_stage as k1
from vsh_plugin_stage import CONFIG, FILES, STAGE_DIR


SERIAL = "2958455453"
USB = "USB\\VID_054C&PID_02D2\\K1_SYNTHETIC"
VSH_ON = b"vsh, ms0:/SEPLUGINS/PSPDockProbe/pspdockvsh.prx, on\n"


class K1StageTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.card = Path(self.t.name) / "psp"
        self.pc = Path(self.t.name) / "pc"
        self.config_backup = Path(self.t.name) / "k1-configs"
        (self.card / "PSP/GAME").mkdir(parents=True)
        (self.card / "PSP/SAVEDATA/KEEP").mkdir(parents=True)
        self.savedata = self.card / "PSP/SAVEDATA/KEEP/FILE.DAT"
        self.savedata.write_bytes(b"valuable save")
        folder = self.card / STAGE_DIR
        folder.mkdir(parents=True)
        project = Path(__file__).resolve().parents[1]
        for filename, (relative, _) in FILES.items():
            (folder / filename).write_bytes((project / relative).read_bytes())
        cfg = self.card / CONFIG
        cfg.write_bytes(VSH_ON)
        self.snapshot = psp_backup.backup(
            self.card, self.pc, "critical", SERIAL, USB, reserve_bytes=0
        )

    def tearDown(self):
        self.t.cleanup()

    def test_prepare_disabled_without_changing_vsh_or_saves(self):
        report = k1.preflight_k1(self.card, self.pc, SERIAL, USB)
        self.assertEqual(report["after"], VSH_ON + k1.K1_ENTRY)
        self.assertEqual((self.card / CONFIG).read_bytes(), VSH_ON)
        self.assertFalse((self.card / k1.K1_STAGE_DIR).exists())
        result = k1.stage_disabled_k1(
            self.card, self.pc, self.config_backup, SERIAL, USB)
        self.assertFalse(result["enabled"])
        self.assertEqual((self.card / CONFIG).read_bytes(), VSH_ON + k1.K1_ENTRY)
        self.assertEqual((self.card / k1.K1_STAGE_DIR / k1.K1_FILE).read_bytes(),
                         k1.K1_BIN.read_bytes())
        self.assertEqual(self.savedata.read_bytes(), b"valuable save")
        self.assertEqual(Path(result["config_backup"]).read_bytes(), VSH_ON)
        self.assertEqual(Path(result["source_backup"]), Path(self.snapshot["directory"]))
        with self.assertRaisesRegex(k1.K1StageError, "existente"):
            k1.preflight_k1(self.card, self.pc, SERIAL, USB)

    def test_rejects_duplicate_and_unexpected_vsh_config(self):
        for data in (b"", VSH_ON + VSH_ON, VSH_ON.replace(b", on", b", off"),
                     VSH_ON + k1.K1_ENTRY,
                     VSH_ON + b"vsh, ms0:/SEPLUGINS/PSPDockKernelK1/fake.prx, on\n"):
            with self.subTest(config=data), self.assertRaises(k1.K1StageError):
                k1.config_with_disabled_k1(data)
        self.assertEqual(k1.config_with_disabled_k1(
            VSH_ON.replace(b"\n", b"\r\n")),
            (VSH_ON + k1.K1_ENTRY).replace(b"\n", b"\r\n"))

    def test_backup_identity_prx_integrity_and_armed_gate(self):
        with self.assertRaisesRegex(k1.K1StageError, "K1"):
            fake = self.card / "malicious.prx"
            fake.write_bytes(b"garbage")
            k1.preflight_k1(self.card, self.pc, SERIAL, USB, fake)
        with self.assertRaises(Exception):
            k1.preflight_k1(self.card, self.pc, SERIAL, "USB\\INVALID")
        (self.card / STAGE_DIR / "ARM.TXT").write_text("armed")
        with self.assertRaisesRegex(k1.K1StageError, "desarmado"):
            k1.preflight_k1(self.card, self.pc, SERIAL, USB)
        (self.card / STAGE_DIR / "ARM.TXT").unlink()
        (self.card / STAGE_DIR / "usbstate.prx").write_bytes(b"tampered")
        with self.assertRaisesRegex(k1.K1StageError, "íntegro"):
            k1.preflight_k1(self.card, self.pc, SERIAL, USB)

    def test_no_staging_when_unverified_backup_or_corrupt_config(self):
        (self.card / CONFIG).write_bytes(VSH_ON + b"vsh, third-party.prx, on\n")
        with self.assertRaisesRegex(Exception, "backup"):
            k1.preflight_k1(self.card, self.pc, SERIAL, USB)
        self.assertFalse((self.card / k1.K1_STAGE_DIR).exists())


if __name__ == "__main__":
    unittest.main()
