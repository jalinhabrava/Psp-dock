from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import tools.psp.psp_backup as psp_backup
import tools.psp.k1_plugin_enable as enable
import tools.psp.k1_plugin_recovery as recover
from tools.psp.k1_plugin_stage import K1StageError, K1_ENTRY, K1_BIN, K1_FILE, K1_STAGE_DIR
from tools.psp.vsh_plugin_stage import CONFIG, FILES, STAGE_DIR

USB = "USB\\VID_054C&PID_02D2\\K1_ENABLE_TEST"
SERIAL = "2958455453"
VSH = b"vsh, ms0:/SEPLUGINS/PSPDockProbe/pspdockvsh.prx, on\n"


class K1EnableTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "psp"
        self.pc = Path(self.tmp.name) / "backups"
        self.cfgbackups = Path(self.tmp.name) / "recovery"
        (self.root / "PSP/GAME").mkdir(parents=True)
        (self.root / "PSP/SAVEDATA/PLAYER").mkdir(parents=True)
        self.save = self.root / "PSP/SAVEDATA/PLAYER/FILE.DAT"
        self.save.write_bytes(b"save kept intact")
        vsh = self.root / STAGE_DIR
        vsh.mkdir(parents=True)
        project = Path(__file__).resolve().parents[1]
        for filename, (relative, _) in FILES.items():
            (vsh / filename).write_bytes((project / relative).read_bytes())
        k1 = self.root / K1_STAGE_DIR
        k1.mkdir(parents=True)
        (k1 / K1_FILE).write_bytes(K1_BIN.read_bytes())
        self.cfg = self.root / CONFIG
        self.before = VSH + K1_ENTRY
        self.cfg.write_bytes(self.before)
        self.snapshot = psp_backup.backup(
            self.root, self.pc, "critical", SERIAL, USB, reserve_bytes=0)

    def tearDown(self):
        self.tmp.cleanup()

    def test_enable_only_k1_and_recover_off(self):
        plan = enable.preflight_enable_k1(self.root, self.pc, SERIAL, USB)
        self.assertEqual(plan["after"], self.before.replace(b", off", b", on"))
        self.assertEqual(self.cfg.read_bytes(), self.before)
        result = enable.enable_k1(self.root, self.pc, self.cfgbackups, SERIAL, USB)
        self.assertTrue(result["enabled"])
        self.assertEqual(self.cfg.read_bytes(), plan["after"])
        self.assertEqual(Path(result["config_backup"]).read_bytes(), self.before)
        self.assertEqual(self.save.read_bytes(), b"save kept intact")
        old, changed = recover.plan_off_k1(self.cfg.read_bytes())
        self.assertTrue(changed)
        self.assertEqual(old, self.before)
        outcome = recover.recover_k1_off(self.root, self.cfgbackups)
        self.assertTrue(outcome["changed"])
        self.assertEqual(self.cfg.read_bytes(), self.before)
        self.assertFalse(recover.recover_k1_off(self.root, self.cfgbackups)["changed"])

    def test_rejects_unknown_or_duplicate_k1_config(self):
        for bad in (self.before + K1_ENTRY,
                    self.before.replace(b", off", b", game"),
                    VSH + b"vsh, ms0:/SEPLUGINS/PSPDockKernelK1/foreign.prx, on\n",
                    VSH):
            with self.subTest(bad=bad[:80]):
                self.cfg.write_bytes(bad)
                with self.assertRaises(K1StageError):
                    enable.plan_enable_k1(bad)
                with self.assertRaises(K1StageError):
                    recover.recover_k1_off(self.root, self.cfgbackups)
                self.assertEqual(self.cfg.read_bytes(), bad)

    def test_mismatched_backup_or_damaged_prx_blocks_on(self):
        with self.assertRaises(Exception):
            enable.preflight_enable_k1(self.root, self.pc, SERIAL, "USB\\BAD")
        (self.root / K1_STAGE_DIR / K1_FILE).write_bytes(b"tampered")
        with self.assertRaisesRegex(K1StageError, "PRX K1"):
            enable.preflight_enable_k1(self.root, self.pc, SERIAL, USB)
        self.assertEqual(self.cfg.read_bytes(), self.before)

    def test_recovery_can_turn_off_corrupted_plugin_without_backup(self):
        self.cfg.write_bytes(enable.plan_enable_k1(self.before))
        (self.root / K1_STAGE_DIR / K1_FILE).write_bytes(b"broken kernel")
        result = recover.recover_k1_off(self.root, self.cfgbackups)
        self.assertTrue(result["changed"])
        self.assertEqual(self.cfg.read_bytes(), self.before)

    def test_no_on_if_commit_fails_and_backup_remains(self):
        with patch.object(enable.os, "replace", side_effect=OSError("USB disappeared")):
            with self.assertRaisesRegex(OSError, "disappeared"):
                enable.enable_k1(self.root, self.pc, self.cfgbackups, SERIAL, USB)
        self.assertEqual(self.cfg.read_bytes(), self.before)
        self.assertEqual(len(list(self.cfgbackups.glob("PLUGINS.before-k1-on-*.bak"))), 1)


if __name__ == "__main__":
    unittest.main()
