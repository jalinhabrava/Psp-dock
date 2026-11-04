"""First VSH ON test must be reversible and require verified source backup."""
from pathlib import Path
import hashlib
import tempfile
import unittest
from unittest.mock import patch

import tools.psp.psp_backup as psp_backup
import tools.psp.vsh_plugin_enable as vsh
import tools.psp.vsh_plugin_recovery as recovery
from tools.psp.vsh_plugin_stage import CONFIG, STAGE_DIR, ENTRY, FILES


USB = "USB\\VID_054C&PID_02D2\\PSP_FIXTURE"
SERIAL = "2958455453"


class VshEnableTests(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory()
        self.base = Path(self.workspace.name)
        self.psp = self.base / "PSP-volume"
        self.archive = self.base / "PC-backups"
        self.config_backup = self.base / "CONFIG-backups"
        (self.psp / "PSP/GAME").mkdir(parents=True)
        (self.psp / "PSP/SAVEDATA/USER").mkdir(parents=True)
        (self.psp / "PSP/SAVEDATA/USER/SAVE.BIN").write_bytes(b"precious data")
        folder = self.psp / STAGE_DIR
        folder.mkdir(parents=True)
        for name, (relative, expected) in FILES.items():
            src = vsh.__file__
            (folder / name).write_bytes((vsh.Path(__file__).resolve().parents[1] /
                                         relative).read_bytes())
            self.assertEqual(hashlib.sha256((folder / name).read_bytes()).hexdigest(),
                             expected)
        config = self.psp / CONFIG
        config.write_bytes(b"vsh, ms0:/SEPLUGINS/example.prx, on\r\n" +
                           ENTRY.replace(b"\n", b"\r\n"))
        self.orig_config = config.read_bytes()
        self.backup_result = psp_backup.backup(self.psp, self.archive, "critical",
                                               SERIAL, USB, verify_identity=lambda: True,
                                               reserve_bytes=0)

    def tearDown(self):
        self.workspace.cleanup()

    def test_enable_requires_matching_verified_backup_and_preserves_saves(self):
        plan = vsh.preflight(self.psp, self.archive, SERIAL, USB)
        self.assertEqual(plan["original"], self.orig_config)
        self.assertNotEqual(plan["proposed"], self.orig_config)
        outcome = vsh.enable_observer_off_to_on(
            self.psp, self.archive, self.config_backup, SERIAL, USB)
        self.assertTrue(outcome["enabled"])
        self.assertFalse(outcome["armed"])
        self.assertEqual((self.psp / CONFIG).read_bytes(),
                         self.orig_config.replace(b", off", b", on"))
        self.assertFalse((self.psp / STAGE_DIR / "ARM.TXT").exists())
        self.assertFalse((self.psp / STAGE_DIR / "BOOT.TXT").exists())
        self.assertEqual((self.psp / "PSP/SAVEDATA/USER/SAVE.BIN").read_bytes(),
                         b"precious data")
        self.assertEqual(Path(outcome["config_backup"]).read_bytes(), self.orig_config)
        self.assertEqual(recovery.plan_off((self.psp / CONFIG).read_bytes())[0],
                         self.orig_config)
        with self.assertRaisesRegex(vsh.EnableError, "OFF"):
            vsh.enable_observer_off_to_on(
                self.psp, self.archive, self.config_backup, SERIAL, USB)

    def test_preflight_does_not_write_when_only_preview(self):
        before = {p.relative_to(self.psp): p.read_bytes()
                  for p in self.psp.rglob("*") if p.is_file()}
        vsh.preflight(self.psp, self.archive, SERIAL, USB)
        after = {p.relative_to(self.psp): p.read_bytes()
                 for p in self.psp.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertFalse(self.config_backup.exists())

    def test_missing_or_wrong_backup_identity_refuses_enable(self):
        with self.assertRaisesRegex(vsh.EnableError, "backup crítico"):
            vsh.preflight(self.psp, self.archive, "WRONG_VOLUME", USB)
        with self.assertRaisesRegex(vsh.EnableError, "backup crítico"):
            vsh.preflight(self.psp, self.archive, SERIAL, "USB\\VID_054C&PID_FFFF")
        self.assertEqual((self.psp / CONFIG).read_bytes(), self.orig_config)

    def test_reject_armed_observer_or_unknown_original_plugin_line(self):
        (self.psp / STAGE_DIR / "ARM.TXT").write_text("present")
        with self.assertRaisesRegex(vsh.EnableError, "ARM"):
            vsh.preflight(self.psp, self.archive, SERIAL, USB)
        (self.psp / STAGE_DIR / "ARM.TXT").unlink()
        (self.psp / CONFIG).write_bytes(b"vsh, other.prx, on\n" +
                                        b"vsh, ms0:/SEPLUGINS/PSPDockProbe/pspdockvsh.prx, game\n")
        with self.assertRaisesRegex(vsh.EnableError, "OFF"):
            vsh.preflight(self.psp, self.archive, SERIAL, USB)

    def test_corrupted_backup_refuses_and_preserves_psp(self):
        snapshot = Path(self.backup_result["directory"])
        (snapshot / "data/SEPLUGINS/PLUGINS.TXT").write_bytes(b"tampered backup")
        with self.assertRaises(psp_backup.BackupError):
            vsh.preflight(self.psp, self.archive, SERIAL, USB)
        self.assertEqual((self.psp / CONFIG).read_bytes(), self.orig_config)

    def test_plugin_binary_modified_refuses_enable(self):
        (self.psp / STAGE_DIR / "pspdockvsh.prx").write_bytes(b"broken")
        with self.assertRaisesRegex(vsh.EnableError, "PRX instalado"):
            vsh.preflight(self.psp, self.archive, SERIAL, USB)

    def test_duplicate_plugin_lines_refuses(self):
        original = (self.psp / CONFIG).read_bytes()
        (self.psp / CONFIG).write_bytes(original + ENTRY)
        with self.assertRaisesRegex(vsh.EnableError, "entrada única"):
            vsh.preflight(self.psp, self.archive, SERIAL, USB)

    def test_failed_commit_preserves_off_and_pc_backup(self):
        with patch.object(vsh.os, "replace",
                          side_effect=OSError("simulated lost USB")):
            with self.assertRaisesRegex(OSError, "lost USB"):
                vsh.enable_observer_off_to_on(
                    self.psp, self.archive, self.config_backup, SERIAL, USB)
        self.assertEqual((self.psp / CONFIG).read_bytes(), self.orig_config)
        backup = list(self.config_backup.glob("*.bak"))
        self.assertEqual(len(backup), 1)
        self.assertEqual(backup[0].read_bytes(), self.orig_config)


if __name__ == "__main__":
    unittest.main()
