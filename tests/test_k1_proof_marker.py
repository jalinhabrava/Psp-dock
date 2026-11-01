from pathlib import Path
import tempfile
import unittest

import k1_proof_admin as proof
import k1_proof_marker as marker
import psp_backup
from k1_plugin_enable import K1_ON_LINE
from k1_plugin_stage import K1_FILE, K1_STAGE_DIR, K1_BIN, CONFIG
from vsh_plugin_stage import FILES, STAGE_DIR


VSH_ON = b"vsh, ms0:/SEPLUGINS/PSPDockProbe/pspdockvsh.prx, on\n"
SERIAL = "2958455453"
USB = "USB\\VID_054C&PID_02D2\\PROOF_ARM_TEST"
PROOF_BYTES = (b"PSPDOCK_K1_PROOF=01\nMAGIC=50444B31\nVERSION=00000001\n"
               b"CAPABILITIES=00000000\nSTATE=00000001\nUSB=00000111\n"
               b"BUS=00000001\nSTORAGE=80243005\nROLE_SWITCHED=0\n")


class K1ProofMarkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "psp"
        self.pc = Path(self.tmp.name) / "pc"
        (self.root / "PSP/GAME").mkdir(parents=True)
        (self.root / "PSP/SAVEDATA/KEEP").mkdir(parents=True)
        self.save = self.root / "PSP/SAVEDATA/KEEP/USER.DAT"
        self.save.write_bytes(b"unchanged")
        vsh = self.root / STAGE_DIR
        vsh.mkdir(parents=True)
        for filename, (rel, _) in FILES.items():
            (vsh / filename).write_bytes((Path(__file__).parents[1] / rel).read_bytes())
        kernel = self.root / K1_STAGE_DIR
        kernel.mkdir(parents=True)
        (kernel / K1_FILE).write_bytes(K1_BIN.read_bytes())
        proofdir = self.root / proof.PROOF_STAGE
        proofdir.mkdir(parents=True)
        (proofdir / proof.PROOF_NAME).write_bytes(proof.PROOF_SOURCE.read_bytes())
        (self.root / CONFIG).write_bytes(VSH_ON + K1_ON_LINE + b"\n" + proof.PROOF_ON + b"\n")
        self.snapshot = psp_backup.backup(
            self.root, self.pc, "critical", SERIAL, USB, reserve_bytes=0)
        self.dir = proofdir

    def tearDown(self):
        self.tmp.cleanup()

    def test_exact_marker_parses_but_never_grants_fat(self):
        report = marker.parse_proof_marker(PROOF_BYTES)
        self.assertEqual(report["magic"], 0x50444B31)
        self.assertEqual(report["state"], 1)
        self.assertEqual(report["storage"], 0x80243005)
        self.assertFalse(report["exclusive_fat_confirmed"])
        self.assertFalse(report["role_switch_confirmed"])
        for bad in (
            PROOF_BYTES.replace(b"STATE=00000001", b"STATE=00000000"),
            PROOF_BYTES.replace(b"USB=00000111", b"USB=00000311"),
            PROOF_BYTES.replace(b"ROLE_SWITCHED=0", b"ROLE_SWITCHED=1"),
            PROOF_BYTES.replace(b"MAGIC=50444B31", b"MAGIC=00000000"),
            PROOF_BYTES.replace(b"CAPABILITIES=00000000", b"CAPABILITIES=00000001"),
            PROOF_BYTES + b"extra\n",
            b"\xff" + PROOF_BYTES,
        ):
            with self.subTest(bad=bad[:40]), self.assertRaises(Exception):
                marker.parse_proof_marker(bad)

    def test_arm_once_after_verified_on_and_disarm_preserving_evidence(self):
        planned = marker.plan_arm(self.root, self.pc, SERIAL, USB)
        self.assertEqual(Path(planned["backup"]), Path(self.snapshot["directory"]))
        outcome = marker.arm(self.root, self.pc, SERIAL, USB)
        self.assertTrue(outcome["armed"])
        self.assertEqual((self.dir / "ARM.TXT").read_bytes(), proof.PROOF_ARM_TOKEN)
        with self.assertRaises(Exception):
            marker.arm(self.root, self.pc, SERIAL, USB)
        (self.dir / "K1PROOF.TXT").write_bytes(PROOF_BYTES)
        self.assertEqual(marker.read_status(self.root)["proof"]["magic"], 0x50444B31)
        response = marker.disarm(self.root)
        self.assertTrue(response["changed"])
        self.assertTrue(response["proof_preserved"])
        self.assertFalse((self.dir / "ARM.TXT").exists())
        self.assertEqual((self.dir / "K1PROOF.TXT").read_bytes(), PROOF_BYTES)
        self.assertEqual(self.save.read_bytes(), b"unchanged")

    def test_unknown_marker_never_deleted(self):
        (self.dir / "ARM.TXT").write_bytes(b"FOREIGN")
        with self.assertRaises(Exception):
            marker.disarm(self.root)
        self.assertEqual((self.dir / "ARM.TXT").read_bytes(), b"FOREIGN")
        with self.assertRaises(Exception):
            marker.plan_arm(self.root, self.pc, SERIAL, USB)

    def test_reject_arming_with_unverified_backup_or_disabled_proof(self):
        with self.assertRaises(Exception):
            marker.plan_arm(self.root, self.pc, SERIAL, "USB\\OTHER")
        cfg = self.root / CONFIG
        cfg.write_bytes(cfg.read_bytes().replace(proof.PROOF_ON, proof.PROOF_OFF))
        with self.assertRaises(Exception):
            marker.plan_arm(self.root, self.pc, SERIAL, USB)

    def test_reject_arming_if_kernel_was_modified(self):
        binary = self.root / K1_STAGE_DIR / K1_FILE
        binary.write_bytes(b"bad kernel")
        with self.assertRaisesRegex(Exception, "kernel"):
            marker.plan_arm(self.root, self.pc, SERIAL, USB)


if __name__ == "__main__":
    unittest.main()
