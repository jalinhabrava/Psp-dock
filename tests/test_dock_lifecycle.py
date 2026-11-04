import json
import importlib.util
from contextlib import contextmanager
from pathlib import Path
import tempfile
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import Mock, patch

import dock
from psp_dock.core.dock_lifecycle import UiLifecycle


A = 'a' * 32
B = 'b' * 32


@contextmanager
def focus_observer():
    # The local review mirror intentionally omits this Windows adapter.
    # The complete Windows suite patches the real function directly.
    if importlib.util.find_spec('psp_dock.emulation.ppsspp_windows_flow') is not None:
        with patch('psp_dock.emulation.ppsspp_windows_flow.return_to_xmb') as focus:
            yield focus
    else:
        focus = Mock()
        with patch.dict(sys.modules, {'psp_dock.emulation.ppsspp_windows_flow':Mock(return_to_xmb=focus)}):
            yield focus


class Clock:
    def __init__(self):
        self.value = 0.0
    def __call__(self):
        return self.value
    def advance(self, seconds):
        self.value += seconds


class LeaseTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.ui = UiLifecycle(now=self.clock)

    def test_reload_grace_and_late_heartbeat_do_not_resurrect_closed_document(self):
        self.ui.heartbeat(A)
        self.ui.release(A, 'pagehide')
        self.assertTrue(self.ui.heartbeat(A)['ignored'])
        self.clock.advance(2)
        self.assertFalse(self.ui.should_stop())
        self.ui.heartbeat(B)
        self.clock.advance(4)
        self.assertFalse(self.ui.should_stop())
        self.assertEqual(self.ui.snapshot()['clients'], 1)

    def test_closing_one_document_does_not_close_other(self):
        self.ui.heartbeat(A)
        self.ui.heartbeat(B)
        self.assertFalse(self.ui.release(A, 'button')['closing'])
        self.clock.advance(4)
        self.assertFalse(self.ui.should_stop(native_visible=False))
        self.ui.release(B, 'button')
        self.clock.advance(3)
        self.assertTrue(self.ui.should_stop(native_visible=True))

    def test_live_native_window_prevents_background_timer_shutdown(self):
        self.ui.heartbeat(A)
        self.assertFalse(self.ui.should_stop(native_visible=True))
        self.clock.advance(300)
        self.assertFalse(self.ui.should_stop(native_visible=True))
        # A minimized HWND remains visible; closing it then expires the
        # native lease, without waiting 90 seconds for throttled JS.
        self.assertFalse(self.ui.should_stop(native_visible=False))
        self.clock.advance(3)
        self.assertTrue(self.ui.should_stop(native_visible=False))

    def test_failed_native_observation_is_not_window_disappearance(self):
        self.ui.heartbeat(A)
        self.ui.should_stop(native_visible=True)
        self.clock.advance(6)
        self.assertFalse(self.ui.should_stop(native_visible=None))
        self.assertEqual(self.ui.snapshot()['clients'], 1)

    def test_startup_timeout_and_browser_fallback_are_bounded(self):
        self.clock.advance(29)
        self.assertFalse(self.ui.should_stop())
        self.clock.advance(1)
        self.assertTrue(self.ui.should_stop())
        self.assertEqual(self.ui.reason, 'ui_start_timeout')
        other = UiLifecycle(now=self.clock)
        other.heartbeat(A)
        self.clock.advance(90)
        self.assertFalse(other.should_stop())
        self.clock.advance(3)
        self.assertTrue(other.should_stop())
        self.assertEqual(other.reason, 'heartbeat_expired')


class AppCloseGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = dock.Dock(self.tmp.name)

    def test_pending_return_and_recovery_metadata_are_preserved_without_sync(self):
        self.app.session = {'pending_return':True, 'running':True,
                            'prepared_volume':'original', 'return_result':'pending'}
        self.app.recovery_required = True
        dock.atomic_json(self.app.data/'session.json', self.app.session)
        before = (self.app.data/'session.json').read_bytes()
        self.app.sync = Mock(side_effect=AssertionError('No sync on UI close'))
        self.app.request_exit = Mock(side_effect=AssertionError('No return on UI close'))
        self.assertIsNone(dock.app_close_blocker(self.app))
        self.assertEqual((self.app.data/'session.json').read_bytes(), before)
        self.app.sync.assert_not_called()
        self.app.request_exit.assert_not_called()

    def test_running_game_transfer_and_volume_preparation_block_explicit_close(self):
        self.app.process = Mock()
        self.app.process.poll.return_value = None
        self.assertIn('PPSSPP', dock.app_close_blocker(self.app))
        self.app.process = None
        self.app.transfer_status['active'] = True
        self.assertIn('copia', dock.app_close_blocker(self.app))
        self.app.transfer_status['active'] = False
        self.app.texture_download_status['active'] = True
        self.assertIn('pack', dock.app_close_blocker(self.app))
        self.app.texture_download_status['active'] = False
        self.app.storage_polling_paused = True
        self.assertIn('volumen', dock.app_close_blocker(self.app))

    def test_game_finishing_during_close_records_metadata_without_restoring_window(self):
        self.app.session.update(running=True,pending_return=True)
        self.app.process = Mock()
        self.app.process.poll.return_value = 0
        self.app.stop.set()
        with focus_observer() as focus:
            self.assertIsNone(dock.app_close_blocker(self.app,settle_finished_game=True))
        focus.assert_not_called()
        self.assertIsNone(self.app.process)
        saved=json.loads((self.app.data/'session.json').read_text())
        self.assertFalse(saved['running'])
        self.assertTrue(saved['pending_return'])


class ManagedServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = Clock()
        self.ui = UiLifecycle(now=self.clock)
        self.app = dock.Dock(self.tmp.name)
        self.started = threading.Event()
        self.watched = threading.Event()
        self.failures = []
        def watch():
            self.watched.set()
            self.app.stop.wait(10)
        self.app.watch = watch
        self.lease_patch=patch.object(dock,'UiLifecycle',return_value=self.ui)
        self.lease_patch.start()
        self.addCleanup(self.lease_patch.stop)
        self.window_patch=patch.object(dock,'launch_xmb_window')
        self.window=self.window_patch.start()
        self.addCleanup(self.window_patch.stop)
        def run():
            try:
                # Passing the old callback flag deliberately must not
                # revive open-on-connect in a manually managed app.
                dock.serve(self.app,0,True,True,False,ui_lifecycle=True)
            except BaseException as exc:
                self.failures.append(exc)
        self.thread=threading.Thread(target=run,daemon=True)
        self.thread.start()
        # unittest does not call tearDown() when setUp() fails. Always end
        # the synthetic service and restore monkeypatches on an exception.
        self.addCleanup(self._stop_orphan_server)
        deadline=time.monotonic()+5
        while True:
            try:
                metadata=json.loads((self.app.data/'server.json').read_text())
                break
            except (FileNotFoundError, PermissionError, json.JSONDecodeError):
                # Windows can momentarily lock server.json between creation
                # and the first complete write. This is not an app failure.
                if time.monotonic() >= deadline or self.failures:
                    self.fail('Managed server did not start: '+repr(self.failures))
                time.sleep(.02)
        self.url='http://127.0.0.1:'+str(metadata['port'])
        with urllib.request.urlopen(self.url+'/api/token',timeout=3) as response:
            self.token=json.load(response)['token']

    def _stop_orphan_server(self):
        self.app.stop.set()
        if self.thread.is_alive():
            self.thread.join(5)

    def tearDown(self):
        self.app.process=None
        self.app.transfer_status['active']=False
        self.app.texture_download_status['active']=False
        self.app.storage_polling_paused=False
        self.ui.stopping=True
        self.thread.join(5)
        self.window_patch.stop()
        self.lease_patch.stop()
        self.tmp.cleanup()
        self.assertFalse(self.thread.is_alive(),'Managed service survived UI lifetime')
        self.assertEqual(self.failures,[])

    def post(self,path,body,*,token=True,origin=None):
        headers={'Content-Type':'application/json','Origin':origin or self.url}
        if token:
            headers['X-Dock-Token']=self.token
        request=urllib.request.Request(self.url+path,data=json.dumps(body).encode(),headers=headers)
        try:
            with urllib.request.urlopen(request,timeout=3) as response:
                return response.status,json.load(response)
        except urllib.error.HTTPError as exc:
            return exc.code,json.load(exc)

    def test_first_authenticated_heartbeat_starts_one_watch_and_close_keeps_pending(self):
        self.assertFalse(self.watched.is_set())
        self.assertIsNone(self.app.on_connected)
        with urllib.request.urlopen(self.url+'/api/health',timeout=3) as response:
            health=json.load(response)
        self.assertFalse(health['ui_connected_once'])
        self.assertEqual(health['native_handles'],[])
        self.assertEqual(self.post('/api/app/heartbeat',{'client_id':A},token=False)[0],403)
        self.assertEqual(self.post('/api/app/heartbeat',{'client_id':A},origin='https://example.com')[0],403)
        self.assertFalse(self.watched.is_set())
        self.assertEqual(self.post('/api/app/heartbeat',{'client_id':A})[0],200)
        self.assertTrue(self.watched.wait(2))
        with urllib.request.urlopen(self.url+'/api/health',timeout=3) as response:
            self.assertTrue(json.load(response)['ui_connected_once'])
        self.app.session={'running':False,'pending_return':True,'prepared_volume':'same'}
        dock.atomic_json(self.app.data/'session.json',self.app.session)
        before=(self.app.data/'session.json').read_bytes()
        status,result=self.post('/api/app/close',{'client_id':A,'reason':'button'})
        self.assertEqual(status,200)
        self.assertTrue(result['pending_return_preserved'])
        self.clock.advance(3)
        self.thread.join(4)
        self.assertFalse(self.thread.is_alive())
        self.assertTrue(self.app.stop.is_set())
        self.assertEqual((self.app.data/'session.json').read_bytes(),before)
        self.assertFalse((self.app.data/'server.json').exists())
        self.window.assert_called_once_with(self.url,app_window=False,reuse_existing=False)

    def test_explicit_close_rejected_during_game_but_pagehide_stops_watch_and_waits(self):
        self.post('/api/app/heartbeat',{'client_id':A})
        self.app.process=Mock()
        self.app.process.poll.return_value=None
        self.app.session.update(running=True,pending_return=True)
        status,result=self.post('/api/app/close',{'client_id':A,'reason':'button'})
        self.assertEqual(status,409)
        self.assertEqual(self.ui.snapshot()['clients'],1)
        self.assertEqual(self.post('/api/app/close',{'client_id':A,'reason':'pagehide'})[0],200)
        self.clock.advance(3)
        self.assertTrue(self.app.stop.wait(3))
        self.assertTrue(self.thread.is_alive())
        with self.assertRaisesRegex(ValueError,'ya está abierto'):
            dock.acquire_instance_lock(self.app.data)
        with focus_observer() as focus:
            self.app.process.poll.return_value=0
            self.thread.join(4)
        focus.assert_not_called()
        self.assertFalse(self.thread.is_alive())
        self.assertTrue(self.app.session['pending_return'])
        self.assertFalse(self.app.session['running'])


if __name__ == '__main__':
    unittest.main()
