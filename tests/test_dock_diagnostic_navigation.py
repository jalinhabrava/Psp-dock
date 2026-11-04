"""A diagnostic controller can navigate an open UI but cannot enable games."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import dock as app
import psp_dock.emulation.ppsspp_hid_session as ppsspp_hid_session
import psp_dock.devices.psp_dock_identity as psp_dock_identity
import psp_dock.devices.psp_launcher_input as psp_launcher_input


STABLE = {'path': r'\\?\hid#vid_054c&pid_01cb#psp', 'session': '01' * 16,
          'product_id': 0x01CB, 'revision': 0x0104, 'mode': 'hid_manual',
          'can_play': True, 'synthetic_storage': False}
DIAGNOSTIC = {**STABLE, 'path': r'\\?\hid#vid_054c&pid_01cc&mi_00#psp',
              'product_id': 0x01CC, 'revision': 0x0207, 'mode': 'msc_diagnostic',
              'can_play': False, 'synthetic_storage': True}


class DiagnosticNavigationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dock = app.Dock(Path(self.temp.name) / 'data')

    def status(self, identity):
        with patch.object(app.os, 'name', 'nt'), \
             patch.object(psp_dock_identity, 'probe_dock', return_value=identity), \
             patch.object(ppsspp_hid_session, 'validate_hid_only',
                          return_value={'path': STABLE['path'], 'session': STABLE['session']}) as stable:
            result = self.dock.controller_status(False)
        return result, stable

    def test_msc_navigation_does_not_use_or_relax_the_stable_game_gate(self):
        result, stable = self.status(DIAGNOSTIC)
        self.assertTrue(result['connected'])
        self.assertEqual(result['mode'], 'msc_diagnostic')
        self.assertFalse(result['can_play'])
        self.assertTrue(result['synthetic_storage'])
        stable.assert_not_called()

    def test_stable_still_requires_matching_live_hid_only_session(self):
        result, stable = self.status(STABLE)
        self.assertTrue(result['connected'] and result['can_play'])
        stable.assert_called_once()
        with patch.object(app.os, 'name', 'nt'), \
             patch.object(psp_dock_identity, 'probe_dock', return_value=STABLE), \
             patch.object(ppsspp_hid_session, 'validate_hid_only',
                          return_value={'path': STABLE['path'], 'session': '02' * 16}):
            self.assertFalse(self.dock.controller_status(False)['connected'])

    def test_absent_ambiguous_and_native_storage_remain_waiting(self):
        result, stable = self.status(None)
        self.assertFalse(result['connected'] or result['can_play'])
        stable.assert_not_called()
        with patch.object(app.os, 'name', 'nt'), \
             patch.object(psp_dock_identity, 'probe_dock',
                          side_effect=psp_dock_identity.DockIdentityError('ambiguous')):
            self.assertFalse(self.dock.controller_status(False)['connected'])
        with patch.object(psp_dock_identity, 'probe_dock') as probe:
            result = self.dock.controller_status(True)
        self.assertEqual(result['mode'], 'usb_storage')
        self.assertFalse(result['connected'] or result['can_play'])
        probe.assert_not_called()

    def test_input_reader_uses_the_verified_product_and_rejects_mixed_identity(self):
        for identity in (STABLE, DIAGNOSTIC):
            self.dock.snapshot['controller_status'] = {**identity, 'connected': True}
            with patch.object(psp_launcher_input, 'PSPWinMMReader') as reader:
                reader.return_value.read.return_value = {'connected': True}
                self.assertTrue(self.dock.psp_launcher_input()['connected'])
            reader.assert_called_once_with(expected_product=identity['product_id'])
        self.dock.snapshot['controller_status'] = {
            **DIAGNOSTIC, 'connected': True, 'product_id': 0x01CB}
        with patch.object(psp_launcher_input, 'PSPWinMMReader') as reader:
            self.assertFalse(self.dock.psp_launcher_input()['connected'])
        reader.assert_not_called()

    def test_prepared_cached_library_does_not_make_diagnostic_ready_to_play(self):
        self.dock.session['prepared_volume'] = self.dock.catalog_volume = 'real-card'
        self.dock.games = Mock(return_value=[{'availability': 'ready'}])
        self.dock.root = Mock(return_value=None)
        self.dock.devices = Mock(return_value=[])
        self.dock.save_plan = Mock(return_value={'actions': [], 'conflicts': [], 'unchanged': 0})
        self.dock.prepared_runtime_path = Mock(return_value=Path(self.temp.name) / 'ppsspp.exe')
        for identity, expected in ((STABLE, True), (DIAGNOSTIC, False)):
            self.dock.controller_status = Mock(return_value={**identity, 'connected': True})
            with patch.object(app, 'assess_storage', wraps=app.assess_storage) as preflight:
                state = self.dock.state()
            self.assertEqual(state['connection_state'], 'dock_connected')
            self.assertEqual(preflight.call_args.kwargs['hid_connected'], expected)
            self.assertEqual(state['can_launch_game'], expected)
            if not expected:
                self.assertNotEqual(state['preflight']['phase'], 'hid_xmb')

    def test_diagnostic_backend_rejects_game_and_copy_request_before_any_io(self):
        self.dock.snapshot['controller_status'] = {**DIAGNOSTIC, 'connected': True}
        with patch.object(self.dock, 'validate_layout') as layout, \
             patch.object(self.dock, 'root') as root, \
             patch.object(app.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(ValueError, 'prueba'):
                self.dock.launch('a' * 24)
            with self.assertRaises(ValueError):
                self.dock.request_game_copy_from_hid('a' * 24)
        layout.assert_not_called()
        root.assert_not_called()
        spawn.assert_not_called()
        self.assertEqual(self.dock.copy_queue, {})

    def test_watch_error_revokes_old_controller_without_hiding_pending_work(self):
        self.dock.session.update(running=True, pending_return=True)
        self.dock.snapshot.update(
            controller_status={**STABLE, 'connected': True},
            can_launch_game=True, connection_state='dock_connected',
            transfer_status={'active': True}, recovery_required=True)
        with patch.object(self.dock, 'root', return_value=None), \
             patch.object(self.dock, 'running', return_value=True), \
             patch.object(self.dock, 'state', side_effect=OSError('scan interrupted')) as state, \
             patch.object(self.dock.stop, 'wait', side_effect=lambda _seconds: self.dock.stop.set()):
            self.dock.watch()
        state.assert_called_once()
        snapshot = self.dock.snapshot
        self.assertFalse(snapshot['controller_status']['connected'])
        self.assertFalse(snapshot['controller_status']['can_play'])
        self.assertFalse(snapshot['can_launch_game'])
        self.assertEqual(snapshot['connection_state'], 'waiting_psp')
        self.assertFalse(snapshot['scanning'])
        self.assertTrue(snapshot['session']['running'])
        self.assertTrue(snapshot['session']['pending_return'])
        self.assertTrue(snapshot['transfer_status']['active'])
        self.assertTrue(snapshot['recovery_required'])


if __name__ == '__main__':
    unittest.main()
