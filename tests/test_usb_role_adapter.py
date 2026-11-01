"""Strictly PC-only checks for the inert USB role adapter contract."""
from dataclasses import replace
import unittest
from unittest.mock import patch

from dock_experience import Experience, ExperienceError, Stage
from usb_role_adapter import (
    INTENT_ONLY, OBSERVATION_ONLY,
    HostUsbStatus, IdentityMismatchError, ObservedRole,
    OneShotReturnLedger, PollingContractError, PollingMode, PollingPauseRequest,
    PollingTicket, ReturnAttemptError, ReturnAttemptIntent, ReturnRecordState,
    RoleContractError, SessionExpectation, StorageIdentity,
    UnavailableRoleAdapter, UnavailableRoleError, UsbRoleAdapter,
    VolumePollingContract,
)


SERIAL = "2958455453"
PARENT = "USB\\VID_054C&PID_02D2\\DEVICE_FROM_HOST"
HID = "d3cfc42418a65b0c043a96b773dde7c9"
OTHER_HID = "bcce5b1e998244cdba8174d77f571d0c"


def identity() -> StorageIdentity:
    return StorageIdentity(SERIAL, PARENT)


def session() -> SessionExpectation:
    return SessionExpectation(identity(), HID)


def paused_gate() -> VolumePollingContract:
    gate = VolumePollingContract(identity())
    request = gate.request_pause()
    gate.acknowledge_pause(request, poller_stopped=True)
    return gate


def return_watch() -> tuple[VolumePollingContract, ReturnAttemptIntent]:
    gate = paused_gate()
    gate.bind_observed_hid_candidate(session())
    ledger = OneShotReturnLedger(
        session(), durable_state=ReturnRecordState.NOT_ATTEMPTED,
    )
    reservation = ledger.reserve_before_dispatch(HID)
    gate.begin_return_watch(reservation)
    return gate, reservation


class StorageIdentityTests(unittest.TestCase):
    def test_sony_identity_case_normalization_and_exact_comparison(self):
        same = StorageIdentity(SERIAL, PARENT.lower())
        self.assertEqual(identity(), same)
        self.assertEqual(identity().usb_parent, PARENT)
        self.assertNotEqual(identity(), StorageIdentity("2958455454", PARENT))
        self.assertNotEqual(identity(), StorageIdentity(
            SERIAL, "USB\\VID_054C&PID_02D2\\ANOTHER_INSTANCE",
        ))

    def test_refuse_any_serial_or_ambiguous_usb_parent(self):
        for serial in ("", "-1", "1.0", " 123", "123 ", "１２３", None, 123, True):
            with self.subTest(serial=serial), self.assertRaises(IdentityMismatchError):
                StorageIdentity(serial, PARENT)
        for parent in (
            "", "USB\\VID_054C&PID_02D2", "USB\\VID_054C&PID_02D2\\",
            "USB\\VID_054C&PID_02D1\\DEVICE", "USB\\VID_054C&PID_01CB\\DEVICE",
            "USB\\VID_1234&PID_02D2\\DEVICE", "USB\\VID_054C&PID_02D2\\DEVICE ",
            None, 123,
        ):
            with self.subTest(parent=parent), self.assertRaises(IdentityMismatchError):
                StorageIdentity(SERIAL, parent)

    def test_session_expectation_matches_only_exact_untrusted_ids(self):
        expected = session()
        self.assertTrue(expected.matches_storage(identity()))
        self.assertTrue(expected.matches_hid(HID))
        self.assertFalse(expected.matches_storage(StorageIdentity("222", PARENT)))
        self.assertFalse(expected.matches_storage(StorageIdentity(
            SERIAL, "USB\\VID_054C&PID_02D2\\DIFFERENT",
        )))
        self.assertFalse(expected.matches_hid(OTHER_HID))
        self.assertFalse(expected.matches_hid(HID.upper()))
        self.assertFalse(expected.matches_hid(None))

    def test_invalid_hid_sessions_rejected(self):
        for token in ("", "0" * 32, HID.upper(), HID[:-1], HID + "0", "x" * 32, 12, None):
            with self.subTest(token=token), self.assertRaises(IdentityMismatchError):
                SessionExpectation(identity(), token)
        with self.assertRaises(IdentityMismatchError):
            SessionExpectation("untrusted", HID)


