"""Product workflow tests with explicit synthetic transport/file adapters.

These verify orchestration and consent, not PSP hardware or save compatibility.
"""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from psp_dock.core.dock_product_flow import (FlowCallbacks, ProductFlow, ProductFlowError,
                               ProductFlowCancelled, plan_session_return)


GAME = "a" * 24
OTHER_GAME = "b" * 24
INVENTORY = {
    "fingerprint": "c" * 64,
    "counts": {"psp_games": 3, "pc_games": 2, "missing_games": 1,
               "save_actions": 2, "save_conflicts": 0},
    "missing_game_ids": [GAME],
}


class Adapter:
    def __init__(self):
        self.calls = []
        self.handlers = {}

    def call(self, name, context):
        self.calls.append((name, deepcopy(context)))
        if name in self.handlers:
            return self.handlers[name](context)
        if name == "inventory":
            return deepcopy(INVENTORY)
        if name == "request_role":
            return {"accepted": True}
        if name == "finalize_exit":
            return {"verified": True, "host_eject_accepted": True}
        return {"verified": True, "conflicts": [],
                **({"direction": "to_psp"} if name == "sync_return" else {})}

    def callbacks(self):
        return FlowCallbacks(**{name: (lambda ctx, name=name: self.call(name, ctx))
                                for name in ("inventory", "sync_initial", "copy_game",
                                             "sync_return", "request_role", "finalize_exit")})

    def names(self):
        return [name for name, _ in self.calls]


def storage(*, session="session-one", volume="12345"):
    return {"session_id": session, "volume_serial": volume,
            "storage_ready": True, "controller_ready": False}


def controller(*, session="session-one", volume="12345"):
    return {"session_id": session, "volume_serial": volume,
            "storage_ready": False, "controller_ready": True}


class ProductFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "product-session.json"
        self.adapter = Adapter()
        self.now = 10.0
        self.flow = self.new_flow()

    def new_flow(self, *, automatic=True, enabled=True):
        return ProductFlow(self.path, self.adapter.callbacks(), enabled=enabled,
                           capabilities={"automatic_transition": automatic},
                           clock=lambda: self.now, role_timeout=30)

    def offer(self):
        self.flow.observe(storage())
        self.assertEqual(self.flow.snapshot()["phase"], "inventory")
        self.assertTrue(self.flow.advance())
        offer = self.flow.snapshot()
        self.assertEqual(offer["phase"], "choosing")
        return offer["offer_id"]

    def ready(self, choice="none"):
        offer_id = self.offer()
        self.flow.choose(choice, offer_id)
        if choice != "none":
            self.flow.advance()
        self.flow.advance()  # Send role command, not proof of the new role.
        self.flow.observe(controller())
        self.assertEqual(self.flow.snapshot()["phase"], "ready")

    def test_inventory_precedes_choice_and_none_never_copies_or_syncs(self):
        self.assertFalse(self.flow.advance())
        with self.assertRaises(ProductFlowError):
            self.flow.choose("none", "stale")
        offer_id = self.offer()
        self.assertEqual(self.adapter.names(), ["inventory"])
        self.flow.choose("none", offer_id)
        self.assertEqual(self.adapter.names(), ["inventory"])
        self.assertEqual(self.flow.snapshot()["phase"], "entering_controller")
        self.flow.advance()
        self.assertEqual(self.adapter.names(), ["inventory", "request_role"])
        self.assertEqual(self.flow.snapshot()["phase"], "entering_controller")
        self.assertTrue(self.flow.snapshot()["blocking"])
        self.flow.observe(controller())
        self.assertEqual(self.flow.snapshot()["phase"], "ready")
        self.assertFalse(self.flow.snapshot()["blocking"])

    def test_orphan_native_close_only_offers_explicit_finalization_without_inventory(self):
        closing = {**storage(), 'storage_ready':False, 'phase':1,
                   'shutdown_pending':True, 'shutdown_cancel':True, 'exit_requested':True}
        self.flow.observe(closing)
        offer = self.flow.snapshot()
        self.assertEqual(offer['phase'], 'attention')
        self.assertTrue(offer['can_retry'])
        self.assertFalse(self.flow.advance())
        self.assertEqual(self.adapter.names(), [])
        self.flow.retry(offer['offer_id'])
        self.assertTrue(self.flow.advance())
        self.assertEqual(self.adapter.names(), ['finalize_exit'])
        self.assertEqual(self.adapter.calls[0][1]['purpose'], 'cancel')

    def test_restart_presents_welcome_without_erasing_recovery_or_repeating_work(self):
        self.offer()
        self.flow.request_cancel()
        self.adapter.handlers['finalize_exit'] = lambda ctx: (_ for _ in ()).throw(
            ProductFlowError('USB terminal not acknowledged'))
        self.flow.advance()
        before = self.path.read_bytes()
        self.flow = self.new_flow()
        self.assertTrue(self.flow.snapshot()['startup_recovery'])
        self.assertEqual(self.flow.snapshot()['resume_phase'], 'finalizing')
        self.assertEqual(self.path.read_bytes(), before)
        closing = {**storage(), 'storage_ready':False, 'phase':1,
                   'shutdown_pending':True, 'shutdown_cancel':True, 'exit_requested':True}
        self.flow.observe(closing)
        state = self.flow.snapshot()
        self.assertTrue(state['startup_recovery'] and state['can_retry'])
        self.assertFalse(self.flow.advance())
        self.assertEqual(self.adapter.names(), ['inventory','finalize_exit'])
        # This exact snapshot is what the browser must receive to expose
        # the finalization Retry; previously resume_phase was omitted.
        self.assertEqual(json.loads(json.dumps(state))['resume_phase'], 'finalizing')
        del self.adapter.handlers['finalize_exit']
        self.flow.retry(state['offer_id'])
        self.assertFalse(self.flow.snapshot()['startup_recovery'])
        self.flow.advance()
        self.assertEqual(self.adapter.names(), ['inventory','finalize_exit','finalize_exit'])

    def test_fresh_start_and_current_failure_are_not_mislabelled_as_old_recovery(self):
        self.assertFalse(self.flow.snapshot()['startup_recovery'])
        self.flow.observe(storage())
        self.flow.halt('current connection failed')
        self.assertFalse(self.flow.snapshot()['startup_recovery'])
        restarted = self.new_flow()
        self.assertTrue(restarted.snapshot()['startup_recovery'])
        self.assertFalse(restarted.advance())

    def test_native_close_recovery_requires_exact_nonce_and_never_repeats_sync(self):
        self.offer()
        self.flow.request_cancel()
        def blocked(context):
            raise ProductFlowError('Synthetic pending terminal close')
        self.adapter.handlers['finalize_exit'] = blocked
        self.flow.advance()
        self.flow = self.new_flow()
        closing = {**storage(), 'storage_ready':False, 'phase':1,
                   'shutdown_pending':True, 'shutdown_cancel':True, 'exit_requested':True}
        self.flow.observe({**closing, 'session_id':'another-session'})
        self.assertFalse(self.flow.snapshot()['can_retry'])
        self.flow.observe(closing)
        offer = self.flow.snapshot()
        self.assertTrue(offer['can_retry'])
        del self.adapter.handlers['finalize_exit']
        self.flow.retry(offer['offer_id'])
        self.flow.advance()
        self.assertEqual(self.adapter.names(), ['inventory', 'finalize_exit', 'finalize_exit'])
        self.assertEqual(self.flow.snapshot()['phase'], 'safe_to_disconnect')

    def test_native_close_observation_never_grants_storage_or_controller_permission(self):
        closing = {**storage(), 'storage_ready':False, 'shutdown_pending':True}
        for changes in ({'storage_ready':True}, {'controller_ready':True},
                        {'safe_ready':True}, {'shutdown_cancel':'true'}):
            with self.subTest(changes=changes), self.assertRaises(ProductFlowError):
                self.flow.observe({**closing, **changes})

    def test_early_psp_exit_skips_inventory_choice_and_all_sync_callbacks(self):
        self.flow.observe({**storage(), 'exit_requested':True})
        self.assertEqual(self.flow.snapshot()['phase'], 'finalizing')
        self.assertEqual(self.flow.snapshot()['purpose'], 'cancel')
        self.assertTrue(self.flow.advance())
        self.assertEqual(self.adapter.names(), ['finalize_exit'])
        self.assertEqual(self.adapter.calls[0][1]['purpose'], 'cancel')
        self.assertEqual(self.flow.snapshot()['message'], 'Conexión cerrada. Ya puedes desconectar la PSP.')

    def test_cancel_after_choice_never_starts_the_authorized_but_not_admitted_sync(self):
        self.flow.choose('all', self.offer())
        self.flow.observe({**storage(), 'exit_requested':True})
        self.flow.advance()
        self.assertEqual(self.adapter.names(), ['inventory', 'finalize_exit'])

    def test_exit_before_first_controller_observation_still_cancels_entry(self):
        self.flow.choose('none', self.offer())
        self.flow.advance()
        self.flow.observe({**controller(), 'exit_requested':True})
        self.assertFalse(self.flow.snapshot()['entry_completed'])
        self.assertEqual(self.flow.snapshot()['purpose'], 'cancel')
        self.flow.advance()
        self.assertNotIn('sync_return', self.adapter.names())

    def test_cancel_during_admitted_callback_drains_then_only_finalizes(self):
        self.flow.choose('saves', self.offer())
        def finish_current(ctx):
            self.flow.request_cancel()
            self.assertTrue(self.flow.snapshot()['operation_active'])
            self.assertTrue(json.loads(self.path.read_text())['cancel_requested'])
            raise ProductFlowCancelled('finished the admitted file')
        self.adapter.handlers['sync_initial'] = finish_current
        self.flow.advance()
        self.assertEqual(self.flow.snapshot()['phase'], 'finalizing')
        self.flow.advance()
        self.assertEqual(self.adapter.names(), ['inventory','sync_initial','finalize_exit'])

    def test_failure_while_cancelling_needs_explicit_retry_and_never_replays_sync(self):
        self.flow.choose('saves', self.offer())
        def failed(ctx):
            self.flow.request_cancel()
            raise OSError('verification failed; recovery journal remains')
        self.adapter.handlers['sync_initial'] = failed
        self.flow.advance()
        self.assertEqual(self.flow.snapshot()['phase'], 'attention')
        self.assertFalse(self.flow.advance())
        self.flow.retry(self.flow.snapshot()['offer_id'])
        self.flow.advance()
        self.assertEqual(self.adapter.names(), ['inventory','sync_initial','finalize_exit'])

    def test_crashed_cancel_intent_requires_review_and_resumes_only_finalization(self):
        self.flow.observe(storage())
        def interrupted(ctx):
            self.flow.request_cancel()
            resumed = self.new_flow()
            resumed.observe(storage())
            self.assertEqual(resumed.snapshot()['phase'], 'attention')
            self.assertEqual(resumed.snapshot()['purpose'], 'cancel')
            self.assertFalse(resumed.advance())
            resumed.retry(resumed.snapshot()['offer_id'])
            resumed.advance()
            return deepcopy(INVENTORY)
        self.adapter.handlers['inventory'] = interrupted
        self.flow.advance()
        self.assertEqual(self.adapter.names(), ['inventory','finalize_exit'])

    def test_entry_cancellation_is_not_an_exit_shortcut_after_playing(self):
        self.ready()
        self.flow.set_game_running(True)
        self.flow.set_game_running(False)
        with self.assertRaises(ProductFlowError):
            self.flow.request_cancel()

    def test_failed_read_only_inventory_can_cancel_without_retrying_handshake(self):
        def fail(context):
            raise ValueError('HOST_BEGIN confirmation missing')
        self.adapter.handlers['inventory'] = fail
        self.flow.observe(storage())
        self.flow.advance()
        self.assertEqual(self.flow.snapshot()['phase'], 'attention')
        self.assertTrue(self.flow.snapshot()['can_cancel'])
        self.assertTrue(self.flow.snapshot()['can_close_app'])
        self.flow.request_cancel()
        self.assertEqual(self.flow.snapshot()['phase'], 'finalizing')
        self.flow.advance()
        self.assertEqual(self.adapter.names(), ['inventory', 'finalize_exit'])
        self.assertEqual(self.flow.snapshot()['phase'], 'safe_to_disconnect')

    def test_failed_write_is_not_offered_as_unreviewed_cancel(self):
        self.flow.choose('saves', self.offer())
        self.adapter.handlers['sync_initial'] = lambda context: (_ for _ in ()).throw(OSError('write interrupted'))
        self.flow.advance()
        self.assertFalse(self.flow.snapshot()['can_cancel'])
        self.assertTrue(self.flow.snapshot()['can_close_app'])
        self.flow.request_cancel()
        self.assertEqual(self.flow.snapshot()['phase'], 'attention')
        self.assertFalse(self.flow.advance())
        self.assertNotIn('finalize_exit', self.adapter.names())

    def test_failed_entry_disconnected_can_close_app_without_claiming_safe_or_retry(self):
        self.flow.observe(storage())
        self.flow.halt('HOST_BEGIN confirmation missing')
        self.flow.observe(None)
        state = self.flow.snapshot()
        self.assertTrue(state['can_close_app'])
        self.assertFalse(state['can_close'] or state['can_cancel'] or state['can_retry'])
        self.assertEqual(self.adapter.names(), [])

    def test_choice_selects_exact_initial_callback_policy(self):
        for choice in ("saves", "games", "all"):
            with self.subTest(choice=choice):
                self.path.unlink(missing_ok=True)
                self.adapter = Adapter()
                self.flow = self.new_flow()
                offer_id = self.offer()
                self.flow.choose(choice, offer_id)
                self.assertEqual(self.flow.snapshot()["phase"], "syncing_in")
                self.assertEqual(self.adapter.names(), ["inventory"])
                self.flow.advance()
                self.assertEqual(self.adapter.names(), ["inventory", "sync_initial"])
                context = self.adapter.calls[-1][1]
                self.assertEqual(context["choice"], choice)
                self.assertEqual(context["inventory"], INVENTORY)
                self.assertEqual(context["identity"], {"session_id": "session-one", "volume_serial": "12345"})
                self.assertEqual(self.flow.snapshot()["phase"], "entering_controller")

    def test_hid_first_requests_storage_and_waits_for_real_mount(self):
        self.flow.observe(controller())
        self.assertEqual(self.flow.snapshot()["phase"], "entering_storage")
        self.flow.advance()
        self.assertEqual(self.adapter.names(), ["request_role"])
        self.assertEqual(self.adapter.calls[0][1]["role"], "storage")
        self.assertFalse(self.flow.advance())
        self.flow.observe(None)  # Expected bus gap during a role transition.
        self.assertEqual(self.flow.snapshot()["phase"], "entering_storage")
        self.flow.observe(storage())
        self.flow.advance()
        self.assertEqual(self.adapter.names(), ["request_role", "inventory"])
        self.assertEqual(self.flow.snapshot()["phase"], "choosing")

    def test_manual_adapter_never_sends_automatic_role_commands(self):
        self.flow = self.new_flow(automatic=False)
        offer_id = self.offer()
        self.flow.choose("none", offer_id)
        self.assertFalse(self.flow.advance())
        self.now += 100
        self.assertFalse(self.flow.advance())
        self.assertEqual(self.adapter.names(), ["inventory"])
        self.assertIn("modo mando", self.flow.snapshot()["message"])
        self.flow.observe(controller())
        self.assertEqual(self.flow.snapshot()["phase"], "ready")

    def test_disabled_diagnostic_flow_never_changes_or_invokes_callbacks(self):
        self.flow = self.new_flow(enabled=False)
        self.flow.observe(storage())
        self.assertFalse(self.flow.advance())
        self.assertFalse(self.flow.snapshot()["enabled"])
        self.assertEqual(self.flow.snapshot()["phase"], "waiting_psp")
        self.assertEqual(self.adapter.names(), [])
        with self.assertRaises(ProductFlowError):
            self.flow.request_exit()

    def test_stale_offer_and_unknown_choice_have_no_side_effects(self):
        offer_id = self.offer()
        before = self.path.read_bytes()
        for choice, token in (("all", "d" * 64), ("overwrite", offer_id), ([], offer_id)):
            with self.subTest(choice=choice):
                with self.assertRaises(ProductFlowError):
                    self.flow.choose(choice, token)
                self.assertEqual(self.path.read_bytes(), before)
                self.assertEqual(self.adapter.names(), ["inventory"])

    def test_new_card_or_session_revokes_the_offer(self):
        for changed in (storage(volume="99999"), storage(session="other-session")):
            with self.subTest(changed=changed):
                self.path.unlink(missing_ok=True)
                self.adapter = Adapter()
                self.flow = self.new_flow()
                offer_id = self.offer()
                self.flow.observe(changed)
                self.assertEqual(self.flow.snapshot()["phase"], "attention")
                self.assertEqual(self.flow.snapshot()["can_retry"], changed['volume_serial'] == '12345')
                # Same-card reconnect offers explicit recovery; observing
                # it still cannot restart inventory or copy on its own.
                self.assertFalse(self.flow.advance())
                with self.assertRaises(ProductFlowError):
                    self.flow.choose("all", offer_id)
                self.assertFalse(self.flow.advance())
                self.assertEqual(self.adapter.names(), ["inventory"])

    def test_disconnection_before_selection_returns_waiting_and_requires_new_inventory(self):
        old_offer = self.offer()
        self.flow.observe(None)
        self.assertEqual(self.flow.snapshot()["phase"], "waiting_psp")
        new_offer = self.offer()
        self.assertNotEqual(old_offer, new_offer)
        with self.assertRaises(ProductFlowError):
            self.flow.choose("all", old_offer)
        self.assertEqual(self.adapter.names(), ["inventory", "inventory"])

    def test_one_game_copy_blocks_then_returns_only_after_controller_observation(self):
        self.ready()
        before = len(self.adapter.calls)
        with self.assertRaises(ProductFlowError):
            self.flow.request_copy(OTHER_GAME)
        self.flow.request_copy(GAME)
        self.assertEqual(self.flow.snapshot()["phase"], "waiting_storage_copy")
        self.assertTrue(self.flow.snapshot()["blocking"])
        self.flow.advance()
        self.assertEqual(self.adapter.calls[-1][1]["role"], "storage")
        self.assertNotIn("copy_game", self.adapter.names())
        self.flow.observe(storage())
        self.flow.advance()
        self.assertEqual(self.flow.snapshot()["phase"], "returning_to_controller")
        self.assertEqual(self.adapter.calls[-1][1]["game_id"], GAME)
        self.flow.advance()
        self.assertEqual(self.flow.snapshot()["phase"], "returning_to_controller")
        self.flow.observe(controller())
        self.assertEqual(self.flow.snapshot()["phase"], "ready")
        self.assertEqual(self.adapter.names()[before:], ["request_role", "copy_game", "request_role"])
        self.assertEqual(self.flow.snapshot()["counts"]["missing_games"], 0)
        with self.assertRaises(ProductFlowError):
            self.flow.request_copy(GAME)

    def test_game_close_stays_in_controller_and_exit_is_outbound_only(self):
        self.ready("saves")
        self.flow.set_game_running(True)
        before = len(self.adapter.calls)
        with self.assertRaises(ProductFlowError):
            self.flow.request_exit()
        with self.assertRaises(ProductFlowError):
            self.flow.request_copy(GAME)
        self.flow.set_game_running(False)
        self.assertEqual(self.flow.snapshot()["phase"], "ready")
        self.assertFalse(self.flow.advance())
        self.assertEqual(len(self.adapter.calls), before)
        self.flow.request_exit()
        self.flow.advance()
        self.assertEqual(self.adapter.calls[-1][1]["role"], "storage")
        self.flow.observe(storage())
        self.flow.advance()
        self.assertEqual(self.adapter.calls[-1][0], "sync_return")
        self.assertEqual(self.adapter.calls[-1][1]["direction"], "to_psp")
        self.assertEqual(self.flow.snapshot()["phase"], "finalizing")
        self.assertFalse(self.flow.snapshot()["can_close"])
        self.flow.advance()
        self.assertEqual(self.flow.snapshot()["phase"], "safe_to_disconnect")
        self.assertTrue(self.flow.snapshot()["can_close"])
        self.assertNotIn("copy_game", self.adapter.names())

    def test_return_result_in_other_direction_never_claims_safe(self):
        self.ready()
        self.adapter.handlers["sync_return"] = lambda ctx: {"verified": True, "direction": "to_pc"}
        self.flow.request_exit()
        self.flow.observe(storage())
        self.flow.advance()
        self.assertEqual(self.flow.snapshot()["phase"], "attention")
        self.assertFalse(self.flow.snapshot()["can_close"])
        self.assertIn("sólo permite", self.flow.snapshot()["attention_reason"])

    def test_return_result_cannot_hide_mixed_directions(self):
        self.ready()
        self.adapter.handlers["sync_return"] = lambda ctx: {
            "verified": True, "direction": "to_psp", "directions": ["to_psp", "to_pc"]}
        self.flow.request_exit()
        self.flow.observe(storage())
        self.flow.advance()
        self.assertEqual(self.flow.snapshot()["phase"], "attention")
        self.assertFalse(self.flow.snapshot()["can_close"])

    def test_conflicts_stop_and_refresh_never_restarts_writes(self):
        offer_id = self.offer()
        self.adapter.handlers["sync_initial"] = lambda ctx: {
            "verified": False, "conflicts": [{"slot": "ULJM00001"}], "reason": "Elige la partida."}
        self.flow.choose("saves", offer_id)
        self.flow.advance()
        state = self.flow.snapshot()
        self.assertEqual(state["phase"], "attention")
        self.assertTrue(state["can_review_saves"])
        self.assertTrue(state["can_retry"])
        self.flow.observe(storage())
        self.assertFalse(self.flow.advance())
        self.assertEqual(self.adapter.names(), ["inventory", "sync_initial"])
        self.flow.retry(state["offer_id"])
        self.flow.observe(storage())
        self.flow.advance()
        self.assertEqual(self.flow.snapshot()["phase"], "choosing")
        self.assertNotEqual(self.flow.snapshot()["offer_id"], offer_id)
        self.assertEqual(self.adapter.names(), ["inventory", "sync_initial", "inventory"])

    def test_intent_is_durable_before_mutating_callback(self):
        offer_id = self.offer()
        observed = []
        def sync(ctx):
            journal = json.loads(self.path.read_text())
            observed.append(journal["operation"])
            self.assertEqual(journal["phase"], "syncing_in")
            self.assertEqual(journal["operation"]["id"], ctx["operation_id"])
            self.assertEqual(journal["operation"]["status"], "started")
            return {"verified": True}
        self.adapter.handlers["sync_initial"] = sync
        self.flow.choose("all", offer_id)
        self.flow.advance()
        self.assertEqual(len(observed), 1)
        self.assertEqual(json.loads(self.path.read_text())["operation"]["status"], "completed")

    def test_persistence_failure_before_intent_never_calls_adapter(self):
        offer_id = self.offer()
        self.flow.choose("all", offer_id)
        with patch("psp_dock.core.dock_product_flow._atomic_json", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.flow.advance()
        self.assertEqual(self.adapter.names(), ["inventory"])
        self.assertFalse(self.flow.snapshot()["operation_active"])

    def test_persistence_failure_after_copy_stops_without_repetition(self):
        self.ready()
        self.flow.request_copy(GAME)
        self.flow.observe(storage())
        import psp_dock.core.dock_product_flow as module
        real_save = module._atomic_json
        attempts = 0
        def save(path, value):
            nonlocal attempts
            attempts += 1
            if attempts >= 2:
                raise OSError("disk full after committed copy")
            return real_save(path, value)
        with patch("psp_dock.core.dock_product_flow._atomic_json", side_effect=save):
            self.flow.advance()
        self.assertEqual(self.flow.snapshot()["phase"], "attention")
        self.assertTrue(self.flow.snapshot()["recovery_required"])
        self.assertFalse(self.flow.advance())
        self.assertEqual(self.adapter.names().count("copy_game"), 1)
        self.assertEqual(json.loads(self.path.read_text())["operation"]["status"], "started")

    def test_restart_requires_new_consent_and_same_card_storage(self):
        offer_id = self.offer()
        self.flow.choose("all", offer_id)
        restarted = self.new_flow()
        self.assertEqual(restarted.snapshot()["phase"], "attention")
        self.assertTrue(restarted.snapshot()["recovery_required"])
        restarted.observe(controller(session="new-host-binding"))
        self.assertFalse(restarted.snapshot()["can_retry"])
        restarted.observe(storage(session="new-host-binding", volume="wrong-card"))
        self.assertFalse(restarted.snapshot()["can_retry"])
        restarted.observe(storage(session="new-host-binding"))
        self.assertTrue(restarted.snapshot()["can_retry"])
        self.assertFalse(restarted.advance())
        self.assertEqual(self.adapter.names(), ["inventory"])
        restarted.retry(restarted.snapshot()["offer_id"])
        restarted.observe(storage(session="new-host-binding"))
        restarted.advance()
        self.assertEqual(restarted.snapshot()["phase"], "choosing")
        self.assertNotEqual(restarted.snapshot()["offer_id"], offer_id)
        self.assertEqual(self.adapter.names(), ["inventory", "inventory"])

    def test_completed_session_new_manual_launch_starts_waiting(self):
        self.ready()
        self.flow.request_exit()
        self.flow.observe(storage())
        self.flow.advance()
        self.flow.advance()
        restarted = self.new_flow()
        self.assertEqual(restarted.snapshot()["phase"], "waiting_psp")
        restarted.observe(storage(session="new-session"))
        restarted.advance()
        self.assertEqual(restarted.snapshot()["phase"], "choosing")

    def test_finalization_veto_retry_never_repeats_savedata(self):
        self.ready()
        self.flow.request_exit()
        self.flow.observe(storage())
        self.flow.advance()
        self.adapter.handlers["finalize_exit"] = lambda ctx: {"verified": False, "host_eject_accepted": False}
        self.flow.advance()
        self.assertEqual(self.flow.snapshot()["phase"], "attention")
        safe = {**storage(), "storage_ready": False, "safe_ready": True}
        self.flow.observe(safe)
        self.flow.retry(self.flow.snapshot()["offer_id"])
        self.assertEqual(self.flow.snapshot()["phase"], "finalizing")
        del self.adapter.handlers["finalize_exit"]
        self.flow.advance()
        self.assertEqual(self.flow.snapshot()["phase"], "safe_to_disconnect")
        self.assertEqual(self.adapter.names().count("sync_return"), 1)
        self.assertEqual(self.adapter.names().count("finalize_exit"), 2)

    def test_restart_after_game_preserves_return_purpose(self):
        self.ready("none")
        self.flow.set_game_running(True)
        self.flow.set_game_running(False)
        restarted = self.new_flow()
        self.assertEqual(restarted.snapshot()["phase"], "attention")
        self.assertEqual(restarted.snapshot()["purpose"], "exit")
        restarted.observe(storage())
        restarted.retry(restarted.snapshot()["offer_id"])
        restarted.observe(storage())
        restarted.advance()
        self.assertEqual(self.adapter.calls[-1][0], "sync_return")
        self.assertEqual(self.adapter.names().count("inventory"), 1)

    def test_live_disconnect_after_game_retries_return_without_new_inventory(self):
        self.ready("none")
        self.flow.set_game_running(True)
        self.flow.set_game_running(False)
        self.flow.observe(None)
        self.assertEqual(self.flow.snapshot()["phase"], "attention")
        self.assertEqual(self.flow.snapshot()["purpose"], "exit")
        self.assertFalse(self.flow.advance())
        self.flow.observe(storage())
        self.assertFalse(self.flow.advance())
        self.flow.retry(self.flow.snapshot()["offer_id"])
        self.flow.observe(storage())
        self.flow.advance()
        self.assertEqual(self.adapter.calls[-1][0], "sync_return")
        self.assertEqual(self.adapter.names().count("inventory"), 1)

    def test_native_return_requires_exit_phase_even_when_storage_is_already_mounted(self):
        self.ready("none")
        self.flow.request_exit()
        self.flow.observe({**storage(), "phase":1})
        self.assertEqual(self.flow.snapshot()["phase"], "waiting_storage_return")
        self.flow.advance()
        self.assertEqual(self.adapter.calls[-1][0], "request_role")
        self.assertEqual(self.adapter.calls[-1][1]["purpose"], "exit")
        self.flow.observe({**storage(), "phase":1})
        self.assertFalse(self.flow.advance())
        self.flow.observe({**storage(), "phase":4})
        self.flow.advance()
        self.assertEqual(self.adapter.calls[-1][0], "sync_return")

    def test_role_delivery_is_one_shot_and_timeout_requires_explicit_retry(self):
        offer_id = self.offer()
        self.flow.choose("none", offer_id)
        self.flow.advance()
        for _ in range(4):
            self.flow.observe(None)
            self.flow.advance()
        self.assertEqual(self.adapter.names().count("request_role"), 1)
        self.now += 31
        self.flow.advance()
        self.assertEqual(self.flow.snapshot()["phase"], "attention")
        self.assertFalse(self.flow.snapshot()["can_retry"])
        self.flow.observe(storage())
        self.assertTrue(self.flow.snapshot()["can_retry"])
        self.assertFalse(self.flow.advance())
        self.assertEqual(self.adapter.names().count("request_role"), 1)

    def test_snapshot_is_io_free_and_callback_is_not_under_state_lock(self):
        offer_id = self.offer()
        entered, release = threading.Event(), threading.Event()
        def sync(ctx):
            entered.set()
            if not release.wait(3):
                raise RuntimeError("test callback timed out")
            return {"verified": True}
        self.adapter.handlers["sync_initial"] = sync
        self.flow.choose("all", offer_id)
        worker = threading.Thread(target=self.flow.advance)
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            with patch("builtins.open", side_effect=AssertionError("I/O from snapshot")), \
                 patch.object(Path, "exists", side_effect=AssertionError("I/O from snapshot")):
                state = self.flow.snapshot()
            self.assertEqual(state["phase"], "syncing_in")
            self.assertTrue(state["operation_active"])
            self.assertFalse(self.flow.advance())
            with self.assertRaises(ProductFlowError):
                self.flow.request_exit()
        finally:
            release.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.flow.snapshot()["phase"], "entering_controller")

    def test_disconnect_during_callback_cannot_be_overwritten_by_success(self):
        offer_id = self.offer()
        def sync(ctx):
            self.flow.observe(None)
            return {"verified": True}
        self.adapter.handlers["sync_initial"] = sync
        self.flow.choose("saves", offer_id)
        self.flow.advance()
        self.assertEqual(self.flow.snapshot()["phase"], "attention")
        self.assertFalse(self.flow.snapshot()["can_close"])
        self.assertNotIn("request_role", self.adapter.names())

    def test_invalid_inventory_never_offers_copy_and_callback_exception_is_not_retried(self):
        for invalid in ({}, {**INVENTORY, "missing_game_ids": []}):
            with self.subTest(invalid=invalid):
                self.path.unlink(missing_ok=True)
                self.adapter = Adapter()
                self.flow = self.new_flow()
                self.adapter.handlers["inventory"] = lambda ctx: invalid
                self.flow.observe(storage())
                self.flow.advance()
                self.assertEqual(self.flow.snapshot()["phase"], "attention")
                self.assertFalse(self.flow.snapshot()["can_choose"])
                self.flow.advance()
                self.assertEqual(self.adapter.names(), ["inventory"])

    def test_corrupt_journal_never_causes_callbacks(self):
        self.path.write_text('{"phase": "syncing_out"}')
        self.flow = self.new_flow()
        self.flow.observe(storage())
        self.assertEqual(self.flow.snapshot()["phase"], "attention")
        self.assertTrue(self.flow.snapshot()["recovery_required"])
        self.assertFalse(self.flow.advance())
        self.assertFalse(self.flow.snapshot()["can_retry"])
        self.assertEqual(self.adapter.names(), [])


