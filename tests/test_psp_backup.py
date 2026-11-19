"""Read-only PSP backup and recovery preflight; all paths are synthetic."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import tools.psp.psp_backup as backup


class PspBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)
        self.source = self.workspace / "PSP-card"
        self.target = self.workspace / "PC-backups"
        (self.source / "PSP/GAME/ARK_Loader").mkdir(parents=True)
        (self.source / "PSP/SAVEDATA/GAME01").mkdir(parents=True)
        (self.source / "PSP/SYSTEM").mkdir(parents=True)
        (self.source / "SEPLUGINS/PSPDockProbe").mkdir(parents=True)
        (self.source / "ISO").mkdir()
        (self.source / "System Volume Information").mkdir()
        self.contents = {
            "PSP/SAVEDATA/GAME01/DATA.BIN": b"vital save \x00\x01",
            "PSP/SAVEDATA/GAME01/ICON0.PNG": b"PNG fixture",
            "PSP/SYSTEM/ARK.CFG": b"user ARK preferences",
            "PSP/GAME/ARK_Loader/EBOOT.PBP": b"synthetic ARK binary",
            "SEPLUGINS/PLUGINS.TXT": b"vsh, other.prx, on\n",
            "SEPLUGINS/PSPDockProbe/pspdockvsh.prx": b"synthetic plugin",
            "ISO/TITLE.iso": b"BIG GAME simulated small",
            "System Volume Information/tracker": b"ignore windows metadata",
        }
        for relative, payload in self.contents.items():
            dest = self.source.joinpath(*relative.split("/"))
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(payload)

    def tearDown(self):
        self.temp.cleanup()

    def test_critical_backup_includes_saves_plugins_firmware_configs(self):
        files, missing = backup.selection(self.source, "critical")
        selected = {name for name, _ in files}
        self.assertIn("PSP/SAVEDATA/GAME01/DATA.BIN", selected)
        self.assertIn("SEPLUGINS/PLUGINS.TXT", selected)
        self.assertIn("PSP/GAME/ARK_Loader/EBOOT.PBP", selected)
        self.assertIn("PSP/SYSTEM/ARK.CFG", selected)
        self.assertNotIn("ISO/TITLE.iso", selected)
        self.assertNotIn("System Volume Information/tracker", selected)
        self.assertEqual(missing, [])

    def test_full_backup_includes_games_not_windows_metadata(self):
        names = {name for name, _ in backup.selection(self.source, "full")[0]}
        self.assertIn("ISO/TITLE.iso", names)
        self.assertNotIn("System Volume Information/tracker", names)

    def test_backup_verified_manifest_on_pc_source_remains_identical(self):
        before = {n: (self.source.joinpath(*n.split("/")).read_bytes(),
                      self.source.joinpath(*n.split("/")).stat().st_mtime_ns)
                  for n in self.contents}
        report = backup.backup(self.source, self.target, "critical", "123456",
                               "USB\\VID_054C&PID_02D2\\FAKE",
                               verify_identity=lambda: True, reserve_bytes=0)
        self.assertEqual(report["status"], "verified")
        copied = Path(report["directory"])
        self.assertTrue((copied / "manifest.json").exists())
        self.assertFalse((copied / "BACKUP-INCOMPLETE.txt").exists())
        self.assertEqual(backup.verify_archive(copied)["status"], "verified")
        import json
        manifest = json.loads((copied / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "verified")
        self.assertEqual(manifest["volume_serial"], "123456")
        for rel, record in manifest["files"].items():
            content = self.source.joinpath(*rel.split("/")).read_bytes()
            self.assertEqual((copied / "data").joinpath(*rel.split("/")).read_bytes(),
                             content)
            self.assertEqual(hashlib.sha256(content).hexdigest(), record["sha256"])
        for rel, (content, mtime) in before.items():
            origin = self.source.joinpath(*rel.split("/"))
            self.assertEqual(origin.read_bytes(), content)
            self.assertEqual(origin.stat().st_mtime_ns, mtime)

    def test_archive_recheck_detects_tampering_or_extra_file(self):
        report = backup.backup(self.source, self.target, "critical", "123456",
                               "USB\\VID_054C&PID_02D2\\FAKE",
                               verify_identity=lambda: True, reserve_bytes=0)
        archive = Path(report["directory"])
        self.assertEqual(backup.verify_archive(archive)["file_count"],
                         report["file_count"])
        (archive / "data" / "SEPLUGINS/PLUGINS.TXT").write_bytes(b"tampered")
        with self.assertRaisesRegex(backup.BackupError, "corrupto"):
            backup.verify_archive(archive)

    def test_archive_recheck_rejects_unmanifested_data(self):
        report = backup.backup(self.source, self.target, "critical", "123456",
                               "USB\\VID_054C&PID_02D2\\FAKE",
                               verify_identity=lambda: True, reserve_bytes=0)
        archive = Path(report["directory"])
        (archive / "data/UNKNOWN.TXT").write_text("extra")
        with self.assertRaisesRegex(backup.BackupError, "no registrados"):
            backup.verify_archive(archive)

    def test_verify_latest_ignores_recovery_config_folder(self):
        report = backup.backup(self.source, self.target, "critical", "123456",
                               "USB\\VID_054C&PID_02D2\\FAKE",
                               verify_identity=lambda: True, reserve_bytes=0)
        (self.target / "recovery-configs").mkdir()
        with patch.object(backup, "DEFAULT_DEST", self.target), \
             patch("sys.argv", ["tools.psp.psp_backup.py", "--verify-latest"]):
            self.assertEqual(backup.main(), 0)

    def test_usb_identity_changed_during_copy_is_incomplete(self):
        with self.assertRaisesRegex(backup.BackupError, "identidad USB"):
            backup.backup(self.source, self.target, "critical", "123456",
                          "USB\\VID_054C&PID_02D2\\FAKE",
                          verify_identity=lambda: False, reserve_bytes=0)
        snapshot = next(self.target.iterdir())
        self.assertTrue((snapshot / "BACKUP-INCOMPLETE.txt").exists())
        self.assertFalse((snapshot / "manifest.json").exists())
        with self.assertRaisesRegex(backup.BackupError, "no está marcado"):
            backup.verify_archive(snapshot)

    def test_copy_hash_mismatch_leaves_incomplete(self):
        original = backup.copy_verified

        def changed(source, dest, size):
            record = original(source, dest, size)
            if source.name == "DATA.BIN":
                raise backup.BackupError("simulación de archivo alterado")
            return record

        with patch.object(backup, "copy_verified", side_effect=changed):
            with self.assertRaises(backup.BackupError):
                backup.backup(self.source, self.target, "critical", "123456",
                              "USB\\VID_054C&PID_02D2\\FAKE",
                              reserve_bytes=0)
        snap = next(self.target.iterdir())
        self.assertTrue((snap / "BACKUP-INCOMPLETE.txt").exists())
        self.assertFalse((snap / "manifest.json").exists())
        self.assertEqual((self.source / "PSP/SAVEDATA/GAME01/DATA.BIN").read_bytes(),
                         self.contents["PSP/SAVEDATA/GAME01/DATA.BIN"])

    def test_reject_backup_inside_psp(self):
        with self.assertRaisesRegex(backup.BackupError, "no puede estar"):
            backup.backup(self.source, self.source / "PSP/DANGEROUS",
                          "critical", "123", "USB\\VID_054C&PID_02D2\\FAKE",
                          reserve_bytes=0)

    def test_reject_root_with_no_psp_layout(self):
        other = self.workspace / "some-random-usb-drive"
        other.mkdir()
        with self.assertRaisesRegex(backup.BackupError, "estructura PSP"):
            backup.selection(other, "critical")

    def test_reject_directory_symlink_or_reparse(self):
        link = self.source / "SEPLUGINS/CROSS_LINK"
        try:
            link.symlink_to(self.workspace, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Windows does not permit symlinks without developer privileges")
        with self.assertRaisesRegex(backup.BackupError, "reanalisis|no seguro"):
            backup.selection(self.source, "critical")

    def test_reject_invalid_scope_and_traversal(self):
        for scope in ("--all", ".", "UNKNOWN"):
            with self.assertRaises(backup.BackupError):
                backup.selection(self.source, scope)
        for path in ("", "../secret", "a/../b", ".", ".."):
            with self.assertRaises(backup.BackupError):
                backup._safe_relative(path)


if __name__ == "__main__":
    unittest.main()
