"""PC-only simulation checks. None of these tests touch a PSP or USB drive."""
import json
import unittest
from unittest.mock import patch

from psp_dock.core.dock_experience import ExperienceError, Stage
from tools.labs.experience_simulator import (
    MOCK_HID_SESSION, MOCK_SERIAL, MOCK_SONY_USB, SYNTHETIC_ONLY,
    MockIdentity, MockInventory, MockPSPDockSession, run_mock_journey,
)


def ready(choice="copy", inventory=None):
    sim = MockPSPDockSession(inventory)
    sim.attach_storage()
    sim.audit_inventory()
    if sim.state.stage == Stage.DECIDE_GAMES:
        sim.choose_games(choice)
        if choice == "copy":
            sim.confirm_game_copies(verified=True)
    for save_id in sim.inventory.conflicting_saves:
        sim.resolve_save_conflict(save_id, "psp")
    sim.review_saves(approve_sync=True)
    if sim.state.stage == Stage.SYNCING_SAVES:
        sim.confirm_initial_save_copies(verified=True)
    assert sim.state.stage == Stage.STORAGE_READY
    return sim


def xmb():
    sim = ready()
    sim.request_dock(synthetic_role_bridge_present=True,
                     synthetic_bridge_live_ack=True)
    sim.lock_host_volume(exclusive=True, flushed=True)
    sim.firmware_prepare_ack(live_ack=True, fat_safe=True, host_still_locked=True)
    sim.stop_mock_storage(firmware_ack=True, host_still_locked=True)
    sim.attach_mock_hid(descriptor_verified=True, same_usb=True,
                        storage_unmounted=True, firmware_ack=True)
    assert sim.state.stage == Stage.XMB
    return sim


def sync_back():
    sim = xmb()
    sim.launch_game("DARIUSBURST")
    sim.close_game(ppsspp_closed=True, save_files_idle=True)
    sim.exit_dock(same_hid_session=True, ppsspp_closed=True)
    sim.record_single_return_request(recorded=True)
    sim.original_storage_returned()
    assert sim.state.stage == Stage.SYNC_BACK
    return sim


