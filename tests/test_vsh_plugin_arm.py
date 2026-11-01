import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import vsh_plugin_stage as stage
from vsh_plugin_arm import arm_observer
from vsh_plugin_status import inspect_volume


class VshArmTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "PSP/GAME").mkdir(parents=True)
        (self.root / "PSP/SAVEDATA/USER").mkdir(parents=True)
        (self.root / "PSP/SAVEDATA/USER/DATA.BIN").write_bytes(b"saves forever")
        self.sources = {}
        for name in stage.FILES:
            src = self.root / ("synthetic_" + name)
            src.write_bytes(("fixture " + name).encode())
            self.sources[name] = (src, hashlib.sha256(src.read_bytes()).hexdigest())
        self.patched = patch.dict(stage.FILES,
                                  {name: ("", sha) for name, (_, sha)
                                   in self.sources.items()}, clear=True)
        self.patched.__enter__()
        stage.stage_disabled_plugin(self.root, self.sources, self.root / "PC-backups")

    def tearDown(self):
        self.patched.__exit__(None, None, None)
        self.tmp.cleanup()

    def test_arm_only_with_disabled_plugin_and_matching_binaries(self):
        file = arm_observer(self.root)
        self.assertEqual(file.read_bytes(), stage.ARM_TOKEN)
        self.assertTrue(inspect_volume(self.root)["armed"])
        self.assertEqual((self.root / stage.CONFIG).read_bytes(), stage.ENTRY)
        self.assertEqual((self.root / "PSP/SAVEDATA/USER/DATA.BIN").read_bytes(),
                         b"saves forever")
        with self.assertRaisesRegex(ValueError, "ya está armado"):
            arm_observer(self.root)

    def test_refuses_if_plugin_enabled(self):
        (self.root / stage.CONFIG).write_bytes(stage.ENTRY.replace(b", off", b", on"))
        with self.assertRaisesRegex(ValueError, "en OFF"):
            arm_observer(self.root)
        self.assertFalse((self.root / stage.STAGE_DIR / "ARM.TXT").exists())

    def test_refuses_if_old_boot_marker_present(self):
        (self.root / stage.STAGE_DIR / "BOOT.TXT").write_bytes(b"stale")
        with self.assertRaisesRegex(ValueError, "sesión anterior"):
            arm_observer(self.root)
        self.assertFalse((self.root / stage.STAGE_DIR / "ARM.TXT").exists())

    def test_refuses_tampered_binaries(self):
        (self.root / stage.STAGE_DIR / "usbstate.prx").write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "no coincide"):
            arm_observer(self.root)
        self.assertFalse((self.root / stage.STAGE_DIR / "ARM.TXT").exists())

    def test_marker_only_counts_if_exactly_expected(self):
        arm = self.root / stage.STAGE_DIR / "ARM.TXT"
        arm.write_bytes(b"mismatched")
        self.assertFalse(inspect_volume(self.root)["armed"])


if __name__ == "__main__":
    unittest.main()