class HostObservationTests(unittest.TestCase):
    def test_classification_is_always_only_observational(self):
        reports = (
            ({"state": "STORAGE_VISIBLE", "volume_serial": SERIAL,
              "usb_parent": PARENT, "hid_candidates": 0}, ObservedRole.STORAGE_VISIBLE),
            ({"state": "HID_VISIBLE_UNBOUND", "volume_serial": None,
              "usb_parent": None, "hid_candidates": 1}, ObservedRole.HID_VISIBLE_UNBOUND),
            ({"state": "DUAL_PRESENT_UNSAFE", "volume_serial": SERIAL,
              "usb_parent": PARENT, "hid_candidates": 2}, ObservedRole.DUAL_PRESENT_UNSAFE),
            ({"state": "WINDOWS_PREPARED_REMOVAL", "hid_candidates": 0},
             ObservedRole.WINDOWS_PREPARED_REMOVAL),
            ({"state": "NO_PSP_INTERFACE_OBSERVED"},
             ObservedRole.NO_PSP_INTERFACE_OBSERVED),
        )
        for report, expected in reports:
            with self.subTest(state=expected):
                status = HostUsbStatus.from_host_report({
                    **report, "role_transition_confirmed": False,
                    "exclusive_fat_confirmed": False,
                    "live_firmware_ack": False,
                    "warnings": ["read-only"], "pnp_nodes": [],
                })
                self.assertEqual(status.state, expected)
                self.assertEqual(status.evidence_kind, OBSERVATION_ONLY)
                self.assertFalse(status.live_firmware_ack)
                self.assertFalse(status.exclusive_fat_confirmed)
                self.assertFalse(status.role_transition_confirmed)

    def test_legacy_boot_txt_true_claim_and_bad_report_are_rejected(self):
        normal = {
            "state": "HID_VISIBLE_UNBOUND", "hid_candidates": 1,
            "role_transition_confirmed": False,
            "exclusive_fat_confirmed": False,
            "live_firmware_ack": False,
        }
        for flag in ("role_transition_confirmed", "exclusive_fat_confirmed",
                     "live_firmware_ack", "hardware_roundtrip_verified"):
            for spoof in (True, "true", 1, None):
                with self.subTest(flag=flag, spoof=spoof):
                    with self.assertRaises(RoleContractError):
                        HostUsbStatus.from_host_report({**normal, flag: spoof})
        with self.assertRaises(RoleContractError):
            HostUsbStatus.from_host_report({"state": "BOOT.TXT", "hid_candidates": 0})
        with self.assertRaises(RoleContractError):
            HostUsbStatus.from_host_report({"state": "REAL_ACK_VERIFIED"})
        with self.assertRaises(RoleContractError):
            HostUsbStatus.from_host_report("not a dictionary")

    def test_reject_impossible_presence_and_invalid_candidate_counts(self):
        cases = (
            {"state": "STORAGE_VISIBLE", "hid_candidates": 0},
            {"state": "HID_VISIBLE_UNBOUND", "hid_candidates": 0},
            {"state": "DUAL_PRESENT_UNSAFE", "hid_candidates": 1},
            {"state": "STORAGE_VISIBLE", "volume_serial": SERIAL,
             "usb_parent": PARENT, "hid_candidates": 1},
            {"state": "NO_PSP_INTERFACE_OBSERVED", "volume_serial": SERIAL,
             "usb_parent": PARENT},
            {"state": "STORAGE_VISIBLE", "volume_serial": SERIAL},
            {"state": "STORAGE_VISIBLE", "usb_parent": PARENT},
            {"state": "HID_VISIBLE_UNBOUND", "hid_candidates": True},
            {"state": "HID_VISIBLE_UNBOUND", "hid_candidates": -1},
        )
        for payload in cases:
            with self.subTest(payload=payload), self.assertRaises(RoleContractError):
                HostUsbStatus.from_host_report(payload)

    def test_no_usb_disappearance_or_pnp_code_proves_fat_release(self):
        for state in ("NO_PSP_INTERFACE_OBSERVED", "WINDOWS_PREPARED_REMOVAL"):
            status = HostUsbStatus.from_host_report({
                "state": state, "pnp_nodes": [{"problem_code": 47}],
            })
            self.assertFalse(status.live_firmware_ack)
            self.assertFalse(status.exclusive_fat_confirmed)