class PSPDockExperienceSimulatorTests(unittest.TestCase):
    def assert_rejected_atomically(self, sim, operation, pattern=None):
        original = sim.checkpoint()
        if pattern:
            with self.assertRaisesRegex(ExperienceError, pattern):
                operation()
        else:
            with self.assertRaises(ExperienceError):
                operation()
        self.assertEqual(sim.checkpoint(), original)

    def test_full_copy_journey_multiple_games_and_safe_unplug(self):
        sim = run_mock_journey("copy")
        actions = [step.action for step in sim.steps]
        stages = [step.after for step in sim.steps]
        self.assertEqual(sim.state.stage, Stage.WAITING)
        self.assertEqual(sim.state.revision, len(actions))
        self.assertEqual(actions.count("launch_game"), 2)
        self.assertEqual(actions.count("game_closed"), 2)
        self.assertEqual(stages.count("xmb"), 3)
        self.assertLess(actions.index("copy_complete"), actions.index("start_dock"))
        self.assertLess(actions.index("save_sync_complete"), actions.index("start_dock"))
        self.assertLess(actions.index("return_sync_verified"), actions.index("cable_removed"))
        self.assertEqual(sim.mock_verified_game_copies, {"LUMINES", "PATAPON"})
        self.assertFalse(sim.pending_pc_saves)
        self.assertTrue(sim.initial_saves_synced)
        self.assertIn("lock_host_volume_no_eject",
                      next(step.intents for step in sim.steps if step.action == "start_dock"))
        self.assertIn("request_hid_return_once",
                      next(step.intents for step in sim.steps if step.action == "exit_dock"))
        self.assertTrue(all(step.evidence == SYNTHETIC_ONLY for step in sim.steps))
        self.assertEqual(sim.checkpoint()["mode"], SYNTHETIC_ONLY)

    def test_skip_branch_never_mocks_missing_copies_and_plays_only_local(self):
        sim = run_mock_journey("skip")
        self.assertEqual(sim.mock_verified_game_copies, set())
        self.assertNotIn("copy_complete", [step.action for step in sim.steps])
        choices = [step for step in sim.steps if step.action == "user_copy_choice"]
        self.assertEqual(choices[0].after, Stage.REVIEW_SAVES.value)
        self.assertEqual(sim.checkpoint()["mode"], SYNTHETIC_ONLY)
        stage = ready("skip")
        self.assertEqual(stage.state.missing_games, 2)
        stage.request_dock(synthetic_role_bridge_present=True,
                           synthetic_bridge_live_ack=True)
        stage.lock_host_volume(exclusive=True, flushed=True)
        stage.firmware_prepare_ack(live_ack=True, fat_safe=True,
                                   host_still_locked=True)
        stage.stop_mock_storage(firmware_ack=True, host_still_locked=True)
        stage.attach_mock_hid(descriptor_verified=True, same_usb=True,
                              storage_unmounted=True, firmware_ack=True)
        self.assert_rejected_atomically(
            stage, lambda: stage.launch_game("PATAPON"), "disponible",
        )
        self.assertEqual(stage.launch_game("DARIUSBURST"),
                         ("launch_isolated_ppsspp",))

    def test_no_missing_games_skip_transfer_prompt(self):
        inv = MockInventory(local_games=("GAME_A",), missing_games=(),
                            save_updates=(), conflicting_saves=())
        sim = MockPSPDockSession(inv)
        sim.attach_storage()
        self.assertEqual(sim.audit_inventory(), ("show_save_review",))
        self.assertEqual(sim.state.stage, Stage.REVIEW_SAVES)
        self.assertEqual(sim.review_saves(approve_sync=False),
                         ("show_storage_ready",))
        self.assertEqual(sim.state.stage, Stage.STORAGE_READY)
        self.assertNotIn("sync_verified_saves_with_backups",
                         [n for step in sim.steps for n in step.intents])

    def test_identity_verification_and_single_volume_fail_closed(self):
        invalid_usb = MockIdentity(MOCK_SERIAL, "USB\\VID_1234&PID_0001\\BAD")
        cases = (
            (invalid_usb, True, True),
            (MockIdentity(), False, True),
            (MockIdentity(), True, False),
            (MockIdentity("NaN", MOCK_SONY_USB), True, True),
        )
        for identity, sony, single in cases:
            with self.subTest(identity=identity, sony=sony, single=single):
                sim = MockPSPDockSession()
                self.assert_rejected_atomically(
                    sim, lambda: sim.attach_storage(identity, sony_verified=sony,
                                                     single_volume=single),
                )
                self.assertEqual(sim.state.stage, Stage.WAITING)
                self.assertIsNone(sim.bound_identity)

    def test_copy_requires_same_inventory_hash_verified_and_no_journals(self):
        sim = MockPSPDockSession()
        sim.attach_storage()
        sim.audit_inventory()
        self.assert_rejected_atomically(
            sim, lambda: sim.choose_games("copy", same_inventory=False), "inventario",
        )
        sim.choose_games("copy")
        for kwargs in (
            {"verified": False},
            {"verified": True, "same_identity": False},
            {"verified": True, "no_journals": False},
        ):
            self.assert_rejected_atomically(
                sim, lambda kw=kwargs: sim.confirm_game_copies(**kw),
            )
        self.assertNotIn("LUMINES", sim.local_games)
        sim.confirm_game_copies(verified=True)
        self.assertIn("LUMINES", sim.local_games)
        self.assertEqual(sim.state.stage, Stage.REVIEW_SAVES)

    def test_conflicts_must_be_resolved_and_saves_require_approval(self):
        sim = MockPSPDockSession()
        sim.attach_storage()
        sim.audit_inventory()
        sim.choose_games("skip")
        self.assert_rejected_atomically(
            sim, lambda: sim.review_saves(approve_sync=True), "conflictos",
        )
        self.assert_rejected_atomically(
            sim, lambda: sim.resolve_save_conflict("SAVE_OTHER", "pc"), "desconocido",
        )
        self.assert_rejected_atomically(
            sim, lambda: sim.resolve_save_conflict("SAVE_RIDGE", "skip"), "PSP o PC",
        )
        sim.resolve_save_conflict("SAVE_RIDGE", "pc")
        self.assert_rejected_atomically(
            sim, lambda: sim.review_saves(approve_sync=False), "autorización",
        )
        sim.review_saves(approve_sync=True)
        self.assertEqual(sim.state.stage, Stage.SYNCING_SAVES)
        for kwargs in ({"verified": False}, {"verified": True, "no_journals": False},
                       {"verified": True, "no_conflicts": False},
                       {"verified": True, "same_identity": False}):
            self.assert_rejected_atomically(
                sim, lambda kw=kwargs: sim.confirm_initial_save_copies(**kw),
            )
        sim.confirm_initial_save_copies(verified=True)
        self.assertEqual(sim.state.stage, Stage.STORAGE_READY)
        self.assertEqual(sim.state.save_actions, 0)

    def test_no_real_bridge_ack_is_inferred_from_mock_usb_or_marker(self):
        sim = ready()
        for present, ack in ((False, False), (True, False), (False, True)):
            with self.subTest(present=present, ack=ack):
                self.assert_rejected_atomically(
                    sim, lambda: sim.request_dock(
                        synthetic_role_bridge_present=present,
                        synthetic_bridge_live_ack=ack,
                    ), "bridge kernel REAL",
                )
        self.assertEqual(sim.state.stage, Stage.STORAGE_READY)
        self.assertFalse(any(step.action == "host_lock_acquired" for step in sim.steps))
        sim.request_dock(synthetic_role_bridge_present=True,
                         synthetic_bridge_live_ack=True)
        self.assertEqual(sim.state.stage, Stage.HOST_LOCKING)

    def test_lock_flush_firmware_ack_fat_and_storage_stop_required(self):
        sim = ready()
        sim.request_dock(synthetic_role_bridge_present=True,
                         synthetic_bridge_live_ack=True)
        for exclusive, flushed in ((False, True), (True, False)):
            self.assert_rejected_atomically(
                sim, lambda: sim.lock_host_volume(exclusive=exclusive,
                                                  flushed=flushed),
            )
        sim.lock_host_volume(exclusive=True, flushed=True)
        for live, fat, locked in ((False, True, True), (True, False, True),
                                  (True, True, False)):
            self.assert_rejected_atomically(
                sim, lambda: sim.firmware_prepare_ack(live_ack=live,
                                                      fat_safe=fat,
                                                      host_still_locked=locked),
            )
        sim.firmware_prepare_ack(live_ack=True, fat_safe=True,
                                 host_still_locked=True)
        self.assert_rejected_atomically(
            sim, lambda: sim.stop_mock_storage(firmware_ack=False,
                                               host_still_locked=True),
        )
        self.assert_rejected_atomically(
            sim, lambda: sim.stop_mock_storage(firmware_ack=True,
                                               host_still_locked=False),
        )
        sim.stop_mock_storage(firmware_ack=True, host_still_locked=True)
        self.assertEqual(sim.state.stage, Stage.WAIT_HID)

    def test_hid_requires_same_usb_absent_storage_and_live_firmware_ack(self):
        sim = ready()
        sim.request_dock(synthetic_role_bridge_present=True,
                         synthetic_bridge_live_ack=True)
        sim.lock_host_volume(exclusive=True, flushed=True)
        sim.firmware_prepare_ack(live_ack=True, fat_safe=True,
                                 host_still_locked=True)
        sim.stop_mock_storage(firmware_ack=True, host_still_locked=True)
        good = dict(descriptor_verified=True, same_usb=True,
                    storage_unmounted=True, firmware_ack=True)
        for field in good:
            with self.subTest(field=field):
                flags = dict(good)
                flags[field] = False
                self.assert_rejected_atomically(
                    sim, lambda flags=flags: sim.attach_mock_hid(**flags),
                )
        self.assert_rejected_atomically(
            sim, lambda: sim.attach_mock_hid(session="0" * 32, **good),
        )
        self.assertEqual(sim.attach_mock_hid(**good), ("show_controller_xmb",))
        self.assertEqual(sim.state.hid_session, MOCK_HID_SESSION)

    def test_two_games_return_to_xmb_and_exit_is_explicit(self):
        sim = xmb()
        for game in ("DARIUSBURST", "PATAPON"):
            self.assertEqual(sim.launch_game(game), ("launch_isolated_ppsspp",))
            self.assertEqual(sim.state.stage, Stage.GAME)
            self.assert_rejected_atomically(
                sim, lambda: sim.exit_dock(same_hid_session=True,
                                           ppsspp_closed=True),
            )
            self.assert_rejected_atomically(
                sim, lambda: sim.close_game(ppsspp_closed=False,
                                            save_files_idle=True),
            )
            self.assert_rejected_atomically(
                sim, lambda: sim.close_game(ppsspp_closed=True,
                                            save_files_idle=False),
            )
            sim.close_game(ppsspp_closed=True, save_files_idle=True)
            self.assertEqual(sim.state.stage, Stage.XMB)
        self.assert_rejected_atomically(
            sim, lambda: sim.exit_dock(same_hid_session=False,
                                       ppsspp_closed=True),
        )
        sim.exit_dock(same_hid_session=True, ppsspp_closed=True)
        self.assertEqual(sim.state.stage, Stage.RETURNING)
        self.assertTrue(sim.pending_pc_saves)

    def test_return_requires_single_command_original_volume_and_backup(self):
        sim = xmb()
        sim.exit_dock(same_hid_session=True, ppsspp_closed=True)
        self.assert_rejected_atomically(
            sim, lambda: sim.record_single_return_request(recorded=False),
        )
        sim.record_single_return_request(recorded=True)
        self.assert_rejected_atomically(
            sim, lambda: sim.record_single_return_request(recorded=True),
        )
        for identity in (
            MockIdentity("999999", MOCK_SONY_USB),
            MockIdentity(MOCK_SERIAL, "USB\\VID_054C&PID_02D2\\OTHER_PSP"),
        ):
            self.assert_rejected_atomically(
                sim, lambda identity=identity: sim.original_storage_returned(identity),
                "Otra PSP",
            )
        self.assert_rejected_atomically(
            sim, lambda: sim.original_storage_returned(single_volume=False),
        )
        sim.original_storage_returned()
        self.assertEqual(sim.state.stage, Stage.SYNC_BACK)
        self.assert_rejected_atomically(
            sim, lambda: sim.confirm_return_save_copies(verified_backups=True),
            "aprobación",
        )
        sim.approve_return_save_sync()
        for kwargs in (
            {"verified_backups": False},
            {"verified_backups": True, "no_conflicts": False},
            {"verified_backups": True, "no_journals": False},
            {"verified_backups": True, "same_identity": False},
        ):
            self.assert_rejected_atomically(
                sim, lambda kw=kwargs: sim.confirm_return_save_copies(**kw),
            )
        sim.confirm_return_save_copies(verified_backups=True)
        self.assertEqual(sim.state.stage, Stage.SAFE_TO_UNPLUG)
        self.assert_rejected_atomically(
            sim, lambda: sim.unplug(host_storage_released=False),
        )
        sim.unplug(host_storage_released=True)
        self.assertEqual(sim.state.stage, Stage.WAITING)

    def test_no_automatic_return_sync_after_storage_reappears(self):
        sim = sync_back()
        self.assertTrue(sim.pending_pc_saves)
        self.assertFalse(sim.return_sync_approved)
        self.assertEqual(sim.steps[-1].intents, ("inspect_and_sync_return_saves",))
        self.assertNotIn("return_sync_verified", [step.action for step in sim.steps])
        self.assert_rejected_atomically(
            sim, lambda: sim.confirm_return_save_copies(verified_backups=True),
        )
        self.assert_rejected_atomically(
            sim, lambda: sim.unplug(host_storage_released=True),
        )

    def test_return_sync_consent_cannot_be_reused_on_next_attachment(self):
        sim = run_mock_journey("copy")
        self.assertTrue(sim.return_sync_approved)
        sim.attach_storage()
        self.assertFalse(sim.return_sync_approved)
        self.assertFalse(sim.initial_sync_approved)
        sim.audit_inventory()
        sim.review_saves(approve_sync=False)
        sim.request_dock(synthetic_role_bridge_present=True,
                         synthetic_bridge_live_ack=True)
        sim.lock_host_volume(exclusive=True, flushed=True)
        sim.firmware_prepare_ack(live_ack=True, fat_safe=True,
                                 host_still_locked=True)
        sim.stop_mock_storage(firmware_ack=True, host_still_locked=True)
        sim.attach_mock_hid(descriptor_verified=True, same_usb=True,
                            storage_unmounted=True, firmware_ack=True)
        sim.exit_dock(same_hid_session=True, ppsspp_closed=True)
        sim.record_single_return_request(recorded=True)
        sim.original_storage_returned()
        self.assert_rejected_atomically(
            sim, lambda: sim.confirm_return_save_copies(verified_backups=True),
            "aprobación",
        )

    def test_disconnection_in_unsafe_stages_halts_without_implicit_recovery(self):
        for label, factory in (
            ("copy", lambda: self._copying()),
            ("hid", xmb),
            ("game", lambda: self._playing()),
            ("return", sync_back),
        ):
            with self.subTest(label=label):
                sim = factory()
                sim.interrupt("Synthetic lost cable")
                self.assertEqual(sim.state.stage, Stage.HALTED)
                self.assertEqual(sim.steps[-1].intents, ())
                self.assert_rejected_atomically(
                    sim, lambda: sim.audit_inventory(), "suspendida",
                )
                self.assert_rejected_atomically(
                    sim, lambda: sim.unplug(host_storage_released=True), "suspendida",
                )

    @staticmethod
    def _copying():
        sim = MockPSPDockSession()
        sim.attach_storage()
        sim.audit_inventory()
        sim.choose_games("copy")
        return sim

    @staticmethod
    def _playing():
        sim = xmb()
        sim.launch_game("DARIUSBURST")
        return sim

    def test_checkpoint_json_roundtrip_halted_recovery_and_manual_reaudit(self):
        sim = self._playing()
        self.assertTrue(sim.pending_pc_saves)
        sim.interrupt("Mock disconnect during gameplay")
        checkpoint = json.loads(json.dumps(sim.checkpoint()))
        restored = MockPSPDockSession.from_checkpoint(checkpoint)
        self.assertEqual(restored.checkpoint(), checkpoint)
        self.assertEqual(restored.state.stage, Stage.HALTED)
        self.assert_rejected_atomically(
            restored, lambda: restored.launch_game("PATAPON"), "suspendida",
        )
        wrong_card = MockIdentity("222222", MOCK_SONY_USB)
        self.assert_rejected_atomically(
            restored, lambda: restored.restart_after_interruption(wrong_card),
        )
        restored.restart_after_interruption(MockIdentity())
        self.assertEqual(restored.state.stage, Stage.AUDITING)
        self.assertTrue(restored.pending_pc_saves)
        self.assertFalse(restored.return_sync_approved)
        self.assertEqual(restored.audit_inventory(), ("show_save_review",))
        self.assertEqual(restored.state.save_actions, 0)
        restored.review_saves(approve_sync=False)
        self.assertEqual(restored.state.stage, Stage.STORAGE_READY)
        self.assertTrue(restored.pending_pc_saves)
        self.assertNotIn("return_sync_verified", [step.action for step in restored.steps])

    def test_snapshots_reject_fabricated_status_and_unknown_fields(self):
        sim = xmb()
        checkpoint = sim.checkpoint()
        self.assertNotIn("role_bridge_proof", json.dumps(checkpoint))
        self.assertNotIn("live_psp_role_bridge", json.dumps(checkpoint))
        changes = (
            {"mode": "LIVE_HARDWARE_PROOF"},
            {"extra": "unsafe"},
            {"state": {**checkpoint["state"], "stage": "broken"}},
            {"local_games": ["INJECTED_GAME"]},
            {"steps": [{**checkpoint["steps"][0], "evidence": "REAL_HARDWARE"}]},
        )
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ExperienceError):
                MockPSPDockSession.from_checkpoint({**checkpoint, **change})

    def test_fixed_traces_are_deterministic_and_never_execute_hardware_io(self):
        with (
            patch("builtins.open", side_effect=AssertionError("disk I/O")),
            patch("subprocess.Popen", side_effect=AssertionError("process")),
            patch("subprocess.run", side_effect=AssertionError("process")),
            patch("os.system", side_effect=AssertionError("shell")),
            patch("socket.socket", side_effect=AssertionError("network")),
        ):
            first = run_mock_journey("copy").checkpoint()
            second = run_mock_journey("copy").checkpoint()
            skipped = run_mock_journey("skip").checkpoint()
        self.assertEqual(first, second)
        self.assertNotEqual(first, skipped)
        self.assertEqual(first["mode"], SYNTHETIC_ONLY)
        self.assertEqual(first["state"]["stage"], Stage.WAITING.value)


if __name__ == "__main__":
    unittest.main()
