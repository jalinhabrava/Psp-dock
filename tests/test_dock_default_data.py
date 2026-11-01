"""Prevent an empty library appearing when the real one lives under Codex."""
from pathlib import Path
import tempfile
import unittest

from dock import default_data_path


class DefaultDataSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.local = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def live(self, relative):
        path = self.local / relative
        path.mkdir(parents=True)
        (path / "settings.json").write_text("{}")
        (path / "session.json").write_text("{}")
        return path

    def test_prefers_exactly_one_existing_codex_live_library(self):
        original = self.live(
            "Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/PSP-Dock/live"
        )
        self.assertEqual(default_data_path(self.local), original.resolve())
        self.assertFalse((self.local / "PSP-Dock/live").exists())

    def test_original_live_library_without_settings_file_is_preserved(self):
        original = self.local / (
            "Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/PSP-Dock/live"
        )
        (original/"library").mkdir(parents=True)
        (original/"session.json").write_text('{"running":false}')
        self.assertFalse((original/"settings.json").exists())
        self.assertEqual(default_data_path(self.local), original.resolve())

    def test_fresh_install_uses_normal_local_pc_default(self):
        self.assertEqual(default_data_path(self.local), self.local / "PSP-Dock/live")
        self.assertFalse((self.local / "PSP-Dock/live").exists())

    def test_ambiguous_live_libraries_block_instead_of_mixing(self):
        self.live("Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/PSP-Dock/live")
        self.live("PSP-Dock/live")
        with self.assertRaisesRegex(ValueError, "varias bibliotecas"):
            default_data_path(self.local)

    def test_demo_isolated_from_any_live_library(self):
        self.live("PSP-Dock/live")
        self.assertEqual(default_data_path(self.local, demo=True),
                         self.local / "PSP-Dock/demo")


if __name__ == "__main__":
    unittest.main()