class UnavailableAdapterTests(unittest.TestCase):
    def test_implements_protocol_but_never_returns_live_proof(self):
        adapter = UnavailableRoleAdapter()
        self.assertIsInstance(adapter, UsbRoleAdapter)
        observed = adapter.observe()
        self.assertEqual(observed.state, ObservedRole.UNAVAILABLE)
        self.assertEqual(observed.evidence_kind, OBSERVATION_ONLY)
        self.assertFalse(observed.role_transition_confirmed)
        self.assertIsNone(adapter.verified_live_proof(identity()))
        with self.assertRaises(IdentityMismatchError):
            adapter.verified_live_proof("PSP")

    def test_never_prepares_or_stops_usb_or_dispatches_return(self):
        adapter = UnavailableRoleAdapter()
        gate, attempt = return_watch()
        mode = gate.mode
        with (
            patch("builtins.open", side_effect=AssertionError("filesystem call")),
            patch("subprocess.Popen", side_effect=AssertionError("process call")),
            patch("subprocess.run", side_effect=AssertionError("process call")),
            patch("os.system", side_effect=AssertionError("shell call")),
            patch("socket.socket", side_effect=AssertionError("network call")),
        ):
            self.assertEqual(adapter.observe().state, ObservedRole.UNAVAILABLE)
            self.assertIsNone(adapter.verified_live_proof(identity()))
            with self.assertRaises(UnavailableRoleError):
                adapter.request_prepare(identity(), polling=gate)
            with self.assertRaises(UnavailableRoleError):
                adapter.request_storage_stop(identity(), polling=gate)
            with self.assertRaises(UnavailableRoleError):
                adapter.request_return_once(attempt, polling=gate)
        self.assertEqual(gate.mode, mode)

    def test_cannot_advance_existing_experience_without_live_attestor(self):
        state = Experience()
        state, _ = state.event("storage_arrived", volume_serial=SERIAL,
                               usb_parent=PARENT, sony_psp_verified=True,
                               single_volume=True)
        state, _ = state.event("inventory_complete", inventory_tag="a" * 64,
                               missing_games=0, ready_games=1,
                               save_actions=0, save_conflicts=0)
        state, _ = state.event("save_review_complete", same_identity=True,
                               conflicts_left=0)
        self.assertEqual(state.stage, Stage.STORAGE_READY)
        adapter = UnavailableRoleAdapter()
        with self.assertRaisesRegex(ExperienceError, "bridge kernel REAL"):
            state.event("start_dock", role_bridge_proof=adapter.verified_live_proof(
                identity()), same_identity=True, no_pending_io=True,
                emulator_closed=True)
        self.assertEqual(state.stage, Stage.STORAGE_READY)
        self.assertEqual(state.revision, 3)


class ReturnOnceTests(unittest.TestCase):
    def test_unknown_or_previous_attempt_blocks_dispatch_reservation(self):
        for previous in (ReturnRecordState.UNKNOWN,
                         ReturnRecordState.ATTEMPT_RECORDED):
            with self.subTest(previous=previous):
                journal = OneShotReturnLedger(session(), durable_state=previous)
                with self.assertRaises(ReturnAttemptError):
                    journal.reserve_before_dispatch(HID)
                self.assertEqual(journal.state, previous)
        with self.assertRaises(ReturnAttemptError):
            OneShotReturnLedger(session(), durable_state=False)

    def test_explicit_unattempted_one_shot_is_consumed_even_if_delivery_unknown(self):
        journal = OneShotReturnLedger(
            session(), durable_state=ReturnRecordState.NOT_ATTEMPTED,
        )
        with self.assertRaises(IdentityMismatchError):
            journal.reserve_before_dispatch(OTHER_HID)
        self.assertEqual(journal.state, ReturnRecordState.NOT_ATTEMPTED)
        request = journal.reserve_before_dispatch(HID)
        self.assertEqual(request.session, session())
        self.assertEqual(request.evidence_kind, INTENT_ONLY)
        self.assertEqual(journal.state, ReturnRecordState.ATTEMPT_RECORDED)
        with self.assertRaises(ReturnAttemptError):
            journal.reserve_before_dispatch(HID)
        restored = OneShotReturnLedger(
            session(), durable_state=journal.state,
        )
        with self.assertRaises(ReturnAttemptError):
            restored.reserve_before_dispatch(HID)

    def test_reservation_is_not_a_live_ack_or_permission_for_physical_send(self):
        with self.assertRaises(ReturnAttemptError):
            ReturnAttemptIntent(session())
        attempt = OneShotReturnLedger(
            session(), durable_state=ReturnRecordState.NOT_ATTEMPTED,
        ).reserve_before_dispatch(HID)
        self.assertNotIn("ack", attempt.__dict__)
        self.assertNotIn("role_bridge_proof", attempt.__dict__)
        with self.assertRaises(UnavailableRoleError):
            UnavailableRoleAdapter().request_return_once(
                attempt, polling=paused_gate(),
            )


