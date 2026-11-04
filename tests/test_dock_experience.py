import unittest
from dataclasses import replace

from psp_dock.core.dock_experience import Experience, ExperienceError, Stage
from psp_dock.devices.usb_handoff import RoleBridgeProof, ROLE_CAPABILITIES


SERIAL = "2958455453"
USB = "USB\\VID_054C&PID_02D2\\8A67FB08B1B4B5A6"
HASH = "a" * 64
HID = "d3cfc42418a65b0c043a96b773dde7c9"


def proof():
    """Synthetic fixture, never an assertion of real kernel capability."""
    return RoleBridgeProof(
        source="live_psp_role_bridge", volume_serial=SERIAL, usb_parent=USB,
        protocol_version=1, capabilities=ROLE_CAPABILITIES,
        hardware_roundtrip_verified=True, live_ack_verified=True,
    )


def attached():
    return Experience().event(
        "storage_arrived", volume_serial=SERIAL, usb_parent=USB,
        sony_psp_verified=True, single_volume=True,
    )[0]


def inventoried(missing=3, ready=2, saves=2, conflicts=0):
    return attached().event(
        "inventory_complete", inventory_tag=HASH, missing_games=missing,
        ready_games=ready, save_actions=saves, save_conflicts=conflicts,
    )[0]


def save_review(choice="skip", missing=3, ready=2, saves=2):
    state = inventoried(missing, ready, saves)
    if state.stage == Stage.DECIDE_GAMES:
        state, intent = state.event(
            "user_copy_choice", choice=choice, same_inventory=True,
        )
        if choice == "copy":
            state, intent = state.event(
                "copy_complete", copies_verified=True,
                same_identity=True, no_journals=True,
            )
    return state


def ready(choice="copy", missing=3, ready=2, saves=2):
    state = save_review(choice, missing, ready, saves)
    state, _ = state.event(
        "save_review_complete", same_identity=True, conflicts_left=0,
        user_approved_sync=True,
    )
    if state.stage == Stage.SYNCING_SAVES:
        state, _ = state.event(
            "save_sync_complete", copies_verified=True,
            no_conflicts=True, no_journals=True, same_identity=True,
        )
    return state


def xmb():
    state = ready()
    state, _ = state.event(
        "start_dock", role_bridge_proof=proof(), same_identity=True,
        no_pending_io=True, emulator_closed=True,
    )
    state, _ = state.event(
        "host_lock_acquired", exclusive_lock_verified=True,
        cached_writes_flushed=True,
    )
    state, _ = state.event(
        "psp_prepare_ack", live_session_ack=True, fat_ownership_safe=True,
        host_volume_still_locked=True,
    )
    state, _ = state.event(
        "storage_stopped", firmware_stop_ack=True,
        host_volume_still_locked=True,
    )
    state, _ = state.event(
        "hid_attached", session=HID, descriptor_verified=True,
        same_physical_usb=True, storage_not_mounted=True,
        live_firmware_ack=True,
    )
    return state


