from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import tools.psp.k1_proof_admin as proof
import tools.psp.psp_backup as psp_backup
from tools.psp.k1_plugin_enable import K1_ON_LINE
from tools.psp.k1_plugin_stage import K1StageError, K1_FILE, K1_STAGE_DIR, K1_BIN, CONFIG
from tools.psp.vsh_plugin_stage import FILES, STAGE_DIR


VSH_ON = b"vsh, ms0:/SEPLUGINS/PSPDockProbe/pspdockvsh.prx, on\n"
SERIAL = "2958455453"
USB = "USB\\VID_054C&PID_02D2\\K1_PROOF_TEST"


class ProofAdminTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.card = Path(self.tmp.name) / "PSP"
        self.pc = Path(self.tmp.name) / "PC"
        self.config_pc = Path(self.tmp.name) / "configs"
        (self.card / "PSP/GAME").mkdir(parents=True)
        (self.card / "PSP/SAVEDATA/REAL").mkdir(parents=True)
        self.save = self.card / "PSP/SAVEDATA/REAL/FILE.DAT"
        self.save.write_bytes(b"original and important")
        vsh = self.card / STAGE_DIR
        vsh.mkdir(parents=True)
        base = Path(__file__).resolve().parents[1]
        for name, (relative, _) in FILES.items():
            (vsh / name).write_bytes((base / relative).read_bytes())
        kernel = self.card / K1_STAGE_DIR
        kernel.mkdir(parents=True)
        (kernel / K1_FILE).write_bytes(K1_BIN.read_bytes())
        self.cfg = self.card / CONFIG
        self.cfg.write_bytes(VSH_ON + K1_ON_LINE + b"\n")
        self.initial = self.cfg.read_bytes()
        self._backup()

    def _backup(self):
        return psp_backup.backup(self.card, self.pc, "critical",
                                 SERIAL, USB, reserve_bytes=0)

    def tearDown(self):
        self.tmp.cleanup()

    def test_stage_off_enable_without_arm_and_recover(self):
        preview = proof.plan_stage(self.card, self.pc, SERIAL, USB)
        self.assertEqual(self.cfg.read_bytes(), self.initial)
        self.assertEqual(preview["after"],
                         self.initial + proof.PROOF_OFF + b"\n")
        staged = proof.stage_off(self.card, self.pc, self.config_pc, SERIAL, USB)
        self.assertEqual(staged["stage"], "OFF")
        self.assertEqual(self.cfg.read_bytes(), preview["after"])
        self.assertEqual((self.card / proof.PROOF_STAGE / proof.PROOF_NAME).read_bytes(),
                         proof.PROOF_SOURCE.read_bytes())
        self.assertEqual(self.save.read_bytes(), b"original and important")
        self.assertFalse((self.card / proof.PROOF_STAGE / "ARM.TXT").exists())
        with self.assertRaisesRegex(K1StageError, "existe"):
            proof.plan_stage(self.card, self.pc, SERIAL, USB)
        with self.assertRaises(Exception):
            proof.plan_enable(self.card, self.pc, SERIAL, USB)
        self._backup()  # Fresh exact OFF config backup.
        enabled = proof.enable_unarmed(self.card, self.pc, self.config_pc, SERIAL, USB)
        self.assertTrue(enabled["proof"] == "ON" and not enabled["armed"])
        self.assertEqual(proof.inspect_proof(self.card)["proof_config"], "ON")
        self.assertEqual(self.cfg.read_bytes(), self.initial + proof.PROOF_ON + b"\n")
        self.assertFalse((self.card / proof.PROOF_STAGE / "ARM.TXT").exists())
        recovered = proof.recover_off(self.card, self.config_pc)
        self.assertTrue(recovered["changed"])
        self.assertEqual(self.cfg.read_bytes(), preview["after"])
        self.assertFalse(proof.recover_off(self.card, self.config_pc)["changed"])
        self.assertEqual(self.save.read_bytes(), b"original and important")

    def test_bad_config_duplicates_and_foreign_backup_refused(self):
        for original in (
            b"", self.initial + self.initial,
            self.initial + proof.PROOF_OFF + b"\n",
            self.initial + b"vsh, ms0:/SEPLUGINS/PSPDockK1Proof/other.prx, on\n",
            self.initial.replace(b", on", b", off"),
        ):
            with self.subTest(original=original[:45]):
                with self.assertRaises(K1StageError):
                    proof.config_for_stage(original)
        with self.assertRaises(Exception):
            proof.plan_stage(self.card, self.pc, SERIAL, "USB\\OTHER")
        self.cfg.write_bytes(self.initial + proof.PROOF_OFF + b"\n")
        with self.assertRaisesRegex(K1StageError, "no sobreescribir|duplicados"):
            proof.plan_stage(self.card, self.pc, SERIAL, USB)

    def test_damaged_k1_or_old_arm_blocks_stage(self):
        (self.card / K1_STAGE_DIR / K1_FILE).write_bytes(b"bad")
        with self.assertRaisesRegex(K1StageError, "kernel"):
            proof.plan_stage(self.card, self.pc, SERIAL, USB)
        (self.card / K1_STAGE_DIR / K1_FILE).write_bytes(K1_BIN.read_bytes())
        (self.card / STAGE_DIR / "ARM.TXT").write_bytes(b"unexpected")
        with self.assertRaisesRegex(K1StageError, "desarmado"):
            proof.plan_stage(self.card, self.pc, SERIAL, USB)

    def test_recovery_does_not_require_kernel_binary_to_remain_healthy(self):
        staged = self.initial + proof.PROOF_ON + b"\n"
        self.cfg.write_bytes(staged)
        (self.card / K1_STAGE_DIR / K1_FILE).write_bytes(b"corrupted")
        recovered = proof.recover_off(self.card, self.config_pc)
        self.assertTrue(recovered["changed"])
        self.assertEqual(self.cfg.read_bytes(), self.initial + proof.PROOF_OFF + b"\n")

    def test_no_config_mutation_if_atomic_publish_fails(self):
        with patch.object(proof.os, "replace", side_effect=OSError("simulated unplug")):
            with self.assertRaisesRegex(OSError, "unplug"):
                proof.stage_off(self.card, self.pc, self.config_pc, SERIAL, USB)
        self.assertEqual(self.cfg.read_bytes(), self.initial)
        self.assertTrue(list(self.config_pc.glob("PLUGINS.before-proof-stage-*.bak")))
        self.assertEqual(self.save.read_bytes(), b"original and important")


if __name__ == "__main__":
    unittest.main()
