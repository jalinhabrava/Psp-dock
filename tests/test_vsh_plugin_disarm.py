from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.psp.vsh_plugin_disarm import plan_disarm, disarm, DisarmError
from tools.psp.vsh_plugin_stage import ARM_TOKEN, STAGE_DIR


class DisarmTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.folder = self.root / STAGE_DIR
        self.folder.mkdir(parents=True)
        self.saves = self.root / "PSP/SAVEDATA/EXAMPLE/USER.DAT"
        self.saves.parent.mkdir(parents=True)
        self.saves.write_bytes(b"valuable save")
        self.config = self.root / "SEPLUGINS/PLUGINS.TXT"
        self.config.write_bytes(b"vsh, another.prx, on\n")
        self.marker = self.folder / "ARM.TXT"
        self.boot = self.folder / "BOOT.TXT"
        self.boot.write_bytes(b"diagnostic evidence")

    def tearDown(self):
        self.temp.cleanup()

    def test_preview_has_no_effect_and_disarm_preserves_everything_else(self):
        self.marker.write_bytes(ARM_TOKEN)
        report = plan_disarm(self.root)
        self.assertTrue(report["armed"])
        self.assertTrue(self.marker.is_file())
        result = disarm(self.root)
        self.assertEqual(result, {"changed": True, "armed": False,
                                  "boot_marker_preserved": True})
        self.assertFalse(self.marker.exists())
        self.assertEqual(self.boot.read_bytes(), b"diagnostic evidence")
        self.assertEqual(self.config.read_bytes(), b"vsh, another.prx, on\n")
        self.assertEqual(self.saves.read_bytes(), b"valuable save")

    def test_already_disarmed_is_idempotent(self):
        self.assertFalse(plan_disarm(self.root)["armed"])
        self.assertFalse(disarm(self.root)["changed"])
        self.assertEqual(self.boot.read_bytes(), b"diagnostic evidence")

    def test_unknown_arm_token_is_never_deleted(self):
        for payload in (b"unknown token", ARM_TOKEN + b"extra", b""):
            self.marker.write_bytes(payload)
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(DisarmError, "token"):
                    plan_disarm(self.root)
                with self.assertRaises(DisarmError):
                    disarm(self.root)
                self.assertEqual(self.marker.read_bytes(), payload)

    def test_disarm_rechecks_marker_before_deletion(self):
        self.marker.write_bytes(ARM_TOKEN)
        original = plan_disarm
        call_count = 0

        def tamper(root):
            nonlocal call_count
            result = original(root)
            call_count += 1
            self.marker.write_bytes(b"changed concurrently")
            return result

        with patch("tools.psp.vsh_plugin_disarm.plan_disarm", side_effect=tamper):
            with self.assertRaisesRegex(DisarmError, "cambió"):
                disarm(self.root)
        self.assertEqual(call_count, 1)
        self.assertTrue(self.marker.is_file())
        self.assertEqual(self.marker.read_bytes(), b"changed concurrently")


if __name__ == "__main__":
    unittest.main()
