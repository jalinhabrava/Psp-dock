"""Native window-close recovery, with fake windows/processes and real temp files."""
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import dock as app
from dock_lifecycle import UiLifecycle
from dock_product_session import DockProductSession
from tests.test_dock_product_session import FakeTransport


A, B = 'a'*32, 'b'*32


class Clock:
    def __init__(self):
        self.now = 0
    def __call__(self):
        return self.now


class NativeWindowCloseTests(unittest.TestCase):
    def setUp(self):
        self.no_usb = patch.dict(app.os.environ, {'PSP_DOCK_TEST_NO_REAL_USB':'1'})
        self.no_usb.start()
        self.addCleanup(self.no_usb.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.psp = self.home/'synthetic-psp'
        (self.psp/'PSP/SAVEDATA/SAVE').mkdir(parents=True)
        (self.psp/'ISO').mkdir()
        (self.psp/'ISO/Game.iso').write_bytes(b'synthetic game, never execute')
        self.psp_save = self.psp/'PSP/SAVEDATA/SAVE/DATA.BIN'
        self.psp_save.write_bytes(b'old')
        self.dock = app.Dock(self.home/'data')
        # A developer's bundled PPSSPP must not turn a window-lifecycle
        # fixture into repeated full runtime copies on Windows.
        self.dock.settings({'psp_root':str(self.psp), 'emulator':''})
        self.pc_save = Path(self.dock.config['memstick'])/'PSP/SAVEDATA/SAVE/DATA.BIN'
        self.pc_save.parent.mkdir(parents=True)
        self.pc_save.write_bytes(b'old')
        self.transport = FakeTransport(self.psp)
        self.product = DockProductSession(self.dock,self.transport)
        self.dock.product_session,self.dock.product_flow,self.dock.native_transport = self.product,self.product.flow,self.transport
        self.clock = Clock()
        self.ui = UiLifecycle(now=self.clock)
        self.ui.heartbeat(A)
        self.ui.should_stop(native_visible=True)
        self.restore = Mock(return_value=True)
        self.dock.on_verified_exit = Mock()

    def tick(self):
        self.dock.stop.clear()
        with patch.object(self.dock.stop,'wait',side_effect=lambda seconds:self.dock.stop.set()), \
             patch('ppsspp_windows_flow.return_to_xmb',return_value=False):
            self.dock.watch()
        self.dock.stop.clear()  # Preserve the live-app fixture between individual worker turns.

    def ready(self):
        self.tick()
        self.product.choose('none',self.product.snapshot()['offer_id'])
        self.tick()
        self.tick()
        self.assertEqual(self.product.flow.snapshot()['phase'],'ready')

    def close_window(self, client):
        self.ui.release(client,'pagehide')
        self.clock.now += 3
        self.assertTrue(self.ui.should_stop(native_visible=False))
        return self.dock.window_close.restore_once(self.ui,self.restore)

    def show_restored_window(self):
        self.ui.heartbeat(B)
        self.ui.should_stop(native_visible=True)
        self.dock.window_close.confirm_visible(clients=self.ui.snapshot()['clients'])

    def finish_or_attention(self):
        for _ in range(8):
            self.tick()
            if self.product.flow.snapshot()['phase'] in ('safe_to_disconnect','attention'):
                return self.product.flow.snapshot()
        self.fail(str(self.product.snapshot()))

    def test_x_restores_once_and_normal_exit_waits_for_visible_ui_then_verifies(self):
        self.ready()
        self.pc_save.write_bytes(b'played on PC')
        self.product.flow.set_game_running(True)
        self.product.flow.set_game_running(False)
        self.dock.session['pending_return'] = True
        self.assertTrue(self.close_window(A))
        self.restore.assert_called_once_with()
        self.assertFalse(self.ui.stopping)
        self.assertTrue(self.ui.heartbeat(A)['ignored'])
        self.tick()  # Browser restoration has not been confirmed yet.
        self.assertEqual(self.product.flow.snapshot()['phase'],'ready')
        self.assertEqual(self.psp_save.read_bytes(),b'old')
        self.show_restored_window()
        self.assertEqual(self.finish_or_attention()['phase'],'safe_to_disconnect')
        self.assertEqual(self.psp_save.read_bytes(),b'played on PC')
        self.assertFalse(self.dock.session['pending_return'])
        self.dock.on_verified_exit.assert_called_once_with()
        record=app.read_json(self.dock.data/'window-close-request.json',{})
        self.assertEqual(record['status'],'verified_closed')
        self.assertFalse(record['recovery_preserved'])
        self.assertFalse(self.close_window(B))  # Safe closure cannot reopen in a loop.
        self.restore.assert_called_once_with()

    def test_closing_before_initial_choice_does_not_copy_games_or_import_saves(self):
        self.pc_save.unlink()
        self.tick()
        self.assertEqual(self.product.flow.snapshot()['phase'],'choosing')
        self.assertTrue(self.close_window(A))
        self.show_restored_window()
        self.assertFalse(self.product.snapshot()['can_choose'])
        self.assertEqual(self.finish_or_attention()['phase'],'safe_to_disconnect')
        self.assertFalse(self.pc_save.exists())
        self.assertFalse((Path(self.dock.config['library'])/'ISO/Game.iso').exists())
        self.assertEqual(self.psp_save.read_bytes(),b'old')
        self.assertIsNone(self.product.flow.snapshot()['choice'])
        self.assertEqual(self.product.flow.snapshot()['purpose'],'cancel')
        self.assertEqual(self.transport.roles, [])

    def test_x_before_inventory_cancels_entry_without_starting_the_scan_or_return(self):
        self.product.flow.enabled = True
        self.product.observation = self.transport.observe(self.dock.devices())
        self.product.flow.observe(self.product.observation)
        self.assertEqual(self.product.flow.snapshot()['phase'], 'inventory')
        self.assertFalse(self.transport.host_confirmed)
        self.assertTrue(self.close_window(A))
        self.show_restored_window()
        with patch('dock.copy_slot', side_effect=AssertionError('early close must not copy saves')):
            self.assertEqual(self.finish_or_attention()['phase'], 'safe_to_disconnect')
        self.assertEqual(self.product.snapshot()['purpose'], 'cancel')
        self.assertEqual(self.transport.roles, [])
        self.assertEqual(self.transport.begins, 1)
        self.assertFalse(self.product.entry_path.exists())
        self.assertEqual(self.psp_save.read_bytes(), b'old')

    def test_open_emulator_is_not_killed_and_return_starts_only_after_normal_exit(self):
        self.ready()
        process=Mock()
        process.poll.return_value=None
        self.dock.process=process
        self.dock.session.update(running=True,pending_return=True,launcher='retroarch')
        self.product.flow.set_game_running(True)
        self.pc_save.write_bytes(b'new after game')
        self.assertTrue(self.close_window(A))
        self.show_restored_window()
        with patch.object(self.dock,'request_exit',wraps=self.dock.request_exit) as exit_request:
            self.tick()
            exit_request.assert_not_called()
            view=self.product.snapshot()
            self.assertEqual(view['phase'],'waiting_game_exit')
            self.assertFalse(view['can_close'] or view['can_retry'] or view['can_choose'])
            self.assertEqual(self.transport.roles,[('controller','initial')])
            self.assertEqual(self.psp_save.read_bytes(),b'old')
            process.poll.return_value=0
            self.assertEqual(self.finish_or_attention()['phase'],'safe_to_disconnect')
            exit_request.assert_called_once_with()
        process.terminate.assert_not_called()
        process.kill.assert_not_called()
        self.assertEqual(self.psp_save.read_bytes(),b'new after game')

    def test_orphan_emulator_blocks_return_and_second_x_drains_without_reopening(self):
        self.ready()
        self.dock.recovery_required=True
        self.dock.session.update(running=True,pending_return=True)
        self.assertTrue(self.close_window(A))
        self.show_restored_window()
        with patch.object(self.dock,'request_exit',side_effect=AssertionError('orphan process still owns saves')):
            self.tick()
            self.assertEqual(self.product.snapshot()['phase'],'waiting_game_exit')
            self.assertIn('recuperación',self.product.snapshot()['window_close_message'])
            self.assertFalse(self.close_window(B))
            self.tick()
        self.restore.assert_called_once_with()
        self.assertFalse(self.dock.window_close.pending())
        self.assertTrue(self.dock.session['pending_return'])
        self.assertEqual(app.read_json(self.dock.data/'window-close-request.json',{})['status'],'forced_closed')
        self.assertTrue(self.ui.stopping)

    def test_restoration_never_waits_for_the_transfer_lock_and_second_x_keeps_journal(self):
        self.ready()
        game=self.dock.games()[0]
        self.product.flow.request_copy(game['id'])
        self.transport.phase=3
        self.product.flow.observe(self.transport.current)
        self.dock.transfer_status['active']=True
        entered,release=threading.Event(),threading.Event()
        def hold_operation():
            with self.dock.lock:
                entered.set()
                release.wait(3)
        worker=threading.Thread(target=hold_operation)
        worker.start()
        self.assertTrue(entered.wait(1))
        try:
            self.assertTrue(self.close_window(A))
            self.show_restored_window()
            self.assertEqual(self.product.snapshot()['phase'],'copying_game')
            self.assertIn('operación',self.product.snapshot()['window_close_message'])
            self.assertIn('operación',app.app_close_blocker(self.dock))
            self.assertFalse(self.close_window(B))
            self.assertEqual(self.product.flow.snapshot()['phase'],'copying_game')
            self.assertFalse(self.dock.window_close.pending())
        finally:
            release.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.restore.assert_called_once_with()
        self.assertEqual(self.transport.roles,[('controller','initial')])
        self.assertFalse((Path(self.dock.config['library'])/'ISO/Game.iso').exists())

    def test_conflict_keeps_restored_window_and_requires_explicit_resolution(self):
        self.ready()
        self.psp_save.write_bytes(b'external PSP')
        self.pc_save.write_bytes(b'PC progress')
        self.assertTrue(self.close_window(A))
        self.show_restored_window()
        self.assertEqual(self.finish_or_attention()['phase'],'attention')
        self.assertFalse(self.ui.should_stop(native_visible=True))
        self.assertTrue(self.product.snapshot()['window_close_requested'])
        self.assertIn('Revisa',self.product.snapshot()['window_close_message'])
        self.assertEqual(self.psp_save.read_bytes(),b'external PSP')
        self.dock.on_verified_exit.assert_not_called()
        row=self.dock.snapshot['saves']['conflicts'][0]
        self.dock.sync({'slot':row['slot'],'choice':'psp','revision':row['revision']})
        self.product.retry(self.product.flow.snapshot()['offer_id'])
        self.assertEqual(self.finish_or_attention()['phase'],'safe_to_disconnect')
        self.restore.assert_called_once_with()
        self.assertEqual(self.pc_save.read_bytes(),b'PC progress')

    def test_eject_veto_keeps_window_and_retries_only_finalization(self):
        self.ready()
        self.pc_save.write_bytes(b'PC progress')
        self.transport.veto=True
        self.assertTrue(self.close_window(A))
        self.show_restored_window()
        with patch('dock.copy_slot',wraps=app.copy_slot) as copier:
            self.assertEqual(self.finish_or_attention()['phase'],'attention')
            self.assertEqual(copier.call_count,1)
            self.assertFalse(self.ui.should_stop(native_visible=True))
            self.dock.on_verified_exit.assert_not_called()
            self.tick()  # Observe terminal SAFE.
            self.transport.veto=False
            self.product.retry(self.product.flow.snapshot()['offer_id'])
            self.assertEqual(self.finish_or_attention()['phase'],'safe_to_disconnect')
            self.assertEqual(copier.call_count,1)
        self.assertEqual(self.transport.finishes,2)
        self.restore.assert_called_once_with()
        self.dock.on_verified_exit.assert_called_once_with()

    def test_failed_restore_does_not_start_return_or_repeat_window_creation(self):
        self.ready()
        self.restore.return_value=False
        self.assertFalse(self.close_window(A))
        self.assertTrue(self.ui.stopping)
        self.assertFalse(self.dock.window_close.pending())
        self.assertEqual(self.product.flow.snapshot()['phase'],'ready')
        self.assertEqual(self.transport.roles,[('controller','initial')])
        self.assertEqual(app.read_json(self.dock.data/'window-close-request.json',{})['status'],'restore_failed')
        self.assertFalse(self.dock.window_close.restore_once(self.ui,self.restore))
        self.restore.assert_called_once_with()

    def test_legacy_and_waiting_do_not_restore_or_change_exit_policy(self):
        self.product.flow.enabled=False
        self.assertFalse(self.close_window(A))
        self.restore.assert_not_called()
        self.assertFalse((self.dock.data/'window-close-request.json').exists())
        self.product.flow.enabled=True
        self.assertEqual(self.product.flow.snapshot()['phase'],'waiting_psp')
        self.assertFalse(self.dock.window_close.restore_once(self.ui,self.restore))
        self.restore.assert_not_called()

    def test_pending_close_blocks_new_game_launch_even_with_stale_ready_snapshot(self):
        self.ready()
        self.assertTrue(self.close_window(A))
        with patch('dock.subprocess.Popen') as spawn:
            with self.assertRaisesRegex(ValueError,'pedido salir'):
                self.dock.launch('a'*24)
            with self.assertRaisesRegex(ValueError,'pedido salir'):
                self.dock.launch_retro('a'*24)
        spawn.assert_not_called()


if __name__=='__main__':
    unittest.main()