class PollingContractTests(unittest.TestCase):
    def test_two_phase_pause_blocks_new_polls_and_drains_old(self):
        gate = VolumePollingContract(identity())
        first = gate.begin_volume_poll()
        second = gate.begin_volume_poll()
        self.assertEqual(gate.polls_in_flight, 2)
        pause = gate.request_pause()
        self.assertEqual(gate.mode, PollingMode.PAUSE_REQUESTED)
        self.assertFalse(gate.normal_poll_allowed)
        with self.assertRaises(PollingContractError):
            gate.begin_volume_poll()
        with self.assertRaises(PollingContractError):
            gate.require_paused()
        with self.assertRaises(PollingContractError):
            gate.acknowledge_pause(pause, poller_stopped=True)
        gate.finish_poll(first)
        gate.finish_poll(second)
        self.assertEqual(gate.polls_in_flight, 0)
        with self.assertRaises(PollingContractError):
            gate.acknowledge_pause(pause, poller_stopped=False)
        self.assertEqual(gate.mode, PollingMode.PAUSE_REQUESTED)
        gate.acknowledge_pause(pause, poller_stopped=True)
        gate.require_paused()
        self.assertEqual(gate.mode, PollingMode.PAUSED)
        self.assertFalse(gate.normal_poll_allowed)
        self.assertFalse(gate.return_watch_allowed)

    def test_pause_ack_requires_original_token_and_no_duplicate_polls(self):
        gate = VolumePollingContract(identity())
        pending = gate.begin_volume_poll()
        fake = replace(pending)
        with self.assertRaises(PollingContractError):
            gate.finish_poll(fake)
        gate.finish_poll(pending)
        with self.assertRaises(PollingContractError):
            gate.finish_poll(pending)
        pause = gate.request_pause()
        false_pause = PollingPauseRequest(pause.sequence, pause.expected_storage)
        with self.assertRaises(PollingContractError):
            gate.acknowledge_pause(false_pause, poller_stopped=True)
        gate.acknowledge_pause(pause, poller_stopped=True)
        with self.assertRaises(PollingContractError):
            gate.acknowledge_pause(pause, poller_stopped=True)
        with self.assertRaises(PollingContractError):
            gate.request_pause()

    def test_return_watch_requires_paused_gate_original_storage_and_hid_session(self):
        gate = VolumePollingContract(identity())
        attempt = OneShotReturnLedger(session(), durable_state=ReturnRecordState.NOT_ATTEMPTED).reserve_before_dispatch(HID)
        with self.assertRaises(PollingContractError):
            gate.begin_return_watch(attempt)
        pause = gate.request_pause()
        with self.assertRaises(PollingContractError):
            gate.bind_observed_hid_candidate(session())
        gate.acknowledge_pause(pause, poller_stopped=True)
        with self.assertRaises(IdentityMismatchError):
            gate.bind_observed_hid_candidate(SessionExpectation(
                StorageIdentity("999", PARENT), HID,
            ))
        gate.bind_observed_hid_candidate(session())
        with self.assertRaises(PollingContractError):
            gate.bind_observed_hid_candidate(session())
        wrong_session = OneShotReturnLedger(
            SessionExpectation(identity(), OTHER_HID),
            durable_state=ReturnRecordState.NOT_ATTEMPTED,
        ).reserve_before_dispatch(OTHER_HID)
        with self.assertRaises(IdentityMismatchError):
            gate.begin_return_watch(wrong_session)
        wrong_storage = OneShotReturnLedger(
            SessionExpectation(StorageIdentity("999", PARENT), HID),
            durable_state=ReturnRecordState.NOT_ATTEMPTED,
        ).reserve_before_dispatch(HID)
        with self.assertRaises(IdentityMismatchError):
            gate.begin_return_watch(wrong_storage)
        gate.begin_return_watch(attempt)
        self.assertTrue(gate.return_watch_allowed)
        self.assertFalse(gate.normal_poll_allowed)
        with self.assertRaises(PollingContractError):
            gate.begin_volume_poll()

    def test_normal_poll_cannot_resume_before_original_return_saves_and_idle(self):
        gate, attempt = return_watch()
        self.assertEqual(attempt.evidence_kind, INTENT_ONLY)
        with self.assertRaises(PollingContractError):
            gate.resume_after_verified_return(identity(),
                                              saves_synced=True, no_pending_io=True)
        ticket = gate.begin_original_storage_probe()
        self.assertFalse(gate.record_original_storage_probe(ticket, None))
        self.assertEqual(gate.mode, PollingMode.RETURN_WATCH_ONLY)
        ticket = gate.begin_original_storage_probe()
        self.assertTrue(gate.record_original_storage_probe(ticket, identity()))
        self.assertEqual(gate.mode, PollingMode.ORIGINAL_STORAGE_OBSERVED)
        for synced, idle in ((False, True), (True, False)):
            with self.subTest(synced=synced, idle=idle):
                with self.assertRaises(PollingContractError):
                    gate.resume_after_verified_return(identity(),
                                                      saves_synced=synced,
                                                      no_pending_io=idle)
        with self.assertRaises(IdentityMismatchError):
            gate.resume_after_verified_return(StorageIdentity("999", PARENT),
                                              saves_synced=True, no_pending_io=True)
        self.assertFalse(gate.normal_poll_allowed)
        gate.resume_after_verified_return(identity(), saves_synced=True,
                                          no_pending_io=True)
        self.assertTrue(gate.normal_poll_allowed)
        self.assertFalse(gate.return_watch_allowed)
        new_poll = gate.begin_volume_poll()
        gate.finish_poll(new_poll)

    def test_wrong_returned_serial_or_parent_halts_polling(self):
        for unexpected in (
            StorageIdentity("999", PARENT),
            StorageIdentity(SERIAL, "USB\\VID_054C&PID_02D2\\NOT_ORIGINAL"),
        ):
            with self.subTest(unexpected=unexpected):
                gate, _ = return_watch()
                ticket = gate.begin_original_storage_probe()
                with self.assertRaises(IdentityMismatchError):
                    gate.record_original_storage_probe(ticket, unexpected)
                self.assertEqual(gate.mode, PollingMode.HALTED)
                self.assertEqual(gate.polls_in_flight, 0)
                with self.assertRaises(PollingContractError):
                    gate.begin_original_storage_probe()
                with self.assertRaises(PollingContractError):
                    gate.begin_volume_poll()

    def test_multiple_inflight_original_only_probes_drain_before_resume(self):
        gate, _ = return_watch()
        a = gate.begin_original_storage_probe()
        b = gate.begin_original_storage_probe()
        self.assertTrue(gate.record_original_storage_probe(a, identity()))
        self.assertEqual(gate.mode, PollingMode.RETURN_WATCH_ONLY)
        self.assertTrue(gate.record_original_storage_probe(b, None))
        self.assertEqual(gate.mode, PollingMode.ORIGINAL_STORAGE_OBSERVED)
        self.assertEqual(gate.polls_in_flight, 0)

    def test_stale_poll_ticket_in_return_phase_is_rejected(self):
        gate, _ = return_watch()
        bogus = PollingTicket(1, identity(), "normal")
        with self.assertRaises(PollingContractError):
            gate.record_original_storage_probe(bogus, identity())
        self.assertEqual(gate.mode, PollingMode.RETURN_WATCH_ONLY)

    def test_interrupt_keeps_polling_suspended_until_manual_new_contract(self):
        gate, _ = return_watch()
        gate.halt()
        self.assertFalse(gate.normal_poll_allowed)
        self.assertFalse(gate.return_watch_allowed)
        with self.assertRaises(PollingContractError):
            gate.begin_volume_poll()
        with self.assertRaises(PollingContractError):
            gate.begin_original_storage_probe()
        with self.assertRaises(PollingContractError):
            gate.request_pause()


if __name__ == "__main__":
    unittest.main()
