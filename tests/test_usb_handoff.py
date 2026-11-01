import unittest
from dataclasses import replace

from usb_handoff import (
    Command, Handoff, HandoffError, Phase,
    ROLE_CAPABILITIES, RoleBridgeProof,
)


SERIAL = "2958455453"
USB = "USB\\VID_054C&PID_02D2\\8A67FB08B1B4B5A6"
HID = "00112233445566778899aabbccddeeff"


def role_proof():
    """Synthetic FUTURE kernel bridge proof; NEVER from observer BOOT.TXT."""
    return RoleBridgeProof(
        source="live_psp_role_bridge", volume_serial=SERIAL, usb_parent=USB,
        protocol_version=1, capabilities=ROLE_CAPABILITIES,
        hardware_roundtrip_verified=True, live_ack_verified=True,
    )


def present(handoff=None):
    obj = handoff or Handoff()
    return obj.event("storage_present", volume_serial=SERIAL, usb_parent=USB,
                     mounted_single_volume=True)[0]


def prepared(handoff=None):
    state = present(handoff)
    return state.event("prepared", games_present=True, saves_verified=True,
                       no_save_conflicts=True, no_pending_copy=True)[0]


def eject(state=None):
    return (state or prepared()).event(
        "dock_requested", role_bridge_proof=role_proof(),
        no_pending_copy=True, emulator_closed=True,
        same_storage_identity=True)[0]


def storage_released(state=None):
    return (state or eject()).event(
        "host_eject_ok", os_confirmed=True, same_storage_identity=True)[0]


def hid_ready(state=None):
    return (state or storage_released()).event(
        "hid_present", session=HID, descriptor_verified=True,
        storage_absent=True, same_physical_usb=True)[0]


def playing(state=None):
    return (state or hid_ready()).event(
        "play_requested", game_id="GAME-ISO-1",
        game_copied_verified=True, same_hid_session=True)[0]


def returning(state=None):
    return (state or playing()).event(
        "emulator_exited", process_gone=True, local_saves_idle=True)[0]


def waiting_return(state=None):
    return (state or returning()).event(
        "hid_return_sent", transport_attempted_once=True)[0]


