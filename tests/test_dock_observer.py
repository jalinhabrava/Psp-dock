"""Passive connection watcher: no USB commands, no silent save writes."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from dock import Dock, volume_identity


class ObserverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.root = self.home / "psp-fixture"
        (self.root / "PSP/SAVEDATA/PLAYER").mkdir(parents=True)
        (self.root / "PSP/SAVEDATA/PLAYER/SAVE.BIN").write_bytes(b"BASE")
        (self.root / "ISO").mkdir()
        self.dock = Dock(self.home/"dock-data")
        self.dock.settings({"psp_root": str(self.root)})
        self.on_connect = MagicMock()
        self.dock.on_connected = self.on_connect

    def tearDown(self):
        self.temp.cleanup()

    def one_watch_cycle(self, state_return=None):
        stop = MagicMock()
        stop.is_set.side_effect = [False, True]
        with patch.object(self.dock, "stop", stop):
            with patch.object(self.dock, "state", return_value=(
                    {"mock": True} if state_return is None else state_return)):
                self.dock.watch()
        stop.wait.assert_called_once_with(3)

    def test_new_attachment_opens_browser_once_and_never_syncs(self):
        with patch.object(self.dock, "sync",
                          side_effect=AssertionError("No automatic copies")):
            self.one_watch_cycle()
            self.one_watch_cycle()
        self.on_connect.assert_called_once_with()
        self.assertEqual((self.root/"PSP/SAVEDATA/PLAYER/SAVE.BIN").read_bytes(),
                         b"BASE")

    def test_return_attachment_does_not_open_a_new_tab(self):
        self.dock.sync()  # Explicit initial preparation on fixture only.
        self.dock.session["pending_return"] = True
        self.one_watch_cycle()
        self.on_connect.assert_not_called()
        self.assertEqual(self.dock.session["prepared_volume"], volume_identity(self.root))

    def test_new_connection_forces_fresh_save_confirmation(self):
        self.dock.sync()
        self.assertTrue(self.dock.session.get("prepared_volume"))
        self.one_watch_cycle()  # Treat first observation as a new attachment.
        self.assertNotIn("prepared_volume", self.dock.session)
        self.dock.sync()  # User has to approve a new comparison.
        self.assertTrue(self.dock.session.get("prepared_volume"))
        self.one_watch_cycle()
        self.assertTrue(self.dock.session.get("prepared_volume"))
        # An external save edit must invalidate stale preparation, not copy.
        (self.root/"PSP/SAVEDATA/PLAYER/SAVE.BIN").write_bytes(b"CHANGED")
        self.one_watch_cycle()
        self.assertNotIn("prepared_volume", self.dock.session)
        self.assertEqual((self.root/"PSP/SAVEDATA/PLAYER/SAVE.BIN").read_bytes(),
                         b"CHANGED")

    def test_disconnection_then_new_attachment_opens_once_more(self):
        self.one_watch_cycle()
        with patch.object(self.dock, "root", return_value=None):
            self.one_watch_cycle()
        self.one_watch_cycle()
        self.assertEqual(self.on_connect.call_count, 2)

    def test_psp_first_hid_connection_opens_xmb_without_launching_game(self):
        self.dock.settings({"psp_root":""})
        frames = iter([
            {"controller_status":{"connected":False}},
            {"controller_status":{"connected":True}},
            {"controller_status":{"connected":True}},
            {"controller_status":{"connected":False}},
            {"controller_status":{"connected":True}},
        ])
        def one_frame():
            return {**next(frames),"selected_device":None,
                    "session":{"running":False},"scanning":False}
        with patch.object(self.dock,"root",return_value=None), \
             patch.object(self.dock,"launch",side_effect=AssertionError(
                 "A HID attach MUST NEVER launch PPSSPP")):
            for _ in range(5):
                self.one_watch_cycle(state_return=one_frame())
        self.assertEqual(self.on_connect.call_count,2,
                         "Only a new PDK1 HID rising edge opens the XMB")

    def test_no_xmb_autopen_after_user_requested_exit(self):
        self.dock.settings({"psp_root":""})
        self.dock.session["exit_requested"]=True
        with patch.object(self.dock,"root",return_value=None):
            self.one_watch_cycle(state_return={
                "controller_status":{"connected":True},
                "selected_device":None,"session":{"running":False},
                "scanning":False})
        self.on_connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
