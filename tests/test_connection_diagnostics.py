from copy import deepcopy
import threading
import unittest
from unittest.mock import patch

from psp_dock.core.connection_diagnostics import ConnectionDiagnostics


class ConnectionDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.now = 10.0
        self.saved = []
        self.trace = ConnectionDiagnostics(clock=lambda:self.now,
            persist=lambda value:self.saved.append(deepcopy(value)), max_events=4)

    def test_active_step_is_visible_without_waiting_for_the_slow_call(self):
        with self.trace.measure('usb.identify_storage'):
            self.now += 12.25
            with patch('builtins.open', side_effect=AssertionError('snapshot did I/O')):
                view = self.trace.snapshot()
            self.assertEqual(view['active'], [{'stage':'usb.identify_storage','elapsed_ms':12250.0}])
        view = self.trace.snapshot()
        self.assertEqual(view['active'], [])
        self.assertEqual(view['stages']['usb.identify_storage']['last_ms'], 12250.0)
        self.assertEqual(self.saved[-1]['events'][-1]['duration_ms'], 12250.0)

    def test_fast_polling_updates_aggregates_without_writing_each_poll(self):
        for _ in range(50):
            with self.trace.measure('usb.observe'):
                self.now += 0.001
        self.assertEqual(self.saved, [])
        self.assertEqual(self.trace.snapshot()['stages']['usb.observe']['count'], 50)

    def test_important_command_has_durable_start_and_final_error_without_nonce(self):
        class Failure(ValueError):
            details = {'operation':'HOST_BEGIN', 'sequence':1, 'session':'private nonce',
                       'last_observed':{'phase':1,'sequence':0,'result':0,
                                        'pending':False,'host_confirmed':False,'session':'private nonce'}}
        with self.assertRaises(Failure):
            with self.trace.measure('usb.begin_command', important=True):
                self.assertEqual(self.saved[-1]['events'][-1]['event'], 'started')
                self.now += 2
                raise Failure('HOST_BEGIN missing ACK')
        row = self.saved[-1]['events'][-1]
        self.assertEqual(row['event'], 'error')
        self.assertEqual(row['details']['last_observed']['sequence'], 0)
        self.assertNotIn('session', row['details'])
        self.assertNotIn('session', row['details']['last_observed'])

    def test_diagnostic_disk_error_does_not_hide_usb_error_or_grant_success(self):
        self.trace._persist = lambda value: (_ for _ in ()).throw(OSError('disk unavailable'))
        with self.assertRaisesRegex(ValueError, 'USB failed'):
            with self.trace.measure('usb.begin_command', important=True):
                raise ValueError('USB failed')
        self.assertIn('disk unavailable', self.trace.snapshot()['persistence_error'])
        self.assertEqual(self.trace.snapshot()['stages']['usb.begin_command']['errors'], 1)

    def test_nested_steps_are_independent_and_event_history_is_bounded(self):
        for _ in range(4):
            with self.trace.measure('callback.inventory', important=True):
                with self.trace.measure('usb.host_begin', important=True):
                    self.now += 0.01
        view = self.trace.snapshot()
        self.assertEqual(len(view['events']), 4)
        self.assertEqual(view['stages']['callback.inventory']['count'], 4)
        self.assertEqual(view['active'], [])

    def test_blocked_persistence_does_not_block_concurrent_snapshot(self):
        entered, release, observed = threading.Event(), threading.Event(), threading.Event()
        views = []
        def slow_disk(value):
            entered.set()
            release.wait(3)
        self.trace._persist = slow_disk
        def operation():
            with self.trace.measure('usb.begin_command', important=True):
                pass
        def read_snapshot():
            views.append(self.trace.snapshot())
            observed.set()
        worker = threading.Thread(target=operation)
        reader = threading.Thread(target=read_snapshot)
        worker.start()
        try:
            self.assertTrue(entered.wait(1), 'fixture did not reach blocked disk')
            reader.start()
            self.assertTrue(observed.wait(0.5), 'snapshot waited for diagnostic fsync')
            self.assertEqual(views[0]['active'][0]['stage'], 'usb.begin_command')
            self.assertFalse(release.is_set(), 'snapshot only returned after disk unblocked')
        finally:
            release.set()
            worker.join(2)
            if reader.ident is not None:
                reader.join(2)
        self.assertFalse(worker.is_alive())
        self.assertFalse(reader.is_alive())


if __name__ == '__main__':
    unittest.main()
