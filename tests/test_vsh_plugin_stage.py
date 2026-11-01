import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import vsh_plugin_stage as vsh


class VshStageTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.root = Path(self.work.name)
        (self.root / "PSP/GAME").mkdir(parents=True)
        (self.root / "PSP/SAVEDATA/ORIGINAL").mkdir(parents=True)
        (self.root / "PSP/SAVEDATA/ORIGINAL/DATA.BIN").write_bytes(b"user save")
        (self.root / "SEPLUGINS").mkdir()
        self.backups = self.root / "PC_CONFIG_BACKUPS"
        self.sources = {}
        for name in vsh.FILES:
            file = self.root / ("synthetic_" + name)
            content = ("NO PSP EXECUTABLE " + name).encode()
            file.write_bytes(content)
            self.sources[name] = (file, hashlib.sha256(content).hexdigest())
        self.ctx = patch.dict(vsh.FILES,
                              {name: ("", checksum) for name, (_, checksum)
                               in self.sources.items()}, clear=True)
        self.ctx.__enter__()

    def tearDown(self):
        self.ctx.__exit__(None, None, None)
        self.work.cleanup()

    def test_plan_keeps_all_existing_plugin_entries_and_appends_off(self):
        before = b"vsh, ms0:/SEPLUGINS/other.prx, on\r\n"
        result = vsh.plan_config(before)
        self.assertTrue(result.startswith(before))
        self.assertTrue(result.endswith(
            b"vsh, ms0:/SEPLUGINS/PSPDockProbe/pspdockvsh.prx, off\r\n"))
        self.assertEqual(result.count(b"PSPDockProbe"), 1)

    def test_plan_handles_empty_and_missing_trailing_newline(self):
        self.assertEqual(vsh.plan_config(b""), vsh.ENTRY)
        self.assertEqual(vsh.plan_config(b"vsh, x.prx, on"),
                         b"vsh, x.prx, on\n" + vsh.ENTRY)

    def test_rejects_conflicting_ark_plugin_configuration(self):
        for bad in (
            b"vsh, ms0:/SEPLUGINS/PSPDockProbe/pspdockvsh.prx, on\n",
            b"vsh, ms0:/SEPLUGINS/PSPDockProbe/pspdockvsh.prx, off\n",
            b"vsh, ms0:/SEPLUGINS/PSPDockProbe/malicious.prx, off\n",
            b"NULL\x00BYTES",
            b"*" * (128 * 1024 + 1)
        ):
            with self.subTest(contents=bad[:30]), self.assertRaises(ValueError):
                vsh.plan_config(bad)

    def test_inspection_is_read_only(self):
        config = self.root / vsh.CONFIG
        config.write_bytes(b"vsh, ms0:/SEPLUGINS/other.prx, on\n")
        before = {p.relative_to(self.root): p.read_bytes()
                  for p in self.root.rglob("*") if p.is_file()}
        result = vsh.inspect_target(self.root, self.sources)
        self.assertEqual(result["before"], config.read_bytes())
        after = {p.relative_to(self.root): p.read_bytes()
                 for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertFalse((self.root / vsh.STAGE_DIR).exists())
        self.assertFalse(self.backups.exists())

    def test_staging_writes_only_disabled_entry_and_preserves_saves(self):
        config = self.root / vsh.CONFIG
        old = b"vsh, ms0:/SEPLUGINS/game.prx, on\r\n"
        config.write_bytes(old)
        result = vsh.stage_disabled_plugin(self.root, self.sources, self.backups)
        self.assertFalse(result["enabled"])
        self.assertFalse(result["armed"])
        self.assertEqual(config.read_bytes(), old + vsh.ENTRY.replace(b"\n", b"\r\n"))
        self.assertEqual((self.root / "PSP/SAVEDATA/ORIGINAL/DATA.BIN").read_bytes(),
                         b"user save")
        for name, (source, checksum) in self.sources.items():
            target = self.root / vsh.STAGE_DIR / name
            self.assertEqual(target.read_bytes(), source.read_bytes())
            self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), checksum)
        self.assertFalse((self.root / vsh.STAGE_DIR / "ARM.TXT").exists())
        self.assertFalse((self.root / vsh.STAGE_DIR / "BOOT.TXT").exists())
        backup = Path(result["backup"])
        self.assertTrue(backup.is_file())
        self.assertEqual(backup.read_bytes(), old)
        with self.assertRaisesRegex(ValueError, "Ya existe"):
            vsh.stage_disabled_plugin(self.root, self.sources, self.backups)

    def test_staging_new_config_does_not_require_backup(self):
        result = vsh.stage_disabled_plugin(self.root, self.sources, self.backups)
        self.assertIsNone(result["backup"])
        self.assertEqual((self.root / vsh.CONFIG).read_bytes(), vsh.ENTRY)

    def test_wrong_binary_hash_fails_before_any_psp_write(self):
        bad_sources = {**self.sources}
        file, checksum = bad_sources["usbstate.prx"]
        bad_sources["usbstate.prx"] = (file, "0" * 64)
        with self.assertRaisesRegex(ValueError, "Binario PSP VSH"):
            vsh.stage_disabled_plugin(self.root, bad_sources, self.backups)
        self.assertFalse((self.root / vsh.STAGE_DIR).exists())
        self.assertFalse((self.root / vsh.CONFIG).exists())
        self.assertFalse(self.backups.exists())

    def test_invalid_psp_root_refuses_to_create_directories(self):
        other = self.root / "NOT_PSP"
        other.mkdir()
        with self.assertRaisesRegex(ValueError, "Memory Stick"):
            vsh.inspect_target(other, self.sources)
        self.assertEqual(list(other.iterdir()), [])

    def test_no_rearm_if_stage_failed_after_backup(self):
        config = self.root / vsh.CONFIG
        config.write_bytes(b"vsh, a.prx, on\n")
        # Simulate a racing user modification right before config publication.
        original = vsh.os.rename

        def race(src, dst):
            original(src, dst)
            config.write_bytes(b"vsh, game.prx, off\n")

        with patch.object(vsh.os, "rename", side_effect=race):
            with self.assertRaisesRegex(ValueError, "cambiado durante"):
                vsh.stage_disabled_plugin(self.root, self.sources, self.backups)
        self.assertEqual(config.read_bytes(), b"vsh, game.prx, off\n")
        self.assertTrue(list(self.backups.glob("PLUGINS.TXT.before-pspdock-*.bak")))
        self.assertFalse((self.root / vsh.STAGE_DIR / "ARM.TXT").exists())


