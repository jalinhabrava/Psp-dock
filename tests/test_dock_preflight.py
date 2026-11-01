from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from dock_preflight import assess_storage
from dock import Dock, volume_identity


def status(*, connected=True, demo=False, games=None, actions=0, conflicts=0,
           running=False, returning=False, recovering=False, prepared=False,
           hid=False, exiting=False):
    games = games if games is not None else [{"availability": "ready"}]
    return assess_storage(
        connected=connected, demo=demo, games=games,
        saves={"actions": [{"slot": str(i)} for i in range(actions)],
               "conflicts": [{"slot": str(i)} for i in range(conflicts)]},
        session={"running": running, "pending_return": returning,
                 "exit_requested": exiting,
                 "prepared_volume": "12345" if prepared else ""},
        recovering=recovering,
        current_volume_serial="12345" if connected or prepared else "",
        hid_connected=hid,
    )


class PreflightTests(unittest.TestCase):
    def test_connected_and_disconnected_states_never_authorize_hid(self):
        cases = (
            ({"prepared": True}, "storage_ready", "prepare_hid"),
            ({}, "save_prepare_offer", "approve_save_sync"),
            ({"connected": False, "games": []}, "no_device", "connect_psp"),
            ({"connected": False}, "offline_library", "connect_psp"),
            ({"demo": True}, "demo", "inspect_demo"),
            ({"recovering": True}, "recovery", "recover_session"),
            ({"running": True}, "playing", "wait_game_close"),
            ({"returning": True}, "return_review", "sync_return"),
            ({"conflicts": 1}, "save_conflicts", "resolve_save_conflicts"),
            ({"actions": 2}, "save_sync_offer", "approve_save_sync"),
            ({"games": [{"availability": "missing"}]}, "copy_offer", "choose_missing_games"),
            ({"games": [{"availability": "different"}]}, "version_review", "inspect_different_versions"),
            ({"games": []}, "no_ready_games", "choose_games"),
        )
        for options, phase, action in cases:
            with self.subTest(phase=phase):
                plan = status(**options)
                self.assertEqual(plan.phase, phase)
                self.assertEqual(plan.next_action, action)
                self.assertFalse(plan.can_start_hid)
                self.assertIsInstance(plan.snapshot(), dict)

    def test_game_choice_precedes_save_review_but_return_has_priority(self):
        games = [{"availability": "missing"}, {"availability": "ready"}]
        self.assertEqual(status(games=games, conflicts=2, actions=2).phase, "copy_offer")
        self.assertEqual(status(games=games, actions=2, returning=True).phase, "return_review")
        self.assertTrue(status(prepared=True).saves_prepared)
        # A prepared volume may have disappeared because the PSP is now HID;
        # that historical preparation never certifies the live controller.
        self.assertTrue(status(connected=False, prepared=True).saves_prepared)

    def test_multiple_games_without_storage_return_until_explicit_exit(self):
        ready = {"connected": False, "prepared": True, "hid": True}
        first = status(**ready)
        self.assertEqual(first.phase, "hid_xmb")
        after_game = status(**ready, returning=True)
        self.assertEqual(after_game.phase, "hid_xmb")
        self.assertEqual(after_game.next_action, "launch_or_exit")
        exit_requested = status(**ready, returning=True, exiting=True)
        self.assertEqual(exit_requested.phase, "return_wait_storage")
        self.assertEqual(status(connected=True, returning=True, exiting=True).phase,
                         "return_review")
        self.assertEqual(status(connected=False, returning=True).phase,
                         "return_wait_storage")

    def test_launch_does_not_implicitly_sync_saves(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            psp = home / "fake-psp"
            (psp / "PSP/SAVEDATA/USER").mkdir(parents=True)
            (psp / "PSP/SAVEDATA/USER/DATA.BIN").write_bytes(b"save")
            (psp / "ISO").mkdir()
            (psp / "ISO/game.iso").write_bytes(b"fixture")
            dock = Dock(home / "data")
            dock.settings({"psp_root": str(psp)})
            with patch.object(dock, "sync", side_effect=AssertionError("Implicit sync")):
                with self.assertRaisesRegex(ValueError, "autoriza"):
                    dock.launch(dock.games()[0]["id"])
            self.assertFalse((Path(dock.config["memstick"]) /
                              "PSP/SAVEDATA/USER").exists())

    def test_return_request_is_explicit_and_blocks_new_games_until_original_storage(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            psp = home / "psp"
            (psp / "PSP/SAVEDATA").mkdir(parents=True)
            (psp / "ISO").mkdir()
            (psp / "ISO/game.iso").write_bytes(b"fixture")
            dock = Dock(home / "data")
            dock.settings({"psp_root": str(psp)})
            game_id = dock.games()[0]["id"]
            dock.transfer([game_id])
            self.assertEqual(dock.games()[0]["availability"], "ready")
            dock.sync()  # Explicit initial PC-only fixture preparation.
            self.assertEqual(dock.session["prepared_volume"], volume_identity(psp))
            dock.session['pending_return'] = True  # Synthetic game closed.
            with patch.object(dock, "root", return_value=None), \
                 patch.object(dock, "prepared_runtime_path", return_value=home/"runtime/fixture.exe"), \
                 patch.object(dock, "controller_status",
                              return_value={"connected": True, "mode": "hid_manual",
                                            "reason": "synthetic controller"}):
                self.assertTrue(dock.state()["can_launch_game"])
                dock.request_exit()
                self.assertFalse(dock.state()["can_launch_game"])
                self.assertEqual(dock.state()["preflight"]["phase"], "return_wait_storage")
                with self.assertRaisesRegex(ValueError, "salida del Dock"):
                    dock.launch(game_id)
            self.assertEqual(dock.state()["preflight"]["phase"], "return_review")
            dock.sync()  # Synthetic return with no changes to copy.
            self.assertFalse(dock.session["pending_return"])
            self.assertFalse(dock.session["exit_requested"])

    def test_no_automatic_save_copy_in_watch_worker(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            psp = home / "fake-psp"
            (psp / "PSP/SAVEDATA/USER").mkdir(parents=True)
            (psp / "PSP/SAVEDATA/USER/DATA.BIN").write_bytes(b"important")
            dock = Dock(home / "dock-data")
            dock.settings({"psp_root": str(psp)})
            local = Path(dock.config["memstick"]) / "PSP/SAVEDATA/USER"
            self.assertFalse(local.exists())

            def finish(_seconds):
                dock.stop.set()

            # The worker must only create PC metadata; saves remain untouched.
            with patch.object(dock.stop, "wait", side_effect=finish), patch.object(
                dock, "sync", side_effect=AssertionError("Unexpected save copy")
            ):
                dock.watch()
            self.assertFalse(local.exists())
            self.assertEqual((psp / "PSP/SAVEDATA/USER/DATA.BIN").read_bytes(), b"important")
            self.assertEqual(dock.snapshot["preflight"]["phase"], "save_sync_offer")
            self.assertEqual(dock.snapshot["preflight"]["save_actions"], 1)

    def test_offline_manifest_mirrors_titles_without_mounting_psp(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            psp = home / "fake-psp"
            (psp / "ISO").mkdir(parents=True)
            (psp / "PSP").mkdir(parents=True)
            (psp / "ISO/Example.iso").write_bytes(b"fixture")
            dock = Dock(home / "data")
            dock.settings({"psp_root": str(psp)})
            connected = dock.state()
            self.assertEqual(len(connected["games"]), 1)
            self.assertEqual(connected["library_source"], "psp_live")
            cache = home / "data/inventory-cache.json"
            self.assertTrue(cache.is_file())
            self.assertEqual(json.loads(cache.read_text())["games"][0]["relative"],
                             "ISO/Example.iso")
            dock.settings({"psp_root": ""})
            with patch("dock.windows_devices", return_value=[]):
                from_disk = Dock(home / "data")
                offline = from_disk.state()
            self.assertEqual(len(offline["games"]), 1)
            self.assertEqual(offline["library_source"], "cached_manifest")
            self.assertEqual(offline["preflight"]["phase"], "offline_library")
            self.assertFalse(offline["preflight"]["can_start_hid"])

    def test_preflight_ui_never_claims_hid_or_sync_writes(self):
        panel = Path(__file__).parents[1] / "web/app.js"
        source = panel.read_text(encoding="utf-8")
        self.assertIn("renderPreflight()", source)
        self.assertIn("preflight-copy", source)
        self.assertIn("preflight-skip", source)
        self.assertIn("preflight-saves", source)
        self.assertIn("no se ha copiado nada", source)


if __name__ == "__main__":
    unittest.main()