class DockExperienceTests(unittest.TestCase):
    def test_plug_in_and_prompt_before_hid_no_toggle_needed_for_yes_no(self):
        initial = Experience()
        state, intent = initial.event(
            "storage_arrived", volume_serial=SERIAL, usb_parent=USB,
            sony_psp_verified=True, single_volume=True,
        )
        self.assertEqual(state.stage, Stage.AUDITING)
        self.assertEqual(intent[0].name, "inventory_read_only")
        state, intent = state.event(
            "inventory_complete", inventory_tag=HASH,
            missing_games=3, ready_games=2,
            save_actions=1, save_conflicts=0,
        )
        self.assertEqual(state.stage, Stage.DECIDE_GAMES)
        self.assertEqual(intent[0].name, "show_game_copy_choice")
        state, intent = state.event(
            "user_copy_choice", choice="skip", same_inventory=True,
        )
        self.assertEqual(state.stage, Stage.REVIEW_SAVES)
        self.assertEqual(intent[0].name, "show_save_review")
        self.assertEqual(state.ready_games, 2)
        self.assertEqual(state.missing_games, 3)
        self.assertEqual(state.transfer_choice, "skip")

    def test_copy_branch_only_after_consent_and_verified_copy(self):
        original = inventoried(missing=2, ready=1, saves=0)
        with self.assertRaisesRegex(ExperienceError, "inventario"):
            original.event("user_copy_choice", choice="copy", same_inventory=False)
        state, intents = original.event(
            "user_copy_choice", choice="copy", same_inventory=True,
        )
        self.assertEqual(state.stage, Stage.COPYING_GAMES)
        self.assertEqual(intents[0].name, "verified_copy_missing_games")
        with self.assertRaises(ExperienceError):
            state.event("copy_complete", copies_verified=False,
                        same_identity=True, no_journals=True)
        with self.assertRaises(ExperienceError):
            state.event("copy_complete", copies_verified=True,
                        same_identity=True, no_journals=False)
        state, _ = state.event("copy_complete", copies_verified=True,
                               same_identity=True, no_journals=True)
        self.assertEqual(state.stage, Stage.REVIEW_SAVES)
        self.assertEqual(state.ready_games, 3)
        self.assertEqual(state.missing_games, 0)
        self.assertEqual(original.stage, Stage.DECIDE_GAMES)

    def test_saves_require_approval_and_fail_closed_on_conflicts(self):
        state = save_review(saves=2)
        for kwargs in (
            {"same_identity": False, "conflicts_left": 0, "user_approved_sync": True},
            {"same_identity": True, "conflicts_left": 1, "user_approved_sync": True},
            {"same_identity": True, "conflicts_left": 0, "user_approved_sync": False},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ExperienceError):
                state.event("save_review_complete", **kwargs)
        state, commands = state.event(
            "save_review_complete", same_identity=True, conflicts_left=0,
            user_approved_sync=True,
        )
        self.assertEqual(state.stage, Stage.SYNCING_SAVES)
        self.assertEqual(commands[0].name, "sync_verified_saves_with_backups")
        with self.assertRaises(ExperienceError):
            state.event("save_sync_complete", copies_verified=True,
                        no_conflicts=True, no_journals=False, same_identity=True)
        state, commands = state.event(
            "save_sync_complete", copies_verified=True, no_conflicts=True,
            no_journals=True, same_identity=True,
        )
        self.assertEqual(state.stage, Stage.STORAGE_READY)
        self.assertEqual(commands[0].name, "show_storage_ready")

    def test_no_copy_needed_can_reach_storage_ready_without_extra_steps(self):
        state = inventoried(missing=0, ready=5, saves=0)
        self.assertEqual(state.stage, Stage.REVIEW_SAVES)
        state, _ = state.event(
            "save_review_complete", same_identity=True,
            conflicts_left=0, user_approved_sync=False,
        )
        self.assertEqual(state.stage, Stage.STORAGE_READY)

    def test_bridge_blocked_until_real_hardware_proof(self):
        state = ready()
        for fake in (
            None, {"boot_marker": "PSPDOCK_K1_PROOF=01"},
            replace(proof(), source="BOOT.TXT"),
            replace(proof(), live_ack_verified=False),
            replace(proof(), hardware_roundtrip_verified=False),
            replace(proof(), volume_serial="999"),
            replace(proof(), capabilities=frozenset()),
        ):
            with self.subTest(fake=fake), self.assertRaisesRegex(
                ExperienceError, "bridge kernel"
            ):
                state.event(
                    "start_dock", role_bridge_proof=fake,
                    same_identity=True, no_pending_io=True,
                    emulator_closed=True,
                )
        self.assertEqual(state.stage, Stage.STORAGE_READY)

    def test_handoff_intents_and_live_acks_required(self):
        state = ready()
        state, commands = state.event(
            "start_dock", role_bridge_proof=proof(),
            same_identity=True, no_pending_io=True,
            emulator_closed=True,
        )
        self.assertEqual(state.stage, Stage.HOST_LOCKING)
        self.assertEqual(commands[0].name, "lock_host_volume_no_eject")
        with self.assertRaises(ExperienceError):
            state.event("host_lock_acquired", exclusive_lock_verified=True,
                        cached_writes_flushed=False)
        state, commands = state.event(
            "host_lock_acquired", exclusive_lock_verified=True,
            cached_writes_flushed=True,
        )
        self.assertEqual(commands[0].name, "request_firmware_prepare")
        with self.assertRaises(ExperienceError):
            state.event("psp_prepare_ack", live_session_ack=False,
                        fat_ownership_safe=True, host_volume_still_locked=True)
        with self.assertRaises(ExperienceError):
            state.event("psp_prepare_ack", live_session_ack=True,
                        fat_ownership_safe=True, host_volume_still_locked=False)
        state, commands = state.event(
            "psp_prepare_ack", live_session_ack=True,
            fat_ownership_safe=True, host_volume_still_locked=True,
        )
        self.assertEqual(commands[0].name, "request_psp_storage_stop_once")
        with self.assertRaises(ExperienceError):
            state.event("storage_stopped", firmware_stop_ack=False,
                        host_volume_still_locked=True)
        state, _ = state.event(
            "storage_stopped", firmware_stop_ack=True,
            host_volume_still_locked=True,
        )
        self.assertEqual(state.stage, Stage.WAIT_HID)
        for field in ("descriptor_verified", "same_physical_usb",
                      "storage_not_mounted", "live_firmware_ack"):
            conditions = {key: True for key in (
                "descriptor_verified", "same_physical_usb",
                "storage_not_mounted", "live_firmware_ack",
            )}
            conditions[field] = False
            with self.subTest(field=field), self.assertRaises(ExperienceError):
                state.event("hid_attached", session=HID, **conditions)
        state, cmds = state.event(
            "hid_attached", session=HID, descriptor_verified=True,
            same_physical_usb=True, storage_not_mounted=True,
            live_firmware_ack=True,
        )
        self.assertEqual(state.stage, Stage.XMB)
        self.assertEqual(cmds[0].name, "show_controller_xmb")

    def test_return_to_xmb_between_games_and_exit_explicitly(self):
        state = xmb()
        state, commands = state.event(
            "launch_game", game_id="DARIUSBURST",
            game_local_verified=True, same_hid_session=True,
        )
        self.assertEqual(state.stage, Stage.GAME)
        self.assertEqual(commands[0].name, "launch_isolated_ppsspp")
        with self.assertRaises(ExperienceError):
            state.event("exit_dock", same_hid_session=True,
                        ppsspp_closed=True)
        with self.assertRaises(ExperienceError):
            state.event("game_closed", ppsspp_closed=False,
                        save_files_idle=True)
        state, commands = state.event(
            "game_closed", ppsspp_closed=True, save_files_idle=True,
        )
        self.assertEqual(state.stage, Stage.XMB)
        self.assertEqual(commands[0].name, "show_controller_xmb")
        state, commands = state.event(
            "exit_dock", same_hid_session=True, ppsspp_closed=True,
        )
        self.assertEqual(state.stage, Stage.RETURNING)
        self.assertEqual(commands[0].name, "request_hid_return_once")

    def test_return_checks_original_identity_then_syncs_before_unplug(self):
        state = xmb().event(
            "exit_dock", same_hid_session=True, ppsspp_closed=True,
        )[0]
        state, _ = state.event("return_issued_once", attempt_recorded=True)
        self.assertEqual(state.stage, Stage.WAIT_STORAGE_RETURN)
        with self.assertRaises(ExperienceError):
            state.event(
                "original_storage_returned", volume_serial="other",
                usb_parent=USB, single_volume=True, ppsspp_closed=True,
            )
        state, commands = state.event(
            "original_storage_returned", volume_serial=SERIAL,
            usb_parent=USB, single_volume=True, ppsspp_closed=True,
        )
        self.assertEqual(state.stage, Stage.SYNC_BACK)
        self.assertEqual(commands[0].name, "inspect_and_sync_return_saves")
        with self.assertRaises(ExperienceError):
            state.event("return_sync_verified", verified_backups=True,
                        no_conflicts=False, no_journals=True,
                        same_identity=True)
        state, commands = state.event(
            "return_sync_verified", verified_backups=True,
            no_conflicts=True, no_journals=True,
            same_identity=True,
        )
        self.assertEqual(state.stage, Stage.SAFE_TO_UNPLUG)
        self.assertEqual(commands[0].name, "announce_safe_disconnect")
        state, cmds = state.event(
            "cable_removed", host_storage_released=True,
        )
        self.assertEqual(state.stage, Stage.WAITING)
        self.assertEqual(cmds, ())

    def test_no_ready_if_zero_copied_games(self):
        state = ready(choice="skip", missing=3, ready=0, saves=0)
        self.assertEqual(state.stage, Stage.STORAGE_READY)
        with self.assertRaisesRegex(ExperienceError, "Ningún juego"):
            state.event("start_dock", role_bridge_proof=proof(),
                        same_identity=True, no_pending_io=True,
                        emulator_closed=True)

    def test_snapshot_recovery_and_halted_behaviour(self):
        state = xmb()
        self.assertEqual(Experience.from_snapshot(state.snapshot()), state)
        suspended, intents = state.event("halt", reason="USB cable lost")
        self.assertEqual(suspended.stage, Stage.HALTED)
        self.assertEqual(intents, ())
        with self.assertRaisesRegex(ExperienceError, "suspendida"):
            suspended.event("launch_game", game_id="GAME")
        for bad in (
            {"stage": "nonsense"},
            {"volume_serial": ""},
            {"revision": -1},
            {"hid_session": "x" * 32},
            {"extra": True},
        ):
            with self.subTest(bad=bad), self.assertRaises(ExperienceError):
                Experience.from_snapshot({**state.snapshot(), **bad})


if __name__ == "__main__":
    unittest.main()