class VshBinaryContractTests(unittest.TestCase):
    base = Path(__file__).resolve().parents[1]

    def test_actual_manifest_hashes_match(self):
        for name, (path, checksum) in vsh.FILES.items():
            with self.subTest(name=name):
                self.assertEqual(hashlib.sha256((self.base / path).read_bytes()).hexdigest(),
                                 checksum)

    def test_disabled_config_and_original_helper_abi(self):
        self.assertEqual(vsh.ENTRY,
                         b"vsh, ms0:/SEPLUGINS/PSPDockProbe/pspdockvsh.prx, off\n")
        code = (self.base / "homebrew/vsh-observer/main.c").read_text()
        self.assertIn("GATE_V07_MAGIC", code)
        self.assertIn("usbstate.prx", code)
        self.assertIn("if (stopping || !read_arm_token())", code)
        self.assertNotIn("sceUsbStart(", code)
        self.assertNotIn("sceUsbActivate(", code)
        self.assertNotIn("flash0:", code)
        self.assertIn("USB_HELPER_PATH", code)
        self.assertIn("ARM.TXT", code)
        self.assertIn("BOOT.TXT", code)
        binary = (self.base / vsh.FILES["pspdockvsh.prx"][0]).read_bytes()
        self.assertNotIn(b"sceUsb", binary)
        self.assertNotIn(b"ModuleMgrForKernel", binary)
        self.assertIn(b"ModuleMgrForUser", binary)


if __name__ == "__main__":
    unittest.main()