class UsbHandoffTest(unittest.TestCase):

    def test_full_flow_only_emits_intents_not_side_effects(self):
        state = Handoff()
        self.assertEqual(state.phase, Phase.WAITING_PSP)
        state, intent = state.event("storage_present", volume_serial=SERIAL,
                                    usb_parent=USB, mounted_single_volume=True)
        self.assertEqual(intent, (Command("inventory_and_plan_saves"),))
        state, intent = state.event("prepared", games_present=True,
                                    saves_verified=True, no_save_conflicts=True,
                                    no_pending_copy=True)
        self.assertEqual(intent[0].name, "show_dock_ready")
        state, intent = state.event("dock_requested", role_bridge_proof=role_proof(),
                                    no_pending_copy=True, emulator_closed=True,
                                    same_storage_identity=True)
        self.assertEqual(intent[0].name, "request_safe_eject")
        state, intent = state.event("host_eject_ok", os_confirmed=True,
                                    same_storage_identity=True)
        self.assertEqual(intent[0].name, "await_verified_hid")
        state, intent = state.event("hid_present", session=HID,
                                    descriptor_verified=True, storage_absent=True,
                                    same_physical_usb=True)
        self.assertEqual(intent[0].name, "show_xmb_library")
        state, intent = state.event("play_requested", game_id="GAME-ISO-1",
                                    game_copied_verified=True, same_hid_session=True)
        self.assertEqual(intent[0].name, "launch_ppsspp")
        state, intent = state.event("emulator_exited", process_gone=True,
                                    local_saves_idle=True)
        self.assertEqual(intent[0].name, "request_hid_return_to_storage")
        state, intent = state.event("hid_return_sent", transport_attempted_once=True)
        self.assertEqual(intent[0].name, "await_original_storage")
        state, intent = state.event("storage_returned", volume_serial=SERIAL,
                                    usb_parent=USB, mounted_single_volume=True,
                                    ppsspp_closed=True)
        self.assertEqual(intent[0].name, "verified_sync_with_backups")
        state, intent = state.event("sync_return_finished", copies_verified=True,
                                    no_conflicts=True, no_copy_journals=True)
        self.assertEqual(state.phase, Phase.SAFE_TO_UNPLUG)
        self.assertEqual(intent[0].name, "notify_safe_to_disconnect")
        state, intent = state.event("psp_disconnected", os_storage_released=True)
        self.assertEqual(state.phase, Phase.WAITING_PSP)
        self.assertEqual(intent, ())
        self.assertFalse(state.volume_serial)
        self.assertFalse(state.hid_session)

    def test_default_dock_request_blocked_until_firmware_verified(self):
        state = prepared()
        with self.assertRaisesRegex(HandoffError, "BOOT.TXT"):
            state.event("dock_requested", firmware_bridge_verified=True,
                        no_pending_copy=True, emulator_closed=True,
                        same_storage_identity=True)
        self.assertEqual(state.phase, Phase.PREPARED)

    def test_observer_marker_or_incomplete_role_proof_never_authorizes_eject(self):
        state = prepared()
        test_cases = [
            {"role_bridge_proof": None},
            {"role_bridge_proof": {"boot_marker": {
                "usb": 0x111, "bus": 1, "storage": 0x80243005}}},
            {"role_bridge_proof": replace(role_proof(), source="BOOT.TXT")},
            {"role_bridge_proof": replace(role_proof(), protocol_version=0)},
            {"role_bridge_proof": replace(role_proof(), volume_serial="999")},
            {"role_bridge_proof": replace(role_proof(), usb_parent="USB\\OTHER")},
            {"role_bridge_proof": replace(role_proof(), capabilities=frozenset())},
            {"role_bridge_proof": replace(role_proof(),
                                           capabilities=ROLE_CAPABILITIES - {"exclusive_fat"})},
            {"role_bridge_proof": replace(role_proof(), live_ack_verified=False)},
            {"role_bridge_proof": replace(role_proof(), hardware_roundtrip_verified=False)},
        ]
        for scenario in test_cases:
            with self.subTest(scenario=scenario), self.assertRaisesRegex(
                HandoffError, "gestor kernel"
            ):
                state.event("dock_requested", no_pending_copy=True,
                            emulator_closed=True, same_storage_identity=True,
                            **scenario)
        self.assertEqual(state.phase, Phase.PREPARED)

    def test_no_eject_when_ppsspp_open_or_copies_open(self):
        state = prepared()
        for emulator_closed, no_pending_copy in ((False, True), (True, False)):
            with self.subTest(emulator_closed=emulator_closed,
                              no_pending_copy=no_pending_copy):
                with self.assertRaises(HandoffError):
                    state.event("dock_requested", role_bridge_proof=role_proof(),
                                emulator_closed=emulator_closed,
                                no_pending_copy=no_pending_copy,
                                same_storage_identity=True)

    def test_never_treat_host_eject_as_psp_usb_role_ack(self):
        state = eject()
        with self.assertRaisesRegex(HandoffError, "Windows no confirmó"):
            state.event("host_eject_ok", os_confirmed=False,
                        same_storage_identity=True)
        state = storage_released(state)
        self.assertEqual(state.phase, Phase.STORAGE_RELEASED)
        with self.assertRaises(HandoffError):
            state.event("play_requested", game_id="TITLE",
                        game_copied_verified=True, same_hid_session=True)
        with self.assertRaisesRegex(HandoffError, "Storage y HID"):
            state.event("hid_present", session=HID, descriptor_verified=True,
                        storage_absent=False, same_physical_usb=True)

    def test_rejects_foreign_or_malformed_hid(self):
        state = storage_released()
        for session in ("123", "z" * 32, "", HID.upper()):
            with self.subTest(session=session), self.assertRaises(HandoffError):
                state.event("hid_present", session=session, descriptor_verified=True,
                            storage_absent=True, same_physical_usb=True)
        for field in ("descriptor_verified", "storage_absent", "same_physical_usb"):
            values = {"descriptor_verified": True, "storage_absent": True,
                      "same_physical_usb": True}
            values[field] = False
            with self.subTest(field=field), self.assertRaises(HandoffError):
                state.event("hid_present", session=HID, **values)

    def test_no_game_play_before_verified_hid(self):
        state = storage_released()
        with self.assertRaises(HandoffError):
            state.event("play_requested", game_id="TITLE",
                        game_copied_verified=True, same_hid_session=True)
        state = hid_ready(state)
        for ok_game, ok_session in ((False, True), (True, False)):
            with self.assertRaises(HandoffError):
                state.event("play_requested", game_id="TITLE",
                            game_copied_verified=ok_game,
                            same_hid_session=ok_session)

    def test_never_return_to_storage_until_game_is_closed(self):
        state = playing()
        with self.assertRaises(HandoffError):
            state.event("emulator_exited", process_gone=False,
                        local_saves_idle=True)
        with self.assertRaises(HandoffError):
            state.event("emulator_exited", process_gone=True,
                        local_saves_idle=False)
        state = returning(state)
        self.assertEqual(state.phase, Phase.RETURN_REQUESTED)

    def test_hid_command_uncertain_delivery_is_not_retried(self):
        state = returning()
        with self.assertRaises(HandoffError):
            state.event("hid_return_sent", transport_attempted_once=False)
        state = waiting_return(state)
        self.assertEqual(state.phase, Phase.STORAGE_RETURN_PENDING)
        with self.assertRaises(HandoffError):
            state.event("hid_return_sent", transport_attempted_once=True)

    def test_never_sync_foreign_memory_stick(self):
        state = waiting_return()
        with self.assertRaisesRegex(HandoffError, "no es la del inicio"):
            state.event("storage_returned", volume_serial="999",
                        usb_parent=USB, mounted_single_volume=True,
                        ppsspp_closed=True)
        with self.assertRaisesRegex(HandoffError, "no es la del inicio"):
            state.event("storage_returned", volume_serial=SERIAL,
                        usb_parent=USB.replace("02D2", "0000"),
                        mounted_single_volume=True, ppsspp_closed=True)
        self.assertEqual(state.phase, Phase.STORAGE_RETURN_PENDING)

    def test_no_safe_to_unplug_until_saves_verified(self):
        state = waiting_return().event(
            "storage_returned", volume_serial=SERIAL, usb_parent=USB,
            mounted_single_volume=True, ppsspp_closed=True)[0]
        for checks in (
            {"copies_verified": False, "no_conflicts": True, "no_copy_journals": True},
            {"copies_verified": True, "no_conflicts": False, "no_copy_journals": True},
            {"copies_verified": True, "no_conflicts": True, "no_copy_journals": False},
        ):
            with self.subTest(checks=checks), self.assertRaises(HandoffError):
                state.event("sync_return_finished", **checks)

    def test_fail_closed_abort_and_snapshot_roundtrip(self):
        state = playing()
        restored = Handoff.from_snapshot(state.snapshot())
        self.assertEqual(restored, state)
        halted, actions = restored.event("abort", reason="La PSP se desconectó")
        self.assertEqual(halted.phase, Phase.HALTED)
        self.assertFalse(actions)
        with self.assertRaisesRegex(HandoffError, "bloqueada"):
            halted.event("psp_disconnected", os_storage_released=True)
        self.assertEqual(Handoff.from_snapshot(halted.snapshot()).phase, Phase.HALTED)

    def test_bad_persistent_state_fails_closed(self):
        old = prepared().snapshot()
        for corrupt in ({"phase": "illegal"}, {"volume_serial": ""},
                        {"unexpected": True}, {"phase": Phase.PLAYING.value}):
            obj = {**old, **corrupt}
            with self.subTest(corrupt=corrupt), self.assertRaises(HandoffError):
                Handoff.from_snapshot(obj)
        with self.assertRaises(HandoffError):
            Handoff.from_snapshot({**playing().snapshot(), "hid_session": ""})
        with self.assertRaises(HandoffError):
            Handoff.from_snapshot({**playing().snapshot(), "revision": -2})
        with self.assertRaises(HandoffError):
            Handoff.from_snapshot({**playing().snapshot(), "hid_session": "x" * 32})

    def test_abort_even_before_storage_mount_is_recoverable_as_halted(self):
        interrupted, commands = Handoff().event("abort", reason="Se cerró el Dock")
        self.assertEqual(interrupted.phase, Phase.HALTED)
        self.assertFalse(commands)
        self.assertEqual(Handoff.from_snapshot(interrupted.snapshot()), interrupted)

    def test_input_validation_prevents_identity_hijack(self):
        for serial, usb, unique in (
            ("", USB, True),
            ("serial-text", USB, True),
            (SERIAL, "USB\\VID_1234&PID_9999", True),
            (SERIAL, USB, False),
        ):
            with self.subTest(serial=serial, usb=usb, unique=unique):
                with self.assertRaises(HandoffError):
                    Handoff().event("storage_present", volume_serial=serial,
                                    usb_parent=usb, mounted_single_volume=unique)

    def test_unexpected_events_do_not_change_state(self):
        state = prepared()
        with self.assertRaises(HandoffError):
            state.event("sync_return_finished", copies_verified=True,
                        no_conflicts=True, no_copy_journals=True)
        self.assertEqual(state.phase, Phase.PREPARED)


if __name__ == "__main__":
    unittest.main()