class SessionReturnTests(unittest.TestCase):
    def test_unchanged_pc_is_never_exported_even_when_entry_versions_differ(self):
        result = plan_session_return({"A": "a"*64}, {"A": "b"*64},
                                     {"A": "a"*64}, {"A": "b"*64})
        self.assertEqual(result["actions"], [])
        self.assertEqual(result["conflicts"], [])

    def test_changed_pc_can_replace_unchanged_different_psp_entry(self):
        result = plan_session_return({"A": "a"*64}, {"A": "b"*64},
                                     {"A": "a"*64}, {"A": "c"*64})
        self.assertEqual(result["actions"], [{"slot": "A", "direction": "to_psp",
                                             "source_hash": "c"*64, "target_hash": "a"*64}])
        self.assertEqual(result["conflicts"], [])

    def test_external_psp_change_conflicts_with_changed_pc(self):
        result = plan_session_return({"A": "a"*64}, {"A": "b"*64},
                                     {"A": "d"*64}, {"A": "c"*64})
        self.assertEqual(result["actions"], [])
        self.assertEqual(len(result["conflicts"]), 1)

    def test_external_psp_changes_are_never_imported_on_exit(self):
        result = plan_session_return({"A": "a"*64}, {"A": "a"*64},
                                     {"A": "d"*64}, {"A": "a"*64})
        self.assertEqual(result["actions"], [])
        self.assertEqual(result["conflicts"], [])
        self.assertEqual(result["ignored_psp_changes"], ["A"])

    def test_current_identical_versions_do_not_require_a_write_or_choice(self):
        result = plan_session_return({"A": "a"*64}, {"A": "b"*64},
                                     {"A": "c"*64}, {"A": "c"*64})
        self.assertEqual(result["actions"], [])
        self.assertEqual(result["conflicts"], [])
        self.assertEqual(result["unchanged"], 1)

    def test_deletions_are_not_propagated_and_new_saves_are_outbound(self):
        result = plan_session_return({"old": "a"*64}, {"old": "a"*64},
                                     {"old": "a"*64}, {"new": "b"*64})
        self.assertEqual([x["slot"] for x in result["conflicts"]], ["old"])
        self.assertEqual(result["actions"], [{"slot": "new", "direction": "to_psp",
                                             "source_hash": "b"*64, "target_hash": None}])

    def test_invalid_snapshot_fails_before_any_plan(self):
        with self.assertRaises(ProductFlowError):
            plan_session_return({}, {}, {}, {"save": "unverified"})


if __name__ == "__main__":
    unittest.main()
